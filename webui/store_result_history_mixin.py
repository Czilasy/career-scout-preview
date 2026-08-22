"""Result history data access for the task store.

This mixin keeps every history read/write on the store's own SQLite
connection so recovery locks and transaction semantics stay consistent
with the rest of ``webui.store.TaskStore``.
"""

from __future__ import annotations

from typing import Any

from webui.store_helpers import _now

_RESULT_SNAPSHOT = "result_snapshot"
_HISTORY_TABLES = (
    "screening_results",
    "screening_pending_results",
    "pipeline_checkpoints",
    "scrape_run_jobs",
    "scrape_page_progress",
)


class ResultHistoryStoreMixin:
    """Store-level history queries and mutations.

    Only ``record_kind='result_snapshot'`` rows are history candidates.
    The list view requires at least one ``screening_results`` row, while
    deletion still accepts any result snapshot for backward compatibility.
    """

    def list_history_rounds(self, platform: str | None = None) -> list[dict[str, Any]]:
        """Return result snapshot rows that produced jobs, newest first."""
        where = (
            "sr.record_kind = ? AND "
            "EXISTS (SELECT 1 FROM screening_results r WHERE r.run_id = sr.id)"
        )
        params: list[Any] = [_RESULT_SNAPSHOT]
        if platform:
            where += " AND sr.platform = ?"
            params.append(str(platform))
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT sr.* FROM screening_runs sr WHERE {where} "
                "ORDER BY sr.created_at DESC, sr.rowid DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def archive_all_current_results(self) -> list[str]:
        """Archive every unarchived result snapshot.

        Archived rows stay visible in history but are no longer returned
        by the default latest-result queries.
        """
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            rows = conn.execute(
                "SELECT id FROM screening_runs "
                "WHERE record_kind = ? AND archived_at IS NULL",
                (_RESULT_SNAPSHOT,),
            ).fetchall()
            run_ids = [str(row["id"]) for row in rows]
            now = _now()
            conn.execute(
                "UPDATE screening_runs SET archived_at = ?, updated_at = ? "
                "WHERE record_kind = ? AND archived_at IS NULL",
                (now, now, _RESULT_SNAPSHOT),
            )
        return run_ids

    def history_round_exists(self, run_id: str) -> bool:
        """Return True when the run is a result snapshot row."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM screening_runs WHERE id = ? AND record_kind = ?",
                (str(run_id), _RESULT_SNAPSHOT),
            ).fetchone()
        return row is not None

    def delete_history_result_preserving_logs(self, run_id: str) -> bool:
        """Delete one result round while keeping task logs and audit rows.

        ``tasks``/``task_logs`` are intentionally preserved. Global job,
        feedback and profile-job tables are not touched.
        """
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            row = conn.execute(
                "SELECT id, platform, archived_at FROM screening_runs "
                "WHERE id = ? AND record_kind = ?",
                (str(run_id), _RESULT_SNAPSHOT),
            ).fetchone()
            if row is None:
                return False

            for table in _HISTORY_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (str(run_id),))
            conn.execute("DELETE FROM screening_runs WHERE id = ?", (str(run_id),))

        return True

    def prune_result_history(self, limit: int = 30) -> list[str]:
        """Drop the oldest rounds per platform until ``limit`` remain."""
        limit = max(1, int(limit))
        candidates: list[str] = []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT platform, COUNT(*) AS n FROM screening_runs "
                "WHERE record_kind = ? AND "
                "EXISTS (SELECT 1 FROM screening_results r WHERE r.run_id = screening_runs.id) "
                "GROUP BY platform",
                (_RESULT_SNAPSHOT,),
            ).fetchall()
            for row in rows:
                platform = str(row["platform"] or "")
                overflow = max(0, int(row["n"]) - limit)
                if overflow <= 0:
                    continue
                old_rows = conn.execute(
                    "SELECT id FROM screening_runs "
                    "WHERE platform = ? AND record_kind = ? AND "
                    "EXISTS (SELECT 1 FROM screening_results r WHERE r.run_id = screening_runs.id) "
                    "ORDER BY created_at ASC, rowid ASC LIMIT ?",
                    (platform, _RESULT_SNAPSHOT, overflow),
                ).fetchall()
                candidates.extend(str(item["id"]) for item in old_rows)
        deleted = []
        for run_id in candidates:
            if self.delete_history_result_preserving_logs(run_id):
                deleted.append(run_id)
        return deleted
