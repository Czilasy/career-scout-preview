"""Tests for webui.job_advice: AI advice adapter with rule fallback.

Covers T015/T016: normal AI, missing JD, unconfigured, missing key, timeout,
network failure, invalid JSON, illegal action, empty reason, input minimization,
platform field absence, reason cleaning, no raw exception leak, and zero
persistence mutation.

FR-021: AI 建议输入必须限于岗位 JD、投递时间、最后跟进时间和经过天数。
FR-022: AI 允许输出的行动方向只能为 follow_up 或 review。
FR-023: 缺少 JD 固定 review；AI 未配置/失败/超时/无效时规则兜底。
FR-025: AI 调用和生命周期状态写入必须相互独立。
"""

from __future__ import annotations

import inspect
import json
import pathlib
import tempfile
import unittest
from datetime import datetime, timezone

from webui.ai import (
    AISecurityError,
    ERROR_AUTH,
    ERROR_INVALID,
    ERROR_NETWORK,
    ERROR_TIMEOUT,
)
from webui.job_advice import (
    ADVICE_ACTIONS,
    ADVICE_SOURCES,
    JobAdviceInput,
    JobAdviceResult,
    generate_advice,
)
from webui.job_feedback import JobFeedbackService
from webui.store import TaskStore


UTC = timezone.utc
NOW = datetime(2026, 8, 5, 0, 0, tzinfo=UTC)


def _ok_provider(response):
    """Build a fake provider that returns *response*."""
    def _provider(endpoint, key, messages, model=""):
        return response
    return _provider


def _raising_provider(exc):
    """Build a fake provider that raises *exc*."""
    def _provider(endpoint, key, messages, model=""):
        raise exc
    return _provider


def _capturing_provider(captured, response=None):
    """Build a fake provider that captures messages and returns *response*."""
    def _provider(endpoint, key, messages, model=""):
        captured.extend(messages)
        return response or {"action": "follow_up", "reason": "ok"}
    return _provider


class JobAdviceInputDTOTests(unittest.TestCase):
    """T017: 最小输入 DTO 只携带允许字段。"""

    def test_input_carries_only_allowed_fields(self):
        dto = JobAdviceInput(
            jd="Python JD",
            applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=35,
        )
        self.assertEqual(dto.jd, "Python JD")
        self.assertEqual(dto.applied_at, "2026-07-01T00:00:00Z")
        self.assertIsNone(dto.last_follow_up_at)
        self.assertEqual(dto.elapsed_days, 35)

    def test_input_has_no_platform_fields(self):
        """FR-021: platform 等字段不得出现在 DTO 参数列表中。"""
        params = set(inspect.signature(JobAdviceInput).parameters)
        forbidden = {"platform", "platform_job_id", "canonical_url", "title", "company"}
        self.assertFalse(
            params & forbidden,
            f"JobAdviceInput 不得包含禁止字段: {params & forbidden}",
        )


class GenerateAdviceAISuccessTests(unittest.TestCase):
    """T015: 正常 AI 调用返回 ai 建议。"""

    def _dto(self, jd="Python 后端 JD"):
        return JobAdviceInput(
            jd=jd,
            applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=35,
        )

    def _settings(self):
        return {"endpoint_url": "https://api.example.com/v1", "is_configured": True}

    def test_normal_ai_returns_follow_up_advice(self):
        provider = _ok_provider({"action": "follow_up", "reason": "建议跟进确认进展"})
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key", provider=provider,
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "ai")
        self.assertEqual(result.reason, "建议跟进确认进展")

    def test_normal_ai_returns_review_advice(self):
        provider = _ok_provider({"action": "review", "reason": "建议复核岗位"})
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key", provider=provider,
        )
        self.assertEqual(result.action, "review")
        self.assertEqual(result.source, "ai")

    def test_ai_advice_includes_last_follow_up_and_elapsed(self):
        """有跟进时间时也能正常调用 AI。"""
        captured = []
        provider = _capturing_provider(captured, {"action": "review", "reason": "复核"})
        dto = JobAdviceInput(
            jd="Python JD",
            applied_at="2026-06-01T00:00:00Z",
            last_follow_up_at="2026-07-01T00:00:00Z",
            elapsed_days=65,
        )
        result = generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key", provider=provider,
        )
        self.assertEqual(result.source, "ai")
        payload = json.loads(captured[1]["content"])
        self.assertEqual(payload["last_follow_up_at"], "2026-07-01T00:00:00Z")
        self.assertEqual(payload["elapsed_days"], 65)


class MissingJDTests(unittest.TestCase):
    """T019: 缺少 JD 固定返回 review，不调 AI。"""

    def _settings(self):
        return {"endpoint_url": "https://x", "is_configured": True}

    def test_empty_jd_returns_rule_review_without_ai_call(self):
        called = []
        def provider(endpoint, key, messages, model=""):
            called.append(True)
            return {"action": "follow_up", "reason": "x"}
        dto = JobAdviceInput(
            jd="", applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None, elapsed_days=35,
        )
        result = generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key", provider=provider,
        )
        self.assertEqual(result.action, "review")
        self.assertEqual(result.source, "rule")
        self.assertEqual(called, [])

    def test_none_jd_returns_rule_review(self):
        dto = JobAdviceInput(
            jd=None, applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None, elapsed_days=35,
        )
        result = generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": "x"}),
        )
        self.assertEqual(result.action, "review")
        self.assertEqual(result.source, "rule")

    def test_whitespace_only_jd_returns_rule_review(self):
        dto = JobAdviceInput(
            jd="   ", applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None, elapsed_days=35,
        )
        result = generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": "x"}),
        )
        self.assertEqual(result.action, "review")
        self.assertEqual(result.source, "rule")


class AIFailureFallbackTests(unittest.TestCase):
    """T015/T019: AI 未配置/缺 key/超时/网络/无效 JSON/非法 action/空 reason 都规则兜底。"""

    def _dto(self):
        return JobAdviceInput(
            jd="Python JD",
            applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=35,
        )

    def _settings(self):
        return {"endpoint_url": "https://x", "is_configured": True}

    def test_ai_unconfigured_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(),
            ai_settings={"endpoint_url": "", "is_configured": False},
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": "x"}),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_missing_api_key_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="",
            provider=_ok_provider({"action": "follow_up", "reason": "x"}),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_missing_credential_ref_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": "x"}),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_timeout_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_raising_provider(AISecurityError(ERROR_TIMEOUT)),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_network_failure_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_raising_provider(AISecurityError(ERROR_NETWORK)),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_auth_failure_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_raising_provider(AISecurityError(ERROR_AUTH)),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_invalid_json_returns_rule_follow_up(self):
        """非法 JSON：call_ai 解析失败抛 ERROR_INVALID，规则兜底 follow_up。"""
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_raising_provider(AISecurityError(ERROR_INVALID)),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_invalid_response_returns_rule_follow_up(self):
        """AI 返回非 dict 时规则兜底。"""
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider("not a dict"),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_illegal_action_returns_rule_follow_up(self):
        """AI 返回 action=delete 等非法值时规则兜底。"""
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "auto_delete", "reason": "x"}),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_empty_reason_returns_rule_follow_up(self):
        """AI 返回空 reason 时规则兜底。"""
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": ""}),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_whitespace_reason_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "review", "reason": "   "}),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_non_string_reason_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": 123}),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")

    def test_unexpected_exception_returns_rule_follow_up(self):
        result = generate_advice(
            self._dto(), ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_raising_provider(RuntimeError("unexpected boom")),
        )
        self.assertEqual(result.action, "follow_up")
        self.assertEqual(result.source, "rule")


class InputMinimizationTests(unittest.TestCase):
    """T016: AI input 只含 JD/applied_at/last_follow_up_at/elapsed_days。"""

    def _settings(self):
        return {"endpoint_url": "https://x", "is_configured": True}

    def test_user_message_contains_only_allowed_fields(self):
        captured = []
        provider = _capturing_provider(captured, {"action": "follow_up", "reason": "ok"})
        dto = JobAdviceInput(
            jd="Python JD",
            applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at="2026-07-15T00:00:00Z",
            elapsed_days=35,
        )
        generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key", provider=provider,
        )
        self.assertEqual(len(captured), 2)
        user_msg = captured[1]
        self.assertEqual(user_msg["role"], "user")
        payload = json.loads(user_msg["content"])
        self.assertEqual(
            set(payload.keys()),
            {"jd", "applied_at", "last_follow_up_at", "elapsed_days"},
        )
        self.assertEqual(payload["jd"], "Python JD")
        self.assertEqual(payload["applied_at"], "2026-07-01T00:00:00Z")
        self.assertEqual(payload["last_follow_up_at"], "2026-07-15T00:00:00Z")
        self.assertEqual(payload["elapsed_days"], 35)

    def test_platform_fields_absent_from_ai_input(self):
        """FR-021: platform/platform_job_id/canonical_url/title/company 不得进入 AI input。"""
        captured = []
        provider = _capturing_provider(captured)
        dto = JobAdviceInput(
            jd="Python JD",
            applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=35,
        )
        generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key", provider=provider,
        )
        user_payload = json.loads(captured[1]["content"])
        forbidden = {"platform", "platform_job_id", "canonical_url", "title", "company"}
        self.assertFalse(
            set(user_payload.keys()) & forbidden,
            f"AI input 不得包含禁止字段: {set(user_payload.keys()) & forbidden}",
        )


class ReasonCleaningTests(unittest.TestCase):
    """T018: reason 文本清洗。"""

    def _settings(self):
        return {"endpoint_url": "https://x", "is_configured": True}

    def test_reason_whitespace_stripped(self):
        provider = _ok_provider({"action": "follow_up", "reason": "  建议跟进  "})
        result = generate_advice(
            JobAdviceInput(jd="JD", applied_at="2026-07-01T00:00:00Z",
                           last_follow_up_at=None, elapsed_days=35),
            ai_settings=self._settings(),
            credential_ref="host", api_key="key", provider=provider,
        )
        self.assertEqual(result.reason, "建议跟进")
        self.assertEqual(result.source, "ai")


class NoLeakTests(unittest.TestCase):
    """T016: 原始异常/Key/endpoint/prompt 不泄露到结果。"""

    SECRET_KEY = "sk-super-secret-key-12345"
    ENDPOINT = "https://secret-endpoint.example.com/v1"

    def _dto(self):
        return JobAdviceInput(
            jd="Python JD",
            applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=35,
        )

    def test_result_does_not_leak_api_key(self):
        result = generate_advice(
            self._dto(),
            ai_settings={"endpoint_url": self.ENDPOINT, "is_configured": True},
            credential_ref="host", api_key=self.SECRET_KEY,
            provider=_raising_provider(AISecurityError(ERROR_NETWORK)),
        )
        blob = json.dumps(
            {"action": result.action, "reason": result.reason, "source": result.source},
            ensure_ascii=False,
        )
        self.assertNotIn(self.SECRET_KEY, blob)

    def test_result_does_not_leak_endpoint(self):
        result = generate_advice(
            self._dto(),
            ai_settings={"endpoint_url": self.ENDPOINT, "is_configured": True},
            credential_ref="host", api_key="key",
            provider=_raising_provider(AISecurityError(ERROR_NETWORK)),
        )
        self.assertNotIn(self.ENDPOINT, result.reason)
        self.assertNotIn(self.ENDPOINT, result.action)

    def test_result_does_not_leak_raw_exception_text(self):
        result = generate_advice(
            self._dto(),
            ai_settings={"endpoint_url": "https://x", "is_configured": True},
            credential_ref="host", api_key="key",
            provider=_raising_provider(RuntimeError("traceback with sensitive info")),
        )
        self.assertNotIn("traceback", result.reason.lower())
        self.assertNotIn("sensitive info", result.reason)

    def test_result_does_not_leak_error_code(self):
        """AISecurityError 的 error_code 不得出现在面向用户的 reason 中。"""
        result = generate_advice(
            self._dto(),
            ai_settings={"endpoint_url": "https://x", "is_configured": True},
            credential_ref="host", api_key="key",
            provider=_raising_provider(AISecurityError(ERROR_INVALID)),
        )
        self.assertNotIn(ERROR_INVALID, result.reason)


class ZeroPersistenceMutationTests(unittest.TestCase):
    """T016/T019: advice 调用前后 profile_jobs/events/receipts/feedback 不变。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile = self.store.create_profile("建议画像")
        self.service = JobFeedbackService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _create_applied_overdue_job(self):
        result = self.store.upsert_job(
            platform="boss",
            platform_job_id="advice-mutation",
            canonical_url="https://www.zhipin.com/job_detail/advice-mutation.html",
            title="Python 后端",
            company="公司",
            salary="20K",
            location="上海",
            jd="Python JD",
        )
        job_id = result["job_id"]
        self.service.execute_action(
            request_id="setup-apply",
            profile_id=self.profile["id"],
            job={"job_id": job_id},
            action="mark_applied",
            applied_at="2026-06-01T00:00:00Z",
            now=NOW,
        )
        return job_id

    def _snapshot_counts(self):
        with self.store._connection() as conn:
            pj = conn.execute("SELECT COUNT(*) FROM profile_jobs").fetchone()[0]
            events = conn.execute(
                "SELECT COUNT(*) FROM profile_job_events"
            ).fetchone()[0]
            receipts = conn.execute(
                "SELECT COUNT(*) FROM profile_job_command_receipts"
            ).fetchone()[0]
            feedback = conn.execute(
                "SELECT COUNT(*) FROM feedback_events"
            ).fetchone()[0]
        return {
            "profile_jobs": pj,
            "profile_job_events": events,
            "profile_job_command_receipts": receipts,
            "feedback_events": feedback,
        }

    def test_advice_does_not_mutate_any_table_ai_success(self):
        self._create_applied_overdue_job()
        before = self._snapshot_counts()
        dto = JobAdviceInput(
            jd="Python JD",
            applied_at="2026-06-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=65,
        )
        generate_advice(
            dto,
            ai_settings={"endpoint_url": "https://x", "is_configured": True},
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": "跟进"}),
        )
        self.assertEqual(self._snapshot_counts(), before)

    def test_advice_does_not_mutate_any_table_ai_failure(self):
        self._create_applied_overdue_job()
        before = self._snapshot_counts()
        dto = JobAdviceInput(
            jd="Python JD",
            applied_at="2026-06-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=65,
        )
        generate_advice(
            dto,
            ai_settings={"endpoint_url": "https://x", "is_configured": True},
            credential_ref="host", api_key="key",
            provider=_raising_provider(AISecurityError(ERROR_TIMEOUT)),
        )
        self.assertEqual(self._snapshot_counts(), before)

    def test_advice_does_not_mutate_any_table_missing_jd(self):
        self._create_applied_overdue_job()
        before = self._snapshot_counts()
        dto = JobAdviceInput(
            jd="",
            applied_at="2026-06-01T00:00:00Z",
            last_follow_up_at=None,
            elapsed_days=65,
        )
        generate_advice(
            dto,
            ai_settings={"endpoint_url": "https://x", "is_configured": True},
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": "x"}),
        )
        self.assertEqual(self._snapshot_counts(), before)


class AdviceAllowlistTests(unittest.TestCase):
    """action 只能是 follow_up 或 review；source 只能是 ai 或 rule。"""

    def test_advice_actions_is_follow_up_or_review(self):
        self.assertEqual(set(ADVICE_ACTIONS), {"follow_up", "review"})

    def test_advice_sources_is_ai_or_rule(self):
        self.assertEqual(set(ADVICE_SOURCES), {"ai", "rule"})

    def test_result_action_always_in_allowlist(self):
        """无论 AI 成功还是兜底，action 必须在 allowlist 内。"""
        dto = JobAdviceInput(
            jd="JD", applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None, elapsed_days=35,
        )
        settings = {"endpoint_url": "https://x", "is_configured": True}
        cases = [
            ("ai-follow_up", _ok_provider({"action": "follow_up", "reason": "r"})),
            ("ai-review", _ok_provider({"action": "review", "reason": "r"})),
            ("illegal-action", _ok_provider({"action": "auto_delete", "reason": "r"})),
            ("timeout", _raising_provider(AISecurityError(ERROR_TIMEOUT))),
        ]
        for label, provider in cases:
            with self.subTest(label=label):
                result = generate_advice(
                    dto, ai_settings=settings,
                    credential_ref="host", api_key="key", provider=provider,
                )
                self.assertIn(result.action, ADVICE_ACTIONS)
                self.assertIn(result.source, ADVICE_SOURCES)


class ElapsedDaysBoundaryTests(unittest.TestCase):
    """T017: elapsed_days 接收边界。"""

    def _settings(self):
        return {"endpoint_url": "https://x", "is_configured": True}

    def test_none_elapsed_days_still_works(self):
        """elapsed_days=None 是合法边界，不阻止建议生成。"""
        dto = JobAdviceInput(
            jd="JD", applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None, elapsed_days=None,
        )
        result = generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "follow_up", "reason": "ok"}),
        )
        self.assertEqual(result.source, "ai")

    def test_zero_elapsed_days_still_works(self):
        dto = JobAdviceInput(
            jd="JD", applied_at="2026-07-01T00:00:00Z",
            last_follow_up_at=None, elapsed_days=0,
        )
        result = generate_advice(
            dto, ai_settings=self._settings(),
            credential_ref="host", api_key="key",
            provider=_ok_provider({"action": "review", "reason": "ok"}),
        )
        self.assertEqual(result.source, "ai")


if __name__ == "__main__":
    unittest.main()
