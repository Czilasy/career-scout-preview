import json
import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace

from webui.ai_screen_api import enqueue_auto_screen_for_scrape
from webui.app import create_app
from webui.app_support import PlatformExecutionCapacity
from webui.flow_service import FlowService, PlatformUnavailableError
from webui.runners.pipeline_task import _sync_flow_track_after_scrape
from webui.store_flow import FlowConflictError
from webui.store import TaskStore


class _RejectingExecutor:
    def submit(self, *_args, **_kwargs):
        raise RuntimeError("executor rejected")


class B096Round2ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-round2-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "round2-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'round2', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _flow(self, selection="boss", key="round2-flow"):
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

    def _screen_run(
        self, run_id, flow_id=None, platform="boss", track_id=None,
        include_track=True,
    ):
        params = {"platform": platform}
        if flow_id is not None:
            params["flow_id"] = flow_id
            if include_track and track_id is None:
                try:
                    owner = self.store.get_flow(flow_id, profile_id=self.profile_id)
                except KeyError:
                    owner = None
                if owner is not None:
                    track_id = next(
                        (
                            item["id"] for item in owner["tracks"]
                            if item["platform"] == platform
                        ),
                        None,
                    )
        if track_id is not None:
            params["track_id"] = track_id
        return self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params=params,
        )

    def test_application_uses_one_lane_for_scrape_and_ai_but_keeps_platforms_parallel(self):
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "DB_PATH": str(pathlib.Path(self.temp.name) / "app" / "webui.db"),
            "RESULT_DIR": str(pathlib.Path(self.temp.name) / "app" / "results"),
        })
        self.assertIs(
            app.config["PLATFORM_EXECUTION_CAPACITY"],
            app.config["AI_PLATFORM_EXECUTION_CAPACITY"],
        )
        capacity = app.config["PLATFORM_EXECUTION_CAPACITY"]
        entered = {"scrape": threading.Event(), "boss_ai": threading.Event(), "zhilian_ai": threading.Event()}
        release = threading.Event()

        def scrape():
            entered["scrape"].set()
            self.assertTrue(release.wait(2))

        def boss_ai():
            entered["boss_ai"].set()

        def zhilian_ai():
            entered["zhilian_ai"].set()

        scrape_future = capacity.submit("boss", scrape)
        self.assertTrue(entered["scrape"].wait(2))
        boss_future = capacity.submit("boss", boss_ai)
        zhilian_future = capacity.submit("zhilian", zhilian_ai)
        self.assertTrue(entered["zhilian_ai"].wait(2))
        self.assertFalse(entered["boss_ai"].is_set())
        release.set()
        scrape_future.result(timeout=2)
        boss_future.result(timeout=2)
        zhilian_future.result(timeout=2)
        self.assertTrue(entered["boss_ai"].is_set())
        capacity.shutdown(wait=True)

    def test_same_lane_reentrant_ai_submission_does_not_deadlock_scrape_worker(self):
        capacity = PlatformExecutionCapacity(thread_name_prefix="round2-reentrant")
        finished = threading.Event()

        def ai():
            finished.set()

        def scrape():
            # The scrape worker has finished its browser portion and may hand
            # off synchronously.  A shared lane must not make this wait on
            # itself forever.
            child = capacity.submit("boss", ai)
            child.result(timeout=1)

        try:
            parent = capacity.submit("boss", scrape)
            parent.result(timeout=2)
            self.assertTrue(finished.is_set())
        finally:
            capacity.shutdown(wait=True)

    def test_begin_ai_claim_race_has_one_winner_and_no_unbound_loser_run(self):
        flow = self._flow()
        runs = [
            self._screen_run(f"round2-ai-{index}", flow["id"])["id"]
            for index in (1, 2)
        ]
        service = FlowService(self.store)
        barrier = threading.Barrier(2)
        outcomes = []

        def claim(run_id):
            barrier.wait(timeout=2)
            try:
                service.begin_ai(
                    flow_id=flow["id"], platform="boss",
                    profile_id=self.profile_id, screen_run_id=run_id,
                )
                outcomes.append(("won", run_id))
            except (FlowConflictError, ValueError) as exc:
                outcomes.append(("lost", run_id, type(exc).__name__))

        threads = [threading.Thread(target=claim, args=(run_id,)) for run_id in runs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        self.assertEqual([item[0] for item in outcomes].count("won"), 1)
        self.assertEqual([item[0] for item in outcomes].count("lost"), 1)
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertIn(track["screen_run_id"], runs)
        with self.store._connection() as conn:
            bound = conn.execute(
                "SELECT id FROM screening_runs WHERE id IN (?, ?) "
                "AND EXISTS (SELECT 1 FROM flow_tracks WHERE screen_run_id = screening_runs.id)",
                tuple(runs),
            ).fetchall()
        self.assertEqual(len(bound), 1)

    def test_begin_ai_requires_exact_flow_id_and_keeps_track_unbound_on_invalid_source(self):
        flow = self._flow(key="round2-exact-flow")
        service = FlowService(self.store)
        missing = self._screen_run("round2-ai-missing")["id"]
        wrong = self._screen_run("round2-ai-wrong", "other-flow")["id"]
        missing_track = self._screen_run(
            "round2-ai-missing-track", flow["id"], include_track=False,
        )["id"]
        wrong_track = self._screen_run(
            "round2-ai-wrong-track", flow["id"], track_id="other-track",
        )["id"]
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs "
                "(id, platform, status, source_count, match_count, mismatch_count, "
                "created_at, updated_at, started_at, profile_id, execution_params_json, record_kind) "
                "VALUES ('round2-ai-missing-profile', 'boss', 'queued', 0, 0, 0, "
                "'2026-01-01', '2026-01-01', '2026-01-01', NULL, ?, 'process_log')",
                (json.dumps({"platform": "boss", "flow_id": flow["id"]}),),
            )
        missing_profile = "round2-ai-missing-profile"
        for run_id in (missing, wrong, missing_track, wrong_track, missing_profile):
            with self.assertRaises(ValueError):
                service.begin_ai(
                    flow_id=flow["id"], platform="boss",
                    profile_id=self.profile_id, screen_run_id=run_id,
                )
            track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
            self.assertIsNone(track["screen_run_id"])

    def test_complete_track_binds_result_and_terminal_state_in_one_transaction(self):
        flow = self._flow(key="round2-complete-atomic")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai",
        )
        wrong = self._screen_run("round2-result-wrong", "other-flow")["id"]
        service = FlowService(self.store)
        with self.assertRaises(ValueError):
            service.mark_result_ready(
                flow_id=flow["id"], platform="boss",
                profile_id=self.profile_id, result_run_id=wrong,
            )
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "running")
        self.assertIsNone(track["result_run_id"])

        # The next Flow is intentionally created only after the first Flow
        # reaches a terminal state; the active-track gate is part of the
        # production contract this test is exercising.
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="failed", stage="ai", error_code="result_source_invalid",
            reason="wrong source",
        )

        failed_flow = self._flow(key="round2-complete-failed")
        self.store.update_flow_track(
            failed_flow["id"], "boss", profile_id=self.profile_id,
            status="failed", stage="ai", error_code="ai_failed",
            reason="AI 筛选失败",
        )
        result = self._screen_run("round2-result-failed", failed_flow["id"])["id"]
        with self.assertRaises(FlowConflictError):
            service.mark_result_ready(
                flow_id=failed_flow["id"], platform="boss",
                profile_id=self.profile_id, result_run_id=result,
            )
        track = self.store.get_flow(failed_flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "failed")
        self.assertIsNone(track["result_run_id"])
        with self.assertRaises(FlowConflictError):
            self.store.update_flow_track(
                failed_flow["id"], "boss", profile_id=self.profile_id,
                status="done", stage="complete", result_run_id=result,
            )
        track = self.store.get_flow(failed_flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertIsNone(track["result_run_id"])

    def test_legacy_scrape_run_can_be_adopted_once_but_not_reused_by_another_flow(self):
        first = self._flow(key="round2-legacy-first")
        legacy = self._screen_run("round2-legacy-scrape")["id"]
        self.store.update_flow_track(
            first["id"], "boss", profile_id=self.profile_id,
            status="running", stage="scrape", screen_run_id=legacy,
        )
        ai = self._screen_run("round2-legacy-ai", first["id"])["id"]
        FlowService(self.store).begin_ai(
            flow_id=first["id"], platform="boss", profile_id=self.profile_id,
            screen_run_id=ai,
        )
        self.store.update_flow_track(
            first["id"], "boss", profile_id=self.profile_id,
            status="done", stage="complete",
        )
        second = self._flow(key="round2-legacy-second")
        with self.assertRaises(ValueError):
            self.store.bind_flow_track_runs(
                second["id"], "boss", profile_id=self.profile_id,
                scrape_run_id=legacy,
            )

    def test_preflight_failure_records_terminal_or_paused_track_state(self):
        service = FlowService(self.store)
        failed = self._flow(key="round2-preflight-failed")
        service.record_preflight_failure(
            flow_id=failed["id"], platform="boss", profile_id=self.profile_id,
            error_code="account_pool_empty", recoverable=False,
        )
        failed_track = self.store.get_flow(failed["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(failed_track["status"], "failed")
        self.assertEqual(failed_track["error_code"], "account_pool_empty")

        paused = self._flow(key="round2-preflight-paused")
        service.record_preflight_failure(
            flow_id=paused["id"], platform="boss", profile_id=self.profile_id,
            error_code="source_cdp_unavailable", recoverable=True,
        )
        paused_track = self.store.get_flow(paused["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(paused_track["status"], "paused")
        self.assertTrue(self.store.flow_has_active_tracks(self.profile_id))

    def test_flow_auto_ai_submit_failure_fails_track_after_claim(self):
        flow = self._flow(key="round2-ai-submit-failure")
        scrape_id = "round2-scrape-submit-failure"
        track_id = flow["tracks"][0]["id"]
        self.store.create_screening_run(
            scrape_id, profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track_id,
            },
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile_id, platform="boss", flow_id=flow["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_id,
        )
        service = FlowService(self.store)
        service.mark_scrape_complete(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
        )
        tasks = {}
        ctx = SimpleNamespace(
            store=self.store,
            flow_service=service,
            tasks=tasks,
            lock=threading.RLock(),
            ai_platform_executor=_RejectingExecutor(),
            executor=_RejectingExecutor(),
            backend_version="test",
            account_for_run=lambda *_args: "account",
            ensure_scrape_source=lambda _task_id: {
                "kind": "scrape", "status": "done", "platform": "boss",
                "result": {"ok": True, "jobs": [{"platform_job_id": "job-1"}]},
            },
            register_pipeline_task=lambda task_id, kind, source_task_id=None: tasks.setdefault(
                task_id, {"kind": kind, "status": "queued", "source_task_id": source_task_id}
            ),
            run_ai_screen_task=lambda *_args, **_kwargs: None,
        )
        task_id = enqueue_auto_screen_for_scrape(None, ctx, scrape_id)
        # Submission rejection is reported by the worker path after the task
        # has been durably registered; the callable may return no task id on
        # the error response, but exactly one failed task must remain visible.
        self.assertEqual(len(tasks), 1)
        self.assertIn(task_id, (None, *tasks.keys()))
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "failed")
        self.assertEqual(track["stage"], "ai")

    def test_ai_claim_event_write_failure_closes_claimed_track(self):
        flow = self._flow(key="round2-ai-event-failure")
        run = self._screen_run("round2-ai-event", flow["id"])
        service = FlowService(self.store)
        original_append = self.store.append_task_event

        def reject_event(*_args, **_kwargs):
            raise RuntimeError("event write failed")

        self.store.append_task_event = reject_event
        try:
            with self.assertRaises(RuntimeError):
                service.begin_ai(
                    flow_id=flow["id"], platform="boss",
                    profile_id=self.profile_id, screen_run_id=run["id"],
                )
        finally:
            self.store.append_task_event = original_append
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "failed")
        self.assertEqual(track["error_code"], "flow_ai_start_failed")

    def test_scrape_completion_marker_failure_is_closed_on_the_track(self):
        calls = []

        class Marker:
            def mark_scrape_complete(self, **_kwargs):
                raise RuntimeError("marker exploded")

            def fail_track(self, **kwargs):
                calls.append(kwargs)

        class Store:
            def get_screening_run(self, _task_id):
                return {
                    "execution_params": {"flow_id": "flow-1", "platform": "boss"},
                    "platform": "boss", "profile_id": "profile-1",
                }

        ctx = SimpleNamespace(store=Store(), flow_service=Marker())
        _sync_flow_track_after_scrape(ctx, "scrape-1", "done")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["status"], "failed")
        self.assertEqual(calls[0]["error_code"], "scrape_failed")

    def test_recoverable_scrape_marker_persists_paused_track(self):
        calls = []

        class Marker:
            def fail_track(self, **kwargs):
                calls.append(kwargs)

            def operate_track(self, **_kwargs):
                self.fail("recoverable pause must use the durable failure path")

        class Store:
            def get_screening_run(self, _task_id):
                return {
                    "execution_params": {"flow_id": "flow-2", "platform": "zhilian"},
                    "platform": "zhilian", "profile_id": "profile-2",
                }

        ctx = SimpleNamespace(store=Store(), flow_service=Marker())
        _sync_flow_track_after_scrape(
            ctx, "scrape-2", "paused", error_code="source_cdp_unavailable",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["status"], "paused")
        self.assertEqual(calls[0]["error_code"], "source_cdp_unavailable")


if __name__ == "__main__":
    unittest.main()
