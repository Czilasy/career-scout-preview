"""Regression tests for the third B096 review round.

These tests exercise the production Flow/Track service and route helpers.  The
browser itself remains a controlled test dependency; no real platform account
is used here.
"""

from __future__ import annotations

import pathlib
import tempfile
import threading
import unittest
from concurrent.futures import Future
from types import SimpleNamespace

from flask import Flask

from webui.flow_service import FlowConflictError, FlowService
from webui.flow_service import submit_platform_task
from webui.flow_future import attach_ai_future_failure, attach_scrape_future_failure
from webui.runners.ai_screen_task import run_ai_screen_task
from webui.runners.pipeline_task import _durable_pause_guard
from webui.store import TaskStore
from webui.task_pause_support import pause_with_mode


class _Lane:
    def __init__(self):
        self.calls = []

    def submit(self, platform, fn, *args, **kwargs):
        self.calls.append((platform, fn, args, kwargs))
        future = Future()
        future.set_result(None)
        return future


class B096Round3ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-round3-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "round3-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'round3', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _flow(self, *, selection="boss", key="round3-flow"):
        return self.store.create_flow(
            profile_id=self.profile_id,
            selection=selection,
            start_key=key,
            confirmed_filters={
                platform: {}
                for platform in ("boss", "zhilian")
                if selection == "all" or platform == selection
            },
        )

    def test_flow_resume_submission_uses_platform_lane(self):
        flow = self._flow(key="round3-resume-lane")
        lane = _Lane()
        service = FlowService(self.store, platform_executor=lane)
        service.record_preflight_failure(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            error_code="browser_busy", recoverable=True,
        )
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "paused")
        with self.assertRaises(FlowConflictError):
            service.operate_track(
                flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
                action="resume",
            )
        self.assertEqual(lane.calls, [])

    def test_production_resume_dispatch_uses_the_flow_lane(self):
        lane = _Lane()
        legacy_executor = _Lane()
        ctx = SimpleNamespace(
            platform_executor=lane,
            executor=legacy_executor,
        )
        marker = []
        submit_platform_task(
            ctx, "flow-resume", "zhilian", lambda: marker.append("ran"),
        )
        self.assertEqual(lane.calls[0][0], "zhilian")
        self.assertEqual(legacy_executor.calls, [])

    def test_flow_dispatch_never_falls_back_to_global_executor(self):
        legacy_executor = _Lane()
        ctx = SimpleNamespace(executor=legacy_executor)
        with self.assertRaises(RuntimeError):
            submit_platform_task(ctx, "flow-without-lane", "boss", lambda: None)
        self.assertEqual(legacy_executor.calls, [])

    def test_running_track_preflight_retry_is_idempotent(self):
        flow = self._flow(key="round3-running-preflight")
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        service = FlowService(self.store)
        service.record_preflight_failure(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            error_code="account_pool_empty", recoverable=False,
        )
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "running")
        self.assertTrue(self.store.flow_has_active_tracks(self.profile_id))

    def test_finalizing_pause_is_conflict_and_does_not_set_stop(self):
        run_id = "round3-finalizing"
        tasks = {
            run_id: {
                "kind": "scrape", "status": "running", "finalizing": True,
                "stop_event": threading.Event(),
            },
        }
        ctx = SimpleNamespace(
            tasks=tasks, lock=threading.RLock(),
            store=SimpleNamespace(get_screening_run=lambda _run_id: {"status": "running"}),
        )
        app = Flask(__name__)
        with app.test_request_context("/", method="POST"):
            response, status = pause_with_mode(ctx, run_id, "graceful")
        self.assertEqual(status, 409)
        self.assertEqual(response.get_json()["error"], "finalizing")
        self.assertFalse(tasks[run_id]["stop_event"].is_set())

    def test_finalizing_completion_guard_preserves_a_committed_pause(self):
        task = {"finalizing": True}
        ctx = SimpleNamespace(
            store=SimpleNamespace(
                get_screening_run=lambda _run_id: {"status": "paused"},
            ),
            operational_errors=(Exception,),
        )
        self.assertTrue(_durable_pause_guard(ctx, "round3-finalizing", task))

    def test_flow_track_snapshot_is_available_for_no_run_resume(self):
        flow = self._flow(key="round3-snapshot")
        snapshot = {
            "script_params": {"keyword": "python", "city": ["上海"], "pages": 1},
            "scope_digest": "scope-round3",
        }
        self.store.save_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
            snapshot=snapshot,
        )
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["submission_snapshot"], snapshot)

    def test_paused_no_run_snapshot_reclaims_through_normal_submit_boundary(self):
        flow = self._flow(key="round3-reclaim-paused")
        FlowService(self.store).save_track_submission_snapshot(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            snapshot={"script_params": {"keyword": "Python", "city": ["全国"]}},
        )
        service = FlowService(self.store)
        service.record_preflight_failure(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            error_code="browser_busy", recoverable=True,
        )
        service.validate_track_submission(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
        )
        claimed = service.claim_track_submission(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
        )
        self.assertEqual(claimed["tracks"][0]["status"], "running")

    def test_ai_browser_activation_failure_closes_task_run_and_track(self):
        flow = self._flow(key="round3-ai-activation")
        track = flow["tracks"][0]
        parent_id = "round3-parent"
        ai_id = "round3-ai"
        self.store.create_screening_run(
            parent_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.create_screening_run(
            ai_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "scrape_task_id": parent_id,
            },
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=parent_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=ai_id,
        )
        tasks = {
            ai_id: {
                "kind": "ai_screen", "status": "queued", "platform": "boss",
                "source_task_id": parent_id, "error": "",
            },
        }
        ctx = SimpleNamespace(
            store=self.store,
            tasks=tasks,
            lock=threading.RLock(),
            flow_service=FlowService(self.store),
            is_user_finished=lambda _run_id: False,
            activate_task_browser=lambda _run_id: (_ for _ in ()).throw(
                RuntimeError("activation exploded")
            ),
            write_run=lambda run_id, **kwargs: self.store.update_screening_run(run_id, **kwargs),
            clear_auto_screen=lambda _run_id: None,
            schedule_pipeline_task_cleanup=lambda _run_id: None,
            release_worker_resume_claims=lambda _task: None,
            operational_errors=(Exception,),
        )
        run_ai_screen_task(
            ctx, ai_id, {}, "", parent_id,
        )
        self.assertEqual(tasks[ai_id]["status"], "failed")
        self.assertEqual(self.store.get_screening_run(ai_id)["status"], "failed")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "failed",
        )

    def test_submission_failure_closes_screen_and_search_rows(self):
        flow = self._flow(key="round3-row-failure")
        task_id = "round3-row-failure-task"
        self.store.create_screening_run(
            task_id,
            profile_id=self.profile_id,
            execution_params={"platform": "boss", "flow_id": flow["id"]},
        )
        self.store.create_scrape_search_run(
            task_id, self.profile_id, platform="boss", flow_id=flow["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        FlowService(self.store).mark_submission_failed(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            task_id=task_id, error_code="submit_failed",
        )
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")
        self.assertEqual(self.store.get_search_run(task_id)["status"], "failed")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "failed",
        )

    def test_ai_future_failure_closes_task_run_and_track(self):
        flow = self._flow(key="round3-future-failure")
        track = flow["tracks"][0]
        parent_id = "round3-future-parent"
        ai_id = "round3-future-ai"
        execution = {
            "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            "scrape_task_id": parent_id,
        }
        self.store.create_screening_run(
            parent_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.create_screening_run(
            ai_id, profile_id=self.profile_id, execution_params=execution,
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=parent_id, screen_run_id=ai_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=ai_id,
        )
        tasks = {ai_id: {"kind": "ai_screen", "status": "queued", "error": ""}}
        ctx = SimpleNamespace(
            store=self.store,
            tasks=tasks,
            lock=threading.RLock(),
            flow_service=FlowService(self.store),
            write_run=lambda run_id, **kwargs: self.store.update_screening_run(run_id, **kwargs),
            clear_auto_screen=lambda _run_id: None,
            schedule_pipeline_task_cleanup=lambda _run_id: None,
            release_worker_resume_claims=lambda _task: None,
        )
        future = Future()
        attach_ai_future_failure(
            future, ctx, task_id=ai_id, scrape_task_id=parent_id, platform="boss",
        )
        future.set_exception(RuntimeError("private platform detail"))
        self.assertEqual(tasks[ai_id]["status"], "failed")
        self.assertEqual(self.store.get_screening_run(ai_id)["status"], "failed")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "failed",
        )

    def test_scrape_future_failure_closes_search_run_task_and_track(self):
        flow = self._flow(key="round3-scrape-future-failure")
        track = flow["tracks"][0]
        task_id = "round3-scrape-future"
        self.store.create_screening_run(
            task_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.create_scrape_search_run(
            task_id, self.profile_id, platform="boss", flow_id=flow["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=task_id,
        )
        tasks = {task_id: {"kind": "scrape", "status": "running", "error": ""}}
        ctx = SimpleNamespace(
            store=self.store,
            flow_service=FlowService(self.store),
            tasks=tasks,
            lock=threading.RLock(),
            write_run=lambda run_id, **kwargs: self.store.update_screening_run(run_id, **kwargs),
        )
        future = Future()
        attach_scrape_future_failure(
            future,
            ctx,
            task_id=task_id,
            flow_id=flow["id"],
            platform="boss",
            profile_id=self.profile_id,
        )
        future.set_exception(RuntimeError("private scrape detail"))
        self.assertEqual(tasks[task_id]["status"], "failed")
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")
        self.assertEqual(self.store.get_search_run(task_id)["status"], "failed")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
