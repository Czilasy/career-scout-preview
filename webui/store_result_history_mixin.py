"""Result history data access for the task store.

This mixin keeps every history read/write on the store's own SQLite
connection so recovery locks and transaction semantics stay consistent
with the rest of ``webui.store.TaskStore``.
"""

from __future__ import annotations

import sqlite3
import time
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

    def list_history_rounds(
        self, platform: str | None = None, profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return result snapshot rows that produced jobs, newest first.

        ``profile_id`` 限定求职画像归属；不传保持旧的全局视图（只读接口
        兼容），真实 UI 调用一律带画像。

        无归属（NULL/空）的轮次是画像功能之前的旧数据，按用户拍板
        （Spec041 收尾）：老数据要留在历史里能看见，对所有画像可见。
        新数据仍严格按归属过滤，隔离保证不变。
        """
        where = (
            "sr.record_kind = ? AND "
            "EXISTS (SELECT 1 FROM screening_results r WHERE r.run_id = sr.id)"
        )
        params: list[Any] = [_RESULT_SNAPSHOT]
        if platform:
            where += " AND sr.platform = ?"
            params.append(str(platform))
        if profile_id:
            where += (
                " AND (sr.profile_id = ? OR sr.profile_id IS NULL OR sr.profile_id = '')"
            )
            params.append(str(profile_id))
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT sr.* FROM screening_runs sr WHERE {where} "
                "ORDER BY sr.created_at DESC, sr.rowid DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def archive_all_current_results(self, profile_id: str | None = None) -> list[str]:
        """Archive unarchived result snapshots of one profile.

        Archived rows stay visible in history but are no longer returned
        by the default latest-result queries.

        Spec041：归档必须限定画像。传入 ``profile_id`` 只归档该画像的
        轮次；不传时只归档没有归属的旧数据（profile_id IS NULL），
        决不跨画像动手。

        Spec041 收尾（用户拍板）：无归属老数据对所有画像可见，开新一轮
        归档时一并收走（否则它会一直挂在"最新"，新旧轮会打架）；
        有归属的轮次仍严格限定，绝不跨画像归档。

        020 US7 模式：归档与 worker 收尾（如 recrawl 回写判定/计数）并发
        抢 SQLite 写锁时，短退避重试扛瞬时锁冲突（与 result_rounds 的
        ``_retry_transient_lock`` 一致）；recovery maintenance 锁抛的
        RuntimeError 不重试（锁未过期前重试无意义）。
        """
        if profile_id:
            scope_sql = "(profile_id = ? OR profile_id IS NULL OR profile_id = '')"
            scope_params: tuple[Any, ...] = (str(profile_id),)
        else:
            scope_sql = "profile_id IS NULL"
            scope_params = ()

        def _archive() -> list[str]:
            with self._connection() as conn:
                self._assert_recovery_writes_allowed(conn)
                rows = conn.execute(
                    "SELECT id FROM screening_runs "
                    f"WHERE record_kind = ? AND archived_at IS NULL AND {scope_sql}",
                    (_RESULT_SNAPSHOT, *scope_params),
                ).fetchall()
                run_ids = [str(row["id"]) for row in rows]
                now = _now()
                conn.execute(
                    "UPDATE screening_runs SET archived_at = ?, updated_at = ? "
                    f"WHERE record_kind = ? AND archived_at IS NULL AND {scope_sql}",
                    (now, now, _RESULT_SNAPSHOT, *scope_params),
                )
            return run_ids

        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(3):
            try:
                return _archive()
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def history_round_exists(self, run_id: str) -> bool:
        """Return True when the run is a result snapshot row."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM screening_runs WHERE id = ? AND record_kind = ?",
                (str(run_id), _RESULT_SNAPSHOT),
            ).fetchone()
        return row is not None

    def delete_history_result_preserving_logs(
        self, run_id: str, profile_id: str | None = None,
    ) -> bool:
        """Delete one result round while keeping task logs and audit rows.

        ``tasks``/``task_logs`` are intentionally preserved. Global job,
        feedback and profile-job tables are not touched.

        Spec041：传入 ``profile_id`` 时校验轮次归属，不属于该画像的轮次
        按不存在处理，禁止跨画像删除。无归属（NULL/空）的老数据是画像
        功能之前的遗留，对所有画像可见可管（Spec041 收尾用户拍板），
        有归属的轮次仍严格校验。
        """
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            row = conn.execute(
                "SELECT id, platform, archived_at, profile_id FROM screening_runs "
                "WHERE id = ? AND record_kind = ?",
                (str(run_id), _RESULT_SNAPSHOT),
            ).fetchone()
            if row is None:
                return False
            if profile_id and row["profile_id"] and str(row["profile_id"]) != str(profile_id):
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
