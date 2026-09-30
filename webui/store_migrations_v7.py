"""Schema migrations 038/039 for persistent parallel discovery flows.

The migration adds only ownership and projection relations.  Existing search,
screening, and result rows are intentionally left untouched; new code can bind
them to a FlowTrack after their owning Flow has been created.
"""

from __future__ import annotations

from webui.store_helpers import _now


class StoreMigrationsV7Mixin:
    """B096 Flow/Track schema migration."""

    @staticmethod
    def _flow_migration_statements() -> tuple[str, ...]:
        """Return DDL in execution order so the transaction can be tested."""
        return (
            """
            CREATE TABLE IF NOT EXISTS flows (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                selection TEXT NOT NULL CHECK (selection IN ('all', 'boss', 'zhilian')),
                start_key TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS flow_tracks (
                id TEXT PRIMARY KEY,
                flow_id TEXT NOT NULL,
                platform TEXT NOT NULL CHECK (platform IN ('boss', 'zhilian')),
                scrape_run_id TEXT,
                screen_run_id TEXT,
                result_run_id TEXT,
                confirmed_filters_snapshot TEXT NOT NULL DEFAULT '{}',
                submission_snapshot_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT NOT NULL DEFAULT 'pending',
                error_code TEXT,
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (flow_id, platform),
                FOREIGN KEY (flow_id) REFERENCES flows(id) ON DELETE CASCADE,
                FOREIGN KEY (scrape_run_id) REFERENCES search_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (screen_run_id) REFERENCES screening_runs(id) ON DELETE SET NULL,
                FOREIGN KEY (result_run_id) REFERENCES screening_runs(id) ON DELETE SET NULL
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_flows_profile_start_key
            ON flows(profile_id, start_key)
            WHERE start_key IS NOT NULL AND start_key <> ''
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_flows_profile_updated
            ON flows(profile_id, updated_at DESC, created_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_flow_tracks_flow_status
            ON flow_tracks(flow_id, status, updated_at DESC)
            """,
        )

    def _migration_038(self):
        """Create Flow/Track ownership tables atomically and idempotently."""
        with self._connection() as conn:
            # SQLite does not implicitly wrap standalone DDL in the surrounding
            # connection context.  Start the migration transaction explicitly
            # so a later DDL failure cannot leave half of v038 behind.
            conn.execute("BEGIN")
            for statement in self._flow_migration_statements():
                conn.execute(statement)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, applied_at, description) "
                "VALUES (38, ?, 'parallel discovery flows and tracks')",
                (_now(),),
            )

    def _migration_039(self):
        """Persist the safe request snapshot needed to retry a preflight pause."""
        with self._connection() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(flow_tracks)")
            }
            if columns and "submission_snapshot_json" not in columns:
                conn.execute(
                    "ALTER TABLE flow_tracks ADD COLUMN "
                    "submission_snapshot_json TEXT NOT NULL DEFAULT '{}'"
                )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, applied_at, description) VALUES "
                "(39, ?, 'Flow Track preflight retry snapshot')",
                (_now(),),
            )
