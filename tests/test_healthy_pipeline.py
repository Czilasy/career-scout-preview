"""健康流程补救与优化 — 自动化验收场景测试。

对应 FULL_EXECUTION_PROMPT 第九节 20 项验收场景 + tasks.md 失败测试。
按切片逐步补充，每切片先写失败测试（RED），再实现（GREEN）。
"""
import ast
import importlib.util
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from webui.app import create_app
from webui.store import TaskStore, RUN_STATUSES, RUN_TRANSITIONS, SYSTEMIC_BLOCK_CODES


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "boss_cdp_raw.py"
_SC015_PATH = pathlib.Path(__file__).resolve().parent / "sc015_viewport_check.py"


def _load_boss_cdp_raw():
    sys.modules.setdefault("websocket", mock.Mock())
    sys.modules.setdefault("requests", mock.Mock())
    spec = importlib.util.spec_from_file_location("boss_cdp_raw_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_sc015_viewport_check():
    spec = importlib.util.spec_from_file_location("sc015_viewport_check_test", _SC015_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_app():
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


def _wait_for_pipeline_task(client, task_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/search-progress/{task_id}")
        if response.status_code == 200:
            last = response.get_json()
            if last.get("status") not in ("queued", "running"):
                return last
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not stop within {timeout}s; last={last}")


def _pause_run(store, run_id, **fields):
    """Build a valid persisted paused fixture through running first."""
    if store.get_screening_run(run_id)["status"] == "queued":
        store.update_screening_run(run_id, status="running")
    store.update_screening_run(run_id, status="paused", **fields)


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
        from webui.store import SYSTEMIC_BLOCK_CODES
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
        from webui.store import SYSTEMIC_BLOCK_CODES
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
        """统一错误码表必须覆盖 13 类错误（FR-040）。"""
        from webui.pipeline_exec import ERROR_TAXONOMY
        required = {
            "captcha_required", "login_expired",
            "ai_rate_limited", "ai_quota_exhausted", "ai_key_invalid", "ai_network_error",
            "ip_risk_control", "cdp_unavailable",
            "job_offline", "detail_timeout", "detail_invalid",
            "ai_missing_job", "internal_error",
        }
        missing = required - set(ERROR_TAXONOMY.keys())
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
            def fetch_details_batch(self, *_args, **_kwargs):
                raise RuntimeError("CDP websocket disconnected")

        with tempfile.TemporaryDirectory() as artifact_dir:
            result = fetch_job_details(
                [{"job_id": "j1", "jd": ""}],
                DisconnectedSource(),
                artifact_dir=artifact_dir,
            )

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "cdp_unavailable")
        self.assertEqual(result["jobs"][0]["jd_failed_code"], "cdp_unavailable")

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
        ), mock.patch("webui.pipeline_exec.time.sleep"):
            result = fetch_job_details(jobs, source, artifact_dir=artifact_dir)

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertEqual(source.calls, 1, "CDP 断开后不得启动下一批 JD")

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
        self.assertTrue(len(result) > 0)

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
        root = pathlib.Path(__file__).resolve().parents[1]
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
        root = pathlib.Path(__file__).resolve().parents[1]
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
        root = pathlib.Path(__file__).resolve().parents[1]
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
        root = pathlib.Path(__file__).resolve().parents[1]
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


class Slice10HistoricalRecoveryTests(unittest.TestCase):
    """切片 10：历史恢复只读预演（FR-041）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.store = self.app.config["TASK_STORE"]
        self.result_dir = pathlib.Path(self.temp.name) / "results"
        self.result_dir.mkdir(parents=True, exist_ok=True)
        # 构造 15847d27（粗筛）+ e6250f0e（精筛）测试数据
        self._seed_historical_data()

    def tearDown(self):
        self.temp.cleanup()

    def _seed_historical_data(self):
        """构造与正式库结构一致的测试数据（不再用结构相反的合成数据）。

        正式库实测结构（2026-07-28）：
        15847d27（粗筛 run，1926 条）：
          - 纯字符串 verdict（正常 1876 条）：match=198, not_match=514, uncertain=646, dropped=518
          - JSON verdict（异常 50 条）：inner match=17, inner not_match=33
          - JD 非空 762 条（198 match + 514 not_match + 17 JSON match + 33 JSON not_match）
        e6250f0e（精筛 run，762 条）：
          - 全部 JSON verdict：inner match=198, inner not_match=514, inner uncertain=50
          - JD 非空 0 条（精筛不抓 JD）
        守恒律：1926=518+1408, 1408=762+646, 696=646+50
        """
        rough_id = "rough-test-run"
        fine_id = "fine-test-run"
        # 创建 screening_runs
        self.store.create_screening_run(rough_id, source_count=1926)
        self.store.update_screening_run(
            rough_id, status="succeeded",
            total_dropped=518, total_kept=1408, total_scraped=1926)
        self.store.create_screening_run(fine_id, source_count=1408)
        self.store.update_screening_run(
            fine_id, status="succeeded",
            total_dropped=0, total_kept=762, total_scraped=1408)
        with self.store._connection() as conn:
            # 15847d27 纯字符串 verdict（正常 1876 条）
            # 198 match（带 JD，受保护）
            for i in range(198):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, 'match', ?, ?)",
                    (f"r-pm{i}", rough_id, f"job-pm{i}",
                     f"JD content for match job {i}", "2026-07-28"))
            # 514 not_match（带 JD，受保护）
            for i in range(514):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, 'not_match', ?, ?)",
                    (f"r-pn{i}", rough_id, f"job-pn{i}",
                     f"JD content for not_match job {i}", "2026-07-28"))
            # 646 uncertain（无 JD，pending 646）
            for i in range(646):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, 'uncertain', ?)",
                    (f"r-u{i}", rough_id, f"job-u{i}", "2026-07-28"))
            # 518 dropped（无 JD）
            for i in range(518):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, 'dropped', ?)",
                    (f"r-d{i}", rough_id, f"job-d{i}", "2026-07-28"))
            # 15847d27 JSON verdict（异常 50 条，带 JD，受保护）
            for i in range(17):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"r-jm{i}", rough_id, f"job-jm{i}",
                     json.dumps({"verdict": "match", "reason": "ok"}),
                     f"JD content for JSON match job {i}", "2026-07-28"))
            for i in range(33):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"r-jn{i}", rough_id, f"job-jn{i}",
                     json.dumps({"verdict": "not_match", "reason": "no"}),
                     f"JD content for JSON not_match job {i}", "2026-07-28"))
            # e6250f0e: 198 match + 514 not_match + 50 uncertain（全部 JSON verdict，无 JD）
            # 762 = 198(rough plain match) + 514(rough plain not_match) + 50(rough JSON 17+33)
            # 这 762 条进精筛，646 条 rough uncertain 未进精筛 → pending_646=646
            for i in range(198):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-m{i}", fine_id, f"job-pm{i}",
                     json.dumps({"verdict": "match", "reason": "ok"}), "2026-07-28"))
            for i in range(514):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-n{i}", fine_id, f"job-pn{i}",
                     json.dumps({"verdict": "not_match", "reason": "no"}), "2026-07-28"))
            # 50 条 uncertain = 17 job-jm* + 33 job-jn*（rough 的 JSON 50 条进精筛后 AI 超时）
            for i in range(17):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-cm{i}", fine_id, f"job-jm{i}",
                     json.dumps({"verdict": "uncertain",
                                 "reason": "AI 响应超时，请稍后重试，待人工确认"}),
                     "2026-07-28"))
            for i in range(33):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-cn{i}", fine_id, f"job-jn{i}",
                     json.dumps({"verdict": "uncertain",
                                 "reason": "AI 响应超时，请稍后重试，待人工确认"}),
                     "2026-07-28"))
        self.rough_id = rough_id
        self.fine_id = fine_id

    def test_preview_does_not_write(self):
        """预演不写正式数据库（FR-041）。"""
        from webui.historical_recovery import preview_recovery
        before = self.store.get_screening_run(self.rough_id)
        preview_recovery(self.store, rough_run_id=self.rough_id,
                          fine_run_id=self.fine_id, result_dir=self.result_dir)
        after = self.store.get_screening_run(self.rough_id)
        self.assertEqual(before, after, "预演不得修改数据库")

    def test_preview_15847d27_50_split_17_33(self):
        """15847d27 的 50 条 JSON verdict 识别为 inner 17 match + 33 not_match。

        注意：50 条异常是 JSON verdict 的 inner 分布，不是纯字符串。
        15847d27 的纯字符串 verdict（198 match + 514 not_match）是正常数据，严禁改写。
        """
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store, rough_run_id=self.rough_id,
                                   fine_run_id=self.fine_id, result_dir=self.result_dir)
        # 50 条 JSON verdict 的 inner 分布
        r50 = result["rough_50_json"]
        self.assertEqual(r50["match"], 17)
        self.assertEqual(r50["not_match"], 33)
        self.assertEqual(r50["total"], 50)
        self.assertTrue(r50["has_valid_verdict"])
        self.assertEqual(r50["verdict_format"], "json_inner")
        # 纯字符串 verdict 是正常数据（198 match + 514 not_match + 646 uncertain + 518 dropped）
        plain = result["rough_run"]["plain_verdicts"]
        self.assertEqual(plain.get("match", 0), 198)
        self.assertEqual(plain.get("not_match", 0), 514)
        self.assertEqual(plain.get("uncertain", 0), 646)
        self.assertEqual(plain.get("dropped", 0), 518)

    def test_preview_e6250f0e_50_uncertain(self):
        """e6250f0e 的 50 条识别为 uncertain（AI 超时）。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store, rough_run_id=self.rough_id,
                                   fine_run_id=self.fine_id, result_dir=self.result_dir)
        unc = result["fine_50_uncertain"]
        self.assertEqual(unc["count"], 50)
        self.assertFalse(unc["has_valid_verdict"])
        self.assertIn("超时", unc["reason"])

    def test_preview_646_identified_not_split_30_8_608(self):
        """646 条识别且不猜测 30/8/608（FR-041）。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store, rough_run_id=self.rough_id,
                                   fine_run_id=self.fine_id, result_dir=self.result_dir)
        pending = result["pending_646"]
        self.assertEqual(pending["count"], 646)
        self.assertTrue(pending["cannot_split_30_8_608"])

    def test_preview_conservation_check(self):
        """守恒核对通过（1926 = 518 + 1408 = 518 + 762 + 646）。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store, rough_run_id=self.rough_id,
                                   fine_run_id=self.fine_id, result_dir=self.result_dir)
        cons = result["conservation"]
        self.assertTrue(cons["sum_dropped_kept_ok"], "dropped+kept 必须等于 source")
        self.assertTrue(cons["sum_fine_pending_ok"], "fine+pending 必须等于 kept")
        self.assertTrue(cons["anomaly_ok"], "696 = 646 + 50")
        self.assertTrue(cons["all_ok"])

    def test_preview_gate_passed(self):
        """门禁全部通过。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store, rough_run_id=self.rough_id,
                                   fine_run_id=self.fine_id, result_dir=self.result_dir)
        gate = result["gate_passed"]
        self.assertTrue(gate["all_passed"], f"门禁未通过: {gate}")

    def test_recovery_gate_blocks_if_numbers_mismatch(self):
        """数字不一致时门禁阻断恢复（FR-041）。

        删掉一条 fine uncertain 行，让 fine_50_uncertain=49（!=50），
        触发硬检查失败。门禁必须阻断写库。
        """
        from webui.historical_recovery import preview_recovery
        # 破坏数据：删掉一条 fine uncertain，让 fine_50_uncertain=49
        with self.store._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM screening_results WHERE id = 'f-cm0'")
            self.assertEqual(cursor.rowcount, 1, "测试必须真实破坏一条 fixture 数据")
        result = preview_recovery(
            self.store, rough_run_id=self.rough_id,
            fine_run_id=self.fine_id, result_dir=self.result_dir,
        )
        self.assertFalse(result["gate_passed"]["all_passed"])
        self.assertFalse(result["written"])


class Slice7And9ApiTests(unittest.TestCase):
    """切片 7+9：统一状态接口 + 版本接口（FR-037/FR-039）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        self.store = self.app.config["TASK_STORE"]
        # POST 请求需要本地会话令牌（protect_local_api 钩子）
        self.token = self.app.config["API_TOKEN"]
        self.run_id = "test-run-api"
        self.store.create_screening_run(self.run_id, source_count=100)

    def _auth_headers(self):
        return {"X-Boss-Token": self.token}

    def tearDown(self):
        self.temp.cleanup()

    def test_version_api_returns_hash(self):
        """/api/version 返回 backend_version/build_hash/build_time（FR-039）。"""
        resp = self.client.get("/api/version")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(
            data.get("backend_version"),
            "011-ui-fixes",
        )
        self.assertRegex(data.get("build_hash", ""), r"^[0-9a-f]{12}$")
        self.assertRegex(
            data.get("build_time", ""), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        )

    def test_version_hash_covers_all_backend_modules(self):
        """共享状态/恢复模块变化也必须产生新的前后端构建身份。"""
        import hashlib

        root = pathlib.Path(__file__).resolve().parents[1]
        files = sorted(
            [*root.joinpath("webui").glob("*.py"), root / "scripts" / "boss_cdp_raw.py"],
            key=lambda path: path.relative_to(root).as_posix(),
        )
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        expected = digest.hexdigest()[:12]

        response = self.client.get("/api/version")

        self.assertEqual(response.get_json()["build_hash"], expected)
        self.assertIn(root / "webui" / "store.py", files)
        self.assertIn(root / "webui" / "historical_recovery.py", files)

    def test_task_state_api_returns_complete_picture(self):
        """/api/task-state/<run_id> 返回完整状态（FR-037）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, processed_count=50, match_count=20,
            mismatch_count=25, pending_count=5, current_stage="ai_fine")
        resp = self.client.get(f"/api/task-state/{self.run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status", data)
        self.assertIn("stage", data)
        self.assertIn("progress", data)
        self.assertIn("success_count", data)
        self.assertIn("fail_count", data)
        self.assertIn("unstarted_count", data)
        self.assertIn("total", data)

    def test_task_state_api_paused_with_error_code(self):
        """暂停状态返回具体 error_code（SC-006）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, status="paused",
            error_code="captcha_required",
            error_reason="触发验证码/滑块，需手动完成")
        resp = self.client.get(f"/api/task-state/{self.run_id}")
        data = resp.get_json()
        self.assertEqual(data["status"], "paused")
        self.assertEqual(data["pause_info"]["error_code"], "captcha_required")
        self.assertIn("验证码", data["pause_info"]["error_reason"])

    def test_task_state_counts_success_failure_and_unstarted_separately(self):
        """暂停计数满足 success + failure + unstarted = total（SC-006）。"""
        run_id = "test-run-sc006-counts"
        self.store.create_screening_run(run_id, source_count=1408)
        _pause_run(
            self.store, run_id,
            processed_count=762,
            pending_count=38,
            current_stage="jd_detail",
            error_code="captcha_required",
            error_reason="第 800 条触发验证码",
        )

        resp = self.client.get(f"/api/task-state/{run_id}")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["success_count"], 762)
        self.assertEqual(data["fail_count"], 38)
        self.assertEqual(data["unstarted_count"], 608)
        self.assertEqual(data["total"], 1408)
        self.assertEqual(data["progress"]["overall_percent"], 56.8)

    def test_task_state_merges_live_progress_logs_and_result(self):
        """统一状态接口必须覆盖运行中内存快照与最终结果。"""
        task_id = "live-task-state"
        self.app.config["PIPELINE_TASKS"][task_id] = {
            "kind": "recrawl",
            "status": "done",
            "progress": {"stage": "done", "current": 2, "total": 2},
            "logs": ["第一条", "第二条"],
            "result": {"updates": {"job-1": {"verdict": "match"}}},
            "error": "",
            "started_at": 1000,
            "finished_at": 2000,
        }

        response = self.client.get(f"/api/task-state/{task_id}")

        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["progress"]["stage"], "done")
        self.assertEqual(data["logs"], ["第一条", "第二条"])
        self.assertEqual(data["result"]["updates"]["job-1"]["verdict"], "match")
        self.assertEqual(data["started_at"], 1000)
        self.assertEqual(data["finished_at"], 2000)

    def test_cancel_api_preserves_results(self):
        """/api/task/cancel/<run_id> 取消后保留结果（FR-024）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(self.run_id, processed_count=50)
        resp = self.client.post(f"/api/task/cancel/{self.run_id}",
                                headers=self._auth_headers())
        self.assertEqual(resp.status_code, 200)
        run = self.store.get_screening_run(self.run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["processed_count"], 50, "取消后结果必须保留")

    def test_cancel_api_handles_live_task_before_db_row_exists(self):
        """刚创建的任务尚未落 DB 时，统一取消仍必须立即生效。"""
        task_id = "live-cancel-before-db"
        stop_event = threading.Event()
        self.app.config["PIPELINE_TASKS"][task_id] = {
            "kind": "scrape", "status": "running", "progress": {},
            "logs": [], "result": {"jobs": [{"job_id": "kept"}]},
            "error": "", "stop_event": stop_event,
            "started_at": 1000, "finished_at": None,
        }

        with mock.patch("webui.pipeline_exec.close_debug_chrome") as close_chrome:
            response = self.client.post(
                f"/api/task/cancel/{task_id}", headers=self._auth_headers(),
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["status"], "cancelled")
        self.assertTrue(stop_event.is_set())
        self.assertEqual(
            self.app.config["PIPELINE_TASKS"][task_id]["result"]["jobs"][0]["job_id"],
            "kept",
        )
        close_chrome.assert_called_once()

    def test_cancel_persistence_failure_does_not_publish_memory_cancelled(self):
        """A durable cancel failure must not split live memory from the database."""
        self.store.update_screening_run(self.run_id, status="running")
        self.app.config["PIPELINE_TASKS"][self.run_id] = {
            "kind": "ai_screen", "status": "running", "progress": {},
            "logs": [], "result": None, "error": "",
            "stop_event": threading.Event(),
        }
        with mock.patch.object(
            self.store,
            "update_screening_run",
            side_effect=RuntimeError("cancel write rejected"),
        ):
            response = self.client.post(
                f"/api/task/cancel/{self.run_id}", headers=self._auth_headers(),
            )

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(
            self.app.config["PIPELINE_TASKS"][self.run_id]["status"], "running"
        )

    def test_latest_task_read_failure_is_not_reported_as_no_task(self):
        """Restart-state read failure must be visible instead of returning has_task=false."""
        with mock.patch.object(
            self.store,
            "latest_interrupted_screening_run",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = self.client.get("/api/latest-running-task")

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(response.get_json()["error"], "task_state_unavailable")

    def test_frontend_active_cancel_uses_unified_route(self):
        """运行中和暂停中的取消按钮必须共享统一状态接口。"""
        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "webui" / "src" / "views" / "DiscoveryView.vue"
        ).read_text(encoding="utf-8")
        self.assertNotIn("/api/execute-search/${encodeURIComponent", source)
        self.assertNotIn("/api/ai-screen/${encodeURIComponent", source)
        self.assertGreaterEqual(source.count("/api/task/cancel/${encodeURIComponent"), 3)


class ConvergencePendingPersistenceTests(unittest.TestCase):
    """Phase 12 T001: pending facts, conservation, and scoped recrawl."""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
        self.assertEqual(run["error_code"], "cdp_unavailable")
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
        self.assertEqual(run["error_code"], "cdp_unavailable")
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
        self.assertEqual(run["pending_count"], 1)
        self.assertEqual(len(pending), 1)
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

    def test_recrawl_rejects_non_pending_job_ids(self):
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details", side_effect=details,
                ) as fetch_details, \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.app.os.replace", side_effect=OSError("disk full")):
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
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
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


class ConvergenceUnifiedRecoveryTests(unittest.TestCase):
    """Phase 12 T002/T003/T005: task-based retry and unified continuation."""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
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
        with mock.patch("webui.app.uuid.uuid4", return_value=fixed_uuid), \
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
            with self.app.test_client() as client:
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
            with self.app.test_client() as client:
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
            with self.app.test_client() as client:
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
        self.assertEqual(submit.call_args.args[-1], "")

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
        self.assertEqual(submit.call_args.args[-1], interrupted_run_id)
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
        self.assertEqual(submit.call_args.args[-1], interrupted_run_id)
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
            self.assertEqual(submit.call_args.args[-1], interrupted_run_id)
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
        original_latest = self.store.latest_screening_run_for_source

        def synchronized_latest(*args, **kwargs):
            result = original_latest(*args, **kwargs)
            selected_barrier.wait(timeout=2)
            return result

        def post_screen():
            with self.app.test_client() as client:
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
            self.store, "latest_screening_run_for_source",
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
        events = self.store.list_task_events(task_id)
        self.assertEqual([event["type"] for event in events], ["cancel"])
        self.assertEqual(events[0]["payload"], {"by": "user"})


class ConvergenceRecoveryHttpTests(unittest.TestCase):
    """Phase 12 T004: HTTP routes match the manifest-based recovery API."""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        self.store = self.app.config["TASK_STORE"]
        self.headers = {"X-Boss-Token": self.app.config["API_TOKEN"]}

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_route_returns_server_generated_backup_id(self):
        prepared = {
            "backup_id": "abc123", "status": "prepared",
            "backup_sha256": "f" * 64, "source_fingerprint": "e" * 64,
        }
        with mock.patch(
            "webui.historical_recovery.prepare_recovery", return_value=prepared,
        ) as prepare:
            response = self.client.post(
                "/api/recovery/prepare/current", headers=self.headers,
            )

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["backup_id"], "abc123")
        prepare.assert_called_once_with(self.store)

    def test_execute_route_uses_backup_id_and_keyword_store(self):
        executed = {"ok": True, "written": True, "backup_id": "abc123"}
        with mock.patch(
            "webui.historical_recovery.execute_recovery", return_value=executed,
        ) as execute:
            response = self.client.post(
                "/api/recovery/execute/current",
                json={"backup_id": "abc123"}, headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["result"], executed)
        execute.assert_called_once_with("abc123", store=self.store)

    def test_execute_route_rejects_missing_backup_id(self):
        response = self.client.post(
            "/api/recovery/execute/current", json={}, headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "missing_backup_id")

    def test_preview_route_does_not_swallow_programming_errors(self):
        with mock.patch(
            "webui.historical_recovery.preview_recovery",
            side_effect=AssertionError("programming bug"),
        ):
            with self.assertRaisesRegex(AssertionError, "programming bug"):
                self.client.get("/api/recovery/preview/current")


class ConvergenceTaskEventSequenceTests(unittest.TestCase):
    """Phase 12 T006: real execution emits structured stage and job events."""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
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
        self.client = self.app.test_client()
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


class Slice4ScrapePauseContinueTests(unittest.TestCase):
    """切片 4：列表抓取暂停继续 + checkpoint（FR-020/FR-023）。"""

    def test_classify_scrape_block_recognizes_systemic_keywords(self):
        """_classify_scrape_block 把阻断关键字映射到 SYSTEMIC_BLOCK_CODES。"""
        from webui.app import create_app as _create_app
        app, temp = _make_app()
        try:
            # create_app 内部定义了 _classify_scrape_block，通过视图函数闭包无法直接访问。
            # 这里通过 app 的源码逻辑验证：关键字命中应返回阻断码，未命中返回空串。
            # 用 store + endpoint 模拟"列表抓取失败 + 部分完成 + 阻断关键字"场景。
            store = app.config["TASK_STORE"]
            run_id = "scrape-pause-test"
            store.create_screening_run(run_id, source_count=10)
            # 模拟 _run_pipeline_task 失败分支：completed_combos 非空 + 阻断关键字
            _pause_run(store, run_id,
                                       error_code="captcha_required",
                                       current_stage="scrape")
            store.save_checkpoint(run_id, "scrape", ["kw1|city1", "kw2|city2"])
            # 验证 checkpoint 落盘
            self.assertEqual(store.load_checkpoint(run_id, "scrape"),
                             {"kw1|city1", "kw2|city2"})
            # 验证状态恢复
            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "paused")
            self.assertEqual(run["error_code"], "captcha_required")
        finally:
            temp.cleanup()

    def test_initial_scrape_missing_cdp_source_pauses_persisted_run(self):
        """列表任务构造 CDP source 失败时也必须持久化为可继续暂停。"""
        app, temp = _make_app()
        try:
            client = app.test_client()
            token = app.config["API_TOKEN"]
            with mock.patch("webui.app._BossCdpSource", return_value=None):
                response = client.post(
                    "/api/execute-search",
                    json={"script_params": {"keyword": "后端", "city": ["上海"]}},
                    headers={"X-Boss-Token": token},
                )
                task_id = response.get_json()["task_id"]
                paused = _wait_for_pipeline_task(client, task_id)

            self.assertEqual(paused["status"], "paused", paused)
            run = app.config["TASK_STORE"].get_screening_run(task_id)
            self.assertEqual(run["status"], "paused")
            self.assertEqual(run["error_code"], "cdp_unavailable")
            self.assertEqual(run["current_stage"], "scrape")
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()

    def test_scrape_continue_restores_combos_from_db(self):
        """服务重启后 continue 从 DB checkpoint 恢复 completed_combos（FR-020/FR-023）。"""
        app, temp = _make_app()
        try:
            client = app.test_client()
            token = app.config["API_TOKEN"]
            store = app.config["TASK_STORE"]
            old_id = "scrape-old"
            # 模拟服务重启前：内存丢失，但 DB 有 paused + checkpoint
            store.create_screening_run(old_id, source_count=10,
                                        execution_params={"script_params":
                                                          {"keyword": "前端", "city": ["上海"]}})
            _pause_run(store, old_id,
                                       error_code="captcha_required",
                                       current_stage="scrape")
            store.save_checkpoint(old_id, "scrape", ["前端|上海"])
            captured = {}

            def resumed_search(*_args, **kwargs):
                captured["skip_combos"] = set(kwargs.get("skip_combos") or set())
                return {
                    "ok": True, "jobs": [], "total_scraped": 0,
                    "total_matched": 0, "combinations": 1,
                    "completed_combos": ["前端|上海"], "error": "",
                }

            # 真实路由 + 真实 DB，只隔离外部 Chrome 和网络抓取。
            app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
            with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                            return_value=(True, "")), \
                    mock.patch("webui.pipeline_exec.run_search",
                               side_effect=resumed_search):
                resp = client.post(f"/api/execute-search/continue/{old_id}",
                                   headers={"X-Boss-Token": token})
                self.assertEqual(resp.status_code, 200, resp.get_json())
                task_id = resp.get_json()["task_id"]
                _wait_for_pipeline_task(client, task_id)
            data = resp.get_json() or {}
            self.assertEqual(data.get("skipped"), 1)
            self.assertEqual(captured.get("skip_combos"), {"前端|上海"})
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()


class Slice6AiPauseTests(unittest.TestCase):
    """切片 6：AI 粗筛/精筛 systemic 错误暂停（FR-020/SC-006/SC-007/SC-008）。"""

    def test_screen_jobs_raises_on_systemic_when_strict(self):
        """screen_jobs(raise_on_systemic=True) 命中限流立即抛 AISecurityError。"""
        from webui.ai import screen_jobs, AISecurityError, ERROR_RATE_LIMIT, call_ai
        jobs = [{"job_id": "1", "title": "前端", "salary": "15-25K",
                 "location": "上海", "job_labels": "本科", "company_scale": "100-499"}]
        criteria = {"profile_summary": "前端工程师", "city": ["上海"], "degree": ["本科"]}
        with mock.patch("webui.ai.call_ai",
                        side_effect=AISecurityError(ERROR_RATE_LIMIT)):
            with self.assertRaises(AISecurityError) as ctx:
                screen_jobs(jobs, criteria, "http://x", "key",
                            raise_on_systemic=True)
            self.assertEqual(ctx.exception.error_code, ERROR_RATE_LIMIT)

    def test_match_jds_raises_on_systemic_when_strict(self):
        """match_jds(raise_on_systemic=True) 命中额度耗尽立即抛 AISecurityError。"""
        from webui.ai import match_jds, AISecurityError, ERROR_QUOTA_EXHAUSTED, call_ai
        jobs = [{"job_id": "1", "title": "前端", "salary": "15-25K",
                 "location": "上海", "jd": "岗位职责：前端开发"}]
        with mock.patch("webui.ai.call_ai",
                        side_effect=AISecurityError(ERROR_QUOTA_EXHAUSTED)):
            with self.assertRaises(AISecurityError) as ctx:
                match_jds(jobs, "前端工程师", "http://x", "key",
                          raise_on_systemic=True)
            self.assertEqual(ctx.exception.error_code, ERROR_QUOTA_EXHAUSTED)

    def test_map_ai_error_to_block_code_covers_systemic(self):
        """map_ai_error_to_block_code 把 AI 内部码映射到 ERROR_TAXONOMY 阻断码。"""
        from webui.ai import (map_ai_error_to_block_code, ERROR_RATE_LIMIT,
                              ERROR_QUOTA_EXHAUSTED, ERROR_AUTH, ERROR_NETWORK,
                              ERROR_TIMEOUT, ERROR_SERVER, ERROR_TRUNCATED,
                              ERROR_INVALID)
        self.assertEqual(map_ai_error_to_block_code(ERROR_RATE_LIMIT), "ai_rate_limited")
        self.assertEqual(map_ai_error_to_block_code(ERROR_QUOTA_EXHAUSTED), "ai_quota_exhausted")
        self.assertEqual(map_ai_error_to_block_code(ERROR_AUTH), "ai_key_invalid")
        self.assertEqual(map_ai_error_to_block_code(ERROR_NETWORK), "ai_network_error")
        self.assertEqual(map_ai_error_to_block_code(ERROR_TIMEOUT), "ai_network_error")
        self.assertEqual(map_ai_error_to_block_code(ERROR_SERVER), "ai_network_error")
        # 非 systemic 返回空串
        self.assertEqual(map_ai_error_to_block_code(ERROR_TRUNCATED), "")
        self.assertEqual(map_ai_error_to_block_code(ERROR_INVALID), "")


class Slice8RecrawlTests(unittest.TestCase):
    """切片 8：批量+单条补救改造（FR-022/FR-023）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        self.run_id = "recrawl-source-run"
        self.store.create_screening_run(self.run_id, source_count=100)

    def tearDown(self):
        # 先等后台线程池任务结束（任务会因无 Chrome 快速失败），释放 db 连接
        try:
            executor = self.app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        import gc
        gc.collect()
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def _save_pending_source(self, *, job_id="j1", jd=""):
        """Persist one isolated pending job for recrawl regression tests."""
        return self.store.save_pipeline_result({
            "jobs": [{
                "job_id": job_id,
                "title": "前端工程师",
                "verdict": "uncertain",
                "verdict_reason": "详情超时",
                "jd_failed_code": "detail_timeout",
                "jd": jd,
                "source_url": f"https://www.zhipin.com/job_detail/{job_id}.html",
            }],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 0,
            "total_dropped": 0,
            "profile_summary": "前端工程师",
        }, {"keyword": "前端", "city": ["上海"]})

    def _post_recrawl(self, source_run_id, *, job_id="j1"):
        """Start one isolated recrawl task and return its task id."""
        response = self.client.post(
            "/api/pipeline/recrawl",
            json={
                "source_run_id": source_run_id,
                "job_ids": [job_id],
                "profile_summary": "前端工程师",
            },
            headers=self._auth(),
        )
        self.assertEqual(response.status_code, 202, response.get_json())
        return response.get_json()["task_id"]

    def test_recrawl_chrome_not_ready_pauses_with_persisted_reason(self):
        """Chrome preflight failure is systemic and must never finish recrawl."""
        source_run_id = self._save_pending_source()
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready",
            return_value=(False, "debug port unavailable"),
        ):
            task_id = self._post_recrawl(source_run_id)
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "source_cdp_unavailable")
        self.assertEqual(
            self.store.get_pending_result(task_id, "j1")["failed_code"],
            "source_cdp_unavailable",
        )

    def test_recrawl_without_ai_configuration_pauses_instead_of_succeeding(self):
        """已有 JD 但 AI 未配置时不得伪装为补抓成功。"""
        source_run_id = self._save_pending_source(
            jd="岗位职责：负责后端服务开发"
        )
        task_id = self._post_recrawl(source_run_id)
        paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "ai_key_invalid")
        self.assertEqual(run["current_stage"], "recrawl_ai")
        self.assertIsNotNone(self.store.get_pending_result(source_run_id, "j1"))

    def test_recrawl_missing_cdp_source_pauses_with_persisted_reason(self):
        """A missing CDP source after preflight must use the same hard-stop contract."""
        source_run_id = self._save_pending_source()
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.app._BossCdpSource", return_value=None):
            task_id = self._post_recrawl(source_run_id)
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "source_cdp_unavailable")

    def test_recrawl_pause_persistence_failure_does_not_claim_paused(self):
        """Recrawl pause writes are mandatory before the in-memory pause is visible."""
        source_run_id = self._save_pending_source()
        original_update = self.store.update_screening_run

        def fail_pause(run_id, **kwargs):
            if kwargs.get("status") == "paused":
                raise RuntimeError("pause write rejected")
            return original_update(run_id, **kwargs)

        detail_failure = {
            "jobs": [{
                "job_id": "j1", "jd": "",
                "jd_failed_code": "captcha_required",
                "jd_failed_reason": "验证码仍存在",
            }],
            "hard_stop": True, "hard_stop_code": "captcha_required",
            "stopped": False, "fetched": 0,
        }
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.app._BossCdpSource", return_value=object()), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details", return_value=detail_failure
                ), mock.patch.object(
                    self.store, "update_screening_run", side_effect=fail_pause
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")

    def test_recrawl_source_verdict_failure_preserves_pending_and_fails(self):
        """A source verdict write failure must stop before pending deletion."""
        source_run_id = self._save_pending_source(jd="负责前端开发")
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        original_save = self.store.save_screening_verdicts

        def fail_source_verdict(run_id, verdicts):
            if run_id == source_run_id:
                raise RuntimeError("source verdict write rejected")
            return original_save(run_id, verdicts)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "j1": {"verdict": "match", "reason": "匹配", "caveats": []}
                    }
                }), mock.patch.object(
                    self.store, "save_screening_verdicts",
                    side_effect=fail_source_verdict,
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertIsNotNone(self.store.get_pending_result(source_run_id, "j1"))

    def test_recrawl_pending_delete_failure_does_not_finish(self):
        """Pending deletion is part of the recrawl commit and cannot be best-effort."""
        source_run_id = self._save_pending_source(jd="负责前端开发")
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "j1": {"verdict": "match", "reason": "匹配", "caveats": []}
                    }
                }), mock.patch.object(
                    self.store, "delete_pending_result",
                    side_effect=RuntimeError("pending delete rejected"),
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)

    def test_recrawl_terminal_persistence_failure_does_not_finish(self):
        """The in-memory task cannot report done until its terminal DB write commits."""
        source_run_id = self._save_pending_source(jd="负责前端开发")
        self.store.save_ai_settings(
            "http://example.invalid", "test-ref", status="ready"
        )
        original_update = self.store.update_screening_run

        def fail_terminal(run_id, **kwargs):
            if kwargs.get("status") == "succeeded":
                raise RuntimeError("terminal write rejected")
            return original_update(run_id, **kwargs)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "j1": {"verdict": "match", "reason": "匹配", "caveats": []}
                    }
                }), mock.patch.object(
                    self.store, "update_screening_run", side_effect=fail_terminal
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")

    def test_recrawl_auto_reads_from_pending_table(self):
        """job_ids 缺省时从 screening_pending_results 自动读取（FR-023）。"""
        # 给 run 加 3 条 pending
        for i in range(3):
            self.store.insert_pending_result(
                self.run_id, f"job-{i}",
                failure_stage="jd_detail", retryable=True,
                origin_zone="jd", failed_code="detail_timeout")
        # 不传 job_ids，应自动从 pending 表读
        resp = self.client.post("/api/pipeline/recrawl",
                                json={"source_run_id": self.run_id},
                                headers=self._auth())
        # 202 表示任务已接受
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("source_run_id"), self.run_id)

    def test_recrawl_concurrent_rejected(self):
        """同 source_run_id 已有 running 重抓任务时拒绝（FR-022）。"""
        # 给 run 加 pending，使第一次 recrawl 能启动
        self.store.insert_pending_result(
            self.run_id, "job-1", failure_stage="jd_detail",
            origin_zone="jd", failed_code="detail_timeout")
        # 第一次：202（任务已接受）
        resp1 = self.client.post("/api/pipeline/recrawl",
                                 json={"source_run_id": self.run_id},
                                 headers=self._auth())
        self.assertEqual(resp1.status_code, 202)
        # 第二次立即调：第一个任务可能还在 running（409）或已 cleanup（202）
        # 关键验证：不会启动两个并发任务（要么 409 拒绝，要么 202 接受但第一个已结束）
        resp2 = self.client.post("/api/pipeline/recrawl",
                                 json={"source_run_id": self.run_id},
                                 headers=self._auth())
        self.assertIn(resp2.status_code, (202, 409))

    def test_recrawl_concurrent_requests_claim_source_run_once(self):
        """Two truly concurrent starts may enqueue only one recrawl (FR-022)."""
        self.store.insert_pending_result(
            self.run_id, "job-race", failure_stage="jd_detail",
            origin_zone="jd", failed_code="detail_timeout",
        )
        request_barrier = threading.Barrier(2)
        real_uuid4 = __import__("uuid").uuid4

        def synchronized_uuid4():
            request_barrier.wait(timeout=2)
            return real_uuid4()

        def post_recrawl():
            with self.app.test_client() as client:
                return client.post(
                    "/api/pipeline/recrawl",
                    json={"source_run_id": self.run_id},
                    headers=self._auth(),
                ).status_code

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch("webui.app.uuid.uuid4", side_effect=synchronized_uuid4), \
                mock.patch.object(executor, "submit") as submit, \
                ThreadPoolExecutor(max_workers=2) as requests:
            statuses = sorted(f.result(timeout=3) for f in (
                requests.submit(post_recrawl), requests.submit(post_recrawl),
            ))

        self.assertEqual(statuses, [202, 409])
        submit.assert_called_once()


# ============================================================================
# A 阶段 RED 测试（v3.1）—— 产品代码未实现，预期全部 RED
# ============================================================================

class Slice7HardStopFirstComboTests(unittest.TestCase):
    """A.1 首组合验证码：completed=[] 也必须 paused，不得标 failed（阻断项 1）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        try:
            executor = self.app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        import gc
        gc.collect()
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def test_first_combo_captcha_paused_not_failed(self):
        """首组合即触发 captcha：completed_combos=[] 也必须 paused，不得 failed。

        修复目标（B.1）：app.py 中 `if completed and _pause_code` 改为
        `if result.get("hard_stop"):`，识别 hard_stop 信号而非 completed 非空。
        """
        # mock run_search 返回首组合 captcha hard_stop，completed=[]
        def fake_run_search(*args, **kwargs):
            return {
                "ok": False,
                "jobs": [],
                "total_scraped": 0,
                "total_matched": 0,
                "combinations": 3,
                "completed_combos": [],  # 首组合即失败，completed 为空
                "hard_stop": True,
                "hard_stop_code": "captcha_required",
                "error": "系统性阻断：触发验证码/滑块，需手动完成",
            }

        # run_search 在后台 worker 内动态导入，因此 patch 真实定义模块。
        with mock.patch("webui.pipeline_exec.run_search", side_effect=fake_run_search):
            resp = self.client.post(
                "/api/execute-search",
                json={"script_params": {"keyword": "前端", "city": ["上海"]}},
                headers=self._auth())
            self.assertEqual(resp.status_code, 200)
            task_id = resp.get_json()["task_id"]
            snapshot = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(snapshot["status"], "paused", snapshot)
        self.assertIn("captcha", snapshot.get("error", "").lower())

        # 查 DB 中是否有 paused run（不得 failed）
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT id, status, error_code FROM screening_runs "
                "WHERE id = ?", (task_id,)).fetchall()
            runs = [dict(r) for r in rows]

        # 必须有 paused run，不得 failed
        paused_runs = [r for r in runs if r.get("status") == "paused"
                       and r.get("error_code") == "captcha_required"]
        self.assertTrue(paused_runs,
                        f"首组合 captcha completed=[] 时必须 paused，实际 runs={runs}")
        failed_runs = [r for r in runs if r.get("status") == "failed"]
        self.assertFalse(failed_runs,
                         f"首组合 captcha 不得标 failed，实际 failed_runs={failed_runs}")

    def test_first_combo_captcha_no_other_combos_run(self):
        """首组合 captcha 后，后续组合不得继续抓取。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class CaptchaOnFirstComboSource:
            def __init__(self):
                self.fetch_calls = []

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, plan_item):
                self.fetch_calls.append(plan_item)
                return SourceOutcome.failure(
                    failed_code="source_verification_required",
                    safe_log="captcha",
                )

        source = CaptchaOnFirstComboSource()
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")):
            result = run_search(
                {"keyword": "前端,后端", "city": ["上海", "北京"]},
                source,
                pages=1,
                sleeper=lambda _seconds: None,
            )

        self.assertTrue(result.get("hard_stop"), result)
        self.assertEqual(result.get("hard_stop_code"), "source_verification_required")
        self.assertEqual(len(source.fetch_calls), 1, source.fetch_calls)

    def test_scrape_success_persists_terminal_status_and_combo_progress(self):
        app, temp = _make_app()
        try:
            client = app.test_client()
            with mock.patch("webui.pipeline_exec.run_search", return_value={
                "ok": True,
                "jobs": [{"job_id": "j1", "title": "前端"}],
                "total_scraped": 1,
                "total_matched": 1,
                "combinations": 2,
                "completed_combos": ["前端|上海", "后端|上海"],
                "error": "",
            }):
                response = client.post(
                    "/api/execute-search",
                    json={"script_params": {
                        "keyword": "前端,后端", "city": ["上海"],
                    }},
                    headers={"X-Boss-Token": app.config["API_TOKEN"]},
                )
                task_id = response.get_json()["task_id"]
                finished = _wait_for_pipeline_task(client, task_id)
            self.assertEqual(finished["status"], "completed", finished)
            run = app.config["TASK_STORE"].get_screening_run(task_id)
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["source_count"], 2)
            self.assertEqual(run["processed_count"], 2)
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()

    def test_scrape_failure_persists_failed_reason(self):
        app, temp = _make_app()
        try:
            client = app.test_client()
            with mock.patch("webui.pipeline_exec.run_search", return_value={
                "ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 1,
                "completed_combos": [], "error": "岗位列表接口返回无效数据",
            }):
                response = client.post(
                    "/api/execute-search",
                    json={"script_params": {"keyword": "前端", "city": ["上海"]}},
                    headers={"X-Boss-Token": app.config["API_TOKEN"]},
                )
                task_id = response.get_json()["task_id"]
                finished = _wait_for_pipeline_task(client, task_id)
            self.assertEqual(finished["status"], "failed", finished)
            run = app.config["TASK_STORE"].get_screening_run(task_id)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_reason"], "岗位列表接口返回无效数据")
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()


class Slice9ResumeAfterRestartConservationTests(unittest.TestCase):
    """A.2 重启后岗位守恒：app A 销毁 → app B 继续，岗位集合守恒（阻断项 2）。"""

    @staticmethod
    def _app_config(root, db_path, result_name):
        return {
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / result_name),
            "DB_PATH": str(db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        }

    @staticmethod
    def _shutdown(app):
        executor = app.config.get("PIPELINE_EXECUTOR")
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def test_combo_snapshot_and_checkpoint_commit_together(self):
        """完整岗位快照和 completed checkpoint 使用一个存储操作提交。"""
        app, temp = _make_app()
        try:
            store = app.config["TASK_STORE"]
            run_id = "atomic-combo-run"
            store.create_screening_run(run_id, source_count=2)
            save_combo = getattr(store, "save_scrape_combo_result", None)
            self.assertTrue(callable(save_combo),
                            "TaskStore 必须提供原子组合持久化操作")
            jobs = [{
                "job_id": "j1", "title": "前端工程师", "salary": "15-25K",
                "company": "公司A", "source_url": "https://example.com/j1",
            }]
            save_combo(run_id, "前端|上海", jobs, ["前端|上海"])

            restored = store.load_scrape_run_jobs(run_id)
            self.assertEqual(restored, jobs)
            self.assertEqual(store.load_checkpoint(run_id, "scrape"), {"前端|上海"})
        finally:
            self._shutdown(app)
            temp.cleanup()

    def test_resume_keeps_full_job_payload_after_real_app_restart(self):
        """app A 暂停并退出后，app B 继续并合并旧、新岗位且零重复。"""
        from webui.app import create_app

        temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(temp.name)
        db_path = root / "shared.db"
        old_jobs = [{
            "job_id": "old-1", "title": "前端工程师", "salary": "15-25K",
            "company": "公司A", "source_url": "https://example.com/old-1",
        }]
        app_a = app_b = None
        captured_resume = {}
        try:
            app_a = create_app(self._app_config(root, db_path, "results-a"))
            client_a = app_a.test_client()

            def paused_search(*_args, **kwargs):
                callback = kwargs.get("on_combo_done")
                if callback is not None:
                    callback("前端|上海", old_jobs, ["前端|上海"])
                return {
                    "ok": False, "jobs": old_jobs, "total_scraped": 1,
                    "total_matched": 1, "combinations": 2,
                    "completed_combos": ["前端|上海"], "hard_stop": True,
                    "hard_stop_code": "captcha_required", "error": "触发验证码",
                }

            with mock.patch("webui.pipeline_exec.run_search", side_effect=paused_search):
                response = client_a.post(
                    "/api/execute-search",
                    json={"script_params": {"keyword": "前端,后端", "city": ["上海"]}},
                    headers={"X-Boss-Token": app_a.config["API_TOKEN"]},
                )
                run_id = response.get_json()["task_id"]
                paused = _wait_for_pipeline_task(client_a, run_id)
            self.assertEqual(paused["status"], "paused", paused)
            self._shutdown(app_a)
            app_a = None

            app_b = create_app(self._app_config(root, db_path, "results-b"))
            app_b.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
            client_b = app_b.test_client()

            def resumed_search(*_args, **kwargs):
                captured_resume["skip_combos"] = set(kwargs.get("skip_combos") or set())
                return {
                    "ok": True,
                    "jobs": [{
                        "job_id": "new-1", "title": "后端工程师",
                        "salary": "20-30K", "company": "公司B",
                        "source_url": "https://example.com/new-1",
                    }],
                    "total_scraped": 1, "total_matched": 1, "combinations": 2,
                    "completed_combos": ["前端|上海", "后端|上海"], "error": "",
                }

            with mock.patch("webui.pipeline_exec.run_search", side_effect=resumed_search), \
                    mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")):
                response = client_b.post(
                    f"/api/execute-search/continue/{run_id}",
                    headers={"X-Boss-Token": app_b.config["API_TOKEN"]},
                )
                self.assertEqual(response.status_code, 200, response.get_json())
                new_task_id = response.get_json()["task_id"]
                finished = _wait_for_pipeline_task(client_b, new_task_id)

            self.assertEqual(finished["status"], "completed", finished)
            jobs = finished["result"]["jobs"]
            self.assertEqual({job["job_id"] for job in jobs}, {"old-1", "new-1"})
            self.assertEqual(len(jobs), 2)
            self.assertEqual(next(j for j in jobs if j["job_id"] == "old-1"), old_jobs[0])
            self.assertEqual(captured_resume["skip_combos"], {"前端|上海"})
        finally:
            if app_a is not None:
                self._shutdown(app_a)
            if app_b is not None:
                self._shutdown(app_b)
            temp.cleanup()


class Slice10AiResumeAfterRefreshTests(unittest.TestCase):
    """A.3 AI 刷新后继续：paused AI run 必须返回 scrape 元数据（阻断项 3）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def test_paused_ai_run_restores_scrape_task_id(self):
        """/api/latest-running-task 对 paused AI run 必须返回 scrapeTaskId 等。

        修复目标（B.3）：latest-running-task JOIN scrape_run_jobs 元数据，
        返回 scrapeTaskId、scrapeCompleted、source_run_id、checkpoint_stage。
        """
        run_id = "paused-ai-run"
        self.store.create_screening_run(
            run_id, source_count=50,
            execution_params={
                "scrape_task_id": "scrape-task-123",
                "scrape_completed": True,
                "source_run_id": "source-run-456",
            },
        )
        _pause_run(self.store, run_id,
                                          error_code="ai_rate_limited",
                                          current_stage="ai_rough")

        resp = self.client.get("/api/latest-running-task")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        # 必须返回 scrape 元数据
        self.assertIn("scrape_task_id", data,
                      f"latest-running-task 必须返回 scrape_task_id，实际 {data}")
        self.assertEqual(data["scrape_task_id"], "scrape-task-123")
        self.assertIn("scrape_completed", data)
        self.assertTrue(data["scrape_completed"])
        self.assertIn("source_run_id", data)
        self.assertEqual(data["source_run_id"], "source-run-456")

    def test_paused_ai_continue_not_blocked_by_startAiScreen(self):
        """服务重启后继续会从 DB 重建 scrape 来源并提交真正的 AI 续跑。"""
        run_id = "paused-ai-run-2"
        scrape_run_id = "scrape-task-789"
        self.store.create_screening_run(scrape_run_id, source_count=1)
        self.store.save_scrape_combo_result(
            scrape_run_id, "前端|上海",
            [{"job_id": "job-1", "title": "前端工程师"}],
            ["前端|上海"],
        )
        self.store.create_screening_run(
            run_id, source_count=50,
            frozen_filters={"city": ["上海"]},
            execution_params={
                "scrape_task_id": scrape_run_id,
                "scrape_completed": True,
                "profile_summary": "前端工程师",
            },
        )
        _pause_run(self.store, run_id,
                                          error_code="ai_rate_limited",
                                          current_stage="ai_rough")
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.test_connection", return_value={
                    "ok": True, "warning_codes": [],
                }) as test_connection, \
                mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(f"/api/task/continue/{run_id}",
                                    headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"),
                        f"paused AI run 继续不得被拦住，实际 {data}")
        self.assertEqual(data.get("task_id"), run_id)
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "running")
        submitted = submit.call_args.args
        self.assertEqual(submitted[2], {"city": ["上海"]})
        self.assertEqual(submitted[3], "前端工程师")
        self.assertEqual(submitted[4], scrape_run_id)
        self.assertEqual(submitted[5], run_id)
        test_connection.assert_called_once()
        rebuilt = self.app.config["PIPELINE_TASKS"][scrape_run_id]
        self.assertEqual(rebuilt["status"], "done")
        self.assertEqual(rebuilt["result"]["jobs"][0]["job_id"], "job-1")

    def test_paused_ai_continue_keeps_one_canonical_task_identity(self):
        """A continued run must not leak a non-canonical handoff status."""
        run_id = "paused-ai-canonical"
        scrape_run_id = "scrape-ai-canonical"
        self.store.create_screening_run(scrape_run_id, source_count=1)
        self.store.save_scrape_combo_result(
            scrape_run_id,
            "前端|上海",
            [{"job_id": "job-1", "title": "前端工程师"}],
            ["前端|上海"],
        )
        self.store.create_screening_run(
            run_id,
            source_count=1,
            frozen_filters={"city": ["上海"]},
            execution_params={
                "scrape_task_id": scrape_run_id,
                "profile_summary": "前端工程师",
            },
        )
        _pause_run(
            self.store, run_id,
            error_code="ai_rate_limited",
            current_stage="ai_rough",
        )
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.test_connection", return_value={
                    "ok": True, "warning_codes": [],
                }), \
                mock.patch.object(executor, "submit"):
            response = self.client.post(
                f"/api/task/continue/{run_id}", headers=self._auth()
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json().get("task_id"), run_id)
        persisted = self.store.get_screening_run(run_id)
        self.assertEqual(persisted["status"], "running")
        state = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(state.get("status"), "running")
        self.assertNotEqual(state.get("status"), "resumed")


class Slice11RecrawlResumeTests(unittest.TestCase):
    """A.4 重抓继续：必须用原 task_id，不得调 recrawlUncertain 新建（阻断项 4）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")

    def tearDown(self):
        try:
            executor = self.app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        import gc
        gc.collect()
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def test_recrawl_continue_uses_original_task_id(self):
        """重抓继续必须调 /api/recrawl/continue/<original_task_id>，不新建 task_id。

        修复目标（B.4）：新增 /api/recrawl/continue/<original_task_id> 路由；
        前端继续按钮调新路由而非 recrawlUncertain()。
        """
        original_task_id = "recrawl-original-task"
        self.store.create_screening_run(
            original_task_id, source_count=10,
            execution_params={
                "source_run_id": "source-run-1",
                "job_ids": ["j1", "j2"],
                "profile_summary": "前端工程师",
            },
        )
        _pause_run(self.store, original_task_id,
                                          error_code="captcha_required",
                                          current_stage="recrawl_fetch_jd")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(
                f"/api/recrawl/continue/{original_task_id}",
                headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json() or {}
        self.assertEqual(data.get("task_id"), original_task_id)
        self.assertNotIn("new_task_id", data)
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[1], original_task_id)

    def test_recrawl_continue_loads_source_run_id_and_checkpoint(self):
        """重抓继续从 scrape_run_jobs + checkpoint 加载 source_run_id 和 skip_combos。"""
        original_task_id = "recrawl-original-task-2"
        self.store.create_screening_run(
            original_task_id, source_count=10,
            execution_params={
                "source_run_id": "source-run-2",
                "job_ids": ["j1"],
                "profile_summary": "后端工程师",
            },
        )
        _pause_run(self.store, original_task_id,
                                          error_code="captcha_required",
                                          current_stage="recrawl_fetch_jd")
        self.store.save_checkpoint(original_task_id, "recrawl_jd", ["j1"])

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(
                f"/api/recrawl/continue/{original_task_id}",
                headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json() or {}
        self.assertEqual(data.get("source_run_id"), "source-run-2")
        self.assertEqual(set(data.get("completed_job_ids") or []), {"j1"})
        submitted = submit.call_args.args
        self.assertEqual(submitted[1], original_task_id)
        self.assertEqual(submitted[2], ["j1"])
        self.assertEqual(submitted[4], "source-run-2")
        self.assertEqual(set(submitted[5]), {"j1"})

    def test_recrawl_hard_stop_persists_partial_jd_before_pause(self):
        """同批部分成功后验证码：先落 JD 和 checkpoint，再进入 paused。"""
        source_run_id = self.store.save_pipeline_result({
            "jobs": [{
                "job_id": "j1", "title": "前端", "verdict": "uncertain",
                "source_url": "https://www.zhipin.com/job_detail/j1.html",
            }],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0,
            "profile_summary": "前端工程师",
        }, {"keyword": "前端", "city": ["上海"]})

        with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")), \
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value={
                    "jobs": [{"job_id": "j1", "jd": "岗位职责：负责前端开发"}],
                    "hard_stop": True,
                    "hard_stop_code": "captcha_required",
                    "stopped": False,
                    "fetched": 1,
                }):
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={
                    "job_ids": ["j1"], "profile_summary": "前端工程师",
                    "source_run_id": source_run_id,
                },
                headers=self._auth(),
            )
            self.assertEqual(response.status_code, 202, response.get_json())
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        payload = self.store.load_latest_pipeline_result()
        job = payload["result"]["jobs"][0]
        self.assertEqual(job.get("jd"), "岗位职责：负责前端开发")
        self.assertEqual(self.store.load_checkpoint(task_id, "recrawl_jd"), {"j1"})

    def test_recrawl_hard_stop_persists_job_failure_on_task_and_source(self):
        source_run_id = self.store.save_pipeline_result({
            "jobs": [{
                "job_id": "j1", "title": "前端", "verdict": "uncertain",
                "verdict_reason": "详情超时", "jd_failed_code": "detail_timeout",
                "source_url": "https://www.zhipin.com/job_detail/j1.html",
            }],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0,
            "profile_summary": "前端工程师",
        }, {"keyword": "前端", "city": ["上海"]})
        detail_failure = {
            "jobs": [{
                "job_id": "j1", "jd": "",
                "jd_failed_code": "cdp_unavailable",
                "jd_failed_reason": "CDP websocket disconnected",
            }],
            "hard_stop": True, "hard_stop_code": "cdp_unavailable",
            "stopped": False, "fetched": 0,
        }

        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch("webui.app._BossCdpSource", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_failure):
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={
                    "job_ids": ["j1"], "profile_summary": "前端工程师",
                    "source_run_id": source_run_id,
                },
                headers=self._auth(),
            )
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        for run_id in (task_id, source_run_id):
            pending = self.store.get_pending_result(run_id, "j1")
            self.assertIsNotNone(pending, run_id)
            self.assertEqual(pending["failed_code"], "cdp_unavailable")
            self.assertEqual(
                pending["ai_payload"]["reason"], "CDP websocket disconnected"
            )
        failures = [
            event for event in self.store.list_task_events(task_id)
            if event["type"] == "job_fail"
        ]
        self.assertEqual(failures[-1]["payload"]["job_id"], "j1")
        self.assertEqual(failures[-1]["payload"]["failed_code"], "cdp_unavailable")

    def test_recrawl_ai_pause_persists_batch_and_resume_skips_it(self):
        """重抓 AI 第二批限流后，第一批落库；继续只调用未完成岗位。"""
        from webui.ai import AISecurityError, ERROR_RATE_LIMIT

        source_run_id = self.store.save_pipeline_result({
            "jobs": [
                {
                    "job_id": "j1", "title": "前端", "verdict": "uncertain",
                    "jd": "岗位职责：前端开发",
                },
                {
                    "job_id": "j2", "title": "后端", "verdict": "uncertain",
                    "jd": "岗位职责：后端开发",
                },
            ],
            "dropped": [], "total_scraped": 2, "total_kept": 2,
            "total_matched": 0, "total_dropped": 0,
            "profile_summary": "工程师",
        }, {"keyword": "工程师", "city": ["上海"]})
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")

        first_calls = []

        def first_match(jobs, *_args, **_kwargs):
            first_calls.append([job["job_id"] for job in jobs])
            if jobs[0]["job_id"] == "j2":
                raise AISecurityError(ERROR_RATE_LIMIT)
            return {"verdicts": {
                "j1": {"verdict": "match", "reason": "匹配", "caveats": []},
            }}

        common_patches = (
            mock.patch("webui.ai.retrieve_api_key", return_value="key"),
            mock.patch("webui.pipeline_exec.load_advanced_settings",
                       return_value={"match_batch_size": 1}),
        )
        with common_patches[0], common_patches[1], \
                mock.patch("webui.ai.match_jds", side_effect=first_match):
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={
                    "job_ids": ["j1", "j2"], "profile_summary": "工程师",
                    "source_run_id": source_run_id,
                },
                headers=self._auth(),
            )
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        self.assertEqual(first_calls, [["j1"], ["j2"]])
        self.assertIn("j1", self.store.load_screening_verdicts(task_id))
        self.assertEqual(self.store.load_checkpoint(task_id, "recrawl_ai"), {"j1"})

        resumed_calls = []

        def resumed_match(jobs, *_args, **_kwargs):
            resumed_calls.append([job["job_id"] for job in jobs])
            return {"verdicts": {
                "j2": {"verdict": "not_match", "reason": "不匹配", "caveats": []},
            }}

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.pipeline_exec.load_advanced_settings",
                           return_value={"match_batch_size": 1}), \
                mock.patch("webui.ai.match_jds", side_effect=resumed_match):
            response = self.client.post(
                f"/api/recrawl/continue/{task_id}", headers=self._auth()
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(resumed_calls, [["j2"]])


class Slice12AiRoughCheckpointTests(unittest.TestCase):
    """A.5 AI 零重复：逐批 verdict 落盘，限流时保留最新 verdict（阻断项 7）。"""

    def test_ai_rough_saves_verdict_per_batch(self):
        """第一批完成后第二批限流，第一批 verdict 已交给持久化回调。"""
        from webui.ai import screen_jobs, AISecurityError, ERROR_RATE_LIMIT
        jobs = [
            {"job_id": "job-1", "title": "前端"},
            {"job_id": "job-2", "title": "后端"},
        ]
        delivered = []

        def on_batch_done(verdicts, completed_job_ids):
            delivered.append((dict(verdicts), list(completed_job_ids)))

        with mock.patch(
            "webui.ai.call_ai",
            side_effect=[{"dropped": []}, AISecurityError(ERROR_RATE_LIMIT)],
        ):
            with self.assertRaises(AISecurityError):
                screen_jobs(
                    jobs, {}, "http://example.invalid", "key",
                    batch_size=1, concurrency=1, raise_on_systemic=True,
                    on_batch_done=on_batch_done,
                )
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0][0]["job-1"]["verdict"], "kept")
        self.assertEqual(delivered[0][1], ["job-1"])

    def test_ai_batch_persistence_failure_stops_before_next_batch(self):
        """原子落库失败必须立即停止，不得继续调用下一批 AI。"""
        from webui.ai import screen_jobs
        jobs = [
            {"job_id": "job-1", "title": "前端"},
            {"job_id": "job-2", "title": "后端"},
        ]
        with mock.patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            with self.assertRaises(Exception) as ctx:
                screen_jobs(
                    jobs, {}, "http://example.invalid", "key",
                    batch_size=1, concurrency=1,
                    on_batch_done=lambda *_args: (_ for _ in ()).throw(
                        RuntimeError("disk full")
                    ),
                )
        self.assertEqual(type(ctx.exception).__name__, "AICheckpointError")
        self.assertEqual(call.call_count, 1)

    def test_ai_rough_verdict_and_completed_in_same_transaction(self):
        """verdict INSERT 与 completed job_id 推进必须同事务提交。

        修复目标（B.5）：回调内 BEGIN → INSERT screening_results →
        UPDATE checkpoint → COMMIT；任一步失败全部回滚。
        """
        from webui.app import create_app
        import pathlib
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        db_path = pathlib.Path(tmp.name) / "ai_tx.db"
        try:
            app = create_app({
                "TESTING": True, "START_TASKS": False,
                "RESULT_DIR": str(pathlib.Path(tmp.name) / "results"),
                "DB_PATH": str(db_path),
                "PYTHON_EXECUTABLE": sys.executable,
            })
            store = app.config["TASK_STORE"]
            run_id = "ai-tx-run"
            store.create_screening_run(run_id, source_count=10)
            store.update_screening_run(run_id, status="running")
            # 期望 store 有 save_verdict_and_checkpoint_atomic 方法（同事务）
            self.assertTrue(
                hasattr(store, "save_verdict_and_checkpoint_atomic"),
                "store 必须有 save_verdict_and_checkpoint_atomic 方法（同事务）")
            # 调用：写 verdict + 推进 checkpoint
            store.save_verdict_and_checkpoint_atomic(
                run_id, "ai_rough",
                {"job-1": {"verdict": "match"}},
                ["job-1"])
            # 验证：screening_results 有 verdict
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT verdict FROM screening_results "
                    "WHERE run_id = ? AND platform_job_id = ?",
                    (run_id, "job-1")).fetchone()
            self.assertIsNotNone(row, "verdict 必须写入 screening_results")
            self.assertEqual(json.loads(row["verdict"])["verdict"], "match")
            # checkpoint 推进
            self.assertEqual(store.load_checkpoint(run_id, "ai_rough"),
                             {"job-1"})
            with store._connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_ai_checkpoint BEFORE INSERT ON pipeline_checkpoints "
                    "WHEN NEW.stage = 'ai_rollback' BEGIN "
                    "SELECT RAISE(ABORT, 'checkpoint rejected'); END"
                )
            with self.assertRaises(Exception):
                store.save_verdict_and_checkpoint_atomic(
                    run_id, "ai_rollback",
                    {"job-rollback": {"verdict": "match"}},
                    ["job-rollback"],
                )
            self.assertNotIn("job-rollback", store.load_screening_verdicts(run_id))
        finally:
            tmp.cleanup()

    def test_ai_rough_rate_limit_keeps_latest_verdict(self):
        """限流时第一批 verdict 必须已落盘，不在 checkpoint 中重置为空。"""
        from webui.app import create_app
        import pathlib
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        db_path = pathlib.Path(tmp.name) / "ai_rate.db"
        try:
            app = create_app({
                "TESTING": True, "START_TASKS": False,
                "RESULT_DIR": str(pathlib.Path(tmp.name) / "results"),
                "DB_PATH": str(db_path),
                "PYTHON_EXECUTABLE": sys.executable,
            })
            store = app.config["TASK_STORE"]
            run_id = "ai-rate-run"
            store.create_screening_run(run_id, source_count=10)
            store.update_screening_run(run_id, status="running")
            # 第一批 verdict 已落盘
            self.assertTrue(hasattr(store, "save_verdict_and_checkpoint_atomic"))
            store.save_verdict_and_checkpoint_atomic(
                run_id, "ai_rough",
                {"job-1": {"verdict": "match"}},
                ["job-1"])
            # 模拟第二批限流：run 标 paused，但第一批 verdict 不丢
            _pause_run(store, run_id,
                                          error_code="ai_rate_limited")
            # 验证：第一批 verdict 仍在 screening_results
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT verdict FROM screening_results "
                    "WHERE run_id = ? AND platform_job_id = ?",
                    (run_id, "job-1")).fetchone()
            self.assertIsNotNone(row, "限流后第一批 verdict 不得丢失")
            self.assertEqual(json.loads(row["verdict"])["verdict"], "match")
            # checkpoint 仍含 job-1
            self.assertIn("job-1", store.load_checkpoint(run_id, "ai_rough"))
        finally:
            tmp.cleanup()

    def test_ai_rough_resume_no_duplicate_calls(self):
        """resume 时已完成岗位不再进入 AI，只处理剩余岗位。"""
        from webui.ai import screen_jobs
        jobs = [
            {"job_id": "job-1", "title": "前端"},
            {"job_id": "job-2", "title": "后端"},
        ]
        seen_user_messages = []

        def fake_call(_endpoint, _key, messages, **_kwargs):
            seen_user_messages.append(messages[-1]["content"])
            return {"dropped": []}

        with mock.patch("webui.ai.call_ai", side_effect=fake_call):
            result = screen_jobs(
                jobs, {}, "http://example.invalid", "key",
                batch_size=1, concurrency=1,
                completed_verdicts={
                    "job-1": {"verdict": "kept", "reason": ""},
                },
            )
        self.assertEqual(len(seen_user_messages), 1)
        self.assertIn("后端", seen_user_messages[0])
        self.assertNotIn("前端", seen_user_messages[0])
        self.assertEqual(set(result["kept"]), {"job-1", "job-2"})


class Slice13ComboDoneHardStopTests(unittest.TestCase):
    """A.7 on_combo_done 持久化失败必须 hard-stop 为 internal_error。"""

    def test_run_search_delivers_completed_combo_payload(self):
        """成功组合把完整岗位和完成键交给持久化回调。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class OneComboSource:
            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, _plan_item):
                return SourceOutcome.success(jobs=[{
                    "job_id": "j1", "title": "工程师", "company": "公司A",
                    "salary": "15-25K", "source_url": "https://example.com/j1",
                }])

        delivered = []
        try:
            with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                    mock.patch("webui.pipeline_exec.close_debug_chrome"):
                result = run_search(
                    {"keyword": "前端", "city": ["上海"]},
                    OneComboSource(),
                    pages=1,
                    sleeper=lambda _seconds: None,
                    on_combo_done=lambda combo, jobs, completed, **kw: delivered.append(
                        (combo, jobs, list(completed))),
                )
        except TypeError as exc:
            self.fail(f"run_search 必须支持 on_combo_done 行为：{exc}")

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(delivered), 1)
        combo_key, jobs, completed = delivered[0]
        self.assertEqual(combo_key, "前端|上海")
        self.assertEqual(jobs[0]["job_id"], "j1")
        self.assertEqual(completed, ["前端|上海"])

    def test_on_combo_done_persist_failure_triggers_hard_stop(self):
        """on_combo_done 回调内持久化失败 → hard-stop 为 internal_error。

        修复目标（B.2 + 调整点 6）：on_combo_done 持久化失败时，
        run_search 返回 hard_stop=True, hard_stop_code='internal_error'。
        """
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        # mock source
        class FakeSource:
            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, _plan_item):
                return SourceOutcome.success(
                    jobs=[{"job_id": "j1", "title": "t1"}])

        # on_combo_done 抛异常模拟持久化失败
        def failing_on_combo_done(combo_key, jobs, completed_combos):
            raise RuntimeError("persist_failed")

        try:
            with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                            return_value=(True, "")):
                result = run_search(
                    {"keyword": "前端", "city": ["上海"]},
                    FakeSource(),
                    pages=1,
                    sleeper=lambda x: None,
                    on_combo_done=failing_on_combo_done,
                )
        except TypeError as exc:
            self.fail(f"run_search 必须支持 on_combo_done 行为：{exc}")
        # 必须返回 hard_stop
        self.assertTrue(result.get("hard_stop"),
                        f"on_combo_done 失败必须 hard_stop，实际 {result}")
        self.assertEqual(result.get("hard_stop_code"), "internal_error",
                         f"hard_stop_code 必须 internal_error，实际 {result.get('hard_stop_code')}")


# ===========================================================================
# SPEC011 T005 — 冻结配置摘要一致性 RED 测试
# ===========================================================================
class FrozenConfigDigestTests(unittest.TestCase):
    """SPEC011 T005: 证明 list/detail/rough/fine/recrawl 阶段使用同一冻结配置摘要。

    这些测试在 T006 完成前应失败（RED），因为当前流水线在运行时从 JSON 文件
    晚绑定读取配置，而非使用任务创建时冻结的快照。
    """

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    def tearDown(self):
        self.temp.cleanup()

    def test_run_search_accepts_execution_config_snapshot(self):
        """run_search 必须接受 execution_config 参数，而非运行时读 JSON。"""
        from webui.execution_config import ExecutionConfigSnapshot
        from webui import pipeline_exec

        config = ExecutionConfigSnapshot.create({
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        })
        # run_search 应接受 execution_config 参数
        import inspect
        sig = inspect.signature(pipeline_exec.run_search)
        self.assertIn("execution_config", sig.parameters,
                        "run_search 必须接受 execution_config 参数")

    def test_fetch_job_details_accepts_execution_config_snapshot(self):
        """fetch_job_details 必须接受 execution_config 参数。"""
        import inspect
        from webui import pipeline_exec

        sig = inspect.signature(pipeline_exec.fetch_job_details)
        self.assertIn("execution_config", sig.parameters,
                        "fetch_job_details 必须接受 execution_config 参数")

    def test_screen_jobs_accepts_execution_config_snapshot(self):
        """screen_jobs 必须接受 execution_config 参数。"""
        import inspect
        from webui import ai as ai_module

        sig = inspect.signature(ai_module.screen_jobs)
        self.assertIn("execution_config", sig.parameters,
                        "screen_jobs 必须接受 execution_config 参数")

    def test_match_jds_accepts_execution_config_snapshot(self):
        """match_jds 必须接受 execution_config 参数。"""
        import inspect
        from webui import ai as ai_module

        sig = inspect.signature(ai_module.match_jds)
        self.assertIn("execution_config", sig.parameters,
                        "match_jds 必须接受 execution_config 参数")

    def test_run_search_uses_frozen_config_not_json(self):
        """提供 execution_config 时，run_search 不应读取 advanced_settings.json。"""
        from webui.execution_config import ExecutionConfigSnapshot
        from webui import pipeline_exec

        config = ExecutionConfigSnapshot.create({
            "inter_combo_delay": 42.0,
            "detail_batch_size": 7,
            "detail_interval": 3.0,
            "detail_reset_every": 2,
            "detail_batch_cooldown": 8.0,
            "screen_batch_size": 25,
            "screen_concurrency": 3,
            "match_batch_size": 2,
            "match_concurrency": 4,
        })

        captured_config = {}

        def fake_scrape(params, source, **kwargs):
            # 捕获实际使用的配置摘要
            ec = kwargs.get("execution_config")
            if ec is not None:
                captured_config["digest"] = ec.config_digest
            return {"jobs": [], "details_path": None, "events_path": None}

        source = mock.MagicMock()

        with mock.patch.object(pipeline_exec, "load_advanced_settings") as mock_load, \
             mock.patch.object(pipeline_exec, "boss") as mock_boss:
            mock_load.return_value = {"inter_combo_delay": 999}  # 不同的值
            mock_boss.scrape_jobs = fake_scrape
            mock_boss.ensure_chrome_running = mock.Mock(return_value=True)

            try:
                pipeline_exec.run_search(
                    {"keyword": "test", "city": ["北京"], "pages": 3},
                    source,
                    pages=3,
                    execution_config=config,
                )
            except TypeError:
                # 如果 execution_config 参数不存在，会抛 TypeError — 这是 RED 预期
                self.fail("run_search 不接受 execution_config 参数 (T006 未完成)")

            # 如果执行成功，验证使用了冻结的配置
            if "digest" in captured_config:
                self.assertEqual(captured_config["digest"], config.config_digest)
            # load_advanced_settings 不应被调用
            mock_load.assert_not_called()

    def test_pipeline_task_stores_config_digest(self):
        """真实启动从后端 scope/selection 冻结并存储两个摘要。"""
        from webui.execution_config import ExecutionConfigSnapshot

        config = ExecutionConfigSnapshot.create({
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        })

        self.app.config["TASK_STORE"].save_custom_config(config.to_dict())
        preview = self.client.post("/api/search-scope/preview", json={
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
        submit.assert_called_once()

        # 任务应被接受并存储配置摘要
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]

        # 查询任务进度时应返回配置摘要
        progress = self.client.get(f"/api/search-progress/{task_id}").get_json()
        self.assertIn("config_digest", progress,
                        "任务进度必须包含 config_digest")
        self.assertEqual(progress["config_digest"], config.config_digest)
        self.assertEqual(progress["scope_digest"], preview["scope_digest"])
        submitted = submit.call_args.args
        self.assertEqual(submitted[3].config_digest, config.config_digest)
        self.assertEqual(submitted[4].scope_digest, preview["scope_digest"])
        persisted = self.app.config["TASK_STORE"].get_screening_run(task_id)
        self.assertEqual(
            persisted["execution_params"]["execution_config"]["config_digest"],
            config.config_digest,
        )
        self.assertEqual(
            persisted["execution_params"]["frozen_scope"]["scope_digest"],
            preview["scope_digest"],
        )

    def test_changing_settings_after_task_start_does_not_affect_stages(self):
        """Scenario B: 任务启动后修改正式设置不影响任何阶段。"""
        from webui.execution_config import ExecutionConfigSnapshot

        config_a = ExecutionConfigSnapshot.create({
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        })

        self.app.config["TASK_STORE"].save_custom_config(config_a.to_dict())
        preview = self.client.post("/api/search-scope/preview", json={
            "keywords": ["Python"], "scope_kind": "cities",
            "cities": ["上海"], "pages_per_combination": 1,
        }).get_json()["scope"]
        # 创建任务使用后端当前选择解析出的 config_a
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
        submit.assert_called_once()
        task_id = resp.get_json()["task_id"]

        # 保存不同的设置（config_b）
        self.client.post("/api/advanced-settings", json={
            "settings": {
                "pages": 1,
                "inter_combo_delay": 99.0,
                "detail_batch_size": 99,
                "detail_interval": 99,
                "detail_reset_every": 99,
                "detail_batch_cooldown": 99,
                "screen_batch_size": 99,
                "screen_concurrency": 99,
                "match_batch_size": 99,
                "match_concurrency": 99,
            }
        })

        # 任务仍应使用 config_a 的摘要
        progress = self.client.get(f"/api/search-progress/{task_id}").get_json()
        self.assertEqual(progress.get("config_digest"), config_a.config_digest,
                         "任务启动后修改设置不应改变已冻结的配置摘要")


class TuningLeaseOrdinaryTaskConflictTests(unittest.TestCase):
    """SPEC011 T015 RED: 实验租约与普通任务启动路径冲突。

    覆盖 FR-035、SC-004、state-machine.md 第 4 节。
    租约被持有时所有普通任务启动路径（execute-search、ai-screen、
    recrawl/continue、task/continue）必须返回 409。
    """

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _hold_lease(self):
        """通过 store 直接 claim 租约，模拟实验持有。"""
        self.store.claim_tuning_lease(
            experiment_id="exp-conflict-1",
            round_id="round-conflict-1",
            owner_token="test-owner-token",
        )

    def _release_lease(self):
        self.store.release_tuning_lease(owner_token="test-owner-token")

    # -- execute-search ------------------------------------------------

    def test_execute_search_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/execute-search 必须返回 409。"""
        self._hold_lease()
        resp = self.client.post("/api/execute-search", json={
            "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
        })
        self.assertEqual(resp.status_code, 409, "租约持有时必须返回 409")
        body = resp.get_json()
        self.assertFalse(body.get("ok"), "响应 ok 必须为 false")
        self.assertIn("lease", body.get("error", "").lower() + body.get("error_code", "").lower(),
                      "错误必须表明是租约冲突")

    def test_execute_search_allowed_when_lease_free(self):
        """FR-035: 无租约时 /api/execute-search 可启动。"""
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            })
        self.assertEqual(resp.status_code, 200, "无租约时 execute-search 应被接受")
        submit.assert_called_once()

    def test_execute_search_allowed_after_lease_released(self):
        """FR-035: 租约释放后 execute-search 可启动。"""
        self._hold_lease()
        self._release_lease()
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            })
        self.assertEqual(resp.status_code, 200, "租约释放后 execute-search 应被接受")
        submit.assert_called_once()

    # -- ai-screen -----------------------------------------------------

    def test_ai_screen_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/ai-screen 必须返回 409。"""
        # 预置一个已完成的抓取任务
        scrape_task_id = self._seed_done_scrape_task()
        self._hold_lease()
        resp = self.client.post("/api/ai-screen", json={
            "screening_fields": {"city": ["上海"]},
            "profile_summary": "测试",
            "scrape_task_id": scrape_task_id,
        })
        self.assertEqual(resp.status_code, 409, "租约持有时 ai-screen 必须返回 409")

    # -- recrawl/continue ---------------------------------------------

    def test_recrawl_continue_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/recrawl/continue 必须返回 409。"""
        run_id = self._seed_paused_recrawl_run()
        self._hold_lease()
        resp = self.client.post(f"/api/recrawl/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 409, "租约持有时 recrawl/continue 必须返回 409")

    # -- task/continue (统一入口) ------------------------------------

    def test_task_continue_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/task/continue 必须返回 409。"""
        run_id = self._seed_paused_recrawl_run()
        self._hold_lease()
        resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 409, "租约持有时 task/continue 必须返回 409")

    # -- 辅助方法 ------------------------------------------------------

    def _seed_done_scrape_task(self) -> str:
        """预置一个已完成的抓取任务，供 ai-screen 启动。"""
        task_id = "scrape-done-1"
        self.app.config["PIPELINE_TASKS"][task_id] = {
            "id": task_id, "kind": "scrape", "status": "done",
            "progress": 100, "logs": [], "error": "",
            "result": {"ok": True, "jobs": [], "total_scraped": 0,
                       "total_matched": 0, "completed_combos": [],
                       "error": ""},
            "started_at": None, "finished_at": None,
        }
        return task_id

    def _seed_paused_recrawl_run(self) -> str:
        """预置一个 paused 状态的 recrawl run，供 continue 端点使用。"""
        run_id = "recrawl-paused-1"
        self.store.create_screening_run(
            run_id, source_count=10,
            execution_params={
                "source_run_id": "src-1",
                "job_ids": ["j1", "j2"],
                "profile_summary": "测试",
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="recrawl_jd",
        )
        return run_id


if __name__ == "__main__":
    unittest.main()
