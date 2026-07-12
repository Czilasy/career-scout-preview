"""Contract tests for the isolated phase 2/3 screening prototype."""

from pathlib import Path
import unittest


PROTOTYPE = Path(__file__).parents[1] / "webui" / "screening-prototype.html"


class ScreeningPrototypeContractTests(unittest.TestCase):
    def setUp(self):
        self.html = PROTOTYPE.read_text(encoding="utf-8")

    def test_exposes_all_simulated_screening_operations(self):
        for text in ("符合", "不符合", "待核验", "感兴趣", "垃圾桶"):
            self.assertIn(text, self.html)
        for action in ("toggleInterest", "moveToTrash", "restoreJob", "retryJob", "retryAll", "manualRoute"):
            self.assertIn(action, self.html)

    def test_persists_long_lived_mock_records_in_browser_storage(self):
        self.assertIn("localStorage", self.html)
        self.assertNotIn("sessionStorage", self.html)

    def test_pending_jobs_explain_retry_state_and_last_failure(self):
        self.assertIn("retryable", self.html)
        self.assertIn("lastFailedAt", self.html)
        self.assertIn("无法自动重试", self.html)

    def test_narrow_screen_has_a_filter_drawer(self):
        self.assertIn("mobile-filter-toggle", self.html)
        self.assertIn("filters-open", self.html)
        self.assertIn("@media (max-width: 720px)", self.html)

    def test_mock_data_is_not_rendered_through_html_interpolation(self):
        self.assertNotIn("innerHTML", self.html)
        self.assertIn("textContent", self.html)

    def test_remains_explicitly_mock_only(self):
        self.assertIn("模拟数据", self.html)
        self.assertIn("不连接真实后端", self.html)
        self.assertNotIn("fetch(", self.html)


if __name__ == "__main__":
    unittest.main()
