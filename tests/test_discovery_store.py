"""Discovery store persistence tests (feature 004)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from webui.store import TaskStore


def _make_profile_and_resume(store: TaskStore, resume_text: str = "示例简历文本") -> tuple[str, str]:
    profile = store.create_profile("测试画像")
    resume = store.save_resume(profile["id"], "storage/path.pdf", "pdf", resume_text, "hash123", "path.pdf")
    return profile["id"], resume["id"]


def _make_confirmed_run(store: TaskStore, *, input_hash: str = "h1") -> dict:
    """Create a profile/resume/analysis/direction/confirmation/run chain."""
    pid, rid = _make_profile_and_resume(store)
    a = store.create_analysis(rid, pid)
    d = store.add_direction(
        a["id"], name="后端", direction_type="core", rationale="r",
        gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
    )
    c = store.create_confirmation(
        profile_id=pid, resume_id=rid, analysis_id=a["id"],
        hard_constraints={}, soft_preferences={}, safe_limits={},
        directions=[{"direction_id": d["id"], "enabled": True, "user_added": False, "user_label": None}],
    )
    run = store.create_discovery_run(
        profile_id=pid, resume_id=rid, analysis_id=a["id"],
        confirmation_id=c["id"], input_hash=input_hash, policy_version="v1",
    )
    return run


def _insert_job(store: TaskStore, job_id: str = "job-x") -> None:
    with store._connection() as conn:
        conn.execute(
            "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
            "VALUES (?, 'https://x', 'https://x', '后端', '公司', '20k', '北京', 'jd', ?, ?)",
            (job_id, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )


class _StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.store = TaskStore(self._tmp.name)

    def tearDown(self) -> None:
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def _table_columns(self, table: str) -> list[str]:
        with self.store._connection() as conn:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]

    def _table_exists(self, table: str) -> bool:
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
        return row is not None

    def _schema_version(self) -> int:
        with self.store._connection() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)


# ---------------------------------------------------------------------------
# T004: Migration 011
# ---------------------------------------------------------------------------


class Migration011Tests(_StoreTestCase):
    def test_schema_version_upgraded_to_13(self):
        self.assertEqual(self._schema_version(), 13)

    def test_candidate_analyses_table_structure(self):
        self.assertTrue(self._table_exists("candidate_analyses"))
        cols = set(self._table_columns("candidate_analyses"))
        required = {
            "id", "resume_id", "profile_id", "version", "status",
            "summary_json", "unknowns_json", "model_name", "contract_version",
            "failure_code", "created_at", "completed_at",
        }
        self.assertTrue(required.issubset(cols), f"missing: {required - cols}")

    def test_resume_evidence_table_structure(self):
        self.assertTrue(self._table_exists("resume_evidence"))
        cols = set(self._table_columns("resume_evidence"))
        required = {
            "id", "analysis_id", "evidence_type", "normalized_value",
            "safe_excerpt", "source_locator_json", "assertion_type",
            "confidence", "sensitive", "created_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_career_directions_table_structure(self):
        self.assertTrue(self._table_exists("career_directions"))
        cols = set(self._table_columns("career_directions"))
        required = {
            "id", "analysis_id", "name", "direction_type", "rationale",
            "gaps_json", "confidence", "default_enabled", "search_terms_json",
            "contract_version", "created_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_direction_evidence_composite_pk(self):
        self.assertTrue(self._table_exists("direction_evidence"))
        with self.store._connection() as conn:
            rows = conn.execute("PRAGMA index_list('direction_evidence')").fetchall()
        pk_names = {r["name"] for r in rows if r["origin"] == "pk"}
        self.assertTrue(pk_names, "direction_evidence should have a primary key")

    def test_idempotent_reopen(self):
        TaskStore(self._tmp.name)
        TaskStore(self._tmp.name)
        self.assertEqual(self._schema_version(), 13)

    def test_unique_resume_version_constraint(self):
        pid, rid = _make_profile_and_resume(self.store)
        self.store.create_analysis(rid, pid)
        self.store.create_analysis(rid, pid)  # version 2
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT INTO candidate_analyses (id, resume_id, profile_id, version, status, summary_json, unknowns_json, model_name, contract_version, failure_code, created_at, completed_at) "
                    "VALUES ('dup', ?, ?, 1, 'queued', '{}', '[]', '', 'v1', NULL, ?, NULL)",
                    (rid, pid, "2026-01-01T00:00:00Z"),
                )

    def test_foreign_key_cascade_on_analysis_delete(self):
        pid, rid = _make_profile_and_resume(self.store)
        analysis = self.store.create_analysis(rid, pid)
        self.store.add_evidence(
            analysis["id"],
            evidence_type="skill",
            normalized_value="Python",
            safe_excerpt="Python",
            source_locator={"page": 1, "start": 0, "end": 6},
            assertion_type="explicit",
            confidence=80,
            sensitive=False,
        )
        with self.store._connection() as conn:
            conn.execute("DELETE FROM candidate_analyses WHERE id = ?", (analysis["id"],))
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM resume_evidence WHERE analysis_id = ?",
                (analysis["id"],),
            ).fetchone()
        self.assertEqual(row["c"], 0, "evidence should cascade-delete with analysis")


# ---------------------------------------------------------------------------
# T006: Migration 012
# ---------------------------------------------------------------------------


class Migration012Tests(_StoreTestCase):
    def test_direction_confirmations_table(self):
        self.assertTrue(self._table_exists("direction_confirmations"))
        cols = set(self._table_columns("direction_confirmations"))
        required = {
            "id", "profile_id", "resume_id", "analysis_id", "version",
            "hard_constraints_json", "soft_preferences_json", "safe_limits_json",
            "confirmed_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_confirmation_directions_table(self):
        self.assertTrue(self._table_exists("confirmation_directions"))
        cols = set(self._table_columns("confirmation_directions"))
        required = {"confirmation_id", "direction_id", "enabled", "user_added", "user_label"}
        self.assertTrue(required.issubset(cols))

    def test_discovery_runs_table(self):
        self.assertTrue(self._table_exists("discovery_runs"))
        cols = set(self._table_columns("discovery_runs"))
        required = {
            "id", "profile_id", "resume_id", "analysis_id", "confirmation_id",
            "status", "stage", "policy_version", "input_hash",
            "source_count", "detail_count", "evaluated_count",
            "high_count", "adjacent_count", "growth_count", "review_count", "unsuitable_count",
            "cancel_requested_at", "failure_code", "failure_stage",
            "created_at", "started_at", "updated_at", "completed_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_discovery_run_events_table(self):
        self.assertTrue(self._table_exists("discovery_run_events"))
        cols = set(self._table_columns("discovery_run_events"))
        required = {"id", "run_id", "sequence", "event_type", "safe_payload_json", "created_at"}
        self.assertTrue(required.issubset(cols))

    def test_search_plans_table(self):
        self.assertTrue(self._table_exists("search_plans"))
        cols = set(self._table_columns("search_plans"))
        required = {"id", "run_id", "plan_version", "status", "item_count", "detail_budget", "created_at", "completed_at"}
        self.assertTrue(required.issubset(cols))

    def test_search_plan_items_table(self):
        self.assertTrue(self._table_exists("search_plan_items"))
        cols = set(self._table_columns("search_plan_items"))
        required = {
            "id", "plan_id", "keyword", "city", "source_filters_json",
            "direction_ids_json", "input_hash", "status",
            "page_cursor", "target_pages", "detail_budget",
            "attempt_count", "failure_code",
            "created_at", "updated_at", "completed_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_search_plan_items_input_hash_unique(self):
        run = _make_confirmed_run(self.store)
        plan = self.store.create_search_plan(run["id"], detail_budget=10)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT INTO search_plan_items (id, plan_id, keyword, city, source_filters_json, direction_ids_json, input_hash, status, page_cursor, target_pages, detail_budget, attempt_count, failure_code, created_at, updated_at, completed_at) "
                    "VALUES ('i1', ?, 'Python', '北京', '{}', '[]', 'dup-hash', 'queued', 0, 1, 5, 0, NULL, ?, ?, NULL)",
                    (plan["id"], "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                )
                conn.execute(
                    "INSERT INTO search_plan_items (id, plan_id, keyword, city, source_filters_json, direction_ids_json, input_hash, status, page_cursor, target_pages, detail_budget, attempt_count, failure_code, created_at, updated_at, completed_at) "
                    "VALUES ('i2', ?, 'Python', '北京', '{}', '[]', 'dup-hash', 'queued', 0, 1, 5, 0, NULL, ?, ?, NULL)",
                    (plan["id"], "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                )

    def test_run_input_hash_not_exposed_in_update_api(self):
        """input_hash is immutable because update_discovery_run does not accept it."""
        run = _make_confirmed_run(self.store)
        with self.assertRaises(TypeError):
            self.store.update_discovery_run(run["id"], input_hash="changed")


# ---------------------------------------------------------------------------
# T008: Migration 013
# ---------------------------------------------------------------------------


class Migration013Tests(_StoreTestCase):
    def test_discovery_job_snapshots_table(self):
        self.assertTrue(self._table_exists("discovery_job_snapshots"))
        cols = set(self._table_columns("discovery_job_snapshots"))
        required = {
            "id", "run_id", "job_id", "source_url",
            "title", "company", "salary", "location", "tags", "jd",
            "company_json", "completeness", "missing_fields_json",
            "source_status", "content_hash", "fetch_status",
            "attempt_count", "failure_code", "fetched_at", "updated_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_job_direction_assessments_table(self):
        self.assertTrue(self._table_exists("job_direction_assessments"))
        cols = set(self._table_columns("job_direction_assessments"))
        required = {
            "id", "run_id", "snapshot_id", "direction_id",
            "status", "hard_outcome", "hard_checks_json", "dimensions_json",
            "match_score", "confidence", "category",
            "candidate_evidence_ids_json", "job_evidence_json", "gaps_json",
            "policy_version", "contract_version",
            "failure_code", "attempt_count",
            "created_at", "updated_at", "completed_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_discovery_feedback_table(self):
        self.assertTrue(self._table_exists("discovery_feedback"))
        cols = set(self._table_columns("discovery_feedback"))
        required = {
            "id", "profile_id", "run_id", "job_id", "direction_id", "assessment_id",
            "target_type", "action", "reason_code", "scope", "safe_note",
            "created_at", "revoked_at",
        }
        self.assertTrue(required.issubset(cols))

    def test_snapshot_unique_run_job(self):
        run = _make_confirmed_run(self.store)
        _insert_job(self.store, "job-x")
        self.store.save_job_snapshot(
            run_id=run["id"], job_id="job-x", source_url="https://x",
            title="后端", company="公司", salary="20k", location="北京", tags="Python",
            jd="jd", company_json={}, completeness="complete", missing_fields=[],
            source_status="active", content_hash="ch1", fetch_status="completed",
        )
        # Re-saving same (run_id, job_id) should UPDATE, not raise (idempotent replay).
        self.store.save_job_snapshot(
            run_id=run["id"], job_id="job-x", source_url="https://x",
            title="后端2", company="公司2", salary="22k", location="北京", tags="Python",
            jd="jd2", company_json={}, completeness="complete", missing_fields=[],
            source_status="active", content_hash="ch2", fetch_status="completed",
        )
        # But inserting a different snapshot row for the same (run, job) must fail.
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT INTO discovery_job_snapshots (id, run_id, job_id, source_url, title, company, salary, location, tags, jd, company_json, completeness, missing_fields_json, source_status, content_hash, fetch_status, updated_at) "
                    "VALUES ('s2', ?, 'job-x', 'https://x', 'dup', 'dup', '20k', '北京', 'Python', 'jd', '{}', 'complete', '[]', 'active', 'ch3', 'completed', ?)",
                    (run["id"], "2026-01-01T00:00:00Z"),
                )


# ---------------------------------------------------------------------------
# T018: Analysis/Evidence/Direction/Confirmation CRUD
# ---------------------------------------------------------------------------


class AnalysisEvidenceDirectionCrudTests(_StoreTestCase):
    def test_create_analysis_increments_version(self):
        pid, rid = _make_profile_and_resume(self.store)
        a1 = self.store.create_analysis(rid, pid)
        a2 = self.store.create_analysis(rid, pid)
        self.assertEqual(a1["version"], 1)
        self.assertEqual(a2["version"], 2)

    def test_get_analysis_returns_dict(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        fetched = self.store.get_analysis(a["id"])
        self.assertEqual(fetched["id"], a["id"])
        self.assertEqual(fetched["status"], "queued")

    def test_list_analyses_by_resume(self):
        pid, rid = _make_profile_and_resume(self.store)
        a1 = self.store.create_analysis(rid, pid)
        a2 = self.store.create_analysis(rid, pid)
        result = self.store.list_analyses(rid)
        self.assertEqual({a["id"] for a in result}, {a1["id"], a2["id"]})

    def test_update_analysis_status(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        self.store.update_analysis_status(a["id"], status="ready", summary={"h": "x"}, unknowns=[])
        fetched = self.store.get_analysis(a["id"])
        self.assertEqual(fetched["status"], "ready")
        self.assertEqual(fetched["summary"], {"h": "x"})
        self.assertIsNotNone(fetched["completed_at"])

    def test_add_and_list_evidence(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        e = self.store.add_evidence(
            a["id"], evidence_type="skill", normalized_value="Python",
            safe_excerpt="Python", source_locator={"start": 0, "end": 6},
            assertion_type="explicit", confidence=80, sensitive=False,
        )
        evs = self.store.list_evidence(a["id"])
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["id"], e["id"])

    def test_add_and_list_directions(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        d = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=["g1"], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        ds = self.store.list_directions(a["id"])
        self.assertEqual(len(ds), 1)
        self.assertEqual(ds[0]["id"], d["id"])
        self.assertEqual(ds[0]["search_terms"], ["Python"])

    def test_link_direction_evidence_and_list(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        e = self.store.add_evidence(
            a["id"], evidence_type="skill", normalized_value="Python",
            safe_excerpt="Python", source_locator={}, assertion_type="explicit",
            confidence=80, sensitive=False,
        )
        d = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        self.store.link_direction_evidence(d["id"], e["id"], role="primary")
        links = self.store.list_direction_evidence(d["id"])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["evidence_id"], e["id"])

    def test_direction_evidence_rejects_cross_analysis(self):
        # data-model.md:108 — direction and evidence must belong to the same analysis.
        pid, rid = _make_profile_and_resume(self.store)
        a1 = self.store.create_analysis(rid, pid)
        a2 = self.store.create_analysis(rid, pid)
        # direction belongs to analysis 1
        d1 = self.store.add_direction(
            a1["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        # evidence belongs to analysis 2
        e2 = self.store.add_evidence(
            a2["id"], evidence_type="skill", normalized_value="Python",
            safe_excerpt="Python", source_locator={}, assertion_type="explicit",
            confidence=80, sensitive=False,
        )
        with self.assertRaises(ValueError):
            self.store.link_direction_evidence(d1["id"], e2["id"], role="primary")

    def test_create_and_get_confirmation(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        d = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={"city": "北京"}, soft_preferences={"weights": {}}, safe_limits={"max_details": 60},
            directions=[{"direction_id": d["id"], "enabled": True, "user_added": False, "user_label": None}],
        )
        fetched = self.store.get_confirmation(c["id"])
        self.assertEqual(fetched["version"], 1)
        self.assertEqual(fetched["hard_constraints"], {"city": "北京"})

    def test_confirmation_version_increments_per_profile(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        d = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c1 = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={},
            directions=[{"direction_id": d["id"], "enabled": True, "user_added": False, "user_label": None}],
        )
        c2 = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={}, safe_limits={},
            directions=[{"direction_id": d["id"], "enabled": True, "user_added": False, "user_label": None}],
        )
        self.assertEqual(c1["version"], 1)
        self.assertEqual(c2["version"], 2)


# ---------------------------------------------------------------------------
# T033: Run/Plan/Snapshot/Assessment CRUD
# ---------------------------------------------------------------------------


class RunPlanSnapshotAssessmentCrudTests(_StoreTestCase):
    def test_create_and_get_run(self):
        run = _make_confirmed_run(self.store)
        fetched = self.store.get_discovery_run(run["id"])
        self.assertEqual(fetched["status"], "created")
        self.assertEqual(fetched["input_hash"], "h1")

    def test_list_runs(self):
        run = _make_confirmed_run(self.store)
        runs = self.store.list_discovery_runs()
        self.assertTrue(any(r["id"] == run["id"] for r in runs))

    def test_update_run_status(self):
        run = _make_confirmed_run(self.store)
        self.store.update_discovery_run(run["id"], status="planning", stage="planning")
        fetched = self.store.get_discovery_run(run["id"])
        self.assertEqual(fetched["status"], "planning")
        self.assertEqual(fetched["stage"], "planning")

    def test_terminal_status_immutable(self):
        run = _make_confirmed_run(self.store)
        self.store.update_discovery_run(run["id"], status="succeeded", stage="assembling", completed=True)
        with self.assertRaises(ValueError):
            self.store.update_discovery_run(run["id"], status="planning")

    def test_append_and_list_events(self):
        run = _make_confirmed_run(self.store)
        self.store.append_discovery_event(run["id"], event_type="stage", payload={"stage": "planning"})
        self.store.append_discovery_event(run["id"], event_type="progress", payload={"count": 1})
        events = self.store.list_discovery_events(run["id"])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["sequence"], 1)
        self.assertEqual(events[1]["sequence"], 2)

    def test_create_and_get_plan(self):
        run = _make_confirmed_run(self.store)
        plan = self.store.create_search_plan(run["id"], detail_budget=10)
        fetched = self.store.get_search_plan(run["id"])
        self.assertEqual(fetched["detail_budget"], 10)
        self.assertEqual(fetched["id"], plan["id"])

    def test_search_plan_unique_per_run(self):
        # data-model.md:181 — one active plan per run.
        run = _make_confirmed_run(self.store)
        self.store.create_search_plan(run["id"], detail_budget=10)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_search_plan(run["id"], detail_budget=20)

    def test_save_and_get_snapshot(self):
        run = _make_confirmed_run(self.store)
        _insert_job(self.store, "job-x")
        snap = self.store.save_job_snapshot(
            run_id=run["id"], job_id="job-x", source_url="https://x",
            title="后端", company="公司", salary="20k", location="北京", tags="Python",
            jd="jd", company_json={}, completeness="complete", missing_fields=[],
            source_status="active", content_hash="ch1", fetch_status="completed",
        )
        fetched = self.store.get_snapshot(run["id"], "job-x")
        self.assertEqual(fetched["title"], "后端")
        self.assertEqual(fetched["completeness"], "complete")
        self.assertEqual(fetched["id"], snap["id"])

    def test_list_snapshots(self):
        run = _make_confirmed_run(self.store)
        _insert_job(self.store, "job-x")
        snap = self.store.save_job_snapshot(
            run_id=run["id"], job_id="job-x", source_url="https://x",
            title="后端", company="公司", salary="20k", location="北京", tags="Python",
            jd="jd", company_json={}, completeness="complete", missing_fields=[],
            source_status="active", content_hash="ch1", fetch_status="completed",
        )
        snaps = self.store.list_snapshots(run["id"])
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["id"], snap["id"])

    def test_create_and_get_assessment(self):
        run = _make_confirmed_run(self.store)
        _insert_job(self.store, "job-x")
        self.store.save_job_snapshot(
            run_id=run["id"], job_id="job-x", source_url="https://x",
            title="后端", company="公司", salary="20k", location="北京", tags="Python",
            jd="jd", company_json={}, completeness="complete", missing_fields=[],
            source_status="active", content_hash="ch1", fetch_status="completed",
        )
        analysis_id = self.store.get_discovery_run(run["id"])["analysis_id"]
        direction = self.store.list_directions(analysis_id)[0]
        self.store.create_assessment(
            run["id"], snap_id := None, direction["id"],
            hard_outcome="pass", hard_checks={}, dimensions={"direction_alignment": {"score": 80}},
            match_score=85, confidence=80, category="high_match",
            candidate_evidence_ids=[], job_evidence={}, gaps=[],
            policy_version="v1", contract_version="v1", status="completed",
        ) if False else self.store.create_assessment(
            run["id"], self.store.get_snapshot(run["id"], "job-x")["id"], direction["id"],
            hard_outcome="pass", hard_checks={}, dimensions={"direction_alignment": {"score": 80}},
            match_score=85, confidence=80, category="high_match",
            candidate_evidence_ids=[], job_evidence={}, gaps=[],
            policy_version="v1", contract_version="v1", status="completed",
        )
        fetched = self.store.get_assessment(
            run["id"],
            self.store.get_snapshot(run["id"], "job-x")["id"],
            direction["id"],
        )
        self.assertEqual(fetched["category"], "high_match")

    def test_list_assessments_by_run(self):
        run = _make_confirmed_run(self.store)
        _insert_job(self.store, "job-x")
        self.store.save_job_snapshot(
            run_id=run["id"], job_id="job-x", source_url="https://x",
            title="后端", company="公司", salary="20k", location="北京", tags="Python",
            jd="jd", company_json={}, completeness="complete", missing_fields=[],
            source_status="active", content_hash="ch1", fetch_status="completed",
        )
        analysis_id = self.store.get_discovery_run(run["id"])["analysis_id"]
        direction = self.store.list_directions(analysis_id)[0]
        snap_id = self.store.get_snapshot(run["id"], "job-x")["id"]
        self.store.create_assessment(
            run["id"], snap_id, direction["id"],
            hard_outcome="pass", hard_checks={}, dimensions={},
            match_score=80, confidence=70, category="high_match",
            candidate_evidence_ids=[], job_evidence={}, gaps=[],
            policy_version="v1", contract_version="v1", status="completed",
        )
        assessments = self.store.list_assessments(run["id"])
        self.assertEqual(len(assessments), 1)


# ---------------------------------------------------------------------------
# T055: Feedback CRUD
# ---------------------------------------------------------------------------


class FeedbackCrudTests(_StoreTestCase):
    def test_create_and_list_feedback(self):
        pid, rid = _make_profile_and_resume(self.store)
        fb = self.store.create_discovery_feedback(
            pid, "direction", "direction_disable",
            reason_code="not_interested", scope="exact_job", safe_note="n",
        )
        fetched = self.store.list_discovery_feedback(pid)
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0]["id"], fb["id"])

    def test_revoke_feedback(self):
        pid, rid = _make_profile_and_resume(self.store)
        fb = self.store.create_discovery_feedback(
            pid, "job", "not_interested",
            reason_code="skip", scope="exact_job", safe_note="",
        )
        self.store.revoke_discovery_feedback(fb["id"])
        fetched = self.store.list_discovery_feedback(pid)
        self.assertIsNotNone(fetched[0]["revoked_at"])

    def test_effective_only_filter(self):
        pid, rid = _make_profile_and_resume(self.store)
        fb1 = self.store.create_discovery_feedback(pid, "job", "not_interested", scope="exact_job")
        fb2 = self.store.create_discovery_feedback(pid, "job", "interested", scope="exact_job")
        self.store.revoke_discovery_feedback(fb2["id"])
        effective = self.store.list_discovery_feedback(pid, effective_only=True)
        self.assertEqual({f["id"] for f in effective}, {fb1["id"]})


# ---------------------------------------------------------------------------
# T061: Legacy interest/trash compat
# ---------------------------------------------------------------------------


class LegacyInterestTrashCompatTests(_StoreTestCase):
    def test_profile_jobs_state_remains_visible(self):
        pid, rid = _make_profile_and_resume(self.store)
        _insert_job(self.store, "job-x")
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO profile_jobs (profile_id, job_id, status, shown_at) VALUES (?, 'job-x', 'interested', ?)",
                (pid, "2026-01-01T00:00:00Z"),
            )
        pj = self.store.get_profile_job(pid, "job-x")
        self.assertEqual(pj["status"], "interested")

    def test_discovery_not_interested_bridges_to_profile_jobs_and_trash(self):
        """T061: discovery not_interested -> profile_jobs.status='deleted' + screening_trash_records."""
        pid, rid = _make_profile_and_resume(self.store)
        _insert_job(self.store, "job-trash-1")
        self.store.create_discovery_feedback(
            profile_id=pid, target_type="job", action="not_interested",
            job_id="job-trash-1", run_id="run-a", reason_code="salary_too_low",
            scope="exact_job",
        )
        pj = self.store.get_profile_job(pid, "job-trash-1")
        self.assertEqual(pj["status"], "deleted")
        # Durable trash record should exist
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_trash_records WHERE profile_id = ? AND job_id = ? AND restored_at IS NULL",
                (pid, "job-trash-1"),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(dict(row)["origin_zone"], "discovery")

    def test_discovery_interested_bridges_to_profile_jobs(self):
        """T061: discovery interested -> profile_jobs.status='interested'."""
        pid, rid = _make_profile_and_resume(self.store)
        _insert_job(self.store, "job-int-1")
        self.store.create_discovery_feedback(
            profile_id=pid, target_type="job", action="interested",
            job_id="job-int-1", run_id="run-b",
        )
        pj = self.store.get_profile_job(pid, "job-int-1")
        self.assertEqual(pj["status"], "interested")

    def test_discovery_feedback_cross_run_visible(self):
        """T061: discovery feedback state is visible across runs."""
        pid, rid = _make_profile_and_resume(self.store)
        _insert_job(self.store, "job-cross-1")
        # Run A: mark not_interested
        self.store.create_discovery_feedback(
            profile_id=pid, target_type="job", action="not_interested",
            job_id="job-cross-1", run_id="run-a",
        )
        # Run B: should still see the trash state
        pj = self.store.get_profile_job(pid, "job-cross-1")
        self.assertEqual(pj["status"], "deleted")
        rejected = self.store.list_screening_rejected_job_ids(pid)
        self.assertIn("job-cross-1", rejected)
        # Mark interested in run B -> should flip state
        self.store.create_discovery_feedback(
            profile_id=pid, target_type="job", action="interested",
            job_id="job-cross-1", run_id="run-b",
        )
        pj = self.store.get_profile_job(pid, "job-cross-1")
        self.assertEqual(pj["status"], "interested")


# ---------------------------------------------------------------------------
# T082: Migration acceptance (schema-10 -> 13)
# ---------------------------------------------------------------------------


class MigrationAcceptanceTests(_StoreTestCase):
    def test_full_migration_creates_all_004_tables(self):
        for table in (
            "candidate_analyses", "resume_evidence", "career_directions", "direction_evidence",
            "direction_confirmations", "confirmation_directions",
            "discovery_runs", "discovery_run_events",
            "search_plans", "search_plan_items",
            "discovery_job_snapshots", "job_direction_assessments", "discovery_feedback",
        ):
            self.assertTrue(self._table_exists(table), f"missing table: {table}")

    def test_restart_marks_active_discovery_runs_interrupted(self):
        run = _make_confirmed_run(self.store)
        self.store.update_discovery_run(run["id"], status="fetching_lists", stage="fetching_lists")
        # Simulate restart by re-instantiating the store.
        store2 = TaskStore(self._tmp.name)
        fetched = store2.get_discovery_run(run["id"])
        self.assertEqual(fetched["status"], "interrupted")
        self.assertEqual(fetched["failure_code"], "restart")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
