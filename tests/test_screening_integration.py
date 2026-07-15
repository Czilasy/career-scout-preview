"""HTTP integration tests for formal pending/recovery/cleanup workflows."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

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
        def execute(filters, keyword, *, output_path, python_executable, store, run_id,
                    **_execution_outputs):
            self._write_artifact(run_id, jobs)
            store.update_screening_run_status(run_id, "running", source_count=len(jobs))
            return {"jobs": jobs, "source_count": len(jobs), "status": "running"}
        return execute

    def test_create_run_persists_pending_and_marks_run_partial(self):
        jobs = [sample_screening_job(job_id="m1"), sample_screening_job(job_id="p1")]
        def partition(batch, *_args, **_kwargs):
            job = batch[0]
            if job["job_id"] == "m1":
                return {"match": [job], "mismatch": [], "pending": [], "pending_failures": {}}
            return {"match": [], "mismatch": [], "pending": [job],
                    "pending_failures": {"p1": "ai_timeout"}}
        with mock.patch("webui.app.execute_first_layer", side_effect=self._fake_execute(jobs)):
            with mock.patch("webui.app.partition_jobs", side_effect=partition):
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

    def test_pending_retry_forwards_configured_model(self):
        """Retry uses the same configured model as the initial semantic check."""
        resume = self.store.save_resume(
            self.profile["id"], "resume.txt", "txt", "Python FastAPI", "resume-hash",
        )
        run = self.store.create_screening_run({}, resume_id=resume["id"])
        self._write_artifact(run["id"], [sample_screening_job(job_id="p1")])
        self.store.add_pending_result(run["id"], "p1", "ai_timeout", retryable=True)
        self.store.save_ai_settings(
            "https://api.example.com/v1/chat/completions", "api.example.com",
            model="deepseek-v4-flash-free",
        )
        with mock.patch("webui.ai.keyring.get_password", return_value="test-key"):
            with mock.patch("webui.app.partition_job", return_value="match") as partition:
                response = self.client.post(
                    f"/api/screening/runs/{run['id']}/pending/p1/retry", json={},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            partition.call_args.kwargs["semantic_options"]["model"],
            "deepseek-v4-flash-free",
        )

    def test_cancel_preserves_already_saved_results(self):
        run = self.store.create_screening_run({})
        self.store.update_screening_run_status(run["id"], "running")
        self.store.add_screening_result(run["id"], "m1", "match")
        response = self.client.post(f"/api/screening/runs/{run['id']}/cancel")
        self.assertEqual(response.get_json()["status"], "interrupted")
        self.assertEqual(len(self.store.get_screening_results(run["id"])), 1)

    def test_resume_uses_saved_artifact_and_skips_already_processed_jobs(self):
        jobs = [
            sample_screening_job(job_id="saved"),
            sample_screening_job(job_id="remaining"),
        ]
        run = self.store.create_screening_run(
            {}, execution={"keyword": "Python", "pages": 1},
        )
        self._write_artifact(run["id"], jobs)
        self.store.update_screening_run_status(run["id"], "running")
        self.store.add_screening_result(run["id"], "saved", "match")
        self.store.update_screening_run_status(
            run["id"], "interrupted", source_count=2,
            match_count=1, processed_count=1, source_cursor=2,
            error_code="cancelled",
        )

        partition = {
            "match": [], "mismatch": [jobs[1]], "pending": [],
            "pending_failures": {},
        }
        with mock.patch("webui.app.execute_first_layer") as execute:
            with mock.patch("webui.app.partition_jobs", return_value=partition):
                response = self.client.post(
                    f"/api/screening/runs/{run['id']}/resume", json={},
                )

        self.assertEqual(response.status_code, 200)
        execute.assert_not_called()
        final = self.store.get_screening_run(run["id"])
        self.assertEqual(final["status"], "partial")
        self.assertEqual(final["processed_count"], 2)
        results = self.store.get_screening_results(run["id"])
        self.assertEqual(
            [(item["job_id"], item["verdict"]) for item in results],
            [("saved", "match"), ("remaining", "mismatch")],
        )

    def test_resume_continues_from_next_source_page_when_checkpoint_available(self):
        saved = sample_screening_job(job_id="saved")
        new_job = sample_screening_job(job_id="new-from-page-2")
        run = self.store.create_screening_run(
            {}, execution={"keyword": "Python", "pages": 3},
        )
        self.result_dir.mkdir(parents=True, exist_ok=True)
        artifact = self.result_dir / f"screening_{run['id']}.json"
        artifact.write_text(json.dumps({
            "keyword": "Python", "last_completed_page": 1, "jobs": [saved],
        }, ensure_ascii=False), encoding="utf-8")
        self.store.update_screening_run_status(run["id"], "running")
        self.store.add_screening_result(run["id"], "saved", "match")
        self.store.update_screening_run_status(
            run["id"], "interrupted", source_count=1,
            match_count=1, processed_count=1, source_cursor=1,
            error_code="cancelled",
        )

        def continue_fetch(*_args, **kwargs):
            self.assertEqual(kwargs["start_page"], 2)
            artifact.write_text(json.dumps({
                "keyword": "Python", "last_completed_page": 3,
                "jobs": [saved, new_job],
            }, ensure_ascii=False), encoding="utf-8")
            return {
                "jobs": [saved, new_job], "source_count": 2,
                "status": "running",
            }

        partition = {
            "match": [], "mismatch": [new_job], "pending": [],
            "pending_failures": {},
        }
        with mock.patch("webui.app.execute_first_layer", side_effect=continue_fetch) as execute:
            with mock.patch("webui.app.partition_jobs", return_value=partition):
                response = self.client.post(
                    f"/api/screening/runs/{run['id']}/resume", json={},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(execute.call_count, 1)
        final = self.store.get_screening_run(run["id"])
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["processed_count"], 2)

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

    def test_restore_after_snapshot_cleanup_recreates_original_zone(self):
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/recover.html", "", "Recover", "ACME", "20-30K", "上海", "JD",
        )
        self.store.link_profile_job(self.profile["id"], job["id"], None, None)
        run = self.store.create_screening_run({})
        self.store.add_screening_result(run["id"], job["id"], "match")
        self.store.move_to_trash_with_origin(
            self.profile["id"], job["id"], "match", run_id=run["id"],
        )
        self.store.update_screening_run_status(run["id"], "succeeded", match_count=1)
        self.store.cleanup_temp_run_data(days=-1)
        response = self.client.post(
            f"/api/screening/trash/{job['id']}/restore",
            json={"profile_id": self.profile["id"]},
        ).get_json()
        self.assertEqual(response["restored_to"], "match")
        self.assertTrue(response["recovery_run_id"])
        matches = self.client.get(
            f"/api/screening/runs/{response['recovery_run_id']}/matches"
        ).get_json()
        self.assertEqual(matches["items"][0]["title"], "Recover")

    def test_cleanup_preview_warns_and_execution_records_result(self):
        preview = self.client.get("/api/screening/cleanup/preview?days=30")
        self.assertEqual(preview.status_code, 200)
        run = self.client.post("/api/screening/cleanup", json={"days": 30})
        self.assertEqual(run.status_code, 200)
        history = self.client.get("/api/screening/cleanup/history").get_json()
        self.assertEqual(len(history["items"]), 1)
        self.assertEqual(history["items"][0]["scope"], "screening_temp_30d")

    def test_cleanup_api_rejects_non_30_day_policy(self):
        response = self.client.post("/api/screening/cleanup", json={"days": 0})
        self.assertEqual(response.status_code, 400)

    def test_invalid_origin_does_not_mutate_reject_state(self):
        run = self.store.create_screening_run({})
        raw_job = sample_screening_job(job_id="invalid-origin")
        self._write_artifact(run["id"], [raw_job])
        response = self.client.post(
            "/api/screening/jobs/invalid-origin/reject",
            json={
                "profile_id": self.profile["id"], "run_id": run["id"],
                "origin_zone": "bad",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.list_screening_rejected(self.profile["id"]), [])
        self.assertEqual(self.store.list_feedback(self.profile["id"]), [])

    def test_pending_moves_exclusively_to_trash_and_restore_reactivates_it(self):
        run = self.store.create_screening_run({})
        raw_job = sample_screening_job(job_id="pending-source")
        self._write_artifact(run["id"], [raw_job])
        self.store.add_pending_result(
            run["id"], "pending-source", "ai_timeout", retryable=True,
        )
        rejected = self.client.post(
            "/api/screening/jobs/pending-source/reject",
            json={
                "profile_id": self.profile["id"], "run_id": run["id"],
                "origin_zone": "pending",
            },
        ).get_json()
        self.assertEqual(self.store.list_pending(run["id"]), [])
        self.assertEqual(self.store.get_screening_run(run["id"])["pending_count"], 0)
        restored = self.client.post(
            f"/api/screening/trash/{rejected['job_id']}/restore",
            json={"profile_id": self.profile["id"]},
        )
        self.assertEqual(restored.status_code, 200)
        pending = self.store.list_pending(run["id"])
        self.assertEqual(pending[0]["job_id"], "pending-source")
        self.assertEqual(pending[0]["failure_stage"], "ai_timeout")
        self.assertEqual(self.store.get_screening_run(run["id"])["pending_count"], 1)

    def test_restore_file_failure_keeps_item_in_trash(self):
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/fail-restore.html", "", "Fail", "ACME",
            "20-30K", "上海", "JD",
        )
        self.store.link_profile_job(self.profile["id"], job["id"], None, None)
        self.store.move_to_trash_with_origin(
            self.profile["id"], job["id"], "match", run_id="cleaned-run",
        )
        with mock.patch("webui.app.Path.open", side_effect=OSError("disk full")):
            response = self.client.post(
                f"/api/screening/trash/{job['id']}/restore",
                json={"profile_id": self.profile["id"]},
            )
        self.assertEqual(response.status_code, 500)
        remaining = self.store.list_trash_with_origin(self.profile["id"])
        self.assertEqual([item["job_id"] for item in remaining], [job["id"]])

    def test_cleanup_removes_controlled_screening_artifact(self):
        run = self.store.create_screening_run({})
        artifact = self.result_dir / f"screening_{run['id']}.json"
        detail_artifact = self.result_dir / f"screening_{run['id']}_details.json"
        self._write_artifact(run["id"], [sample_screening_job(job_id="old")])
        detail_artifact.write_text(
            json.dumps([{"job_id": "old", "jd": "旧 JD"}], ensure_ascii=False),
            encoding="utf-8",
        )
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE screening_runs SET created_at = ?, updated_at = ? WHERE id = ?",
                (old, old, run["id"]),
            )
        response = self.client.post("/api/screening/cleanup", json={"days": 30})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(artifact.exists())
        self.assertFalse(detail_artifact.exists())


class ScreeningBackgroundRuntimeTests(unittest.TestCase):
    def test_runtime_post_returns_202_and_cancel_prevents_late_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            started = threading.Event()
            release = threading.Event()

            def blocked_fetch(*_args, **_kwargs):
                started.set()
                release.wait(timeout=5)
                return {
                    "jobs": [sample_screening_job(job_id="late-job")],
                    "source_count": 1,
                    "status": "running",
                }

            app = create_app({
                "TESTING": True,
                "START_TASKS": True,
                "RESULT_DIR": str(root / "results"),
                "DB_PATH": str(root / "state" / "webui.db"),
                "PYTHON_EXECUTABLE": sys.executable,
                "SCREENING_TIMEOUT_SECONDS": 30,
            })
            client = app.test_client()
            token = client.get("/api/session").get_json()["token"]
            client.environ_base["HTTP_X_BOSS_TOKEN"] = token
            store = app.config["TASK_STORE"]

            with mock.patch("webui.app.execute_first_layer", side_effect=blocked_fetch):
                response = client.post(
                    "/api/screening/runs",
                    json={"keyword": "Python", "filters": {}},
                )
                self.assertEqual(response.status_code, 202)
                run_id = response.get_json()["run_id"]
                self.assertTrue(started.wait(timeout=2), "background worker did not start")

                cancelled = client.post(f"/api/screening/runs/{run_id}/cancel")
                self.assertEqual(cancelled.status_code, 200)
                self.assertEqual(cancelled.get_json()["status"], "interrupted")
                release.set()

                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if run_id not in app.config["SCREENING_RUNNER"]._cancel_events:
                        break
                    time.sleep(0.01)

            final = store.get_screening_run(run_id)
            self.assertEqual(final["status"], "interrupted")
            self.assertEqual(final["error_code"], "cancelled")
            self.assertEqual(store.get_screening_results(run_id), [])
            self.assertEqual(store.list_pending(run_id), [])


if __name__ == "__main__":
    unittest.main()
