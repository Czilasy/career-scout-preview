"""Tests for webui.ai: JSON validation, connection testing, error sanitization, batching.

Covers T014: AI JSON parsing success/failure, timeout handling, rejection of
unknown job_ids, error sanitization (no API key leak) and rank_jds batch limits.
Uses shared fixtures from tests/test_workbench_fixtures.py.
"""

from __future__ import annotations

import json
import os
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

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_stream_total_timeout_raises_safe_error(self, mock_post, _mock_sleep):
        """首字正常到达，但总时长超过 STREAM_TOTAL_TIMEOUT → ERROR_TIMEOUT。"""
        from webui.ai import call_ai, AISecurityError, STREAM_TOTAL_TIMEOUT

        # 构造一个流式响应：多个 chunk，第一个正常到达，后续触发总超时
        response = MagicMock()
        response.status_code = 200
        chunk1 = json.dumps({"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]})
        chunk2 = json.dumps({"choices": [{"delta": {"content": " world"}, "finish_reason": None}]})
        response.iter_lines.return_value = iter([
            f"data: {chunk1}", f"data: {chunk2}", "data: [DONE]", "",
        ])
        mock_post.return_value = response

        # 模拟时间流逝：首次调用返回 0（t0），之后全部返回 61（超过 60s 上限）
        call_count = [0]
        def fake_time():
            call_count[0] += 1
            return 0.0 if call_count[0] % 2 == 1 else float(STREAM_TOTAL_TIMEOUT + 1)

        with patch("webui.ai.time.time", side_effect=fake_time), \
             patch("webui.ai.RATE_LIMIT_ATTEMPTS", 1):
            with self.assertRaises(AISecurityError) as ctx:
                call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                        [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "timeout")
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(_mock_sleep.call_args_list, [
            unittest.mock.call(30.0), unittest.mock.call(30.0),
        ])

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
        self.assertEqual(mock_post.call_count, 3)

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

    @patch("webui.ai._windows_schannel_post")
    @patch("webui.ai.requests.post")
    def test_ssl_error_uses_windows_schannel_fallback(
        self, mock_post, mock_fallback,
    ):
        from webui.ai import call_ai

        expected = sample_ai_resume_response()
        mock_post.side_effect = requests.exceptions.SSLError("tls eof")
        mock_fallback.return_value = _mock_chat_response(expected)

        result = call_ai(
            "https://api.example.com/v1/chat/completions", "secret-key",
            [{"role": "user", "content": "hi"}],
        )

        self.assertEqual(result["profile_name"], expected["profile_name"])
        mock_fallback.assert_called_once()

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_error_does_not_leak_api_key(self, mock_post, _mock_sleep):
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

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_exception_context_suppressed_to_prevent_traceback_leak(self, mock_post, _mock_sleep):
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

    @patch("webui.ai._windows_schannel_post")
    @patch("webui.ai.requests.post")
    def test_ssl_error_uses_windows_schannel_fallback(
        self, mock_post, mock_fallback,
    ):
        from webui.ai import test_connection

        mock_post.side_effect = requests.exceptions.SSLError("tls eof")
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "pong"}}]
        }
        mock_fallback.return_value = response

        result = test_connection(
            "https://api.example.com/v1", "secret-key", model="test-model"
        )

        self.assertTrue(result["ok"])
        mock_fallback.assert_called_once()

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


class WindowsSchannelFallbackTests(unittest.TestCase):
    @patch("webui.ai.subprocess.run")
    def test_secret_is_sent_over_stdin_not_process_arguments(self, mock_run):
        from webui.ai import _windows_schannel_post

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "ok": True, "status": 200,
                "body": json.dumps({"choices": []}),
            }).encode("utf-8"),
            stderr=b"",
        )
        secret = "sk-never-in-process-arguments"

        response = _windows_schannel_post(
            "https://api.example.com/v1/chat/completions",
            secret,
            {"model": "test", "messages": []},
            timeout_seconds=30,
        )

        self.assertEqual(response.status_code, 200)
        command = " ".join(mock_run.call_args.args[0])
        self.assertNotIn(secret, command)
        stdin_payload = json.loads(
            mock_run.call_args.kwargs["input"].decode("utf-8")
        )
        self.assertEqual(stdin_payload["api_key"], secret)
        if os.name == "nt":
            import subprocess as _sp
            flags = int(mock_run.call_args.kwargs.get("creationflags", 0))
            self.assertTrue(flags & _sp.CREATE_NO_WINDOW,
                            "Schannel fallback 必须抑制 PowerShell 控制台窗口")


# ---------------------------------------------------------------------------
# rank_jds — batch limits and unknown job_id rejection
# ---------------------------------------------------------------------------

class ResumePlatformProjectionTests(unittest.TestCase):
    """简历分析按平台 schema 投影：BOSS stage / 智联 company_nature。"""

    def test_boss_accepts_stage_and_drops_company_nature(self):
        from webui.ai import _validate_unified_fields
        fields = _validate_unified_fields({
            "keyword": ["Python"], "city": "上海",
            "salary": "20-50K", "experience": "3-5年", "degree": "本科",
            "industry": "互联网", "scale": "100-499人", "stage": "B轮",
            "company_nature": "国企",
        }, "boss")
        self.assertEqual(fields["stage"], ["804"])
        self.assertEqual(fields["experience"], ["105"])
        self.assertNotIn("company_nature", fields)

    def test_zhilian_accepts_company_nature_and_drops_stage(self):
        from webui.ai import _validate_unified_fields
        fields = _validate_unified_fields({
            "keyword": ["Python"], "city": "上海",
            "experience": "3-5年", "degree": "本科", "company_nature": "国企",
            "stage": "B轮", "salary": "not-a-real-salary",
        }, "zhilian")
        self.assertEqual(fields["experience"], ["0305"])
        self.assertEqual(fields["degree"], ["4"])
        self.assertEqual(fields["company_nature"], ["1"])
        self.assertNotIn("stage", fields)
        self.assertEqual(fields["salary"], [])

    def test_sentinel_and_invalid_values_are_dropped(self):
        from webui.ai import _validate_unified_fields
        fields = _validate_unified_fields({
            "keyword": ["Python"], "city": ["上海", "不存在城市"],
            "experience": ["不限", "3-5年", "999"], "stage": ["0", "B轮", "bad"],
        }, "boss")
        self.assertEqual(fields["city"], ["上海"])
        self.assertEqual(fields["experience"], ["105"])
        self.assertEqual(fields["stage"], ["804"])

    def test_prompt_lists_platform_specific_filter_fields(self):
        from webui.ai import _build_field_options_prompt
        boss_prompt = _build_field_options_prompt("boss")
        self.assertIn("stage", boss_prompt)
        self.assertNotIn("company_nature", boss_prompt)
        zhilian_prompt = _build_field_options_prompt("zhilian")
        self.assertIn("company_nature", zhilian_prompt)
        self.assertNotIn("stage", zhilian_prompt)

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

    def test_missing_batch_result_uses_manifest_bounded_single_item_retry(self):
        from webui.ai import match_jds

        jobs = [
            {"job_id": "job-001", "title": "Python", "jd": "Python"},
            {"job_id": "job-002", "title": "Agent", "jd": "Agent"},
        ]
        responses = [
            {"results": [{"i": 0, "match": True, "reason": "匹配"}]},
            {"results": [{"i": 0, "match": False, "reason": "不匹配"}]},
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch("webui.ai.call_ai", side_effect=responses) as call:
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1, measurement_callback=capture,
                missing_result_retry_budget=1,
            )

        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["verdicts"]["job-001"]["verdict"], "match")
        self.assertEqual(
            result["verdicts"]["job-002"]["verdict"], "not_match"
        )
        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(len(terminals), 2)
        self.assertEqual(
            sorted(event["counts"]["item_index"] for event in terminals),
            [0, 1],
        )

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

    def test_transport_failure_emits_uncertain_terminals_without_batch_retry(self):
        """传输失败批次直接按 uncertain 落库并发终态，不再末尾补一轮。"""
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [
            {"job_id": f"job-{i}", "title": "岗位", "jd": "JD"}
            for i in range(4)
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_NETWORK),
        ):
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=4,
                concurrency=1, measurement_callback=capture,
            )

        terminals = [e for e in events if e["event_type"] == "item_terminal"]
        retries = [e for e in events if e["event_type"] == "retry"]
        self.assertEqual(len(terminals), 4)
        self.assertEqual(retries, [])
        self.assertEqual(
            sorted(e["counts"]["item_index"] for e in terminals),
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [result["verdicts"][f"job-{i}"]["verdict"] for i in range(4)],
            ["uncertain"] * 4,
        )

    def test_systemic_failure_emits_one_terminal_per_remaining_item(self):
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [
            {"job_id": f"job-{i}", "title": "岗位", "jd": "JD"}
            for i in range(3)
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_NETWORK)):
            with self.assertRaises(AISecurityError):
                match_jds(
                    jobs, "画像", "https://x", "key", batch_size=2,
                    concurrency=1, measurement_callback=capture,
                    raise_on_systemic=True,
                )

        terminals = [e for e in events if e["event_type"] == "item_terminal"]
        self.assertEqual(
            sorted(e["counts"]["item_index"] for e in terminals), [0, 1, 2]
        )

    def test_transport_failure_does_not_spend_missing_result_retry_budget(self):
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [
            {"job_id": "j0", "title": "岗位0", "jd": "JD0"},
            {"job_id": "j1", "title": "岗位1", "jd": "JD1"},
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        responses = [
            AISecurityError(ERROR_NETWORK),
            {"results": [{"i": 0, "match": True, "reason": "匹配"}]},
            AISecurityError(ERROR_NETWORK),
        ]
        with patch("webui.ai.call_ai", side_effect=responses):
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1, measurement_callback=capture,
                missing_result_retry_budget=1,
            )

        self.assertEqual(result["verdicts"]["j0"]["verdict"], "uncertain")
        self.assertEqual(result["verdicts"]["j1"]["verdict"], "uncertain")
        terminals = [e for e in events if e["event_type"] == "item_terminal"]
        self.assertEqual(
            sorted(e["counts"]["item_index"] for e in terminals), [0, 1]
        )

    def test_salary_hard_rule_filters_out_of_range_without_ai(self):
        from webui.ai import match_jds

        jobs = [
            {"job_id": "out", "title": "高薪岗", "salary": "22-30K", "jd": "JD"},
            {"job_id": "overlap", "title": "重叠岗", "salary": "15-25K", "jd": "JD"},
            {"job_id": "daily", "title": "日薪实习", "salary": "400-500元/天", "jd": "JD"},
            {"job_id": "unknown", "title": "面议岗", "salary": "面议", "jd": "JD"},
        ]
        criteria = {"salary": ["403", "404", "405"]}  # 3-5K/5-10K/10-20K
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "匹配"},
            {"i": 1, "match": True, "reason": "匹配"},
            {"i": 2, "match": True, "reason": "匹配"},
        ]}) as call:
            result = match_jds(
                jobs, "画像", "https://x", "key",
                batch_size=10, concurrency=1, criteria=criteria,
            )

        self.assertEqual(result["verdicts"]["out"]["verdict"], "not_match")
        self.assertIn("不在筛选范围", result["verdicts"]["out"]["reason"])
        self.assertEqual(result["verdicts"]["overlap"]["verdict"], "match")
        self.assertEqual(result["verdicts"]["daily"]["verdict"], "match")
        self.assertEqual(result["verdicts"]["unknown"]["verdict"], "match")
        self.assertEqual(call.call_count, 1)

    def test_salary_hard_rule_all_out_of_range_skips_ai(self):
        from webui.ai import match_jds

        jobs = [{"job_id": "j1", "title": "高薪岗", "salary": "30-40K", "jd": "JD"}]
        with patch("webui.ai.call_ai") as call:
            result = match_jds(
                jobs, "画像", "https://x", "key",
                batch_size=10, concurrency=1, criteria={"salary": ["403"]},
            )

        self.assertEqual(result["verdicts"]["j1"]["verdict"], "not_match")
        call.assert_not_called()

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

    def _call_with_retry_limits(self, call_ai, retry_limits):
        try:
            return call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}],
                retry_limits=retry_limits,
            )
        except TypeError as exc:
            self.fail(f"call_ai 必须接受显式 retry_limits: {exc}")

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_explicit_network_retry_budget_zero_allows_one_attempt(
        self, mock_post, _mock_sleep,
    ):
        from webui.ai import AISecurityError, call_ai

        mock_post.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(AISecurityError):
            self._call_with_retry_limits(call_ai, {"network_error": 0})

        self.assertEqual(mock_post.call_count, 1)

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_explicit_network_retry_budget_two_allows_at_most_three_attempts(
        self, mock_post, _mock_sleep,
    ):
        from webui.ai import AISecurityError, call_ai

        mock_post.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(AISecurityError):
            self._call_with_retry_limits(call_ai, {"network_error": 2})

        self.assertEqual(mock_post.call_count, 3)

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_explicit_network_budget_does_not_retry_unauthorized_transport_codes(
        self, mock_post, mock_sleep,
    ):
        from webui.ai import AISecurityError, call_ai

        cases = [
            ("timeout", requests.Timeout("timeout")),
            ("rate_limited", MagicMock(status_code=429)),
        ]
        cases[1][1].json.return_value = {"error": {"type": "rate_limit"}}
        for code, failure in cases:
            with self.subTest(code=code):
                mock_post.reset_mock()
                mock_sleep.reset_mock()
                mock_post.side_effect = None
                if isinstance(failure, BaseException):
                    mock_post.side_effect = failure
                else:
                    mock_post.return_value = failure
                with self.assertRaises(AISecurityError) as raised:
                    self._call_with_retry_limits(call_ai, {"network_error": 2})
                self.assertEqual(raised.exception.error_code, code)
                self.assertEqual(mock_post.call_count, 1)
                mock_sleep.assert_not_called()

    @patch("webui.ai.call_ai")
    def test_strict_tuning_transport_failure_does_not_retry_batch(
        self, mock_call_ai,
    ):
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        mock_call_ai.side_effect = AISecurityError(ERROR_NETWORK)
        with self.assertRaises(AISecurityError):
            match_jds(
                [{"job_id": "job-1", "title": "岗位", "jd": "JD"}],
                "画像", "https://x", "key", batch_size=1, concurrency=1,
                raise_on_systemic=True, retry_limits={"network_error": 0},
            )

        self.assertEqual(mock_call_ai.call_count, 1)

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_attempts_retries_and_recovery_are_all_measured(
        self, mock_post, _mock_sleep,
    ):
        from webui.ai import call_ai

        mock_post.side_effect = [
            requests.Timeout("t"), self._ok_response({"ok": True}),
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        result = call_ai(
            "https://api.example.com/v1/chat/completions", "key",
            [{"role": "user", "content": "hi"}],
            measurement_callback=capture, measurement_stage="fine",
        )

        self.assertEqual(result, {"ok": True})
        requests_seen = [e for e in events if e["event_type"] == "request"]
        retries = [e for e in events if e["event_type"] == "retry"]
        self.assertEqual(len(requests_seen), 2)
        self.assertEqual(len(retries), 1)
        self.assertEqual(requests_seen[0]["error_code"], "timeout")
        self.assertEqual(requests_seen[0]["metadata"]["failure_phase"], "connect")
        self.assertEqual(requests_seen[1]["metadata"]["outcome"], "success")
        correlation_ids = {
            e["metadata"]["correlation_id"] for e in requests_seen
        }
        self.assertEqual(len(correlation_ids), 1)

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
        self.assertEqual(mock_post.call_count, 3)

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
    def test_invalid_response_keeps_safe_parse_diagnostics(self, mock_post):
        from webui.ai import AISecurityError, call_ai

        mock_post.return_value = _mock_stream_raw(
            "不是 JSON", finish_reason="stop"
        )

        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "secret-key",
                [{"role": "user", "content": "hi"}],
            )

        diagnostics = ctx.exception.diagnostics
        self.assertEqual(diagnostics["failure_phase"], "json_decode")
        self.assertEqual(diagnostics["finish_reason"], "stop")
        self.assertEqual(diagnostics["response_length"], len("不是 JSON"))
        self.assertIn("parse_error", diagnostics)
        self.assertNotIn("secret-key", json.dumps(diagnostics, ensure_ascii=False))

    @patch("webui.ai.time.sleep")
    @patch("webui.ai._post_ai_json")
    def test_tls_failure_is_distinct_from_connection_failure(
        self, mock_post, _mock_sleep,
    ):
        from webui.ai import AISecurityError, call_ai

        mock_post.side_effect = requests.exceptions.SSLError("tls eof")
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "secret-key",
                [{"role": "user", "content": "hi"}],
                measurement_callback=capture,
            )

        self.assertEqual(ctx.exception.diagnostics["failure_phase"], "tls")
        self.assertEqual(ctx.exception.diagnostics["exception_type"], "SSLError")
        self.assertTrue(all(e.get("metadata", {}).get("failure_phase") == "tls"
                            for e in events if e["event_type"] == "request"))

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
    def test_default_retry_wait_ignores_single_timeout_budget(self, mock_post, mock_sleep):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 429
        response.json.return_value = {"error": {"type": "rate_limit"}}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}], timeout=10)

        self.assertEqual(ctx.exception.error_code, "rate_limited")
        # 默认策略固定 3 次/30 秒，不受单次 timeout=10 预算截断
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [
            unittest.mock.call(30.0), unittest.mock.call(30.0),
        ])


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

    def test_on_batch_done_serial_reports_each_batch(self):
        from webui.ai import match_jds

        snapshots = []
        with patch("webui.ai.call_ai", return_value={
            "results": [
                {"i": 0, "match": True, "reason": "合适", "caveats": []},
                {"i": 1, "match": False, "reason": "不合适", "caveats": []},
            ]
        }):
            result = match_jds(
                self._jobs(4), "画像", "https://x", "key",
                batch_size=2, concurrency=1,
                on_batch_done=lambda verdicts, done: snapshots.append(
                    (dict(verdicts), sorted(done))
                ),
            )

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0][1], ["job-000", "job-001"])
        self.assertEqual(snapshots[1][1], ["job-000", "job-001", "job-002", "job-003"])
        self.assertEqual(len(result["verdicts"]), 4)

    def test_on_batch_done_concurrent_reports_cumulative_snapshot(self):
        from webui.ai import match_jds

        snapshots = []
        with patch("webui.ai.call_ai", return_value={
            "results": [
                {"i": 0, "match": True, "reason": "合适", "caveats": []},
                {"i": 1, "match": True, "reason": "合适", "caveats": []},
            ]
        }):
            result = match_jds(
                self._jobs(4), "画像", "https://x", "key",
                batch_size=2, concurrency=2,
                on_batch_done=lambda verdicts, done: snapshots.append(
                    (dict(verdicts), set(done))
                ),
            )

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[-1][1], {"job-000", "job-001", "job-002", "job-003"})
        self.assertEqual(len(result["verdicts"]), 4)

    def test_on_batch_done_failure_raises_checkpoint_error(self):
        from webui.ai import AICheckpointError, match_jds

        def boom(_verdicts, _done):
            raise RuntimeError("checkpoint rejected")

        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }):
            with self.assertRaises(AICheckpointError):
                match_jds(
                    self._jobs(1), "画像", "https://x", "key",
                    batch_size=1, on_batch_done=boom,
                )


class MatchJdsFlagsTests(unittest.TestCase):
    """精筛 flags（B033 靠谱判定）：结构化解析、分级判定、高危强制 not_match。"""

    def _jobs(self, n):
        return [{
            "job_id": f"job-{i:03d}",
            "title": f"岗位{i}",
            "salary": "20-30K",
            "location": "上海",
            "jd": "负责后端开发",
        } for i in range(n)]

    def test_high_flag_forces_not_match_with_prefix(self):
        """命中高危特征：即使 match=true 也强制 not_match，reason 以\"疑似骗局：\"开头。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "技能契合",
                "caveats": [],
                "flags": [{"code": "C1", "level": "high", "reason": "要求先交培训费"}],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["verdict"], "not_match")
        self.assertTrue(verdict["reason"].startswith("疑似骗局："))
        self.assertEqual(verdict["flags"][0]["level"], "high")

    def test_high_flag_without_reason_gets_default_prefix(self):
        """高危命中但 AI 未写 reason：reason 补为\"疑似骗局：命中高危可疑特征\"。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": False, "reason": "",
                "flags": [{"code": "E1", "level": "high", "reason": "标题与JD不符"}],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["verdict"], "not_match")
        self.assertTrue(verdict["reason"].startswith("疑似骗局："))

    def test_single_medium_flag_degrades_to_caveats(self):
        """仅命中 1 条中危：不输出 flags，降级为 caveats 文本。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "合适",
                "flags": [{"code": "B1", "level": "medium", "reason": "标题含无责底薪"}],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["flags"], [])
        self.assertTrue(any("需留意" in c for c in verdict["caveats"]))

    def test_two_medium_flags_are_output(self):
        """命中 ≥2 条中危：输出 flags，不改 match 判定。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "合适",
                "flags": [
                    {"code": "B1", "level": "medium", "reason": "标题含无责底薪"},
                    {"code": "F3", "level": "medium", "reason": "试用期未写明"},
                ],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["verdict"], "match")
        self.assertEqual(len(verdict["flags"]), 2)
        self.assertTrue(all(f["level"] == "medium" for f in verdict["flags"]))

    def test_flags_missing_field_does_not_break(self):
        """老模型不输出 flags 字段：不报错，flags 为空列表。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适"}]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        self.assertEqual(result["verdicts"]["job-000"]["flags"], [])

    def test_dirty_flag_items_are_dropped(self):
        """旧字符串格式/非法 level/空 reason 的 flags 项丢弃，合法项保留。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "合适",
                "flags": [
                    "需留意：疑似中介",  # 旧字符串格式
                    {"code": "X1", "level": "weird", "reason": "非法级别"},
                    {"code": "A1", "level": "medium", "reason": ""},  # 空 reason
                    {"code": "F1", "level": "high", "reason": "JD留个人微信"},
                ],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual([f["code"] for f in verdict["flags"]], ["F1"])
        self.assertEqual(verdict["verdict"], "not_match")


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


class AIScreeningPromptPolicyTests(unittest.TestCase):
    """粗筛/精筛提示词：候选人方向为锚，硬性条件才排除，显式放宽才覆盖默认。"""

    def test_match_jds_system_prompt_uses_candidate_direction_as_anchor(self):
        """精筛以候选人主业方向为锚：跨链路默认不匹配，显式放宽才覆盖。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "AI产品客户成功", "jd": "服务AI客户"}]
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "技能可迁移", "caveats": []}]
        }) as call:
            match_jds(jobs, "AI应用开发，只找正儿八经的应用开发", "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("判断是参考不是法律", prompt)
        self.assertIn("匹配从宽只适用于候选人没有约束的维度", prompt)
        self.assertIn("以候选人自己的主业方向为锚", prompt)
        self.assertIn("明显跨链路的岗位默认 match=false", prompt)
        self.assertIn("用户明确写'不限/都可以/接受xx'", prompt)
        self.assertIn("以 JD 主责为准", prompt)
        self.assertIn("不得把 AI 已识别出的方向冲突、硬性不满足只写进 caveats 后仍判 match", prompt)
        self.assertNotIn("本身不得作为 match=false 的理由", prompt)
        self.assertNotIn("行业、类别、技能不完全一致不排除", prompt)

    def test_screen_jobs_system_prompt_ignores_job_category_for_dropping(self):
        from webui.ai import screen_jobs

        jobs = [{"job_id": "job-001", "title": "教学讲师", "salary": "10-15K", "location": "东莞"}]
        with patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            screen_jobs(jobs, {"profile_summary": "AI应用开发"}, "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("岗位名称或类别（如客服、讲师、销售、内容制作、运营等）不得单独作为剔除理由", prompt)
        self.assertIn("字段为空或未列出 = 不限", prompt)
        self.assertIn("不得按该维度剔除", prompt)

    def test_screen_jobs_prompt_has_profile_widening_rule(self):
        """初筛 prompt 含求职画像放宽规则（B033，初筛判定逻辑本体不动）。"""
        from webui.ai import screen_jobs

        jobs = [{"job_id": "job-001", "title": "教学讲师", "salary": "10-15K", "location": "东莞"}]
        with patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            screen_jobs(
                jobs,
                {"profile_summary": "3年经验，东莞、深圳都可以", "city": ["东莞"]},
                "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("求职画像放宽", prompt)
        self.assertIn("以画像表述为准放宽对应判断", prompt)
        self.assertIn("候选人画像（仅用于放宽，不作为硬条件）：3年经验，东莞、深圳都可以", prompt)

    def test_match_jds_prompt_three_channels(self):
        """精筛 prompt 三通道：求职意愿 > 筛选条件 > 画像事实；未体现不得推断。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "AI教学", "jd": "负责AI课程教学"}]
        criteria = {"city": ["东莞"], "degree": ["4"], "salary": ["406"]}
        facts = {
            "core_skills": ["Python"],
            "projects": [{"name": "订单系统", "role": "后端"}],
            "job_type": "全职",
            "languages": ["英语"],
        }
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }) as call:
            match_jds(
                jobs, "3年Python后端，AI相关行业都可以", "https://x", "key",
                batch_size=1, criteria=criteria, profile_facts=facts)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("【第一层·求职意愿】", prompt)
        self.assertIn("AI相关行业都可以", prompt)
        self.assertIn("【第二层·筛选条件】", prompt)
        self.assertIn("【第三层·画像事实】", prompt)
        self.assertIn("核心技能：Python", prompt)
        self.assertIn("默认匹配，不得写'候选人未知'", prompt)
        self.assertIn("以意愿为准", prompt)
        # 特征清单已并入 prompt，且 flags 为必填字段
        self.assertIn("岗位靠谱判定", prompt)
        self.assertIn("C1（高危）培训收费", prompt)
        self.assertIn("flags 为必填字段，无命中输出空数组", prompt)
        self.assertIn("疑似骗局：", prompt)

    def test_match_jds_prompt_salary_filter_is_hard_rule(self):
        """薪资筛选是硬规则，精筛 prompt 明确禁止 AI 再按薪资范围互相矛盾地拒绝。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "salary": "15-25K", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "合适", "caveats": []}
        ]}) as call:
            match_jds(
                jobs, "画像", "https://x", "key", batch_size=1,
                criteria={"salary": ["405"]})

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("薪资筛选区间已由系统硬性核对", prompt)

    def test_match_jds_prompt_without_facts_falls_back(self):
        """老轮无画像事实/筛选条件：退化两通道，不报错。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }) as call:
            match_jds(jobs, "画像", "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("（无画像事实，按未体现处理）", prompt)
        self.assertIn("（无明确标准，宽松判断）", prompt)

    def test_match_jds_prompt_marks_unconfirmed_preferences(self):
        """精筛信息包对未确认的求职偏好显式标记，不静默默认匹配。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }) as call:
            match_jds(jobs, "3年Python后端", "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("【第四层·未确认偏好】", prompt)
        self.assertIn("标记为未填写/未确认", prompt)
        self.assertIn("只找全职，兼职/外包/按单结算不考虑", prompt)
        self.assertIn("不接受996", prompt)
        self.assertIn("不得当作默认匹配", prompt)
        self.assertIn("求职类型未确认，JD 为兼职", prompt)
        self.assertIn("默认匹配，不得写'候选人未知'", prompt)


class FlagFeaturesTests(unittest.TestCase):
    """flag_features 特征清单与分级判定边界（B033 T004 Checkpoint）。"""

    def test_feature_list_has_20_items_with_valid_levels(self):
        from webui.flag_features import FLAG_FEATURES, VALID_FLAG_LEVELS
        self.assertEqual(len(FLAG_FEATURES), 20)
        codes = [f["code"] for f in FLAG_FEATURES]
        self.assertEqual(len(set(codes)), 20)  # code 唯一
        for item in FLAG_FEATURES:
            self.assertIn(item["level"], VALID_FLAG_LEVELS)
            self.assertTrue(item["name"])
            self.assertTrue(item["basis"])

    def test_single_medium_degrades_to_caveats(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([{"code": "B1", "level": "medium", "reason": "标题含无责底薪"}])
        self.assertEqual(decide_flags(flags), {
            "flags": [], "caveats": ["需留意：销售话术标题：标题含无责底薪"]})

    def test_d2_day_rate_is_medium_not_high(self):
        from webui.flag_features import FLAG_FEATURES_BY_CODE
        d2 = FLAG_FEATURES_BY_CODE["D2"]
        self.assertEqual(d2["level"], "medium")
        self.assertIn("元/天", d2["basis"])
        self.assertIn("不单独作为异常", d2["basis"])

    def test_clean_flags_downgrades_d2_day_rate_to_medium(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([{"code": "D2", "level": "high", "reason": "薪资450-600元/天"}])
        self.assertEqual(flags[0]["level"], "medium")
        decided = decide_flags(flags)
        self.assertEqual(decided["flags"], [])
        self.assertEqual(len(decided["caveats"]), 1)

    def test_two_medium_output_flags(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([
            {"code": "B1", "level": "medium", "reason": "a"},
            {"code": "F3", "level": "medium", "reason": "b"},
        ])
        decided = decide_flags(flags)
        self.assertEqual(len(decided["flags"]), 2)
        self.assertEqual(decided["caveats"], [])

    def test_one_high_output_flags(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([{"code": "C1", "level": "high", "reason": "收培训费"}])
        decided = decide_flags(flags)
        self.assertEqual(len(decided["flags"]), 1)
        self.assertEqual(decided["caveats"], [])

    def test_empty_input(self):
        from webui.flag_features import clean_flags, decide_flags
        self.assertEqual(decide_flags(clean_flags([])), {"flags": [], "caveats": []})
        self.assertEqual(decide_flags(clean_flags(None)), {"flags": [], "caveats": []})

    def test_clean_flags_drops_invalid_items(self):
        from webui.flag_features import clean_flags
        cleaned = clean_flags([
            "旧格式文本",
            {"code": "X", "level": "weird", "reason": "非法级别"},
            {"code": "X", "level": "medium", "reason": ""},
            {"code": "F1", "level": "high", "reason": "留个人微信"},
            {"code": "UNKNOWN", "level": "medium", "reason": "清单外但结构合法"},
        ])
        self.assertEqual([f["code"] for f in cleaned], ["F1", "UNKNOWN"])

    def test_prompt_text_renders_all_features(self):
        from webui.flag_features import build_features_prompt_text
        text = build_features_prompt_text()
        self.assertEqual(text.count("\n- "), 19)
        self.assertIn("C1（高危）培训收费", text)
        self.assertIn("A1（中危）劳务派遣/外包包装", text)
        self.assertNotIn("常年挂着", text)


class ProfileFactsTests(unittest.TestCase):
    """画像事实提取与宽松验证（B033 T005/T006）。"""

    def test_validate_profile_facts_keeps_valid_items(self):
        from webui.ai import _validate_profile_facts
        facts = _validate_profile_facts({
            "core_skills": ["Python", "Django"],
            "projects": [{"name": "订单系统", "role": "后端", "stack": "Django", "summary": "订单模块"}],
            "job_type": "全职",
            "languages": ["英语"],
        })
        self.assertEqual(facts["core_skills"], ["Python", "Django"])
        self.assertEqual(facts["projects"][0]["name"], "订单系统")
        self.assertEqual(facts["job_type"], "全职")
        self.assertEqual(facts["languages"], ["英语"])

    def test_validate_profile_facts_drops_invalid_items(self):
        from webui.ai import _validate_profile_facts
        facts = _validate_profile_facts({
            "core_skills": ["Python", 123, "", "  "],
            "projects": [
                {"name": "好项目", "role": "后端"},
                {"stack": "无name的项目"},
                "不是对象",
            ],
            "job_type": "不限",  # 非法枚举
            "languages": [None, "英语"],
        })
        self.assertEqual(facts["core_skills"], ["Python"])
        self.assertEqual([p["name"] for p in facts["projects"]], ["好项目"])
        self.assertNotIn("job_type", facts)
        self.assertEqual(facts["languages"], ["英语"])

    def test_validate_profile_facts_missing_fields(self):
        from webui.ai import _validate_profile_facts
        self.assertEqual(_validate_profile_facts(None), {})
        self.assertEqual(_validate_profile_facts("not-a-dict"), {})
        facts = _validate_profile_facts({"job_type": "未体现"})
        self.assertEqual(facts, {"job_type": "未体现"})

    def test_validate_profile_facts_keeps_explicit_degree_only(self):
        from webui.ai import _validate_profile_facts
        facts = _validate_profile_facts({"degree": "本科"})
        self.assertEqual(facts["degree"], "本科")
        self.assertNotIn("degree", _validate_profile_facts({"degree": ""}))
        self.assertNotIn("degree", _validate_profile_facts({"degree": 123}))

    def test_analyze_resume_extracts_profile_facts(self):
        from webui.ai import analyze_resume_to_fields

        payload = {
            "keyword": [{"word": "Python", "recommended": True}],
            "city": "上海",
            "profile_summary": "3年Python后端经验，本科学历，期望上海15-25K，技能Python/Django，做过后端订单系统。",
            "profile_facts": {
                "core_skills": ["Python", "Django"],
                "projects": [{"name": "订单系统", "role": "后端开发"}],
                "job_type": "全职",
                "languages": ["英语"],
            },
        }
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="3年Python后端"):
            result = analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        self.assertEqual(result["profile_facts"]["core_skills"], ["Python", "Django"])
        self.assertEqual(result["profile_facts"]["job_type"], "全职")

    def test_analyze_resume_missing_profile_facts(self):
        """老端点不返回 profile_facts：返回空 dict，不报错。"""
        from webui.ai import analyze_resume_to_fields

        payload = {"keyword": [{"word": "Python", "recommended": True}],
                   "city": "上海", "profile_summary": "3年Python后端"}
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="3年Python后端"):
            result = analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        self.assertEqual(result["profile_facts"], {})

    def test_analyze_resume_prompt_contains_facts_rules(self):
        from webui.ai import analyze_resume_to_fields

        with patch("webui.ai.call_ai", return_value={
            "keyword": [], "city": "", "profile_summary": "s",
        }) as call, patch("webui.ai._resume_bytes_to_text", return_value="简历"):
            analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("profile_facts", prompt)
        self.assertIn("core_skills", prompt)
        self.assertIn("job_type", prompt)
        self.assertIn("未体现", prompt)
        self.assertIn("自然语言", prompt)
        self.assertIn("简历里明确写了就填，没写的字段留空", prompt)
        self.assertIn("简历写了什么就写什么，没写的不补", prompt)
        self.assertIn("projects 只列简历明确的项目/工作经历", prompt)
        self.assertNotIn("事实清单式", prompt)
        self.assertNotIn("第一句写工作年限", prompt)
        self.assertNotIn("禁止评价性概括", prompt)

    def test_analyze_resume_prompt_contains_preference_fill_rules(self):
        """简历分析提示词包含偷偷塞字偏好规则与 5-10 句扩写约束。"""
        from webui.ai import analyze_resume_to_fields

        with patch("webui.ai.call_ai", return_value={
            "keyword": [], "city": "", "profile_summary": "s",
        }) as call, patch("webui.ai._resume_bytes_to_text", return_value="简历"):
            analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("最终总共5-10句", prompt)
        self.assertIn("随机挑1-3个自然补充", prompt)
        self.assertIn("不一次全塞", prompt)
        self.assertIn("只找全职，兼职/外包/按单结算不考虑", prompt)
        self.assertIn("双休", prompt)
        self.assertIn("远程全职可接受", prompt)
        self.assertIn("不接受996", prompt)
        self.assertIn("画像里已有该偏好就不重复", prompt)
        self.assertIn("degree", prompt)
        self.assertIn("项目经历只写项目方向、个人角色和所用技术栈", prompt)
        self.assertIn("summary 可写职责、实现方式和量化成果", prompt)


class AIMeasurementEventTests(unittest.TestCase):
    """T016 RED: AI 阶段测量事件 — 请求时长、重试、批次计数、敏感字段拒绝。

    覆盖 FR-030、SC-006、SC-007、data-model.md 2.9。
    screen_jobs/match_jds 必须通过 measurement sink 记录：
    - 每次请求的 attempt、duration、error_code；
    - 退避和截断拆分；
    - 批次输入输出数量；
    - 不保存密钥、原始简历或敏感响应。
    """

    def test_screen_jobs_emits_measurement_events(self):
        """screen_jobs 必须通过 measurement_callback 发射事件。"""
        from webui import ai
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jobs = [{"job_id": f"j{i}", "title": "T", "company": "C",
                 "salary": "10K", "city": "上海", "jd": "safe"}
                for i in range(3)]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"kept": ["j0", "j1", "j2"], "dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x", "key",
                batch_size=3, concurrency=1,
                measurement_callback=capture,
            )

        event_types = [e["event_type"] for e in events]
        # 必须至少有 request 事件和 batch 事件
        self.assertTrue(any(et in event_types for et in ("request", "batch", "item_terminal")),
                        f"必须发射测量事件，实际: {event_types}")

    def test_screen_jobs_measurement_excludes_api_key(self):
        """SC-006: 测量事件不得包含 api_key。"""
        from webui import ai
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jobs = [{"job_id": "j0", "title": "T", "company": "C",
                 "salary": "10K", "city": "上海", "jd": "safe"}]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"kept": ["j0"], "dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x",
                "sk-secret-key-12345",
                batch_size=1, concurrency=1,
                measurement_callback=capture,
            )

        for ev in events:
            payload = json.dumps(ev, ensure_ascii=False)
            self.assertNotIn("sk-secret-key-12345", payload,
                              "测量事件不得包含 API key")
            self.assertNotIn("api_key", payload.lower(),
                              "测量事件不得出现 api_key 字段名")

    def test_screen_jobs_measurement_excludes_resume_text(self):
        """SC-006: 测量事件不得包含原始简历文本。"""
        from webui import ai
        events = []
        sensitive_resume = "张三 13800001111 身份证110xxx"

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jobs = [{"job_id": "j0", "title": "T", "company": "C",
                 "salary": "10K", "city": "上海",
                 "jd": "safe", "resume": sensitive_resume}]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"kept": ["j0"], "dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x", "key",
                batch_size=1, concurrency=1,
                measurement_callback=capture,
            )

        for ev in events:
            payload = json.dumps(ev, ensure_ascii=False)
            self.assertNotIn(sensitive_resume, payload,
                              "测量事件不得包含原始简历文本")

    def test_match_jds_emits_measurement_events(self):
        """match_jds 必须通过 measurement_callback 发射事件。"""
        from webui import ai
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jds = [{"job_id": f"j{i}", "title": "T", "jd": "safe"} for i in range(3)]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"results": [
                {"i": 0, "match": True, "reason": "匹配"},
                {"i": 1, "match": False, "reason": "不匹配"},
                {"i": 2, "match": True, "reason": "匹配"},
            ]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.match_jds(
                jds, "候选人画像", "https://x", "key",
                batch_size=3, concurrency=1,
                measurement_callback=capture,
            )

        [e["event_type"] for e in events]
        self.assertGreater(len(events), 0, "match_jds 必须发射测量事件")

    def test_match_jds_terminal_events_use_global_indices_across_batches(self):
        """精筛分批后终态索引仍对应原始输入，不能在每批从零开始。"""
        from webui import ai
        events = []
        jobs = [{"job_id": f"j{i}", "title": "T", "jd": "safe"}
                for i in range(5)]

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        def fake_call_ai(messages, url, api_key, **kw):
            return {"results": [
                {"i": 0, "match": True, "reason": "匹配"},
                {"i": 1, "match": False, "reason": "不匹配"},
            ]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1, measurement_callback=capture,
            )

        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(
            [event["counts"]["item_index"] for event in terminals],
            [0, 1, 2, 3, 4],
        )
        self.assertTrue(
            all(event["counts"]["input_count"] == 5 for event in terminals)
        )

    def test_match_jds_preserves_runner_assigned_indices_after_rough_filter(self):
        """精筛必须保留粗筛前的原始索引，避免与 dropped 项碰撞。"""
        from webui import ai
        events = []
        jobs = [
            {"job_id": "j1", "title": "T", "jd": "safe",
             "_tuning_measurement_index": 1},
            {"job_id": "j4", "title": "T", "jd": "safe",
             "_tuning_measurement_index": 4},
        ]

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "匹配"},
            {"i": 1, "match": False, "reason": "不匹配"},
        ]}):
            ai.match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                measurement_callback=capture, measurement_input_count=5,
            )

        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(
            [event["counts"]["item_index"] for event in terminals], [1, 4]
        )
        self.assertTrue(
            all(event["counts"]["input_count"] == 5 for event in terminals)
        )

    def test_screen_jobs_can_emit_only_final_dropped_terminals(self):
        """端到端粗筛保留项仍会进入精筛，只有 dropped 才是最终终态。"""
        from webui import ai
        events = []
        jobs = [{"job_id": f"j{i}", "title": "T", "salary": "10K",
                 "location": "上海"} for i in range(5)]

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        def fake_call_ai(messages, url, api_key, **kw):
            return {"dropped": [{"i": 0, "reason": "城市不符"}]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x", "key",
                batch_size=2, concurrency=1, measurement_callback=capture,
                emit_kept_terminal=False,
            )

        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(
            [event["counts"]["item_index"] for event in terminals],
            [0, 2, 4],
        )
        self.assertTrue(
            all(event["counts"]["status"] == "dropped" for event in terminals)
        )
        self.assertTrue(
            all(event["counts"]["input_count"] == 5 for event in terminals)
        )


if __name__ == "__main__":
    unittest.main()
