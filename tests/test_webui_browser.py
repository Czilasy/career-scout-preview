"""T047: Browser-level DOM contract tests for the workbench frontend.

Validates card structure, link safety, button non-navigation, JD
truncation, not-interested exit animation and narrow-screen drawer —
without a real browser, by parsing the served HTML and inline JS.
"""

import re
import sys
import pathlib
import tempfile
import unittest

from webui.app import create_app


class WorkbenchBrowserContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        resp = self.client.get("/")
        self.html = resp.get_data(as_text=True)
        resp.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_card_links_are_https_zhipin_only(self):
        """Card read-area links must only point to HTTPS zhipin domains."""
        # Find the JS that builds canonical_url into href
        # The template must not produce http: or non-zhipin links
        self.assertNotIn("http://www.zhipin.com", self.html)
        self.assertNotIn("javascript:void", self.html)
        # Must validate URLs before opening
        self.assertTrue(
            "zhipin.com" in self.html,
            "前端必须包含 zhipin.com 域名校验逻辑",
        )

    def test_feedback_buttons_prevent_navigation(self):
        """Interested / not-interested buttons must not trigger card link navigation."""
        # Buttons should be type="button" (not submit) and should call
        # preventDefault / stopPropagation in the inline JS
        self.assertTrue(
            'type="button"' in self.html or '.type = "button"' in self.html,
            "反馈按钮必须显式指定 button 类型",
        )
        # Must have event prevention in JS
        has_prevention = (
            "preventDefault" in self.html
            or "stopPropagation" in self.html
            or "event.stopPropagation" in self.html
        )
        self.assertTrue(has_prevention, "反馈按钮必须阻止事件冒泡或默认行为")

    def test_jd_excerpt_is_truncated(self):
        """JD text on cards must be visually truncated, not full-length."""
        # CSS must limit card JD height or use line-clamp / max-height
        has_truncation = (
            "line-clamp" in self.html
            or "max-height" in self.html
            or "text-overflow" in self.html
            or "-webkit-line-clamp" in self.html
        )
        self.assertTrue(has_truncation, "JD 摘要必须有截断样式")

    def test_not_interested_has_exit_animation(self):
        """Not-interested action must trigger a smooth exit animation."""
        # CSS must define a transition or animation for card removal
        has_animation = (
            "transition" in self.html
            or "@keyframes" in self.html
            or "animation" in self.html
        )
        self.assertTrue(has_animation, "卡片必须有过渡或动画")
        # JS must add a class for exit or set opacity
        has_exit = (
            "opacity" in self.html
            or "fade-out" in self.html
            or "slide-out" in self.html
            or "exit" in self.html.lower()
        )
        self.assertTrue(has_exit, "不感兴趣必须有退场效果")

    def test_undo_after_not_interested(self):
        """Undo must be available after marking not-interested."""
        self.assertIn("撤销", self.html)

    def test_narrow_screen_drawer(self):
        """Narrow screens must collapse settings into a drawer."""
        self.assertIn("@media (max-width: 720px)", self.html)
        # Settings panel must be hideable
        self.assertIn("settingsPanel", self.html)

    def test_no_ai_scores_in_card_template(self):
        """Card template must not expose AI scores, ranks or match reasons."""
        self.assertNotIn("match_score", self.html)
        self.assertNotIn("ai_rank", self.html)
        self.assertNotIn("match_reason", self.html)
        self.assertNotIn("ai_score", self.html)

    def test_no_auto_application_ui(self):
        """Frontend must not show auto-apply, auto-message or probability UI."""
        self.assertNotIn("自动投递", self.html)
        self.assertNotIn("联系招聘者", self.html)
        self.assertNotIn("录用概率", self.html)
        self.assertNotIn("/api/apply", self.html)

    def test_fixed_height_cards(self):
        """Cards must have fixed height — no variable-height expansion."""
        # CSS must set a fixed or max height on job cards
        has_fixed_height = (
            "fixed-height" in self.html
            or "max-height" in self.html
            or re.search(r"height:\s*\d+px", self.html) is not None
            or "height: calc(" in self.html
        )
        self.assertTrue(has_fixed_height, "岗位卡片必须有固定高度")

    def test_untrusted_ai_and_url_values_are_not_interpolated_as_html(self):
        """AI suggestions and validated URLs must be rendered with DOM APIs, not HTML strings."""
        self.assertNotIn('el.innerHTML = `', self.html)
        self.assertNotIn('href="${safeUrl}"', self.html)
        self.assertIn("textContent", self.html)


if __name__ == "__main__":
    unittest.main()
