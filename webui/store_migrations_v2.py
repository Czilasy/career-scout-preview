# -*- coding: utf-8 -*-

"""迁移 009-016（021 B7 T024 自 store_migrations.py 物理搬运）。"""

from __future__ import annotations

from webui.store_helpers import _now
from webui.store_migrations_v1 import _DDL_TEXT_DEFAULT_OBJECT, _SQL_SCREENING_RUN_COLUMNS

class StoreMigrationsV2Mixin:

    def _migration_009(self):
        """Add model column to ai_settings for user-selectable model."""
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_settings)")}
            if "model" not in columns:
                conn.execute("ALTER TABLE ai_settings ADD COLUMN model TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (9, ?, 'ai_settings model column')",
                (_now(),),
            )

    def _migration_010(self):
        """Persist the inputs needed to identify and diagnose screening work."""
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute(_SQL_SCREENING_RUN_COLUMNS)}
            additions = {
                "profile_id": "TEXT",
                "execution_params_json": _DDL_TEXT_DEFAULT_OBJECT,
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE screening_runs ADD COLUMN {name} {definition}")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (10, ?, 'screening execution inputs')",
                (_now(),),
            )

    def _migration_011(self):
        """004 migration: candidate analyses, evidence, directions and links."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_analyses (
                    id TEXT PRIMARY KEY,
                    resume_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    unknowns_json TEXT NOT NULL DEFAULT '[]',
                    model_name TEXT NOT NULL DEFAULT '',
                    contract_version TEXT NOT NULL DEFAULT 'v1',
                    failure_code TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE CASCADE,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    UNIQUE (resume_id, version)
                );

                CREATE TABLE IF NOT EXISTS resume_evidence (
                    id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    safe_excerpt TEXT NOT NULL DEFAULT '',
                    source_locator_json TEXT NOT NULL DEFAULT '{}',
                    assertion_type TEXT NOT NULL DEFAULT 'explicit',
                    confidence INTEGER NOT NULL DEFAULT 0,
                    sensitive INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (analysis_id) REFERENCES candidate_analyses(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS career_directions (
                    id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    direction_type TEXT NOT NULL,
                    rationale TEXT NOT NULL DEFAULT '',
                    gaps_json TEXT NOT NULL DEFAULT '[]',
                    confidence INTEGER NOT NULL DEFAULT 0,
                    default_enabled INTEGER NOT NULL DEFAULT 0,
                    search_terms_json TEXT NOT NULL DEFAULT '[]',
                    contract_version TEXT NOT NULL DEFAULT 'v1',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (analysis_id) REFERENCES candidate_analyses(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS direction_evidence (
                    direction_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'primary',
                    PRIMARY KEY (direction_id, evidence_id),
                    FOREIGN KEY (direction_id) REFERENCES career_directions(id) ON DELETE CASCADE,
                    FOREIGN KEY (evidence_id) REFERENCES resume_evidence(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (11, ?, '004 candidate analysis/evidence/directions')",
                (_now(),),
            )

    def _migration_012(self):
        """004 migration: confirmations, discovery runs, search plans."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS direction_confirmations (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    resume_id TEXT NOT NULL,
                    analysis_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    hard_constraints_json TEXT NOT NULL DEFAULT '{}',
                    soft_preferences_json TEXT NOT NULL DEFAULT '{}',
                    safe_limits_json TEXT NOT NULL DEFAULT '{}',
                    confirmed_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE CASCADE,
                    FOREIGN KEY (analysis_id) REFERENCES candidate_analyses(id) ON DELETE CASCADE,
                    UNIQUE (profile_id, version)
                );

                CREATE TABLE IF NOT EXISTS confirmation_directions (
                    confirmation_id TEXT NOT NULL,
                    direction_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    user_added INTEGER NOT NULL DEFAULT 0,
                    user_label TEXT,
                    PRIMARY KEY (confirmation_id, direction_id),
                    FOREIGN KEY (confirmation_id) REFERENCES direction_confirmations(id) ON DELETE CASCADE,
                    FOREIGN KEY (direction_id) REFERENCES career_directions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS discovery_runs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    resume_id TEXT NOT NULL,
                    analysis_id TEXT NOT NULL,
                    confirmation_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    stage TEXT NOT NULL DEFAULT 'created',
                    policy_version TEXT NOT NULL DEFAULT 'v1',
                    input_hash TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    detail_count INTEGER NOT NULL DEFAULT 0,
                    evaluated_count INTEGER NOT NULL DEFAULT 0,
                    high_count INTEGER NOT NULL DEFAULT 0,
                    adjacent_count INTEGER NOT NULL DEFAULT 0,
                    growth_count INTEGER NOT NULL DEFAULT 0,
                    review_count INTEGER NOT NULL DEFAULT 0,
                    unsuitable_count INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at TEXT,
                    failure_code TEXT,
                    failure_stage TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE CASCADE,
                    FOREIGN KEY (analysis_id) REFERENCES candidate_analyses(id) ON DELETE CASCADE,
                    FOREIGN KEY (confirmation_id) REFERENCES direction_confirmations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS discovery_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    safe_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE,
                    UNIQUE (run_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS search_plans (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    plan_version TEXT NOT NULL DEFAULT 'v1',
                    status TEXT NOT NULL DEFAULT 'draft',
                    item_count INTEGER NOT NULL DEFAULT 0,
                    detail_budget INTEGER NOT NULL DEFAULT 60,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE (run_id),
                    FOREIGN KEY (run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS search_plan_items (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    city TEXT NOT NULL DEFAULT '',
                    source_filters_json TEXT NOT NULL DEFAULT '{}',
                    direction_ids_json TEXT NOT NULL DEFAULT '[]',
                    input_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    page_cursor INTEGER NOT NULL DEFAULT 0,
                    target_pages INTEGER NOT NULL DEFAULT 1,
                    detail_budget INTEGER NOT NULL DEFAULT 0,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    failure_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (plan_id) REFERENCES search_plans(id) ON DELETE CASCADE,
                    UNIQUE (plan_id, input_hash)
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (12, ?, '004 confirmations/runs/plans')",
                (_now(),),
            )

    def _migration_013(self):
        """004 migration: job snapshots, per-direction assessments, feedback."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery_job_snapshots (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    salary TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    jd TEXT NOT NULL DEFAULT '',
                    company_json TEXT NOT NULL DEFAULT '{}',
                    completeness TEXT NOT NULL DEFAULT 'unavailable',
                    missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    source_status TEXT NOT NULL DEFAULT 'unknown',
                    content_hash TEXT NOT NULL DEFAULT '',
                    fetch_status TEXT NOT NULL DEFAULT 'queued',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    failure_code TEXT,
                    fetched_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                    UNIQUE (run_id, job_id)
                );

                CREATE TABLE IF NOT EXISTS job_direction_assessments (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    direction_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    hard_outcome TEXT NOT NULL DEFAULT 'unknown',
                    hard_checks_json TEXT NOT NULL DEFAULT '{}',
                    dimensions_json TEXT NOT NULL DEFAULT '{}',
                    match_score INTEGER,
                    confidence INTEGER,
                    category TEXT NOT NULL DEFAULT 'needs_review',
                    candidate_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    job_evidence_json TEXT NOT NULL DEFAULT '{}',
                    gaps_json TEXT NOT NULL DEFAULT '[]',
                    policy_version TEXT NOT NULL DEFAULT 'v1',
                    contract_version TEXT NOT NULL DEFAULT 'v1',
                    failure_code TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY (snapshot_id) REFERENCES discovery_job_snapshots(id) ON DELETE CASCADE,
                    FOREIGN KEY (direction_id) REFERENCES career_directions(id) ON DELETE CASCADE,
                    UNIQUE (run_id, snapshot_id, direction_id)
                );

                CREATE TABLE IF NOT EXISTS discovery_feedback (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    run_id TEXT,
                    job_id TEXT,
                    direction_id TEXT,
                    assessment_id TEXT,
                    target_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason_code TEXT,
                    scope TEXT NOT NULL DEFAULT 'exact_job',
                    safe_note TEXT,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (direction_id) REFERENCES career_directions(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (13, ?, '004 snapshots/assessments/feedback')",
                (_now(),),
            )

    def _migration_014(self):
        """Candidate v3 analysis lifecycle and safe quality warnings."""
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(candidate_analyses)")}
            additions = {
                "analysis_stage": "TEXT NOT NULL DEFAULT 'queued'",
                "quality_status": "TEXT NOT NULL DEFAULT 'complete'",
                "quality_warnings_json": "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE candidate_analyses ADD COLUMN {name} {definition}")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (14, ?, 'candidate v3 lifecycle and quality warnings')", (_now(),)
            )

    def _migration_015(self):
        """005 additive candidate-profile and durable discovery work units."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_profile_versions (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    resume_id TEXT NOT NULL,
                    analysis_id TEXT,
                    version INTEGER NOT NULL CHECK (version > 0),
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'confirmed', 'superseded', 'deleted')),
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    unknowns_json TEXT NOT NULL DEFAULT '[]',
                    contract_version TEXT NOT NULL DEFAULT 'candidate_profile_v1',
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    supersedes_version_id TEXT,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE CASCADE,
                    FOREIGN KEY (analysis_id) REFERENCES candidate_analyses(id) ON DELETE SET NULL,
                    FOREIGN KEY (supersedes_version_id) REFERENCES candidate_profile_versions(id) ON DELETE SET NULL,
                    UNIQUE (profile_id, version)
                );

                CREATE TABLE IF NOT EXISTS candidate_fact_items (
                    id TEXT PRIMARY KEY,
                    profile_version_id TEXT NOT NULL,
                    fact_type TEXT NOT NULL CHECK (
                        fact_type IN ('work', 'project', 'skill', 'industry', 'education',
                                      'achievement', 'seniority')
                    ),
                    stable_key TEXT NOT NULL,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    normalized_value TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL CHECK (
                        source_kind IN ('resume_explicit', 'resume_inferred',
                                        'user_added', 'user_corrected')
                    ),
                    assertion_type TEXT NOT NULL CHECK (assertion_type IN ('explicit', 'inferred')),
                    confidence INTEGER NOT NULL CHECK (
                        typeof(confidence) = 'integer' AND confidence BETWEEN 0 AND 100
                    ),
                    verification_status TEXT NOT NULL CHECK (
                        verification_status IN ('extracted', 'confirmed', 'corrected',
                                                'rejected', 'unknown')
                    ),
                    supersedes_fact_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (profile_version_id) REFERENCES candidate_profile_versions(id) ON DELETE CASCADE,
                    FOREIGN KEY (supersedes_fact_id) REFERENCES candidate_fact_items(id) ON DELETE SET NULL,
                    UNIQUE (profile_version_id, stable_key)
                );

                CREATE TABLE IF NOT EXISTS candidate_fact_evidence (
                    fact_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'primary' CHECK (role IN ('primary', 'supporting')),
                    PRIMARY KEY (fact_id, evidence_id),
                    FOREIGN KEY (fact_id) REFERENCES candidate_fact_items(id) ON DELETE CASCADE,
                    FOREIGN KEY (evidence_id) REFERENCES resume_evidence(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS discovery_run_candidates (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    direction_ids_json TEXT NOT NULL DEFAULT '[]',
                    search_terms_json TEXT NOT NULL DEFAULT '[]',
                    source_positions_json TEXT NOT NULL DEFAULT '[]',
                    list_fields_json TEXT NOT NULL DEFAULT '{}',
                    dedupe_key TEXT NOT NULL,
                    precheck_outcome TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (precheck_outcome IN ('pass', 'violation', 'unknown')),
                    precheck_json TEXT NOT NULL DEFAULT '{}',
                    priority_components_json TEXT NOT NULL DEFAULT '{}',
                    selection_decision TEXT NOT NULL DEFAULT 'pending'
                        CHECK (selection_decision IN ('pending', 'selected', 'deferred',
                                                     'excluded', 'blocked')),
                    selection_reason TEXT,
                    selection_rank INTEGER CHECK (selection_rank IS NULL OR selection_rank > 0),
                    state TEXT NOT NULL DEFAULT 'discovered' CHECK (
                        state IN ('discovered', 'prechecked_pass', 'prechecked_unknown',
                                  'excluded', 'selected', 'deferred', 'detail_fetching',
                                  'detail_reused', 'detail_ready', 'detail_failed', 'cancelled',
                                  'evaluating', 'recommended', 'needs_review', 'unsuitable',
                                  'evaluation_failed', 'reordered', 'withdrawn')
                    ),
                    snapshot_id TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                    failure_code TEXT,
                    input_hash TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    selected_at TEXT,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                    FOREIGN KEY (snapshot_id) REFERENCES discovery_job_snapshots(id) ON DELETE SET NULL,
                    UNIQUE (run_id, job_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_discovery_run_candidates_selected_rank
                    ON discovery_run_candidates(run_id, selection_rank)
                    WHERE selection_rank IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_candidate_profile_versions_owner
                    ON candidate_profile_versions(profile_id, status, version);
                CREATE INDEX IF NOT EXISTS idx_candidate_fact_items_version
                    ON candidate_fact_items(profile_version_id, verification_status);
                CREATE INDEX IF NOT EXISTS idx_discovery_run_candidates_state
                    ON discovery_run_candidates(run_id, selection_decision, state);

                CREATE TRIGGER IF NOT EXISTS candidate_profile_versions_lineage_insert
                BEFORE INSERT ON candidate_profile_versions
                WHEN NOT EXISTS (
                        SELECT 1 FROM resumes
                        WHERE id = NEW.resume_id AND profile_id = NEW.profile_id
                    )
                    OR (
                        NEW.analysis_id IS NOT NULL AND NOT EXISTS (
                            SELECT 1 FROM candidate_analyses
                            WHERE id = NEW.analysis_id
                              AND resume_id = NEW.resume_id
                              AND profile_id = NEW.profile_id
                        )
                    )
                BEGIN
                    SELECT RAISE(ABORT, 'candidate profile lineage mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_profile_versions_lineage_update
                BEFORE UPDATE OF profile_id, resume_id, analysis_id ON candidate_profile_versions
                WHEN NOT EXISTS (
                        SELECT 1 FROM resumes
                        WHERE id = NEW.resume_id AND profile_id = NEW.profile_id
                    )
                    OR (
                        NEW.analysis_id IS NOT NULL AND NOT EXISTS (
                            SELECT 1 FROM candidate_analyses
                            WHERE id = NEW.analysis_id
                              AND resume_id = NEW.resume_id
                              AND profile_id = NEW.profile_id
                        )
                    )
                BEGIN
                    SELECT RAISE(ABORT, 'candidate profile lineage mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_profile_versions_immutable
                BEFORE UPDATE ON candidate_profile_versions
                WHEN OLD.status IN ('confirmed', 'superseded')
                     AND NEW.status <> 'deleted'
                     AND (
                        NEW.profile_id IS NOT OLD.profile_id
                        OR NEW.resume_id IS NOT OLD.resume_id
                        OR NEW.analysis_id IS NOT OLD.analysis_id
                        OR NEW.version IS NOT OLD.version
                        OR NEW.summary_json IS NOT OLD.summary_json
                        OR NEW.unknowns_json IS NOT OLD.unknowns_json
                        OR NEW.contract_version IS NOT OLD.contract_version
                        OR NEW.content_hash IS NOT OLD.content_hash
                        OR NEW.supersedes_version_id IS NOT OLD.supersedes_version_id
                     )
                BEGIN
                    SELECT RAISE(ABORT, 'confirmed candidate profile is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_profile_versions_status_transition
                BEFORE UPDATE OF status ON candidate_profile_versions
                WHEN NEW.status <> OLD.status
                     AND NOT (
                        (OLD.status = 'draft' AND NEW.status IN ('confirmed', 'deleted'))
                        OR (OLD.status = 'confirmed' AND NEW.status IN ('superseded', 'deleted'))
                        OR (OLD.status = 'superseded' AND NEW.status = 'deleted')
                     )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid candidate profile status transition');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_items_insert_draft_only
                BEFORE INSERT ON candidate_fact_items
                WHEN NOT EXISTS (
                    SELECT 1 FROM candidate_profile_versions
                    WHERE id = NEW.profile_version_id AND status = 'draft'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'candidate facts require a draft profile');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_items_update_draft_only
                BEFORE UPDATE ON candidate_fact_items
                WHEN NOT EXISTS (
                    SELECT 1 FROM candidate_profile_versions
                    WHERE id = OLD.profile_version_id AND status = 'draft'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'confirmed candidate facts are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_items_delete_draft_only
                BEFORE DELETE ON candidate_fact_items
                WHEN EXISTS (
                    SELECT 1 FROM candidate_profile_versions
                    WHERE id = OLD.profile_version_id AND status <> 'draft'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'confirmed candidate facts are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_evidence_insert_draft_only
                BEFORE INSERT ON candidate_fact_evidence
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM candidate_fact_items AS fact
                    JOIN candidate_profile_versions AS version
                      ON version.id = fact.profile_version_id
                    WHERE fact.id = NEW.fact_id AND version.status = 'draft'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'candidate fact evidence requires a draft profile');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_evidence_update_draft_only
                BEFORE UPDATE ON candidate_fact_evidence
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM candidate_fact_items AS fact
                    JOIN candidate_profile_versions AS version
                      ON version.id = fact.profile_version_id
                    WHERE fact.id = OLD.fact_id AND version.status = 'draft'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'confirmed candidate fact evidence is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_evidence_delete_draft_only
                BEFORE DELETE ON candidate_fact_evidence
                WHEN EXISTS (
                    SELECT 1
                    FROM candidate_fact_items AS fact
                    JOIN candidate_profile_versions AS version
                      ON version.id = fact.profile_version_id
                    WHERE fact.id = OLD.fact_id AND version.status <> 'draft'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'confirmed candidate fact evidence is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_evidence_lineage_insert
                BEFORE INSERT ON candidate_fact_evidence
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM candidate_fact_items AS fact
                    JOIN candidate_profile_versions AS version
                      ON version.id = fact.profile_version_id
                    JOIN resume_evidence AS evidence
                      ON evidence.id = NEW.evidence_id
                    WHERE fact.id = NEW.fact_id
                      AND version.analysis_id = evidence.analysis_id
                      AND evidence.sensitive = 0
                )
                BEGIN
                    SELECT RAISE(ABORT, 'candidate fact evidence lineage mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS candidate_fact_evidence_lineage_update
                BEFORE UPDATE OF fact_id, evidence_id ON candidate_fact_evidence
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM candidate_fact_items AS fact
                    JOIN candidate_profile_versions AS version
                      ON version.id = fact.profile_version_id
                    JOIN resume_evidence AS evidence
                      ON evidence.id = NEW.evidence_id
                    WHERE fact.id = NEW.fact_id
                      AND version.analysis_id = evidence.analysis_id
                      AND evidence.sensitive = 0
                )
                BEGIN
                    SELECT RAISE(ABORT, 'candidate fact evidence lineage mismatch');
                END;

                CREATE TRIGGER IF NOT EXISTS discovery_run_candidates_input_hash_immutable
                BEFORE UPDATE OF input_hash ON discovery_run_candidates
                WHEN NEW.input_hash IS NOT OLD.input_hash
                BEGIN
                    SELECT RAISE(ABORT, 'run candidate input hash is immutable');
                END;
                """
            )

            additions = {
                "candidate_analyses": {
                    "provider_call_count": "INTEGER CHECK (provider_call_count IS NULL OR provider_call_count >= 0)",
                },
                "direction_confirmations": {
                    "candidate_profile_version_id": (
                        "TEXT REFERENCES candidate_profile_versions(id) ON DELETE RESTRICT"
                    ),
                    "intent_contract_version": "TEXT",
                    "intent_hash": "TEXT",
                },
                "discovery_runs": {
                    "candidate_profile_version_id": (
                        "TEXT REFERENCES candidate_profile_versions(id) ON DELETE RESTRICT"
                    ),
                    "list_candidate_count": "INTEGER CHECK (list_candidate_count IS NULL OR list_candidate_count >= 0)",
                    "detail_selected_count": "INTEGER CHECK (detail_selected_count IS NULL OR detail_selected_count >= 0)",
                    "detail_completed_count": "INTEGER CHECK (detail_completed_count IS NULL OR detail_completed_count >= 0)",
                    "assessment_completed_count": "INTEGER CHECK (assessment_completed_count IS NULL OR assessment_completed_count >= 0)",
                    "recommendation_count": "INTEGER CHECK (recommendation_count IS NULL OR recommendation_count >= 0)",
                    "detail_reused_count": "INTEGER CHECK (detail_reused_count IS NULL OR detail_reused_count >= 0)",
                    "ai_call_count": "INTEGER CHECK (ai_call_count IS NULL OR ai_call_count >= 0)",
                    "result_revision": "INTEGER CHECK (result_revision IS NULL OR result_revision >= 0)",
                    "first_result_at": "TEXT",
                    "first_batch_at": "TEXT",
                    "list_completed_at": "TEXT",
                    "processing_completed_at": "TEXT",
                },
                "discovery_job_snapshots": {
                    "run_candidate_id": (
                        "TEXT REFERENCES discovery_run_candidates(id) ON DELETE SET NULL"
                    ),
                    "reused_from_snapshot_id": (
                        "TEXT REFERENCES discovery_job_snapshots(id) ON DELETE SET NULL"
                    ),
                    "fresh_until": "TEXT",
                    "fetch_duration_ms": "INTEGER CHECK (fetch_duration_ms IS NULL OR fetch_duration_ms >= 0)",
                    "wait_duration_ms": "INTEGER CHECK (wait_duration_ms IS NULL OR wait_duration_ms >= 0)",
                    "fetch_policy_version": "TEXT",
                    "source_fetched_at": "TEXT",
                },
                "job_direction_assessments": {
                    "evaluation_group_id": "TEXT",
                    "input_hash": "TEXT",
                    "evaluation_duration_ms": "INTEGER CHECK (evaluation_duration_ms IS NULL OR evaluation_duration_ms >= 0)",
                    "ai_call_count": "INTEGER CHECK (ai_call_count IS NULL OR ai_call_count >= 0)",
                    "result_revision": "INTEGER CHECK (result_revision IS NULL OR result_revision >= 0)",
                },
            }
            for table, columns in additions.items():
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (15, ?, '005 candidate profiles and durable discovery candidates')",
                (_now(),),
            )

    def _migration_016(self):
        """009 code review: add performance indexes for cleanup and discovery queries."""
        with self._connection() as conn:
            # idx_jobs_expires_at: cleanup_expired_jobs JOIN jobs ON expires_at < cutoff
            # WHERE expires_at IS NOT NULL 用 partial 索引节省空间
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_expires_at "
                "ON jobs (expires_at) WHERE expires_at IS NOT NULL"
            )
            # idx_jobs_last_seen_at: 按 last_seen_at 排序的 latest 查询
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs (last_seen_at)"
            )
            # idx_discovery_job_snapshots_run_status: discovery_runner 按 (run_id, fetch_status) 查询
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_discovery_job_snapshots_run_status "
                "ON discovery_job_snapshots (run_id, fetch_status)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (16, ?, '009 performance indexes for cleanup and discovery')",
                (_now(),),
            )
