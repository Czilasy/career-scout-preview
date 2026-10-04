"""B094 召回链路自动化（T011、T019、T027 失败用例）。

从真实 runner → 真实粗筛段 → 真实 JD 段 → 真实精筛函数一路跑到模型网络
边界；列表岗位、详情抓取与模型调用是明确假件/桩，数据库是临时 TaskStore。
这些用例只证明条件传递、事实可达与恢复契约，不能证明真实模型语义判断。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.ai.test_ai_domain_context import (
    BOSS_GAME_CODE,
    BOSS_INTERNET_CODE,
    GAME_GROUP,
    INTERNET_GROUP,
    snapshot,
)
from webui import ai_domain_policy as policy
from webui.execution_config import ExecutionConfigSnapshot, FrozenTaskScope
from webui.flow_service import FlowService
from webui.runners.ai_screen_task import run_ai_screen_task
from webui.store import TaskStore

TAIL_EVIDENCE = "公司主营业务：自研大模型推理平台与 AI 训练数据集产品"
HEAD_FILLER = "岗位职责：负责日常事务推进与跨部门协作。" * 80


def _domain_job(job_id, **overrides):
    job = {
        "job_id": job_id, "platform_job_id": job_id, "platform": "boss",
        "title": "大客户销售", "salary": "15-25K", "location": "上海",
        "company": "示例科技", "company_industry": "教育培训",
        "company_scale": "100-499人", "job_labels": "1-3年 | 本科",
    }
    job.update(overrides)
    return job


class DomainRecallHarness(unittest.TestCase):
    """共用装配：一份 Track 快照 + 一条抓取源 + 一个可复用的运行入口。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b094-recall-")
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")
        self.profile_id = "b094-recall-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'b094', '{}', '{}', '2026-10-02', '2026-10-02')",
                (self.profile_id,),
            )
        self._seq = 0

    def tearDown(self):
        self.temp.cleanup()

    # ------------------------------------------------------------------ 装配
    def _track_snapshot(self, *, groups, platform_labels):
        return snapshot(
            unified_industry=list(groups), boss_industry=list(platform_labels),
        )

    def _new_source(self, *, jobs, screening_fields, track_snapshot=None,
                    industry_override=None):
        """建立抓取源 + Track（含条件快照），返回 (scrape_id, flow)。"""
        self._seq += 1
        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="boss",
            start_key=f"b094-recall-{self._seq}",
            confirmed_filters={"boss": track_snapshot or {}},
        )
        track = flow["tracks"][0]
        scrape_id = f"recall-{self._seq}-scrape"
        config = ExecutionConfigSnapshot({
            "inter_combo_delay": 0, "detail_batch_size": 5, "detail_interval": 0,
            "detail_reset_every": 1, "detail_batch_cooldown": 0,
            "detail_tab_pool_size": 1, "screen_batch_size": 10,
            "screen_concurrency": 1, "match_batch_size": 4, "match_concurrency": 1,
        })
        scope = FrozenTaskScope(
            keywords=["Python"], scope_kind="cities", cities=["上海"],
            pages_per_combination=1, combination_count=1, planned_pages=1,
            task_size="small", platform="boss",
        )
        self.store.create_screening_run(
            scrape_id, profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "execution_config": config.to_dict(), "frozen_scope": scope.to_dict(),
                "script_params": {"keyword": "Python", "city": ["上海"]},
            },
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile_id, platform="boss", flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id, scrape_run_id=scrape_id,
        )
        self.store.update_screening_run(scrape_id, status="running")
        self.store.update_screening_run(scrape_id, status="succeeded")
        FlowService(self.store).mark_scrape_complete(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
        )
        tasks = {
            scrape_id: {
                "kind": "scrape", "status": "done", "platform": "boss",
                "profile_id": self.profile_id,
                "result": {
                    "ok": True, "jobs": [dict(job) for job in jobs],
                    "dropped": [], "total_scraped": len(jobs), "combinations": 1,
                },
            },
        }
        ctx = self._context(scrape_id, tasks)
        fields = dict(screening_fields)
        if industry_override is not None:
            fields["industry"] = industry_override
        return ctx, flow, scrape_id, fields

    def _context(self, scrape_id, tasks):
        app = SimpleNamespace(config={"RESULT_DIR": str(self.root)})

        def jd_path_for(result_dir, run_id):
            return str(pathlib.Path(result_dir) / f"jd-{run_id}.json")

        ctx = SimpleNamespace(
            app=app,
            store=self.store,
            flow_service=FlowService(self.store),
            tasks=tasks,
            lock=threading.RLock(),
            operational_errors=(Exception,),
            backend_version="test",
            event_stage_names={},
            screen_stage_messages={},
            activate_task_browser=lambda _task_id: None,
            is_user_finished=lambda _task_id: False,
            account_for_run=lambda *_args: "account-a",
            ensure_scrape_source=lambda task_id: tasks.get(task_id),
            write_run=lambda run_id, **kwargs: self.store.update_screening_run(run_id, **kwargs),
            clear_auto_screen=lambda _task_id: None,
            schedule_pipeline_task_cleanup=lambda _task_id: None,
            release_worker_resume_claims=lambda _task: None,
            screen_overall_percent=lambda *_args: 0,
            record_pause_failure=lambda *_args, **_kwargs: None,
            persist_jd_job_failures=lambda *_args, **_kwargs: None,
            make_cdp_source=lambda **_kwargs: object(),
            jd_checkpoint_path=jd_path_for,
            load_jd_checkpoint=self._load_jd_file,
            save_jd_checkpoint=self._save_jd_file,
            remove_jd_checkpoint=self._remove_jd_file,
            msg_user_finished="已结束",
            msg_user_stopped_screen="已停止",
        )
        self.store.get_ai_settings = lambda: {
            "endpoint_url": "https://ai.test", "model": "test-model",
        }
        self.store.get_credential_ref = lambda: None
        return ctx

    @staticmethod
    def _load_jd_file(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _save_jd_file(path, value):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)

    @staticmethod
    def _remove_jd_file(path):
        try:
            pathlib.Path(path).unlink()
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------ 运行
    def _run(self, ctx, ai_run_id, scrape_id, screening_fields, *,
             resume_from_run_id="", detail_jobs=None,
             profile_summary="3 年 Python 后端，期望上海"):
        """真实三段流程；仅列表岗位、详情抓取与模型是边界假件/桩。"""
        calls = []

        def _fine_batch_size(payload):
            try:
                return len(json.loads(payload["user"]))
            except (TypeError, ValueError):
                return 0

        def fake_call_ai(endpoint_url, api_key, messages, **kwargs):
            payload = {
                "system": messages[0]["content"],
                "user": messages[1]["content"],
                "stage": kwargs.get("measurement_stage") or "",
            }
            calls.append(payload)
            if payload["stage"] == "rough":
                return {"dropped": []}
            return {
                "results": [
                    {"i": index, "match": True, "reason": "领域相关",
                     "caveats": [], "flags": []}
                    for index in range(_fine_batch_size(payload))
                ]
            }

        detail_calls = []

        def fake_fetch_job_details(chunk, source, **kwargs):
            detail_calls.append([str(job.get("job_id")) for job in chunk])
            provided = detail_jobs if detail_jobs is not None else {}
            jobs = []
            for job in chunk:
                entry = dict(job)
                entry["jd"] = provided.get(
                    str(job.get("job_id")), "负责大模型推理平台的后端开发")
                jobs.append(entry)
            return {"jobs": jobs}

        chrome = {"ready": 0, "closed": 0}

        def fake_ensure_chrome_ready(*_args, **_kwargs):
            chrome["ready"] += 1
            return True, ""

        def fake_close_debug_chrome(*_args, **_kwargs):
            chrome["closed"] += 1

        ctx.tasks.setdefault(ai_run_id, {
            "kind": "ai_screen", "status": "queued", "platform": "boss",
            "profile_id": self.profile_id, "source_task_id": scrape_id,
            "logs": [], "progress": {}, "error": "", "stop_event": threading.Event(),
        })
        with mock.patch("webui.ai.call_ai", side_effect=fake_call_ai), \
                mock.patch("webui.ai.is_ai_available", return_value=True), \
                mock.patch("webui.pipeline_exec.fetch_job_details", fake_fetch_job_details), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", fake_ensure_chrome_ready), \
                mock.patch("webui.pipeline_exec.close_debug_chrome", fake_close_debug_chrome), \
                mock.patch("webui.account_round_robin.make_detail_robin", return_value=None):
            run_ai_screen_task(
                ctx, ai_run_id, screening_fields, profile_summary, scrape_id,
                resume_from_run_id=resume_from_run_id,
                profile_facts={"core_skills": ["Python"]},
                cross_platform_dedupe=False,
            )
        self.last_detail_calls = detail_calls
        self.last_chrome = chrome
        return calls

    def _ai_run_row(self, ai_run_id):
        return self.store.get_screening_run(ai_run_id) or {}


class DomainRecallTransmissionTests(DomainRecallHarness):
    """SC-001 / SC-002：事实必须完整到达模型边界。"""

    def test_industry_mismatch_no_longer_dropped_before_model(self):
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        calls = self._run(ctx, "recall-ai-1", scrape_id, fields)
        rough = [call for call in calls if call["stage"] == "rough"]
        fine = [call for call in calls if call["stage"] == "fine"]
        self.assertTrue(rough, "领域生效时行业分类不同仍要进入粗筛判断")
        self.assertIn("大客户销售", rough[0]["user"])
        self.assertTrue(fine, "行业分类不同不得在精筛之前被硬剔除")
        run_row = self._ai_run_row("recall-ai-1")
        self.assertEqual(
            run_row["execution_params"].get(policy.METADATA_SOURCE_KEY),
            policy.SOURCE_ORIGINAL_GROUP,
        )

    def test_generic_function_titles_reach_fine_stage(self):
        jobs = [
            _domain_job("admin", title="行政专员"),
            _domain_job("hr", title="人事专员"),
            _domain_job("finance", title="财务"),
        ]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        calls = self._run(ctx, "recall-ai-2", scrape_id, fields)
        fine_user = "".join(call["user"] for call in calls if call["stage"] == "fine")
        for title in ("行政专员", "人事专员", "财务"):
            self.assertIn(title, fine_user)

    def test_list_company_background_and_full_jd_reach_model(self):
        long_jd = HEAD_FILLER + TAIL_EVIDENCE
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        calls = self._run(
            ctx, "recall-ai-3", scrape_id, fields, detail_jobs={"job-1": long_jd},
        )
        fine = [call for call in calls if call["stage"] == "fine"]
        self.assertTrue(fine)
        self.assertGreater(len(long_jd), 1500, "用例前提：业务证据在第 1500 字之后")
        self.assertIn(TAIL_EVIDENCE, fine[0]["user"])
        self.assertIn("示例科技", fine[0]["user"])
        self.assertIn("教育培训", fine[0]["user"])

    def test_without_domain_selection_jd_truncation_is_preserved(self):
        long_jd = HEAD_FILLER + TAIL_EVIDENCE
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={"experience": ["1-3年"]},
        )
        calls = self._run(
            ctx, "recall-ai-4", scrape_id, fields, detail_jobs={"job-1": long_jd},
        )
        fine = [call for call in calls if call["stage"] == "fine"]
        self.assertTrue(fine)
        self.assertNotIn(TAIL_EVIDENCE, fine[0]["user"])

    def test_domain_labels_reach_both_stages_with_stage_specific_rules(self):
        """两阶段都看得到所选领域，但只有精筛拿到领域判定口径。

        粗筛输入不含公司与业务事实，写「按主营业务判断」会让它在无据时剔除。
        """
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        calls = self._run(ctx, "recall-ai-5", scrape_id, fields)
        rough = [call["system"] for call in calls if call["stage"] == "rough"]
        fine = [call["system"] for call in calls if call["stage"] == "fine"]
        self.assertTrue(rough and fine)
        for system in rough + fine:
            self.assertIn(INTERNET_GROUP, system)
        self.assertIn("不得以「领域不相关」为理由剔除", rough[0])
        self.assertNotIn("主营业务", rough[0])
        self.assertIn("主营业务", fine[0])
        self.assertIn("偶然", fine[0])
        self.assertNotIn("只要提到关键词即视为相关", fine[0])

    def test_missing_jd_still_uses_existing_unknown_verdict(self):
        """领域生效不改变未知口径：缺 JD 仍是待确认，不称领域相关也不称不相关。"""
        from webui.ai_screening import match_jds

        selection = policy.selection_from_snapshot(
            snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            platform="boss", current_labels=["互联网"],
        )
        criteria = {
            "industry": [BOSS_INTERNET_CODE],
            "profile_summary": "3 年 Python 后端",
            policy.CRITERIA_DOMAIN_KEY: {
                "labels": list(selection.labels),
                "source_kind": selection.source_kind,
                "version": policy.POLICY_VERSION,
            },
        }
        with mock.patch("webui.ai.call_ai") as call_ai:
            result = match_jds(
                [{"job_id": "job-1", "title": "AI 销售", "jd": ""}],
                "3 年 Python 后端", "https://ai.test", "key",
                criteria=criteria, platform="boss",
            )
        call_ai.assert_not_called()
        verdict = result["verdicts"]["job-1"]
        self.assertEqual(verdict["verdict"], "uncertain")
        self.assertIn("JD", str(verdict["reason"]))

    def test_other_hard_conditions_still_drop_before_model(self):
        """领域相关不豁免其它条件：薪资明确冲突的岗位仍在模型之前剔除。"""
        jobs = [
            _domain_job("job-1", title="AI 销售经理", salary="3-5K"),
            _domain_job("job-2", title="AI 产品经理", salary="20-30K"),
        ]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={"salary": ["20-50K"]},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        calls = self._run(ctx, "recall-ai-6", scrape_id, fields)
        fine_user = "".join(call["user"] for call in calls if call["stage"] == "fine")
        self.assertIn("AI 产品经理", fine_user)
        self.assertNotIn("AI 销售经理", fine_user, "薪资明确冲突时领域相关也不豁免原有硬条件")
        verdicts = self.store.load_screening_verdicts("recall-ai-6")
        self.assertEqual(verdicts["job-1"]["verdict"], "dropped")
        self.assertIn("薪资", str(verdicts["job-1"]["reason"]))


class DomainMaterialReuseTests(DomainRecallHarness):
    """C4a：判定不兼容不切断 JD 资料；缓存齐全不启动详情抓取。"""

    def _seed_old_run(self, scrape_id, *, run_id, jd_payload, verdicts=None,
                      checkpoint_in_db=True, result_rows=None):
        self.store.create_screening_run(
            run_id, frozen_filters={"industry": [BOSS_INTERNET_CODE]},
            profile_id=self.profile_id, source_count=1,
            execution_params={
                "platform": "boss", "scrape_task_id": scrape_id,
                "profile_summary": "3 年 Python 后端，期望上海",
                "profile_facts": {"core_skills": ["Python"]},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="failed")
        path = self.root / f"jd-{run_id}.json"
        if jd_payload is not None:
            path.write_text(json.dumps(jd_payload, ensure_ascii=False), encoding="utf-8")
        if verdicts:
            self.store.save_screening_verdicts(run_id, verdicts)
        if checkpoint_in_db and verdicts:
            self.store.save_checkpoint(
                run_id, "ai_rough", sorted(verdicts),
            )
        for index, (pid, jd, dropped) in enumerate(result_rows or []):
            with self.store._connection() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM screening_results WHERE run_id = ? "
                    "AND platform_job_id = ?", (run_id, pid),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO screening_results "
                        "(id, run_id, platform, platform_job_id, verdict, created_at, "
                        "is_dropped, jd) VALUES (?, ?, 'boss', ?, 'dropped', "
                        "'2026-10-01T00:00:00', ?, ?)",
                        (f"{run_id}-{index}", run_id, pid, int(dropped), jd),
                    )
                else:
                    conn.execute(
                        "UPDATE screening_results SET is_dropped = ?, jd = ? "
                        "WHERE run_id = ? AND platform_job_id = ?",
                        (int(dropped), jd, run_id, pid),
                    )
        return run_id

    def test_complete_cache_skips_detail_browser_and_fetch(self):
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        self._seed_old_run(
            scrape_id, run_id="old-complete",
            jd_payload={"job-1": " cached 完整 JD 原文：AI 推理平台售前 " + TAIL_EVIDENCE},
            verdicts={"job-1": {"verdict": "dropped", "reason": "行业不符"}},
        )
        calls = self._run(ctx, "recall-ai-7", scrape_id, fields)
        self.assertEqual(self.last_chrome["ready"], 0, "资料齐全不得启动详情浏览器")
        self.assertEqual(self.last_detail_calls, [], "资料齐全不得发起详情抓取")
        fine = [call for call in calls if call["stage"] == "fine"]
        self.assertTrue(fine)
        self.assertIn(TAIL_EVIDENCE, fine[0]["user"])

    def test_partial_cache_fetches_only_missing(self):
        jobs = [_domain_job("job-1"), _domain_job("job-2", title="AI 产品经理")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        self._seed_old_run(
            scrape_id, run_id="old-partial",
            jd_payload={"job-1": "只有 job-1 的缓存 JD"},
        )
        calls = self._run(
            ctx, "recall-ai-8", scrape_id, fields,
            detail_jobs={"job-2": "新抓的 job-2 JD"},
        )
        self.assertEqual(self.last_detail_calls, [["job-2"]])
        fine_user = "".join(call["user"] for call in calls if call["stage"] == "fine")
        self.assertIn("只有 job-1 的缓存 JD", fine_user)
        self.assertIn("新抓的 job-2 JD", fine_user)

    def test_dropped_row_jd_is_readable_but_old_verdict_is_not_reused(self):
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        self._seed_old_run(
            scrape_id, run_id="old-dropped-row", jd_payload=None,
            verdicts={"job-1": {"verdict": "dropped", "reason": "行业分类不在范围"}},
            result_rows=[("job-1", "被旧规则剔除但 JD 完整：AI 数据产品交付", 1)],
        )
        calls = self._run(ctx, "recall-ai-9", scrape_id, fields)
        rough_user = "".join(call["user"] for call in calls if call["stage"] == "rough")
        self.assertIn("大客户销售", rough_user, "旧剔除判定不得让岗位跳过重判")
        fine_user = "".join(call["user"] for call in calls if call["stage"] == "fine")
        self.assertIn("被旧规则剔除但 JD 完整", fine_user)

    def test_new_run_does_not_inherit_old_stage_checkpoints(self):
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        self._seed_old_run(
            scrape_id, run_id="old-checkpoint",
            jd_payload={"job-1": "旧 JD"},
            verdicts={"job-1": {"verdict": "not_match", "reason": "旧规则跨链路"}},
        )
        calls = self._run(ctx, "recall-ai-10", scrape_id, fields)
        self.assertTrue([call for call in calls if call["stage"] == "rough"])
        self.assertTrue([call for call in calls if call["stage"] == "fine"])

    def test_other_profile_material_is_not_reused(self):
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        self.store.create_screening_run(
            "old-other-profile", frozen_filters={"industry": [BOSS_INTERNET_CODE]},
            profile_id="another-profile", source_count=1,
            execution_params={"platform": "boss", "scrape_task_id": scrape_id},
        )
        self.store.update_screening_run("old-other-profile", status="running")
        self.store.update_screening_run("old-other-profile", status="succeeded")
        (self.root / "jd-old-other-profile.json").write_text(
            json.dumps({"job-1": "别的画像的 JD"}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls = self._run(ctx, "recall-ai-11", scrape_id, fields)
        self.assertEqual(self.last_detail_calls, [["job-1"]])
        fine_user = "".join(call["user"] for call in calls if call["stage"] == "fine")
        self.assertNotIn("别的画像的 JD", fine_user)


class DomainResumeFailureTests(DomainRecallHarness):
    """C4b：不兼容续跑在读取旧断点之前收口为既有 failed。"""

    def _seed_legacy_domain_run(self, scrape_id, run_id, *, flow):
        """未版本化的旧规则领域 run：paused、带剔除判定与旧断点。"""
        track = flow["tracks"][0]
        self.store.create_screening_run(
            run_id, frozen_filters={"industry": [BOSS_INTERNET_CODE]},
            profile_id=self.profile_id, source_count=1,
            execution_params={
                "platform": "boss", "scrape_task_id": scrape_id,
                "flow_id": flow["id"], "track_id": track["id"],
                "profile_summary": "3 年 Python 后端，期望上海",
                "profile_facts": {"core_skills": ["Python"]},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused", error_code="user_paused")
        self.store.save_screening_verdicts(
            run_id, {"job-1": {"verdict": "dropped", "reason": "行业分类不在范围"}},
        )
        self.store.save_checkpoint(run_id, "ai_rough", ["job-1"])
        (self.root / f"jd-{run_id}.json").write_text(
            json.dumps({"job-1": "旧断点 JD"}, ensure_ascii=False), encoding="utf-8",
        )
        return run_id

    def test_incompatible_explicit_resume_fails_with_registered_message(self):
        from webui.error_registry import FAILED_CODE_LABELS

        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        legacy = self._seed_legacy_domain_run(scrape_id, "legacy-domain-run", flow=_flow)
        self.store.update_screening_run(legacy, status="running")
        ctx.tasks[legacy] = {
            "kind": "ai_screen", "status": "running", "platform": "boss",
            "profile_id": self.profile_id, "source_task_id": scrape_id,
            "logs": [], "progress": {}, "error": "", "stop_event": threading.Event(),
        }
        calls = self._run(
            ctx, legacy, scrape_id, fields, resume_from_run_id=legacy,
        )
        run_row = self.store.get_screening_run(legacy) or {}
        message = FAILED_CODE_LABELS["screening_policy_incompatible"]
        self.assertEqual(run_row["status"], "failed")
        self.assertEqual(run_row["error_code"], "screening_policy_incompatible")
        self.assertEqual(run_row["error_reason"], message)
        self.assertEqual(ctx.tasks[legacy]["error"], message)
        self.assertNotIn("screening verdicts of run", message)
        self.assertEqual(calls, [], "不兼容续跑不得推进任何 AI 阶段")
        self.assertEqual(
            [str(verdict["verdict"])
             for verdict in self.store.load_screening_verdicts(legacy).values()],
            ["dropped"],
            "旧判定原样保留，不清空伪装重判",
        )
        self.assertEqual(
            set(self.store.load_checkpoint(legacy, "ai_rough")), {"job-1"},
            "旧断点必须原样保留，不静默清空",
        )
        self.assertTrue((self.root / f"jd-{legacy}.json").exists())

    def test_new_run_keeps_domain_metadata_for_later_pause(self):
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        calls = self._run(ctx, "recall-ai-12", scrape_id, fields)
        self.assertTrue(calls)
        params = self._ai_run_row("recall-ai-12")["execution_params"]
        self.assertEqual(params.get(policy.METADATA_VERSION_KEY), policy.POLICY_VERSION)
        self.assertEqual(
            params.get(policy.METADATA_LABELS_KEY), [INTERNET_GROUP])
        self.assertTrue(params.get(policy.METADATA_SUMMARY_KEY))

    def test_no_selection_resume_path_unchanged(self):
        jobs = [_domain_job("job-1", company_industry="教育培训")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={"experience": ["1-3年"]},
        )
        calls = self._run(ctx, "recall-ai-13", scrape_id, fields)
        params = self._ai_run_row("recall-ai-13")["execution_params"]
        self.assertEqual(params.get(policy.METADATA_VERSION_KEY), policy.POLICY_VERSION)
        self.assertEqual(params.get(policy.METADATA_SOURCE_KEY), policy.SOURCE_NONE)
        self.assertEqual(params.get(policy.METADATA_LABELS_KEY), [])
        self.assertTrue([call for call in calls if call["stage"] == "fine"])


class DomainUnionPromptTests(DomainRecallHarness):
    """SC-003 / SC-005：多选取并集，整组含义不收窄为单一概念。"""

    def test_multiple_groups_are_joined_without_intersection_wording(self):
        jobs = [_domain_job("job-1")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP, GAME_GROUP],
                platform_labels=["互联网", "游戏"],
            ),
            industry_override=[BOSS_INTERNET_CODE, BOSS_GAME_CODE],
        )
        calls = self._run(ctx, "recall-ai-14", scrape_id, fields)
        systems = [call["system"] for call in calls if call["stage"] in ("rough", "fine")]
        self.assertTrue(systems)
        for system in systems:
            self.assertIn(INTERNET_GROUP, system)
            self.assertIn(GAME_GROUP, system)
            self.assertNotIn("同时涉及全部所选领域", system)

    def test_software_business_satisfies_whole_internet_group(self):
        jobs = [_domain_job("job-1", title="软件交付工程师")]
        ctx, _flow, scrape_id, fields = self._new_source(
            jobs=jobs, screening_fields={},
            track_snapshot=self._track_snapshot(
                groups=[INTERNET_GROUP], platform_labels=["互联网"],
            ),
            industry_override=[BOSS_INTERNET_CODE],
        )
        calls = self._run(ctx, "recall-ai-15", scrape_id, fields)
        systems = [call["system"] for call in calls if call["stage"] in ("rough", "fine")]
        for system in systems:
            self.assertIn(INTERNET_GROUP, system)
            self.assertNotIn("仅接受 AI 岗位", system)


if __name__ == "__main__":
    unittest.main()
