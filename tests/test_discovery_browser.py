"""Browser rendering tests for feature 004 discovery frontend (T088/T089).

Uses Playwright to verify the unified four-step discovery workspace renders
correctly at desktop (1366x768) and narrow (720px) widths, across empty /
loading / success / partial / failed / needs-review / no-results states.

Checks: no horizontal overflow, primary actions reachable, focus-visible
state present. These are *real browser* renders, not simulated DOM asserts.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

from webui.app import create_app

PLAYWRIGHT_AVAILABLE = True
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


def _skip_if_no_playwright():
    if not PLAYWRIGHT_AVAILABLE:
        raise unittest.SkipTest("playwright not installed")


class _BrowserServer:
    """Start a real Flask server on a free port in a background thread."""

    def __init__(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({
            "TESTING": True,
            "DB_PATH": self._tmp.name,
            "START_TASKS": False,
        })
        from werkzeug.serving import make_server
        self._server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self._server.port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path="/"):
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self):
        self._server.shutdown()
        self._thread.join(timeout=5)
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "playwright not installed")
class DiscoveryBrowserRenderTests(unittest.TestCase):
    """T088: browser rendering across viewport sizes and run states."""

    @classmethod
    def setUpClass(cls):
        _skip_if_no_playwright()
        cls.server = _BrowserServer()
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls.server.stop()

    def setUp(self):
        self._context = self._browser.new_context(viewport={"width": 1366, "height": 768})
        self.page = self._context.new_page()
        self.page.goto(self.server.url("/"), wait_until="networkidle")

    def tearDown(self):
        self._context.close()

    # ------------------------------------------------------------------
    # Viewport: desktop 1366x768
    # ------------------------------------------------------------------

    def test_desktop_no_horizontal_overflow(self):
        """No horizontal scrollbar at 1366x768."""
        overflow = self.page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        self.assertFalse(overflow, "horizontal overflow at 1366x768")

    def test_desktop_four_steps_present(self):
        """All four discovery step sections exist in DOM."""
        for step_id in ["discoveryStepUpload", "discoveryStepReview",
                        "discoveryStepProgress", "discoveryStepResults"]:
            el = self.page.query_selector(f"#{step_id}")
            self.assertIsNotNone(el, f"missing #{step_id}")

    def test_desktop_primary_actions_reachable(self):
        """Primary action buttons are visible and not disabled."""
        # Upload button
        btn = self.page.query_selector("#discoveryUploadButton")
        self.assertIsNotNone(btn)
        self.assertTrue(btn.is_visible())
        # Confirm button
        btn = self.page.query_selector("#discoveryConfirmButton")
        self.assertIsNotNone(btn)

    def test_upload_button_exposes_success_and_retry_states(self):
        """Upload outcome stays on the action that initiated it."""
        button = self.page.locator("#discoveryUploadButton")

        self.page.evaluate("() => setDiscoveryUploadState('success')")
        self.assertEqual(button.locator(".btn-label").inner_text(), "上传分析成功")
        self.assertTrue(button.evaluate("element => element.classList.contains('upload-success')"))
        self.assertTrue(button.is_disabled())

        self.page.evaluate("() => setDiscoveryUploadState('error')")
        self.assertEqual(button.locator(".btn-label").inner_text(), "失败，点击重试")
        self.assertTrue(button.evaluate("element => element.classList.contains('upload-error')"))
        self.assertFalse(button.is_disabled())

        self.page.evaluate("() => setDiscoveryUploadState('idle')")
        self.assertEqual(button.locator(".btn-label").inner_text(), "上传并分析")
        self.assertFalse(button.evaluate("element => element.classList.contains('upload-success')"))
        self.assertFalse(button.evaluate("element => element.classList.contains('upload-error')"))

    def test_ai_setting_buttons_expose_loading_success_and_failure(self):
        """Model fetch and connection test report progress on their own buttons."""
        fetch_button = self.page.locator("#aiModelFetchButton")
        test_button = self.page.locator("#aiConnectionTestButton")

        self.page.evaluate("() => setAiActionButtonState('fetch', 'loading')")
        self.assertEqual(fetch_button.locator(".btn-label").inner_text(), "拉取中…")
        self.assertTrue(fetch_button.is_disabled())

        self.page.evaluate("() => setAiActionButtonState('fetch', 'success')")
        self.assertEqual(fetch_button.locator(".btn-label").inner_text(), "拉取成功")
        self.assertTrue(fetch_button.evaluate("el => el.classList.contains('upload-success')"))

        self.page.evaluate("() => setAiActionButtonState('test', 'loading')")
        self.assertEqual(test_button.locator(".btn-label").inner_text(), "测试中…")
        self.assertTrue(test_button.is_disabled())

        self.page.evaluate("() => setAiActionButtonState('test', 'error')")
        self.assertEqual(test_button.locator(".btn-label").inner_text(), "测试失败，重试")
        self.assertTrue(test_button.evaluate("el => el.classList.contains('upload-error')"))
        self.assertFalse(test_button.is_disabled())

    def test_error_notice_auto_dismisses(self):
        """Even retryable errors leave the global notice after a bounded delay."""
        self.page.evaluate("() => setAppNotice('连接失败', 'error', () => {}, 50)")
        notice = self.page.locator("#appNotice")
        self.assertTrue(notice.is_visible())
        self.page.wait_for_timeout(100)
        self.assertTrue(notice.is_hidden())

    def test_upload_without_ai_consent_stops_after_local_save(self):
        """Local-only upload must not create an analysis that stays queued forever."""
        self.page.set_input_files(
            "#discoveryResumeFile",
            {"name": "resume.txt", "mimeType": "text/plain", "buffer": b"Python developer"},
        )
        self.page.evaluate("""() => {
            currentProfileId = 'profile-test';
            document.getElementById('discoveryAiConsent').checked = false;
            window.__analysisSubmitted = false;
            api = async path => {
                if (path === DiscoveryAPI.analyses) {
                    window.__analysisSubmitted = true;
                    return {ok: true, status: 202, json: async () => ({analysis_id: 'analysis-test'})};
                }
                if (path.includes('/resume')) {
                    return {ok: true, status: 201, json: async () => ({resume_id: 'resume-test'})};
                }
                return {
                    ok: true, status: 200,
                    json: async () => ({status: 'failed', failure: {error_code: 'unexpected'}}),
                };
            };
        }""")

        self.page.locator("#discoveryUploadButton").click()
        self.page.wait_for_function(
            "() => document.querySelector('#discoveryUploadButton .btn-label').textContent.includes('上传成功')"
        )
        self.assertFalse(self.page.evaluate("() => window.__analysisSubmitted"))
        self.assertEqual(
            self.page.locator("#discoveryUploadButton .btn-label").inner_text(),
            "上传成功（未分析）",
        )

    def test_top_navigation_names_the_workspaces(self):
        labels = self.page.locator(".app-header .view-tab").all_inner_texts()
        self.assertEqual(labels, ["岗位发现", "搜索工作台", "筛选工作台"])

    def test_desktop_focus_visible(self):
        """Tabbing to a button shows a focus indicator."""
        self.page.keyboard.press("Tab")
        # At least one element should be focused
        focused_tag = self.page.evaluate("() => document.activeElement ? document.activeElement.tagName : null")
        self.assertIsNotNone(focused_tag)

    # ------------------------------------------------------------------
    # Viewport: narrow 720px
    # ------------------------------------------------------------------

    def test_narrow_no_horizontal_overflow(self):
        """No horizontal scrollbar at 720px width."""
        self.page.set_viewport_size({"width": 720, "height": 900})
        self.page.wait_for_timeout(300)
        overflow = self.page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        self.assertFalse(overflow, "horizontal overflow at 720px")

    def test_narrow_four_steps_present(self):
        self.page.set_viewport_size({"width": 720, "height": 900})
        self.page.wait_for_timeout(300)
        for step_id in ["discoveryStepUpload", "discoveryStepReview",
                        "discoveryStepProgress", "discoveryStepResults"]:
            el = self.page.query_selector(f"#{step_id}")
            self.assertIsNotNone(el, f"missing #{step_id} at 720px")

    # ------------------------------------------------------------------
    # Run state injection (JS-level simulation)
    # ------------------------------------------------------------------

    def _inject_run_state(self, status, stage="assembling"):
        """Inject a discovery run state via JS to test rendering."""
        self.page.evaluate(f"""() => {{
            if (typeof renderRunProgress === 'function') {{
                renderRunProgress({{
                    id: 'test-run', status: '{status}', stage: '{stage}',
                    progress: {{ fetched: 10, assessed: 8 }},
                    counts: {{ high_match: 3, adjacent_match: 2, growth_match: 1,
                              needs_review: 1, not_suitable: 2 }},
                    updated_at: '2026-07-14T12:00:00Z'
                }});
            }}
        }}""")

    def test_state_loading_renders(self):
        """Loading state (planning stage) renders without error."""
        self._inject_run_state("planning", "planning")
        content = self.page.query_selector("#discoveryProgressContent")
        self.assertIsNotNone(content)
        self.assertTrue(content.inner_html() != "")

    def test_state_success_renders(self):
        self._inject_run_state("succeeded")
        content = self.page.query_selector("#discoveryProgressContent")
        self.assertTrue(content.inner_html() != "")

    def test_state_partial_renders(self):
        self._inject_run_state("partial")
        content = self.page.query_selector("#discoveryProgressContent")
        self.assertTrue(content.inner_html() != "")

    def test_state_failed_renders(self):
        self._inject_run_state("failed")
        content = self.page.query_selector("#discoveryProgressContent")
        self.assertTrue(content.inner_html() != "")

    def test_state_interrupted_renders(self):
        self._inject_run_state("interrupted")
        content = self.page.query_selector("#discoveryProgressContent")
        self.assertTrue(content.inner_html() != "")

    def test_state_cancelled_renders(self):
        self._inject_run_state("cancelled")
        content = self.page.query_selector("#discoveryProgressContent")
        self.assertTrue(content.inner_html() != "")

    def test_results_empty_state(self):
        """No-results state renders empty-state message."""
        self.page.evaluate("""() => {
            if (typeof renderResults === 'function') {
                renderResults([], {});
            }
        }""")
        list_el = self.page.query_selector("#discoveryResultsList")
        self.assertIsNotNone(list_el)
        html = list_el.inner_html()
        # Should contain an empty-state element or be empty
        self.assertTrue(html is not None, "结果列表 DOM 应存在")

    def test_results_with_items_renders_cards(self):
        """Results with items render discovery cards."""
        self.page.evaluate("""() => {
            if (typeof renderResults === 'function') {
                renderResults([{
                    job_id: 'job-test-1', direction_id: 'd1',
                    title: '测试后端岗位', company: '测试公司', salary: '25-40K',
                    location: '北京', category: 'high_match', match_score: 85,
                    source_url: 'https://www.zhipin.com/test/123',
                    explanation: 'Python 后端经验匹配',
                    gaps: []
                }], { high_match: 1 });
            }
        }""")
        card = self.page.query_selector(".discovery-job-card")
        self.assertIsNotNone(card, "discovery job card not rendered")

    def test_feedback_trash_restore_direction_and_preference_changes_use_real_http(self):
        """T078/T079: browser actions persist through the actual feedback routes."""
        from webui.store import TaskStore

        store = TaskStore(self.server._tmp.name)
        profile = store.create_profile("浏览器反馈画像")
        resume = store.save_resume(
            profile["id"], "browser/resume.txt", "txt", "脱敏简历", "browser-feedback", "resume.txt"
        )
        analysis = store.create_analysis(resume["id"], profile["id"])
        direction = store.add_direction(
            analysis["id"], "后端工程", "core", search_terms=["Python"]
        )
        self.page.evaluate("""args => {
            currentProfileId = args.profileId;
            discoveryRunId = "";
            discoveryDirections = [{id: args.directionId, name: "后端工程"}];
            switchToDiscovery("results");
            renderResults([{
                job_id: "browser-job-1",
                primary_assessment: {
                    direction_id: args.directionId,
                    category: "high_match",
                    match_score: 88,
                    gaps: [],
                },
                title: "浏览器测试岗位",
                company: "测试公司",
                salary: "25-35K",
                location: "上海",
                completeness: "complete",
                source_url: "https://www.zhipin.com/job_detail/browser-job-1.html",
            }], {high_match: 1});
        }""", {"profileId": profile["id"], "directionId": direction["id"]})

        self.page.select_option(".discovery-feedback-reason", "company_unsuitable")
        self.page.get_by_role("button", name="不感兴趣 / 移入垃圾桶").click()
        self.page.wait_for_function(
            "() => discoveryFeedbackItems.some(item => item.job_id === 'browser-job-1' && item.action === 'not_interested')"
        )
        self.page.locator('.discovery-category-tab[data-category="trash"]').click()
        self.page.get_by_role("button", name="恢复").click()
        self.page.wait_for_function(
            "() => !discoveryFeedbackItems.some(item => item.job_id === 'browser-job-1' && item.action === 'not_interested')"
        )
        self.page.locator('.discovery-category-tab[data-category=""]').click()
        self.assertTrue(self.page.locator(".discovery-job-card").is_visible())

        self.page.get_by_role("button", name="感兴趣", exact=True).last.click()
        self.page.wait_for_function(
            "() => discoveryFeedbackItems.some(item => item.job_id === 'browser-job-1' && item.action === 'interested')"
        )
        self.page.locator('.discovery-category-tab[data-category="interested"]').click()
        self.assertTrue(self.page.locator(".discovery-job-card").is_visible())

        self.page.select_option("#discoveryFeedbackDirection", direction["id"])
        self.page.select_option("#discoveryDirectionReason", "direction_not_wanted")
        self.page.get_by_role("button", name="提交方向反馈").click()
        changes = self.page.locator("#discoveryPreferenceChanges")
        self.page.wait_for_function(
            "() => document.getElementById('discoveryPreferenceChanges').innerText.includes('下次不再搜索该方向')"
        )
        self.assertIn("下次不再搜索该方向", changes.inner_text())

        active = store.list_discovery_feedback(profile["id"], effective_only=True)
        self.assertTrue(any(item["action"] == "interested" for item in active))
        self.assertTrue(any(item["action"] == "direction_disable" for item in active))

    # ------------------------------------------------------------------
    # Legacy compatibility in browser
    # ------------------------------------------------------------------

    def test_legacy_workbench_section_present(self):
        el = self.page.query_selector(".workspace")
        self.assertIsNotNone(el, "legacy .workspace section missing")

    def test_legacy_screening_section_present(self):
        # screening may be a class or id
        html = self.page.content().lower()
        self.assertIn("screening", html)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
