"""Static browser-safety and accessibility contracts for the Vue WebUI.

Runtime behavior lives in Vitest and the real-browser acceptance pass.  This
module intentionally reads source files rather than minified Vite output.
"""

from pathlib import Path
import re
import tempfile
import unittest

from webui.app import create_app


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "webui" / "src"
APP = (SRC / "App.vue").read_text(encoding="utf-8")
DIALOG = (SRC / "components" / "BaseDialog.vue").read_text(encoding="utf-8")
JOBS = (SRC / "components" / "JobWorkspace.vue").read_text(encoding="utf-8")
AI = (SRC / "components" / "AiSettingsDialog.vue").read_text(encoding="utf-8")
NOTICE = (SRC / "components" / "NoticeBar.vue").read_text(encoding="utf-8")
DISCOVERY = (SRC / "views" / "DiscoveryView.vue").read_text(encoding="utf-8")
CSS = (SRC / "styles.css").read_text(encoding="utf-8")
ALL_VUE = "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.vue"))


class BuiltFrontendEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_homepage_uses_local_hashed_assets_only(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertRegex(html, r'src="/static/assets/index-[^"]+\.js"')
        self.assertRegex(html, r'href="/static/assets/index-[^"]+\.css"')
        self.assertNotIn("cdn", html.lower())
        self.assertNotRegex(html.lower(), r'(?:src|href)="http://')


class VueBrowserContractTests(unittest.TestCase):
    def test_navigation_and_actions_use_native_semantics(self):
        for match in re.finditer(r"<button\b([^>]*)>", ALL_VUE):
            self.assertIn("type=", match.group(1), match.group(0))

    def test_dialog_has_modal_semantics_escape_and_focus_trap(self):
        self.assertIn('role="dialog"', DIALOG)
        self.assertIn('aria-modal="true"', DIALOG)
        self.assertIn('event.key === "Escape"', DIALOG)
        self.assertIn('event.key !== "Tab"', DIALOG)
        self.assertIn("previousFocus", DIALOG)

    def test_ai_settings_is_reachable_on_narrow_screens(self):
        self.assertIn('data-testid="ai-settings-trigger"', APP)
        self.assertIn(".ai-settings-trigger", CSS)
        mobile = CSS.split("@media (max-width: 760px)", 1)[1]
        self.assertIn(".ai-settings-trigger", mobile)
        self.assertNotRegex(mobile, r"\.ai-settings-trigger\s*\{[^}]*display:\s*none")

    def test_profile_switcher_is_reachable_on_narrow_screens(self):
        mobile = CSS.split("@media (max-width: 760px)", 1)[1]
        profile = mobile.split(".profile-picker", 1)[1].split("}", 1)[0]
        self.assertNotIn("display: none", profile)
        self.assertIn("grid-row: 3", profile)
        self.assertIn("width: 100%", mobile.split(".profile-picker select", 1)[1].split("}", 1)[0])

    def test_touch_targets_and_focus_states_are_explicit(self):
        self.assertIn("min-height: 44px", CSS)
        self.assertIn(":focus-visible", CSS)
        self.assertIn("outline: 3px", CSS)
        self.assertRegex(CSS, r"\.brand\s*\{[^}]*min-height:\s*44px")
        self.assertRegex(CSS, r"\.result-tabs button\s*\{[^}]*min-height:\s*44px")

    def test_mobile_detail_is_full_screen_and_page_does_not_gain_horizontal_scroll(self):
        mobile = CSS.split("@media (max-width: 760px)", 1)[1]
        detail = mobile.split(".job-detail-pane", 1)[1].split("}", 1)[0]
        self.assertIn("position: fixed", detail)
        self.assertIn("inset: 0", detail)
        self.assertIn("min-width: 320px", CSS)

    def test_mobile_navigation_uses_fitted_grids_without_scrollbars(self):
        mobile = CSS.split("@media (max-width: 760px)", 1)[1]
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", mobile)
        self.assertNotIn("min-width: 390px", mobile)

    def test_mobile_header_actions_stay_exactly_44_pixels(self):
        mobile = CSS.split("@media (max-width: 760px)", 1)[1]
        stage_action = mobile.split(".stage-header .button", 1)[1].split("}", 1)[0]
        self.assertIn("height: 44px", stage_action)
        self.assertIn("font-size: 0", stage_action)

    def test_reduced_motion_is_supported(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)

    def test_untrusted_content_is_not_rendered_with_v_html(self):
        self.assertNotIn("v-html", ALL_VUE)
        self.assertNotIn("innerHTML", ALL_VUE)
        self.assertIn("{{ selectedJob.jd", JOBS)

    def test_external_job_links_use_noopener(self):
        self.assertIn('target="_blank"', JOBS)
        self.assertIn('rel="noopener noreferrer"', JOBS)
        self.assertIn('parsed.protocol === "https:"', JOBS)
        self.assertIn('host.endsWith(".zhipin.com")', JOBS)

    def test_global_notices_are_live_and_dismissible(self):
        self.assertIn('role="status"', NOTICE)
        self.assertIn('aria-live="polite"', NOTICE)
        self.assertIn('aria-label="关闭提示"', NOTICE)

    def test_ai_model_refresh_keeps_full_accessible_meaning(self):
        self.assertIn('aria-label="拉取可用模型"', AI)
        self.assertIn('title="拉取可用模型"', AI)
        self.assertIn("拉取模型", AI)

    def test_product_does_not_claim_automatic_application_or_contact(self):
        # 覆盖全部 Vue 源码（含已并入 DiscoveryView 的原筛选工作台区域）
        self.assertNotIn("自动投递", ALL_VUE)
        self.assertNotIn("联系招聘者", ALL_VUE)
        self.assertNotIn("录用概率", ALL_VUE)


if __name__ == "__main__":
    unittest.main()
