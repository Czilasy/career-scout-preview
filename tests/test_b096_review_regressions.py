import pathlib
import tempfile
import threading
import unittest

from webui.flow_service import FlowService
from webui.store import TaskStore
from webui.store_flow import FlowConflictError


class B096ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-review-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "review-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'review', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _flow(self, selection="all", key="review-flow"):
        return self.store.create_flow(
            profile_id=self.profile_id,
            selection=selection,
            start_key=key,
            confirmed_filters={platform: {} for platform in ("boss", "zhilian") if selection == "all" or platform == selection},
        )

    def test_track_claim_allows_one_external_submission_under_race(self):
        flow = self._flow()
        barrier = threading.Barrier(2)
        outcomes = []

        def claim():
            barrier.wait(timeout=2)
            try:
                claimed = self.store.claim_flow_track_submission(
                    flow["id"], "boss", profile_id=self.profile_id,
                )
                outcomes.append(("claimed", claimed["id"]))
            except FlowConflictError:
                outcomes.append(("conflict", None))

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual([item[0] for item in outcomes].count("claimed"), 1)
        self.assertEqual([item[0] for item in outcomes].count("conflict"), 1)

    def test_terminal_track_cannot_be_resumed_or_paused_and_repeated_stop_is_idempotent(self):
        flow = self._flow(selection="boss", key="review-terminal")
        flow = self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id, status="running", stage="scrape",
        )
        flow = self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id, status="stopped", stage="scrape",
        )
        with self.assertRaises(FlowConflictError):
            self.store.update_flow_track(
                flow["id"], "boss", profile_id=self.profile_id, status="running",
            )
        service = FlowService(self.store)
        stopped = service.operate_track(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id, action="stop",
        )
        self.assertEqual(stopped["tracks"][0]["status"], "stopped")

    def test_completed_flow_remains_current_but_does_not_block_new_round(self):
        flow = self._flow(selection="boss", key="review-current")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id, status="running", stage="scrape",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id, status="done", stage="complete",
        )
        current = self.store.get_current_flow(self.profile_id)
        self.assertEqual(current["id"], flow["id"])
        self.assertFalse(self.store.flow_has_active_tracks(self.profile_id))

    def test_run_binding_rejects_same_profile_run_owned_by_another_flow(self):
        first = self._flow(selection="boss", key="review-owner-1")
        self.store.update_flow_track(
            first["id"], "boss", profile_id=self.profile_id, status="running", stage="scrape",
        )
        self.store.update_flow_track(
            first["id"], "boss", profile_id=self.profile_id, status="done", stage="complete",
        )
        second = self._flow(selection="zhilian", key="review-owner-2")
        run = self.store.save_pipeline_result(
            {"platform": "boss", "jobs": [], "dropped": [], "total_scraped": 0},
            {"platform": "boss"}, profile_id=self.profile_id,
            execution_params={"platform": "boss", "flow_id": first["id"]},
        )
        with self.assertRaises(ValueError):
            self.store.bind_flow_track_runs(
                second["id"], "zhilian", profile_id=self.profile_id, result_run_id=run,
            )

    def test_legacy_scrape_in_screen_slot_is_migrated_before_ai_binding(self):
        flow = self._flow(selection="boss", key="review-legacy-ai")
        old_scrape = self.store.create_screening_run(
            "legacy-scrape", profile_id=self.profile_id,
            execution_params={"platform": "boss"},
        )
        ai_run = self.store.create_screening_run(
            "new-ai", profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": flow["tracks"][0]["id"],
            },
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="scrape", screen_run_id=old_scrape["id"],
        )
        updated = FlowService(self.store).begin_ai(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            screen_run_id=ai_run["id"],
        )
        track = updated["tracks"][0]
        self.assertEqual(track["scrape_run_id"], old_scrape["id"])
        self.assertEqual(track["screen_run_id"], ai_run["id"])


if __name__ == "__main__":
    unittest.main()
