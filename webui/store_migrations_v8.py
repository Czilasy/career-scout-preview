"""Schema migration 040 for B096 V2 condition snapshots.

Adds a durable JSON column to search_packages so a saved package can restore
the full unified/final condition state exactly as the user froze it.  Existing
rows keep the default empty object; no historical mapping is recalculated.
"""

from __future__ import annotations

from webui.store_helpers import _now


class StoreMigrationsV8Mixin:
    """B096 V2 condition snapshot migration."""

    def _migration_040(self):
        """Add condition_snapshot_json to search_packages atomically."""
        with self._connection() as conn:
            columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(search_packages)"
                )
            }
            if columns and "condition_snapshot_json" not in columns:
                conn.execute(
                    "ALTER TABLE search_packages ADD COLUMN "
                    "condition_snapshot_json TEXT NOT NULL DEFAULT '{}'"
                )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(version, applied_at, description) "
                "VALUES (40, ?, 'search package condition snapshots')",
                (_now(),),
            )