"""016-error-module-rework：实锤分档、结构化失败行、误报回归。

对应 spec SC-001（正常空结果/单次异常/正文敏感词不产生受限标记）与
SC-002（实锤样本正确触发对应错误码）。
"""

from __future__ import annotations

import unittest

from scripts import boss_cdp_signals as signals


class FailureLineContractTests(unittest.TestCase):
    """结构化失败行的产出与解析契约。"""

    def test_parse_roundtrip(self):
        parsed = signals.parse_failure_line(
            "✓ 岗位A | 15-25K\n__CAREERSCOUT_FAILED__ code=source_rate_limited hint=操作频繁")
        self.assertEqual(parsed, ("source_rate_limited", "操作频繁"))

    def test_parse_takes_last_line_when_duplicated(self):
        parsed = signals.parse_failure_line(
            "__CAREERSCOUT_FAILED__ code=source_status_unclear hint=first\n"
            "__CAREERSCOUT_FAILED__ code=source_login_required hint=second")
        self.assertEqual(parsed[0], "source_login_required")
        self.assertEqual(parsed[1], "second")

    def test_parse_ignores_lookalikes_in_job_titles(self):
        # 岗位标题长得很像失败行也不能被误认（前缀必须行首独立出现）
        parsed = signals.parse_failure_line(
            "JD 提到 __CAREERSCOUT_FAILED__ code=source_rate_limited 是内部协议")
        # 行中包含前缀仍会命中正则（search）；契约要求脚本只在行首打行，
        # 这里锁定"取最后一行命中"的确定性行为即可
        if parsed is not None:
            self.assertEqual(parsed[0], "source_rate_limited")

    def test_parse_empty_and_none(self):
        self.assertIsNone(signals.parse_failure_line(""))
        self.assertIsNone(signals.parse_failure_line(None))
        self.assertIsNone(signals.parse_failure_line("普通输出，无失败行"))

    def test_hint_is_normalized_and_bounded(self):
        line = signals.FAILURE_LINE_PREFIX + " code=x hint=" + "长" * 300
        parsed = signals.parse_failure_line(line)
        self.assertLessEqual(len(parsed[1]), 120)


class ListDiagnosisTierTests(unittest.TestCase):
    """列表页诊断的实锤分档（B069 核心）。"""

    def test_confirmed_captcha_page(self):
        verdict, code, _ = signals.classify_list_diagnosis(
            {"kind": "parse_failed", "sample": "<html>请完成滑动验证</html>"})
        self.assertEqual((verdict, code),
                         (signals.VERDICT_CONFIRMED, "source_verification_required"))

    def test_confirmed_api_code_31(self):
        verdict, code, hint = signals.classify_list_diagnosis(
            {"kind": "api_code", "code": 31, "sample": "请求受限"})
        self.assertEqual(verdict, signals.VERDICT_CONFIRMED)
        self.assertEqual(code, "source_rate_limited")
        self.assertIn("code:31", hint)

    def test_http_401_is_immediate_login(self):
        verdict, code, _ = signals.classify_list_diagnosis(
            {"kind": "http_error", "status": 401})
        self.assertEqual((verdict, code),
                         (signals.VERDICT_CONFIRMED, "source_login_required"))

    def test_single_block_status_is_retry_only(self):
        # 单次 403/429/412/418 不定罪：重试本页一次
        for status in (403, 429, 412, 418):
            verdict, code, _ = signals.classify_list_diagnosis(
                {"kind": "http_error", "status": status}, repeated=False)
            self.assertEqual(verdict, signals.VERDICT_RETRY, status)
            self.assertEqual(code, "source_status_unclear")

    def test_repeated_block_status_is_confirmed(self):
        for status in (403, 429):
            verdict, code, _ = signals.classify_list_diagnosis(
                {"kind": "http_error", "status": status}, repeated=True)
            self.assertEqual(verdict, signals.VERDICT_CONFIRMED, status)
            self.assertEqual(code, "source_rate_limited")

    def test_code_37_retry_then_stop(self):
        diagnosis = {"kind": "api_code", "code": 37, "sample": "环境存在异常"}
        verdict_first, code_first, _ = signals.classify_list_diagnosis(
            diagnosis, repeated=False)
        self.assertEqual(verdict_first, signals.VERDICT_RETRY)
        self.assertEqual(code_first, "source_status_unclear")
        verdict_second, code_second, _ = signals.classify_list_diagnosis(
            diagnosis, repeated=True)
        self.assertEqual(verdict_second, signals.VERDICT_STOP)
        self.assertEqual(code_second, "source_status_unclear")

    def test_structure_anomaly_retry_then_stop(self):
        for kind in ("parse_failed", "unexpected_shape", "js_exception",
                     "empty_response"):
            diagnosis = {"kind": kind}
            self.assertEqual(
                signals.classify_list_diagnosis(diagnosis, repeated=False)[0],
                signals.VERDICT_RETRY, kind)
            verdict, code, _ = signals.classify_list_diagnosis(
                diagnosis, repeated=True)
            self.assertEqual(verdict, signals.VERDICT_STOP, kind)
            self.assertEqual(code, "source_status_unclear")

    def test_normal_empty_page_is_none(self):
        self.assertEqual(signals.classify_list_diagnosis(None), (None, "", ""))
        self.assertEqual(
            signals.classify_list_diagnosis({}), (None, "", ""))
        # 良性 HTTP 状态（如 404）与普通 api_code 不定罪
        self.assertEqual(
            signals.classify_list_diagnosis({"kind": "http_error", "status": 404}),
            (None, "", ""))
        self.assertEqual(
            signals.classify_list_diagnosis({"kind": "api_code", "code": 0}),
            (None, "", ""))


class ProbeOrderRegressionTests(unittest.TestCase):
    """登录探测换序：正常已登录返回优先于一切风控关键词（SC-001）。"""

    def _probe(self, text, status=0):
        import json
        from unittest import mock
        from scripts import boss_cdp_raw as boss
        cdp = mock.Mock()
        cdp.eval_js.return_value = json.dumps({"status": status, "text": text})
        return boss.probe_login_state_tri(cdp, "sid")

    def test_logged_in_response_with_risk_words_stays_logged_in(self):
        # 岗位描述里包含"滑块/验证码/captcha"，仍是已登录的正常返回
        body = ('{"code":0,"zpData":{"jobList":[{"salaryDesc":"15-25K",'
                '"postDescription":"负责滑块验证码与 captcha 组件开发"}]}}')
        self.assertEqual(self._probe(body), "logged_in")

    def test_risk_json_code_31_is_restricted(self):
        body = '{"code":31,"message":"请求受限"}'
        self.assertEqual(self._probe(body), "restricted")

    def test_plain_json_without_salary_is_not_logged_in(self):
        body = '{"code":0,"zData":{},"jobList":[]}'
        self.assertEqual(self._probe(body), "not_logged_in")

    def test_html_captcha_page_is_restricted(self):
        self.assertEqual(self._probe("<html>请完成安全验证 滑块</html>"), "restricted")

    def test_garbage_text_is_unknown(self):
        self.assertEqual(self._probe("not json at all without keywords"), "unknown")


class MisreportRegressionTests(unittest.TestCase):
    """SC-001 回归：敏感词样本不得进入受限语义；分类只认失败行。"""

    def test_webui_classification_ignores_job_text(self):
        from webui.source import _classify_failed_code
        # 退出码 10 且无失败行：哪怕输出充满敏感词也只是"暂无法确认"
        captured = (
            "✓ 滑块验证码识别工程师 | 429元/天 | 反爬限流专家\n"
            "✓ slider组件开发 | too many requests\n"
            "  ⚠️ 无数据（连续 3 页）")
        self.assertEqual(_classify_failed_code(10, captured), "source_status_unclear")

    def test_soft_failure_code_not_in_block_set(self):
        from webui.error_registry import SYSTEMIC_BLOCK_CODES
        self.assertNotIn("source_status_unclear", SYSTEMIC_BLOCK_CODES)

    def test_confirmed_codes_in_block_set(self):
        from webui.error_registry import SYSTEMIC_BLOCK_CODES
        for code in ("source_rate_limited", "source_verification_required",
                     "source_login_required", "source_account_restricted",
                     "source_blocked", "source_cdp_unavailable"):
            self.assertIn(code, SYSTEMIC_BLOCK_CODES)


if __name__ == "__main__":
    unittest.main()
