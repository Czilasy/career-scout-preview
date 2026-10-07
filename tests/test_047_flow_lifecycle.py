"""047 US5 生命周期竞争回归：当前尝试写权、收尾窗口与迟到回调。

覆盖（T016）：
- finish claim（用户结束保存）期间，迟到 worker 失败不得把 Track 改成
  failed（否则用户点“结束并保存”会被误拒绝，且界面先闪错误再进 04）；
- 新尝试（retry/rebind）之后，旧 run 的失败收口不得改写已绑定新 run 的 Track；
- 同 run resume 后，旧 worker 实例（相同 run id）的比对必须可识别；
- 兄弟轨道与已保存结果不受任何迟到路径影响。
竞争全部用 Event/一次性桩控制，不等待真实网络。
"""

from __future__ import annotations

import pathlib
import tempfile
import threading
import unittest

from webui.store import TaskStore


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s047-life-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        with self.store._connection() as conn:
            for pid in ("p1",):
                conn.execute(
                    "INSERT INTO candidate_profiles "
                    "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                    "VALUES (?, ?, '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                    (pid, pid),
                )
        self.profile = "p1"

    def tearDown(self):
        self.temp.cleanup()

    def make_flow(self, platform="boss"):
        flow = self.store.create_flow(
            profile_id=self.profile, selection=platform, start_key=f"life-{platform}",
            confirmed_filters={platform: {}},
        )
        return flow, flow["tracks"][0]

    def make_scrape_run(self, flow, track, run_id):
        self.store.create_search_run(
            self.profile,
            {"flow_id": flow["id"], "track_id": track["id"], "platform": track["platform"]},
            "ai",
        ) if False else None
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"],
                "platform": track["platform"],
            },
        )
        self.store.bind_flow_track_runs(
            flow["id"], track["platform"], profile_id=self.profile,
            scrape_run_id=run_id,
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        return run_id


class FinishClaimWindowTests(_Fixture):
    """claim 之后、结果尚未绑定期间的迟到失败必须被挡下。"""

    def test_public_closure_keeps_live_finish_and_worker_claim(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from webui.flow_task_state import close_flow_task_state

        flow, track = self.make_flow()
        run_id = self.make_scrape_run(flow, track, "live-finish")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile,
            status="running", stage="scrape",
        )
        self.store.claim_flow_finish(
            flow["id"], "boss", profile_id=self.profile, task_run_id=run_id,
        )
        task = {"status": "running", "error": "", "finish_requested": True}
        ctx = SimpleNamespace(
            store=self.store, lock=threading.RLock(), tasks={run_id: task},
            clear_auto_screen=Mock(), schedule_pipeline_task_cleanup=Mock(),
            release_worker_resume_claims=Mock(),
        )
        close_flow_task_state(
            ctx, task_id=run_id, scrape_task_id=run_id,
            status="failed", error_code="scrape_failed", stage="scrape",
        )
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["error"], "")
        ctx.clear_auto_screen.assert_not_called()
        ctx.schedule_pipeline_task_cleanup.assert_not_called()
        ctx.release_worker_resume_claims.assert_not_called()
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "interrupted")
        self.assertTrue(any(
            e["type"] == "late_callback" for e in self.store.list_task_events(run_id)
        ))

    def test_late_worker_failure_does_not_flip_claiming_track(self):
        flow, track = self.make_flow()
        run_id = self.make_scrape_run(flow, track, "scrape-a")
        self.store.update_flow_track(
            flow["id"], track["platform"], profile_id=self.profile,
            status="running", stage="scrape",
        )
        claim = self.store.claim_flow_finish(
            flow["id"], track["platform"], profile_id=self.profile,
            task_run_id=run_id,
        )
        self.assertTrue(claim.get("claimed", True))
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["error_code"], "user_finished")

        blocked = self.store.close_flow_task_state_atomic(
            flow["id"], track["platform"], self.profile,
            task_run_id=run_id, scrape_run_id=run_id,
            status="failed", error_code="scrape_failed",
            error_reason="late worker failure",
        )
        tracks = {t["id"]: t for t in blocked["tracks"]}
        self.assertEqual(
            tracks[track["id"]]["status"], "running",
            "claim 窗口内的迟到失败不得把 Track 改成 failed",
        )
        # late_callback 审计可查
        events = self.store.list_task_events(run_id)
        self.assertTrue(
            any(e.get("type") == "late_callback" for e in events),
            "claim 窗口的迟到回调必须留安全审计",
        )

        # 结束保存随后可以正常提交（不会被“已结束”误拒）
        snapshot = self.store.create_screening_run(
            "result-a", profile_id=self.profile,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": track["platform"],
            },
        ) if False else None
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs "
                "(id, status, frozen_filters_json, source_count, match_count, mismatch_count, "
                "created_at, updated_at, record_kind, profile_id, execution_params_json) "
                "VALUES ('result-a', 'succeeded', '{}', 0, 0, 0, "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'result_snapshot', ?, ?)",
                (self.profile, '{"flow_id": "' + flow["id"] + '", "track_id": "' + track["id"] + '", "platform": "' + track["platform"] + '"}'),
            )
        finished = self.store.finish_flow_task_atomic(
            flow["id"], track["platform"], profile_id=self.profile,
            task_run_id=run_id, result_run_id="result-a",
        )
        tracks = {t["id"]: t for t in finished["tracks"]}
        self.assertEqual(tracks[track["id"]]["status"], "stopped")


class SupersededAttemptTests(_Fixture):
    """新尝试绑定之后，旧 run 的收口不得改写 Track。"""

    def test_public_closure_preserves_unknown_source_reason(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from webui.flow_task_state import close_flow_task_state
        from webui.pipeline_exec_status import user_visible_failure_reason

        flow, track = self.make_flow()
        run_id = self.make_scrape_run(flow, track, "source-unknown")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile, status="running", stage="scrape",
        )
        ctx = SimpleNamespace(
            store=self.store, lock=threading.RLock(),
            tasks={run_id: {"status": "running", "error": ""}},
            clear_auto_screen=Mock(), schedule_pipeline_task_cleanup=Mock(),
            release_worker_resume_claims=Mock(),
        )
        close_flow_task_state(
            ctx, task_id=run_id, scrape_task_id=run_id,
            status="failed", error_code="source_status_unclear", stage="scrape",
        )
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["error_code"], "source_status_unclear")
        self.assertEqual(ctx.tasks[run_id]["error"], user_visible_failure_reason(
            "source_status_unclear", "", "boss",
        ))
        current = self.store.get_flow(flow["id"], profile_id=self.profile)["tracks"][0]
        self.assertEqual(current["error_code"], "source_status_unclear")

    def test_old_run_closure_does_not_touch_rebound_track(self):
        flow, track = self.make_flow()
        old_run = self.make_scrape_run(flow, track, "scrape-old")
        self.store.update_screening_run(old_run, status="failed", error_code="scrape_failed")

        new_run = "scrape-new"
        self.store.create_screening_run(
            new_run, profile_id=self.profile,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"],
                "platform": track["platform"],
            },
        )
        self.store.bind_flow_track_runs(
            flow["id"], track["platform"], profile_id=self.profile,
            scrape_run_id=new_run,
        )
        self.store.update_screening_run(new_run, status="running", current_stage="scrape")
        self.store.update_flow_track(
            flow["id"], track["platform"], profile_id=self.profile,
            status="running", stage="scrape",
        )

        result = self.store.close_flow_task_state_atomic(
            flow["id"], track["platform"], self.profile,
            task_run_id=old_run, scrape_run_id=old_run,
            status="failed", error_code="scrape_failed",
            error_reason="late callback from superseded attempt",
        )
        tracks = {t["id"]: t for t in result["tracks"]}
        self.assertEqual(
            tracks[track["id"]]["status"], "running",
            "旧尝试的迟到收口不得改写已绑定新 run 的 Track",
        )
        self.assertEqual(tracks[track["id"]]["scrape_run_id"], new_run)
        # 迟到事实可查询
        events = self.store.list_task_events(old_run)
        self.assertTrue(
            any(e.get("type") == "late_callback" for e in events),
            "迟到回调必须留安全审计",
        )


class WhiteboxLateCallbackTests(_Fixture):
    """047 C3：已终结白箱的迟到 unit/page 事实只留诊断，不改结论。"""

    def _whitebox(self, run_id="scrape-wb"):
        from webui.whitebox import WhiteboxService
        service = WhiteboxService(self.store)
        plan = {"stages": ["scrape_list"], "units": [
            {"unit_key": "a|上海", "unit_kind": "keyword_city",
             "stage": "scrape_list", "planned_pages": 3},
            {"unit_key": "b|北京", "unit_kind": "keyword_city",
             "stage": "scrape_list", "planned_pages": 3},
        ]}
        ref = service.begin("scrape", run_id, plan)
        return service, ref

    def _completed_fact(self, key, attempt=1):
        return {
            "idempotency_key": f"scope-completed:{key}:{attempt}",
            "event_type": "scope_completed", "occurred_at": "2026-10-07T00:00:00Z",
            "stage": "scrape_list", "unit_kind": "keyword_city", "unit_key": key,
            "attempt_no": attempt, "required_evidence": True,
            "payload": {"scope_complete": True, "source_exhausted": True,
                        "stop_reason": "target_reached", "returned_total_count": 1,
                        "unit_unique_count": 1},
        }

    def test_late_failure_after_interrupted_close_keeps_conclusion(self):
        service, ref = self._whitebox()
        service.record(ref, self._completed_fact("a|上海"))
        service.record(ref, self._completed_fact("b|北京"))
        closed = service.finalize(ref, lifecycle_end="interrupted")
        self.assertEqual(closed["conclusion"], "interrupted")
        revision = closed["revision"]

        # 用户结束保存之后，旧 worker 的迟到失败只留诊断
        service.record(ref, {
            "idempotency_key": "unit-failed:b|北京:1:late",
            "event_type": "unit_failed", "occurred_at": "2026-10-07T00:00:05Z",
            "stage": "scrape_list", "unit_kind": "keyword_city",
            "unit_key": "b|北京", "attempt_no": 1, "required_evidence": True,
            "severity": "error",
            "payload": {"error_code": "source_cdp_unavailable",
                        "error_reason": "late worker failure"},
        })
        report = service.report("scrape", "scrape-wb")
        self.assertEqual(
            report["integrity"]["conclusion"], "interrupted",
            "已终结的正常收尾结论不得被迟到失败覆盖",
        )
        self.assertEqual(report["integrity"]["revision"], revision)
        events = self.store.list_whitebox_events(ref.id)
        self.assertTrue(
            any(e.get("event_type") == "late_callback" for e in events),
            "迟到事实必须留 late_callback 诊断",
        )
        units = {u["unit_key"]: u for u in self.store.list_whitebox_units(ref.id)}
        self.assertEqual(units["b|北京"]["status"], "succeeded")

    def test_resume_reopens_and_accepts_new_evidence(self):
        service, ref = self._whitebox()
        service.record(ref, self._completed_fact("a|上海"))
        service.finalize(ref, lifecycle_end="failed")
        service.resume("scrape", "scrape-wb", ref_plan(service, ref))
        service.record(ref, self._completed_fact("b|北京"))
        result = service.finalize(ref)
        self.assertEqual(result["conclusion"], "succeeded")

    def test_late_page_fact_after_close_keeps_units_and_revision(self):
        """已终结后迟到的页级事实不得把单元改回 running 或改结论。"""
        service, ref = self._whitebox()
        service.record(ref, self._completed_fact("a|上海"))
        service.record(ref, self._completed_fact("b|北京"))
        closed = service.finalize(ref, lifecycle_end="interrupted")
        revision = closed["revision"]

        service.record(ref, {
            "idempotency_key": "page:b|北京:1:9",
            "event_type": "page_completed", "occurred_at": "2026-10-07T00:00:09Z",
            "stage": "scrape_list", "unit_kind": "keyword_city",
            "unit_key": "b|北京", "attempt_no": 1, "required_evidence": True,
            "payload": {"page": 9, "planned_pages": 3, "returned_count": 30,
                        "new_unique_count": 30, "has_more": True, "resume_page": 10},
        })
        report = service.report("scrape", "scrape-wb")
        self.assertEqual(report["integrity"]["conclusion"], "interrupted")
        self.assertEqual(report["integrity"]["revision"], revision)
        units = {u["unit_key"]: u for u in self.store.list_whitebox_units(ref.id)}
        self.assertEqual(units["b|北京"]["completed_pages"], 0)
        events = self.store.list_whitebox_events(ref.id)
        self.assertTrue(any(e.get("event_type") == "late_callback" for e in events))


def ref_plan(service, ref):
    import json
    run = service.store.get_whitebox_run_by_id(ref.id)
    return json.loads(run["plan_json"])


class HelperContractTests(unittest.TestCase):
    """flow_run_lifecycle 公开 helper 的行为契约。"""

    def test_worker_instance_superseded_detects_replacement(self):
        from webui.flow_run_lifecycle import worker_instance_superseded

        class _Ctx:
            tasks = {}
            lock = threading.RLock()

        ctx = _Ctx()
        original = {"status": "running"}
        ctx.tasks["run-1"] = original
        self.assertFalse(worker_instance_superseded(ctx, "run-1", original))
        replacement = {"status": "running"}
        ctx.tasks["run-1"] = replacement
        self.assertTrue(worker_instance_superseded(ctx, "run-1", original))

    def test_sync_blocked_reason_reports_superseded(self):
        from webui.flow_run_lifecycle import sync_blocked_reason

        temp = tempfile.TemporaryDirectory(prefix="s047-life2-")
        self.addCleanup(temp.cleanup)
        store = TaskStore(pathlib.Path(temp.name) / "state" / "webui.db")
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES ('p1', 'p1', '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            )
        flow = store.create_flow(
            profile_id="p1", selection="boss", start_key="k1", confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        store.create_screening_run(
            "new-run", profile_id="p1",
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": "boss",
            },
        )
        store.bind_flow_track_runs(
            flow["id"], "boss", profile_id="p1", scrape_run_id="new-run",
        )
        self.assertEqual(
            sync_blocked_reason(
                store, run_id="old-run", flow_id=flow["id"], platform="boss",
            ),
            "superseded_attempt",
        )
        self.assertEqual(
            sync_blocked_reason(
                store, run_id="new-run", flow_id=flow["id"], platform="boss",
            ),
            "",
        )



class AiRetryLateCallbackTests(_Fixture):
    """复核 P1-1：AI 重试后，旧 AI 回调不得改写新尝试、结果或兄弟轨道。"""

    def _seed_ai_failed_with_result(self, flow):
        track = next(t for t in flow["tracks"] if t["platform"] == "boss")
        scrape_id = "ai-late-scrape"
        self.store.create_screening_run(
            scrape_id, profile_id=self.profile,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": "boss",
            },
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile, platform="boss",
            flow_id=flow["id"], track_id=track["id"],
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile, scrape_run_id=scrape_id,
        )
        self.store.update_screening_run(
            scrape_id, status="running", current_stage="scrape",
        )
        old_screen = "ai-late-screen-old"
        self.store.create_screening_run(
            old_screen, profile_id=self.profile,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": "boss",
                "scrape_task_id": scrape_id,
            },
        )
        self.store.update_screening_run(
            old_screen, status="running", current_stage="ai_rough",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile,
            status="running", stage="ai", screen_run_id=old_screen,
        )
        result_id = self.store.save_pipeline_result(
            {"platform": "boss", "jobs": [
                {"platform_job_id": "ai-late-1", "verdict": "match"},
            ], "dropped": []},
            {"platform": "boss"},
            profile_id=self.profile,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": track["id"], "scrape_task_id": scrape_id,
            },
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile, result_run_id=result_id,
        )
        self.store.update_screening_run(
            old_screen, status="failed", current_stage="ai_rough",
            error_code="flow_ai_start_failed", error_reason="AI 筛选启动失败",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile,
            status="failed", stage="ai", error_code="flow_ai_start_failed",
            reason="AI 筛选启动失败",
        )
        return track, scrape_id, old_screen, result_id

    def test_old_ai_callback_after_ai_retry_cannot_flip_new_track(self):
        flow = self.store.create_flow(
            profile_id=self.profile, selection="all", start_key="ai-late-all",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        flow = self.store.get_flow(flow["id"], profile_id=self.profile)
        track, scrape_id, old_screen, result_id = self._seed_ai_failed_with_result(flow)
        current = next(
            t for t in self.store.get_flow(flow["id"], profile_id=self.profile)["tracks"]
            if t["id"] == track["id"]
        )
        claim = self.store.claim_flow_track_retry(
            flow["id"], "boss", profile_id=self.profile,
            expected_track_id=track["id"],
            expected_run_id=old_screen,
            expected_updated_at=current["updated_at"],
            stage="ai",
            attempt_params={"scrape_task_id": scrape_id},
        )
        self.assertTrue(claim.get("claimed"), claim)
        new_screen = claim["new_run_id"]

        result = self.store.close_flow_task_state_atomic(
            flow["id"], "boss", self.profile,
            task_run_id=old_screen, scrape_run_id=scrape_id,
            status="failed", error_code="flow_ai_start_failed",
            error_reason="old AI late failure", stage="ai",
        )
        boss = next(t for t in result["tracks"] if t["platform"] == "boss")
        self.assertEqual(
            boss["status"], "running",
            "AI 重试后的旧 AI 失败回调不得把新轨道改回 failed",
        )
        self.assertEqual(boss["screen_run_id"], new_screen)
        self.assertEqual(boss["scrape_run_id"], scrape_id)
        self.assertEqual(
            boss["result_run_id"], result_id,
            "新尝试结果提交前的旧结果指针必须保留",
        )
        self.assertEqual(
            self.store.get_screening_run(new_screen)["status"], "running",
        )
        sibling = next(t for t in result["tracks"] if t["platform"] == "zhilian")
        self.assertEqual(sibling["status"], "queued")
        events = self.store.list_task_events(old_screen)
        self.assertTrue(
            any(e.get("type") == "late_callback" for e in events),
            "旧 AI 回调必须留安全审计",
        )




class SameRunWorkerReplacementTests(_Fixture):
    """复核 P1-2：同一 run 恢复后，旧 worker 实例不得覆盖新 worker。

    真实 runner 协作回归：经 run_pipeline_task 的正式入口拿到旧 worker 的
    task 实例，再模拟恢复把 ctx.tasks[run_id] 换成新实例；随后旧 worker 的
    收口必须只写 durable 事实、不改新实例内存，并把旧实例移出替换后的新轨。
    """

    def _install_run(self, store, run_id):
        store.create_screening_run(run_id, source_count=1, execution_params={"platform": "boss"})
        store.update_screening_run(run_id, status="running", current_stage="scrape")

    def test_real_lane_auto_ai_handoff_does_not_hold_global_task_lock(self):
        from types import SimpleNamespace
        from unittest import mock
        from webui.app_support import PlatformExecutionCapacity
        from webui.constants import _OPERATIONAL_ERRORS
        from webui.flow_service import FlowService
        from webui.runners.pipeline_task import run_pipeline_task

        flow, track = self.make_flow()
        run_id = self.make_scrape_run(flow, track, "inline-ai-lock")
        task = {"kind": "scrape", "status": "queued", "progress": {}, "logs": [],
                "result": None, "error": "", "stop_event": threading.Event()}
        ctx = SimpleNamespace(
            store=self.store, operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(), tasks={run_id: task},
            app=SimpleNamespace(config={"RESULT_DIR": tempfile.gettempdir()}),
            flow_service=FlowService(self.store), is_user_finished=lambda _rid: False,
            activate_task_browser=mock.Mock(), make_cdp_source=lambda **kw: object(),
            release_worker_resume_claims=mock.Mock(), schedule_pipeline_task_cleanup=mock.Mock(),
            clear_auto_screen=mock.Mock(), consume_auto_screen=mock.Mock(), record_pause_failure=mock.Mock(),
            write_run=lambda rid, **kw: self.store.update_screening_run(rid, **kw),
        )
        acquired = threading.Event()
        probe_threads = []
        observations = []
        executor = PlatformExecutionCapacity()

        def ai_work():
            def read_sibling_task():
                with ctx.lock:
                    acquired.set()
            probe = threading.Thread(target=read_sibling_task)
            probe_threads.append(probe)
            probe.start()
            observations.append(acquired.wait(2))

        ctx.enqueue_auto_screen_for_scrape = lambda _rid: executor.submit("boss", ai_work).result()
        result = {"ok": True, "jobs": [], "completed_combos": ["kw|city"],
                  "total_scraped": 0, "integrity": {"conclusion": "succeeded"}}
        try:
            with mock.patch("webui.pipeline_exec.run_search", return_value=result):
                executor.submit("boss", run_pipeline_task, ctx, run_id,
                                {"keyword": "kw", "city": ["city"], "pages": 1}).result(timeout=8)
        finally:
            executor.shutdown()
            for probe in probe_threads:
                probe.join(timeout=2)
        self.assertEqual(observations, [True], "同平台内联 AI 期间，兄弟终止/轮询必须能读取任务锁")
        ctx.consume_auto_screen.assert_called_once_with(run_id)

    def test_detached_worker_has_no_finalization_write_authority(self):
        from types import SimpleNamespace
        from webui.pipeline_task_outcome import finalize_scrape_outcome

        run_id = "same-run-detached"
        self._install_run(self.store, run_id)
        old = {"status": "running", "stop_event": threading.Event()}
        ctx = SimpleNamespace(
            store=self.store, lock=threading.RLock(), tasks={},
            write_run=lambda rid, **kw: self.store.update_screening_run(rid, **kw),
        )
        outcome = finalize_scrape_outcome(
            ctx, run_id, {"ok": True, "jobs": [], "completed_combos": ["kw|city"],
                          "integrity": {"conclusion": "succeeded"}},
            stop_event=old["stop_event"], skip_combos=None, original_task=old,
        )
        self.assertTrue(outcome.get("superseded"))
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "running")

    def test_old_worker_combo_callback_cannot_persist_after_replacement(self):
        from types import SimpleNamespace
        from unittest import mock
        from webui.constants import _OPERATIONAL_ERRORS
        from webui.runners.pipeline_task import run_pipeline_task

        run_id = "same-run-late-combo"
        self._install_run(self.store, run_id)
        task = {
            "kind": "scrape", "status": "queued", "progress": {}, "logs": [],
            "result": None, "error": "", "stop_event": threading.Event(),
        }
        ctx = SimpleNamespace(
            store=self.store, operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(), tasks={run_id: task},
            app=SimpleNamespace(config={"RESULT_DIR": tempfile.gettempdir()}),
            is_user_finished=lambda _rid: False,
            activate_task_browser=mock.Mock(), make_cdp_source=lambda **kw: object(),
            release_worker_resume_claims=mock.Mock(),
            schedule_pipeline_task_cleanup=mock.Mock(), clear_auto_screen=mock.Mock(),
            write_run=lambda rid, **kw: self.store.update_screening_run(rid, **kw),
            record_pause_failure=mock.Mock(),
        )

        def late_search(*args, **kw):
            with ctx.lock:
                ctx.tasks[run_id] = dict(task, status="running", progress={"message": "new"})
            kw["on_combo_done"]("kw|city", [{"job_id": "old-worker-job"}], ["kw|city"])
            return {"ok": True, "jobs": [], "completed_combos": [], "total_scraped": 0}

        with mock.patch("webui.pipeline_exec.run_search", side_effect=late_search):
            run_pipeline_task(ctx, run_id, {"keyword": "kw", "city": ["city"], "pages": 1})
        self.assertEqual(self.store.load_scrape_run_jobs(run_id), [])
        self.assertIsNone(self.store.get_latest_source_attempt(run_id, "kw|city"))
        self.assertEqual(ctx.tasks[run_id]["progress"], {"message": "new"})
        ctx.release_worker_resume_claims.assert_not_called()
        ctx.schedule_pipeline_task_cleanup.assert_not_called()

    def test_replacement_after_finalize_does_not_release_new_worker_claim(self):
        from types import SimpleNamespace
        from unittest import mock
        from webui.constants import _OPERATIONAL_ERRORS
        from webui.pipeline_task_outcome import finalize_scrape_outcome
        from webui.runners.pipeline_task import run_pipeline_task

        run_id = "same-run-late-cleanup"
        self._install_run(self.store, run_id)
        old = {"kind": "scrape", "status": "queued", "progress": {}, "logs": [],
               "result": None, "error": "", "stop_event": threading.Event()}
        replacement = dict(old, status="running", progress={"message": "new"})
        ctx = SimpleNamespace(
            store=self.store, operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(), tasks={run_id: old},
            app=SimpleNamespace(config={"RESULT_DIR": tempfile.gettempdir()}),
            is_user_finished=lambda _rid: False, activate_task_browser=mock.Mock(),
            make_cdp_source=lambda **kw: object(), release_worker_resume_claims=mock.Mock(),
            schedule_pipeline_task_cleanup=mock.Mock(), clear_auto_screen=mock.Mock(),
            write_run=lambda rid, **kw: self.store.update_screening_run(rid, **kw),
            record_pause_failure=mock.Mock(), msg_user_stopped_scrape="stopped",
        )

        def finalize_then_replace(*args, **kwargs):
            outcome = finalize_scrape_outcome(*args, **kwargs)
            with ctx.lock:
                ctx.tasks[run_id] = replacement
            return outcome

        result = {"ok": False, "jobs": [], "completed_combos": [],
                  "error": "old failure", "failed_code": "source_status_unclear"}
        with mock.patch("webui.pipeline_exec.run_search", return_value=result), mock.patch(
            "webui.runners.pipeline_task.finalize_scrape_outcome", side_effect=finalize_then_replace,
        ):
            run_pipeline_task(ctx, run_id, {"keyword": "kw", "city": ["city"], "pages": 1})
        ctx.release_worker_resume_claims.assert_not_called()
        ctx.schedule_pipeline_task_cleanup.assert_not_called()
        ctx.clear_auto_screen.assert_not_called()
        self.assertEqual(replacement["status"], "running")

    def test_old_worker_finalize_cannot_overwrite_replacement_task(self):
        import threading
        from types import SimpleNamespace
        from unittest import mock
        from webui.constants import _OPERATIONAL_ERRORS
        from webui.runners.pipeline_task import run_pipeline_task

        run_id = "same-run-replaced"
        self._install_run(self.store, run_id)
        old_task = {
            "kind": "scrape", "status": "queued", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": 1, "finished_at": None,
            "stop_event": threading.Event(),
        }
        captured = {}

        def fake_source(**kwargs):
            captured["task"] = ctx.tasks[run_id]
            # 模拟同 run 恢复：替换 ctx.tasks[run_id]，旧实例被移出。
            ctx.tasks[run_id] = dict(
                old_task, status="running", result=None, error="",
                stop_event=threading.Event(),
            )
            ctx.tasks[run_id]["resumed"] = True
            return object()

        ctx = SimpleNamespace(
            store=self.store, operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(), tasks={run_id: old_task},
            app=SimpleNamespace(config={"RESULT_DIR": __import__("tempfile").gettempdir()}),
            is_user_finished=lambda _rid: False,
            activate_task_browser=mock.Mock(),
            make_cdp_source=fake_source,
            release_worker_resume_claims=mock.Mock(),
            schedule_pipeline_task_cleanup=mock.Mock(),
            clear_auto_screen=mock.Mock(),
            write_run=lambda rid, **kw: self.store.update_screening_run(rid, **kw),
            record_pause_failure=mock.Mock(),
        )
        result = {
            "ok": False, "jobs": [], "total_scraped": 0, "combinations": 1,
            "completed_combos": [], "hard_stop": True, "hard_stop_code": "internal_error",
            "error": "旧 worker 收尾失败",
        }
        with mock.patch("webui.pipeline_exec.run_search", return_value=result):
            run_pipeline_task(ctx, run_id, {"keyword": "kw", "city": ["city"], "pages": 1})

        replacement = ctx.tasks[run_id]
        self.assertTrue(replacement.get("resumed"), "替换实例不应被旧 worker 覆盖")
        self.assertNotEqual(
            replacement["status"], "failed",
            "旧 worker 实例的收口不得把替换后的新 worker 状态改成 failed",
        )
        self.assertEqual(replacement["error"], "")
        self.assertIsNone(replacement["result"])
        # 过期 worker 不推进 durable 状态；run 仍归新 worker（保持 running）。
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "running")

    def test_old_worker_finalize_keeps_replacement_progress(self):
        import threading
        from types import SimpleNamespace
        from unittest import mock
        from webui.constants import _OPERATIONAL_ERRORS
        from webui.pipeline_task_outcome import finalize_scrape_outcome

        run_id = "same-run-progress"
        self._install_run(self.store, run_id)
        old_task = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "stop_event": threading.Event(),
        }
        replacement = {
            "kind": "scrape", "status": "running",
            "progress": {"message": "新 worker 的进度"},
            "logs": [], "result": None, "error": "", "resumed": True,
            "stop_event": threading.Event(),
        }
        ctx = SimpleNamespace(
            store=self.store, operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(), tasks={run_id: replacement},
            msg_user_stopped_scrape="stopped",
            record_pause_failure=lambda *a, **k: None,
            write_run=lambda rid, **kw: self.store.update_screening_run(rid, **kw),
            publish_scrape_outcome_atomic=self.store.publish_scrape_outcome_atomic,
            get_screening_run=self.store.get_screening_run,
        )
        result = {
            "ok": False, "jobs": [], "total_scraped": 0, "combinations": 1,
            "completed_combos": [], "hard_stop": True, "hard_stop_code": "internal_error",
            "error": "旧 worker 硬失败",
        }
        # 重现真实缺陷：调用方把替换前的旧实例交给 finalize。
        finalize_scrape_outcome(
            ctx, run_id, result, stop_event=old_task["stop_event"], skip_combos=None,
            original_task=old_task,
        )
        self.assertEqual(
            replacement["progress"], {"message": "新 worker 的进度"},
            "旧 worker 收口不得覆盖替换实例的进度",
        )
        self.assertEqual(replacement["status"], "running")
        self.assertIsNone(replacement["result"])
        # 旧实例不推进 durable 终态：run 仍归新 worker。
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "running")



if __name__ == "__main__":
    unittest.main()
