"""Schema migration 033 for the task completion evidence whitebox."""

from __future__ import annotations

from webui.store_helpers import _now


class StoreMigrationsV5Mixin:
    """Create the append-only whitebox projections and event ledger."""

    def _migration_033(self):
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS whitebox_runs (
                    id TEXT PRIMARY KEY,
                    owner_kind TEXT NOT NULL CHECK (owner_kind IN
                        ('scrape','screening','recrawl','workbench','legacy_task','tuning')),
                    owner_id TEXT NOT NULL,
                    parent_owner_id TEXT,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    lifecycle_status TEXT NOT NULL DEFAULT 'queued' CHECK
                        (lifecycle_status IN ('queued','running','paused','terminal')),
                    conclusion TEXT CHECK
                        (conclusion IS NULL OR conclusion IN
                        ('succeeded','empty','partial','failed','unverifiable','interrupted')),
                    evidence_complete INTEGER NOT NULL DEFAULT 0 CHECK (evidence_complete IN (0,1)),
                    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0,1)),
                    planned_unit_count INTEGER NOT NULL DEFAULT 0 CHECK (planned_unit_count >= 0),
                    observed_unit_count INTEGER NOT NULL DEFAULT 0 CHECK (observed_unit_count >= 0),
                    completed_unit_count INTEGER NOT NULL DEFAULT 0 CHECK (completed_unit_count >= 0),
                    failed_unit_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_unit_count >= 0),
                    unknown_unit_count INTEGER NOT NULL DEFAULT 0 CHECK (unknown_unit_count >= 0),
                    unit_output_sum INTEGER NOT NULL DEFAULT 0 CHECK (unit_output_sum >= 0),
                    run_unique_count INTEGER NOT NULL DEFAULT 0 CHECK (run_unique_count >= 0),
                    quality_counts_json TEXT NOT NULL DEFAULT '{}',
                    primary_code TEXT,
                    primary_reason TEXT,
                    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                    started_at TEXT NOT NULL,
                    finalized_at TEXT,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 33,
                    UNIQUE(owner_kind, owner_id)
                );

                CREATE TABLE IF NOT EXISTS whitebox_units (
                    id TEXT PRIMARY KEY,
                    whitebox_run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    unit_kind TEXT NOT NULL,
                    unit_key TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
                    recovered_from_unit_id TEXT,
                    planned_pages INTEGER CHECK (planned_pages IS NULL OR planned_pages >= 0),
                    completed_pages INTEGER NOT NULL DEFAULT 0 CHECK (completed_pages >= 0),
                    last_completed_page INTEGER CHECK (last_completed_page IS NULL OR last_completed_page >= 0),
                    scope_complete INTEGER CHECK (scope_complete IS NULL OR scope_complete IN (0,1)),
                    source_exhausted INTEGER CHECK (source_exhausted IS NULL OR source_exhausted IN (0,1)),
                    returned_total_count INTEGER NOT NULL DEFAULT 0 CHECK (returned_total_count >= 0),
                    unit_unique_count INTEGER NOT NULL DEFAULT 0 CHECK (unit_unique_count >= 0),
                    stop_reason TEXT,
                    status TEXT NOT NULL DEFAULT 'planned' CHECK
                        (status IN ('planned','running','succeeded','empty','failed','incomplete','skipped','unverifiable','interrupted')),
                    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0,1)),
                    evidence_complete INTEGER NOT NULL DEFAULT 0 CHECK (evidence_complete IN (0,1)),
                    quality_counts_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    error_reason TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(whitebox_run_id, stage, unit_kind, unit_key, attempt_no),
                    FOREIGN KEY (whitebox_run_id) REFERENCES whitebox_runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS whitebox_events (
                    id TEXT PRIMARY KEY,
                    whitebox_run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK (sequence >= 1),
                    idempotency_key TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    unit_kind TEXT,
                    unit_key TEXT,
                    attempt_no INTEGER CHECK (attempt_no IS NULL OR attempt_no >= 1),
                    event_type TEXT NOT NULL,
                    required_evidence INTEGER NOT NULL DEFAULT 0 CHECK (required_evidence IN (0,1)),
                    severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warning','error')),
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    origin TEXT NOT NULL DEFAULT 'primary',
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(whitebox_run_id, sequence),
                    UNIQUE(whitebox_run_id, idempotency_key),
                    FOREIGN KEY (whitebox_run_id) REFERENCES whitebox_runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_whitebox_runs_owner
                    ON whitebox_runs(owner_kind, owner_id);
                CREATE INDEX IF NOT EXISTS idx_whitebox_units_run_key
                    ON whitebox_units(whitebox_run_id, unit_key, attempt_no);
                CREATE INDEX IF NOT EXISTS idx_whitebox_events_run_sequence
                    ON whitebox_events(whitebox_run_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_whitebox_events_run_unit
                    ON whitebox_events(whitebox_run_id, unit_key, sequence);
                INSERT OR IGNORE INTO schema_migrations(version, applied_at, description)
                    VALUES (33, CURRENT_TIMESTAMP, 'task completion evidence whitebox');
                """
            )
