import pathlib
import tempfile
import threading
import unittest

from webui.app_support import PlatformExecutionCapacity
from webui.flow_service import (
    FlowService,
    PlatformUnavailableError,
)
from webui.flow_task_coordinator import FlowTaskOperationError
from webui.store import TaskStore


class B096FlowServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-service-")
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.profile_id = "profile-service"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'service', '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (self.profile_id,),
            )

    def tearDown(self):
        if getattr(self, "capacity", None) is not None:
            self.capacity.shutdown(wait=True)
        self.temp.cleanup()

    def _submit_runs(self, platform, flow, track):
        scrape = self.store.create_search_run(
            self.profile_id,
            {"flow_id": flow["id"], "track_id": track["id"], "platform": platform},
            "ai",
        )
        screen = self.store.create_screening_run(
            f"screen-{platform}",
            profile_id=self.profile_id,
            execution_params={
                "platform": platform,
                "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        return {
            "scrape_run_id": scrape["id"],
            "screen_run_id": screen["id"],
            "result_run_id": screen["id"],
        }

    def test_all_start_submits_two_tracks_and_keeps_failure_on_one_line(self):
        calls = []

        def submit(platform, flow, track):
            calls.append(platform)
            if platform == "zhilian":
                raise RuntimeError("zhilian submit unavailable")
            return self._submit_runs(platform, flow, track)

        service = FlowService(
            self.store,
            submit_track=submit,
            platform_enabled=lambda _platform: True,
        )
        flow = service.start_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="service-all-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        self.assertEqual(calls, ["boss", "zhilian"])
        boss = next(track for track in flow["tracks"] if track["platform"] == "boss")
        zhilian = next(track for track in flow["tracks"] if track["platform"] == "zhilian")
        self.assertEqual(boss["status"], "running")
        self.assertEqual(boss["stage"], "scrape")
        self.assertEqual(zhilian["status"], "failed")
        self.assertEqual(zhilian["error_code"], "track_submit_failed")
        self.assertEqual(zhilian["reason"], "平台任务提交失败")
        with self.store._connection() as conn:
            event = conn.execute(
                "SELECT line FROM task_logs WHERE task_id = ? ORDER BY seq DESC LIMIT 1",
                (boss["screen_run_id"],),
            ).fetchone()
        self.assertIsNotNone(event)

    def test_disabled_platform_blocks_all_before_persisting_a_flow(self):
        service = FlowService(
            self.store,
            platform_enabled=lambda platform: platform == "boss",
        )
        with self.assertRaises(PlatformUnavailableError):
            service.start_flow(
                profile_id=self.profile_id,
                selection="all",
                start_key="service-disabled",
                confirmed_filters={"boss": {}, "zhilian": {}},
            )
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0], 0)

    def test_new_round_gate_covers_single_and_all_selections(self):
        service = FlowService(self.store, platform_enabled=lambda _platform: True)
        service.start_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="service-boss-active",
            confirmed_filters={"boss": {}},
        )
        with self.assertRaises(ValueError):
            service.start_flow(
                profile_id=self.profile_id,
                selection="all",
                start_key="service-all-blocked",
                confirmed_filters={"boss": {}, "zhilian": {}},
            )

    def test_track_operation_only_changes_target_line_and_records_submit_failure(self):
        service = FlowService(self.store, platform_enabled=lambda _platform: True)
        flow = service.start_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="service-operation",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        updated = service.operate_track(
            flow_id=flow["id"],
            platform="boss",
            profile_id=self.profile_id,
            action="pause",
        )
        self.assertEqual(
            next(item for item in updated["tracks"] if item["platform"] == "boss")["status"],
            "paused",
        )
        self.assertEqual(
            next(item for item in updated["tracks"] if item["platform"] == "zhilian")["status"],
            "queued",
        )

    def test_http_style_operation_failure_does_not_publish_track(self):
        service = FlowService(
            self.store,
            platform_enabled=lambda _platform: True,
            operate_track=lambda *_args: ({"ok": False}, 503),
        )
        flow = service.start_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="service-operation-http-failure",
            confirmed_filters={"boss": {}},
        )
        with self.assertRaises(FlowTaskOperationError):
            service.operate_track(
                flow_id=flow["id"], platform="boss",
                profile_id=self.profile_id, action="pause",
            )
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "queued",
        )

    def test_track_progress_advances_independently_and_duplicate_ai_start_is_rejected(self):
        service = FlowService(self.store, platform_enabled=lambda _platform: True)
        flow = service.start_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="service-progress",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        flow = service.mark_scrape_complete(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
        )
        boss = next(item for item in flow["tracks"] if item["platform"] == "boss")
        zhilian = next(item for item in flow["tracks"] if item["platform"] == "zhilian")
        self.assertEqual((boss["status"], boss["stage"]), ("running", "ai"))
        self.assertEqual((zhilian["status"], zhilian["stage"]), ("queued", "pending"))

        screen_run = self.store.create_screening_run(
            "screen-progress-boss",
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": next(
                    item["id"] for item in flow["tracks"]
                    if item["platform"] == "boss"
                ),
            },
        )
        flow = service.begin_ai(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            screen_run_id=screen_run["id"],
        )
        repeated = service.begin_ai(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            screen_run_id=screen_run["id"],
        )
        self.assertEqual(
            next(item for item in repeated["tracks"] if item["platform"] == "boss")["screen_run_id"],
            screen_run["id"],
        )

        result_run = self.store.save_pipeline_result(
            {"platform": "boss", "jobs": [], "dropped": [], "total_scraped": 0},
            {"platform": "boss"},
            profile_id=self.profile_id,
            execution_params={"platform": "boss", "flow_id": flow["id"]},
        )
        completed = service.mark_result_ready(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            result_run_id=result_run,
        )
        boss = next(item for item in completed["tracks"] if item["platform"] == "boss")
        zhilian = next(item for item in completed["tracks"] if item["platform"] == "zhilian")
        self.assertEqual((boss["status"], boss["stage"]), ("done", "complete"))
        self.assertEqual(zhilian["status"], "queued")

    def test_all_flow_submits_platform_callbacks_concurrently(self):
        self.capacity = PlatformExecutionCapacity()
        barrier = threading.Barrier(2)
        entered = []

        def submit(platform, flow, track):
            entered.append(platform)
            barrier.wait(timeout=2)
            return None

        service = FlowService(
            self.store,
            submit_track=submit,
            platform_enabled=lambda _platform: True,
            platform_executor=self.capacity,
        )
        flow = service.start_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="service-concurrent",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )

        self.assertEqual(set(entered), {"boss", "zhilian"})
        self.assertEqual(
            {track["status"] for track in flow["tracks"]}, {"running"},
        )


if __name__ == "__main__":
    unittest.main()
