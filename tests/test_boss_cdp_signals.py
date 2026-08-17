"""Regression coverage for explicit BOSS runtime signal classification."""

from __future__ import annotations

import unittest

from scripts.boss_cdp_signals import (
    api_code_diagnosis,
    api_code_hint,
    detail_page_hint,
    is_risk_api_code,
)


class BossCdpSignalTests(unittest.TestCase):
    def test_code_37_is_a_explicit_risk_signal(self):
        self.assertTrue(is_risk_api_code(37))
        self.assertEqual(
            api_code_diagnosis("37", "您的环境存在异常"),
            {
                "kind": "api_code",
                "code": 37,
                "sample": "您的环境存在异常",
            },
        )
        self.assertEqual(api_code_hint(37), "BOSS 返回 code:37（环境存在异常）")

    def test_about_blank_has_a_specific_safe_hint(self):
        self.assertEqual(detail_page_hint("about:blank"), "详情页停留在 about:blank")
        self.assertEqual(detail_page_hint("https://www.zhipin.com/job_detail/a.html"), "")


if __name__ == "__main__":
    unittest.main()