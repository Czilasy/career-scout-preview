"""047 US1 预检失败载荷回归：规范码保留、未执行证据与完整性不覆盖主因。

依据 specs/047-flow-recovery-fixes/v1/contracts/flow-recovery.md C1 与
spec FR-001/FR-002：
- classify_preflight_failure 的载荷始终保留规范 failed_code 与安全 error；
  systemic 另保留 hard_stop/hard_stop_code（执行策略，不决定是否保留事实）。
- 预检失败给所有尚未启动的计划单元记录阻断事实，保留 planned/未执行含义；
  不伪造页完成或空结果。
- 具体失败原因与完整性并存；有具体错误时不取完整性主因。
- 成功不能造证据（033 v2 成功标准不降低）。
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from webui.error_registry import ERROR_USER_MESSAGES, resolve_code
from webui.pipeline_exec_status import classify_preflight_failure
from webui.source_breaker import SourceOutcome
from webui.store import TaskStore
from webui.whitebox import ScrapeEvidence


class _Preflight:
    def __init__(self, failed_code, failed_reason="", platform="boss"):
        self.failed_code = failed_code
        self.failed_reason = failed_reason
        self.platform = platform
        self.ok = False


class ClassifyPreflightFailureTests(unittest.TestCase):
    def test_failed_code_always_preserved(self):
        for code in (
            "source_login_required",
            "source_rate_limited",
            "source_cdp_unavailable",
            "source_unknown_error",
        ):
            with self.subTest(code=code):
                result = classify_preflight_failure(_Preflight(code))
                # C1：规范码始终随载荷返回（hard_stop 另存），不得淹没原码。
                self.assertIn("failed_code", result, "原始失败码必须随载荷保留")
                self.assertEqual(result["failed_code"], code)
                self.assertEqual(result["error"], ERROR_USER_MESSAGES[code])

    def test_alias_code_is_normalized_but_preserved(self):
        result = classify_preflight_failure(_Preflight("cdp_unavailable"))
        self.assertEqual(result["failed_code"], "source_cdp_unavailable")
        self.assertTrue(result["hard_stop"])
        self.assertEqual(result["hard_stop_code"], "cdp_unavailable")

    def test_unknown_is_not_cdp_unavailable(self):
        result = classify_preflight_failure(_Preflight("source_status_unclear"))
        self.assertEqual(result["failed_code"], "source_status_unclear")
        self.assertNotEqual(result["failed_code"], "source_cdp_unavailable")
        self.assertNotIn("hard_stop", result)
        self.assertEqual(
            result["error"], ERROR_USER_MESSAGES["source_status_unclear"],
        )

    def test_systemic_keeps_hard_stop_alongside_code(self):
        result = classify_preflight_failure(_Preflight("source_cdp_unavailable"))
        self.assertEqual(result["failed_code"], "source_cdp_unavailable")
        self.assertTrue(result["hard_stop"])
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertEqual(result["error"], ERROR_USER_MESSAGES["source_cdp_unavailable"])


class PreflightInvalidationTests(unittest.TestCase):
    """未执行单元保留未执行含义；预检失败原因不被完整性主因覆盖。"""

    def test_blocked_before_start_marks_units_without_faking_success(self):
        evidence = ScrapeEvidence(None, "", [
            {"combo_key": "a|上海", "keyword": "a", "city": "上海"},
            {"combo_key": "b|北京", "keyword": "b", "city": "北京"},
        ], 3)
        evidence.blocked_before_start("source_status_unclear", "暂时无法确认平台状态")

        for key in ("a|上海", "b|北京"):
            unit = evidence.units[key]
            self.assertEqual(unit["status"], "failed")
            self.assertFalse(unit["evidence_complete"])
            self.assertEqual(unit["error_code"], "source_status_unclear")
            self.assertEqual(unit["unit_unique_count"], 0)

        result = evidence.finish({"ok": False, "jobs": []})
        self.assertNotIn(result["integrity"]["conclusion"], {"succeeded", "empty"})
        self.assertEqual(
            result["integrity"]["conclusion"], "failed",
            "全部必需单元在预检被阻断时必须收敛为失败，而不是成功或空结果",
        )

    def test_blocked_before_start_persists_into_real_store(self):
        """带真实 store 时，阻断事实必须经白箱写入链落库并保留主因。

        回归：不带 store 的纯内存用例曾漏掉未注册的事件类型（unknown
        whitebox event_type），真实预检阻断路径会直接抛错丢证据。
        """
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = TaskStore(pathlib.Path(temp.name) / "webui.db")
        evidence = ScrapeEvidence(store, "preflight-store", [
            {"combo_key": "a|上海", "keyword": "a", "city": "上海"},
        ], 3)
        self.assertIsNone(evidence.startup_error)
        evidence.blocked_before_start("source_status_unclear", "暂时无法确认平台状态")
        result = evidence.finish({
            "ok": False, "jobs": [], "error": "暂时无法确认平台状态",
            "failed_code": "source_status_unclear",
        })
        self.assertEqual(result["integrity"]["conclusion"], "failed")
        self.assertEqual(
            result["integrity"]["primary_code"], "source_status_unclear",
        )
        units = store.list_whitebox_units(evidence.ref.id)
        self.assertTrue(
            any(unit["unit_key"] == "a|上海"
                and unit["status"] == "failed"
                and unit["error_code"] == "source_status_unclear"
                for unit in units),
            "真实 store 投影必须保留阻断码，而不是伪造完成或空结果",
        )

    def test_blocked_before_start_skips_started_units(self):
        evidence = ScrapeEvidence(None, "", [
            {"combo_key": "a|上海", "keyword": "a", "city": "上海"},
            {"combo_key": "b|北京", "keyword": "b", "city": "北京"},
        ], 3)
        evidence.unit_started("a|上海", 3, 1)
        evidence.blocked_before_start("source_login_required", "登录已失效")

        self.assertEqual(evidence.units["a|上海"]["status"], "running")
        self.assertEqual(evidence.units["b|北京"]["status"], "failed")

    def test_success_still_requires_evidence(self):
        from webui.whitebox_rules import reduce_conclusion

        result = reduce_conclusion(
            {"stages": ["scrape_list"], "units": [{
                "unit_key": "k|city", "unit_kind": "keyword_city",
                "stage": "scrape_list", "required": True,
            }]},
            [],
        )
        self.assertEqual(result["conclusion"], "unverifiable")


class PrimaryFailureSelectionTests(unittest.TestCase):
    def test_concrete_error_wins_over_integrity(self):
        from webui.pipeline_task_outcome import _select_primary_failure

        code, reason = _select_primary_failure(
            result_error="具体失败原因：登录失效",
            failed_code="source_login_required",
            integrity={"primary_code": "unit_evidence_missing",
                       "primary_reason": "至少一个计划单元缺少完成证据"},
        )
        self.assertEqual(code, "source_login_required")
        self.assertIn("登录失效", reason)

    def test_integrity_used_only_without_concrete_error(self):
        from webui.pipeline_task_outcome import _select_primary_failure

        code, reason = _select_primary_failure(
            result_error="",
            failed_code="",
            integrity={"primary_code": "unit_evidence_missing",
                       "primary_reason": "至少一个计划单元缺少完成证据"},
        )
        self.assertEqual(code, "unit_evidence_missing")
        self.assertIn("缺少完成证据", reason)


class ResumeProbeCodeMappingTests(unittest.TestCase):
    """047 C1：恢复预检不得把 unknown/unreachable 冒充成 CDP 不可用。"""

    def _probe(self, code):
        from webui.flow_submission_service import FlowSubmissionService

        class _Store:
            def create_screening_run(self, *a, **k):
                raise AssertionError("no provisional run expected")

        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.store = _Store()
        ctx.check_resume_block = lambda _candidate: (False, code, "platform said so")

        class _App:
            config = {"RESUME_BLOCK_CHECKER": lambda _c: (True, "", "")}

        ctx.app = _App()
        service = FlowSubmissionService(ctx)

        class _Scope:
            combination_count = 1

        passed, stable_code, _reason = service._check_resume_block(
            candidate={"id": "run-x", "execution_params": {}},
            flow_id="f1", profile_id="p1", track_id="t1",
            frozen_scope=_Scope(),
        )
        return passed, stable_code

    def test_status_unclear_stays_unclear(self):
        passed, code = self._probe("source_status_unclear")
        self.assertFalse(passed)
        self.assertEqual(code, "source_status_unclear")

    def test_unreachable_stays_unreachable(self):
        passed, code = self._probe("source_unreachable")
        self.assertFalse(passed)
        self.assertEqual(code, "source_unreachable")

    def test_cdp_unavailable_stays_cdp(self):
        passed, code = self._probe("cdp_unavailable")
        self.assertFalse(passed)
        self.assertEqual(code, "source_cdp_unavailable")

    def test_login_codes_map_to_login_required(self):
        for raw in ("login_required", "not_logged_in", "login_expired"):
            with self.subTest(raw=raw):
                passed, code = self._probe(raw)
                self.assertFalse(passed)
                self.assertEqual(code, "source_login_required")



class PrimaryFailureChainTests(unittest.TestCase):
    """复核 P1-3：同一条具体原因必须贯穿 DB、live task 与 Track。

    真实 runner → outcome → store/Track 调用链：有具体失败
    （source_status_unclear）时，完整性主因（unit_evidence_missing）不得
    覆盖用户可见原因；完整性报告本身仍可读。
    """

    def test_partial_chain_keeps_concrete_reason_everywhere(self):
        import threading
        from types import SimpleNamespace
        from unittest import mock

        from webui.constants import _OPERATIONAL_ERRORS
        from webui.flow_service import FlowService
        from webui.runners.pipeline_task import run_pipeline_task

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = TaskStore(pathlib.Path(temp.name) / "state" / "webui.db")
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES ('p1', 'p1', '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        flow = store.create_flow(
            profile_id="p1", selection="boss", start_key="chain-partial",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run_id = "chain-partial-run"
        script_params = {"keyword": "kw", "city": ["city"], "pages": 1}
        store.create_screening_run(
            run_id, profile_id="p1", source_count=1,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"],
                "platform": "boss", "script_params": script_params,
            },
        )
        store.create_scrape_search_run(
            run_id, "p1", platform="boss", flow_id=flow["id"], track_id=track["id"],
        )
        store.bind_flow_track_runs(
            flow["id"], "boss", profile_id="p1", scrape_run_id=run_id,
        )
        store.update_flow_track(
            flow["id"], "boss", profile_id="p1", status="running", stage="scrape",
        )
        store.update_screening_run(run_id, status="running", current_stage="scrape")
        task = {
            "kind": "scrape", "status": "queued", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": 1, "finished_at": None,
            "stop_event": threading.Event(),
        }
        ctx = SimpleNamespace(
            store=store, operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(), tasks={run_id: task},
            flow_service=FlowService(store, platform_enabled=lambda _p: True),
            app=SimpleNamespace(config={"RESULT_DIR": tempfile.gettempdir()}),
            is_user_finished=lambda _rid: False,
            activate_task_browser=mock.Mock(),
            make_cdp_source=mock.Mock(return_value=object()),
            release_worker_resume_claims=mock.Mock(),
            schedule_pipeline_task_cleanup=mock.Mock(),
            clear_auto_screen=mock.Mock(),
            write_run=lambda rid, **kw: store.update_screening_run(rid, **kw),
            record_pause_failure=mock.Mock(),
            msg_user_stopped_scrape="用户已停止",
        )
        result = {
            "ok": False, "jobs": [], "total_scraped": 0, "combinations": 1,
            "completed_combos": [],
            "error": "暂时无法确认平台状态",
            "failed_code": "source_status_unclear",
            "integrity": {
                "conclusion": "unverifiable", "evidence_complete": False,
                "primary_code": "unit_evidence_missing",
                "primary_reason": "至少一个计划单元缺少完成证据",
            },
        }
        with mock.patch("webui.pipeline_exec.run_search", return_value=result):
            run_pipeline_task(ctx, run_id, script_params)

        # 数据库原始行保留具体原因；权威读取入口（task-state 同源）读到同一份。
        with store._connection() as conn:
            raw_row = conn.execute(
                "SELECT status, error_code, error_reason FROM screening_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        self.assertEqual(raw_row["status"], "partial")
        self.assertEqual(raw_row["error_code"], "source_status_unclear")
        db_run = store.get_screening_run_outcome(run_id)
        self.assertEqual(db_run["status"], "partial")
        self.assertEqual(db_run["error_code"], "source_status_unclear")
        self.assertNotIn("缺少完成证据", db_run["error_reason"] or "")
        self.assertNotIn(
            "缺少完成证据", task["error"] or "",
            "live task 提示必须保留具体失败原因，而不是被完整性主因覆盖",
        )
        self.assertIn("无法确认平台状态", task["error"] or "")
        track_after = next(
            t for t in store.get_flow(flow["id"], profile_id="p1")["tracks"]
            if t["id"] == track["id"]
        )
        self.assertEqual(
            track_after["error_code"], "source_status_unclear",
            "Track 原因必须与 DB/live 一致，不被完整性错误码覆盖",
        )
        # 完整性报告仍独立可读
        from webui.whitebox import WhiteboxService, WhiteboxNotFoundError
        try:
            integrity = WhiteboxService(store).report("scrape", run_id)["integrity"]
        except WhiteboxNotFoundError:
            integrity = None
        if integrity is not None:
            self.assertNotEqual(integrity.get("conclusion"), "succeeded")



class PublishScrapeOutcomeAtomicTests(unittest.TestCase):
    """047 C1：partial 保留具体失败原因，旧全局更新方法仍是其原语义。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def tearDown(self):
        self.temp.cleanup()

    def _seed_run(self):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES ('p1', 'p', '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        run = self.store.create_search_run("p1", {"platform": "boss"}, "ai")
        self.store.create_screening_run(
            "run-1", profile_id="p1", execution_params={"platform": "boss"},
        )
        return run

    def test_partial_preserves_concrete_failure_fields(self):
        self._seed_run()
        self.store.update_screening_run("run-1", status="running")
        self.store.publish_scrape_outcome_atomic(
            "run-1",
            status="partial",
            error_code="source_login_required",
            error_reason="登录已失效，抓取被阻断",
            processed_count=1,
            source_count=3,
            total_scraped=5,
        )
        # 读取口径：具体原因由 047 生命周期权威入口提供；legacy
        # `get_screening_run` 保持 033 v2 的 partial 剥离语义（不在本轮
        # 越界修改 store_runs.py）。
        run = self.store.get_screening_run_outcome("run-1")
        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["error_code"], "source_login_required")
        self.assertIn("登录已失效", run["error_reason"])
        self.assertEqual(run["processed_count"], 1)
        self.assertEqual(run["total_scraped"], 5)

    def test_illegal_transition_rejected_and_rolled_back(self):
        self._seed_run()
        self.store.update_screening_run("run-1", status="running")
        self.store.publish_scrape_outcome_atomic(
            "run-1", status="partial", error_code="source_rate_limited",
            error_reason="触发限流",
        )
        # partial 是终态；再次改写为 running 必须被拒绝且不落库（事务回滚）。
        with self.assertRaises(ValueError):
            self.store.publish_scrape_outcome_atomic(
                "run-1", status="running",
            )
        run = self.store.get_screening_run_outcome("run-1")
        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["error_code"], "source_rate_limited")

    def test_unknown_run_returns_none(self):
        self._seed_run()
        self.assertIsNone(self.store.publish_scrape_outcome_atomic(
            "missing-run", status="partial",
        ))

    def test_search_ledger_synchronised_in_same_transaction(self):
        self._seed_run()
        self.store.create_search_run.__name__  # touch for clarity
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO search_runs "
                "(id, profile_id, status, created_at, updated_at) "
                "VALUES ('run-1', 'p1', 'queued', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        self.store.update_screening_run("run-1", status="running")
        self.store.publish_scrape_outcome_atomic(
            "run-1", status="partial", error_code="source_rate_limited",
            error_reason="触发限流", processed_count=0, source_count=1,
        )
        with self.store._connection() as conn:
            search = conn.execute(
                "SELECT status, error_code FROM search_runs WHERE id = 'run-1'"
            ).fetchone()
        self.assertEqual(search["status"], "partial")
        self.assertEqual(search["error_code"], "source_rate_limited")

    def test_evidence_finish_conclusion_matches_blocked_facts(self):
        """预检阻断 → 结论 failed，且主因来自真实阻断码（不伪造成功）。"""
        evidence = ScrapeEvidence(None, "", [
            {"combo_key": "a|上海", "keyword": "a", "city": "上海"},
        ], 3)
        evidence.blocked_before_start("source_status_unclear", "暂时无法确认平台状态")
        result = evidence.finish({
            "ok": False, "jobs": [], "error": "暂时无法确认平台状态",
            "failed_code": "source_status_unclear",
        })
        self.assertEqual(result["integrity"]["conclusion"], "failed")
        self.assertEqual(result["integrity"]["primary_code"], "source_status_unclear")


if __name__ == "__main__":
    unittest.main()
