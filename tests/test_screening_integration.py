"""HTTP integration tests for formal pending/recovery/cleanup workflows."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_screening_fixtures import sample_screening_job
from webui.app import create_app


class ScreeningResilienceAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.result_dir = root / "results"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.result_dir),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.profile = self.store.create_profile("integration")

    def tearDown(self):
        self.temp.cleanup()

    def _write_artifact(self, run_id, jobs):
        self.result_dir.mkdir(parents=True, exist_ok=True)
        (self.result_dir / f"screening_{run_id}.json").write_text(
            json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8",
        )

    def _fake_execute(self, jobs):
        def execute(filters, keyword, *, output_path, python_executable, store, run_id):
            self._write_artifact(run_id, jobs)
            store.update_screening_run_status(run_id, "running", source_count=len(jobs))
            return {"jobs": jobs, "source_count": len(jobs), "status": "running"}
        return execute

    def test_create_run_persists_pending_and_marks_run_partial(self):
        jobs = [sample_screening_job(job_id="m1"), sample_screening_job(job_id="p1")]
        partition = {
            "match": [jobs[0]], "mismatch": [], "pending": [jobs[1]],
            "pending_failures": {"p1": "ai_timeout"},
        }
        with mock.patch("webui.app.execute_first_layer", side_effect=self._fake_execute(jobs)):
            with mock.patch("webui.app.partition_jobs", return_value=partition):
                response = self.client.post("/api/screening/runs", json={
                    "keyword": "Python", "filters": {},
                })
        body = response.get_json()
        self.assertEqual(body["status"], "partial")
        self.assertEqual(body["pending_count"], 1)
        pending = self.client.get(
            f"/api/screening/runs/{body['run_id']}/pending"
        ).get_json()
        self.assertEqual(pending["items"][0]["job_id"], "p1")
        self.assertEqual(pending["items"][0]["failure_stage"], "ai_timeout")
        self.assertNotIn("ai_payload", pending["items"][0])

    def test_manual_route_moves_pending_to_selected_result(self):
        run = self.store.create_screening_run({})
        self._write_artifact(run["id"], [sample_screening_job(job_id="p1")])
        self.store.add_pending_result(run["id"], "p1", "ai_uncertain")
        response = self.client.post(
            f"/api/screening/runs/{run['id']}/pending/p1/route",
            json={"target": "mismatch"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.list_pending(run["id"]), [])
        self.assertEqual(
            self.store.get_screening_results(run["id"], "mismatch")[0]["job_id"],
            "p1",
        )

    def test_retry_all_skips_non_retryable_pending(self):
        run = self.store.create_screening_run({})
        self._write_artifact(run["id"], [
            sample_screening_job(job_id="p1"), sample_screening_job(job_id="p2"),
        ])
        self.store.add_pending_result(run["id"], "p1", "ai_timeout", retryable=True)
        self.store.add_pending_result(run["id"], "p2", "verification_error", retryable=False)
        with mock.patch("webui.app.partition_job", return_value="match"):
            response = self.client.post(
                f"/api/screening/runs/{run['id']}/pending/retry-all", json={},
            )
        body = response.get_json()
        self.assertEqual(body["retried"], 1)
        self.assertEqual(body["skipped"], 1)
        self.assertEqual([item["job_id"] for item in self.store.list_pending(run["id"])], ["p2"])

    def test_cancel_preserves_already_saved_results(self):
        run = self.store.create_screening_run({})
        self.store.update_screening_run_status(run["id"], "running")
        self.store.add_screening_result(run["id"], "m1", "match")
        response = self.client.post(f"/api/screening/runs/{run['id']}/cancel")
        self.assertEqual(response.get_json()["status"], "interrupted")
        self.assertEqual(len(self.store.get_screening_results(run["id"])), 1)

    def test_trash_restore_returns_recorded_origin_zone(self):
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/abc.html", "", "Python", "ACME", "20-30K", "上海", "JD",
        )
        self.store.link_profile_job(self.profile["id"], job["id"], None, None)
        self.store.mark_screening_reject(self.profile["id"], job["id"])
        self.store.move_to_trash_with_origin(self.profile["id"], job["id"], "pending")
        response = self.client.post(
            f"/api/screening/trash/{job['id']}/restore",
            json={"profile_id": self.profile["id"]},
        )
        self.assertEqual(response.get_json()["restored_to"], "pending")

    def test_cleanup_preview_warns_and_execution_records_result(self):
        preview = self.client.get("/api/screening/cleanup/preview?days=30")
        self.assertEqual(preview.status_code, 200)
        run = self.client.post("/api/screening/cleanup", json={"days": 30})
        self.assertEqual(run.status_code, 200)
        history = self.client.get("/api/screening/cleanup/history").get_json()
        self.assertEqual(len(history["items"]), 1)
        self.assertEqual(history["items"][0]["scope"], "screening_temp_30d")


if __name__ == "__main__":
    unittest.main()
