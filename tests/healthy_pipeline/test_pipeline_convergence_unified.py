"""健康流程统一收敛合同测试（027 自 tests/test_healthy_pipeline.py 拆出）。

031 B7：历史恢复用例（Slice10 预演 + /api/recovery/* 三条 HTTP 路由）已随
能力迁出——预演用例改直调工具层落 tests/maintenance/test_historical_recovery.py，
HTTP 路由用例随路由撤除一并删除。
"""
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from webui.app import create_app
from webui.store import (
    DiscoveryStoreConflictError,
    RUN_STATUSES, RUN_TRANSITIONS, SYSTEMIC_BLOCK_CODES,
)

from tests.healthy_pipeline.harness import _make_app, _authed_test_client, _wait_for_pipeline_task, _pause_run


class ConvergenceUnifiedRecoveryTests(unittest.TestCase):
    """Phase 12 T002/T003/T005: task-based retry and unified continuation."""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        self.store = self.app.config["TASK_STORE"]
        self.headers = {"X-Boss-Token": self.app.config["API_TOKEN"]}
        self.source_run_id = self.store.save_pipeline_result({
            "jobs": [{
                "job_id": "pending-1", "verdict": "uncertain",
                "verdict_reason": "详情超时", "jd_failed_code": "detail_timeout",
                "source_url": "https://www.zhipin.com/job_detail/pending-1.html",
            }],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0,
        }, {})

    def tearDown(self):
        executor = self.app.config.get("PIPELINE_EXECUTOR")
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    def test_single_retry_creates_persisted_recrawl_task(self):
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                "/api/pipeline/jobs/pending-1/jd",
                json={"source_run_id": self.source_run_id},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 202, response.get_json())
        task_id = response.get_json()["task_id"]
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["current_stage"], "recrawl_fetch_jd")
        self.assertEqual(run["execution_params"]["job_ids"], ["pending-1"])
        submit.assert_called_once()

    def test_single_retry_submit_failure_persists_failed_state(self):
        """executor 拒绝提交时不得留下 DB running / 内存 queued 分裂。"""
        executor = self.app.config["PIPELINE_EXECUTOR"]
        fixed_uuid = mock.Mock(hex="abcdef1234567890")
        with mock.patch("uuid.uuid4", return_value=fixed_uuid), \
                mock.patch.object(
                    executor, "submit", side_effect=RuntimeError("executor rejected")
                ):
            response = self.client.post(
                "/api/pipeline/jobs/pending-1/jd",
                json={"source_run_id": self.source_run_id},
                headers=self.headers,
            )

        task_id = "recrawl-abcdef123456"
        self.assertEqual(response.status_code, 500, response.get_json())
        self.assertEqual(response.get_json()["error"], "single_retry_submit_failed")
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "failed")
        state = self.client.get(
            f"/api/task-state/{task_id}", headers=self.headers
        ).get_json()
        self.assertEqual(state["status"], "failed")

    def test_single_retry_rejects_non_pending_job(self):
        response = self.client.post(
            "/api/pipeline/jobs/match-1/jd",
            json={"source_run_id": self.source_run_id},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "not_pending")

    def test_unified_continue_dispatches_recrawl(self):
        task_id = "unified-recrawl"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "source_run_id": self.source_run_id,
                "job_ids": ["pending-1"], "profile_summary": "",
            },
        )
        _pause_run(
            self.store, task_id, current_stage="recrawl_fetch_jd",
            error_code="captcha_required",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                f"/api/task/continue/{task_id}", headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["task_id"], task_id)
        submit.assert_called_once()
        events = self.store.list_task_events(task_id)
        self.assertEqual(
            [event["type"] for event in events],
            ["block_check", "resume"],
        )
        self.assertTrue(events[0]["payload"]["passed"])

    def test_unified_continue_dispatches_scrape(self):
        task_id = "unified-scrape"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={"script_params": {"keyword": "前端", "city": ["上海"]}},
        )
        _pause_run(
            self.store, task_id, current_stage="scrape",
            error_code="captcha_required",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                f"/api/task/continue/{task_id}", headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["resumed_from"], task_id)
        submit.assert_called_once()

    def test_resume_then_fail_then_finish_not_blocked_by_resume_claim(self):
        task_id = "resume-fail-finish"
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={"script_params": {"keyword": "前端", "city": ["上海"]}},
        )
        self.store.save_scrape_combo_result(task_id, "kw|city", jobs, ["kw|city"])
        _pause_run(
            self.store, task_id, current_stage="scrape",
            error_code="captcha_required",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")

        def failed_search(*_args, **_kwargs):
            return {
                "ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 1,
                "completed_combos": [], "error": "再次失败",
            }

        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.run_search", side_effect=failed_search):
            resumed = self.client.post(
                f"/api/task/continue/{task_id}", headers=self.headers,
            )
            self.assertEqual(resumed.status_code, 200, resumed.get_json())
            _wait_for_pipeline_task(self.client, task_id)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")
        finished = self.client.post(f"/api/task/finish/{task_id}", headers=self.headers)
        self.assertEqual(finished.status_code, 200, finished.get_json())
        self.assertEqual(finished.get_json()["result"]["total_scraped"], 1)
        self.assertEqual(
            self.store.get_screening_run(task_id)["error_code"], "user_finished")

    def test_finish_running_single_combo_with_page_snapshot_succeeds(self):
        """单组合未完成但已有页级岗位快照时，结束并保存不得再 409。"""
        task_id = "single-combo-page-finish"
        self.store.create_screening_run(task_id, source_count=1)
        self.store.save_scrape_page_progress(
            task_id, "Python|北京",
            {"combo_key": "Python|北京", "page": 2, "target_pages": 10,
             "resume_page": 3, "has_more": True, "jobs_count": 1,
             "jobs_snapshot": [
                 {"platform_job_id": "j1", "job_id": "j1", "title": "工程师",
                  "source_url": "https://zhipin.example/j1"},
             ]},
        )
        self.store.update_screening_run(
            task_id, status="running", current_stage="scrape")
        finished = self.client.post(f"/api/task/finish/{task_id}", headers=self.headers)
        self.assertEqual(finished.status_code, 200, finished.get_json())
        payload = finished.get_json()
        self.assertEqual(payload["result"]["total_scraped"], 1)
        self.assertEqual(
            self.store.get_screening_run(task_id)["error_code"], "user_finished")

    def test_finish_waits_for_in_flight_page_save_without_deadlock(self):
        """结束保存时若页级落库正在写，应等待锁释放而非重复 acquire。"""
        task_id = "finish-waits-page-flush"
        self.store.create_screening_run(task_id, source_count=1)
        self.store.save_scrape_page_progress(
            task_id, "Python|北京",
            {"combo_key": "Python|北京", "page": 1, "target_pages": 10,
             "resume_page": 2, "has_more": True, "jobs_count": 1,
             "jobs_snapshot": [
                 {"platform_job_id": "j1", "job_id": "j1", "title": "工程师",
                  "source_url": "https://zhipin.example/j1"},
             ]},
        )
        self.store.update_screening_run(
            task_id, status="running", current_stage="scrape")
        flush_lock = threading.Lock()
        release_holder = threading.Event()
        holder_done = threading.Event()

        def hold_flush_lock():
            flush_lock.acquire()
            release_holder.wait(timeout=5)
            flush_lock.release()
            holder_done.set()

        threading.Thread(target=hold_flush_lock, daemon=True).start()
        threading.Timer(1.0, release_holder.set).start()
        time.sleep(0.1)
        self.app.config["PIPELINE_TASKS"][task_id] = {
            "kind": "scrape", "status": "running", "progress": {},
            "logs": [], "result": None, "error": "",
            "stop_event": threading.Event(), "page_flush_lock": flush_lock,
            "page_persist_seq": 1, "last_page_snapshot_at": time.time(),
        }
        started = time.monotonic()
        with mock.patch(
                "webui.pipeline_exec.close_debug_chrome", return_value=True):
            finished = self.client.post(f"/api/task/finish/{task_id}", headers=self.headers)
        elapsed = time.monotonic() - started

        self.assertEqual(finished.status_code, 200, finished.get_json())
        self.assertLess(elapsed, 3.0, "重复 acquire 会导致结束保存额外阻塞超时")
        self.assertTrue(holder_done.wait(timeout=5))

    def test_finish_running_then_worker_cannot_overwrite_user_finished(self):
        task_id = "running-finish-guard"
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.create_screening_run(task_id, source_count=1)
        self.store.save_scrape_combo_result(task_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(task_id, status="running", current_stage="scrape")
        finished = self.client.post(f"/api/task/finish/{task_id}", headers=self.headers)
        self.assertEqual(finished.status_code, 200, finished.get_json())
        with self.assertRaises(DiscoveryStoreConflictError):
            self.store.update_screening_run(task_id, status="succeeded")
        self.assertEqual(
            self.store.get_screening_run(task_id)["error_code"], "user_finished")

    def test_concurrent_unified_scrape_continue_claims_run_once(self):
        task_id = "concurrent-unified-scrape"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "script_params": {"keyword": "前端", "city": ["上海"]}
            },
        )
        _pause_run(
            self.store, task_id, current_stage="scrape",
            error_code="captcha_required",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        checkpoint_barrier = threading.Barrier(2)
        original_load_checkpoint = self.store.load_checkpoint

        def synchronized_load_checkpoint(run_id, stage):
            result = original_load_checkpoint(run_id, stage)
            checkpoint_barrier.wait(timeout=2)
            return result

        def post_continue():
            with _authed_test_client(self.app) as client:
                return client.post(
                    f"/api/task/continue/{task_id}", headers=self.headers,
                ).status_code

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(
            self.store, "load_checkpoint", side_effect=synchronized_load_checkpoint
        ), mock.patch.object(executor, "submit") as submit, \
                ThreadPoolExecutor(max_workers=2) as requests:
            statuses = sorted(f.result(timeout=3) for f in (
                requests.submit(post_continue), requests.submit(post_continue),
            ))

        self.assertEqual(statuses, [200, 409])
        submit.assert_called_once()

    def test_duplicate_unified_continue_submits_only_once(self):
        task_id = "duplicate-unified-recrawl"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "source_run_id": self.source_run_id,
                "job_ids": ["pending-1"], "profile_summary": "",
            },
        )
        _pause_run(
            self.store, task_id, current_stage="recrawl_fetch_jd",
            error_code="captcha_required",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            first = self.client.post(
                f"/api/task/continue/{task_id}", headers=self.headers,
            )
            second = self.client.post(
                f"/api/task/continue/{task_id}", headers=self.headers,
            )

        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(second.status_code, 409, second.get_json())
        self.assertEqual(second.get_json()["error"], "not_paused")
        submit.assert_called_once()

    def test_concurrent_unified_recrawl_continue_claims_task_once(self):
        task_id = "concurrent-unified-recrawl"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "source_run_id": self.source_run_id,
                "job_ids": ["pending-1"], "profile_summary": "",
            },
        )
        _pause_run(
            self.store, task_id, current_stage="recrawl_fetch_jd",
            error_code="captcha_required",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        checkpoint_barrier = threading.Barrier(2)
        original_load_checkpoint = self.store.load_checkpoint

        def synchronized_load_checkpoint(run_id, stage):
            result = original_load_checkpoint(run_id, stage)
            checkpoint_barrier.wait(timeout=2)
            return result

        def post_continue():
            with _authed_test_client(self.app) as client:
                return client.post(
                    f"/api/task/continue/{task_id}", headers=self.headers,
                ).status_code

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(
            self.store, "load_checkpoint", side_effect=synchronized_load_checkpoint
        ), mock.patch.object(executor, "submit") as submit, \
                ThreadPoolExecutor(max_workers=2) as requests:
            statuses = sorted(f.result(timeout=3) for f in (
                requests.submit(post_continue), requests.submit(post_continue),
            ))

        self.assertEqual(statuses, [200, 409])
        submit.assert_called_once()

    def test_concurrent_unified_ai_continue_claims_run_once(self):
        task_id = "concurrent-unified-ai"
        scrape_task_id = "concurrent-ai-source"
        self.store.create_screening_run(scrape_task_id, source_count=1)
        self.store.save_scrape_combo_result(
            scrape_task_id, "前端|上海",
            [{"job_id": "job-ai-1", "title": "前端工程师"}],
            ["前端|上海"],
        )
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "前端工程师",
            },
        )
        _pause_run(
            self.store, task_id, current_stage="ai_rough",
            error_code="ai_rate_limited",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        source_barrier = threading.Barrier(2)
        original_load_jobs = self.store.load_scrape_run_jobs

        def synchronized_load_jobs(run_id):
            result = original_load_jobs(run_id)
            source_barrier.wait(timeout=2)
            return result

        def post_continue():
            with _authed_test_client(self.app) as client:
                return client.post(
                    f"/api/task/continue/{task_id}", headers=self.headers,
                ).status_code

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(
            self.store, "load_scrape_run_jobs", side_effect=synchronized_load_jobs
        ), mock.patch.object(executor, "submit") as submit, \
                ThreadPoolExecutor(max_workers=2) as requests:
            statuses = sorted(f.result(timeout=3) for f in (
                requests.submit(post_continue), requests.submit(post_continue),
            ))

        self.assertEqual(statuses, [200, 409])
        submit.assert_called_once()

    def test_failed_block_check_keeps_paused_and_records_event(self):
        task_id = "blocked-scrape"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={"script_params": {"keyword": "前端", "city": ["上海"]}},
        )
        _pause_run(
            self.store, task_id, current_stage="scrape",
            error_code="captcha_required", error_reason="验证码仍存在",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = (
            lambda _run: (False, "captcha_required", "验证码仍存在")
        )
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                f"/api/task/continue/{task_id}", headers=self.headers,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "paused")
        events = self.store.list_task_events(task_id)
        self.assertEqual(events[-1]["type"], "block_check")
        self.assertFalse(events[-1]["payload"]["passed"])
        submit.assert_not_called()

    def test_default_ai_block_check_rejects_unresolved_rate_or_network_failure(self):
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        for code in ("ai_rate_limited", "ai_network_error"):
            with self.subTest(code=code):
                task_id = f"default-block-check-{code}"
                self.store.create_screening_run(task_id, source_count=1)
                _pause_run(
                    self.store, task_id, current_stage="ai_rough",
                    error_code=code,
                )
                with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                        mock.patch("webui.ai.test_connection", return_value={
                            "ok": False, "warning_codes": ["network_error"],
                        }) as test_connection, \
                        mock.patch.object(executor, "submit") as submit:
                    response = self.client.post(
                        f"/api/task/continue/{task_id}", headers=self.headers,
                    )

                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(response.get_json()["error"], "block_not_resolved")
                test_connection.assert_called_once()
                submit.assert_not_called()

    def test_legacy_continue_rejects_cancelled_before_executor_submit(self):
        task_id = "legacy-cancelled-terminal"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "script_params": {"keyword": "后端", "city": ["上海"]},
            },
        )
        self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(task_id, status="cancelled")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                f"/api/execute-search/continue/{task_id}", headers=self.headers,
            )

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["error"], "not_paused")
        submit.assert_not_called()

    def test_new_ai_screen_does_not_inherit_cancelled_run_checkpoint(self):
        scrape_task_id = "cancelled-resume-source"
        cancelled_run_id = "cancelled-ai-run"
        screening_fields = {"keyword": "后端"}
        self.app.config["PIPELINE_TASKS"][scrape_task_id] = {
            "kind": "scrape", "status": "done",
            "result": {"ok": True, "jobs": [{"job_id": "job-1"}]},
            "progress": {}, "logs": [], "error": "",
        }
        self.store.create_screening_run(
            cancelled_run_id, source_count=1,
            frozen_filters=screening_fields,
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        self.store.update_screening_run(cancelled_run_id, status="running")
        self.store.update_screening_run(cancelled_run_id, status="cancelled")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                "/api/ai-screen",
                json={
                    "screening_fields": screening_fields,
                    "profile_summary": "后端工程师",
                    "scrape_task_id": scrape_task_id,
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(response.get_json()["resuming"])
        # _run_ai_screen_task(task_id, screening_fields, profile_summary,
        # scrape_task_id, resume_from_run_id, profile_facts)
        self.assertEqual(submit.call_args.args[5], "")

    def test_new_ai_screen_inherits_restart_interrupted_checkpoint(self):
        """服务重启打断的 interrupted（error_code=restart）可被重新开始继承断点。"""
        scrape_task_id = "restart-interrupted-source"
        interrupted_run_id = "restart-interrupted-ai-run"
        screening_fields = {"keyword": "后端"}
        self.app.config["PIPELINE_TASKS"][scrape_task_id] = {
            "kind": "scrape", "status": "done",
            "result": {"ok": True, "jobs": [{"job_id": "job-1"}]},
            "progress": {}, "logs": [], "error": "",
        }
        self.store.create_screening_run(
            interrupted_run_id, source_count=1,
            frozen_filters=screening_fields,
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        self.store.update_screening_run(interrupted_run_id, status="running")
        self.store.update_screening_run(
            interrupted_run_id, status="interrupted", error_code="restart")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                "/api/ai-screen",
                json={
                    "screening_fields": screening_fields,
                    "profile_summary": "后端工程师",
                    "scrape_task_id": scrape_task_id,
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["resuming"])
        self.assertNotEqual(response.get_json()["task_id"], interrupted_run_id)
        # _run_ai_screen_task(task_id, screening_fields, profile_summary,
        # scrape_task_id, resume_from_run_id, profile_facts)
        self.assertEqual(submit.call_args.args[5], interrupted_run_id)
        self.assertEqual(
            self.store.get_screening_run(interrupted_run_id)["status"], "interrupted")

    def test_new_ai_screen_inherits_restart_interrupted_checkpoint_from_db(self):
        """服务重启后内存来源丢失，仍能从 DB 重建抓取快照并继承 interrupted 断点。"""
        scrape_task_id = "restart-db-source"
        interrupted_run_id = "restart-db-ai-run"
        screening_fields = {"keyword": "后端"}
        jobs = [{"job_id": "job-1", "title": "后端工程师"}]
        self.store.create_screening_run(scrape_task_id, source_count=len(jobs))
        self.store.save_scrape_combo_result(
            scrape_task_id, "后端|上海", jobs, ["后端|上海"])
        self.store.update_screening_run(scrape_task_id, status="succeeded")
        self.store.create_screening_run(
            interrupted_run_id, source_count=1,
            frozen_filters=screening_fields,
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        self.store.update_screening_run(interrupted_run_id, status="running")
        self.store.update_screening_run(
            interrupted_run_id, status="interrupted", error_code="restart")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            response = self.client.post(
                "/api/ai-screen",
                json={
                    "screening_fields": screening_fields,
                    "profile_summary": "后端工程师",
                    "scrape_task_id": scrape_task_id,
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["resuming"])
        # _run_ai_screen_task(task_id, screening_fields, profile_summary,
        # scrape_task_id, resume_from_run_id, profile_facts)
        self.assertEqual(submit.call_args.args[5], interrupted_run_id)
        self.assertIn(scrape_task_id, self.app.config["PIPELINE_TASKS"])
        self.assertEqual(
            self.store.get_screening_run(interrupted_run_id)["status"], "interrupted")

    def test_latest_running_task_reports_restart_interrupted(self):
        self.store.create_screening_run(
            "latest-restart-interrupted", source_count=1,
            execution_params={"scrape_task_id": "src-1", "profile_summary": "后端工程师"},
            frozen_filters={"keyword": "后端"},
        )
        self.store.update_screening_run("latest-restart-interrupted", status="running")
        self.store.update_screening_run(
            "latest-restart-interrupted", status="interrupted", error_code="restart")
        response = self.client.get("/api/latest-running-task")
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "interrupted")
        self.assertTrue(data["resumable"])
        self.assertEqual(data["frozen_filters"], {"keyword": "后端"})
        self.assertEqual(data["profile_summary"], "后端工程师")
        self.assertEqual(data["kind"], "ai_screen")

    def test_latest_running_task_reports_interrupted_scrape_kind(self):
        self.store.create_screening_run("latest-interrupted-scrape", source_count=1)
        self.store.update_screening_run(
            "latest-interrupted-scrape", status="running", current_stage="scrape")
        self.store.update_screening_run(
            "latest-interrupted-scrape", status="interrupted", error_code="restart")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(data["kind"], "scrape")
        self.assertEqual(data["status"], "interrupted")

    def test_ai_screen_marks_old_interrupted_consumed_and_blocks_duplicate(self):
        """重启中断续跑接管旧 run 后，重复提交同一来源会被拒绝。"""
        scrape_task_id = "restart-claimed-source"
        interrupted_run_id = "restart-claimed-ai-run"
        screening_fields = {"keyword": "后端"}
        self.app.config["PIPELINE_TASKS"][scrape_task_id] = {
            "kind": "scrape", "status": "done",
            "result": {"ok": True, "jobs": [{"job_id": "job-1"}]},
            "progress": {}, "logs": [], "error": "",
        }
        self.store.create_screening_run(
            interrupted_run_id, source_count=1,
            frozen_filters=screening_fields,
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        self.store.update_screening_run(interrupted_run_id, status="running")
        self.store.update_screening_run(
            interrupted_run_id, status="interrupted", error_code="restart")
        payload = {
            "screening_fields": screening_fields,
            "profile_summary": "后端工程师",
            "scrape_task_id": scrape_task_id,
        }
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            first = self.client.post("/api/ai-screen", json=payload, headers=self.headers)
            self.assertEqual(first.status_code, 200, first.get_json())
            self.assertTrue(first.get_json()["resuming"])
            # _run_ai_screen_task(task_id, screening_fields, profile_summary,
            # scrape_task_id, resume_from_run_id, profile_facts)
            self.assertEqual(submit.call_args.args[5], interrupted_run_id)
            self.assertEqual(
                self.store.get_screening_run(interrupted_run_id)["error_code"], "resumed")
            self.assertIsNone(self.store.latest_interrupted_screening_run())
            second = self.client.post("/api/ai-screen", json=payload, headers=self.headers)
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.get_json()["error"], "already_running")

    def test_concurrent_new_ai_screen_claims_paused_run_once(self):
        """自动继承 paused 断点也必须原子 claim，只能提交一次。"""
        scrape_task_id = "concurrent-auto-resume-source"
        paused_run_id = "concurrent-auto-resume-run"
        screening_fields = {"keyword": "后端"}
        self.app.config["PIPELINE_TASKS"][scrape_task_id] = {
            "kind": "scrape", "status": "done",
            "result": {"ok": True, "jobs": [{"job_id": "job-1"}]},
            "progress": {}, "logs": [], "error": "",
        }
        self.store.create_screening_run(
            paused_run_id, source_count=1,
            frozen_filters=screening_fields,
            execution_params={
                "scrape_task_id": scrape_task_id,
                "profile_summary": "后端工程师",
            },
        )
        _pause_run(
            self.store, paused_run_id, error_code="ai_rate_limited"
        )
        selected_barrier = threading.Barrier(2)
        original_latest = self.store.latest_screen_runs_for_source

        def synchronized_latest(*args, **kwargs):
            result = original_latest(*args, **kwargs)
            selected_barrier.wait(timeout=2)
            return result

        def post_screen():
            with _authed_test_client(self.app) as client:
                return client.post(
                    "/api/ai-screen",
                    json={
                        "screening_fields": screening_fields,
                        "profile_summary": "后端工程师",
                        "scrape_task_id": scrape_task_id,
                    },
                    headers=self.headers,
                ).status_code

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(
            self.store, "latest_screen_runs_for_source",
            side_effect=synchronized_latest,
        ), mock.patch.object(executor, "submit") as submit, \
                ThreadPoolExecutor(max_workers=2) as requests:
            statuses = sorted(f.result(timeout=3) for f in (
                requests.submit(post_screen), requests.submit(post_screen),
            ))

        self.assertEqual(statuses, [200, 409])
        submit.assert_called_once()

    def test_cancelled_paused_run_records_cancel_event(self):
        task_id = "cancelled-paused-run"
        self.store.create_screening_run(task_id, source_count=3)
        _pause_run(
            self.store, task_id, current_stage="ai_fine",
            error_code="ai_rate_limited", processed_count=1,
        )

        response = self.client.post(
            f"/api/task/cancel/{task_id}", headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["status"], "cancelled")
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["error_code"], "user_cancelled")
        self.assertEqual(run["error_reason"], "用户已取消")
        events = self.store.list_task_events(task_id)
        self.assertEqual([event["type"] for event in events], ["cancel"])
        self.assertEqual(events[0]["payload"], {"by": "user"})


class ConvergenceTaskEventSequenceTests(unittest.TestCase):
    """Phase 12 T006: real execution emits structured stage and job events."""

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

    def test_scrape_records_stage_and_job_event_sequence(self):
        def fake_run_search(*_args, **kwargs):
            kwargs["on_combo_done"](
                "前端|上海",
                [{"job_id": "job-1", "title": "前端"}],
                ["前端|上海"],
            )
            return {
                "ok": True,
                "jobs": [{"job_id": "job-1", "title": "前端"}],
                "total_scraped": 1, "total_matched": 1,
                "combinations": 1, "completed_combos": ["前端|上海"],
                "error": "",
            }

        with mock.patch("webui.pipeline_exec.run_search", side_effect=fake_run_search):
            response = self.client.post(
                "/api/execute-search",
                json={"script_params": {"keyword": "前端", "city": ["上海"]}},
                headers=self.headers,
            )
            task_id = response.get_json()["task_id"]
            snapshot = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(snapshot["status"], "completed")
        events = self.store.list_task_events(task_id)
        event_types = [event["type"] for event in events]
        self.assertEqual(event_types, ["stage_start", "job_success", "stage_complete"])
        self.assertEqual(events[1]["payload"]["job_id"], "job-1")
        self.assertEqual(
            self.store.get_screening_run(task_id)["backend_version"],
            "011-ui-fixes",
        )


class ConvergenceBuildIdentityTests(unittest.TestCase):
    """Phase 12 T007: mutating requests are bound to the running build."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "REQUIRE_BUILD_IDENTITY": True,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = _authed_test_client(self.app)
        self.token = self.app.config["API_TOKEN"]
        self.build_hash = self.client.get("/api/version").get_json()["build_hash"]

    def tearDown(self):
        self.temp.cleanup()

    def test_write_rejects_missing_build_identity(self):
        response = self.client.post(
            "/api/task/cancel/missing",
            headers={"X-Boss-Token": self.token},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "build_identity_required")

    def test_write_rejects_mismatched_build_identity(self):
        response = self.client.post(
            "/api/task/cancel/missing",
            headers={"X-Boss-Token": self.token, "X-Boss-Build": "old-build"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "build_identity_mismatch")

    def test_write_with_current_identity_reaches_route(self):
        response = self.client.post(
            "/api/task/cancel/missing",
            headers={"X-Boss-Token": self.token, "X-Boss-Build": self.build_hash},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
