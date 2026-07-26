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

from webui.constants import CLEANUP_EXPIRED_DAYS, DETAIL_BUDGET


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


_CST = timezone(timedelta(hours=8))  # 东八区


def _now():
    return datetime.now(_CST).isoformat()


def _uuid():
    return uuid.uuid4().hex[:16]


def _opt_str(value):
    """把 None 转为 SQL NULL（None），其他值转 str。"""
    return None if value is None else str(value)


def _now_minus_days(days):
    """返回 N 天前的 ISO 时间字符串（用于清理阈值）。"""
    return (datetime.now(_CST) - timedelta(days=int(days))).isoformat()


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
        if current < 16:
            self._migration_016()
        if current < 17:
            self._migration_017()
        if current < 18:
            self._migration_018()
        if current < 19:
            self._migration_019()
        # Always reconcile: copy old default profile if not yet in candidate_profiles
        self._copy_legacy_default_profile()

    def _mark_stale_runs_interrupted(self):
        """Reconcile run state on process restart.

        A process restart cannot resume an in-memory child process. Mark runs
        left in an active state as interrupted so the UI does not show a
        permanently "running" state.
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
            run_cols = {row["name"] for row in conn.execute("PRAGMA table_info(screening_runs)")}
            run_additions = {
                "search_params_json": "TEXT NOT NULL DEFAULT '{}'",
                "profile_summary": "TEXT NOT NULL DEFAULT ''",
                "total_scraped": "INTEGER NOT NULL DEFAULT 0",
                "total_kept": "INTEGER NOT NULL DEFAULT 0",
                "total_dropped": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in run_additions.items():
                if name not in run_cols:
                    conn.execute(f"ALTER TABLE screening_runs ADD COLUMN {name} {definition}")

            # Expand screening_results with full job data
            res_cols = {row["name"] for row in conn.execute("PRAGMA table_info(screening_results)")}
            res_additions = {
                "title": "TEXT NOT NULL DEFAULT ''",
                "company": "TEXT NOT NULL DEFAULT ''",
                "salary": "TEXT NOT NULL DEFAULT ''",
                "location": "TEXT NOT NULL DEFAULT ''",
                "tags": "TEXT NOT NULL DEFAULT ''",
                "jd": "TEXT NOT NULL DEFAULT ''",
                "source_url": "TEXT NOT NULL DEFAULT ''",
                "verdict_reason": "TEXT NOT NULL DEFAULT ''",
                "caveats_json": "TEXT NOT NULL DEFAULT '[]'",
                "is_dropped": "INTEGER NOT NULL DEFAULT 0",
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
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(screening_runs)")}
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

    # ===================================================================
    # Pipeline result persistence (replaces latest_pipeline_result.json)
    # ===================================================================

    def save_pipeline_result(self, result: dict, script_params: dict) -> str:
        """Persist a complete pipeline run result to the database.

        Creates a screening_runs row and one screening_results row per job
        (both kept and dropped). Returns the run_id.
        """
        run_id = str(uuid.uuid4())
        now = _now()
        jobs = result.get("jobs") or []
        dropped = result.get("dropped") or []
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs "
                "(id, frozen_filters_json, status, source_count, match_count, mismatch_count, "
                " created_at, updated_at, search_params_json, profile_summary, "
                " total_scraped, total_kept, total_dropped, record_kind) "
                "VALUES (?, ?, 'done', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'result_snapshot')",
                (
                    run_id,
                    json.dumps(script_params, ensure_ascii=False),
                    result.get("total_scraped", 0),
                    result.get("total_matched", 0),
                    result.get("total_kept", 0) - result.get("total_matched", 0),
                    now, now,
                    json.dumps(script_params, ensure_ascii=False),
                    result.get("profile_summary", ""),
                    result.get("total_scraped", 0),
                    result.get("total_kept", 0),
                    result.get("total_dropped", len(dropped)),
                ),
            )
            # Insert kept jobs
            for job in jobs:
                conn.execute(
                    "INSERT OR REPLACE INTO screening_results "
                    "(id, run_id, job_id, verdict, created_at, title, company, salary, "
                    " location, tags, jd, source_url, verdict_reason, caveats_json, is_dropped) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (
                        str(uuid.uuid4()), run_id,
                        str(job.get("job_id", "")),
                        job.get("verdict", "uncertain"),
                        now,
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("salary", ""),
                        job.get("location", ""),
                        job.get("tags", ""),
                        job.get("jd", ""),
                        job.get("source_url", ""),
                        job.get("verdict_reason", ""),
                        json.dumps(job.get("caveats") or [], ensure_ascii=False),
                    ),
                )
            # Insert dropped jobs
            for job in dropped:
                conn.execute(
                    "INSERT OR REPLACE INTO screening_results "
                    "(id, run_id, job_id, verdict, created_at, title, company, salary, "
                    " location, tags, jd, source_url, verdict_reason, caveats_json, is_dropped) "
                    "VALUES (?, ?, ?, 'dropped', ?, ?, ?, ?, ?, ?, '', ?, ?, '[]', 1)",
                    (
                        str(uuid.uuid4()), run_id,
                        str(job.get("job_id", "")),
                        now,
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("salary", ""),
                        job.get("location", ""),
                        job.get("tags", ""),
                        job.get("canonical_url", ""),
                        job.get("reason", ""),
                    ),
                )
        return run_id

    def load_latest_pipeline_result(self) -> dict | None:
        """Load the most recent successful pipeline run from the database.

        Returns a payload matching the old JSON file format:
        {"saved_at": ..., "script_params": {...}, "result": {...}}
        or None if no successful run exists.
        """
        with self._connection() as conn:
            run = conn.execute(
                "SELECT * FROM screening_runs WHERE status = 'done' "
                "AND record_kind = 'result_snapshot' "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
            if run is None:
                return None
            run = dict(run)
            rows = conn.execute(
                "SELECT * FROM screening_results WHERE run_id = ? ORDER BY rowid",
                (run["id"],),
            ).fetchall()

        jobs = []
        dropped = []
        for row in rows:
            row = dict(row)
            if row.get("is_dropped"):
                dropped.append({
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "reason": row["verdict_reason"],
                    "canonical_url": row["source_url"],
                })
            else:
                caveats = []
                try:
                    caveats = json.loads(row.get("caveats_json") or "[]")
                except (json.JSONDecodeError, TypeError):
                    pass
                jobs.append({
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "company": row["company"],
                    "salary": row["salary"],
                    "location": row["location"],
                    "tags": row["tags"],
                    "jd": row["jd"],
                    "source_url": row["source_url"],
                    "verdict": row["verdict"],
                    "verdict_reason": row["verdict_reason"],
                    "caveats": caveats,
                })

        script_params = {}
        try:
            script_params = json.loads(run.get("search_params_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        result = {
            "ok": True,
            "jobs": jobs,
            "dropped": dropped,
            "total_scraped": run.get("total_scraped", 0),
            "total_kept": run.get("total_kept", len(jobs)),
            "total_matched": run.get("match_count", 0),
            "total_dropped": run.get("total_dropped", len(dropped)),
            "profile_summary": run.get("profile_summary", ""),
            "error": "",
        }
        return {
            "saved_at": run["created_at"],
            "script_params": script_params,
            "result": result,
        }

    def get_latest_done_run_id(self) -> str | None:
        """Return the run_id of the most recent successful pipeline run, or None."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM screening_runs WHERE status = 'done' "
                "AND record_kind = 'result_snapshot' "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        return row["id"] if row else None

    def update_pipeline_job_jd(self, run_id: str, job_id: str, jd: str):
        """Update the JD text for a specific job in a pipeline run (补抓 JD)."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE screening_results SET jd = ? WHERE run_id = ? AND job_id = ?",
                (jd, str(run_id), str(job_id)),
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
            # BEGIN IMMEDIATE: 立即获取写锁，避免并发下两线程读到相同 MAX(seq)
            # 后第二个 INSERT 撞 UNIQUE(task_id, seq)
            connection.execute("BEGIN IMMEDIATE")
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

    def list_all_interested(self) -> list:
        """返回所有 profile 的 interested 岗位列表，带 profile_id 用于取消收藏。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM profile_jobs WHERE status = 'interested' ORDER BY shown_at DESC",
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

    # -- screening runs（AI 筛选任务持久化：进度落库 + 断点续筛） ----------

    def create_screening_run(self, run_id, *, frozen_filters=None, source_count=0,
                             profile_id=None, execution_params=None):
        """登记一个 AI 筛选任务（网页两段式筛选）。

        表是 migration_004/007/010 建好的（此前无写入方），本方法是启用入口。
        run_id 直接用任务 id，便于与内存任务/前端轮询对齐。
        """
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, frozen_filters_json, status, source_count, match_count, mismatch_count, "
                "created_at, updated_at, error_code, resume_id, pending_count, processed_count, "
                "source_cursor, parse_failure_count, parse_failures_json, profile_id, "
                "execution_params_json, record_kind) "
                "VALUES (?, ?, 'queued', ?, 0, 0, ?, ?, NULL, NULL, 0, 0, 0, 0, '{}', ?, ?, 'process_log')",
                (
                    str(run_id),
                    json.dumps(frozen_filters or {}, ensure_ascii=False),
                    int(source_count), ts, ts,
                    str(profile_id) if profile_id else None,
                    json.dumps(execution_params or {}, ensure_ascii=False),
                ),
            )
        return self.get_screening_run(run_id)

    def get_screening_run(self, run_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        if row is None:
            return None
        return self._screening_run_row(row)

    def update_screening_run(self, run_id, *, status=None, processed_count=None,
                             source_cursor=None, match_count=None, mismatch_count=None,
                             error_code=None, pending_count=None):
        """宽松更新（不做状态机校验：终态来源多——完成/失败/取消/登录墙/中断）。"""
        sets = []
        params = []
        if status is not None:
            sets.append("status = ?")
            params.append(str(status))
        if processed_count is not None:
            sets.append("processed_count = ?")
            params.append(int(processed_count))
        if source_cursor is not None:
            sets.append("source_cursor = ?")
            params.append(int(source_cursor))
        if match_count is not None:
            sets.append("match_count = ?")
            params.append(int(match_count))
        if mismatch_count is not None:
            sets.append("mismatch_count = ?")
            params.append(int(mismatch_count))
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(str(error_code))
        if pending_count is not None:
            sets.append("pending_count = ?")
            params.append(int(pending_count))
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(str(run_id))
        with self._connection() as conn:
            conn.execute(
                f"UPDATE screening_runs SET {', '.join(sets)} WHERE id = ?", params
            )

    def latest_screening_run_for_source(self, source_task_id, *, statuses=None):
        """找同一抓取任务最近一次 AI 筛选 run（供断点续筛）。

        数据量小（本地单用户），直接取最近 50 条在 Python 侧按
        execution_params.scrape_task_id 过滤。
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_runs ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        for row in rows:
            run = self._screening_run_row(row)
            params = run.get("execution_params") or {}
            if str(params.get("scrape_task_id", "")) != str(source_task_id):
                continue
            if statuses is None or run["status"] in statuses:
                return run
        return None

    def latest_interrupted_screening_run(self):
        """进程重启后被标记 interrupted 的最近一次筛选（供恢复提示）。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_runs WHERE status = 'interrupted' "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return self._screening_run_row(row) if row is not None else None

    def save_screening_verdicts(self, run_id, verdicts):
        """每批精筛判定落盘（upsert）：进程崩了也能从 screening_results 续。"""
        if not verdicts:
            return
        ts = _now()
        with self._connection() as conn:
            for job_id, verdict in verdicts.items():
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, job_id) DO UPDATE SET verdict = excluded.verdict",
                    (
                        _uuid(), str(run_id), str(job_id),
                        json.dumps(verdict, ensure_ascii=False), ts,
                    ),
                )

    def load_screening_verdicts(self, run_id):
        """载入某次筛选已落盘的判定 {job_id: verdict}（断点续筛用）。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT job_id, verdict FROM screening_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchall()
        out = {}
        for row in rows:
            try:
                value = json.loads(row["verdict"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                out[str(row["job_id"])] = value
        return out

    def _screening_run_row(self, row) -> dict:
        return {
            "id": row["id"],
            "status": row["status"],
            "frozen_filters": json.loads(row["frozen_filters_json"] or "{}"),
            "source_count": row["source_count"],
            "match_count": row["match_count"],
            "mismatch_count": row["mismatch_count"],
            "processed_count": row["processed_count"],
            "source_cursor": row["source_cursor"],
            "error_code": row["error_code"],
            "profile_id": row["profile_id"],
            "execution_params": json.loads(row["execution_params_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "record_kind": row["record_kind"],
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
        ts = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        jid = _uuid()
        with self._connection() as conn:
            # ON CONFLICT(canonical_url) DO UPDATE: 单语句 UPSERT，避免并发下
            # SELECT-then-INSERT 撞 UNIQUE(canonical_url)。
            # RETURNING id 取回实际写入行的 id（新插入=jid，已存在=原 id）。
            row = conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_url) DO UPDATE SET "
                "source_url = excluded.source_url, title = excluded.title, company = excluded.company, "
                "salary = excluded.salary, location = excluded.location, jd = excluded.jd, "
                "last_seen_at = excluded.last_seen_at "
                "RETURNING id",
                (jid, canonical_url, source_url, title, company, salary, location, jd, ts, ts, expires_at),
            ).fetchone()
            jid = row["id"]
        return self.get_job(jid)

    def get_job(self, job_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def list_jobs_by_ids(self, job_ids) -> dict:
        """批量查询 jobs，一次 SELECT WHERE id IN (...)。

        返回 {job_id: row_dict}。不存在的 job_id 不在结果中。
        空列表返回 {}。单次连接，消除 N+1 模式。
        """
        ids = [str(jid) for jid in job_ids if jid]
        if not ids:
            return {}
        # 分批避免 SQL IN 列表过长（SQLite 限制 SQLITE_MAX_VARIABLE_NUMBER，默认 999）
        out: dict = {}
        with self._connection() as conn:
            for i in range(0, len(ids), 500):
                batch = ids[i:i + 500]
                placeholders = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE id IN ({placeholders})",
                    batch,
                ).fetchall()
                for row in rows:
                    out[str(row["id"])] = dict(row)
        return out

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
            # ON CONFLICT(profile_id, job_id) DO UPDATE: 单语句 UPSERT，避免并发下
            # SELECT-then-INSERT 撞 PRIMARY KEY(profile_id, job_id)。
            conn.execute(
                "INSERT INTO profile_jobs (profile_id, job_id, first_run_id, last_run_id, ai_rank, shown_at, status, note, applied_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL) "
                "ON CONFLICT(profile_id, job_id) DO UPDATE SET "
                "last_run_id = excluded.last_run_id, ai_rank = excluded.ai_rank, "
                "shown_at = COALESCE(shown_at, excluded.shown_at)",
                (str(profile_id), str(job_id), first_run_id, last_run_id, ai_rank, ts, status),
            )
        return self.get_profile_job(profile_id, job_id)

    def update_profile_job(self, profile_id, job_id, status=None, note=None, applied_at=None):
        # 字段名来自内部调用方（hardcoded），非用户输入，无需白名单
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

    def cleanup_expired_jobs(self, days=CLEANUP_EXPIRED_DAYS) -> int:
        """Remove normal results older than *days*. Preserves interested/applied."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            # 单条 UPDATE + 子查询，消除原来逐行 UPDATE 的 N 次 DB 往返。
            # 命中 idx_jobs_expires_at 索引（partial: WHERE expires_at IS NOT NULL）。
            cursor = conn.execute(
                """UPDATE profile_jobs SET status = 'deleted'
                   WHERE status = 'new'
                     AND (profile_id, job_id) IN (
                       SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                       JOIN jobs j ON pj.job_id = j.id
                       WHERE pj.status = 'new'
                         AND j.expires_at IS NOT NULL
                         AND j.expires_at < ?
                     )""",
                (cutoff,),
            )
            return cursor.rowcount

    def preview_cleanup_expired_jobs(self, days=CLEANUP_EXPIRED_DAYS) -> list:
        """Preview which profile_jobs would be cleaned up, without modifying data.

        Returns a list of ``{profile_id, job_id}`` dicts.  The real cleanup
        is performed by :meth:`cleanup_expired_jobs`.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                   JOIN jobs j ON pj.job_id = j.id
                   WHERE pj.status = 'new' AND j.expires_at IS NOT NULL AND j.expires_at < ?""",
                (cutoff,),
            ).fetchall()
        return [{"profile_id": row["profile_id"], "job_id": row["job_id"]} for row in rows]
