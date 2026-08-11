"""Versioned schema migrations for webui.TaskStore.

Kept as a mixin so the store class stays readable while migrations stay
independent and testable.  The class exposes the same methods as before;
only the implementation location changed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from webui.store_helpers import _CST, _now, _uuid

_DDL_TEXT_NOT_NULL = "TEXT NOT NULL"
_DDL_TEXT_DEFAULT_EMPTY = "TEXT NOT NULL DEFAULT ''"
_DDL_TEXT_DEFAULT_OBJECT = "TEXT NOT NULL DEFAULT '{}'"
_DDL_TEXT_DEFAULT_BOSS = "TEXT NOT NULL DEFAULT 'boss'"
_DDL_INTEGER_DEFAULT_0 = "INTEGER NOT NULL DEFAULT 0"
_SQL_SCREENING_RUN_COLUMNS = "PRAGMA table_info(screening_runs)"


class MigrationBackupError(RuntimeError):
    """Raised when pre-migration bootstrap backup or verification fails.

    TaskStore construction must abort when this is raised; the source database
    must not receive any v27 partial writes.
    """


class StoreMigrationsMixin:
    def _migrate(self):
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                )
                """
            )
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            current = int(row["v"] or 0)

        if current < 1:
            self._migration_001()
        if current < 2:
            self._migration_002()
        if current < 3:
            self._migration_003()
        if current < 4:
            self._migration_004()
        if current < 5:
            self._migration_005()
        if current < 6:
            self._migration_006()
        if current < 7:
            self._migration_007()
        if current < 8:
            self._migration_008()
        if current < 9:
            self._migration_009()
        if current < 10:
            self._migration_010()
        if current < 11:
            self._migration_011()
        if current < 12:
            self._migration_012()
        if current < 13:
            self._migration_013()
        if current < 14:
            self._migration_014()
        if current < 15:
            self._migration_015()
        if current < 16:
            self._migration_016()
        if current < 17:
            self._migration_017()
        if current < 18:
            self._migration_018()
        if current < 19:
            self._migration_019()
        if current < 20:
            self._migration_020()
        if current < 21:
            self._migration_021()
        if current < 22:
            self._migration_022()
        if current < 23:
            self._migration_023()
        if current < 24:
            self._migration_024()
        if current < 25:
            self._migration_025()
        if current < 26:
            self._migration_026()
        if current < 27:
            self._migration_027()
        if current < 28:
            self._migration_028()
        if current < 29:
            self._migration_029()
        if current < 30:
            self._migration_030()
        if current < 31:
            self._migration_031()
        # Always reconcile: copy old default profile if not yet in candidate_profiles
        self._copy_legacy_default_profile()

    def _mark_stale_runs_interrupted(self):
        """Reconcile run state on process restart.

        A process restart cannot resume an in-memory child process. Mark runs
        left in an active state as interrupted so the UI does not show a
        permanently "running" state.

        screening_runs 必须同时写 interruption_kind='process_restart'
        （data-model.md:114 / quickstart.md:173）：否则 _public_task_status
        会把 interrupted 映射成终态 cancelled、finish 接口因
        interruption_kind 不在 (process_restart, operator_stop) 而 409 拒绝，
        任务既不能 continue 也不能 finish，彻底卡死。
        """
        with self._connection() as conn:
            conn.execute(
                "UPDATE search_runs SET status = 'interrupted', error_code = 'restart', updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )
            conn.execute(
                "UPDATE screening_runs SET status = 'interrupted', error_code = 'restart', "
                "interruption_kind = 'process_restart', updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )

    def _migration_001(self):
        """First workbench migration: add all new tables."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    confirmed_fields_json TEXT NOT NULL DEFAULT '{}',
                    ai_preference_json TEXT NOT NULL DEFAULT '{}',
                    resume_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS resumes (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    original_filename TEXT,
                    format TEXT NOT NULL,
                    extracted_text TEXT,
                    content_hash TEXT,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ai_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    endpoint_url TEXT NOT NULL DEFAULT '',
                    credential_ref TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'unconfigured',
                    last_error_code TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_runs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    mode TEXT NOT NULL DEFAULT 'ai',
                    status TEXT NOT NULL DEFAULT 'queued',
                    total_detail_budget INTEGER NOT NULL DEFAULT 60,
                    discovered_count INTEGER NOT NULL DEFAULT 0,
                    completed_jd_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_queries (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    frozen_query_json TEXT NOT NULL DEFAULT '{}',
                    list_output_path TEXT,
                    detail_output_path TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    detail_budget INTEGER NOT NULL DEFAULT 0,
                    counts_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES search_runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL UNIQUE,
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    salary TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    jd TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT,
                    FOREIGN KEY (id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS profile_jobs (
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    first_run_id TEXT,
                    last_run_id TEXT,
                    ai_rank INTEGER,
                    shown_at TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    note TEXT,
                    applied_at TEXT,
                    PRIMARY KEY (profile_id, job_id),
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS feedback_events (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    run_id TEXT,
                    action TEXT NOT NULL,
                    reason TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS preference_versions (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    source_feedback_count INTEGER NOT NULL,
                    preference_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (1, ?, 'workbench base tables')",
                (_now(),),
            )

    def _migration_002(self):
        """Add removable, unconfirmed AI resume suggestions."""
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(resumes)")}
            if "suggestions_json" not in columns:
                conn.execute("ALTER TABLE resumes ADD COLUMN suggestions_json TEXT NOT NULL DEFAULT '{}'")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (2, ?, 'resume suggestions')",
                (_now(),),
            )
            # Mark unfinished search runs interrupted (like tasks)
            conn.execute(
                "UPDATE search_runs SET status = 'interrupted', error_code = 'restart' "
                "WHERE status IN ('queued', 'running')"
            )

    def _migration_003(self):
        """Store resumable, cursor-addressable search events."""
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_run_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, type TEXT NOT NULL, "
                "payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, "
                "FOREIGN KEY (run_id) REFERENCES search_runs(id) ON DELETE CASCADE)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (3, ?, 'search run events')",
                (_now(),),
            )

    def _migration_004(self):
        """Add screening_runs and screening_results tables (002 resume-driven filtering)."""
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_runs (
                    id TEXT PRIMARY KEY,
                    frozen_filters_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    match_count INTEGER NOT NULL DEFAULT 0,
                    mismatch_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_results (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, job_id),
                    FOREIGN KEY (run_id) REFERENCES screening_runs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (4, ?, 'screening runs and results')",
                (_now(),),
            )

    def _migration_005(self):
        """Add screening_pending_results for 003 FR-011~016.

        待核验区：未完成核验的岗位（AI 超时、AI 无效输出、核验异常）。
        记录失败阶段、是否可重试、尝试次数、最近失败时间、原所在区域。
        同一 (run_id, job_id) 只有一条 pending 记录，重试时更新 attempts 与 last_failed_at。
        """
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_pending_results (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    failure_stage TEXT NOT NULL,
                    retryable INTEGER NOT NULL DEFAULT 1,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    last_failed_at TEXT NOT NULL,
                    origin_zone TEXT NOT NULL DEFAULT 'match',
                    ai_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, job_id),
                    FOREIGN KEY (run_id) REFERENCES screening_runs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (5, ?, 'screening pending results')",
                (_now(),),
            )

    def _migration_006(self):
        """Add screening_trash_records and screening_cleanup_records for 003 FR-020~027.

        trash_records：垃圾桶带原区域记录，支持永久恢复（FR-020~023）。
        cleanup_records：30 天清理产生的可查询历史（FR-024~027）。
        """
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_trash_records (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    origin_zone TEXT NOT NULL,
                    run_id TEXT,
                    feedback_ref TEXT,
                    deleted_at TEXT NOT NULL,
                    restored_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(profile_id, job_id),
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_cleanup_records (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    pending_at_cleanup INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (6, ?, 'screening trash and cleanup records')",
                (_now(),),
            )

    def _migration_007(self):
        """Persist screening progress, pending counts and parse-failure summary."""
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute(_SQL_SCREENING_RUN_COLUMNS)}
            additions = {
                "resume_id": "TEXT",
                "pending_count": _DDL_INTEGER_DEFAULT_0,
                "processed_count": _DDL_INTEGER_DEFAULT_0,
                "source_cursor": _DDL_INTEGER_DEFAULT_0,
                "parse_failure_count": _DDL_INTEGER_DEFAULT_0,
                "parse_failures_json": _DDL_TEXT_DEFAULT_OBJECT,
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE screening_runs ADD COLUMN {name} {definition}")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (7, ?, 'screening progress and parse summary')",
                (_now(),),
            )

    def _migration_008(self):
        """Persist suspended pending metadata needed for atomic trash recovery."""
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute(
                "PRAGMA table_info(screening_trash_records)"
            )}
            additions = {
                "source_job_id": "TEXT",
                "pending_failure_stage": "TEXT",
                "pending_retryable": "INTEGER",
                "pending_attempts": "INTEGER",
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE screening_trash_records ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (8, ?, 'screening trash suspended pending metadata')",
                (_now(),),
            )

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

    def _migration_017(self):
        """Add caveats_json column to job_direction_assessments for soft-preference notes."""
        with self._connection() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(job_direction_assessments)")}
            if "caveats_json" not in cols:
                conn.execute(
                    "ALTER TABLE job_direction_assessments ADD COLUMN caveats_json TEXT"
                )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (17, ?, 'caveats_json column for soft-preference notes')",
                (_now(),),
            )

    def _migration_018(self):
        """Expand screening tables to store full pipeline results (replaces JSON files)."""
        with self._connection() as conn:
            # Expand screening_runs with pipeline-level metadata
            run_cols = {row["name"] for row in conn.execute(_SQL_SCREENING_RUN_COLUMNS)}
            run_additions = {
                "search_params_json": _DDL_TEXT_DEFAULT_OBJECT,
                "profile_summary": _DDL_TEXT_DEFAULT_EMPTY,
                "total_scraped": _DDL_INTEGER_DEFAULT_0,
                "total_kept": _DDL_INTEGER_DEFAULT_0,
                "total_dropped": _DDL_INTEGER_DEFAULT_0,
            }
            for name, definition in run_additions.items():
                if name not in run_cols:
                    conn.execute(f"ALTER TABLE screening_runs ADD COLUMN {name} {definition}")

            # Expand screening_results with full job data
            res_cols = {row["name"] for row in conn.execute("PRAGMA table_info(screening_results)")}
            res_additions = {
                "title": _DDL_TEXT_DEFAULT_EMPTY,
                "company": _DDL_TEXT_DEFAULT_EMPTY,
                "salary": _DDL_TEXT_DEFAULT_EMPTY,
                "location": _DDL_TEXT_DEFAULT_EMPTY,
                "tags": _DDL_TEXT_DEFAULT_EMPTY,
                "jd": _DDL_TEXT_DEFAULT_EMPTY,
                "source_url": _DDL_TEXT_DEFAULT_EMPTY,
                "verdict_reason": _DDL_TEXT_DEFAULT_EMPTY,
                "caveats_json": "TEXT NOT NULL DEFAULT '[]'",
                "is_dropped": _DDL_INTEGER_DEFAULT_0,
            }
            for name, definition in res_additions.items():
                if name not in res_cols:
                    conn.execute(f"ALTER TABLE screening_results ADD COLUMN {name} {definition}")

            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (18, ?, 'full pipeline result storage in screening tables')",
                (_now(),),
            )

    def _migration_019(self):
        """Add record_kind column to screening_runs.

        区分两种语义的行：
        - process_log（工作日记）：create_screening_run 写入，筛选过程中持续更新，
          含 status/processed_count/source_cursor 等过程字段。查"筛选跑到哪了"看这里。
        - result_snapshot（结果存档）：save_pipeline_result 写入，筛选完成时一次性
          写入全部结果，created=updated。查"最终判定结果"看这里。

        默认值 process_log 保持向后兼容（旧数据全是 process_log 语义）。
        历史数据回填：用启发式把已有的 result_snapshot 行标出来——
        created_at == updated_at 且 total_kept > 0 的行视为 result_snapshot。
        """
        with self._connection() as conn:
            cols = {row["name"] for row in conn.execute(_SQL_SCREENING_RUN_COLUMNS)}
            if "record_kind" not in cols:
                conn.execute(
                    "ALTER TABLE screening_runs ADD COLUMN record_kind TEXT NOT NULL DEFAULT 'process_log'"
                )
            # 历史数据回填：result_snapshot 的特征是 created_at == updated_at 且 total_kept > 0
            # （process_log 在筛选过程中 updated_at 会持续更新，绝不会与 created_at 相等；
            # result_snapshot 是一次性写入，两个时间戳必然相等）
            conn.execute(
                "UPDATE screening_runs SET record_kind = 'result_snapshot' "
                "WHERE record_kind = 'process_log' "
                "AND created_at = updated_at "
                "AND total_kept > 0"
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (19, ?, 'record_kind column to distinguish process_log vs result_snapshot')",
                (_now(),),
            )

    def _migration_020(self):
        """010 healthy-pipeline-recovery: 暂停状态持久化 + 断点 + 失败码分类。

        - screening_runs 加 current_stage / error_reason / backend_version（FR-005/FR-037/FR-039）
        - screening_results 加 failed_code / failed_stage / retryable / attempts（FR-040）
        - 新增 pipeline_checkpoints 表保存断点（FR-023）
        - screening_pending_results 已在 migration_005 建，本处不重建
        """
        with self._connection() as conn:
            run_cols = {row["name"] for row in conn.execute(_SQL_SCREENING_RUN_COLUMNS)}
            for name, definition in {
                "current_stage": "TEXT",
                "error_reason": "TEXT",
                "backend_version": "TEXT",
            }.items():
                if name not in run_cols:
                    conn.execute(f"ALTER TABLE screening_runs ADD COLUMN {name} {definition}")

            res_cols = {row["name"] for row in conn.execute("PRAGMA table_info(screening_results)")}
            for name, definition in {
                "failed_code": "TEXT",
                "failed_stage": "TEXT",
                "retryable": _DDL_INTEGER_DEFAULT_0,
                "attempts": _DDL_INTEGER_DEFAULT_0,
            }.items():
                if name not in res_cols:
                    conn.execute(f"ALTER TABLE screening_results ADD COLUMN {name} {definition}")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    completed_keys_json TEXT NOT NULL DEFAULT '[]',
                    saved_at TEXT NOT NULL,
                    UNIQUE(run_id, stage),
                    FOREIGN KEY (run_id) REFERENCES screening_runs(id) ON DELETE CASCADE
                )
                """
            )
            # screening_pending_results 也补 failed_code 字段（migration_005 没有这列）
            pend_cols = {row["name"] for row in conn.execute(
                "PRAGMA table_info(screening_pending_results)"
            )}
            if "failed_code" not in pend_cols:
                conn.execute(
                    "ALTER TABLE screening_pending_results ADD COLUMN failed_code TEXT"
                )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (20, ?, 'healthy pipeline: stage/error_reason/backend_version + checkpoints + failed_code')"
                ,
                (_now(),),
            )

    def _migration_021(self):
        """Persist each completed scrape combination with its checkpoint."""
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scrape_run_jobs (
                    run_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    combo_key TEXT NOT NULL,
                    job_payload_json TEXT NOT NULL,
                    scraped_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, job_id),
                    FOREIGN KEY (run_id) REFERENCES screening_runs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (21, ?, 'persist scrape jobs and combo checkpoints atomically')",
                (_now(),),
            )

    def _migration_022(self):
        """Recovery audit state machine and global maintenance lock."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recovery_audit (
                    id TEXT PRIMARY KEY,
                    recovery_key TEXT NOT NULL UNIQUE,
                    backup_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tx_committed INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS recovery_lock (
                    lock_id INTEGER PRIMARY KEY CHECK (lock_id = 1),
                    owner_token TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    maintenance INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (22, ?, 'recovery audit state machine and maintenance lock')",
                (_now(),),
            )

    def _migration_023(self):
        """SPEC011: advanced_config_state + mode_config_versions tables."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS advanced_config_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    active_selection TEXT NOT NULL DEFAULT 'custom',
                    active_mode_version_id TEXT,
                    last_custom_config_json TEXT,
                    last_custom_digest TEXT,
                    legacy_imported_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mode_config_versions (
                    id TEXT PRIMARY KEY,
                    source_experiment_id TEXT,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    matrix_json TEXT NOT NULL,
                    manual_ranges_json TEXT NOT NULL DEFAULT '{}',
                    version_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                );
                INSERT OR IGNORE INTO advanced_config_state (id, active_selection, updated_at)
                VALUES (1, 'custom', 'epoch');
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (23, ?, 'SPEC011 advanced config state and mode versions')",
                (_now(),),
            )

    def _migration_024(self):
        """SPEC011: tuning experiment entity tables (data-model.md section 2)."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tuning_experiments (
                    id TEXT PRIMARY KEY,
                    spec_version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    input_version_id TEXT,
                    quality_reference_id TEXT,
                    baseline_config_json TEXT,
                    baseline_config_digest TEXT,
                    current_stage TEXT,
                    current_candidate_id TEXT,
                    estimated_remaining_seconds INTEGER,
                    blocked_code TEXT,
                    blocked_reason TEXT,
                    source_scope_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS tuning_input_versions (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    scope_digest TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES tuning_experiments(id)
                );

                CREATE TABLE IF NOT EXISTS tuning_workloads (
                    id TEXT PRIMARY KEY,
                    input_version_id TEXT NOT NULL,
                    task_size TEXT NOT NULL,
                    structure_index INTEGER NOT NULL,
                    frozen_scope_json TEXT NOT NULL,
                    planned_pages INTEGER NOT NULL,
                    expected_raw_jobs INTEGER,
                    artifact_manifest_json TEXT,
                    artifact_digest TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    FOREIGN KEY (input_version_id) REFERENCES tuning_input_versions(id),
                    UNIQUE (input_version_id, task_size, structure_index)
                );

                CREATE TABLE IF NOT EXISTS tuning_quality_references (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    input_version_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'building',
                    item_results_json TEXT,
                    variation_summary_json TEXT,
                    reviewed_item_ids_json TEXT,
                    reference_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    FOREIGN KEY (experiment_id) REFERENCES tuning_experiments(id)
                );

                CREATE TABLE IF NOT EXISTS tuning_candidates (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    strategy_step TEXT NOT NULL,
                    parent_candidate_id TEXT,
                    config_json TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    pressure_rank INTEGER NOT NULL DEFAULT 0,
                    promotion_reason TEXT,
                    rejection_code TEXT,
                    aggregate_metrics_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES tuning_experiments(id)
                );

                CREATE TABLE IF NOT EXISTS tuning_rounds (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    workload_id TEXT NOT NULL,
                    quality_reference_id TEXT,
                    round_kind TEXT NOT NULL,
                    repetition_index INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned',
                    manifest_id TEXT,
                    source_run_id TEXT,
                    metrics_json TEXT,
                    evidence_manifest_json TEXT,
                    failure_code TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES tuning_experiments(id),
                    FOREIGN KEY (candidate_id) REFERENCES tuning_candidates(id),
                    UNIQUE (candidate_id, workload_id, round_kind, repetition_index)
                );

                CREATE TABLE IF NOT EXISTS tuning_task_manifests (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    round_id TEXT NOT NULL,
                    manifest_version INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    rendered_task_path TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    issued_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES tuning_experiments(id),
                    FOREIGN KEY (round_id) REFERENCES tuning_rounds(id)
                );

                CREATE TABLE IF NOT EXISTS tuning_executor_reports (
                    id TEXT PRIMARY KEY,
                    manifest_id TEXT NOT NULL UNIQUE,
                    report_version INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    reported_manifest_digest TEXT NOT NULL,
                    evidence_digest TEXT NOT NULL,
                    validation_status TEXT NOT NULL DEFAULT 'pending',
                    validation_errors_json TEXT,
                    created_at TEXT NOT NULL,
                    validated_at TEXT,
                    FOREIGN KEY (manifest_id) REFERENCES tuning_task_manifests(id)
                );

                CREATE TABLE IF NOT EXISTS tuning_measurement_events (
                    round_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    stage TEXT,
                    started_monotonic_ms INTEGER,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    counts_json TEXT,
                    error_code TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (round_id, seq),
                    FOREIGN KEY (round_id) REFERENCES tuning_rounds(id)
                );

                CREATE TABLE IF NOT EXISTS tuning_execution_lease (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    owner_experiment_id TEXT,
                    owner_round_id TEXT,
                    owner_token_digest TEXT,
                    lease_until TEXT,
                    heartbeat_at TEXT,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO tuning_execution_lease (id, updated_at)
                VALUES (1, 'epoch');
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (24, ?, 'SPEC011 tuning experiment entity tables')",
                (_now(),),
            )

    def _migration_025(self):
        """SPEC011 real-chain: frozen quality context and append-only stage artifacts."""
        with self._connection() as conn:
            columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(tuning_input_versions)"
                ).fetchall()
            }
            if "quality_context_json" not in columns:
                conn.execute(
                    "ALTER TABLE tuning_input_versions "
                    "ADD COLUMN quality_context_json TEXT"
                )
            if "quality_context_digest" not in columns:
                conn.execute(
                    "ALTER TABLE tuning_input_versions "
                    "ADD COLUMN quality_context_digest TEXT"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tuning_stage_artifacts (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    input_version_id TEXT NOT NULL,
                    workload_id TEXT NOT NULL,
                    producer_round_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    source_artifact_id TEXT,
                    artifact_path TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ready',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES tuning_experiments(id),
                    FOREIGN KEY (input_version_id) REFERENCES tuning_input_versions(id),
                    FOREIGN KEY (workload_id) REFERENCES tuning_workloads(id),
                    FOREIGN KEY (producer_round_id) REFERENCES tuning_rounds(id),
                    FOREIGN KEY (source_artifact_id) REFERENCES tuning_stage_artifacts(id),
                    UNIQUE (producer_round_id, stage)
                );
                CREATE INDEX IF NOT EXISTS idx_tuning_stage_artifacts_workload
                    ON tuning_stage_artifacts(workload_id, stage, created_at);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, applied_at, description) VALUES "
                "(25, ?, 'SPEC011 quality context and stage artifacts')",
                (_now(),),
            )

    def _migration_026(self):
        """Persist real screening start/finish timestamps and backfill history."""
        with self._connection() as conn:
            columns = {
                row["name"] for row in conn.execute(
                    _SQL_SCREENING_RUN_COLUMNS
                ).fetchall()
            }
            if "started_at" not in columns:
                conn.execute(
                    "ALTER TABLE screening_runs ADD COLUMN started_at TEXT"
                )
            if "finished_at" not in columns:
                conn.execute(
                    "ALTER TABLE screening_runs ADD COLUMN finished_at TEXT"
                )
            conn.execute(
                "UPDATE screening_runs SET started_at = created_at, finished_at = updated_at "
                "WHERE record_kind = 'process_log' AND started_at IS NULL"
            )
            process_logs = [
                dict(row) for row in conn.execute(
                    "SELECT id, created_at, updated_at FROM screening_runs "
                    "WHERE record_kind = 'process_log'"
                ).fetchall()
            ]
            snapshots = [
                dict(row) for row in conn.execute(
                    "SELECT id, created_at, updated_at FROM screening_runs "
                    "WHERE record_kind = 'result_snapshot' AND started_at IS NULL"
                ).fetchall()
            ]
            candidates = []
            for row in process_logs:
                try:
                    started = datetime.fromisoformat(str(row["created_at"]))
                    finished = datetime.fromisoformat(
                        str(row["updated_at"] or row["created_at"])
                    )
                except (TypeError, ValueError):
                    continue
                if started.tzinfo is None:
                    started = started.replace(tzinfo=_CST)
                if finished.tzinfo is None:
                    finished = finished.replace(tzinfo=_CST)
                candidates.append((row["id"], started, finished))
            for row in snapshots:
                try:
                    snapshot_at = datetime.fromisoformat(str(row["created_at"]))
                except (TypeError, ValueError):
                    continue
                if snapshot_at.tzinfo is None:
                    snapshot_at = snapshot_at.replace(tzinfo=_CST)
                best = None
                for _run_id, started, _finished in candidates:
                    if started <= snapshot_at and (
                        best is None or started > best[1]
                    ):
                        best = (_run_id, started, _finished)
                if best is not None:
                    conn.execute(
                        "UPDATE screening_runs SET started_at = ?, finished_at = ? "
                        "WHERE id = ?",
                        (best[1].isoformat(), snapshot_at.isoformat(), row["id"]),
                    )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, applied_at, description) VALUES "
                "(26, ?, 'screening run start/finish timestamps')",
                (_now(),),
            )


    def _migration_027(self):
        """Migration 27: 平台字段、双身份、筛选快照、source attempt（tasks002 T106-T110）。

        单事务原子操作：
        1. jobs 新增 platform、platform_job_id、experience、degree、extra_json
        2. screening_runs 新增 platform、filter_schema_version、filter_snapshot_json、
           task_input_digest、interruption_kind
        3. screening_results 新增 platform、platform_job_id、内部 job_id 可空、
           experience、degree、extra_json
        4. screening_pending_results 新增 platform，job_id 重命名为 platform_job_id
        5. scrape_run_jobs 的 job_id 重命名为 platform_job_id
        6. tuning_experiments/tuning_task_manifests 新增 platform
        7. tuning_stage_artifacts 新增 platform、source_artifact_kind、scope_digest、task_input_digest
        8. 创建 screening_source_attempts 追加表
        9. 存量记录回填 platform='boss'
        10. 创建 (platform, platform_job_id) 部分唯一索引
        11. 外键、重复身份、URL 归属、收藏/反馈计数、调优摘要守恒检查

        任一检查失败整笔回滚。
        """
        with self._connection() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            # --------------------------------------------------------------
            # 1. jobs 新增列
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "jobs", "platform", _DDL_TEXT_DEFAULT_BOSS
            )
            self._add_column_if_missing(
                conn, "jobs", "platform_job_id", "TEXT"
            )
            self._add_column_if_missing(
                conn, "jobs", "experience", _DDL_TEXT_DEFAULT_EMPTY
            )
            self._add_column_if_missing(
                conn, "jobs", "degree", _DDL_TEXT_DEFAULT_EMPTY
            )
            self._add_column_if_missing(
                conn, "jobs", "extra_json", _DDL_TEXT_DEFAULT_OBJECT
            )

            # --------------------------------------------------------------
            # 2. screening_runs 新增列
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "screening_runs", "platform", _DDL_TEXT_DEFAULT_BOSS
            )
            self._add_column_if_missing(
                conn, "screening_runs", "filter_schema_version", "INTEGER"
            )
            self._add_column_if_missing(
                conn, "screening_runs", "filter_snapshot_json", "TEXT"
            )
            self._add_column_if_missing(
                conn, "screening_runs", "task_input_digest", "TEXT"
            )
            self._add_column_if_missing(
                conn, "screening_runs", "interruption_kind", "TEXT"
            )

            # --------------------------------------------------------------
            # 3. screening_results: 旧 job_id（语义=平台原始ID）重命名为
            #    platform_job_id，再新增可空内部 job_id（语义=内部UUID）
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "screening_results", "platform", _DDL_TEXT_DEFAULT_BOSS
            )
            # 旧 job_id 列语义=平台原始ID，按 data-model.md 重命名为 platform_job_id
            self._rename_column_with_data(
                conn, "screening_results", "job_id", "platform_job_id",
                old_type=_DDL_TEXT_NOT_NULL, new_type=_DDL_TEXT_NOT_NULL,
            )
            # 新增可空内部 job_id（内部UUID语义，落库前可空）
            self._add_column_if_missing(
                conn, "screening_results", "job_id", "TEXT"
            )
            self._add_column_if_missing(
                conn, "screening_results", "experience", _DDL_TEXT_DEFAULT_EMPTY
            )
            self._add_column_if_missing(
                conn, "screening_results", "degree", _DDL_TEXT_DEFAULT_EMPTY
            )
            self._add_column_if_missing(
                conn, "screening_results", "extra_json", _DDL_TEXT_DEFAULT_OBJECT
            )

            # --------------------------------------------------------------
            # 4. screening_pending_results: job_id → platform_job_id
            # --------------------------------------------------------------
            self._rename_column_with_data(
                conn, "screening_pending_results", "job_id", "platform_job_id",
                old_type=_DDL_TEXT_NOT_NULL, new_type=_DDL_TEXT_NOT_NULL,
            )
            self._add_column_if_missing(
                conn, "screening_pending_results", "platform", _DDL_TEXT_DEFAULT_BOSS
            )

            # --------------------------------------------------------------
            # 5. scrape_run_jobs: job_id → platform_job_id
            # --------------------------------------------------------------
            self._rename_column_with_data(
                conn, "scrape_run_jobs", "job_id", "platform_job_id",
                old_type=_DDL_TEXT_NOT_NULL, new_type=_DDL_TEXT_NOT_NULL,
            )

            # --------------------------------------------------------------
            # 6. tuning_experiments / tuning_task_manifests 新增 platform
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "tuning_experiments", "platform", _DDL_TEXT_DEFAULT_BOSS
            )
            self._add_column_if_missing(
                conn, "tuning_task_manifests", "platform", _DDL_TEXT_DEFAULT_BOSS
            )

            # --------------------------------------------------------------
            # 7. tuning_stage_artifacts 新增外层列
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "tuning_stage_artifacts", "platform", _DDL_TEXT_DEFAULT_BOSS
            )
            self._add_column_if_missing(
                conn, "tuning_stage_artifacts", "source_artifact_kind", "TEXT"
            )
            self._add_column_if_missing(
                conn, "tuning_stage_artifacts", "scope_digest", "TEXT"
            )
            self._add_column_if_missing(
                conn, "tuning_stage_artifacts", "task_input_digest", "TEXT"
            )

            # --------------------------------------------------------------
            # 8. 创建 screening_source_attempts 追加表
            # --------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_source_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    combo_key TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    input_hash TEXT,
                    outcome_kind TEXT NOT NULL,
                    job_count INTEGER NOT NULL DEFAULT 0,
                    empty_evidence_json TEXT,
                    error_code TEXT,
                    error_reason TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, combo_key, attempt_no),
                    FOREIGN KEY (run_id) REFERENCES screening_runs(id) ON DELETE CASCADE
                )
                """
            )

            # --------------------------------------------------------------
            # 9. 存量记录回填 platform='boss'（DEFAULT 'boss' 已覆盖新插入，
            #    但 ALTER TABLE ADD COLUMN ... DEFAULT 对旧行也生效；
            #    显式 UPDATE 确保 NOT NULL 约束满足）
            # --------------------------------------------------------------
            for table in (
                "jobs", "screening_runs", "screening_results",
                "screening_pending_results", "tuning_experiments",
                "tuning_task_manifests", "tuning_stage_artifacts",
            ):
                conn.execute(
                    f"UPDATE {table} SET platform = 'boss' WHERE platform IS NULL OR platform = ''"
                )
            # --------------------------------------------------------------
            # 10. (platform, platform_job_id) 部分唯一索引
            # --------------------------------------------------------------
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_platform_job_id "
                "ON jobs(platform, platform_job_id) WHERE platform_job_id IS NOT NULL"
            )

            # --------------------------------------------------------------
            # 11. 守恒检查（失败整笔回滚）
            # --------------------------------------------------------------
            # 外键检查
            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_errors:
                raise RuntimeError(f"migration_27 foreign_key_check failed: {fk_errors}")

            # 重复身份检查：同一 (platform, platform_job_id) 不得有多行
            dup = conn.execute(
                "SELECT platform, platform_job_id, COUNT(*) AS c FROM jobs "
                "WHERE platform_job_id IS NOT NULL "
                "GROUP BY platform, platform_job_id HAVING c > 1"
            ).fetchall()
            if dup:
                raise RuntimeError(f"migration_27 duplicate platform_job_id: {dup}")

            # URL 归属检查：canonical_url 全局唯一（表定义已含 UNIQUE，此处复核）
            url_dup = conn.execute(
                "SELECT canonical_url, COUNT(*) AS c FROM jobs "
                "GROUP BY canonical_url HAVING c > 1"
            ).fetchall()
            if url_dup:
                raise RuntimeError(f"migration_27 duplicate canonical_url: {url_dup}")

            # 收藏/反馈计数守恒：profile_jobs/feedback_events 的 job_id 必须在 jobs.id 中存在
            orphan_pj = conn.execute(
                "SELECT COUNT(*) FROM profile_jobs pj "
                "LEFT JOIN jobs j ON pj.job_id = j.id WHERE j.id IS NULL"
            ).fetchone()
            if orphan_pj and orphan_pj[0] > 0:
                raise RuntimeError(
                    f"migration_27 orphan profile_jobs: {orphan_pj[0]}"
                )
            orphan_fb = conn.execute(
                "SELECT COUNT(*) FROM feedback_events fe "
                "LEFT JOIN jobs j ON fe.job_id = j.id WHERE j.id IS NULL"
            ).fetchone()
            if orphan_fb and orphan_fb[0] > 0:
                raise RuntimeError(
                    f"migration_27 orphan feedback_events: {orphan_fb[0]}"
                )

            # 调优摘要守恒：回填 platform 后行数不得变化（回填只更新不删除）
            # tuning_experiments / tuning_task_manifests / tuning_stage_artifacts
            # 的 platform 列必须全部为 'boss'（存量只有 BOSS）
            for tune_table in (
                "tuning_experiments", "tuning_task_manifests", "tuning_stage_artifacts",
            ):
                non_boss = conn.execute(
                    f"SELECT COUNT(*) FROM {tune_table} WHERE platform != 'boss'"
                ).fetchone()
                if non_boss and non_boss[0] > 0:
                    raise RuntimeError(
                        f"migration_27 {tune_table} has non-boss rows: {non_boss[0]}"
                    )
                # 行数守恒：platform 列不得为空（NOT NULL 约束已保证，复核）
                null_platform = conn.execute(
                    f"SELECT COUNT(*) FROM {tune_table} WHERE platform IS NULL OR platform = ''"
                ).fetchone()
                if null_platform and null_platform[0] > 0:
                    raise RuntimeError(
                        f"migration_27 {tune_table} has null platform: {null_platform[0]}"
                    )

            # 记录 migration
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (27, ?, 'platform fields, dual identity, filter snapshot, source attempts')",
                (_now(),),
            )

    def _migration_028(self):
        """Migration 28: lifecycle timestamps, append-only events and receipts.

        This migration is additive. Existing profile-job and preference rows are
        preserved byte-for-byte at the application level; no historical lifecycle
        facts are inferred from them.
        """
        with self._connection() as conn:
            # DDL is transactional in SQLite only after an explicit BEGIN.
            # Start before the first ALTER so any migration failure restores v27.
            conn.execute("BEGIN IMMEDIATE")
            profile_job_count = conn.execute(
                "SELECT COUNT(*) AS c FROM profile_jobs"
            ).fetchone()["c"]
            feedback_rows = [
                tuple(row)
                for row in conn.execute(
                    "SELECT id, profile_id, job_id, run_id, action, reason, revoked_at, created_at "
                    "FROM feedback_events ORDER BY id"
                )
            ]

            self._add_column_if_missing(
                conn, "profile_jobs", "last_follow_up_at", "TEXT"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_job_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    from_applied_at TEXT,
                    to_applied_at TEXT,
                    from_last_follow_up_at TEXT,
                    to_last_follow_up_at TEXT,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id, job_id)
                        REFERENCES profile_jobs(profile_id, job_id) ON DELETE RESTRICT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_job_command_receipts (
                    request_id TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    changed INTEGER NOT NULL CHECK (changed IN (0, 1)),
                    event_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id, job_id)
                        REFERENCES profile_jobs(profile_id, job_id) ON DELETE RESTRICT,
                    FOREIGN KEY (event_id) REFERENCES profile_job_events(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_profile_jobs_reminder_candidates "
                "ON profile_jobs(profile_id, status, applied_at, last_follow_up_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_profile_job_events_history "
                "ON profile_job_events(profile_id, job_id, sequence)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_profile_job_command_receipts_job "
                "ON profile_job_command_receipts(profile_id, job_id, created_at)"
            )

            post_profile_job_count = conn.execute(
                "SELECT COUNT(*) AS c FROM profile_jobs"
            ).fetchone()["c"]
            post_feedback_rows = [
                tuple(row)
                for row in conn.execute(
                    "SELECT id, profile_id, job_id, run_id, action, reason, revoked_at, created_at "
                    "FROM feedback_events ORDER BY id"
                )
            ]
            if post_profile_job_count != profile_job_count:
                raise RuntimeError("migration_28 profile_jobs row count changed")
            if post_feedback_rows != feedback_rows:
                raise RuntimeError("migration_28 feedback_events changed")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("migration_28 foreign_key_check failed")

            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (28, ?, 'job lifecycle events, command receipts and reminders')",
                (_now(),),
            )

    def _migration_029(self):
        """Persist per-page scrape progress with an atomic jobs snapshot.

        单组合未完成前，每完成一页把岗位快照写入 scrape_run_jobs，并记录
        当前页/恢复页；两写在同一个事务里，失败整笔回滚。
        """
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scrape_page_progress (
                    run_id TEXT NOT NULL,
                    combo_key TEXT NOT NULL,
                    completed_pages INTEGER NOT NULL,
                    target_pages INTEGER NOT NULL,
                    resume_page INTEGER NOT NULL,
                    has_more INTEGER NOT NULL DEFAULT 1,
                    jobs_count INTEGER NOT NULL DEFAULT 0,
                    last_completed_page INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, combo_key),
                    FOREIGN KEY (run_id) REFERENCES screening_runs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (29, ?, 'scrape page progress checkpoints')",
                (_now(),),
            )


    def _migration_030(self):
        """Add archived_at to result snapshots for multi-round history."""
        with self._connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(screening_runs)")}
            if "archived_at" not in cols:
                conn.execute("ALTER TABLE screening_runs ADD COLUMN archived_at TEXT")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (30, ?, 'multi-round history archived_at')",
                (_now(),),
            )

    def _migration_031(self):
        """Add hidden profile facts and structured screening flags (B033).

        - screening_runs.profile_facts_json: 简历画像事实快照（隐藏层，精筛三通道输入）
        - screening_results.flags_json: 岗位靠谱判定结构化 flags（[{code,level,reason}]）

        两列均可空；存量行保持 NULL = 老轮次/无 flags，不做数据回填。
        """
        with self._connection() as conn:
            run_cols = {row["name"] for row in conn.execute(_SQL_SCREENING_RUN_COLUMNS)}
            if "profile_facts_json" not in run_cols:
                conn.execute(
                    "ALTER TABLE screening_runs ADD COLUMN profile_facts_json TEXT"
                )
            res_cols = {row["name"] for row in conn.execute(
                "PRAGMA table_info(screening_results)"
            )}
            if "flags_json" not in res_cols:
                conn.execute(
                    "ALTER TABLE screening_results ADD COLUMN flags_json TEXT"
                )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (31, ?, 'profile facts and structured screening flags')",
                (_now(),),
            )

    # -- migration 27 helpers ---------------------------------------------

    @staticmethod
    def _add_column_if_missing(conn, table: str, column: str, definition: str) -> None:
        """若列不存在则 ALTER TABLE ADD COLUMN。"""
        cols = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _rename_column_with_data(
        conn, table: str, old_col: str, new_col: str,
        old_type: str = "TEXT", new_type: str = "TEXT",
    ) -> None:
        """重命名列并保留数据；优先使用 SQLite 原生 RENAME COLUMN。

        原生重命名完整保留 NOT NULL、UNIQUE、索引和外键。旧版 SQLite（< 3.25）
        回退为：新增可空 new_col、复制数据、重建表去掉 old_col。
        """
        cols = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if new_col in cols:
            return  # 已重命名
        if old_col not in cols:
            return  # 旧列不存在，无需重命名

        sqlite_version = getattr(sqlite3, "sqlite_version_info", (0,))
        if sqlite_version >= (3, 25, 0):
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}")
            return

        # 旧版 SQLite 回退：新列先按可空添加，避免 NOT NULL 无默认值报错
        nullable_new_type = new_type.replace(" NOT NULL", "", 1)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {new_col} {nullable_new_type}")
        conn.execute(
            f"UPDATE {table} SET {new_col} = {old_col} WHERE {old_col} IS NOT NULL"
        )

        # 重建表去掉旧列，并重建调用方依赖的唯一索引
        tmp_name = f"_tmp_{table}_{old_col}_removed"
        all_cols = [
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        ]
        keep_cols = [c for c in all_cols if c != old_col]
        col_list = ", ".join(keep_cols)
        conn.execute(f"DROP TABLE IF EXISTS {tmp_name}")
        conn.execute(
            f"CREATE TABLE {tmp_name} AS SELECT {col_list} FROM {table}"
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {tmp_name} RENAME TO {table}")

        # 重建索引（针对被重建的表）
        # 注：原表的 UNIQUE/PK 约束已丢失，需要调用方或上层重建
        # screening_pending_results 和 scrape_run_jobs 的 UNIQUE 约束需重建
        if table == "screening_pending_results":
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_screening_pending_run_job "
                "ON screening_pending_results(run_id, platform_job_id)"
            )
        elif table == "scrape_run_jobs":
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_scrape_run_jobs_run_job "
                "ON scrape_run_jobs(run_id, platform_job_id)"
            )
        elif table == "screening_results":
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_screening_results_run_pid "
                "ON screening_results(run_id, platform_job_id)"
            )

    def _copy_legacy_default_profile(self):
        """Copy old default profile to candidate_profiles if not already present."""
        with self._connection() as conn:
            if conn.execute("SELECT 1 FROM candidate_profiles WHERE name = 'default'").fetchone():
                return  # 已存在，短路避免无谓查询
            old = conn.execute("SELECT value_json FROM profiles WHERE name = 'default'").fetchone()
            if old:
                conn.execute(
                    "INSERT INTO candidate_profiles (id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                    "VALUES (?, 'default', ?, '{}', ?, ?)",
                    (_uuid(), old["value_json"], _now(), _now()),
                )
