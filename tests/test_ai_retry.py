import json
import unittest
from unittest.mock import MagicMock, patch

import requests

from webui.ai_retry import (
    DEFAULT_AI_RETRY_TOTAL_WAIT_SECONDS,
    DEFAULT_RETRY_POLICY,
    effective_retry_plan,
    normalize_retry_policy,
    retry_delay_seconds,
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
    def test_default_plan_uses_per_code_policy_and_sixty_second_cap(self):
        plan = effective_retry_plan(None)
        self.assertEqual(plan["mode"], "default")
        self.assertEqual(
            plan["total_wait_seconds"], DEFAULT_AI_RETRY_TOTAL_WAIT_SECONDS)
        self.assertEqual(
            plan["policy"]["network_error"]["max_retries"], 3)
        self.assertEqual(
            plan["policy"]["rate_limited"]["max_retries"], 3)
        # timeout/server_error 在注册表中归入 ai_network_error，保留网络退避。
        self.assertIn("timeout", plan["policy"])
        self.assertIn("server_error", plan["policy"])

    def test_default_policy_has_expected_backoffs(self):
        self.assertEqual(
            tuple(DEFAULT_RETRY_POLICY["network_error"]["backoff_seconds"]),
            (2.0, 4.0, 8.0),
        )
        self.assertEqual(
            tuple(DEFAULT_RETRY_POLICY["rate_limited"]["backoff_seconds"]),
            (5.0, 15.0, 30.0),
        )

    def test_tuning_plan_wins_when_provided(self):
        plan = effective_retry_plan({"network_error": 2})
        self.assertEqual(plan["mode"], "tuning")
        self.assertEqual(plan["retry_limits"], {"network_error": 2})
        self.assertEqual(
            plan["total_wait_seconds"], DEFAULT_AI_RETRY_TOTAL_WAIT_SECONDS)

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    def test_network_delays_are_2_4_8(self, _mock_uniform):
        plan = effective_retry_plan(None)
        self.assertEqual(
            [retry_delay_seconds("network_error", i, plan) for i in range(3)],
            [2.0, 4.0, 8.0],
        )

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    def test_rate_limited_delays_are_5_15_30(self, _mock_uniform):
        plan = effective_retry_plan(None)
        self.assertEqual(
            [retry_delay_seconds("rate_limited", i, plan) for i in range(3)],
            [5.0, 15.0, 30.0],
        )

    @patch("webui.ai_retry.random.uniform", return_value=0.99)
    def test_jitter_stays_within_bounds(self, _mock_uniform):
        plan = effective_retry_plan(None)
        delay = retry_delay_seconds("network_error", 0, plan)
        self.assertGreaterEqual(delay, 2.0)
        self.assertLessEqual(delay, 3.0)

    def test_invalid_response_has_transport_delay(self):
        # B063：invalid_response 进入默认策略（空响应退避 1/2s），不再是无退避。
        plan = effective_retry_plan(None)
        self.assertEqual(
            tuple(DEFAULT_RETRY_POLICY["invalid_response"]["backoff_seconds"]),
            (1.0, 2.0),
        )
        self.assertEqual(
            plan["policy"]["invalid_response"]["max_retries"], 2)


class RetryPolicyNormalizationTests(unittest.TestCase):
    def test_valid_policy_normalized(self):
        policy = {
            "network_error": {
                "max_retries": 2,
                "backoff_seconds": [1, 2],
                "jitter_seconds": 0.5,
            },
        }
        normalized = normalize_retry_policy(policy)
        self.assertEqual(normalized["network_error"]["max_retries"], 2)
        self.assertEqual(
            normalized["network_error"]["backoff_seconds"], [1.0, 2.0])
        self.assertEqual(normalized["network_error"]["jitter_seconds"], 0.5)

    def test_missing_or_empty_policy_returns_none(self):
        self.assertIsNone(normalize_retry_policy(None))
        self.assertIsNone(normalize_retry_policy({}))

    def test_legacy_recoverable_codes_shape_normalized(self):
        normalized = normalize_retry_policy({
            "recoverable_codes": ["network_error", "rate_limited"],
            "max_retries": 2,
        })
        self.assertEqual(normalized["network_error"]["max_retries"], 2)
        self.assertEqual(normalized["rate_limited"]["max_retries"], 2)

    def test_scalar_backoff_normalized(self):
        normalized = normalize_retry_policy({
            "detail_timeout": {"max_retries": 1, "backoff_seconds": 3},
        })
        self.assertEqual(
            normalized["detail_timeout"]["backoff_seconds"], [3.0])

    def test_invalid_policy_returns_none(self):
        self.assertIsNone(normalize_retry_policy(
            {"network_error": {"max_retries": -1}}))
        self.assertIsNone(normalize_retry_policy(
            {"network_error": {"max_retries": "x"}}))
        self.assertIsNone(normalize_retry_policy(
            {"network_error": {"max_retries": 1, "backoff_seconds": []}}))
        self.assertIsNone(normalize_retry_policy(
            {"network_error": {"max_retries": 1, "jitter_seconds": -1}}))


class CallAiDefaultRetryTests(unittest.TestCase):
    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_two_failures_then_success_uses_third_attempt(
        self, mock_post, mock_sleep, _mock_uniform,
    ):
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
            unittest.mock.call(5.0), unittest.mock.call(2.0),
        ])

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_three_failures_raise_safe_error(self, mock_post, mock_sleep, _mock_uniform):
        from webui.ai import AISecurityError, call_ai

        mock_post.side_effect = [
            _mock_status(500), _mock_status(500),
            _mock_status(500), _mock_status(500),
        ]
        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}],
            )
        self.assertEqual(ctx.exception.error_code, "server_error")
        self.assertEqual(mock_post.call_count, 4)
        self.assertEqual(mock_sleep.call_args_list, [
            unittest.mock.call(2.0), unittest.mock.call(4.0),
            unittest.mock.call(8.0),
        ])

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_total_wait_cap_stops_before_overflow(self, mock_post, mock_sleep, _mock_uniform):
        from webui.ai import AISecurityError, call_ai

        # 5+2+4+8+15=34 秒已等待；下一次 30 秒会超过 60 秒上限，必须停。
        mock_post.side_effect = [
            _mock_status(429), _mock_status(500), _mock_status(500),
            _mock_status(500), _mock_status(429), _mock_status(429),
        ]
        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}],
            )
        self.assertEqual(ctx.exception.error_code, "rate_limited")
        self.assertEqual(mock_post.call_count, 6)
        self.assertEqual(mock_sleep.call_args_list, [
            unittest.mock.call(5.0), unittest.mock.call(2.0),
            unittest.mock.call(4.0), unittest.mock.call(8.0),
            unittest.mock.call(15.0),
        ])

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

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_empty_response_retries_twice_then_invalid(
        self, mock_post, mock_sleep, _mock_uniform,
    ):
        """B063：HTTP 200 空 body 在统一层重试默认 2 次，全部为空才抛 invalid_response。

        旧行为：空 body 直接抛错（mock_post 只调用 1 次）。新行为：重试 2 次
        （共 3 次调用），耗尽后仍抛 invalid_response 且带 empty_response 诊断。
        """
        from webui.ai import AISecurityError, call_ai

        def _empty():
            response = MagicMock()
            response.status_code = 200
            response.iter_lines.return_value = iter(["data: [DONE]", ""])
            return response

        mock_post.side_effect = [_empty(), _empty(), _empty()]
        with self.assertRaises(AISecurityError) as ctx:
            call_ai(
                "https://api.example.com/v1/chat/completions", "key",
                [{"role": "user", "content": "hi"}], timeout=30,
            )
        self.assertEqual(ctx.exception.error_code, "invalid_response")
        self.assertEqual(
            ctx.exception.diagnostics.get("failure_phase"), "empty_response")
        self.assertEqual(mock_post.call_count, 3)

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_empty_response_retries_then_success(self, mock_post, mock_sleep, _mock_uniform):
        """B063：第一次空 body、第二次正常返回 → 自动重试成功，无异常。"""
        from webui.ai import call_ai

        def _empty():
            response = MagicMock()
            response.status_code = 200
            response.iter_lines.return_value = iter(["data: [DONE]", ""])
            return response

        mock_post.side_effect = [_empty(), _mock_chat_response({"ok": True})]
        result = call_ai(
            "https://api.example.com/v1/chat/completions", "key",
            [{"role": "user", "content": "hi"}], timeout=30,
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_post.call_count, 2)

    @patch("webui.ai_retry.random.uniform", return_value=0.0)
    @patch("webui.ai.time.sleep")
    @patch("webui.ai.requests.post")
    def test_network_timeout_uses_network_backoff(
        self, mock_post, mock_sleep, _mock_uniform,
    ):
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
            unittest.mock.call(2.0), unittest.mock.call(4.0),
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
        mock_sleep.assert_called()


if __name__ == "__main__":
    unittest.main()
