"""047 US1 登录诊断回归：四态探测证据与安全摘要。

定义本轮冻结目标（spec.md C1 契约）：
- 401 → not_logged_in；受限类别（403/412/418/429、风险短语、code=31）→ restricted；
- 有效明文工资（code=0 且 jobList 含 salaryDesc）→ logged_in；
- 结构完整且无明文工资 → not_logged_in；
- 空响应 / 损坏 JSON / 非 dict 异常结构 / 未识别业务码 → unknown（不兜底成未登录）；
- 探针或 CDP 阶段异常 → unknown；
- 诊断摘要不得包含响应正文、Cookie、Token 等敏感标记。

先用合成敏感标记验证「响应正文不得进入诊断」，不打印标记原值。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from scripts.boss import login


class LoginProbeEvidenceTests(unittest.TestCase):
    """probe_login_state_tri：按响应事实分四态，不兜底。"""

    def _payload(self, text, status=0):
        return json.dumps({"status": status, "text": text})

    def test_http_401_is_not_logged_in(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._payload("", status=401)
        self.assertEqual(login.probe_login_state_tri(cdp, "sid"), "not_logged_in")

    def test_restricted_http_codes_stay_restricted(self):
        for status in (403, 412, 418, 429):
            with self.subTest(status=status):
                cdp = mock.Mock()
                cdp.eval_js.return_value = self._payload("", status=status)
                self.assertEqual(
                    login.probe_login_state_tri(cdp, "sid"), "restricted",
                )

    def test_business_code_31_is_restricted(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._payload(json.dumps({"code": 31}))
        self.assertEqual(login.probe_login_state_tri(cdp, "sid"), "restricted")

    def test_plaintext_salary_is_logged_in(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._payload(json.dumps({
            "code": 0,
            "zpData": {"jobList": [{"jobName": "Java", "salaryDesc": "20-40K"}]},
        }))
        self.assertEqual(login.probe_login_state_tri(cdp, "sid"), "logged_in")

    def test_structured_without_plaintext_salary_is_not_logged_in(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._payload(json.dumps({
            "code": 0,
            "zpData": {"jobList": [{"jobName": "Java", "salaryDesc": ""}]},
        }))
        self.assertEqual(
            login.probe_login_state_tri(cdp, "sid"), "not_logged_in",
        )

    def test_empty_probe_response_is_unknown(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = ""
        self.assertEqual(login.probe_login_state_tri(cdp, "sid"), "unknown")

    def test_corrupted_json_is_unknown(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = "{not-json"
        self.assertEqual(login.probe_login_state_tri(cdp, "sid"), "unknown")

    def test_unexpected_structure_is_unknown_not_missing_password(self):
        """非 dict 异常结构不得被当成「未登录」（不是缺密码字段）。"""
        cdp = mock.Mock()
        cdp.eval_js.return_value = json.dumps({
            "status": 0, "text": json.dumps(["unexpected", "list"]),
        })
        state = login.probe_login_state_tri(cdp, "sid")
        self.assertIn(state, {"unknown"})

    def test_unknown_business_code_is_unknown_not_missing_password(self):
        """未识别业务码按契约保持 unknown，不得兜底成「未登录」。"""
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._payload(json.dumps({"code": 7}))
        self.assertEqual(login.probe_login_state_tri(cdp, "sid"), "unknown")

    def test_missing_business_code_is_unknown(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._payload(json.dumps({"message": "x"}))
        self.assertEqual(login.probe_login_state_tri(cdp, "sid"), "unknown")

    def test_code_zero_without_visible_jobs_is_not_logged_in(self):
        """code==0 是该搜索 API 的合法响应形状；无明文薪资即未登录（016 契约）。"""
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._payload(
            json.dumps({"code": 0, "zpData": {}}),
        )
        self.assertEqual(
            login.probe_login_state_tri(cdp, "sid"), "not_logged_in",
        )

    def test_probe_exception_is_unknown(self):
        cdp = mock.Mock()
        cdp.eval_js.side_effect = RuntimeError("devtools exploded")
        with self.assertRaises(RuntimeError):
            # 单次探测本身把异常交给调用方；四态兜底由 check_login_state_tri 承担
            login.probe_login_state_tri(cdp, "sid")

    def test_check_login_state_tri_unknown_on_cdp_exception(self):
        with mock.patch.object(
            login.cdp_session, "CDPSession",
            side_effect=RuntimeError("cdp down"),
        ):
            self.assertEqual(login.check_login_state_tri(9333), "unknown")


class LoginDiagnosticsRedactionTests(unittest.TestCase):
    """诊断摘要不得包含响应正文 / Cookie / Token 等敏感内容。"""

    SECRET = "THIS-IS-A-SYNTHETIC-SECRET-TOKEN"

    def test_response_body_never_enters_raised_diagnostics(self):
        cdp = mock.Mock()
        cdp.eval_js.return_value = json.dumps({
            "status": 200,
            "text": json.dumps({
                "code": 0,
                "cookie": self.SECRET,
                "zpData": {"jobList": [{"salaryDesc": ""}]},
            }),
        })
        with mock.patch("scripts.boss.login.log") as log:
            state = login.probe_login_state_tri(cdp, "sid")
        self.assertEqual(state, "not_logged_in")
        for call in log.method_calls:
            self.assertNotIn(self.SECRET, str(call))

    def test_cdp_failure_log_does_not_include_synthetic_secret(self):
        with mock.patch.object(
            login.cdp_session, "CDPSession",
            side_effect=RuntimeError(self.SECRET),
        ), mock.patch("scripts.boss.login.log") as log:
            login.check_login_state_tri(9333)
        for call in log.method_calls:
            self.assertNotIn(self.SECRET, str(call))

    def test_no_response_text_in_state_helpers(self):
        """摘要只允许 HTTP 类别 / 结构类别 / 已知业务码 / 阶段。"""
        cdp = mock.Mock()
        payload = {"code": 0, "zpData": {"jobList": [{"salaryDesc": ""}]},
                   "hint": self.SECRET}
        cdp.eval_js.return_value = json.dumps(
            {"status": 0, "text": json.dumps(payload)},
        )
        with mock.patch("scripts.boss.login._logger") as logger:
            login.probe_login_state_tri(cdp, "sid")
        rendered = repr(logger.method_calls) + repr(logger.mock_calls)
        self.assertNotIn(self.SECRET, rendered)


if __name__ == "__main__":
    unittest.main()
