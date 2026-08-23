# -*- coding: utf-8 -*-

"""迁移 017-024（021 B7 T024 自 store_migrations.py 物理搬运）。"""

from __future__ import annotations

from webui.store_helpers import _now
from webui.store_migrations_v1 import _DDL_INTEGER_DEFAULT_0, _DDL_TEXT_DEFAULT_EMPTY, _DDL_TEXT_DEFAULT_OBJECT, _SQL_SCREENING_RUN_COLUMNS

class StoreMigrationsV3Mixin:

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
