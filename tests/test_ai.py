"""Tests for webui.ai: JSON validation, connection testing, error sanitization, batching.

Covers T014: AI JSON parsing success/failure, timeout handling, rejection of
unknown job_ids, error sanitization (no API key leak) and rank_jds batch limits.
Uses shared fixtures from tests/test_workbench_fixtures.py.
"""

from __future__ import annotations

import json
import copy
import unittest
from unittest.mock import patch, MagicMock

import requests

from tests.test_workbench_fixtures import (
    sample_ai_resume_response,
    sample_ai_rank_response,
    sample_ai_preference_response,
    sample_resume_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_chat_response(payload: dict, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response simulating a streaming chat completions reply.

    call_ai now uses stream=True and reads SSE lines via iter_lines().
    The mock produces ``data: {...}`` chunks followed by ``data: [DONE]``.
    """
    response = MagicMock()
    response.status_code = status_code
    content_str = json.dumps(payload, ensure_ascii=False)
    # 模拟流式：把完整 content 拆成若干 chunk（每 chunk 最多 40 字符）
    chunk_size = 40
    lines = []
    for i in range(0, len(content_str), chunk_size):
        chunk_text = content_str[i:i + chunk_size]
        sse_data = json.dumps(
            {"choices": [{"delta": {"content": chunk_text}, "finish_reason": None}]},
            ensure_ascii=False,
        )
        lines.append(f"data: {sse_data}")
    # 最后一个 chunk 带 finish_reason
    lines.append(f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})}')
    lines.append("data: [DONE]")
    lines.append("")  # 尾部空行
    response.iter_lines.return_value = iter(lines)
    return response


def _mock_stream_raw(content_str: str, status_code: int = 200,
                     finish_reason: str | None = "stop") -> MagicMock:
    """Build a streaming mock from a raw content string (may be invalid JSON)."""
    response = MagicMock()
    response.status_code = status_code
    sse_data = json.dumps(
        {"choices": [{"delta": {"content": content_str},
                      "finish_reason": finish_reason}]},
        ensure_ascii=False,
    )
    lines = [f"data: {sse_data}", "data: [DONE]", ""]
    response.iter_lines.return_value = iter(lines)
    return response


# ---------------------------------------------------------------------------
# validate_resume_response
# ---------------------------------------------------------------------------

class ValidateResumeResponseTests(unittest.TestCase):
    """JSON parsing success and failure for resume responses."""

    def test_accepts_valid_response_with_correct_fields_and_types(self):
        from webui.ai import validate_resume_response

        data = sample_ai_resume_response()
        result = validate_resume_response(data)

        self.assertEqual(result["profile_name"], "Python 后端")
        self.assertEqual(result["city"], "上海")
        self.assertEqual(result["roles"], ["Python 后端工程师", "后端开发工程师"])
        self.assertEqual(result["skills"], ["Python", "FastAPI", "Redis", "PostgreSQL"])
        self.assertEqual(result["keywords"], ["Python 后端", "FastAPI 后端", "微服务开发"])
        self.assertEqual(len(result["suggestions"]), 2)
        self.assertEqual(result["suggestions"][0]["field"], "city")
        self.assertFalse(result["suggestions"][0]["uncertain"])

    def test_rejects_missing_required_field(self):
        from webui.ai import validate_resume_response

        for field in ("profile_name", "city", "roles", "skills", "keywords", "suggestions"):
            with self.subTest(field=field):
                data = sample_ai_resume_response()
                del data[field]
                with self.assertRaises(ValueError):
                    validate_resume_response(data)

    def test_rejects_wrong_type_for_field(self):
        from webui.ai import validate_resume_response

        cases = {
            "profile_name": 123,
            "city": 456,
            "roles": "not a list",
            "skills": "not a list",
            "keywords": "not a list",
            "suggestions": "not a list",
        }
        for field, bad_value in cases.items():
            with self.subTest(field=field):
                data = sample_ai_resume_response()
                data[field] = bad_value
                with self.assertRaises(ValueError):
                    validate_resume_response(data)

    def test_rejects_non_string_element_in_list(self):
        from webui.ai import validate_resume_response

        for field in ("roles", "skills", "keywords"):
            with self.subTest(field=field):
                data = sample_ai_resume_response()
                data[field] = ["valid", 123]
                with self.assertRaises(ValueError):
                    validate_resume_response(data)

    def test_rejects_non_dict_input(self):
        from webui.ai import validate_resume_response

        for bad in ("not a dict", None, [], 42):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    validate_resume_response(bad)

    def test_rejects_malformed_suggestion(self):
        from webui.ai import validate_resume_response

        data = sample_ai_resume_response()
        data["suggestions"] = [{"field": "city"}]  # missing value/source/uncertain
        with self.assertRaises(ValueError):
            validate_resume_response(data)

    def test_rejects_suggestion_with_wrong_uncertain_type(self):
        from webui.ai import validate_resume_response

        data = sample_ai_resume_response()
        data["suggestions"] = [
            {"field": "city", "value": "上海", "source": "resume", "uncertain": "yes"}
        ]
        with self.assertRaises(ValueError):
            validate_resume_response(data)


# ---------------------------------------------------------------------------
# validate_rank_response
# ---------------------------------------------------------------------------

class ValidateRankResponseTests(unittest.TestCase):
    """JSON parsing for ranking responses, including unknown job_id rejection."""

    def test_accepts_valid_ranked_response(self):
        from webui.ai import validate_rank_response

        job_ids = ["job-001", "job-002", "job-003"]
        data = sample_ai_rank_response(job_ids)
        result = validate_rank_response(data, job_ids)

        self.assertEqual(result, job_ids)

    def test_accepts_reordered_job_ids(self):
        from webui.ai import validate_rank_response

        input_ids = ["job-001", "job-002", "job-003"]
        data = {"ranked_job_ids": ["job-003", "job-001", "job-002"]}
        result = validate_rank_response(data, input_ids)

        self.assertEqual(result, ["job-003", "job-001", "job-002"])

    def test_rejects_unknown_job_id(self):
        from webui.ai import validate_rank_response

        data = {"ranked_job_ids": ["job-001", "job-002", "unknown-job"]}
        with self.assertRaises(ValueError):
            validate_rank_response(data, ["job-001", "job-002"])

    def test_rejects_non_dict_input(self):
        from webui.ai import validate_rank_response

        with self.assertRaises(ValueError):
            validate_rank_response("not a dict", ["job-001"])

    def test_rejects_missing_field(self):
        from webui.ai import validate_rank_response

        with self.assertRaises(ValueError):
            validate_rank_response({}, ["job-001"])

    def test_rejects_wrong_type(self):
        from webui.ai import validate_rank_response

        data = {"ranked_job_ids": "not a list"}
        with self.assertRaises(ValueError):
            validate_rank_response(data, ["job-001"])

    def test_rejects_non_string_element(self):
        from webui.ai import validate_rank_response

        data = {"ranked_job_ids": ["job-001", 123]}
        with self.assertRaises(ValueError):
            validate_rank_response(data, ["job-001", "123"])


# ---------------------------------------------------------------------------
# validate_preference_response
# ---------------------------------------------------------------------------

class ValidatePreferenceResponseTests(unittest.TestCase):
    """JSON parsing for preference update responses."""

    def test_accepts_valid_response(self):
        from webui.ai import validate_preference_response

        data = sample_ai_preference_response()
        result = validate_preference_response(data)

        self.assertEqual(result["positive_terms"], ["Python", "FastAPI"])
        self.assertEqual(result["negative_terms"], ["外包"])
        self.assertEqual(result["keyword_weights"], {"Python": 1.0, "FastAPI": 0.8})
        self.assertEqual(result["uncertain"], [])

    def test_rejects_missing_field(self):
        from webui.ai import validate_preference_response

        for field in ("positive_terms", "negative_terms", "keyword_weights", "uncertain"):
            with self.subTest(field=field):
                data = sample_ai_preference_response()
                del data[field]
                with self.assertRaises(ValueError):
                    validate_preference_response(data)

    def test_rejects_wrong_type(self):
        from webui.ai import validate_preference_response

        cases = {
            "positive_terms": "not a list",
            "negative_terms": "not a list",
            "keyword_weights": "not a dict",
            "uncertain": "not a list",
        }
        for field, bad_value in cases.items():
            with self.subTest(field=field):
                data = sample_ai_preference_response()
                data[field] = bad_value
                with self.assertRaises(ValueError):
                    validate_preference_response(data)

    def test_rejects_non_string_in_terms(self):
        from webui.ai import validate_preference_response

        data = sample_ai_preference_response()
        data["positive_terms"] = ["Python", 123]
        with self.assertRaises(ValueError):
            validate_preference_response(data)

    def test_rejects_invalid_keyword_weight_value(self):
        from webui.ai import validate_preference_response

        data = sample_ai_preference_response()
        data["keyword_weights"] = {"Python": "not a number"}
        with self.assertRaises(ValueError):
            validate_preference_response(data)

    def test_rejects_boolean_keyword_weight(self):
        from webui.ai import validate_preference_response

        data = sample_ai_preference_response()
        data["keyword_weights"] = {"Python": True}
        with self.assertRaises(ValueError):
            validate_preference_response(data)


# ---------------------------------------------------------------------------
# call_ai — timeout, error sanitization, success
# ---------------------------------------------------------------------------

class CallAITests(unittest.TestCase):
    """Timeout handling, error sanitization and successful AI calls."""

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_timeout_raises_safe_error(self, mock_post, _mock_sleep):
        from webui.ai import call_ai, AISecurityError

        mock_post.side_effect = requests.Timeout("connection timed out")

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "timeout")

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_network_error_raises_safe_error(self, mock_post, _mock_sleep):
        from webui.ai import call_ai, AISecurityError

        mock_post.side_effect = requests.ConnectionError("DNS resolution failed")

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "network_error")

    @patch("webui.ai.requests.post")
    def test_auth_failure_raises_safe_error(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        response = MagicMock()
        response.status_code = 401
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "auth_failed")

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_server_error_raises_safe_error(self, mock_post, _mock_sleep):
        from webui.ai import call_ai, AISecurityError

        response = MagicMock()
        response.status_code = 500
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        # 500 系先退避重试，耗尽后报 server_error（区别于"返回无效"）
        self.assertEqual(ctx.exception.error_code, "server_error")
        self.assertEqual(mock_post.call_count, 2)

    @patch("webui.ai.requests.post")
    def test_malformed_response_body_raises_invalid_response(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        # 流式返回空内容（端点 200 但没出字）
        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = iter(["data: [DONE]", ""])
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "invalid_response")

    @patch("webui.ai.requests.post")
    def test_non_json_content_raises_invalid_response(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        mock_post.return_value = _mock_stream_raw("not json at all")

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "invalid_response")

    @patch("webui.ai.requests.post")
    def test_successful_call_returns_parsed_json(self, mock_post):
        from webui.ai import call_ai

        expected = sample_ai_resume_response()
        mock_post.return_value = _mock_chat_response(expected)

        result = call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                         [{"role": "user", "content": "hi"}])

        self.assertEqual(result["profile_name"], expected["profile_name"])
        self.assertEqual(result["roles"], expected["roles"])

    @patch("webui.ai.requests.post")
    def test_error_does_not_leak_api_key(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        api_key = "sk-super-secret-key-12345"
        # The requests exception message might contain the key
        mock_post.side_effect = requests.ConnectionError(
            f"request to https://api.example.com with bearer {api_key} failed"
        )

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", api_key,
                    [{"role": "user", "content": "hi"}])

        self.assertNotIn(api_key, ctx.exception.error_code)
        self.assertNotIn(api_key, str(ctx.exception))

    @patch("webui.ai.requests.post")
    def test_auth_error_does_not_leak_api_key(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        api_key = "sk-super-secret-key-12345"
        response = MagicMock()
        response.status_code = 401
        response.text = f"Unauthorized: invalid key {api_key}"
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", api_key,
                    [{"role": "user", "content": "hi"}])

        self.assertNotIn(api_key, str(ctx.exception))

    @patch("webui.ai.requests.post")
    def test_exception_context_suppressed_to_prevent_traceback_leak(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        api_key = "sk-super-secret-key-12345"
        mock_post.side_effect = requests.ConnectionError(f"failed with {api_key}")

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", api_key,
                    [{"role": "user", "content": "hi"}])

        # ``from None`` sets __suppress_context__ so tracebacks do not show
        # the original exception (which may contain the API key).
        self.assertTrue(ctx.exception.__suppress_context__)


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------

class TestConnectionTests(unittest.TestCase):
    """Connection testing returns safe error classifications."""

    @patch("webui.ai.requests.post")
    def test_successful_connection(self, mock_post):
        from webui.ai import test_connection

        response = MagicMock(); response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
        mock_post.return_value = response

        result = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )
        # 轻量 ping 版本：只验通道+密钥+生成层，不重跑 candidate-v3 合约验证
        # （合约验证留给真实业务路径里的 call_ai + provider adapter）
        self.assertEqual(result, {"ok": True, "transport": "ready", "generation": "ready",
                                  "candidate_contract": "manual_required", "warning_codes": []})

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_timeout_returns_safe_code(self, mock_post, _mock_sleep):
        from webui.ai import test_connection

        mock_post.side_effect = requests.Timeout("timed out")

        result = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(result["ok"]); self.assertEqual(result["transport"], "failed"); self.assertEqual(result["warning_codes"], ["timeout"])

    @patch("webui.ai.requests.post")
    def test_auth_failure_returns_safe_code(self, mock_post):
        from webui.ai import test_connection

        response = MagicMock()
        response.status_code = 403
        mock_post.return_value = response

        result = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(result["ok"]); self.assertEqual(result["warning_codes"], ["auth_failed"])

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_network_error_returns_safe_code(self, mock_post, _mock_sleep):
        from webui.ai import test_connection

        mock_post.side_effect = requests.ConnectionError("DNS failed")

        result = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(result["ok"]); self.assertEqual(result["warning_codes"], ["network_error"])

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_server_error_returns_safe_code(self, mock_post, _mock_sleep):
        from webui.ai import test_connection

        response = MagicMock()
        response.status_code = 500
        mock_post.return_value = response

        result = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(result["ok"]); self.assertEqual(result["generation"], "failed"); self.assertEqual(result["warning_codes"], ["server_error"])

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_connection_error_does_not_leak_api_key(self, mock_post, _mock_sleep):
        from webui.ai import test_connection

        api_key = "sk-super-secret-key-12345"
        mock_post.side_effect = requests.ConnectionError(f"failed with {api_key}")

        result = test_connection(
            "https://api.example.com/v1/chat/completions", api_key
        )

        self.assertFalse(result["ok"])
        self.assertNotIn(api_key, str(result))

    @patch("webui.ai.requests.post")
    def test_invalid_json_reports_generation_failure(self, mock_post):
        from webui.ai import test_connection
        # 轻量 ping 版本：transport=ready（HTTP 200 已拿到），但 choices 缺字段
        # → generation=failed。模拟一个不合规的响应体。
        response = MagicMock(); response.status_code = 200
        response.json.return_value = {}  # 缺 choices
        mock_post.return_value = response
        result = test_connection("https://api.example.com/v1", "secret", "model")
        self.assertFalse(result["ok"])
        self.assertEqual(result["transport"], "ready")
        self.assertEqual(result["generation"], "failed")


# ---------------------------------------------------------------------------
# rank_jds — batch limits and unknown job_id rejection
# ---------------------------------------------------------------------------

class RankJdsTests(unittest.TestCase):
    """JD ranking with batch limits and unknown job_id rejection."""

    @staticmethod
    def _echo_mock():
        """Return a side_effect that echoes back the job_ids it receives."""
        def side_effect(*args, **kwargs):
            payload = kwargs.get("json", {})
            user_content = payload["messages"][-1]["content"]
            parsed = json.loads(user_content)
            batch_ids = [j["job_id"] for j in parsed["jobs"]]
            return _mock_chat_response({"ranked_job_ids": batch_ids})
        return side_effect

    @patch("webui.ai.requests.post")
    def test_single_batch_under_limit(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": "job-001", "title": "Python", "jd": "desc1"},
            {"job_id": "job-002", "title": "Go", "jd": "desc2"},
        ]
        mock_post.return_value = _mock_chat_response({"ranked_job_ids": ["job-002", "job-001"]})

        result = rank_jds({}, jobs, "https://api.example.com", "key")

        self.assertEqual(result, ["job-002", "job-001"])
        self.assertEqual(mock_post.call_count, 1)

    @patch("webui.ai.requests.post")
    def test_batches_at_most_10_jobs_per_call(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": f"job-{i:03d}", "title": f"Job {i}", "jd": f"描述 {i}"}
            for i in range(25)
        ]
        mock_post.side_effect = self._echo_mock()

        result = rank_jds({}, jobs, "https://api.example.com", "key")

        # 25 jobs / 10 per batch = 3 batches
        self.assertEqual(mock_post.call_count, 3)
        # Each call must have at most 10 jobs
        for call in mock_post.call_args_list:
            payload = call.kwargs["json"]
            user_content = payload["messages"][-1]["content"]
            parsed = json.loads(user_content)
            self.assertLessEqual(len(parsed["jobs"]), 10)
        # All 25 job_ids should be in the result
        self.assertEqual(len(result), 25)

    @patch("webui.ai.requests.post")
    def test_exactly_10_jobs_uses_single_batch(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": f"job-{i:03d}", "title": f"Job {i}", "jd": "desc"}
            for i in range(10)
        ]
        mock_post.side_effect = self._echo_mock()

        result = rank_jds({}, jobs, "https://api.example.com", "key")

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(len(result), 10)

    @patch("webui.ai.requests.post")
    def test_empty_jobs_returns_empty_without_call(self, mock_post):
        from webui.ai import rank_jds

        result = rank_jds({}, [], "https://api.example.com", "key")

        self.assertEqual(result, [])
        mock_post.assert_not_called()

    @patch("webui.ai.requests.post")
    def test_rejects_unknown_job_id_from_ai(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": "job-001", "title": "Python", "jd": "desc"},
            {"job_id": "job-002", "title": "Go", "jd": "desc"},
        ]
        # AI returns an id that was not in the input
        mock_post.return_value = _mock_chat_response(
            {"ranked_job_ids": ["job-001", "unknown-job"]}
        )

        with self.assertRaises(ValueError):
            rank_jds({}, jobs, "https://api.example.com", "key")


# ---------------------------------------------------------------------------
# parse_resume — end-to-end with mocked AI
# ---------------------------------------------------------------------------

class ParseResumeTests(unittest.TestCase):
    """End-to-end resume parsing with mocked AI responses."""

    @patch("webui.ai.requests.post")
    def test_successful_parse_returns_validated_fields(self, mock_post):
        from webui.ai import parse_resume

        expected = sample_ai_resume_response()
        mock_post.return_value = _mock_chat_response(expected)

        result = parse_resume(
            sample_resume_text(),
            "https://api.example.com/v1/chat/completions",
            "secret-key",
        )

        self.assertEqual(result["profile_name"], expected["profile_name"])
        self.assertEqual(result["city"], expected["city"])
        self.assertEqual(result["roles"], expected["roles"])
        self.assertEqual(result["skills"], expected["skills"])
        self.assertEqual(result["keywords"], expected["keywords"])

    @patch("webui.ai.requests.post")
    def test_parse_rejects_invalid_ai_output(self, mock_post):
        from webui.ai import parse_resume

        # Missing required fields
        mock_post.return_value = _mock_chat_response({"profile_name": "x"})

        with self.assertRaises(ValueError):
            parse_resume(
                sample_resume_text(),
                "https://api.example.com/v1/chat/completions",
                "secret-key",
            )


# ---------------------------------------------------------------------------
# update_preference — end-to-end with mocked AI
# ---------------------------------------------------------------------------

class UpdatePreferenceTests(unittest.TestCase):
    """End-to-end preference update with mocked AI responses."""

    @patch("webui.ai.requests.post")
    def test_successful_update_returns_validated_preference(self, mock_post):
        from webui.ai import update_preference

        expected = sample_ai_preference_response()
        mock_post.return_value = _mock_chat_response(expected)

        result = update_preference(
            {"name": "Python 后端"},
            [{"action": "interested", "job_id": "job-001"}],
            "https://api.example.com/v1/chat/completions",
            "secret-key",
        )

        self.assertEqual(result["positive_terms"], expected["positive_terms"])
        self.assertEqual(result["negative_terms"], expected["negative_terms"])
        self.assertEqual(result["keyword_weights"], expected["keyword_weights"])

    @patch("webui.ai.requests.post")
    def test_update_rejects_invalid_ai_output(self, mock_post):
        from webui.ai import update_preference

        # Missing required fields
        mock_post.return_value = _mock_chat_response({"positive_terms": ["Python"]})

        with self.assertRaises(ValueError):
            update_preference(
                {"name": "Python 后端"},
                [],
                "https://api.example.com/v1/chat/completions",
                "secret-key",
            )


# ---------------------------------------------------------------------------
# Credential management (mocked keyring)
# ---------------------------------------------------------------------------

class CredentialManagementTests(unittest.TestCase):
    """Credential storage using the system keyring (mocked)."""

    @patch("webui.ai.keyring")
    def test_store_api_key_returns_host_as_credential_ref(self, mock_keyring):
        from webui.ai import store_api_key

        ref = store_api_key(
            "https://api.openai.com/v1/chat/completions", "sk-12345"
        )

        self.assertEqual(ref, "api.openai.com")
        mock_keyring.set_password.assert_called_once_with(
            "boss-workbench", "api.openai.com", "sk-12345"
        )

    @patch("webui.ai.keyring")
    def test_retrieve_api_key_uses_credential_ref(self, mock_keyring):
        from webui.ai import retrieve_api_key

        mock_keyring.get_password.return_value = "sk-12345"

        result = retrieve_api_key("api.openai.com")

        self.assertEqual(result, "sk-12345")
        mock_keyring.get_password.assert_called_once_with(
            "boss-workbench", "api.openai.com"
        )

    @patch("webui.ai.keyring")
    def test_delete_api_key_uses_credential_ref(self, mock_keyring):
        from webui.ai import delete_api_key

        delete_api_key("api.openai.com")

        mock_keyring.delete_password.assert_called_once_with(
            "boss-workbench", "api.openai.com"
        )


# ---------------------------------------------------------------------------
# suggest_screening_filters (T012)
# ---------------------------------------------------------------------------

class AIAvailabilityTests(unittest.TestCase):
    """T045: AI 不可用检测与提示（FR-031, FR-032）。

    is_ai_available 是纯函数：接收 settings（含 is_configured）、credential_ref、
    api_key，返回 bool。不调 AI、不访 keyring、不发 HTTP。调用方负责从凭据库
    取 api_key。不可用时返回 False，可用时返回 True，永不泄露凭据。
    """

    def setUp(self):
        from webui.ai import is_ai_available
        self.check = is_ai_available

    def _settings(self, configured=True):
        return {
            "endpoint_url": "https://api.example.com/v1" if configured else "",
            "status": "ready" if configured else "unconfigured",
            "is_configured": configured,
        }

    # -- 可用 --

    def test_available_when_configured_and_key_present(self):
        self.assertTrue(self.check(self._settings(True), "host-ref", "sk-real-key"))

    # -- 不可用 --

    def test_unavailable_when_not_configured(self):
        self.assertFalse(self.check(self._settings(False), "host-ref", "sk-real-key"))

    def test_unavailable_when_credential_ref_empty(self):
        self.assertFalse(self.check(self._settings(True), "", "sk-real-key"))

    def test_unavailable_when_api_key_empty(self):
        self.assertFalse(self.check(self._settings(True), "host-ref", ""))

    def test_unavailable_when_all_missing(self):
        self.assertFalse(self.check(self._settings(False), "", ""))

    # -- 安全：不泄露凭据 --

    def test_return_value_is_bool_not_string_with_key(self):
        result = self.check(self._settings(True), "host-ref", "SECRET_KEY_42")
        self.assertIsInstance(result, bool)
        self.assertNotIn("SECRET_KEY_42", str(result))

    def test_does_not_call_ai(self):
        with patch("webui.ai.call_ai") as mock_call:
            self.check(self._settings(True), "ref", "key")
            mock_call.assert_not_called()

    def test_does_not_access_keyring(self):
        with patch("webui.ai.keyring") as mock_keyring:
            self.check(self._settings(True), "ref", "key")
            mock_keyring.get_password.assert_not_called()

    def test_does_not_make_http_requests(self):
        with patch("webui.ai.requests") as mock_requests:
            self.check(self._settings(True), "ref", "key")
            mock_requests.post.assert_not_called()

    # -- 边界 --

    def test_none_settings_returns_false(self):
        self.assertFalse(self.check(None, "ref", "key"))

    def test_none_credential_ref_returns_false(self):
        self.assertFalse(self.check(self._settings(True), None, "key"))

    def test_none_api_key_returns_false(self):
        self.assertFalse(self.check(self._settings(True), "ref", None))


class MatchJdsFailurePolicyTests(unittest.TestCase):
    """Stage B failures must stay reviewable instead of becoming matches."""

    def test_ai_transport_failure_marks_jobs_uncertain(self):
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [{
            "job_id": "job-001",
            "title": "Python 后端工程师",
            "salary": "20-30K",
            "location": "上海",
            "jd": "负责 Python 服务开发",
        }]

        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_NETWORK),
        ):
            result = match_jds(
                jobs,
                "5 年 Python 后端经验",
                "https://api.example.com/v1/chat/completions",
                "secret-key",
            )

        verdict = result["verdicts"]["job-001"]
        self.assertEqual(verdict["verdict"], "uncertain")
        self.assertIn("待人工确认", verdict["reason"])


class CallAIRetryTests(unittest.TestCase):
    """call_ai 重试扩展：5xx/超时/网络可重试，配额撞墙立即停，截断单独识别。"""

    @staticmethod
    def _ok_response(payload, finish_reason="stop"):
        return _mock_chat_response(payload) if finish_reason == "stop" else \
            _mock_stream_raw(json.dumps(payload), finish_reason=finish_reason)

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_server_error_then_success_retries(self, mock_post, _mock_sleep):
        from webui.ai import call_ai

        err = MagicMock()
        err.status_code = 500
        mock_post.side_effect = [err, self._ok_response({"a": 1})]

        result = call_ai("https://api.example.com/v1/chat/completions", "key",
                         [{"role": "user", "content": "hi"}])

        self.assertEqual(result, {"a": 1})
        self.assertEqual(mock_post.call_count, 2)

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_timeout_then_success_retries(self, mock_post, _mock_sleep):
        from webui.ai import call_ai

        mock_post.side_effect = [requests.Timeout("t"), self._ok_response({"ok": True})]

        result = call_ai("https://api.example.com/v1/chat/completions", "key",
                         [{"role": "user", "content": "hi"}])

        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_post.call_count, 2)

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_quota_exhausted_stops_immediately_without_retry(self, mock_post, mock_sleep):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 429
        response.json.return_value = {"error": {"type": "insufficient_quota"}}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "quota_exhausted")
        self.assertEqual(mock_post.call_count, 1)  # 配额撞墙不重试
        mock_sleep.assert_not_called()

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_plain_429_retries_then_rate_limited(self, mock_post, _mock_sleep):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 429
        response.json.return_value = {"error": {"type": "rate_limit"}}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "rate_limited")
        self.assertEqual(mock_post.call_count, 2)

    @patch("webui.ai.requests.post")
    def test_truncated_finish_reason_length(self, mock_post):
        from webui.ai import AISecurityError, call_ai

        mock_post.return_value = _mock_stream_raw(
            '{"results": [{"i":0,', finish_reason="length")

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.error_code, "truncated")

    @patch("webui.ai.requests.post")
    def test_truncated_unbalanced_brackets_without_finish_reason(self, mock_post):
        from webui.ai import AISecurityError, call_ai

        # 无 finish_reason（有的端点不返回），括号不闭合算截断
        mock_post.return_value = _mock_stream_raw(
            '{"results": [{"i": 0}', finish_reason=None)

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.error_code, "truncated")

    @patch("webui.ai.requests.post")
    def test_plain_garbage_stays_invalid_response(self, mock_post):
        from webui.ai import AISecurityError, call_ai

        mock_post.return_value = _mock_stream_raw(
            "不好意思，我无法回答", finish_reason="stop")

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.error_code, "invalid_response")

    @patch("webui.ai.requests.post")
    def test_missing_finish_reason_does_not_crash(self, mock_post):
        from webui.ai import call_ai

        mock_post.return_value = self._ok_response({"x": 2}, finish_reason=None)

        self.assertEqual(
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}]),
            {"x": 2})

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_backoff_budget_only_counts_wait_time(self, mock_post, mock_sleep):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 429
        response.json.return_value = {"error": {"type": "rate_limit"}}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}], timeout=10)

        self.assertEqual(ctx.exception.error_code, "rate_limited")
        # 预算=10s 且只计退避等待：5s 可执行（5≤10），下一档 15s 被拒（5+15>10）
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(5)


class MatchJdsResumeAndTruncationTests(unittest.TestCase):
    """match_jds 断点续筛（completed_verdicts）与截断拆半重跑。"""

    def _jobs(self, n):
        return [{
            "job_id": f"job-{i:03d}",
            "title": f"岗位{i}",
            "salary": "20-30K",
            "location": "上海",
            "jd": "负责后端开发",
        } for i in range(n)]

    def test_completed_verdicts_are_skipped_and_merged(self):
        from webui.ai import match_jds

        done = {"job-000": {"verdict": "match", "reason": "已筛过"}}
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": False, "reason": "不合适", "caveats": []}]
        }) as call:
            result = match_jds(
                self._jobs(2), "画像", "https://x", "key",
                batch_size=10, completed_verdicts=done)

        self.assertEqual(call.call_count, 1)  # 只剩 job-001 要筛
        self.assertEqual(result["verdicts"]["job-000"]["verdict"], "match")  # 原样并入
        self.assertEqual(result["verdicts"]["job-001"]["verdict"], "not_match")

    def test_completed_verdicts_none_keeps_legacy_behavior(self):
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [
                {"i": 0, "match": True, "reason": "合适"},
                {"i": 1, "match": True, "reason": "合适"},
            ]
        }) as call:
            result = match_jds(self._jobs(2), "画像", "https://x", "key", batch_size=10)

        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(result["verdicts"]), 2)

    def test_truncated_batch_is_split_until_single(self):
        from webui.ai import AISecurityError, ERROR_TRUNCATED, match_jds

        calls = []

        def fake_call_ai(endpoint, key, messages, **kw):
            payload = json.loads(messages[-1]["content"])
            calls.append(len(payload))
            if len(payload) > 1:
                raise AISecurityError(ERROR_TRUNCATED)
            return {"results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            result = match_jds(self._jobs(3), "画像", "https://x", "key", batch_size=3)

        # 3 条一批被截断 → 拆 1+2 → 2 还截 → 再拆 1+1：全部单条成功
        self.assertEqual(
            [result["verdicts"][f"job-{i:03d}"]["verdict"] for i in range(3)],
            ["match", "match", "match"])
        self.assertEqual(sorted(calls), [1, 1, 1, 2, 3])


class ScreenJobsTruncationTests(unittest.TestCase):
    """screen_jobs 粗筛：截断拆半重跑，单条仍失败则该条保留（防错杀）。"""

    def test_truncated_batch_is_split(self):
        from webui.ai import AISecurityError, ERROR_TRUNCATED, screen_jobs

        jobs = [{
            "job_id": f"j{i}", "title": f"岗位{i}", "salary": "",
            "location": "", "job_labels": "", "company_scale": "",
        } for i in range(3)]

        def fake_call_ai(endpoint, key, messages, **kw):
            lines = messages[-1]["content"].strip().split("\n")
            if len(lines) > 1:
                raise AISecurityError(ERROR_TRUNCATED)
            return {"dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            result = screen_jobs(jobs, {"profile_summary": "画像"},
                                 "https://x", "key", batch_size=3, concurrency=1)

        self.assertEqual(sorted(result["kept"]), ["j0", "j1", "j2"])
        self.assertEqual(result["dropped"], [])


if __name__ == "__main__":
    unittest.main()
