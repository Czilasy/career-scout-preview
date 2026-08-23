# -*- coding: utf-8 -*-

"""迁移 001-008 与调度/公共助手（021 B7 T024 自 store_migrations.py 物理搬运）。"""

from __future__ import annotations

import sqlite3
from webui.store_helpers import _now, _uuid

_DDL_TEXT_NOT_NULL = "TEXT NOT NULL"
_DDL_TEXT_DEFAULT_EMPTY = "TEXT NOT NULL DEFAULT ''"
_DDL_TEXT_DEFAULT_OBJECT = "TEXT NOT NULL DEFAULT '{}'"
_DDL_TEXT_DEFAULT_BOSS = "TEXT NOT NULL DEFAULT 'boss'"
_DDL_INTEGER_DEFAULT_0 = "INTEGER NOT NULL DEFAULT 0"
_SQL_SCREENING_RUN_COLUMNS = "PRAGMA table_info(screening_runs)"

class StoreMigrationsV1Mixin:

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
        if current < 32:
            self._migration_032()
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
