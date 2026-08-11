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


if __name__ == "__main__":
    unittest.main()
