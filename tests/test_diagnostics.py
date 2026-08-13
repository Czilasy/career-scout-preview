import pathlib
import sys
import tempfile
import unittest

from webui.app import create_app
from webui.diagnostics import build_diagnostic_payload, record_failure


class _FakeStore:
    def __init__(self):
        self.events = []

    def append_task_event(self, task_id, event_type, payload=None):
        self.events.append({
            "task_id": str(task_id),
            "type": str(event_type),
            "payload": dict(payload or {}),
        })


class DiagnosticsUnitTests(unittest.TestCase):
    def test_record_failure_writes_structured_event(self):
        store = _FakeStore()
        payload = record_failure(
            store, "run-1",
            stage="ai_fine", error_code="ai_network_error",
            reason="AI 网络或服务故障", correlation_id="corr-1",
            diagnostics={"http_status": 502},
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["error_code"], "ai_network_error")
        self.assertEqual(payload["correlation_id"], "corr-1")
        self.assertEqual(store.events[0]["type"], "failure")
        self.assertEqual(store.events[0]["payload"]["stage"], "ai_fine")

    def test_record_failure_never_blocks_when_store_is_broken(self):
        class BrokenStore:
            def append_task_event(self, *_args, **_kwargs):
                raise RuntimeError("db down")

        result = record_failure(
            BrokenStore(), "run-1", stage="scrape",
            error_code="internal_error", reason="boom",
        )
        self.assertIsNone(result)

    def test_record_failure_includes_sanitized_exception_message(self):
        store = _FakeStore()
        try:
            raise RuntimeError("boom sk-abcdef1234567890")
        except RuntimeError as exc:
            payload = record_failure(
                store, "run-1", stage="scrape",
                error_code="internal_error", reason="boom",
                exception=exc, include_traceback=True,
            )
        self.assertIsNotNone(payload)
        diagnostics = payload["diagnostics"]
        self.assertEqual(diagnostics["exception_type"], "RuntimeError")
        self.assertIn("boom", diagnostics["exception_message"])
        self.assertNotIn("sk-abcdef1234567890", diagnostics["exception_message"])

    def test_build_diagnostic_payload_keeps_only_tail_events(self):
        events = [{"seq": i, "type": "failure"} for i in range(25)]
        payload = build_diagnostic_payload(
            run_id="run-1",
            run={"status": "failed", "current_stage": "scrape",
                 "error_code": "internal_error", "error_reason": "boom"},
            events=events,
            correlation_id="corr-1",
            next_action="需人工排查日志",
        )
        self.assertEqual(len(payload["events"]), 20)
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["next_action"], "需人工排查日志")


class DiagnosticsApiTests(unittest.TestCase):
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
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token

    def tearDown(self):
        self.temp.cleanup()

    def test_diagnostics_returns_safe_summary_for_run(self):
        store = self.app.config["TASK_STORE"]
        store.create_screening_run(
            "run-1",
            frozen_filters={},
            execution_params={"correlation_id": "corr-1", "platform": "boss"},
            backend_version="test",
        )
        store.append_task_event("run-1", "failure", {
            "stage": "ai_fine", "error_code": "ai_network_error",
            "reason": "AI 网络或服务故障", "correlation_id": "corr-1",
        })
        resp = self.client.get("/api/runs/run-1/diagnostics")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["correlation_id"], "corr-1")
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["type"], "failure")

    def test_diagnostics_defaults_correlation_id_to_run_id(self):
        store = self.app.config["TASK_STORE"]
        store.create_screening_run(
            "run-corr", frozen_filters={}, backend_version="test",
        )
        data = self.client.get("/api/runs/run-corr/diagnostics").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["correlation_id"], "run-corr")

    def test_diagnostics_returns_404_for_unknown_run(self):
        resp = self.client.get("/api/runs/nope/diagnostics")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error_code"], "not_found")


if __name__ == "__main__":
    unittest.main()
