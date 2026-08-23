"""019 T004/T006/T007/T011/T012/T014：跨平台去重单测与集成用例。

纯逻辑用例用内存 FakeStore；集成用例走 create_app + /api/ai-screen
真实链路（mock AI/抓取），断言判定落库、粗筛输入、进度与台账三处对账。
"""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from webui.app import create_app
from webui.cross_platform_dedupe import (
    DEDUPE_WINDOW_DAYS,
    EXTRA_KEY,
    DedupeOutcome,
    apply_to_screening_input,
    collect_other_platform_jobs,
    split_cross_platform_duplicates,
)

_CST_NOW = datetime.fromisoformat("2026-08-23T12:00:00+08:00")


class _FakeStore:
    """只读判定源桩：platform → 轮列表；run_id → 结果载荷。"""

    def __init__(self, rounds_by_platform=None):
        self.rounds_by_platform = rounds_by_platform or {}
        self.payloads: dict[str, dict] = {}
        for rounds in self.rounds_by_platform.values():
            for run in rounds:
                self.payloads[str(run["id"])] = {
                    "result": {"jobs": run.get("_jobs") or [],
                               "dropped": run.get("_dropped") or []},
                }

    def list_history_rounds(self, platform=None):
        return list(self.rounds_by_platform.get(platform, []))

    def load_latest_pipeline_result(self, run_id=None):
        return self.payloads.get(str(run_id))


def _round(run_id, status="done", finished_at=None, jobs=None, dropped=None,
           platform="boss", profile_summary="画像A"):
    return {
        "id": run_id, "platform": platform, "status": status,
        "finished_at": finished_at
        or _CST_NOW.isoformat(),
        "created_at": finished_at or _CST_NOW.isoformat(),
        "profile_summary": profile_summary,
        "_jobs": jobs or [], "_dropped": dropped or [],
    }


def _job(job_id, *, company="字节跳动", title="Python开发", location="北京",
         platform="boss", salary="20-30K", url=None):
    return {
        "job_id": job_id, "platform_job_id": job_id, "platform": platform,
        "company": company, "title": title, "location": location,
        "salary": salary, "source_url": url or f"https://e.example/{job_id}",
    }


class SplitDuplicatesTests(unittest.TestCase):
    def _others(self):
        return collect_other_platform_jobs(
            _FakeStore({"boss": [_round("r1", jobs=[
                _job("boss-x", company="北京字节跳动科技有限公司",
                     title="Python开发", location="北京·朝阳区"),
            ])]}),
            "zhilian", now=_CST_NOW)

    def test_equivalent_job_dropped_with_reason_and_extra(self):
        raw = [_job("zl-1", platform="zhilian", company="字节跳动",
                    title="python 开发", location="北京市")]
        outcome = split_cross_platform_duplicates(
            raw, self._others(), "zhilian")
        self.assertEqual(outcome.kept_jobs, [])
        self.assertEqual(outcome.deduped_count, 1)
        entry = outcome.dropped_entries[0]
        self.assertEqual(entry["job_id"], "zl-1")
        self.assertIn("跨平台重复", entry["reason"])
        self.assertIn("BOSS", entry["reason"])  # 对端平台名可见
        dup_of = entry["extra"][EXTRA_KEY]
        self.assertEqual(dup_of["platform"], "boss")
        self.assertEqual(dup_of["platform_job_id"], "boss-x")
        self.assertEqual(dup_of["source_url"], "https://e.example/boss-x")
        self.assertTrue(dup_of["finished_at"])
        self.assertEqual(
            outcome.dup_verdicts["zl-1"],
            {"verdict": "dropped", "reason": entry["reason"]})

    def test_no_other_platform_no_drop(self):
        raw = [_job("zl-1", platform="zhilian")]
        outcome = split_cross_platform_duplicates(raw, [], "zhilian")
        self.assertEqual(outcome.kept_jobs, raw)
        self.assertEqual(outcome.dropped_entries, [])

    def test_other_platform_dropped_only_round_not_used(self):
        """对端全是剔除行（无非剔除岗位）→ 不作判定源。"""
        store = _FakeStore({"boss": [_round(
            "r1", jobs=[],
            dropped=[{"job_id": "boss-x", "reason": "粗筛移除"}])]})
        others = collect_other_platform_jobs(store, "zhilian", now=_CST_NOW)
        self.assertEqual(others, [])
        outcome = split_cross_platform_duplicates(
            [_job("zl-1", platform="zhilian")], others, "zhilian")
        self.assertEqual(outcome.deduped_count, 0)

    def test_same_job_in_multiple_rounds_traces_latest(self):
        """同岗位对端多轮：首个命中定身份，追溯取最近包含轮。"""
        older = _round("r-old", finished_at="2026-08-01T10:00:00+08:00",
                       jobs=[_job("boss-x", url="https://e.example/old")])
        newer = _round("r-new", finished_at="2026-08-22T10:00:00+08:00",
                       jobs=[_job("boss-x", url="https://e.example/new")])
        others = collect_other_platform_jobs(
            _FakeStore({"boss": [newer, older]}),  # 列表新→旧
            "zhilian", now=_CST_NOW)
        outcome = split_cross_platform_duplicates(
            [_job("zl-1", platform="zhilian")], others, "zhilian")
        dup_of = outcome.dropped_entries[0]["extra"][EXTRA_KEY]
        self.assertEqual(dup_of["platform_job_id"], "boss-x")
        self.assertEqual(dup_of["source_url"], "https://e.example/old")
        self.assertEqual(dup_of["finished_at"],
                         "2026-08-22T10:00:00+08:00")

    def test_fingerprintless_job_kept(self):
        raw = [_job("zl-1", platform="zhilian", company="", location="北京")]
        outcome = split_cross_platform_duplicates(raw, self._others(), "zhilian")
        self.assertEqual(outcome.kept_jobs, raw)

    def test_no_false_merge_different_triple(self):
        others = self._others()
        for mutated in (
            {"company": "字节跳动", "title": "Python开发工程师",
             "location": "北京"},
            {"company": "字节跳动", "title": "Python开发", "location": "上海"},
            {"company": "飞书", "title": "Python开发", "location": "北京"},
        ):
            raw = [_job("zl-x", platform="zhilian", **mutated)]
            outcome = split_cross_platform_duplicates(raw, others, "zhilian")
            self.assertEqual(outcome.deduped_count, 0, mutated)


class ApplySwitchTests(unittest.TestCase):
    def test_disabled_passes_through(self):
        """开关关闭：直通，零剔除、零台账、零进度报数。"""
        raw = [_job("zl-1", platform="zhilian")]
        outcome = apply_to_screening_input(
            _FakeStore({"boss": [_round("r1", jobs=[_job("boss-x")])]}),
            raw, "zhilian", enabled=False, now=_CST_NOW)
        self.assertEqual(outcome.kept_jobs, raw)
        self.assertEqual(outcome.dropped_entries, [])
        self.assertEqual(outcome.dup_verdicts, {})
        self.assertIsNone(outcome.progress_message)
        self.assertIsNone(outcome.ledger_payload())

    def test_enabled_collects_and_splits(self):
        raw = [_job("zl-1", platform="zhilian", company="字节跳动",
                    title="python 开发", location="北京")]
        outcome = apply_to_screening_input(
            _FakeStore({"boss": [_round("r1", jobs=[
                _job("boss-x", company="北京字节跳动科技有限公司")])]}),
            raw, "zhilian", now=_CST_NOW)
        self.assertEqual(outcome.deduped_count, 1)
        self.assertEqual(outcome.total_scraped, 1)


class CollectWindowTests(unittest.TestCase):
    def test_round_over_window_excluded(self):
        old_finished = (
            _CST_NOW - timedelta(days=DEDUPE_WINDOW_DAYS + 1)
        ).isoformat()
        store = _FakeStore({"boss": [_round(
            "r-old", finished_at=old_finished, jobs=[_job("boss-x")])]})
        self.assertEqual(
            collect_other_platform_jobs(store, "zhilian", now=_CST_NOW), [])

    def test_round_within_window_included_boundary(self):
        edge_finished = (
            _CST_NOW - timedelta(days=DEDUPE_WINDOW_DAYS - 1)
        ).isoformat()
        store = _FakeStore({"boss": [_round(
            "r-edge", finished_at=edge_finished, jobs=[_job("boss-x")])]})
        others = collect_other_platform_jobs(store, "zhilian", now=_CST_NOW)
        self.assertEqual(len(others), 1)
        self.assertEqual(others[0].platform, "boss")

    def test_invisible_status_round_excluded(self):
        store = _FakeStore({"boss": [
            _round("r-failed", status="failed", jobs=[_job("boss-x")]),
            _round("r-partial", status="partial", jobs=[_job("boss-y")]),
            _round("r-scraped", status="scraped_only", jobs=[_job("boss-z")]),
        ]})
        others = collect_other_platform_jobs(store, "zhilian", now=_CST_NOW)
        self.assertEqual(
            {item.job["job_id"] for item in others}, {"boss-y", "boss-z"})

    def test_same_platform_rounds_not_collected(self):
        store = _FakeStore({"zhilian": [_round(
            "r-self", platform="zhilian", jobs=[_job("zl-x")])]})
        self.assertEqual(
            collect_other_platform_jobs(store, "zhilian", now=_CST_NOW), [])


class OutcomeVisibilityTests(unittest.TestCase):
    def test_zero_dedupe_silent(self):
        outcome = DedupeOutcome(kept_jobs=[{"job_id": "a"}], total_scraped=1)
        self.assertIsNone(outcome.progress_message)
        self.assertIsNone(outcome.ledger_payload())

    def test_ledger_payload_structure(self):
        outcome = apply_to_screening_input(
            _FakeStore({"boss": [_round("r1", jobs=[
                _job("boss-x", company="北京字节跳动科技有限公司")])]}),
            [_job("zl-1", platform="zhilian", title="python 开发",
                  location="北京"), _job("zl-2", platform="zhilian",
                                         company="别的公司")],
            "zhilian", now=_CST_NOW)
        payload = outcome.ledger_payload()
        self.assertEqual(payload["counts"], {"scraped": 2, "deduped": 1})
        self.assertEqual(payload["dropped"][0]["job_id"], "zl-1")
        self.assertEqual(
            payload["dropped"][0]["dup_of"]["platform_job_id"], "boss-x")
        self.assertIn("跨平台重复", outcome.progress_message)
        self.assertIn("2 条中 1 条", outcome.progress_message)


# ---------------------------------------------------------------------------
# 集成用例（T006/T007/T011/T012/T014）：create_app + /api/ai-screen 真实链路
# ---------------------------------------------------------------------------

def _make_app():
    import pathlib
    import sys
    import tempfile
    temp = tempfile.TemporaryDirectory()
    root = pathlib.Path(temp.name)
    app = create_app({
        "TESTING": True,
        "START_TASKS": False,
        "RESULT_DIR": str(root / "results"),
        "DB_PATH": str(root / "state" / "webui.db"),
        "PYTHON_EXECUTABLE": sys.executable,
    })
    return app, temp


def _wait_for_pipeline_task(client, task_id, timeout=8.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/search-progress/{task_id}")
        if response.status_code == 200:
            last = response.get_json()
            if last.get("status") not in ("queued", "running"):
                return last
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not stop; last={last}")


def _boss_kept_job(job_id, *, company="北京字节跳动科技有限公司",
                   title="Python开发", location="北京·朝阳区"):
    return {
        "job_id": job_id, "platform_job_id": job_id, "platform": "boss",
        "company": company, "title": title, "location": location,
        "salary": "25-40K", "source_url": f"https://e.example/{job_id}",
        "verdict": "match", "verdict_reason": "匹配",
    }


def _zl_job(job_id, *, company="字节跳动", title="python 开发",
            location="北京市"):
    return {
        "job_id": job_id, "platform_job_id": job_id,
        "company": company, "title": title, "location": location,
        "salary": "20-35K", "source_url": f"https://e.example/{job_id}",
    }


class CrossPlatformDedupeIntegrationTests(unittest.TestCase):
    """T006：后跑平台筛选输入组装处的跨平台剔除（US1 主链路）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.app.config["API_TOKEN"]
        self.headers = {"X-Boss-Token": self.app.config["API_TOKEN"]}
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        executor = self.app.config.get("PIPELINE_EXECUTOR")
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    # -- 构造 --------------------------------------------------------------

    def _save_boss_round(self, jobs, *, days_ago=1.0, dropped=None):
        finished_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp()
            * 1000)
        return self.store.save_pipeline_result({
            "jobs": jobs, "dropped": dropped or [],
            "total_scraped": len(jobs), "total_kept": len(jobs),
            "total_matched": 0, "total_dropped": 0,
        }, {"platform": "boss"}, finished_at=finished_ms)

    def _install_zhilian_source(self, scrape_task_id, jobs):
        from webui.execution_config import (
            ExecutionConfigSnapshot,
            normalize_scope,
        )
        state = self.store.get_advanced_config_state()
        config = ExecutionConfigSnapshot.create(state["last_custom_config"])
        scope = normalize_scope(
            keywords=["后端"], scope_kind="cities", cities=["北京"],
            pages_per_combination=1,
        )
        self.app.config["PIPELINE_TASKS"][scrape_task_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "platform": "zhilian",
            "result": {
                "ok": True, "jobs": [dict(job) for job in jobs], "dropped": [],
                "total_scraped": len(jobs), "total_matched": len(jobs),
                "completed_combos": ["后端|北京"], "error": "",
            },
            "error": "", "stop_event": threading.Event(),
            "started_at": 1, "finished_at": 2,
            "config_digest": config.config_digest,
            "scope_digest": scope.scope_digest,
        }
        self.store.create_screening_run(
            scrape_task_id,
            frozen_filters={"keyword": "后端"},
            source_count=len(jobs),
            execution_params={
                "platform": "zhilian",
                "script_params": {"keyword": "后端", "city": ["北京"], "pages": 1},
                "execution_config": config.to_dict(),
                "frozen_scope": scope.to_dict(),
                "browser_account": "a",
                "cdp_port": 9223,
                "profile_key": "zhilian:a",
            },
            backend_version="test",
        )
        self.store.update_screening_run(scrape_task_id, status="running")
        self.store.update_screening_run(scrape_task_id, status="succeeded")
        # 重启恢复路径依赖 DB 抓取快照重建输入（T012）。
        self.store.save_scrape_combo_result(
            scrape_task_id, "后端|北京", jobs, ["后端|北京"])
        self.store.save_ai_settings(
            "http://example.invalid", "test-ref", status="ready")

    def _run_zhilian_screen(self, scrape_task_id, *, extra_body=None,
                            screen_side_effect=None):
        """跑一轮智联筛选；返回 (最终快照, 粗筛实际收到的 job_id 列表)。"""
        seen: list[str] = []

        def fake_screen(jobs, *args, **kwargs):
            seen.extend(str(j.get("job_id")) for j in jobs)
            return {"kept": [str(j.get("job_id")) for j in jobs],
                    "dropped": [], "verdicts": {}}

        body = {
            "screening_fields": {"keyword": "后端"},
            "profile_summary": "后端工程师",
            "scrape_task_id": scrape_task_id,
        }
        body.update(extra_body or {})
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs",
                           side_effect=screen_side_effect or fake_screen), \
                mock.patch("webui.ai.match_jds", side_effect=lambda chunk, *a, **k: {
                    "verdicts": {
                        str(job["job_id"]): {
                            "verdict": "match", "reason": "匹配", "caveats": [],
                        } for job in chunk
                    }}), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.source.ZhilianCdpSource",
                           return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details",
                           side_effect=lambda chunk, *a, **k: {
                               "jobs": [{**job, "jd": "职责描述"}
                                        for job in chunk],
                               "hard_stop": False, "hard_stop_code": None,
                               "stopped": False, "fetched": len(chunk),
                           }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self.client.post(
                "/api/ai-screen", json=body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)
        return finished, seen, task_id

    def _continue_paused(self, run_id, seen):
        """续跑一个 paused 筛选 run；返回最终快照。"""
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=(
                    lambda jobs, *a, **k: (
                        seen.extend(str(j.get("job_id")) for j in jobs),
                        {"kept": [str(j.get("job_id")) for j in jobs],
                         "dropped": [], "verdicts": {}},)[1])), \
                mock.patch("webui.ai.match_jds", side_effect=lambda chunk, *a, **k: {
                    "verdicts": {
                        str(job["job_id"]): {
                            "verdict": "match", "reason": "匹配", "caveats": [],
                        } for job in chunk
                    }}), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.source.ZhilianCdpSource",
                           return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details",
                           side_effect=lambda chunk, *a, **k: {
                               "jobs": [{**job, "jd": "职责描述"}
                                        for job in chunk],
                               "hard_stop": False, "hard_stop_code": None,
                               "stopped": False, "fetched": len(chunk),
                           }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self.client.post(
                f"/api/task/continue/{run_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            return _wait_for_pipeline_task(self.client, run_id)

    # -- 用例 --------------------------------------------------------------

    def test_dup_job_excluded_from_ai_and_persisted_with_ledger(self):
        """等价岗剔除：粗筛输入不含、判定/断点落库、三处数字互洽（T006）。"""
        old_round = self._save_boss_round(
            [_boss_kept_job("boss-x")], days_ago=10)
        self._save_boss_round(
            [_boss_kept_job("boss-other", title="前端开发")], days_ago=1)
        self._install_zhilian_source(
            "zl-src", [_zl_job("zl-dup"), _zl_job("zl-keep", company="别的公司")])

        finished, seen, task_id = self._run_zhilian_screen("zl-src")

        self.assertEqual(finished["status"], "completed", finished.get("error"))
        self.assertEqual(seen, ["zl-keep"])  # AI 粗筛清单不含重复岗
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["total_scraped"], 2)  # 抓取计数含重复
        self.assertEqual(run["total_dropped"], 1)
        verdicts = self.store.load_screening_verdicts(task_id)
        self.assertIn("跨平台重复", verdicts["zl-dup"]["reason"])
        self.assertIn("BOSS", verdicts["zl-dup"]["reason"])
        checkpoint = set(self.store.load_checkpoint(task_id, "ai_rough"))
        self.assertEqual(checkpoint, {"zl-dup", "zl-keep"})
        # 进度报数与完成文案三数对账
        self.assertTrue(any(
            "2 条中 1 条跨平台重复" in str(line)
            for line in finished["logs"]), finished["logs"])
        self.assertIn("抓取 2，跨平台重复 1，实际筛选 1",
                      str(finished["progress"].get("message", ""))
                      + str(finished["result"].get("error", ""))
                      + "".join(finished["logs"]))
        # 台账事件一条且数字互洽
        ledger = [e for e in self.store.list_task_events(task_id)
                  if e["type"] == "cross_platform_dedup"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["payload"]["counts"],
                         {"scraped": 2, "deduped": 1})
        self.assertEqual(
            ledger[0]["payload"]["dropped"][0]["dup_of"]["platform_job_id"],
            "boss-x")
        # T007：轮落库后 dropped 行 extra 追溯完整，dup_of 指向最近包含轮
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        round_dropped = payload["result"]["dropped"]
        self.assertEqual(len(round_dropped), 1)
        entry = round_dropped[0]
        self.assertEqual(entry["platform_job_id"], "zl-dup")
        self.assertIn("跨平台重复", entry["reason"])
        dup_of = entry["extra"][EXTRA_KEY]
        self.assertEqual(dup_of["platform"], "boss")
        self.assertEqual(dup_of["platform_job_id"], "boss-x")
        self.assertEqual(dup_of["source_url"], "https://e.example/boss-x")
        old_row = next(r for r in self.store.list_history_rounds("boss")
                       if r["id"] == old_round)
        self.assertEqual(dup_of["finished_at"], old_row["finished_at"])

    def test_all_dup_input_completes_with_empty_stage_a(self):
        """粗筛输入全为重复岗：Stage A 空输入守卫，正常完成并成轮。"""
        self._save_boss_round([_boss_kept_job("boss-x")], days_ago=1)
        self._install_zhilian_source("zl-src", [_zl_job("zl-dup")])

        finished, seen, task_id = self._run_zhilian_screen("zl-src")

        self.assertEqual(seen, [])
        self.assertEqual(finished["status"], "completed", finished.get("error"))
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["total_scraped"], 1)
        self.assertEqual(run["total_dropped"], 1)
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        self.assertEqual(len(payload["result"]["dropped"]), 1)

    def test_round_beyond_window_not_compared(self):
        """超 30 天的对端轮不参与比对（岗位照常进入筛选）。"""
        self._save_boss_round(
            [_boss_kept_job("boss-x")],
            days_ago=DEDUPE_WINDOW_DAYS + 5)
        self._install_zhilian_source("zl-src", [_zl_job("zl-dup")])

        finished, seen, _task_id = self._run_zhilian_screen("zl-src")

        self.assertEqual(seen, ["zl-dup"])
        self.assertEqual(finished["status"], "completed")
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        self.assertEqual(payload["result"]["total_dropped"], 0)

    def test_switch_false_keeps_current_behavior(self):
        """开关关闭：零剔除、无台账事件、行为与现状一致。"""
        self._save_boss_round([_boss_kept_job("boss-x")], days_ago=1)
        self._install_zhilian_source("zl-src", [_zl_job("zl-dup")])

        finished, seen, task_id = self._run_zhilian_screen(
            "zl-src", extra_body={"cross_platform_dedupe": False})

        self.assertEqual(seen, ["zl-dup"])
        self.assertEqual(finished["status"], "completed")
        self.assertFalse(any(
            "跨平台重复" in str(line) for line in finished["logs"]))
        self.assertEqual([
            e for e in self.store.list_task_events(task_id)
            if e["type"] == "cross_platform_dedup"], [])
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        self.assertEqual(payload["result"]["total_dropped"], 0)

    def test_no_other_platform_rounds_behaves_as_before(self):
        """对端无可见轮：不做跨平台剔除（US1 场景 8）。"""
        self._install_zhilian_source("zl-src", [_zl_job("zl-dup")])
        finished, seen, _task_id = self._run_zhilian_screen("zl-src")
        self.assertEqual(seen, ["zl-dup"])
        self.assertEqual(finished["status"], "completed")

    # -- T011：中断续跑后口径一致（US3） -----------------------------------

    def _pause_at_rough(self, scrape_task_id):
        """跑一轮在粗筛遇限流暂停的智联筛选；返回 (task_id, 最终快照)。"""
        from webui.ai import AISecurityError, ERROR_RATE_LIMIT

        def rate_limited(jobs, *args, **kwargs):
            raise AISecurityError(ERROR_RATE_LIMIT)

        finished, _seen, task_id = self._run_zhilian_screen(
            scrape_task_id, screen_side_effect=rate_limited)
        self.assertEqual(finished["status"], "paused", finished)
        return task_id

    def test_pause_resume_does_not_revive_dup_and_stays_consistent(self):
        """续跑：重复岗不复活、最终轮 reason/extra 不变、计数自洽、无幽灵轮。"""
        self._save_boss_round([_boss_kept_job("boss-x")], days_ago=1)
        self._install_zhilian_source(
            "zl-src", [_zl_job("zl-dup"), _zl_job("zl-keep", company="别的公司")])

        task_id = self._pause_at_rough("zl-src")
        # 暂停时跨平台剔除判定已持久（断点可被暂停路径重写为粗筛进度，
        # 但续跑在组装点确定性重算并重新并入——见下方恢复后断点断言）。
        verdicts = self.store.load_screening_verdicts(task_id)
        self.assertIn("跨平台重复", verdicts["zl-dup"]["reason"])

        seen: list[str] = []
        finished = self._continue_paused(task_id, seen)

        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(seen, ["zl-keep"])  # 重复岗不复活、不重复计数
        checkpoint = set(self.store.load_checkpoint(task_id, "ai_rough"))
        self.assertEqual(checkpoint, {"zl-dup", "zl-keep"})
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["total_scraped"], 2)
        self.assertEqual(run["total_dropped"], 1)
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        entry = payload["result"]["dropped"][0]
        self.assertEqual(entry["platform_job_id"], "zl-dup")
        self.assertIn("跨平台重复", entry["reason"])
        self.assertEqual(entry["extra"][EXTRA_KEY]["platform_job_id"], "boss-x")
        # 018 收尾契约：无幽灵轮（一条流程一条轮）
        zhilian_rounds = [
            r for r in self.store.list_history_rounds("zhilian")
            if r["status"] in ("done", "partial", "scraped_only")
            and str((r.get("execution_params") or {}).get("platform")
                    or r.get("platform") or "") == "zhilian"
        ]
        self.assertEqual(len(zhilian_rounds), 1)

    def test_switch_false_round_stays_dedup_free_after_resume(self):
        """开关关闭的轮次续跑后仍不剔除（冻结沿用）。"""
        self._save_boss_round([_boss_kept_job("boss-x")], days_ago=1)
        self._install_zhilian_source("zl-src", [_zl_job("zl-dup")])

        from webui.ai import AISecurityError, ERROR_RATE_LIMIT
        finished, _seen, task_id = self._run_zhilian_screen(
            "zl-src", extra_body={"cross_platform_dedupe": False},
            screen_side_effect=lambda *a, **k: (
                (_ for _ in ()).throw(AISecurityError(ERROR_RATE_LIMIT))))
        self.assertEqual(finished["status"], "paused")

        seen: list[str] = []
        resumed = self._continue_paused(task_id, seen)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(seen, ["zl-dup"])  # 冻结为关闭：照常进 AI
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        self.assertEqual(payload["result"]["total_dropped"], 0)

    # -- T012：重启恢复路径（US3） ----------------------------------------

    def test_restart_recovery_replays_dedup_identically(self):
        """服务重启（interrupted + DB 快照重建输入）后按同一规则重放去重。"""
        self._save_boss_round([_boss_kept_job("boss-x")], days_ago=1)
        self._install_zhilian_source(
            "zl-src", [_zl_job("zl-dup"), _zl_job("zl-keep", company="别的公司")])

        old_task_id = self._pause_at_rough("zl-src")
        # 模拟服务重启：paused → interrupted(restart)，内存任务全部消失
        self.store.update_screening_run(
            old_task_id, status="interrupted", error_code="restart")
        self.app.config["PIPELINE_TASKS"].clear()

        seen: list[str] = []
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=(
                    lambda jobs, *a, **k: (
                        seen.extend(str(j.get("job_id")) for j in jobs),
                        {"kept": [str(j.get("job_id")) for j in jobs],
                         "dropped": [], "verdicts": {}},)[1])), \
                mock.patch("webui.ai.match_jds", side_effect=lambda chunk, *a, **k: {
                    "verdicts": {
                        str(job["job_id"]): {
                            "verdict": "match", "reason": "匹配", "caveats": [],
                        } for job in chunk
                    }}), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.source.ZhilianCdpSource",
                           return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details",
                           side_effect=lambda chunk, *a, **k: {
                               "jobs": [{**job, "jd": "职责描述"}
                                        for job in chunk],
                               "hard_stop": False, "hard_stop_code": None,
                               "stopped": False, "fetched": len(chunk),
                           }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self.client.post("/api/ai-screen", json={
                "screening_fields": {"keyword": "后端"},
                "profile_summary": "后端工程师",
                "scrape_task_id": "zl-src",
            }, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertTrue(response.get_json()["resuming"])
            new_task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, new_task_id)

        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(seen, ["zl-keep"])  # 与一次跑完一致：重复岗不进 AI
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        entry = payload["result"]["dropped"][0]
        self.assertEqual(entry["platform_job_id"], "zl-dup")
        self.assertIn("跨平台重复", entry["reason"])
        self.assertEqual(entry["extra"][EXTRA_KEY]["platform_job_id"], "boss-x")
        self.assertEqual(payload["result"]["total_scraped"], 2)
        self.assertEqual(payload["result"]["total_dropped"], 1)


class ProfileFilterTests(unittest.TestCase):
    """T014（US4）：画像过滤逐轮生效，不跨画像串台（research R4）。"""

    def _collect(self, rounds, current_summary):
        return collect_other_platform_jobs(
            _FakeStore({"boss": rounds}), "zhilian",
            current_summary, now=_CST_NOW)

    def test_mismatched_profile_round_skipped_matching_round_kept(self):
        """画像不符的轮跳过、相符的轮照常参与（逐轮判断）。"""
        rounds = [
            _round("r-a", profile_summary="画像A", jobs=[_job("boss-a")]),
            _round("r-b", profile_summary="画像B", jobs=[_job("boss-b")]),
        ]
        others = self._collect(rounds, "画像A")
        self.assertEqual(
            {item.job["job_id"] for item in others}, {"boss-a"})

    def test_identical_nonempty_summary_dedupes_normally(self):
        rounds = [_round("r-a", profile_summary="画像A", jobs=[_job("boss-a")])]
        others = self._collect(rounds, "画像A")
        self.assertEqual(len(others), 1)

    def test_either_side_empty_summary_not_filtered(self):
        """任一侧画像摘要为空 → 不过滤，照常参与去重。"""
        rounds = [
            _round("r-empty", profile_summary="", jobs=[_job("boss-e")]),
            _round("r-set", profile_summary="画像B", jobs=[_job("boss-b")]),
        ]
        ids_empty_current = {i.job["job_id"] for i in self._collect(
            rounds, "")}
        self.assertEqual(ids_empty_current, {"boss-e", "boss-b"})
        ids_empty_round = {i.job["job_id"] for i in self._collect(
            [_round("r-empty", profile_summary="", jobs=[_job("boss-e")])],
            "画像A")}
        self.assertEqual(ids_empty_round, {"boss-e"})

    def test_scraped_only_round_is_valid_source(self):
        """scraped_only 轮可作判定源（岗位身份与是否筛选无关）。"""
        rounds = [_round("r-so", status="scraped_only", jobs=[_job("boss-so")])]
        others = self._collect(rounds, "画像A")
        self.assertEqual([i.job["job_id"] for i in others], ["boss-so"])

    def test_window_and_profile_combined(self):
        """30 天窗与画像过滤组合：窗外的相符轮、窗内的不符轮都不参与。"""
        stale = _CST_NOW - timedelta(days=DEDUPE_WINDOW_DAYS + 3)
        rounds = [
            _round("r-old-ok", finished_at=stale.isoformat(),
                   profile_summary="画像A", jobs=[_job("boss-old")]),
            _round("r-new-miss", profile_summary="画像B",
                   jobs=[_job("boss-miss")]),
            _round("r-new-ok", profile_summary="画像A",
                   jobs=[_job("boss-ok")]),
        ]
        others = self._collect(rounds, "画像A")
        self.assertEqual([i.job["job_id"] for i in others], ["boss-ok"])


if __name__ == "__main__":
    unittest.main()
