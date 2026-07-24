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
    """Build a mock requests.Response whose body is a chat completions JSON."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }
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
        self.assertEqual(mock_post.call_count, 4)

    @patch("webui.ai.requests.post")
    def test_malformed_response_body_raises_invalid_response(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"unexpected": "shape"}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "invalid_response")

    @patch("webui.ai.requests.post")
    def test_non_json_content_raises_invalid_response(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "not json at all"}}]
        }
        mock_post.return_value = response

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
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(CandidateV3ProviderAdapterTests()._complete())}}]}
        mock_post.return_value = response

        result = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )
        self.assertEqual(result, {"ok": True, "transport": "ready", "generation": "ready", "candidate_contract": "complete", "warning_codes": []})

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

    @patch("webui.ai.call_ai")
    def test_partial_candidate_capability_is_supported(self, call):
        from webui.ai import test_connection
        value = CandidateV3ProviderAdapterTests()._complete(); value["summary"]["strengths"] = ["Python", 1]
        call.return_value = value
        result = test_connection("https://api.example.com/v1", "secret", "model")
        self.assertTrue(result["ok"]); self.assertEqual(result["candidate_contract"], "partial")
        self.assertIn("invalid_type", result["warning_codes"])

    @patch("webui.ai.call_ai", return_value="not-json")
    def test_invalid_json_reports_generation_failure(self, call):
        from webui.ai import test_connection
        result = test_connection("https://api.example.com/v1", "secret", "model")
        self.assertFalse(result["ok"]); self.assertEqual(result["transport"], "ready"); self.assertEqual(result["generation"], "failed")


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


# ---------------------------------------------------------------------------
# T097: DiscoveryAIProvider — 真实 provider 公共边界与错误映射
# ---------------------------------------------------------------------------


class DiscoveryAIProviderTests(unittest.TestCase):
    """T097: 真实 DiscoveryAIProvider 公共边界。

    Provider 只持 endpoint/model/api_key，不读写 TaskStore。
    认证/超时/网络/无效输出映射到 feature-safe 错误码（ai_* 前缀）。
    结构性失败触发单次纠正重试；第二次仍失败抛 ai_invalid_output。
    """

    def setUp(self):
        from webui.ai import DiscoveryAIProvider, AISecurityError
        self.ProviderClass = DiscoveryAIProvider
        self.AISecurityError = AISecurityError

    # -- 构造与隔离 --

    def test_provider_can_be_constructed_with_endpoint_model_api_key(self):
        provider = self.ProviderClass(
            endpoint="https://api.example.com/v1",
            model="deepseek-v4-flash-free",
            api_key="sk-test-key",
        )
        self.assertIsNotNone(provider)

    def test_provider_does_not_require_store_parameter(self):
        import inspect
        sig = inspect.signature(self.ProviderClass.__init__)
        param_names = set(sig.parameters.keys()) - {"self"}
        self.assertNotIn("store", param_names)
        self.assertNotIn("task_store", param_names)

    def test_provider_instance_does_not_hold_store_attribute(self):
        provider = self.ProviderClass("e", "m", "k")
        self.assertFalse(hasattr(provider, "store"))
        self.assertFalse(hasattr(provider, "_store"))

    # -- 公共方法 --

    def test_provider_exposes_analyze_callable(self):
        provider = self.ProviderClass("e", "m", "k")
        self.assertTrue(callable(getattr(provider, "analyze", None)))

    def test_provider_exposes_assess_job_callable(self):
        provider = self.ProviderClass("e", "m", "k")
        self.assertTrue(callable(getattr(provider, "assess_job", None)))

    def test_assessment_prompt_declares_allowed_band_and_reference_rules(self):
        messages = self.ProviderClass._build_assess_messages(
            {"headline": "数据工程师"},
            {"id": "d1", "evidence_refs": ["e1"]},
            [{"id": "e1"}],
            {"fields": {"title": "数据工程师", "jd": "负责数据开发"}},
        )
        system_prompt = messages[0]["content"]
        self.assertIn(
            "proposed_band 只能是 high/adjacent/growth/unsuitable/uncertain",
            system_prompt,
        )
        self.assertIn("证据引用必须使用输入中已有的 ID", system_prompt)

    def test_assessment_prompt_handles_experience_level_conflict(self):
        messages = self.ProviderClass._build_assess_messages(
            {"headline": "数据工程师", "experience_level": "5年全职经验"},
            {"id": "d1", "evidence_refs": ["e1"]},
            [{"id": "e1"}],
            {"fields": {"title": "数据开发实习生", "jd": "面向在校生"}},
        )
        system_prompt = messages[0]["content"]
        self.assertIn("实习/校招/应届", system_prompt)
        self.assertIn("多年全职经历", system_prompt)
        self.assertIn("不得给出 high 或 adjacent", system_prompt)

    # -- 错误码映射（feature-safe：ai_* 前缀） --

    def test_auth_failure_maps_to_ai_auth_failed(self):
        provider = self.ProviderClass("e", "m", "k")
        with patch("webui.ai.call_ai", side_effect=self.AISecurityError("auth_failed")):
            with self.assertRaises(self.AISecurityError) as ctx:
                provider.analyze(resume_text="resume text here")
            self.assertEqual(ctx.exception.error_code, "ai_auth_failed")

    def test_timeout_maps_to_ai_timeout(self):
        provider = self.ProviderClass("e", "m", "k")
        with patch("webui.ai.call_ai", side_effect=self.AISecurityError("timeout")):
            with self.assertRaises(self.AISecurityError) as ctx:
                provider.analyze(resume_text="resume text here")
            self.assertEqual(ctx.exception.error_code, "ai_timeout")

    def test_network_error_maps_to_ai_network_error(self):
        provider = self.ProviderClass("e", "m", "k")
        with patch("webui.ai.call_ai", side_effect=self.AISecurityError("network_error")):
            with self.assertRaises(self.AISecurityError) as ctx:
                provider.analyze(resume_text="resume text here")
            self.assertEqual(ctx.exception.error_code, "ai_network_error")

    def test_invalid_response_maps_to_ai_invalid_output(self):
        provider = self.ProviderClass("e", "m", "k")
        with patch("webui.ai.call_ai", side_effect=self.AISecurityError("invalid_response")):
            with self.assertRaises(self.AISecurityError) as ctx:
                provider.analyze(resume_text="resume text here")
            self.assertEqual(ctx.exception.error_code, "ai_invalid_output")

    # -- 单次纠正重试（call_ai 成功返回但结构无效） --

    def test_unrecognizable_response_is_terminal_without_retry(self):
        provider = self.ProviderClass("e", "m", "k")
        with patch("webui.ai.call_ai", return_value={}) as call:
            with self.assertRaises(self.AISecurityError) as ctx:
                provider.analyze(resume_text=self._resume_text())
        self.assertEqual(ctx.exception.error_code, "ai_invalid_output")
        self.assertEqual(call.call_count, 1)

    def test_second_invalid_response_raises_ai_invalid_output(self):
        provider = self.ProviderClass("e", "m", "k")
        invalid_response = {}
        with patch("webui.ai.call_ai", return_value=invalid_response):
            with self.assertRaises(self.AISecurityError) as ctx:
                provider.analyze(resume_text=self._resume_text())
            self.assertEqual(ctx.exception.error_code, "ai_invalid_output")

    def test_valid_response_no_retry(self):
        provider = self.ProviderClass("e", "m", "k")
        valid_response = self._valid_v2_response()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            return valid_response

        with patch("webui.ai.call_ai", side_effect=side_effect):
            provider.analyze(resume_text=self._resume_text())
        self.assertEqual(call_count[0], 1)

    # -- 最小不泄漏 --

    def test_analyze_does_not_leak_api_key_in_exception(self):
        provider = self.ProviderClass("e", "m", "sk-SECRET-KEY-42")
        with patch("webui.ai.call_ai", side_effect=self.AISecurityError("timeout")):
            with self.assertRaises(self.AISecurityError) as ctx:
                provider.analyze(resume_text="resume")
        self.assertNotIn("sk-SECRET-KEY-42", str(ctx.exception))
        self.assertNotIn("sk-SECRET-KEY-42", repr(ctx.exception))

    # -- T105: v2 exact-quote locator enrichment --

    def test_analyze_generates_program_locator_for_valid_quote(self):
        """analyze 返回的 evidence 必须包含程序生成的 source_locator。"""
        provider = self.ProviderClass("e", "m", "k")
        with patch("webui.ai.call_ai", return_value=self._valid_v2_response()):
            result = provider.analyze(resume_text=self._resume_text())
        evidence = result["evidence"][0]
        self.assertIn("source_locator", evidence)
        self.assertIsInstance(evidence["source_locator"], dict)
        self.assertIn("start", evidence["source_locator"])
        self.assertIn("end", evidence["source_locator"])

    def test_analyze_ignores_model_provided_locator(self):
        """模型如果返回 source_locator，analyze 必须忽略它并生成自己的。"""
        provider = self.ProviderClass("e", "m", "k")
        response = self._valid_v2_response()
        # 模型故意返回错误的 locator
        response["evidence"][0]["source_locator"] = {"start": 999, "end": 9999}
        with patch("webui.ai.call_ai", return_value=response):
            result = provider.analyze(resume_text=self._resume_text())
        locator = result["evidence"][0]["source_locator"]
        # 程序生成的 locator 必须与简历文本一致，不是模型的 999/9999
        self.assertNotEqual(locator["start"], 999)
        self.assertNotEqual(locator["end"], 9999)

    def test_analyze_locator_slice_matches_quote(self):
        """程序生成的 locator 切片必须 == source_quote。"""
        provider = self.ProviderClass("e", "m", "k")
        resume = self._resume_text()
        with patch("webui.ai.call_ai", return_value=self._valid_v2_response()):
            result = provider.analyze(resume_text=resume)
        from webui.candidate import canonicalize_resume_text_v2
        canonical = canonicalize_resume_text_v2(resume)
        for ev in result["evidence"]:
            quote = ev["source_quote"]
            loc = ev["source_locator"]
            self.assertEqual(canonical[loc["start"]:loc["end"]], quote)

    def test_analyze_generates_safe_excerpt(self):
        """analyze 必须为每条 evidence 生成程序 safe_excerpt。"""
        provider = self.ProviderClass("e", "m", "k")
        with patch("webui.ai.call_ai", return_value=self._valid_v2_response()):
            result = provider.analyze(resume_text=self._resume_text())
        for ev in result["evidence"]:
            self.assertIn("safe_excerpt", ev)
            self.assertIsInstance(ev["safe_excerpt"], str)
            self.assertTrue(ev["safe_excerpt"])  # 非空

    def test_analyze_quarantines_quote_not_found(self):
        """source_quote 不在简历中时丢弃该证据并返回降级结果。"""
        provider = self.ProviderClass("e", "m", "k")
        response = self._valid_v2_response()
        response["evidence"][0]["source_quote"] = "不存在的经历描述xyz"
        with patch("webui.ai.call_ai", return_value=response) as call:
            result = provider.analyze(resume_text=self._resume_text())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["evidence"], [])
        self.assertIn({"code": "invalid_evidence", "path": "evidence[0]"}, result["quality"]["warnings"])

    def test_analyze_quarantines_ambiguous_quote(self):
        """source_quote 重复出现时丢弃该证据并返回降级结果。"""
        provider = self.ProviderClass("e", "m", "k")
        response = self._valid_v2_response()
        # "Python" 在简历中出现多次
        response["evidence"][0]["source_quote"] = "Python"
        resume = "Python 后端经验，5年开发，Python 熟悉 Django/Flask"
        with patch("webui.ai.call_ai", return_value=response) as call:
            result = provider.analyze(resume_text=resume)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["evidence"], [])
        self.assertIn({"code": "invalid_evidence", "path": "evidence[0]"}, result["quality"]["warnings"])

    def test_analyze_quarantines_sensitive_quote(self):
        """source_quote 含敏感信息时丢弃该证据并返回降级结果。"""
        provider = self.ProviderClass("e", "m", "k")
        response = self._valid_v2_response()
        response["evidence"][0]["source_quote"] = "13912345678"
        # 简历中包含该号码以便 find 能匹配，但应被敏感检测拒绝
        resume = "Python 后端经验，电话 13912345678，5年开发"
        with patch("webui.ai.call_ai", return_value=response) as call:
            result = provider.analyze(resume_text=resume)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["evidence"], [])
        self.assertIn({"code": "sensitive_value", "path": "evidence[0]"}, result["quality"]["warnings"])

    def test_analyze_preserves_valid_evidence_when_one_quote_is_invalid(self):
        """一个 quote 无法解析时保留其他有效证据并返回 partial。"""
        provider = self.ProviderClass("e", "m", "k")
        response = self._valid_v2_response()
        response["evidence"].append({
            "client_ref": "e2",
            "type": "skill",
            "normalized_value": "Go",
            "source_quote": "不存在的Go经历",
            "assertion_type": "explicit",
            "confidence": 80,
        })
        response["directions"][0]["evidence_refs"] = ["e1", "e2"]
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            return response
        with patch("webui.ai.call_ai", side_effect=side_effect):
            result = provider.analyze(resume_text=self._resume_text())
        self.assertEqual(call_count[0], 2)
        self.assertEqual([item["client_ref"] for item in result["evidence"]], ["e1"])
        self.assertEqual(result["quality"]["status"], "partial")

    def test_analyze_v2_retry_on_locator_failure_then_success(self):
        """locator 失败触发一次重试，重试返回可解析 quote → 成功。"""
        provider = self.ProviderClass("e", "m", "k")
        bad_response = self._valid_v2_response()
        bad_response["evidence"][0]["source_quote"] = "不存在的经历"
        good_response = self._valid_v2_response()
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            return bad_response if call_count[0] == 1 else good_response
        with patch("webui.ai.call_ai", side_effect=side_effect):
            result = provider.analyze(resume_text=self._resume_text())
        self.assertEqual(call_count[0], 2)
        self.assertIn("source_locator", result["evidence"][0])

    # -- 辅助夹具 --

    @staticmethod
    def _resume_text() -> str:
        return "Python 后端经验，5年开发，熟悉 Django/Flask"

    @staticmethod
    def _valid_v2_response() -> dict:
        """最小结构合法的候选人分析响应。"""
        return {
            "contract_version": "v3",
            "summary": {
                "headline": "后端开发工程师",
                "experience_level": "中级",
                "domains": ["后端"],
                "strengths": ["Python"],
            },
            "evidence": [
                {
                    "client_ref": "e1",
                    "type": "skill",
                    "normalized_value": "Python",
                    "source_quote": "Python 后端经验",
                    "assertion_type": "explicit",
                    "confidence": 90,
                },
            ],
            "unknowns": [],
            "directions": [
                {
                    "client_ref": "d1",
                    "name": "后端开发工程师",
                    "type": "core",
                    "rationale": "5年后端经验",
                    "evidence_refs": ["e1"],
                    "gaps": [],
                    "confidence": 90,
                    "default_enabled": True,
                    "search_terms": ["Python 后端"],
                },
            ],
        }


# ---------------------------------------------------------------------------
# Task 3: candidate-analysis v3 provider adapter contract
# ---------------------------------------------------------------------------
class CandidateV3ProviderAdapterTests(unittest.TestCase):
    """Focused contract/cleanup/retry assertions for the approved v3 flow."""

    def setUp(self):
        import webui.ai as ai
        import webui.candidate as candidate
        self.ai, self.candidate = ai, candidate
        self.provider = ai.DiscoveryAIProvider("e", "m", "k")

    def _contract(self):
        return getattr(self.candidate, "CANDIDATE_ANALYSIS_V3_CONTRACT")

    def _empty(self):
        return self.candidate.build_empty_candidate_analysis()

    def _cleanup(self, value):
        fn = getattr(self.ai, "cleanup_candidate_analysis_response", None) or getattr(self.ai, "_cleanup_candidate_response")
        return fn(value)

    def _schema(self):
        text = self.provider._build_analyze_messages("resume")[0]["content"]
        begin = "CANONICAL_CANDIDATE_V3_SCHEMA_BEGIN"
        end = "CANONICAL_CANDIDATE_V3_SCHEMA_END"
        self.assertIn(begin, text)
        self.assertIn(end, text)
        encoded = text.split(begin, 1)[1].split(end, 1)[0].strip()
        return json.loads(encoded)

    def _provider_projection(self):
        contract = self._contract()
        top = {key: value for key, value in contract["top"].items() if key != "quality"}
        evidence = {
            key: value for key, value in contract["evidence"].items()
            if key not in {"source_locator", "safe_excerpt"}
        }
        return {
            "version": contract["version"],
            "top": top,
            "summary": contract["summary"],
            "evidence": evidence,
            "unknown": contract["unknown"],
            "direction": contract["direction"],
        }

    def _keys(self, value):
        out = set()
        if isinstance(value, dict):
            for k, v in value.items():
                out.add(k); out |= self._keys(v)
        elif isinstance(value, list):
            for v in value: out |= self._keys(v)
        return out

    @staticmethod
    def _resume():
        return "候选人具备 Python 后端经验，负责订单系统。"

    def _complete(self):
        return {
            "contract_version": "v3",
            "summary": {"headline": "后端工程师", "experience_level": "senior", "domains": ["互联网"], "strengths": ["Python"]},
            "evidence": [{"client_ref": "e1", "type": "skill", "normalized_value": "Python", "source_quote": "Python 后端经验", "assertion_type": "explicit", "confidence": 90}],
            "unknowns": [],
            "directions": [{"client_ref": "d1", "name": "后端开发工程师", "type": "core", "rationale": "5年后端经验", "evidence_refs": ["e1"], "gaps": [], "confidence": 90, "default_enabled": True, "search_terms": ["Python 后端"]}],
        }

    def _partial(self):
        value = self._complete(); value["summary"]["strengths"] = ["Python", 1]; return value

    def _manual(self):
        value = self._complete(); value["directions"][0]["search_terms"] = []; return value

    def test_v3_schema_covers_provider_fields_once(self):
        self.assertEqual(self._schema(), json.loads(json.dumps(self._provider_projection())))

    def test_v3_typed_empty_is_exact(self):
        empty = self._empty()
        self.assertIsInstance(empty, dict)
        self.assertEqual(empty, self.candidate.build_empty_candidate_analysis())

    def test_identity_fields_are_not_provider_output_fields(self):
        keys = self._keys(self._schema())
        for field in ("full_name", "phone", "email", "gender", "age", "id_number", "exact_address"):
            self.assertNotIn(field, keys)
        self.assertIn("name", self._keys(self._schema().get("direction", {})))

    def test_locator_excerpt_quality_are_program_owned(self):
        keys = self._keys(self._schema())
        for field in ("source_locator", "safe_excerpt", "quality", "offset"):
            self.assertNotIn(field, keys)

    def test_response_format_is_json_object(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(self._complete())}}]
        }
        with patch("webui.ai.requests.post", return_value=response) as post:
            self.ai.call_ai("https://example.test", "key", [{"role": "user", "content": "x"}])
        self.assertEqual(post.call_args.kwargs["json"]["response_format"], {"type": "json_object"})

    def test_cleanup_accepts_plain_json(self):
        value = self._empty(); self.assertEqual(self._cleanup(json.dumps(value)), value)

    def test_cleanup_accepts_json_and_unlabelled_fences(self):
        value = self._empty()
        for wrapped in (f"```json\n{json.dumps(value)}\n```", f"```\n{json.dumps(value)}\n```"):
            self.assertEqual(self._cleanup(wrapped), value)

    def test_cleanup_accepts_single_data_or_result_envelope(self):
        value = self._empty()
        self.assertEqual(self._cleanup(json.dumps({"data": value})), value)
        self.assertEqual(self._cleanup(json.dumps({"result": value})), value)

    def test_cleanup_rejects_unknown_nested_multiple_and_trailing(self):
        value = self._empty()
        for raw in (json.dumps({"foo": value}), json.dumps({"data":{"result":value}}), json.dumps({"data":value,"result":value}), json.dumps(value)+" trailing", "```{", f"```python\n{json.dumps(value)}\n```", f"```json\n{json.dumps(value)}\n```trailing"):
            with self.assertRaises(ValueError): self._cleanup(raw)

    def test_cleanup_does_not_fill_semantic_values(self):
        value = self._empty(); out = self._cleanup(json.dumps(value)); self.assertEqual(out, value)

    def test_complete_v3_result_does_not_retry(self):
        with patch("webui.ai.call_ai", return_value=self._complete()) as call:
            self.provider.analyze(resume_text=self._resume())
        self.assertEqual(call.call_count, 1)

    def test_partial_result_triggers_one_correction_with_safe_diagnostics(self):
        partial = self._partial(); good = self._complete()
        with patch("webui.ai.call_ai", side_effect=[partial, good]) as call:
            self.provider.analyze(resume_text=self._resume())
        self.assertEqual(call.call_count, 2)
        correction_messages = call.call_args_list[1].args[2]
        self.assertEqual(correction_messages[-2]["role"], "assistant")
        self.assertEqual(correction_messages[-1]["role"], "user")
        correction = correction_messages[-1]["content"]
        self.assertNotIn(self._resume(), correction)
        self.assertNotIn("Python 后端经验", correction)
        self.assertNotIn("互联网", correction)
        diagnostics = json.loads(correction.split("：", 1)[1])
        self.assertTrue(diagnostics)
        self.assertTrue(all(set(item) == {"code", "path"} for item in diagnostics))

    def test_manual_required_triggers_one_correction(self):
        with patch("webui.ai.call_ai", side_effect=[self._manual(), self._complete()]) as call:
            self.provider.analyze(resume_text=self._resume())
        self.assertEqual(call.call_count, 2)

    def test_corrected_higher_quality_result_is_used(self):
        first = self._partial(); second = self._complete(); second["summary"]["headline"] = "平台后端工程师"
        with patch("webui.ai.call_ai", side_effect=[first, second]):
            result = self.provider.analyze(resume_text=self._resume())
        self.assertEqual(result["summary"]["headline"], second["summary"]["headline"])

    def test_worse_correction_cannot_discard_useful_original(self):
        first = self._partial(); second = self._manual()
        with patch("webui.ai.call_ai", side_effect=[first, second]):
            result = self.provider.analyze(resume_text=self._resume())
        expected = self.candidate.normalize_candidate_analysis(first, self._resume())
        self.assertEqual(result, expected)

    def test_second_partial_returns_best_result_after_two_calls(self):
        first = self._partial(); second = self._manual()
        with patch("webui.ai.call_ai", side_effect=[first, second]) as call:
            result = self.provider.analyze(resume_text=self._resume())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result, self.candidate.normalize_candidate_analysis(first, self._resume()))

    def test_sensitive_value_never_enters_correction_diagnostics(self):
        partial = self._complete(); partial["evidence"][0]["normalized_value"] = "13912345678"
        with patch("webui.ai.call_ai", side_effect=[partial, self._complete()]) as call:
            self.provider.analyze(resume_text=self._resume())
        correction = call.call_args_list[1].args[2][-1]["content"]
        self.assertNotIn("13912345678", correction)
        diagnostics = json.loads(correction.split("：", 1)[1])
        self.assertTrue(all(set(item) == {"code", "path"} for item in diagnostics))

    def test_invalid_json_is_terminal_without_retry(self):
        with patch("webui.ai.call_ai", return_value="not-json") as call:
            with self.assertRaises(self.ai.AISecurityError): self.provider.analyze(resume_text=self._resume())
        self.assertEqual(call.call_count, 1)

    def test_typed_transport_failures_do_not_retry(self):
        for code in ("timeout", "auth_failed", "network_error"):
            with self.subTest(code=code), patch("webui.ai.call_ai", side_effect=self.ai.AISecurityError(code)) as call:
                with self.assertRaises(self.ai.AISecurityError): self.provider.analyze(resume_text=self._resume())
                self.assertEqual(call.call_count, 1)

    def test_correction_is_normalized_through_cleanup_path(self):
        with patch("webui.ai.call_ai", side_effect=[self._partial(), f"```json\n{json.dumps(self._complete())}\n```"]) as call:
            self.provider.analyze(resume_text=self._resume())
        self.assertEqual(call.call_count, 2)


class DiscoveryAIVersionRoutingV2Tests(unittest.TestCase):
    """T015 RED contracts for job-assessment v2 and unknown-version rejection."""

    def setUp(self):
        from webui.ai import DiscoveryAIProvider
        self.provider = DiscoveryAIProvider("https://ai.example/v1", "model", "secret-key")

    def test_job_assessment_v2_accepts_one_job_and_at_most_two_direction_refs(self):
        response = {
            "contract_version": "job_assessment_v2",
            "assessments": [],
            "raw_model_output": "DO-NOT-RETURN",
        }
        directions = [
            {"id": "d1", "fact_refs": ["f1"], "evidence_refs": ["e1"]},
            {"id": "d2", "fact_refs": ["f2"], "evidence_refs": ["e2"]},
            {"id": "d3", "fact_refs": ["f3"], "evidence_refs": ["e3"]},
        ]
        with patch("webui.ai.call_ai", return_value=response):
            with self.assertRaises(ValueError):
                self.provider.assess_job(
                    candidate_profile={"facts": [], "evidence": []},
                    directions=directions,
                    job_snapshot={"id": "snap-1", "fields": {"title": "后端"}},
                    contract_version="job_assessment_v2",
                )

    def test_unknown_discovery_ai_contract_version_is_not_silently_downgraded(self):
        with self.assertRaises(ValueError):
            self.provider.analyze(
                resume_text="Python 后端",
                contract_version="v999",
            )


class JobAssessmentV2ProviderTests(unittest.TestCase):
    """T055 RED: job-assessment v2 一岗位/最多两方向、四维度、整数分数、
    双侧证据、partial quarantine 与一次定向纠正。

    契约来源: contracts/ai-contracts.md#job-assessment-v2 与 research.md R7。
    RED 状态: DiscoveryAIProvider.assess_job 对 job_assessment_v2 抛
    NotImplementedError（T056 实现）。

    返回合同（T056 实现）::

        {
          "contract_version": "job_assessment_v2",
          "assessments": [ {direction_id, dimensions{4}, match_score,
                            confidence, positive[], gaps[], proposed_band} ],
          "quarantined": [ {"direction_id", "reason"} ],
          "quality": {"status": "complete"|"partial"|"manual_required",
                      "warnings": [...]},
          "metrics": {"provider_call_count": int},
        }
    """

    REQUIRED_DIMS = (
        "direction_alignment", "skill_coverage",
        "experience_match", "industry_relevance",
    )

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        path = Path(__file__).parent / "fixtures" / "discovery" / "ai_job_assessment_v2.json"
        cls.fixture = json.loads(path.read_text(encoding="utf-8"))

    def setUp(self):
        from webui.ai import DiscoveryAIProvider
        self.provider = DiscoveryAIProvider("https://ai.example/v1", "model", "secret-key")
        inp = self.fixture["input"]
        self.candidate_profile = inp["candidate"]
        self.directions = inp["directions"]
        self.job_snapshot = inp["job"]

    def _assess(self, **overrides):
        kwargs = dict(
            candidate_profile=self.candidate_profile,
            directions=self.directions,
            job_snapshot=self.job_snapshot,
            contract_version="job_assessment_v2",
        )
        kwargs.update(overrides)
        return self.provider.assess_job(**kwargs)

    def _output(self, name):
        return copy.deepcopy(self.fixture["outputs"][name])

    # --- 最多两方向 ----------------------------------------------------

    def test_more_than_two_directions_raises(self):
        three = self.directions + [{"id": "dir-3", "fact_refs": [], "evidence_refs": []}]
        with self.assertRaises(ValueError):
            self._assess(directions=three)

    def test_zero_directions_raises(self):
        with self.assertRaises(ValueError):
            self._assess(directions=[])

    # --- 一岗位两方向：单次调用、四维度、整数分数 ----------------------

    def test_valid_two_directions_single_call_complete(self):
        with patch("webui.ai.call_ai", return_value=self._output("valid_two_directions")) as call:
            result = self._assess()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["contract_version"], "job_assessment_v2")
        ids = {a["direction_id"] for a in result["assessments"]}
        self.assertEqual(ids, {"dir-1", "dir-2"})
        self.assertEqual(result["quarantined"], [])
        self.assertEqual(result["quality"]["status"], "complete")
        self.assertEqual(result["metrics"]["provider_call_count"], 1)

    def test_all_four_dimensions_present_with_integer_scores(self):
        with patch("webui.ai.call_ai", return_value=self._output("valid_two_directions")):
            result = self._assess()
        for assessment in result["assessments"]:
            dims = assessment["dimensions"]
            for name in self.REQUIRED_DIMS:
                self.assertIn(name, dims)
                score = dims[name]["score"]
                self.assertIsInstance(score, int)
                self.assertNotIsInstance(score, bool)
                self.assertGreaterEqual(score, 0)
                self.assertLessEqual(score, 100)
            self.assertIsInstance(assessment["match_score"], int)
            self.assertIsInstance(assessment["confidence"], int)

    # --- 双侧证据 ------------------------------------------------------

    def test_positive_requires_bilateral_evidence(self):
        broken = self._output("valid_two_directions")
        # dir-1 的 positive 去掉岗位侧证据 -> 该 positive 不应保留
        broken["assessments"][0]["positive"][0]["job_evidence_refs"] = []
        with patch("webui.ai.call_ai", return_value=broken):
            result = self._assess()
        for assessment in result["assessments"]:
            for item in assessment.get("positive", []):
                self.assertTrue(item.get("candidate_evidence_refs"),
                                "positive 缺少候选侧证据")
                self.assertTrue(item.get("job_evidence_refs"),
                                "positive 缺少岗位侧证据")

    # --- partial quarantine -------------------------------------------

    def test_non_integer_score_quarantines_direction_only(self):
        with patch("webui.ai.call_ai", return_value=self._output("partial_invalid")):
            result = self._assess()
        valid_ids = {a["direction_id"] for a in result["assessments"]}
        quarantined_ids = {q["direction_id"] for q in result["quarantined"]}
        self.assertEqual(valid_ids, {"dir-1"})
        self.assertEqual(quarantined_ids, {"dir-2"})
        self.assertEqual(result["quality"]["status"], "partial")

    def test_cross_direction_reference_quarantined(self):
        with patch("webui.ai.call_ai", return_value=self._output("cross_direction_reference")):
            result = self._assess()
        valid_ids = {a["direction_id"] for a in result["assessments"]}
        quarantined_ids = {q["direction_id"] for q in result["quarantined"]}
        self.assertIn("dir-1", valid_ids)
        self.assertIn("dir-2", quarantined_ids)
        self.assertEqual(result["quality"]["status"], "partial")

    # --- 一次定向纠正 --------------------------------------------------

    def test_single_targeted_correction_recovers_invalid_direction(self):
        responses = [self._output("partial_invalid"), self._output("correction_response")]
        with patch("webui.ai.call_ai", side_effect=responses) as call:
            result = self._assess()
        self.assertEqual(call.call_count, 2)
        valid_ids = {a["direction_id"] for a in result["assessments"]}
        self.assertEqual(valid_ids, {"dir-1", "dir-2"})
        self.assertEqual(result["quarantined"], [])
        self.assertEqual(result["quality"]["status"], "complete")
        self.assertEqual(result["metrics"]["provider_call_count"], 2)

    def test_correction_request_targets_only_invalid_direction_and_no_secret(self):
        responses = [self._output("partial_invalid"), self._output("correction_response")]
        with patch("webui.ai.call_ai", side_effect=responses) as call:
            self._assess()
        correction_messages = call.call_args_list[1].args[2]
        correction_text = json.dumps(correction_messages, ensure_ascii=False)
        self.assertIn("dir-2", correction_text)
        self.assertNotIn("secret-key", correction_text)

    def test_uncorrected_invalid_direction_stays_quarantined_after_one_retry(self):
        responses = [self._output("partial_invalid"), self._output("partial_invalid")]
        with patch("webui.ai.call_ai", side_effect=responses) as call:
            result = self._assess()
        self.assertEqual(call.call_count, 2)
        valid_ids = {a["direction_id"] for a in result["assessments"]}
        quarantined_ids = {q["direction_id"] for q in result["quarantined"]}
        self.assertEqual(valid_ids, {"dir-1"})
        self.assertEqual(quarantined_ids, {"dir-2"})
        self.assertEqual(result["quality"]["status"], "partial")

    # --- 原始字段不存活 ------------------------------------------------

    def test_raw_provider_fields_never_survive_v2_return(self):
        response = self._output("valid_two_directions")
        response["raw_model_output"] = "RAW-V2-SECRET"
        with patch("webui.ai.call_ai", return_value=response):
            result = self._assess()
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("raw_model_output", rendered)
        self.assertNotIn("RAW-V2-SECRET", rendered)


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
        response = MagicMock()
        response.status_code = 200
        choice = {"message": {"content": json.dumps(payload)}}
        if finish_reason is not None:
            choice["finish_reason"] = finish_reason
        response.json.return_value = {"choices": [choice]}
        return response

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
        self.assertEqual(mock_post.call_count, 4)

    @patch("webui.ai.requests.post")
    def test_truncated_finish_reason_length(self, mock_post):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"choices": [{
            "message": {"content": '{"results": [{"i":0,'},
            "finish_reason": "length",
        }]}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.error_code, "truncated")

    @patch("webui.ai.requests.post")
    def test_truncated_unbalanced_brackets_without_finish_reason(self, mock_post):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 200
        # 无 finish_reason 字段（有的端点不返回），括号不闭合算截断
        response.json.return_value = {"choices": [{
            "message": {"content": '{"results": [{"i": 0}'},
        }]}
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "key",
                    [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.error_code, "truncated")

    @patch("webui.ai.requests.post")
    def test_plain_garbage_stays_invalid_response(self, mock_post):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"choices": [{
            "message": {"content": "不好意思，我无法回答"},
            "finish_reason": "stop",
        }]}
        mock_post.return_value = response

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
