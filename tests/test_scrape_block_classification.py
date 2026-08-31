# -*- coding: utf-8 -*-
"""034-scrape-block-handling 聚焦测试：两处接线 + 事件归类 + 兜底。

覆盖（Spec 034 / SC-001~SC-004）：
- 脚本主入口薄映射：RiskControlError / LoginRequiredError / RequestLimitExceededError
  → 结构化失败行（带精确码）+ 对应退出码（R1）；
- webui 分类侧：失败行解析出精确账号级码（非 source_unknown_error）；
- 详情批非零退出：读事件文件按真实 safe_code 逐岗位归类（R2），账号级码推进
  熔断信号、单条软失败码带原因；
- 兜底：事件文件缺失 → 回退 _classify_failed_code，不崩溃（FR-007）。
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts.boss.exceptions import (
    LoginRequiredError,
    RequestLimitExceededError,
    RiskControlError,
)
from scripts.boss_cdp_signals import map_block_exception
from webui.source_boss_detail_events import event_outcome_code, index_events_by_url
from webui.source import _classify_failed_code


# ---------------------------------------------------------------------------
# R1：脚本主入口薄映射（异常 → 失败行 code + 退出码）
# ---------------------------------------------------------------------------


class MapBlockExceptionTests(unittest.TestCase):
    """三类账号级阻断异常映射为结构化失败行 + 退出码。"""

    def test_risk_control_error_uses_exc_code_and_exit_10(self):
        exc = RiskControlError("验证码页面", code="source_verification_required")
        self.assertEqual(
            map_block_exception(exc), ("source_verification_required", 10))

    def test_risk_control_error_missing_code_falls_back_status_unclear(self):
        exc = RiskControlError("无法确认状态")
        self.assertEqual(map_block_exception(exc), ("source_status_unclear", 10))

    def test_login_required_maps_to_exit_1(self):
        exc = LoginRequiredError("未检测到登录态")
        self.assertEqual(map_block_exception(exc), ("source_login_required", 1))

    def test_request_limit_maps_to_exit_11(self):
        exc = RequestLimitExceededError("已达请求上限")
        self.assertEqual(
            map_block_exception(exc), ("source_request_limit_exceeded", 11))

    def test_unsupported_exception_raises_type_error(self):
        with self.assertRaises(TypeError):
            map_block_exception(ValueError("unsupported"))


class ScriptMainFailureLineTests(unittest.TestCase):
    """脚本 __main__ 薄映射产出的失败行可被 webui 精确分类。"""

    def _line(self, code, hint=""):
        # 等价于 boss_cdp_raw __main__ 的 emit_failure_line 调用路径
        from scripts.boss_cdp_signals import emit_failure_line
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            emit_failure_line(code, hint)
        return buf.getvalue()

    def test_verification_failure_line_classifies_precisely(self):
        captured = self._line("source_verification_required", "验证码")
        self.assertEqual(
            _classify_failed_code(10, captured), "source_verification_required")

    def test_login_failure_line_classifies_precisely(self):
        captured = self._line("source_login_required", "登录失效")
        self.assertEqual(
            _classify_failed_code(1, captured), "source_login_required")

    def test_request_limit_failure_line_classifies_precisely(self):
        captured = self._line("source_request_limit_exceeded", "请求上限")
        self.assertEqual(
            _classify_failed_code(11, captured), "source_request_limit_exceeded")

    def test_risk_control_without_line_falls_back_not_unknown(self):
        # 缺失败行时退出码 10 兜底为 status_unclear（不是 unknown），
        # 但若代码路径正确 emit 失败行，则分类精确（上一组断言覆盖）。
        self.assertEqual(
            _classify_failed_code(10, "some tail output"),
            "source_status_unclear")


# ---------------------------------------------------------------------------
# R2：详情批非零退出 → 事件文件逐岗位归类（纯函数 + fetch_details_batch）
# ---------------------------------------------------------------------------


class IndexEventsByUrlTests(unittest.TestCase):
    """事件文件 → {job_link: event} 索引（first wins，基础校验）。"""

    def test_indexes_valid_detail_events(self):
        events = [
            {"kind": "detail", "status": "failed", "job_id": "u1",
             "duration_ms": 10, "safe_code": "source_rate_limited"},
            {"kind": "detail", "status": "completed", "job_id": "u2",
             "duration_ms": 20, "safe_code": "ok"},
        ]
        indexed = index_events_by_url(events, {"u1", "u2"})
        self.assertEqual(set(indexed), {"u1", "u2"})
        self.assertEqual(indexed["u1"]["safe_code"], "source_rate_limited")

    def test_skips_non_detail_and_mismatched_jobs(self):
        events = [
            {"kind": "runtime", "event": "x", "job_id": "u1",
             "duration_ms": 0, "safe_code": "ok"},
            {"kind": "detail", "status": "failed", "job_id": "u3",
             "duration_ms": 10, "safe_code": "source_blocked"},
            None,
        ]
        indexed = index_events_by_url(events, {"u1", "u2"})
        self.assertEqual(indexed, {})

    def test_first_occurrence_wins(self):
        events = [
            {"kind": "detail", "status": "failed", "job_id": "u1",
             "duration_ms": 10, "safe_code": "source_rate_limited"},
            {"kind": "detail", "status": "completed", "job_id": "u1",
             "duration_ms": 20, "safe_code": "ok"},
        ]
        indexed = index_events_by_url(events, {"u1"})
        self.assertEqual(indexed["u1"]["safe_code"], "source_rate_limited")


class EventOutcomeCodeTests(unittest.TestCase):
    """事件 → 岗位失败码（账号级 vs 单条软失败分流）。"""

    def test_unavailable_uses_safe_code(self):
        event = {"status": "unavailable", "safe_code": "source_login_required"}
        self.assertEqual(event_outcome_code(event, "fallback"), "source_login_required")

    def test_failed_uses_safe_code(self):
        event = {"status": "failed", "safe_code": "source_invalid_output"}
        self.assertEqual(event_outcome_code(event, "fallback"), "source_invalid_output")

    def test_cancelled_ok_falls_back_unknown(self):
        event = {"status": "cancelled", "safe_code": "ok"}
        self.assertEqual(event_outcome_code(event, "fallback"), "source_unknown_error")

    def test_cancelled_specific_code_kept(self):
        event = {"status": "cancelled", "safe_code": "source_rate_limited"}
        self.assertEqual(event_outcome_code(event, "fallback"), "source_rate_limited")

    def test_none_and_unknown_status_fall_back(self):
        self.assertEqual(event_outcome_code(None, "fallback"), "fallback")
        event = {"status": "completed", "safe_code": "ok"}
        self.assertEqual(event_outcome_code(event, "fallback"), "fallback")


class FetchDetailsBatchNonZeroExitTests(unittest.TestCase):
    """fetch_details_batch 非零退出：读事件文件逐岗位归类 + 熔断信号 + 兜底。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _make_source(self, **overrides):
        from webui.source import BossCdpSource
        kwargs = dict(
            artifact_root=self.artifact_root,
            cdp_port=9222,
            in_process=True,
        )
        kwargs.update(overrides)
        return BossCdpSource(**kwargs)

    def _write_json(self, path, payload):
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _write_events(self, path, events):
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def test_nonzero_exit_classifies_by_event_safe_code(self):
        """非零退出时账号级码岗位按事件真实 safe_code 归类，软失败码带原因。"""
        source = self._make_source()
        detail_path = str(self.artifact_root / "batch_blocked.json")
        jobs = [
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
             "job_link": "https://www.zhipin.com/job/1"},
            {"job_id": "j2", "source_url": "https://www.zhipin.com/job/2",
             "job_link": "https://www.zhipin.com/job/2"},
            {"job_id": "j3", "source_url": "https://www.zhipin.com/job/3",
             "job_link": "https://www.zhipin.com/job/3"},
        ]
        # j1 已落盘（抢救成功）；j2 账号级限流；j3 单条软失败；j4 无事件（兜底）
        self._write_json(detail_path, [
            {"job_id": "j1", "jd": "已抓JD",
             "job_link": "https://www.zhipin.com/job/1"},
        ])
        self._write_events(f"{detail_path}.events.jsonl", [
            {"kind": "detail", "status": "failed", "job_id": "https://www.zhipin.com/job/2",
             "duration_ms": 100, "safe_code": "source_rate_limited",
             "safe_hint": "操作频繁"},
            {"kind": "detail", "status": "failed", "job_id": "https://www.zhipin.com/job/3",
             "duration_ms": 100, "safe_code": "source_invalid_output",
             "safe_hint": "页面解析异常"},
        ])
        with mock.patch.object(source, "_run_command",
                               return_value=(10, "__CAREERSCOUT_FAILED__ code=source_rate_limited hint=操作频繁")):
            results = source.fetch_details_batch(
                jobs, detail_output_path=detail_path)

        self.assertEqual(len(results), 3)
        # j1 抢救成功
        self.assertTrue(results["j1"].ok, results["j1"].safe_log)
        self.assertIn("rescued_partial", results["j1"].safe_log)
        # j2 账号级码来自事件文件（非退出码兜底）
        self.assertFalse(results["j2"].ok)
        self.assertEqual(results["j2"].failed_code, "source_rate_limited")
        self.assertIn("操作频繁", results["j2"].failed_reason or "")
        # j3 单条软失败码来自事件文件
        self.assertFalse(results["j3"].ok)
        self.assertEqual(results["j3"].failed_code, "source_invalid_output")

    def test_nonzero_exit_no_events_falls_back_to_exit_code(self):
        """事件文件缺失/无记录 → 回退 _classify_failed_code，不崩溃。"""
        source = self._make_source()
        detail_path = str(self.artifact_root / "batch_fallback.json")
        jobs = [
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
             "job_link": "https://www.zhipin.com/job/1"},
        ]
        with mock.patch.object(source, "_run_command",
                               return_value=(11, "")):
            results = source.fetch_details_batch(
                jobs, detail_output_path=detail_path)

        self.assertFalse(results["j1"].ok)
        self.assertEqual(results["j1"].failed_code, "source_request_limit_exceeded")

    def test_nonzero_exit_account_signal_advances_breaker(self):
        """账号级码岗位推进熔断器连续信号（连续 2 次开闸）。"""
        source = self._make_source()
        detail_path = str(self.artifact_root / "batch_signal.json")
        jobs = [
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
             "job_link": "https://www.zhipin.com/job/1"},
            {"job_id": "j2", "source_url": "https://www.zhipin.com/job/2",
             "job_link": "https://www.zhipin.com/job/2"},
        ]
        self._write_events(f"{detail_path}.events.jsonl", [
            {"kind": "detail", "status": "failed", "job_id": "https://www.zhipin.com/job/1",
             "duration_ms": 100, "safe_code": "source_login_required"},
            {"kind": "detail", "status": "failed", "job_id": "https://www.zhipin.com/job/2",
             "duration_ms": 100, "safe_code": "source_login_required"},
        ])
        with mock.patch.object(source, "_run_command",
                               return_value=(1, "__CAREERSCOUT_FAILED__ code=source_login_required hint=401")):
            results = source.fetch_details_batch(
                jobs, detail_output_path=detail_path)

        # 两条账号级信号 → 熔断器打开（连续 2 次）
        self.assertTrue(source.breaker.is_open())
        self.assertEqual(source.breaker.open_failure_code(), "source_login_required")
        for outcome in results.values():
            self.assertEqual(outcome.failed_code, "source_login_required")


if __name__ == "__main__":
    unittest.main()
