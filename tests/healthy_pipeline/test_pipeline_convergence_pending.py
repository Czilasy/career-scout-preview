"""健康流程待定收敛合同测试（027 自 tests/test_healthy_pipeline.py 拆出）。"""
import os
import json
import pathlib
import sys
import threading
import unittest
from unittest import mock

from tests.healthy_pipeline.harness import _make_app, _authed_test_client, _wait_for_pipeline_task, _pause_run


class ConvergencePendingPersistenceTests(unittest.TestCase):
    """Phase 12 T001: pending facts, conservation, and scoped recrawl."""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        self.store = self.app.config["TASK_STORE"]
        self.headers = {"X-Boss-Token": self.app.config["API_TOKEN"]}

    def tearDown(self):
        executor = self.app.config.get("PIPELINE_EXECUTOR")
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    def _save_mixed_result(self):
        return self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "match-1", "verdict": "match", "verdict_reason": "匹配"},
                {"job_id": "mismatch-1", "verdict": "not_match", "verdict_reason": "不匹配"},
                {
                    "job_id": "pending-1", "verdict": "uncertain",
                    "verdict_reason": "岗位详情请求超时",
                    "jd_failed_code": "detail_timeout",
                    "failed_stage": "jd_detail",
                },
                {
                    "job_id": "uncertain-jd-1", "verdict": "uncertain",
                    "verdict_reason": "已抓取 JD，精筛未完成",
                    "jd": "负责后端开发", "failed_code": "ai_missing_job",
                    "failed_stage": "ai_fine",
                },
            ],
            "dropped": [{"job_id": "drop-1", "reason": "粗筛移除"}],
            "total_scraped": 4,
            "total_kept": 3,
            "total_matched": 1,
            "total_dropped": 1,
        }, {})

    def _install_scrape_source(self, scrape_task_id, jobs):
        from webui.execution_config import ExecutionConfigSnapshot, normalize_scope
        state = self.store.get_advanced_config_state()
        config = ExecutionConfigSnapshot.create(state["last_custom_config"])
        scope = normalize_scope(
            keywords=["后端"], scope_kind="cities", cities=["上海"],
            pages_per_combination=1,
        )
        self.app.config["PIPELINE_TASKS"][scrape_task_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {
                "ok": True, "jobs": [dict(job) for job in jobs], "dropped": [],
                "total_scraped": len(jobs), "total_matched": len(jobs),
                "completed_combos": ["后端|上海"], "error": "",
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
                "script_params": {"keyword": "后端", "city": ["上海"], "pages": 1},
                "execution_config": config.to_dict(),
                "frozen_scope": scope.to_dict(),
            },
            backend_version="test",
        )
        self.store.update_screening_run(scrape_task_id, status="running")
        self.store.update_screening_run(scrape_task_id, status="succeeded")
        self.store.save_ai_settings(
            "http://example.invalid", "test-ref", status="ready"
        )

    def _post_ai_screen(self, scrape_task_id, *, profile_summary="后端工程师"):
        return self.client.post(
            "/api/ai-screen",
            json={
                "screening_fields": {"keyword": "后端"},
                "profile_summary": profile_summary,
                "scrape_task_id": scrape_task_id,
            },
            headers=self.headers,
        )

    def test_main_ai_independent_failure_finishes_partial_with_exact_counts(self):
        """独立 JD 失败必须落为 partial，不能把主任务写成 succeeded。"""
        scrape_task_id = "partial-main-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        detail_result = {
            "jobs": [{
                **jobs[0], "jd": "", "jd_failed_code": "detail_timeout",
                "jd_failed_reason": "岗位详情请求超时",
            }],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 0,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_result), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            _wait_for_pipeline_task(self.client, task_id)

        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "partial", run)
        self.assertEqual(run["match_count"], 0)
        self.assertEqual(run["mismatch_count"], 0)
        self.assertEqual(run["pending_count"], 1)

    def test_main_ai_screen_persists_jd_failed_evidence_to_event_and_pending(self):
        """正常收尾的 JD 失败也必须保留证据：事件和待确认 payload 都不能为空。"""
        scrape_task_id = "jd-evidence-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        evidence = "platform=zhilian stage=batch failed_code=source_invalid_output signal=invalid"
        detail_result = {
            "jobs": [{
                **jobs[0], "jd": "", "jd_failed_code": "source_invalid_output",
                "jd_failed_reason": "输入校验失败或页面解析异常",
                "jd_failed_evidence": evidence,
            }],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 0,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_result), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            _wait_for_pipeline_task(self.client, task_id)

        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "partial", run)
        failures = [
            event for event in self.store.list_task_events(task_id)
            if event["type"] == "job_fail"
        ]
        self.assertEqual(failures[-1]["payload"]["evidence_detail"], evidence)
        snapshot = self.store.load_latest_pipeline_result()
        self.assertIsNotNone(snapshot)
        pending = self.store.list_pending_results(snapshot["run_id"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["failed_code"], "source_invalid_output")
        self.assertEqual(pending[0]["ai_payload"]["evidence_detail"], evidence)

    def test_failure_before_screening_does_not_create_history_snapshot(self):
        scrape_task_id = "pre-screen-failure-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.save_scrape_combo_result(scrape_task_id, "kw|city", jobs, ["kw|city"])

        def fail_before_screening(*_args, **_kwargs):
            raise RuntimeError("boom before screening")

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=fail_before_screening):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.list_history_rounds(), [])

    def test_failure_after_rough_verdicts_creates_no_history_round(self):
        """017-US1: 粗筛已判部分岗位后出错强停，历史不新增轮（不再写失败快照）。"""
        scrape_task_id = "rough-verdict-failure-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.save_scrape_combo_result(scrape_task_id, "kw|city", jobs, ["kw|city"])

        def fail_after_rough_batch(todo, *_args, on_batch_done=None, **_kwargs):
            if on_batch_done:
                job_ids = [str(job["job_id"]) for job in todo]
                on_batch_done({job_id: "kept" for job_id in job_ids}, job_ids)
            raise RuntimeError("boom after rough")

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=fail_after_rough_batch):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        # 017-US1: 出错强停不产生历史轮；底层岗位数据与判定保留（可恢复）
        self.assertEqual(self.store.list_history_rounds(), [])
        self.assertGreater(len(self.store.load_scrape_run_jobs(scrape_task_id)), 0)
        self.assertNotEqual(self.store.load_screening_verdicts(task_id), {})

    def test_terminal_write_failure_leaves_no_history_round(self):
        """018：终态校验/写入先于写历史轮——终态写失败时库里没有任何轮。"""
        scrape_task_id = "post-snapshot-failure-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)

        def matched(chunk, *_args, **_kwargs):
            return {"verdicts": {
                str(job["job_id"]): {
                    "verdict": "match", "reason": "匹配", "caveats": [],
                }
                for job in chunk
            }}

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [job["job_id"] for job in jobs], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value={
                    "jobs": [{**jobs[0], "jd": "负责后端开发"}],
                    "hard_stop": False, "hard_stop_code": None,
                    "stopped": False, "fetched": 1,
                }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", side_effect=matched), \
                mock.patch.object(
                    self.store, "finalize_run_status",
                    side_effect=RuntimeError("terminal write failed"),
                ):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        # 018：finalize 先于 save_finished_round——终态写失败时任务失败，
        # 且库里没有任何历史轮（不再有"已写轮后终态失败"的中间态）。
        self.assertEqual(self.store.list_history_rounds(), [])

    def test_main_ai_uses_source_frozen_execution_config(self):
        scrape_task_id = "frozen-config-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [], "dropped": ["job-1"],
                }) as screen_jobs:
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            _wait_for_pipeline_task(self.client, task_id)

        used = screen_jobs.call_args.kwargs.get("execution_config")
        source = self.store.get_screening_run(scrape_task_id)
        self.assertIsNotNone(used)
        self.assertEqual(
            used.config_digest,
            source["execution_params"]["execution_config"]["config_digest"],
        )

    def test_main_ai_without_configuration_uses_chinese_error(self):
        """无 AI 配置时失败文案必须是中文，不能暴露 RuntimeError。"""
        scrape_task_id = "main-ai-no-config-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.save_ai_settings("", "", status="unconfigured")
        response = self._post_ai_screen(scrape_task_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        task_id = response.get_json()["task_id"]
        finished = _wait_for_pipeline_task(self.client, task_id)
        self.assertEqual(finished["status"], "failed", finished)
        self.assertIn("AI 未配置", str(finished.get("error") or ""))
        self.assertNotIn("RuntimeError", str(finished.get("error") or ""))
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "ai_not_configured")
        self.assertEqual(run["error_reason"], "AI 未配置，请先设置 API 地址和密钥")

    def test_main_ai_chrome_not_ready_pauses_with_cdp_reason(self):
        """主 AI 的 JD 阶段遇到 Chrome 阻断必须可继续暂停。"""
        scrape_task_id = "main-ai-cdp-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(False, "debug port unavailable")):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "source_cdp_unavailable")
        self.assertEqual(run["current_stage"], "jd_detail")
        self.assertEqual(self.store.load_checkpoint(task_id, "ai_rough"), {"job-1"})
        self.assertEqual(run["processed_count"], 0)

    def test_resumed_ai_screen_persists_inherited_jd_before_early_pause(self):
        """新 run 继承旧 JD 断点后，Chrome 未就绪暂停也必须把继承 JD 落盘。"""
        scrape_task_id = "resume-jd-early-pause-source"
        interrupted_run_id = "resume-jd-early-pause-run"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.create_screening_run(
            interrupted_run_id,
            source_count=1,
            frozen_filters={"keyword": "后端"},
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        self.store.update_screening_run(interrupted_run_id, status="running")
        self.store.update_screening_run(
            interrupted_run_id, status="interrupted", error_code="restart")
        old_jd = (
            pathlib.Path(self.app.config["RESULT_DIR"])
            / f"ai_screen_jd_{interrupted_run_id}.json"
        )
        old_jd.write_text(
            json.dumps({"job-1": "已抓取的 JD 正文"}), encoding="utf-8")

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready",
                    return_value=(False, "debug port unavailable"),
                ):
            response = self._post_ai_screen(scrape_task_id)
            self.assertEqual(response.status_code, 200, response.get_json())
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "source_cdp_unavailable")
        new_jd = (
            pathlib.Path(self.app.config["RESULT_DIR"])
            / f"ai_screen_jd_{task_id}.json"
        )
        self.assertTrue(new_jd.exists(), "继承的 JD 断点必须在新 run 落盘")
        self.assertEqual(
            json.loads(new_jd.read_text(encoding="utf-8")),
            {"job-1": "已抓取的 JD 正文"},
        )

    def test_main_ai_run_creation_failure_stops_before_ai(self):
        """无法建立持久化 run 时不得继续做任何 AI 工作。"""
        scrape_task_id = "create-run-failure-source"
        self._install_scrape_source(
            scrape_task_id, [{"job_id": "job-1", "title": "后端工程师"}]
        )
        # T407: create_screening_run 现在在路由处理器中调用。
        # 使用原始方法保存引用，避免递归调用 patched 版本。
        _orig_create = self.store.create_screening_run
        _create_call = [0]

        def _side_effect_create(*a, **kw):
            _create_call[0] += 1
            if _create_call[0] > 1:
                raise RuntimeError("disk full")
            return _orig_create(*a, **kw)

        with mock.patch.object(
            self.store, "create_screening_run", side_effect=_side_effect_create
        ), mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }) as screen_jobs, \
                mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready",
                    return_value=(False, "must not reach Chrome"),
                ):
            response = self._post_ai_screen(scrape_task_id)
            data = response.get_json()
            self.assertEqual(response.status_code, 200, data)
            task_id = data["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        screen_jobs.assert_not_called()

    def test_resume_ai_fine_does_not_treat_rough_kept_as_fine(self):
        """续跑时必须只继承精筛判定，粗筛 kept 仍要进入精筛。"""
        scrape_task_id = "resume-fine-split-source"
        jobs = [
            {"job_id": "job-kept", "title": "后端工程师"},
            {"job_id": "job-drop", "title": "测试岗位"},
        ]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.save_scrape_combo_result(
            scrape_task_id, "后端|上海", jobs, ["后端|上海"],
        )
        run_id = "resume-fine-split-run"
        self.store.create_screening_run(
            run_id, source_count=2,
            frozen_filters={"keyword": "后端"},
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        _pause_run(
            self.store, run_id,
            error_code="ai_rate_limited",
            current_stage="ai_fine",
        )
        self.store.save_checkpoint(run_id, "ai_rough", ["job-kept", "job-drop"])
        self.store.save_screening_verdicts(run_id, {
            "job-kept": {"verdict": "kept", "reason": ""},
            "job-drop": {"verdict": "dropped", "reason": "粗筛移除"},
        })
        detail_result = {
            "jobs": [{"job_id": "job-kept", "title": "后端工程师", "jd": "负责后端开发"}],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 1,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.test_connection", return_value={
                    "ok": True, "warning_codes": [],
                }), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_result), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {"job-kept": {"verdict": "match", "reason": "匹配", "caveats": []}},
                }) as match_jds:
            response = self.client.post(
                f"/api/task/continue/{run_id}", headers=self.headers,
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            finished = _wait_for_pipeline_task(self.client, run_id)
        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(match_jds.call_count, 1)
        called_jobs = match_jds.call_args.args[0]
        self.assertEqual([j["job_id"] for j in called_jobs], ["job-kept"])
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["match_count"], 1)
        self.assertEqual(run["total_dropped"], 1)
        self.assertEqual(run["pending_count"], 0)

    def test_resume_fine_completed_keeps_not_match_and_uncertain_as_survivors(self):
        """精筛已全部落库时续跑，not_match/uncertain 仍属于粗筛保留，不能缩水成只留 match。"""
        scrape_task_id = "resume-fine-complete-source"
        jobs = [
            {"job_id": "job-match", "title": "后端工程师"},
            {"job_id": "job-notmatch", "title": "Java工程师"},
            {"job_id": "job-uncertain", "title": "客服"},
            {"job_id": "job-drop", "title": "测试岗位"},
        ]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.save_scrape_combo_result(
            scrape_task_id, "后端|上海", jobs, ["后端|上海"],
        )
        run_id = "resume-fine-complete-run"
        self.store.create_screening_run(
            run_id, source_count=len(jobs),
            frozen_filters={"keyword": "后端"},
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        _pause_run(
            self.store, run_id,
            error_code="ai_network_error",
            current_stage="ai_fine",
            total_kept=3, total_dropped=1,
        )
        self.store.save_checkpoint(
            run_id, "ai_rough",
            ["job-match", "job-notmatch", "job-uncertain", "job-drop"],
        )
        self.store.save_checkpoint(
            run_id, "ai_fine",
            ["job-match", "job-notmatch", "job-uncertain"],
        )
        self.store.save_screening_verdicts(run_id, {
            "job-match": {"verdict": "match", "reason": "匹配"},
            "job-notmatch": {"verdict": "not_match", "reason": "不匹配"},
            "job-uncertain": {"verdict": "uncertain", "reason": "AI 失败，待人工确认"},
            "job-drop": {"verdict": "dropped", "reason": "粗筛移除"},
        })
        detail_result = {
            "jobs": [
                {"job_id": "job-match", "title": "后端工程师", "jd": "负责后端开发"},
                {"job_id": "job-notmatch", "title": "Java工程师", "jd": "要求 Java"},
                {"job_id": "job-uncertain", "title": "客服", "jd": "负责客服"},
            ],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 3,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.test_connection", return_value={
                    "ok": True, "warning_codes": [],
                }), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_result), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", return_value={"verdicts": {}}) as match_jds:
            response = self.client.post(
                f"/api/task/continue/{run_id}", headers=self.headers,
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            finished = _wait_for_pipeline_task(self.client, run_id)
        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(match_jds.call_count, 1)
        self.assertEqual(match_jds.call_args.args[0], [])
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "partial", run)
        self.assertEqual(run["total_kept"], 3)
        self.assertEqual(run["total_dropped"], 1)
        self.assertEqual(run["match_count"], 1)
        self.assertEqual(run["mismatch_count"], 1)
        self.assertEqual(run["pending_count"], 1)

    def test_main_ai_fine_persistence_failure_stops_before_next_batch(self):
        """精筛 verdict/checkpoint 原子落库失败后不得调用下一批 AI。"""
        scrape_task_id = "fine-persistence-failure-source"
        jobs = [
            {"job_id": f"job-{index}", "title": "后端工程师"}
            for index in range(21)
        ]
        self._install_scrape_source(scrape_task_id, jobs)

        def details(chunk, *_args, **_kwargs):
            return {
                "jobs": [{**job, "jd": "负责后端服务开发与线上故障排查"} for job in chunk],
                "hard_stop": False, "hard_stop_code": None,
                "stopped": False, "fetched": len(chunk),
            }

        def matched(chunk, *_args, **_kwargs):
            return {"verdicts": {
                str(job["job_id"]): {
                    "verdict": "match", "reason": "匹配", "caveats": [],
                }
                for job in chunk
            }}

        original_atomic = self.store.save_verdict_and_checkpoint_atomic

        def fail_fine_atomic(run_id, stage, verdicts, completed_job_ids):
            if stage == "ai_fine":
                raise RuntimeError("checkpoint rejected")
            return original_atomic(run_id, stage, verdicts, completed_job_ids)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [job["job_id"] for job in jobs], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", side_effect=details), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", side_effect=matched) as match_jds, \
                mock.patch.object(
                    self.store, "save_verdict_and_checkpoint_atomic",
                    side_effect=fail_fine_atomic,
                ):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(match_jds.call_count, 1)

    def test_main_ai_screen_marks_fine_stage_before_match(self):
        """精筛开始前阶段切到 ai_fine，且进度归零、不再沿用 JD 计数。"""
        scrape_task_id = "fine-stage-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        seen = {}

        def matched(chunk, *_args, **_kwargs):
            run = self.store.latest_screening_run_for_source(
                scrape_task_id, statuses=("running",))
            seen["stage"] = run.get("current_stage") if run else None
            seen["processed"] = run.get("processed_count") if run else None
            seen["pending"] = run.get("pending_count") if run else None
            return {"verdicts": {
                str(job["job_id"]): {
                    "verdict": "match", "reason": "匹配", "caveats": [],
                }
                for job in chunk
            }}

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [job["job_id"] for job in jobs], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value={
                    "jobs": [{**jobs[0], "jd": "负责后端开发"}],
                    "hard_stop": False, "hard_stop_code": None,
                    "stopped": False, "fetched": 1,
                }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", side_effect=matched):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(seen.get("stage"), "ai_fine")
        self.assertEqual(seen.get("processed"), 0)
        self.assertEqual(seen.get("pending"), 0)

    def test_main_ai_screen_counts_missing_jd_as_pending_at_fine_start(self):
        """无 JD 岗位进入精筛时即计为待确认，不再伪装成未开始。"""
        scrape_task_id = "fine-pending-source"
        jobs = [
            {"job_id": "job-ok", "title": "后端工程师"},
            {"job_id": "job-missing", "title": "测试岗位"},
        ]
        self._install_scrape_source(scrape_task_id, jobs)
        seen = {}

        def matched(chunk, *_args, **_kwargs):
            run = self.store.latest_screening_run_for_source(
                scrape_task_id, statuses=("running",))
            seen["pending"] = run.get("pending_count") if run else None
            return {"verdicts": {
                str(job["job_id"]): {
                    "verdict": "match", "reason": "匹配", "caveats": [],
                }
                for job in chunk
            }}

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [job["job_id"] for job in jobs], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value={
                    "jobs": [
                        {**jobs[0], "jd": "负责后端开发"},
                        {**jobs[1], "jd": "", "jd_failed_code": "detail_timeout",
                         "jd_failed_reason": "岗位详情请求超时"},
                    ],
                    "hard_stop": False, "hard_stop_code": None,
                    "stopped": False, "fetched": 1,
                }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", side_effect=matched):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(seen.get("pending"), 1)

    def test_main_ai_screen_splits_by_frozen_batch_settings(self):
        """主链路按冻结设置切分：JD 每批 15、精筛 4/并发 10，不再固定 10/20。"""
        scrape_task_id = "frozen-split-source"
        jobs = [
            {"job_id": f"job-{index:03d}", "title": "后端工程师"}
            for index in range(21)
        ]
        self._install_scrape_source(scrape_task_id, jobs)
        detail_chunks = []

        def details(chunk, *_args, **_kwargs):
            detail_chunks.append(len(chunk))
            return {
                "jobs": [{**job, "jd": "负责后端开发"} for job in chunk],
                "hard_stop": False, "hard_stop_code": None,
                "stopped": False, "fetched": len(chunk),
            }

        match_calls = []

        def matched(chunk, *_args, **_kwargs):
            match_calls.append((len(chunk), _kwargs.get("execution_config")))
            return {"verdicts": {
                str(job["job_id"]): {
                    "verdict": "match", "reason": "匹配", "caveats": [],
                }
                for job in chunk
            }}

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [job["job_id"] for job in jobs], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", side_effect=details), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", side_effect=matched):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(detail_chunks, [15, 6])
        self.assertEqual(len(match_calls), 1)
        self.assertEqual(match_calls[0][0], 21)
        config = match_calls[0][1]
        self.assertEqual(int(config.match_batch_size), 4)
        self.assertEqual(int(config.match_concurrency), 10)

    def test_main_ai_terminal_persistence_failure_is_not_reported_done(self):
        """终态写库失败时内存任务也不得宣称完成。"""
        scrape_task_id = "terminal-persistence-failure-source"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        original_update = self.store.update_screening_run

        def fail_terminal(run_id, **kwargs):
            if kwargs.get("status") in {"done", "succeeded", "partial"}:
                raise RuntimeError("terminal write rejected")
            return original_update(run_id, **kwargs)

        detail_result = {
            "jobs": [{
                **jobs[0], "jd": "", "jd_failed_code": "detail_timeout",
                "jd_failed_reason": "岗位详情请求超时",
            }],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 0,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_result), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch.object(
                    self.store, "update_screening_run", side_effect=fail_terminal,
                ):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)

    def test_save_pipeline_result_persists_pending_and_exact_counts(self):
        run_id = self._save_mixed_result()

        run = self.store.get_screening_run(run_id)
        pending = self.store.list_pending_results(run_id)

        self.assertEqual(run["match_count"], 1)
        self.assertEqual(run["mismatch_count"], 1)
        self.assertEqual(run["pending_count"], 2)
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[0]["job_id"], "pending-1")
        self.assertEqual(pending[0]["failed_code"], "detail_timeout")
        self.assertEqual(pending[0]["failure_stage"], "jd_detail")

    def test_finalize_counts_pending_as_processed_work(self):
        run_id = "convergence-finalize-pending"
        self.store.create_screening_run(run_id, source_count=800)
        self.store.update_screening_run(
            run_id, status="running", processed_count=762, pending_count=38,
        )

        self.assertEqual(self.store.finalize_run_status(run_id), "partial")

    def test_latest_result_exposes_source_run_id(self):
        run_id = self._save_mixed_result()

        response = self.client.get("/api/latest-pipeline-result")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source_run_id"], run_id)

    def test_recrawl_rejects_final_verdict_job_ids(self):
        """B051：已有最终判定的 ID 仍返回 non_pending_job_ids。"""
        run_id = self._save_mixed_result()
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={"source_run_id": run_id, "job_ids": ["pending-1", "match-1"]},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "non_pending_job_ids")
        submit.assert_not_called()

    def test_recrawl_accepts_uncertain_jd_from_snapshot(self):
        """B051：有 JD 但精筛未完成的岗位即使不在待确认表也可全部重抓。"""
        run_id = self._save_mixed_result()
        self.store.delete_pending_result(run_id, "uncertain-jd-1")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={"source_run_id": run_id, "job_ids": ["uncertain-jd-1"]},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 202, response.get_json())
        task_id = response.get_json()["task_id"]
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["execution_params"]["job_ids"], ["uncertain-jd-1"])
        submit.assert_called_once()

    def test_ai_fine_flags_independent_field_in_job(self):
        """精筛 flags（B033 靠谱判定）独立写入结果 job 的 flags 字段，不进 caveats。"""
        scrape_task_id = "fine-flags-source"
        self._install_scrape_source(scrape_task_id, [{
            "job_id": "job-1", "title": "后端工程师",
            "source_url": "https://www.zhipin.com/job_detail/job-1.html",
        }])
        detail_result = {
            "jobs": [{"job_id": "job-1", "title": "后端工程师", "jd": "负责后端开发"}],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 1,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_result), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {"job-1": {
                        "verdict": "match", "reason": "匹配",
                        "caveats": ["优先英语六级"],
                        "flags": [
                            {"code": "B1", "level": "medium", "reason": "标题含无责底薪"},
                            {"code": "F3", "level": "medium", "reason": "试用期未写明"},
                        ],
                    }},
                }):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "completed", finished)
        job = next(j for j in finished["result"]["jobs"] if j["job_id"] == "job-1")
        self.assertEqual(job["verdict"], "match")
        # flags 独立字段透传前端（详情页高危红/中危黄），不并入 caveats
        self.assertEqual(job["caveats"], ["优先英语六级"])
        self.assertEqual(len(job["flags"]), 2)
        self.assertTrue(all(f["level"] == "medium" for f in job["flags"]))

    def test_main_jd_hard_stop_persists_each_job_reason_before_return(self):
        scrape_task_id = "main-jd-hard-stop-source"
        self._install_scrape_source(scrape_task_id, [{
            "job_id": "job-1", "title": "后端工程师",
            "source_url": "https://www.zhipin.com/job_detail/job-1.html",
        }])
        detail_failure = {
            "jobs": [{
                "job_id": "job-1", "jd": "",
                "jd_failed_code": "internal_error",
                "jd_failed_reason": "CDP websocket disconnected",
            }],
            "hard_stop": True, "hard_stop_code": "internal_error",
            "stopped": False, "fetched": 0,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_failure):
            response = self.client.post(
                "/api/ai-screen",
                json={
                    "screening_fields": {"keyword": "后端"},
                    "profile_summary": "后端工程师",
                    "scrape_task_id": scrape_task_id,
                },
                headers=self.headers,
            )
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        pending = self.store.get_pending_result(task_id, "job-1")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["failed_code"], "internal_error")
        self.assertEqual(
            pending["ai_payload"]["reason"], "CDP websocket disconnected"
        )
        events = self.store.list_task_events(task_id)
        failures = [event for event in events if event["type"] == "job_fail"]
        self.assertEqual(failures[-1]["payload"]["job_id"], "job-1")
        self.assertEqual(failures[-1]["payload"]["failed_code"], "internal_error")

    def test_ai_rough_pause_persists_processed_count_for_refresh(self):
        """A rough-filter pause must expose the committed batch count after refresh."""
        from webui.ai import AISecurityError, ERROR_RATE_LIMIT

        scrape_task_id = "rough-progress-source"
        jobs = [
            {"job_id": "job-1", "title": "前端工程师"},
            {"job_id": "job-2", "title": "后端工程师"},
        ]
        self._install_scrape_source(scrape_task_id, jobs)

        def pause_after_first_batch(_jobs, *_args, **kwargs):
            kwargs["on_batch_done"](
                {"job-1": {"verdict": "kept", "reason": "保留"}},
                ["job-1"],
            )
            raise AISecurityError(ERROR_RATE_LIMIT)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=pause_after_first_batch):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        state = self.client.get(f"/api/task-state/{task_id}").get_json()
        self.assertEqual(state["processed"], 1, state)
        self.assertEqual(state["unstarted_count"], 1, state)

    def test_ai_rough_pause_persistence_failure_does_not_claim_paused(self):
        """A failed rough-pause write must fail the task instead of splitting state."""
        from webui.ai import AISecurityError, ERROR_RATE_LIMIT

        scrape_task_id = "rough-pause-write-failure"
        self._install_scrape_source(
            scrape_task_id, [{"job_id": "job-1", "title": "后端工程师"}]
        )
        original_update = self.store.update_screening_run

        def fail_pause(run_id, **kwargs):
            if kwargs.get("status") == "paused":
                raise RuntimeError("pause write rejected")
            return original_update(run_id, **kwargs)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=AISecurityError(
                    ERROR_RATE_LIMIT
                )), \
                mock.patch.object(
                    self.store, "update_screening_run", side_effect=fail_pause
                ):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")

    def test_main_jd_pause_persistence_failure_does_not_claim_paused(self):
        """A failed JD-pause write must stop before returning a paused snapshot."""
        scrape_task_id = "jd-pause-write-failure"
        jobs = [{
            "job_id": "job-1", "title": "后端工程师",
            "source_url": "https://www.zhipin.com/job_detail/job-1.html",
        }]
        self._install_scrape_source(scrape_task_id, jobs)
        original_update = self.store.update_screening_run

        def fail_pause(run_id, **kwargs):
            if kwargs.get("status") == "paused":
                raise RuntimeError("pause write rejected")
            return original_update(run_id, **kwargs)

        detail_failure = {
            "jobs": [{
                "job_id": "job-1", "jd": "",
                "jd_failed_code": "captcha_required",
                "jd_failed_reason": "验证码仍存在",
            }],
            "hard_stop": True, "hard_stop_code": "captcha_required",
            "stopped": False, "fetched": 0,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_failure), \
                mock.patch.object(
                    self.store, "update_screening_run", side_effect=fail_pause
                ):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")

    def test_corrupt_resume_jd_checkpoint_fails_before_refetch(self):
        """A corrupt persisted JD checkpoint must not be treated as no progress."""
        scrape_task_id = "corrupt-jd-checkpoint-source"
        task_id = "corrupt-jd-checkpoint-run"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.create_screening_run(
            task_id,
            source_count=1,
            frozen_filters={"keyword": "后端"},
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        _pause_run(
            self.store, task_id,
            current_stage="jd_detail",
            error_code="captcha_required",
        )
        checkpoint = (
            pathlib.Path(self.app.config["RESULT_DIR"])
            / f"ai_screen_jd_{task_id}.json"
        )
        checkpoint.write_text("{broken-json", encoding="utf-8")
        detail_result = {
            "jobs": [{**jobs[0], "jd": "负责后端服务开发"}],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 1,
        }

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
                ), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details",
                    return_value=detail_result,
                ) as fetch_details, \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "job-1": {
                            "verdict": "match", "reason": "匹配", "caveats": [],
                        }
                    }
                }):
            response = self._post_ai_screen(scrape_task_id)
            self.assertEqual(response.status_code, 200, response.get_json())
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        fetch_details.assert_not_called()

    def test_resume_verdict_read_failure_stops_before_refetch(self):
        """A transient verdict read failure must not be converted into empty progress."""
        scrape_task_id = "verdict-read-failure-source"
        task_id = "verdict-read-failure-run"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        self.store.create_screening_run(
            task_id,
            source_count=1,
            frozen_filters={"keyword": "后端"},
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        _pause_run(
            self.store, task_id,
            current_stage="ai_fine",
            error_code="ai_rate_limited",
        )
        detail_result = {
            "jobs": [{**jobs[0], "jd": "负责后端服务开发"}],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 1,
        }

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
                ), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details",
                    return_value=detail_result,
                ) as fetch_details, \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "job-1": {
                            "verdict": "match", "reason": "匹配", "caveats": [],
                        }
                    }
                }), \
                mock.patch.object(
                    self.store,
                    "load_screening_verdicts",
                    side_effect=[RuntimeError("database unavailable"), {}],
                ):
            response = self._post_ai_screen(scrape_task_id)
            self.assertEqual(response.status_code, 200, response.get_json())
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        fetch_details.assert_not_called()

    def test_jd_checkpoint_write_failure_stops_before_next_chunk(self):
        """A failed JD checkpoint write must stop before fetching another batch."""
        scrape_task_id = "jd-checkpoint-write-failure-source"
        jobs = [
            {"job_id": f"job-{index}", "title": "后端工程师"}
            for index in range(11)
        ]
        self._install_scrape_source(scrape_task_id, jobs)

        def details(chunk, *_args, **_kwargs):
            return {
                "jobs": [
                    {**job, "jd": "负责后端服务开发与线上故障排查"}
                    for job in chunk
                ],
                "hard_stop": False, "hard_stop_code": None,
                "stopped": False, "fetched": len(chunk),
            }

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": [job["job_id"] for job in jobs], "dropped": [],
                }), \
                mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
                ), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details", side_effect=details,
                ) as fetch_details, \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("os.replace", side_effect=OSError("disk full")):
            response = self._post_ai_screen(scrape_task_id, profile_summary="")
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(fetch_details.call_count, 1)

    def test_task_failure_status_write_is_not_silently_swallowed(self):
        """A non-terminal legacy task must surface failure-persistence rejection."""
        from webui.app import TaskRunner

        class RejectingStore:
            def __init__(self):
                self.status = "queued"

            def get_task(self, _task_id):
                return {
                    "id": "task-1", "kind": "setup_chrome",
                    "status": self.status, "params": {},
                }

            def update_task(self, _task_id, status, **_kwargs):
                if status == "failed":
                    raise ValueError("terminal write rejected")
                self.status = status

            def append_log(self, _task_id, _message):
                return None

        store = RejectingStore()
        runner = TaskRunner(
            store,
            self.app.config["RESULT_DIR"],
            sys.executable,
            start_tasks=False,
        )
        runner.process_executor.execute = mock.Mock(
            side_effect=RuntimeError("process crashed")
        )

        with self.assertRaisesRegex(
            RuntimeError, "task_failure_persistence_failed"
        ):
            runner._execute("task-1")

    def test_ai_fine_pause_checkpoint_failure_does_not_claim_paused(self):
        """A failed fine-pause checkpoint must transition the durable run to failed."""
        from webui.ai import AISecurityError, ERROR_RATE_LIMIT

        scrape_task_id = "fine-pause-checkpoint-failure"
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self._install_scrape_source(scrape_task_id, jobs)
        original_save = self.store.save_checkpoint

        def fail_fine_checkpoint(run_id, stage, keys):
            if stage == "ai_fine":
                raise RuntimeError("checkpoint write rejected")
            return original_save(run_id, stage, keys)

        detail_result = {
            "jobs": [{**jobs[0], "jd": "负责后端服务开发"}],
            "hard_stop": False, "hard_stop_code": None,
            "stopped": False, "fetched": 1,
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", return_value={
                    "kept": ["job-1"], "dropped": [],
                }), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_result), \
                mock.patch("webui.ai.match_jds", side_effect=AISecurityError(
                    ERROR_RATE_LIMIT
                )), \
                mock.patch.object(
                    self.store, "save_checkpoint", side_effect=fail_fine_checkpoint
                ):
            response = self._post_ai_screen(scrape_task_id)
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id, timeout=10.0)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
