"""Source contracts for the Vue screening workbench."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
VIEW = (ROOT / "webui" / "src" / "views" / "ScreeningView.vue").read_text(encoding="utf-8")
JOBS = (ROOT / "webui" / "src" / "components" / "JobWorkspace.vue").read_text(encoding="utf-8")
CSS = (ROOT / "webui" / "src" / "styles.css").read_text(encoding="utf-8")


class VueScreeningFrontendTests(unittest.TestCase):
    def test_seven_filter_fields_and_execution_limits_are_present(self):
        for field in ("city", "salary", "experience", "degree", "scale", "stage", "industry"):
            with self.subTest(field=field):
                self.assertIn(f'{field}: "', VIEW)
        self.assertIn('data-testid="screening-keyword"', VIEW)
        self.assertIn("v-model.number=\"pages\"", VIEW)
        self.assertIn("v-model.number=\"maxDetails\"", VIEW)

    def test_resume_suggestion_and_manual_degradation_are_both_available(self):
        self.assertIn('"/api/screening/resume"', VIEW)
        self.assertIn('"/api/screening/resume/suggest"', VIEW)
        self.assertIn("ai_unavailable", VIEW)
        self.assertIn("跳过简历，手动填筛", VIEW)

    def test_five_zones_are_semantic_tabs(self):
        for zone in ("match", "mismatch", "pending", "interested", "trash"):
            with self.subTest(zone=zone):
                self.assertIn(f'{zone}: "', VIEW)
        self.assertIn('role="tablist"', VIEW)
        self.assertIn('role="tab"', VIEW)
        self.assertIn(':data-zone-tab="zone.id"', VIEW)

    def test_pending_retry_and_manual_routing_use_existing_endpoints(self):
        self.assertIn("/pending/${encodeURIComponent(id)}/retry", VIEW)
        self.assertIn("/pending/${encodeURIComponent(id)}/route", VIEW)
        self.assertIn("/pending/retry-all", VIEW)
        self.assertIn("人工通过", VIEW)
        self.assertIn("人工不通过", VIEW)

    def test_run_cancel_resume_and_profile_scoped_restore_are_preserved(self):
        self.assertIn("/cancel", VIEW)
        self.assertIn("/resume", VIEW)
        self.assertIn('localStorage.setItem("boss-screening-run"', VIEW)
        self.assertIn("saved.profile_id !== props.profileId", VIEW)

    def test_long_term_zones_use_the_persistent_apis(self):
        self.assertIn("/api/screening/interested?profile_id=", VIEW)
        self.assertIn("/api/screening/trash?profile_id=", VIEW)
        self.assertIn("/api/screening/trash/${encodeURIComponent(id)}/restore", VIEW)

    def test_cleanup_uses_a_real_confirmation_dialog(self):
        self.assertIn("<BaseDialog", VIEW)
        self.assertIn("/api/screening/cleanup/preview?days=30", VIEW)
        self.assertIn('"/api/screening/cleanup"', VIEW)

    def test_results_share_the_batched_list_detail_component(self):
        self.assertIn("<JobWorkspace", VIEW)
        self.assertIn("visibleJobs", JOBS)
        self.assertIn("job-detail-pane", JOBS)

    def test_responsive_styles_cover_controls_and_result_workspace(self):
        self.assertIn("@media (max-width: 760px)", CSS)
        self.assertIn(".screening-filter-grid", CSS)
        self.assertIn(".screening-zone-tabs", CSS)
        self.assertIn(".job-detail-pane", CSS)


if __name__ == "__main__":
    unittest.main()
