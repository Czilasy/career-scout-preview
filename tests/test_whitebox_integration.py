"""033 V2 统一服务红测。"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from webui.browser_recovery import BrowserRecovery
from webui.store import TaskStore
from webui.source_breaker import SourceOutcome
from webui.whitebox import ScrapeEvidence, WhiteboxService, WhiteboxWriteError


class WhiteboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.service = WhiteboxService(self.store, emergency_path=pathlib.Path(self.temp.name) / "emergency.jsonl")

    def tearDown(self):
        self.temp.cleanup()

    def test_callers_cannot_request_success_and_report_shares_revision(self):
        ref = self.service.begin("scrape", "run-1", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a", "required": True}]
        })
        self.service.record(ref, {
            "idempotency_key": "finish-a", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "a", "attempt_no": 1, "required_evidence": True,
            "payload": {"scope_complete": True, "source_exhausted": False,
                        "stop_reason": "target_reached", "returned_total_count": 1,
                        "unit_unique_count": 1},
        })
        result = self.service.finalize(ref)
        self.assertEqual(result["conclusion"], "succeeded")
        self.assertNotIn("ok", result)
        report = self.service.report("scrape", "run-1")
        self.assertEqual(report["integrity"]["revision"], result["revision"])
        self.assertEqual(report["integrity"]["conclusion"], "succeeded")

    def test_required_write_failure_never_returns_success(self):
        class BrokenStore:
            def create_whitebox_run(self, *args, **kwargs):
                raise OSError("disk full")

            def append_whitebox_emergency(self, *args, **kwargs):
                raise OSError("emergency unavailable")

        service = WhiteboxService(BrokenStore(), emergency_path=pathlib.Path(self.temp.name) / "broken.jsonl")
        with self.assertRaises(WhiteboxWriteError):
            service.begin("scrape", "run-broken", {
                "stages": ["scrape_list"], "units": [{"unit_key": "a"}]
            })

    def test_task_state_write_failure_blocks_success(self):
        self.store.create_task("legacy-sync", "scrape", {})
        service = WhiteboxService(self.store, sync_task_state=lambda _run, _result: (_ for _ in ()).throw(OSError("state unavailable")))
        ref = service.begin("legacy_task", "legacy-sync", {
            "stages": ["task"], "units": [{"unit_key": "task"}],
        })
        service.record(ref, {
            "idempotency_key": "task-done", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "task",
            "unit_key": "task", "required_evidence": True,
            "payload": {"scope_complete": True, "returned_total_count": 1,
                        "unit_unique_count": 1},
        })
        with self.assertRaises(WhiteboxWriteError): service.finalize(ref)
        report = WhiteboxService(self.store).report("legacy_task", "legacy-sync")
        self.assertFalse(report["integrity"]["evidence_complete"])

    def test_legacy_report_is_unverifiable_without_writing_old_rows(self):
        self.store.create_task("legacy-1", "scrape", {})
        report = self.service.report("legacy_task", "legacy-1")
        self.assertEqual(report["integrity"]["conclusion"], "unverifiable")
        self.assertEqual(report["integrity"]["primary_code"], "legacy_evidence_missing")

    def test_count_layers_and_salary_quality_are_aggregated(self):
        ref = self.service.begin("scrape", "run-counts", {
            "stages": ["scrape_list"],
            "units": [{"unit_key": "a"}, {"unit_key": "b"}],
        })
        for key, returned, unique in (("a", 4044, 2200), ("b", 0, 0)):
            self.service.record(ref, {
                "idempotency_key": f"scope:{key}", "event_type": "scope_completed",
                "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
                "unit_kind": "keyword_city", "unit_key": key, "attempt_no": 1,
                "required_evidence": True,
                "payload": {"scope_complete": True, "source_exhausted": True,
                            "stop_reason": "source_exhausted", "returned_total_count": returned,
                            "unit_unique_count": unique, "quality_counts": {"salary_source.api_empty": 4}},
            })
            if not returned:
                self.service.record(ref, {
                    "idempotency_key": f"empty:{key}", "event_type": "explicit_empty",
                    "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
                    "unit_key": key, "required_evidence": True, "payload": {"empty_evidence": True},
                })
        result = self.service.finalize(ref)
        self.assertEqual(result["summary"]["unit_output_sum"], 2200)
        self.assertEqual(result["summary"]["quality_counts"]["salary_source.api_empty"], 8)

    def test_result_snapshot_uses_source_whitebox_revision(self):
        source = self.service.begin("scrape", "source-1", {
            "stages": ["scrape_list"], "units": [{"unit_key": "kw|city"}],
        })
        self.service.record(source, {
            "idempotency_key": "source-scope", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "kw|city", "required_evidence": True,
            "payload": {"scope_complete": True, "returned_total_count": 1,
                        "unit_unique_count": 1, "stop_reason": "target_reached"},
        })
        source_result = self.service.finalize(source)
        snapshot_id = self.store.save_pipeline_result(
            {"jobs": [{"job_id": "j1", "platform_job_id": "j1", "verdict": "match"}],
             "dropped": [], "total_scraped": 1, "total_kept": 1, "total_dropped": 0},
            {"platform": "boss"},
            execution_params={"scrape_task_id": "source-1"},
        )
        integrity = self.service.integrity_for_result(snapshot_id)
        self.assertEqual(integrity["conclusion"], "succeeded")
        self.assertEqual(integrity["revision"], source_result["revision"])

    def test_submission_failure_is_a_terminal_failed_conclusion(self):
        ref = self.service.begin("screening", "submit-failed", {
            "stages": ["screening"], "units": [{"unit_key": "screening"}],
        })
        self.service.record(ref, {
            "idempotency_key": "submission-failed", "event_type": "submission_failed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "submit",
            "unit_key": "screening", "required_evidence": True, "severity": "error",
            "payload": {"error_code": "submit_failed", "error_reason": "执行器不可用"},
        })
        result = self.service.finalize(ref, lifecycle_end="failed")
        self.assertEqual(result["conclusion"], "failed")
        self.assertEqual(result["primary_code"], "submit_failed")

    def test_submission_failure_marks_every_planned_unit(self):
        plan = {"stages": ["ai_rough", "jd_detail", "ai_fine"], "units": [
            {"unit_key": key, "unit_kind": "ai_stage", "stage": key}
            for key in ("ai_rough", "jd_detail", "ai_fine")
        ]}
        result = self.service.mark_submission_failed(
            "screening", "submit-all", plan, "执行器不可用")
        self.assertEqual(result["conclusion"], "failed")
        run = self.store.get_whitebox_run("screening", "submit-all")
        units = self.store.list_whitebox_units(run["id"])
        self.assertEqual({unit["status"] for unit in units}, {"failed"})
        events = self.store.list_whitebox_events(run["id"])
        self.assertEqual(sum(event["event_type"] == "submission_failed" for event in events), 3)

    def test_diagnostic_failure_binds_to_a_planned_unit(self):
        ref = self.service.begin("screening", "diagnostic-unit", {
            "stages": ["ai_rough", "jd_detail", "ai_fine"],
            "units": [
                {"unit_key": key, "unit_kind": "ai_stage", "stage": key}
                for key in ("ai_rough", "jd_detail", "ai_fine")
            ],
        })
        self.assertTrue(self.service.record_for_owner("screening", "diagnostic-unit", {
            "idempotency_key": "diagnostic-failure",
            "event_type": "unit_failed", "occurred_at": "2026-09-05T00:00:00+08:00",
            "stage": "ai_screen", "unit_kind": "diagnostic",
            "unit_key": "ai_screen", "attempt_no": 1,
            "required_evidence": True, "severity": "error",
            "payload": {"error_code": "ai_failed", "error_reason": "请求失败"},
        }))
        run = self.store.get_whitebox_run("screening", "diagnostic-unit")
        units = self.store.list_whitebox_units(run["id"])
        self.assertEqual({unit["unit_key"] for unit in units}, {
            "ai_rough", "jd_detail", "ai_fine",
        })
        events = self.store.list_whitebox_events(run["id"])
        self.assertIn(events[-1]["unit_key"], {
            "ai_rough", "jd_detail", "ai_fine",
        })

    def test_repeated_finalize_preserves_interrupted_conclusion(self):
        ref = self.service.begin("scrape", "interrupted-once", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        first = self.service.finalize(ref, lifecycle_end="cancelled")
        second = self.service.finalize(ref)
        self.assertEqual(first["conclusion"], "interrupted")
        self.assertEqual(second["conclusion"], "interrupted")
        self.assertEqual(second["revision"], first["revision"])

    def test_primary_and_emergency_whitebox_write_failure_cannot_be_success(self):
        ref = self.service.begin("scrape", "write-failed", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        original_append = self.store.append_whitebox_event
        original_mark = self.store.mark_whitebox_incomplete
        self.store.append_whitebox_event = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))
        self.store.mark_whitebox_incomplete = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with self.assertRaises(WhiteboxWriteError):
                self.service.record(ref, {
                    "idempotency_key": "required-fact", "event_type": "scope_completed",
                    "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
                    "unit_key": "a", "required_evidence": True,
                    "payload": {"scope_complete": True, "returned_total_count": 1,
                                "unit_unique_count": 1},
                })
        finally:
            self.store.append_whitebox_event = original_append
            self.store.mark_whitebox_incomplete = original_mark
        self.assertTrue((pathlib.Path(self.temp.name) / "emergency.jsonl").exists())

    def test_persisted_whitebox_incomplete_event_blocks_later_success(self):
        ref = self.service.begin("scrape", "already-incomplete", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        self.service.record(ref, {
            "idempotency_key": "scope-a", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "a", "attempt_no": 1, "required_evidence": True,
            "payload": {"scope_complete": True, "returned_total_count": 1,
                        "unit_unique_count": 1, "stop_reason": "target_reached"},
        })
        self.store.mark_whitebox_incomplete(ref.id, stage="scrape_list", reason="disk_full")
        result = self.service.finalize(ref)
        self.assertEqual(result["conclusion"], "unverifiable")
        self.assertEqual(result["primary_code"], "whitebox_incomplete")

    def test_emergency_record_is_imported_before_finalize(self):
        ref = self.service.begin("scrape", "emergency-before-finalize", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        self.service.record(ref, {
            "idempotency_key": "scope-a", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "a", "required_evidence": True,
            "payload": {"scope_complete": True, "returned_total_count": 1,
                        "unit_unique_count": 1, "stop_reason": "target_reached"},
        })
        self.service.emergency_path.write_text(json.dumps({
            "run_id": ref.id, "owner_id": ref.id, "event_type": "whitebox_incomplete",
            "idempotency_key": "emergency-incomplete-1", "stage": "scrape_list",
            "occurred_at": "2026-09-05T00:00:01+08:00", "payload": {"reason": "disk_full"},
        }) + "\n", encoding="utf-8")

        result = self.service.finalize(ref)

        self.assertEqual(result["conclusion"], "unverifiable")
        self.assertEqual(result["primary_code"], "whitebox_incomplete")
        events = self.store.list_whitebox_events(ref.id)
        self.assertTrue(any(event["event_type"] == "emergency_record_imported" for event in events))

    def test_browser_recovery_does_not_repair_whitebox_write_failure(self):
        ref = self.service.begin("scrape", "browser-recovery-not-repair", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        self.service.record(ref, {
            "idempotency_key": "scope-a", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "a", "required_evidence": True,
            "payload": {"scope_complete": True, "returned_total_count": 1,
                        "unit_unique_count": 1, "stop_reason": "target_reached"},
        })
        self.store.mark_whitebox_incomplete(ref.id, stage="scrape_list", reason="disk_full")
        recovery = BrowserRecovery(
            task_id="browser-recovery-not-repair", unit_key="a", store=self.store,
            ensure_chrome_ready=lambda _port, **_kwargs: (True, "ready"),
        )
        self.assertTrue(recovery.try_restart()[0])
        recovery.mark_progress()

        result = self.service.finalize(ref)

        self.assertEqual(result["conclusion"], "unverifiable")
        self.assertEqual(result["primary_code"], "whitebox_incomplete")

    def test_finalization_is_atomic_when_final_event_write_fails(self):
        ref = self.service.begin("scrape", "atomic-finalize", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        self.service.record(ref, {
            "idempotency_key": "scope-a", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "a", "required_evidence": True,
            "payload": {"scope_complete": True, "returned_total_count": 1,
                        "unit_unique_count": 1, "stop_reason": "target_reached"},
        })
        with self.store._connection() as conn:
            conn.execute("""
                CREATE TRIGGER fail_task_finalized
                BEFORE INSERT ON whitebox_events
                WHEN NEW.event_type = 'task_finalized'
                BEGIN SELECT RAISE(ABORT, 'task_finalized unavailable'); END
            """)

        with self.assertRaises(WhiteboxWriteError):
            self.service.finalize(ref)

        stored = self.store.get_whitebox_run_by_id(ref.id)
        self.assertNotEqual(stored.get("conclusion"), "succeeded")
        self.assertNotEqual(stored.get("lifecycle_status"), "terminal")

    def test_explicit_success_cannot_upgrade_interrupted_run(self):
        ref = self.service.begin("scrape", "interrupted-no-upgrade", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        self.service.record(ref, {
            "idempotency_key": "scope-a", "event_type": "scope_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "a", "required_evidence": True,
            "payload": {"scope_complete": True, "returned_total_count": 1,
                        "unit_unique_count": 1, "stop_reason": "target_reached"},
        })
        self.assertEqual(self.service.finalize(ref, lifecycle_end="interrupted")["conclusion"], "interrupted")

        result = self.service.finalize(ref, lifecycle_end="succeeded")

        self.assertEqual(result["conclusion"], "interrupted")
        self.assertEqual(self.store.get_whitebox_run_by_id(ref.id)["conclusion"], "interrupted")

    def test_primary_and_emergency_write_failures_are_both_terminal(self):
        ref = self.service.begin("scrape", "double-write-failed", {
            "stages": ["scrape_list"], "units": [{"unit_key": "a"}],
        })
        original_append = self.store.append_whitebox_event
        original_mark = self.store.mark_whitebox_incomplete
        original_emergency = self.service._persist_emergency
        self.store.append_whitebox_event = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))
        self.store.mark_whitebox_incomplete = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))
        self.service._persist_emergency = lambda *args, **kwargs: False
        try:
            with self.assertRaises(WhiteboxWriteError):
                self.service.record(ref, {
                    "idempotency_key": "double-failure", "event_type": "scope_completed",
                    "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
                    "unit_key": "a", "required_evidence": True,
                    "payload": {"scope_complete": True, "returned_total_count": 1,
                                "unit_unique_count": 1},
                })
        finally:
            self.store.append_whitebox_event = original_append
            self.store.mark_whitebox_incomplete = original_mark
            self.service._persist_emergency = original_emergency

    def test_page_unique_count_is_preserved_when_stopped_before_scope_completed(self):
        evidence = ScrapeEvidence(self.store, "mid-page-stop", [{"combo_key": "a"}], 2)
        evidence.unit_started("a", 2, 1)
        evidence.page("a", {"page": 1, "returned_count": 2,
                             "new_unique_count": 2, "has_more": True,
                             "resume_page": 2}, 2)
        evidence.page("a", {"page": 2, "returned_count": 3,
                             "new_unique_count": 3, "has_more": None,
                             "resume_page": 3}, 2)
        run = self.store.get_whitebox_run("scrape", "mid-page-stop")
        unit = self.store.list_whitebox_units(run["id"])[0]
        self.assertEqual(unit["unit_unique_count"], 5)
        result = evidence.finish({"jobs": []}, lifecycle_end="cancelled")
        self.assertEqual(result["integrity"]["conclusion"], "interrupted")

    def test_retry_creates_distinct_attempt_and_keeps_recovery_link(self):
        evidence = ScrapeEvidence(self.store, "retry-run", [{"combo_key": "a"}], 1)
        evidence.unit_started("a", 1, 1)
        evidence.failed("a", SourceOutcome.failure(failed_code="source_timeout"))
        evidence.unit_started("a", 1, 1)
        page = {"event_type": "page_completed", "page": 1, "returned_count": 1,
                "new_unique_count": 1, "unit_unique_count": 1, "has_more": False,
                "resume_page": 2}
        evidence.page("a", page, 1)
        evidence.completed("a", SourceOutcome.success(
            jobs=[{"platform_job_id": "job-1"}], scope_complete=True,
            source_exhausted=True, page_evidence=[page]))
        result = evidence.finish({"jobs": [{"platform_job_id": "job-1"}]})
        self.assertEqual(result["integrity"]["conclusion"], "succeeded")
        run = self.store.get_whitebox_run("scrape", "retry-run")
        units = self.store.list_whitebox_units(run["id"])
        attempts = [unit for unit in units if unit["unit_key"] == "a"]
        self.assertEqual([unit["attempt_no"] for unit in attempts], [1, 2])
        self.assertEqual(attempts[1]["recovered_from_unit_id"], attempts[0]["id"])


if __name__ == "__main__":
    unittest.main()
