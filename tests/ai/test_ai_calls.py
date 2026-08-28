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

from tests.ai.harness import _mock_chat_response, _mock_stream_raw


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
             patch("webui.ai_retry.random.uniform", return_value=0.0):
            with self.assertRaises(AISecurityError) as ctx:
                call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                        [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "timeout")
        self.assertEqual(mock_post.call_count, 4)
        self.assertEqual(_mock_sleep.call_args_list, [
            unittest.mock.call(2.0), unittest.mock.call(4.0),
            unittest.mock.call(8.0),
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
        self.assertEqual(mock_post.call_count, 4)

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
    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    def test_empty_response_raw_logged_per_attempt_and_retried(
        self, mock_sleep, _mock_uniform, mock_post,
    ):
        """B063：HTTP 200 空 body 在统一层重试 2 次，三次尝试各写一次原始日志。

        旧行为：空 body 直接抛错且只记录 1 次；新行为重试（共 3 次尝试），
        每次尝试的原始响应都记录，诊断带 empty_response。
        """
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = iter(["data: [DONE]", ""])
        mock_post.return_value = response

        with patch("webui.ai.record_raw_ai_response") as record:
            with self.assertRaises(AISecurityError) as ctx:
                call_ai(
                    "https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}],
                )
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(record.call_count, 3)
        self.assertEqual(
            ctx.exception.diagnostics.get("failure_phase"), "empty_response")

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

    def test_stream_read_blocked_forever_hits_budget(self):
        """AI 端点连接保持但不返回任何行时，join(budget) 兜底抛 ERROR_TIMEOUT。

        回归：_read_stream 的 STREAM_TOTAL_TIMEOUT 检查只在 iter_lines 循环体内
        执行；iter_lines 阻塞在 readline（read timeout 对流式不保证生效）时循环体
        永不执行，任务会无限卡死。线程 + join(budget) 必须在此场景兜底。
        """
        import threading
        from webui.ai import ERROR_TIMEOUT, AISecurityError, call_ai

        release = threading.Event()

        class _BlockingLines:
            def __iter__(self):
                return self

            def __next__(self):
                release.wait(30)  # 模拟 AI 端点连接保持但不返回任何行
                raise StopIteration

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = _BlockingLines()
        with patch("webui.ai.requests.post", return_value=response), \
             patch("webui.ai.time.sleep"), \
             patch("webui.ai_retry.random.uniform", return_value=0.0):
            with self.assertRaises(AISecurityError) as ctx:
                call_ai("https://api.example.com/v1/chat/completions", "key",
                        [{"role": "user", "content": "hi"}], timeout=0.2)
        release.set()  # 解除阻塞读取线程，避免泄漏
        self.assertEqual(ctx.exception.error_code, ERROR_TIMEOUT)
        # 超时路径必须尝试关闭连接，尽早解除底层阻塞
        self.assertTrue(response.close.called)

    @patch("webui.ai.call_ai")
    def test_match_jds_fine_batch_uses_batch_timeout(self, mock_call):
        """match_jds 精筛每批调用 call_ai 必须携带 FINE_BATCH_TIMEOUT（180s）。"""
        from webui.ai import FINE_BATCH_TIMEOUT, match_jds

        mock_call.return_value = {"results": [
            {"i": 0, "match": True, "reason": "ok", "caveats": [], "flags": []},
        ]}
        jobs = [{"job_id": "j1", "title": "Python", "jd": "需要 Python 3 年经验"}]
        match_jds(jobs, "画像", "https://x", "key", batch_size=1)

        self.assertEqual(mock_call.call_count, 1)
        self.assertEqual(
            mock_call.call_args.kwargs.get("timeout"), FINE_BATCH_TIMEOUT,
            "精筛单批 AI 请求必须使用 FINE_BATCH_TIMEOUT 作为总时长上限",
        )


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

    @patch("webui.ai.requests.post")
    def test_reasoning_field_accepted_for_reasoning_models(self, mock_post):
        """DeepSeek V4 等推理模型把思考放 message.reasoning 且 content 为空。

        回归：opencode.ai zen/go 端点返回 content="" + reasoning="..." 时，
        test_connection 必须判定可用，不能误报 invalid_response。
        """
        from webui.ai import test_connection
        response = MagicMock(); response.status_code = 200
        response.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning": "We need to reply exactly pong.",
                },
            }],
        }
        mock_post.return_value = response
        result = test_connection("https://api.example.com/v1", "secret", "model")
        self.assertTrue(result["ok"])
        self.assertEqual(result["generation"], "ready")


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
        self.assertEqual(mock_post.call_count, 4)

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

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_default_retry_wait_ignores_single_timeout_budget(
        self, mock_post, mock_sleep, _mock_uniform,
    ):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 429
        response.json.return_value = {"error": {"type": "rate_limit"}}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}], timeout=10)

        self.assertEqual(ctx.exception.error_code, "rate_limited")
        # 默认策略按错误码退避，不受单次 timeout=10 预算截断
        self.assertEqual(mock_post.call_count, 4)
        self.assertEqual(mock_sleep.call_args_list, [
            unittest.mock.call(5.0), unittest.mock.call(15.0),
            unittest.mock.call(30.0),
        ])


class BadResponseTypeGuardTests(unittest.TestCase):
    """018：端点返回非列表 results/dropped 时按无结果降级，不抛 TypeError。"""

    def test_match_jds_int_results_degrades_to_uncertain(self):
        from webui import ai
        jobs = [{"job_id": "j0", "title": "后端", "jd": "负责服务开发"}]
        with patch("webui.ai.call_ai", return_value={"results": 40}):
            result = ai.match_jds(jobs, "候选人画像", "https://x", "key")
        self.assertEqual(result["verdicts"]["j0"]["verdict"], "uncertain")

    def test_match_jds_non_list_results_variants_degrade(self):
        from webui import ai
        jobs = [{"job_id": "j0", "title": "后端", "jd": "负责服务开发"}]
        for bad in (None, "ok", True, {"i": 0}):
            with self.subTest(bad=bad), patch(
                "webui.ai.call_ai", return_value={"results": bad},
            ):
                result = ai.match_jds(jobs, "候选人画像", "https://x", "key")
            self.assertEqual(result["verdicts"]["j0"]["verdict"], "uncertain")

    def test_screen_jobs_int_dropped_keeps_whole_batch(self):
        from webui import ai
        jobs = [{"job_id": f"j{i}", "title": "T", "salary": "10K",
                 "location": "上海"} for i in range(3)]
        with patch("webui.ai.call_ai", return_value={"dropped": 40}):
            result = ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x", "key",
            )
        self.assertEqual(result["kept"], ["j0", "j1", "j2"])
        self.assertEqual(result["dropped"], [])
        self.assertTrue(
            all(v["verdict"] == "kept" for v in result["verdicts"].values())
        )


if __name__ == "__main__":
    unittest.main()
