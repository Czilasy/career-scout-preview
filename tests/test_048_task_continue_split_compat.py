"""Characterization gates for the 048 task-continue facade split."""

from __future__ import annotations

import ast
from concurrent.futures import Future
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from webui.app import create_app
from webui.flow_ai_coordinator import FlowAiCoordinator
from webui.flow_submission_service import FlowSubmissionService


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TaskContinueSplitCompatibilityTests(unittest.TestCase):
    def test_facade_and_extracted_python_modules_stay_below_hard_limit(self):
        facade = ROOT / "webui" / "task_continue_api.py"
        self.assertLess(len(facade.read_text(encoding="utf-8").splitlines()), 800)
        extracted = sorted((ROOT / "webui").glob("task_continue_*.py"))
        self.assertGreaterEqual(len(extracted), 3)
        for path in extracted:
            self.assertLess(
                len(path.read_text(encoding="utf-8").splitlines()), 800,
                path.name,
            )

    def test_routes_remain_registered_by_public_facade(self):
        source = "\n".join(
            (path.read_text(encoding="utf-8") for path in (
                ROOT / "webui" / "task_continue_api.py",
                ROOT / "webui" / "task_continue_finish.py",
            ))
        )
        tree = ast.parse(source)
        routes = {
            decorator.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "route"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        }
        self.assertTrue(any("/api/task/continue/" in route for route in routes))
        self.assertTrue(any("/api/task/pause/" in route for route in routes))
        self.assertTrue(any("/api/task/cancel/" in route for route in routes))
        self.assertTrue(any("/api/task/finish/" in route for route in routes))

    def test_b096_routes_use_service_boundaries(self):
        sources = {
            "flow": (ROOT / "webui" / "flow_api.py").read_text(encoding="utf-8"),
            "search": (ROOT / "webui" / "exec_search_api.py").read_text(encoding="utf-8"),
            "ai": (ROOT / "webui" / "ai_screen_api.py").read_text(encoding="utf-8"),
        }
        self.assertNotIn("service.store.", sources["flow"])
        write_methods = {
            "update_screening_run",
            "append_task_event",
            "claim_paused_screening_run",
            "update_search_run",
        }
        for key in ("search", "ai"):
            tree = ast.parse(sources[key])
            route_nodes = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name in {"execute_search", "ai_screen"}
            ]
            direct_writes = []
            for route in route_nodes:
                for call in ast.walk(route):
                    if not isinstance(call, ast.Call):
                        continue
                    func = call.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr in write_methods
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "store"
                        and isinstance(func.value.value, ast.Name)
                        and func.value.value.id == "ctx"
                    ):
                        direct_writes.append(func.attr)
            self.assertEqual(direct_writes, [], f"{key} route bypasses coordinator: {direct_writes}")


class B096FlowRouteBoundaryHttpTests(unittest.TestCase):
    """Exercise the real Flask routes with injected production coordinators."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-route-boundary-")
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.profile_id = "route-boundary-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'boundary', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_execute_search_flow_http_uses_injected_submission_coordinator(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="route-boundary-flow",
            confirmed_filters={"boss": {}},
        )
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        ctx = self.app.config["PIPELINE_CONTEXT"]
        real = FlowSubmissionService(ctx, ctx.flow_service)
        spy = mock.Mock(wraps=real)
        ctx.flow_submission_service = spy
        with mock.patch.object(self.app.config["PLATFORM_EXECUTION_CAPACITY"], "submit"):
            response = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "flow_id": flow["id"],
                "profile_id": self.profile_id,
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        spy.claim_track.assert_called_once()
        spy.create_scrape_records.assert_called_once()
        spy.begin_whitebox.assert_called_once()
        spy.submit_scrape.assert_called_once()

    def test_execute_search_submit_failure_reports_atomic_compensation_error(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="route-boundary-submit-state-failure",
            confirmed_filters={"boss": {}},
        )
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        with self.store._connection() as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_route_boundary_search_close
                BEFORE UPDATE OF status ON search_runs
                BEGIN
                    SELECT RAISE(ABORT, 'injected search close failure');
                END
                """
            )
        capacity = self.app.config["PLATFORM_EXECUTION_CAPACITY"]
        with mock.patch.object(capacity, "submit", side_effect=RuntimeError("private detail")):
            response = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "flow_id": flow["id"],
                "profile_id": self.profile_id,
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
                "scope_digest": preview["scope_digest"],
            })
        payload = response.get_json()
        self.assertEqual(response.status_code, 503, payload)
        self.assertEqual(payload["error_code"], "flow_failure_compensation_failed")
        self.assertNotIn("private detail", response.get_data(as_text=True))
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "running")
        self.assertEqual(self.store.get_screening_run(track["scrape_run_id"])["status"], "running")
        self.assertEqual(self.store.get_search_run(track["scrape_run_id"])["status"], "queued")

    def test_execute_search_attaches_failed_lane_future_to_flow_state(self):
        """初次 Flow 提交的已失败 Future 也必须触发统一状态收口。"""
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="route-boundary-future-failure",
            confirmed_filters={"boss": {}},
        )
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        failed_future = Future()
        failed_future.set_exception(RuntimeError("private lane failure"))
        capacity = self.app.config["PLATFORM_EXECUTION_CAPACITY"]
        with mock.patch.object(capacity, "submit", return_value=failed_future):
            response = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "flow_id": flow["id"],
                "profile_id": self.profile_id,
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "failed")
        self.assertEqual(track["error_code"], "submit_failed")
        task_id = track["scrape_run_id"]
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")
        self.assertEqual(self.store.get_search_run(task_id)["status"], "failed")
        with self.app.config["PIPELINE_CONTEXT"].lock:
            task = self.app.config["PIPELINE_CONTEXT"].tasks[task_id]
        self.assertEqual(task["status"], "failed")

    def test_ai_screen_flow_http_uses_injected_ai_coordinator(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="route-boundary-ai-flow",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape_id = f"{flow['id']}-scrape"
        self.store.create_screening_run(
            scrape_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
                "browser_account": "test-account",
                "cdp_port": 9222,
                "profile_key": "flow-boundary",
            },
        )
        self.store.create_scrape_search_run(
            scrape_id,
            self.profile_id,
            platform="boss",
            flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_id,
        )
        self.store.update_screening_run(scrape_id, status="running")
        self.store.update_screening_run(scrape_id, status="succeeded")
        ctx = self.app.config["PIPELINE_CONTEXT"]
        ctx.ensure_scrape_source = lambda _run_id: {
            "kind": "scrape",
            "status": "done",
            "platform": "boss",
            "result": {"ok": True, "jobs": []},
        }
        real = FlowAiCoordinator(ctx)
        spy = mock.Mock(wraps=real)
        spy.submit_flow = mock.Mock(return_value=None)
        ctx.flow_ai_coordinator = spy
        with mock.patch(
            "webui.ai_screen_api.activate_frozen_identity_candidate",
            return_value={"ok": True},
        ):
            response = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "platform": "boss",
                "screening_fields": {"salary": ["405"]},
                "profile_summary": "boundary",
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        spy.create_screening_run.assert_called_once()
        spy.claim_track.assert_called_once()
        spy.begin_whitebox.assert_called_once()
        spy.submit_flow.assert_called_once()

    def test_ai_flow_failure_reports_atomic_compensation_error_without_stage_typeerror(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="route-boundary-ai-state-failure",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape_id = f"{flow['id']}-scrape"
        self.store.create_screening_run(
            scrape_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
                "browser_account": "test-account",
                "cdp_port": 9222,
                "profile_key": "flow-boundary",
            },
        )
        self.store.create_scrape_search_run(
            scrape_id,
            self.profile_id,
            platform="boss",
            flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="scrape",
        )
        self.store.update_screening_run(scrape_id, status="running")
        self.store.update_screening_run(scrape_id, status="succeeded")
        with self.store._connection() as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_route_boundary_ai_search_close
                BEFORE UPDATE OF status ON search_runs
                BEGIN
                    SELECT RAISE(ABORT, 'injected AI search close failure');
                END
                """
            )
        ctx = self.app.config["PIPELINE_CONTEXT"]
        ctx.ensure_scrape_source = lambda _run_id: {
            "kind": "scrape",
            "status": "done",
            "platform": "boss",
            "result": {"ok": True, "jobs": []},
        }
        ctx.claim_pipeline_task_id = mock.Mock(
            side_effect=RuntimeError("private claim detail"),
        )
        with mock.patch(
            "webui.ai_screen_api.activate_frozen_identity_candidate",
            return_value={"ok": True},
        ):
            response = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "platform": "boss",
                "screening_fields": {"salary": ["405"]},
                "profile_summary": "boundary",
            })
        payload = response.get_json()
        self.assertEqual(response.status_code, 503, payload)
        self.assertEqual(payload["error_code"], "flow_failure_compensation_failed")
        self.assertNotIn("TypeError", response.get_data(as_text=True))
        self.assertNotIn("private claim detail", response.get_data(as_text=True))
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "running",
        )
        self.assertEqual(self.store.get_screening_run(scrape_id)["status"], "succeeded")
        self.assertEqual(self.store.get_search_run(scrape_id)["status"], "queued")
