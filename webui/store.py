"""SQLite persistence for the AI job workbench.

Extends the original task/profile store with versioned migrations,
candidate profiles, resumes, AI settings, search runs, jobs, feedback
and preference versions.  Old tables (tasks, task_logs, profiles) are
preserved unchanged.
"""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


class DiscoveryStoreConflictError(Exception):
    """Raised when a CAS-guarded store update detects a state conflict."""


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "interrupted", "partial"}
ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed", "interrupted"},
    "running": {"succeeded", "failed", "interrupted", "partial"},
    "succeeded": set(),
    "failed": set(),
    "interrupted": set(),
    "partial": set(),
}

RUN_STATUSES = {"queued", "running", "succeeded", "partial", "failed", "interrupted"}
RUN_TRANSITIONS = {
    "queued": {"running", "failed", "interrupted"},
    "running": {"succeeded", "partial", "failed", "interrupted"},
    "succeeded": set(),
    "partial": set(),
    "failed": set(),
    "interrupted": set(),
}

QUERY_STATUSES = {"queued", "running", "succeeded", "failed", "interrupted"}
FEEDBACK_ACTIONS = {"interested", "not_interested"}
FEEDBACK_REASONS = {"role", "salary", "location", "company", None}
PROFILE_JOB_STATUSES = {"new", "interested", "applied", "deleted"}
AI_STATUS_VALUES = {"unconfigured", "testing", "ready", "failed"}
RESUME_FORMATS = {"txt", "pdf", "docx"}
MAX_DETAIL_BUDGET = 60
_INITIALIZE_LOCK = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return uuid.uuid4().hex[:16]


def _opt_str(value):
    """把 None 转为 SQL NULL（None），其他值转 str。"""
    return None if value is None else str(value)


def _now_minus_days(days):
    """返回 N 天前的 ISO 时间字符串（用于清理阈值）。"""
    return (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()


def _safe_quality_warnings(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("code"), str) and isinstance(item.get("path"), str):
            result.append({"code": item["code"], "path": item["path"]})
    return result


def _decode_json(value, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _candidate_profile_content_hash(summary, unknowns, facts) -> str:
    normalized_facts = []
    for fact in facts or []:
        normalized_facts.append({
            "stable_key": fact.get("stable_key", ""),
            "fact_type": fact.get("fact_type", ""),
            "value": fact.get("value", {}),
            "normalized_value": fact.get("normalized_value", ""),
            "source_kind": fact.get("source_kind", ""),
            "assertion_type": fact.get("assertion_type", ""),
            "confidence": fact.get("confidence", 0),
            "verification_status": fact.get("verification_status", ""),
            "evidence_ids": sorted(fact.get("evidence_ids", []) or []),
        })
    normalized_facts.sort(key=lambda item: (item["stable_key"], item["fact_type"], item["normalized_value"]))
    blob = json.dumps(
        {"summary": summary or {}, "unknowns": unknowns or [], "facts": normalized_facts},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TaskStore:
    def __init__(self, db_path):
        self.db_path = os.path.abspath(os.fspath(db_path))
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with _INITIALIZE_LOCK:
            self._configure_database()
            self._initialize()
            self._migrate()
            self._mark_stale_runs_interrupted()

    # -- connection --------------------------------------------------------

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _configure_database(self):
        """Configure persistent concurrency settings before schema work."""
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        finally:
            connection.close()

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # -- legacy initialization (tasks/task_logs/profiles) ------------------

    def _initialize(self):
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    output_path TEXT,
                    detail_output_path TEXT,
                    returncode INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_logs (
                    task_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    line TEXT NOT NULL,
                    PRIMARY KEY (task_id, seq),
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    name TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "UPDATE tasks SET status = 'interrupted', error = ?, updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                ("服务重启，原任务已中断", _now()),
            )

    # -- migrations --------------------------------------------------------

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
        # Always reconcile: copy old default profile if not yet in candidate_profiles
        self._copy_legacy_default_profile()

    def _mark_stale_runs_interrupted(self):
        """Reconcile run state on process restart.

        A process restart cannot resume an in-memory child process. Mark runs
        left in an active state as interrupted so the UI does not show a
        permanently "running" state. This is runtime reconciliation, not
        schema migration; ``mark_interrupted_on_restart`` in
        ``discovery_runner.py`` performs the equivalent operation with an
        appended audit event.
        """
        with self._connection() as conn:
            conn.execute(
                "UPDATE search_runs SET status = 'interrupted', error_code = 'restart', updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )
            conn.execute(
                "UPDATE screening_runs SET status = 'interrupted', error_code = 'restart', updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )
            conn.execute(
                "UPDATE discovery_runs SET status = 'interrupted', failure_code = 'restart', updated_at = ? "
                "WHERE status IN ('created', 'planning', 'fetching_lists', 'fetching_details', "
                "'evaluating', 'assembling')",
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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(screening_runs)")}
            additions = {
                "resume_id": "TEXT",
                "pending_count": "INTEGER NOT NULL DEFAULT 0",
                "processed_count": "INTEGER NOT NULL DEFAULT 0",
                "source_cursor": "INTEGER NOT NULL DEFAULT 0",
                "parse_failure_count": "INTEGER NOT NULL DEFAULT 0",
                "parse_failures_json": "TEXT NOT NULL DEFAULT '{}'",
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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(screening_runs)")}
            additions = {
                "profile_id": "TEXT",
                "execution_params_json": "TEXT NOT NULL DEFAULT '{}'",
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

    def _copy_legacy_default_profile(self):
        """Copy old default profile to candidate_profiles if not already present."""
        with self._connection() as conn:
            old = conn.execute("SELECT value_json FROM profiles WHERE name = 'default'").fetchone()
            if old and not conn.execute("SELECT 1 FROM candidate_profiles WHERE name = 'default'").fetchone():
                conn.execute(
                    "INSERT INTO candidate_profiles (id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                    "VALUES (?, 'default', ?, '{}', ?, ?)",
                    (_uuid(), old["value_json"], _now(), _now()),
                )

    def schema_version(self) -> int:
        with self._connection() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    # -- legacy task API (unchanged) --------------------------------------

    def create_task(self, task_id, kind, params, output_path=None, detail_output_path=None):
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO tasks
                   (id, kind, status, params_json, output_path, detail_output_path,
                    returncode, error, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?, NULL, NULL, ?, ?)""",
                (
                    str(task_id), str(kind), json.dumps(params or {}, ensure_ascii=False),
                    output_path, detail_output_path, timestamp, timestamp,
                ),
            )
        return self.get_task(task_id)

    def update_task(self, task_id, status, returncode=None, error=None):
        current = self.get_task(task_id)
        if status not in ALLOWED_TRANSITIONS:
            raise ValueError(f"未知任务状态: {status}")
        if status not in ALLOWED_TRANSITIONS[current["status"]]:
            raise ValueError(f"任务不能从 {current['status']} 转换到 {status}")
        with self._connection() as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, returncode = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, returncode, error, _now(), str(task_id)),
            )
        return self.get_task(task_id)

    def append_log(self, task_id, line):
        self.get_task(task_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM task_logs WHERE task_id = ?",
                (str(task_id),),
            ).fetchone()
            seq = int(row["next_seq"])
            connection.execute(
                "INSERT INTO task_logs (task_id, seq, created_at, line) VALUES (?, ?, ?, ?)",
                (str(task_id), seq, _now(), str(line)),
            )
        return seq

    def get_logs(self, task_id, after=0):
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (str(task_id),)
            ).fetchone()
            if exists is None:
                raise KeyError(str(task_id))
            rows = connection.execute(
                "SELECT seq, created_at, line FROM task_logs WHERE task_id = ? AND seq > ? ORDER BY seq",
                (str(task_id), int(after or 0)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self, task_id, include_logs=False):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        if row is None:
            raise KeyError(str(task_id))
        task = dict(row)
        task["params"] = json.loads(task.pop("params_json") or "{}")
        if include_logs:
            task["logs"] = self.get_logs(task_id)
        return task

    def list_tasks(self, limit=30):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        tasks = []
        for row in rows:
            item = dict(row)
            item["params"] = json.loads(item.pop("params_json") or "{}")
            tasks.append(item)
        return tasks

    def save_profile(self, profile, name="default"):
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO profiles (name, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET value_json = excluded.value_json,
                   updated_at = excluded.updated_at""",
                (name, json.dumps(profile or {}, ensure_ascii=False), timestamp),
            )

    def load_profile(self, name="default"):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM profiles WHERE name = ?", (name,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else {}

    # -- candidate profiles ------------------------------------------------

    def create_profile(self, name, confirmed_fields=None, resume_id=None, copy_from=None):
        name = str(name or "").strip()
        if not name or len(name) > 80:
            raise ValueError("画像名称长度必须为 1 至 80 个字符")
        confirmed = confirmed_fields or {}
        if copy_from:
            source = self.get_profile(copy_from)
            # Only copy manual (confirmed) fields, never AI preference
            confirmed = {**source["confirmed_fields"], **confirmed}
        pid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles (id, name, confirmed_fields_json, ai_preference_json, resume_id, created_at, updated_at) "
                "VALUES (?, ?, ?, '{}', ?, ?, ?)",
                (pid, name, json.dumps(confirmed, ensure_ascii=False), resume_id, ts, ts),
            )
        return self.get_profile(pid)

    def get_profile(self, profile_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM candidate_profiles WHERE id = ?", (str(profile_id),)).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return self._profile_row(row)

    def list_candidate_profiles(self) -> list:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM candidate_profiles ORDER BY created_at ASC").fetchall()
        return [self._profile_row(row) for row in rows]

    def update_profile(self, profile_id, name=None, confirmed_fields=None, ai_preference=None, resume_id=None):
        current = self.get_profile(profile_id)
        ts = _now()
        new_name = name.strip() if name else current["name"]
        if not new_name or len(new_name) > 80:
            raise ValueError("画像名称长度必须为 1 至 80 个字符")
        fields = confirmed_fields if confirmed_fields is not None else current["confirmed_fields"]
        pref = ai_preference if ai_preference is not None else current["ai_preference"]
        rid = resume_id if resume_id is not None else current["resume_id"]
        with self._connection() as conn:
            conn.execute(
                "UPDATE candidate_profiles SET name = ?, confirmed_fields_json = ?, ai_preference_json = ?, resume_id = ?, updated_at = ? WHERE id = ?",
                (new_name, json.dumps(fields, ensure_ascii=False), json.dumps(pref, ensure_ascii=False), rid, ts, str(profile_id)),
            )
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id, resume_dir=None):
        """删除画像及其关联数据。

        - 先逐个删除该画像下的简历物理文件（若提供 resume_dir）
        - 再删除 candidate_profiles 行，外键 ON DELETE CASCADE 自动清理
          profile_jobs / search_runs / resumes / screening_* 等关联表
        """
        pid = str(profile_id)
        # 校验存在，不存在抛 KeyError 与 get_profile 行为一致
        self.get_profile(pid)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM resumes WHERE profile_id = ? AND deleted_at IS NULL",
                (pid,),
            ).fetchall()
        resume_ids = [r["id"] for r in rows]
        # 删除简历文件需要 resume_service，但 store 不依赖 resume_service；
        # 这里只清数据库层，文件删除由 app 层调用前清理（见 app.py delete_profile 路由）。
        with self._connection() as conn:
            conn.execute("DELETE FROM candidate_profiles WHERE id = ?", (pid,))
        return {"deleted": True, "resume_ids": resume_ids}

    def _profile_row(self, row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "confirmed_fields": json.loads(row["confirmed_fields_json"] or "{}"),
            "ai_preference": json.loads(row["ai_preference_json"] or "{}"),
            "resume_id": row["resume_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- resumes -----------------------------------------------------------

    def save_resume(self, profile_id, storage_path, fmt, extracted_text, content_hash, original_filename=None):
        if fmt not in RESUME_FORMATS:
            raise ValueError(f"不支持的简历格式: {fmt}")
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO resumes (id, profile_id, storage_path, original_filename, format, extracted_text, content_hash, created_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (rid, str(profile_id), storage_path, original_filename, fmt, extracted_text, content_hash, ts),
            )
            conn.execute(
                "UPDATE candidate_profiles SET resume_id = ?, updated_at = ? WHERE id = ?",
                (rid, ts, str(profile_id)),
            )
        return self.get_resume(rid)

    def get_resume(self, resume_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM resumes WHERE id = ?", (str(resume_id),)).fetchone()
        if row is None:
            raise KeyError(resume_id)
        result = dict(row)
        result["suggestions"] = json.loads(result.pop("suggestions_json", "{}") or "{}")
        return result

    def save_resume_suggestions(self, resume_id, suggestions):
        self.get_resume(resume_id)
        with self._connection() as conn:
            conn.execute(
                "UPDATE resumes SET suggestions_json = ? WHERE id = ? AND deleted_at IS NULL",
                (json.dumps(suggestions or {}, ensure_ascii=False), str(resume_id)),
            )
        return self.get_resume(resume_id)

    def delete_resume(self, resume_id):
        """Wipe resume text, hash, filename, storage_path and break the profile link.

        File removal is the responsibility of ``resume_service.delete_resume``,
        which knows the resume directory.  Here we only wipe database fields so
        the store layer never depends on the filesystem layout.
        """
        resume = self.get_resume(resume_id)
        with self._connection() as conn:
            # Wipe all sensitive fields then mark deleted_at
            conn.execute(
                "UPDATE resumes SET extracted_text = NULL, content_hash = NULL, original_filename = NULL, suggestions_json = '{}', storage_path = '', deleted_at = ? WHERE id = ?",
                (_now(), str(resume_id)),
            )
            # Break the profile->resume link so unconfirmed AI suggestions
            # derived from this resume no longer appear active.
            conn.execute(
                "UPDATE candidate_profiles SET resume_id = NULL, updated_at = ? "
                "WHERE resume_id = ?",
                (_now(), str(resume_id)),
            )
        return True

    def list_resumes(self, profile_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, profile_id, format, created_at, deleted_at FROM resumes WHERE profile_id = ? ORDER BY created_at DESC",
                (str(profile_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- AI settings -------------------------------------------------------

    def save_ai_settings(self, endpoint_url, credential_ref, status="unconfigured", last_error_code=None, model=""):
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO ai_settings (id, endpoint_url, credential_ref, status, last_error_code, model, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET endpoint_url = excluded.endpoint_url,
                   credential_ref = excluded.credential_ref, status = excluded.status,
                   last_error_code = excluded.last_error_code, model = excluded.model,
                   updated_at = excluded.updated_at""",
                (endpoint_url, credential_ref, status, last_error_code, str(model or ""), ts),
            )
        return self.get_ai_settings()

    def get_ai_settings(self) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone()
        if row is None:
            return {"endpoint_url": "", "model": "", "status": "unconfigured", "last_error_code": None, "updated_at": None, "is_configured": False}
        result = dict(row)
        result["is_configured"] = bool(result["endpoint_url"] and result["credential_ref"])
        # Never expose credential_ref outside the store — callers only see is_configured
        result.pop("credential_ref", None)
        if "model" not in result:
            result["model"] = ""
        return result

    def get_credential_ref(self) -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT credential_ref FROM ai_settings WHERE id = 1").fetchone()
        return row["credential_ref"] if row else ""

    def update_ai_status(self, status, last_error_code=None):
        if status not in AI_STATUS_VALUES:
            raise ValueError(f"未知 AI 状态: {status}")
        with self._connection() as conn:
            conn.execute(
                "UPDATE ai_settings SET status = ?, last_error_code = ?, updated_at = ? WHERE id = 1",
                (status, last_error_code, _now()),
            )
        return self.get_ai_settings()

    # -- screening runs ----------------------------------------------------

    def create_screening_run(self, frozen_filters, resume_id=None, *,
                             profile_id=None, execution=None) -> dict:
        """Create a queued screening run with frozen filters and resume reference."""
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs (id, frozen_filters_json, status, source_count, "
                "match_count, mismatch_count, resume_id, profile_id, execution_params_json, "
                "created_at, updated_at, error_code) "
                "VALUES (?, ?, 'queued', 0, 0, 0, ?, ?, ?, ?, ?, NULL)",
                (rid, json.dumps(frozen_filters, ensure_ascii=False), _opt_str(resume_id),
                 _opt_str(profile_id), json.dumps(execution or {}, ensure_ascii=False), ts, ts),
            )
        return self.get_screening_run(rid)

    def get_screening_run(self, run_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM screening_runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result["frozen_filters"] = json.loads(result.pop("frozen_filters_json", "{}") or "{}")
        result["parse_failures"] = json.loads(result.pop("parse_failures_json", "{}") or "{}")
        result["execution"] = json.loads(result.pop("execution_params_json", "{}") or "{}")
        return result

    def requeue_screening_run(self, run_id) -> dict:
        """Requeue an interrupted/failed run while retaining saved progress."""
        ts = _now()
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE screening_runs SET status = 'queued', error_code = NULL, updated_at = ? "
                "WHERE id = ? AND status IN ('interrupted', 'failed')",
                (ts, str(run_id)),
            )
        if cursor.rowcount == 0:
            run = self.get_screening_run(run_id)
            raise ValueError(f"当前筛选状态不能续处理: {run['status']}")
        return self.get_screening_run(run_id)

    def update_screening_run_status(self, run_id, status, *, source_count=None,
                                    match_count=None, mismatch_count=None,
                                    pending_count=None, processed_count=None,
                                    source_cursor=None, parse_failure_count=None,
                                    parse_failures=None, error_code=None,
                                    expected_statuses=None) -> dict:
        ts = _now()
        fields = ["status = ?", "updated_at = ?"]
        values = [status, ts]
        if source_count is not None:
            fields.append("source_count = ?")
            values.append(int(source_count))
        if match_count is not None:
            fields.append("match_count = ?")
            values.append(int(match_count))
        if mismatch_count is not None:
            fields.append("mismatch_count = ?")
            values.append(int(mismatch_count))
        if pending_count is not None:
            fields.append("pending_count = ?")
            values.append(int(pending_count))
        if processed_count is not None:
            fields.append("processed_count = ?")
            values.append(int(processed_count))
        if source_cursor is not None:
            fields.append("source_cursor = ?")
            values.append(int(source_cursor))
        if parse_failure_count is not None:
            fields.append("parse_failure_count = ?")
            values.append(int(parse_failure_count))
        if parse_failures is not None:
            fields.append("parse_failures_json = ?")
            values.append(json.dumps(parse_failures, ensure_ascii=False))
        if error_code is not None:
            fields.append("error_code = ?")
            values.append(error_code)
        values.append(str(run_id))
        where = "id = ?"
        if expected_statuses is not None:
            expected = sorted({str(item) for item in expected_statuses})
            if not expected:
                return self.get_screening_run(run_id)
            where += f" AND status IN ({','.join('?' for _ in expected)})"
            values.extend(expected)
        with self._connection() as conn:
            conn.execute(
                f"UPDATE screening_runs SET {', '.join(fields)} WHERE {where}",
                values,
            )
        return self.get_screening_run(run_id)

    # -- screening results (match/mismatch zones, run-isolated) -----------

    def add_screening_result(self, run_id, job_id, verdict, *,
                             expected_run_statuses=None) -> dict | None:
        """添加一条核验结果到指定 run。

        verdict 为 "match" 或 "mismatch"。同一 (run_id, job_id) 重复添加
        会因 UNIQUE 约束抛 IntegrityError。不存储核验明细或排除原因
        (data-model.md: "不存储核验明细或排除原因")。
        """
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            if expected_run_statuses is None:
                cursor = conn.execute(
                    "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (rid, str(run_id), str(job_id), verdict, ts),
                )
            else:
                expected = sorted({str(item) for item in expected_run_statuses})
                if not expected:
                    return None
                cursor = conn.execute(
                    "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                    "SELECT ?, ?, ?, ?, ? WHERE EXISTS ("
                    f"SELECT 1 FROM screening_runs WHERE id = ? AND status IN ({','.join('?' for _ in expected)})"
                    ")",
                    (rid, str(run_id), str(job_id), verdict, ts, str(run_id), *expected),
                )
                if cursor.rowcount == 0:
                    return None
        return {
            "id": rid,
            "run_id": str(run_id),
            "job_id": str(job_id),
            "verdict": verdict,
            "created_at": ts,
        }

    def get_screening_results(self, run_id, verdict=None) -> list:
        """查询指定 run 的核验结果，按 created_at 升序（即插入/抓回顺序）。

        可选 verdict 过滤 ("match"/"mismatch")。返回 list of dict，每条含
        id/run_id/job_id/verdict/created_at。新 run 自然返回空列表
        (区域清空通过 run_id 隔离实现，旧 run 结果作为历史保留)。
        """
        with self._connection() as conn:
            if verdict:
                rows = conn.execute(
                    "SELECT * FROM screening_results WHERE run_id = ? AND verdict = ? "
                    "ORDER BY created_at ASC, id ASC",
                    (str(run_id), verdict),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM screening_results WHERE run_id = ? "
                    "ORDER BY created_at ASC, id ASC",
                    (str(run_id),),
                ).fetchall()
        return [dict(row) for row in rows]

    def count_screening_results(self, run_id) -> dict:
        """统计指定 run 的 match/mismatch 数量。返回 {"match": N, "mismatch": N}。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) AS cnt FROM screening_results "
                "WHERE run_id = ? GROUP BY verdict",
                (str(run_id),),
            ).fetchall()
        counts = {"match": 0, "mismatch": 0}
        for row in rows:
            v = row["verdict"]
            if v in counts:
                counts[v] = int(row["cnt"])
        return counts

    # -- screening feedback persistence (interested / rejected zones) -----

    def mark_screening_interest(self, profile_id, job_id, run_id=None) -> dict:
        """标记岗位为感兴趣：profile_jobs.status='interested' + feedback_events。

        复用 001 的 create_feedback（内部已更新 status='interested'）。
        若 profile_job 记录不存在则先建立。感兴趣进持久感兴趣区，跨简历保留。
        """
        # 确保 profile_job 记录存在（status 默认 new）
        try:
            self.get_profile_job(profile_id, job_id)
        except KeyError:
            self.link_profile_job(profile_id, job_id, run_id, run_id, status="new")
        # create_feedback 内部对 action='interested' 会更新 status='interested'
        return self.create_feedback(profile_id, job_id, run_id, "interested")

    def mark_screening_reject(self, profile_id, job_id, run_id=None) -> dict:
        """标记岗位为不感兴趣：profile_jobs.status='deleted' + feedback_events。

        复用 001 的 create_feedback（写 not_interested 反馈），并显式设
        status='deleted' 使其进入持久垃圾桶区。跨简历保留。
        """
        # 确保 profile_job 记录存在
        try:
            self.get_profile_job(profile_id, job_id)
        except KeyError:
            self.link_profile_job(profile_id, job_id, run_id, run_id, status="new")
        # create_feedback 对 not_interested 不自动更新 status，需显式设
        feedback = self.create_feedback(profile_id, job_id, run_id, "not_interested")
        self.update_profile_job(profile_id, job_id, status="deleted")
        return feedback

    def cancel_screening_interest(self, profile_id, job_id):
        """撤销感兴趣标记：把 profile_jobs.status 从 interested 回退到默认 'new'。

        幂等——若当前不是 interested（或记录不存在）也不报错。schema 中
        status 列为 NOT NULL DEFAULT 'new'，故回退到 'new' 而非 NULL。
        仅清状态，不撤销历史 feedback_events。
        """
        with self._connection() as conn:
            conn.execute(
                "UPDATE profile_jobs SET status = 'new' "
                "WHERE profile_id = ? AND job_id = ? AND status = 'interested'",
                (str(profile_id), str(job_id)),
            )
        try:
            return self.get_profile_job(profile_id, job_id)
        except KeyError:
            return None

    def list_screening_interested(self, profile_id) -> list:
        """返回持久感兴趣区的 profile_jobs 列表（status='interested'）。

        按最近反馈时间降序（shown_at DESC），便于长期回看。
        """
        return self.list_profile_jobs(profile_id, status="interested")

    def list_screening_rejected(self, profile_id) -> list:
        """返回持久垃圾桶区的 profile_jobs 列表（status='deleted'）。"""
        return self.list_profile_jobs(profile_id, status="deleted")

    def list_screening_rejected_job_ids(self, profile_id) -> list:
        """返回垃圾桶区的 job_id 列表，用于展示阶段排除。

        只返回当前 status='deleted' 的 job_id；已改为 interested 的不包含。
        排除只按具体岗位识别，不扩展。
        """
        rejected = self.list_screening_rejected(profile_id)
        return [r["job_id"] for r in rejected]

    def reject_screening_with_origin(self, profile_id, job_id, source_job_id,
                                     run_id, origin_zone) -> dict:
        """Atomically reject a job, record its origin, and suspend pending state."""
        if origin_zone not in {"match", "mismatch", "pending", "interested"}:
            raise ValueError(f"invalid origin zone: {origin_zone}")
        feedback_id = _uuid()
        trash_id = _uuid()
        ts = _now()
        with self._connection() as conn:
            profile_job = conn.execute(
                "SELECT 1 FROM profile_jobs WHERE profile_id = ? AND job_id = ?",
                (str(profile_id), str(job_id)),
            ).fetchone()
            if profile_job:
                conn.execute(
                    "UPDATE profile_jobs SET status = 'deleted', last_run_id = ? "
                    "WHERE profile_id = ? AND job_id = ?",
                    (_opt_str(run_id), str(profile_id), str(job_id)),
                )
            else:
                conn.execute(
                    "INSERT INTO profile_jobs "
                    "(profile_id, job_id, first_run_id, last_run_id, ai_rank, shown_at, status) "
                    "VALUES (?, ?, ?, ?, NULL, ?, 'deleted')",
                    (str(profile_id), str(job_id), _opt_str(run_id),
                     _opt_str(run_id), ts),
                )
            conn.execute(
                "INSERT INTO feedback_events "
                "(id, profile_id, job_id, run_id, action, reason, revoked_at, created_at) "
                "VALUES (?, ?, ?, ?, 'not_interested', NULL, NULL, ?)",
                (feedback_id, str(profile_id), str(job_id), _opt_str(run_id), ts),
            )

            pending = None
            if origin_zone == "pending":
                pending = conn.execute(
                    "SELECT * FROM screening_pending_results "
                    "WHERE run_id = ? AND job_id = ?",
                    (str(run_id), str(source_job_id)),
                ).fetchone()
                if pending:
                    conn.execute(
                        "DELETE FROM screening_pending_results WHERE id = ?",
                        (pending["id"],),
                    )
                    conn.execute(
                        "UPDATE screening_runs SET pending_count = ("
                        "SELECT COUNT(*) FROM screening_pending_results WHERE run_id = ?"
                        "), updated_at = ? WHERE id = ?",
                        (str(run_id), ts, str(run_id)),
                    )

            existing = conn.execute(
                "SELECT id FROM screening_trash_records "
                "WHERE profile_id = ? AND job_id = ?",
                (str(profile_id), str(job_id)),
            ).fetchone()
            values = (
                origin_zone, _opt_str(run_id), feedback_id, ts,
                str(source_job_id),
                pending["failure_stage"] if pending else None,
                int(pending["retryable"]) if pending else None,
                int(pending["attempts"]) if pending else None,
            )
            if existing:
                trash_id = existing["id"]
                conn.execute(
                    "UPDATE screening_trash_records SET origin_zone = ?, run_id = ?, "
                    "feedback_ref = ?, deleted_at = ?, restored_at = NULL, "
                    "source_job_id = ?, pending_failure_stage = ?, "
                    "pending_retryable = ?, pending_attempts = ? WHERE id = ?",
                    (*values, trash_id),
                )
            else:
                conn.execute(
                    "INSERT INTO screening_trash_records "
                    "(id, profile_id, job_id, origin_zone, run_id, feedback_ref, "
                    "deleted_at, restored_at, created_at, source_job_id, "
                    "pending_failure_stage, pending_retryable, pending_attempts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                    (trash_id, str(profile_id), str(job_id), origin_zone,
                     _opt_str(run_id), feedback_id, ts, ts, str(source_job_id),
                     pending["failure_stage"] if pending else None,
                     int(pending["retryable"]) if pending else None,
                     int(pending["attempts"]) if pending else None),
                )
        return {"id": trash_id, "feedback_id": feedback_id,
                "origin_zone": origin_zone, "job_id": str(job_id)}

    # -- screening pending / trash-with-origin / cleanup (003 FR-011~027) --

    def add_pending_result(self, run_id, job_id, failure_stage, retryable=True,
                           origin_zone="match", ai_payload=None, *, attempts=None,
                           expected_run_statuses=None) -> dict | None:
        """添加或更新一条待核验记录。同一 (run_id, job_id) 只有一条；
        重试时 attempts+1 并刷新 last_failed_at。"""
        import json as _json
        rid = _uuid()
        ts = _now()
        payload_json = _json.dumps(ai_payload or {}, ensure_ascii=False)
        att = 1 if attempts is None else int(attempts)
        with self._connection() as conn:
            if expected_run_statuses is not None:
                expected = sorted({str(item) for item in expected_run_statuses})
                if not expected:
                    return None
                placeholders = ",".join("?" for _ in expected)
                active = conn.execute(
                    f"SELECT 1 FROM screening_runs WHERE id = ? AND status IN ({placeholders})",
                    (str(run_id), *expected),
                ).fetchone()
                if not active:
                    return None
            existing = conn.execute(
                "SELECT id, attempts FROM screening_pending_results "
                "WHERE run_id = ? AND job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
            if existing:
                new_attempts = int(existing["attempts"]) + 1
                conn.execute(
                    "UPDATE screening_pending_results "
                    "SET failure_stage = ?, retryable = ?, origin_zone = ?, "
                    "ai_payload_json = ?, attempts = ?, last_failed_at = ? "
                    "WHERE id = ?",
                    (failure_stage, 1 if retryable else 0, origin_zone,
                     payload_json, new_attempts, ts, existing["id"]),
                )
                rid = existing["id"]
                att = new_attempts
            else:
                conn.execute(
                    "INSERT INTO screening_pending_results "
                    "(id, run_id, job_id, failure_stage, retryable, attempts, "
                    " last_failed_at, origin_zone, ai_payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, str(run_id), str(job_id), failure_stage,
                     1 if retryable else 0, att, ts, origin_zone, payload_json, ts),
                )
        return {
            "id": rid, "run_id": str(run_id), "job_id": str(job_id),
            "failure_stage": failure_stage, "retryable": bool(retryable),
            "attempts": att, "last_failed_at": ts,
            "origin_zone": origin_zone, "ai_payload": ai_payload or {},
            "created_at": ts,
        }

    def list_pending(self, run_id) -> list:
        """返回指定 run 的待核验记录，按 created_at 升序。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_pending_results WHERE run_id = ? "
                "ORDER BY created_at ASC, id ASC",
                (str(run_id),),
            ).fetchall()
        import json as _json
        out = []
        for row in rows:
            d = dict(row)
            try:
                d["ai_payload"] = _json.loads(d.get("ai_payload_json") or "{}")
            except Exception:
                d["ai_payload"] = {}
            d["retryable"] = bool(d.get("retryable"))
            out.append(d)
        return out

    def retry_pending(self, run_id, job_id) -> dict:
        """对待核验记录做一次重试登记：attempts+1、刷新 last_failed_at。
        不删除记录（重试若成功会由 manual_route 或 add_screening_result 流出）。
        """
        ts = _now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, attempts FROM screening_pending_results "
                "WHERE run_id = ? AND job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
            if not row:
                raise KeyError(f"pending result not found: run={run_id} job={job_id}")
            new_attempts = int(row["attempts"]) + 1
            conn.execute(
                "UPDATE screening_pending_results "
                "SET attempts = ?, last_failed_at = ? WHERE id = ?",
                (new_attempts, ts, row["id"]),
            )
        return {"id": row["id"], "attempts": new_attempts, "last_failed_at": ts}

    def manual_route_pending(self, run_id, job_id, target) -> dict:
        """把待核验记录人工分流到 match/mismatch 结果区，并从 pending 删除。"""
        if target not in ("match", "mismatch"):
            raise ValueError(f"invalid manual route target: {target}")
        result_id = _uuid()
        ts = _now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM screening_pending_results "
                "WHERE run_id = ? AND job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
            if not row:
                raise KeyError(f"pending result not found: run={run_id} job={job_id}")
            existing = conn.execute(
                "SELECT * FROM screening_results WHERE run_id = ? AND job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM screening_pending_results WHERE id = ?", (row["id"],))
                return dict(existing)
            conn.execute(
                "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (result_id, str(run_id), str(job_id), target, ts),
            )
            conn.execute("DELETE FROM screening_pending_results WHERE id = ?", (row["id"],))
        return {"id": result_id, "run_id": str(run_id), "job_id": str(job_id),
                "verdict": target, "created_at": ts}

    def move_to_trash_with_origin(self, profile_id, job_id, origin_zone="match",
                                  run_id=None, feedback_ref=None) -> dict:
        """把岗位移入垃圾桶并记录原区域（match/mismatch/pending/interested）。
        若已有 trash 记录则更新 origin_zone（保留 created_at）。"""
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM screening_trash_records "
                "WHERE profile_id = ? AND job_id = ?",
                (str(profile_id), str(job_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE screening_trash_records "
                    "SET origin_zone = ?, run_id = ?, feedback_ref = ?, "
                    "deleted_at = ?, restored_at = NULL WHERE id = ?",
                    (origin_zone, _opt_str(run_id), _opt_str(feedback_ref),
                     ts, existing["id"]),
                )
                rid = existing["id"]
                created = existing["created_at"]
            else:
                conn.execute(
                    "INSERT INTO screening_trash_records "
                    "(id, profile_id, job_id, origin_zone, run_id, feedback_ref, "
                    " deleted_at, restored_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    (rid, str(profile_id), str(job_id), origin_zone,
                     _opt_str(run_id), _opt_str(feedback_ref), ts, ts),
                )
                created = ts
        return {
            "id": rid, "profile_id": str(profile_id), "job_id": str(job_id),
            "origin_zone": origin_zone, "run_id": run_id,
            "feedback_ref": feedback_ref, "deleted_at": ts,
            "created_at": created,
        }

    def list_trash_with_origin(self, profile_id) -> list:
        """返回垃圾桶中未恢复的记录（restored_at IS NULL）。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_trash_records "
                "WHERE profile_id = ? AND restored_at IS NULL "
                "ORDER BY deleted_at DESC, id ASC",
                (str(profile_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_trash_record(self, profile_id, job_id) -> dict:
        """Return one active trash record without changing its recoverability."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_trash_records "
                "WHERE profile_id = ? AND job_id = ? AND restored_at IS NULL",
                (str(profile_id), str(job_id)),
            ).fetchone()
        if not row:
            raise KeyError(f"trash record not found: profile={profile_id} job={job_id}")
        return dict(row)

    def complete_trash_restore(self, profile_id, job_id, profile_status,
                               recovery_run_id=None, create_recovery=False) -> dict:
        """Atomically reactivate a trash item after external artifacts are ready."""
        ts = _now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_trash_records "
                "WHERE profile_id = ? AND job_id = ? AND restored_at IS NULL",
                (str(profile_id), str(job_id)),
            ).fetchone()
            if not row:
                raise KeyError(f"trash record not found: profile={profile_id} job={job_id}")
            origin = row["origin_zone"]
            target_run_id = recovery_run_id or row["run_id"]
            source_job_id = row["source_job_id"] or str(job_id)

            if create_recovery:
                if not recovery_run_id or origin not in {"match", "mismatch", "pending"}:
                    raise ValueError("invalid recovery run")
                pending_count = 1 if origin == "pending" else 0
                match_count = 1 if origin == "match" else 0
                mismatch_count = 1 if origin == "mismatch" else 0
                status = "partial" if origin == "pending" else "succeeded"
                conn.execute(
                    "INSERT INTO screening_runs "
                    "(id, frozen_filters_json, status, source_count, match_count, "
                    "mismatch_count, created_at, updated_at, error_code, pending_count, "
                    "processed_count, source_cursor, parse_failure_count, parse_failures_json) "
                    "VALUES (?, '{}', ?, 1, ?, ?, ?, ?, NULL, ?, 1, 1, 0, '{}')",
                    (recovery_run_id, status, match_count, mismatch_count,
                     ts, ts, pending_count),
                )

            if origin == "pending":
                if not target_run_id:
                    raise ValueError("pending restore requires run")
                conn.execute(
                    "INSERT INTO screening_pending_results "
                    "(id, run_id, job_id, failure_stage, retryable, attempts, "
                    "last_failed_at, origin_zone, ai_payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '{}', ?) "
                    "ON CONFLICT(run_id, job_id) DO UPDATE SET "
                    "failure_stage = excluded.failure_stage, retryable = excluded.retryable, "
                    "attempts = excluded.attempts, last_failed_at = excluded.last_failed_at",
                    (_uuid(), str(target_run_id), str(source_job_id),
                     row["pending_failure_stage"] or "verification_error",
                     int(row["pending_retryable"] if row["pending_retryable"] is not None else 1),
                    int(row["pending_attempts"] or 1), ts, ts),
                )
                if not create_recovery:
                    conn.execute(
                        "UPDATE screening_runs SET pending_count = ("
                        "SELECT COUNT(*) FROM screening_pending_results WHERE run_id = ?"
                        "), updated_at = ? WHERE id = ?",
                        (str(target_run_id), ts, str(target_run_id)),
                    )
            elif create_recovery and origin in {"match", "mismatch"}:
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, job_id, verdict, created_at) VALUES (?, ?, ?, ?, ?)",
                    (_uuid(), str(recovery_run_id), str(source_job_id), origin, ts),
                )

            conn.execute(
                "UPDATE profile_jobs SET status = ? WHERE profile_id = ? AND job_id = ?",
                (profile_status, str(profile_id), str(job_id)),
            )
            conn.execute(
                "UPDATE screening_trash_records SET restored_at = ? WHERE id = ?",
                (ts, row["id"]),
            )
        result = dict(row)
        result["restored_at"] = ts
        result["recovery_run_id"] = target_run_id
        return result

    def restore_from_trash(self, profile_id, job_id) -> dict:
        """从垃圾桶恢复岗位：标记 restored_at，返回原区域信息。
        不修改 profile_jobs.status（保留既有感兴趣/不感兴趣标记）。
        """
        ts = _now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_trash_records "
                "WHERE profile_id = ? AND job_id = ? AND restored_at IS NULL",
                (str(profile_id), str(job_id)),
            ).fetchone()
            if not row:
                raise KeyError(f"trash record not found: profile={profile_id} job={job_id}")
            conn.execute(
                "UPDATE screening_trash_records SET restored_at = ? WHERE id = ?",
                (ts, row["id"]),
            )
        out = dict(row)
        out["restored_at"] = ts
        return out

    def cleanup_temp_run_data(self, days=30) -> dict:
        """清理超期临时执行数据：screening_runs/results/pending 超 N 天。
        保留长期感兴趣、垃圾桶记录、清理记录本身。返回 success/fail/pending 计数。"""
        cutoff = _now_minus_days(days)
        success = 0
        fail = 0
        pending = 0
        old_runs = []
        try:
            with self._connection() as conn:
                pending = conn.execute(
                    "SELECT COUNT(*) AS c FROM screening_pending_results "
                    "WHERE created_at < ?",
                    (cutoff,),
                ).fetchone()["c"]
                old_runs = [r["id"] for r in conn.execute(
                    "SELECT id FROM screening_runs WHERE created_at < ?",
                    (cutoff,),
                ).fetchall()]
                if old_runs:
                    placeholders = ",".join("?" * len(old_runs))
                    conn.execute(
                        f"DELETE FROM screening_pending_results "
                        f"WHERE run_id IN ({placeholders})",
                        old_runs,
                    )
                    conn.execute(
                        f"DELETE FROM screening_results "
                        f"WHERE run_id IN ({placeholders})",
                        old_runs,
                    )
                    conn.execute(
                        f"DELETE FROM screening_runs "
                        f"WHERE id IN ({placeholders})",
                        old_runs,
                    )
                    success = len(old_runs)
        except sqlite3.Error:
            success = 0
            fail = len(old_runs)
        return {"success_count": success, "fail_count": fail,
                "pending_at_cleanup": pending, "run_ids": old_runs}

    def preview_cleanup_with_pending_prompt(self, days=30) -> dict:
        """预览 30 天清理：返回将清理的 pending 数等。
        用于在执行清理前给用户提示有待核验记录将被清理。"""
        cutoff = _now_minus_days(days)
        with self._connection() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) AS c FROM screening_pending_results "
                "WHERE created_at < ?",
                (cutoff,),
            ).fetchone()["c"]
            runs = conn.execute(
                "SELECT COUNT(*) AS c FROM screening_runs WHERE created_at < ?",
                (cutoff,),
            ).fetchone()["c"]
            results = conn.execute(
                "SELECT COUNT(*) AS c FROM screening_results "
                "WHERE created_at < ?",
                (cutoff,),
            ).fetchone()["c"]
        return {
            "days": days,
            "pending_at_cleanup": pending,
            "runs_to_cleanup": runs,
            "results_to_cleanup": results,
        }

    def record_cleanup(self, scope, success_count, fail_count,
                       pending_at_cleanup) -> dict:
        """记录一次清理历史，可查询。"""
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO screening_cleanup_records "
                "(id, scope, success_count, fail_count, pending_at_cleanup, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rid, scope, int(success_count), int(fail_count),
                 int(pending_at_cleanup), ts),
            )
        return {
            "id": rid, "scope": scope,
            "success_count": int(success_count),
            "fail_count": int(fail_count),
            "pending_at_cleanup": int(pending_at_cleanup),
            "created_at": ts,
        }

    def list_cleanup_records(self, limit=50) -> list:
        """返回清理历史，按 created_at 降序。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_cleanup_records "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- search runs -------------------------------------------------------

    def create_search_run(self, profile_id, profile_snapshot, mode, total_detail_budget=MAX_DETAIL_BUDGET):
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO search_runs (id, profile_id, profile_snapshot_json, mode, status, total_detail_budget, discovered_count, completed_jd_count, created_at, updated_at, error_code) "
                "VALUES (?, ?, ?, ?, 'queued', ?, 0, 0, ?, ?, NULL)",
                (rid, str(profile_id), json.dumps(profile_snapshot, ensure_ascii=False), mode, int(total_detail_budget), ts, ts),
            )
        return self.get_search_run(rid)

    def get_search_run(self, run_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM search_runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_row(row)

    def update_search_run(self, run_id, status=None, discovered_count=None, completed_jd_count=None, error_code=None):
        current = self.get_search_run(run_id)
        if status and status not in RUN_STATUSES:
            raise ValueError(f"未知运行状态: {status}")
        if status and status not in RUN_TRANSITIONS[current["status"]]:
            raise ValueError(f"运行不能从 {current['status']} 转换到 {status}")
        sets = []
        params = []
        if status:
            sets.append("status = ?")
            params.append(status)
        if discovered_count is not None:
            sets.append("discovered_count = ?")
            params.append(int(discovered_count))
        if completed_jd_count is not None:
            sets.append("completed_jd_count = ?")
            params.append(int(completed_jd_count))
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(error_code)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(str(run_id))
        with self._connection() as conn:
            conn.execute(f"UPDATE search_runs SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_search_run(run_id)

    def list_search_runs(self, profile_id=None, limit=30):
        with self._connection() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT * FROM search_runs WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(profile_id), max(1, int(limit))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM search_runs ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
                ).fetchall()
        return [self._run_row(row) for row in rows]

    def _run_row(self, row) -> dict:
        return {
            "id": row["id"],
            "profile_id": row["profile_id"],
            "profile_snapshot": json.loads(row["profile_snapshot_json"] or "{}"),
            "mode": row["mode"],
            "status": row["status"],
            "total_detail_budget": row["total_detail_budget"],
            "discovered_count": row["discovered_count"],
            "completed_jd_count": row["completed_jd_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "error_code": row["error_code"],
        }

    def append_search_event(self, run_id, event_type, payload=None):
        self.get_search_run(run_id)
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO search_run_events (run_id, type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (str(run_id), str(event_type), json.dumps(payload or {}, ensure_ascii=False), _now()),
            )
            event_id = cursor.lastrowid
        return {"id": event_id, "run_id": str(run_id), "type": str(event_type), "payload": payload or {}}

    def list_search_events(self, run_id, after=0):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, run_id, type, payload_json, created_at FROM search_run_events "
                "WHERE run_id = ? AND id > ? ORDER BY id ASC",
                (str(run_id), int(after or 0)),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"] or "{}"), "payload_json": None} for row in rows]

    # -- run queries -------------------------------------------------------

    def create_run_query(self, run_id, ordinal, frozen_query, list_output_path, detail_output_path, detail_budget):
        qid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO run_queries (id, run_id, ordinal, frozen_query_json, list_output_path, detail_output_path, status, detail_budget, counts_json, error_code, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, '{}', NULL, ?, ?)",
                (qid, str(run_id), int(ordinal), json.dumps(frozen_query, ensure_ascii=False), list_output_path, detail_output_path, int(detail_budget), ts, ts),
            )
        return self.get_run_query(qid)

    def get_run_query(self, query_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM run_queries WHERE id = ?", (str(query_id),)).fetchone()
        if row is None:
            raise KeyError(query_id)
        return self._query_row(row)

    def update_run_query(self, query_id, status=None, counts=None, error_code=None):
        sets = []
        params = []
        if status:
            sets.append("status = ?")
            params.append(status)
        if counts is not None:
            sets.append("counts_json = ?")
            params.append(json.dumps(counts, ensure_ascii=False))
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(error_code)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(str(query_id))
        with self._connection() as conn:
            conn.execute(f"UPDATE run_queries SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_run_query(query_id)

    def list_run_queries(self, run_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM run_queries WHERE run_id = ? ORDER BY ordinal ASC", (str(run_id),)
            ).fetchall()
        return [self._query_row(row) for row in rows]

    def _query_row(self, row) -> dict:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "ordinal": row["ordinal"],
            "frozen_query": json.loads(row["frozen_query_json"] or "{}"),
            "list_output_path": row["list_output_path"],
            "detail_output_path": row["detail_output_path"],
            "status": row["status"],
            "detail_budget": row["detail_budget"],
            "counts": json.loads(row["counts_json"] or "{}"),
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- jobs --------------------------------------------------------------

    def save_job(self, canonical_url, source_url, title, company, salary, location, jd):
        from datetime import timedelta

        ts = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        jid = _uuid()
        with self._connection() as conn:
            existing = conn.execute("SELECT id FROM jobs WHERE canonical_url = ?", (canonical_url,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE jobs SET source_url = ?, title = ?, company = ?, salary = ?, location = ?, jd = ?, last_seen_at = ? WHERE id = ?",
                    (source_url, title, company, salary, location, jd, ts, existing["id"]),
                )
                jid = existing["id"]
            else:
                conn.execute(
                    "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (jid, canonical_url, source_url, title, company, salary, location, jd, ts, ts, expires_at),
                )
        return self.get_job(jid)

    def get_job(self, job_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def update_job_expiry(self, job_id, expires_at):
        with self._connection() as conn:
            conn.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", (expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at), str(job_id)))
        return self.get_job(job_id)

    # -- profile jobs ------------------------------------------------------

    def link_profile_job(self, profile_id, job_id, first_run_id, last_run_id, ai_rank=None, status="new"):
        if status not in PROFILE_JOB_STATUSES:
            raise ValueError(f"未知岗位状态: {status}")
        ts = _now()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT * FROM profile_jobs WHERE profile_id = ? AND job_id = ?",
                (str(profile_id), str(job_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE profile_jobs SET last_run_id = ?, ai_rank = ?, shown_at = COALESCE(shown_at, ?) WHERE profile_id = ? AND job_id = ?",
                    (last_run_id, ai_rank, ts, str(profile_id), str(job_id)),
                )
            else:
                conn.execute(
                    "INSERT INTO profile_jobs (profile_id, job_id, first_run_id, last_run_id, ai_rank, shown_at, status, note, applied_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (str(profile_id), str(job_id), first_run_id, last_run_id, ai_rank, ts, status),
                )
        return self.get_profile_job(profile_id, job_id)

    def update_profile_job(self, profile_id, job_id, status=None, note=None, applied_at=None):
        sets = []
        params = []
        if status:
            if status not in PROFILE_JOB_STATUSES:
                raise ValueError(f"未知岗位状态: {status}")
            sets.append("status = ?")
            params.append(status)
        if note is not None:
            sets.append("note = ?")
            params.append(note)
        if applied_at is not None:
            sets.append("applied_at = ?")
            params.append(applied_at)
        if sets:
            params.extend([str(profile_id), str(job_id)])
            with self._connection() as conn:
                conn.execute(f"UPDATE profile_jobs SET {', '.join(sets)} WHERE profile_id = ? AND job_id = ?", params)
        return self.get_profile_job(profile_id, job_id)

    def get_profile_job(self, profile_id, job_id) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM profile_jobs WHERE profile_id = ? AND job_id = ?",
                (str(profile_id), str(job_id)),
            ).fetchone()
        if row is None:
            raise KeyError((profile_id, job_id))
        return dict(row)

    def list_profile_jobs(self, profile_id, status=None, run_id=None) -> list:
        clauses = ["profile_id = ?"]
        params = [str(profile_id)]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("(first_run_id = ? OR last_run_id = ?)")
            params.extend([str(run_id), str(run_id)])
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM profile_jobs WHERE {' AND '.join(clauses)} ORDER BY shown_at DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    # -- feedback ----------------------------------------------------------

    def create_feedback(self, profile_id, job_id, run_id, action, reason=None):
        if action not in FEEDBACK_ACTIONS:
            raise ValueError(f"未知反馈动作: {action}")
        if reason not in FEEDBACK_REASONS:
            raise ValueError(f"未知反馈原因: {reason}")
        fid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO feedback_events (id, profile_id, job_id, run_id, action, reason, revoked_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (fid, str(profile_id), str(job_id), run_id, action, reason, ts),
            )
            # Update profile_job status to match feedback
            if action == "interested":
                conn.execute(
                    "UPDATE profile_jobs SET status = 'interested' WHERE profile_id = ? AND job_id = ?",
                    (str(profile_id), str(job_id)),
                )
        return self.get_feedback(fid)

    def get_feedback(self, feedback_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM feedback_events WHERE id = ?", (str(feedback_id),)).fetchone()
        if row is None:
            raise KeyError(feedback_id)
        return dict(row)

    def revoke_feedback(self, feedback_id):
        with self._connection() as conn:
            conn.execute(
                "UPDATE feedback_events SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_now(), str(feedback_id)),
            )

    def list_feedback(self, profile_id, job_id=None) -> list:
        clauses = ["profile_id = ?"]
        params = [str(profile_id)]
        if job_id:
            clauses.append("job_id = ?")
            params.append(str(job_id))
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM feedback_events WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_effective_feedback(self, profile_id) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM feedback_events WHERE profile_id = ? AND revoked_at IS NULL",
                (str(profile_id),),
            ).fetchone()
        return int(row["c"])

    # -- preference versions ----------------------------------------------

    def save_preference_version(self, profile_id, source_feedback_count, preference_json):
        pid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO preference_versions (id, profile_id, source_feedback_count, preference_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, str(profile_id), int(source_feedback_count), json.dumps(preference_json, ensure_ascii=False), ts),
            )
            # Persist the preference on the profile too
            conn.execute(
                "UPDATE candidate_profiles SET ai_preference_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(preference_json, ensure_ascii=False), ts, str(profile_id)),
            )
        return {"id": pid, "profile_id": str(profile_id), "source_feedback_count": int(source_feedback_count), "preference_json": preference_json, "created_at": ts}

    def get_latest_preference(self, profile_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM preference_versions WHERE profile_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(profile_id),),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["preference_json"] = json.loads(result["preference_json"] or "{}")
        return result

    # -- cleanup -----------------------------------------------------------

    def cleanup_expired_jobs(self, days=30) -> int:
        """Remove normal results older than *days*. Preserves interested/applied."""
        from datetime import datetime, timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            # Find profile_jobs that are 'new' and whose job expired before cutoff
            rows = conn.execute(
                """SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                   JOIN jobs j ON pj.job_id = j.id
                   WHERE pj.status = 'new' AND j.expires_at IS NOT NULL AND j.expires_at < ?""",
                (cutoff,),
            ).fetchall()
            count = 0
            for row in rows:
                conn.execute(
                    "UPDATE profile_jobs SET status = 'deleted' WHERE profile_id = ? AND job_id = ?",
                    (row["profile_id"], row["job_id"]),
                )
                count += 1
            return count

    def preview_cleanup_expired_jobs(self, days=30) -> list:
        """Preview which profile_jobs would be cleaned up, without modifying data.

        Returns a list of ``{profile_id, job_id}`` dicts.  The real cleanup
        is performed by :meth:`cleanup_expired_jobs`.
        """
        from datetime import datetime, timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                   JOIN jobs j ON pj.job_id = j.id
                   WHERE pj.status = 'new' AND j.expires_at IS NOT NULL AND j.expires_at < ?""",
                (cutoff,),
            ).fetchall()
        return [{"profile_id": row["profile_id"], "job_id": row["job_id"]} for row in rows]

    # ===================================================================
    # 004 discovery: analyses, evidence, directions, confirmations
    # ===================================================================

    def create_analysis(self, resume_id, profile_id, *, model_name="", contract_version="v1") -> dict:
        resume_id, profile_id = str(resume_id), str(profile_id)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_v FROM candidate_analyses WHERE resume_id = ?",
                (resume_id,),
            ).fetchone()
            version = int(row["next_v"])
            aid = _uuid()
            ts = _now()
            conn.execute(
                "INSERT INTO candidate_analyses (id, resume_id, profile_id, version, status, analysis_stage, quality_status, "
                "quality_warnings_json, model_name, contract_version, created_at) VALUES (?, ?, ?, ?, 'queued', 'queued', "
                "'complete', '[]', ?, ?, ?)",
                (aid, resume_id, profile_id, version, model_name, contract_version, ts),
            )
        return {"id": aid, "resume_id": resume_id, "profile_id": profile_id, "version": version,
                "status": "queued", "stage": "queued", "quality_status": "complete",
                "quality_warnings": [], "model_name": model_name,
                "contract_version": contract_version,
                "candidate_profile_version_id": None, "created_at": ts}

    def update_analysis_status(self, analysis_id, status, *, failure_code=None, summary=None, unknowns=None,
                               analysis_stage=None, stage=None, quality_status=None, quality_warnings=None,
                               expected_statuses=None, expected_stages=None, provider_call_count=None):
        aid = str(analysis_id)
        sets = ["status = ?"]
        params = [status]
        next_stage = analysis_stage if analysis_stage is not None else stage
        if next_stage is not None:
            sets.append("analysis_stage = ?"); params.append(next_stage)
        if quality_status is not None:
            sets.append("quality_status = ?"); params.append(quality_status)
        if quality_warnings is not None:
            sets.append("quality_warnings_json = ?")
            params.append(json.dumps(_safe_quality_warnings(quality_warnings), ensure_ascii=False))
        if failure_code is not None:
            sets.append("failure_code = ?")
            params.append(failure_code)
        if provider_call_count is not None:
            sets.append("provider_call_count = ?")
            params.append(int(provider_call_count))
        if summary is not None:
            sets.append("summary_json = ?")
            params.append(json.dumps(summary, ensure_ascii=False))
        if unknowns is not None:
            sets.append("unknowns_json = ?")
            params.append(json.dumps(unknowns, ensure_ascii=False))
        if status in ("ready", "failed"):
            sets.append("completed_at = ?")
            params.append(_now())
        where = ["id = ?"]; where_params = [aid]
        if expected_statuses is not None:
            vals = [str(v) for v in expected_statuses]; where.append("status IN (" + ",".join("?" for _ in vals) + ")"); where_params.extend(vals)
        if expected_stages is not None:
            vals = [str(v) for v in expected_stages]; where.append("analysis_stage IN (" + ",".join("?" for _ in vals) + ")"); where_params.extend(vals)
        params.extend(where_params)
        with self._connection() as conn:
            conn.execute(f"UPDATE candidate_analyses SET {', '.join(sets)} WHERE {' AND '.join(where)}", params)
        return self.get_analysis(aid)

    def claim_analysis(self, analysis_id) -> bool:
        """Atomically claim one queued analysis for a single worker."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE candidate_analyses SET status = 'analyzing', analysis_stage = 'requesting' "
                "WHERE id = ? AND status = 'queued' AND analysis_stage = 'queued'",
                (str(analysis_id),),
            )
            return cursor.rowcount == 1

    def get_analysis(self, analysis_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM candidate_analyses WHERE id = ?", (str(analysis_id),)).fetchone()
            if row is None:
                raise KeyError(analysis_id)
            d = dict(row)
            d["summary"] = _decode_json(d.pop("summary_json"), {})
            d["unknowns"] = _decode_json(d.pop("unknowns_json"), [])
            d["quality_warnings"] = _safe_quality_warnings(_decode_json(d.pop("quality_warnings_json", "[]"), []))
            d["stage"] = d.pop("analysis_stage", "queued")
            profile_version = conn.execute(
                "SELECT id FROM candidate_profile_versions WHERE analysis_id=? "
                "ORDER BY version DESC LIMIT 1",
                (str(analysis_id),),
            ).fetchone()
            d["candidate_profile_version_id"] = profile_version["id"] if profile_version else None
            return d

    def list_analyses(self, resume_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM candidate_analyses "
                "WHERE resume_id = ? AND status <> 'deleted' ORDER BY version ASC",
                (str(resume_id),),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["summary"] = _decode_json(d.pop("summary_json"), {})
            d["unknowns"] = _decode_json(d.pop("unknowns_json"), [])
            d["quality_warnings"] = _safe_quality_warnings(_decode_json(d.pop("quality_warnings_json", "[]"), []))
            d["stage"] = d.pop("analysis_stage", "queued")
            with self._connection() as lookup:
                profile_version = lookup.execute(
                    "SELECT id FROM candidate_profile_versions WHERE analysis_id=? "
                    "ORDER BY version DESC LIMIT 1", (d["id"],),
                ).fetchone()
            d["candidate_profile_version_id"] = profile_version["id"] if profile_version else None
            out.append(d)
        return out

    def add_evidence(self, analysis_id, evidence_type, normalized_value, *, safe_excerpt="",
                     source_locator=None, assertion_type="explicit", confidence=0, sensitive=False) -> dict:
        eid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO resume_evidence (id, analysis_id, evidence_type, normalized_value, safe_excerpt, "
                "source_locator_json, assertion_type, confidence, sensitive, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, str(analysis_id), evidence_type, normalized_value, safe_excerpt,
                 json.dumps(source_locator or {}, ensure_ascii=False), assertion_type,
                 int(confidence), int(bool(sensitive)), ts),
            )
        return {"id": eid, "analysis_id": str(analysis_id), "evidence_type": evidence_type,
                "normalized_value": normalized_value, "safe_excerpt": safe_excerpt,
                "source_locator": source_locator or {}, "assertion_type": assertion_type,
                "confidence": int(confidence), "sensitive": bool(sensitive), "created_at": ts}

    def add_evidence_batch(self, analysis_id, items) -> list:
        out = []
        for it in items:
            out.append(self.add_evidence(analysis_id, it["evidence_type"], it["normalized_value"],
                                         safe_excerpt=it.get("safe_excerpt", ""),
                                         source_locator=it.get("source_locator"),
                                         assertion_type=it.get("assertion_type", "explicit"),
                                         confidence=it.get("confidence", 0),
                                         sensitive=it.get("sensitive", False)))
        return out

    def list_evidence(self, analysis_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM resume_evidence WHERE analysis_id = ? ORDER BY created_at ASC",
                (str(analysis_id),),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["source_locator"] = json.loads(d.pop("source_locator_json") or "{}")
            d["sensitive"] = bool(d.pop("sensitive"))
            out.append(d)
        return out

    def add_direction(self, analysis_id, name, direction_type, *, rationale="", gaps=None,
                      confidence=0, default_enabled=False, search_terms=None, contract_version="v1") -> dict:
        did = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO career_directions (id, analysis_id, name, direction_type, rationale, gaps_json, "
                "confidence, default_enabled, search_terms_json, contract_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (did, str(analysis_id), name, direction_type, rationale,
                 json.dumps(gaps or [], ensure_ascii=False), int(confidence),
                 int(bool(default_enabled)),
                 json.dumps(search_terms or [], ensure_ascii=False), contract_version, ts),
            )
        return {"id": did, "analysis_id": str(analysis_id), "name": name, "direction_type": direction_type,
                "rationale": rationale, "gaps": gaps or [], "confidence": int(confidence),
                "default_enabled": bool(default_enabled), "search_terms": search_terms or [],
                "contract_version": contract_version, "created_at": ts}

    def add_direction_batch(self, analysis_id, directions) -> list:
        return [self.add_direction(analysis_id, d["name"], d["direction_type"],
                                   rationale=d.get("rationale", ""), gaps=d.get("gaps"),
                                   confidence=d.get("confidence", 0),
                                   default_enabled=d.get("default_enabled", False),
                                   search_terms=d.get("search_terms"),
                                   contract_version=d.get("contract_version", "v1")) for d in directions]

    def link_direction_evidence(self, direction_id, evidence_id, role="primary"):
        # data-model.md:108 — direction and evidence must belong to the same analysis.
        with self._connection() as conn:
            d_row = conn.execute(
                "SELECT analysis_id FROM career_directions WHERE id = ?",
                (str(direction_id),),
            ).fetchone()
            e_row = conn.execute(
                "SELECT analysis_id FROM resume_evidence WHERE id = ?",
                (str(evidence_id),),
            ).fetchone()
            if (d_row is None or e_row is None
                    or d_row["analysis_id"] != e_row["analysis_id"]):
                raise ValueError("cross_analysis_link")
            conn.execute(
                "INSERT OR IGNORE INTO direction_evidence (direction_id, evidence_id, role) VALUES (?, ?, ?)",
                (str(direction_id), str(evidence_id), role),
            )

    def list_directions(self, analysis_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT d.* FROM career_directions d "
                "JOIN candidate_analyses a ON a.id = d.analysis_id "
                "WHERE d.analysis_id = ? AND a.status <> 'deleted' ORDER BY d.created_at ASC",
                (str(analysis_id),),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["gaps"] = json.loads(d.pop("gaps_json") or "[]")
            d["default_enabled"] = bool(d.pop("default_enabled"))
            d["search_terms"] = json.loads(d.pop("search_terms_json") or "[]")
            out.append(d)
        return out

    def list_direction_evidence(self, direction_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT evidence_id, role FROM direction_evidence WHERE direction_id = ?",
                (str(direction_id),),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_confirmation(self, profile_id, resume_id, analysis_id, *, hard_constraints,
                            soft_preferences, safe_limits, directions, version=None) -> dict:
        cid = _uuid()
        ts = _now()
        with self._connection() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS next_v FROM direction_confirmations WHERE profile_id = ?",
                    (str(profile_id),),
                ).fetchone()
                version = int(row["next_v"])
            conn.execute(
                "INSERT INTO direction_confirmations (id, profile_id, resume_id, analysis_id, version, "
                "hard_constraints_json, soft_preferences_json, safe_limits_json, confirmed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, str(profile_id), str(resume_id), str(analysis_id), version,
                 json.dumps(hard_constraints, ensure_ascii=False),
                 json.dumps(soft_preferences, ensure_ascii=False),
                 json.dumps(safe_limits, ensure_ascii=False), ts),
            )
            for d in directions:
                conn.execute(
                    "INSERT OR IGNORE INTO confirmation_directions "
                    "(confirmation_id, direction_id, enabled, user_added, user_label) VALUES (?, ?, ?, ?, ?)",
                    (cid, str(d["direction_id"]), int(bool(d.get("enabled", True))),
                     int(bool(d.get("user_added", False))), d.get("user_label")),
                )
        return {"id": cid, "profile_id": str(profile_id), "resume_id": str(resume_id),
                "analysis_id": str(analysis_id), "version": version, "confirmed_at": ts}

    def create_confirmation_v2(
        self, *, candidate_profile_version_id, expected_content_hash, hard_constraints,
        soft_preferences, safe_limits, directions, intent_hash,
    ) -> dict:
        """Atomically confirm one profile draft and freeze its typed intent."""
        cid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile_version = self._candidate_profile_version_view(
                conn, candidate_profile_version_id,
            )
            if profile_version["status"] != "draft":
                raise ValueError("candidate_version_not_draft")
            if profile_version["content_hash"] != expected_content_hash:
                raise ValueError("candidate_version_conflict")
            version = int(conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM direction_confirmations WHERE profile_id=?",
                (profile_version["profile_id"],),
            ).fetchone()[0])
            conn.execute(
                "UPDATE candidate_profile_versions SET status='confirmed', confirmed_at=?, updated_at=? WHERE id=?",
                (ts, ts, str(candidate_profile_version_id)),
            )
            conn.execute(
                "INSERT INTO direction_confirmations "
                "(id, profile_id, resume_id, analysis_id, version, hard_constraints_json, "
                "soft_preferences_json, safe_limits_json, confirmed_at, candidate_profile_version_id, "
                "intent_contract_version, intent_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'intent_v2', ?)",
                (cid, profile_version["profile_id"], profile_version["resume_id"],
                 profile_version["analysis_id"], version,
                 json.dumps(hard_constraints or {}, ensure_ascii=False),
                 json.dumps(soft_preferences or {}, ensure_ascii=False),
                 json.dumps(safe_limits or {}, ensure_ascii=False), ts,
                 str(candidate_profile_version_id), str(intent_hash)),
            )
            for direction in directions:
                conn.execute(
                    "INSERT INTO confirmation_directions "
                    "(confirmation_id, direction_id, enabled, user_added, user_label) VALUES (?, ?, ?, ?, ?)",
                    (cid, str(direction["direction_id"]), int(bool(direction.get("enabled", True))),
                     int(bool(direction.get("user_added", False))), direction.get("user_label")),
                )
        return self.get_confirmation(cid)

    def get_confirmation(self, confirmation_id) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM direction_confirmations WHERE id = ?", (str(confirmation_id),),
            ).fetchone()
            if row is None:
                raise KeyError(confirmation_id)
            d = dict(row)
            d["hard_constraints"] = json.loads(d.pop("hard_constraints_json") or "{}")
            d["soft_preferences"] = json.loads(d.pop("soft_preferences_json") or "{}")
            d["safe_limits"] = json.loads(d.pop("safe_limits_json") or "{}")
            drows = conn.execute(
                "SELECT * FROM confirmation_directions WHERE confirmation_id = ?", (str(confirmation_id),),
            ).fetchall()
            d["directions"] = [
                {"direction_id": r["direction_id"], "enabled": bool(r["enabled"]),
                 "user_added": bool(r["user_added"]), "user_label": r["user_label"]}
                for r in drows
            ]
            return d

    # ===================================================================
    # 005 candidate profile versions and fact drafts
    # ===================================================================

    def _candidate_profile_version_view(self, conn, version_id) -> dict:
        row = conn.execute(
            "SELECT * FROM candidate_profile_versions WHERE id=?", (str(version_id),),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        result = dict(row)
        result["summary"] = _decode_json(result.pop("summary_json"), {})
        result["unknowns"] = _decode_json(result.pop("unknowns_json"), [])
        fact_rows = conn.execute(
            "SELECT * FROM candidate_fact_items WHERE profile_version_id=? "
            "ORDER BY created_at ASC, id ASC",
            (str(version_id),),
        ).fetchall()
        facts = []
        for fact_row in fact_rows:
            fact = dict(fact_row)
            fact["value"] = _decode_json(fact.pop("value_json"), {})
            evidence_rows = conn.execute(
                "SELECT evidence_id FROM candidate_fact_evidence WHERE fact_id=? ORDER BY evidence_id",
                (fact["id"],),
            ).fetchall()
            fact["evidence_ids"] = [item["evidence_id"] for item in evidence_rows]
            facts.append(fact)
        result["facts"] = facts
        return result

    def get_candidate_profile_version(self, version_id) -> dict:
        with self._connection() as conn:
            return self._candidate_profile_version_view(conn, version_id)

    def create_candidate_profile_version(
        self, *, profile_id, resume_id, analysis_id, summary=None, unknowns=None,
        facts=None, contract_version="candidate_profile_v1",
    ) -> dict:
        ts = _now()
        version_id = _uuid()
        fact_inputs = list(facts or [])
        prepared = []
        for item in fact_inputs:
            prepared.append({
                "id": _uuid(),
                "fact_type": item["fact_type"],
                "stable_key": item.get("stable_key") or f"{item['fact_type']}:{item.get('client_ref') or _uuid()}",
                "value": dict(item.get("value") or {}),
                "normalized_value": str(item.get("normalized_value") or ""),
                "source_kind": item.get("source_kind", "resume_explicit"),
                "assertion_type": item.get("assertion_type", "explicit"),
                "confidence": int(item.get("confidence", 0)),
                "verification_status": item.get("verification_status", "extracted"),
                "supersedes_fact_id": item.get("supersedes_fact_id"),
                "evidence_ids": list(dict.fromkeys(item.get("evidence_ids", []) or [])),
            })
        content_hash = _candidate_profile_content_hash(summary, unknowns, prepared)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            next_version = int(conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM candidate_profile_versions WHERE profile_id=?",
                (str(profile_id),),
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO candidate_profile_versions "
                "(id, profile_id, resume_id, analysis_id, version, status, summary_json, "
                "unknowns_json, contract_version, content_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
                (version_id, str(profile_id), str(resume_id), _opt_str(analysis_id), next_version,
                 json.dumps(summary or {}, ensure_ascii=False),
                 json.dumps(unknowns or [], ensure_ascii=False), contract_version,
                 content_hash, ts, ts),
            )
            for fact in prepared:
                conn.execute(
                    "INSERT INTO candidate_fact_items "
                    "(id, profile_version_id, fact_type, stable_key, value_json, normalized_value, "
                    "source_kind, assertion_type, confidence, verification_status, supersedes_fact_id, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (fact["id"], version_id, fact["fact_type"], fact["stable_key"],
                     json.dumps(fact["value"], ensure_ascii=False), fact["normalized_value"],
                     fact["source_kind"], fact["assertion_type"], fact["confidence"],
                     fact["verification_status"], _opt_str(fact["supersedes_fact_id"]), ts, ts),
                )
                for evidence_id in fact["evidence_ids"]:
                    conn.execute(
                        "INSERT INTO candidate_fact_evidence (fact_id, evidence_id, role) VALUES (?, ?, 'primary')",
                        (fact["id"], str(evidence_id)),
                    )
        return self.get_candidate_profile_version(version_id)

    def update_candidate_profile_draft(
        self, version_id, *, expected_content_hash, operations=None, unknown_resolutions=None,
    ) -> dict:
        ts = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._candidate_profile_version_view(conn, version_id)
            if current["status"] != "draft":
                raise ValueError("candidate_version_not_draft")
            if current["content_hash"] != expected_content_hash:
                raise ValueError("candidate_version_conflict")
            for operation in operations or []:
                op = operation.get("op") if isinstance(operation, dict) else None
                if op in {"correct", "reject"}:
                    fact_id = str(operation.get("fact_id") or "")
                    fact = conn.execute(
                        "SELECT * FROM candidate_fact_items WHERE id=? AND profile_version_id=?",
                        (fact_id, str(version_id)),
                    ).fetchone()
                    if fact is None:
                        raise ValueError("candidate_fact_invalid")
                if op == "reject":
                    conn.execute(
                        "UPDATE candidate_fact_items SET verification_status='rejected', updated_at=? WHERE id=?",
                        (ts, fact_id),
                    )
                elif op == "correct":
                    old = dict(fact)
                    conn.execute(
                        "UPDATE candidate_fact_items SET stable_key=?, verification_status='rejected', updated_at=? WHERE id=?",
                        (f"rejected:{fact_id}", ts, fact_id),
                    )
                    new_id = _uuid()
                    conn.execute(
                        "INSERT INTO candidate_fact_items "
                        "(id, profile_version_id, fact_type, stable_key, value_json, normalized_value, "
                        "source_kind, assertion_type, confidence, verification_status, supersedes_fact_id, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'user_corrected', "
                        "'explicit', 100, 'corrected', ?, ?, ?)",
                        (new_id, str(version_id), old["fact_type"], old["stable_key"],
                         json.dumps(operation.get("value") or {}, ensure_ascii=False),
                         str(operation.get("normalized_value") or ""), fact_id, ts, ts),
                    )
                elif op == "add":
                    fact_type = operation.get("fact_type")
                    new_id = _uuid()
                    conn.execute(
                        "INSERT INTO candidate_fact_items "
                        "(id, profile_version_id, fact_type, stable_key, value_json, normalized_value, "
                        "source_kind, assertion_type, confidence, verification_status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'user_added', 'explicit', 100, 'confirmed', ?, ?)",
                        (new_id, str(version_id), fact_type,
                         operation.get("stable_key") or f"{fact_type}:user:{new_id}",
                         json.dumps(operation.get("value") or {}, ensure_ascii=False),
                         str(operation.get("normalized_value") or ""), ts, ts),
                    )
                else:
                    raise ValueError("candidate_fact_invalid")
            unknowns = current["unknowns"] if unknown_resolutions is None else list(unknown_resolutions)
            refreshed = self._candidate_profile_version_view(conn, version_id)
            content_hash = _candidate_profile_content_hash(current["summary"], unknowns, refreshed["facts"])
            conn.execute(
                "UPDATE candidate_profile_versions SET unknowns_json=?, content_hash=?, updated_at=? WHERE id=?",
                (json.dumps(unknowns, ensure_ascii=False), content_hash, ts, str(version_id)),
            )
        return self.get_candidate_profile_version(version_id)

    def confirm_candidate_profile_version(self, version_id, *, expected_content_hash) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._candidate_profile_version_view(conn, version_id)
            if current["status"] != "draft":
                raise ValueError("candidate_version_not_draft")
            if current["content_hash"] != expected_content_hash:
                raise ValueError("candidate_version_conflict")
            ts = _now()
            conn.execute(
                "UPDATE candidate_profile_versions SET status='confirmed', confirmed_at=?, updated_at=? WHERE id=?",
                (ts, ts, str(version_id)),
            )
        return self.get_candidate_profile_version(version_id)

    def copy_candidate_profile_draft(self, source_version_id) -> dict:
        source = self.get_candidate_profile_version(source_version_id)
        copied = self.create_candidate_profile_version(
            profile_id=source["profile_id"], resume_id=source["resume_id"],
            analysis_id=source["analysis_id"], summary=source["summary"],
            unknowns=source["unknowns"], contract_version=source["contract_version"],
            facts=[{
                **fact,
                "evidence_ids": fact.get("evidence_ids", []),
            } for fact in source["facts"]],
        )
        with self._connection() as conn:
            conn.execute(
                "UPDATE candidate_profile_versions SET supersedes_version_id=?, updated_at=? WHERE id=?",
                (str(source_version_id), _now(), copied["id"]),
            )
        return self.get_candidate_profile_version(copied["id"])

    def tombstone_candidate_profile_version(self, version_id) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._candidate_profile_version_view(conn, version_id)
            if current["status"] != "draft":
                raise ValueError("candidate_version_not_draft")
            conn.execute("DELETE FROM candidate_fact_items WHERE profile_version_id=?", (str(version_id),))
            empty_hash = _candidate_profile_content_hash({}, [], [])
            conn.execute(
                "UPDATE candidate_profile_versions SET status='deleted', summary_json='{}', "
                "unknowns_json='[]', content_hash=?, updated_at=? WHERE id=?",
                (empty_hash, _now(), str(version_id)),
            )
        return self.get_candidate_profile_version(version_id)

    # ===================================================================
    # 004 discovery: runs, plans, snapshots, assessments, feedback
    # ===================================================================

    def create_discovery_run(self, profile_id, resume_id, analysis_id, confirmation_id, *,
                             input_hash, policy_version="v1") -> dict:
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            if policy_version == "discovery_v2":
                conn.execute(
                    "INSERT INTO discovery_runs (id, profile_id, resume_id, analysis_id, confirmation_id, "
                    "status, stage, policy_version, input_hash, list_candidate_count, "
                    "detail_selected_count, detail_completed_count, assessment_completed_count, "
                    "recommendation_count, detail_reused_count, ai_call_count, result_revision, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'created', 'created', ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)",
                    (rid, str(profile_id), str(resume_id), str(analysis_id), str(confirmation_id),
                     policy_version, input_hash, ts, ts),
                )
            else:
                conn.execute(
                    "INSERT INTO discovery_runs (id, profile_id, resume_id, analysis_id, confirmation_id, "
                    "status, stage, policy_version, input_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'created', 'created', ?, ?, ?, ?)",
                    (rid, str(profile_id), str(resume_id), str(analysis_id), str(confirmation_id),
                     policy_version, input_hash, ts, ts),
                )
        return {"id": rid, "profile_id": str(profile_id), "resume_id": str(resume_id),
                "analysis_id": str(analysis_id), "confirmation_id": str(confirmation_id),
                "status": "created", "stage": "created", "policy_version": policy_version,
                "input_hash": input_hash, "created_at": ts, "updated_at": ts}

    def update_discovery_run(self, run_id, *, status=None, stage=None, failure_code=None,
                             failure_stage=None, counters=None, cancel_requested=False,
                             started=False, completed=False):
        # Terminal-state immutability: succeeded/failed/cancelled may not move
        # back into an active stage. `interrupted` and `partial` remain
        # resumable per the state machine contract.
        TERMINAL = {"succeeded", "failed", "cancelled"}
        if status is not None and status not in TERMINAL:
            current = self.get_discovery_run(run_id)
            if current["status"] in TERMINAL:
                raise ValueError(
                    f"run {run_id} is terminal ({current['status']}); "
                    f"cannot transition to {status}"
                )
        sets = ["updated_at = ?"]
        params = [_now()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if stage is not None:
            sets.append("stage = ?")
            params.append(stage)
        if failure_code is not None:
            sets.append("failure_code = ?")
            params.append(failure_code)
        if failure_stage is not None:
            sets.append("failure_stage = ?")
            params.append(failure_stage)
        if cancel_requested:
            sets.append("cancel_requested_at = ?")
            params.append(_now())
        if started:
            sets.append("started_at = ?")
            params.append(_now())
        if completed:
            sets.append("completed_at = ?")
            params.append(_now())
        if counters and isinstance(counters, dict):
            for key in ("source_count", "detail_count", "evaluated_count", "high_count",
                        "adjacent_count", "growth_count", "review_count", "unsuitable_count",
                        "result_revision", "list_candidate_count", "detail_selected_count",
                        "detail_completed_count", "assessment_completed_count"):
                if key in counters:
                    sets.append(f"{key} = ?")
                    params.append(int(counters[key]))
        params.append(str(run_id))
        with self._connection() as conn:
            conn.execute(f"UPDATE discovery_runs SET {', '.join(sets)} WHERE id = ?", params)

    def mark_run_timing(self, run_id, *, first_result_at=None, first_batch_at=None,
                        list_completed_at=None, processing_completed_at=None):
        """T077: set additive timing fields on discovery_runs.

        ``first_result_at`` and ``first_batch_at`` are monotonic: once set
        they are never overwritten (NULL → value only). This supports the
        http-api.md L218-220 contract where these timestamps mark the first
        occurrence of a visible result / first batch of five.

        ``list_completed_at`` and ``processing_completed_at`` mark stage
        boundaries and may be re-stamped on resume.
        """
        sets: list[str] = []
        params: list = []
        if first_result_at is not None:
            # COALESCE keeps the existing value if already non-NULL.
            sets.append("first_result_at = COALESCE(first_result_at, ?)")
            params.append(first_result_at)
        if first_batch_at is not None:
            sets.append("first_batch_at = COALESCE(first_batch_at, ?)")
            params.append(first_batch_at)
        if list_completed_at is not None:
            sets.append("list_completed_at = ?")
            params.append(list_completed_at)
        if processing_completed_at is not None:
            sets.append("processing_completed_at = ?")
            params.append(processing_completed_at)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(str(run_id))
        with self._connection() as conn:
            conn.execute(
                f"UPDATE discovery_runs SET {', '.join(sets)} WHERE id = ?", params,
            )

    def transition_discovery_run_v2(
        self,
        run_id,
        *,
        expected_state,
        target_state,
        input_hash,
        counters=None,
        event_type=None,
        event_payload=None,
    ) -> dict:
        """CAS one policy-v2 run transition with counters and event in one transaction."""
        from webui.discovery import (
            DiscoveryError,
            require_matching_input_hash,
            sanitize_discovery_payload,
            validate_v2_run_transition,
        )

        counter_fields = {
            "list_candidate_count", "detail_selected_count", "detail_completed_count",
            "assessment_completed_count", "recommendation_count", "detail_reused_count",
            "ai_call_count", "result_revision",
        }
        clean_counters = {}
        if counters is not None:
            if not isinstance(counters, dict) or not set(counters).issubset(counter_fields):
                raise DiscoveryError("state_conflict")
            for key, value in counters.items():
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise DiscoveryError("state_conflict")
                clean_counters[key] = value
        clean_event = None
        if event_type is not None:
            if not isinstance(event_type, str) or not event_type or len(event_type) > 100:
                raise DiscoveryError("detail_event_invalid")
            clean_event = sanitize_discovery_payload(event_payload or {}, payload_kind="event")

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM discovery_runs WHERE id=?", (str(run_id),),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = dict(row)
            if current["policy_version"] != "discovery_v2":
                raise DiscoveryError("state_conflict")
            if current["stage"] != expected_state:
                raise DiscoveryError("state_conflict")
            require_matching_input_hash(input_hash, current["input_hash"])
            validate_v2_run_transition(current["stage"], target_state)

            sets = ["status=?", "stage=?", "updated_at=?"]
            params = [target_state, target_state, _now()]
            for key, value in clean_counters.items():
                sets.append(f"{key}=?")
                params.append(value)
            if target_state in {"succeeded", "partial", "failed", "cancelled"}:
                sets.append("completed_at=?")
                params.append(_now())
            params.extend([str(run_id), expected_state, input_hash])
            changed = conn.execute(
                f"UPDATE discovery_runs SET {', '.join(sets)} "
                "WHERE id=? AND stage=? AND input_hash=?",
                params,
            ).rowcount
            if changed != 1:
                raise DiscoveryError("state_conflict")
            if event_type is not None:
                seq = int(conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM discovery_run_events WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()[0])
                conn.execute(
                    "INSERT INTO discovery_run_events "
                    "(run_id, sequence, event_type, safe_payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (str(run_id), seq, event_type,
                     json.dumps(clean_event, ensure_ascii=False), _now()),
                )
        return self.get_discovery_run(run_id)

    def reconcile_discovery_run_v2(self, run_id) -> dict:
        """Rebuild v2 counters from persisted rows and append a safe checkpoint event."""
        from webui.discovery import DiscoveryError, sanitize_discovery_payload

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT policy_version FROM discovery_runs WHERE id=?", (str(run_id),),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["policy_version"] != "discovery_v2":
                raise DiscoveryError("state_conflict")

            scalar_queries = {
                "list_candidate_count": (
                    "SELECT COUNT(*) FROM discovery_run_candidates WHERE run_id=?"
                ),
                "detail_selected_count": (
                    "SELECT COUNT(*) FROM discovery_run_candidates "
                    "WHERE run_id=? AND selection_decision='selected'"
                ),
                "detail_completed_count": (
                    "SELECT COUNT(*) FROM discovery_job_snapshots WHERE run_id=? "
                    "AND completeness IN ('complete','partial','unavailable') "
                    "AND fetch_status NOT IN ('queued','fetching')"
                ),
                "assessment_completed_count": (
                    "SELECT COUNT(*) FROM job_direction_assessments WHERE run_id=? "
                    "AND status NOT IN ('queued','running','evaluating')"
                ),
                "recommendation_count": (
                    "SELECT COUNT(DISTINCT snapshot_id) FROM job_direction_assessments WHERE run_id=? "
                    "AND status NOT IN ('queued','running','evaluating') "
                    "AND category IN ('high_match','adjacent_match','growth_match')"
                ),
                "detail_reused_count": (
                    "SELECT COUNT(*) FROM discovery_job_snapshots WHERE run_id=? "
                    "AND reused_from_snapshot_id IS NOT NULL"
                ),
                "ai_call_count": (
                    "SELECT COALESCE(SUM(ai_call_count), 0) FROM job_direction_assessments WHERE run_id=?"
                ),
            }
            counters = {
                key: int(conn.execute(sql, (str(run_id),)).fetchone()[0] or 0)
                for key, sql in scalar_queries.items()
            }
            assignments = ", ".join(f"{key}=?" for key in counters)
            conn.execute(
                f"UPDATE discovery_runs SET {assignments}, updated_at=? WHERE id=?",
                [*counters.values(), _now(), str(run_id)],
            )
            payload = sanitize_discovery_payload(counters, payload_kind="event")
            seq = int(conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM discovery_run_events WHERE run_id=?",
                (str(run_id),),
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO discovery_run_events "
                "(run_id, sequence, event_type, safe_payload_json, created_at) VALUES (?, ?, 'progress_reconciled', ?, ?)",
                (str(run_id), seq, json.dumps(payload, ensure_ascii=False), _now()),
            )
        return self.get_discovery_run(run_id)

    def get_discovery_run(self, run_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM discovery_runs WHERE id = ?", (str(run_id),)).fetchone()
            if row is None:
                raise KeyError(run_id)
            d = dict(row)
        # T077: v2 four-class progress names (http-api.md L203-208).
        # Legacy v1 names (source_count/detail_count/evaluated_count) kept as
        # compatibility aliases; v2 names are authoritative for policy v2 runs.
        d["progress"] = {
            "source_count": d["source_count"], "detail_count": d["detail_count"],
            "evaluated_count": d["evaluated_count"],
            # v2 four-class progress (authoritative for discovery_v2 runs).
            "search_queries_completed": d["source_count"],
            "list_candidates": d.get("list_candidate_count"),
            "details_selected": d.get("detail_selected_count"),
            "details_completed": d.get("detail_completed_count"),
            "assessments_completed": d.get("assessment_completed_count"),
            "recommendations": d.get("recommendation_count"),
        }
        d["counts"] = {
            "high": d["high_count"], "adjacent": d["adjacent_count"], "growth": d["growth_count"],
            "review": d["review_count"], "unsuitable": d["unsuitable_count"],
        }
        return d

    def list_discovery_runs(self, profile_id=None, limit=30) -> list:
        with self._connection() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT * FROM discovery_runs WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(profile_id), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM discovery_runs ORDER BY created_at DESC LIMIT ?", (int(limit),),
                ).fetchall()
        return [dict(r) for r in rows]

    def append_discovery_event(self, run_id, event_type, payload=None):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_s FROM discovery_run_events WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            seq = int(row["next_s"])
            conn.execute(
                "INSERT INTO discovery_run_events (run_id, sequence, event_type, safe_payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(run_id), seq, event_type, json.dumps(payload or {}, ensure_ascii=False), _now()),
            )
        return seq

    def list_discovery_events(self, run_id, after=0) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM discovery_run_events WHERE run_id = ? AND sequence > ? ORDER BY sequence ASC",
                (str(run_id), int(after)),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["payload"] = json.loads(d.pop("safe_payload_json") or "{}")
            out.append(d)
        return out

    # -- discovery run candidates (005 US2) --------------------------------

    def upsert_run_candidate(self, *, run_id, job_id, source_url, direction_ids,
                             search_terms, source_positions, list_fields, input_hash) -> dict:
        """Create or merge a run candidate. Same (run_id, job_id) merges provenance."""
        rid, jid = str(run_id), str(job_id)
        canonical = source_url.split("?")[0].rstrip("/")
        dedupe_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        ts = _now()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id, direction_ids_json, search_terms_json, source_positions_json, list_fields_json "
                "FROM discovery_run_candidates WHERE run_id = ? AND job_id = ?",
                (rid, jid),
            ).fetchone()
            if existing is None:
                cid = _uuid()
                conn.execute(
                    "INSERT INTO discovery_run_candidates "
                    "(id, run_id, job_id, source_url, direction_ids_json, search_terms_json, "
                    "source_positions_json, list_fields_json, dedupe_key, precheck_outcome, "
                    "precheck_json, priority_components_json, selection_decision, state, "
                    "attempt_count, input_hash, discovered_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', '{}', '{}', 'pending', 'discovered', 0, ?, ?, ?)",
                    (cid, rid, jid, source_url,
                     json.dumps(direction_ids, ensure_ascii=False),
                     json.dumps(search_terms, ensure_ascii=False),
                     json.dumps(source_positions, ensure_ascii=False),
                     json.dumps(list_fields, ensure_ascii=False),
                     dedupe_key, input_hash, ts, ts),
                )
            else:
                cid = existing["id"]
                merged_dirs = json.loads(existing["direction_ids_json"] or "[]")
                for d in direction_ids:
                    if d not in merged_dirs:
                        merged_dirs.append(d)
                merged_terms = json.loads(existing["search_terms_json"] or "[]")
                for t in search_terms:
                    if t not in merged_terms:
                        merged_terms.append(t)
                merged_positions = json.loads(existing["source_positions_json"] or "[]")
                merged_positions.extend(source_positions)
                merged_fields = json.loads(existing["list_fields_json"] or "{}")
                merged_fields.update(list_fields)
                conn.execute(
                    "UPDATE discovery_run_candidates SET "
                    "direction_ids_json = ?, search_terms_json = ?, source_positions_json = ?, "
                    "list_fields_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(merged_dirs, ensure_ascii=False),
                     json.dumps(merged_terms, ensure_ascii=False),
                     json.dumps(merged_positions, ensure_ascii=False),
                     json.dumps(merged_fields, ensure_ascii=False),
                     ts, cid),
                )
        return self.get_run_candidate(cid)

    def get_run_candidate(self, candidate_id) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_run_candidates WHERE id = ?", (str(candidate_id),),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        d = dict(row)
        d["direction_ids"] = json.loads(d.pop("direction_ids_json") or "[]")
        d["search_terms"] = json.loads(d.pop("search_terms_json") or "[]")
        d["source_positions"] = json.loads(d.pop("source_positions_json") or "[]")
        d["list_fields"] = json.loads(d.pop("list_fields_json") or "{}")
        d["precheck"] = json.loads(d.pop("precheck_json") or "{}")
        d["priority_components"] = json.loads(d.pop("priority_components_json") or "{}")
        return d

    def list_run_candidates(self, run_id, *, state=None, selection_decision=None) -> list:
        query = "SELECT * FROM discovery_run_candidates WHERE run_id = ?"
        params: list = [str(run_id)]
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        if selection_decision is not None:
            query += " AND selection_decision = ?"
            params.append(selection_decision)
        query += " ORDER BY discovered_at ASC"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["direction_ids"] = json.loads(d.pop("direction_ids_json") or "[]")
            d["search_terms"] = json.loads(d.pop("search_terms_json") or "[]")
            d["source_positions"] = json.loads(d.pop("source_positions_json") or "[]")
            d["list_fields"] = json.loads(d.pop("list_fields_json") or "{}")
            d["precheck"] = json.loads(d.pop("precheck_json") or "{}")
            d["priority_components"] = json.loads(d.pop("priority_components_json") or "{}")
            out.append(d)
        return out

    def update_run_candidate_state(self, candidate_id, *, state=None, selection_decision=None,
                                   selection_reason=None, selection_rank=None,
                                   precheck_outcome=None, precheck=None,
                                   priority_components=None, failure_code=None,
                                   expected_state=None) -> dict:
        """CAS-guarded state update for a run candidate."""
        cid = str(candidate_id)
        sets = ["updated_at = ?"]
        params: list = [_now()]
        if state is not None:
            sets.append("state = ?"); params.append(state)
        if selection_decision is not None:
            sets.append("selection_decision = ?"); params.append(selection_decision)
        if selection_reason is not None:
            sets.append("selection_reason = ?"); params.append(selection_reason)
        if selection_rank is not None:
            sets.append("selection_rank = ?"); params.append(int(selection_rank))
        if precheck_outcome is not None:
            sets.append("precheck_outcome = ?"); params.append(precheck_outcome)
        if precheck is not None:
            sets.append("precheck_json = ?"); params.append(json.dumps(precheck, ensure_ascii=False))
        if priority_components is not None:
            sets.append("priority_components_json = ?"); params.append(json.dumps(priority_components, ensure_ascii=False))
        if failure_code is not None:
            sets.append("failure_code = ?"); params.append(failure_code)
        where = "id = ?"
        where_params: list = [cid]
        if expected_state is not None:
            where += " AND state = ?"
            where_params.append(expected_state)
        params.extend(where_params)
        with self._connection() as conn:
            cursor = conn.execute(
                f"UPDATE discovery_run_candidates SET {', '.join(sets)} WHERE {where}", params,
            )
            if cursor.rowcount == 0 and expected_state is not None:
                raise DiscoveryStoreConflictError(
                    f"run candidate {cid} state conflict (expected {expected_state})"
                )
        return self.get_run_candidate(cid)

    def create_search_plan(self, run_id, *, plan_version="v1", detail_budget=60,
                           items=None) -> dict:
        pid = _uuid()
        ts = _now()
        item_list = items or []
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO search_plans (id, run_id, plan_version, status, item_count, detail_budget, "
                "created_at) VALUES (?, ?, ?, 'ready', ?, ?, ?)",
                (pid, str(run_id), plan_version, len(item_list), int(detail_budget), ts),
            )
            for it in item_list:
                iid = _uuid()
                conn.execute(
                    "INSERT INTO search_plan_items (id, plan_id, keyword, city, source_filters_json, "
                    "direction_ids_json, input_hash, status, target_pages, detail_budget, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                    (iid, pid, it["keyword"], it.get("city", ""),
                     json.dumps(it.get("source_filters", {}), ensure_ascii=False),
                     json.dumps(it.get("direction_ids", []), ensure_ascii=False),
                     it["input_hash"], int(it.get("target_pages", 1)),
                     int(it.get("detail_budget", 0)), ts, ts),
                )
        return {"id": pid, "run_id": str(run_id), "plan_version": plan_version,
                "status": "ready", "item_count": len(item_list), "detail_budget": detail_budget,
                "items": item_list}

    def get_search_plan(self, run_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM search_plans WHERE run_id = ?", (str(run_id),)).fetchone()
            if row is None:
                raise KeyError(run_id)
            d = dict(row)
            irows = conn.execute(
                "SELECT * FROM search_plan_items WHERE plan_id = ? ORDER BY created_at ASC",
                (d["id"],),
            ).fetchall()
            items = []
            for ir in irows:
                idd = dict(ir)
                idd["source_filters"] = json.loads(idd.pop("source_filters_json") or "{}")
                idd["direction_ids"] = json.loads(idd.pop("direction_ids_json") or "[]")
                items.append(idd)
            d["items"] = items
            return d

    def get_search_plan_item(self, item_id) -> dict:
        """Return a single search_plan_items row by its id, or raise KeyError."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM search_plan_items WHERE id = ?",
                (str(item_id),),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            idd = dict(row)
            idd["source_filters"] = json.loads(idd.pop("source_filters_json") or "{}")
            idd["direction_ids"] = json.loads(idd.pop("direction_ids_json") or "[]")
            return idd

    def update_plan_item(self, item_id, *, status=None, page_cursor=None, failure_code=None,
                         attempt=False, completed=False):
        sets = ["updated_at = ?"]
        params = [_now()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if page_cursor is not None:
            sets.append("page_cursor = ?")
            params.append(int(page_cursor))
        if failure_code is not None:
            sets.append("failure_code = ?")
            params.append(failure_code)
        if attempt:
            sets.append("attempt_count = attempt_count + 1")
        if completed:
            sets.append("completed_at = ?")
            params.append(_now())
        params.append(str(item_id))
        with self._connection() as conn:
            conn.execute(f"UPDATE search_plan_items SET {', '.join(sets)} WHERE id = ?", params)

    def save_job_snapshot(self, run_id, job_id, *, source_url="", title="", company="",
                          salary="", location="", tags="", jd="", company_json=None,
                          completeness="unavailable", missing_fields=None, source_status="unknown",
                          content_hash="", fetch_status="queued") -> dict:
        sid = _uuid()
        ts = _now()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM discovery_job_snapshots WHERE run_id = ? AND job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
            if existing:
                sid = existing["id"]
                conn.execute(
                    "UPDATE discovery_job_snapshots SET source_url=?, title=?, company=?, salary=?, "
                    "location=?, tags=?, jd=?, company_json=?, completeness=?, missing_fields_json=?, "
                    "source_status=?, content_hash=?, fetch_status=?, updated_at=? WHERE id=?",
                    (source_url, title, company, salary, location, tags, jd,
                     json.dumps(company_json or {}, ensure_ascii=False), completeness,
                     json.dumps(missing_fields or [], ensure_ascii=False), source_status,
                     content_hash, fetch_status, ts, sid),
                )
            else:
                conn.execute(
                    "INSERT INTO discovery_job_snapshots (id, run_id, job_id, source_url, title, company, "
                    "salary, location, tags, jd, company_json, completeness, missing_fields_json, "
                    "source_status, content_hash, fetch_status, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, str(run_id), str(job_id), source_url, title, company, salary, location,
                     tags, jd, json.dumps(company_json or {}, ensure_ascii=False), completeness,
                     json.dumps(missing_fields or [], ensure_ascii=False), source_status,
                     content_hash, fetch_status, ts),
                )
        return {"id": sid, "run_id": str(run_id), "job_id": str(job_id), "completeness": completeness,
                "source_status": source_status, "fetch_status": fetch_status}

    def get_snapshot(self, run_id, job_id) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_job_snapshots WHERE run_id = ? AND job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
            if row is None:
                raise KeyError((run_id, job_id))
            d = dict(row)
            d["company_json"] = json.loads(d.pop("company_json") or "{}")
            d["missing_fields"] = json.loads(d.pop("missing_fields_json") or "[]")
            d["reused"] = bool(d.get("reused_from_snapshot_id"))
            return d

    def reset_job_snapshot(self, run_id, job_id) -> None:
        """Reset a snapshot's fetch_status to 'queued' so the runner re-fetches it."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE discovery_job_snapshots SET fetch_status='queued', updated_at=? "
                "WHERE run_id=? AND job_id=?",
                (_now(), str(run_id), str(job_id)),
            )

    def list_snapshots(self, run_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM discovery_job_snapshots WHERE run_id = ? ORDER BY updated_at ASC",
                (str(run_id),),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["company_json"] = json.loads(d.pop("company_json") or "{}")
            d["missing_fields"] = json.loads(d.pop("missing_fields_json") or "[]")
            # T073: projection metadata for reuse. ``reused`` is derived from
            # ``reused_from_snapshot_id`` so callers don't need to know the
            # column name. ``source_fetched_at`` exposes the original capture
            # time for reused snapshots (NULL for fresh captures).
            d["reused"] = bool(d.get("reused_from_snapshot_id"))
            out.append(d)
        return out

    # ------------------------------------------------------------------
    # T073: Detail reuse (data-model.md L332-341)
    # ------------------------------------------------------------------

    _REUSE_FRESHNESS_HOURS = 12
    _REUSE_IDENTITY_FIELDS = ("title", "company", "salary", "location")

    @staticmethod
    def _canonical_source_url(source_url: str) -> str:
        """Canonicalize source URL for reuse identity: strip query, rstrip /."""
        return (source_url or "").split("?")[0].rstrip("/")

    def find_reusable_snapshot(
        self,
        job_id,
        source_url,
        current_list_fields,
        *,
        refresh_requested=False,
        max_age_hours=None,
        now_iso=None,
        exclude_run_id=None,
    ) -> dict | None:
        """Find a prior snapshot that can be reused for the current run.

        Returns None when any of these guards fail:
        - ``refresh_requested`` is True (user asked for fresh capture);
        - no prior snapshot exists for ``job_id`` with matching canonical URL;
        - prior snapshot ``completeness`` != ``'complete'``;
        - prior snapshot ``source_status`` != ``'active'``;
        - prior snapshot ``fresh_until`` is NULL or earlier than ``now_iso``;
        - prior snapshot identity fields (title/company/salary/location)
          drift from ``current_list_fields``.

        ``exclude_run_id`` skips snapshots belonging to the given run
        (typically the current run, to avoid self-reuse).

        Returned dict is the raw snapshot row (including ``run_id``) so
        callers can inspect provenance and pass it to
        :meth:`create_reused_snapshot`.
        """
        if refresh_requested:
            return None
        if not job_id or not source_url:
            return None
        jid = str(job_id)
        canonical = self._canonical_source_url(source_url)
        if not canonical:
            return None
        now = now_iso or _now()
        max_age = self._REUSE_FRESHNESS_HOURS if max_age_hours is None else int(max_age_hours)
        with self._connection() as conn:
            query = (
                "SELECT * FROM discovery_job_snapshots "
                "WHERE job_id = ? AND completeness = 'complete' "
                "AND source_status = 'active' "
                "AND fresh_until IS NOT NULL AND fresh_until >= ? "
            )
            params: list = [jid, now]
            if exclude_run_id is not None:
                query += "AND run_id != ? "
                params.append(str(exclude_run_id))
            query += "ORDER BY fetched_at DESC LIMIT 10"
            rows = conn.execute(query, params).fetchall()
        if not rows:
            return None
        current_fields = current_list_fields or {}
        for row in rows:
            d = dict(row)
            prior_canonical = self._canonical_source_url(d.get("source_url", ""))
            if prior_canonical != canonical:
                continue
            # Identity drift check: title/company/salary/location must match.
            drift = False
            for field in self._REUSE_IDENTITY_FIELDS:
                prior_val = str(d.get(field, "") or "").strip()
                curr_val = str(current_fields.get(field, "") or "").strip()
                if prior_val != curr_val:
                    drift = True
                    break
            if drift:
                continue
            # Found a reusable snapshot.
            d["company_json"] = json.loads(d.pop("company_json") or "{}")
            d["missing_fields"] = json.loads(d.pop("missing_fields_json") or "[]")
            d["reused"] = bool(d.get("reused_from_snapshot_id"))
            return d
        return None

    def create_reused_snapshot(
        self,
        run_id,
        run_candidate_id,
        source_snapshot,
        *,
        fetch_policy_version="discovery_v2",
        now_iso=None,
        max_age_hours=None,
    ) -> dict:
        """Create a new snapshot in ``run_id`` by copying ``source_snapshot``.

        The new snapshot is self-sufficient: it copies all content fields
        (jd/tags/company_json/completeness/source_status/content_hash) from
        the source, sets ``reused_from_snapshot_id`` to the source id, and
        records the original capture time in ``source_fetched_at`` so the
        chain remains readable even after the parent row is deleted.

        ``fetched_at`` is set to ``now_iso`` (current run time), and
        ``fresh_until`` is renewed to ``now_iso + max_age_hours`` so the
        reused snapshot itself can be reused by future runs within the
        freshness window.

        Increments ``detail_reused_count`` on the run.
        """
        if not isinstance(source_snapshot, dict):
            raise TypeError("source_snapshot must be a dict")
        rid = str(run_id)
        ts = now_iso or _now()
        max_age = self._REUSE_FRESHNESS_HOURS if max_age_hours is None else int(max_age_hours)
        try:
            from datetime import datetime, timedelta, timezone
            base = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            base = datetime.now(timezone.utc)
        fresh_until_dt = base + timedelta(hours=max_age)
        fresh_until = fresh_until_dt.isoformat()
        sid = _uuid()
        source_id = source_snapshot.get("id")
        if not source_id:
            raise ValueError("source_snapshot must contain 'id'")
        source_fetched_at = source_snapshot.get("fetched_at") or ""
        company_json = source_snapshot.get("company_json")
        if isinstance(company_json, str):
            try:
                company_json = json.loads(company_json or "{}")
            except json.JSONDecodeError:
                company_json = {}
        elif company_json is None:
            company_json = {}
        missing_fields = source_snapshot.get("missing_fields")
        if isinstance(missing_fields, str):
            try:
                missing_fields = json.loads(missing_fields or "[]")
            except json.JSONDecodeError:
                missing_fields = []
        elif missing_fields is None:
            missing_fields = []
        with self._connection() as conn:
            # Ensure no stale snapshot for (run_id, job_id) — reuse the row
            # if it exists (e.g., re-running progressive eval after interrupt).
            existing = conn.execute(
                "SELECT id FROM discovery_job_snapshots WHERE run_id = ? AND job_id = ?",
                (rid, str(source_snapshot.get("job_id", ""))),
            ).fetchone()
            if existing:
                sid = existing["id"]
                conn.execute(
                    "UPDATE discovery_job_snapshots SET "
                    "source_url=?, title=?, company=?, salary=?, location=?, tags=?, jd=?, "
                    "company_json=?, completeness=?, missing_fields_json=?, source_status=?, "
                    "content_hash=?, fetch_status='completed', fetched_at=?, fresh_until=?, "
                    "updated_at=?, run_candidate_id=?, reused_from_snapshot_id=?, "
                    "fetch_policy_version=?, source_fetched_at=? WHERE id=?",
                    (
                        str(source_snapshot.get("source_url", "")),
                        str(source_snapshot.get("title", "")),
                        str(source_snapshot.get("company", "")),
                        str(source_snapshot.get("salary", "")),
                        str(source_snapshot.get("location", "")),
                        str(source_snapshot.get("tags", "")),
                        str(source_snapshot.get("jd", "")),
                        json.dumps(company_json, ensure_ascii=False),
                        str(source_snapshot.get("completeness", "complete")),
                        json.dumps(missing_fields, ensure_ascii=False),
                        str(source_snapshot.get("source_status", "active")),
                        str(source_snapshot.get("content_hash", "")),
                        ts, fresh_until, ts,
                        str(run_candidate_id) if run_candidate_id else None,
                        str(source_id),
                        str(fetch_policy_version),
                        source_fetched_at or None,
                        sid,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO discovery_job_snapshots ("
                    "id, run_id, job_id, source_url, title, company, salary, location, "
                    "tags, jd, company_json, completeness, missing_fields_json, "
                    "source_status, content_hash, fetch_status, fetched_at, fresh_until, "
                    "updated_at, run_candidate_id, reused_from_snapshot_id, "
                    "fetch_policy_version, source_fetched_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sid, rid, str(source_snapshot.get("job_id", "")),
                        str(source_snapshot.get("source_url", "")),
                        str(source_snapshot.get("title", "")),
                        str(source_snapshot.get("company", "")),
                        str(source_snapshot.get("salary", "")),
                        str(source_snapshot.get("location", "")),
                        str(source_snapshot.get("tags", "")),
                        str(source_snapshot.get("jd", "")),
                        json.dumps(company_json, ensure_ascii=False),
                        str(source_snapshot.get("completeness", "complete")),
                        json.dumps(missing_fields, ensure_ascii=False),
                        str(source_snapshot.get("source_status", "active")),
                        str(source_snapshot.get("content_hash", "")),
                        "completed", ts, fresh_until, ts,
                        str(run_candidate_id) if run_candidate_id else None,
                        str(source_id),
                        str(fetch_policy_version),
                        source_fetched_at or None,
                    ),
                )
            # Increment detail_reused_count atomically.
            conn.execute(
                "UPDATE discovery_runs SET detail_reused_count = "
                "COALESCE(detail_reused_count, 0) + 1, updated_at = ? WHERE id = ?",
                (ts, rid),
            )
        return self.get_snapshot(rid, source_snapshot.get("job_id", ""))

    def create_assessment(self, run_id, snapshot_id, direction_id, *, hard_outcome="unknown",
                          hard_checks=None, dimensions=None, match_score=None, confidence=None,
                          category="needs_review", candidate_evidence_ids=None, job_evidence=None,
                          gaps=None, policy_version="v1", contract_version="v1",
                          failure_code=None, status="queued",
                          evaluation_group_id=None, input_hash=None, ai_call_count=None) -> dict:
        asid = _uuid()
        ts = _now()
        # Migration-015 columns are only written when supplied, so legacy v1
        # calls stay compatible with schema-14 databases (pre-migration-015).
        has_v2_cols = (
            evaluation_group_id is not None or input_hash is not None or ai_call_count is not None
        )
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM job_direction_assessments WHERE run_id=? AND snapshot_id=? AND direction_id=?",
                (str(run_id), str(snapshot_id), str(direction_id)),
            ).fetchone()
            if existing:
                asid = existing["id"]
                set_clause = (
                    "hard_outcome=?, hard_checks_json=?, dimensions_json=?, match_score=?, confidence=?, "
                    "category=?, candidate_evidence_ids_json=?, job_evidence_json=?, gaps_json=?, "
                    "failure_code=?, status=?"
                )
                params = [
                    hard_outcome, json.dumps(hard_checks or {}, ensure_ascii=False),
                    json.dumps(dimensions or {}, ensure_ascii=False), match_score, confidence, category,
                    json.dumps(candidate_evidence_ids or [], ensure_ascii=False),
                    json.dumps(job_evidence or {}, ensure_ascii=False),
                    json.dumps(gaps or [], ensure_ascii=False), failure_code, status,
                ]
                if has_v2_cols:
                    set_clause += ", evaluation_group_id=?, input_hash=?, ai_call_count=?"
                    params.extend([_opt_str(evaluation_group_id), _opt_str(input_hash), ai_call_count])
                set_clause += ", updated_at=?"
                params.extend([ts, asid])
                conn.execute(
                    f"UPDATE job_direction_assessments SET {set_clause} WHERE id=?", params,
                )
            else:
                columns = [
                    "id", "run_id", "snapshot_id", "direction_id", "status", "hard_outcome",
                    "hard_checks_json", "dimensions_json", "match_score", "confidence", "category",
                    "candidate_evidence_ids_json", "job_evidence_json", "gaps_json", "policy_version",
                    "contract_version", "failure_code",
                ]
                values = [
                    asid, str(run_id), str(snapshot_id), str(direction_id), status, hard_outcome,
                    json.dumps(hard_checks or {}, ensure_ascii=False),
                    json.dumps(dimensions or {}, ensure_ascii=False), match_score, confidence, category,
                    json.dumps(candidate_evidence_ids or [], ensure_ascii=False),
                    json.dumps(job_evidence or {}, ensure_ascii=False),
                    json.dumps(gaps or [], ensure_ascii=False), policy_version, contract_version,
                    failure_code,
                ]
                if has_v2_cols:
                    columns.extend(["evaluation_group_id", "input_hash", "ai_call_count"])
                    values.extend([_opt_str(evaluation_group_id), _opt_str(input_hash), ai_call_count])
                columns.extend(["created_at", "updated_at"])
                values.extend([ts, ts])
                placeholders = ", ".join(["?"] * len(columns))
                conn.execute(
                    f"INSERT INTO job_direction_assessments ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
            if status == "completed":
                conn.execute("UPDATE job_direction_assessments SET completed_at=? WHERE id=?", (ts, asid))
        return {"id": asid, "run_id": str(run_id), "snapshot_id": str(snapshot_id),
                "direction_id": str(direction_id), "category": category, "hard_outcome": hard_outcome,
                "status": status, "evaluation_group_id": _opt_str(evaluation_group_id),
                "input_hash": _opt_str(input_hash)}

    def get_assessment(self, run_id, snapshot_id, direction_id) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM job_direction_assessments WHERE run_id=? AND snapshot_id=? AND direction_id=?",
                (str(run_id), str(snapshot_id), str(direction_id)),
            ).fetchone()
            if row is None:
                raise KeyError((run_id, snapshot_id, direction_id))
            d = dict(row)
            d["hard_checks"] = json.loads(d.pop("hard_checks_json") or "{}")
            d["dimensions"] = json.loads(d.pop("dimensions_json") or "{}")
            d["candidate_evidence_ids"] = json.loads(d.pop("candidate_evidence_ids_json") or "[]")
            d["job_evidence"] = json.loads(d.pop("job_evidence_json") or "{}")
            d["gaps"] = json.loads(d.pop("gaps_json") or "[]")
            return d

    def list_assessments(self, run_id, *, category=None, direction_id=None) -> list:
        clauses = ["run_id = ?"]
        params = [str(run_id)]
        if category:
            clauses.append("category = ?")
            params.append(category)
        if direction_id:
            clauses.append("direction_id = ?")
            params.append(str(direction_id))
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM job_direction_assessments WHERE {' AND '.join(clauses)} ORDER BY updated_at ASC",
                params,
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["hard_checks"] = json.loads(d.pop("hard_checks_json") or "{}")
            d["dimensions"] = json.loads(d.pop("dimensions_json") or "{}")
            d["candidate_evidence_ids"] = json.loads(d.pop("candidate_evidence_ids_json") or "[]")
            d["job_evidence"] = json.loads(d.pop("job_evidence_json") or "{}")
            d["gaps"] = json.loads(d.pop("gaps_json") or "[]")
            out.append(d)
        return out

    def create_discovery_feedback(self, profile_id, target_type, action, *, run_id=None, job_id=None,
                                  direction_id=None, assessment_id=None, reason_code=None,
                                  scope="exact_job", safe_note=None) -> dict:
        fid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO discovery_feedback (id, profile_id, run_id, job_id, direction_id, assessment_id, "
                "target_type, action, reason_code, scope, safe_note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fid, str(profile_id),
                 _opt_str(run_id), _opt_str(job_id), _opt_str(direction_id), _opt_str(assessment_id),
                 target_type, action, _opt_str(reason_code), scope,
                 safe_note[:500] if safe_note else None, ts),
            )
        # T061: Bridge to legacy profile_jobs + screening_trash_records so that
        # discovery feedback is visible in the existing interested/trash zones
        # and persists across runs. Only applies to job-level feedback.
        if target_type == "job" and job_id:
            self._bridge_discovery_feedback_to_legacy(
                profile_id, job_id, action, run_id=run_id, reason_code=reason_code,
            )
        return {"id": fid, "profile_id": str(profile_id), "target_type": target_type, "action": action,
                "scope": scope, "created_at": ts}

    def _bridge_discovery_feedback_to_legacy(self, profile_id, job_id, action, *,
                                             run_id=None, reason_code=None):
        """Mirror job-level discovery feedback into profile_jobs + screening_trash_records.

        - ``not_interested`` -> profile_jobs.status='deleted' + screening_trash_records row.
        - ``interested`` -> profile_jobs.status='interested'.
        - Other actions -> no-op (only bridge explicit interest/trash).
        """
        # Ensure canonical job row exists (profile_jobs FK -> jobs.id).
        with self._connection() as conn:
            existing_job = conn.execute(
                "SELECT id FROM jobs WHERE id = ?", (str(job_id),),
            ).fetchone()
        if not existing_job:
            # Keep the explicit feedback event, but do not invent a source URL
            # merely to satisfy the legacy profile_jobs foreign key.
            return
        # Ensure profile_job record exists.
        try:
            previous_status = self.get_profile_job(profile_id, job_id).get("status", "new")
        except KeyError:
            self.link_profile_job(profile_id, job_id, run_id, run_id, status="new")
            previous_status = "new"
        if action == "not_interested":
            self.update_profile_job(profile_id, job_id, status="deleted")
            # Also write/refresh the durable trash record.
            origin_zone = "interested" if previous_status == "interested" else "discovery"
            existing = None
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT id FROM screening_trash_records "
                    "WHERE profile_id = ? AND job_id = ? AND restored_at IS NULL",
                    (str(profile_id), str(job_id)),
                ).fetchone()
                existing = dict(row) if row else None
            ts = _now()
            if existing:
                with self._connection() as conn:
                    conn.execute(
                        "UPDATE screening_trash_records SET origin_zone = ?, run_id = ?, "
                        "feedback_ref = ?, deleted_at = ? WHERE id = ?",
                        (origin_zone, _opt_str(run_id), _opt_str(reason_code), ts, existing["id"]),
                    )
            else:
                with self._connection() as conn:
                    conn.execute(
                        "INSERT INTO screening_trash_records "
                        "(id, profile_id, job_id, origin_zone, run_id, feedback_ref, deleted_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (_uuid(), str(profile_id), str(job_id), origin_zone,
                         _opt_str(run_id), _opt_str(reason_code), ts, ts),
                    )
        elif action == "interested":
            self.update_profile_job(profile_id, job_id, status="interested")

    def revoke_discovery_feedback(self, feedback_id):
        ts = _now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_feedback WHERE id = ?",
                (str(feedback_id),),
            ).fetchone()
            if row is None:
                raise KeyError(feedback_id)
            cur = conn.execute(
                "UPDATE discovery_feedback SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (ts, str(feedback_id)),
            )
            if cur.rowcount == 0:
                return dict(row)

            feedback = dict(row)
            job_id = feedback.get("job_id")
            profile_id = feedback.get("profile_id")
            if feedback.get("target_type") == "job" and job_id:
                latest = conn.execute(
                    "SELECT action FROM discovery_feedback "
                    "WHERE profile_id=? AND job_id=? AND target_type='job' AND revoked_at IS NULL "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (str(profile_id), str(job_id)),
                ).fetchone()
                trash = conn.execute(
                    "SELECT origin_zone FROM screening_trash_records "
                    "WHERE profile_id=? AND job_id=? AND restored_at IS NULL "
                    "ORDER BY deleted_at DESC, id DESC LIMIT 1",
                    (str(profile_id), str(job_id)),
                ).fetchone()
                status = "new"
                if latest and latest["action"] == "interested":
                    status = "interested"
                elif latest and latest["action"] == "not_interested":
                    status = "deleted"
                elif trash and trash["origin_zone"] == "interested":
                    status = "interested"
                conn.execute(
                    "UPDATE profile_jobs SET status=? WHERE profile_id=? AND job_id=?",
                    (status, str(profile_id), str(job_id)),
                )
                if status != "deleted":
                    conn.execute(
                        "UPDATE screening_trash_records SET restored_at=? "
                        "WHERE profile_id=? AND job_id=? AND restored_at IS NULL",
                        (ts, str(profile_id), str(job_id)),
                    )
            feedback["revoked_at"] = ts
            return feedback

    def list_discovery_feedback(self, profile_id, *, effective_only=False) -> list:
        clauses = ["profile_id = ?"]
        params = [str(profile_id)]
        if effective_only:
            clauses.append("revoked_at IS NULL")
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM discovery_feedback WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_resume_derived_evidence(self, resume_id):
        """Forget resume-derived content while preserving historical job runs.

        Analyses and directions are logical tombstones because discovery runs
        reference them through cascading foreign keys. Physical deletion would
        erase the user's run history, snapshots and assessments. Their derived
        contents are scrubbed and hidden from normal list APIs instead.
        """
        with self._connection() as conn:
            aids = [r["id"] for r in conn.execute(
                "SELECT id FROM candidate_analyses WHERE resume_id = ?", (str(resume_id),),
            ).fetchall()]
            if not aids:
                return

            # Historical jobs remain visible, but no score or explanation may
            # survive after its resume evidence has been forgotten.
            conn.execute(
                "UPDATE job_direction_assessments SET hard_outcome='unknown', "
                "hard_checks_json='{}', dimensions_json='{}', match_score=NULL, confidence=NULL, "
                "category='needs_review', candidate_evidence_ids_json='[]', "
                "job_evidence_json='{}', gaps_json='[]', failure_code='resume_deleted', "
                "updated_at=? WHERE run_id IN ("
                "  SELECT id FROM discovery_runs WHERE resume_id = ?)",
                (_now(), str(resume_id)),
            )
            for aid in aids:
                conn.execute(
                    "DELETE FROM direction_evidence WHERE direction_id IN ("
                    "SELECT id FROM career_directions WHERE analysis_id = ?)",
                    (aid,),
                )
                conn.execute("DELETE FROM resume_evidence WHERE analysis_id = ?", (aid,))
                conn.execute(
                    "UPDATE career_directions SET name='', rationale='', gaps_json='[]', "
                    "confidence=0, default_enabled=0, search_terms_json='[]' "
                    "WHERE analysis_id = ?",
                    (aid,),
                )
                conn.execute(
                    "UPDATE candidate_analyses SET status='deleted', summary_json='{}', "
                    "unknowns_json='[]', model_name='', failure_code='resume_deleted', "
                    "completed_at=? WHERE id = ?",
                    (_now(), aid),
                )
