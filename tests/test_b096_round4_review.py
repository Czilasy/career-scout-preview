"""Regression tests for the fourth B096 review round (real dual-platform shape).

Every case here mirrors durable data produced by a real browser round: one
Flow with two Tracks, each Track carrying ``scrape_run_id`` then ``screen_run_id``.
No real platform account and no real browser is used; the worker itself stays a
controlled test dependency.
"""

from __future__ import annotations

import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace

from flask import Flask

from webui.ai_screen_api import enqueue_auto_screen_for_scrape
from webui.app_support import PlatformExecutionCapacity
from webui.flow_api import register_flow_routes
from webui.flow_service import FlowService
from webui.store import TaskStore


class B096Round4ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-round4-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "round4-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'round4', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )
        self.service = FlowService(self.store)

    def tearDown(self):
        if getattr(self, "capacity", None) is not None:
            self.capacity.shutdown(wait=True)
        self.temp.cleanup()

    def _flow(self, *, selection="all", key="round4-flow"):
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

    def _scrape_run(self, flow, platform, run_id, *, stage="scrape"):
        """Seed the durable scrape pair and advance one Track to its AI stage."""
        track = next(
            item for item in flow["tracks"] if item["platform"] == platform
        )
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": platform,
                "flow_id": flow["id"],
                "track_id": track["id"],
                "auto_screen": True,
                "auto_screen_fields": {"salary": ["406"]},
                "auto_screen_profile": "画像",
            },
        )
        self.store.create_scrape_search_run(
            run_id, self.profile_id, platform=platform, flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], platform, profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], platform, profile_id=self.profile_id, scrape_run_id=run_id,
        )
        self.service.mark_scrape_complete(
            flow_id=flow["id"], platform=platform, profile_id=self.profile_id,
        )
        return track

    def _track(self, flow_id, platform):
        flow = self.store.get_flow(flow_id, profile_id=self.profile_id)
        return next(item for item in flow["tracks"] if item["platform"] == platform)

    # ------------------------------------------------------------------
    # 1. AI 筛选必须绑定到 Track，轨道级暂停才有可操作对象
    # ------------------------------------------------------------------
    def test_auto_screen_binds_ai_run_and_track_pause_succeeds(self):
        flow = self._flow(key="round4-auto-pause")
        self._scrape_run(flow, "boss", "round4-auto-boss-scrape")

        tasks = {}
        release = threading.Event()
        self.capacity = PlatformExecutionCapacity(thread_name_prefix="round4-auto")

        def register(task_id, kind, *, source_task_id=None):
            task = {
                "kind": kind, "status": "queued", "logs": [],
                "source_task_id": source_task_id,
                "stop_event": threading.Event(),
            }
            tasks[task_id] = task
            return task

        def run_ai(task_id, _fields, _summary, source_id, *_args, **_kwargs):
            tasks[task_id]["status"] = "running"
            self.assertTrue(release.wait(timeout=3))

        ctx = SimpleNamespace(
            store=self.store,
            flow_service=self.service,
            tasks=tasks,
            lock=threading.RLock(),
            ai_platform_executor=self.capacity,
            executor=self.capacity,
            backend_version="test",
            account_for_run=lambda *_args: "a",
            ensure_scrape_source=lambda _task_id: {
                "kind": "scrape", "status": "done", "platform": "boss",
                "result": {"ok": True, "jobs": [{"platform_job_id": "boss-1"}]},
            },
            register_pipeline_task=register,
            run_ai_screen_task=run_ai,
        )

        ai_task_id = enqueue_auto_screen_for_scrape(None, ctx, "round4-auto-boss-scrape")
        self.assertIsNotNone(ai_task_id)
        # 真实缺陷：轨道只有 scrape_run_id，AI run 从未绑定，
        # /tracks/boss/pause 只能对着已结束的抓取任务报 503。
        track = self._track(flow["id"], "boss")
        self.assertEqual(track["screen_run_id"], ai_task_id)

        app = Flask(__name__)
        register_flow_routes(app, ctx)
        response = app.test_client().post(
            f"/api/flows/{flow['id']}/tracks/boss/pause",
            json={"profile_id": self.profile_id},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(tasks[ai_task_id]["stop_event"].is_set())
        self.assertEqual(
            tasks[ai_task_id]["stop_mode"], "pause",
        )
        self.assertEqual(self._track(flow["id"], "boss")["status"], "paused")
        release.set()

    # ------------------------------------------------------------------
    # 2. 服务重启后 Track 状态必须随任务归一，界面不能再显示运行中
    # ------------------------------------------------------------------
    def test_ensure_ai_run_bound_to_track_binds_takeover_run(self):
        """接管式续跑的新 run 落库后必须认领轨道（worker 侧同一树干入口）。"""
        from webui.flow_task_coordinator import ensure_ai_run_bound_to_track

        flow = self._flow(key="round4-takeover")
        self._scrape_run(flow, "zhilian", "round4-takeover-scrape")
        track = self._track(flow["id"], "zhilian")
        ai_run_id = "round4-takeover-screen"
        self.store.create_screening_run(
            ai_run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "zhilian", "flow_id": flow["id"],
                "track_id": track["id"], "scrape_task_id": "round4-takeover-scrape",
            },
        )
        ctx = SimpleNamespace(store=self.store, flow_service=self.service)

        ensure_ai_run_bound_to_track(ctx, ai_run_id)

        current = self._track(flow["id"], "zhilian")
        self.assertEqual(current["screen_run_id"], ai_run_id)
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["stage"], "ai")
        # 幂等：再走一次不改变事实。
        ensure_ai_run_bound_to_track(ctx, ai_run_id)
        self.assertEqual(self._track(flow["id"], "zhilian")["screen_run_id"], ai_run_id)

    def test_ensure_ai_run_bound_to_track_leaves_legacy_run_alone(self):
        from webui.flow_task_coordinator import ensure_ai_run_bound_to_track

        run_id = "round4-legacy-screen"
        self.store.create_screening_run(
            run_id, profile_id=self.profile_id,
            execution_params={"platform": "zhilian"},
        )
        ctx = SimpleNamespace(store=self.store, flow_service=self.service)
        self.assertIsNone(ensure_ai_run_bound_to_track(ctx, run_id))

    def test_ai_screen_route_binds_resumed_run_to_track(self):
        """续跑路径也必须把 AI run 绑回轨道：暂停才有可操作对象。

        真实一轮里两条轨道都从自动续跑进入 AI 阶段，绑定语句被
        ``if not resume_from_run_id`` 挡在外面，轨道只剩抓取 id。
        """
        from unittest import mock

        from tests.healthy_pipeline.harness import _authed_test_client, _make_app

        app, app_temp = _make_app()
        self.addCleanup(app_temp.cleanup)
        store = app.config["TASK_STORE"]
        ctx = app.config["PIPELINE_CONTEXT"]
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'round4-app', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )
        flow = store.create_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="round4-route-flow", confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape_id = "round4-route-scrape"
        ai_id = "round4-route-screen"
        store.create_screening_run(
            scrape_id, profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        store.create_scrape_search_run(
            scrape_id, self.profile_id, platform="boss",
            flow_id=flow["id"], track_id=track["id"],
        )
        store.claim_flow_track_submission(flow["id"], "boss", profile_id=self.profile_id)
        store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id, scrape_run_id=scrape_id,
        )
        ctx.flow_service.mark_scrape_complete(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
        )
        store.create_screening_run(
            ai_id,
            profile_id=self.profile_id,
            frozen_filters={"salary": ["406"]},
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["406"]},
                "profile_summary": "", "profile_facts": None,
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        store.update_screening_run(ai_id, status="running", current_stage="ai_rough")
        store.update_screening_run(ai_id, status="paused", error_code="user_paused")

        submitted = []
        lane = SimpleNamespace(
            submit=lambda platform, fn, *args, **kwargs: submitted.append((platform, args)),
        )
        with (
            mock.patch.object(ctx, "ensure_scrape_source", lambda _tid: {
                "kind": "scrape", "status": "done", "platform": "boss",
                "result": {"ok": True, "jobs": [{"platform_job_id": "boss-1"}]},
            }),
            mock.patch.object(ctx, "activate_run_browser", lambda *_args: None),
            mock.patch.object(ctx, "platform_executor", lane),
            mock.patch.object(ctx, "ai_platform_executor", lane),
        ):
            response = _authed_test_client(app).post(
                "/api/ai-screen",
                json={
                    "scrape_task_id": scrape_id,
                    "platform": "boss",
                    "screening_fields": {"salary": ["406"]},
                    "profile_summary": "",
                },
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(submitted), 1)

        current = store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(current["screen_run_id"], ai_id)
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["stage"], "ai")

    # ------------------------------------------------------------------
    # 3. 服务重启后 Track 状态必须随任务归一，界面不能再显示运行中
    # ------------------------------------------------------------------
    def test_restart_binds_declared_ai_run_and_moves_track_off_running(self):
        """真实一轮的形状：轨道有抓取、AI run 自认属于该轨道却没绑定。

        重启后 AI run 已归一为 interrupted，轨道却仍是 running/ai，
        灵动岛说「已中断」、轨道卡却写「抓取中」并继续计时，
        暂停/恢复必然 503（没有可操作对象）。
        """
        flow = self._flow(key="round4-orphan")
        self._scrape_run(flow, "boss", "round4-orphan-scrape")
        track = self._track(flow["id"], "boss")
        ai_run_id = "round4-orphan-screen"
        self.store.create_screening_run(
            ai_run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"], "scrape_task_id": "round4-orphan-scrape",
            },
        )
        self.store.update_screening_run(ai_run_id, status="running", current_stage="jd_detail")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["stage"], "ai",
        )
        self.assertIsNone(self._track(flow["id"], "boss")["screen_run_id"])

        TaskStore(self.store.db_path)

        current = self._track(flow["id"], "boss")
        self.assertEqual(current["screen_run_id"], ai_run_id)
        self.assertEqual(current["status"], "interrupted")
        self.assertEqual(current["stage"], "ai")

    def test_restart_normalization_moves_track_off_running(self):
        flow = self._flow(key="round4-restart")
        self._scrape_run(flow, "boss", "round4-restart-scrape")
        ai_run_id = "round4-restart-screen"
        track = self._track(flow["id"], "boss")
        self.store.create_screening_run(
            ai_run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"], "scrape_task_id": "round4-restart-scrape",
            },
        )
        self.store.update_screening_run(ai_run_id, status="running", current_stage="jd_detail")
        self.service.begin_ai(
            flow_id=flow["id"], platform="boss",
            profile_id=self.profile_id, screen_run_id=ai_run_id,
        )
        self.assertEqual(self._track(flow["id"], "boss")["status"], "running")

        TaskStore(self.store.db_path)

        current = self._track(flow["id"], "boss")
        self.assertEqual(current["status"], "interrupted")
        self.assertEqual(current["stage"], "ai")

    def test_user_finished_screen_run_keeps_track_terminal_not_interrupted(self):
        """用户主动结束并保存的区分不得被重启归一抹平。"""
        flow = self._flow(key="round4-user-finished")
        self._scrape_run(flow, "boss", "round4-user-finished-scrape")
        ai_run_id = "round4-user-finished-screen"
        track = self._track(flow["id"], "boss")
        self.store.create_screening_run(
            ai_run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"], "scrape_task_id": "round4-user-finished-scrape",
            },
        )
        self.store.update_screening_run(ai_run_id, status="running", current_stage="ai_fine")
        self.service.begin_ai(
            flow_id=flow["id"], platform="boss",
            profile_id=self.profile_id, screen_run_id=ai_run_id,
        )
        self.store.update_screening_run(
            ai_run_id, status="succeeded", current_stage="done",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="done", stage="complete",
        )

        TaskStore(self.store.db_path)

        current = self._track(flow["id"], "boss")
        self.assertEqual(current["status"], "done")
        self.assertEqual(current["stage"], "complete")


class B096Round4TrackActionErrorTests(unittest.TestCase):
    """轨道动作没有可操作对象时必须是可区分、可读的说法。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-round4-action-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "round4-action-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'round4', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )
        self.service = FlowService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_pause_without_any_bound_run_reports_unavailable_target(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="zhilian",
            start_key="round4-no-run", confirmed_filters={"zhilian": {}},
        )
        track = flow["tracks"][0]
        self.store.claim_flow_track_submission(
            flow["id"], "zhilian", profile_id=self.profile_id,
        )
        self.store.update_flow_track(
            flow["id"], "zhilian", profile_id=self.profile_id,
            status="running", stage="ai",
        )
        app = Flask(__name__)
        register_flow_routes(app, SimpleNamespace(
            store=self.store, flow_service=self.service,
            tasks={}, lock=threading.RLock(),
        ))

        response = app.test_client().post(
            f"/api/flows/{flow['id']}/tracks/zhilian/pause",
            json={"profile_id": self.profile_id},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 503, payload)
        # 真实缺陷：所有失败都归成笼统的「目标任务操作失败」，
        # 用户与前端都分不出是「这一线还没有可操作的筛选任务」。
        self.assertEqual(payload["error_code"], "flow_track_task_unavailable")
        self.assertNotEqual(payload["message"], "目标任务操作失败，请刷新任务状态后重试")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"], "running",
        )


if __name__ == "__main__":
    unittest.main()
