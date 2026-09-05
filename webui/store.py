"""SQLite persistence for the AI job workbench（021 B2 起为域 mixin 组装门面）。

TaskStore = 连接/迁移引导核心 + 既有 mixin（result history / scrape only /
migrations / screen resume）+ 021 B2 拆出的域 mixin：
  store_config / store_recovery / store_pipeline_results / store_jobs /
  store_tasks / store_profiles / store_runs / store_scrape_runs /
  store_job_catalog / store_tuning_experiments / store_tuning_rounds /
  store_tuning_reports
共享常量见 store_constants；旧 ``from webui.store import X`` 路径不变。
除拆分批次外不得在此追加逻辑（宪法 VI）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from webui.constants import CLEANUP_EXPIRED_DAYS
from webui.store_helpers import (
    latest_screening_run_for_source,
    _CST,
    _build_pipeline_result_rows,
    _decode_json,
    _now,
    _to_iso_timestamp,
    _uuid,
)
from webui.error_registry import INDEPENDENT_FAILURE_CODES, SYSTEMIC_BLOCK_CODES
from webui.store_constants import (  # noqa: F401 — 门面 re-export，保持旧 import 路径
    ACTIVE_STATUSES,
    AI_STATUS_VALUES,
    ALLOWED_TRANSITIONS,
    DiscoveryStoreConflictError,
    FEEDBACK_ACTIONS,
    FEEDBACK_REASONS,
    MAX_DETAIL_BUDGET,
    PROFILE_JOB_STATUSES,
    QUERY_STATUSES,
    RESUME_FORMATS,
    RUN_STATUSES,
    RUN_TRANSITIONS,
    TASK_STATUSES,
    TASK_TO_RUN_STATUS,
    TERMINAL_STATUSES,
    _BEGIN_IMMEDIATE,
    _ERROR_CODE_SET_CLAUSE,
    _LATEST_RESULT_FILTER,
    _LATEST_RESULT_VISIBLE_STATUSES,
    _SHA256_PREFIX,
    _SQL_DELETE_EXPIRED_RECOVERY_LOCK,
    _SQL_MAX_SCHEMA_VERSION,
    _SQL_TUNING_EXPERIMENT_STATUS,
    _SQL_TUNING_MANIFEST,
    _STATUS_SET_CLAUSE,
    _UPDATED_AT_SET_CLAUSE,
)
from webui.store_migrations import MigrationBackupError, StoreMigrationsMixin
from webui.store_result_history_mixin import ResultHistoryStoreMixin
from webui.store_scrape_only_mixin import ScrapeOnlyStoreMixin
from webui.store_screen_resume_mixin import StoreScreenResumeMixin
from webui.store_config import StoreConfigMixin
from webui.store_recovery import StoreRecoveryMixin
from webui.store_pipeline_results import StorePipelineResultsMixin
from webui.store_jobs import StoreJobsMixin
from webui.store_tasks import StoreTasksMixin
from webui.store_profiles import StoreProfilesMixin
from webui.store_runs import StoreRunsMixin
from webui.store_scrape_runs import StoreScrapeRunsMixin
from webui.store_job_catalog import StoreJobCatalogMixin
from webui.store_tuning_experiments import StoreTuningExperimentsMixin
from webui.store_tuning_rounds import StoreTuningRoundsMixin
from webui.store_tuning_reports import StoreTuningReportsMixin
from webui.store_whitebox import StoreWhiteboxMixin

from webui.logging_setup import get_logger

_logger = get_logger(__name__)




def _db_env(db_path) -> str:
    """Infer a lightweight live/test marker from env or path for db_meta."""
    explicit = os.environ.get("CAREER_SCOUT_ENV", "").strip().lower()
    if explicit in ("live", "test", "dev"):
        return "live" if explicit == "live" else "test"
    normalized = os.fspath(db_path).replace("\\", "/")
    if ".webui-state" in normalized or "/test/" in normalized or "-test." in normalized:
        return "test"
    return "live"


_INITIALIZE_LOCK = threading.RLock()



class TaskStore(
    StoreConfigMixin,
    StoreRecoveryMixin,
    StorePipelineResultsMixin,
    StoreJobsMixin,
    StoreTasksMixin,
    StoreProfilesMixin,
    StoreRunsMixin,
    StoreScrapeRunsMixin,
    StoreJobCatalogMixin,
    StoreTuningExperimentsMixin,
    StoreTuningRoundsMixin,
    StoreTuningReportsMixin,
    StoreWhiteboxMixin,
    ResultHistoryStoreMixin,
    ScrapeOnlyStoreMixin,
    StoreMigrationsMixin,
    StoreScreenResumeMixin,
):
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

    _MIGRATION_BACKUP_TARGET_VERSION = 28
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
                # 吞噬白名单（031 B4）：finally 连接关闭清理，无上下文可留痕
                pass

            try:
                backup_conn.close()
            except Exception:
                # 吞噬白名单（031 B4）：finally 连接关闭清理，无上下文可留痕
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
                _SQL_MAX_SCHEMA_VERSION
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
                _SQL_MAX_SCHEMA_VERSION
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
                CREATE TABLE IF NOT EXISTS db_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    env TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            now = _now()
            connection.execute(
                "INSERT OR IGNORE INTO db_meta (id, env, created_at, updated_at) "
                "VALUES (1, ?, ?, ?)",
                (_db_env(self.db_path), now, now),
            )
            connection.execute(
                "UPDATE db_meta SET updated_at = ? WHERE id = 1", (now,),
            )
            connection.execute(
                "UPDATE tasks SET status = 'interrupted', error = ?, updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                ("服务重启，原任务已中断", _now()),
            )
