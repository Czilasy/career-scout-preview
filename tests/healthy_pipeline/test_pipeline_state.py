"""健康流程补救与优化 — 自动化验收场景测试。

对应 FULL_EXECUTION_PROMPT 第九节 20 项验收场景 + tasks.md 失败测试。
按切片逐步补充，每切片先写失败测试（RED），再实现（GREEN）。
"""
import ast
import pathlib
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from webui.store import (
    DiscoveryStoreConflictError,
    RUN_STATUSES, RUN_TRANSITIONS, SYSTEMIC_BLOCK_CODES,
)

from tests.healthy_pipeline.harness import _SC015_PATH, _load_boss_cdp_raw, _load_sc015_viewport_check, _make_app


class Slice1StateAndFinalizeTests(unittest.TestCase):
    """切片 1：状态与完成判定。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.store = self.app.config["TASK_STORE"]
        self.run_id = "test-run-slice1"
        self.store.create_screening_run(self.run_id, source_count=100)

    def tearDown(self):
        self.temp.cleanup()

    def test_finalize_status_no_unstarted_must_not_complete(self):
        """有未开始岗位时不得 completed（FR-016, SC-010, SC-011）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(self.run_id, processed_count=50)
        result = self.store.finalize_run_status(self.run_id)
        self.assertEqual(result, "paused", "有未开始岗位时必须 paused，不得 completed")

    def test_systemic_block_must_pause_not_complete(self):
        """系统性阻断时必须 paused（FR-016, SC-006）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, processed_count=100, error_code="captcha_required"
        )
        result = self.store.finalize_run_status(self.run_id)
        self.assertEqual(result, "paused", "系统性阻断时必须 paused")

    def test_all_processed_with_few_pending_completes_with_pending(self):
        """全部处理 + 少量 pending → completed_with_pending（SC-012）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, processed_count=100, pending_count=5
        )
        result = self.store.finalize_run_status(self.run_id)
        self.assertEqual(result, "partial", "全部处理+少量pending应 completed_with_pending")

    def test_terminal_success_and_partial_clear_stale_error_details(self):
        """终态不能残留此前暂停/失败的错误，避免前端同时显示旧错误。"""
        self.store.update_screening_run(
            self.run_id, status="running", error_code="source_cdp_unavailable",
            error_reason="旧的浏览器未就绪",
        )
        self.store.update_screening_run(
            self.run_id, status="partial", processed_count=100, pending_count=2,
        )
        run = self.store.get_screening_run(self.run_id)
        self.assertEqual(run["status"], "partial")
        self.assertIsNone(run["error_code"])
        self.assertIsNone(run["error_reason"])

        succeeded_id = "test-run-terminal-success"
        self.store.create_screening_run(succeeded_id, source_count=1)
        self.store.update_screening_run(
            succeeded_id, status="running", error_code="source_cdp_unavailable",
            error_reason="旧错误",
        )
        self.store.update_screening_run(succeeded_id, status="succeeded", processed_count=1)
        run = self.store.get_screening_run(succeeded_id)
        self.assertIsNone(run["error_code"])
        self.assertIsNone(run["error_reason"])

    def test_state_machine_rejects_illegal_transition(self):
        """非法状态迁移被拒绝（FR-005）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(self.run_id, status="paused")
        # paused → succeeded 是非法（必须先回到 running）
        with self.assertRaises(ValueError):
            self.store.update_screening_run(self.run_id, status="succeeded")

    def test_waiting_run_cannot_pause_before_it_starts(self):
        """waiting/queued 必须先进入 running，不能伪造暂停现场。"""
        with self.assertRaises(ValueError):
            self.store.update_screening_run(self.run_id, status="paused")

    def test_concurrent_terminal_transitions_cannot_overwrite_each_other(self):
        """并发取消/成功只能有一个提交，终态不得被随后覆盖（FR-005/024）。"""
        self.store.update_screening_run(self.run_id, status="running")
        barrier = threading.Barrier(2)
        original_get = self.store.get_screening_run

        def synchronized_get(run_id):
            snapshot = original_get(run_id)
            barrier.wait(timeout=2)
            return snapshot

        def transition(status):
            try:
                self.store.update_screening_run(self.run_id, status=status)
                return "committed"
            except ValueError:
                return "rejected"

        with mock.patch.object(
            self.store, "get_screening_run", side_effect=synchronized_get
        ), ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = sorted(f.result(timeout=3) for f in (
                workers.submit(transition, "cancelled"),
                workers.submit(transition, "succeeded"),
            ))

        self.assertEqual(outcomes, ["committed", "rejected"])
        self.assertIn(
            self.store.get_screening_run(self.run_id)["status"],
            {"interrupted", "succeeded"},
        )

    def test_cancel_preserves_results_no_auto_resume(self):
        """取消后保留结果，不自动恢复（FR-024）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(self.run_id, processed_count=50)
        # 取消（app.py 用 cancelled，映射到 interrupted）
        self.store.update_screening_run(self.run_id, status="cancelled")
        run = self.store.get_screening_run(self.run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["processed_count"], 50, "取消后结果必须保留")
        # interrupted 是终态，不能再迁移
        with self.assertRaises(ValueError):
            self.store.update_screening_run(self.run_id, status="running")

    def test_paused_status_in_run_statuses(self):
        """paused 必须在 RUN_STATUSES 中（FR-005）。"""
        self.assertIn("paused", RUN_STATUSES)
        self.assertIn("paused", RUN_TRANSITIONS)

    def test_paused_can_resume_to_running(self):
        """paused → running 是合法迁移（FR-020）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(self.run_id, status="paused")
        self.store.update_screening_run(self.run_id, status="running")
        run = self.store.get_screening_run(self.run_id)
        self.assertEqual(run["status"], "running")


class Slice1ConservationTests(unittest.TestCase):
    """切片 1：统计总和守恒（SC-018）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.store = self.app.config["TASK_STORE"]
        self.run_id = "test-run-conservation"
        self.store.create_screening_run(self.run_id, source_count=100)

    def tearDown(self):
        self.temp.cleanup()

    def test_statistics_sum_equals_total(self):
        """统计分类总和严格等于岗位总数（SC-018）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id,
            processed_count=80,
            match_count=30,
            mismatch_count=45,
            pending_count=5,
        )
        run = self.store.get_screening_run(self.run_id)
        # match + mismatch + pending = processed
        self.assertEqual(
            run["match_count"] + run["mismatch_count"] + run["pending_count"],
            run["processed_count"],
            "match+mismatch+pending 必须等于 processed_count"
        )


class Slice2PersistenceTests(unittest.TestCase):
    """切片 2：pending 表 + checkpoint + 事件流（FR-011~016/FR-023/FR-038）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.store = self.app.config["TASK_STORE"]
        self.run_id = "test-run-slice2"
        self.store.create_screening_run(self.run_id, source_count=100)

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_results_actually_written(self):
        """失败岗位必须真实写入 screening_pending_results（FR-040）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.insert_pending_result(
            self.run_id, "job-A",
            failure_stage="jd_detail", failed_code="job_offline",
            retryable=False, origin_zone="kept",
        )
        rows = self.store.list_pending_results(self.run_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], "job-A")
        self.assertEqual(rows[0]["failed_code"], "job_offline")
        self.assertEqual(rows[0]["failure_stage"], "jd_detail")

    def test_pending_count_reflects_reality(self):
        """pending_count 必须实时反映 pending 表实际行数（SC-018）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.insert_pending_result(
            self.run_id, "job-A", failure_stage="jd_detail", failed_code="job_offline")
        self.store.insert_pending_result(
            self.run_id, "job-B", failure_stage="ai_fine", failed_code="ai_missing_job")
        # 重复写同一 job 不应增加计数
        self.store.insert_pending_result(
            self.run_id, "job-A", failure_stage="jd_detail", failed_code="job_offline")
        run = self.store.get_screening_run(self.run_id)
        self.assertEqual(run["pending_count"], 2, "pending_count 必须等于实际待确认数")

    def test_checkpoint_saved_on_pause(self):
        """暂停时保存 checkpoint，继续时能加载（FR-023）。"""
        self.store.save_checkpoint(self.run_id, "jd_detail", ["job-1", "job-2", "job-3"])
        keys = self.store.load_checkpoint(self.run_id, "jd_detail")
        self.assertEqual(keys, {"job-1", "job-2", "job-3"})

    def test_continue_skips_checkpoint_keys(self):
        """继续时通过 checkpoint 跳过已完成项（FR-023）。"""
        self.store.save_checkpoint(self.run_id, "scrape", ["kw1|city1", "kw2|city2"])
        keys = self.store.load_checkpoint(self.run_id, "scrape")
        self.assertIn("kw1|city1", keys)
        self.assertIn("kw2|city2", keys)
        # 不同 stage 的 checkpoint 互不影响
        self.assertEqual(self.store.load_checkpoint(self.run_id, "jd_detail"), set())

    def test_task_events_recorded(self):
        """流程事件必须写入 task_logs（FR-038）。"""
        self.store.append_task_event(self.run_id, "stage_start", {"stage": "scrape"})
        self.store.append_task_event(self.run_id, "job_success", {"job_id": "j1"})
        self.store.append_task_event(self.run_id, "pause",
                                      {"error_code": "captcha_required"})
        events = self.store.list_task_events(self.run_id)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["type"], "stage_start")
        self.assertEqual(events[1]["type"], "job_success")
        self.assertEqual(events[2]["type"], "pause")
        self.assertEqual(events[2]["payload"]["error_code"], "captcha_required")

    def test_pending_result_deletable_after_recovery(self):
        """补救成功后能从 pending 表移除（FR-041）。"""
        self.store.insert_pending_result(
            self.run_id, "job-X", failure_stage="jd_detail", failed_code="detail_timeout")
        deleted = self.store.delete_pending_result(self.run_id, "job-X")
        self.assertTrue(deleted)
        self.assertEqual(self.store.list_pending_results(self.run_id), [])
        # pending_count 同步归零
        run = self.store.get_screening_run(self.run_id)
        self.assertEqual(run["pending_count"], 0)


class Slice3ErrorClassificationTests(unittest.TestCase):
    """切片 3：统一错误分类码表（FR-040/SC-006）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def test_ai_rate_limit_classified_as_systemic_block(self):
        """AI 限流必须归类为系统性阻断，命中即暂停（SC-006）。"""
        self.assertIn("ai_rate_limited", SYSTEMIC_BLOCK_CODES)
        self.assertIn("ai_quota_exhausted", SYSTEMIC_BLOCK_CODES)
        self.assertIn("ai_key_invalid", SYSTEMIC_BLOCK_CODES)
        self.assertIn("ai_network_error", SYSTEMIC_BLOCK_CODES)

    def test_job_offline_is_independent_failure(self):
        """岗位下架是独立失败，不阻断整个 run（FR-040）。"""
        from webui.store import INDEPENDENT_FAILURE_CODES
        self.assertIn("job_offline", INDEPENDENT_FAILURE_CODES)
        self.assertIn("detail_timeout", INDEPENDENT_FAILURE_CODES)
        self.assertIn("detail_invalid", INDEPENDENT_FAILURE_CODES)
        self.assertIn("ai_missing_job", INDEPENDENT_FAILURE_CODES)
        # 独立失败码不能同时是系统性阻断码
        self.assertFalse(
            INDEPENDENT_FAILURE_CODES & SYSTEMIC_BLOCK_CODES,
            "独立失败码与系统性阻断码不得重叠"
        )

    def test_no_bare_uncertain_without_reason(self):
        """uncertain 必须带具体 failed_code，禁止仅用"待确认"（FR-040）。"""
        run_id = "test-run-slice3"
        self.store.create_screening_run(run_id, source_count=10)
        self.store.update_screening_run(run_id, status="running")
        # 写入 pending 时必须带 failed_code
        self.store.insert_pending_result(
            run_id, "job-Y", failure_stage="ai_fine", failed_code="ai_missing_job")
        rows = self.store.list_pending_results(run_id)
        self.assertEqual(rows[0]["failed_code"], "ai_missing_job")
        self.assertIsNotNone(rows[0]["failed_code"], "uncertain 必须带具体 failed_code")

    def test_error_taxonomy_covers_13_types(self):
        """统一错误码表必须覆盖 13 类错误（FR-040；016 后旧码经别名归一）。"""
        from webui.error_registry import resolve_code
        from webui.pipeline_exec import ERROR_TAXONOMY
        required = {
            "captcha_required", "login_expired",
            "ai_rate_limited", "ai_quota_exhausted", "ai_key_invalid", "ai_network_error",
            "ip_risk_control", "cdp_unavailable",
            "job_offline", "detail_timeout", "detail_invalid",
            "ai_missing_job", "internal_error",
        }
        missing = {
            code for code in required
            if resolve_code(code) not in ERROR_TAXONOMY
        }
        self.assertFalse(missing, f"ERROR_TAXONOMY 缺失: {missing}")
        # 每条必须有 impact/blocking/retryable/reason/resume_condition
        for code, info in ERROR_TAXONOMY.items():
            for field in ("impact", "blocking", "retryable", "reason", "resume_condition"):
                self.assertIn(field, info, f"{code} 缺字段 {field}")


class JDBatchExceptionClassificationTests(unittest.TestCase):
    """JD 批调用直接抛错时也必须保留可追踪原因并停止空推进。"""

    def test_cdp_disconnect_exception_pauses_with_specific_job_failure(self):
        from webui.pipeline_exec import fetch_job_details

        class DisconnectedSource:
            def __init__(self):
                self.calls = 0

            def fetch_details_batch(self, *_args, **_kwargs):
                self.calls += 1
                raise RuntimeError("CDP websocket disconnected")

        source = DisconnectedSource()
        with tempfile.TemporaryDirectory() as artifact_dir, mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ):
            result = fetch_job_details(
                [{"job_id": "j1", "jd": ""}],
                source,
                artifact_dir=artifact_dir,
            )

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertEqual(result["jobs"][0]["jd_failed_code"], "source_cdp_unavailable")
        self.assertEqual(source.calls, 2, "同一失联事件只自动重启一次，随后必须暂停")

    def test_cdp_disconnect_restart_success_resumes_same_batch(self):
        from webui.pipeline_exec import fetch_job_details
        from webui.source import SourceOutcome

        class RecoverableSource:
            def __init__(self):
                self.calls = 0
                self.jobs_seen = []

            def fetch_details_batch(self, jobs, **_kwargs):
                self.calls += 1
                self.jobs_seen.append([job["job_id"] for job in jobs])
                if self.calls == 1:
                    raise RuntimeError("CDP websocket disconnected")
                return {
                    job["job_id"]: SourceOutcome.success(detail={"jd": "职责"})
                    for job in jobs
                }

        source = RecoverableSource()
        with tempfile.TemporaryDirectory() as artifact_dir, mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ):
            result = fetch_job_details(
                [{"job_id": "j1", "jd": ""}], source, artifact_dir=artifact_dir
            )

        self.assertFalse(result["hard_stop"], result)
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["jobs"][0]["jd"], "职责")
        self.assertEqual(source.calls, 2)
        self.assertEqual(source.jobs_seen, [["j1"], ["j1"]])

    def test_cdp_disconnect_restart_failure_pauses(self):
        from webui.pipeline_exec import fetch_job_details

        class AlwaysLostSource:
            def __init__(self):
                self.calls = 0

            def fetch_details_batch(self, *_args, **_kwargs):
                self.calls += 1
                raise RuntimeError("CDP websocket disconnected")

        source = AlwaysLostSource()
        with tempfile.TemporaryDirectory() as artifact_dir, mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(False, "launch failed")
        ):
            result = fetch_job_details(
                [{"job_id": "j1", "jd": ""}], source, artifact_dir=artifact_dir
            )

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertEqual(source.calls, 1, "自动启动失败必须直接暂停，不重复启动")

    def test_partial_cdp_loss_retries_only_lost_entries(self):
        from webui.pipeline_exec import fetch_job_details
        from webui.source import SourceOutcome

        class PartialLostSource:
            def __init__(self):
                self.calls = 0
                self.jobs_seen = []

            def fetch_details_batch(self, jobs, **_kwargs):
                self.calls += 1
                self.jobs_seen.append([job["job_id"] for job in jobs])
                results = {}
                for job in jobs:
                    if self.calls == 1 and job["job_id"] == "j2":
                        results[job["job_id"]] = SourceOutcome.failure(
                            failed_code="source_cdp_unavailable", safe_log="lost")
                    else:
                        results[job["job_id"]] = SourceOutcome.success(
                            detail={"jd": f"jd-{job['job_id']}"})
                return results

        source = PartialLostSource()
        jobs = [{"job_id": "j1", "jd": ""}, {"job_id": "j2", "jd": ""}]
        with tempfile.TemporaryDirectory() as artifact_dir, mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ):
            result = fetch_job_details(jobs, source, artifact_dir=artifact_dir)

        self.assertFalse(result["hard_stop"], result)
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["jobs"][1]["jd"], "jd-j2")
        self.assertEqual(source.calls, 2)
        self.assertEqual(source.jobs_seen[1], ["j2"], "只重试失联条目")

    def test_unexpected_batch_exception_is_not_returned_as_empty_success(self):
        from webui.pipeline_exec import fetch_job_details

        class BrokenSource:
            def fetch_details_batch(self, *_args, **_kwargs):
                raise ValueError("malformed batch state")

        with tempfile.TemporaryDirectory() as artifact_dir:
            result = fetch_job_details(
                [{"job_id": "j1", "jd": ""}],
                BrokenSource(),
                artifact_dir=artifact_dir,
            )

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "internal_error")
        self.assertEqual(result["jobs"][0]["jd_failed_code"], "internal_error")

    def test_type_error_with_cdp_class_name_is_not_misclassified_as_cdp(self):
        """类名里的 Cdp 不能把普通 TypeError 误判成 cdp_unavailable（真实回归）。"""
        from webui.pipeline_exec import _classify_detail_batch_exception
        exc = TypeError(
            "ZhilianCdpSource.fetch_detail() got an unexpected keyword argument "
            "'max_batch_size'"
        )
        self.assertEqual(
            _classify_detail_batch_exception(exc), "internal_error",
        )

    def test_source_cdp_unavailable_outcome_stops_before_next_batch(self):
        from webui.pipeline_exec import fetch_job_details
        from webui.source import SourceOutcome

        class MissingCdpSource:
            def __init__(self):
                self.calls = 0

            def fetch_details_batch(self, jobs, **_kwargs):
                self.calls += 1
                return {
                    job["job_id"]: SourceOutcome.failure(
                        failed_code="source_cdp_unavailable",
                        safe_log="调试浏览器连接已断开",
                    )
                    for job in jobs
                }

        source = MissingCdpSource()
        jobs = [{"job_id": f"j{i}", "jd": ""} for i in range(6)]
        settings = {
            "detail_batch_size": 5,
            "detail_interval": 0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 0,
        }
        with tempfile.TemporaryDirectory() as artifact_dir, mock.patch(
            "webui.pipeline_exec.load_advanced_settings", return_value=settings
        ), mock.patch("webui.pipeline_exec.time.sleep"), mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ):
            result = fetch_job_details(jobs, source, artifact_dir=artifact_dir)

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertEqual(source.calls, 2, "自动重启一次后仍失联，必须暂停且不得启动下一批 JD")

    def test_detail_failure_carries_safe_log_evidence(self):
        """回归：JD 失败必须携带 source 的脱敏证据，不能只剩笼统错误码。"""
        from webui.pipeline_exec import fetch_job_details
        from webui.source import SourceOutcome

        class BlockedSource:
            def fetch_details_batch(self, jobs, **_kwargs):
                return {
                    job["job_id"]: SourceOutcome.failure(
                        failed_code="source_blocked",
                        safe_log="platform=zhilian stage=batch failed_code=source_blocked signal=blocked",
                        failed_reason="智联平台封禁或阻断",
                    )
                    for job in jobs
                }

        with tempfile.TemporaryDirectory() as artifact_dir, mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ):
            result = fetch_job_details(
                [{"job_id": "j1", "jd": ""}], BlockedSource(), artifact_dir=artifact_dir,
            )

        job = result["jobs"][0]
        self.assertEqual(job["jd_failed_code"], "source_blocked")
        self.assertIn("signal=blocked", job.get("jd_failed_evidence", ""))

    def test_captcha_required_outcome_stops_before_next_batch(self):
        from webui.pipeline_exec import fetch_job_details
        from webui.source import SourceOutcome

        class CaptchaSource:
            def __init__(self):
                self.calls = 0

            def fetch_details_batch(self, jobs, **_kwargs):
                self.calls += 1
                return {
                    job["job_id"]: SourceOutcome.failure(
                        failed_code="captcha_required",
                        safe_log="验证码仍存在",
                    )
                    for job in jobs
                }

        source = CaptchaSource()
        jobs = [{"job_id": f"j{i}", "jd": ""} for i in range(6)]
        settings = {
            "detail_batch_size": 5,
            "detail_interval": 0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 0,
        }
        with tempfile.TemporaryDirectory() as artifact_dir, mock.patch(
            "webui.pipeline_exec.load_advanced_settings", return_value=settings
        ), mock.patch("webui.pipeline_exec.time.sleep"):
            result = fetch_job_details(jobs, source, artifact_dir=artifact_dir)

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "captcha_required")
        self.assertEqual(source.calls, 1, "验证码后不得启动下一批 JD")


class Slice5ShortJDTests(unittest.TestCase):
    """切片 5：短 JD 内容真实性判断（FR-032）。"""

    @classmethod
    def setUpClass(cls):
        cls.module = _load_boss_cdp_raw()

    def test_short_jd_30_chars_accepted_if_real(self):
        """30 字真实短 JD（含语义标记）必须通过（FR-032）。"""
        # 30 字含"负责"语义标记
        jd_text = "负责后端API开发，熟悉Python"
        result = self.module.extract_job_description({"jd": jd_text, "page_text": ""})
        self.assertEqual(result.strip(), jd_text.strip())

    def test_short_jd_80_chars_accepted_if_real(self):
        """80 字真实短 JD 必须通过（FR-032）。"""
        jd_text = "岗位职责：负责后端系统开发与维护，参与项目架构设计。" \
                  "任职要求：熟练掌握 Python，熟悉 Flask 框架。"
        result = self.module.extract_job_description({"jd": jd_text, "page_text": ""})
        self.assertIn("岗位职责", result)

    def test_short_jd_119_chars_accepted_if_real(self):
        """119 字真实短 JD 必须通过（FR-032）。"""
        # 构造恰好 119 字含语义标记的 JD
        jd_text = (
            "岗位职责：负责后端 API 开发与维护，参与系统架构设计，"
            "配合前端完成接口联调。任职要求：熟练掌握 Python 语言，"
            "熟悉 Flask 或 Django 框架，了解数据库优化与缓存设计。"
            "学历要求本科及以上，有团队协作经验优先考虑录用。"
        )
        # 补齐或截断到 119 字（保留语义标记）
        if len(jd_text) < 119:
            jd_text = jd_text + "x" * (119 - len(jd_text))
        else:
            jd_text = jd_text[:119]
        self.assertEqual(len(jd_text), 119, f"测试数据应为 119 字，实际 {len(jd_text)} 字")
        result = self.module.extract_job_description({"jd": jd_text, "page_text": ""})
        # 含语义标记必须通过（不论长度）
        self.assertGreater(len(result), 0)

    def test_short_operational_duties_without_marker_keywords_are_accepted(self):
        jd_text = "维护服务器，排查线上故障，轮值响应告警。"
        result = self.module.extract_job_description({"jd": jd_text, "page_text": ""})
        self.assertEqual(result, jd_text)

    def test_short_delivery_duties_without_marker_keywords_are_accepted(self):
        jd_text = "编写自动化脚本，定位线上问题，按排期交付。"
        result = self.module.extract_job_description({"jd": jd_text, "page_text": ""})
        self.assertEqual(result, jd_text)

    def test_login_wall_still_rejected(self):
        """登录墙仍然被拒绝（FR-032 保留检查）。"""
        with self.assertRaises(self.module.DetailLoginRequiredError):
            self.module.extract_job_description({
                "jd": "",
                "page_text": "登录查看完整内容",
            })

    def test_empty_jd_rejected(self):
        """空 JD 被拒绝。"""
        with self.assertRaises(self.module.DetailExtractionError):
            self.module.extract_job_description({"jd": "", "page_text": ""})

    def test_navigation_shell_without_markers_rejected(self):
        """无语义标记的导航壳被拒绝。"""
        with self.assertRaises(self.module.DetailExtractionError):
            self.module.extract_job_description({
                "jd": "首页 消息 求职 招聘",
                "page_text": "",
            })


class Sc015AcceptanceHarnessTests(unittest.TestCase):
    """SC-015 脚本必须针对隔离端口执行，并以退出码阻断假阳性。"""

    def test_validator_rejects_overflow_and_missing_critical_controls(self):
        module = _load_sc015_viewport_check()
        validator = getattr(module, "validate_viewport_result", None)
        self.assertTrue(callable(validator), "SC-015 脚本必须提供可测试的硬断言")
        invalid = {
            "url": "http://127.0.0.1:5050/",
            "clientWidth": 375,
            "scrollWidth": 420,
            "overflow": True,
            "isMobile": True,
            "taskProgress": "none",
            "pauseReason": "",
            "continueButton": "missing",
            "pendingReason": "",
        }
        with self.assertRaises(AssertionError):
            validator(invalid, 375, "http://127.0.0.1:5050")

    def test_no_cdp_page_is_a_nonzero_failure(self):
        module = _load_sc015_viewport_check()
        run = getattr(module, "run", None)
        self.assertTrue(callable(run), "SC-015 脚本必须返回可用于 CI 的退出码")
        with mock.patch.object(module, "find_page_tab", return_value=None), \
                mock.patch("builtins.print"):
            self.assertNotEqual(run([]), 0)

    def test_default_target_is_isolated_port_5050(self):
        module = _load_sc015_viewport_check()
        parse_args = getattr(module, "parse_args", None)
        self.assertTrue(callable(parse_args), "SC-015 脚本必须显式解析目标地址")
        self.assertEqual(parse_args([]).base_url, "http://127.0.0.1:5050")

    def test_validator_rejects_hidden_or_offscreen_continue_control(self):
        """DOM 中存在但隐藏/越界的继续按钮不得伪装成可见。"""
        module = _load_sc015_viewport_check()
        valid_except_control = {
            "url": "http://127.0.0.1:5050/",
            "clientWidth": 375,
            "scrollWidth": 375,
            "overflow": False,
            "isMobile": True,
            "viewShellCols": "375px",
            "taskProgress": "exists",
            "taskProgressVisible": True,
            "pauseReason": "验证码仍存在",
            "pauseReasonVisible": True,
            "continueButton": "visible",
            "continueButtonVisible": False,
            "pendingReason": "岗位详情请求超时",
            "pendingReasonVisible": True,
        }

        with self.assertRaises(AssertionError):
            module.validate_viewport_result(
                valid_except_control, 375, "http://127.0.0.1:5050"
            )

    def test_sc015_runs_desktop_and_narrow_viewports(self):
        module = _load_sc015_viewport_check()
        self.assertTrue(
            hasattr(module, "VIEWPORTS"), "SC-015 必须集中声明验收视口"
        )
        widths = {width for width, _height in module.VIEWPORTS}
        self.assertIn(375, widths)
        self.assertIn(1440, widths)

    def test_sc015_targets_real_resume_controls_and_mobile_pending_detail(self):
        module = _load_sc015_viewport_check()
        self.assertTrue(hasattr(module, "CONTINUE_SELECTOR"))
        self.assertTrue(hasattr(module, "PENDING_ROW_SELECTOR"))
        self.assertIn("resume-ai-screen", module.CONTINUE_SELECTOR)
        self.assertIn("resume-recrawl", module.CONTINUE_SELECTOR)
        self.assertIn("job-row", module.PENDING_ROW_SELECTOR)

    def test_sc015_uses_declared_websocket_client_dependency(self):
        """The real CDP harness must use the project's declared websocket client."""
        source = _SC015_PATH.read_text(encoding="utf-8")
        self.assertIn("import websocket", source)
        self.assertNotIn("import websockets", source)

    def test_spec010_public_validation_entrypoints_have_docstrings(self):
        """Public recovery and SC-015 entrypoints follow project conventions."""
        root = pathlib.Path(__file__).resolve().parents[2]
        required = {
            root / "webui" / "historical_recovery.py": {
                "preview_recovery", "prepare_recovery", "execute_recovery",
            },
            _SC015_PATH: {
                "parse_args", "find_page_tab", "test_viewport",
                "validate_viewport_result", "run",
            },
        }
        for path, names in required.items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            functions = {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for name in names:
                self.assertIn(name, functions, f"{path}:{name}")
                self.assertTrue(
                    ast.get_docstring(functions[name]), f"{path}:{name} 缺少 docstring"
                )


class Spec010DocumentConsistencyTests(unittest.TestCase):
    """FR-045：规格工件不得把无法核验的 30/8/608 写成事实。"""

    def _skip_if_internal_specs_missing(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        missing = [
            relative
            for relative in (
                "specs/010-healthy-pipeline-recovery/contracts/api-contracts.md",
                "specs/010-healthy-pipeline-recovery/data-model.md",
                "specs/010-healthy-pipeline-recovery/quickstart.md",
                "specs/010-healthy-pipeline-recovery/spec.md",
                "specs/010-healthy-pipeline-recovery/tasks.md",
            )
            if not (root / relative).exists()
        ]
        if missing:
            self.skipTest(
                "internal spec artifacts are not shipped in public releases: " + missing[0]
            )

    def test_contract_and_quickstart_do_not_guess_historical_subclasses(self):
        self._skip_if_internal_specs_missing()
        root = pathlib.Path(__file__).resolve().parents[2]
        for relative in (
            "specs/010-healthy-pipeline-recovery/contracts/api-contracts.md",
            "specs/010-healthy-pipeline-recovery/data-model.md",
            "specs/010-healthy-pipeline-recovery/quickstart.md",
        ):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertNotIn("detail_invalid: 30", text, relative)
            self.assertNotIn('"detail_invalid": 30', text, relative)
            for fabricated in (
                "30 条详情无效", "8 条验证码失败", "608 条未开始",
                "详情输出无效：30 条", "验证码失败：8 条",
                "未继续执行：608 条",
            ):
                self.assertNotIn(fabricated, text, relative)
            self.assertNotIn("captcha: 8", text, relative)
            self.assertNotIn("captcha_failed: 8", text, relative)
            self.assertNotIn('"captcha_failed": 8', text, relative)
            self.assertNotIn("failed_code=NULL", text, relative)
            self.assertIn("historical_reason_unavailable", text, relative)

    def test_final_artifacts_record_acceptance_amendments_without_fabricating_passes(self):
        self._skip_if_internal_specs_missing()
        root = pathlib.Path(__file__).resolve().parents[2]
        spec = (
            root / "specs/010-healthy-pipeline-recovery/spec.md"
        ).read_text(encoding="utf-8")
        tasks = (
            root / "specs/010-healthy-pipeline-recovery/tasks.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Acceptance Amendment (2026-07-28)", spec)
        self.assertIn("不声称曾取得 24 小时墙钟证据", spec)
        self.assertIn("[X] F004", tasks)
        self.assertIn("[X] F005", tasks)


if __name__ == "__main__":
    unittest.main()
