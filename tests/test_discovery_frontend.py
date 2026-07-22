"""Source contracts for the Vue-based four-step discovery workspace.

Interactive behavior is covered by Vitest in ``webui/src``.  These tests keep
Python's normal regression suite aware of the production frontend boundary
without coupling it to Vite's minified bundle text.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"
DISCOVERY = (WEBUI / "src" / "views" / "DiscoveryView.vue").read_text(encoding="utf-8")
STEPS = (WEBUI / "src" / "components" / "StepNavigator.vue").read_text(encoding="utf-8")
JOBS = (WEBUI / "src" / "components" / "JobWorkspace.vue").read_text(encoding="utf-8")
HELPERS = (WEBUI / "src" / "discovery.ts").read_text(encoding="utf-8")
INDEX = (WEBUI / "index.html").read_text(encoding="utf-8")


class VueDiscoveryFrontendTests(unittest.TestCase):
    def test_vite_entry_replaces_legacy_inline_application(self):
        self.assertIn('<div id="app"></div>', INDEX)
        self.assertIn('src="/src/main.ts"', INDEX)
        self.assertNotIn("function renderAnalysis", INDEX)
        self.assertNotIn("onclick=", INDEX)

    def test_four_steps_keep_the_business_order(self):
        order = ["上传简历", "广泛抓取", "AI 筛选", "查看结果"]
        positions = [DISCOVERY.index(label) for label in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(':disabled="!enabled.has(step.id)"', STEPS)
        self.assertIn('aria-current', STEPS)

    def test_resume_requires_explicit_ai_consent(self):
        self.assertIn('data-testid="resume-consent"', DISCOVERY)
        self.assertIn("if (!aiConsent.value)", DISCOVERY)
        self.assertIn('data-testid="analyze-resume"', DISCOVERY)
        self.assertIn('accept=".txt,.pdf,.docx"', DISCOVERY)

    def test_scrape_and_ai_screen_are_separate_requests(self):
        self.assertIn('"/api/execute-search"', DISCOVERY)
        self.assertIn('"/api/ai-screen"', DISCOVERY)
        self.assertIn('data-testid="start-scrape"', DISCOVERY)
        self.assertIn('data-testid="start-ai-screen"', DISCOVERY)
        self.assertIn("scrapeCompleted.value", DISCOVERY)

    def test_broad_scrape_uses_only_keywords_and_cities(self):
        self.assertIn("buildSearchScriptParams", DISCOVERY)
        self.assertIn('keyword: uniqueNonEmpty(keywords).join(",")', HELPERS)
        self.assertIn("city: uniqueNonEmpty(cities)", HELPERS)
        self.assertIn("filters: {}", HELPERS)

    def test_ai_screen_is_bound_to_the_completed_scrape_task(self):
        self.assertIn("scrape_task_id: scrapeTaskId.value", DISCOVERY)
        self.assertIn("!scrapeCompleted.value || !scrapeTaskId.value", DISCOVERY)

    def test_stage_b_failures_have_a_visible_uncertain_zone(self):
        self.assertIn('id: "uncertain" as const', DISCOVERY)
        self.assertIn('label: "待确认"', DISCOVERY)
        self.assertIn('job.verdict === "match"', HELPERS)
        self.assertIn('job.verdict === "not_match"', HELPERS)
        self.assertIn("groups.uncertain.push(job)", HELPERS)

    def test_large_results_render_in_batches_with_one_detail_panel(self):
        self.assertIn("props.jobs.slice(0, visibleCount.value)", JOBS)
        self.assertIn('data-testid="load-more"', JOBS)
        self.assertEqual(JOBS.count('data-testid="job-detail"'), 1)

    def test_feedback_preserves_persistence_boundary(self):
        self.assertIn('"/api/pipeline/jobs/interest"', DISCOVERY)
        self.assertIn('"/api/pipeline/jobs/interest/cancel"', DISCOVERY)
        self.assertIn("rejectedIds.value", DISCOVERY)
        self.assertIn("仅本轮有效", DISCOVERY)
        self.assertNotIn('"/api/pipeline/jobs/reject"', DISCOVERY)

    def test_jd_retry_does_not_restart_ai_screen(self):
        self.assertIn("/api/pipeline/jobs/${encodeURIComponent(id)}/jd", DISCOVERY)
        self.assertIn("原 AI 判定保持不变", DISCOVERY)

    def test_refresh_can_offer_latest_result_without_skipping_upload_step(self):
        self.assertIn('const activeStep = ref<StepId>("upload")', DISCOVERY)
        self.assertIn("loadLatestResult()", DISCOVERY)
        self.assertIn("resultLoaded.value = true", DISCOVERY)


if __name__ == "__main__":
    unittest.main()
