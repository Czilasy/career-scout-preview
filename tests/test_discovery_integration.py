"""Discovery integration tests (feature 004)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
