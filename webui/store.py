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
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from webui.constants import CLEANUP_EXPIRED_DAYS, DETAIL_BUDGET


class DiscoveryStoreConflictError(Exception):
    """Raised when a CAS-guarded store update detects a state conflict."""


class MigrationBackupError(RuntimeError):
    """Raised when pre-migration bootstrap backup or verification fails.

    TaskStore construction must abort when this is raised; the source database
    must not receive any v27 partial writes.
    """


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

RUN_STATUSES = {"queued", "running", "succeeded", "partial", "failed", "interrupted", "paused"}
RUN_TRANSITIONS = {
    # 未开始的任务不能伪造暂停现场；必须先进入 running 再因真实阻断暂停。
    "queued": {"running", "succeeded", "partial", "failed", "interrupted"},
    "running": {"succeeded", "partial", "failed", "interrupted", "paused"},
    "paused": {"running", "failed", "interrupted"},
    "succeeded": set(),
    "partial": set(),
    "failed": set(),
    "interrupted": set(),
}

# 统一任务状态机（FR-005）：语义清晰的状态名，与 RUN_STATUSES 映射
TASK_STATUSES = {
    "waiting",              # = queued
    "running",              # = running
    "paused",               # = paused（系统性阻断）
    "completed",            # = succeeded（无待确认）
    "completed_with_pending",  # = partial（有待确认）
    "failed",               # = failed
    "cancelled",            # = interrupted（用户取消）
}

# 统一状态名 → DB 状态名映射
TASK_TO_RUN_STATUS = {
    "waiting": "queued",
    "running": "running",
    "paused": "paused",
    "completed": "succeeded",
    "completed_with_pending": "partial",
    "failed": "failed",
    "cancelled": "interrupted",
}
RUN_TO_TASK_STATUS = {v: k for k, v in TASK_TO_RUN_STATUS.items()}

# 系统性阻断码集合（命中即暂停整个任务）
SYSTEMIC_BLOCK_CODES = {
    "captcha_required", "login_expired", "ai_rate_limited",
    "ai_quota_exhausted", "ai_key_invalid", "ai_network_error",
    "ip_risk_control", "cdp_unavailable", "internal_error",
    "source_verification_required", "source_login_required",
    "source_rate_limited", "source_blocked", "source_cdp_unavailable",
}

# 独立失败码集合（仅该岗位进待确认，不阻断）
INDEPENDENT_FAILURE_CODES = {
    "job_offline", "detail_timeout", "detail_invalid", "ai_missing_job",
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


def _to_iso_timestamp(value):
    """Normalize epoch milliseconds or ISO text to local ISO text."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, _CST).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CST)
    return parsed.astimezone(_CST).isoformat()


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
            self._bootstrap_migration_backup()
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

    # -- pre-migration bootstrap backup (T103) ----------------------------

    _MIGRATION_BACKUP_TARGET_VERSION = 27
    _MIGRATION_BACKUP_TOOL_VERSION = "career-scout-bootstrap-v1"

    def backup_dir_for_tests(self) -> Path:
        """Return the absolute backup directory used by the bootstrap step.

        Tests use this to locate generated backup and manifest artifacts.
        Production code should not call this method.
        """
        return self._migration_backup_dir()

    def _migration_backup_dir(self) -> Path:
        """Local-only, git-ignored directory for migration backups and manifests."""
        # 位于仓库根的 .career-scout/backups/ 下；.gitignore 已覆盖 .career-scout/
        repo_root = Path(__file__).resolve().parent.parent
        return repo_root / ".career-scout" / "backups"

    def _bootstrap_migration_backup(self) -> None:
        """在 _initialize/_migrate 之前为待迁移库生成一致性备份与 manifest。

        合同（data-model.md 迁移前 bootstrap）：
        1. 源库文件不存在或 schema version >= 27 时跳过，不生成备份。
        2. 源库 schema version < 27 时，用 SQLite backup API 生成带时间戳的备份。
        3. 生成 manifest，含源库/备份 schema version、字节大小、SHA-256、
           创建时间和工具版本；manifest 不得记录本地绝对路径。
        4. 关闭备份连接后用只读连接验证 SHA-256、PRAGMA quick_check、
           schema_migrations 可读且版本等于源版本；任一失败抛 MigrationBackupError。
        5. 备份文件、manifest 位于本地忽略目录，不进入仓库。

        失败时 TaskStore 构造中止；源库不被 v27 部分写入（因为 _migrate 还未执行）。
        """
        if not os.path.exists(self.db_path):
            return  # 新库，无需备份

        source_version = self._read_schema_version_readonly()
        # source_version == 0 表示空库（_configure_database 刚创建文件但无表），
        # 或无法读取；此时无数据需要保护，跳过备份。
        if source_version < 1:
            return  # 新库或空库，无需备份
        if source_version >= self._MIGRATION_BACKUP_TARGET_VERSION:
            return  # 已迁移或更高，无需备份

        backup_dir = self._migration_backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_name = f"webui-v{source_version}-to-v{self._MIGRATION_BACKUP_TARGET_VERSION}-{timestamp}.sqlite"
        backup_path = backup_dir / backup_name

        # 用 SQLite backup API 生成一致性快照
        source_conn = sqlite3.connect(self.db_path, timeout=10)
        backup_conn = sqlite3.connect(str(backup_path))
        try:
            source_conn.backup(backup_conn)
        except sqlite3.Error as exc:
            source_conn.close()
            backup_conn.close()
            self._safe_unlink(backup_path)
            raise MigrationBackupError(f"backup_failed: {exc}") from exc
        finally:
            try:
                source_conn.close()
            except Exception:
                pass
            try:
                backup_conn.close()
            except Exception:
                pass

        source_size = os.path.getsize(self.db_path)
        backup_size = os.path.getsize(backup_path)
        source_sha = self._sha256_of_file(self.db_path)
        backup_sha = self._sha256_of_file(backup_path)

        # 只读连接验证备份库：quick_check、schema_migrations 可读、版本一致
        try:
            ro = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            self._safe_unlink(backup_path)
            raise MigrationBackupError(f"backup_open_failed: {exc}") from exc
        try:
            row = ro.execute("PRAGMA quick_check").fetchone()
            if row is None or row[0] != "ok":
                self._safe_unlink(backup_path)
                raise MigrationBackupError("backup_quick_check_failed")
            v_row = ro.execute(
                "SELECT MAX(version) AS v FROM schema_migrations"
            ).fetchone()
            backup_version = int(v_row[0] or 0) if v_row else 0
            if backup_version != source_version:
                self._safe_unlink(backup_path)
                raise MigrationBackupError(
                    f"backup_version_mismatch: source={source_version} backup={backup_version}"
                )
        except sqlite3.Error as exc:
            self._safe_unlink(backup_path)
            raise MigrationBackupError(f"backup_verify_failed: {exc}") from exc
        finally:
            ro.close()

        # manifest：只含文件名和元数据，不含绝对路径
        manifest = {
            "backup_file": backup_path.name,
            "source_schema_version": source_version,
            "backup_schema_version": backup_version,
            "source_size_bytes": source_size,
            "backup_size_bytes": backup_size,
            "source_sha256": source_sha,
            "backup_sha256": backup_sha,
            "created_at": _now(),
            "tool_version": self._MIGRATION_BACKUP_TOOL_VERSION,
        }
        manifest_path = backup_path.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _read_schema_version_readonly(self) -> int:
        """以只读连接读取源库 schema_migrations 的最大版本号。

        新库（无 schema_migrations 表或文件刚创建）返回 0。
        任何读取异常返回 0，保守视为需要备份。
        """
        try:
            ro = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, timeout=5
            )
        except sqlite3.Error:
            return 0
        try:
            # 检查 schema_migrations 表是否存在
            row = ro.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if row is None:
                return 0
            v_row = ro.execute(
                "SELECT MAX(version) AS v FROM schema_migrations"
            ).fetchone()
            return int(v_row[0] or 0) if v_row else 0
        except sqlite3.Error:
            return 0
        finally:
            ro.close()

    @staticmethod
    def _sha256_of_file(path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _safe_unlink(path) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

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

    def _migration_020(self):
        """010 healthy-pipeline-recovery: 暂停状态持久化 + 断点 + 失败码分类。

        - screening_runs 加 current_stage / error_reason / backend_version（FR-005/FR-037/FR-039）
        - screening_results 加 failed_code / failed_stage / retryable / attempts（FR-040）
        - 新增 pipeline_checkpoints 表保存断点（FR-023）
        - screening_pending_results 已在 migration_005 建，本处不重建
        """
        with self._connection() as conn:
            run_cols = {row["name"] for row in conn.execute("PRAGMA table_info(screening_runs)")}
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
                "retryable": "INTEGER NOT NULL DEFAULT 0",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
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
                    "PRAGMA table_info(screening_runs)"
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

    # -- SPEC011 advanced config state -----------------------------------

    def get_advanced_config_state(self) -> dict:
        """返回当前高级配置状态：selection、custom config、mode version。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM advanced_config_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return {
                "active_selection": "custom",
                "active_mode_version_id": None,
                "last_custom_config": None,
                "last_custom_digest": None,
                "legacy_imported_at": None,
            }
        custom_config = _decode_json(row["last_custom_config_json"], None)
        return {
            "active_selection": row["active_selection"],
            "active_mode_version_id": row["active_mode_version_id"],
            "last_custom_config": custom_config,
            "last_custom_digest": row["last_custom_digest"],
            "legacy_imported_at": row["legacy_imported_at"],
        }

    def save_custom_config(self, config: dict) -> str:
        """原子保存完整自定义配置（含 JD 并发 Tab 数），返回 digest。

        部分字段保存被拒绝；pages 不属于配置快照。
        """
        from webui.execution_config import (
            DEFAULT_DETAIL_TAB_POOL_SIZE, ExecutionConfigSnapshot, SPEED_FIELDS,
        )
        config = dict(config)
        config.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        missing = [f for f in SPEED_FIELDS if f not in config]
        if missing:
            raise ValueError(f"缺少必填字段: {missing}")
        # 验证配置快照有效性（含物理边界校验）
        snapshot = ExecutionConfigSnapshot.create(config)
        config_json = json.dumps(snapshot.to_dict(), ensure_ascii=False)
        with self._connection() as conn:
            conn.execute(
                "UPDATE advanced_config_state "
                "SET active_selection = 'custom', "
                "    last_custom_config_json = ?, "
                "    last_custom_digest = ?, "
                "    updated_at = ? "
                "WHERE id = 1",
                (config_json, snapshot.config_digest, _now()),
            )
        return snapshot.config_digest

    def select_mode(self, mode: str, *, task_size: str) -> dict:
        """选择系统参考模式，返回对应规模的配置快照。

        FR-009: pages 不出现在返回结果中。
        FR-056: 根据任务规模载入内部配置。
        """
        from webui.execution_config import ExecutionConfigSnapshot, get_mode_config
        if mode not in ("stable", "balanced", "extreme", "custom"):
            raise ValueError(f"未知模式: {mode}")
        if task_size not in ("small", "medium", "large"):
            raise ValueError(f"未知任务规模: {task_size}")
        if mode == "custom":
            state = self.get_advanced_config_state()
            if state["last_custom_config"] is None:
                raise ValueError("无最近自定义配置可恢复")
            with self._connection() as conn:
                conn.execute(
                    "UPDATE advanced_config_state "
                    "SET active_selection = 'custom', updated_at = ? WHERE id = 1",
                    (_now(),),
                )
            return {
                "selection": "custom",
                "config": state["last_custom_config"],
                "mode_version_id": state["active_mode_version_id"],
            }
        state = self.get_advanced_config_state()
        active_version_id = state["active_mode_version_id"]
        version_digest = None
        if active_version_id:
            version = self.get_mode_version(active_version_id)
            try:
                slot = version["matrix"][mode][task_size]
            except (KeyError, TypeError) as exc:
                raise ValueError("活动模式版本缺少所需配置槽位") from exc
            snapshot = ExecutionConfigSnapshot.create(slot)
            version_digest = version["version_digest"]
        else:
            snapshot = get_mode_config(mode, task_size=task_size)
        # 更新活跃选择
        with self._connection() as conn:
            conn.execute(
                "UPDATE advanced_config_state SET active_selection = ?, updated_at = ? WHERE id = 1",
                (mode, _now()),
            )
        return {
            "selection": mode,
            "config": snapshot.to_dict(),
            "mode_version_id": active_version_id,
            "mode_version_digest": version_digest,
        }

    def create_mode_version(
        self, *, matrix: dict, manual_ranges: dict,
        source_experiment_id: str | None = None,
    ) -> str:
        """创建一个候选模式版本（3 模式 × 3 规模）。

        不完整矩阵被拒绝（data-model.md 不变量：No partial mode matrix）。
        """
        required_modes = {"stable", "balanced", "extreme"}
        required_sizes = {"small", "medium", "large"}
        if not isinstance(matrix, dict):
            raise ValueError("matrix 必须是字典")
        extra_modes = set(matrix.keys()) - required_modes
        missing_modes = required_modes - set(matrix.keys())
        if missing_modes:
            raise ValueError(f"matrix 缺少模式: {missing_modes}")
        if extra_modes:
            raise ValueError(f"matrix 包含未知模式: {extra_modes}")
        from webui.execution_config import ExecutionConfigSnapshot
        for mode, sizes in matrix.items():
            if not isinstance(sizes, dict):
                raise ValueError(f"模式 {mode} 的规模配置必须是字典")
            missing_sizes = required_sizes - set(sizes.keys())
            if missing_sizes:
                raise ValueError(f"模式 {mode} 缺少规模: {missing_sizes}")
            extra_sizes = set(sizes.keys()) - required_sizes
            if extra_sizes:
                raise ValueError(f"模式 {mode} 包含未知规模: {extra_sizes}")
            for size, config in sizes.items():
                ExecutionConfigSnapshot.create(config)
        version_id = _uuid()
        matrix_json = json.dumps(matrix, ensure_ascii=False, sort_keys=True)
        ranges_json = json.dumps(manual_ranges, ensure_ascii=False, sort_keys=True)
        version_digest = "sha256:" + hashlib.sha256(
            (matrix_json + ranges_json).encode("utf-8")
        ).hexdigest()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO mode_config_versions "
                "(id, source_experiment_id, status, matrix_json, "
                " manual_ranges_json, version_digest, created_at) "
                "VALUES (?, ?, 'candidate', ?, ?, ?, ?)",
                (version_id, source_experiment_id, matrix_json, ranges_json,
                 version_digest, _now()),
            )
        return version_id

    def get_mode_version(self, version_id: str) -> dict:
        """Return one complete mode version without mutating active state."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM mode_config_versions WHERE id = ?", (version_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"模式版本不存在: {version_id}")
        return {
            "id": row["id"],
            "source_experiment_id": row["source_experiment_id"],
            "status": row["status"],
            "matrix": _decode_json(row["matrix_json"], {}),
            "manual_ranges": _decode_json(row["manual_ranges_json"], {}),
            "version_digest": row["version_digest"],
            "created_at": row["created_at"],
            "applied_at": row["applied_at"],
        }

    def get_experiment_mode_version(self, experiment_id: str) -> dict | None:
        """Return the newest complete candidate/active version for an experiment."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions "
                "WHERE source_experiment_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
        return self.get_mode_version(row["id"]) if row is not None else None

    def get_previous_mode_version(self, active_version_id: str) -> dict | None:
        """Return the newest complete superseded version available for rollback."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions "
                "WHERE id != ? AND status = 'superseded' "
                "ORDER BY COALESCE(applied_at, created_at) DESC LIMIT 1",
                (active_version_id,),
            ).fetchone()
        return self.get_mode_version(row["id"]) if row is not None else None

    def apply_mode_version(self, version_id: str) -> None:
        """原子应用模式版本：旧的被 superseded，新的变 active。"""
        version = self.get_mode_version(version_id)
        source_experiment_id = version.get("source_experiment_id")
        if source_experiment_id:
            experiment = self.get_tuning_experiment(source_experiment_id)
            issues = self.get_tuning_completion_issues(source_experiment_id)
            if experiment["status"] != "completed" or issues:
                raise ValueError("实验候选版本尚未通过全部最终轮次门禁")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"模式版本不存在: {version_id}")
            # 旧 active 版本标记为 superseded
            conn.execute(
                "UPDATE mode_config_versions SET status = 'superseded' WHERE status = 'active'"
            )
            # 新版本标记为 active
            conn.execute(
                "UPDATE mode_config_versions SET status = 'active', applied_at = ? WHERE id = ?",
                (_now(), version_id),
            )
            # 更新活跃选择
            conn.execute(
                "UPDATE advanced_config_state "
                "SET active_mode_version_id = ?, updated_at = ? WHERE id = 1",
                (version_id, _now()),
            )

    def rollback_mode_version(self, version_id: str) -> None:
        """回退到指定版本：整体恢复。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"模式版本不存在: {version_id}")
            conn.execute(
                "UPDATE mode_config_versions SET status = 'superseded' WHERE status = 'active'"
            )
            conn.execute(
                "UPDATE mode_config_versions SET status = 'active', applied_at = ? WHERE id = ?",
                (_now(), version_id),
            )
            conn.execute(
                "UPDATE advanced_config_state "
                "SET active_mode_version_id = ?, updated_at = ? WHERE id = 1",
                (version_id, _now()),
            )

    def import_legacy_advanced_settings(self, legacy_path) -> None:
        """一次性导入旧 advanced_settings.json。

        已导入过时不覆盖；pages 不导入到配置快照。
        """
        from pathlib import Path
        legacy_path = Path(legacy_path)
        # 检查是否已导入
        with self._connection() as conn:
            row = conn.execute(
                "SELECT legacy_imported_at FROM advanced_config_state WHERE id = 1"
            ).fetchone()
            if row is not None and row["legacy_imported_at"] is not None:
                return  # 已导入，不覆盖
        if not legacy_path.is_file():
            return
        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(raw, dict):
            return
        # 只取速度字段，排除 pages；旧 9 字段配置补默认 JD Tab 数
        from webui.execution_config import DEFAULT_DETAIL_TAB_POOL_SIZE, SPEED_FIELDS
        config = {k: v for k, v in raw.items() if k in SPEED_FIELDS}
        config.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        if len(config) != len(SPEED_FIELDS):
            return  # 字段不完整，不导入
        self.save_custom_config(config)
        with self._connection() as conn:
            conn.execute(
                "UPDATE advanced_config_state SET legacy_imported_at = ? WHERE id = 1",
                (_now(),),
            )

    # -- recovery maintenance lock ---------------------------------------

    def _active_worker_count(self, conn=None) -> int:
        if conn is None:
            with self._connection() as owned_conn:
                return self._active_worker_count(owned_conn)
        counts = [
            conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('queued', 'running')"
            ).fetchone()["n"],
            conn.execute(
                "SELECT COUNT(*) AS n FROM search_runs WHERE status IN ('queued', 'running')"
            ).fetchone()["n"],
            conn.execute(
                "SELECT COUNT(*) AS n FROM screening_runs WHERE status IN ('queued', 'running')"
            ).fetchone()["n"],
        ]
        return sum(int(value or 0) for value in counts)

    def acquire_recovery_lock(self, *, owner_token, maintenance=True,
                              ttl_seconds=300, wait_timeout=30):
        deadline = time.monotonic() + max(0, float(wait_timeout))
        while True:
            now = datetime.now(_CST)
            expires_at = (now + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
            active_workers = 0
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM recovery_lock WHERE expires_at <= ?", (now.isoformat(),)
                )
                row = conn.execute(
                    "SELECT owner_token FROM recovery_lock WHERE lock_id = 1"
                ).fetchone()
                if row is not None and row["owner_token"] != str(owner_token):
                    raise RuntimeError("recovery maintenance lock is already held")
                active_workers = self._active_worker_count(conn)
                if active_workers == 0:
                    conn.execute(
                        "INSERT INTO recovery_lock "
                        "(lock_id, owner_token, acquired_at, expires_at, maintenance) "
                        "VALUES (1, ?, ?, ?, ?) "
                        "ON CONFLICT(lock_id) DO UPDATE SET "
                        " owner_token = excluded.owner_token, acquired_at = excluded.acquired_at, "
                        " expires_at = excluded.expires_at, maintenance = excluded.maintenance",
                        (str(owner_token), now.isoformat(), expires_at, int(bool(maintenance))),
                    )
            if active_workers == 0:
                return True
            if time.monotonic() >= deadline:
                raise TimeoutError("recovery maintenance waiting for active workers")
            time.sleep(0.05)

    def release_recovery_lock(self, *, owner_token):
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM recovery_lock WHERE lock_id = 1 AND owner_token = ?",
                (str(owner_token),),
            )
        return cursor.rowcount > 0

    def is_recovery_locked(self) -> bool:
        now = _now()
        with self._connection() as conn:
            conn.execute("DELETE FROM recovery_lock WHERE expires_at <= ?", (now,))
            row = conn.execute(
                "SELECT maintenance FROM recovery_lock WHERE lock_id = 1"
            ).fetchone()
        return bool(row is not None and row["maintenance"])

    def _assert_recovery_writes_allowed(self, conn=None):
        if conn is None:
            with self._connection() as owned_conn:
                return self._assert_recovery_writes_allowed(owned_conn)
        now = _now()
        conn.execute("DELETE FROM recovery_lock WHERE expires_at <= ?", (now,))
        row = conn.execute(
            "SELECT maintenance FROM recovery_lock WHERE lock_id = 1"
        ).fetchone()
        if row is not None and row["maintenance"]:
            raise RuntimeError("recovery maintenance is active; new tasks are blocked")

    # ===================================================================
    # Pipeline result persistence (replaces latest_pipeline_result.json)
    # ===================================================================

    def save_pipeline_result(self, result: dict, script_params: dict, *,
                             started_at=None, finished_at=None, execution_config=None,
                             status: str = "done") -> str:
        """Persist a complete or partial pipeline run result to the database.

        Creates a screening_runs row and one screening_results row per job
        (both kept and dropped). ``status`` is the raw snapshot status:
        ``done`` for completed runs, ``partial`` for user-finished partial runs.
        Returns the run_id.
        """
        run_id = str(uuid.uuid4())
        now = _now()
        started_at = _to_iso_timestamp(started_at)
        finished_at = _to_iso_timestamp(finished_at) or now
        jobs = result.get("jobs") or []
        dropped = result.get("dropped") or []
        match_count = sum(1 for job in jobs if job.get("verdict") == "match")
        mismatch_count = sum(
            1 for job in jobs if job.get("verdict") in ("not_match", "mismatch")
        )
        pending_jobs = [
            job for job in jobs
            if job.get("verdict") not in ("match", "not_match", "mismatch")
        ]
        if status == "done" and pending_jobs:
            status = "partial"
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO screening_runs "
                "(id, platform, frozen_filters_json, status, source_count, match_count, mismatch_count, "
                " pending_count, processed_count, created_at, updated_at, started_at, "
                " finished_at, search_params_json, execution_params_json, "
                " profile_summary, total_scraped, total_kept, total_dropped, record_kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'result_snapshot')",
                (
                    run_id,
                    str(script_params.get("platform") or result.get("platform") or "boss"),
                    json.dumps(script_params, ensure_ascii=False),
                    str(status),
                    result.get("total_scraped", 0),
                    match_count,
                    mismatch_count,
                    len(pending_jobs),
                    match_count + mismatch_count,
                    now, now, started_at, finished_at,
                    json.dumps(script_params, ensure_ascii=False),
                    json.dumps(
                        {"execution_config": execution_config or {}}, ensure_ascii=False
                    ),
                    result.get("profile_summary", ""),
                    result.get("total_scraped", 0),
                    result.get("total_kept", 0),
                    result.get("total_dropped", len(dropped)),
                ),
            )
            # Insert kept jobs
            for job in jobs:
                platform = str(script_params.get("platform") or result.get("platform") or "boss")
                conn.execute(
                    "INSERT OR REPLACE INTO screening_results "
                    "(id, run_id, platform, platform_job_id, job_id, verdict, created_at, title, company, salary, "
                    " location, tags, jd, source_url, verdict_reason, caveats_json, is_dropped, "
                    " experience, degree, extra_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), run_id, platform,
                        str(job.get("platform_job_id") or job.get("job_id") or ""),
                        None,  # 内部 UUID 由收藏/反馈落库时回填
                        job.get("verdict", "uncertain"),
                        now,
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("salary", ""),
                        job.get("location", ""),
                        job.get("tags", ""),
                        job.get("jd", ""),
                        job.get("canonical_url") or job.get("source_url") or "",
                        job.get("verdict_reason", ""),
                        json.dumps(job.get("caveats") or [], ensure_ascii=False),
                        job.get("experience", ""),
                        job.get("degree", ""),
                        json.dumps(job.get("extra") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
            # Insert dropped jobs
            for job in dropped:
                platform = str(script_params.get("platform") or result.get("platform") or "boss")
                conn.execute(
                    "INSERT OR REPLACE INTO screening_results "
                    "(id, run_id, platform, platform_job_id, job_id, verdict, created_at, title, company, salary, "
                    " location, tags, jd, source_url, verdict_reason, caveats_json, is_dropped, "
                    " experience, degree, extra_json) "
                    "VALUES (?, ?, ?, ?, ?, 'dropped', ?, ?, ?, ?, ?, ?, '', ?, ?, '[]', 1, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), run_id, platform,
                        str(job.get("platform_job_id") or job.get("job_id") or ""),
                        None,
                        now,
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("salary", ""),
                        job.get("location", ""),
                        job.get("tags", ""),
                        job.get("canonical_url", ""),
                        job.get("reason", ""),
                        job.get("experience", ""),
                        job.get("degree", ""),
                        json.dumps(job.get("extra") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
            for job in pending_jobs:
                failed_code = str(
                    job.get("failed_code") or job.get("jd_failed_code")
                    or ("ai_missing_job" if job.get("jd") else "detail_invalid")
                )
                failure_stage = str(
                    job.get("failed_stage")
                    or ("ai_fine" if job.get("jd") else "jd_detail")
                )
                conn.execute(
                    "INSERT INTO screening_pending_results "
                    "(id, run_id, platform, platform_job_id, failure_stage, retryable, attempts, "
                    " last_failed_at, origin_zone, ai_payload_json, created_at, failed_code) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), run_id,
                        str(script_params.get("platform") or result.get("platform") or "boss"),
                        str(job.get("platform_job_id") or job.get("job_id") or ""),
                        failure_stage,
                        0 if failed_code == "job_offline" else 1,
                        int(job.get("attempts") or 1), now,
                        str(job.get("origin_zone") or "kept"),
                        json.dumps(job.get("ai_payload") or {}, ensure_ascii=False),
                        now, failed_code,
                    ),
                )
        return run_id

    def load_latest_pipeline_result(self, run_id: str | None = None) -> dict | None:
        """Load the most recent successful pipeline run from the database.

        Returns a payload matching the old JSON file format:
        {"saved_at": ..., "script_params": {...}, "result": {...}}
        or None if no successful run exists.
        """
        with self._connection() as conn:
            if run_id:
                run = conn.execute(
                    "SELECT * FROM screening_runs WHERE id = ? "
                    "AND record_kind = 'result_snapshot' LIMIT 1",
                    (str(run_id),),
                ).fetchone()
            else:
                run = conn.execute(
                    "SELECT * FROM screening_runs WHERE status IN ('done', 'partial') "
                    "AND record_kind = 'result_snapshot' "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1",
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
                raw_verdict = row.get("verdict") or ""
                reason = row.get("verdict_reason") or ""
                try:
                    parsed = json.loads(raw_verdict)
                    if isinstance(parsed, dict):
                        reason = str(parsed.get("reason") or reason)
                except (json.JSONDecodeError, TypeError):
                    pass
                extra = {}
                try:
                    extra = json.loads(row.get("extra_json") or "{}")
                except (json.JSONDecodeError, TypeError):
                    pass
                dropped.append({
                    "platform": row.get("platform"),
                    "platform_job_id": row["platform_job_id"],
                    "job_id": row.get("job_id"),
                    "title": row["title"],
                    "experience": row.get("experience") or "",
                    "degree": row.get("degree") or "",
                    "extra": extra,
                    "reason": reason,
                    "canonical_url": row["source_url"],
                })
            else:
                raw_verdict = row.get("verdict") or ""
                verdict = raw_verdict
                verdict_reason = row.get("verdict_reason") or ""
                caveats = []
                try:
                    caveats = json.loads(row.get("caveats_json") or "[]")
                except (json.JSONDecodeError, TypeError):
                    pass
                try:
                    parsed = json.loads(raw_verdict)
                    if isinstance(parsed, dict):
                        verdict = str(parsed.get("verdict") or raw_verdict)
                        verdict_reason = str(parsed.get("reason") or verdict_reason)
                        if isinstance(parsed.get("caveats"), list):
                            caveats = parsed["caveats"]
                except (json.JSONDecodeError, TypeError):
                    pass
                extra = {}
                try:
                    extra = json.loads(row.get("extra_json") or "{}")
                except (json.JSONDecodeError, TypeError):
                    pass
                jobs.append({
                    "platform": row.get("platform"),
                    "platform_job_id": row["platform_job_id"],
                    "job_id": row.get("job_id"),
                    "title": row["title"],
                    "company": row["company"],
                    "salary": row["salary"],
                    "location": row["location"],
                    "experience": row.get("experience") or "",
                    "degree": row.get("degree") or "",
                    "extra": extra,
                    "tags": row["tags"],
                    "jd": row["jd"],
                    "source_url": row["source_url"],
                    "canonical_url": row["source_url"],
                    "verdict": verdict,
                    "verdict_reason": verdict_reason,
                    "caveats": caveats,
                })

        script_params = {}
        try:
            script_params = json.loads(run.get("search_params_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        execution_params = {}
        try:
            execution_params = json.loads(run.get("execution_params_json") or "{}")
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
            "run_id": run["id"],
            "platform": run.get("platform"),
            "saved_at": run["created_at"],
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "script_params": script_params,
            "status": "completed_with_pending" if run.get("status") == "partial" else "completed",
            "execution_config": execution_params.get("execution_config") or {},
            "result": result,
        }

    def recount_pipeline_result(self, run_id):
        """Recompute result_snapshot counts after recrawl write-back."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT record_kind FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            if row is None or row["record_kind"] != "result_snapshot":
                return None
            rows = conn.execute(
                "SELECT verdict, is_dropped FROM screening_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()["n"]
        match = mismatch = kept = dropped = 0
        for row in rows:
            if row["is_dropped"]:
                dropped += 1
                continue
            kept += 1
            verdict = row["verdict"] or ""
            try:
                parsed = json.loads(verdict)
                if isinstance(parsed, dict):
                    verdict = str(parsed.get("verdict") or "")
            except (json.JSONDecodeError, TypeError):
                pass
            if verdict == "match":
                match += 1
            elif verdict in ("not_match", "mismatch"):
                mismatch += 1
        status = "done" if pending == 0 else "partial"
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET status = ?, match_count = ?, mismatch_count = ?, "
                " pending_count = ?, processed_count = ?, total_kept = ?, "
                " total_dropped = ?, source_count = ?, updated_at = ? WHERE id = ?",
                (
                    status, match, mismatch, pending, match + mismatch,
                    kept, dropped, kept + dropped, _now(), str(run_id),
                ),
            )
        return {
            "status": status, "match_count": match, "mismatch_count": mismatch,
            "pending_count": pending, "processed_count": match + mismatch,
            "total_kept": kept, "total_dropped": dropped,
        }

    def get_latest_done_run_id(self) -> str | None:
        """Return the run_id of the most recent successful pipeline run, or None."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM screening_runs WHERE status IN ('done', 'partial') "
                "AND record_kind = 'result_snapshot' "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        return row["id"] if row else None

    def load_latest_pipeline_result_for_platform(self, platform: str) -> dict | None:
        """T409: 按平台加载最近一次成功结果。"""
        with self._connection() as conn:
            run = conn.execute(
                "SELECT * FROM screening_runs WHERE platform=? AND "
                "status IN ('done', 'partial') AND record_kind = 'result_snapshot' "
                "ORDER BY created_at DESC LIMIT 1",
                (str(platform),),
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
                raw_verdict = row.get("verdict") or ""
                reason = row.get("verdict_reason") or ""
                try:
                    parsed = json.loads(raw_verdict)
                    if isinstance(parsed, dict):
                        reason = str(parsed.get("reason") or reason)
                except (json.JSONDecodeError, TypeError):
                    pass
                dropped.append({
                    "job_id": row.get("job_id"),
                    "platform_job_id": row.get("platform_job_id"),
                    "platform": row.get("platform"),
                    "title": row.get("title"),
                    "company": row.get("company"),
                    "salary": row.get("salary"),
                    "location": row.get("location"),
                    "reason": reason,
                    "canonical_url": row.get("canonical_url"),
                })
                continue
            jobs.append({
                "job_id": row.get("job_id"),
                "platform_job_id": row.get("platform_job_id"),
                "platform": row.get("platform"),
                "title": row.get("title"),
                "company": row.get("company"),
                "salary": row.get("salary"),
                "location": row.get("location"),
                "experience": row.get("experience"),
                "degree": row.get("degree"),
                "jd": row.get("jd") or "",
                "canonical_url": row.get("canonical_url"),
                "source_url": row.get("canonical_url"),
                "extra": {},
            })

        execution_params = json.loads(run["execution_params_json"] or "{}") if "execution_params_json" in run.keys() else {}
        return {
            "run_id": run["id"],
            "platform": run.get("platform"),
            "status": run.get("status"),
            "saved_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "script_params": execution_params.get("script_params", {}),
            "execution_config": execution_params.get("execution_config", {}),
            "result": {
                "total_scraped": run.get("total_scraped", 0),
                "total_matched": run.get("total_scraped", 0),
                "jobs": jobs,
                "dropped": dropped,
                "profile_summary": execution_params.get("profile_summary", ""),
            },
        }

    def latest_pipeline_result_saved_at(self) -> str | None:
        """Return created_at of the newest result snapshot, or None."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT created_at FROM screening_runs "
                "WHERE status IN ('done', 'partial') AND record_kind = 'result_snapshot' "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        return row["created_at"] if row is not None else None

    def update_pipeline_job_jd(self, run_id: str, job_id: str, jd: str):
        """Update the JD text for a specific job in a pipeline run (补抓 JD)."""
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_results SET jd = ? WHERE run_id = ? AND platform_job_id = ?",
                (jd, str(run_id), str(job_id)),
            )

    def clear_latest_pipeline_result(self) -> bool:
        """Delete the most recent result_snapshot and its cascade data.

        重新上传简历时调用：只清结果存档，不动 process_log 和进行中的任务。
        """
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            run = conn.execute(
                "SELECT id FROM screening_runs WHERE status IN ('done', 'partial') "
                "AND record_kind = 'result_snapshot' "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
            if run is None:
                return False
            run_id = str(run["id"])
            # 先删 tasks 占位行，让 task_logs 经外键级联一起清掉
            conn.execute("DELETE FROM tasks WHERE id = ?", (run_id,))
            for table in (
                "screening_results",
                "screening_pending_results",
                "pipeline_checkpoints",
                "scrape_run_jobs",
            ):
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM screening_runs WHERE id = ?", (run_id,))
            return True

    def clear_pipeline_result(self, run_id: str) -> bool:
        """T418: 删除指定 run 的结果存档，保留 source attempts 和审计记录。

        不删除 screening_source_attempts、jobs、profile_jobs、feedback_events
        或其他 run 的数据。
        """
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            run = conn.execute(
                "SELECT id FROM screening_runs WHERE id=? AND record_kind='result_snapshot'",
                (str(run_id),),
            ).fetchone()
            if run is None:
                return False
            conn.execute("DELETE FROM tasks WHERE id = ?", (str(run_id),))
            for table in (
                "screening_results",
                "screening_pending_results",
                "pipeline_checkpoints",
                "scrape_run_jobs",
            ):
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (str(run_id),))
            conn.execute("DELETE FROM screening_runs WHERE id = ?", (str(run_id),))
            return True

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
                conn, "jobs", "platform", "TEXT NOT NULL DEFAULT 'boss'"
            )
            self._add_column_if_missing(
                conn, "jobs", "platform_job_id", "TEXT"
            )
            self._add_column_if_missing(
                conn, "jobs", "experience", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column_if_missing(
                conn, "jobs", "degree", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column_if_missing(
                conn, "jobs", "extra_json", "TEXT NOT NULL DEFAULT '{}'"
            )

            # --------------------------------------------------------------
            # 2. screening_runs 新增列
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "screening_runs", "platform", "TEXT NOT NULL DEFAULT 'boss'"
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
                conn, "screening_results", "platform", "TEXT NOT NULL DEFAULT 'boss'"
            )
            # 旧 job_id 列语义=平台原始ID，按 data-model.md 重命名为 platform_job_id
            self._rename_column_with_data(
                conn, "screening_results", "job_id", "platform_job_id",
                old_type="TEXT NOT NULL", new_type="TEXT NOT NULL",
            )
            # 新增可空内部 job_id（内部UUID语义，落库前可空）
            self._add_column_if_missing(
                conn, "screening_results", "job_id", "TEXT"
            )
            self._add_column_if_missing(
                conn, "screening_results", "experience", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column_if_missing(
                conn, "screening_results", "degree", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column_if_missing(
                conn, "screening_results", "extra_json", "TEXT NOT NULL DEFAULT '{}'"
            )

            # --------------------------------------------------------------
            # 4. screening_pending_results: job_id → platform_job_id
            # --------------------------------------------------------------
            self._rename_column_with_data(
                conn, "screening_pending_results", "job_id", "platform_job_id",
                old_type="TEXT NOT NULL", new_type="TEXT NOT NULL",
            )
            self._add_column_if_missing(
                conn, "screening_pending_results", "platform", "TEXT NOT NULL DEFAULT 'boss'"
            )

            # --------------------------------------------------------------
            # 5. scrape_run_jobs: job_id → platform_job_id
            # --------------------------------------------------------------
            self._rename_column_with_data(
                conn, "scrape_run_jobs", "job_id", "platform_job_id",
                old_type="TEXT NOT NULL", new_type="TEXT NOT NULL",
            )

            # --------------------------------------------------------------
            # 6. tuning_experiments / tuning_task_manifests 新增 platform
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "tuning_experiments", "platform", "TEXT NOT NULL DEFAULT 'boss'"
            )
            self._add_column_if_missing(
                conn, "tuning_task_manifests", "platform", "TEXT NOT NULL DEFAULT 'boss'"
            )

            # --------------------------------------------------------------
            # 7. tuning_stage_artifacts 新增外层列
            # --------------------------------------------------------------
            self._add_column_if_missing(
                conn, "tuning_stage_artifacts", "platform", "TEXT NOT NULL DEFAULT 'boss'"
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

    # -- T112: Job 双索引冲突算法 upsert ---------------------------------

    def upsert_job(
        self,
        *,
        platform: str,
        platform_job_id: str | None,
        canonical_url: str,
        title: str = "",
        company: str = "",
        salary: str = "",
        location: str = "",
        jd: str = "",
        experience: str = "",
        degree: str = "",
        extra: dict | None = None,
    ) -> dict:
        """Job 双索引冲突算法（data-model.md "Job upsert 冲突算法"）。

        返回 {"ok": bool, "job_id": str | None, "error_code": str | None}。
        所有 8 个分支在同一事务中完成。
        """
        # 分支1：URL host/path 必须属于声明平台——复用 tasks001 的平台注册规则
        # （contracts/job-source.md 禁止在 store 内复制第二套 host/path 规则）
        from webui.platforms import normalize_job_url
        if not normalize_job_url(platform, canonical_url):
            return {"ok": False, "job_id": None, "error_code": "platform_url_mismatch"}

        extra_json = json.dumps(extra or {}, ensure_ascii=False, sort_keys=True)
        now = _now()

        with self._connection() as conn:
            # 分支2：查询 (platform, platform_job_id) 和 canonical_url
            by_pid = None
            if platform_job_id:
                row = conn.execute(
                    "SELECT id, canonical_url FROM jobs WHERE platform=? AND platform_job_id=?",
                    (platform, platform_job_id),
                ).fetchone()
                if row is not None:
                    by_pid = {"id": row["id"], "canonical_url": row["canonical_url"]}

            by_url = conn.execute(
                "SELECT id, platform, platform_job_id FROM jobs WHERE canonical_url=?",
                (canonical_url,),
            ).fetchone()
            by_url_dict = None
            if by_url is not None:
                by_url_dict = {
                    "id": by_url["id"],
                    "platform": by_url["platform"],
                    "platform_job_id": by_url["platform_job_id"],
                }

            # 分支2：URL 命中但平台不一致
            if by_url_dict and by_url_dict["platform"] != platform:
                return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}

            # 分支3：两者都没有 → 创建新行
            if by_pid is None and by_url_dict is None:
                new_id = _uuid()
                conn.execute(
                    "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, "
                    "location, jd, first_seen_at, last_seen_at, platform, platform_job_id, "
                    "experience, degree, extra_json) "
                    "VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id, canonical_url, title, company, salary, location, jd,
                     now, now, platform, platform_job_id, experience, degree, extra_json),
                )
                return {"ok": True, "job_id": new_id, "error_code": None}

            # 分支4：只命中平台ID
            if by_pid is not None and by_url_dict is None:
                # 检查新 URL 是否被其它行占用
                other = conn.execute(
                    "SELECT id FROM jobs WHERE canonical_url=? AND id != ?",
                    (canonical_url, by_pid["id"]),
                ).fetchone()
                if other is not None:
                    return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
                # 更新原行的 URL 和可变字段
                conn.execute(
                    "UPDATE jobs SET canonical_url=?, title=?, company=?, salary=?, location=?, "
                    "jd=?, experience=?, degree=?, extra_json=?, last_seen_at=? WHERE id=?",
                    (canonical_url, title, company, salary, location, jd,
                     experience, degree, extra_json, now, by_pid["id"]),
                )
                return {"ok": True, "job_id": by_pid["id"], "error_code": None}

            # 分支5：只命中URL
            if by_pid is None and by_url_dict is not None:
                # 平台必须一致（分支2已检查）
                # platform_job_id 为 NULL 或等于输入值时补写
                existing_pid = by_url_dict["platform_job_id"]
                if existing_pid is not None and platform_job_id is not None and existing_pid != platform_job_id:
                    return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
                conn.execute(
                    "UPDATE jobs SET platform_job_id=?, title=?, company=?, salary=?, location=?, "
                    "jd=?, experience=?, degree=?, extra_json=?, last_seen_at=? WHERE id=?",
                    (platform_job_id, title, company, salary, location, jd,
                     experience, degree, extra_json, now, by_url_dict["id"]),
                )
                return {"ok": True, "job_id": by_url_dict["id"], "error_code": None}

            # 分支6和7：两者都命中
            if by_pid is not None and by_url_dict is not None:
                if by_pid["id"] == by_url_dict["id"]:
                    # 分支6：同一行，更新可变字段
                    conn.execute(
                        "UPDATE jobs SET canonical_url=?, title=?, company=?, salary=?, location=?, "
                        "jd=?, experience=?, degree=?, extra_json=?, last_seen_at=? WHERE id=?",
                        (canonical_url, title, company, salary, location, jd,
                         experience, degree, extra_json, now, by_pid["id"]),
                    )
                    return {"ok": True, "job_id": by_pid["id"], "error_code": None}
                else:
                    # 分支7：不同行，冲突
                    return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}

            return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}

    # -- T113: 结果快照读写 API -------------------------------------------

    def save_result_snapshot(
        self,
        *,
        run_id: str,
        platform: str,
        platform_job_id: str,
        job_id: str | None = None,
        verdict: str,
        title: str = "",
        company: str = "",
        salary: str = "",
        location: str = "",
        jd: str = "",
        experience: str = "",
        degree: str = "",
        extra: dict | None = None,
    ) -> str:
        """T113: 保存结果快照，同时记录 platform、platform_job_id、可空内部 job_id 和完整岗位字段。

        返回结果行 id。如果 (run_id, platform_job_id) 已存在则更新。
        """
        extra_json = json.dumps(extra or {}, ensure_ascii=False, sort_keys=True)
        ts = _now()
        result_id = _uuid()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO screening_results "
                "(id, run_id, job_id, verdict, created_at, platform, platform_job_id, "
                " experience, degree, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                " job_id = excluded.job_id, verdict = excluded.verdict, "
                " experience = excluded.experience, degree = excluded.degree, "
                " extra_json = excluded.extra_json",
                (result_id, str(run_id), job_id, str(verdict), ts,
                 str(platform), str(platform_job_id),
                 experience, degree, extra_json),
            )
        return result_id

    # -- T114: source attempt 追加及汇总 API ------------------------------

    def append_source_attempt(
        self,
        *,
        run_id: str,
        platform: str,
        combo_key: str,
        attempt_no: int,
        input_hash: str | None = None,
        outcome_kind: str,
        job_count: int = 0,
        empty_evidence: dict | None = None,
        error_code: str | None = None,
        error_reason: str | None = None,
    ) -> str:
        """T114: 追加一条 source attempt 记录。返回记录 id。

        禁止从零岗位反推 empty：outcome_kind='empty' 时 empty_evidence 必填。
        """
        if outcome_kind == "empty" and not empty_evidence:
            raise ValueError("outcome_kind='empty' 时 empty_evidence 必填")
        evidence_json = json.dumps(empty_evidence or {}, ensure_ascii=False, sort_keys=True) if empty_evidence else None
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            cur = conn.execute(
                "INSERT INTO screening_source_attempts "
                "(run_id, platform, combo_key, attempt_no, input_hash, "
                " outcome_kind, job_count, empty_evidence_json, error_code, error_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(run_id), str(platform), str(combo_key), int(attempt_no),
                 input_hash, str(outcome_kind), int(job_count), evidence_json,
                 error_code, error_reason, ts),
            )
            attempt_id = cur.lastrowid
        return attempt_id

    def get_latest_source_attempt(self, run_id: str, combo_key: str) -> dict | None:
        """T114: 按 run/combo 获取最新 attempt。返回字典或 None。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_source_attempts "
                "WHERE run_id=? AND combo_key=? ORDER BY attempt_no DESC LIMIT 1",
                (str(run_id), str(combo_key)),
            ).fetchone()
        if row is None:
            return None
        return self._source_attempt_row(row)

    def list_latest_source_attempts(self, run_id: str) -> list[dict]:
        """T405: 按 run 列出所有 combo 的最新 attempt。

        返回安全投影列表（不含敏感字段），每个 combo 一条。
        刷新/重启后从此方法汇总 source outcomes，不从岗位数为零反推 empty。
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT s.* FROM screening_source_attempts s "
                "INNER JOIN ("
                "  SELECT combo_key, MAX(attempt_no) AS max_no "
                "  FROM screening_source_attempts WHERE run_id=? "
                "  GROUP BY combo_key"
                ") m ON s.combo_key=m.combo_key AND s.attempt_no=m.max_no "
                "WHERE s.run_id=? "
                "ORDER BY s.combo_key",
                (str(run_id), str(run_id)),
            ).fetchall()
        return [self._source_attempt_row(row) for row in rows]

    @staticmethod
    def _source_attempt_row(row) -> dict:
        """安全投影：返回 source attempt 的安全字段。"""
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "platform": row["platform"],
            "combo_key": row["combo_key"],
            "attempt_no": int(row["attempt_no"]),
            "input_hash": row["input_hash"],
            "outcome_kind": row["outcome_kind"],
            "job_count": int(row["job_count"]),
            "empty_evidence": json.loads(row["empty_evidence_json"] or "{}") if row["empty_evidence_json"] else None,
            "error_code": row["error_code"],
            "error_reason": row["error_reason"],
            "created_at": row["created_at"],
        }

    # -- T115: 筛选快照、task digest、interruption kind 持久化 ------------

    def save_filter_snapshot(
        self,
        run_id: str,
        *,
        platform: str,
        filter_schema_version: int | None = None,
        filter_snapshot: dict | None = None,
        task_input_digest: str | None = None,
    ) -> None:
        """T115: 持久化筛选快照、schema version 和 task input digest。"""
        snapshot_json = json.dumps(filter_snapshot or {}, ensure_ascii=False, sort_keys=True)
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET platform=?, filter_schema_version=?, "
                "filter_snapshot_json=?, task_input_digest=?, updated_at=? WHERE id=?",
                (str(platform), filter_schema_version, snapshot_json,
                 task_input_digest, ts, str(run_id)),
            )

    def save_interruption_kind(self, run_id: str, interruption_kind: str) -> None:
        """T115: 持久化 interruption kind（仅 status=interrupted 时使用）。"""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET interruption_kind=?, updated_at=? WHERE id=?",
                (str(interruption_kind), ts, str(run_id)),
            )

    def get_run_checkpoint_identity(self, run_id: str) -> dict | None:
        """T115: 读取 run 的 checkpoint 身份一致性信息。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT platform, filter_schema_version, filter_snapshot_json, "
                "task_input_digest, interruption_kind FROM screening_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "platform": row["platform"],
            "filter_schema_version": row["filter_schema_version"],
            "filter_snapshot": json.loads(row["filter_snapshot_json"] or "{}") if row["filter_snapshot_json"] else {},
            "task_input_digest": row["task_input_digest"],
            "interruption_kind": row["interruption_kind"],
        }

    def save_checkpoint_identity(
        self,
        run_id: str,
        *,
        platform: str,
        filter_schema_version: int | None = None,
        filter_snapshot: dict | None = None,
        task_input_digest: str | None = None,
        interruption_kind: str | None = None,
    ) -> None:
        """T115: 持久化 checkpoint 身份一致性信息（写入端）。

        一次性写入 platform、filter_schema_version、filter_snapshot_json、
        task_input_digest 和 interruption_kind。用于 run 创建时冻结身份。
        """
        snapshot_json = json.dumps(filter_snapshot or {}, ensure_ascii=False, sort_keys=True)
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET platform=?, filter_schema_version=?, "
                "filter_snapshot_json=?, task_input_digest=?, interruption_kind=?, "
                "updated_at=? WHERE id=?",
                (str(platform), filter_schema_version, snapshot_json,
                 task_input_digest, interruption_kind, ts, str(run_id)),
            )

    def verify_checkpoint_identity(
        self,
        run_id: str,
        *,
        expected_platform: str,
        expected_task_input_digest: str | None = None,
    ) -> tuple[bool, str]:
        """T115: 校验 checkpoint 身份一致性。

        继续运行前调用：若 run 的 platform 与 expected_platform 不一致，
        或 task_input_digest 与期望值不一致，返回 (False, reason)。
        """
        identity = self.get_run_checkpoint_identity(run_id)
        if identity is None:
            return (False, "run_not_found")
        if identity["platform"] != expected_platform:
            return (False, f"platform_mismatch: {identity['platform']} != {expected_platform}")
        if expected_task_input_digest is not None and identity["task_input_digest"] != expected_task_input_digest:
            return (False, "task_input_digest_mismatch")
        return (True, "")

    # -- T116: 收藏/反馈原子 upsert + 内部 UUID 关联 ----------------------

    def upsert_job_for_favorite(
        self,
        *,
        platform: str,
        platform_job_id: str,
        canonical_url: str,
        title: str = "",
        company: str = "",
        salary: str = "",
        location: str = "",
        jd: str = "",
        experience: str = "",
        degree: str = "",
        extra: dict | None = None,
    ) -> dict:
        """T116: 收藏/反馈所需的原子"岗位 upsert + 内部 UUID 关联"存储操作。

        不把 platform_job_id 当内部 UUID。内部 job_id 由 upsert_job 分配。
        """
        return self.upsert_job(
            platform=platform,
            platform_job_id=platform_job_id,
            canonical_url=canonical_url,
            title=title,
            company=company,
            salary=salary,
            location=location,
            jd=jd,
            experience=experience,
            degree=degree,
            extra=extra,
        )

    def schema_version(self) -> int:
        with self._connection() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    # -- legacy task API (unchanged) --------------------------------------

    def create_task(self, task_id, kind, params, output_path=None, detail_output_path=None):
        timestamp = _now()
        with self._connection() as connection:
            self._assert_recovery_writes_allowed(connection)
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
            self._assert_recovery_writes_allowed(conn)
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
                             profile_id=None, execution_params=None,
                             backend_version=None):
        """登记一个 AI 筛选任务（网页两段式筛选）。

        表是 migration_004/007/010 建好的（此前无写入方），本方法是启用入口。
        run_id 直接用任务 id，便于与内存任务/前端轮询对齐。
        """
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, frozen_filters_json, status, source_count, match_count, mismatch_count, "
                "created_at, updated_at, started_at, error_code, resume_id, pending_count, "
                "processed_count, source_cursor, parse_failure_count, parse_failures_json, "
                "profile_id, execution_params_json, record_kind, backend_version) "
                "VALUES (?, ?, ?, 'queued', ?, 0, 0, ?, ?, ?, NULL, NULL, 0, 0, 0, 0, '{}', ?, ?, 'process_log', ?)",
                (
                    str(run_id),
                    str((execution_params or {}).get("platform") or "boss"),
                    json.dumps(frozen_filters or {}, ensure_ascii=False),
                    int(source_count), ts, ts, ts,
                    str(profile_id) if profile_id else None,
                    json.dumps(execution_params or {}, ensure_ascii=False),
                    str(backend_version) if backend_version else None,
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
                             error_code=None, pending_count=None,
                             current_stage=None, error_reason=None,
                             backend_version=None, total_dropped=None,
                             total_kept=None, total_scraped=None,
                             source_count=None):
        """更新 screening_run，含状态机校验（FR-005）。

        状态必须按 RUN_TRANSITIONS 合法路径迁移。非法迁移抛 ValueError。
        新增字段（migration_020）：current_stage / error_reason / backend_version。
        守恒字段（migration_018）：total_dropped / total_kept / total_scraped。
        """
        if status is not None:
            # 向后兼容映射（app.py 历史用 done/cancelled，统一到 RUN_STATUSES）
            _status_aliases = {"done": "succeeded", "cancelled": "interrupted"}
            status = _status_aliases.get(status, status)
            if status not in RUN_STATUSES:
                raise ValueError(f"未知运行状态: {status}")
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
        if current_stage is not None:
            sets.append("current_stage = ?")
            params.append(str(current_stage))
        if error_reason is not None:
            sets.append("error_reason = ?")
            params.append(str(error_reason))
        if backend_version is not None:
            sets.append("backend_version = ?")
            params.append(str(backend_version))
        if total_dropped is not None:
            sets.append("total_dropped = ?")
            params.append(int(total_dropped))
        if total_kept is not None:
            sets.append("total_kept = ?")
            params.append(int(total_kept))
        if total_scraped is not None:
            sets.append("total_scraped = ?")
            params.append(int(total_scraped))
        if source_count is not None:
            sets.append("source_count = ?")
            params.append(int(source_count))
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(str(run_id))
        with self._connection() as conn:
            # 状态校验和写入必须共享同一个立即事务。否则两个线程可同时读到
            # running，并分别把取消/成功两个互斥终态写入，后写者覆盖先写者。
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            if status is not None:
                current = conn.execute(
                    "SELECT status FROM screening_runs WHERE id = ?", (str(run_id),)
                ).fetchone()
                if current is None:
                    raise KeyError(run_id)
                cur_status = current["status"]
                if (
                    status != cur_status
                    and status not in RUN_TRANSITIONS.get(cur_status, set())
                ):
                    raise ValueError(f"运行不能从 {cur_status} 转换到 {status}")
            conn.execute(
                f"UPDATE screening_runs SET {', '.join(sets)} WHERE id = ?", params
            )

    def update_screening_execution_params(self, run_id, params: dict) -> None:
        """Replace the JSON execution params for a screening run."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET execution_params_json = ?, updated_at = ? "
                "WHERE id = ?", (
                    json.dumps(params or {}, ensure_ascii=False), _now(), str(run_id),
                ),
            )

    def claim_paused_screening_run(self, run_id) -> bool:
        """Atomically claim one paused run for in-place continuation.

        Unlike the general status updater, this operation is deliberately not
        idempotent: exactly one caller may change ``paused`` to ``running``.
        """
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            cursor = conn.execute(
                "UPDATE screening_runs SET status = 'running', error_code = NULL, "
                "error_reason = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'paused'",
                (_now(), str(run_id)),
            )
            return cursor.rowcount == 1

    def finalize_run_status(self, run_id):
        """根据当前进度判定最终状态（FR-016, FR-036）。

        - 存在未开始岗位 OR 系统性阻断 → paused
        - 存在待确认岗位（独立失败）但无阻断 → partial（completed_with_pending）
        - 全部处理且无待确认 → succeeded（completed）
        """
        run = self.get_screening_run(run_id)
        if run is None:
            raise KeyError(run_id)
        cur_status = run["status"]
        # 终态不再重新判定
        if cur_status in ("succeeded", "partial", "failed", "interrupted"):
            return cur_status
        source_count = run.get("source_count", 0) or 0
        processed = run.get("processed_count", 0) or 0
        match = run.get("match_count", 0) or 0
        mismatch = run.get("mismatch_count", 0) or 0
        pending = run.get("pending_count", 0) or 0
        error_code = run.get("error_code")
        # 系统性阻断 → paused
        if error_code and error_code in SYSTEMIC_BLOCK_CODES:
            if cur_status != "paused":
                self.update_screening_run(run_id, status="paused")
            return "paused"
        # 存在未开始岗位 → paused（不得伪装完成）
        dropped = run.get("total_dropped", 0) or 0
        total_accounted = processed + pending + dropped
        if total_accounted < source_count:
            if cur_status != "paused":
                self.update_screening_run(run_id, status="paused")
            return "paused"
        # 有待确认但无阻断 → partial（completed_with_pending）
        if pending > 0:
            if cur_status != "partial":
                self.update_screening_run(run_id, status="partial")
            return "partial"
        # 全部处理且无待确认 → succeeded（completed）
        if cur_status != "succeeded":
            self.update_screening_run(run_id, status="succeeded")
        return "succeeded"

    # -- pending results（待确认岗位，FR-011~016/FR-040） -------------------

    def insert_pending_result(self, run_id, job_id, *, failure_stage, retryable=True,
                              attempts=1, origin_zone="match", ai_payload_json=None,
                              failed_code=None):
        """登记一条待确认岗位（独立失败）。同一 (run_id, job_id) 重复写则更新。

        FR-040：必须带具体 failed_code，禁止仅用"未抓到 JD"等模糊描述。
        """
        if not failed_code and failure_stage:
            # 兜底：failure_stage 推默认 code（仍要求调用方尽量传 failed_code）
            failed_code = failed_code or failure_stage
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO screening_pending_results "
                "(id, run_id, platform_job_id, failure_stage, retryable, attempts, last_failed_at, "
                " origin_zone, ai_payload_json, created_at, failed_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                " failure_stage = excluded.failure_stage, "
                " retryable = excluded.retryable, "
                " attempts = excluded.attempts, "
                " last_failed_at = excluded.last_failed_at, "
                " origin_zone = excluded.origin_zone, "
                " ai_payload_json = excluded.ai_payload_json, "
                " failed_code = excluded.failed_code",
                (
                    _uuid(), str(run_id), str(job_id), str(failure_stage),
                    1 if retryable else 0, int(attempts), ts,
                    str(origin_zone),
                    json.dumps(ai_payload_json or {}, ensure_ascii=False),
                    ts, str(failed_code) if failed_code else None,
                ),
            )
        self.update_pending_count(run_id)
        return self.get_pending_result(run_id, job_id)

    def update_pending_count(self, run_id):
        """从 screening_pending_results 实时计数并写回 screening_runs.pending_count。

        FR-016/SC-018：pending_count 必须反映真实待确认数，不得恒为 0。
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
        count = int(row["n"] or 0)
        # 直接写库，绕过状态机（pending_count 是数据字段，不是状态）
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET pending_count = ?, updated_at = ? WHERE id = ?",
                (count, _now(), str(run_id)),
            )
        return count

    def get_pending_result(self, run_id, job_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_pending_results WHERE run_id = ? AND platform_job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
        return self._pending_result_row(row) if row is not None else None

    def list_pending_results(self, run_id):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_pending_results WHERE run_id = ? "
                "ORDER BY last_failed_at ASC",
                (str(run_id),),
            ).fetchall()
        return [self._pending_result_row(r) for r in rows]

    def delete_pending_result(self, run_id, job_id):
        """补救成功后从待确认表移除。返回是否实际删除。"""
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            cur = conn.execute(
                "DELETE FROM screening_pending_results WHERE run_id = ? AND platform_job_id = ?",
                (str(run_id), str(job_id)),
            )
            deleted = cur.rowcount > 0
        if deleted:
            self.update_pending_count(run_id)
        return deleted

    def _pending_result_row(self, row) -> dict:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "job_id": row["platform_job_id"],
            "failure_stage": row["failure_stage"],
            "retryable": bool(row["retryable"]),
            "attempts": int(row["attempts"]),
            "last_failed_at": row["last_failed_at"],
            "origin_zone": row["origin_zone"],
            "ai_payload": json.loads(row["ai_payload_json"] or "{}"),
            "created_at": row["created_at"],
            "failed_code": row["failed_code"] if "failed_code" in row.keys() else None,
        }

    # -- checkpoints（断点续抓，FR-023） -----------------------------------

    def save_scrape_combo_result(self, run_id, combo_key, jobs, completed_combos):
        """Atomically persist one completed combination and its checkpoint."""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for job in jobs or []:
                if not isinstance(job, dict):
                    continue
                job_id = str(job.get("platform_job_id") or job.get("job_id") or job.get("source_url") or "").strip()
                if not job_id:
                    continue
                conn.execute(
                    "INSERT INTO scrape_run_jobs "
                    "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                    " combo_key = excluded.combo_key, "
                    " job_payload_json = excluded.job_payload_json, "
                    " scraped_at = excluded.scraped_at",
                    (
                        str(run_id), job_id, str(combo_key),
                        json.dumps(job, ensure_ascii=False), ts,
                    ),
                )
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) VALUES (?, 'scrape', ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(run_id),
                    json.dumps(list(completed_combos or []), ensure_ascii=False),
                    ts,
                ),
            )

    def save_recrawl_jd_and_checkpoint(
            self, source_run_id, recrawl_run_id, jd_by_job, completed_job_ids):
        """Atomically persist partial recrawl JDs and their resume checkpoint."""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for job_id, jd in (jd_by_job or {}).items():
                conn.execute(
                    "UPDATE screening_results SET jd = ? "
                    "WHERE run_id = ? AND platform_job_id = ?",
                    (str(jd), str(source_run_id), str(job_id)),
                )
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) "
                "VALUES (?, 'recrawl_jd', ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(recrawl_run_id),
                    json.dumps(sorted(set(completed_job_ids or [])), ensure_ascii=False),
                    ts,
                ),
            )

    def load_scrape_run_jobs(self, run_id):
        """Load the complete persisted job payload for a scrape run."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT job_payload_json FROM scrape_run_jobs "
                "WHERE run_id = ? ORDER BY scraped_at ASC, platform_job_id ASC",
                (str(run_id),),
            ).fetchall()
        jobs = []
        for row in rows:
            try:
                payload = json.loads(row["job_payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                jobs.append(payload)
        return jobs

    def save_checkpoint(self, run_id, stage, keys):
        """保存某阶段的已完成 key 列表。同 (run_id, stage) 覆盖。"""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO pipeline_checkpoints (run_id, stage, completed_keys_json, saved_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(run_id), str(stage),
                    json.dumps(list(keys or []), ensure_ascii=False),
                    ts,
                ),
            )

    def load_checkpoint(self, run_id, stage):
        """加载某阶段的已完成 key 列表；无记录返回空集合。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT completed_keys_json FROM pipeline_checkpoints "
                "WHERE run_id = ? AND stage = ?",
                (str(run_id), str(stage)),
            ).fetchone()
        if row is None:
            return set()
        try:
            return set(json.loads(row["completed_keys_json"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            return set()

    def list_checkpoints(self, run_id):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT stage, completed_keys_json, saved_at FROM pipeline_checkpoints "
                "WHERE run_id = ? ORDER BY saved_at ASC",
                (str(run_id),),
            ).fetchall()
        return [
            {
                "stage": r["stage"],
                "completed_keys": json.loads(r["completed_keys_json"] or "[]"),
                "saved_at": r["saved_at"],
            }
            for r in rows
        ]

    def delete_checkpoint(self, run_id, stage=None):
        """删除断点。stage=None 删除该 run 全部断点（任务成功收尾时用）。"""
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            if stage is None:
                conn.execute(
                    "DELETE FROM pipeline_checkpoints WHERE run_id = ?",
                    (str(run_id),),
                )
            else:
                conn.execute(
                    "DELETE FROM pipeline_checkpoints WHERE run_id = ? AND stage = ?",
                    (str(run_id), str(stage)),
                )

    # -- task events（FR-038） ---------------------------------------------

    def append_task_event(self, run_id, event_type, payload=None):
        """追加一条流程事件到 task_logs（FR-038）。

        事件类型：stage_start / stage_complete / job_success / job_fail /
        pause / resume / cancel / block_check。
        line 字段存 JSON：{"type":..., "payload":..., "at":...}。

        task_logs 有 FOREIGN KEY (task_id) REFERENCES tasks(id)，但 screening
        任务的 run_id 不在 tasks 表中。先 INSERT OR IGNORE 一个占位 tasks 行
        满足外键约束（status='logging' 表示仅用于事件日志锚点）。
        """
        return self.append_task_events(
            run_id, [(event_type, payload or {})]
        )[0]

    def append_task_events(self, run_id, events):
        """Append multiple structured events with consecutive sequence numbers."""
        normalized = [
            (str(event_type), payload if isinstance(payload, dict) else {})
            for event_type, payload in events
        ]
        if not normalized:
            return []
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            # 占位 tasks 行（已存在则忽略）
            conn.execute(
                "INSERT OR IGNORE INTO tasks (id, kind, status, params_json, created_at, updated_at) "
                "VALUES (?, 'screening_event_log', 'logging', '{}', ?, ?)",
                (str(run_id), ts, ts),
            )
            cur = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM task_logs WHERE task_id = ?",
                (str(run_id),),
            )
            first_seq = int(cur.fetchone()["next_seq"])
            rows = []
            result = []
            for offset, (event_type, payload) in enumerate(normalized):
                seq = first_seq + offset
                at = _now()
                line = json.dumps(
                    {"type": event_type, "payload": payload, "at": at},
                    ensure_ascii=False,
                )
                rows.append((str(run_id), seq, at, line))
                result.append({
                    "task_id": str(run_id), "seq": seq, "type": event_type,
                    "payload": payload, "at": at,
                })
            conn.executemany(
                "INSERT INTO task_logs (task_id, seq, created_at, line) VALUES (?, ?, ?, ?)",
                rows,
            )
            return result

    def list_task_events(self, run_id, after_seq=0):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT task_id, seq, created_at, line FROM task_logs "
                "WHERE task_id = ? AND seq > ? ORDER BY seq ASC",
                (str(run_id), int(after_seq)),
            ).fetchall()
        events = []
        for r in rows:
            try:
                data = json.loads(r["line"])
            except (json.JSONDecodeError, TypeError):
                data = {"type": "raw", "payload": {"text": r["line"]}, "at": r["created_at"]}
            events.append({
                "seq": int(r["seq"]), "type": data.get("type", "raw"),
                "payload": data.get("payload", {}), "at": data.get("at", r["created_at"]),
            })
        return events

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
                "AND error_code = 'restart' "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return self._screening_run_row(row) if row is not None else None

    def save_screening_verdicts(self, run_id, verdicts):
        """每批精筛判定落盘（upsert）：进程崩了也能从 screening_results 续。"""
        if not verdicts:
            return
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for job_id, verdict in verdicts.items():
                if isinstance(verdict, dict):
                    verdict_value = str(verdict.get("verdict") or "")
                    reason = str(verdict.get("reason") or "")
                    caveats = verdict.get("caveats") if isinstance(verdict.get("caveats"), list) else []
                else:
                    verdict_value = str(verdict or "")
                    reason = ""
                    caveats = []
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, platform_job_id, verdict, verdict_reason, caveats_json, is_dropped, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                    " verdict = excluded.verdict, "
                    " verdict_reason = excluded.verdict_reason, "
                    " caveats_json = excluded.caveats_json, "
                    " is_dropped = excluded.is_dropped",
                    (
                        _uuid(), str(run_id), str(job_id), verdict_value, reason,
                        json.dumps(caveats, ensure_ascii=False),
                        1 if verdict_value == "dropped" else 0, ts,
                    ),
                )

    def save_verdict_and_checkpoint_atomic(
            self, run_id, stage, verdicts, completed_job_ids):
        """Persist one AI batch and advance its checkpoint in one transaction."""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for job_id, verdict in (verdicts or {}).items():
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, platform_job_id, verdict, created_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET verdict = excluded.verdict",
                    (
                        _uuid(), str(run_id), str(job_id),
                        json.dumps(verdict, ensure_ascii=False), ts,
                    ),
                )
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(run_id), str(stage),
                    json.dumps(list(completed_job_ids or []), ensure_ascii=False), ts,
                ),
            )

    def load_screening_verdicts(self, run_id):
        """载入某次筛选已落盘的判定 {job_id: verdict}（断点续筛用）。

        同时支持 JSON verdict（精筛）和纯字符串 verdict（粗筛）。
        - JSON verdict：返回完整 dict
        - 纯字符串 verdict：返回 {"verdict": "match"/"not_match"/...}
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT platform_job_id, verdict, verdict_reason, caveats_json "
                "FROM screening_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchall()
        out = {}
        for row in rows:
            v = row["verdict"] or ""
            reason = row["verdict_reason"] or ""
            try:
                caveats = json.loads(row["caveats_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                caveats = []
            try:
                value = json.loads(v)
                if isinstance(value, dict):
                    if not value.get("reason"):
                        value["reason"] = reason
                    if "caveats" not in value:
                        value["caveats"] = caveats
                    out[str(row["platform_job_id"])] = value
                else:
                    out[str(row["platform_job_id"])] = {"verdict": str(value), "reason": reason, "caveats": caveats}
            except (json.JSONDecodeError, TypeError):
                # 纯字符串 verdict（如 match/not_match/uncertain/dropped）
                if v:
                    out[str(row["platform_job_id"])] = {"verdict": v, "reason": reason, "caveats": caveats}
        return out

    def load_screening_pending(self, run_id):
        """Return per-job pending failures for a screening run."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT platform_job_id, failure_stage, retryable, attempts, failed_code, "
                " ai_payload_json, last_failed_at FROM screening_pending_results "
                "WHERE run_id = ?", (str(run_id),),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["ai_payload"] = json.loads(item.get("ai_payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["ai_payload"] = {}
            item.pop("ai_payload_json", None)
            out.append(item)
        return out

    def _screening_run_row(self, row) -> dict:
        keys = row.keys()
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
            "started_at": row["started_at"] if "started_at" in keys else None,
            "finished_at": row["finished_at"] if "finished_at" in keys else None,
            "record_kind": row["record_kind"],
            # FR-016/SC-018 守恒字段（migration_007/018 加的列，必须读出来）
            "pending_count": row["pending_count"],
            "parse_failure_count": row["parse_failure_count"],
            "parse_failures": json.loads(row["parse_failures_json"] or "{}"),
            "resume_id": row["resume_id"],
            "total_scraped": row["total_scraped"],
            "total_kept": row["total_kept"],
            "total_dropped": row["total_dropped"],
            "search_params": json.loads(row["search_params_json"] or "{}"),
            "profile_summary": row["profile_summary"],
            # FR-005/FR-037 新增字段（migration_020 加的列）
            "current_stage": row["current_stage"] if "current_stage" in keys else None,
            "error_reason": row["error_reason"] if "error_reason" in keys else None,
            "backend_version": row["backend_version"] if "backend_version" in keys else None,
            # migration 27 平台身份字段（T405: 进度/状态接口返回）
            "platform": row["platform"] if "platform" in keys else None,
            "task_input_digest": row["task_input_digest"] if "task_input_digest" in keys else None,
            "interruption_kind": row["interruption_kind"] if "interruption_kind" in keys else None,
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

    # -- SPEC011 tuning experiment persistence ---------------------------

    # Experiment state machine legal transitions (state-machine.md section 1)
    _EXPERIMENT_TERMINAL_STATES = frozenset({"cancelled", "failed", "completed"})
    _EXPERIMENT_LEGAL_TRANSITIONS = {
        "draft": {"preflight", "cancelled"},
        "preflight": {"awaiting_instruction", "blocked", "cancelled"},
        "awaiting_instruction": {"queued", "blocked", "cancelled"},
        "queued": {"running", "blocked", "cancelled"},
        "running": {"evaluating", "blocked", "failed", "cancelled"},
        "evaluating": {"awaiting_instruction", "blocked", "failed", "cancelled", "completed"},
        "blocked": {"awaiting_instruction", "cancelled"},
    }

    def create_tuning_experiment(self, *, spec_version: str, source_scope: dict) -> dict:
        """创建 draft 状态的实验记录。不启动压力工作。"""
        exp_id = _uuid()
        now = _now()
        scope_json = json.dumps(source_scope, ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_experiments "
                "(id, spec_version, status, source_scope_json, created_at, updated_at) "
                "VALUES (?, ?, 'draft', ?, ?, ?)",
                (exp_id, spec_version, scope_json, now, now),
            )
        return {"id": exp_id, "status": "draft"}

    def create_tuning_experiment_with_input(
        self, *, spec_version: str, source_scope: dict,
        workloads: list[dict], quality_context: dict,
        workspace_root: str | os.PathLike,
    ) -> dict:
        """Atomically create a draft experiment and its proposed input bundle."""
        from webui.execution_config import preview_scope

        if not isinstance(quality_context, dict):
            raise ValueError("quality_context 必须是对象")
        profile_summary = quality_context.get("profile_summary")
        screening_fields = quality_context.get("screening_fields")
        profile_ref = quality_context.get("profile_ref")
        if not isinstance(profile_summary, str) or not profile_summary.strip():
            raise ValueError("quality_context.profile_summary 不能为空")
        if not isinstance(screening_fields, dict):
            raise ValueError("quality_context.screening_fields 必须是对象")
        if not isinstance(profile_ref, str) or not profile_ref.strip():
            raise ValueError("quality_context.profile_ref 不能为空")
        normalized_quality_context = {
            "profile_summary": profile_summary.strip(),
            "screening_fields": json.loads(json.dumps(
                screening_fields, ensure_ascii=False, sort_keys=True,
            )),
            "profile_ref": profile_ref.strip(),
        }
        quality_context_bytes = json.dumps(
            normalized_quality_context, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        quality_context_digest = (
            "sha256:" + hashlib.sha256(quality_context_bytes).hexdigest()
        )

        source_result = preview_scope(
            keywords=source_scope.get("keywords"),
            scope_kind=source_scope.get("scope_kind", "cities"),
            cities=source_scope.get("cities", []),
            pages_per_combination=int(source_scope.get("pages_per_combination", 0)),
            platform=source_scope.get("platform", "boss"),
        )
        normalized_source = source_result["scope"]
        # T606: 保留 controller 冻结的 runtime 字段（preview_scope 不返回它们）
        for runtime_field in (
            "browser_account", "cdp_port", "profile_key",
            "filter_schema_version", "task_input_digest",
        ):
            if runtime_field in source_scope:
                normalized_source[runtime_field] = source_scope[runtime_field]
        normalized_workloads = []
        for raw in workloads:
            if not isinstance(raw, dict):
                raise ValueError("workload 必须是对象")
            claimed_size = raw.get("task_size")
            structure_index = raw.get("structure_index")
            if claimed_size not in ("small", "medium", "large"):
                raise ValueError("workload task_size 无效")
            if not isinstance(structure_index, int) or structure_index < 1:
                raise ValueError("workload structure_index 必须是正整数")
            workload_scope = raw.get("scope") or source_scope
            scope_result = preview_scope(
                keywords=workload_scope.get("keywords"),
                scope_kind=workload_scope.get("scope_kind", "cities"),
                cities=workload_scope.get("cities", []),
                pages_per_combination=int(
                    workload_scope.get("pages_per_combination", 0)
                ),
            )
            frozen_scope = scope_result["scope"]
            if frozen_scope["task_size"] != claimed_size:
                raise ValueError(
                    "workload task_size 与后端计算结果不一致: "
                    f"{claimed_size} != {frozen_scope['task_size']}"
                )
            normalized_workloads.append((
                claimed_size, structure_index, frozen_scope,
            ))

        exp_id = _uuid()
        input_version_id = _uuid()
        artifact_records = []
        for task_size, structure_index, frozen_scope in normalized_workloads:
            workload_id = _uuid()
            artifact_path = f"tuning/{exp_id}/input/{workload_id}.json"
            # T606: workload artifact manifest 必须保存 platform/runtime
            # 见 data-model.md 第 233-249 行
            artifact_manifest = {
                "schema_version": 1,
                "artifact_manifest_path": artifact_path,
                "experiment_id": exp_id,
                "input_version_id": input_version_id,
                "workload_id": workload_id,
                "task_size": task_size,
                "structure_index": structure_index,
                "scope": frozen_scope,
                "scope_digest": frozen_scope["scope_digest"],
                "planned_pages": frozen_scope["planned_pages"],
                "expected_raw_jobs": frozen_scope["planned_pages"] * 40,
                "quality_context": normalized_quality_context,
                "quality_context_digest": quality_context_digest,
                "platform": normalized_source.get("platform", "boss"),
                "browser_account": normalized_source.get("browser_account"),
                "cdp_port": normalized_source.get("cdp_port"),
                "profile_key": normalized_source.get("profile_key"),
                "filter_schema_version": normalized_source.get("filter_schema_version"),
                "task_input_digest": normalized_source.get("task_input_digest"),
            }
            artifact_bytes = json.dumps(
                artifact_manifest, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            artifact_records.append({
                "id": workload_id,
                "task_size": task_size,
                "structure_index": structure_index,
                "scope": frozen_scope,
                "path": artifact_path,
                "manifest": artifact_manifest,
                "bytes": artifact_bytes,
                "digest": "sha256:" + hashlib.sha256(artifact_bytes).hexdigest(),
            })

        root = Path(workspace_root).resolve()
        tuning_root = (root / "tuning").resolve()
        if root not in tuning_root.parents:
            raise ValueError("tuning artifact 根目录越过 workspace")
        experiment_root = (tuning_root / exp_id).resolve()
        if tuning_root not in experiment_root.parents:
            raise ValueError("实验 artifact 目录越过 tuning 根目录")
        expected_parent = (experiment_root / "input").resolve()
        if experiment_root.exists():
            raise ValueError("实验 artifact 目录已存在")
        written_paths: list[Path] = []
        temporary_paths: list[Path] = []
        created_artifact_tree = False
        try:
            expected_parent.mkdir(parents=True, exist_ok=False)
            created_artifact_tree = True
            for record in artifact_records:
                artifact_path = (root / record["path"]).resolve()
                if artifact_path.parent != expected_parent:
                    raise ValueError("artifact manifest 路径越过实验 input 目录")
                temporary_path = artifact_path.with_name(
                    artifact_path.name + f".{_uuid()}.tmp"
                )
                temporary_paths.append(temporary_path)
                with temporary_path.open("xb") as handle:
                    handle.write(record["bytes"])
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, artifact_path)
                temporary_paths.remove(temporary_path)
                written_paths.append(artifact_path)
                persisted = artifact_path.read_bytes()
                if persisted != record["bytes"] or (
                    "sha256:" + hashlib.sha256(persisted).hexdigest()
                    != record["digest"]
                ):
                    raise OSError("artifact manifest 原子写入后校验失败")

            now = _now()
            with self._connection() as conn:
                scope_json_str = json.dumps(
                    normalized_source, ensure_ascii=False, sort_keys=True,
                )
                conn.execute(
                    "INSERT INTO tuning_experiments "
                    "(id, spec_version, status, input_version_id, platform, "
                    " source_scope_json, created_at, updated_at) "
                    "VALUES (?, ?, 'draft', ?, json_extract(?, '$.platform'), ?, ?, ?)",
                    (exp_id, spec_version, input_version_id,
                     scope_json_str, scope_json_str, now, now),
                )
                conn.execute(
                    "INSERT INTO tuning_input_versions "
                    "(id, experiment_id, scope_json, scope_digest, "
                    " quality_context_json, quality_context_digest, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                    (input_version_id, exp_id,
                     json.dumps(normalized_source, ensure_ascii=False, sort_keys=True),
                     normalized_source["scope_digest"],
                     quality_context_bytes.decode("utf-8"),
                     quality_context_digest, now),
                )
                for record in artifact_records:
                    frozen_scope = record["scope"]
                    conn.execute(
                        "INSERT INTO tuning_workloads "
                        "(id, input_version_id, task_size, structure_index, "
                        " frozen_scope_json, planned_pages, expected_raw_jobs, "
                        " artifact_manifest_json, artifact_digest, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                        (record["id"], input_version_id, record["task_size"],
                         record["structure_index"],
                         json.dumps(frozen_scope, ensure_ascii=False, sort_keys=True),
                         frozen_scope["planned_pages"],
                         frozen_scope["planned_pages"] * 40,
                         record["bytes"].decode("utf-8"), record["digest"]),
                    )
        except Exception:
            for temporary_path in temporary_paths:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            for artifact_path in written_paths:
                try:
                    artifact_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if created_artifact_tree:
                for directory in (expected_parent, experiment_root):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            raise
        return {
            "id": exp_id, "status": "draft",
            "input_version_id": input_version_id,
        }

    def get_tuning_input_bundle(self, experiment_id: str) -> dict:
        """Return the persisted input version and ordered workload structures."""
        with self._connection() as conn:
            input_row = conn.execute(
                "SELECT * FROM tuning_input_versions WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if input_row is None:
                raise KeyError(f"实验没有输入版本: {experiment_id}")
            workload_rows = conn.execute(
                "SELECT * FROM tuning_workloads WHERE input_version_id = ? "
                "ORDER BY CASE task_size WHEN 'small' THEN 1 WHEN 'medium' THEN 2 "
                "ELSE 3 END, structure_index",
                (input_row["id"],),
            ).fetchall()
        workloads = []
        for row in workload_rows:
            artifact_manifest = _decode_json(row["artifact_manifest_json"], {})
            workloads.append({
                "id": row["id"], "task_size": row["task_size"],
                "structure_index": row["structure_index"],
                "scope": _decode_json(row["frozen_scope_json"], {}),
                "planned_pages": row["planned_pages"],
                "artifact_manifest": artifact_manifest,
                "artifact_manifest_path": artifact_manifest.get(
                    "artifact_manifest_path"
                ),
                "artifact_digest": row["artifact_digest"],
                "status": row["status"],
            })
        return {
            "input_version": {
                "id": input_row["id"],
                "experiment_id": input_row["experiment_id"],
                "scope": _decode_json(input_row["scope_json"], {}),
                "scope_digest": input_row["scope_digest"],
                "quality_context": _decode_json(
                    input_row["quality_context_json"], None
                ),
                "quality_context_digest": input_row["quality_context_digest"],
                "status": input_row["status"],
                "confirmed_at": input_row["confirmed_at"],
            },
            "workloads": workloads,
        }

    def confirm_tuning_input(
        self, experiment_id: str, *, workspace_root: str | os.PathLike,
    ) -> dict:
        """Freeze a complete two-structures-per-size input bundle atomically."""
        now = _now()
        with self._connection() as conn:
            experiment = conn.execute(
                "SELECT status, input_version_id, source_scope_json "
                "FROM tuning_experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                raise KeyError(f"实验不存在: {experiment_id}")
            if experiment["status"] != "draft":
                raise ValueError("只有 draft 实验可以确认输入")
            input_version_id = experiment["input_version_id"]
            if not input_version_id:
                raise ValueError("实验缺少输入版本")
            # T606: 读取 experiment source_scope 用于 workload artifact manifest 校验
            experiment_scope = _decode_json(
                experiment["source_scope_json"], {}
            )
            rows = conn.execute(
                "SELECT id, task_size, structure_index, frozen_scope_json, "
                "planned_pages, expected_raw_jobs, artifact_manifest_json, "
                "artifact_digest "
                "FROM tuning_workloads WHERE input_version_id = ?",
                (input_version_id,),
            ).fetchall()
            input_row = conn.execute(
                "SELECT scope_digest, quality_context_json, "
                "quality_context_digest FROM tuning_input_versions WHERE id = ?",
                (input_version_id,),
            ).fetchone()
            quality_context = _decode_json(
                input_row["quality_context_json"], None
            )
            quality_context_digest = input_row["quality_context_digest"]
            if not isinstance(quality_context, dict) or not quality_context:
                raise ValueError("冻结质量上下文缺失")
            if not isinstance(quality_context_digest, str) or not quality_context_digest:
                raise ValueError("冻结质量上下文摘要缺失")
            quality_bytes = json.dumps(
                quality_context, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if "sha256:" + hashlib.sha256(quality_bytes).hexdigest() != quality_context_digest:
                raise ValueError("冻结质量上下文摘要不匹配")
            structures = {size: set() for size in ("small", "medium", "large")}
            structure_digests = {
                size: set() for size in ("small", "medium", "large")
            }
            workload_digests = []
            workspace = Path(workspace_root).resolve()
            tuning_root = (workspace / "tuning").resolve()
            if workspace not in tuning_root.parents:
                raise ValueError("tuning artifact 根目录越过 workspace")
            expected_input_root = (
                tuning_root / experiment_id / "input"
            ).resolve()
            if tuning_root not in expected_input_root.parents:
                raise ValueError("workload artifact input 目录越界")
            for row in rows:
                structures[row["task_size"]].add(row["structure_index"])
                scope = _decode_json(row["frozen_scope_json"], {})
                digest = scope.get("scope_digest")
                workload_digests.append(digest)
                if digest:
                    structure_digests[row["task_size"]].add(digest)
                artifact_manifest = _decode_json(
                    row["artifact_manifest_json"], None
                )
                artifact_digest = row["artifact_digest"]
                if not isinstance(artifact_manifest, dict) or not artifact_manifest:
                    raise ValueError("workload artifact manifest 缺失")
                artifact_path = artifact_manifest.get("artifact_manifest_path")
                expected_relative = (
                    f"tuning/{experiment_id}/input/{row['id']}.json"
                )
                if artifact_path != expected_relative:
                    raise ValueError("workload artifact manifest 路径不匹配或越界")
                absolute_path = (workspace / artifact_path).resolve()
                if absolute_path.parent != expected_input_root:
                    raise ValueError("workload artifact manifest 路径越界")
                expected_manifest = {
                    "schema_version": 1,
                    "artifact_manifest_path": expected_relative,
                    "experiment_id": experiment_id,
                    "input_version_id": input_version_id,
                    "workload_id": row["id"],
                    "task_size": row["task_size"],
                    "structure_index": row["structure_index"],
                    "scope": scope,
                    "scope_digest": digest,
                    "planned_pages": row["planned_pages"],
                    "expected_raw_jobs": row["expected_raw_jobs"],
                    "quality_context": quality_context,
                    "quality_context_digest": quality_context_digest,
                    "platform": experiment_scope.get("platform", "boss"),
                    "browser_account": experiment_scope.get("browser_account"),
                    "cdp_port": experiment_scope.get("cdp_port"),
                    "profile_key": experiment_scope.get("profile_key"),
                    "filter_schema_version": experiment_scope.get(
                        "filter_schema_version"
                    ),
                    "task_input_digest": experiment_scope.get("task_input_digest"),
                }
                if artifact_manifest != expected_manifest:
                    raise ValueError("workload artifact manifest 身份或内容不匹配")
                if not isinstance(artifact_digest, str) or not artifact_digest:
                    raise ValueError("workload artifact digest 缺失")
                if not absolute_path.is_file():
                    raise ValueError("workload artifact 产物不存在")
                try:
                    artifact_bytes = absolute_path.read_bytes()
                    persisted_manifest = json.loads(artifact_bytes.decode("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("workload artifact 产物不可读或非 JSON") from exc
                actual_digest = (
                    "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
                )
                if actual_digest != artifact_digest:
                    raise ValueError("workload artifact digest 摘要不匹配")
                if persisted_manifest != artifact_manifest:
                    raise ValueError("workload artifact manifest 内容与数据库不一致")
            incomplete = [
                size for size, indexes in structures.items() if len(indexes) < 2
            ]
            if incomplete:
                raise ValueError(
                    "每个任务规模至少需要两种结构，缺少: " + ", ".join(incomplete)
                )
            duplicate_sizes = [
                size for size, digests in structure_digests.items()
                if len(digests) < 2
            ]
            if duplicate_sizes:
                raise ValueError(
                    "每个任务规模需要两种内容不同的结构，重复: "
                    + ", ".join(duplicate_sizes)
                )
            conn.execute(
                "UPDATE tuning_input_versions SET status = 'confirmed', "
                "confirmed_at = ? WHERE id = ?",
                (now, input_version_id),
            )
            conn.execute(
                "UPDATE tuning_experiments SET status = 'preflight', updated_at = ? "
                "WHERE id = ?",
                (now, experiment_id),
            )
        return {
            "input_version_id": input_version_id,
            "scope_digest": input_row["scope_digest"],
            "workload_digests": workload_digests,
            "status": "preflight",
        }

    def get_tuning_experiment(self, experiment_id: str) -> dict:
        """返回实验记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"实验不存在: {experiment_id}")
        return {
            "id": row["id"],
            "platform": row["platform"],
            "spec_version": row["spec_version"],
            "status": row["status"],
            "input_version_id": row["input_version_id"],
            "quality_reference_id": row["quality_reference_id"],
            "baseline_config": _decode_json(row["baseline_config_json"], None),
            "baseline_config_digest": row["baseline_config_digest"],
            "current_stage": row["current_stage"],
            "current_candidate_id": row["current_candidate_id"],
            "estimated_remaining_seconds": row["estimated_remaining_seconds"],
            "blocked_code": row["blocked_code"],
            "blocked_reason": row["blocked_reason"],
            "source_scope": _decode_json(row["source_scope_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def update_tuning_experiment_status(
        self, experiment_id: str, *, status: str,
        blocked_code: str | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        """更新实验状态，强制合法转换。"""
        current = self.get_tuning_experiment(experiment_id)
        current_status = current["status"]
        if current_status in self._EXPERIMENT_TERMINAL_STATES:
            raise ValueError(
                f"实验已处于终态 {current_status}，不能转为 {status}"
            )
        legal = self._EXPERIMENT_LEGAL_TRANSITIONS.get(current_status, set())
        if status not in legal and status != current_status:
            raise ValueError(
                f"非法状态转换: {current_status} → {status}"
            )
        if status == "completed":
            issues = self.get_tuning_completion_issues(experiment_id)
            if issues:
                raise ValueError("实验最终门禁未通过: " + "; ".join(issues))
        now = _now()
        completed_at = now if status == "completed" else None
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_experiments "
                "SET status = ?, blocked_code = ?, blocked_reason = ?, "
                "    completed_at = COALESCE(?, completed_at), updated_at = ? "
                "WHERE id = ?",
                (status, blocked_code, blocked_reason, completed_at, now, experiment_id),
            )

    def get_tuning_completion_issues(self, experiment_id: str) -> list[str]:
        """客观核验九槽版本和 2 结构 × 3 次端到端最终证据。"""
        import math
        from webui.execution_config import ExecutionConfigSnapshot

        issues: list[str] = []
        with self._connection() as conn:
            version_row = conn.execute(
                "SELECT matrix_json FROM mode_config_versions "
                "WHERE source_experiment_id = ? ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            input_row = conn.execute(
                "SELECT id, status FROM tuning_input_versions "
                "WHERE experiment_id = ? ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            candidate_rows = conn.execute(
                "SELECT id, config_digest FROM tuning_candidates WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchall()
            round_rows = conn.execute(
                "SELECT r.*, q.status AS reference_status "
                "FROM tuning_rounds r LEFT JOIN tuning_quality_references q "
                "ON q.id = r.quality_reference_id "
                "WHERE r.experiment_id = ? AND r.round_kind = 'end_to_end'",
                (experiment_id,),
            ).fetchall()
            workloads = [] if input_row is None else conn.execute(
                "SELECT id, task_size, frozen_scope_json FROM tuning_workloads "
                "WHERE input_version_id = ? ORDER BY task_size, structure_index",
                (input_row["id"],),
            ).fetchall()

        if version_row is None:
            issues.append("missing_candidate_mode_version")
            return issues
        try:
            matrix = _decode_json(version_row["matrix_json"], {})
            required_modes = ("stable", "balanced", "extreme")
            required_sizes = ("small", "medium", "large")
            slot_keys: set[tuple[str, str]] = set()
            for mode in required_modes:
                for size in required_sizes:
                    config = matrix[mode][size]
                    snapshot = ExecutionConfigSnapshot.create(config)
                    slot_keys.add((snapshot.config_digest, size))
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(f"invalid_candidate_mode_version:{exc}")
            return issues

        if input_row is None or input_row["status"] != "confirmed":
            issues.append("input_version_not_confirmed")
        workload_by_size: dict[str, list[Any]] = {
            size: [] for size in ("small", "medium", "large")
        }
        for workload in workloads:
            workload_by_size.setdefault(workload["task_size"], []).append(workload)
        for size, size_workloads in workload_by_size.items():
            digests = {
                _decode_json(row["frozen_scope_json"], {}).get("scope_digest")
                for row in size_workloads
            }
            digests.discard(None)
            if len(size_workloads) < 2 or len(digests) < 2:
                issues.append(f"insufficient_workload_structures:{size}")

        candidate_ids_by_digest: dict[str, set[str]] = {}
        for candidate in candidate_rows:
            candidate_ids_by_digest.setdefault(
                candidate["config_digest"], set(),
            ).add(candidate["id"])

        required_metrics = {
            "total_duration_ms", "work_duration_ms", "wait_duration_ms",
            "retry_duration_ms", "input_count", "terminal_count",
            "missing_count", "duplicate_count", "quality_diff_count",
        }
        for config_digest, size in sorted(slot_keys):
            candidate_ids = candidate_ids_by_digest.get(config_digest, set())
            if not candidate_ids:
                issues.append(f"missing_candidate_for_slot:{size}:{config_digest}")
                continue
            for workload in workload_by_size.get(size, []):
                matching = [
                    row for row in round_rows
                    if row["candidate_id"] in candidate_ids
                    and row["workload_id"] == workload["id"]
                    and row["status"] == "confirmed"
                ]
                repetitions = {row["repetition_index"] for row in matching}
                if len(repetitions) < 3:
                    issues.append(
                        f"insufficient_confirmed_repetitions:{size}:{workload['id']}"
                    )
                    continue
                for row in matching:
                    metrics = _decode_json(row["metrics_json"], {})
                    if not required_metrics.issubset(metrics):
                        issues.append(f"missing_round_metrics:{row['id']}")
                        continue
                    numeric = [metrics[key] for key in required_metrics]
                    if any(
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value < 0
                        for value in numeric
                    ):
                        issues.append(f"invalid_round_metrics:{row['id']}")
                        continue
                    if (
                        metrics["terminal_count"] != metrics["input_count"]
                        or metrics["missing_count"] != 0
                        or metrics["duplicate_count"] != 0
                    ):
                        issues.append(f"terminal_conservation_failed:{row['id']}")
                    accounted = (
                        metrics["work_duration_ms"] + metrics["wait_duration_ms"]
                        + metrics["retry_duration_ms"]
                    )
                    if metrics["total_duration_ms"] != accounted:
                        issues.append(f"duration_not_accounted:{row['id']}")
                    if metrics["quality_diff_count"] != 0:
                        issues.append(f"quality_gate_failed:{row['id']}")
                    if row["quality_reference_id"] is None or row["reference_status"] != "confirmed":
                        issues.append(f"quality_reference_not_confirmed:{row['id']}")
        return issues

    def save_tuning_candidate(
        self, *, experiment_id: str, stage: str, strategy_step: str,
        config: dict, parent_candidate_id: str | None = None,
        pressure_rank: int = 0,
    ) -> dict:
        """保存候选配置到实验表（不写入 advanced_config_state）。"""
        from webui.execution_config import ExecutionConfigSnapshot
        snapshot = ExecutionConfigSnapshot.create(config)
        candidate_id = _uuid()
        now = _now()
        config_json = json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_candidates "
                "(id, experiment_id, stage, strategy_step, parent_candidate_id, "
                " config_json, config_digest, status, pressure_rank, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)",
                (candidate_id, experiment_id, stage, strategy_step,
                 parent_candidate_id, config_json, snapshot.config_digest,
                 pressure_rank, now, now),
            )
        return {"id": candidate_id, "config_digest": snapshot.config_digest}

    def get_tuning_candidate(self, candidate_id: str) -> dict:
        """返回候选记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"候选不存在: {candidate_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "stage": row["stage"],
            "strategy_step": row["strategy_step"],
            "parent_candidate_id": row["parent_candidate_id"],
            "config": _decode_json(row["config_json"], {}),
            "config_digest": row["config_digest"],
            "status": row["status"],
            "pressure_rank": row["pressure_rank"],
            "promotion_reason": _decode_json(row["promotion_reason"], None),
            "rejection_code": row["rejection_code"],
        }

    def create_tuning_round(
        self, *, experiment_id: str, candidate_id: str, workload_id: str,
        round_kind: str, repetition_index: int,
        quality_reference_id: str | None = None,
    ) -> dict:
        """创建 planned 状态的轮次。"""
        round_id = _uuid()
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_rounds "
                "(id, experiment_id, candidate_id, workload_id, quality_reference_id, "
                " round_kind, repetition_index, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)",
                (round_id, experiment_id, candidate_id, workload_id,
                 quality_reference_id, round_kind, repetition_index, now),
            )
        return {"id": round_id, "status": "planned"}

    def get_tuning_round(self, round_id: str) -> dict:
        """返回轮次记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_rounds WHERE id = ?", (round_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"轮次不存在: {round_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "candidate_id": row["candidate_id"],
            "workload_id": row["workload_id"],
            "round_kind": row["round_kind"],
            "repetition_index": row["repetition_index"],
            "status": row["status"],
            "manifest_id": row["manifest_id"],
            "metrics": _decode_json(row["metrics_json"], None),
            "failure_code": row["failure_code"],
        }

    def update_tuning_round_status(
        self, round_id: str, *, status: str,
        failure_code: str | None = None,
    ) -> None:
        """更新轮次状态，强制 state-machine.md 的合法转换。"""
        current = self.get_tuning_round(round_id)
        current_status = current["status"]
        legal_transitions = {
            "planned": {"issued", "cancelled"},
            "issued": {"running", "uncertain", "blocked", "cancelled"},
            "running": {"reported", "uncertain", "blocked", "cancelled"},
            "reported": {"confirmed", "invalid", "blocked"},
        }
        if status != current_status and status not in legal_transitions.get(
            current_status, set()
        ):
            raise ValueError(
                f"非法轮次状态转换: {current_status} → {status}"
            )
        if status == "running":
            lease = self.get_tuning_lease()
            if (
                lease.get("owner_experiment_id") != current["experiment_id"]
                or lease.get("owner_round_id") != round_id
            ):
                raise ValueError("轮次未持有独占租约，不能进入 running")
        now = _now()
        finished_at = now if status in ("confirmed", "invalid", "blocked", "cancelled") else None
        confirmed_at = now if status == "confirmed" else None
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_rounds "
                "SET status = ?, failure_code = COALESCE(?, failure_code), "
                "    finished_at = COALESCE(?, finished_at), "
                "    confirmed_at = COALESCE(?, confirmed_at) "
                "WHERE id = ?",
                (status, failure_code, finished_at, confirmed_at, round_id),
            )

    def save_tuning_stage_artifact(
        self, *, round_id: str, stage: str, payload: dict,
        workspace_root: str | os.PathLike,
        source_artifact_id: str | None = None,
    ) -> dict:
        """Append one immutable, digest-verified stage result for a round."""
        if not isinstance(payload, dict):
            raise ValueError("阶段产物 payload 必须是对象")
        round_record = self.get_tuning_round(round_id)
        if round_record["status"] not in ("running", "reported"):
            raise ValueError("只有 running/reported 轮次可以保存阶段产物")
        if stage != round_record["round_kind"]:
            raise ValueError("阶段产物类型与轮次类型不一致")
        if stage not in ("list", "detail", "rough", "fine", "end_to_end"):
            raise ValueError("阶段产物类型无效")

        with self._connection() as conn:
            workload = conn.execute(
                "SELECT input_version_id FROM tuning_workloads WHERE id = ?",
                (round_record["workload_id"],),
            ).fetchone()
            if workload is None:
                raise KeyError("轮次 workload 不存在")
            # T607: 从 experiment 读取 platform/scope_digest/task_input_digest
            # 写入 stage artifact 外层列
            experiment_row = conn.execute(
                "SELECT platform, source_scope_json FROM tuning_experiments "
                "WHERE id = ?",
                (round_record["experiment_id"],),
            ).fetchone()
            artifact_platform = experiment_row["platform"] if experiment_row else None
            source_scope_json = (
                experiment_row["source_scope_json"] if experiment_row else None
            )
            source_scope = _decode_json(source_scope_json, {})
            scope_digest = source_scope.get("scope_digest")
            task_input_digest = source_scope.get("task_input_digest")
            if source_artifact_id is not None:
                source = conn.execute(
                    "SELECT experiment_id, input_version_id, workload_id, "
                    "platform, status "
                    "FROM tuning_stage_artifacts WHERE id = ?",
                    (source_artifact_id,),
                ).fetchone()
                if source is None:
                    raise ValueError("上游阶段产物不存在")
                if (
                    source["experiment_id"] != round_record["experiment_id"]
                    or source["input_version_id"] != workload["input_version_id"]
                    or source["workload_id"] != round_record["workload_id"]
                    or source["status"] != "ready"
                ):
                    raise ValueError("上游阶段产物身份不匹配")
                # T611: detail 只接受同平台 list artifact
                if source["platform"] != artifact_platform:
                    raise ValueError(
                        f"上游阶段产物平台 {source['platform']!r} 与当前实验 "
                        f"平台 {artifact_platform!r} 不一致"
                    )

        artifact_id = _uuid()
        relative_path = (
            f"tuning/{round_record['experiment_id']}/artifacts/"
            f"{round_id}/{stage}-{artifact_id}.json"
        )
        artifact_bytes = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        artifact_digest = (
            "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
        )
        workspace = Path(workspace_root).resolve()
        experiment_root = (
            workspace / "tuning" / round_record["experiment_id"]
        ).resolve()
        absolute_path = (workspace / relative_path).resolve()
        if experiment_root not in absolute_path.parents:
            raise ValueError("阶段产物路径越过实验根目录")
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = absolute_path.with_name(
            absolute_path.name + f".{_uuid()}.tmp"
        )
        try:
            with temporary_path.open("xb") as handle:
                handle.write(artifact_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, absolute_path)
            persisted = absolute_path.read_bytes()
            if persisted != artifact_bytes:
                raise OSError("阶段产物原子写入后内容不一致")
            jobs = payload.get("jobs")
            verdicts = payload.get("verdicts")
            item_count = (
                len(jobs) if isinstance(jobs, list)
                else len(verdicts) if isinstance(verdicts, dict)
                else 0
            )
            with self._connection() as conn:
                # T609: source_artifact_kind 只有 list/detail 可复用
                source_artifact_kind = stage if stage in ("list", "detail") else None
                conn.execute(
                    "INSERT INTO tuning_stage_artifacts "
                    "(id, experiment_id, input_version_id, workload_id, "
                    " producer_round_id, stage, platform, source_artifact_kind, "
                    " scope_digest, task_input_digest, source_artifact_id, "
                    " artifact_path, artifact_digest, item_count, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)",
                    (artifact_id, round_record["experiment_id"],
                     workload["input_version_id"], round_record["workload_id"],
                     round_id, stage, artifact_platform, source_artifact_kind,
                     scope_digest, task_input_digest, source_artifact_id,
                     relative_path, artifact_digest, item_count, _now()),
                )
        except sqlite3.IntegrityError as exc:
            temporary_path.unlink(missing_ok=True)
            absolute_path.unlink(missing_ok=True)
            raise ValueError("同一轮次的阶段产物已经存在") from exc
        except Exception:
            temporary_path.unlink(missing_ok=True)
            absolute_path.unlink(missing_ok=True)
            raise
        return self.get_tuning_stage_artifact(artifact_id)

    def get_tuning_stage_artifact(self, artifact_id: str) -> dict:
        """Return one immutable stage artifact record."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_stage_artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"阶段产物不存在: {artifact_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "input_version_id": row["input_version_id"],
            "workload_id": row["workload_id"],
            "producer_round_id": row["producer_round_id"],
            "stage": row["stage"],
            "platform": row["platform"],
            "source_artifact_kind": row["source_artifact_kind"],
            "scope_digest": row["scope_digest"],
            "task_input_digest": row["task_input_digest"],
            "source_artifact_id": row["source_artifact_id"],
            "artifact_path": row["artifact_path"],
            "artifact_digest": row["artifact_digest"],
            "item_count": row["item_count"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    # -- SPEC011 tuning execution lease ----------------------------------

    _LEASE_TTL_SECONDS = 300  # 5 分钟心跳超时

    def claim_tuning_lease(
        self, *, experiment_id: str, round_id: str, owner_token: str,
        allow_stale_takeover: bool = False,
    ) -> dict:
        """原子 claim 独占租约。"""
        import hashlib as _hashlib
        from datetime import datetime, timezone, timedelta
        token_digest = _hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=self._LEASE_TTL_SECONDS)).isoformat()
        now_iso = now.isoformat()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT owner_token_digest, lease_until FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            is_free = row["owner_token_digest"] is None
            is_stale = False
            if not is_free and row["lease_until"]:
                try:
                    lease_time = datetime.fromisoformat(
                        row["lease_until"].replace("Z", "+00:00")
                    )
                    is_stale = lease_time < now
                except (ValueError, TypeError):
                    is_stale = True
            if is_free or (is_stale and allow_stale_takeover):
                conn.execute(
                    "UPDATE tuning_execution_lease "
                    "SET owner_experiment_id = ?, owner_round_id = ?, "
                    "    owner_token_digest = ?, lease_until = ?, "
                    "    heartbeat_at = ?, updated_at = ? "
                    "WHERE id = 1",
                    (experiment_id, round_id, token_digest,
                     lease_until, now_iso, now_iso),
                )
                return {"ok": True, "experiment_id": experiment_id, "round_id": round_id}
            return {"ok": False, "reason": "lease_held"}

    def release_tuning_lease(self, *, owner_token: str) -> None:
        """释放租约（仅持有者可释放）。"""
        import hashlib as _hashlib
        token_digest = _hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = _now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT owner_token_digest FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if row and row["owner_token_digest"] == token_digest:
                conn.execute(
                    "UPDATE tuning_execution_lease "
                    "SET owner_experiment_id = NULL, owner_round_id = NULL, "
                    "    owner_token_digest = NULL, lease_until = NULL, "
                    "    heartbeat_at = NULL, updated_at = ? "
                    "WHERE id = 1",
                    (now,),
                )

    def heartbeat_tuning_lease(self, *, owner_token: str) -> None:
        """延长租约心跳。"""
        import hashlib as _hashlib
        from datetime import datetime, timezone, timedelta
        token_digest = _hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=self._LEASE_TTL_SECONDS)).isoformat()
        now_iso = now.isoformat()
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_execution_lease "
                "SET heartbeat_at = ?, lease_until = ?, updated_at = ? "
                "WHERE id = 1 AND owner_token_digest = ?",
                (now_iso, lease_until, now_iso, token_digest),
            )

    def get_tuning_lease(self) -> dict:
        """返回当前租约状态。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"owner_experiment_id": None, "owner_round_id": None}
        return {
            "owner_experiment_id": row["owner_experiment_id"],
            "owner_round_id": row["owner_round_id"],
            "lease_until": row["lease_until"],
            "heartbeat_at": row["heartbeat_at"],
        }

    def reconcile_tuning_after_restart(self) -> None:
        """原子恢复中断轮次、阻断所属实验，然后释放旧进程租约。

        不修改 advanced_config_state（FR-042/SC-014）。
        """
        now = _now()
        with self._connection() as conn:
            interrupted = conn.execute(
                "SELECT id, experiment_id FROM tuning_rounds "
                "WHERE status IN ('running', 'issued', 'reported') "
                "ORDER BY created_at, id"
            ).fetchall()
            by_experiment: dict[str, list[str]] = {}
            for row in interrupted:
                by_experiment.setdefault(row["experiment_id"], []).append(row["id"])
            conn.execute(
                "UPDATE tuning_rounds SET status = 'uncertain', "
                "failure_code = COALESCE(failure_code, 'restart_interrupted_round'), "
                "finished_at = COALESCE(finished_at, ?) "
                "WHERE status IN ('running', 'issued', 'reported')",
                (now,),
            )
            for experiment_id, round_ids in by_experiment.items():
                reason = "重启中断了未原子确认的轮次: " + ", ".join(round_ids)
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'blocked', "
                    "blocked_code = 'restart_interrupted_round', "
                    "blocked_reason = ?, updated_at = ? "
                    "WHERE id = ? AND status IN ('queued', 'running')",
                    (reason, now, experiment_id),
                )
            # 状态对账完成后才释放租约；新进程必须重新签发轮次。
            conn.execute(
                "UPDATE tuning_execution_lease "
                "SET owner_experiment_id = NULL, owner_round_id = NULL, "
                "    owner_token_digest = NULL, lease_until = NULL, "
                "    heartbeat_at = NULL, updated_at = ? "
                "WHERE id = 1",
                (now,),
            )

    # -- 测量事件持久化 (data-model.md 2.9) ---------------------------

    # 敏感字段黑名单：这些键名或值内容不得进入测量事件
    _MEASUREMENT_FORBIDDEN_KEYS = frozenset({
        "api_key", "apikey", "secret", "token", "password", "credential",
        "resume_text", "resume", "jd_body", "jd", "model_response",
        "raw_response", "authorization",
    })

    def _validate_measurement_payload(self, payload: dict) -> None:
        """校验测量事件 payload 不包含敏感字段。"""
        if not isinstance(payload, dict):
            return
        for key in payload:
            key_lower = str(key).lower()
            if key_lower in self._MEASUREMENT_FORBIDDEN_KEYS:
                raise ValueError(f"测量事件禁止包含敏感字段: {key}")

    def save_tuning_measurement_event(
        self, *, round_id: str, event_type: str, stage: str,
        duration_ms: int, started_monotonic_ms: int | None = None,
        counts: dict | None = None, error_code: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """持久化一条测量事件。

        data-model.md 2.9: 禁止凭据、原始简历、原始模型响应和 JD 正文。
        """
        # 校验敏感字段
        if counts:
            self._validate_measurement_payload(counts)
        if metadata:
            self._validate_measurement_payload(metadata)
        if duration_ms < 0:
            raise ValueError("duration_ms 必须非负")
        if started_monotonic_ms is not None and started_monotonic_ms < 0:
            raise ValueError("started_monotonic_ms 必须非负")

        now = _now()
        counts_json = json.dumps(counts, ensure_ascii=False, sort_keys=True) if counts else None
        metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else None

        with self._connection() as conn:
            # 先取得 SQLite 写锁，使 MAX(seq)+INSERT 成为一个不可交错的分配事务。
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                "FROM tuning_measurement_events WHERE round_id = ?",
                (round_id,),
            ).fetchone()
            next_seq = row["next_seq"]
            if started_monotonic_ms is None:
                started_monotonic_ms = 0
            conn.execute(
                "INSERT INTO tuning_measurement_events "
                "(round_id, seq, event_type, stage, started_monotonic_ms, "
                " duration_ms, counts_json, error_code, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (round_id, next_seq, event_type, stage, started_monotonic_ms,
                 duration_ms, counts_json, error_code, metadata_json, now),
            )
        return {
            "round_id": round_id, "seq": next_seq, "event_type": event_type,
            "stage": stage, "started_monotonic_ms": started_monotonic_ms,
            "duration_ms": duration_ms, "counts": counts,
            "error_code": error_code, "metadata": metadata,
        }

    def list_tuning_measurement_events(self, round_id: str) -> list[dict]:
        """列出某轮次的全部测量事件，按 seq 升序。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tuning_measurement_events "
                "WHERE round_id = ? ORDER BY seq ASC",
                (round_id,),
            ).fetchall()
        result = []
        for row in rows:
            counts = json.loads(row["counts_json"]) if row["counts_json"] else None
            metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else None
            result.append({
                "round_id": row["round_id"], "seq": row["seq"],
                "event_type": row["event_type"], "stage": row["stage"],
                "started_monotonic_ms": row["started_monotonic_ms"],
                "duration_ms": row["duration_ms"],
                "counts": counts, "error_code": row["error_code"],
                "metadata": metadata, "created_at": row["created_at"],
            })
        return result

    # -- T020: 质量参考 CRUD (data-model.md 2.4) ------------------------

    def save_quality_reference(
        self, *, experiment_id: str, input_version_id: str,
        item_results: dict, variation_summary: dict,
        reference_digest: str,
    ) -> dict:
        """创建一条 building 状态的质量参考记录。"""
        ref_id = _uuid()
        now = _now()
        item_results_json = json.dumps(item_results, ensure_ascii=False, sort_keys=True)
        variation_json = json.dumps(variation_summary, ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_quality_references "
                "(id, experiment_id, input_version_id, status, "
                " item_results_json, variation_summary_json, reviewed_item_ids_json, "
                " reference_digest, created_at, confirmed_at) "
                "VALUES (?, ?, ?, 'building', ?, ?, NULL, ?, ?, NULL)",
                (ref_id, experiment_id, input_version_id,
                 item_results_json, variation_json,
                 reference_digest, now),
            )
        return {
            "id": ref_id, "experiment_id": experiment_id,
            "input_version_id": input_version_id, "status": "building",
            "item_results": item_results,
            "variation_summary": variation_summary,
            "reviewed_item_ids": [],
            "reference_digest": reference_digest,
            "created_at": now, "confirmed_at": None,
        }

    def get_quality_reference(self, reference_id: str) -> dict:
        """返回质量参考记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_quality_references WHERE id = ?",
                (reference_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"质量参考不存在: {reference_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "input_version_id": row["input_version_id"],
            "status": row["status"],
            "item_results": _decode_json(row["item_results_json"], {"items": []}),
            "variation_summary": _decode_json(row["variation_summary_json"], {}),
            "reviewed_item_ids": _decode_json(row["reviewed_item_ids_json"], []),
            "reference_digest": row["reference_digest"],
            "created_at": row["created_at"],
            "confirmed_at": row["confirmed_at"],
        }

    def list_quality_references(self, experiment_id: str) -> list[dict]:
        """列出实验的全部质量参考，按创建时间降序。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tuning_quality_references "
                "WHERE experiment_id = ? ORDER BY created_at DESC",
                (experiment_id,),
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "experiment_id": row["experiment_id"],
                "input_version_id": row["input_version_id"],
                "status": row["status"],
                "item_results": _decode_json(row["item_results_json"], {"items": []}),
                "variation_summary": _decode_json(row["variation_summary_json"], {}),
                "reviewed_item_ids": _decode_json(row["reviewed_item_ids_json"], []),
                "reference_digest": row["reference_digest"],
                "created_at": row["created_at"],
                "confirmed_at": row["confirmed_at"],
            })
        return result

    def update_quality_reference_status(
        self, reference_id: str, *, status: str,
        reviewed_item_ids: list | None = None,
    ) -> dict:
        """更新质量参考状态。

        合法状态：building → confirmed → review_required → confirmed
                         或 confirmed → superseded
        """
        valid_statuses = {"building", "confirmed", "review_required", "superseded"}
        if status not in valid_statuses:
            raise ValueError(f"非法质量参考状态: {status}")
        now = _now()
        confirmed_at = now if status == "confirmed" else None
        reviewed_json = None
        if reviewed_item_ids is not None:
            reviewed_json = json.dumps(reviewed_item_ids, ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            if reviewed_json is not None:
                conn.execute(
                    "UPDATE tuning_quality_references "
                    "SET status = ?, reviewed_item_ids_json = ?, "
                    "    confirmed_at = COALESCE(?, confirmed_at) "
                    "WHERE id = ?",
                    (status, reviewed_json, confirmed_at, reference_id),
                )
            else:
                conn.execute(
                    "UPDATE tuning_quality_references "
                    "SET status = ?, confirmed_at = COALESCE(?, confirmed_at) "
                    "WHERE id = ?",
                    (status, confirmed_at, reference_id),
                )
        return self.get_quality_reference(reference_id)

    def supersede_quality_references(
        self, experiment_id: str, except_id: str,
    ) -> None:
        """将实验的全部 confirmed/review_required 参考标记为 superseded，
        保留 except_id 不变。"""
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_quality_references "
                "SET status = 'superseded' "
                "WHERE experiment_id = ? AND id != ? "
                "  AND status IN ('confirmed', 'review_required', 'building')",
                (experiment_id, except_id),
            )

    def set_experiment_quality_reference(
        self, experiment_id: str, reference_id: str,
    ) -> None:
        """设置实验的活动质量参考。"""
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_experiments "
                "SET quality_reference_id = ?, updated_at = ? WHERE id = ?",
                (reference_id, now, experiment_id),
            )

    # -- T022: 任务单与报告持久化 (data-model.md 2.7/2.8) ----------------

    def save_task_manifest(
        self, *, experiment_id: str, candidate_id: str, round_id: str,
        manifest_version: int, manifest_json: str, manifest_digest: str,
        rendered_task_path: str,
    ) -> dict:
        """持久化一份已签发的任务单。"""
        manifest_id = _uuid()
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_task_manifests "
                "(id, experiment_id, candidate_id, round_id, manifest_version, "
                " manifest_json, manifest_digest, rendered_task_path, "
                " status, issued_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'issued', ?, ?)",
                (manifest_id, experiment_id, candidate_id, round_id,
                 manifest_version, manifest_json, manifest_digest,
                 rendered_task_path, now, now),
            )
        return {
            "manifest_id": manifest_id,
            "manifest_digest": manifest_digest,
            "rendered_task_path": rendered_task_path,
            "status": "issued",
        }

    def issue_task_manifest_atomic(
        self, *, experiment_id: str, candidate_id: str, round_id: str,
        manifest_version: int, manifest_json: str, manifest_digest: str,
        rendered_task_path: str, owner_token: str,
    ) -> dict:
        """在一个 IMMEDIATE 事务中 claim 租约并签发唯一任务单。"""
        token_digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        manifest_id = _uuid()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_until = (now_dt + timedelta(seconds=self._LEASE_TTL_SECONDS)).isoformat()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            experiment = conn.execute(
                "SELECT status FROM tuning_experiments WHERE id = ?", (experiment_id,),
            ).fetchone()
            round_row = conn.execute(
                "SELECT status, experiment_id, candidate_id FROM tuning_rounds WHERE id = ?",
                (round_id,),
            ).fetchone()
            lease = conn.execute(
                "SELECT owner_token_digest FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if experiment is None or experiment["status"] != "awaiting_instruction":
                raise ValueError("实验状态已变化，任务单签发中止")
            if (
                round_row is None or round_row["status"] != "planned"
                or round_row["experiment_id"] != experiment_id
                or round_row["candidate_id"] != candidate_id
            ):
                raise ValueError("轮次状态或归属已变化，任务单签发中止")
            if lease is None or lease["owner_token_digest"] is not None:
                raise ValueError("独占执行租约被占用，不能签发任务单")
            conn.execute(
                "UPDATE tuning_execution_lease SET owner_experiment_id = ?, "
                "owner_round_id = ?, owner_token_digest = ?, lease_until = ?, "
                "heartbeat_at = ?, updated_at = ? WHERE id = 1",
                (experiment_id, round_id, token_digest, lease_until, now, now),
            )
            conn.execute(
                "INSERT INTO tuning_task_manifests "
                "(id, experiment_id, candidate_id, round_id, manifest_version, "
                "platform, manifest_json, manifest_digest, rendered_task_path, "
                "status, issued_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, json_extract(?, '$.fixed_fields.platform'), "
                "?, ?, ?, 'issued', ?, ?)",
                (manifest_id, experiment_id, candidate_id, round_id,
                 manifest_version, manifest_json, manifest_json,
                 manifest_digest, rendered_task_path, now, now),
            )
            conn.execute(
                "UPDATE tuning_rounds SET status = 'issued', manifest_id = ? WHERE id = ?",
                (manifest_id, round_id),
            )
            conn.execute(
                "UPDATE tuning_experiments SET status = 'queued', updated_at = ? WHERE id = ?",
                (now, experiment_id),
            )
        return {
            "manifest_id": manifest_id, "manifest_digest": manifest_digest,
            "rendered_task_path": rendered_task_path, "status": "issued",
        }

    def get_task_manifest(self, manifest_id: str) -> dict:
        """返回任务单记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_task_manifests WHERE id = ?",
                (manifest_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"任务单不存在: {manifest_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "candidate_id": row["candidate_id"],
            "round_id": row["round_id"],
            "manifest_version": row["manifest_version"],
            "platform": row["platform"],
            "manifest": _decode_json(row["manifest_json"], {}),
            "manifest_digest": row["manifest_digest"],
            "rendered_task_path": row["rendered_task_path"],
            "status": row["status"],
            "issued_at": row["issued_at"],
            "updated_at": row["updated_at"],
        }

    def update_task_manifest_status(
        self, manifest_id: str, *, status: str,
    ) -> None:
        """更新任务单状态。"""
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_task_manifests "
                "SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, manifest_id),
            )

    def start_task_manifest_atomic(self, manifest_id: str, *, owner_token: str) -> dict:
        """核对签发租约并原子推进 queued/issued → running。"""
        token_digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            manifest = conn.execute(
                "SELECT * FROM tuning_task_manifests WHERE id = ?", (manifest_id,),
            ).fetchone()
            if manifest is None:
                raise KeyError(f"任务单不存在: {manifest_id}")
            round_row = conn.execute(
                "SELECT status FROM tuning_rounds WHERE id = ?", (manifest["round_id"],),
            ).fetchone()
            experiment = conn.execute(
                "SELECT status FROM tuning_experiments WHERE id = ?",
                (manifest["experiment_id"],),
            ).fetchone()
            lease = conn.execute(
                "SELECT owner_token_digest, owner_experiment_id, owner_round_id "
                "FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if manifest["status"] != "issued":
                raise ValueError("任务单不是 issued 状态")
            if round_row is None or round_row["status"] != "issued":
                raise ValueError("轮次不是 issued 状态")
            if experiment is None or experiment["status"] != "queued":
                raise ValueError("实验不是 queued 状态")
            if (
                lease is None or lease["owner_token_digest"] != token_digest
                or lease["owner_experiment_id"] != manifest["experiment_id"]
                or lease["owner_round_id"] != manifest["round_id"]
            ):
                raise ValueError("任务单未持有匹配的应用租约")
            conn.execute(
                "UPDATE tuning_rounds SET status = 'running', started_at = ? WHERE id = ?",
                (now, manifest["round_id"]),
            )
            conn.execute(
                "UPDATE tuning_experiments SET status = 'running', updated_at = ? WHERE id = ?",
                (now, manifest["experiment_id"]),
            )
            conn.execute(
                "UPDATE tuning_task_manifests SET status = 'running', updated_at = ? WHERE id = ?",
                (now, manifest_id),
            )
        return {
            "manifest_id": manifest_id, "round_id": manifest["round_id"],
            "experiment_id": manifest["experiment_id"], "status": "running",
        }

    def save_executor_report(
        self, *, manifest_id: str, report_version: int,
        report_json: str, reported_manifest_digest: str,
        evidence_digest: str, validation_status: str,
        validation_errors: list | None = None,
    ) -> dict:
        """持久化一份执行者报告。"""
        report_id = _uuid()
        now = _now()
        errors_json = json.dumps(
            validation_errors or [], ensure_ascii=False, sort_keys=True,
        )
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_executor_reports "
                "(id, manifest_id, report_version, report_json, "
                " reported_manifest_digest, evidence_digest, "
                " validation_status, validation_errors_json, "
                " created_at, validated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, manifest_id, report_version, report_json,
                 reported_manifest_digest, evidence_digest,
                 validation_status, errors_json, now, now),
            )
        return {
            "report_id": report_id,
            "manifest_id": manifest_id,
            "validation_status": validation_status,
        }

    def save_executor_report_atomic(
        self, *, manifest_id: str, report_version: int, report_json: str,
        reported_manifest_digest: str, evidence_digest: str,
        validation_status: str, validation_errors: list | None,
        report_status: str | None, owner_token: str,
    ) -> dict:
        """原子保存报告、推进轮次/实验并释放应用持有的租约。"""
        report_id = _uuid()
        now = _now()
        token_digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        errors_json = json.dumps(
            validation_errors or [], ensure_ascii=False, sort_keys=True,
        )
        parsed = _decode_json(report_json, {})
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            manifest = conn.execute(
                "SELECT * FROM tuning_task_manifests WHERE id = ?", (manifest_id,),
            ).fetchone()
            if manifest is None:
                raise KeyError(f"任务单不存在: {manifest_id}")
            round_row = conn.execute(
                "SELECT status FROM tuning_rounds WHERE id = ?", (manifest["round_id"],),
            ).fetchone()
            experiment = conn.execute(
                "SELECT status FROM tuning_experiments WHERE id = ?",
                (manifest["experiment_id"],),
            ).fetchone()
            lease = conn.execute(
                "SELECT owner_token_digest, owner_round_id FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if (
                round_row is None or experiment is None or lease is None
                or lease["owner_token_digest"] != token_digest
                or lease["owner_round_id"] != manifest["round_id"]
            ):
                raise ValueError("报告接收时租约或状态归属不一致")
            if round_row["status"] not in ("running", "reported"):
                raise ValueError(f"轮次状态 {round_row['status']} 不能接收报告")
            conn.execute(
                "INSERT INTO tuning_executor_reports "
                "(id, manifest_id, report_version, report_json, "
                "reported_manifest_digest, evidence_digest, validation_status, "
                "validation_errors_json, created_at, validated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, manifest_id, report_version, report_json,
                 reported_manifest_digest, evidence_digest, validation_status,
                 errors_json, now, now),
            )
            if validation_status == "accepted" and report_status == "completed":
                if experiment["status"] != "running":
                    raise ValueError("实验不在 running，不能确认完成报告")
                conn.execute(
                    "UPDATE tuning_rounds SET status = 'confirmed', metrics_json = ?, "
                    "evidence_manifest_json = ?, finished_at = ?, confirmed_at = ? WHERE id = ?",
                    (json.dumps(parsed.get("program_evidence", {}), ensure_ascii=False,
                                sort_keys=True),
                     json.dumps({"artifacts": parsed.get("artifacts", [])},
                                ensure_ascii=False, sort_keys=True),
                     now, now, manifest["round_id"]),
                )
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'evaluating', updated_at = ? WHERE id = ?",
                    (now, manifest["experiment_id"]),
                )
                final_round_status, final_experiment_status = "confirmed", "evaluating"
            elif validation_status == "accepted" and report_status == "blocked":
                conn.execute(
                    "UPDATE tuning_rounds SET status = 'blocked', failure_code = ?, "
                    "metrics_json = ?, evidence_manifest_json = ?, finished_at = ? WHERE id = ?",
                    (parsed.get("stop_reason") or "executor_blocked",
                     json.dumps(parsed.get("program_evidence", {}), ensure_ascii=False,
                                sort_keys=True),
                     json.dumps({"artifacts": parsed.get("artifacts", [])},
                                ensure_ascii=False, sort_keys=True),
                     now, manifest["round_id"]),
                )
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'blocked', blocked_code = ?, "
                    "blocked_reason = ?, updated_at = ? WHERE id = ?",
                    (parsed.get("stop_reason") or "executor_blocked",
                     "执行者按任务单停止条件阻断", now, manifest["experiment_id"]),
                )
                final_round_status, final_experiment_status = "blocked", "blocked"
            else:
                conn.execute(
                    "UPDATE tuning_rounds SET status = 'invalid', "
                    "failure_code = 'report_validation_failed', finished_at = ? WHERE id = ?",
                    (now, manifest["round_id"]),
                )
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'blocked', "
                    "blocked_code = 'report_validation_failed', blocked_reason = ?, "
                    "updated_at = ? WHERE id = ?",
                    ("; ".join(validation_errors or []), now, manifest["experiment_id"]),
                )
                final_round_status, final_experiment_status = "invalid", "blocked"
            conn.execute(
                "UPDATE tuning_task_manifests SET status = ?, updated_at = ? WHERE id = ?",
                ("reported" if validation_status == "accepted" else "rejected", now, manifest_id),
            )
            conn.execute(
                "UPDATE tuning_execution_lease SET owner_experiment_id = NULL, "
                "owner_round_id = NULL, owner_token_digest = NULL, lease_until = NULL, "
                "heartbeat_at = NULL, updated_at = ? WHERE id = 1",
                (now,),
            )
        return {
            "report_id": report_id, "manifest_id": manifest_id,
            "validation_status": validation_status,
            "round_status": final_round_status,
            "experiment_status": final_experiment_status,
        }
