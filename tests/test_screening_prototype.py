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
        actions = (
            "markInterested", "moveToTrash", "restoreJob", "retryJob",
            "retryAll", "manualRoute",
        )
        for action in actions:
            self.assertIn(action, self.html)

    def test_separates_temporary_run_state_from_long_lived_records(self):
        self.assertIn("localStorage", self.html)
        self.assertIn("RUN_STORAGE_KEY", self.html)
        self.assertIn("LONG_TERM_STORAGE_KEY", self.html)
        self.assertIn("createdAt", self.html)
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

    def test_tracks_not_interested_separately_from_trash(self):
        self.assertIn("markNotInterested", self.html)
        self.assertIn("notInterested", self.html)
        self.assertIn("userActions", self.html)
        self.assertIn("移入垃圾桶", self.html)

    def test_simulates_31_day_cleanup_without_deleting_long_lived_records(
        self,
    ):
        self.assertIn("simulateCleanup", self.html)
        self.assertIn("cleanupRecords", self.html)
        self.assertIn("模拟 31 天后清理", self.html)
        self.assertIn("待核验岗位", self.html)
        self.assertIn("allPendingJobs", self.html)
        self.assertIn("pendingRemoved", self.html)

    def test_keeps_every_user_action_in_queryable_history(self):
        self.assertIn("userActions", self.html)
        self.assertNotIn("userActions=longTerm.userActions.slice", self.html)
        self.assertIn("全部操作历史", self.html)

    def test_python_contract_test_lines_stay_within_pep8_limit(self):
        for line_number, line in enumerate(
            Path(__file__).read_text(encoding="utf-8").splitlines(), start=1
        ):
            message = f"line {line_number} is too long"
            self.assertLessEqual(len(line), 79, message)


if __name__ == "__main__":
    unittest.main()
