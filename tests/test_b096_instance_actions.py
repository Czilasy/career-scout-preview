"""046 V2 instance action contracts; isolated SQLite and HTTP test client."""
import unittest
import threading
from types import SimpleNamespace
from unittest import mock
from flask import request
from webui.flow_task_coordinator import operate_bound_task
from webui.task_pause_support import pause_with_mode
from tests import test_b096_flow_api as fixtures


class InstanceActionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.B096FlowApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store = self.fixture.store
        self.profile = self.fixture.profile_id

    def seed(self, error_code="source_login_required", status="failed"):
        flow = self.store.create_flow(
            profile_id=self.profile, selection="all", start_key="instance-actions",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        track = flow["tracks"][0]
        run_id = "instance-screen"
        self.store.create_screening_run(run_id, profile_id=self.profile, execution_params={
            "flow_id": flow["id"], "track_id": track["id"], "platform": track["platform"],
        })
        self.store.bind_flow_track_runs(flow["id"], track["platform"], profile_id=self.profile, screen_run_id=run_id)
        self.store.update_screening_run(run_id, status="running", current_stage="ai_rough")
        self.store.update_screening_run(run_id, status=status, current_stage="ai_rough", error_code=error_code)
        self.store.update_flow_track(flow["id"], track["platform"], profile_id=self.profile, status=status, stage="ai")
        return flow, track, run_id

    def action(self, flow, track, action, **extra):
        return self.fixture.client.post(
            f"/api/flows/{flow['id']}/tracks/{track['platform']}/{action}",
            json={"profile_id": self.profile, **extra},
        )

    def test_paused_resume_reuses_callback_and_preserves_sibling(self):
        flow, track, run_id = self.seed(status="paused")
        calls = []
        self.fixture.service.operate_track_callback = lambda *args: calls.append(args) or True
        response = self.action(flow, track, "resume", expected_run_id=run_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(calls), 1)
        current = response.get_json()["flow"]["tracks"]
        self.assertEqual(next(t for t in current if t["id"] == track["id"])["status"], "running")
        self.assertEqual(next(t for t in current if t["id"] != track["id"])["status"], "queued")

    def test_terminal_failed_resume_is_rejected_before_callback(self):
        flow, track, run_id = self.seed("unrecoverable_test_failure")
        calls = []
        self.fixture.service.operate_track_callback = lambda *args: calls.append(args) or True
        self.assertEqual(self.action(flow, track, "resume", expected_run_id=run_id).status_code, 409)
        self.assertEqual(calls, [])

    def test_terminal_failed_stop_is_rejected_before_callback(self):
        flow, track, run_id = self.seed("unrecoverable_test_failure")
        calls = []
        self.fixture.service.operate_track_callback = lambda *args: calls.append(args) or True
        response = self.action(flow, track, "stop", expected_run_id=run_id)
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(calls, [])

    def test_old_stage_click_cannot_operate_current_ai_run(self):
        flow, track, _ = self.seed(status="running")
        calls = []
        self.fixture.service.operate_track_callback = lambda *args: calls.append(args) or True
        response = self.action(flow, track, "pause", expected_run_id="old-scrape")
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(calls, [])

    def test_flow_immediate_pause_reuses_task_pause_guard_for_exact_run(self):
        flow, track, run_id = self.seed(status="running")
        event = threading.Event()
        task = {"kind": "ai_screen", "status": "running", "stop_event": event}
        sibling_event = threading.Event()
        ctx = SimpleNamespace(store=self.store, lock=threading.RLock(), tasks={
            run_id: task, "sibling-run": {"status": "running", "stop_event": sibling_event},
        }, pipeline_guard=mock.Mock(), operational_errors=(RuntimeError,))
        self.fixture.service.operate_track_callback = lambda action, platform, current, target: operate_bound_task(ctx, action, platform, current, target)
        self.fixture.app.add_url_rule("/api/task/pause/<run_id>", "api_task_pause",
            lambda run_id: pause_with_mode(ctx, run_id, str((request.get_json() or {}).get("mode") or "graceful")), methods=["POST"])
        response = self.action(flow, track, "pause", expected_run_id=run_id, mode="immediate")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(event.is_set())
        self.assertTrue(event.immediate)
        ctx.pipeline_guard.immediate_stop_task.assert_called_once_with(run_id)
        self.assertFalse(sibling_event.is_set())
