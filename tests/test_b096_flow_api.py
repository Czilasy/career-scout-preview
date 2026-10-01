import pathlib
import tempfile
import unittest
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from webui.app import create_app
from webui.flow_api import register_flow_routes
from webui.flow_service import FlowConflictError, FlowResumeError, FlowService
from webui.flow_submission_service import FlowSubmissionService
from webui.store import TaskStore


class B096FlowApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-api-")
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        store = TaskStore(self.db_path)
        self.profile_id = "profile-api"
        self.other_profile_id = "profile-api-other"
        with store._connection() as conn:
            for profile_id in (self.profile_id, self.other_profile_id):
                conn.execute(
                    "INSERT INTO candidate_profiles "
                    "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                    "VALUES (?, ?, '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                    (profile_id, profile_id),
                )
        self.store = store
        self.enabled = {"boss": True, "zhilian": True}
        self.service = FlowService(
            store,
            platform_enabled=lambda platform: self.enabled[platform],
        )
        self.app = Flask(__name__)
        register_flow_routes(
            self.app,
            SimpleNamespace(store=store, flow_service=self.service),
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def _payload(self, **extra):
        body = {
            "profile_id": self.profile_id,
            "selection": "all",
            "start_key": "api-start-1",
            "confirmed": {"boss": True, "zhilian": True},
            "confirmed_filters": {"boss": {}, "zhilian": {}},
        }
        body.update(extra)
        return body

    def test_all_defaults_to_all_without_confirmation_gate(self):
        response = self.client.post(
            "/api/flows",
            json=self._payload(selection=None, confirmed=None),
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["flow"]["selection"], "all")
        self.assertEqual(
            {track["platform"] for track in payload["flow"]["tracks"]},
            {"boss", "zhilian"},
        )

    def test_invalid_selection_uses_safe_public_message(self):
        response = self.client.post(
            "/api/flows",
            json=self._payload(selection="secret-internal-selection"),
        )

        self.assertEqual(response.status_code, 422)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "invalid_request")
        self.assertEqual(payload["message"], "流程选择参数无效")
        self.assertNotIn("secret-internal-selection", response.get_data(as_text=True))

    def test_disabled_platform_rejects_all_but_allows_a_single_platform(self):
        self.enabled["zhilian"] = False
        response = self.client.post("/api/flows", json=self._payload())
        self.assertIn(response.status_code, (409, 503))
        self.assertEqual(response.get_json()["error_code"], "platform_disabled")

        single = self.client.post(
            "/api/flows",
            json=self._payload(
                selection="boss",
                start_key="api-boss-1",
                confirmed={"boss": True},
                confirmed_filters={"boss": {}},
            ),
        )
        self.assertEqual(single.status_code, 201)
        self.assertEqual(len(single.get_json()["flow"]["tracks"]), 1)

    def test_disabled_platform_message_does_not_leak_the_platform_code(self):
        """SPEC 046 D-08 组：面向用户的文案不得把内部平台码直接吐到界面上。

        显示名由前端既有的平台投影负责；后端只给中文说明与结构化 platform 字段。
        """
        self.enabled["zhilian"] = False
        response = self.client.post(
            "/api/flows",
            json=self._payload(start_key="api-disabled-message"),
        )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "platform_disabled")
        self.assertEqual(payload["platform"], "zhilian")
        self.assertNotIn("zhilian", payload["message"])
        self.assertNotIn("boss", payload["message"])
        self.assertTrue(payload["message"])

    def test_disabled_boss_message_names_the_platform_and_gives_a_reason(self):
        """SPEC 046 Edge Cases「指出不可用平台」在 BOSS 侧同样要落得下来。

        树枝（webui/platforms_boss）在注册数据缺项时把 BOSS 标为不可用并带上可读
        原因；树干只转述注册表那一份原因，显示名投影仍归前端，不在此硬编码平台名。
        """
        from webui.platforms import get_platform, register_platform
        from webui.platforms_boss import BOSS_AVAILABILITY_REASON, register_boss_from_maps

        original = get_platform("boss")
        self.enabled["boss"] = False
        register_boss_from_maps({}, {})
        try:
            response = self.client.post(
                "/api/flows",
                json=self._payload(start_key="api-disabled-boss-message"),
            )
        finally:
            register_platform(original)

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "platform_disabled")
        self.assertEqual(payload["platform"], "boss")
        # 原因非空，且不落在「该平台暂不可用」这类不点名的兜底文案上。
        self.assertTrue(payload["message"].strip())
        self.assertNotIn("暂不可用，请改用可用平台", payload["message"])
        self.assertIn("BOSS直聘", payload["message"])
        self.assertEqual(payload["message"], BOSS_AVAILABILITY_REASON)

    def test_retry_key_returns_same_flow_and_active_gate_blocks_other_round(self):
        first = self.client.post("/api/flows", json=self._payload())
        self.assertEqual(first.status_code, 201)
        retried = self.client.post(
            "/api/flows",
            json=self._payload(confirmed_filters={"boss": {"changed": True}, "zhilian": {}}),
        )
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.get_json()["flow"]["id"], first.get_json()["flow"]["id"])

        blocked = self.client.post(
            "/api/flows",
            json=self._payload(
                selection="boss",
                start_key="api-boss-blocked",
                confirmed={"boss": True},
                confirmed_filters={"boss": {}},
            ),
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.get_json()["error_code"], "flow_conflict")

    def test_current_and_detail_are_profile_scoped(self):
        created = self.client.post("/api/flows", json=self._payload())
        flow_id = created.get_json()["flow"]["id"]
        current = self.client.get(f"/api/flows/current?profile_id={self.profile_id}")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.get_json()["flow"]["id"], flow_id)

        wrong = self.client.get(
            f"/api/flows/{flow_id}?profile_id={self.other_profile_id}"
        )
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(wrong.get_json()["error_code"], "flow_not_found")

        paused = self.client.post(
            f"/api/flows/{flow_id}/tracks/boss/pause",
            json={"profile_id": self.profile_id},
        )
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(
            next(
                track for track in paused.get_json()["flow"]["tracks"]
                if track["platform"] == "boss"
            )["status"],
            "paused",
        )
        self.assertEqual(
            next(
                track for track in paused.get_json()["flow"]["tracks"]
                if track["platform"] == "zhilian"
            )["status"],
            "queued",
        )

    def test_current_flow_reads_an_interrupted_envelope_for_a_dead_ended_round(self):
        # 一条轨道已出结果、另一条被重启中断：读取边界必须如实说「已中断」，
        # 谎报「排队中」会让前端的新一轮入口永久锁死。
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="api-interrupted-envelope",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        result_run = self.store.create_screening_run(
            "api-interrupted-envelope-result",
            profile_id=self.profile_id,
            execution_params={"flow_id": flow["id"]},
        )
        self.store.update_flow_track(
            flow["id"], "boss",
            profile_id=self.profile_id,
            status="done", stage="complete", result_run_id=result_run["id"],
        )
        self.store.update_flow_track(
            flow["id"], "zhilian",
            profile_id=self.profile_id, status="running", stage="ai",
        )
        screen_run = self.store.create_screening_run(
            "api-interrupted-envelope-screen",
            profile_id=self.profile_id,
            execution_params={"flow_id": flow["id"]},
        )
        self.store.update_flow_track(
            flow["id"], "zhilian",
            profile_id=self.profile_id, screen_run_id=screen_run["id"],
        )
        # 重新打开同一个库 = 进程重启，中断状态按产线口径落到轨道上。
        restarted = TaskStore(self.db_path)
        self.assertEqual(
            next(
                track for track in restarted.get_flow(
                    flow["id"], profile_id=self.profile_id,
                )["tracks"] if track["platform"] == "zhilian"
            )["status"],
            "interrupted",
        )

        current = self.client.get(
            f"/api/flows/current?profile_id={self.profile_id}"
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.get_json()["flow"]["status"], "interrupted")

    def test_flow_results_endpoint_returns_only_bound_track_jobs_and_unfinished_ai_marker(self):
        created = self.client.post("/api/flows", json=self._payload())
        self.assertEqual(created.status_code, 201)
        flow = created.get_json()["flow"]
        scrape_run_id = self.store.create_search_run(
            self.profile_id, {}, "ai"
        )["id"]
        self.store.create_screening_run(
            scrape_run_id,
            profile_id=self.profile_id,
            execution_params={"platform": "boss", "flow_id": flow["id"]},
        )
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO scrape_run_jobs "
                "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    scrape_run_id,
                    "boss-api-job-1",
                    "default",
                    json.dumps({"platform": "boss", "platform_job_id": "boss-api-job-1", "title": "A"}),
                    "2026-01-01T00:00:00Z",
                ),
            )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            screen_run_id=scrape_run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="failed", stage="screen", error_code="ai_unavailable",
            reason="AI unavailable",
        )

        response = self.client.get(
            f"/api/flows/{flow['id']}/results?profile_id={self.profile_id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["results"]["flow_id"], flow["id"])
        boss = next(track for track in payload["results"]["tracks"] if track["platform"] == "boss")
        self.assertEqual(boss["message"], "未完成 AI 筛选")
        self.assertEqual(boss["screened_count"], 0)
        self.assertEqual([job["platform_job_id"] for job in boss["jobs"]], ["boss-api-job-1"])

        wrong_profile = self.client.get(
            f"/api/flows/{flow['id']}/results?profile_id={self.other_profile_id}"
        )
        self.assertEqual(wrong_profile.status_code, 404)

    def test_flow_results_endpoint_normalizes_job_platform_to_bound_track(self):
        created = self.client.post("/api/flows", json=self._payload())
        self.assertEqual(created.status_code, 201)
        flow = created.get_json()["flow"]
        scrape_run_id = self.store.create_search_run(
            self.profile_id, {}, "ai"
        )["id"]
        self.store.create_screening_run(
            scrape_run_id,
            profile_id=self.profile_id,
            execution_params={"platform": "boss", "flow_id": flow["id"]},
        )
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO scrape_run_jobs "
                "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    scrape_run_id,
                    "boss-authoritative-job",
                    "default",
                    json.dumps({
                        "platform": "zhilian",
                        "platform_job_id": "boss-authoritative-job",
                        "title": "BOSS 岗位",
                    }),
                    "2026-01-01T00:00:00Z",
                ),
            )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            screen_run_id=scrape_run_id,
        )

        response = self.client.get(
            f"/api/flows/{flow['id']}/results?profile_id={self.profile_id}"
        )

        self.assertEqual(response.status_code, 200)
        boss = next(
            track for track in response.get_json()["results"]["tracks"]
            if track["platform"] == "boss"
        )
        self.assertEqual(boss["jobs"][0]["platform"], "boss")

    def test_finish_route_publishes_flow_result_and_releases_new_round_gate(self):
        from webui.task_continue_finish import register_finish_route

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="api-finish-flow",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        boss = next(item for item in flow["tracks"] if item["platform"] == "boss")
        self.store.update_flow_track(
            flow["id"], "zhilian", profile_id=self.profile_id,
            status="failed", stage="scrape", error_code="source_login_required",
            reason="登录已失效",
        )
        task_id = "api-finish-flow-task"
        self.store.create_screening_run(
            task_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": boss["id"],
            },
        )
        self.store.update_screening_run(task_id, status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=task_id,
        )
        snapshot_id = self.store.save_pipeline_result(
            {
                "platform": "boss",
                "jobs": [{"platform_job_id": "api-finish-job", "verdict": "uncertain"}],
                "dropped": [],
                "total_scraped": 1,
            },
            {"platform": "boss"},
            profile_id=self.profile_id,
            status="partial",
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": boss["id"], "scrape_task_id": task_id,
            },
        )
        task = {
            "kind": "ai_screen", "status": "paused", "stop_event": threading.Event(),
        }
        ctx = SimpleNamespace(
            store=self.store,
            tasks={task_id: task},
            lock=threading.RLock(),
            resume_claims={task_id},
            operational_errors=(Exception,),
            app=SimpleNamespace(config={
                "RESULT_DIR": self.temp.name,
                "BROWSER_ACCOUNTS_PATH": str(pathlib.Path(self.temp.name) / "accounts.json"),
            }),
            clear_auto_screen=mock.Mock(),
            prune_history_best_effort=mock.Mock(),
            schedule_pipeline_task_cleanup=mock.Mock(),
        )
        compat = SimpleNamespace(
            _FINALIZE_WAIT_TIMEOUT_S=0,
            _public_task_status=lambda status, _kind=None: status,
            _wait_for_jd_batch_settle=lambda _ctx, _run_id: False,
            request_stop=lambda _task, _event, _mode: None,
            STOP_MODE_FINISH="finish",
            build_partial_pipeline_result=lambda *_args, **_kwargs: {
                "ok": True,
                "jobs": [{"platform_job_id": "api-finish-job", "verdict": "uncertain"}],
                "dropped": [],
            },
            append_task_event_best_effort=lambda *_args, **_kwargs: None,
            _logger=mock.Mock(),
            DiscoveryStoreConflictError=Exception,
        )
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        with mock.patch(
            "webui.result_rounds.save_finished_round", return_value=snapshot_id,
        ), mock.patch(
            "webui.screen_flow.build_round_script_params", return_value={},
        ), mock.patch(
            "webui.task_finish_whitebox.finalize_manual_partial_whitebox_or_none",
            return_value={"conclusion": "partial", "evidence_complete": True},
        ), mock.patch(
            "webui.frozen_browser_identity.close_frozen_run_browser",
            return_value=cleanup,
        ):
            app = Flask(__name__)
            app.config.update(ctx.app.config)
            register_finish_route(app, ctx, compat)
            response = app.test_client().post(f"/api/task/finish/{task_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["snapshot_run_id"], snapshot_id)
        finished = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        boss = next(item for item in finished["tracks"] if item["platform"] == "boss")
        zhilian = next(item for item in finished["tracks"] if item["platform"] == "zhilian")
        self.assertEqual(boss["result_run_id"], snapshot_id)
        self.assertEqual(boss["stage"], "complete")
        self.assertEqual(boss["status"], "stopped")
        self.assertEqual(zhilian["status"], "failed")
        self.assertFalse(self.store.flow_has_active_tracks(self.profile_id))
        results = self.store.get_flow_results(flow["id"], profile_id=self.profile_id)
        self.assertEqual(results["tracks"][0]["jobs"][0]["platform_job_id"], "api-finish-job")

    def test_finish_route_real_snapshot_is_bound_to_flow_track(self):
        """The production snapshot writer must feed the atomic Flow closure."""
        from webui.task_continue_finish import register_finish_route

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-finish-real-snapshot",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape_id = "api-finish-real-source"
        self.store.create_screening_run(
            scrape_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "script_params": {"keyword": "Python", "city": ["上海"]},
            },
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile_id, platform="boss",
            flow_id=flow["id"], track_id=track["id"],
        )
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO scrape_run_jobs "
                "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    scrape_id, "api-finish-real-job", "default",
                    json.dumps({
                        "platform": "boss", "platform_job_id": "api-finish-real-job",
                        "title": "A", "jd": "Python",
                    }),
                    "2026-01-01T00:00:00Z",
                ),
            )
        ai_id = "api-finish-real-ai"
        self.store.create_screening_run(
            ai_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "scrape_task_id": scrape_id,
            },
        )
        self.store.update_screening_run(ai_id, status="running")
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_id, screen_run_id=ai_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=ai_id,
        )
        ctx = SimpleNamespace(
            store=self.store,
            tasks={ai_id: {"kind": "ai_screen", "status": "paused", "stop_event": threading.Event()}},
            lock=threading.RLock(), resume_claims={ai_id},
            operational_errors=(Exception,),
            app=SimpleNamespace(config={
                "RESULT_DIR": self.temp.name,
                "BROWSER_ACCOUNTS_PATH": str(pathlib.Path(self.temp.name) / "accounts.json"),
            }),
            clear_auto_screen=mock.Mock(), prune_history_best_effort=mock.Mock(),
            load_jd_checkpoint=lambda _path: {},
            jd_checkpoint_path=lambda _result_dir, _run_id: str(pathlib.Path(self.temp.name) / "jd.json"),
        )
        compat = SimpleNamespace(
            _FINALIZE_WAIT_TIMEOUT_S=0,
            _public_task_status=lambda status, _kind=None: status,
            _wait_for_jd_batch_settle=lambda _ctx, _run_id: False,
            request_stop=lambda _task, _event, _mode: None,
            STOP_MODE_FINISH="finish",
            build_partial_pipeline_result=lambda source_jobs, *_args, **_kwargs: {
                "ok": True, "jobs": [dict(source_jobs[0])], "dropped": [],
            },
            append_task_event_best_effort=lambda *_args, **_kwargs: None,
            _logger=mock.Mock(), DiscoveryStoreConflictError=Exception,
        )
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        with mock.patch(
            "webui.screen_flow.build_round_script_params", return_value={},
        ), mock.patch(
            "webui.task_finish_whitebox.finalize_manual_partial_whitebox_or_none",
            return_value={"conclusion": "partial", "evidence_complete": True},
        ), mock.patch(
            "webui.frozen_browser_identity.close_frozen_run_browser",
            return_value=cleanup,
        ):
            app = Flask(__name__)
            app.config.update(ctx.app.config)
            register_finish_route(app, ctx, compat)
            response = app.test_client().post(f"/api/task/finish/{ai_id}")

        self.assertEqual(response.status_code, 200)
        snapshot_id = response.get_json()["snapshot_run_id"]
        finished = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        self.assertEqual(finished["tracks"][0]["result_run_id"], snapshot_id)
        self.assertEqual(finished["tracks"][0]["status"], "stopped")
        results = self.store.get_flow_results(flow["id"], profile_id=self.profile_id)
        self.assertEqual(results["jobs"][0]["platform_job_id"], "api-finish-real-job")
        repeated = app.test_client().post(f"/api/task/finish/{ai_id}")
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.get_json().get("idempotent"))
        self.assertEqual(repeated.get_json().get("snapshot_run_id"), snapshot_id)
        with mock.patch.object(
            self.store,
            "get_flow_results",
            side_effect=sqlite3.OperationalError("results read failed"),
        ):
            unreadable = app.test_client().post(f"/api/task/finish/{ai_id}")
        self.assertEqual(unreadable.status_code, 503, unreadable.get_json())
        self.assertEqual(
            unreadable.get_json()["error_code"], "flow_results_unavailable"
        )

    def test_late_finish_conflict_does_not_create_snapshot_or_operator_stop(self):
        """A finish CAS conflict must happen before irreversible snapshot work."""
        from webui.store_constants import DiscoveryStoreConflictError
        from webui.task_pause_support import STOP_MODE_FINISH, request_stop
        from webui.task_continue_finish import register_finish_route

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-finish-late-conflict",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        task_id = "api-finish-late-conflict-task"
        self.store.create_screening_run(
            task_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        self.store.update_screening_run(task_id, status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=task_id,
        )
        stop_event = threading.Event()
        ctx = SimpleNamespace(
            store=self.store,
            tasks={task_id: {"kind": "ai_screen", "status": "paused", "stop_event": stop_event}},
            lock=threading.RLock(), resume_claims={task_id},
            operational_errors=(Exception,),
            app=SimpleNamespace(config={
                "RESULT_DIR": self.temp.name,
                "BROWSER_ACCOUNTS_PATH": str(pathlib.Path(self.temp.name) / "accounts.json"),
            }),
            clear_auto_screen=mock.Mock(),
            prune_history_best_effort=mock.Mock(),
        )
        compat = SimpleNamespace(
            _FINALIZE_WAIT_TIMEOUT_S=0,
            _public_task_status=lambda status, _kind=None: status,
            _wait_for_jd_batch_settle=lambda _ctx, _run_id: False,
            request_stop=request_stop,
            STOP_MODE_FINISH=STOP_MODE_FINISH,
            build_partial_pipeline_result=lambda *_args, **_kwargs: {
                "ok": True, "jobs": [{"platform_job_id": "late"}], "dropped": [],
            },
            append_task_event_best_effort=lambda *_args, **_kwargs: None,
            _logger=mock.Mock(),
            DiscoveryStoreConflictError=DiscoveryStoreConflictError,
        )
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        with mock.patch(
            "webui.result_rounds.save_finished_round", return_value="orphan",
        ) as save_snapshot, mock.patch(
            "webui.screen_flow.build_round_script_params", return_value={},
        ), mock.patch.object(
            self.store,
            "claim_flow_finish",
            side_effect=DiscoveryStoreConflictError("already_terminal"),
        ), mock.patch.object(
            self.store,
            "finish_flow_task_atomic",
            side_effect=DiscoveryStoreConflictError("already_terminal"),
        ), mock.patch(
            "webui.frozen_browser_identity.close_frozen_run_browser",
            return_value=cleanup,
        ):
            app = Flask(__name__)
            app.config.update(ctx.app.config)
            register_finish_route(app, ctx, compat)
            response = app.test_client().post(f"/api/task/finish/{task_id}")

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertFalse(stop_event.is_set())
        save_snapshot.assert_not_called()
        current = self.store.get_screening_run(task_id)
        self.assertEqual(current["status"], "running")
        self.assertNotEqual(current.get("interruption_kind"), "operator_stop")
        self.assertIsNone(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["result_run_id"]
        )

    def test_finish_snapshot_failure_keeps_claimed_state_retryable_after_signal(self):
        """A post-signal failure must not restore an active Flow finish claim."""
        from webui.store_constants import DiscoveryStoreConflictError
        from webui.task_pause_support import STOP_MODE_FINISH, request_stop
        from webui.task_continue_finish import register_finish_route

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-finish-claim-retry",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape_id = "api-finish-claim-retry-source"
        self.store.create_screening_run(
            scrape_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile_id, platform="boss",
            flow_id=flow["id"], track_id=track["id"],
        )
        self.store.save_scrape_combo_result(
            scrape_id, "Python|上海",
            [{"platform_job_id": "claim-retry-job", "title": "A"}],
            ["Python|上海"],
        )
        task_id = "api-finish-claim-retry-task"
        self.store.create_screening_run(
            task_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"], "scrape_task_id": scrape_id,
            },
        )
        self.store.update_screening_run(
            task_id, status="running", current_stage="ai",
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_id, screen_run_id=task_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=task_id,
        )
        stop_event = threading.Event()
        ctx = SimpleNamespace(
            store=self.store,
            tasks={task_id: {
                "kind": "ai_screen", "status": "running",
                "stop_event": stop_event,
            }},
            lock=threading.RLock(), resume_claims={task_id},
            operational_errors=(Exception,),
            app=SimpleNamespace(config={
                "RESULT_DIR": self.temp.name,
                "BROWSER_ACCOUNTS_PATH": str(pathlib.Path(self.temp.name) / "accounts.json"),
            }),
            clear_auto_screen=mock.Mock(),
            prune_history_best_effort=mock.Mock(),
            load_jd_checkpoint=lambda _path: {},
            jd_checkpoint_path=lambda _result_dir, _run_id: str(
                pathlib.Path(self.temp.name) / "jd.json"
            ),
        )
        compat = SimpleNamespace(
            _FINALIZE_WAIT_TIMEOUT_S=0,
            _public_task_status=lambda status, _kind=None: status,
            _wait_for_jd_batch_settle=lambda _ctx, _run_id: False,
            request_stop=request_stop,
            STOP_MODE_FINISH=STOP_MODE_FINISH,
            build_partial_pipeline_result=lambda *_args, **_kwargs: {
                "ok": True, "jobs": [{"platform_job_id": "claim-retry-job"}],
                "dropped": [],
            },
            append_task_event_best_effort=lambda *_args, **_kwargs: None,
            _logger=mock.Mock(),
            DiscoveryStoreConflictError=DiscoveryStoreConflictError,
        )
        app = Flask(__name__)
        app.config.update(ctx.app.config)
        register_finish_route(app, ctx, compat)
        with mock.patch(
            "webui.result_rounds.save_finished_round",
            side_effect=RuntimeError("snapshot failed"),
        ), mock.patch(
            "webui.screen_flow.build_round_script_params", return_value={},
        ):
            response = app.test_client().post(f"/api/task/finish/{task_id}")

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertTrue(stop_event.is_set())
        claimed = self.store.get_screening_run(task_id)
        self.assertEqual(claimed["status"], "interrupted")
        self.assertEqual(claimed["error_code"], "user_finished")
        self.assertEqual(claimed["interruption_kind"], "user_finished")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"],
            "running",
        )
        retry_claim = self.store.claim_flow_finish(
            flow["id"], "boss", profile_id=self.profile_id, task_run_id=task_id,
        )
        self.assertTrue(retry_claim.get("claimed"))
        self.assertTrue(retry_claim.get("finish_pending"))

        snapshot_id = self.store.save_pipeline_result(
            {"platform": "boss", "jobs": [{"platform_job_id": "claim-retry-job"}], "dropped": []},
            {"platform": "boss"},
            profile_id=self.profile_id,
            status="partial",
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"], "scrape_task_id": scrape_id,
            },
        )
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        with mock.patch(
            "webui.result_rounds.save_finished_round",
            side_effect=AssertionError("pending finish should reuse snapshot"),
        ), mock.patch(
            "webui.screen_flow.build_round_script_params", return_value={},
        ), mock.patch(
            "webui.task_finish_whitebox.finalize_manual_partial_whitebox_or_none",
            return_value={"conclusion": "partial", "evidence_complete": True},
        ), mock.patch(
            "webui.frozen_browser_identity.close_frozen_run_browser",
            return_value=cleanup,
        ):
            retried = app.test_client().post(f"/api/task/finish/{task_id}")

        self.assertEqual(retried.status_code, 200, retried.get_json())
        finished = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        self.assertEqual(finished["tracks"][0]["status"], "stopped")
        self.assertEqual(finished["tracks"][0]["result_run_id"], snapshot_id)

    def test_flow_action_operates_bound_task_before_track_update_and_is_idempotent(self):
        flow = self.service.start_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="api-operate-task",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        boss = next(track for track in flow["tracks"] if track["platform"] == "boss")
        run = self.store.create_screening_run(
            "api-operate-task-run",
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": boss["id"],
            },
        )
        self.store.update_screening_run(run["id"], status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=run["id"],
        )
        stop_event = threading.Event()
        task = {
            "kind": "ai_screen", "status": "running", "stop_event": stop_event,
            "logs": [],
        }
        ctx = SimpleNamespace(
            store=self.store,
            flow_service=self.service,
            tasks={run["id"]: task},
            lock=threading.RLock(),
        )
        self.service.operate_track_callback = None
        app = Flask(__name__)
        register_flow_routes(app, ctx)

        response = app.test_client().post(
            f"/api/flows/{flow['id']}/tracks/boss/pause",
            json={"profile_id": self.profile_id},
        )

        self.assertEqual(response.status_code, 200)

        self.assertTrue(stop_event.is_set())
        self.assertEqual(task["stop_mode"], "pause")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"],
            "paused",
        )
        response = app.test_client().post(
            f"/api/flows/{flow['id']}/tracks/boss/pause",
            json={"profile_id": self.profile_id},
        )
        self.assertEqual(response.status_code, 200)

    def test_flow_pause_track_write_failure_can_self_heal_after_worker_pauses(self):
        """Direct Flow pause retries the same signalled task after a write error."""
        from webui.flow_task_coordinator import FlowTaskOperationError

        flow = self.service.start_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-flow-pause-retry",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-flow-pause-retry-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="ai_rough",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai_rough", screen_run_id=run_id,
        )
        stop_event = threading.Event()
        task = {
            "kind": "ai_screen", "status": "running", "stop_event": stop_event,
        }
        ctx = SimpleNamespace(
            store=self.store, flow_service=self.service,
            tasks={run_id: task}, lock=threading.RLock(),
        )
        original_update = self.store.update_flow_track
        calls = {"count": 0}

        def fail_once(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise FlowTaskOperationError("track write unavailable")
            return original_update(*args, **kwargs)

        self.service.operate_track_callback = None
        app = Flask(__name__)
        register_flow_routes(app, ctx)
        with mock.patch.object(
            self.store, "update_flow_track", side_effect=fail_once,
        ):
            first = app.test_client().post(
                f"/api/flows/{flow['id']}/tracks/boss/pause",
                json={"profile_id": self.profile_id},
            )
            self.assertEqual(first.status_code, 503, first.get_json())
            self.assertTrue(stop_event.is_set())
            task["status"] = "paused"
            self.store.update_screening_run(
                run_id, status="paused", current_stage="ai_rough",
                error_code="user_paused",
            )
            second = app.test_client().post(
                f"/api/flows/{flow['id']}/tracks/boss/pause",
                json={"profile_id": self.profile_id},
            )

        self.assertEqual(second.status_code, 200, second.get_json())
        current = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        self.assertEqual(current["tracks"][0]["status"], "paused")
        self.assertEqual(current["tracks"][0]["stage"], "ai")

    def test_legacy_pause_route_syncs_the_bound_flow_track(self):
        from webui.task_pause_support import pause_with_mode

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-legacy-pause-flow",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-legacy-pause-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.create_scrape_search_run(
            run_id,
            self.profile_id,
            platform="boss",
            flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="scrape", scrape_run_id=run_id,
        )
        task = {"kind": "scrape", "status": "running", "stop_event": threading.Event()}
        ctx = SimpleNamespace(
            store=self.store, tasks={run_id: task}, lock=threading.RLock(),
            pipeline_guard=None,
        )
        app = Flask(__name__)
        with app.test_request_context(f"/api/task/pause/{run_id}", method="POST"):
            response = pause_with_mode(ctx, run_id, "graceful")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(task["stop_event"].is_set())
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"],
            "paused",
        )

    def test_pause_track_write_failure_can_self_heal_after_worker_pauses(self):
        """A signal/write split must remain retryable for both platform lanes."""
        from webui.task_pause_support import pause_with_mode

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="api-pause-retry-both",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        for platform in ("boss", "zhilian"):
            track = next(
                item for item in flow["tracks"] if item["platform"] == platform
            )
            run_id = f"api-pause-retry-{platform}-run"
            self.store.create_screening_run(
                run_id,
                profile_id=self.profile_id,
                execution_params={
                    "platform": platform,
                    "flow_id": flow["id"],
                    "track_id": track["id"],
                },
            )
            self.store.create_scrape_search_run(
                run_id,
                self.profile_id,
                platform=platform,
                flow_id=flow["id"],
                track_id=track["id"],
            )
            self.store.update_screening_run(
                run_id, status="running", current_stage="ai_rough",
            )
            self.store.update_flow_track(
                flow["id"], platform, profile_id=self.profile_id,
                status="running", stage="ai_rough", screen_run_id=run_id,
            )
            task = {
                "kind": "ai_screen", "status": "running",
                "stop_event": threading.Event(),
            }
            ctx = SimpleNamespace(
                store=self.store, tasks={run_id: task}, lock=threading.RLock(),
                pipeline_guard=None,
            )
            original_update = self.store.update_flow_track
            calls = {"count": 0}

            def fail_once(*args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise sqlite3.OperationalError("track write unavailable")
                return original_update(*args, **kwargs)

            with mock.patch.object(
                self.store, "update_flow_track", side_effect=fail_once,
            ), self.app.test_request_context(
                f"/api/task/pause/{run_id}", method="POST",
            ):
                first = pause_with_mode(ctx, run_id, "graceful")
                self.assertEqual(first[1], 503)
                self.assertTrue(task["stop_event"].is_set())
                # The worker can finish its own pause between the failed
                # signal publication and the user retry.
                self.store.update_screening_run(
                    run_id, status="paused", current_stage="ai_rough",
                    error_code="user_paused",
                )
                task["status"] = "paused"
                second = pause_with_mode(ctx, run_id, "graceful")

            second_status = second[1] if isinstance(second, tuple) else second.status_code
            self.assertEqual(second_status, 200)
            current = self.store.get_flow(flow["id"], profile_id=self.profile_id)
            current_track = current["tracks"][0]
            self.assertEqual(current_track["status"], "paused")
            self.assertEqual(current_track["stage"], "ai")

    def test_legacy_execute_search_cancel_syncs_the_bound_flow_track(self):
        from webui.exec_search_cancel import register_cancel_execute_search

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-legacy-cancel-flow",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-legacy-cancel-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.create_scrape_search_run(
            run_id,
            self.profile_id,
            platform="boss",
            flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="scrape", scrape_run_id=run_id,
        )
        task = {
            "kind": "scrape", "status": "running", "stop_event": threading.Event(),
            "logs": [], "platform": "boss",
        }
        ctx = SimpleNamespace(
            store=self.store, tasks={run_id: task}, lock=threading.RLock(),
            operational_errors=(Exception,), clear_auto_screen=mock.Mock(),
        )
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        app = Flask(__name__)
        app.config["BROWSER_ACCOUNTS_PATH"] = str(pathlib.Path(self.temp.name) / "accounts.json")
        register_cancel_execute_search(app, ctx)
        with mock.patch(
            "webui.frozen_browser_identity.cleanup_frozen_task_browser",
            return_value=cleanup,
        ):
            response = app.test_client().post(f"/api/execute-search/{run_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"],
            "cancelled",
        )
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "interrupted")
        self.assertEqual(self.store.get_screening_run(run_id)["error_code"], "user_cancelled")
        self.assertEqual(self.store.get_search_run(run_id)["status"], "interrupted")

    def test_ai_cancel_persists_run_and_flow_track_before_memory_ack(self):
        from webui.ai_screen_api import register_ai_screen_routes

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-ai-cancel-flow",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-ai-cancel-run"
        self.store.create_screening_run(
            run_id, profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running", current_stage="ai")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=run_id,
        )
        task = {
            "kind": "ai_screen", "status": "running", "platform": "boss",
            "stop_event": threading.Event(), "logs": [],
        }
        ctx = SimpleNamespace(
            store=self.store, tasks={run_id: task}, lock=threading.RLock(),
            operational_errors=(Exception,), flow_service=self.service,
        )
        app = Flask(__name__)
        app.config["BROWSER_ACCOUNTS_PATH"] = str(pathlib.Path(self.temp.name) / "accounts.json")
        register_ai_screen_routes(app, ctx)
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        with mock.patch(
            "webui.frozen_browser_identity.cleanup_frozen_task_browser",
            return_value=cleanup,
        ):
            response = app.test_client().post(f"/api/ai-screen/{run_id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "interrupted")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "cancelled",
        )
        self.assertEqual(task["status"], "cancelled")

    def test_flow_action_failure_response_does_not_publish_track_state(self):
        flow = self.service.start_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-flow-action-failure",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-flow-action-failure-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=run_id,
        )
        ctx = SimpleNamespace(
            store=self.store,
            flow_service=self.service,
            tasks={run_id: {"kind": "ai_screen", "status": "running"}},
            lock=threading.RLock(),
            operate_flow_task=lambda *_args: SimpleNamespace(status_code=503),
        )
        self.service.operate_track_callback = None
        app = Flask(__name__)
        register_flow_routes(app, ctx)
        response = app.test_client().post(
            f"/api/flows/{flow['id']}/tracks/boss/pause",
            json={"profile_id": self.profile_id},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"],
            "running",
        )

    def test_flow_resume_uses_ai_continuation_for_ai_track(self):
        """AI/重抓 Tracks must not be sent through scrape-only continuation."""
        from webui.flow_task_coordinator import operate_bound_task

        run_id = "api-flow-ai-resume"
        task = {
            "kind": "ai_screen", "status": "paused",
            "stop_event": threading.Event(),
        }
        scrape_continue = mock.Mock(return_value=SimpleNamespace(status_code=200))
        flow_continue = mock.Mock(return_value=SimpleNamespace(status_code=202))
        ctx = SimpleNamespace(
            tasks={run_id: task}, lock=threading.RLock(),
            continue_execute_search=scrape_continue,
            continue_flow_task=flow_continue,
        )
        track = {
            "stage": "ai", "screen_run_id": run_id,
            "scrape_run_id": "api-flow-ai-source",
        }

        result = operate_bound_task(ctx, "resume", "boss", {}, track)

        self.assertEqual(result.status_code, 202)
        flow_continue.assert_called_once_with(run_id, "boss", {}, track)
        scrape_continue.assert_not_called()

    def test_flow_action_rejects_false_mapping_from_operation_hook(self):
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            operate_bound_task,
        )

        ctx = SimpleNamespace(
            operate_flow_task=lambda *_args: {"ok": False},
        )
        with self.assertRaises(FlowTaskOperationError):
            operate_bound_task(ctx, "pause", "boss", {}, {"stage": "scrape"})

    def test_flow_pause_rejects_live_task_without_stop_signal(self):
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            operate_bound_task,
        )

        run_id = "api-flow-no-stop-signal"
        ctx = SimpleNamespace(
            tasks={run_id: {"kind": "scrape", "status": "running"}},
            lock=threading.RLock(),
        )
        with self.assertRaises(FlowTaskOperationError):
            operate_bound_task(
                ctx, "pause", "boss", {},
                {"stage": "scrape", "scrape_run_id": run_id},
            )

    def test_flow_pause_rejects_missing_real_task_without_publishing_track(self):
        flow = self.service.start_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="api-flow-missing-task",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-flow-missing-task-run"
        self.store.create_screening_run(
            run_id, profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=run_id,
        )
        ctx = SimpleNamespace(
            store=self.store, flow_service=self.service, tasks={}, lock=threading.RLock(),
        )
        app = Flask(__name__)
        register_flow_routes(app, ctx)
        response = app.test_client().post(
            f"/api/flows/{flow['id']}/tracks/boss/pause",
            json={"profile_id": self.profile_id},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "running",
        )

    def test_flow_stop_persists_target_run_cancelled_before_track_update(self):
        """Stopping a Flow task closes only its bound run durably."""
        from webui.flow_task_coordinator import operate_bound_task

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="api-flow-stop-durable",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        boss = next(item for item in flow["tracks"] if item["platform"] == "boss")
        run_id = "api-flow-stop-durable-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": boss["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=run_id,
        )
        task = {
            "kind": "ai_screen", "status": "running",
            "stop_event": threading.Event(),
        }
        ctx = SimpleNamespace(
            store=self.store, tasks={run_id: task}, lock=threading.RLock(),
        )

        operate_bound_task(ctx, "stop", "boss", flow, {
            **boss, "stage": "ai", "screen_run_id": run_id,
        })

        closed = self.store.get_screening_run(run_id)
        self.assertEqual(closed["status"], "interrupted")
        self.assertEqual(closed["error_code"], "user_cancelled")
        self.assertEqual(closed["interruption_kind"], "user_cancelled")
        self.assertEqual(task["status"], "cancelled")

    def test_flow_service_stop_publishes_stopped_after_atomic_run_close(self):
        from webui.flow_task_coordinator import operate_bound_task

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-flow-service-stop",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-flow-service-stop-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai", screen_run_id=run_id,
        )
        task = {
            "kind": "ai_screen", "status": "running",
            "stop_event": threading.Event(),
        }
        ctx = SimpleNamespace(
            store=self.store, tasks={run_id: task}, lock=threading.RLock(),
        )
        self.service.operation_context = ctx
        self.service.operate_track_callback = lambda action, platform, current_flow, current_track: operate_bound_task(
            ctx, action, platform, current_flow, current_track,
        )

        self.service.operate_track(
            flow_id=flow["id"], platform="boss",
            profile_id=self.profile_id, action="stop",
        )

        self.assertEqual(self.store.get_screening_run(run_id)["status"], "interrupted")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"],
            "stopped",
        )

    def test_legacy_cancel_does_not_rewrite_terminal_flow_track(self):
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            persist_task_cancelled,
        )

        flow = self.service.start_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="api-flow-terminal-cancel",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-flow-terminal-cancel-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            screen_run_id=run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="stopped", stage="complete",
        )
        with self.assertRaises(FlowTaskOperationError):
            persist_task_cancelled(SimpleNamespace(store=self.store), run_id)
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "running")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "stopped",
        )

    def test_cancel_closes_stale_paused_track_when_bound_run_is_already_failed(self):
        """A recoverable legacy failure must not keep its Flow Track occupied."""
        from webui.flow_task_coordinator import persist_task_cancelled

        flow = self.service.start_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="api-flow-failed-cancel",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-flow-failed-cancel-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(
            run_id,
            status="failed",
            error_code="source_cdp_unavailable",
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            screen_run_id=run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="paused", stage="ai",
        )

        persisted = persist_task_cancelled(SimpleNamespace(store=self.store), run_id)

        self.assertEqual(persisted.get("_cancel_outcome"), "not_required")
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "failed")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "cancelled",
        )

    def test_cancel_rejects_terminal_screening_with_active_search_projection(self):
        """A terminal local snapshot must not hide an active sibling projection."""
        from webui.flow_task_coordinator import FlowTaskOperationError, persist_task_cancelled

        flow = self.service.start_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="api-flow-mixed-cancel",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run = self.store.create_search_run(
            self.profile_id,
            {
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
            "ai",
        )
        run_id = run["id"]
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="succeeded")
        self.store.update_search_run(run_id, status="running")
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=run_id, screen_run_id=run_id,
        )

        with self.assertRaises(FlowTaskOperationError):
            persist_task_cancelled(SimpleNamespace(store=self.store), run_id)

        self.assertEqual(self.store.get_screening_run(run_id)["status"], "succeeded")
        self.assertEqual(self.store.get_search_run(run_id)["status"], "running")

    def test_cancel_acknowledges_terminal_screening_and_search_projections(self):
        """When every projection is terminal, cancellation is an explicit no-op."""
        from webui.flow_task_coordinator import persist_task_cancelled

        flow = self.service.start_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="api-flow-terminal-paired-cancel",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run = self.store.create_search_run(
            self.profile_id,
            {
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
            "ai",
        )
        run_id = run["id"]
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="succeeded")
        self.store.update_search_run(run_id, status="running")
        self.store.update_search_run(run_id, status="succeeded")
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=run_id, screen_run_id=run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="done", stage="complete",
        )

        persisted = persist_task_cancelled(SimpleNamespace(store=self.store), run_id)

        self.assertEqual(persisted.get("_cancel_outcome"), "not_required")
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "succeeded")
        self.assertEqual(self.store.get_search_run(run_id)["status"], "succeeded")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "done",
        )

    def test_cancel_terminal_scrape_rejects_when_sibling_screen_run_is_active(self):
        """A terminal scrape projection must not close a Track over live AI work."""
        from webui.flow_task_coordinator import FlowTaskOperationError, persist_task_cancelled

        flow = self.service.start_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="api-flow-sibling-active-cancel",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape = self.store.create_search_run(
            self.profile_id,
            {
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
            "ai",
        )
        scrape_run_id = scrape["id"]
        screen_run_id = "api-flow-sibling-active-screen"
        self.store.create_screening_run(
            screen_run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_search_run(scrape_run_id, status="running")
        self.store.update_search_run(scrape_run_id, status="succeeded")
        self.store.update_screening_run(screen_run_id, status="running")
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_run_id, screen_run_id=screen_run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai",
        )

        with self.assertRaises(FlowTaskOperationError):
            persist_task_cancelled(SimpleNamespace(store=self.store), scrape_run_id)

        self.assertEqual(self.store.get_search_run(scrape_run_id)["status"], "succeeded")
        self.assertEqual(self.store.get_screening_run(screen_run_id)["status"], "running")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "running",
        )

    def test_cancel_user_cancelled_scrape_rejects_when_sibling_screen_run_is_active(self):
        """A settled scrape projection must not close a Track over live AI work."""
        from webui.flow_task_coordinator import FlowTaskOperationError, persist_task_cancelled

        flow = self.service.start_flow(
            profile_id=self.profile_id, selection="boss",
            start_key="api-flow-user-cancelled-sibling-active",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape = self.store.create_search_run(
            self.profile_id,
            {
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
            "ai",
        )
        scrape_run_id = scrape["id"]
        screen_run_id = "api-flow-user-cancelled-sibling-screen"
        self.store.create_screening_run(
            screen_run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_search_run(scrape_run_id, status="running")
        self.store.update_search_run(
            scrape_run_id, status="interrupted", error_code="user_cancelled",
        )
        self.store.update_screening_run(screen_run_id, status="running")
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_run_id, screen_run_id=screen_run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai",
        )

        with self.assertRaises(FlowTaskOperationError):
            persist_task_cancelled(SimpleNamespace(store=self.store), scrape_run_id)

        self.assertEqual(
            self.store.get_search_run(scrape_run_id)["status"], "interrupted",
        )
        self.assertEqual(
            self.store.get_search_run(scrape_run_id)["error_code"], "user_cancelled",
        )
        self.assertEqual(self.store.get_screening_run(screen_run_id)["status"], "running")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]["status"],
            "running",
        )

    def test_legacy_binding_rejects_run_track_mismatch(self):
        """A forged/stale Track id must not redirect an action to a sibling."""
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            MissingPlatformIdentityError,
            resolve_flow_binding,
        )

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="api-flow-binding-mismatch",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        boss = next(item for item in flow["tracks"] if item["platform"] == "boss")
        zhilian = next(item for item in flow["tracks"] if item["platform"] == "zhilian")
        run_id = "api-flow-binding-mismatch-run"
        run = self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": boss["id"],
            },
        )
        self.store.create_scrape_search_run(
            run_id, self.profile_id, platform="boss",
            flow_id=flow["id"], track_id=boss["id"],
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=run_id,
        )
        # Simulate a stale/mutated frozen payload after the durable binding was
        # created.  The action must refuse the mismatch rather than guess.
        self.store.update_screening_execution_params(
            run_id,
            {
                **run["execution_params"],
                "track_id": zhilian["id"],
            },
        )
        ctx = SimpleNamespace(store=self.store)

        with self.assertRaises(FlowTaskOperationError):
            resolve_flow_binding(ctx, run_id)

    def test_flow_binding_without_durable_track_does_not_default_to_boss(self):
        from webui.flow_task_coordinator import (
            MissingPlatformIdentityError,
            resolve_flow_binding,
        )

        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="zhilian",
            start_key="api-flow-no-track", confirmed_filters={"zhilian": {}},
        )
        run_id = "api-flow-no-track-run"
        self.store.create_screening_run(
            run_id, profile_id=self.profile_id,
            execution_params={"flow_id": flow["id"], "track_id": "missing-track"},
        )
        with self.assertRaises(MissingPlatformIdentityError):
            resolve_flow_binding(SimpleNamespace(store=self.store), run_id)

    def test_legacy_action_does_not_acknowledge_terminal_track_as_success(self):
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            sync_flow_track_for_run,
        )

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-flow-terminal-sync",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-flow-terminal-sync-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.create_scrape_search_run(
            run_id, self.profile_id, platform="boss",
            flow_id=flow["id"], track_id=track["id"],
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="failed", stage="scrape", error_code="source_login_required",
            reason="登录已失效",
        )

        with self.assertRaises(FlowTaskOperationError):
            sync_flow_track_for_run(
                SimpleNamespace(store=self.store), run_id,
                status="paused", stage="scrape",
            )

    def test_legacy_process_cancel_without_db_run_only_signals_worker(self):
        """A pre-persistence legacy task may still be cancelled in memory."""
        from webui.exec_search_cancel import register_cancel_execute_search

        run_id = "legacy-process-only-cancel"
        task = {
            "kind": "scrape",
            "status": "running",
            "stop_event": threading.Event(),
            "logs": [],
            "platform": "boss",
        }
        ctx = SimpleNamespace(
            store=self.store,
            tasks={run_id: task},
            lock=threading.RLock(),
            operational_errors=(Exception,),
            clear_auto_screen=mock.Mock(),
        )
        app = Flask(__name__)
        app.config["BROWSER_ACCOUNTS_PATH"] = str(
            pathlib.Path(self.temp.name) / "accounts.json"
        )
        register_cancel_execute_search(app, ctx)
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        with mock.patch(
            "webui.frozen_browser_identity.cleanup_frozen_task_browser",
            return_value=cleanup,
        ):
            response = app.test_client().post(
                f"/api/execute-search/{run_id}/cancel"
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(task["stop_event"].is_set())
        self.assertEqual(task["status"], "cancelled")
        self.assertIsNone(self.store.get_screening_run(run_id))
        with self.assertRaises(KeyError):
            self.store.get_search_run(run_id)

    def test_bound_cancel_rolls_back_both_runs_when_track_write_fails(self):
        """Run projections cannot be left cancelled after a Track CAS failure."""
        from webui.exec_search_cancel import register_cancel_execute_search

        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-atomic-cancel-failure",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "api-atomic-cancel-failure-run"
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.create_scrape_search_run(
            run_id,
            self.profile_id,
            platform="boss",
            flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.update_search_run(run_id, status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="scrape", scrape_run_id=run_id,
        )
        with self.store._connection() as conn:
            conn.execute(
                "CREATE TRIGGER fail_cancel_track BEFORE UPDATE OF status ON flow_tracks "
                "WHEN NEW.status = 'cancelled' BEGIN SELECT RAISE(ABORT, 'injected'); END"
            )
        task = {
            "kind": "scrape",
            "status": "running",
            "stop_event": threading.Event(),
            "logs": [],
            "platform": "boss",
        }
        ctx = SimpleNamespace(
            store=self.store,
            tasks={run_id: task},
            lock=threading.RLock(),
            operational_errors=(Exception,),
            clear_auto_screen=mock.Mock(),
        )
        app = Flask(__name__)
        app.config["BROWSER_ACCOUNTS_PATH"] = str(
            pathlib.Path(self.temp.name) / "accounts.json"
        )
        register_cancel_execute_search(app, ctx)
        cleanup = SimpleNamespace(ok=True, as_dict=lambda: {})
        with mock.patch(
            "webui.frozen_browser_identity.cleanup_frozen_task_browser",
            return_value=cleanup,
        ):
            response = app.test_client().post(
                f"/api/execute-search/{run_id}/cancel"
            )

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "running")
        self.assertEqual(self.store.get_search_run(run_id)["status"], "running")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["status"],
            "running",
        )

    def test_flow_action_maps_missing_platform_identity_to_stable_409(self):
        from webui.flow_task_coordinator import MissingPlatformIdentityError

        flow = self.service.start_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-missing-platform-action",
            confirmed_filters={"boss": {}},
        )
        with mock.patch.object(
            self.service,
            "operate_track",
            side_effect=MissingPlatformIdentityError("missing platform"),
        ):
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/pause",
                json={"profile_id": self.profile_id},
            )

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(
            response.get_json()["error_code"], "platform_identity_missing"
        )

    def test_flow_results_read_failure_is_not_reported_as_empty_success(self):
        flow = self.service.start_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-results-read-failure",
            confirmed_filters={"boss": {}},
        )
        with mock.patch.object(
            self.service,
            "get_flow_results",
            side_effect=sqlite3.OperationalError("database busy"),
        ):
            response = self.client.get(
                f"/api/flows/{flow['id']}/results?profile_id={self.profile_id}"
            )

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(
            response.get_json()["error_code"], "flow_results_unavailable"
        )


class B096V2FlowApiContractTests(unittest.TestCase):
    """T011: V2 removes the per-platform confirmation gate."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-api-v2-")
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        store = TaskStore(self.db_path)
        self.profile_id = "profile-api-v2"
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, ?, '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (self.profile_id, self.profile_id),
            )
        self.store = store
        self.enabled = {"boss": True, "zhilian": True}
        self.service = FlowService(
            store,
            platform_enabled=lambda platform: self.enabled[platform],
        )
        self.app = Flask(__name__)
        register_flow_routes(
            self.app,
            SimpleNamespace(store=store, flow_service=self.service),
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_all_starts_without_confirmation_gate(self):
        response = self.client.post(
            "/api/flows",
            json={
                "profile_id": self.profile_id,
                "selection": "all",
                "start_key": "v2-api-start",
                "confirmed_filters": {
                    "boss": {"snapshotVersion": 2},
                    "zhilian": {"snapshotVersion": 2},
                },
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["flow"]["selection"], "all")
        self.assertNotIn("confirmations_required", response.get_data(as_text=True))
        tracks = payload["flow"]["tracks"]
        self.assertEqual(
            {track["platform"] for track in tracks}, {"boss", "zhilian"},
        )

    def test_profile_selection_and_platform_availability_are_still_checked(self):
        missing_profile = self.client.post(
            "/api/flows",
            json={"profile_id": "missing", "selection": "all"},
        )
        self.assertEqual(missing_profile.status_code, 404)

        self.enabled["zhilian"] = False
        disabled = self.client.post(
            "/api/flows",
            json={
                "profile_id": self.profile_id,
                "selection": "all",
                "start_key": "v2-disabled",
            },
        )
        self.assertIn(disabled.status_code, (409, 503))
        self.assertEqual(disabled.get_json()["error_code"], "platform_disabled")


class B096PreflightFlowActionTests(unittest.TestCase):
    """Flow actions own recovery of preflight-paused Tracks without a Run."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-preflight-action-")
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.context = self.app.config["PIPELINE_CONTEXT"]
        self.profile_id = "profile-preflight-action"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'preflight', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _seed_paused_preflight_flow(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="api-preflight-resume-flow",
            confirmed_filters={
                "boss": {"keyword": ["Python"], "city": ["上海"], "pages": 1},
                "zhilian": {"keyword": ["Python"], "city": ["上海"], "pages": 1},
            },
        )
        service = self.app.config["PIPELINE_CONTEXT"].flow_service
        for platform in ("boss", "zhilian"):
            service.save_track_submission_snapshot(
                flow_id=flow["id"], platform=platform,
                profile_id=self.profile_id,
                snapshot={
                    "script_params": {
                        "keyword": "Python", "city": ["上海"], "pages": 1,
                    },
                },
            )
            service.record_preflight_failure(
                flow_id=flow["id"], platform=platform,
                profile_id=self.profile_id,
                error_code="browser_busy", recoverable=True,
            )
        return flow

    def test_preflight_resume_reclaims_both_platform_tracks_without_execute_search(self):
        flow = self._seed_paused_preflight_flow()
        lane = self.app.config["PLATFORM_EXECUTION_CAPACITY"]
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch.object(
                    self.context, "check_resume_block", return_value=(True, "", ""),
                ), mock.patch.object(
                    lane, "submit", return_value=mock.Mock(),
                ) as submit:
            for platform in ("boss", "zhilian"):
                response = self.client.post(
                    f"/api/flows/{flow['id']}/tracks/{platform}/resume",
                    json={"profile_id": self.profile_id},
                )
                self.assertEqual(response.status_code, 200, response.get_json())

        current = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        tracks = {track["platform"]: track for track in current["tracks"]}
        self.assertEqual({track["status"] for track in tracks.values()}, {"running"})
        self.assertTrue(all(track["scrape_run_id"] for track in tracks.values()))
        self.assertEqual(
            [call.args[0] for call in submit.call_args_list], ["boss", "zhilian"],
        )

    def test_preflight_resume_preserves_execute_search_default_pages(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="api-preflight-default-pages",
            confirmed_filters={"boss": {"keyword": ["Python"], "city": ["上海"]}},
        )
        service = self.app.config["PIPELINE_CONTEXT"].flow_service
        service.save_track_submission_snapshot(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            snapshot={
                "script_params": {"keyword": "Python", "city": ["上海"]},
            },
        )
        service.record_preflight_failure(
            flow_id=flow["id"], platform="boss", profile_id=self.profile_id,
            error_code="browser_busy", recoverable=True,
        )
        lane = self.app.config["PLATFORM_EXECUTION_CAPACITY"]
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch.object(
                    self.context, "check_resume_block", return_value=(True, "", ""),
                ), mock.patch.object(
                    lane, "submit", return_value=mock.Mock(),
                ):
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/resume",
                json={"profile_id": self.profile_id},
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        run = self.store.get_screening_run(track["scrape_run_id"])
        self.assertEqual(run["execution_params"]["script_params"]["pages"], 3)

    def test_preflight_resume_is_idempotent_and_keeps_sibling_unchanged(self):
        flow = self._seed_paused_preflight_flow()
        lane = self.app.config["PLATFORM_EXECUTION_CAPACITY"]
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch.object(
                    self.context, "check_resume_block", return_value=(True, "", ""),
                ), mock.patch.object(
                    lane, "submit", return_value=mock.Mock(),
                ) as submit:
            for platform in ("boss", "zhilian"):
                first = self.client.post(
                    f"/api/flows/{flow['id']}/tracks/{platform}/resume",
                    json={"profile_id": self.profile_id},
                )
                self.assertEqual(first.status_code, 200, first.get_json())
                after_first = self.store.get_flow(
                    flow["id"], profile_id=self.profile_id,
                )["tracks"]
                sibling_platform = "zhilian" if platform == "boss" else "boss"
                sibling_before = next(
                    track for track in after_first
                    if track["platform"] == sibling_platform
                )
                second = self.client.post(
                    f"/api/flows/{flow['id']}/tracks/{platform}/resume",
                    json={"profile_id": self.profile_id},
                )
                self.assertEqual(second.status_code, 200, second.get_json())
                current = self.store.get_flow(
                    flow["id"], profile_id=self.profile_id,
                )["tracks"]
                resumed = next(track for track in current if track["platform"] == platform)
                sibling = next(track for track in current if track["platform"] == sibling_platform)
                self.assertEqual(resumed["scrape_run_id"], next(
                    track for track in after_first if track["platform"] == platform
                )["scrape_run_id"])
                self.assertEqual(sibling, sibling_before)

        self.assertEqual(submit.call_count, 2)

    def test_preflight_resume_login_unready_keeps_track_paused(self):
        flow = self._seed_paused_preflight_flow()
        with mock.patch.object(
            self.context, "activate_run_browser",
            side_effect=RuntimeError("login required"),
        ), mock.patch.object(
            self.app.config["PLATFORM_EXECUTION_CAPACITY"], "submit",
        ) as submit:
            for platform in ("boss", "zhilian"):
                response = self.client.post(
                    f"/api/flows/{flow['id']}/tracks/{platform}/resume",
                    json={"profile_id": self.profile_id},
                )
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(
                    response.get_json()["error_code"], "source_cdp_unavailable",
                )

        current = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        boss = next(track for track in current["tracks"] if track["platform"] == "boss")
        sibling = next(track for track in current["tracks"] if track["platform"] == "zhilian")
        self.assertEqual(boss["status"], "paused")
        self.assertIsNone(boss["scrape_run_id"])
        self.assertEqual(sibling["status"], "paused")
        submit.assert_not_called()

    def test_preflight_resume_runs_default_login_block_check(self):
        flow = self._seed_paused_preflight_flow()
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch.object(
                    self.context,
                    "check_resume_block",
                    return_value=(False, "source_login_required", "登录已失效"),
                ) as checker, mock.patch.object(
                    self.app.config["PLATFORM_EXECUTION_CAPACITY"], "submit",
                ) as submit:
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/resume",
                json={"profile_id": self.profile_id},
            )
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["error_code"], "source_login_required")
        checker.assert_called_once()
        probe_id = checker.call_args.args[0]["id"]
        self.assertIsNone(self.store.get_screening_run(probe_id))
        with self.store._connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM tasks WHERE id = ?", (probe_id,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM task_logs WHERE task_id = ?", (probe_id,)).fetchone()[0],
                0,
            )
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        self.assertEqual(track["status"], "paused")
        self.assertIsNone(track["scrape_run_id"])
        submit.assert_not_called()

    def test_preflight_resume_default_checker_maps_cdp_failure(self):
        flow = self._seed_paused_preflight_flow()
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    return_value=(False, "debug port unavailable"),
                ), mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready",
                    return_value=(False, "debug port unavailable"),
                ), mock.patch.object(
                    self.app.config["PLATFORM_EXECUTION_CAPACITY"], "submit",
                ) as submit:
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/resume",
                json={"profile_id": self.profile_id},
            )
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["error_code"], "source_cdp_unavailable")
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        self.assertEqual(track["status"], "paused")
        self.assertIsNone(track["scrape_run_id"])
        submit.assert_not_called()

    def test_preflight_resume_default_checker_maps_login_failure_for_both_lanes(self):
        flow = self._seed_paused_preflight_flow()

        class _LoginFailureSource:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            def recheck_login(self):
                return SimpleNamespace(ok=False, failed_code="source_login_required")

        lane = self.app.config["PLATFORM_EXECUTION_CAPACITY"]
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    return_value=(True, ""),
                ), mock.patch.object(
                    self.context, "source_class", _LoginFailureSource,
                ), mock.patch(
                    "webui.source.ZhilianCdpSource", _LoginFailureSource,
                ), mock.patch.object(lane, "submit") as submit:
            for platform in ("boss", "zhilian"):
                response = self.client.post(
                    f"/api/flows/{flow['id']}/tracks/{platform}/resume",
                    json={"profile_id": self.profile_id},
                )
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(
                    response.get_json()["error_code"], "source_login_required",
                )
        current = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        self.assertEqual({track["status"] for track in current["tracks"]}, {"paused"})
        self.assertTrue(all(track["scrape_run_id"] is None for track in current["tracks"]))
        submit.assert_not_called()

    def test_stop_and_submission_claim_race_has_one_winner(self):
        flow = self._seed_paused_preflight_flow()
        barrier = threading.Barrier(2)
        outcomes = []

        def _claim():
            barrier.wait()
            try:
                self.store.claim_flow_track_submission(
                    flow["id"], "boss", profile_id=self.profile_id,
                )
                outcomes.append("claim")
            except FlowConflictError:
                outcomes.append("claim_conflict")

        def _stop():
            barrier.wait()
            try:
                self.store.stop_flow_track_without_run(
                    flow["id"], "boss", profile_id=self.profile_id,
                )
                outcomes.append("stop")
            except FlowConflictError:
                outcomes.append("stop_conflict")

        workers = [threading.Thread(target=_claim), threading.Thread(target=_stop)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(
            {item.endswith("conflict") for item in outcomes}, {False, True},
        )
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        if "stop" in outcomes:
            self.assertEqual(track["status"], "stopped")
            self.assertIsNone(track["scrape_run_id"])
        else:
            self.assertEqual(track["status"], "running")

    def test_claimed_resume_flow_resume_error_rolls_track_back_to_paused(self):
        flow = self._seed_paused_preflight_flow()
        service = self.app.config["PIPELINE_CONTEXT"].flow_service
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch.object(
                    self.context, "check_resume_block", return_value=(True, "", ""),
                ), mock.patch.object(
                    FlowSubmissionService,
                    "create_scrape_records",
                    side_effect=FlowResumeError("scope_validation_failed"),
                ):
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/resume",
                json={"profile_id": self.profile_id},
            )
        self.assertEqual(response.status_code, 409, response.get_json())
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        self.assertEqual(track["status"], "paused")
        self.assertIsNone(track["scrape_run_id"])

    def test_claim_failure_compensation_error_is_observable(self):
        flow = self._seed_paused_preflight_flow()
        service = self.app.config["PIPELINE_CONTEXT"].flow_service
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch.object(
                    self.context, "check_resume_block", return_value=(True, "", ""),
                ), mock.patch.object(
                    FlowSubmissionService,
                    "create_scrape_records",
                    side_effect=FlowResumeError("scope_validation_failed"),
                ), mock.patch.object(
                    service,
                    "mark_submission_failed",
                    side_effect=RuntimeError("injected closure failure"),
                ):
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/resume",
                json={"profile_id": self.profile_id},
            )
        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(
            response.get_json()["error_code"], "flow_failure_compensation_failed",
        )

    def test_claimed_resume_conflict_after_future_submission_compensates(self):
        flow = self._seed_paused_preflight_flow()
        future = mock.Mock()
        future.cancel.return_value = True
        with mock.patch.object(self.context, "activate_run_browser"), \
                mock.patch.object(
                    self.context, "check_resume_block", return_value=(True, "", ""),
                ), mock.patch.object(
                    self.context, "register_pipeline_task", wraps=self.context.register_pipeline_task,
                ), mock.patch.object(
                    FlowSubmissionService, "submit_scrape", return_value=future,
                ), mock.patch(
                    "webui.flow_future.attach_scrape_future_failure",
                    side_effect=FlowConflictError("late claim conflict"),
                ):
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/resume",
                json={"profile_id": self.profile_id},
            )
        self.assertEqual(response.status_code, 409, response.get_json())
        future.cancel.assert_called_once()
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        self.assertEqual(track["status"], "paused")
        self.assertIsNotNone(track["scrape_run_id"])
        self.assertEqual(
            self.store.get_screening_run(track["scrape_run_id"])["status"], "paused",
        )

    def test_invalid_execute_search_does_not_pollute_submission_snapshot(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="invalid-snapshot-input",
            confirmed_filters={"boss": {}},
        )
        response = self.client.post(
            "/api/execute-search",
            json={
                "flow_id": flow["id"], "profile_id": self.profile_id,
                "platform": "boss", "script_params": {"city": ["上海"]},
            },
        )
        self.assertEqual(response.status_code, 400)
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["submission_snapshot"], {})

    def test_replayed_valid_snapshot_cannot_overwrite_existing_retry_payload(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key="replay-snapshot-input",
            confirmed_filters={"boss": {}},
        )
        ctx = self.context
        body = {
            "flow_id": flow["id"], "profile_id": self.profile_id,
            "platform": "boss",
            "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
        }
        with mock.patch.object(ctx, "browser_busy", return_value=True):
            first = self.client.post("/api/execute-search", json=body)
            self.assertEqual(first.status_code, 409)
        with mock.patch.object(ctx, "browser_busy", return_value=False):
            second = self.client.post(
                "/api/execute-search",
                json={
                    **body,
                    "script_params": {
                        "keyword": "Java", "city": ["北京"], "pages": 1,
                    },
                },
            )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["error_code"], "flow_conflict")
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["submission_snapshot"]["script_params"]["keyword"], "Python")

    def test_submission_snapshot_write_rejects_a_claimed_track(self):
        flow = self._seed_paused_preflight_flow()
        before = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        with self.assertRaises(FlowConflictError):
            self.store.save_flow_track_submission(
                flow["id"], "boss", profile_id=self.profile_id,
                snapshot={
                    "script_params": {
                        "keyword": "Java", "city": ["北京"], "pages": 1,
                    },
                },
            )
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        original = next(item for item in before["tracks"] if item["platform"] == "boss")
        self.assertEqual(track["submission_snapshot"], original["submission_snapshot"])

    def test_preflight_resume_login_resolver_failure_is_retryable(self):
        flow = self._seed_paused_preflight_flow()
        with mock.patch(
            "webui.platforms.resolve_login_space",
            side_effect=RuntimeError("login state unavailable"),
        ), mock.patch.object(
            self.app.config["PLATFORM_EXECUTION_CAPACITY"], "submit",
        ) as submit:
            response = self.client.post(
                f"/api/flows/{flow['id']}/tracks/boss/resume",
                json={"profile_id": self.profile_id},
            )
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["error_code"], "source_cdp_unavailable")
        track = next(
            item for item in self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"]
            if item["platform"] == "boss"
        )
        self.assertEqual(track["status"], "paused")
        self.assertIsNone(track["scrape_run_id"])
        submit.assert_not_called()

    def test_preflight_stop_without_run_closes_only_target_track(self):
        flow = self._seed_paused_preflight_flow()
        response = self.client.post(
            f"/api/flows/{flow['id']}/tracks/boss/stop",
            json={"profile_id": self.profile_id},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        current = self.store.get_flow(flow["id"], profile_id=self.profile_id)
        boss = next(track for track in current["tracks"] if track["platform"] == "boss")
        sibling = next(track for track in current["tracks"] if track["platform"] == "zhilian")
        self.assertEqual(boss["status"], "stopped")
        self.assertEqual(sibling["status"], "paused")

if __name__ == "__main__":
    unittest.main()
