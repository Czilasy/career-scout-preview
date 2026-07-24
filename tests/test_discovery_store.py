"""Discovery store persistence tests (feature 004)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

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
    def test_schema_version_upgraded_to_15(self):
        # migration_016+ 可能继续升版本号，这里只断言 ≥15（migration_011 已生效）
        self.assertGreaterEqual(self._schema_version(), 15)

    def test_candidate_analyses_table_structure(self):
        self.assertTrue(self._table_exists("candidate_analyses"))
        cols = set(self._table_columns("candidate_analyses"))
        required = {
            "id", "resume_id", "profile_id", "version", "status",
            "summary_json", "unknowns_json", "model_name", "contract_version",
            "failure_code", "created_at", "completed_at",
            "analysis_stage", "quality_status", "quality_warnings_json",
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
        self.assertGreaterEqual(self._schema_version(), 15)

    def test_connections_use_wal_and_busy_timeout(self):
        with self.store._connection() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertGreaterEqual(busy_timeout, 10_000)

    def test_concurrent_initialization_is_serialized(self):
        barrier = threading.Barrier(2)
        errors = []

        def initialize():
            try:
                barrier.wait(timeout=2)
                TaskStore(self._tmp.name)
            except Exception as exc:  # captured for assertion in the test thread
                errors.append(exc)

        threads = [threading.Thread(target=initialize) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))

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


# ---------------------------------------------------------------------------
# T008-T010: Migration 015
# ---------------------------------------------------------------------------


class Migration015Tests(_StoreTestCase):
    def test_schema_14_upgrades_additively_and_preserves_v1_rows(self):
        legacy_path = self._tmp.name + ".v14"
        try:
            # Build a real schema-14 database by suppressing only migration 015+.
            # ``create=True`` keeps this RED test runnable before the method exists.
            # 注意：新增 migration（018…）时需同步加入屏蔽清单，否则"旧库"版本会漂。
            with mock.patch.object(TaskStore, "_migration_015", lambda _self: None, create=True), \
                 mock.patch.object(TaskStore, "_migration_016", lambda _self: None, create=True), \
                 mock.patch.object(TaskStore, "_migration_017", lambda _self: None, create=True):
                legacy = TaskStore(legacy_path)
            self.assertEqual(legacy.schema_version(), 14)

            run = _make_confirmed_run(legacy, input_hash="legacy-input-hash")
            with legacy._connection() as conn:
                conn.execute(
                    "UPDATE discovery_runs SET status='succeeded', stage='succeeded', "
                    "source_count=3, detail_count=1, evaluated_count=1 WHERE id=?",
                    (run["id"],),
                )
            _insert_job(legacy, "legacy-job")
            snapshot = legacy.save_job_snapshot(
                run_id=run["id"], job_id="legacy-job", source_url="https://x",
                title="旧岗位", company="旧公司", salary="20-30K", location="北京",
                tags="Python", jd="legacy jd", company_json={"stage": "A"},
                completeness="complete", missing_fields=[], source_status="active",
                content_hash="legacy-content", fetch_status="completed",
            )
            with legacy._connection() as conn:
                direction_id = conn.execute(
                    "SELECT direction_id FROM confirmation_directions "
                    "WHERE confirmation_id=?",
                    (run["confirmation_id"],),
                ).fetchone()["direction_id"]
            # create_assessment 会写入 migration 017 新增的 caveats_json 列，
            # schema-14 旧库没有该列；造"旧数据"必须用当时的表结构裸 INSERT。
            assessment_id = "legacy-assessment-1"
            with legacy._connection() as conn:
                conn.execute(
                    "INSERT INTO job_direction_assessments ("
                    "id, run_id, snapshot_id, direction_id, status, hard_outcome, "
                    "match_score, confidence, category, policy_version, contract_version, "
                    "created_at, updated_at, completed_at"
                    ") VALUES (?, ?, ?, ?, 'completed', 'pass', 88, 81, "
                    "'high_match', 'v1', 'v1', ?, ?, ?)",
                    (assessment_id, run["id"], snapshot["id"], direction_id,
                     "2026-01-01T00:00:00", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
                )
            assessment = {"id": assessment_id}

            with legacy._connection() as conn:
                before = {
                    "confirmations": conn.execute("SELECT COUNT(*) FROM direction_confirmations").fetchone()[0],
                    "runs": conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0],
                    "snapshots": conn.execute("SELECT COUNT(*) FROM discovery_job_snapshots").fetchone()[0],
                    "assessments": conn.execute("SELECT COUNT(*) FROM job_direction_assessments").fetchone()[0],
                    "run": tuple(conn.execute(
                        "SELECT policy_version, input_hash, source_count, detail_count, evaluated_count "
                        "FROM discovery_runs WHERE id=?", (run["id"],),
                    ).fetchone()),
                    "snapshot": tuple(conn.execute(
                        "SELECT title, company, content_hash FROM discovery_job_snapshots WHERE id=?",
                        (snapshot["id"],),
                    ).fetchone()),
                }

            upgraded = TaskStore(legacy_path)
            # 升级后 migration_015 已应用，migration_016 也可能已应用
            self.assertGreaterEqual(upgraded.schema_version(), 15)
            with upgraded._connection() as conn:
                after = {
                    "confirmations": conn.execute("SELECT COUNT(*) FROM direction_confirmations").fetchone()[0],
                    "runs": conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0],
                    "snapshots": conn.execute("SELECT COUNT(*) FROM discovery_job_snapshots").fetchone()[0],
                    "assessments": conn.execute("SELECT COUNT(*) FROM job_direction_assessments").fetchone()[0],
                    "run": tuple(conn.execute(
                        "SELECT policy_version, input_hash, source_count, detail_count, evaluated_count "
                        "FROM discovery_runs WHERE id=?", (run["id"],),
                    ).fetchone()),
                    "snapshot": tuple(conn.execute(
                        "SELECT title, company, content_hash FROM discovery_job_snapshots WHERE id=?",
                        (snapshot["id"],),
                    ).fetchone()),
                }
                legacy_v1_fields = conn.execute(
                    "SELECT candidate_profile_version_id, list_candidate_count, "
                    "detail_selected_count, detail_completed_count, assessment_completed_count, "
                    "recommendation_count, detail_reused_count, ai_call_count, result_revision, "
                    "first_result_at, first_batch_at, list_completed_at, processing_completed_at "
                    "FROM discovery_runs WHERE id=?",
                    (run["id"],),
                ).fetchone()
                legacy_confirmation_fields = conn.execute(
                    "SELECT candidate_profile_version_id, intent_contract_version, intent_hash "
                    "FROM direction_confirmations WHERE id=?",
                    (run["confirmation_id"],),
                ).fetchone()
                legacy_snapshot_fields = conn.execute(
                    "SELECT run_candidate_id, reused_from_snapshot_id, fresh_until, "
                    "fetch_duration_ms, wait_duration_ms, fetch_policy_version "
                    "FROM discovery_job_snapshots WHERE id=?",
                    (snapshot["id"],),
                ).fetchone()
                legacy_assessment_fields = conn.execute(
                    "SELECT evaluation_group_id, input_hash, evaluation_duration_ms, "
                    "ai_call_count, result_revision FROM job_direction_assessments WHERE id=?",
                    (assessment["id"],),
                ).fetchone()
            self.assertEqual(after, before)
            for row in (
                legacy_v1_fields,
                legacy_confirmation_fields,
                legacy_snapshot_fields,
                legacy_assessment_fields,
            ):
                self.assertTrue(all(value is None for value in row))
        finally:
            if os.path.exists(legacy_path):
                os.unlink(legacy_path)

    def test_migration_015_reopen_is_idempotent(self):
        TaskStore(self._tmp.name)
        TaskStore(self._tmp.name)
        with self.store._connection() as conn:
            applied = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=15"
            ).fetchone()[0]
        self.assertGreaterEqual(self._schema_version(), 15)
        self.assertEqual(applied, 1)

    def test_candidate_profile_tables_have_required_columns(self):
        expected = {
            "candidate_profile_versions": {
                "id", "profile_id", "resume_id", "analysis_id", "version", "status",
                "summary_json", "unknowns_json", "contract_version", "content_hash",
                "created_at", "updated_at", "confirmed_at", "supersedes_version_id",
            },
            "candidate_fact_items": {
                "id", "profile_version_id", "fact_type", "stable_key", "value_json",
                "normalized_value", "source_kind", "assertion_type", "confidence",
                "verification_status", "supersedes_fact_id", "created_at", "updated_at",
            },
            "candidate_fact_evidence": {"fact_id", "evidence_id", "role"},
        }
        for table, required in expected.items():
            self.assertTrue(self._table_exists(table), table)
            columns = set(self._table_columns(table))
            self.assertTrue(required.issubset(columns), f"{table} missing {required - columns}")

    def test_candidate_profile_version_foreign_keys_unique_status_and_immutability(self):
        pid, rid = _make_profile_and_resume(self.store)
        analysis = self.store.create_analysis(rid, pid)
        values = (pid, rid, analysis["id"], "2026-01-01T00:00:00Z")
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profile_versions "
                "(id, profile_id, resume_id, analysis_id, version, status, summary_json, "
                "unknowns_json, contract_version, content_hash, created_at, updated_at) "
                "VALUES ('cpv-1', ?, ?, ?, 1, 'draft', '{}', '[]', "
                "'candidate_profile_v1', 'hash-1', ?, ?)",
                values + (values[-1],),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO candidate_profile_versions "
                    "(id, profile_id, resume_id, analysis_id, version, status, summary_json, "
                    "unknowns_json, contract_version, content_hash, created_at, updated_at) "
                    "VALUES ('cpv-dup', ?, ?, ?, 1, 'draft', '{}', '[]', "
                    "'candidate_profile_v1', 'hash-dup', ?, ?)",
                    values + (values[-1],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO candidate_profile_versions "
                    "(id, profile_id, resume_id, analysis_id, version, status, summary_json, "
                    "unknowns_json, contract_version, content_hash, created_at, updated_at) "
                    "VALUES ('cpv-bad-status', ?, ?, ?, 2, 'ready', '{}', '[]', "
                    "'candidate_profile_v1', 'hash-bad', ?, ?)",
                    values + (values[-1],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO candidate_profile_versions "
                    "(id, profile_id, resume_id, analysis_id, version, status, summary_json, "
                    "unknowns_json, contract_version, content_hash, created_at, updated_at) "
                    "VALUES ('cpv-orphan', 'missing-profile', ?, ?, 2, 'draft', '{}', '[]', "
                    "'candidate_profile_v1', 'hash-orphan', ?, ?)",
                    (rid, analysis["id"], values[-1], values[-1]),
                )
            conn.execute(
                "UPDATE candidate_profile_versions SET status='confirmed', confirmed_at=? WHERE id='cpv-1'",
                (values[-1],),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE candidate_profile_versions SET summary_json='{\"changed\":true}' "
                    "WHERE id='cpv-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE candidate_profile_versions SET status='draft' WHERE id='cpv-1'"
                )

    def test_candidate_facts_enforce_types_lineage_and_confirmed_immutability(self):
        pid, rid = _make_profile_and_resume(self.store)
        analysis = self.store.create_analysis(rid, pid)
        other = self.store.create_analysis(rid, pid)
        evidence = self.store.add_evidence(
            analysis["id"], evidence_type="skill", normalized_value="Python",
            safe_excerpt="Python", source_locator={}, assertion_type="explicit",
            confidence=90, sensitive=False,
        )
        other_evidence = self.store.add_evidence(
            other["id"], evidence_type="skill", normalized_value="Java",
            safe_excerpt="Java", source_locator={}, assertion_type="explicit",
            confidence=90, sensitive=False,
        )
        ts = "2026-01-01T00:00:00Z"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profile_versions "
                "(id, profile_id, resume_id, analysis_id, version, status, summary_json, "
                "unknowns_json, contract_version, content_hash, created_at, updated_at) "
                "VALUES ('cpv-facts', ?, ?, ?, 1, 'draft', '{}', '[]', "
                "'candidate_profile_v1', 'facts-hash', ?, ?)",
                (pid, rid, analysis["id"], ts, ts),
            )
            conn.execute(
                "INSERT INTO candidate_fact_items "
                "(id, profile_version_id, fact_type, stable_key, value_json, normalized_value, "
                "source_kind, assertion_type, confidence, verification_status, created_at, updated_at) "
                "VALUES ('fact-1', 'cpv-facts', 'skill', 'skill:python', '{}', 'Python', "
                "'resume_explicit', 'explicit', 90, 'extracted', ?, ?)",
                (ts, ts),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO candidate_fact_items "
                    "(id, profile_version_id, fact_type, stable_key, value_json, normalized_value, "
                    "source_kind, assertion_type, confidence, verification_status, created_at, updated_at) "
                    "VALUES ('fact-dup', 'cpv-facts', 'skill', 'skill:python', '{}', 'Python', "
                    "'resume_explicit', 'explicit', 90, 'extracted', ?, ?)",
                    (ts, ts),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO candidate_fact_items "
                    "(id, profile_version_id, fact_type, stable_key, value_json, normalized_value, "
                    "source_kind, assertion_type, confidence, verification_status, created_at, updated_at) "
                    "VALUES ('fact-bad', 'cpv-facts', 'preference', 'bad', '{}', '', "
                    "'resume_explicit', 'explicit', 101, 'extracted', ?, ?)",
                    (ts, ts),
                )
            conn.execute(
                "INSERT INTO candidate_fact_evidence (fact_id, evidence_id, role) "
                "VALUES ('fact-1', ?, 'primary')",
                (evidence["id"],),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO candidate_fact_evidence (fact_id, evidence_id, role) "
                    "VALUES ('fact-1', ?, 'supporting')",
                    (other_evidence["id"],),
                )
            conn.execute(
                "UPDATE candidate_profile_versions SET status='confirmed', confirmed_at=? "
                "WHERE id='cpv-facts'",
                (ts,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE candidate_fact_items SET normalized_value='Go' WHERE id='fact-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "DELETE FROM candidate_fact_evidence WHERE fact_id='fact-1' "
                    "AND evidence_id=?",
                    (evidence["id"],),
                )

    def test_discovery_run_candidates_constraints(self):
        run = _make_confirmed_run(self.store)
        _insert_job(self.store, "candidate-job")
        ts = "2026-01-01T00:00:00Z"
        insert = (
            "INSERT INTO discovery_run_candidates "
            "(id, run_id, job_id, source_url, direction_ids_json, search_terms_json, "
            "source_positions_json, list_fields_json, dedupe_key, precheck_outcome, "
            "precheck_json, priority_components_json, selection_decision, selection_reason, "
            "selection_rank, state, attempt_count, input_hash, discovered_at, updated_at) "
            "VALUES (?, ?, ?, 'https://www.zhipin.com/job_detail/x.html', '[]', '[]', '[]', "
            "'{}', 'job-hash', ?, '{}', '{}', ?, NULL, ?, ?, 0, 'input-hash', ?, ?)"
        )
        with self.store._connection() as conn:
            conn.execute(insert, (
                "candidate-1", run["id"], "candidate-job", "pass", "selected", 1,
                "selected", ts, ts,
            ))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(insert, (
                    "candidate-dup", run["id"], "candidate-job", "pass", "deferred", None,
                    "deferred", ts, ts,
                ))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(insert, (
                    "candidate-bad", run["id"], "candidate-job", "maybe", "pending", None,
                    "invented", ts, ts,
                ))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE discovery_run_candidates SET input_hash='changed' WHERE id='candidate-1'")

    def test_migration_015_additive_columns_and_foreign_keys(self):
        expected = {
            "direction_confirmations": {
                "candidate_profile_version_id", "intent_contract_version", "intent_hash",
            },
            "discovery_runs": {
                "candidate_profile_version_id", "list_candidate_count", "detail_selected_count",
                "detail_completed_count", "assessment_completed_count", "recommendation_count",
                "detail_reused_count", "ai_call_count", "result_revision", "first_result_at",
                "first_batch_at", "list_completed_at", "processing_completed_at",
            },
            "discovery_job_snapshots": {
                "run_candidate_id", "reused_from_snapshot_id", "fresh_until",
                "fetch_duration_ms", "wait_duration_ms", "fetch_policy_version",
            },
            "job_direction_assessments": {
                "evaluation_group_id", "input_hash", "evaluation_duration_ms",
                "ai_call_count", "result_revision",
            },
        }
        for table, required in expected.items():
            columns = set(self._table_columns(table))
            self.assertTrue(required.issubset(columns), f"{table} missing {required - columns}")

        expected_fks = {
            ("direction_confirmations", "candidate_profile_version_id", "candidate_profile_versions"),
            ("discovery_runs", "candidate_profile_version_id", "candidate_profile_versions"),
            ("discovery_job_snapshots", "run_candidate_id", "discovery_run_candidates"),
            ("discovery_job_snapshots", "reused_from_snapshot_id", "discovery_job_snapshots"),
        }
        actual = set()
        with self.store._connection() as conn:
            for table, _, _ in expected_fks:
                for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
                    actual.add((table, row["from"], row["table"]))
        self.assertTrue(expected_fks.issubset(actual), f"missing foreign keys: {expected_fks - actual}")

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


class CandidateProfileVersionCrudV2Tests(_StoreTestCase):
    """T026/T027: draft edits, immutable confirmations and version copies."""

    def setUp(self):
        super().setUp()
        self.profile_id, self.resume_id = _make_profile_and_resume(
            self.store, "5年 Python 后端经验，主导订单服务重构。",
        )
        self.analysis = self.store.create_analysis(self.resume_id, self.profile_id, contract_version="v3")
        self.evidence = self.store.add_evidence(
            self.analysis["id"], evidence_type="skill", normalized_value="Python",
            safe_excerpt="Python 后端经验", source_locator={"start": 3, "end": 14},
            assertion_type="explicit", confidence=95, sensitive=False,
        )

    def _create_draft(self):
        return self.store.create_candidate_profile_version(
            profile_id=self.profile_id, resume_id=self.resume_id,
            analysis_id=self.analysis["id"],
            summary={"headline": "后端工程师"},
            unknowns=[{"field": "current_city", "message": "待确认"}],
            facts=[{
                "client_ref": "f1", "fact_type": "skill", "stable_key": "skill:python",
                "value": {"name": "Python"}, "normalized_value": "Python",
                "source_kind": "resume_explicit", "assertion_type": "explicit",
                "confidence": 95, "verification_status": "extracted",
                "evidence_ids": [self.evidence["id"]],
            }],
        )

    def test_create_and_read_draft_with_fact_evidence_and_stable_hash(self):
        draft = self._create_draft()
        self.assertEqual((draft["version"], draft["status"]), (1, "draft"))
        self.assertEqual(draft["summary"], {"headline": "后端工程师"})
        self.assertEqual(len(draft["facts"]), 1)
        self.assertEqual(draft["facts"][0]["evidence_ids"], [self.evidence["id"]])
        self.assertEqual(len(draft["content_hash"]), 64)
        self.assertEqual(
            self.store.get_candidate_profile_version(draft["id"])["content_hash"],
            draft["content_hash"],
        )

    def test_draft_correct_add_reject_and_user_value_wins(self):
        draft = self._create_draft()
        original = draft["facts"][0]
        updated = self.store.update_candidate_profile_draft(
            draft["id"], expected_content_hash=draft["content_hash"],
            operations=[
                {"op": "correct", "fact_id": original["id"], "value": {"name": "Go"}, "normalized_value": "Go"},
                {"op": "add", "fact_type": "industry", "value": {"name": "金融科技", "contexts": []}, "normalized_value": "金融科技"},
            ],
            unknown_resolutions=[{"field": "current_city", "value": "上海", "intent_only": True}],
        )
        active = [fact for fact in updated["facts"] if fact["verification_status"] != "rejected"]
        self.assertEqual({fact["normalized_value"] for fact in active}, {"Go", "金融科技"})
        corrected = next(fact for fact in active if fact["normalized_value"] == "Go")
        self.assertEqual((corrected["source_kind"], corrected["confidence"]), ("user_corrected", 100))
        self.assertEqual(corrected["supersedes_fact_id"], original["id"])
        self.assertEqual(updated["unknowns"], [{"field": "current_city", "value": "上海", "intent_only": True}])
        self.assertNotEqual(updated["content_hash"], draft["content_hash"])

        rejected = self.store.update_candidate_profile_draft(
            updated["id"], expected_content_hash=updated["content_hash"],
            operations=[{"op": "reject", "fact_id": corrected["id"]}],
        )
        self.assertEqual(
            next(f for f in rejected["facts"] if f["id"] == corrected["id"])["verification_status"],
            "rejected",
        )

    def test_stale_hash_and_editing_confirmed_version_are_rejected(self):
        draft = self._create_draft()
        with self.assertRaises(ValueError):
            self.store.update_candidate_profile_draft(
                draft["id"], expected_content_hash="stale",
                operations=[{"op": "reject", "fact_id": draft["facts"][0]["id"]}],
            )
        confirmed = self.store.confirm_candidate_profile_version(
            draft["id"], expected_content_hash=draft["content_hash"],
        )
        self.assertEqual(confirmed["status"], "confirmed")
        with self.assertRaises(ValueError):
            self.store.update_candidate_profile_draft(
                confirmed["id"], expected_content_hash=confirmed["content_hash"],
                operations=[],
            )

    def test_copy_confirmed_version_creates_independent_next_draft(self):
        first = self._create_draft()
        confirmed = self.store.confirm_candidate_profile_version(
            first["id"], expected_content_hash=first["content_hash"],
        )
        copied = self.store.copy_candidate_profile_draft(confirmed["id"])
        self.assertEqual((copied["version"], copied["status"]), (2, "draft"))
        self.assertEqual(copied["supersedes_version_id"], confirmed["id"])
        self.assertNotEqual(copied["facts"][0]["id"], confirmed["facts"][0]["id"])
        self.assertEqual(copied["facts"][0]["evidence_ids"], confirmed["facts"][0]["evidence_ids"])

    def test_tombstone_clears_candidate_content_but_keeps_safe_identity(self):
        draft = self._create_draft()
        deleted = self.store.tombstone_candidate_profile_version(draft["id"])
        self.assertEqual(deleted["status"], "deleted")
        self.assertEqual((deleted["summary"], deleted["unknowns"], deleted["facts"]), ({}, [], []))
        self.assertEqual(deleted["id"], draft["id"])


# ---------------------------------------------------------------------------
# T018: Analysis/Evidence/Direction/Confirmation CRUD
# ---------------------------------------------------------------------------


class AnalysisEvidenceDirectionCrudTests(_StoreTestCase):
    def test_v3_analysis_defaults_and_quality_warning_sanitization(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid, contract_version="v3")
        self.assertEqual(a["stage"], "queued")
        self.assertEqual(a["quality_status"], "complete")
        self.store.update_analysis_status(a["id"], "partial", stage="normalized",
            quality_status="partial", quality_warnings=[{"code": "x", "path": "summary"}, {"code": 1}, "bad"])
        got = self.store.get_analysis(a["id"])
        self.assertEqual(got["quality_warnings"], [{"code": "x", "path": "summary"}])

    def test_malformed_quality_warning_json_falls_back_to_empty(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        with self.store._connection() as conn:
            conn.execute("UPDATE candidate_analyses SET quality_warnings_json = ? WHERE id = ?", ("{bad", a["id"]))
        self.assertEqual(self.store.get_analysis(a["id"])["quality_warnings"], [])

    def test_conditional_update_rejects_late_worker(self):
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        self.store.update_analysis_status(a["id"], "failed", stage="failed")
        self.store.update_analysis_status(a["id"], "ready", stage="persisting",
            expected_statuses={"queued", "running"}, expected_stages={"queued", "running"})
        got = self.store.get_analysis(a["id"])
        self.assertEqual(got["status"], "failed")
        self.assertEqual(got["stage"], "failed")

    def test_analysis_claim_is_atomic_and_single_use(self):
        pid, rid = _make_profile_and_resume(self.store)
        analysis = self.store.create_analysis(rid, pid, contract_version="v3")
        self.assertTrue(self.store.claim_analysis(analysis["id"]))
        self.assertFalse(self.store.claim_analysis(analysis["id"]))
        claimed = self.store.get_analysis(analysis["id"])
        self.assertEqual((claimed["status"], claimed["stage"]), ("analyzing", "requesting"))

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
# T037: Run candidate upsert, canonical identity, cross-direction dedup
# ---------------------------------------------------------------------------


class RunCandidateUpsertTests(_StoreTestCase):
    """T037: list candidate upsert, canonical URL/job identity, cross-direction dedup and provenance merge."""

    def _setup_run_and_job(self, job_id="job-upsert"):
        run = _make_confirmed_run(self.store)
        _insert_job(self.store, job_id)
        return run

    def test_upsert_creates_new_candidate(self):
        run = self._setup_run_and_job()
        candidate = self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d1"], search_terms=["Python"],
            source_positions=[{"item": 0, "page": 1, "rank": 3}],
            list_fields={"title": "后端", "salary": "20-30K"},
            input_hash="hash-1",
        )
        self.assertIn("id", candidate)
        self.assertEqual(candidate["run_id"], run["id"])
        self.assertEqual(candidate["job_id"], "job-upsert")
        self.assertEqual(candidate["direction_ids"], ["d1"])
        self.assertEqual(candidate["search_terms"], ["Python"])
        self.assertEqual(candidate["state"], "discovered")
        self.assertEqual(candidate["selection_decision"], "pending")

    def test_upsert_same_run_and_job_merges_not_duplicates(self):
        run = self._setup_run_and_job()
        first = self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d1"], search_terms=["Python"],
            source_positions=[{"item": 0, "page": 1, "rank": 3}],
            list_fields={"title": "后端"},
            input_hash="hash-1",
        )
        second = self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d2"], search_terms=["Go"],
            source_positions=[{"item": 1, "page": 2, "rank": 1}],
            list_fields={"title": "后端开发"},
            input_hash="hash-1",
        )
        self.assertEqual(first["id"], second["id"])
        candidates = self.store.list_run_candidates(run["id"])
        self.assertEqual(len(candidates), 1)

    def test_cross_direction_dedup_merges_direction_ids(self):
        run = self._setup_run_and_job()
        self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d1"], search_terms=["Python"],
            source_positions=[], list_fields={},
            input_hash="hash-1",
        )
        merged = self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d2", "d3"], search_terms=["Go"],
            source_positions=[], list_fields={},
            input_hash="hash-1",
        )
        self.assertEqual(sorted(merged["direction_ids"]), ["d1", "d2", "d3"])

    def test_provenance_merging_accumulates_search_terms_and_positions(self):
        run = self._setup_run_and_job()
        self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d1"], search_terms=["Python"],
            source_positions=[{"item": 0, "page": 1, "rank": 3}],
            list_fields={"title": "后端"},
            input_hash="hash-1",
        )
        merged = self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d2"], search_terms=["Python", "Go"],
            source_positions=[{"item": 2, "page": 1, "rank": 5}],
            list_fields={"title": "后端", "salary": "25K"},
            input_hash="hash-1",
        )
        self.assertIn("Python", merged["search_terms"])
        self.assertIn("Go", merged["search_terms"])
        self.assertEqual(len(merged["source_positions"]), 2)

    def test_canonical_dedupe_key_derived_from_job_identity(self):
        run = self._setup_run_and_job()
        candidate = self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html?ka=track",
            direction_ids=["d1"], search_terms=["Python"],
            source_positions=[], list_fields={},
            input_hash="hash-1",
        )
        self.assertIn("dedupe_key", candidate)
        self.assertTrue(len(candidate["dedupe_key"]) > 0)

    def test_get_run_candidate_by_id(self):
        run = self._setup_run_and_job()
        created = self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-upsert",
            source_url="https://www.zhipin.com/job_detail/abc.html",
            direction_ids=["d1"], search_terms=["Python"],
            source_positions=[], list_fields={},
            input_hash="hash-1",
        )
        fetched = self.store.get_run_candidate(created["id"])
        self.assertEqual(fetched["id"], created["id"])
        self.assertEqual(fetched["job_id"], "job-upsert")

    def test_list_run_candidates_filters_by_run(self):
        run = self._setup_run_and_job("job-a")
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                "VALUES ('job-b', 'https://y', 'https://y', '前端', '公司B', '15k', '上海', 'jd', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-a",
            source_url="https://www.zhipin.com/job_detail/a.html",
            direction_ids=["d1"], search_terms=[], source_positions=[],
            list_fields={}, input_hash="hash-1",
        )
        self.store.upsert_run_candidate(
            run_id=run["id"], job_id="job-b",
            source_url="https://www.zhipin.com/job_detail/b.html",
            direction_ids=["d1"], search_terms=[], source_positions=[],
            list_fields={}, input_hash="hash-1",
        )
        candidates = self.store.list_run_candidates(run["id"])
        self.assertEqual(len(candidates), 2)
        other_run = _make_confirmed_run(self.store, input_hash="h2")
        self.assertEqual(len(self.store.list_run_candidates(other_run["id"])), 0)


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
# T086: Feedback CRUD scope/revoked_at/history-invariant verification (US5)
# ---------------------------------------------------------------------------


class FeedbackScopeAndHistoryInvariantsTests(_StoreTestCase):
    """T086 验证 US5 反馈 CRUD 的作用域默认值、维度覆盖和历史不变性。

    合同来源:
    - spec.md FR-050: 岗位和方向反馈必须作用于后续运行，不得改写历史画像、
      确认快照和评估事实。
    - spec.md US5 acceptance scenario 3: 判断错误反馈记录受影响维度，不得
      直接改写历史评分。
    - data-model.md L33: discovery_feedback 表继续保存岗位、方向、评估和约束反馈。
    """

    def test_default_scope_is_exact_job_when_unspecified(self):
        """spec.md US5 scenario 1: 单个岗位不感兴趣默认只排除该岗位。"""
        pid, _ = _make_profile_and_resume(self.store)
        fb = self.store.create_discovery_feedback(
            pid, "job", "not_interested", job_id="j1",
        )
        self.assertEqual(fb["scope"], "exact_job",
                         "T086: 岗位反馈默认 scope 必须为 exact_job")
        rows = self.store.list_discovery_feedback(pid)
        self.assertEqual(rows[0]["scope"], "exact_job")

    def test_direction_feedback_persists_direction_id(self):
        """spec.md US5 scenario 2: 关闭方向反馈必须记录 direction_id 维度。"""
        pid, rid = _make_profile_and_resume(self.store)
        a = self.store.create_analysis(rid, pid)
        d = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        fb = self.store.create_discovery_feedback(
            pid, "direction", "direction_disable",
            direction_id=d["id"], scope="exact_direction",
        )
        rows = self.store.list_discovery_feedback(pid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_type"], "direction")
        self.assertEqual(rows[0]["direction_id"], d["id"])
        self.assertEqual(rows[0]["scope"], "exact_direction")

    def test_assessment_judgment_error_feedback_persists_assessment_id(self):
        """spec.md US5 scenario 3: 判断错误反馈必须记录 assessment_id 维度。"""
        pid, _ = _make_profile_and_resume(self.store)
        fb = self.store.create_discovery_feedback(
            pid, "assessment", "judgment_error",
            assessment_id="a-1", reason_code="dimension_wrong",
            scope="exact_assessment",
        )
        rows = self.store.list_discovery_feedback(pid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_type"], "assessment")
        self.assertEqual(rows[0]["assessment_id"], "a-1")
        self.assertEqual(rows[0]["action"], "judgment_error")

    def test_revoke_sets_revoked_at_timestamp(self):
        """FR-051: 撤销反馈必须写入 revoked_at 时间戳。"""
        pid, _ = _make_profile_and_resume(self.store)
        fb = self.store.create_discovery_feedback(
            pid, "job", "not_interested", job_id="j1",
        )
        self.assertIsNone(self.store.list_discovery_feedback(pid)[0]["revoked_at"])
        self.store.revoke_discovery_feedback(fb["id"])
        row = self.store.list_discovery_feedback(pid)[0]
        self.assertIsNotNone(row["revoked_at"],
                             "T086: revoke 必须写入 revoked_at")

    def test_revoke_is_idempotent_for_already_revoked_feedback(self):
        """FR-051: 重复撤销不应报错或刷新 revoked_at。"""
        pid, _ = _make_profile_and_resume(self.store)
        fb = self.store.create_discovery_feedback(
            pid, "job", "not_interested", job_id="j1",
        )
        first = self.store.revoke_discovery_feedback(fb["id"])
        first_ts = first["revoked_at"]
        second = self.store.revoke_discovery_feedback(fb["id"])
        self.assertEqual(second["revoked_at"], first_ts,
                         "T086: 重复撤销必须保持原 revoked_at 不变")

    def test_revoke_unknown_feedback_raises_keyerror(self):
        pid, _ = _make_profile_and_resume(self.store)
        with self.assertRaises(KeyError):
            self.store.revoke_discovery_feedback("nonexistent-fb-id")

    def test_feedback_does_not_modify_historical_run_counters(self):
        """FR-050: 反馈不得改写历史 run 的计数器。"""
        run = _make_confirmed_run(self.store)
        pid = run["profile_id"]
        self.store.update_discovery_run(
            run["id"], status="succeeded", stage="assembling", started=True,
            counters={"high_count": 5, "adjacent_count": 3, "growth_count": 2,
                      "list_candidate_count": 50, "detail_completed_count": 15,
                      "assessment_completed_count": 15},
        )
        before = self.store.get_discovery_run(run["id"])
        # Submit feedback targeting this historical run.
        self.store.create_discovery_feedback(
            pid, "job", "not_interested", run_id=run["id"], job_id="j_hist",
            scope="exact_job",
        )
        after = self.store.get_discovery_run(run["id"])
        # All counters must remain unchanged.
        for key in ("high_count", "adjacent_count", "growth_count",
                    "list_candidate_count", "detail_completed_count",
                    "assessment_completed_count"):
            self.assertEqual(before.get(key), after.get(key),
                             f"T086/FR-050: 反馈不得改写历史 run 的 {key}")

    def test_feedback_does_not_modify_historical_snapshot(self):
        """FR-050: 反馈不得改写历史 snapshot 内容。"""
        run = _make_confirmed_run(self.store)
        pid = run["profile_id"]
        _insert_job(self.store, "snap-job-1")
        snap_id = "snap-" + run["id"]
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO discovery_job_snapshots "
                "(id, run_id, job_id, completeness, jd, fetched_at, updated_at) VALUES "
                "(?, ?, ?, 'complete', 'jd-text', '2026-01-01', '2026-01-01')",
                (snap_id, run["id"], "snap-job-1"),
            )
        # Submit feedback targeting this snapshot's job.
        self.store.create_discovery_feedback(
            pid, "job", "not_interested", run_id=run["id"], job_id="snap-job-1",
            scope="exact_job",
        )
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_job_snapshots WHERE id = ?",
                (snap_id,),
            ).fetchone()
        self.assertEqual(dict(row)["jd"], "jd-text",
                         "T086/FR-050: 反馈不得改写历史 snapshot 内容")
        self.assertEqual(dict(row)["completeness"], "complete")

    def test_feedback_does_not_modify_historical_assessment_scores(self):
        """FR-050 + US5 scenario 3: 判断错误反馈不得改写历史 assessment 分数。"""
        # _make_confirmed_run creates a real direction we can FK-reference.
        run = _make_confirmed_run(self.store)
        pid = run["profile_id"]
        # Look up the direction created by _make_confirmed_run.
        with self.store._connection() as conn:
            d_row = conn.execute(
                "SELECT id FROM career_directions WHERE analysis_id = ? LIMIT 1",
                (run["analysis_id"],),
            ).fetchone()
        direction_id = dict(d_row)["id"]
        _insert_job(self.store, "score-job-1")
        # Insert a historical snapshot + assessment with scores.
        snap_id = "snap-score-" + run["id"]
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO discovery_job_snapshots "
                "(id, run_id, job_id, completeness, jd, fetched_at, updated_at) VALUES "
                "(?, ?, ?, 'complete', 'jd', '2026-01-01', '2026-01-01')",
                (snap_id, run["id"], "score-job-1"),
            )
            conn.execute(
                "INSERT INTO job_direction_assessments "
                "(id, snapshot_id, direction_id, run_id, dimensions_json, match_score, "
                "confidence, gaps_json, contract_version, created_at, updated_at, status) "
                "VALUES (?, ?, ?, ?, ?, 85, 80, '[]', 'job_assessment_v2', '2026-01-01', '2026-01-01', 'completed')",
                ("a-score-1", snap_id, direction_id, run["id"],
                 '{"capability": {"score": 85}}'),
            )
        # Submit judgment_error feedback targeting this assessment.
        self.store.create_discovery_feedback(
            pid, "assessment", "judgment_error",
            run_id=run["id"], assessment_id="a-score-1",
            reason_code="dimension_wrong", scope="exact_assessment",
        )
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT * FROM job_direction_assessments WHERE id = ?",
                ("a-score-1",),
            ).fetchone()
        self.assertEqual(dict(row)["match_score"], 85,
                         "T086/FR-050/US5-3: 判断错误反馈不得改写历史 assessment 分数")
        self.assertEqual(dict(row)["status"], "completed")


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

    def test_feedback_for_unknown_job_does_not_create_fake_source(self):
        pid, _ = _make_profile_and_resume(self.store)
        self.store.create_discovery_feedback(
            profile_id=pid, target_type="job", action="interested",
            job_id="unknown-job", run_id="run-x",
        )
        with self.store._connection() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id='unknown-job'").fetchone()
        self.assertIsNone(job)
        self.assertEqual(len(self.store.list_discovery_feedback(pid)), 1)

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
