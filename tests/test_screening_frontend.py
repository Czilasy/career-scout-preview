"""Formal five-view screening UI contract tests."""

from __future__ import annotations

import pathlib
import re
import unittest


HTML = pathlib.Path(__file__).parents[1] / "webui" / "index.html"


class ScreeningFiveViewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")

    def test_all_five_formal_views_are_present(self):
        for zone in ("match", "mismatch", "pending", "interested", "trash"):
            self.assertIn(f'data-zone-tab="{zone}"', self.html)
            self.assertIn(f'data-zone="{zone}"', self.html)

    def test_pending_has_retry_all_and_manual_routing(self):
        self.assertIn("retryScreeningPendingAll", self.html)
        self.assertIn("routeScreeningPending", self.html)
        self.assertIn("/pending/retry-all", self.html)

    def test_trash_has_permanent_restore_action(self):
        self.assertIn("restoreScreeningTrash", self.html)
        self.assertIn("/restore", self.html)

    def test_frontend_uses_api_items_contract(self):
        self.assertIn("matchData.items", self.html)
        self.assertIn("pendingData.items", self.html)
        self.assertIn("data.items", self.html)

    def test_compact_rows_do_not_use_fixed_card_height(self):
        rule = re.search(r"\.screening-card\s*\{([^}]+)\}", self.html, re.S)
        self.assertIsNotNone(rule)
        self.assertIn("grid-template-columns", rule.group(1))
        self.assertNotIn("height: var(--card-h)", rule.group(1))

    def test_narrow_screen_prevents_page_horizontal_scroll(self):
        self.assertIn("overflow-x: hidden", self.html)
        self.assertIn("@media (max-width: 720px)", self.html)

    def test_reduced_motion_is_supported(self):
        self.assertIn("prefers-reduced-motion: reduce", self.html)


if __name__ == "__main__":
    unittest.main()
