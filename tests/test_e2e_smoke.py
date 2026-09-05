# -*- coding: utf-8 -*-
"""020 E2E 冒烟：一次请求进、最终历史轮出的跨层端到端。

与既有集成测试的区别：不拦截 PIPELINE_EXECUTOR.submit，任务跑在真实
工作线程里，前端视角只靠 HTTP 轮询推进。mock 仅限三个外部边界
（AI 粗筛/精筛、岗位详情抓取）与环境准备函数，其余全部走真实代码。
"""

import gc
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from webui.app import create_app

_TERMINAL_STATUSES = (
    "completed", "completed_with_pending", "failed", "cancelled", "interrupted",
)
_POLL_INTERVAL = 0.05
_POLL_TIMEOUT = 30.0


class _E2EBase(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": __import__("sys").executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- 数据准备 --------------------------------------------------------

    def _create_scrape(self, n=4):
        """造一个已完成的抓取父 run（含真实落库 + 内存任务注册）。"""
        jobs = [
            {"job_id": f"j{i}", "platform_job_id": f"j{i}",
             "title": f"岗位{i}", "salary": "20-30K", "location": "上海",
             "source_url": f"https://zhipin.example/j{i}.html"}
            for i in range(n)
        ]
        run_id = f"scrape_{uuid.uuid4().hex[:8]}"
        self.store.create_screening_run(
            run_id,
            frozen_filters={},
            source_count=len(jobs),
            execution_params={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "execution_config": {
                    "schema_version": 1, "inter_combo_delay": 10,
                    "detail_batch_size": 15, "detail_interval": 2,
                    "detail_reset_every": 4, "detail_batch_cooldown": 5,
                    "detail_tab_pool_size": 5, "screen_batch_size": 50,
                    "screen_concurrency": 5, "match_batch_size": 4,
                    "match_concurrency": 10, "config_digest": None,
                },
                "frozen_scope": {
                    "schema_version": 1, "platform": "boss",
                    "keywords": ["Python"], "scope_kind": "cities",
                    "cities": ["上海"], "pages_per_combination": 1,
                    "combination_count": 1, "planned_pages": 1,
                    "task_size": "small", "scope_digest": None,
                },
            },
            backend_version="test",
        )
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done",
            processed_count=len(jobs), match_count=0,
        )
        self.store.save_scrape_combo_result(
            run_id, "kw|city", jobs, ["kw|city"])
        from webui.store_helpers import _now
        from webui.whitebox import WhiteboxService
        whitebox = WhiteboxService(self.store)
        whitebox_ref = whitebox.begin("scrape", run_id, {
            "stages": ["scrape_list"],
            "units": [{
                "unit_key": "kw|city", "unit_kind": "keyword_city",
                "stage": "scrape_list", "planned_pages": 1,
                "required": True,
            }],
        })
        whitebox.record(whitebox_ref, {
            "idempotency_key": f"fixture-start:{run_id}",
            "event_type": "unit_started", "occurred_at": _now(),
            "stage": "scrape_list", "unit_kind": "keyword_city",
            "unit_key": "kw|city", "attempt_no": 1,
            "required_evidence": False, "payload": {
                "planned_pages": 1, "start_page": 1,
            },
        })
        whitebox.record(whitebox_ref, {
            "idempotency_key": f"fixture-page:{run_id}",
            "event_type": "page_completed", "occurred_at": _now(),
            "stage": "scrape_list", "unit_kind": "keyword_city",
            "unit_key": "kw|city", "attempt_no": 1,
            "required_evidence": True, "payload": {
                "page": 1, "planned_pages": 1,
                "returned_count": len(jobs),
                "new_unique_count": len(jobs), "has_more": False,
                "resume_page": 2,
            },
        })
        whitebox.record(whitebox_ref, {
            "idempotency_key": f"fixture-scope:{run_id}",
            "event_type": "scope_completed", "occurred_at": _now(),
            "stage": "scrape_list", "unit_kind": "keyword_city",
            "unit_key": "kw|city", "attempt_no": 1,
            "required_evidence": True, "payload": {
                "scope_complete": True, "source_exhausted": True,
                "stop_reason": "target_reached",
                "returned_total_count": len(jobs),
                "unit_unique_count": len(jobs),
            },
        })
        scrape_integrity = whitebox.finalize(whitebox_ref)
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {"ok": True, "jobs": jobs, "total_scraped": len(jobs),
                       "total_matched": 0, "completed_combos": ["kw|city"],
                       "integrity": scrape_integrity},
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        return run_id, jobs

    # -- 外部边界 mock ----------------------------------------------------

    def _patch_boundaries(self, *, match_ids=(), save_side_effect=None):
        """mock 三个外部边界 + 环境准备函数；返回 (patches, trackers)。

        match_ids：精筛判 match 的 job_id 集合，其余判 not_match。
        """
        from webui import pipeline_exec

        trackers = {
            "screen_calls": [], "match_calls": [], "fetch_calls": 0,
        }

        def _fake_screen_jobs(jobs_arg, *a, **kw):
            trackers["screen_calls"].append(
                [str(j.get("job_id")) for j in jobs_arg])
            return {
                "kept": [str(j.get("job_id")) for j in jobs_arg],
                "dropped": [], "verdicts": {},
            }

        def _fake_match_jds(jobs_arg, *a, **kw):
            trackers["match_calls"].append(
                [str(j["job_id"]) for j in jobs_arg])
            verdicts = {}
            for j in jobs_arg:
                jid = str(j["job_id"])
                verdict = "match" if jid in match_ids else "not_match"
                verdicts[jid] = {
                    "verdict": verdict,
                    "reason": "匹配" if verdict == "match" else "不匹配",
                    "caveats": [], "flags": [],
                }
            if verdicts and kw.get("on_batch_done"):
                kw["on_batch_done"](
                    verdicts, [str(j["job_id"]) for j in jobs_arg])
            return {"verdicts": verdicts}

        def _fake_fetch_details(chunk, source, **kw):
            trackers["fetch_calls"] += 1
            return {
                "jobs": [dict(j, jd="岗位描述") for j in chunk],
                "hard_stop": False, "stopped": False,
            }

        patches = [
            mock.patch.object(pipeline_exec, "resolve_browser_account",
                              return_value=""),
            mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"),
            mock.patch.object(pipeline_exec, "ensure_chrome_ready",
                              return_value=(True, "")),
            mock.patch.object(pipeline_exec, "close_debug_chrome"),
            mock.patch.object(pipeline_exec, "fetch_job_details",
                              side_effect=_fake_fetch_details),
            mock.patch.object(
                self.store, "get_ai_settings",
                return_value={"endpoint_url": "http://ai.test", "model": "m",
                              "is_configured": True}),
            mock.patch("webui.ai.is_ai_available",
                       return_value=True),
            mock.patch("webui.ai.screen_jobs", side_effect=_fake_screen_jobs),
            mock.patch("webui.ai.match_jds", side_effect=_fake_match_jds),
        ]
        save_patch = None
        if save_side_effect is not None:
            save_patch = mock.patch.object(
                self.store, "save_pipeline_result",
                side_effect=save_side_effect)
            patches.append(save_patch)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self._trackers = trackers
        self._save_patch = save_patch
        return patches, trackers

    # -- HTTP 驱动 --------------------------------------------------------

    def _start_ai_screen(self, scrape_id):
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "screening_fields": {"salary": ["20-30K"]},
            "profile_summary": "测试画像",
            "profile_facts": {"years": 3},
            "filter_schema_version": 1,
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["task_id"]

    def _poll_terminal(self, task_id):
        """前端视角轮询到终态；返回最终快照。"""
        deadline = time.monotonic() + _POLL_TIMEOUT
        last = None
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/search-progress/{task_id}")
            self.assertEqual(resp.status_code, 200,
                             resp.get_data(as_text=True))
            last = resp.get_json()
            if last["status"] in _TERMINAL_STATUSES:
                return last
            time.sleep(_POLL_INTERVAL)
        self.fail(f"轮询超时未到终态：{task_id} 最后状态 "
                  f"{(last or {}).get('status')}")


class E2EMainChainSmokeTests(_E2EBase):
    """主链：POST 筛选 → 轮询终态 → 最新结果/历史轮三处对账。"""

    def test_full_chain_request_to_history_round(self):
        scrape_id, jobs = self._create_scrape(n=4)
        match_ids = {"j0", "j1"}
        # mock 边界之外零真实网络：socket 层直接拉响警报。
        socket_guard = mock.patch(
            "socket.socket",
            side_effect=AssertionError("越界真实网络调用"))
        socket_guard.start()
        self.addCleanup(socket_guard.stop)

        self._patch_boundaries(match_ids=match_ids)
        task_id = self._start_ai_screen(scrape_id)
        snapshot = self._poll_terminal(task_id)

        # 终态 completed，无降级事件（020 US7）
        self.assertEqual(snapshot["status"], "completed", snapshot)
        events = [e["type"] for e in self.store.list_task_events(task_id)]
        self.assertNotIn("result_round_save_failed", events)

        # 对账一：轮询结果里的岗位与计数（jobs 含全部带判定岗位，
        # 判定挂在每个岗位上；total_matched 只数 match）
        result = snapshot["result"]
        self.assertTrue(result["ok"], result)
        result_jobs = {str(j["job_id"]): j for j in result["jobs"]}
        self.assertEqual(sorted(result_jobs), ["j0", "j1", "j2", "j3"])
        self.assertEqual(
            {jid for jid, j in result_jobs.items()
             if j.get("verdict") == "match"}, {"j0", "j1"})
        self.assertEqual(result["total_matched"], 2)

        # 对账二：DB run 计数与判定
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["processed_count"], 4)
        self.assertEqual(run["match_count"], 2)
        self.assertEqual(run["total_kept"], 4)
        self.assertEqual(run["total_dropped"], 0)
        verdicts = self.store.load_screening_verdicts(task_id)
        self.assertEqual(len(verdicts), 4)
        self.assertEqual(
            {j for j, v in verdicts.items() if v["verdict"] == "match"},
            match_ids)

        # 对账三：最新结果接口 + 历史轮恰一条，且与本轮一致
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertTrue(latest["has_result"], latest)
        # 持久化行按 platform_job_id 对账（job_id 是内部 UUID，合成岗位为 None）
        latest_jobs = {str(j["platform_job_id"]): j
                       for j in latest["result"]["jobs"]}
        self.assertEqual(sorted(latest_jobs), ["j0", "j1", "j2", "j3"])
        self.assertEqual(
            {jid for jid, j in latest_jobs.items()
             if j.get("verdict") == "match"}, {"j0", "j1"})
        self.assertEqual(latest["result"]["total_matched"], 2)
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(len(history["items"]), 1, history)
        rounds = self.store.list_history_rounds()
        self.assertEqual(len(rounds), 1)
        # 历史轮与最新结果接口指向同一条结果快照 run
        persisted = self.store.load_latest_pipeline_result()
        self.assertEqual(str(rounds[0]["id"]), str(persisted["run_id"]))

        # mock 边界确实被走到（而非绕过）
        self.assertGreaterEqual(self._trackers["fetch_calls"], 1)


class E2ERescueChainSmokeTests(_E2EBase):
    """救援链（020 US7 用户视角）：锁错 → 降级 failed → 续跑重建结果轮。"""

    def test_lock_failure_downgrade_then_resume_rebuilds(self):
        import webui.result_rounds as result_rounds

        scrape_id, _jobs = self._create_scrape(n=3)

        # 重试 sleep 置零，避免真实等待；记录次数
        sleeps = []
        sleep_patch = mock.patch.object(
            result_rounds, "_retry_sleep",
            side_effect=lambda s: sleeps.append(s))
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

        # 第一轮：写轮恒抛瞬时锁错 → 重试 3 次 → 条件降级 failed。
        # 精筛全部判 match，续跑继承链上判定后 match_count 才有对账意义。
        attempts = []

        def always_locked(*a, **kw):
            attempts.append(1)
            raise sqlite3.OperationalError("database is locked")

        self._patch_boundaries(match_ids={"j0", "j1", "j2"},
                               save_side_effect=always_locked)
        task1 = self._start_ai_screen(scrape_id)
        snapshot = self._poll_terminal(task1)

        self.assertEqual(snapshot["status"], "failed", snapshot)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(sleeps), 2)
        run = self.store.get_screening_run(task1)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "result_round_save_failed")
        self.assertIn("点继续可重试保存", snapshot["error"])
        events = [e["type"] for e in self.store.list_task_events(task1)]
        self.assertIn("result_round_save_failed", events)
        self.assertEqual(self.store.list_history_rounds(), [])

        # 第二轮：解除写轮故障后续跑，轮询到 succeeded、结果轮重建。
        # 判定继承自链上第一轮（match），不重筛不重判。
        if self._save_patch is not None:
            self._save_patch.stop()
        self._patch_boundaries(match_ids={"j0", "j1", "j2"})
        task2 = self._start_ai_screen(scrape_id)
        snapshot2 = self._poll_terminal(task2)

        self.assertEqual(snapshot2["status"], "completed", snapshot2)
        run2 = self.store.get_screening_run(task2)
        self.assertEqual(run2["status"], "succeeded")
        self.assertEqual(run2["match_count"], 3)
        self.assertTrue(all(c == [] for c in self._trackers["screen_calls"]),
                        self._trackers["screen_calls"])
        self.assertTrue(all(c == [] for c in self._trackers["match_calls"]),
                        self._trackers["match_calls"])
        rounds = self.store.list_history_rounds()
        self.assertEqual(len(rounds), 1)
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(len(history["items"]), 1)
