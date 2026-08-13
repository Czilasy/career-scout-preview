import unittest

from scripts import zhilian_cdp_raw as zha


class ZhilianRiskSignalTests(unittest.TestCase):
    def test_chrome_error_page_is_unreachable(self):
        self.assertEqual(
            zha._risk_signal("无法访问此网站", "chrome-error://chromewebdata/"),
            "unreachable",
        )

    def test_data_url_chromewebdata_is_unreachable(self):
        self.assertEqual(
            zha._risk_signal("", "data:text/html,chromewebdata"),
            "unreachable",
        )

    def test_real_block_marker_still_blocks(self):
        self.assertEqual(
            zha._risk_signal("访问被拒绝，请稍后重试", "https://www.zhaopin.com/"),
            "blocked",
        )

    def test_normal_page_keeps_existing_signal(self):
        self.assertIsNone(
            zha._risk_signal("职位详情加载成功", "https://www.zhaopin.com/jobdetail/x.htm"),
        )

    def test_normal_company_text_with_429_is_not_rate_limited(self):
        """回归：公司介绍里的“位列429”等正文数字不得触发限流。"""
        self.assertIsNone(
            zha._risk_signal(
                "公司位列429。软通动力拥有软通咨询、软通金科等业务子品牌。",
                "https://www.zhaopin.com/jobdetail/CC000544460J40824500616.htm",
            )
        )

    def test_normal_text_with_403_is_not_blocked(self):
        """回归：招聘人数/门牌号等正文里的 403 不得误判为封禁。"""
        self.assertIsNone(
            zha._risk_signal(
                "招聘人数：403人，办公地址：403室，页面加载正常。",
                "https://www.zhaopin.com/jobdetail/x.htm",
            )
        )

    def test_forbidden_in_normal_english_copy_is_not_blocked(self):
        """回归：正文英文里的 forbidden 不得单独触发封禁。"""
        self.assertIsNone(
            zha._risk_signal(
                "Candidates may apply for any position not forbidden by local law.",
                "https://www.zhaopin.com/jobdetail/x.htm",
            )
        )


if __name__ == "__main__":
    unittest.main()
