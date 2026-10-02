import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace

from webui.ai_screen_api import enqueue_auto_screen_for_scrape
from webui.app_support import PlatformExecutionCapacity
from webui.flow_service import FlowService
from webui.store import TaskStore


class B096ProductionFlowPathTests(unittest.TestCase):
    """Exercise the Flow worker hand-off through real stores and capacity."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-production-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "production-flow-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'production', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        if getattr(self, "capacity", None) is not None:
            self.capacity.shutdown(wait=True)
        self.temp.cleanup()

    def test_completed_track_auto_submits_ai_and_both_platform_lanes_overlap(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="production-flow",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        tasks = {}
        context_lock = threading.RLock()
        started = {"boss": threading.Event(), "zhilian": threading.Event()}
        finished = {"boss": threading.Event(), "zhilian": threading.Event()}
        release = threading.Event()

        def source_snapshot(task_id):
            platform = "boss" if task_id.endswith("boss") else "zhilian"
            return {
                "kind": "scrape",
                "status": "done",
                "platform": platform,
                "result": {
                    "ok": True,
                    "jobs": [{"platform": platform, "platform_job_id": f"{platform}-1"}],
                },
            }

        def register(task_id, kind, *, source_task_id=None):
            task = {"kind": kind, "status": "queued", "source_task_id": source_task_id}
            tasks[task_id] = task
            return task

        def run_ai(task_id, _fields, _summary, source_id, *_args, **_kwargs):
            platform = "boss" if source_id.endswith("boss") else "zhilian"
            started[platform].set()
            self.assertTrue(release.wait(timeout=3))
            result_id = self.store.save_pipeline_result(
                {
                    "platform": platform,
                    "jobs": [{"platform": platform, "platform_job_id": f"{platform}-1", "verdict": "match"}],
                    "dropped": [],
                    "total_scraped": 1,
                },
                {"platform": platform},
                profile_id=self.profile_id,
                execution_params={"platform": platform, "flow_id": flow["id"]},
            )
            service.mark_result_ready(
                flow_id=flow["id"], platform=platform,
                profile_id=self.profile_id, result_run_id=result_id,
            )
            tasks[task_id]["status"] = "done"
            finished[platform].set()

        self.capacity = PlatformExecutionCapacity(thread_name_prefix="production-ai")
        service = FlowService(self.store)
        ctx = SimpleNamespace(
            store=self.store,
            flow_service=service,
            tasks=tasks,
            lock=context_lock,
            ai_platform_executor=self.capacity,
            executor=self.capacity,
            backend_version="test",
            account_for_run=lambda *_args: "a",
            ensure_scrape_source=source_snapshot,
            register_pipeline_task=register,
            run_ai_screen_task=run_ai,
        )

        for platform in ("boss", "zhilian"):
            task_id = f"scrape-{platform}"
            track_id = next(
                track["id"] for track in flow["tracks"]
                if track["platform"] == platform
            )
            self.store.create_screening_run(
                task_id,
                profile_id=self.profile_id,
                execution_params={
                    "platform": platform,
                    "flow_id": flow["id"],
                    "track_id": track_id,
                    "auto_screen_fields": {"salary": ["406"]},
                },
            )
            self.store.create_scrape_search_run(
                task_id, self.profile_id, platform=platform, flow_id=flow["id"],
            )
            self.store.claim_flow_track_submission(
                flow["id"], platform, profile_id=self.profile_id,
            )
            self.store.bind_flow_track_runs(
                flow["id"], platform, profile_id=self.profile_id,
                scrape_run_id=task_id,
            )
            service.mark_scrape_complete(
                flow_id=flow["id"], platform=platform, profile_id=self.profile_id,
            )

        boss_ai = enqueue_auto_screen_for_scrape(None, ctx, "scrape-boss")
        zhilian_ai = enqueue_auto_screen_for_scrape(None, ctx, "scrape-zhilian")
        self.assertIsNotNone(boss_ai)
        self.assertIsNotNone(zhilian_ai)
        self.assertTrue(started["boss"].wait(timeout=3))
        self.assertTrue(started["zhilian"].wait(timeout=3))
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["stage"],
            "ai",
        )
        release.set()
        self.assertTrue(finished["boss"].wait(timeout=3))
        self.assertTrue(finished["zhilian"].wait(timeout=3))
        ready = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        self.assertEqual({track["status"] for track in ready["tracks"]}, {"done"})

    def test_ai_submission_failure_closes_boss_and_zhilian_track_from_durable_binding(self):
        from webui.flow_ai_coordinator import FlowAiCoordinator

        ctx = SimpleNamespace(
            store=self.store,
            tasks={},
            lock=threading.RLock(),
            clear_auto_screen=lambda _run_id: None,
            schedule_pipeline_task_cleanup=lambda _run_id: None,
            release_worker_resume_claims=lambda _task: None,
        )
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="failure-all",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        for platform in ("boss", "zhilian"):
            track = next(item for item in flow["tracks"] if item["platform"] == platform)
            source_id = f"failure-source-{platform}"
            ai_id = f"failure-ai-{platform}"
            self.store.create_screening_run(
                source_id,
                profile_id=self.profile_id,
                execution_params={
                    "platform": platform, "flow_id": flow["id"], "track_id": track["id"],
                },
            )
            self.store.create_scrape_search_run(
                source_id, self.profile_id, platform=platform,
                flow_id=flow["id"], track_id=track["id"],
            )
            self.store.update_screening_run(source_id, status="running")
            self.store.update_flow_track(
                flow["id"], platform, profile_id=self.profile_id,
                status="running", stage="ai", scrape_run_id=source_id,
            )
            self.store.create_screening_run(
                ai_id,
                profile_id=self.profile_id,
                execution_params={
                    "platform": platform, "flow_id": flow["id"],
                    "track_id": track["id"], "scrape_task_id": source_id,
                },
            )
            FlowAiCoordinator(ctx).mark_submission_failed(
                task_id=ai_id,
                scrape_task_id=source_id,
                flow_id=flow["id"],
                platform=platform,
                profile_id=self.profile_id,
                error_code="source_cdp_unavailable",
                reason="登录空间不可用",
                status="paused",
            )
            current = next(
                item for item in self.store.get_flow(
                    flow["id"], profile_id=self.profile_id
                )["tracks"] if item["platform"] == platform
            )
            self.assertEqual(current["status"], "paused")
            self.assertEqual(current["platform"], platform)


class ScreeningPolicyIncompatibleClosureTests(unittest.TestCase):
    """B094 T005：不兼容说明必须经公开失败入口在 run/task/Track 三处一致可见。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b094-closure-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "b094-closure-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'b094', '{}', '{}', '2026-10-02', '2026-10-02')",
                (self.profile_id,),
            )
        self.flow = self.store.create_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="b094-closure", confirmed_filters={"boss": {}},
        )
        track = self.flow["tracks"][0]
        self.source_id = "b094-closure-source"
        self.ai_id = "b094-closure-ai"
        self.store.create_screening_run(
            self.source_id, profile_id=self.profile_id,
            execution_params={"platform": "boss", "flow_id": self.flow["id"],
                              "track_id": track["id"]},
        )
        self.store.create_scrape_search_run(
            self.source_id, self.profile_id, platform="boss",
            flow_id=self.flow["id"], track_id=track["id"],
        )
        self.store.update_screening_run(self.source_id, status="running")
        self.store.create_screening_run(
            self.ai_id, profile_id=self.profile_id,
            execution_params={"platform": "boss", "flow_id": self.flow["id"],
                              "track_id": track["id"],
                              "scrape_task_id": self.source_id},
        )
        self.store.update_screening_run(self.ai_id, status="running")
        self.store.update_flow_track(
            self.flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", scrape_run_id=self.source_id,
            screen_run_id=self.ai_id,
        )
        self.tasks = {self.ai_id: {"kind": "ai_screen", "status": "running", "error": ""}}
        self.ctx = SimpleNamespace(
            store=self.store,
            tasks=self.tasks,
            lock=threading.RLock(),
            write_run=lambda run_id, **kw: self.store.update_screening_run(run_id, **kw),
            clear_auto_screen=lambda _run_id: None,
            schedule_pipeline_task_cleanup=lambda _run_id: None,
            release_worker_resume_claims=lambda _task: None,
            account_for_run=lambda *_args: "a",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _read_back(self):
        from webui.error_registry import FAILED_CODE_LABELS
        from webui.ai_screen_failure import persist_ai_worker_failure

        persist_ai_worker_failure(
            self.ctx, self.ai_id, self.source_id,
            "screening_policy_incompatible", "原始异常正文不得外泄",
            platform="boss",
        )
        run = self.store.get_screening_run(self.ai_id) or {}
        track = next(
            item for item in self.store.get_flow(
                self.flow["id"], profile_id=self.profile_id
            )["tracks"] if item["platform"] == "boss"
        )
        expected = FAILED_CODE_LABELS["screening_policy_incompatible"]
        return run, track, expected

    def test_registered_message_reaches_every_public_field(self):
        run, track, expected = self._read_back()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "screening_policy_incompatible")
        self.assertEqual(run["error_reason"], expected)
        self.assertEqual(self.tasks[self.ai_id]["error"], expected)
        self.assertEqual(track["status"], "failed")
        self.assertEqual(track["reason"], expected)

    def test_message_survives_refresh_and_does_not_leak_exception(self):
        _run, _track, expected = self._read_back()
        reread = self.store.get_screening_run(self.ai_id) or {}
        track = next(
            item for item in self.store.get_flow(
                self.flow["id"], profile_id=self.profile_id
            )["tracks"] if item["platform"] == "boss"
        )
        self.assertEqual(reread["error_reason"], expected)
        self.assertEqual(track["reason"], expected)
        self.assertNotIn("原始异常正文不得外泄", reread["error_reason"])

    def test_incompatible_run_is_not_offered_as_resumable_block(self):
        from webui.error_registry import RECOVERABLE_SYSTEMIC_BLOCK_CODES
        run, _track, _expected = self._read_back()
        self.assertEqual(run["status"], "failed")
        self.assertNotIn(run["error_code"], RECOVERABLE_SYSTEMIC_BLOCK_CODES)


if __name__ == "__main__":
    unittest.main()
