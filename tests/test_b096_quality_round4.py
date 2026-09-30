"""Quality gates for the fourth B096 review round."""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace

from webui.app import create_app
from webui.flow_service import FlowService
from webui.flow_task_state import close_flow_task_state
from webui.runners.ai_screen_task import run_ai_screen_task
from webui.store import TaskStore


class B096QualityRound4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-quality-round4-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "round4-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'round4', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _seed_flow_runs(self, *, error_code="source_cdp_unavailable"):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key=f"round4-{error_code}",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape_id = f"{flow['id']}-scrape"
        ai_id = f"{flow['id']}-ai"
        self.store.create_screening_run(
            scrape_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        self.store.create_scrape_search_run(
            scrape_id,
            self.profile_id,
            platform="boss",
            flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.create_screening_run(
            ai_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
                "scrape_task_id": scrape_id,
            },
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_id, screen_run_id=ai_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=ai_id,
        )
        self.store.update_screening_run(scrape_id, status="running")
        self.store.update_screening_run(ai_id, status="running", current_stage="ai_rough")
        return flow, scrape_id, ai_id, error_code

    def _activation_context(self, flow, scrape_id, ai_id, error_code):
        tasks = {
            ai_id: {
                "kind": "ai_screen", "status": "queued", "platform": "boss",
                "source_task_id": scrape_id, "error": "",
            },
        }
        def _activation(_run_id):
            error = RuntimeError("browser activation failure")
            error.error_code = error_code
            raise error
        return SimpleNamespace(
            store=self.store,
            tasks=tasks,
            lock=threading.RLock(),
            flow_service=FlowService(self.store),
            is_user_finished=lambda _run_id: False,
            activate_task_browser=_activation,
            write_run=lambda run_id, **kwargs: self.store.update_screening_run(run_id, **kwargs),
            clear_auto_screen=lambda _run_id: None,
            schedule_pipeline_task_cleanup=lambda _run_id: None,
            release_worker_resume_claims=lambda _task: None,
            operational_errors=(Exception,),
        ), tasks

    def test_recoverable_activation_closes_every_flow_row_as_paused(self):
        flow, scrape_id, ai_id, code = self._seed_flow_runs()
        ctx, tasks = self._activation_context(flow, scrape_id, ai_id, code)
        run_ai_screen_task(ctx, ai_id, {}, "", scrape_id)
        self.assertEqual(tasks[ai_id]["status"], "paused")
        self.assertEqual(self.store.get_screening_run(ai_id)["status"], "paused")
        self.assertEqual(self.store.get_screening_run(scrape_id)["status"], "paused")
        self.assertEqual(self.store.get_search_run(scrape_id)["status"], "paused")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "paused",
        )

    def test_nonrecoverable_activation_closes_every_flow_row_as_failed(self):
        flow, scrape_id, ai_id, code = self._seed_flow_runs(error_code="internal_error")
        ctx, tasks = self._activation_context(flow, scrape_id, ai_id, code)
        run_ai_screen_task(ctx, ai_id, {}, "", scrape_id)
        self.assertEqual(tasks[ai_id]["status"], "failed")
        self.assertEqual(self.store.get_screening_run(ai_id)["status"], "failed")
        self.assertEqual(self.store.get_screening_run(scrape_id)["status"], "failed")
        self.assertEqual(self.store.get_search_run(scrape_id)["status"], "failed")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "failed",
        )

    def test_flow_api_rejects_pause_when_track_task_is_finalizing(self):
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "DB_PATH": str(pathlib.Path(self.temp.name) / "api" / "webui.db"),
            "RESULT_DIR": str(pathlib.Path(self.temp.name) / "results"),
        })
        client = app.test_client()
        token = client.get("/api/session").get_json()["token"]
        client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        store = app.config["TASK_STORE"]
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES ('api-round4-profile', 'round4', '{}', '{}', '2026-01-01', '2026-01-01')",
            )
        create = client.post(
            "/api/flows",
            json={
                "profile_id": "api-round4-profile",
                "selection": "boss",
                "confirmed": {"boss": True},
                "filters": {"boss": {}},
                "start_key": "api-round4-flow",
            },
        )
        self.assertIn(create.status_code, (200, 201), create.get_json())
        flow = create.get_json()["flow"]
        track = flow["tracks"][0]
        store.claim_flow_track_submission(
            flow["id"], "boss", profile_id="api-round4-profile",
        )
        store.update_flow_track(
            flow["id"], "boss", profile_id="api-round4-profile",
            status="running", stage="scrape",
        )
        scrape_id = f"{flow['id']}-scrape"
        store.create_screening_run(
            scrape_id,
            profile_id="api-round4-profile",
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        store.bind_flow_track_runs(
            flow["id"], "boss", profile_id="api-round4-profile",
            scrape_run_id=scrape_id,
        )
        ctx = app.config["PIPELINE_CONTEXT"]
        with ctx.lock:
            ctx.tasks[scrape_id] = {"status": "running", "finalizing": True}
        response = client.post(
            f"/api/flows/{flow['id']}/tracks/boss/pause?profile_id=api-round4-profile",
        )
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["error_code"], "flow_conflict")
        self.assertEqual(
            store.get_flow(flow["id"], profile_id="api-round4-profile")["tracks"][0]["status"],
            "running",
        )

    def test_flow_failure_state_is_atomic_when_search_update_fails(self):
        flow, scrape_id, ai_id, code = self._seed_flow_runs()
        ctx, _tasks = self._activation_context(flow, scrape_id, ai_id, code)
        with self.store._connection() as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_b096_search_close
                BEFORE UPDATE OF status ON search_runs
                BEGIN
                    SELECT RAISE(ABORT, 'injected search close failure');
                END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            close_flow_task_state(
                ctx,
                task_id=ai_id,
                scrape_task_id=scrape_id,
                status="failed",
                error_code="submit_failed",
                reason="后台任务提交失败",
                platform="boss",
                stage="ai",
            )
        self.assertEqual(self.store.get_screening_run(ai_id)["status"], "running")
        self.assertEqual(self.store.get_screening_run(scrape_id)["status"], "running")
        self.assertEqual(self.store.get_search_run(scrape_id)["status"], "queued")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "running",
        )
