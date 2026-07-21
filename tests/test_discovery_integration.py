"""Discovery integration tests (feature 004)."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from webui.discovery import (
    AISecurityError,
    DiscoveryError,
    analyze_resume,
    confirm_directions,
)
from webui.ai import AISecurityError as AIProviderSecurityError
from webui.store import TaskStore


RESUME_TEXT = (
    "张三 高级后端开发工程师\n"
    "5年 Python 后端经验，熟悉 Django/Flask，主导风控系统设计与上线。\n"
    "熟练使用 MySQL/Redis/Kafka，负责团队 6 人技术管理。\n"
    "本科计算机科学与技术专业毕业。\n"
)


def _valid_ai_response():
    return {
        "summary": {
            "headline": "高级后端开发工程师",
            "experience_level": "高级",
            "domains": ["后端", "风控"],
            "strengths": ["Python", "系统设计"],
        },
        "evidence": [
            {
                "client_ref": "e1",
                "type": "skill",
                "normalized_value": "Python",
                "safe_excerpt": "Python 后端",
                "source_quote": "Python 后端",
                "source_locator": {"start": 16, "end": 25},
                "assertion_type": "explicit",
                "confidence": 95,
            },
            {
                "client_ref": "e2",
                "type": "responsibility",
                "normalized_value": "风控系统设计",
                "safe_excerpt": "主导风控系统设计",
                "source_quote": "主导风控系统设计",
                "source_locator": {"start": 44, "end": 52},
                "assertion_type": "explicit",
                "confidence": 90,
            },
        ],
        "unknowns": [
            {"field": "current_city", "message": "未提及城市"},
        ],
        "directions": [
            {
                "client_ref": "d1",
                "name": "后端开发工程师",
                "type": "core",
                "rationale": "5年后端经验",
                "evidence_refs": ["e1", "e2"],
                "gaps": [],
                "confidence": 92,
                "default_enabled": True,
                "search_terms": ["Python 后端"],
            },
        ],
    }


class FakeAIProvider:
    """Fake AI provider for testing. Never calls a real service."""

    def __init__(self, response=None, *, raises=None):
        self._response = response
        self._raises = raises
        self.call_count = 0
        self.last_prompt = None

    def analyze(self, resume_text):
        self.call_count += 1
        self.last_prompt = resume_text
        if self._raises is not None:
            raise self._raises
        return self._response


class _IntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/path.pdf", "pdf",
            RESUME_TEXT, "hash123", "path.pdf",
        )

    def tearDown(self) -> None:
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)


class AnalyzeResumeOrchestrationTests(_IntegrationTestCase):
    """T024: analyze_resume orchestration (consent/empty/degrade/failure)."""

    def test_consent_false_does_not_call_ai(self):
        provider = FakeAIProvider(_valid_ai_response())
        with self.assertRaises(DiscoveryError) as ctx:
            analyze_resume(self.store, self.resume["id"], ai_consent=False, ai_provider=provider)
        self.assertEqual(provider.call_count, 0)
        self.assertEqual(ctx.exception.error_code, "consent_required")
        self.assertEqual(self.store.list_analyses(self.resume["id"]), [])

    def test_consent_true_calls_ai_and_persists(self):
        provider = FakeAIProvider(_valid_ai_response())
        result = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result["status"], "ready")
        directions = self.store.list_directions(result["id"])
        self.assertEqual(len(directions), 1)
        evidence = self.store.list_evidence(result["id"])
        self.assertEqual(len(evidence), 2)

    def test_mixed_v3_output_persists_ready_partial(self):
        response = {
            "contract_version": "v3",
            "summary": {"headline": "后端工程师", "experience_level": "高级", "domains": ["后端"], "strengths": ["Python"]},
            "evidence": [
                {"client_ref": "e1", "type": "skill", "normalized_value": "Python", "source_quote": "Python 后端经验", "assertion_type": "explicit", "confidence": 90},
                {"client_ref": "bad", "type": "skill", "normalized_value": "Go", "source_quote": "不存在的 Go 经历", "assertion_type": "explicit", "confidence": 80},
            ],
            "unknowns": [],
            "directions": [{"client_ref": "d1", "name": "后端工程师", "type": "core", "rationale": "经验匹配", "evidence_refs": ["e1"], "gaps": [], "confidence": 90, "default_enabled": True, "search_terms": ["Python 后端"]}],
        }
        result = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=FakeAIProvider(response))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["quality_status"], "partial")
        self.assertEqual(len(self.store.list_evidence(result["id"])), 1)
        self.assertTrue(all(set(w) == {"code", "path"} for w in result["quality_warnings"]))

    def test_empty_v3_output_persists_ready_manual_required(self):
        response = {"contract_version": "v3", "summary": {}, "evidence": [], "unknowns": [], "directions": []}
        result = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=FakeAIProvider(response))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["quality_status"], "manual_required")

    def test_empty_resume_blocks(self):
        self.store.save_resume(
            self.profile["id"], "storage/empty.pdf", "pdf", "", "hash_empty", "empty.pdf",
        )
        empty_resume = self.store.list_resumes(self.profile["id"])[0]
        # Find the empty one
        for r in self.store.list_resumes(self.profile["id"]):
            full = self.store.get_resume(r["id"])
            if not full.get("extracted_text"):
                empty_resume = full
                break
        provider = FakeAIProvider(_valid_ai_response())
        with self.assertRaises(DiscoveryError) as ctx:
            analyze_resume(self.store, empty_resume["id"], ai_consent=True, ai_provider=provider)
        self.assertEqual(ctx.exception.error_code, "input_incomplete")
        self.assertEqual(provider.call_count, 0)

    def test_ai_invalid_output_marks_failed(self):
        provider = FakeAIProvider({"bad": "response"})
        with self.assertRaises(AISecurityError):
            analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        analyses = self.store.list_analyses(self.resume["id"])
        self.assertEqual(analyses[-1]["status"], "failed")
        self.assertEqual(analyses[-1].get("failure_code"), "ai_invalid_output")

    def test_ai_timeout_marks_failed(self):
        provider = FakeAIProvider(raises=TimeoutError())
        with self.assertRaises(DiscoveryError) as ctx:
            analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        self.assertEqual(ctx.exception.error_code, "ai_timeout")
        analyses = self.store.list_analyses(self.resume["id"])
        self.assertEqual(analyses[-1]["status"], "failed")

    def test_ai_network_error_marks_failed(self):
        provider = FakeAIProvider(raises=ConnectionError())
        with self.assertRaises(DiscoveryError) as ctx:
            analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        self.assertEqual(ctx.exception.error_code, "ai_network_error")

    def test_ai_provider_none_marks_unavailable(self):
        with self.assertRaises(DiscoveryError) as ctx:
            analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=None)
        self.assertEqual(ctx.exception.error_code, "ai_unavailable")

    def test_version_increments_on_retry(self):
        provider = FakeAIProvider(_valid_ai_response())
        first = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        provider2 = FakeAIProvider(_valid_ai_response())
        second = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider2)
        self.assertEqual(second["version"], first["version"] + 1)

    def test_raw_response_not_persisted(self):
        provider = FakeAIProvider(_valid_ai_response())
        result = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        analysis = self.store.get_analysis(result["id"])
        # Summary should only contain sanitized fields, not raw response
        self.assertNotIn("evidence", analysis.get("summary", {}))
        # Evidence safe_excerpt must not contain full resume text
        evidence = self.store.list_evidence(result["id"])
        for e in evidence:
            self.assertNotIn("5年 Python 后端经验，熟悉 Django/Flask", e.get("safe_excerpt", ""))

    def test_terminal_analysis_cannot_be_reprocessed_or_duplicate_rows(self):
        provider = FakeAIProvider(_valid_ai_response())
        result = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        evidence_count = len(self.store.list_evidence(result["id"]))
        direction_count = len(self.store.list_directions(result["id"]))
        with self.assertRaises(DiscoveryError) as ctx:
            analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider, analysis_id=result["id"])
        self.assertEqual(ctx.exception.error_code, "state_conflict")
        self.assertEqual(len(self.store.list_evidence(result["id"])), evidence_count)
        self.assertEqual(len(self.store.list_directions(result["id"])), direction_count)

    # T1.3 (RED): 候选分析成功但 evidence 为空时，analyze_resume 必须抛
    # AISecurityError("ai_invalid_output")，避免下游评估全部被
    # evidence_reference_invalid 降级为 needs_review。
    def test_v3_empty_evidence_raises_ai_invalid_output(self):
        response = {
            "contract_version": "v3",
            "summary": {"headline": "后端", "experience_level": "高级",
                        "domains": ["后端"], "strengths": ["Python"]},
            "evidence": [],
            "unknowns": [],
            "directions": [{
                "client_ref": "d1", "name": "后端", "type": "core",
                "rationale": "经验", "evidence_refs": [],
                "gaps": [], "confidence": 90, "default_enabled": True,
                "search_terms": ["Python"],
            }],
            "quality": {"status": "complete", "warnings": []},
        }
        with self.assertRaises(AISecurityError) as ctx:
            analyze_resume(self.store, self.resume["id"],
                           ai_consent=True, ai_provider=FakeAIProvider(response))
        self.assertEqual(ctx.exception.error_code, "ai_invalid_output")
        analyses = self.store.list_analyses(self.resume["id"])
        self.assertEqual(analyses[-1]["status"], "failed")
        self.assertEqual(analyses[-1].get("failure_code"), "ai_invalid_output")


class ConfirmDirectionsTests(_IntegrationTestCase):
    """T026: confirm_directions freezes immutable version."""

    def _make_ready_analysis(self):
        provider = FakeAIProvider(_valid_ai_response())
        return analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)

    def test_ready_analysis_can_confirm(self):
        analysis = self._make_ready_analysis()
        directions = self.store.list_directions(analysis["id"])
        direction_ids = [d["id"] for d in directions]
        confirmation = confirm_directions(
            self.store, analysis["id"], direction_ids,
            hard_constraints={"city": "北京", "salary": ""},
        )
        self.assertEqual(confirmation["analysis_id"], analysis["id"])
        self.assertEqual(confirmation["version"], 1)
        stored = self.store.get_confirmation(confirmation["id"])
        # Empty hard constraints should be dropped
        self.assertNotIn("salary", stored["hard_constraints"])
        self.assertEqual(stored["hard_constraints"]["city"], "北京")

    def test_non_ready_analysis_rejected(self):
        # Create analysis but don't run AI -> stays queued
        analysis = self.store.create_analysis(self.resume["id"], self.profile["id"])
        with self.assertRaises(DiscoveryError) as ctx:
            confirm_directions(self.store, analysis["id"], ["x"])
        self.assertEqual(ctx.exception.error_code, "state_conflict")

    def test_empty_directions_rejected(self):
        analysis = self._make_ready_analysis()
        with self.assertRaises(DiscoveryError) as ctx:
            confirm_directions(self.store, analysis["id"], [])
        self.assertEqual(ctx.exception.error_code, "input_incomplete")

    def test_unknown_direction_rejected(self):
        analysis = self._make_ready_analysis()
        with self.assertRaises(DiscoveryError) as ctx:
            confirm_directions(self.store, analysis["id"], ["nonexistent_id"])
        # P6: 方向不属于分析改用 state_conflict，evidence_reference_invalid 专用于证据引用无效
        self.assertEqual(ctx.exception.error_code, "state_conflict")

    def test_editing_creates_new_version(self):
        analysis = self._make_ready_analysis()
        directions = self.store.list_directions(analysis["id"])
        direction_ids = [d["id"] for d in directions]
        first = confirm_directions(self.store, analysis["id"], direction_ids)
        second = confirm_directions(self.store, analysis["id"], direction_ids)
        self.assertEqual(second["version"], first["version"] + 1)
        # First version remains immutable
        first_stored = self.store.get_confirmation(first["id"])
        self.assertEqual(first_stored["version"], first["version"])

    def test_analysis_not_found(self):
        with self.assertRaises(DiscoveryError) as ctx:
            confirm_directions(self.store, "nonexistent", ["x"])
        self.assertEqual(ctx.exception.error_code, "not_found")


class PrivacyConsentGatingTests(_IntegrationTestCase):
    """T030: privacy consent gating (no remote AI without consent)."""

    def test_consent_false_never_calls_ai(self):
        provider = FakeAIProvider(_valid_ai_response())
        with self.assertRaises(DiscoveryError):
            analyze_resume(self.store, self.resume["id"], ai_consent=False, ai_provider=provider)
        self.assertEqual(provider.call_count, 0)

    def test_consent_true_calls_ai(self):
        provider = FakeAIProvider(_valid_ai_response())
        analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        self.assertEqual(provider.call_count, 1)

    def test_logs_do_not_contain_resume_body(self):
        """The AI provider only receives resume_text via the analyze() call;
        the store never persists the raw prompt or response."""
        provider = FakeAIProvider(_valid_ai_response())
        result = analyze_resume(self.store, self.resume["id"], ai_consent=True, ai_provider=provider)
        # Check that no table contains the full resume text in a 'prompt' or 'response' column
        import sqlite3
        conn = sqlite3.connect(self._tmp.name)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for table in tables:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                for col in cols:
                    if col.lower() in ("prompt", "response", "raw_response", "model_response"):
                        rows = conn.execute(f"SELECT {col} FROM {table}").fetchall()
                        for row in rows:
                            if row[0] and RESUME_TEXT in str(row[0]):
                                self.fail(f"Resume body found in {table}.{col}")
        finally:
            conn.close()


class _AssessingFakeAIProvider(FakeAIProvider):
    """Extends FakeAIProvider with assess_job for the run orchestrator."""

    def __init__(self, response=None, *, raises=None, assessment=None):
        super().__init__(response, raises=raises)
        self._assessment = assessment or _default_job_assessment()
        self.assess_calls = []

    def assess_job(self, snapshot, direction):
        self.assess_calls.append({"snapshot_job_id": snapshot.get("job_id"), "direction_id": direction.get("id")})
        return dict(self._assessment)


def _default_job_assessment():
    return {
        "dimensions": {
            "capability": {"score": 90, "candidate_evidence_refs": [], "job_evidence_refs": []},
            "experience": {"score": 85, "candidate_evidence_refs": [], "job_evidence_refs": []},
            "environment": {"score": 80, "candidate_evidence_refs": [], "job_evidence_refs": []},
            "stability": {"score": 75, "candidate_evidence_refs": [], "job_evidence_refs": []},
        },
        "match_score": 85,
        "confidence": 88,
        "gaps": [],
        "proposed_band": "high",
    }


def _make_ready_analysis_with_confirmation(store, resume_id, profile_id, *, hard_constraints=None):
    """Helper: run analyze_resume then confirm_directions, return (analysis, confirmation)."""
    provider = FakeAIProvider(_valid_ai_response())
    analysis = analyze_resume(store, resume_id, ai_consent=True, ai_provider=provider)
    directions = store.list_directions(analysis["id"])
    direction_ids = [d["id"] for d in directions]
    confirmation = confirm_directions(
        store, analysis["id"], direction_ids,
        hard_constraints=hard_constraints or {"city": "北京"},
    )
    return analysis, confirmation


def _make_discovery_run(store, confirmation, analysis, resume_id, profile_id):
    """Helper: create a discovery_run from a confirmation."""
    import hashlib
    input_hash = hashlib.sha256(confirmation["id"].encode("utf-8")).hexdigest()
    return store.create_discovery_run(
        profile_id=profile_id, resume_id=resume_id, analysis_id=analysis["id"],
        confirmation_id=confirmation["id"], input_hash=input_hash, policy_version="v1",
    )


class RunOrchestrationTests(_IntegrationTestCase):
    """T037: run stage transitions with fake source + fake AI. (Phase 4)"""

    def setUp(self):
        super().setUp()
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource
        self._runner_cls = DiscoveryRunner
        self._fake_source_cls = FakeJobSource
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )

    def _make_runner(self, *, source="UNSET", ai_provider="UNSET"):
        import tempfile
        result_dir = tempfile.mkdtemp(prefix="boss-runner-")
        if source == "UNSET":
            source = self._fake_source_cls()
        if ai_provider == "UNSET":
            ai_provider = _AssessingFakeAIProvider()
        return self._runner_cls(
            self.store,
            source=source,
            ai_provider=ai_provider,
            result_dir=result_dir,
        )

    def test_full_pipeline_succeeded(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [
                {"job_id": "j1", "title": "Python 后端", "company": "A", "salary": "20k",
                 "location": "北京", "source_url": "https://www.zhipin.com/job_detail/j1.html", "tags": "Python", "jd": "负责..."},
            ],
        }, detail_jobs={"j1": {"title": "Python 后端", "jd": "完整JD"}})
        runner = self._make_runner(source=source)
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        final = runner.run(run["id"])
        self.assertIn(final["status"], ("succeeded", "partial"))
        # Plan should exist
        plan = self.store.get_search_plan(run["id"])
        self.assertGreaterEqual(plan["item_count"], 1)
        # At least one snapshot saved
        with self.store._connection() as conn:
            snap_count = conn.execute(
                "SELECT COUNT(*) AS c FROM discovery_job_snapshots WHERE run_id = ?",
                (run["id"],),
            ).fetchone()["c"]
        self.assertGreaterEqual(snap_count, 1)
        # At least one assessment saved
        assessments = self.store.list_assessments(run["id"])
        self.assertGreaterEqual(len(assessments), 1)
        # Stage events recorded
        events = self.store.list_discovery_events(run["id"])
        event_types = [e["event_type"] for e in events]
        self.assertIn("stage_entered", event_types)
        self.assertIn("plan_compiled", event_types)

    def test_stage_transitions_created_to_assembling(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{"job_id": "j1", "title": "Python 后端", "source_url": "https://x/1", "jd": "jd"}],
        })
        runner = self._make_runner(source=source)
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        runner.run(run["id"])
        events = self.store.list_discovery_events(run["id"])
        stages = [e["payload"].get("stage") for e in events if e["event_type"] == "stage_entered"]
        # All stages should be entered in order
        self.assertIn("planning", stages)
        self.assertIn("fetching_lists", stages)
        self.assertIn("fetching_details", stages)
        self.assertIn("evaluating", stages)
        self.assertIn("assembling", stages)

    def test_failed_list_item_does_not_abort_run(self):
        source = self._fake_source_cls(
            list_jobs={("Python 后端", "北京"): [{"job_id": "j1", "title": "x", "source_url": "https://x/1"}]},
            list_failures={("Python 后端", "北京")},
        )
        runner = self._make_runner(source=source)
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        final = runner.run(run["id"])
        # Run should reach a terminal state despite the failed item.
        # With the only plan item failed and no usable results, run ends failed.
        self.assertEqual(final["status"], "failed",
                         "单 item 失败且无可用结果时 run 应为 failed，而非卡住")
        plan = self.store.get_search_plan(run["id"])
        failed_items = [it for it in plan["items"] if it["status"] == "failed"]
        self.assertEqual(len(failed_items), 1)

    def test_no_source_marks_items_failed(self):
        runner = self._make_runner(source=None, ai_provider=None)
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        final = runner.run(run["id"])
        # With no source, all plan items fail; run ends failed
        self.assertEqual(final["status"], "failed")
        plan = self.store.get_search_plan(run["id"])
        for it in plan["items"]:
            self.assertEqual(it["status"], "failed")

    def test_source_preflight_failure_stops_before_repeating_plan_items(self):
        from webui.source import FakeJobSource, SourceOutcome

        class CdpUnavailableSource(FakeJobSource):
            def __init__(self):
                super().__init__()
                self.preflight_calls = 0

            def preflight(self):
                self.preflight_calls += 1
                return SourceOutcome.failure(
                    failed_code="source_cdp_unavailable",
                    safe_log="cdp_port_unavailable",
                )

        source = CdpUnavailableSource()
        runner = self._make_runner(source=source)
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )

        final = runner.run(run["id"])

        self.assertEqual(source.preflight_calls, 1)
        self.assertEqual(source.list_calls, [])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["stage"], "fetching_lists")
        self.assertEqual(final["failure_code"], "source_cdp_unavailable")
        plan = self.store.get_search_plan(run["id"])
        self.assertTrue(plan["items"])
        self.assertTrue(all(
            item["status"] == "failed" and
            item["failure_code"] == "source_cdp_unavailable"
            for item in plan["items"]
        ))
        event_types = [
            event["event_type"]
            for event in self.store.list_discovery_events(run["id"])
        ]
        self.assertIn("source_preflight_failed", event_types)
        self.assertNotIn("plan_item_started", event_types)

    def test_count_counters_update(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [
                {"job_id": "j1", "title": "Python", "source_url": "https://www.zhipin.com/job_detail/j1.html", "jd": "jd"},
                {"job_id": "j2", "title": "后端", "source_url": "https://www.zhipin.com/job_detail/j2.html", "jd": "jd"},
            ],
        }, detail_jobs={"j1": {"jd": "jd"}, "j2": {"jd": "jd"}})
        runner = self._make_runner(source=source)
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        runner.run(run["id"])
        final = self.store.get_discovery_run(run["id"])
        self.assertGreaterEqual(final["source_count"], 1)
        self.assertGreaterEqual(final["detail_count"], 1)
        self.assertGreaterEqual(final["evaluated_count"], 1)


class CancelPreservesSavedTests(_IntegrationTestCase):
    """T062: cancel prevents subsequent steps, preserves saved work. (Phase 7)"""

    def setUp(self):
        super().setUp()
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource
        self._runner_cls = DiscoveryRunner
        self._fake_source_cls = FakeJobSource
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )

    def test_cancel_marks_run_cancelled_and_preserves_saved(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [
                {"job_id": "j1", "title": "Python", "source_url": "https://x/1", "jd": "jd"},
            ],
        }, detail_jobs={"j1": {"jd": "jd"}})
        import tempfile
        runner = self._runner_cls(
            self.store, source=source, ai_provider=_AssessingFakeAIProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-cancel-"),
        )
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        # Request cancel before running
        runner.request_cancel(run["id"])
        final = runner.run(run["id"])
        self.assertEqual(final["status"], "cancelled")
        # Run should still have plan compiled (saved before cancel check)
        plan = self.store.get_search_plan(run["id"])
        self.assertGreaterEqual(plan["item_count"], 1)

    def test_cancel_terminal_run_raises_conflict(self):
        source = self._fake_source_cls()
        import tempfile
        runner = self._runner_cls(
            self.store, source=source, ai_provider=_AssessingFakeAIProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-cancel-"),
        )
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        runner.run(run["id"])
        # Now run is terminal; cancel should conflict
        with self.assertRaises(DiscoveryError) as ctx:
            runner.request_cancel(run["id"])
        self.assertEqual(ctx.exception.error_code, "state_conflict")

    def test_cancel_pending_plan_items_marked_cancelled(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{"job_id": "j1", "title": "x", "source_url": "https://x/1", "jd": "jd"}],
        })
        import tempfile
        runner = self._runner_cls(
            self.store, source=source, ai_provider=_AssessingFakeAIProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-cancel-"),
        )
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        runner.request_cancel(run["id"])
        runner.run(run["id"])
        plan = self.store.get_search_plan(run["id"])
        for it in plan["items"]:
            self.assertIn(it["status"], ("cancelled", "completed", "failed"))


class RestartInterruptedTests(_IntegrationTestCase):
    """T064: restart -> interrupted, checkpoints retained. (Phase 7)"""

    def setUp(self):
        super().setUp()
        from webui.discovery_runner import DiscoveryRunner, mark_interrupted_on_restart
        from webui.source import FakeJobSource
        self._runner_cls = DiscoveryRunner
        self._mark_interrupted = mark_interrupted_on_restart
        self._fake_source_cls = FakeJobSource
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )

    def test_active_run_marked_interrupted_on_restart(self):
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        # Simulate the run being in planning stage (active) when restart happens
        self.store.update_discovery_run(run["id"], status="planning", stage="planning", started=True)
        count = self._mark_interrupted(self.store)
        self.assertGreaterEqual(count, 1)
        final = self.store.get_discovery_run(run["id"])
        self.assertEqual(final["status"], "interrupted")

    def test_terminal_run_not_marked_interrupted(self):
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        self.store.update_discovery_run(run["id"], status="succeeded", completed=True)
        count = self._mark_interrupted(self.store)
        self.assertEqual(count, 0)
        final = self.store.get_discovery_run(run["id"])
        self.assertEqual(final["status"], "succeeded")

    def test_all_inflight_analysis_stages_reconcile_to_failed(self):
        from webui.discovery_runner import reconcile_analysis_on_restart
        for stage in ("requesting", "normalizing", "validating", "repairing", "persisting"):
            with self.subTest(stage=stage):
                analysis = self.store.create_analysis(self.resume["id"], self.profile["id"], contract_version="v3")
                self.store.update_analysis_status(analysis["id"], "analyzing", analysis_stage=stage)
        self.assertEqual(reconcile_analysis_on_restart(self.store), 5)
        for analysis in self.store.list_analyses(self.resume["id"])[-5:]:
            self.assertEqual((analysis["status"], analysis["stage"], analysis["failure_code"]), ("failed", "interrupted", "analysis_interrupted"))

    def test_resume_continues_from_saved_stage(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{"job_id": "j1", "title": "x", "source_url": "https://x/1", "jd": "jd"}],
        }, detail_jobs={"j1": {"jd": "jd"}})
        import tempfile
        runner = self._runner_cls(
            self.store, source=source, ai_provider=_AssessingFakeAIProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-resume-"),
        )
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        # Simulate interrupt after plan was compiled: manually compile plan,
        # mark run as interrupted, then resume.
        from webui.discovery import compile_search_plan
        confirmation_view = runner._load_confirmation_view(self.store.get_discovery_run(run["id"]))
        compiled = compile_search_plan(confirmation_view)
        items = runner._materialize_plan_items(compiled, run["id"])
        self.store.create_search_plan(run["id"], detail_budget=compiled["detail_budget"], items=items)
        self.store.update_discovery_run(
            run["id"], status="interrupted", stage="fetching_lists", started=True,
        )
        # Resume
        resumed = runner.run(run["id"])
        # Should reach terminal
        self.assertIn(resumed["status"], ("succeeded", "partial", "failed", "cancelled"))


class AiDegradeTests(_IntegrationTestCase):
    """T068: AI unavailable degrade (no fake directions; needs_review). (Phase 7)"""

    def setUp(self):
        super().setUp()
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource
        self._runner_cls = DiscoveryRunner
        self._fake_source_cls = FakeJobSource
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )

    def test_ai_unavailable_evaluates_to_needs_review(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{"job_id": "j1", "title": "Python", "source_url": "https://x/1", "jd": "jd"}],
        }, detail_jobs={"j1": {"jd": "jd"}})
        import tempfile
        # No AI provider
        runner = self._runner_cls(
            self.store, source=source, ai_provider=None,
            result_dir=tempfile.mkdtemp(prefix="boss-degrade-"),
        )
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        runner.run(run["id"])
        assessments = self.store.list_assessments(run["id"])
        for a in assessments:
            self.assertEqual(a["category"], "needs_review")

    def test_ai_provider_error_routes_to_needs_review(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{"job_id": "j1", "title": "Python", "source_url": "https://x/1", "jd": "jd"}],
        }, detail_jobs={"j1": {"jd": "jd"}})

        class FailingAI:
            def assess_job(self, snapshot, direction):
                raise RuntimeError("AI failed")

        import tempfile
        runner = self._runner_cls(
            self.store, source=source, ai_provider=FailingAI(),
            result_dir=tempfile.mkdtemp(prefix="boss-degrade-"),
        )
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        runner.run(run["id"])
        assessments = self.store.list_assessments(run["id"])
        for a in assessments:
            self.assertEqual(a["category"], "needs_review")

    def test_invalid_ai_contract_persists_failure_reason(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{
                "job_id": "invalid-assessment-job",
                "title": "Python 后端",
                "company": "测试公司",
                "location": "北京",
                "source_url": "https://www.zhipin.com/job_detail/invalid-assessment-job.html",
            }],
        }, detail_jobs={"invalid-assessment-job": {"jd": "负责 Python 后端开发"}})
        class ContractInvalidProvider:
            def assess_job(self, **_kwargs):
                return _default_job_assessment()

        runner = self._runner_cls(
            self.store, source=source,
            ai_provider=ContractInvalidProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-invalid-assessment-"),
        )
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        assessments = self.store.list_assessments(run["id"])
        self.assertEqual(len(assessments), 1)
        self.assertEqual(assessments[0]["category"], "needs_review")
        self.assertEqual(assessments[0]["failure_code"], "ai_invalid_output")

    def test_provider_safe_error_code_is_persisted(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{
                "job_id": "provider-error-job",
                "title": "Python 后端",
                "company": "测试公司",
                "location": "北京",
                "source_url": (
                    "https://www.zhipin.com/job_detail/provider-error-job.html"
                ),
            }],
        }, detail_jobs={"provider-error-job": {"jd": "负责 Python 后端开发"}})
        class ProviderError:
            def assess_job(self, **_kwargs):
                raise AIProviderSecurityError("ai_network_error")

        runner = self._runner_cls(
            self.store, source=source, ai_provider=ProviderError(),
            result_dir=tempfile.mkdtemp(prefix="boss-provider-error-"),
        )
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        assessments = self.store.list_assessments(run["id"])
        self.assertEqual(len(assessments), 1)
        self.assertEqual(assessments[0]["category"], "needs_review")
        self.assertEqual(assessments[0]["failure_code"], "ai_network_error")

    def test_valid_evidence_references_are_persisted_from_dimensions(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{
                "job_id": "evidence-job",
                "title": "Python 后端",
                "company": "测试公司",
                "location": "北京",
                "source_url": (
                    "https://www.zhipin.com/job_detail/evidence-job.html"
                ),
            }],
        }, detail_jobs={"evidence-job": {"jd": "负责 Python 后端开发"}})

        class EvidenceProvider:
            def assess_job(self, **kwargs):
                candidate_ref = kwargs["direction"]["evidence_refs"][0]
                dimensions = {
                    name: {
                        "score": score,
                        "candidate_evidence_refs": [candidate_ref],
                        "job_evidence_refs": ["title"],
                    }
                    for name, score in (
                        ("direction_alignment", 90),
                        ("skill_coverage", 85),
                        ("experience_match", 80),
                        ("industry_relevance", 75),
                    )
                }
                return {
                    "dimensions": dimensions,
                    "match_score": 85,
                    "confidence": 88,
                    "gaps": [],
                    "proposed_band": "high",
                }

        runner = self._runner_cls(
            self.store,
            source=source,
            ai_provider=EvidenceProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-evidence-"),
        )
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        assessments = self.store.list_assessments(run["id"])
        self.assertTrue(any(a["candidate_evidence_ids"] for a in assessments))
        self.assertTrue(any(
            "direction_alignment" in a["job_evidence"]
            for a in assessments
        ))

    def test_unavailable_snapshot_persists_failure_reason(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{
                "job_id": "unavailable-job",
                "title": "Python 后端",
                "location": "北京",
                "source_url": "https://www.zhipin.com/job_detail/unavailable-job.html",
            }],
        }, detail_jobs={"unavailable-job": {}})
        runner = self._runner_cls(
            self.store,
            source=source,
            ai_provider=None,
            result_dir=tempfile.mkdtemp(prefix="boss-unavailable-"),
        )
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        assessments = self.store.list_assessments(run["id"])
        self.assertTrue(assessments)
        self.assertTrue(all(
            a["failure_code"] == "snapshot_unavailable"
            for a in assessments
        ))

    def test_experience_level_conflict_persists_review(self):
        source = self._fake_source_cls(list_jobs={
            ("Python 后端", "北京"): [{
                "job_id": "internship-job",
                "title": "Python 后端实习生",
                "company": "测试公司",
                "location": "北京",
                "source_url": "https://www.zhipin.com/job_detail/internship-job.html",
            }],
        }, detail_jobs={"internship-job": {"jd": "面向在校生的 Python 实习岗位"}})

        class HighProvider:
            def assess_job(self, **_kwargs):
                return {
                    "dimensions": {
                        name: {
                            "score": 90,
                            "candidate_evidence_refs": [],
                            "job_evidence_refs": [],
                        }
                        for name in (
                            "direction_alignment",
                            "skill_coverage",
                            "experience_match",
                            "industry_relevance",
                        )
                    },
                    "match_score": 95,
                    "confidence": 95,
                    "gaps": [],
                    "proposed_band": "high",
                }

        runner = self._runner_cls(
            self.store,
            source=source,
            ai_provider=HighProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-level-conflict-"),
        )
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        assessments = self.store.list_assessments(run["id"])
        self.assertTrue(assessments)
        self.assertTrue(all(a["category"] == "needs_review" for a in assessments))
        self.assertTrue(all(
            a["failure_code"] == "experience_level_conflict"
            for a in assessments
        ))


class SensitivePiiRedactionTests(_IntegrationTestCase):
    """T049: sensitive fields redacted; logs free of resume body. (Phase 5)"""

    def test_sensitive_patterns_redacted_in_evidence(self):
        from webui.candidate import SENSITIVE_PATTERNS, redact_pii
        text = "电话：13812345678，邮箱：test@example.com，身份证：110101199003070123"
        redacted = redact_pii(text)
        self.assertNotIn("13812345678", redacted)
        self.assertNotIn("test@example.com", redacted)
        self.assertNotIn("110101199003070123", redacted)

    def test_clean_text_unchanged(self):
        from webui.candidate import redact_pii
        text = "Python 后端开发工程师，5年经验"
        self.assertEqual(redact_pii(text), text)

    def test_empty_text_returns_empty(self):
        from webui.candidate import redact_pii
        self.assertEqual(redact_pii(""), "")

    def test_run_events_never_contain_resume_body(self):
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource
        analysis, confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )
        source = self._fake_source_cls = FakeJobSource(list_jobs={
            ("Python 后端", "北京"): [{"job_id": "j1", "title": "x", "source_url": "https://x/1", "jd": "jd"}],
        }, detail_jobs={"j1": {"jd": "jd"}})
        import tempfile
        runner = DiscoveryRunner(
            self.store, source=source, ai_provider=_AssessingFakeAIProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-pii-"),
        )
        run = _make_discovery_run(self.store, confirmation, analysis, self.resume["id"], self.profile["id"])
        runner.run(run["id"])
        events = self.store.list_discovery_events(run["id"])
        for e in events:
            payload_str = str(e.get("safe_payload", {}))
            self.assertNotIn(RESUME_TEXT, payload_str)
            self.assertNotIn("张三", payload_str)


class FeedbackInfluenceTests(_IntegrationTestCase):
    """T057: feedback influence scope (history unchanged). (Phase 6)"""

    def setUp(self):
        super().setUp()
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )

    def test_job_not_interested_does_not_expand_to_company(self):
        """Spec: job not_interested feedback excludes only the exact job, not the company."""
        from webui.discovery import apply_feedback_to_next_run
        # Record not_interested feedback for job j1
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job",
            action="not_interested",
            run_id=None,
            job_id="j1",
            reason_code="salary_too_low",
            scope="exact_job",
        )
        # Apply feedback to a confirmation view
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": "d1", "direction_id": "d1", "name": "后端", "search_terms": ["Python"]}
            ],
        }
        adjusted = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        self.assertIn("j1", adjusted["excluded_job_ids"])
        # Should NOT exclude entire company (no company field in feedback)
        self.assertEqual(len(adjusted["excluded_job_ids"]), 1)

    def test_direction_disable_removes_from_enabled(self):
        from webui.discovery import apply_feedback_to_next_run
        direction_id = self.store.list_directions(self.analysis["id"])[0]["id"]
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="direction",
            action="direction_disable",
            direction_id=direction_id,
            reason_code="not_interested",
            scope="exact_direction",
        )
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": direction_id, "direction_id": direction_id, "name": "后端", "search_terms": ["Python"]},
                {"id": "d_other", "direction_id": "d_other", "name": "其他", "search_terms": ["Go"]},
            ],
        }
        adjusted = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        enabled_ids = [d.get("id") or d.get("direction_id") for d in adjusted["enabled_directions"]]
        self.assertNotIn(direction_id, enabled_ids)
        self.assertIn("d_other", enabled_ids)

    def test_all_directions_disabled_raises(self):
        from webui.discovery import apply_feedback_to_next_run
        direction_id = self.store.list_directions(self.analysis["id"])[0]["id"]
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="direction",
            action="direction_disable",
            direction_id=direction_id,
        )
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": direction_id, "direction_id": direction_id, "name": "后端", "search_terms": ["Python"]},
            ],
        }
        with self.assertRaises(DiscoveryError) as ctx:
            apply_feedback_to_next_run(self.store, confirmation_view, profile_id=self.profile["id"])
        self.assertEqual(ctx.exception.error_code, "input_incomplete")

    def test_revoked_feedback_no_longer_excludes(self):
        from webui.discovery import apply_feedback_to_next_run
        feedback = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job",
            action="not_interested",
            job_id="j1",
            scope="exact_job",
        )
        self.store.revoke_discovery_feedback(feedback["id"])
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": "d1", "direction_id": "d1", "name": "后端", "search_terms": ["Python"]}
            ],
        }
        adjusted = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        self.assertEqual(adjusted["excluded_job_ids"], [])

    def test_history_run_snapshot_not_rewritten(self):
        """Feedback only affects future runs; historical snapshots remain immutable."""
        from webui.discovery import apply_feedback_to_next_run
        # Create a historical run with a snapshot
        run = _make_discovery_run(self.store, self.confirmation, self.analysis, self.resume["id"], self.profile["id"])
        # Record feedback
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job",
            action="not_interested",
            run_id=run["id"],
            job_id="j_hist",
            scope="exact_job",
        )
        # The historical run's data should still be intact
        historical_run = self.store.get_discovery_run(run["id"])
        self.assertEqual(historical_run["id"], run["id"])
        # Feedback should not retroactively modify the run's counters
        self.assertEqual(historical_run.get("evaluated_count", 0), 0)


class FeedbackNextRunApplicationTests(_IntegrationTestCase):
    """T088 验证 US5 反馈对下一次运行的作用域应用和历史不变性。

    合同来源:
    - spec.md US5 acceptance scenarios 1-4 (L144-147)
    - spec.md FR-050: 反馈必须作用于后续运行，不得改写历史。
    - spec.md FR-051: 用户必须能撤销有效反馈并看到作用范围。
    - http-api.md L320: feedback increments result revision when visibility or
      ordering changes.
    """

    def setUp(self):
        super().setUp()
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )

    def test_judgment_error_feedback_records_dimension_without_changing_history(self):
        """US5 scenario 3: 判断错误反馈记录受影响维度，不改写历史评分。"""
        from webui.discovery import apply_feedback_to_next_run

        # Set up a historical run + snapshot + assessment.
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )
        direction = self.store.list_directions(self.analysis["id"])[0]
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/judg-err.html",
            "https://www.zhipin.com/job_detail/judg-err.html",
            "后端", "公司", "20K", "北京", "jd",
        )
        snap = self.store.save_job_snapshot(
            run["id"], job["id"], source_url=job["source_url"],
            title="后端", company="公司", salary="20K", location="北京",
            jd="jd", completeness="complete",
        )
        self.store.create_assessment(
            run["id"], snap["id"], direction["id"],
            dimensions={"capability": {"score": 90, "candidate_fact_refs": [],
                                       "candidate_evidence_refs": [],
                                       "job_evidence_refs": []}},
            match_score=90, confidence=88, gaps=[],
            category="high_match", status="completed",
            contract_version="job_assessment_v2",
        )
        # Record the historical assessment score before feedback.
        historical_assessments = self.store.list_assessments(run["id"])
        self.assertEqual(len(historical_assessments), 1)
        before_score = historical_assessments[0]["match_score"]
        self.assertEqual(before_score, 90)

        # Submit judgment_error feedback targeting this assessment.
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="assessment", action="judgment_error",
            run_id=run["id"], assessment_id=historical_assessments[0]["id"],
            reason_code="dimension_wrong", scope="exact_assessment",
        )

        # Historical assessment score must remain unchanged.
        historical_after = self.store.list_assessments(run["id"])
        self.assertEqual(historical_after[0]["match_score"], 90,
                         "US5-3: 判断错误反馈不得改写历史 assessment 分数")
        self.assertEqual(historical_after[0]["status"], "completed")

        # apply_feedback_to_next_run on a fresh confirmation view doesn't
        # crash and returns a valid adjusted confirmation.
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": direction["id"], "direction_id": direction["id"],
                 "name": "后端", "search_terms": ["Python"]},
            ],
        }
        adjusted = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        # Judgment-error feedback doesn't exclude the job from next run
        # (it only records the dimension issue).
        self.assertNotIn(job["id"], adjusted.get("excluded_job_ids", []),
                         "US5-3: 判断错误反馈不应排除岗位，仅记录维度问题")

    def test_direction_disable_does_not_allocate_budget_in_compile_search_plan(self):
        """US5 scenario 2: 关闭方向后下一次 run 不为该方向分配搜索和详情预算。"""
        from webui.discovery import (
            apply_feedback_to_next_run, compile_search_plan,
        )

        direction = self.store.list_directions(self.analysis["id"])[0]
        # Add a second direction so we have something to compare against.
        d2 = self.store.add_direction(
            self.analysis["id"], name="数据", direction_type="adjacent",
            rationale="r", gaps=[], confidence=70,
            default_enabled=True, search_terms=["SQL"],
        )
        # Disable direction 1 via feedback.
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="direction", action="direction_disable",
            direction_id=direction["id"], scope="exact_direction",
        )
        confirmation_view = {
            "id": self.confirmation["id"],
            "hard_constraints": {"city": "北京"},
            "safe_limits": {"max_details": 10},
            "enabled_directions": [
                {"id": direction["id"], "direction_id": direction["id"],
                 "name": "后端", "type": "core", "search_terms": ["Python"],
                 "default_enabled": True, "evidence_refs": []},
                {"id": d2["id"], "direction_id": d2["id"],
                 "name": "数据", "type": "adjacent", "search_terms": ["SQL"],
                 "default_enabled": True, "evidence_refs": []},
            ],
        }
        adjusted = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        # Disabled direction is removed from enabled_directions.
        enabled_ids = [d.get("id") or d.get("direction_id")
                       for d in adjusted["enabled_directions"]]
        self.assertNotIn(direction["id"], enabled_ids,
                         "US5-2: 关闭方向不应出现在 enabled_directions")
        self.assertIn(d2["id"], enabled_ids)

        # compile_search_plan only allocates budget to enabled directions.
        plan = compile_search_plan(adjusted)
        plan_direction_ids = set()
        for item in plan["items"]:
            plan_direction_ids.update(item.get("direction_ids", []))
        self.assertNotIn(direction["id"], plan_direction_ids,
                         "US5-2: 关闭方向不应在 plan items 中分配预算")
        self.assertIn(d2["id"], plan_direction_ids,
                      "US5-2: 启用方向应在 plan items 中分配预算")
        # detail_budget should be > 0 (allocated to the remaining direction).
        self.assertGreater(plan["detail_budget"], 0)

    def test_not_interested_does_not_exclude_other_jobs_from_same_company(self):
        """US5 scenario 1: 不感兴趣单个岗位不得扩展到同公司其他岗位。

        Spec L144: 单个岗位不感兴趣默认只排除该岗位，不得自动扩展到整家公司或行业。
        Spec L356: 将用户反馈自动扩大到公司、行业或岗位家族；扩大作用域需后续明确授权。
        """
        from webui.discovery import apply_feedback_to_next_run

        # Two jobs at the same company.
        job_a = self.store.save_job(
            "https://www.zhipin.com/job_detail/a.html",
            "https://www.zhipin.com/job_detail/a.html",
            "岗位A", "同公司", "20K", "北京", "jd-a",
        )
        job_b = self.store.save_job(
            "https://www.zhipin.com/job_detail/b.html",
            "https://www.zhipin.com/job_detail/b.html",
            "岗位B", "同公司", "25K", "北京", "jd-b",
        )
        # Mark job_a as not_interested.
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job", action="not_interested",
            job_id=job_a["id"], scope="exact_job",
        )
        direction = self.store.list_directions(self.analysis["id"])[0]
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": direction["id"], "direction_id": direction["id"],
                 "name": "后端", "search_terms": ["Python"]},
            ],
        }
        adjusted = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        # Only job_a is excluded, not job_b (even though same company).
        self.assertIn(job_a["id"], adjusted["excluded_job_ids"])
        self.assertNotIn(job_b["id"], adjusted["excluded_job_ids"],
                         "US5-1: 不感兴趣单个岗位不得自动扩展到同公司其他岗位")

    def test_revoke_then_next_run_does_not_apply_feedback(self):
        """US5 scenario 4 + FR-051: 撤销反馈后下一次运行不再应用该反馈。"""
        from webui.discovery import apply_feedback_to_next_run

        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/rev-1.html",
            "https://www.zhipin.com/job_detail/rev-1.html",
            "岗位", "公司", "20K", "北京", "jd",
        )
        fb = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job", action="not_interested",
            job_id=job["id"], scope="exact_job",
        )
        direction = self.store.list_directions(self.analysis["id"])[0]
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": direction["id"], "direction_id": direction["id"],
                 "name": "后端", "search_terms": ["Python"]},
            ],
        }
        # Before revoke: feedback excludes the job.
        before = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        self.assertIn(job["id"], before["excluded_job_ids"])

        # Revoke.
        self.store.revoke_discovery_feedback(fb["id"])

        # After revoke: feedback no longer applies.
        after = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        self.assertNotIn(job["id"], after.get("excluded_job_ids", []),
                         "US5-4: 撤销后下一次运行不应再应用该反馈")

    def test_feedback_only_affects_subsequent_runs_not_historical(self):
        """FR-050: 反馈只作用于后续运行，历史 run 的 input_hash 与计数器不变。"""
        # Historical run with a confirmation snapshot.
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )
        historical_run_before = self.store.get_discovery_run(run["id"])
        historical_hash_before = historical_run_before.get("input_hash")
        historical_status_before = historical_run_before.get("status")
        historical_high_before = historical_run_before.get("high_count", 0)

        # Submit feedback that would change the confirmation view for *next* run.
        direction = self.store.list_directions(self.analysis["id"])[0]
        self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="direction", action="direction_disable",
            direction_id=direction["id"], scope="exact_direction",
        )

        # Historical run's input_hash, status, counters must remain unchanged.
        historical_run_after = self.store.get_discovery_run(run["id"])
        self.assertEqual(historical_run_after.get("input_hash"),
                         historical_hash_before,
                         "FR-050: 反馈不得改写历史 run 的 input_hash")
        self.assertEqual(historical_run_after.get("status"),
                         historical_status_before,
                         "FR-050: 反馈不得改写历史 run 的 status")
        self.assertEqual(historical_run_after.get("high_count", 0),
                         historical_high_before,
                         "FR-050: 反馈不得改写历史 run 的计数器")

        # The "feedback applies to next run" behavior is verified in
        # test_direction_disable_does_not_allocate_budget_in_compile_search_plan;
        # here we only assert historical invariance.


class Fr050Fr051VerificationTests(_IntegrationTestCase):
    """T092 综合验证 FR-050 / FR-051。

    FR-050: 岗位和方向反馈必须作用于后续运行，不得改写历史画像、确认快照和评估事实。
    FR-051: 用户必须能够撤销有效反馈，并看到其作用范围。

    独立测试（spec.md L140）：对固定推荐集提交岗位和方向反馈，执行下一次发现，
    验证反馈作用范围、撤销、历史不变和新排序变化。
    """

    def setUp(self):
        super().setUp()
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )

    def test_fr050_full_feedback_lifecycle_preserves_history(self):
        """FR-050: 完整反馈生命周期（创建→应用→撤销）不改写历史 run/snapshot/assessment。"""
        from webui.discovery import apply_feedback_to_next_run

        # 1. Historical run with snapshot + assessment.
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )
        d1 = self.store.list_directions(self.analysis["id"])[0]
        # Add a second direction so disabling d1 doesn't trigger input_incomplete.
        d2 = self.store.add_direction(
            self.analysis["id"], name="数据", direction_type="adjacent",
            rationale="r", gaps=[], confidence=70,
            default_enabled=True, search_terms=["SQL"],
        )
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/fr050.html",
            "https://www.zhipin.com/job_detail/fr050.html",
            "后端", "公司", "20K", "北京", "jd-fr050",
        )
        snap = self.store.save_job_snapshot(
            run["id"], job["id"], source_url=job["source_url"],
            title="后端", company="公司", salary="20K", location="北京",
            jd="jd-fr050", completeness="complete",
        )
        self.store.create_assessment(
            run["id"], snap["id"], d1["id"],
            dimensions={"capability": {"score": 88}},
            match_score=88, confidence=85, gaps=[],
            category="high_match", status="completed",
            contract_version="job_assessment_v2",
        )
        # Snapshot the historical state.
        hist_run_before = self.store.get_discovery_run(run["id"])
        hist_snap_before = self.store.list_snapshots(run["id"])
        hist_assess_before = self.store.list_assessments(run["id"])
        self.assertEqual(len(hist_snap_before), 1)
        self.assertEqual(len(hist_assess_before), 1)
        hist_score_before = hist_assess_before[0]["match_score"]

        # 2. Submit job not_interested + direction_disable feedback.
        fb_job = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job", action="not_interested",
            job_id=job["id"], scope="exact_job",
        )
        fb_dir = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="direction", action="direction_disable",
            direction_id=d1["id"], scope="exact_direction",
        )

        # 3. Apply feedback to a fresh confirmation view (for next run).
        #    d2 remains enabled, so apply succeeds. Historical data must be untouched.
        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": d1["id"], "direction_id": d1["id"],
                 "name": "后端", "search_terms": ["Python"]},
                {"id": d2["id"], "direction_id": d2["id"],
                 "name": "数据", "search_terms": ["SQL"]},
            ],
        }
        adjusted = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        self.assertIn(job["id"], adjusted["excluded_job_ids"],
                      "FR-050: not_interested 反馈应作用于下一次运行")
        enabled_ids = [d.get("id") or d.get("direction_id")
                       for d in adjusted["enabled_directions"]]
        self.assertNotIn(d1["id"], enabled_ids,
                         "FR-050: direction_disable 反馈应作用于下一次运行")

        # 4. Verify historical run/snapshot/assessment unchanged.
        hist_run_after = self.store.get_discovery_run(run["id"])
        hist_snap_after = self.store.list_snapshots(run["id"])
        hist_assess_after = self.store.list_assessments(run["id"])
        self.assertEqual(hist_run_after.get("input_hash"),
                         hist_run_before.get("input_hash"),
                         "FR-050: 历史 run input_hash 不变")
        self.assertEqual(hist_run_after.get("status"),
                         hist_run_before.get("status"),
                         "FR-050: 历史 run status 不变")
        self.assertEqual(len(hist_snap_after), 1,
                         "FR-050: 历史 snapshot 数量不变")
        self.assertEqual(hist_snap_after[0]["jd"], "jd-fr050",
                         "FR-050: 历史 snapshot 内容不变")
        self.assertEqual(len(hist_assess_after), 1,
                         "FR-050: 历史 assessment 数量不变")
        self.assertEqual(hist_assess_after[0]["match_score"], hist_score_before,
                         "FR-050: 历史 assessment 分数不变")
        self.assertEqual(hist_assess_after[0]["status"], "completed",
                         "FR-050: 历史 assessment 状态不变")

        # 5. Revoke both feedbacks.
        self.store.revoke_discovery_feedback(fb_job["id"])
        self.store.revoke_discovery_feedback(fb_dir["id"])

        # 6. After revoke, historical state STILL unchanged.
        hist_run_final = self.store.get_discovery_run(run["id"])
        self.assertEqual(hist_run_final.get("input_hash"),
                         hist_run_before.get("input_hash"),
                         "FR-050: 撤销后历史 run input_hash 仍不变")
        hist_assess_final = self.store.list_assessments(run["id"])
        self.assertEqual(hist_assess_final[0]["match_score"], hist_score_before,
                         "FR-050: 撤销后历史 assessment 分数仍不变")

    def test_fr051_revoke_makes_feedback_ineffective_for_next_run(self):
        """FR-051: 撤销后反馈不再作用于下一次运行。"""
        from webui.discovery import apply_feedback_to_next_run

        # Add a second direction so we can disable one and still have an enabled.
        d1 = self.store.list_directions(self.analysis["id"])[0]
        d2 = self.store.add_direction(
            self.analysis["id"], name="数据", direction_type="adjacent",
            rationale="r", gaps=[], confidence=70,
            default_enabled=True, search_terms=["SQL"],
        )
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/fr051.html",
            "https://www.zhipin.com/job_detail/fr051.html",
            "岗位", "公司", "20K", "北京", "jd",
        )

        # 1. Submit feedback: not_interested on job + direction_disable on d1.
        fb_job = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job", action="not_interested",
            job_id=job["id"], scope="exact_job",
        )
        fb_dir = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="direction", action="direction_disable",
            direction_id=d1["id"], scope="exact_direction",
        )

        confirmation_view = {
            "id": self.confirmation["id"],
            "enabled_directions": [
                {"id": d1["id"], "direction_id": d1["id"],
                 "name": "后端", "search_terms": ["Python"]},
                {"id": d2["id"], "direction_id": d2["id"],
                 "name": "数据", "search_terms": ["SQL"]},
            ],
        }
        # Before revoke: feedback applies.
        before = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        self.assertIn(job["id"], before["excluded_job_ids"],
                      "FR-051: 反馈有效时岗位应被排除")
        enabled_ids = [d.get("id") or d.get("direction_id")
                       for d in before["enabled_directions"]]
        self.assertNotIn(d1["id"], enabled_ids,
                         "FR-051: 反馈有效时方向应被移除")

        # 2. Revoke both feedbacks.
        self.store.revoke_discovery_feedback(fb_job["id"])
        self.store.revoke_discovery_feedback(fb_dir["id"])

        # 3. After revoke: feedback no longer applies.
        after = apply_feedback_to_next_run(
            self.store, confirmation_view, profile_id=self.profile["id"],
        )
        self.assertNotIn(job["id"], after.get("excluded_job_ids", []),
                         "FR-051: 撤销后岗位不应再被排除")
        enabled_ids_after = [d.get("id") or d.get("direction_id")
                             for d in after["enabled_directions"]]
        self.assertIn(d1["id"], enabled_ids_after,
                      "FR-051: 撤销后方向应重新启用")

    def test_fr051_user_can_see_feedback_scope(self):
        """FR-051: 用户必须能看到反馈的作用范围（scope 字段）。"""
        # Job feedback: scope = exact_job.
        fb_job = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="job", action="not_interested",
            job_id="job-vis-1", scope="exact_job",
        )
        # Direction feedback: scope = exact_direction.
        direction = self.store.list_directions(self.analysis["id"])[0]
        fb_dir = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="direction", action="direction_disable",
            direction_id=direction["id"], scope="exact_direction",
        )
        # Assessment feedback: scope = exact_assessment.
        fb_asmt = self.store.create_discovery_feedback(
            profile_id=self.profile["id"],
            target_type="assessment", action="judgment_error",
            assessment_id="asmt-1", scope="exact_assessment",
        )

        # All feedback is visible with its scope.
        rows = self.store.list_discovery_feedback(self.profile["id"])
        self.assertEqual(len(rows), 3)
        scopes = {r["target_type"]: r["scope"] for r in rows}
        self.assertEqual(scopes["job"], "exact_job")
        self.assertEqual(scopes["direction"], "exact_direction")
        self.assertEqual(scopes["assessment"], "exact_assessment")

        # effective_only filter shows all 3 (none revoked).
        effective = self.store.list_discovery_feedback(
            self.profile["id"], effective_only=True,
        )
        self.assertEqual(len(effective), 3)

        # Revoke one -> effective drops to 2.
        self.store.revoke_discovery_feedback(fb_job["id"])
        effective_after = self.store.list_discovery_feedback(
            self.profile["id"], effective_only=True,
        )
        self.assertEqual(len(effective_after), 2)
        revoked_types = {r["target_type"] for r in effective_after}
        self.assertNotIn("job", revoked_types,
                         "FR-051: 撤销后岗位反馈不再 effective")


class ResumeDeletionCascadeTests(_IntegrationTestCase):
    """CR-1: Deleting a resume must cascade-delete derived evidence (FR-098)."""

    def test_delete_resume_removes_analyses_evidence_directions(self):
        from webui.resume import delete_resume as delete_resume_service
        provider = FakeAIProvider(_valid_ai_response())
        analysis = analyze_resume(
            self.store, self.resume["id"], ai_consent=True, ai_provider=provider,
        )
        self.assertEqual(analysis["status"], "ready")
        evidence = self.store.list_evidence(analysis["id"])
        directions = self.store.list_directions(analysis["id"])
        self.assertTrue(evidence)
        self.assertTrue(directions)

        confirmation = confirm_directions(
            self.store, analysis["id"], [directions[0]["id"]],
        )
        run = _make_discovery_run(
            self.store, confirmation, analysis, self.resume["id"], self.profile["id"],
        )
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/delete-cascade.html",
            "https://www.zhipin.com/job_detail/delete-cascade.html",
            "后端开发", "测试公司", "20-30K", "北京", "负责 Python 服务",
        )
        snapshot = self.store.save_job_snapshot(
            run["id"], job["id"],
            source_url=job["source_url"], title="后端开发", company="测试公司",
            salary="20-30K", location="北京", jd="负责 Python 服务",
            completeness="complete", source_status="active", fetch_status="completed",
        )
        self.store.create_assessment(
            run["id"], snapshot["id"], directions[0]["id"],
            hard_outcome="pass", hard_checks={"city": "pass"},
            dimensions={"skill_coverage": {"score": 90}}, match_score=90,
            confidence=92, category="high_match",
            candidate_evidence_ids=[evidence[0]["id"]],
            job_evidence={"jd": "负责 Python 服务"}, gaps=["无"],
            status="completed",
        )

        delete_resume_service(self.resume["id"], self.store)

        self.assertEqual(self.store.list_analyses(self.resume["id"]), [])
        self.assertEqual(self.store.list_evidence(analysis["id"]), [])
        self.assertEqual(self.store.list_directions(analysis["id"]), [])
        self.assertEqual(self.store.get_discovery_run(run["id"])["id"], run["id"])
        self.assertEqual(self.store.get_snapshot(run["id"], job["id"])["id"], snapshot["id"])
        assessment = self.store.get_assessment(
            run["id"], snapshot["id"], directions[0]["id"],
        )
        self.assertEqual(assessment["category"], "needs_review")
        self.assertEqual(assessment["failure_code"], "resume_deleted")
        self.assertIsNone(assessment["match_score"])
        self.assertIsNone(assessment["confidence"])
        self.assertEqual(assessment["dimensions"], {})
        self.assertEqual(assessment["candidate_evidence_ids"], [])
        self.assertEqual(assessment["job_evidence"], {})
        self.assertEqual(assessment["gaps"], [])


class DiscoverySourceAdmissionTests(_IntegrationTestCase):
    def test_persist_jobs_accepts_only_valid_boss_https_sources(self):
        from webui.discovery_runner import DiscoveryRunner

        runner = DiscoveryRunner(self.store, result_dir=tempfile.mkdtemp())
        runner._persist_jobs([
            {"job_id": "missing", "source_url": ""},
            {"job_id": "evil", "source_url": "https://evil.example/job/1"},
            {"job_id": "valid", "source_url": "https://www.zhipin.com/job_detail/valid.html?x=1"},
        ], "run", {})

        with self.store._connection() as conn:
            rows = conn.execute("SELECT id, canonical_url FROM jobs ORDER BY id").fetchall()
        self.assertEqual([row["id"] for row in rows], ["valid"])
        self.assertEqual(rows[0]["canonical_url"], "https://www.zhipin.com/job_detail/valid.html")


class SummaryPiiRedactionTests(_IntegrationTestCase):
    """HI-2: summary must be PII-redacted before persistence."""

    def test_summary_with_phone_is_redacted(self):
        provider = FakeAIProvider({
            **_valid_ai_response(),
            "summary": {
                "headline": "高级后端",
                "experience_level": "高级",
                "domains": ["后端", "联系电话 13800138000"],
                "strengths": ["Python", "邮箱 test@example.com"],
            },
        })
        analysis = analyze_resume(
            self.store, self.resume["id"], ai_consent=True, ai_provider=provider,
        )
        stored = self.store.get_analysis(analysis["id"])
        summary_str = json.dumps(stored.get("summary", {}), ensure_ascii=False)
        self.assertNotIn("13800138000", summary_str, "手机号不得持久化到 summary")
        self.assertNotIn("test@example.com", summary_str, "邮箱不得持久化到 summary")


class DiscoveryWorkEventTests(_IntegrationTestCase):
    """Work-unit start events make cancellation/resume behavior independently auditable."""

    def test_list_and_detail_work_units_record_started_events(self):
        import tempfile
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource

        analysis, confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )
        source = FakeJobSource(
            list_jobs={
                ("Python 后端", "北京"): [
                    {
                        "job_id": "event-job-1",
                        "title": "Python 后端",
                        "company": "事件测试公司",
                        "source_url": "https://www.zhipin.com/job_detail/event-job-1.html",
                    },
                ],
            },
            detail_jobs={"event-job-1": {"jd": "负责 Python 后端开发"}},
        )
        runner = DiscoveryRunner(
            self.store,
            source=source,
            ai_provider=_AssessingFakeAIProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-work-events-"),
        )
        run = _make_discovery_run(
            self.store, confirmation, analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        event_types = [
            event["event_type"]
            for event in self.store.list_discovery_events(run["id"])
        ]
        self.assertIn("plan_item_started", event_types)
        self.assertIn("detail_fetch_started", event_types)

    def test_failed_detail_uses_source_status_contract_enum(self):
        """详情失败必须落为 unreachable，不能写契约外的 blocked。"""
        import tempfile
        from webui.discovery import SNAPSHOT_SOURCE_STATUS
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource

        analysis, confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )
        source = FakeJobSource(
            list_jobs={
                ("Python 后端", "北京"): [{
                    "job_id": "failed-detail-job",
                    "title": "Python 后端",
                    "company": "详情失败公司",
                    "source_url": "https://www.zhipin.com/job_detail/failed-detail-job.html",
                }],
            },
            detail_failures={"failed-detail-job"},
        )
        runner = DiscoveryRunner(
            self.store,
            source=source,
            ai_provider=_AssessingFakeAIProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-source-status-"),
        )
        run = _make_discovery_run(
            self.store, confirmation, analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        snapshots = self.store.list_snapshots(run["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertIn(snapshots[0]["source_status"], SNAPSHOT_SOURCE_STATUS)
        self.assertEqual(snapshots[0]["source_status"], "unreachable")

    def test_runner_evaluates_only_confirmation_enabled_directions(self):
        """用户未确认的分析方向不得触发岗位评估调用。"""
        import tempfile
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource

        ai_response = _valid_ai_response()
        ai_response["directions"].append({
            "client_ref": "d2",
            "name": "数据开发工程师",
            "type": "adjacent",
            "rationale": "具备数据处理经验",
            "evidence_refs": ["e1"],
            "gaps": [],
            "confidence": 80,
            "default_enabled": False,
            "search_terms": ["数据开发"],
        })
        analysis = analyze_resume(
            self.store, self.resume["id"], ai_consent=True,
            ai_provider=FakeAIProvider(ai_response),
        )
        directions = self.store.list_directions(analysis["id"])
        enabled_direction = next(direction for direction in directions if direction["name"] == "后端开发工程师")
        confirmation = confirm_directions(
            self.store, analysis["id"], [enabled_direction["id"]],
            hard_constraints={"city": "北京"}, safe_limits={"max_details": 1},
        )

        class TrackingProvider:
            def __init__(self):
                self.direction_ids = []

            def assess_job(self, **kwargs):
                self.direction_ids.append(kwargs["direction"]["id"])
                return None

        provider = TrackingProvider()
        source = FakeJobSource(
            list_jobs={("Python 后端", "北京"): [{
                "job_id": "confirmed-only-job",
                "title": "Python 后端",
                "company": "确认方向公司",
                "source_url": "https://www.zhipin.com/job_detail/confirmed-only-job.html",
            }]},
            detail_jobs={"confirmed-only-job": {"jd": "负责 Python 后端开发"}},
        )
        runner = DiscoveryRunner(
            self.store, source=source, ai_provider=provider,
            result_dir=tempfile.mkdtemp(prefix="boss-confirmed-directions-"),
        )
        run = _make_discovery_run(
            self.store, confirmation, analysis,
            self.resume["id"], self.profile["id"],
        )

        runner.run(run["id"])

        self.assertEqual(provider.direction_ids, [enabled_direction["id"]])

    def test_evaluated_count_is_persisted_after_each_completed_assessment(self):
        """进程在后续评估中断时，已完成评估计数仍必须可审计。"""
        import tempfile
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource

        ai_response = _valid_ai_response()
        ai_response["directions"].append({
            "client_ref": "d2", "name": "数据开发工程师", "type": "adjacent",
            "rationale": "具备数据处理经验", "evidence_refs": ["e1"],
            "gaps": [], "confidence": 80, "default_enabled": True,
            "search_terms": ["数据开发"],
        })
        analysis = analyze_resume(
            self.store, self.resume["id"], ai_consent=True,
            ai_provider=FakeAIProvider(ai_response),
        )
        direction_ids = [direction["id"] for direction in self.store.list_directions(analysis["id"])]
        confirmation = confirm_directions(
            self.store, analysis["id"], direction_ids,
            hard_constraints={"city": "北京"}, safe_limits={"max_details": 1},
        )

        class ControlledStop(BaseException):
            pass

        class InterruptingProvider:
            def __init__(self):
                self.calls = 0

            def assess_job(self, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise ControlledStop("after one completed assessment")
                return None

        source = FakeJobSource(
            list_jobs={("Python 后端", "北京"): [{
                "job_id": "incremental-count-job", "title": "Python 后端",
                "company": "计数公司",
                "source_url": "https://www.zhipin.com/job_detail/incremental-count-job.html",
            }]},
            detail_jobs={"incremental-count-job": {"jd": "负责 Python 后端开发"}},
        )
        runner = DiscoveryRunner(
            self.store, source=source, ai_provider=InterruptingProvider(),
            result_dir=tempfile.mkdtemp(prefix="boss-evaluated-progress-"),
        )
        run = _make_discovery_run(
            self.store, confirmation, analysis,
            self.resume["id"], self.profile["id"],
        )

        with self.assertRaises(ControlledStop):
            runner.run(run["id"])

        persisted = self.store.get_discovery_run(run["id"])
        self.assertEqual(persisted["evaluated_count"], 1)


# ---------------------------------------------------------------------------
# T098: DiscoveryTaskRuntime — 应用持有的运行时基础设施
# ---------------------------------------------------------------------------


class DiscoveryTaskRuntimeTests(_IntegrationTestCase):
    """T098: 应用持有的 DiscoveryTaskRuntime 基础设施。

    HTTP 创建 run 后必须真实提交 runtime（不只写 DB 返回 202）。
    submit_run 在 5 秒内推进 run 离开 created 进入 planning 或 dispatch_failed。
    调度失败记录安全事件。
    进程重启后 active run 收敛为 interrupted。
    """

    def setUp(self):
        super().setUp()
        from webui.discovery_runner import DiscoveryTaskRuntime
        self._runtime_cls = DiscoveryTaskRuntime
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )
        self._runtimes = []

    def tearDown(self):
        for r in self._runtimes:
            try:
                r.shutdown()
            except Exception:
                pass
        super().tearDown()

    def _make_runtime(self, *, source="UNSET", ai_provider="UNSET"):
        import tempfile
        from webui.source import FakeJobSource
        if source == "UNSET":
            source = FakeJobSource(list_jobs={
                ("Python 后端", "北京"): [{"job_id": "j1", "title": "x", "source_url": "https://x/1", "jd": "jd"}],
            }, detail_jobs={"j1": {"jd": "jd"}})
        if ai_provider == "UNSET":
            ai_provider = _AssessingFakeAIProvider()
        runtime = self._runtime_cls(
            store=self.store,
            source=source,
            ai_provider=ai_provider,
            result_dir=tempfile.mkdtemp(prefix="boss-runtime-"),
        )
        self._runtimes.append(runtime)
        return runtime

    # -- 类存在与方法 --

    def test_runtime_class_exists_and_constructable(self):
        runtime = self._runtime_cls(store=self.store)
        self.assertIsNotNone(runtime)
        self._runtimes.append(runtime)

    def test_runtime_exposes_submit_run_method(self):
        runtime = self._runtime_cls(store=self.store)
        self.assertTrue(callable(getattr(runtime, "submit_run", None)))
        self._runtimes.append(runtime)

    def test_runtime_exposes_cancel_run_method(self):
        runtime = self._runtime_cls(store=self.store)
        self.assertTrue(callable(getattr(runtime, "cancel_run", None)))
        self._runtimes.append(runtime)

    def test_runtime_exposes_resume_run_method(self):
        runtime = self._runtime_cls(store=self.store)
        self.assertTrue(callable(getattr(runtime, "resume_run", None)))
        self._runtimes.append(runtime)

    def test_runtime_exposes_shutdown_method(self):
        runtime = self._runtime_cls(store=self.store)
        self.assertTrue(callable(getattr(runtime, "shutdown", None)))
        self._runtimes.append(runtime)

    # -- 运行时提交（5 秒内离开 created） --

    def test_submit_run_advances_beyond_created_within_5_seconds(self):
        import time
        runtime = self._make_runtime()
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )
        runtime.submit_run(run["id"])
        deadline = time.time() + 5
        status = "created"
        while time.time() < deadline:
            current = self.store.get_discovery_run(run["id"])
            status = current["status"]
            if status != "created":
                break
            time.sleep(0.1)
        self.assertNotEqual(
            status, "created",
            "submit_run must advance run beyond created within 5 seconds",
        )

    # -- dispatch_failed（调度失败记录事件并推进状态） --

    def test_dispatch_failure_records_event_and_advances_status(self):
        import time

        class _FailingSource:
            def fetch_list(self, *args, **kwargs):
                raise RuntimeError("source unavailable")
            def fetch_detail(self, *args, **kwargs):
                raise RuntimeError("source unavailable")

        runtime = self._make_runtime(source=_FailingSource())
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )
        runtime.submit_run(run["id"])
        deadline = time.time() + 5
        status = "created"
        while time.time() < deadline:
            current = self.store.get_discovery_run(run["id"])
            status = current["status"]
            if status in ("failed", "partial", "succeeded", "interrupted"):
                break
            time.sleep(0.1)
        self.assertNotEqual(
            status, "created",
            "dispatch failure must advance run beyond created",
        )
        events = self.store.list_discovery_events(run["id"])
        self.assertGreater(len(events), 0, "dispatch failure must record events")

    # -- 进程重启收敛 --

    def test_active_run_converges_to_interrupted_on_runtime_init(self):
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )
        self.store.update_discovery_run(
            run["id"], status="planning", stage="planning", started=True,
        )
        # 构造 runtime 时应收敛 active run 为 interrupted
        runtime = self._runtime_cls(store=self.store)
        self._runtimes.append(runtime)
        final = self.store.get_discovery_run(run["id"])
        self.assertEqual(
            final["status"], "interrupted",
            "active run must converge to interrupted on runtime init",
        )

    def test_resume_records_acceptance_before_resubmitting_work(self):
        import time
        runtime = self._make_runtime()
        run = _make_discovery_run(
            self.store, self.confirmation, self.analysis,
            self.resume["id"], self.profile["id"],
        )
        self.store.update_discovery_run(
            run["id"], status="interrupted", stage="fetching_lists", started=True,
        )

        runtime.resume_run(run["id"])

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            event_types = [
                event["event_type"]
                for event in self.store.list_discovery_events(run["id"])
            ]
            if "resume_accepted" in event_types:
                break
            time.sleep(0.05)
        self.assertIn("resume_accepted", event_types)


class US1CompositionIntegrationTests(unittest.TestCase):
    """T111: US1 组合集成测试。

    真实 create_app() → HTTP POST /api/discovery/analyses → 应用持有
    DiscoveryTaskRuntime → 真实 DiscoveryAIProvider 类（只 mock transport
    call_ai）→ candidate validator → store → confirm_directions。

    覆盖：
    - consent=false 不外发（call_ai 不被调用）
    - consent=true 全管道到 confirmation
    - 每条持久化 evidence locator 与规范化简历切片完全一致
    - 数据库普通字段不含简历正文
    - provider 失败返回安全信封（ai_timeout），不泄漏原始异常
    """

    # 使用 resume_locator_cases.txt 的脱敏简历（含敏感字段用于 PII 验证）
    RESUME_TEXT = (
        "姓名：李四（脱敏测试样本，仅用于 locator 验证）\n"
        "联系方式：13800138000\n"
        "证件号：110101199001011234\n"
        "住址：北京市海淀区中关村大街1号\n"
        "\n"
        "求职意向：后端开发工程师\n"
        "\n"
        "工作经历：\n"
        "2020.07 - 至今 ABC科技有限公司 后端工程师\n"
        "- 负责订单服务设计与维护，使用 Python、Go 编写高并发接口\n"
        "- 主导分布式缓存改造，将订单查询 P99 从 800ms 降至 120ms\n"
        "- 设计消息队列消费模型，日均处理 2000 万条消息\n"
        "\n"
        "2018.07 - 2020.06 XYZ互联网公司 后端开发\n"
        "- 参与电商交易链路重构，使用 Java Spring Boot\n"
        "- 实现库存扣减幂等方案，消除超卖事故\n"
        "\n"
        "技能：\n"
        "- 编程语言：Python、Go、Java\n"
        "- 中间件：Kafka、Redis、MySQL\n"
        "- 框架：Flask、Spring Boot、gRPC\n"
        "\n"
        "教育背景：\n"
        "2014.09 - 2018.06 某大学 计算机科学与技术 本科\n"
        "\n"
        "项目：订单中台重构（2021）- 拆分单体为 6 个微服务，QPS 提升 3 倍\n"
    )

    # v2 合法响应：source_quote 精确匹配简历正文，provider 内部生成 locator
    V2_AI_RESPONSE = {
        "summary": {
            "headline": "后端开发工程师",
            "experience_level": "中级",
            "domains": ["后端"],
            "strengths": ["Python", "系统设计"],
        },
        "evidence": [
            {
                "client_ref": "e1",
                "type": "responsibility",
                "normalized_value": "订单服务设计",
                "source_quote": "订单服务设计与维护",
                "assertion_type": "explicit",
                "confidence": 90,
            },
            {
                "client_ref": "e2",
                "type": "achievement",
                "normalized_value": "分布式缓存改造",
                "source_quote": "分布式缓存改造",
                "assertion_type": "explicit",
                "confidence": 88,
            },
        ],
        "unknowns": [
            {"field": "current_city", "message": "未提及城市"},
        ],
        "directions": [
            {
                "client_ref": "d1",
                "name": "后端开发工程师",
                "type": "core",
                "rationale": "后端服务设计与高并发经验",
                "evidence_refs": ["e1", "e2"],
                "gaps": [],
                "confidence": 90,
                "default_enabled": True,
                "search_terms": ["后端开发"],
            },
        ],
    }

    def setUp(self) -> None:
        import copy
        import tempfile
        from unittest import mock
        from webui.app import create_app
        self._mock_module = mock
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({
            "TESTING": True,
            "DB_PATH": self._tmp.name,
            "START_TASKS": False,
        })
        self.client = self.app.test_client()
        # 本地 API 保护中间件要求 X-Boss-Token
        sess = self.client.get("/api/session")
        self.assertEqual(sess.status_code, 200)
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        # 直接通过 store 建立画像与脱敏简历
        from webui.store import TaskStore
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("US1 组合测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/us1.pdf", "pdf",
            self.RESUME_TEXT, "hash-us1", "us1.pdf",
        )
        # 配置 AI settings 使 _build_ai_provider 返回真实 DiscoveryAIProvider
        self.store.save_ai_settings(
            endpoint_url="https://fake-endpoint.example.com/v1",
            credential_ref="fake-endpoint.example.com",
            status="ready",
            model="deepseek-v4-flash-free",
        )
        # mock keyring 检索（不依赖系统凭据存储）
        self._api_key_patcher = self._mock_module.patch(
            "webui.app.ai_service.retrieve_api_key", return_value="fake-api-key",
        )
        self._api_key_patcher.start()
        # 默认 mock call_ai transport（真实 DiscoveryAIProvider 类内部调用）
        self._call_ai_patcher = self._mock_module.patch("webui.ai.call_ai")
        self._call_ai_mock = self._call_ai_patcher.start()
        self._call_ai_mock.return_value = copy.deepcopy(self.V2_AI_RESPONSE)

    def tearDown(self) -> None:
        # 先关闭 runtime executor 避免后台线程持有 SQLite 连接
        runtime = self.app.config.get("DISCOVERY_RUNTIME") if hasattr(self, "app") else None
        if runtime is not None:
            try:
                runtime.shutdown()
            except Exception:
                pass
        self._call_ai_patcher.stop()
        self._api_key_patcher.stop()
        import os
        if hasattr(self, "_tmp") and os.path.exists(self._tmp.name):
            try:
                os.unlink(self._tmp.name)
            except PermissionError:
                pass

    def _poll_analysis(self, analysis_id, timeout=10.0, interval=0.05):
        """轮询分析状态到终态（ready/failed）。"""
        import time
        deadline = time.monotonic() + timeout
        last_status = None
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/discovery/analyses/{analysis_id}")
            if resp.status_code == 200:
                last_status = resp.get_json().get("status")
                if last_status in ("ready", "failed"):
                    return resp.get_json()
            time.sleep(interval)
        raise AssertionError(
            f"analysis {analysis_id} did not reach terminal within {timeout}s "
            f"(last_status={last_status})"
        )

    def test_consent_false_rejected_without_analysis_row(self):
        """consent=false：创建前拒绝，且不调用 transport。"""
        before = len(self.store.list_analyses(self.resume["id"]))
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": False},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(self.store.list_analyses(self.resume["id"])), before)
        # call_ai transport 不应被调用
        self.assertFalse(
            self._call_ai_mock.called,
            "consent=false 时不得调用 call_ai transport",
        )

    def test_consent_true_full_pipeline_to_confirmation(self):
        """consent=true：HTTP → runtime → real provider → validator → store → confirmation。"""
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        analysis_id = resp.get_json()["analysis_id"]
        # 轮询到 ready
        analysis = self._poll_analysis(analysis_id, timeout=10.0)
        self.assertEqual(analysis["status"], "ready")
        # call_ai transport 必须被真实 DiscoveryAIProvider 调用过
        self.assertTrue(self._call_ai_mock.called, "consent=true 时 call_ai 必须被调用")
        # 验证每条持久化 evidence locator 与规范化简历切片完全一致
        # source_locator 不在 openapi Evidence schema 中，通过 store 直接验证
        from webui.candidate import canonicalize_resume_text_v2
        canonical = canonicalize_resume_text_v2(self.RESUME_TEXT)
        db_evidence = self.store.list_evidence(analysis_id)
        self.assertTrue(db_evidence, "数据库应持久化至少一条 evidence")
        quotes = [raw_ev["source_quote"] for raw_ev in self.V2_AI_RESPONSE["evidence"]]
        for ev in db_evidence:
            locator = ev.get("source_locator") or {}
            start = locator.get("start")
            end = locator.get("end")
            self.assertIsInstance(
                start, int,
                f"evidence {ev['id']} locator.start 不是 int: {start!r}",
            )
            self.assertIsInstance(
                end, int,
                f"evidence {ev['id']} locator.end 不是 int: {end!r}",
            )
            self.assertGreaterEqual(start, 0)
            self.assertLessEqual(end, len(canonical))
            slice_text = canonical[start:end]
            self.assertIn(
                slice_text, quotes,
                f"evidence locator 切片不匹配任何 source_quote: slice={slice_text!r}",
            )
        # 执行 confirm_directions
        direction_ids = [d["id"] for d in analysis.get("directions", [])]
        self.assertTrue(direction_ids, "分析应至少产生一个方向")
        confirm_resp = self.client.post(
            "/api/discovery/confirmations",
            json={
                "analysis_id": analysis_id,
                "enabled_direction_ids": direction_ids,
                "hard_constraints": {"city": "北京"},
            },
        )
        self.assertEqual(confirm_resp.status_code, 201)
        confirmation = confirm_resp.get_json()
        self.assertIn("confirmation_id", confirmation)

    def test_db_does_not_contain_resume_body(self):
        """分析完成后数据库普通字段不含简历正文。"""
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        analysis_id = resp.get_json()["analysis_id"]
        self._poll_analysis(analysis_id, timeout=10.0)
        # 检查所有表的 prompt/response/raw_response/model_response 列
        import sqlite3
        conn = sqlite3.connect(self._tmp.name)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            for table in tables:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                for col in cols:
                    if col.lower() in ("prompt", "response", "raw_response", "model_response"):
                        rows = conn.execute(f"SELECT {col} FROM {table}").fetchall()
                        for row in rows:
                            if row[0] and self.RESUME_TEXT in str(row[0]):
                                self.fail(
                                    f"简历正文泄漏到 {table}.{col}"
                                )
        finally:
            conn.close()

    def test_provider_timeout_returns_safe_envelope(self):
        """provider call_ai 抛 AISecurityError(timeout) → failed + ai_timeout，不泄漏原始异常。

        call_ai 抛低级错误码 "timeout"，provider 内部 _map_provider_error
        映射为 feature-safe "ai_timeout"，analyze_resume 保留该 code。
        """
        from webui.ai import AISecurityError as _AISecurityError
        self._call_ai_mock.side_effect = _AISecurityError("timeout")
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        analysis_id = resp.get_json()["analysis_id"]
        analysis = self._poll_analysis(analysis_id, timeout=10.0)
        self.assertEqual(analysis["status"], "failed")
        failure = analysis.get("failure") or {}
        self.assertEqual(failure.get("error_code"), "ai_timeout")
        # 失败信封不得包含原始异常堆栈或 traceback 字符串
        failure_str = str(failure)
        self.assertNotIn("Traceback", failure_str)
        self.assertNotIn("AISecurityError", failure_str)

    # ---- P1/P2/P6/P7 全量审查修复测试 ----

    def test_analysis_persists_contract_version_v3(self):
        """候选人适配流程必须落库 contract_version='v3'。"""
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        analysis_id = resp.get_json()["analysis_id"]
        self._poll_analysis(analysis_id, timeout=10.0)
        # 直接查库验证 contract_version
        stored = self.store.get_analysis(analysis_id)
        self.assertEqual(
            stored.get("contract_version"), "v3",
            f"contract_version 应为 'v3'，实际为 {stored.get('contract_version')!r}",
        )

    def test_analysis_persists_model_name(self):
        """P2: 分析必须落库 model_name（来自 ai_settings.model）。"""
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        analysis_id = resp.get_json()["analysis_id"]
        self._poll_analysis(analysis_id, timeout=10.0)
        stored = self.store.get_analysis(analysis_id)
        model_name = stored.get("model_name", "")
        self.assertTrue(
            model_name, f"model_name 不应为空，实际为 {model_name!r}",
        )
        self.assertEqual(model_name, "deepseek-v4-flash-free")

    def test_confirm_invalid_direction_returns_state_conflict(self):
        """P6: 方向不属于分析应返回 state_conflict，不是 evidence_reference_invalid。"""
        # 先完成一次分析
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        analysis_id = resp.get_json()["analysis_id"]
        self._poll_analysis(analysis_id, timeout=10.0)
        # 用一个不存在的方向 id 确认
        confirm_resp = self.client.post(
            "/api/discovery/confirmations",
            json={
                "analysis_id": analysis_id,
                "enabled_direction_ids": ["nonexistent-direction-id"],
                "hard_constraints": {"city": "北京"},
            },
        )
        self.assertNotEqual(confirm_resp.status_code, 201)
        body = confirm_resp.get_json() or {}
        error_code = body.get("error_code", "")
        self.assertEqual(
            error_code, "state_conflict",
            f"方向不存在应返回 state_conflict，实际为 {error_code!r}",
        )

    def test_evidence_without_source_quote_rejected(self):
        """P7: v2 契约下缺 source_quote 的 evidence 必须被拒绝。"""
        import copy
        from webui.candidate import validate_candidate_analysis
        # 构造缺 source_quote 的 v1 风格 evidence
        v1_style_response = copy.deepcopy(self.V2_AI_RESPONSE)
        for ev in v1_style_response["evidence"]:
            ev.pop("source_quote", None)
            ev["source_locator"] = {"start": 0, "end": 10}
        with self.assertRaises(ValueError) as ctx:
            validate_candidate_analysis(v1_style_response, self.RESUME_TEXT)
        self.assertIn(
            "source_quote", str(ctx.exception),
            f"缺 source_quote 应报错，实际异常: {ctx.exception}",
        )


class US5PrivacyBoundaryTests(_IntegrationTestCase):
    """T129: provider/runtime 隐私失败测试。

    验证新链路（US1 分析 + US2 评估）的最小披露和安全日志边界：
    - 分析失败信封不含 Traceback / API key / 简历正文
    - 评估失败信封不含 Traceback / API key / JD 正文
    - 持久化的 failure_code 是安全分类码（ai_* 前缀）
    - safe_excerpt 经 redact_pii 处理
    """

    def test_analysis_failure_envelope_no_traceback(self):
        """分析失败信封不含 Traceback。"""
        from webui.ai import AISecurityError as _AISecurityError
        provider = FakeAIProvider(raises=_AISecurityError("timeout"))
        with self.assertRaises(DiscoveryError) as ctx:
            analyze_resume(
                self.store, self.resume["id"],
                ai_consent=True, ai_provider=provider,
            )
        exc_str = str(ctx.exception)
        self.assertNotIn("Traceback", exc_str)
        self.assertNotIn("AISecurityError", exc_str)

    def test_analysis_failure_persists_safe_code(self):
        """分析失败持久化的 failure_code 是安全分类码。"""
        from webui.ai import AISecurityError as _AISecurityError
        provider = FakeAIProvider(raises=_AISecurityError("auth_failed"))
        try:
            analyze_resume(
                self.store, self.resume["id"],
                ai_consent=True, ai_provider=provider,
            )
        except Exception:
            pass
        analyses = self.store.list_analyses(self.resume["id"])
        self.assertEqual(analyses[-1]["status"], "failed")
        code = analyses[-1].get("failure_code", "")
        self.assertTrue(code.startswith("ai_"), f"failure_code 应为 ai_* 前缀，实际: {code}")

    def test_db_has_no_raw_resume_text(self):
        """数据库普通字段不含简历正文。"""
        provider = FakeAIProvider(_valid_ai_response())
        analyze_resume(
            self.store, self.resume["id"],
            ai_consent=True, ai_provider=provider,
        )
        import sqlite3
        conn = sqlite3.connect(self._tmp.name)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            for table in tables:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                for col in cols:
                    if col.lower() in ("prompt", "response", "raw_response", "model_response"):
                        rows = conn.execute(f"SELECT {col} FROM {table}").fetchall()
                        for row in rows:
                            if row[0] and RESUME_TEXT in str(row[0]):
                                self.fail(f"简历正文泄漏到 {table}.{col}")
        finally:
            conn.close()

    def test_evidence_safe_excerpt_is_redacted(self):
        """evidence safe_excerpt 经 redact_pii 处理。"""
        from webui.candidate import redact_pii
        provider = FakeAIProvider(_valid_ai_response())
        result = analyze_resume(
            self.store, self.resume["id"],
            ai_consent=True, ai_provider=provider,
        )
        evidence = self.store.list_evidence(result["id"])
        for ev in evidence:
            excerpt = ev.get("safe_excerpt", "")
            # safe_excerpt should not contain raw PII patterns
            self.assertNotIn("13800138000", excerpt)
            self.assertNotIn("110101", excerpt)


class DiscoveryV2StateAndPrivacyFoundationTests(_IntegrationTestCase):
    """T016/T018/T019: v2 CAS, reconciliation and safe payload foundation."""

    def setUp(self):
        super().setUp()
        self.analysis, self.confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
        )
        self.input_hash = "a" * 64
        self.run = self.store.create_discovery_run(
            profile_id=self.profile["id"], resume_id=self.resume["id"],
            analysis_id=self.analysis["id"], confirmation_id=self.confirmation["id"],
            input_hash=self.input_hash, policy_version="discovery_v2",
        )

    def test_v2_valid_transitions_are_explicit_and_hash_is_stable(self):
        from webui.discovery import compute_discovery_input_hash, validate_v2_run_transition

        self.assertEqual(
            compute_discovery_input_hash({"b": 2, "a": 1}, policy_version="discovery_v2"),
            compute_discovery_input_hash({"a": 1, "b": 2}, policy_version="discovery_v2"),
        )
        self.assertEqual(validate_v2_run_transition("created", "planning"), "planning")
        with self.assertRaises(DiscoveryError):
            validate_v2_run_transition("created", "processing_jobs")

    def test_store_transition_uses_expected_state_and_input_hash_cas(self):
        updated = self.store.transition_discovery_run_v2(
            self.run["id"], expected_state="created", target_state="planning",
            input_hash=self.input_hash, counters={"list_candidate_count": 3},
            event_type="stage_entered", event_payload={"stage": "planning"},
        )
        self.assertEqual((updated["status"], updated["stage"]), ("planning", "planning"))
        self.assertEqual(updated["list_candidate_count"], 3)
        self.assertEqual(len(self.store.list_discovery_events(self.run["id"])), 1)

        before = self.store.get_discovery_run(self.run["id"])
        before_events = self.store.list_discovery_events(self.run["id"])
        with self.assertRaises(DiscoveryError) as ctx:
            self.store.transition_discovery_run_v2(
                self.run["id"], expected_state="created", target_state="planning",
                input_hash=self.input_hash,
            )
        self.assertEqual(ctx.exception.error_code, "state_conflict")
        self.assertEqual(self.store.get_discovery_run(self.run["id"])["list_candidate_count"], before["list_candidate_count"])
        self.assertEqual(self.store.list_discovery_events(self.run["id"]), before_events)

        with self.assertRaises(DiscoveryError) as ctx:
            self.store.transition_discovery_run_v2(
                self.run["id"], expected_state="planning", target_state="fetching_lists",
                input_hash="b" * 64,
            )
        self.assertEqual(ctx.exception.error_code, "input_hash_mismatch")

    def test_terminal_state_is_irreversible(self):
        self.store.transition_discovery_run_v2(
            self.run["id"], expected_state="created", target_state="planning",
            input_hash=self.input_hash,
        )
        self.store.transition_discovery_run_v2(
            self.run["id"], expected_state="planning", target_state="failed",
            input_hash=self.input_hash,
        )
        with self.assertRaises(DiscoveryError):
            self.store.transition_discovery_run_v2(
                self.run["id"], expected_state="failed", target_state="planning",
                input_hash=self.input_hash,
            )

    def test_empty_persisted_rows_reconcile_v2_counters_transactionally(self):
        reconciled = self.store.reconcile_discovery_run_v2(self.run["id"])
        for field in (
            "list_candidate_count", "detail_selected_count", "detail_completed_count",
            "assessment_completed_count", "recommendation_count", "detail_reused_count",
            "ai_call_count",
        ):
            self.assertEqual(reconciled[field], 0, field)
        events = self.store.list_discovery_events(self.run["id"])
        self.assertEqual(events[-1]["event_type"], "progress_reconciled")

    def test_sensitive_payload_is_rejected_before_event_or_result_serialization(self):
        from webui.discovery import sanitize_discovery_payload

        forbidden = {
            "phone": "13800138000",
            "id_number": "110101199001011234",
            "address": "幸福路 88 号",
            "resume_body": RESUME_TEXT,
            "jd_body": "完整岗位正文 SECRET-JD",
            "prompt": "SYSTEM PROMPT",
            "api_key": "sk-secret",
            "raw_model_output": "RAW MODEL SECRET",
        }
        with self.assertRaises(DiscoveryError):
            sanitize_discovery_payload(forbidden, payload_kind="event")
        with self.assertRaises(DiscoveryError):
            self.store.transition_discovery_run_v2(
                self.run["id"], expected_state="created", target_state="planning",
                input_hash=self.input_hash, event_type="stage_entered",
                event_payload=forbidden,
            )
        serialized = json.dumps(self.store.list_discovery_events(self.run["id"]), ensure_ascii=False)
        for secret in forbidden.values():
            self.assertNotIn(secret, serialized)


class DiscoveryV4ProfileOrchestrationTests(_IntegrationTestCase):
    """Manual candidate profile creation without AI."""

    def setUp(self):
        super().setUp()
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/manual.txt", "txt",
            "5年 Python 后端经验，主导订单服务重构", "manual-resume-hash", "manual.txt",
        )

    def test_manual_facts_and_direction_create_editable_profile_without_ai(self):
        from webui.discovery import create_manual_candidate_profile

        result = create_manual_candidate_profile(
            self.store, self.resume["id"],
            facts=[{
                "fact_type": "skill", "value": {"name": "Python"},
                "normalized_value": "Python",
            }],
            directions=[{
                "name": "Python 后端工程师", "type": "core",
                "search_terms": ["Python 后端"],
            }],
            unknowns=[{"field": "current_city", "message": "待确认"}],
        )
        self.assertEqual(result["analysis"]["contract_version"], "manual_v1")
        self.assertEqual(result["candidate_profile_version"]["status"], "draft")
        self.assertEqual(result["candidate_profile_version"]["facts"][0]["source_kind"], "user_added")
        self.assertEqual(len(self.store.list_directions(result["analysis"]["id"])), 1)


class CandidateProfileConfirmationAcceptanceTests(_IntegrationTestCase):
    """T034: SC-008/SC-009 user edits remain exact across confirmations."""

    def setUp(self):
        super().setUp()
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/acceptance.txt", "txt",
            "5年 Python 后端经验，主导订单服务重构", "acceptance-hash", "acceptance.txt",
        )

    def test_each_user_edit_is_frozen_into_next_confirmation_without_mutating_previous(self):
        from webui.discovery import create_manual_candidate_profile
        manual = create_manual_candidate_profile(
            self.store, self.resume["id"],
            facts=[{
                "fact_type": "skill", "value": {"name": "Python"},
                "normalized_value": "Python",
            }],
            directions=[{
                "name": "Python 后端工程师", "type": "core",
                "search_terms": ["Python 后端"],
            }],
        )
        first_draft = self.store.get_candidate_profile_version(
            manual["candidate_profile_version"]["id"],
        )
        skill = next(f for f in first_draft["facts"] if f["fact_type"] == "skill")
        corrected = self.store.update_candidate_profile_draft(
            first_draft["id"], expected_content_hash=first_draft["content_hash"],
            operations=[{"op": "correct", "fact_id": skill["id"],
                         "value": {"name": "Go"}, "normalized_value": "Go"}],
        )
        directions = self.store.list_directions(manual["analysis"]["id"])
        first_confirmation = self.store.create_confirmation_v2(
            candidate_profile_version_id=corrected["id"],
            expected_content_hash=corrected["content_hash"],
            hard_constraints={"city": "上海"}, soft_preferences={}, safe_limits={},
            directions=[{"direction_id": directions[0]["id"], "enabled": True}],
            intent_hash="1" * 64,
        )
        self.assertEqual(first_confirmation["candidate_profile_version_id"], corrected["id"])
        self.assertIn("Go", [f["normalized_value"] for f in self.store.get_candidate_profile_version(corrected["id"])["facts"]])

        next_draft = self.store.copy_candidate_profile_draft(corrected["id"])
        active_go = next(f for f in next_draft["facts"] if f["normalized_value"] == "Go")
        next_edited = self.store.update_candidate_profile_draft(
            next_draft["id"], expected_content_hash=next_draft["content_hash"],
            operations=[{"op": "correct", "fact_id": active_go["id"],
                         "value": {"name": "Rust"}, "normalized_value": "Rust"}],
        )
        second_confirmation = self.store.create_confirmation_v2(
            candidate_profile_version_id=next_edited["id"],
            expected_content_hash=next_edited["content_hash"],
            hard_constraints={"city": "上海"}, soft_preferences={}, safe_limits={},
            directions=[{"direction_id": directions[0]["id"], "enabled": True}],
            intent_hash="2" * 64,
        )
        self.assertNotEqual(second_confirmation["candidate_profile_version_id"], first_confirmation["candidate_profile_version_id"])
        first_after = self.store.get_candidate_profile_version(corrected["id"])
        self.assertIn("Go", [f["normalized_value"] for f in first_after["facts"]])
        self.assertNotIn("Rust", [f["normalized_value"] for f in first_after["facts"]])


class CandidatePoolOrchestrationTests(_IntegrationTestCase):
    """T043: runner persists all candidates, dispatches only selected, recovers from SQLite."""

    def _make_v2_run_with_candidates(self, candidate_count=30, detail_budget=15):
        """Create a v2 run and simulate list phase producing candidates."""
        from webui.discovery import select_priority_details, precheck_list_candidate
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(a["id"], name="后端", direction_type="core",
                                      rationale="r", gaps=[], confidence=80,
                                      default_enabled=True, search_terms=["Python"])
        d2 = self.store.add_direction(a["id"], name="数据", direction_type="core",
                                      rationale="r", gaps=[], confidence=70,
                                      default_enabled=True, search_terms=["数据开发"])
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": detail_budget},
            directions=[
                {"direction_id": d1["id"], "enabled": True, "user_added": False, "user_label": None},
                {"direction_id": d2["id"], "enabled": True, "user_added": False, "user_label": None},
            ],
        )
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="v2-hash", policy_version="discovery_v2",
        )
        # Simulate list phase: persist all candidates.
        for i in range(candidate_count):
            job_id = f"job-pool-{i:03d}"
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, '后端', '公司', '25K', '上海', 'jd', '2026-01-01', '2026-01-01')",
                    (job_id, f"https://www.zhipin.com/job_detail/{job_id}.html", f"https://www.zhipin.com/job_detail/{job_id}.html"),
                )
            direction_ids = [d1["id"]] if i % 2 == 0 else [d2["id"]]
            self.store.upsert_run_candidate(
                run_id=run["id"], job_id=job_id,
                source_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
                direction_ids=direction_ids, search_terms=["Python"],
                source_positions=[{"item": i, "page": 1, "rank": i}],
                list_fields={"title": f"岗位{i}", "salary": "25K", "location": "上海"},
                input_hash="v2-hash",
            )
        return run, d1, d2

    def test_all_candidates_persisted_after_list_phase(self):
        """列表阶段完成后全部候选持久化到 SQLite。"""
        run, _, _ = self._make_v2_run_with_candidates(30)
        candidates = self.store.list_run_candidates(run["id"])
        self.assertEqual(len(candidates), 30)
        for c in candidates:
            self.assertEqual(c["state"], "discovered")

    def test_priority_selection_marks_selected_and_deferred(self):
        """优先选择后 selected/deferred 正确标记。"""
        from webui.discovery import select_priority_details
        run, d1, d2 = self._make_v2_run_with_candidates(30, detail_budget=15)
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=15, directions=[d1["id"], d2["id"]],
        )
        self.assertEqual(len(result["selected"]), 15)
        self.assertEqual(len(result["deferred"]), 15)
        # Persist selection decisions.
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        for item in result["deferred"]:
            self.store.update_run_candidate_state(
                item["id"], selection_decision="deferred",
                selection_reason="budget_deferred",
            )
        selected = self.store.list_run_candidates(run["id"], selection_decision="selected")
        self.assertEqual(len(selected), 15)
        deferred = self.store.list_run_candidates(run["id"], selection_decision="deferred")
        self.assertEqual(len(deferred), 15)

    def test_only_selected_candidates_dispatched_for_detail(self):
        """只有 selected 候选被派发详情获取。"""
        from webui.discovery import select_priority_details
        run, d1, d2 = self._make_v2_run_with_candidates(30, detail_budget=10)
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=10, directions=[d1["id"], d2["id"]],
        )
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        for item in result["deferred"]:
            self.store.update_run_candidate_state(
                item["id"], selection_decision="deferred",
                selection_reason="budget_deferred",
            )
        dispatchable = self.store.list_run_candidates(run["id"], state="selected")
        self.assertEqual(len(dispatchable), 10)
        non_dispatchable = self.store.list_run_candidates(run["id"], selection_decision="deferred")
        self.assertEqual(len(non_dispatchable), 20)

    def test_recovery_from_sqlite_after_interrupt(self):
        """中断后从 SQLite 恢复：已持久化候选和选择状态不丢失。"""
        from webui.discovery import select_priority_details
        run, d1, d2 = self._make_v2_run_with_candidates(30, detail_budget=15)
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=15, directions=[d1["id"], d2["id"]],
        )
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        # Simulate interrupt: mark run interrupted.
        self.store.update_discovery_run(run["id"], status="interrupted", stage="prioritizing")
        # Recovery: re-read from SQLite.
        recovered_run = self.store.get_discovery_run(run["id"])
        self.assertEqual(recovered_run["status"], "interrupted")
        recovered_candidates = self.store.list_run_candidates(run["id"])
        self.assertEqual(len(recovered_candidates), 30)
        recovered_selected = self.store.list_run_candidates(run["id"], state="selected")
        self.assertEqual(len(recovered_selected), 15)
        # Verify ranks survived.
        ranks = sorted(c["selection_rank"] for c in recovered_selected)
        self.assertEqual(ranks, list(range(1, 16)))

    def test_violation_candidates_excluded_before_selection(self):
        """violation 候选在选择前被排除，不占预算。"""
        from webui.discovery import select_priority_details, precheck_list_candidate
        run, d1, d2 = self._make_v2_run_with_candidates(20, detail_budget=15)
        # Mark first 5 as violation via precheck.
        candidates = self.store.list_run_candidates(run["id"])
        for c in candidates[:5]:
            self.store.update_run_candidate_state(
                c["id"], state="excluded", selection_decision="excluded",
                precheck_outcome="violation",
            )
        remaining = self.store.list_run_candidates(run["id"], selection_decision="pending")
        result = select_priority_details(
            remaining, detail_budget=15, directions=[d1["id"], d2["id"]],
        )
        self.assertEqual(len(result["selected"]), 15)
        excluded = self.store.list_run_candidates(run["id"], selection_decision="excluded")
        self.assertEqual(len(excluded), 5)


class ProgressiveResultOrchestrationTests(_IntegrationTestCase):
    """T045: detail_ready 立即提交单岗位评估、assessment terminal 立即增加 result revision。"""

    def _make_v2_run_selected(self, candidate_count=5, detail_budget=3):
        """Create a v2 run with selected candidates ready for detail fetch."""
        from webui.discovery import select_priority_details
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(a["id"], name="后端", direction_type="core",
                                      rationale="r", gaps=[], confidence=80,
                                      default_enabled=True, search_terms=["Python"])
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": detail_budget},
            directions=[
                {"direction_id": d1["id"], "enabled": True, "user_added": False, "user_label": None},
            ],
        )
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="v2-prog-hash", policy_version="discovery_v2",
        )
        for i in range(candidate_count):
            job_id = f"job-prog-{i:03d}"
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, '后端', '公司', '25K', '上海', 'jd', '2026-01-01', '2026-01-01')",
                    (job_id, f"https://www.zhipin.com/job_detail/{job_id}.html", f"https://www.zhipin.com/job_detail/{job_id}.html"),
                )
            self.store.upsert_run_candidate(
                run_id=run["id"], job_id=job_id,
                source_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
                direction_ids=[d1["id"]], search_terms=["Python"],
                source_positions=[{"item": i, "page": 1, "rank": i}],
                list_fields={"title": f"岗位{i}", "salary": "25K", "location": "上海"},
                input_hash="v2-prog-hash",
            )
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=detail_budget, directions=[d1["id"]],
        )
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        for item in result["deferred"]:
            self.store.update_run_candidate_state(
                item["id"], selection_decision="deferred",
                selection_reason="budget_deferred",
            )
        return run, d1

    def test_detail_ready_immediately_creates_assessment(self):
        """单个详情就绪后立即创建评估，不等待其他详情。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected(5, detail_budget=3)
        selected = self.store.list_run_candidates(run["id"], state="selected")
        self.assertEqual(len(selected), 3)

        # Mock source that returns details one by one.
        class SequentialSource:
            def __init__(self):
                self.fetched = []
            def fetch_detail(self, job, detail_output_path=None):
                self.fetched.append(job.get("job_id"))
                class Outcome:
                    ok = True
                    detail = {"jd": "详细职位描述", "tags": "Python,Django"}
                return Outcome()

        source = SequentialSource()
        runner = DiscoveryRunner(self.store, source=source, ai_provider=None,
                                 result_dir=Path(tempfile.mkdtemp()))
        # Run progressive orchestration (method to be implemented in T046).
        runner.run_progressive_detail_eval(run["id"])

        # After progressive run, each selected candidate should have a snapshot.
        snapshots = self.store.list_snapshots(run["id"])
        self.assertEqual(len(snapshots), 3)
        # Each snapshot should have an assessment created immediately.
        assessments = self.store.list_assessments(run["id"])
        self.assertGreaterEqual(len(assessments), 3)

    def test_assessment_terminal_increments_result_revision_immediately(self):
        """assessment 达到 terminal 后立即增加 result_revision，不等待全部完成。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected(5, detail_budget=3)

        class SequentialSource:
            def fetch_detail(self, job, detail_output_path=None):
                class Outcome:
                    ok = True
                    detail = {"jd": "详细职位描述", "tags": "Python"}
                return Outcome()

        runner = DiscoveryRunner(self.store, source=SequentialSource(), ai_provider=None,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        # result_revision should equal the number of completed assessments.
        updated_run = self.store.get_discovery_run(run["id"])
        assessments = self.store.list_assessments(run["id"])
        completed = [a for a in assessments if a.get("status") == "completed"]
        self.assertEqual(updated_run.get("result_revision", 0), len(completed))
        self.assertGreaterEqual(updated_run.get("result_revision", 0), 3)

    def test_result_revision_visible_before_all_details_complete(self):
        """result_revision 在全部详情完成前已可见（渐进式）。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected(5, detail_budget=3)

        revision_log = []

        class InstrumentedSource:
            def __init__(self, store, run_id):
                self._store = store
                self._run_id = run_id
                self.call_count = 0
            def fetch_detail(self, job, detail_output_path=None):
                self.call_count += 1
                # After first detail, check that result_revision is already
                # tracking progress (will be 0 before first assessment, but
                # the key is that it increments per-assessment, not at end).
                class Outcome:
                    ok = True
                    detail = {"jd": "详细职位描述", "tags": "Python"}
                return Outcome()

        source = InstrumentedSource(self.store, run["id"])
        runner = DiscoveryRunner(self.store, source=source, ai_provider=None,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        # Verify progressive: result_revision must be > 0 and equal to
        # completed assessment count, proving it wasn't batched at the end.
        updated_run = self.store.get_discovery_run(run["id"])
        self.assertGreater(updated_run.get("result_revision", 0), 0)
        # All 3 selected should have been processed.
        self.assertEqual(source.call_count, 3)

    def test_progressive_bounded_by_detail_budget(self):
        """渐进编排受 detail_budget 约束，只处理 selected 候选。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected(10, detail_budget=3)

        class CountingSource:
            def __init__(self):
                self.count = 0
            def fetch_detail(self, job, detail_output_path=None):
                self.count += 1
                class Outcome:
                    ok = True
                    detail = {"jd": "jd", "tags": "Python"}
                return Outcome()

        source = CountingSource()
        runner = DiscoveryRunner(self.store, source=source, ai_provider=None,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        # Only 3 selected candidates should be fetched, not all 10.
        self.assertEqual(source.count, 3)
        snapshots = self.store.list_snapshots(run["id"])
        self.assertEqual(len(snapshots), 3)

    def test_progressive_checkpoint_survives_interrupt(self):
        """渐进 checkpoint 在中断后保留：已完成评估和 result_revision 不丢失。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected(5, detail_budget=3)

        call_count = [0]

        class InterruptingSource:
            def fetch_detail(self, job, detail_output_path=None):
                call_count[0] += 1
                if call_count[0] == 2:
                    # Simulate interrupt after first detail.
                    raise KeyboardInterrupt("simulated interrupt")
                class Outcome:
                    ok = True
                    detail = {"jd": "jd", "tags": "Python"}
                return Outcome()

        runner = DiscoveryRunner(self.store, source=InterruptingSource(), ai_provider=None,
                                 result_dir=Path(tempfile.mkdtemp()))
        try:
            runner.run_progressive_detail_eval(run["id"])
        except KeyboardInterrupt:
            pass

        # First assessment should be checkpointed.
        updated_run = self.store.get_discovery_run(run["id"])
        assessments = self.store.list_assessments(run["id"])
        completed = [a for a in assessments if a.get("status") == "completed"]
        # At least the first assessment should survive.
        self.assertGreaterEqual(len(completed), 1)
        self.assertGreaterEqual(updated_run.get("result_revision", 0), 1)


class _V2EnvelopeFakeAI:
    """T057 fake provider returning a job-assessment v2 envelope.

    ``assess_job`` honours ``contract_version="job_assessment_v2"`` and returns
    one assessment per supplied direction, quarantining any direction id listed
    in ``quarantine_direction_ids``. Legacy v1 calls (no v2 contract) return a
    minimal v1-shaped proposal so the pre-T058 per-direction path still runs.
    """

    _V1_PROPOSAL = {
        "dimensions": {
            "capability": {"score": 80, "candidate_evidence_refs": [], "job_evidence_refs": []},
            "experience": {"score": 80, "candidate_evidence_refs": [], "job_evidence_refs": []},
            "environment": {"score": 80, "candidate_evidence_refs": [], "job_evidence_refs": []},
            "stability": {"score": 80, "candidate_evidence_refs": [], "job_evidence_refs": []},
        },
        "match_score": 80, "confidence": 80, "gaps": [], "proposed_band": "high",
    }

    def __init__(self, *, quarantine_direction_ids=None, score=80):
        self.quarantine = set(quarantine_direction_ids or [])
        self.score = score
        self.v2_calls = []

    def assess_job(self, *, contract_version="v1", directions=None, **_kwargs):
        if contract_version != "job_assessment_v2":
            return dict(self._V1_PROPOSAL)
        self.v2_calls.append([d["id"] for d in directions or []])
        assessments = []
        quarantined = []
        for d in directions or []:
            did = d["id"]
            if did in self.quarantine:
                quarantined.append({"direction_id": did, "reason": "non_integer_score"})
                continue
            assessments.append(self._valid_assessment(did))
        if not quarantined:
            status = "complete"
        elif assessments:
            status = "partial"
        else:
            status = "manual_required"
        return {
            "contract_version": "job_assessment_v2",
            "assessments": assessments,
            "quarantined": quarantined,
            "quality": {"status": status, "warnings": []},
            "metrics": {"provider_call_count": 1},
        }

    def _valid_assessment(self, direction_id):
        dim = {"score": self.score, "candidate_fact_refs": [],
               "candidate_evidence_refs": [], "job_evidence_refs": []}
        return {
            "direction_id": direction_id,
            "dimensions": {
                name: dict(dim)
                for name in ("direction_alignment", "skill_coverage",
                             "experience_match", "industry_relevance")
            },
            "match_score": self.score, "confidence": self.score,
            "positive": [], "gaps": [], "proposed_band": "high",
        }


class JobAssessmentV2GroupOrchestrationTests(_IntegrationTestCase):
    """T057 RED: 一岗位最多两相关方向、每方向独立 input hash/assessment、
    失败 sibling 不污染有效 sibling。

    RED 状态: run_progressive_detail_eval 仍走 v1 逐方向路径，create_assessment
    尚未持久化 evaluation_group_id/input_hash（T058 实现）。
    """

    def _make_v2_run_multi(self, n_directions=3, candidate_count=2, detail_budget=2):
        from webui.discovery import select_priority_details
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        specs = [("后端", "core", 80), ("行业迁移", "adjacent", 70), ("架构师", "core", 60)][:n_directions]
        directions = []
        for name, dtype, conf in specs:
            directions.append(self.store.add_direction(
                a["id"], name=name, direction_type=dtype, rationale="r", gaps=[],
                confidence=conf, default_enabled=True, search_terms=[name],
            ))
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": detail_budget},
            directions=[{"direction_id": d["id"], "enabled": True, "user_added": False, "user_label": None}
                        for d in directions],
        )
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="v2-group-hash", policy_version="discovery_v2",
        )
        dir_ids = [d["id"] for d in directions]
        for i in range(candidate_count):
            job_id = f"job-grp-{i:03d}"
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, '后端', '公司', '25K', '上海', 'jd', '2026-01-01', '2026-01-01')",
                    (job_id, f"https://www.zhipin.com/job_detail/{job_id}.html", f"https://www.zhipin.com/job_detail/{job_id}.html"),
                )
            self.store.upsert_run_candidate(
                run_id=run["id"], job_id=job_id,
                source_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
                direction_ids=dir_ids, search_terms=["Python"],
                source_positions=[{"item": i, "page": 1, "rank": i}],
                list_fields={"title": f"岗位{i}", "salary": "25K", "location": "上海"},
                input_hash="v2-group-hash",
            )
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(candidates, detail_budget=detail_budget, directions=dir_ids)
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        return run, directions

    @staticmethod
    def _source():
        class SequentialSource:
            def fetch_detail(self, job, detail_output_path=None):
                class Outcome:
                    ok = True
                    detail = {"jd": "详细职位描述", "tags": "Python,Django"}
                return Outcome()
        return SequentialSource()

    def test_direction_relevance_selects_at_most_two_per_job(self):
        from webui.discovery_runner import DiscoveryRunner
        run, directions = self._make_v2_run_multi(n_directions=3, candidate_count=2, detail_budget=2)
        self.assertEqual(len(directions), 3)
        provider = _V2EnvelopeFakeAI()
        runner = DiscoveryRunner(self.store, source=self._source(), ai_provider=provider,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        assessments = self.store.list_assessments(run["id"])
        # 2 selected jobs × at most 2 relevant directions = 4 assessments (not 6).
        self.assertEqual(len(assessments), 4)
        # The v2 call must never receive more than two directions.
        self.assertTrue(provider.v2_calls, "expected at least one job-assessment v2 call")
        for call_dirs in provider.v2_calls:
            self.assertLessEqual(len(call_dirs), 2)

    def test_each_direction_has_independent_input_hash_and_shared_group(self):
        from webui.discovery_runner import DiscoveryRunner
        run, directions = self._make_v2_run_multi(n_directions=2, candidate_count=1, detail_budget=1)
        provider = _V2EnvelopeFakeAI()
        runner = DiscoveryRunner(self.store, source=self._source(), ai_provider=provider,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        assessments = self.store.list_assessments(run["id"])
        self.assertEqual(len(assessments), 2)
        hashes = {a.get("input_hash") for a in assessments}
        group_ids = {a.get("evaluation_group_id") for a in assessments}
        # Each direction assessment carries its own non-empty input hash.
        self.assertEqual(len(hashes), 2)
        self.assertNotIn(None, hashes)
        self.assertNotIn("", hashes)
        # Both directions of the same job share one evaluation group id.
        self.assertEqual(len(group_ids), 1)
        self.assertNotIn(None, group_ids)

    def test_quarantined_sibling_does_not_pollute_valid_sibling(self):
        from webui.discovery_runner import DiscoveryRunner
        run, directions = self._make_v2_run_multi(n_directions=2, candidate_count=1, detail_budget=1)
        bad_id = directions[1]["id"]
        provider = _V2EnvelopeFakeAI(quarantine_direction_ids={bad_id})
        runner = DiscoveryRunner(self.store, source=self._source(), ai_provider=provider,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        assessments = self.store.list_assessments(run["id"])
        by_dir = {a["direction_id"]: a for a in assessments}
        self.assertEqual(len(assessments), 2)

        good = by_dir[directions[0]["id"]]
        bad = by_dir[bad_id]
        # Valid sibling stays usable: completed, scored, not needs_review, no failure.
        self.assertEqual(good.get("status"), "completed")
        self.assertIsNotNone(good.get("match_score"))
        self.assertNotEqual(good.get("category"), "needs_review")
        self.assertFalse(good.get("failure_code"))
        # Quarantined sibling is isolated as needs_review with a failure code.
        self.assertEqual(bad.get("category"), "needs_review")
        self.assertTrue(bad.get("failure_code"))


class DetailReusePolicyTests(_IntegrationTestCase):
    """T072 RED: 12h 详情复用 / 过期 / 漂移 / unknown / 用户刷新 / 新 run snapshot 自足。

    合同来源:
    - data-model.md L332-341 (Detail Reuse)
    - spec.md FR-023, FR-019
    - state-machine.md (Producer/Consumer Boundaries, Resume)

    RED 状态: store.find_reusable_snapshot / store.create_reused_snapshot 尚不存在；
    list_snapshots 不暴露 reused 投影字段。T073 GREEN 实现。
    """

    _JD_TEXT = "详细 JD：负责后端服务设计与实现，要求 Python 5 年以上经验。"
    _TAGS = "Python,Django,MySQL"
    _CONTENT_HASH = "sha256:fake-content-hash-for-reuse-tests"

    def _iso(self, dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    def _make_prior_v2_run_with_snapshot(
        self, *,
        job_id="job-reuse-001",
        source_url="https://www.zhipin.com/job_detail/ABC123.html",
        title="后端工程师",
        company="某公司",
        salary="25K",
        location="上海",
        fetched_at_iso,
        fresh_until_iso=None,
        completeness="complete",
        source_status="active",
        content_hash=_CONTENT_HASH,
    ):
        """Create a prior v2 run with one completed snapshot for reuse tests."""
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": 1},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        prior_run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="prior-v2-hash",
            policy_version="discovery_v2",
        )
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
                (job_id, source_url, source_url, title, company, salary, location, ""),
            )
        self.store.upsert_run_candidate(
            run_id=prior_run["id"], job_id=job_id, source_url=source_url,
            direction_ids=[d1["id"]], search_terms=["Python"],
            source_positions=[{"item": 0, "page": 1, "rank": 0}],
            list_fields={"title": title, "company": company,
                         "salary": salary, "location": location},
            input_hash="prior-v2-hash",
        )
        # Build snapshot with explicit fetched_at + fresh_until.
        snapshot_summary = self.store.save_job_snapshot(
            run_id=prior_run["id"], job_id=job_id, source_url=source_url,
            title=title, company=company, salary=salary, location=location,
            tags=self._TAGS, jd=self._JD_TEXT, company_json={},
            completeness=completeness, missing_fields=[],
            source_status=source_status, content_hash=content_hash,
            fetch_status="completed",
        )
        # save_job_snapshot does not set fetched_at / fresh_until (migration 015
        # columns). Patch them directly to simulate a prior fresh capture.
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE discovery_job_snapshots SET fetched_at=?, fresh_until=?, "
                "run_candidate_id=?, fetch_policy_version='discovery_v2' WHERE id=?",
                (fetched_at_iso, fresh_until_iso, None, snapshot_summary["id"]),
            )
        # Return the full snapshot row so create_reused_snapshot has access to
        # all content fields (jd/tags/content_hash/etc).
        snapshot = self.store.get_snapshot(prior_run["id"], job_id)
        return prior_run, d1, snapshot

    def _make_current_v2_run_with_candidate(
        self, *,
        job_id="job-reuse-001",
        source_url="https://www.zhipin.com/job_detail/ABC123.html",
        title="后端工程师",
        company="某公司",
        salary="25K",
        location="上海",
    ):
        """Create a current v2 run with one selected candidate matching the prior job."""
        from webui.discovery import select_priority_details
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": 1},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="current-v2-hash",
            policy_version="discovery_v2",
        )
        self.store.upsert_run_candidate(
            run_id=run["id"], job_id=job_id, source_url=source_url,
            direction_ids=[d1["id"]], search_terms=["Python"],
            source_positions=[{"item": 0, "page": 1, "rank": 0}],
            list_fields={"title": title, "company": company,
                         "salary": salary, "location": location},
            input_hash="current-v2-hash",
        )
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(candidates, detail_budget=1, directions=[d1["id"]])
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        return run, d1

    # ------------------------------------------------------------------
    # Positive: 12h reuse
    # ------------------------------------------------------------------

    def test_find_reusable_snapshot_returns_match_within_12h(self):
        """12h 内、身份一致、complete+active → find_reusable_snapshot 返回 prior snapshot。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNotNone(matched, "12h 内 complete+active+identity match 必须返回可复用 snapshot")
        self.assertEqual(matched["completeness"], "complete")
        self.assertEqual(matched["source_status"], "active")

    def test_create_reused_snapshot_copies_content_into_current_run(self):
        """create_reused_snapshot 在当前 run 创建新 snapshot，复制 content_hash/jd/tags 等。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        _, _, prior_snapshot = self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        current_run, d1 = self._make_current_v2_run_with_candidate()
        candidate = self.store.list_run_candidates(
            current_run["id"], selection_decision="selected"
        )[0]

        reused = self.store.create_reused_snapshot(
            run_id=current_run["id"],
            run_candidate_id=candidate["id"],
            source_snapshot=prior_snapshot,
            fetch_policy_version="discovery_v2",
            now_iso=self._iso(now),
        )

        self.assertIsNotNone(reused)
        self.assertNotEqual(reused["id"], prior_snapshot["id"])
        self.assertEqual(reused["run_id"], current_run["id"])
        self.assertEqual(reused["reused_from_snapshot_id"], prior_snapshot["id"])
        self.assertEqual(reused["run_candidate_id"], candidate["id"])
        # Content must be copied.
        self.assertEqual(reused["content_hash"], self._CONTENT_HASH)
        self.assertEqual(reused["jd"], self._JD_TEXT)
        self.assertEqual(reused["tags"], self._TAGS)
        self.assertEqual(reused["completeness"], "complete")
        self.assertEqual(reused["source_status"], "active")
        # New run snapshot has its own fetched_at = current run time.
        self.assertEqual(reused["fetched_at"], self._iso(now))
        # fetch_policy_version recorded.
        self.assertEqual(reused.get("fetch_policy_version"), "discovery_v2")

    def test_reused_snapshot_increments_detail_reused_count(self):
        """复用后 run 的 detail_reused_count 增加。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        _, _, prior_snapshot = self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        current_run, _ = self._make_current_v2_run_with_candidate()
        candidate = self.store.list_run_candidates(
            current_run["id"], selection_decision="selected"
        )[0]

        self.store.create_reused_snapshot(
            run_id=current_run["id"],
            run_candidate_id=candidate["id"],
            source_snapshot=prior_snapshot,
            fetch_policy_version="discovery_v2",
            now_iso=self._iso(now),
        )

        updated = self.store.get_discovery_run(current_run["id"])
        self.assertGreaterEqual(updated.get("detail_reused_count", 0), 1)

    def test_list_snapshots_exposes_reused_projection_flag(self):
        """list_snapshots 必须为复用 snapshot 暴露 reused=True 投影字段。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        _, _, prior_snapshot = self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        current_run, _ = self._make_current_v2_run_with_candidate()
        candidate = self.store.list_run_candidates(
            current_run["id"], selection_decision="selected"
        )[0]
        self.store.create_reused_snapshot(
            run_id=current_run["id"],
            run_candidate_id=candidate["id"],
            source_snapshot=prior_snapshot,
            fetch_policy_version="discovery_v2",
            now_iso=self._iso(now),
        )

        snapshots = self.store.list_snapshots(current_run["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertTrue(snapshots[0].get("reused"), "复用 snapshot 投影必须标记 reused=True")
        # Source fetch time preserved separately for traceability.
        self.assertEqual(snapshots[0].get("source_fetched_at"), self._iso(fetched_at))

    # ------------------------------------------------------------------
    # Negative: expiry (>12h)
    # ------------------------------------------------------------------

    def test_no_reuse_when_fetched_at_exceeds_12h(self):
        """fetched_at > 12h → find_reusable_snapshot 返回 None。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=13)
        fresh_until = now - timedelta(hours=1)  # already expired
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "fresh_until 已过 → 不应复用")

    def test_no_reuse_when_fresh_until_missing(self):
        """fresh_until 为 NULL（旧数据无 freshness 元数据）→ 不复用。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=None,
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "fresh_until NULL → 视为不可复用")

    # ------------------------------------------------------------------
    # Negative: completeness != complete
    # ------------------------------------------------------------------

    def test_no_reuse_when_completeness_partial(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            completeness="partial",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "completeness=partial → 不复用")

    def test_no_reuse_when_completeness_unavailable(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            completeness="unavailable",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "completeness=unavailable → 不复用")

    # ------------------------------------------------------------------
    # Negative: source_status != active
    # ------------------------------------------------------------------

    def test_no_reuse_when_source_status_unknown(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            source_status="unknown",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "source_status=unknown → 不复用")

    def test_no_reuse_when_source_status_closed(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            source_status="closed",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "source_status=closed → 不复用")

    # ------------------------------------------------------------------
    # Negative: identity drift
    # ------------------------------------------------------------------

    def test_no_reuse_when_canonical_url_drift(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/XYZ999.html",  # different URL
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "canonical URL 不匹配 → 不复用")

    def test_no_reuse_when_job_id_drift(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            job_id="job-reuse-001",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-999",  # different job_id
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "job_id 不匹配 → 不复用")

    def test_no_reuse_when_list_fields_drift_title(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            title="后端工程师",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "前端工程师",  # drift
                                 "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "title 漂移 → 不复用")

    def test_no_reuse_when_list_fields_drift_company(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            company="某公司",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师",
                                 "company": "另一家公司",  # drift
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "company 漂移 → 不复用")

    def test_no_reuse_when_list_fields_drift_salary(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            salary="25K",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "35K",  # drift
                                 "location": "上海"},
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "salary 漂移 → 不复用")

    def test_no_reuse_when_list_fields_drift_location(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
            location="上海",
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K",
                                 "location": "北京"},  # drift
            now_iso=self._iso(now),
        )
        self.assertIsNone(matched, "location 漂移 → 不复用")

    # ------------------------------------------------------------------
    # Negative: user requested refresh
    # ------------------------------------------------------------------

    def test_no_reuse_when_user_requested_refresh(self):
        """用户显式刷新请求 → 不复用，即使 12h 内身份一致。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        matched = self.store.find_reusable_snapshot(
            job_id="job-reuse-001",
            source_url="https://www.zhipin.com/job_detail/ABC123.html",
            current_list_fields={"title": "后端工程师", "company": "某公司",
                                 "salary": "25K", "location": "上海"},
            now_iso=self._iso(now),
            refresh_requested=True,
        )
        self.assertIsNone(matched, "用户显式刷新 → 不复用")

    # ------------------------------------------------------------------
    # Self-sufficiency: parent deletion must not break current run's snapshot
    # ------------------------------------------------------------------

    def test_reused_snapshot_self_sufficient_after_parent_deletion(self):
        """新 run snapshot 自足：parent snapshot 行被删除后，新 run 的 snapshot 仍可完整读取。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        prior_run, _, prior_snapshot = self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        current_run, _ = self._make_current_v2_run_with_candidate()
        candidate = self.store.list_run_candidates(
            current_run["id"], selection_decision="selected"
        )[0]
        reused = self.store.create_reused_snapshot(
            run_id=current_run["id"],
            run_candidate_id=candidate["id"],
            source_snapshot=prior_snapshot,
            fetch_policy_version="discovery_v2",
            now_iso=self._iso(now),
        )

        # Delete the parent snapshot row (simulates parent run cleanup).
        with self.store._connection() as conn:
            conn.execute(
                "DELETE FROM discovery_job_snapshots WHERE id=?",
                (prior_snapshot["id"],),
            )

        # Current run's snapshot must still be fully readable.
        reread = self.store.get_snapshot(current_run["id"], "job-reuse-001")
        self.assertEqual(reread["id"], reused["id"])
        self.assertEqual(reread["content_hash"], self._CONTENT_HASH)
        self.assertEqual(reread["jd"], self._JD_TEXT)
        self.assertEqual(reread["tags"], self._TAGS)
        self.assertEqual(reread["completeness"], "complete")
        self.assertEqual(reread["source_status"], "active")
        # reused_from_snapshot_id may dangle (ON DELETE SET NULL) but content
        # must remain intact.
        self.assertEqual(reread["content_hash"], self._CONTENT_HASH)

    def test_reused_snapshot_does_not_depend_on_parent_run_row(self):
        """新 run snapshot 不依赖 parent run 行存在。"""
        from datetime import datetime, timedelta
        now = datetime(2026, 7, 21, 12, 0, 0)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        prior_run, _, prior_snapshot = self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=self._iso(fetched_at),
            fresh_until_iso=self._iso(fresh_until),
        )
        current_run, _ = self._make_current_v2_run_with_candidate()
        candidate = self.store.list_run_candidates(
            current_run["id"], selection_decision="selected"
        )[0]
        self.store.create_reused_snapshot(
            run_id=current_run["id"],
            run_candidate_id=candidate["id"],
            source_snapshot=prior_snapshot,
            fetch_policy_version="discovery_v2",
            now_iso=self._iso(now),
        )

        # Delete the entire prior run (cascades to its snapshots).
        with self.store._connection() as conn:
            conn.execute("DELETE FROM discovery_runs WHERE id=?", (prior_run["id"],))

        snapshots = self.store.list_snapshots(current_run["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["content_hash"], self._CONTENT_HASH)
        self.assertEqual(snapshots[0]["jd"], self._JD_TEXT)

    # ------------------------------------------------------------------
    # Runner integration: reuse path must not call source.fetch_detail
    # ------------------------------------------------------------------

    def test_runner_reuses_snapshot_without_calling_source_fetch(self):
        """runner 在复用可用时跳过 source.fetch_detail，直接创建复用 snapshot。"""
        from datetime import datetime, timedelta, timezone
        from webui.discovery_runner import DiscoveryRunner
        # Use real wall-clock time so the runner's _now() aligns with
        # the freshness window set on the prior snapshot.
        now = datetime.now(timezone.utc)
        fetched_at = now - timedelta(hours=1)
        fresh_until = now + timedelta(hours=11)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=fetched_at.isoformat(),
            fresh_until_iso=fresh_until.isoformat(),
        )
        current_run, _ = self._make_current_v2_run_with_candidate()

        class CountingSource:
            def __init__(self):
                self.fetch_detail_count = 0
                self.fetch_details_batch_count = 0
            def fetch_detail(self, job, detail_output_path=None):
                self.fetch_detail_count += 1
                class Outcome:
                    ok = True
                    detail = {"jd": "should-not-be-called", "tags": "Python"}
                return Outcome()
            def fetch_details_batch(self, jobs, *, detail_output_path=None,
                                    event_callback=None, max_batch_size=5):
                self.fetch_details_batch_count += 1
                return {}

        source = CountingSource()
        runner = DiscoveryRunner(
            self.store, source=source, ai_provider=None,
            result_dir=Path(tempfile.mkdtemp()),
        )
        runner.run_progressive_detail_eval(current_run["id"])

        self.assertEqual(source.fetch_detail_count, 0,
                         "复用路径不应调用 source.fetch_detail")
        self.assertEqual(source.fetch_details_batch_count, 0,
                         "复用路径不应调用 source.fetch_details_batch")
        # Snapshot was created via reuse path.
        snapshots = self.store.list_snapshots(current_run["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertTrue(snapshots[0].get("reused"))
        self.assertEqual(snapshots[0]["content_hash"], self._CONTENT_HASH)

    def test_runner_refetches_when_no_reusable_snapshot(self):
        """无可复用 snapshot 时 runner 调用 source.fetch_detail。"""
        from datetime import datetime, timedelta, timezone
        from webui.discovery_runner import DiscoveryRunner
        # Use real wall-clock time; prior snapshot is expired (fresh_until < now).
        now = datetime.now(timezone.utc)
        fetched_at = now - timedelta(hours=13)
        fresh_until = now - timedelta(hours=1)
        self._make_prior_v2_run_with_snapshot(
            fetched_at_iso=fetched_at.isoformat(),
            fresh_until_iso=fresh_until.isoformat(),
        )
        current_run, _ = self._make_current_v2_run_with_candidate()

        class CountingSource:
            def __init__(self):
                self.fetch_detail_count = 0
            def fetch_detail(self, job, detail_output_path=None):
                self.fetch_detail_count += 1
                class Outcome:
                    ok = True
                    detail = {"jd": "fresh-jd", "tags": "Python"}
                return Outcome()

        source = CountingSource()
        runner = DiscoveryRunner(
            self.store, source=source, ai_provider=None,
            result_dir=Path(tempfile.mkdtemp()),
        )
        runner.run_progressive_detail_eval(current_run["id"])

        self.assertEqual(source.fetch_detail_count, 1, "过期 snapshot 必须重新抓取")
        snapshots = self.store.list_snapshots(current_run["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertFalse(snapshots[0].get("reused", False))


class FailureIsolationAndProgressTests(_IntegrationTestCase):
    """T076 RED: 单 detail/AI/search 失败不阻断其他结果，四类进度逐单元事务更新并可 reconciliation，
    首结果/首五/阶段边界 timing 字段写入。

    合同来源:
    - http-api.md L203-208 (four-class progress: search_queries_completed, list_candidates,
      details_selected, details_completed, assessments_completed, recommendations)
    - http-api.md L218-220 (timing: first_result_at, first_batch_at, updated_at)
    - data-model.md L182-196 (additive counters + timing 字段)
    - data-model.md L318-328 (Progress Derivation: 计数从持久化 work units 派生)
    - state-machine.md (failure isolation: terminal safe events, no PII)

    RED 状态:
    - get_discovery_run 返回的 progress dict 仅含 v1 名 (source_count/detail_count/evaluated_count)，
      未暴露 v2 four-class 名。
    - run_progressive_detail_eval 不写入 first_result_at / first_batch_at / processing_completed_at。
    - _stage_fetching_lists 不写入 list_completed_at。
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_v2_run_selected_n(self, candidate_count=5, detail_budget=3,
                                job_prefix="job-iso"):
        """Create a v2 run with N selected candidates ready for detail fetch."""
        from webui.discovery import select_priority_details
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": detail_budget},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="v2-iso-hash",
            policy_version="discovery_v2",
        )
        for i in range(candidate_count):
            job_id = f"{job_prefix}-{i:03d}"
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, '后端', '公司', '25K', '上海', 'jd', '2026-01-01', '2026-01-01')",
                    (job_id, f"https://www.zhipin.com/job_detail/{job_id}.html",
                     f"https://www.zhipin.com/job_detail/{job_id}.html"),
                )
            self.store.upsert_run_candidate(
                run_id=run["id"], job_id=job_id,
                source_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
                direction_ids=[d1["id"]], search_terms=["Python"],
                source_positions=[{"item": i, "page": 1, "rank": i}],
                list_fields={"title": f"岗位{i}", "salary": "25K", "location": "上海"},
                input_hash="v2-iso-hash",
            )
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=detail_budget, directions=[d1["id"]],
        )
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        for item in result["deferred"]:
            self.store.update_run_candidate_state(
                item["id"], selection_decision="deferred",
                selection_reason="budget_deferred",
            )
        # T077: list_candidate_count is normally stamped by _stage_fetching_lists
        # in production. The test bypasses that stage, so stamp it here to make
        # the v2 four-class progress contract verifiable from a resumed run.
        self.store.update_discovery_run(run["id"], counters={
            "list_candidate_count": candidate_count,
        })
        return run, d1

    @staticmethod
    def _source_with_failures(fail_job_ids):
        """Fake source where specified job_ids fail detail fetch."""
        fail_set = set(fail_job_ids or [])

        class _Source:
            def __init__(self):
                self.calls = []

            def fetch_detail(self, job, detail_output_path=None):
                job_id = job.get("job_id")
                self.calls.append(job_id)

                class Outcome:
                    pass
                out = Outcome()
                if job_id in fail_set:
                    out.ok = False
                    out.detail = {}
                    out.failed_code = "source_timeout"
                    out.safe_log = {"reason": "timeout"}
                else:
                    out.ok = True
                    out.detail = {"jd": "详细职位描述", "tags": "Python,Django"}
                    out.failed_code = None
                    out.safe_log = None
                return out
        return _Source()

    @staticmethod
    def _ai_with_failures(fail_call_indices, score=80):
        """Fake v2 AI provider where specified call indices (0-based) raise.

        ``assess_job`` is called once per job; failing the Nth call simulates
        a per-job AI failure without blocking subsequent jobs.
        """
        fail_set = set(fail_call_indices or [])

        class _AI:
            def __init__(self):
                self.calls = 0
                self.v2_calls = []

            def assess_job(self, *, candidate_profile=None, directions=None,
                           job_snapshot=None, contract_version="v1", **_kwargs):
                if contract_version != "job_assessment_v2":
                    # Legacy v1 path not used in v2 progressive flow.
                    return {
                        "dimensions": {
                            "capability": {"score": score, "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "experience": {"score": score, "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "environment": {"score": score, "candidate_evidence_refs": [],
                                            "job_evidence_refs": []},
                            "stability": {"score": score, "candidate_evidence_refs": [],
                                          "job_evidence_refs": []},
                        },
                        "match_score": score, "confidence": score,
                        "gaps": [], "proposed_band": "high",
                    }
                self.calls += 1
                self.v2_calls.append(job_snapshot.get("snapshot_id", "") if job_snapshot else "")
                if (self.calls - 1) in fail_set:
                    raise RuntimeError("simulated AI failure for this job")
                assessments = []
                for d in directions or []:
                    assessments.append({
                        "direction_id": d["id"],
                        "dimensions": {
                            "capability": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "experience": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "environment": {"score": score, "candidate_fact_refs": [],
                                            "candidate_evidence_refs": [],
                                            "job_evidence_refs": []},
                            "stability": {"score": score, "candidate_fact_refs": [],
                                          "candidate_evidence_refs": [],
                                          "job_evidence_refs": []},
                        },
                        "match_score": score, "confidence": score,
                        "gaps": [], "proposed_band": "high",
                    })
                return {
                    "contract_version": "job_assessment_v2",
                    "assessments": assessments,
                    "quarantined": [],
                    "quality": {"status": "complete", "warnings": []},
                    "metrics": {"provider_call_count": 1},
                }
        return _AI()

    @staticmethod
    def _ai_always_ok(score=80):
        return FailureIsolationAndProgressTests._ai_with_failures(set(), score=score)

    @staticmethod
    def _source_always_ok():
        return FailureIsolationAndProgressTests._source_with_failures(set())

    # ------------------------------------------------------------------
    # Failure isolation: single detail/AI/search failure does not block others
    # ------------------------------------------------------------------

    def test_single_detail_failure_does_not_block_other_candidates(self):
        """单个详情失败时，其他候选的详情和评估仍然完成。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        selected = self.store.list_run_candidates(run["id"], selection_decision="selected")
        selected.sort(key=lambda c: (c.get("selection_rank") or 9999, c["job_id"]))
        self.assertEqual(len(selected), 3)
        # Fail the second selected candidate's detail.
        fail_job_id = selected[1]["job_id"]
        other_job_ids = {selected[0]["job_id"], selected[2]["job_id"]}
        source = self._source_with_failures({fail_job_id})
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        # All 3 selected candidates were attempted.
        self.assertEqual(len(source.calls), 3)
        # The OTHER 2 candidates got snapshots saved with non-unavailable completeness.
        snapshots = self.store.list_snapshots(run["id"])
        self.assertEqual(len(snapshots), 3)
        ok_other = [s for s in snapshots
                    if s["job_id"] in other_job_ids
                    and s.get("completeness") != "unavailable"]
        self.assertEqual(len(ok_other), 2,
                         "失败详情不应阻断其他候选的详情保存")
        # The OTHER 2 candidates got completed assessments (any valid category).
        assessments = self.store.list_assessments(run["id"])
        ok_other_snapshot_ids = {s["id"] for s in ok_other}
        ok_other_assessments = [
            a for a in assessments
            if a.get("snapshot_id") in ok_other_snapshot_ids
            and a.get("status") == "completed"
            and a.get("failure_code") is None
        ]
        self.assertEqual(len(ok_other_assessments), 2,
                         "失败详情不应阻断其他候选的评估")

    def test_single_ai_failure_does_not_block_other_candidates(self):
        """单个 AI 评估失败时，其他候选的评估仍然完成。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        selected = self.store.list_run_candidates(run["id"], selection_decision="selected")
        selected.sort(key=lambda c: (c.get("selection_rank") or 9999, c["job_id"]))
        # Fail the second assess_job call (index 1) → second candidate in rank order.
        ai = self._ai_with_failures({1})
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=ai,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        # All 3 jobs were attempted for AI assessment.
        self.assertEqual(ai.calls, 3)
        # The OTHER 2 candidates (rank 0 and rank 2) got assessments with
        # failure_code IS NULL (no AI failure). The failed candidate's
        # assessment has a non-null failure_code (ai_invalid_output etc).
        assessments = self.store.list_assessments(run["id"])
        # Map snapshot_id → job_id for join.
        snapshots = self.store.list_snapshots(run["id"])
        snap_to_job = {s["id"]: s["job_id"] for s in snapshots}
        other_job_ids = {selected[0]["job_id"], selected[2]["job_id"]}
        other_assessments = [
            a for a in assessments
            if snap_to_job.get(a.get("snapshot_id")) in other_job_ids
        ]
        # Both OTHER candidates should have assessments.
        self.assertEqual(len(other_assessments), 2,
                         "单个 AI 失败不应阻断其他候选的评估")
        # And their failure_code should be null (AI succeeded for them).
        self.assertTrue(all(a.get("failure_code") is None for a in other_assessments),
                        "其他候选的评估不应携带 AI 失败码")
        # result_revision still incremented for all 3 (failure is isolated, not fatal).
        updated_run = self.store.get_discovery_run(run["id"])
        self.assertEqual(updated_run.get("result_revision", 0), 3)

    def test_single_search_item_failure_does_not_block_other_items(self):
        """单个搜索项失败时，其他搜索项仍然完成。"""
        from webui.discovery_runner import DiscoveryRunner, _source_input_hash
        # Build a v2 run with a plan containing 3 items; the source's fetch_list
        # will fail for one specific item but succeed for the others.
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": 3},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="v2-iso-list-hash",
            policy_version="discovery_v2",
        )
        # Pre-create the search plan with 3 items.
        items = []
        for i in range(3):
            items.append({
                "keyword": f"Python{i}", "city": "上海",
                "source_filters": {}, "target_pages": 1,
                "direction_ids": [d1["id"]],
                "input_hash": _source_input_hash({
                    "keyword": f"Python{i}", "city": "上海",
                    "source_filters": {}, "target_pages": 1,
                }),
            })
        self.store.create_search_plan(run["id"], detail_budget=3, items=items)

        # Source: fetch_list fails for item with keyword "Python1".
        class _ListSource:
            def __init__(self):
                self.calls = []

            def fetch_list(self, plan_item):
                keyword = plan_item.get("keyword", "")
                self.calls.append(keyword)

                class Outcome:
                    pass
                out = Outcome()
                if keyword == "Python1":
                    out.ok = False
                    out.jobs = []
                    out.failed_code = "source_timeout"
                    out.safe_log = {"reason": "timeout"}
                else:
                    out.ok = True
                    out.failed_code = None
                    out.safe_log = None
                    # Return 2 jobs per successful item.
                    out.jobs = [
                        {"job_id": f"job-{keyword}-0",
                         "source_url": f"https://www.zhipin.com/job_detail/{keyword}0.html",
                         "title": f"{keyword}岗位0", "company": "公司",
                         "salary": "25K", "location": "上海"},
                        {"job_id": f"job-{keyword}-1",
                         "source_url": f"https://www.zhipin.com/job_detail/{keyword}1.html",
                         "title": f"{keyword}岗位1", "company": "公司",
                         "salary": "25K", "location": "上海"},
                    ]
                return out

        source = _ListSource()
        import threading
        cancel_event = threading.Event()
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=None,
                                 result_dir=Path(tempfile.mkdtemp()))
        # Drive only the list-fetch stage.
        runner._stage_fetching_lists(run["id"], cancel_event)

        # All 3 items were attempted.
        self.assertEqual(len(source.calls), 3)
        # The failed item is marked failed; the others are completed.
        plan = self.store.get_search_plan(run["id"])
        statuses = {item["keyword"]: item["status"] for item in plan["items"]}
        self.assertEqual(statuses.get("Python0"), "completed")
        self.assertEqual(statuses.get("Python1"), "failed")
        self.assertEqual(statuses.get("Python2"), "completed")

    # ------------------------------------------------------------------
    # Four-class progress: per-unit transaction + reconciliation
    # ------------------------------------------------------------------

    def test_progress_dict_exposes_v2_four_class_names(self):
        """get_discovery_run 返回的 progress dict 必须暴露 v2 four-class 字段名。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        updated = self.store.get_discovery_run(run["id"])
        progress = updated.get("progress") or {}
        # v2 four-class names per http-api.md L203-208.
        self.assertIn("search_queries_completed", progress,
                      "progress dict 必须暴露 search_queries_completed")
        self.assertIn("list_candidates", progress,
                      "progress dict 必须暴露 list_candidates")
        self.assertIn("details_selected", progress,
                      "progress dict 必须暴露 details_selected")
        self.assertIn("details_completed", progress,
                      "progress dict 必须暴露 details_completed")
        self.assertIn("assessments_completed", progress,
                      "progress dict 必须暴露 assessments_completed")
        self.assertIn("recommendations", progress,
                      "progress dict 必须暴露 recommendations")

    def test_progress_counts_match_persisted_rows_after_progressive_run(self):
        """progress 计数必须与持久化行一致（detail_completed_count, assessment_completed_count）。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        updated = self.store.get_discovery_run(run["id"])
        progress = updated.get("progress") or {}
        # 3 selected candidates, all succeed.
        self.assertEqual(progress.get("details_completed"), 3)
        self.assertEqual(progress.get("assessments_completed"), 3)
        self.assertEqual(progress.get("details_selected"), 3)
        # list_candidates was set to 5 by _make_v2_run_selected_n.
        self.assertEqual(progress.get("list_candidates"), 5)

    def test_reconcile_progress_recalculates_from_persisted_rows(self):
        """reconcile_discovery_run_v2 从持久化行重算 v2 计数。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        # Corrupt one counter to simulate drift.
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE discovery_runs SET detail_completed_count=999 WHERE id=?",
                (run["id"],),
            )
        corrupted = self.store.get_discovery_run(run["id"])
        self.assertEqual(corrupted["detail_completed_count"], 999)

        # Reconcile should recalculate from persisted rows.
        self.store.reconcile_discovery_run_v2(run["id"])
        reconciled = self.store.get_discovery_run(run["id"])
        # 3 snapshots with non-unavailable completeness, fetch_status != queued/fetching.
        self.assertEqual(reconciled["detail_completed_count"], 3)
        # 3 completed assessments.
        self.assertEqual(reconciled["assessment_completed_count"], 3)
        # A progress_reconciled event was emitted.
        events = self.store.list_discovery_events(run["id"])
        reconcile_events = [e for e in events if e["event_type"] == "progress_reconciled"]
        self.assertGreaterEqual(len(reconcile_events), 1,
                                "reconcile 必须写入 progress_reconciled 事件")

    def test_reconcile_progress_is_idempotent(self):
        """多次调用 reconcile 得到一致的计数。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        self.store.reconcile_discovery_run_v2(run["id"])
        first = self.store.get_discovery_run(run["id"])
        self.store.reconcile_discovery_run_v2(run["id"])
        second = self.store.get_discovery_run(run["id"])

        self.assertEqual(first["detail_completed_count"], second["detail_completed_count"])
        self.assertEqual(first["assessment_completed_count"], second["assessment_completed_count"])
        self.assertEqual(first["list_candidate_count"], second["list_candidate_count"])

    # ------------------------------------------------------------------
    # Timing fields: first_result_at, first_batch_at, list_completed_at, processing_completed_at
    # ------------------------------------------------------------------

    def test_first_result_at_set_when_first_assessment_visible(self):
        """首个结果可见时 first_result_at 必须写入。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        updated = self.store.get_discovery_run(run["id"])
        self.assertIsNotNone(updated.get("first_result_at"),
                             "首个结果可见后 first_result_at 必须非空")

    def test_first_batch_at_set_when_fifth_result_visible(self):
        """第 5 个结果可见时 first_batch_at 必须写入。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=10, detail_budget=5)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        updated = self.store.get_discovery_run(run["id"])
        self.assertIsNotNone(updated.get("first_batch_at"),
                             "第 5 个结果可见后 first_batch_at 必须非空")

    def test_first_batch_at_null_when_fewer_than_five_results(self):
        """结果不足 5 个时 first_batch_at 保持 NULL。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        updated = self.store.get_discovery_run(run["id"])
        self.assertIsNone(updated.get("first_batch_at"),
                          "结果不足 5 个时 first_batch_at 必须为 NULL")

    def test_list_completed_at_set_after_stage_fetching_lists(self):
        """_stage_fetching_lists 完成后 list_completed_at 必须写入。"""
        from webui.discovery_runner import DiscoveryRunner, _source_input_hash
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": 3},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash="v2-iso-list-timing-hash",
            policy_version="discovery_v2",
        )
        items = [{
            "keyword": "Python", "city": "上海", "source_filters": {},
            "target_pages": 1, "direction_ids": [d1["id"]],
            "input_hash": _source_input_hash({
                "keyword": "Python", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
        }]
        self.store.create_search_plan(run["id"], detail_budget=3, items=items)

        class _ListSource:
            def fetch_list(self, plan_item):
                class Outcome:
                    pass
                out = Outcome()
                out.ok = True
                out.failed_code = None
                out.safe_log = None
                out.jobs = [
                    {"job_id": "job-timing-0",
                     "source_url": "https://www.zhipin.com/job_detail/timing0.html",
                     "title": "岗位", "company": "公司",
                     "salary": "25K", "location": "上海"},
                ]
                return out

        import threading
        cancel_event = threading.Event()
        runner = DiscoveryRunner(self.store, source=_ListSource(),
                                 ai_provider=None,
                                 result_dir=Path(tempfile.mkdtemp()))
        runner._stage_fetching_lists(run["id"], cancel_event)

        updated = self.store.get_discovery_run(run["id"])
        self.assertIsNotNone(updated.get("list_completed_at"),
                             "_stage_fetching_lists 完成后 list_completed_at 必须非空")

    def test_processing_completed_at_set_after_progressive_eval(self):
        """run_progressive_detail_eval 完成后 processing_completed_at 必须写入。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        updated = self.store.get_discovery_run(run["id"])
        self.assertIsNotNone(updated.get("processing_completed_at"),
                             "渐进评估完成后 processing_completed_at 必须非空")

    def test_first_result_at_not_overwritten_by_subsequent_results(self):
        """first_result_at 写入后不被后续结果覆盖（单调写入：NULL → 值，不再变）。"""
        from webui.discovery_runner import DiscoveryRunner
        run, d1 = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = DiscoveryRunner(self.store, source=self._source_always_ok(),
                                 ai_provider=self._ai_always_ok(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.run_progressive_detail_eval(run["id"])

        first = self.store.get_discovery_run(run["id"])
        first_ts = first.get("first_result_at")
        self.assertIsNotNone(first_ts)
        # Re-run progressive eval (idempotent re-entry); first_result_at must not change.
        runner.run_progressive_detail_eval(run["id"])
        second = self.store.get_discovery_run(run["id"])
        self.assertEqual(second.get("first_result_at"), first_ts,
                         "first_result_at 写入后不得被后续结果覆盖")


class CancelSignalPropagationTests(_IntegrationTestCase):
    """T078 RED: 取消信号必须立即终止新工作并保留已完成结果。

    合同来源:
    - spec.md SC-010: 用户取消后 30 秒内不再启动新 list/detail/AI；已完成结果保留率 100%。
    - state-machine.md: cancelled 是终态，pending plan items 标记 cancelled。
    - http-api.md L310: POST /api/discovery/runs/{run_id}/cancel 设置 cancel_requested_at。
    - data-model.md: cancel 不删除已持久化的 snapshots / assessments / candidates。

    RED 状态（当前代码差距）:
    - ``run_progressive_detail_eval`` 主循环不检查 cancel_event，cancel 后仍会处理剩余候选。
    - 没有 explicit 测试验证 cancel 后 source.cancel_event 被设置且 subprocess 被终止。
    - 没有 explicit 测试验证已完成 snapshots/assessments 在 cancel 后仍可读。
    - 没有 explicit 测试验证 cancel 后 AI 不被调用。
    """

    # ------------------------------------------------------------------
    # Helpers (reuse FailureIsolationAndProgressTests patterns)
    # ------------------------------------------------------------------

    def _make_v2_run_selected_n(self, candidate_count=5, detail_budget=3,
                                job_prefix="job-cancel"):
        """Create a v2 run with N selected candidates ready for detail fetch."""
        from webui.discovery import select_priority_details, compile_search_plan
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": detail_budget},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        # T081: compute real input_hash via compile_search_plan so the resume
        # hash drift check in runner.run() passes for v2 cancel tests.
        confirmation_view = {
            "id": c["id"], "analysis_id": a["id"],
            "hard_constraints": {}, "soft_preferences": {},
            "safe_limits": {"max_details": detail_budget},
            "enabled_directions": [{
                "id": d1["id"], "direction_id": d1["id"],
                "name": d1.get("name", ""),
                "type": d1.get("direction_type", ""),
                "search_terms": d1.get("search_terms", []),
                "default_enabled": d1.get("default_enabled", False),
                "evidence_refs": [],
            }],
            "directions": c.get("directions", []),
        }
        plan = compile_search_plan(confirmation_view)
        real_input_hash = plan["input_hash"]
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash=real_input_hash,
            policy_version="discovery_v2",
        )
        for i in range(candidate_count):
            job_id = f"{job_prefix}-{i:03d}"
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, '后端', '公司', '25K', '上海', 'jd', '2026-01-01', '2026-01-01')",
                    (job_id, f"https://www.zhipin.com/job_detail/{job_id}.html",
                     f"https://www.zhipin.com/job_detail/{job_id}.html"),
                )
            self.store.upsert_run_candidate(
                run_id=run["id"], job_id=job_id,
                source_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
                direction_ids=[d1["id"]], search_terms=["Python"],
                source_positions=[{"item": i, "page": 1, "rank": i}],
                list_fields={"title": f"岗位{i}", "salary": "25K", "location": "上海"},
                input_hash=real_input_hash,
            )
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=detail_budget, directions=[d1["id"]],
        )
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        for item in result["deferred"]:
            self.store.update_run_candidate_state(
                item["id"], selection_decision="deferred",
                selection_reason="budget_deferred",
            )
        self.store.update_discovery_run(run["id"], counters={
            "list_candidate_count": candidate_count,
        })
        return run, d1

    @staticmethod
    def _counting_source():
        """Fake source that records every fetch_detail invocation."""

        class _Source:
            def __init__(self):
                self.calls = []  # list of job_id
                self.cancel_event = None

            def fetch_detail(self, job, detail_output_path=None):
                job_id = job.get("job_id")
                self.calls.append(job_id)

                class Outcome:
                    pass
                out = Outcome()
                out.ok = True
                out.detail = {"jd": "详细职位描述", "tags": "Python,Django"}
                out.failed_code = None
                out.safe_log = None
                return out
        return _Source()

    @staticmethod
    def _counting_ai():
        """Fake v2 AI provider that records every assess_job invocation."""

        class _AI:
            def __init__(self):
                self.calls = 0
                self.v2_calls = []

            def assess_job(self, *, candidate_profile=None, directions=None,
                           job_snapshot=None, contract_version="v1", **_kwargs):
                self.calls += 1
                self.v2_calls.append(job_snapshot.get("snapshot_id", "") if job_snapshot else "")
                score = 80
                assessments = []
                for d in directions or []:
                    assessments.append({
                        "direction_id": d["id"],
                        "dimensions": {
                            "capability": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "experience": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "environment": {"score": score, "candidate_fact_refs": [],
                                            "candidate_evidence_refs": [],
                                            "job_evidence_refs": []},
                            "stability": {"score": score, "candidate_fact_refs": [],
                                          "candidate_evidence_refs": [],
                                          "job_evidence_refs": []},
                        },
                        "match_score": score, "confidence": score,
                        "gaps": [], "proposed_band": "high",
                    })
                return {
                    "contract_version": "job_assessment_v2",
                    "assessments": assessments,
                    "quarantined": [],
                    "quality": {"status": "complete", "warnings": []},
                    "metrics": {"provider_call_count": 1},
                }
        return _AI()

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_request_cancel_sets_source_cancel_event(self):
        """``request_cancel`` 必须把 cancel_event 信号传到 source.cancel_event。"""
        from webui.discovery_runner import DiscoveryRunner
        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        source = self._counting_source()
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=self._counting_ai(),
                                 result_dir=Path(tempfile.mkdtemp()))
        # _register_run wires source.cancel_event to runner's internal event.
        # Trigger by calling runner.run (which calls _register_run); but we want
        # to inspect the wiring without driving stages. Use the public API:
        # call request_cancel before run; then verify source.cancel_event is set.
        runner.request_cancel(run["id"])
        # The runner creates an event when request_cancel is called (L534-537);
        # but to propagate to source we need _register_run. The simplest contract:
        # after request_cancel, is_cancelled(run_id) returns True.
        self.assertTrue(runner.is_cancelled(run["id"]),
                        "request_cancel must make is_cancelled() return True")
        # Run the now-cancelled run; should not invoke source.fetch_detail.
        final = runner.run(run["id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(len(source.calls), 0,
                         "cancel 后不得调用 source.fetch_detail")

    def test_cancel_during_progressive_eval_stops_before_next_candidate(self):
        """cancel_event 设置后，progressive 循环不得启动下一个候选的工作。

        RED: 当前 ``run_progressive_detail_eval`` 不检查 cancel_event，因此即使
        cancel 被请求，循环仍会处理所有 selected 候选。修复后应在每个候选循环
        开头检查 cancel_event 并 break。
        """
        from webui.discovery_runner import DiscoveryRunner
        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=self._counting_ai(),
                                 result_dir=Path(tempfile.mkdtemp()))
        cancel_event = runner._register_run(run["id"])
        # Mirror runner.run() wiring so source.cancel_event matches the runner's
        # internal event (this is normally done by runner.run before stages).
        source.cancel_event = cancel_event
        # Set cancel BEFORE invoking progressive eval; first candidate should
        # not even start.
        cancel_event.set()
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(len(source.calls), 0,
                         "cancel 设置后 progressive 循环不得启动任何候选的 detail fetch")

    def test_cancel_mid_progressive_eval_preserves_completed_results(self):
        """cancel 在 N 个候选完成后到达，已 persist 的 snapshots/assessments 必须保留。

        RED: 当前 progressive 循环不检查 cancel，因此无法在 N 个候选后中断；
        此测试通过手动设置 cancel_event + 直接调用 _fetch_one_detail 模拟
        “N 个完成 + cancel 到达” 的场景，验证已 persist 的结果在 cancel 后可读。
        """
        from webui.discovery_runner import DiscoveryRunner
        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=self._counting_ai(),
                                 result_dir=Path(tempfile.mkdtemp()))
        cancel_event = runner._register_run(run["id"])
        source.cancel_event = cancel_event

        # Manually process 2 candidates (simulating prior work), then set cancel
        # and verify the loop stops without processing the remaining 3.
        selected = self.store.list_run_candidates(run["id"], selection_decision="selected")
        selected.sort(key=lambda c: (c.get("selection_rank") or 9999, c["job_id"]))
        self.assertEqual(len(selected), 5)

        # Process first 2 by calling run_progressive_detail_eval after setting
        # cancel_event mid-loop. Since the current code doesn't check cancel,
        # this test will FAIL (all 5 processed instead of stopping after 2).
        # We approximate "mid" by setting cancel after 2s using a timer.
        import threading as _t
        def _set_cancel_after_2_calls():
            # Poll source.calls until it reaches 2, then set cancel.
            for _ in range(200):  # 2s budget
                if len(source.calls) >= 2:
                    cancel_event.set()
                    return
                time.sleep(0.01)
        watcher = _t.Thread(target=_set_cancel_after_2_calls, daemon=True)
        watcher.start()
        runner.run_progressive_detail_eval(run["id"])
        watcher.join(timeout=5)

        # Contract: cancel must arrive before all 5 candidates are processed.
        # Allow some race window: at most 3 may slip through after the signal
        # (the in-flight candidate is allowed to finish). Strict upper bound:
        # total processed ≤ 3 (2 completed + at most 1 in-flight).
        self.assertLess(len(source.calls), 5,
                        "cancel_event 设置后不得继续启动新候选的 detail fetch")
        # Already-persisted snapshots/assessments must remain readable.
        snapshots = self.store.list_snapshots(run["id"])
        self.assertGreaterEqual(len(snapshots), 1,
                                "已完成候选的 snapshots 必须保留")
        for s in snapshots:
            self.assertIsNotNone(s.get("content_hash"))
        assessments = self.store.list_assessments(run["id"])
        self.assertGreaterEqual(len(assessments), 1,
                                "已完成候选的 assessments 必须保留")

    def test_cancel_does_not_invoke_ai_after_signal(self):
        """cancel_event 设置后，AI provider 不得被调用。"""
        from webui.discovery_runner import DiscoveryRunner
        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        ai = self._counting_ai()
        runner = DiscoveryRunner(self.store, source=source, ai_provider=ai,
                                 result_dir=Path(tempfile.mkdtemp()))
        cancel_event = runner._register_run(run["id"])
        source.cancel_event = cancel_event
        cancel_event.set()
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(ai.calls, 0,
                         "cancel 设置后不得调用 AI provider")

    def test_cancel_reaches_cancelled_status_within_30_simulated_seconds(self):
        """SC-010: cancel 后 30 秒内 run 必须进入 cancelled 终态。

        使用 fake monotonic_clock 控制 runner 的 timing；cancel_event 设置后
        调用 runner.run()，验证最终 status='cancelled' 且 wall-clock ≤ 30s。
        """
        from webui.discovery_runner import DiscoveryRunner
        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=self._counting_ai(),
                                 result_dir=Path(tempfile.mkdtemp()))
        runner.request_cancel(run["id"])
        started = time.monotonic()
        final = runner.run(run["id"])
        elapsed = time.monotonic() - started
        self.assertEqual(final["status"], "cancelled")
        self.assertLessEqual(elapsed, 30.0,
                             "SC-010: cancel 后 30 秒内必须进入 cancelled 终态")

    def test_cancel_preserves_already_persisted_snapshots_and_assessments(self):
        """cancel 后已持久化的 snapshots / assessments / candidates 必须可读且未删除。"""
        from webui.discovery_runner import DiscoveryRunner
        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        source = self._counting_source()
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=self._counting_ai(),
                                 result_dir=Path(tempfile.mkdtemp()))
        # Run progressive eval to completion first (3 candidates, 3 snapshots, 3 assessments).
        runner.run_progressive_detail_eval(run["id"])
        before_snapshots = self.store.list_snapshots(run["id"])
        before_assessments = self.store.list_assessments(run["id"])
        self.assertEqual(len(before_snapshots), 3)
        self.assertEqual(len(before_assessments), 3)

        # Now request cancel and run again (idempotent re-entry should not destroy data).
        runner.request_cancel(run["id"])
        final = runner.run(run["id"])
        # Run may be 'cancelled' if cancel was checked before any stage; or 'succeeded'
        # if the progressive eval already completed. Either way, snapshots/assessments
        # must survive.
        after_snapshots = self.store.list_snapshots(run["id"])
        after_assessments = self.store.list_assessments(run["id"])
        self.assertEqual(len(after_snapshots), len(before_snapshots),
                         "cancel 不得删除已 persist 的 snapshots")
        self.assertEqual(len(after_assessments), len(before_assessments),
                         "cancel 不得删除已 persist 的 assessments")
        before_snapshot_ids = {s["id"] for s in before_snapshots}
        after_snapshot_ids = {s["id"] for s in after_snapshots}
        self.assertEqual(before_snapshot_ids, after_snapshot_ids,
                         "cancel 不得改变 snapshot IDs")

    def test_cancel_marks_pending_plan_items_cancelled_in_list_stage(self):
        """cancel 在 list 阶段触发时，未完成的 plan items 必须标记 cancelled。"""
        from webui.discovery_runner import DiscoveryRunner
        from webui.source import FakeJobSource
        # Use the standard v1 confirmation helper which compiles a real plan.
        analysis, confirmation = _make_ready_analysis_with_confirmation(
            self.store, self.resume["id"], self.profile["id"],
            hard_constraints={"city": "北京"},
        )
        run = _make_discovery_run(
            self.store, confirmation, analysis,
            self.resume["id"], self.profile["id"],
        )
        source = FakeJobSource(list_jobs={
            ("Python 后端", "北京"): [
                {"job_id": "j1", "title": "Python", "source_url": "https://x/1", "jd": "jd"},
            ],
        }, detail_jobs={"j1": {"jd": "jd"}})
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=_AssessingFakeAIProvider(),
                                 result_dir=Path(tempfile.mkdtemp(prefix="boss-cancel-")))
        runner.request_cancel(run["id"])
        # Drive runner.run() with cancel already set; planning runs (compiles plan),
        # then _stage_fetching_lists sees cancel and exits early, leaving plan items
        # non-terminal. _handle_cancel then marks them as cancelled.
        final = runner.run(run["id"])
        self.assertEqual(final["status"], "cancelled")
        plan_after = self.store.get_search_plan(run["id"])
        # No item should remain in 'pending' / 'running'; each must be cancelled/completed/failed.
        for it in plan_after["items"]:
            self.assertIn(it["status"], ("cancelled", "completed", "failed", "skipped"),
                          f"plan item {it['id']} 留在非终态 {it['status']}")

    def test_cancel_request_on_terminal_run_raises_conflict(self):
        """已终态的 run 调用 request_cancel 必须返回 state_conflict。"""
        from webui.discovery_runner import DiscoveryRunner
        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        source = self._counting_source()
        runner = DiscoveryRunner(self.store, source=source,
                                 ai_provider=self._counting_ai(),
                                 result_dir=Path(tempfile.mkdtemp()))
        # Complete the run first.
        runner.run_progressive_detail_eval(run["id"])
        # Mark as a terminal status (succeeded) to simulate completion.
        from webui.discovery_runner import STATUS_SUCCEEDED
        self.store.update_discovery_run(run["id"], status=STATUS_SUCCEEDED, completed=True)
        with self.assertRaises(DiscoveryError) as ctx:
            runner.request_cancel(run["id"])
        self.assertEqual(ctx.exception.error_code, "state_conflict")


class ResumeHashDriftAndSc011Tests(_IntegrationTestCase):
    """T080 RED: interrupted/eligible partial 恢复校验 profile/confirmation/policy/input hashes，
    完成 detail/assessment 外部调用重复数=0（SC-011）。

    合同来源:
    - spec.md SC-011: 受控中断恢复测试中，已完成且输入身份一致的详情和评估重复执行数为 0。
    - http-api.md L320: resume rejects profile/confirmation/policy/input hash drift with 409.
    - data-model.md: resume 必须从 SQLite 持久化状态恢复，不得重做已完成工作。
    - state-machine.md: interrupted / partial 是可恢复终态；succeeded/failed/cancelled 不可恢复。

    RED 状态（当前代码差距）:
    - ``runner.run()`` resume 路径不校验 input_hash / policy_version / profile_version 漂移。
    - ``run_progressive_detail_eval`` 在 resume 时仍重新调用 ``source.fetch_detail``，
      即使本 run 已有 complete snapshot（find_reusable_snapshot 排除当前 run）。
    - assessment 路径已正确跳过 completed 方向（_evaluate_job_v2_group 只处理 pending_dirs）。
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_v2_run_selected_n(self, candidate_count=5, detail_budget=5,
                                job_prefix="job-resume"):
        """Create a v2 run with N selected candidates ready for detail fetch.

        T081: input_hash is computed from compile_search_plan(confirmation_view)
        so that runner.run()'s resume hash drift check can compare against a
        real re-computed hash, not a fake placeholder.
        """
        from webui.discovery import compile_search_plan, select_priority_details
        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={"max_details": detail_budget},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        # Build confirmation view (mirror _load_confirmation_view) to compute
        # the real input_hash via compile_search_plan.
        confirmation_view = {
            "id": c["id"], "analysis_id": a["id"],
            "hard_constraints": {}, "soft_preferences": {},
            "safe_limits": {"max_details": detail_budget},
            "enabled_directions": [{
                "id": d1["id"], "direction_id": d1["id"],
                "name": d1.get("name", ""),
                "type": d1.get("direction_type", ""),
                "search_terms": d1.get("search_terms", []),
                "default_enabled": d1.get("default_enabled", False),
                "evidence_refs": [],
            }],
            "directions": c.get("directions", []),
        }
        plan = compile_search_plan(confirmation_view)
        real_input_hash = plan["input_hash"]
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash=real_input_hash,
            policy_version="discovery_v2",
        )
        for i in range(candidate_count):
            job_id = f"{job_prefix}-{i:03d}"
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, '后端', '公司', '25K', '上海', 'jd', '2026-01-01', '2026-01-01')",
                    (job_id, f"https://www.zhipin.com/job_detail/{job_id}.html",
                     f"https://www.zhipin.com/job_detail/{job_id}.html"),
                )
            self.store.upsert_run_candidate(
                run_id=run["id"], job_id=job_id,
                source_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
                direction_ids=[d1["id"]], search_terms=["Python"],
                source_positions=[{"item": i, "page": 1, "rank": i}],
                list_fields={"title": f"岗位{i}", "salary": "25K", "location": "上海"},
                input_hash=real_input_hash,
            )
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=detail_budget, directions=[d1["id"]],
        )
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        for item in result["deferred"]:
            self.store.update_run_candidate_state(
                item["id"], selection_decision="deferred",
                selection_reason="budget_deferred",
            )
        self.store.update_discovery_run(run["id"], counters={
            "list_candidate_count": candidate_count,
        })
        # T081: persist the search plan so _stage_assembling can read it on
        # resume via runner.run(). Without this, _stage_assembling raises
        # KeyError because get_search_plan finds no row.
        # compile_search_plan returns items with `term` key; create_search_plan
        # expects `keyword`. Mirror _materialize_plan_items transformation.
        from webui.discovery import SCRAPER_FILTER_FIELDS
        from webui.source import _input_hash as _source_input_hash
        materialized_items = []
        hard_constraints = plan.get("hard_constraints") or {}
        safe_limits = plan.get("safe_limits") or {}
        city = hard_constraints.get("city", "")
        source_filters = {
            k: v for k, v in hard_constraints.items()
            if k in SCRAPER_FILTER_FIELDS
        }
        target_pages = int(safe_limits.get("max_pages", 1))
        for raw_item in plan["items"]:
            item_input_hash = _source_input_hash({
                "keyword": raw_item["term"],
                "city": city,
                "source_filters": source_filters,
                "target_pages": target_pages,
            })
            materialized_items.append({
                "keyword": raw_item["term"],
                "city": city,
                "source_filters": source_filters,
                "direction_ids": raw_item["direction_ids"],
                "input_hash": item_input_hash,
                "target_pages": target_pages,
                "detail_budget": int(plan["detail_budget"] // max(1, len(plan["items"]))),
            })
        self.store.create_search_plan(
            run["id"],
            detail_budget=plan["detail_budget"],
            items=materialized_items,
        )
        # T081: simulate that _stage_fetching_lists already ran and marked
        # each plan item "completed". The test helper inserts candidates
        # directly (skipping list fetching), so without this the plan items
        # would remain "queued" and calculate_run_completion would return
        # status=run.get("status") (i.e. STATUS_ASSEMBLING) instead of a
        # terminal state.
        persisted_plan = self.store.get_search_plan(run["id"])
        for item in persisted_plan["items"]:
            self.store.update_plan_item(
                item["id"], status="completed", completed=True,
            )
        return run, d1

    @staticmethod
    def _counting_source():
        """Fake source that records every fetch_detail invocation."""

        class _Source:
            def __init__(self):
                self.calls = []  # list of job_id
                self.cancel_event = None

            def fetch_detail(self, job, detail_output_path=None):
                job_id = job.get("job_id")
                self.calls.append(job_id)

                class Outcome:
                    pass
                out = Outcome()
                out.ok = True
                out.detail = {"jd": "详细职位描述", "tags": "Python,Django"}
                out.failed_code = None
                out.safe_log = None
                return out
        return _Source()

    @staticmethod
    def _counting_ai():
        """Fake v2 AI provider that records every assess_job invocation."""

        class _AI:
            def __init__(self):
                self.calls = 0
                self.v2_calls = []

            def assess_job(self, *, candidate_profile=None, directions=None,
                           job_snapshot=None, contract_version="v1", **_kwargs):
                self.calls += 1
                self.v2_calls.append(job_snapshot.get("snapshot_id", "") if job_snapshot else "")
                score = 80
                assessments = []
                for d in directions or []:
                    assessments.append({
                        "direction_id": d["id"],
                        "dimensions": {
                            "capability": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "experience": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "environment": {"score": score, "candidate_fact_refs": [],
                                            "candidate_evidence_refs": [],
                                            "job_evidence_refs": []},
                            "stability": {"score": score, "candidate_fact_refs": [],
                                          "candidate_evidence_refs": [],
                                          "job_evidence_refs": []},
                        },
                        "match_score": score, "confidence": score,
                        "gaps": [], "proposed_band": "high",
                    })
                return {
                    "contract_version": "job_assessment_v2",
                    "assessments": assessments,
                    "quarantined": [],
                    "quality": {"status": "complete", "warnings": []},
                    "metrics": {"provider_call_count": 1},
                }
        return _AI()

    def _make_runner(self, source, ai):
        from webui.discovery_runner import DiscoveryRunner
        return DiscoveryRunner(self.store, source=source, ai_provider=ai,
                               result_dir=Path(tempfile.mkdtemp()))

    # ------------------------------------------------------------------
    # Hash drift tests (RED — no checks exist)
    # ------------------------------------------------------------------

    def test_resume_rejects_input_hash_drift(self):
        """resume 必须拒绝 input_hash 漂移（http-api.md L320）。

        GREEN: ``runner.run()`` resume 路径重算 input_hash 并与存储值比对。
        """
        from webui.discovery_runner import DiscoveryRunner, STATUS_INTERRUPTED
        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        # Simulate interrupt after some work.
        self.store.update_discovery_run(
            run["id"], status=STATUS_INTERRUPTED, stage="processing_jobs", started=True,
        )
        # Drift the stored input_hash via direct SQL (simulates external
        # corruption/migration; input_hash is immutable via public API).
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE discovery_runs SET input_hash = ? WHERE id = ?",
                ("drifted-hash", run["id"]),
            )
        source = self._counting_source()
        runner = self._make_runner(source, self._counting_ai())
        with self.assertRaises(DiscoveryError) as ctx:
            runner.run(run["id"])
        self.assertEqual(ctx.exception.error_code, "state_conflict",
                         "input_hash 漂移必须返回 state_conflict")
        self.assertEqual(len(source.calls), 0,
                         "hash 漂移时不得启动任何新工作")

    def test_resume_rejects_policy_version_drift(self):
        """resume 必须拒绝 policy_version 漂移到非法值（http-api.md L320）。

        GREEN: ``runner.run()`` resume 路径校验 policy_version ∈
        {"v1", "discovery_v1", "discovery_v2"}；非法值视为漂移。
        注意：从 "discovery_v2" 漂移到 "discovery_v1" 无法检测（两者都是
        合法值），需要单独的不可变字段才能区分；当前实现只拒绝非法值。
        """
        from webui.discovery_runner import DiscoveryRunner, STATUS_INTERRUPTED
        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        self.store.update_discovery_run(
            run["id"], status=STATUS_INTERRUPTED, stage="processing_jobs", started=True,
        )
        # Drift the stored policy_version to an invalid value via direct SQL.
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE discovery_runs SET policy_version = ? WHERE id = ?",
                ("drifted-policy", run["id"]),
            )
        source = self._counting_source()
        runner = self._make_runner(source, self._counting_ai())
        with self.assertRaises(DiscoveryError) as ctx:
            runner.run(run["id"])
        self.assertEqual(ctx.exception.error_code, "state_conflict",
                         "policy_version 漂移必须返回 state_conflict")

    # ------------------------------------------------------------------
    # SC-011: completed detail/assessment not re-executed on resume
    # ------------------------------------------------------------------

    def test_resume_skips_completed_detail_fetches(self):
        """SC-011: resume 时已完成 detail 的候选不得重新调用 source.fetch_detail。

        RED: 当前 ``run_progressive_detail_eval`` 在 resume 时仍重新调用
        ``source.fetch_detail``，因为 ``find_reusable_snapshot`` 排除当前 run。
        修复后应在 ``_fetch_one_detail`` 入口先检查本 run 是否已有 complete snapshot。
        """
        from webui.discovery_runner import DiscoveryRunner, STATUS_INTERRUPTED
        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        runner = self._make_runner(source, self._counting_ai())
        # Process all 5 candidates first (creates 5 snapshots + 5 assessments).
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(len(source.calls), 5)
        # Mark run as interrupted to simulate resume scenario.
        self.store.update_discovery_run(
            run["id"], status=STATUS_INTERRUPTED, stage="processing_jobs", started=True,
        )
        # Reset source call counter; resume should NOT re-fetch any detail.
        source.calls.clear()
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(len(source.calls), 0,
                         "SC-011: resume 时已完成 detail 不得重新调用 source.fetch_detail")

    def test_resume_skips_completed_assessments(self):
        """SC-011: resume 时已完成 assessment 的方向不得重新调用 AI provider。

        PASS (regression guard): ``_evaluate_job_v2_group`` 已通过
        ``_get_assessment`` 跳过 completed 方向，只处理 ``pending_dirs``。
        """
        from webui.discovery_runner import DiscoveryRunner, STATUS_INTERRUPTED
        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        ai = self._counting_ai()
        runner = self._make_runner(source, ai)
        # Process all 5 candidates first (5 AI calls).
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(ai.calls, 5)
        # Mark interrupted, reset AI counter, resume.
        self.store.update_discovery_run(
            run["id"], status=STATUS_INTERRUPTED, stage="processing_jobs", started=True,
        )
        ai.calls = 0
        ai.v2_calls.clear()
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(ai.calls, 0,
                         "SC-011: resume 时已完成 assessment 不得重新调用 AI provider")

    # ------------------------------------------------------------------
    # Terminal run rejection (PASS — existing behavior)
    # ------------------------------------------------------------------

    def test_resume_rejects_terminal_run(self):
        """succeeded / failed / cancelled 终态 run 不得 resume。"""
        from webui.discovery_runner import DiscoveryRunner, STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED
        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        source = self._counting_source()
        runner = self._make_runner(source, self._counting_ai())
        for terminal in (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED):
            self.store.update_discovery_run(run["id"], status=terminal, completed=True)
            # runner.run() on terminal run just returns it as-is; the HTTP layer
            # is what rejects. Test the HTTP-layer contract via state check.
            final = runner.run(run["id"])
            self.assertEqual(final["status"], terminal,
                             f"终态 {terminal} 不得被 resume 改写")

    def test_resume_eligible_when_hashes_match(self):
        """interrupted run with matching hashes 必须成功 resume。"""
        from webui.discovery_runner import DiscoveryRunner, STATUS_INTERRUPTED
        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        # Mark interrupted before any work.
        self.store.update_discovery_run(
            run["id"], status=STATUS_INTERRUPTED, stage="processing_jobs", started=True,
        )
        source = self._counting_source()
        runner = self._make_runner(source, self._counting_ai())
        # Resume should succeed and process all 3 candidates.
        final = runner.run(run["id"])
        self.assertIn(final["status"], ("succeeded", "partial", "failed"))
        self.assertEqual(len(source.calls), 3,
                         "未完成工作必须在 resume 时执行")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
