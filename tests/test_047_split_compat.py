"""047 characterization and line-gate tests.

The public route/DOM assertions intentionally describe the pre-split contract.
The line assertions are the first red tests for the internal facade split.
"""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SplitCompatibilityTests(unittest.TestCase):
    def test_search_route_facade_keeps_registration_entrypoint(self):
        from webui.exec_search_api import register_exec_search_routes

        self.assertTrue(callable(register_exec_search_routes))

    def test_discovery_view_keeps_public_mount_and_interaction_markers(self):
        source = (ROOT / "webui/src/views/DiscoveryView.vue").read_text(encoding="utf-8")
        for marker in (
            'data-testid="start-one-click"',
            'data-testid="start-scrape"',
            'data-testid="continue-to-screen"',
            'data-testid="latest-result-empty"',
            "<OneClickScreenDialog",
            "<ResultHistoryDrawer",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_search_api_is_below_python_redline_after_split(self):
        path = ROOT / "webui/exec_search_api.py"
        self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 800)

    def test_discovery_view_is_below_vue_redline_after_split(self):
        path = ROOT / "webui/src/views/DiscoveryView.vue"
        self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 1200)

    def test_b096_flow_store_and_run_facades_are_below_python_redline(self):
        """B096 storage facades must stay thin after the second extraction."""
        for relative in (
            "webui/store_flow.py",
            "webui/store_runs.py",
            "webui/store_flow_core.py",
            "webui/store_flow_claims.py",
            "webui/store_flow_results.py",
            "webui/store_flow_legacy.py",
            "webui/store_flow_runs.py",
        ):
            with self.subTest(relative=relative):
                path = ROOT / relative
                self.assertTrue(path.exists(), relative)
                self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 800)


if __name__ == "__main__":
    unittest.main()
