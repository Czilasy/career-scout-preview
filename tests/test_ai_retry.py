import json
import unittest
from unittest.mock import MagicMock, patch

import requests

from webui.ai_retry import (
    DEFAULT_AI_RETRY_DELAY_SECONDS,
    DEFAULT_AI_RETRY_MAX_ATTEMPTS,
    effective_retry_plan,
)


def _mock_chat_response(payload):
    response = MagicMock()
    response.status_code = 200
    content = json.dumps(payload, ensure_ascii=False)
    sse = json.dumps(
        {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]},
        ensure_ascii=False,
    )
    response.iter_lines.return_value = iter([f"data: {sse}", "data: [DONE]", ""])
    return response


def _mock_status(status, provider_error=None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = {
        "error": provider_error or {"type": "server_error", "message": "boom"},
    }
    return response


class AiRetryPlanTests(unittest.TestCase):
    def test_default_plan_is_three_attempts_with_thirty_seconds(self):
        plan = effective_retry_plan(None)
        self.assertEqual(plan["max_attempts"], DEFAULT_AI_RETRY_MAX_ATTEMPTS)
        self.assertEqual(plan["delay_seconds"], DEFAULT_AI_RETRY_DELAY_SECONDS)

    def test_tuning_plan_wins_when_provided(self):
        plan = effective_retry_plan({"network_error": 2})
        self.assertEqual(plan["mode"], "tuning")
        self.assertEqual(plan["retry_limits"], {"network_error": 2})


class CallAiDefaultRetryTests(unittest.TestCase):
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_two_failures_then_success_uses_third_attempt(self, mock_post, mock_sleep):
        from webui.ai import call_ai

        mock_post.side_effect = [
            _mock_status(429), _mock_status(502), _mock_chat_response({"ok": True}),
        ]
        result = call_ai(
            "https://api.example.com/v1/chat/completions", "key",
            [{"role": "user", "content": "hi"}],
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [
            unittest.mock.call(30.0), unittest.mock.call(30.0),
        ])

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_three_failures_raise_safe_error(self, mock_post, mock_sleep):
        from webui.ai import AISecurityError, call_ai

        mock_post.side_effect = [
            _mock_status(429), _mock_status(500), _mock_status(503),
        ]
        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}],
            )
        self.assertEqual(ctx.exception.error_code, "server_error")
        self.assertEqual(mock_post.call_count, 3)

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_401_does_not_retry(self, mock_post, mock_sleep):
        from webui.ai import AISecurityError, call_ai

        mock_post.return_value = _mock_status(401)
        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}],
            )
        self.assertEqual(ctx.exception.error_code, "auth_failed")
        self.assertEqual(mock_post.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_invalid_response_does_not_retry(self, mock_post, mock_sleep):
        from webui.ai import AISecurityError, call_ai

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = iter(["data: [DONE]", ""])
        mock_post.return_value = response
        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}],
            )
        self.assertEqual(ctx.exception.error_code, "invalid_response")
        self.assertEqual(mock_post.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_network_timeout_is_retried_without_timeout_budget_cap(self, mock_post, mock_sleep):
        from webui.ai import call_ai

        mock_post.side_effect = [
            requests.Timeout("t"), requests.Timeout("t"), _mock_chat_response({"ok": True}),
        ]
        result = call_ai(
            "https://api.example.com/v1/chat/completions", "key",
            [{"role": "user", "content": "hi"}], timeout=10,
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [
            unittest.mock.call(30.0), unittest.mock.call(30.0),
        ])


class CallAiTuningRetryTests(unittest.TestCase):
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_explicit_budget_keeps_existing_behavior(self, mock_post, mock_sleep):
        from webui.ai import AISecurityError, call_ai

        mock_post.side_effect = requests.ConnectionError("offline")
        with self.assertRaises(AISecurityError):
            call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}],
                retry_limits={"network_error": 1},
            )
        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
