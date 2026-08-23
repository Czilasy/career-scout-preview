# -*- coding: utf-8 -*-

"""迁移 025-032（021 B7 T024 自 store_migrations.py 物理搬运）。"""

from __future__ import annotations

from datetime import datetime
from webui.store_helpers import _CST, _now
from webui.store_migrations_v1 import _DDL_TEXT_DEFAULT_BOSS, _DDL_TEXT_DEFAULT_EMPTY, _DDL_TEXT_DEFAULT_OBJECT, _DDL_TEXT_NOT_NULL, _SQL_SCREENING_RUN_COLUMNS

class StoreMigrationsV4Mixin:

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

    def _migration_032(self):
        """017-US3: 一次性清空存量历史轮（FR-009/SC-005）。

        删除全部 ``record_kind='result_snapshot'`` 轮及其子表行（表集合与
        ``delete_history_result_preserving_logs`` 一致）；任务行（process_log）、
        任务日志/事件与活动任务进度/断点一律不动。迁移幂等（版本号只跑一次）。
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM screening_runs WHERE record_kind = 'result_snapshot'"
            ).fetchall()
            for row in rows:
                run_id = str(row["id"])
                for table in (
                    "screening_results",
                    "screening_pending_results",
                    "pipeline_checkpoints",
                    "scrape_run_jobs",
                    "scrape_page_progress",
                ):
                    conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM screening_runs WHERE id = ?", (run_id,))
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) "
                "VALUES (32, ?, 'clear legacy history result snapshots')",
                (_now(),),
            )
