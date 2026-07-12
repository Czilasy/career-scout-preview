"""Tests for webui.ai: JSON validation, connection testing, error sanitization, batching.

Covers T014: AI JSON parsing success/failure, timeout handling, rejection of
unknown job_ids, error sanitization (no API key leak) and rank_jds batch limits.
Uses shared fixtures from tests/test_workbench_fixtures.py.
"""

from __future__ import annotations

import json
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

    @patch("webui.ai.requests.post")
    def test_timeout_raises_safe_error(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        mock_post.side_effect = requests.Timeout("connection timed out")

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "timeout")

    @patch("webui.ai.requests.post")
    def test_network_error_raises_safe_error(self, mock_post):
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

    @patch("webui.ai.requests.post")
    def test_server_error_raises_safe_error(self, mock_post):
        from webui.ai import call_ai, AISecurityError

        response = MagicMock()
        response.status_code = 500
        mock_post.return_value = response

        with self.assertRaises(AISecurityError) as ctx:
            call_ai("https://api.example.com/v1/chat/completions", "secret-key",
                    [{"role": "user", "content": "hi"}])

        self.assertEqual(ctx.exception.error_code, "invalid_response")

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

        response = MagicMock()
        response.status_code = 200
        mock_post.return_value = response

        success, error_code = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertTrue(success)
        self.assertIsNone(error_code)

    @patch("webui.ai.requests.post")
    def test_timeout_returns_safe_code(self, mock_post):
        from webui.ai import test_connection

        mock_post.side_effect = requests.Timeout("timed out")

        success, error_code = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(success)
        self.assertEqual(error_code, "timeout")

    @patch("webui.ai.requests.post")
    def test_auth_failure_returns_safe_code(self, mock_post):
        from webui.ai import test_connection

        response = MagicMock()
        response.status_code = 403
        mock_post.return_value = response

        success, error_code = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(success)
        self.assertEqual(error_code, "auth_failed")

    @patch("webui.ai.requests.post")
    def test_network_error_returns_safe_code(self, mock_post):
        from webui.ai import test_connection

        mock_post.side_effect = requests.ConnectionError("DNS failed")

        success, error_code = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(success)
        self.assertEqual(error_code, "network_error")

    @patch("webui.ai.requests.post")
    def test_server_error_returns_safe_code(self, mock_post):
        from webui.ai import test_connection

        response = MagicMock()
        response.status_code = 500
        mock_post.return_value = response

        success, error_code = test_connection(
            "https://api.example.com/v1/chat/completions", "secret-key"
        )

        self.assertFalse(success)
        self.assertEqual(error_code, "invalid_response")

    @patch("webui.ai.requests.post")
    def test_connection_error_does_not_leak_api_key(self, mock_post):
        from webui.ai import test_connection

        api_key = "sk-super-secret-key-12345"
        mock_post.side_effect = requests.ConnectionError(f"failed with {api_key}")

        success, error_code = test_connection(
            "https://api.example.com/v1/chat/completions", api_key
        )

        self.assertFalse(success)
        self.assertNotIn(api_key, error_code or "")


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

class ScreeningSuggestTests(unittest.TestCase):
    """T012: AI suggest screening filters — JSON parse, validate, timeout, safe errors."""

    def _valid_payload(self):
        return {
            "city": "上海", "salary": "405", "experience": "105",
            "degree": "203", "scale": "", "stage": "", "industry": "",
        }

    def test_valid_suggest_returns_seven_fields(self):
        from webui.ai import suggest_screening_filters

        with patch("webui.ai.call_ai", return_value=self._valid_payload()):
            result = suggest_screening_filters("resume", "http://ep", "key")
        expected = {"city", "salary", "experience", "degree", "scale", "stage", "industry"}
        self.assertEqual(set(result.keys()), expected)
        self.assertEqual(result["city"], "上海")
        self.assertEqual(result["salary"], "405")

    def test_invalid_salary_code_becomes_empty(self):
        from webui.ai import suggest_screening_filters

        payload = self._valid_payload()
        payload["salary"] = "999"  # 非法代码
        with patch("webui.ai.call_ai", return_value=payload):
            result = suggest_screening_filters("resume", "http://ep", "key")
        self.assertEqual(result["salary"], "")

    def test_invalid_city_becomes_empty(self):
        from webui.ai import suggest_screening_filters

        payload = self._valid_payload()
        payload["city"] = "不存在的城市"
        with patch("webui.ai.call_ai", return_value=payload):
            result = suggest_screening_filters("resume", "http://ep", "key")
        self.assertEqual(result["city"], "")

    def test_empty_strings_preserved(self):
        from webui.ai import suggest_screening_filters

        with patch("webui.ai.call_ai", return_value=self._valid_payload()):
            result = suggest_screening_filters("resume", "http://ep", "key")
        self.assertEqual(result["scale"], "")
        self.assertEqual(result["stage"], "")

    def test_non_string_value_coerced_to_empty(self):
        from webui.ai import suggest_screening_filters

        payload = self._valid_payload()
        payload["salary"] = 405  # int, not str
        with patch("webui.ai.call_ai", return_value=payload):
            result = suggest_screening_filters("resume", "http://ep", "key")
        self.assertEqual(result["salary"], "")

    def test_timeout_raises_ai_security_error(self):
        from webui.ai import suggest_screening_filters, AISecurityError, ERROR_TIMEOUT

        with patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_TIMEOUT)):
            with self.assertRaises(AISecurityError) as ctx:
                suggest_screening_filters("resume", "http://ep", "key")
            self.assertEqual(ctx.exception.error_code, ERROR_TIMEOUT)

    def test_network_error_raises_ai_security_error(self):
        from webui.ai import suggest_screening_filters, AISecurityError, ERROR_NETWORK

        with patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_NETWORK)):
            with self.assertRaises(AISecurityError) as ctx:
                suggest_screening_filters("resume", "http://ep", "key")
            self.assertEqual(ctx.exception.error_code, ERROR_NETWORK)

    def test_invalid_json_raises_ai_security_error(self):
        from webui.ai import suggest_screening_filters, AISecurityError, ERROR_INVALID

        with patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_INVALID)):
            with self.assertRaises(AISecurityError) as ctx:
                suggest_screening_filters("resume", "http://ep", "key")
            self.assertEqual(ctx.exception.error_code, ERROR_INVALID)

    def test_error_excludes_resume_text(self):
        from webui.ai import suggest_screening_filters, AISecurityError, ERROR_TIMEOUT

        sensitive = "我的真实姓名是张三身份证110101199001011234"
        with patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_TIMEOUT)):
            with self.assertRaises(AISecurityError) as ctx:
                suggest_screening_filters(sensitive, "http://ep", "key")
            self.assertNotIn(sensitive, str(ctx.exception))
            self.assertNotIn("张三", str(ctx.exception))

    def test_error_excludes_api_key(self):
        from webui.ai import suggest_screening_filters, AISecurityError, ERROR_TIMEOUT

        api_key = "sk-secret-key-1234567890"
        with patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_TIMEOUT)):
            with self.assertRaises(AISecurityError) as ctx:
                suggest_screening_filters("resume", "http://ep", api_key)
            self.assertNotIn(api_key, str(ctx.exception))

    def test_suggest_does_not_return_resume_text(self):
        from webui.ai import suggest_screening_filters

        sensitive = "我的真实姓名是张三"
        with patch("webui.ai.call_ai", return_value=self._valid_payload()):
            result = suggest_screening_filters(sensitive, "http://ep", "key")
        # 返回值只有 7 个筛选项字段，不含简历正文
        self.assertNotIn(sensitive, str(result))
        self.assertNotIn("张三", str(result))


# ---------------------------------------------------------------------------
# T026: assess_semantic_similarity — AI 语义相似度占位（恒返回过、不调 AI）
# ---------------------------------------------------------------------------

class SemanticSimilarityPlaceholderTests(unittest.TestCase):
    """T026: AI semantic similarity placeholder.

    Contract: input resume_text + jd_text, output dict with verdict
    (match/mismatch). Placeholder always returns verdict="match", does not
    call AI, does not access keyring or make HTTP requests. Framework design
    is deferred; replacing the placeholder must not change the contract.
    """

    def setUp(self):
        from webui.ai import assess_semantic_similarity
        self.assess = assess_semantic_similarity

    def test_placeholder_returns_match_verdict(self):
        result = self.assess("resume text", "jd text")
        self.assertEqual(result.get("verdict"), "match")

    def test_placeholder_always_returns_match_for_any_input(self):
        for resume, jd in [
            ("Python 后端", "Java 前端"),
            ("", ""),
            ("经验丰富", "要求初级"),
        ]:
            result = self.assess(resume, jd)
            self.assertEqual(result["verdict"], "match", f"{resume!r}/{jd!r}")

    def test_placeholder_does_not_call_ai(self):
        with patch("webui.ai.call_ai") as mock_call:
            result = self.assess("resume", "jd")
            mock_call.assert_not_called()
            self.assertEqual(result["verdict"], "match")

    def test_placeholder_does_not_access_keyring(self):
        with patch("webui.ai.keyring") as mock_keyring:
            result = self.assess("resume", "jd")
            mock_keyring.get_password.assert_not_called()
            self.assertEqual(result["verdict"], "match")

    def test_placeholder_does_not_make_http_requests(self):
        with patch("webui.ai.requests") as mock_requests:
            result = self.assess("resume", "jd")
            mock_requests.post.assert_not_called()
            self.assertEqual(result["verdict"], "match")

    def test_placeholder_returns_dict_with_verdict_key(self):
        result = self.assess("resume", "jd")
        self.assertIsInstance(result, dict)
        self.assertIn("verdict", result)

    def test_placeholder_does_not_leak_resume_text(self):
        sensitive = "SECRET_RESUME_42"
        result = self.assess(sensitive, "jd")
        self.assertNotIn(sensitive, str(result))

    def test_placeholder_does_not_leak_jd_text(self):
        sensitive = "SECRET_JD_99"
        result = self.assess("resume", sensitive)
        self.assertNotIn(sensitive, str(result))


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


if __name__ == "__main__":
    unittest.main()
