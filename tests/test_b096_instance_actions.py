"""046 V2 instance action contracts; isolated SQLite and HTTP test client."""
import unittest
import threading
from types import SimpleNamespace
from unittest import mock
from flask import Flask, jsonify, request
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

    def check_cold_paused_stop(self, kind):
        from webui.flow_api import register_flow_routes
        flow = self.store.create_flow(profile_id=self.profile, selection="all",
                                      confirmed_filters={"boss": {}, "zhilian": {}}, start_key="cold-stop")
        track = flow["tracks"][0]
        run_id = (self.store.create_search_run(self.profile, {"platform": track["platform"], "flow_id": flow["id"], "track_id": track["id"]}, "balanced")["id"]
                  if kind == "scrape" else "cold-screen")
        self.store.create_screening_run(run_id, profile_id=self.profile, execution_params={
            "flow_id": flow["id"], "track_id": track["id"], "platform": track["platform"],
        })
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused", error_code="source_login_required")
        if kind == "scrape":
            self.store.update_search_run(run_id, status="interrupted")
        self.store.update_flow_track(flow["id"], track["platform"], profile_id=self.profile,
                                    status="paused", stage="scrape" if kind == "scrape" else "ai",
                                    **{"scrape_run_id" if kind == "scrape" else "screen_run_id": run_id})
        app = Flask(__name__)
        ctx = SimpleNamespace(store=self.store, flow_service=self.fixture.service, tasks={}, lock=threading.RLock())
        self.fixture.service.operate_track_callback = None
        register_flow_routes(app, ctx)
        response = app.test_client().post(
            f"/api/flows/{flow['id']}/tracks/{track['platform']}/stop",
            json={"profile_id": self.profile, "expected_track_id": track["id"], "expected_run_id": run_id},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        tracks = response.get_json()["flow"]["tracks"]
        self.assertEqual(next(t for t in tracks if t["id"] == track["id"])["status"], "stopped")
        self.assertEqual(next(t for t in tracks if t["id"] != track["id"])["status"], "queued")
        self.assertEqual(self.store.get_screening_run(run_id)["interruption_kind"], "user_cancelled")
        if kind == "scrape":
            self.assertEqual(self.store.get_search_run(run_id)["status"], "interrupted")

    def test_cold_paused_scrape_can_stop_without_a_live_worker(self):
        self.check_cold_paused_stop("scrape")

    def test_cold_paused_ai_can_stop_without_a_live_worker(self):
        self.check_cold_paused_stop("ai_screen")

    def check_resume_rejection_reason(self, platform, kind):
        from webui.flow_api import register_flow_routes
        from webui.error_registry import ERROR_USER_MESSAGES
        flow = self.store.create_flow(profile_id=self.profile, selection="all",
                                      confirmed_filters={"boss": {}, "zhilian": {}}, start_key="resume-reason")
        track = next(t for t in flow["tracks"] if t["platform"] == platform)
        run_id = (self.store.create_search_run(self.profile, {"platform": platform}, "balanced")["id"]
                  if kind == "scrape" else "resume-reason-run")
        self.store.create_screening_run(run_id, profile_id=self.profile, execution_params={
            "flow_id": flow["id"], "track_id": track["id"], "platform": platform,
        })
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused")
        self.store.update_flow_track(flow["id"], platform, profile_id=self.profile,
                                    status="paused", stage="ai" if kind == "ai_screen" else "scrape",
                                    **{"screen_run_id" if kind == "ai_screen" else "scrape_run_id": run_id})
        code = "source_login_required"
        app = Flask(__name__)
        calls = []
        def continuation(*args):
            calls.append(args)
            return jsonify({"ok": False, "error_code": code,
                            "error_reason": "private diagnostic must not reach the user"}), 409
        ctx = SimpleNamespace(store=self.store, flow_service=self.fixture.service,
                              tasks={run_id: {"kind": kind, "status": "paused"}}, lock=threading.RLock(),
                              continue_execute_search=continuation)
        app.add_url_rule("/api/task/continue/<run_id>", "api_task_continue", continuation, methods=["POST"])
        self.fixture.service.operate_track_callback = None
        register_flow_routes(app, ctx)
        for code in ("source_login_required", "source_status_unclear", "source_cdp_unavailable"):
            with self.subTest(code=code):
                response = app.test_client().post(
                    f"/api/flows/{flow['id']}/tracks/{platform}/resume",
                    json={"profile_id": self.profile, "expected_run_id": run_id})
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(response.get_json()["error_code"], code)
                self.assertEqual(response.get_json()["message"], ERROR_USER_MESSAGES[code])
                self.assertNotIn("private diagnostic", response.get_data(as_text=True))
                current = self.store.get_flow(flow["id"], profile_id=self.profile)
                self.assertEqual(next(t for t in current["tracks"] if t["id"] == track["id"])["status"], "paused")
                self.assertEqual(next(t for t in current["tracks"] if t["id"] != track["id"])["status"], "queued")
        self.assertEqual(len(calls), 3)
        self.assertIs(ctx.continue_execute_search, continuation)

    def test_scrape_resume_preserves_fresh_rejection_reason(self):
        self.check_resume_rejection_reason("zhilian", "scrape")

    def test_ai_resume_preserves_fresh_rejection_reason(self):
        self.check_resume_rejection_reason("boss", "ai_screen")

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

    def test_failed_track_retry_action_is_reachable_but_old_resume_still_blocked(self):
        """047 C2：旧 resume/stop 拒绝保留；failed 改为走独立 retry 出口。"""
        flow, track, run_id = self.seed("unrecoverable_test_failure")
        calls = []
        self.fixture.service.operate_track_callback = lambda *args: calls.append(args) or True
        resume_response = self.action(flow, track, "resume", expected_run_id=run_id)
        stop_response = self.action(flow, track, "stop", expected_run_id=run_id)
        self.assertEqual(resume_response.status_code, 409)
        self.assertEqual(stop_response.status_code, 409)
        self.assertEqual(calls, [])
        # retry 不再是普通 resume；缺提交上下文时必须给出安全响应，不能假成功。
        retry_response = self.action(flow, track, "retry", expected_run_id=run_id)
        self.assertIn(retry_response.status_code, (409, 503))
        self.assertFalse(retry_response.get_json().get("ok", True))

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
