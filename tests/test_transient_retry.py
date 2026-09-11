# -*- coding: utf-8 -*-
"""039 偶发失败一次重试：S1/S2/S3/S5 后端聚焦测试。

覆盖：
- S1 偶发超时 → 自动重试一次、从断点续抓、重试成功零失败痕迹；
- S2 两次尝试均超时 → 先记 retry_scheduled，再按跳过定稿；
- S3 智联列表「连不上浏览器」正名 + 进入失联重启通道 + 仍失联硬停暂停；
- S5 需人工介入类失败不自动重试（回归）；
- 尝试预算：失联重启重试、登录复核重试与偶发重试共享一次额度（SC-002）。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from webui import pipeline_exec
from webui.error_registry import ERROR_USER_MESSAGES
from webui.source import SourceOutcome
from webui.store import TaskStore


class _ScriptedSource:
    """按调用次数返回预设 outcome，并记录每次尝试的计划输入。

    ``pages_evidence``：第一次尝试按页回调前 N 页完成事件（模拟断点）。
    """

    cdp_port = 9222

    def __init__(self, outcomes, *, platform="boss", pages_evidence=0, recheck=None):
        self.platform = platform
        self._outcomes = list(outcomes)
        self._pages_evidence = int(pages_evidence)
        self.calls = 0
        self.start_pages: list[int] = []
        self.existing_jobs: list[list] = []
        if recheck is not None:
            self.recheck_login = recheck

    def preflight(self):
        return SourceOutcome.success()

    def fetch_list(self, plan_item, *, on_page_completed=None):
        self.calls += 1
        self.start_pages.append(int(plan_item.get("start_page") or 1))
        self.existing_jobs.append(list(plan_item.get("existing_jobs") or []))
        if self._pages_evidence and self.calls == 1 and on_page_completed is not None:
            for page in range(1, self._pages_evidence + 1):
                on_page_completed({
                    "kind": "page_completed", "page": page, "resume_page": page + 1,
                    "jobs_count": 20, "jobs_delta": 20,
                    "jobs_snapshot": [{"job_id": f"j{page}"}],
                })
        return self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]


class _RetryCase(unittest.TestCase):
    """共享：临时库 + 白箱读数 + 受控 run_search。"""

    run_id = "039-transient-retry"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "webui.db")
        self.resume_pages: dict[str, int] = {}
        self.resume_jobs: dict[str, list[dict]] = {}
        self.issues: list[tuple] = []
        self._case_no = 1

    def tearDown(self):
        self.temp.cleanup()

    def _fresh_case(self, outcomes, **source_kwargs):
        """单个测试方法内跑多个子场景时，换任务号避免白箱 run 重名。"""
        self.run_id = f"039-transient-retry-{self._case_no}"
        self._case_no += 1
        self.resume_pages, self.resume_jobs, self.issues = {}, {}, []
        return _ScriptedSource(outcomes, **source_kwargs)

    def _run_search(self, source, *, platform="boss", params=None, pages=10,
                    chrome_side_effect=None, **kwargs):
        """在固定浏览器/存储环境下跑一轮抓取；chrome 就绪结果可控。"""
        chrome = (
            mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                       side_effect=chrome_side_effect)
            if chrome_side_effect is not None
            else mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                            return_value=(True, ""))
        )
        with chrome, mock.patch("webui.pipeline_exec.close_debug_chrome"):
            return pipeline_exec.run_search(
                params or {"keyword": "A", "city": ["上海"]},
                source, pages=pages, sleeper=lambda _seconds: None,
                task_id=self.run_id, task_event_store=self.store,
                resume_pages=self.resume_pages, resume_jobs=self.resume_jobs,
                on_issue=lambda combo, entry: self.issues.append((combo, dict(entry))),
                close_chrome_on_success=False, **kwargs,
            )

    def _events(self, event_type=None):
        run = self.store.get_whitebox_run("scrape", self.run_id)
        self.assertIsNotNone(run, "白箱 run 缺失")
        events = self.store.list_whitebox_events(run["id"])
        if event_type is None:
            return events
        return [event for event in events if event["event_type"] == event_type]

    def _payload(self, event):
        return json.loads(event["payload_json"])


class TransientRetryPolicyTests(unittest.TestCase):
    """白名单、额度与事实构造的纯逻辑契约。"""

    def test_whitelist_matches_contract(self):
        from webui.pipeline_exec_retry import TRANSIENT_RETRY_CODES

        self.assertEqual(TRANSIENT_RETRY_CODES, frozenset({
            "source_timeout", "source_unreachable", "source_invalid_output",
            "source_unknown_error", "source_result_write_failed",
        }))

    def test_only_whitelisted_codes_with_budget_are_retryable(self):
        from webui.pipeline_exec_retry import is_transient_retryable

        self.assertTrue(is_transient_retryable("source_timeout", retry_used=False))
        self.assertFalse(is_transient_retryable("source_timeout", retry_used=True))
        for code in (
            "source_login_required", "source_verification_required",
            "source_rate_limited", "source_blocked", "source_account_restricted",
            "source_request_limit_exceeded", "source_cdp_unavailable",
            "source_input_drift", "source_not_found", "internal_error", None, "",
        ):
            with self.subTest(code=code):
                self.assertFalse(is_transient_retryable(code, retry_used=False))

    def test_event_fact_is_single_info_evidence(self):
        from webui.pipeline_exec_retry import retry_event_fact

        fact = retry_event_fact("A|上海", SourceOutcome.failure(
            failed_code="source_timeout", safe_log="reason=第 9 页无响应"))
        self.assertEqual(fact["event_type"], "retry_scheduled")
        self.assertEqual(fact["idempotency_key"], "retry-scheduled:A|上海")
        self.assertEqual(fact["unit_key"], "A|上海")
        self.assertEqual(fact["attempt_no"], 1)
        self.assertFalse(fact["required_evidence"])
        self.assertEqual(fact["severity"], "info")
        self.assertEqual(fact["payload"]["failed_code"], "source_timeout")
        self.assertEqual(fact["payload"]["error_reason"], "第 9 页无响应")

    def test_resume_fields_follow_checkpoint(self):
        from webui.pipeline_exec_retry import retry_resume_fields

        fields = retry_resume_fields(
            {"A|上海": 9}, {"A|上海": [{"job_id": "j8"}]}, "A|上海", 10)
        self.assertEqual(fields, {"start_page": 9, "existing_jobs": [{"job_id": "j8"}]})
        self.assertEqual(retry_resume_fields({}, {}, "A|上海", 10)["start_page"], 1)
        # 断点越过目标页数时不得给出非法起始页
        self.assertEqual(
            retry_resume_fields({"A|上海": 99}, {}, "A|上海", 10)["start_page"], 10)


class TransientRetrySuccessTests(_RetryCase):
    """S1：偶发失败自动重试一次，重试成功零痕迹。"""

    def test_timeout_retries_from_checkpoint_without_failure_trace(self):
        source = _ScriptedSource(
            [
                SourceOutcome.failure(
                    failed_code="source_timeout", safe_log="reason=第 9 页 30 秒无响应"),
                SourceOutcome.success(jobs=[{"job_id": "j1"}], scope_complete=True),
            ],
            # 智联计划适配器会回填 existing_jobs，用来验证已抓岗位快照被带入重试。
            platform="zhilian", pages_evidence=8,
        )
        result = self._run_search(source)

        self.assertEqual(source.calls, 2)
        self.assertEqual(source.start_pages, [1, 9])
        self.assertEqual(source.existing_jobs[1], [{"job_id": "j8"}])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["completed_combos"], ["A|上海"])
        self.assertEqual(result["total_scraped"], 1)
        self.assertEqual(result["integrity"]["conclusion"], "succeeded")

        retries = self._events("retry_scheduled")
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["unit_key"], "A|上海")
        self.assertEqual(int(retries[0]["attempt_no"]), 1)
        self.assertFalse(retries[0]["required_evidence"])
        self.assertEqual(self._payload(retries[0])["failed_code"], "source_timeout")
        # 重试成功：完成证据记在第二次尝试，且没有任何最终失败事件。
        attempts = {int(event["attempt_no"]) for event in self._events("scope_completed")}
        self.assertIn(2, attempts)
        self.assertEqual(self._events("unit_failed"), [])
        self.assertEqual(self._events("unit_skipped"), [])
        self.assertEqual(result["integrity"]["summary"]["failed_units"], 0)


class PerComboRetryBudgetTests(_RetryCase):
    """额度作用域：重试额度按组合各一份，不跨组合消耗（数据模型 RetryQuota）。"""

    def test_each_combo_gets_its_own_single_retry(self):
        source = _ScriptedSource([
            SourceOutcome.failure(failed_code="source_timeout"),
            SourceOutcome.success(jobs=[{"job_id": "a1"}], scope_complete=True),
            SourceOutcome.failure(failed_code="source_timeout"),
            SourceOutcome.success(jobs=[{"job_id": "b1"}], scope_complete=True),
        ])
        result = self._run_search(
            source, params={"keyword": "A,B", "city": ["上海"]})

        self.assertEqual(source.calls, 4)
        self.assertEqual(result["completed_combos"], ["A|上海", "B|上海"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self._events("retry_scheduled")), 2)
        self.assertEqual(self._events("unit_failed"), [])


class RetryPauseContractTests(_RetryCase):
    """US1 AC-4：重试期间用户暂停/取消，遵循既有行为且已抓内容保留。"""

    def test_pause_requested_during_retry_keeps_saved_progress(self):
        import threading

        from webui.task_pause_support import STOP_MODE_PAUSE, set_stop_mode

        stop_event = threading.Event()

        class _PausingSource(_ScriptedSource):
            def fetch_list(self, plan_item, *, on_page_completed=None):
                outcome = super().fetch_list(
                    plan_item, on_page_completed=on_page_completed)
                if self.calls == 2:
                    # 用户在重试进行中点了暂停
                    set_stop_mode({}, stop_event, STOP_MODE_PAUSE)
                    stop_event.set()
                return outcome

        source = _PausingSource(
            [
                SourceOutcome.failure(failed_code="source_timeout"),
                SourceOutcome.success(jobs=[{"job_id": "j1"}], scope_complete=True),
            ],
            pages_evidence=1,
        )
        result = self._run_search(source, stop_event=stop_event)

        self.assertEqual(source.calls, 2)
        self.assertEqual(result["stop_mode"], "pause")
        self.assertFalse(result["ok"])
        # 已抓内容保留：页级证据与暂停留痕都在，结果仍带本轮已抓岗位。
        self.assertTrue(self._events("page_completed"))
        self.assertEqual(len(self._events("task_paused")), 1)
        self.assertEqual(result["jobs"], [{"job_id": "j1"}])


class TransientRetryExhaustedTests(_RetryCase):
    """S2：重试仍失败 → 留痕后按跳过定稿。"""

    def test_second_failure_is_recorded_then_skipped(self):
        source = _ScriptedSource(
            [
                SourceOutcome.failure(
                    failed_code="source_timeout", safe_log="reason=第 9 页无响应"),
                SourceOutcome.failure(
                    failed_code="source_timeout", safe_log="reason=重试后仍无响应"),
            ],
            pages_evidence=8,
        )
        result = self._run_search(source)

        self.assertEqual(source.calls, 2)
        self.assertEqual(source.start_pages, [1, 9])
        self.assertFalse(result["ok"], result)
        self.assertFalse(result.get("hard_stop"))
        self.assertEqual(result["completed_combos"], [])
        # 面板失败数 +1：该组合按跳过上报，文案用注册表名称。
        failed_issues = [entry for _combo, entry in self.issues
                         if entry.get("kind") == "combo_failed"]
        self.assertEqual(len(failed_issues), 1)
        self.assertEqual(failed_issues[0]["failed_code"], "source_timeout")

        self.assertEqual(len(self._events("retry_scheduled")), 1)
        failures = self._events("unit_failed")
        self.assertEqual(len(failures), 1)
        self.assertEqual(int(failures[0]["attempt_no"]), 2)
        payload = self._payload(failures[0])
        self.assertEqual(payload["error_code"], "source_timeout")
        # 白箱跳过原因用注册表名称（诊断留在诊断链路），不出现“未知”。
        self.assertEqual(payload["error_reason"], ERROR_USER_MESSAGES["source_timeout"])
        self.assertEqual(result["integrity"]["conclusion"], "failed")


class SingleAttemptBudgetTests(_RetryCase):
    """SC-002：同一组合合计不超过两次尝试，三条重试路径共享额度。"""

    def test_browser_restart_retry_consumes_the_single_budget(self):
        source = _ScriptedSource([
            SourceOutcome.failure(failed_code="source_cdp_unavailable"),
            SourceOutcome.failure(
                failed_code="source_timeout", safe_log="reason=重启后超时"),
        ])
        result = self._run_search(
            source, chrome_side_effect=[(True, ""), (True, "")])

        self.assertEqual(source.calls, 2)
        self.assertEqual(self._events("retry_scheduled"), [])
        self.assertFalse(result.get("hard_stop"))
        self.assertEqual(len(self._events("unit_failed")), 1)

    def test_login_recheck_retry_consumes_the_single_budget(self):
        source = _ScriptedSource(
            [
                SourceOutcome.failure(failed_code="source_login_required"),
                SourceOutcome.failure(
                    failed_code="source_timeout", safe_log="reason=复核后超时"),
            ],
            recheck=lambda: SourceOutcome.success(),
        )
        result = self._run_search(source)

        self.assertEqual(source.calls, 2)
        self.assertEqual(self._events("retry_scheduled"), [])
        self.assertFalse(result.get("hard_stop"))

    def test_zero_trace_retry_still_uses_one_budget(self):
        """重试成功后再无第三次尝试：成功即结束本组合。"""
        source = _ScriptedSource([
            SourceOutcome.failure(failed_code="source_unreachable"),
            SourceOutcome.success(jobs=[{"job_id": "j1"}], scope_complete=True),
        ])
        result = self._run_search(source, params={"keyword": "A", "city": ["上海"]})

        self.assertEqual(source.calls, 2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["completed_combos"], ["A|上海"])


class ManualInterventionNotRetriedTests(_RetryCase):
    """S5：需人工介入类失败维持现状，不自动重试。"""

    def test_verification_and_rate_limit_are_not_retried(self):
        for code in ("source_verification_required", "source_rate_limited"):
            with self.subTest(code=code):
                source = self._fresh_case([SourceOutcome.failure(failed_code=code)])
                result = self._run_search(source)
                self.assertEqual(source.calls, 1)
                self.assertEqual(self._events("retry_scheduled"), [])
                self.assertTrue(result["hard_stop"], result)
                self.assertEqual(result["hard_stop_code"], code)

    def test_confirmed_login_failure_is_skipped_without_retry(self):
        source = _ScriptedSource(
            [SourceOutcome.failure(failed_code="source_login_required")],
            recheck=lambda: SourceOutcome.failure(failed_code="source_login_required"),
        )
        result = self._run_search(source)

        self.assertEqual(source.calls, 1)
        self.assertEqual(self._events("retry_scheduled"), [])
        self.assertFalse(result.get("hard_stop"))
        self.assertIn("登录失效", result["error"])
        self.assertEqual(len(self._events("unit_skipped")), 1)


class ZhilianBrowserLossTests(_RetryCase):
    """S3：列表抓取「连不上浏览器」正名并恢复自动恢复资格。"""

    def test_list_signal_map_registers_registry_name(self):
        from webui.source_zhilian_cdp import _ZHILIAN_LIST_SIGNAL_MAP
        from webui.source_zhilian_runtime_adapter import build_zhilian_list_signal_map

        self.assertEqual(
            _ZHILIAN_LIST_SIGNAL_MAP["cdp_unavailable"], "source_cdp_unavailable")
        self.assertEqual(
            build_zhilian_list_signal_map({})["cdp_unavailable"],
            "source_cdp_unavailable",
        )
        # 既有映射保持原样
        self.assertEqual(_ZHILIAN_LIST_SIGNAL_MAP["timeout"], "source_timeout")
        self.assertIsNone(_ZHILIAN_LIST_SIGNAL_MAP["empty"])

    def test_zhilian_list_browser_loss_uses_registered_name(self):
        from webui.error_registry import ERROR_USER_MESSAGES
        from webui.source import ZhilianCdpSource

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            list_runner=lambda _item: ("cdp_unavailable", []),
        )
        outcome = source.fetch_list(self._zhilian_plan_item())

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_cdp_unavailable")
        self.assertEqual(outcome.failed_reason, ERROR_USER_MESSAGES["source_cdp_unavailable"])
        self.assertNotIn("未知", outcome.failed_reason)

    def test_browser_loss_enters_restart_channel_then_pauses(self):
        source = _ScriptedSource([
            SourceOutcome.failure(failed_code="source_cdp_unavailable"),
            SourceOutcome.failure(failed_code="source_cdp_unavailable"),
        ])
        result = self._run_search(
            source, chrome_side_effect=[(True, ""), (True, "")])

        self.assertEqual(source.calls, 2)
        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertIn("连不上调试浏览器", result["error"])
        self.assertEqual(self._events("retry_scheduled"), [])

    @staticmethod
    def _zhilian_plan_item():
        from webui.platform_input_adapter import compute_zhilian_input_hash

        city = {"name": "全国", "platform_code": "jl0", "mapping_version": 1}
        payload = {"platform": "zhilian", "keyword": "Python", "city": city,
                   "target_pages": 1}
        return {
            "platform": "zhilian", "keyword": "Python", "city": city,
            "combo_key": "Python|全国", "target_pages": 1, "start_page": 1,
            "input_hash": compute_zhilian_input_hash(payload),
        }


if __name__ == "__main__":
    unittest.main()
