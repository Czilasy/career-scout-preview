"""047 US1：执行段终态/原因与关联账本的同事务公开入口。

`store_runs.update_screening_run` 把 partial 视为 terminal_success 并清空
error 字段，无法保留“有具体失败原因的 partial”。本模块提供公开原子入口
`publish_scrape_outcome_atomic`：在同一 SQLite 事务内写 screening/search
执行段终态、原因与关联账本；沿既有 RUN_TRANSITIONS 合法性，不修改旧全局
更新方法的语义。
"""

from __future__ import annotations

from webui.store_constants import RUN_TRANSITIONS
from webui.store_helpers import _now


class StoreRunLifecycleMixin:
    """Atomic execution-outcome publication with preserved failure reasons."""

    def publish_scrape_outcome_atomic(
        self,
        run_id,
        *,
        status,
        error_code="",
        error_reason="",
        integrity=None,
        processed_count=None,
        source_count=None,
        total_scraped=None,
        current_stage="scrape",
        search_status=None,
    ) -> dict | None:
        """Write one scrape execution outcome and its ledger in one transaction.

        ``partial`` keeps the concrete failure code/reason when the caller has
        one; the integrity report stays available separately on the run's
        event stream and in the caller's response, so completeness never
        erases the original cause.
        """
        run_id = str(run_id or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        status = str(status or "").strip()
        if status not in RUN_TRANSITIONS:
            raise ValueError(f"unknown execution status: {status}")
        timestamp = _now()

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            screen = conn.execute(
                "SELECT id, status FROM screening_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            search = conn.execute(
                "SELECT id, status FROM search_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if screen is None and search is None:
                return None

            if screen is not None:
                self._lifecycle_apply_status(
                    conn, "screening_runs", run_id,
                    current=str(screen["status"] or ""), requested=status,
                    error_code=error_code, error_reason=error_reason,
                    timestamp=timestamp, processed_count=processed_count,
                    source_count=source_count, total_scraped=total_scraped,
                    current_stage=current_stage,
                )
            if search is not None:
                ledger_status = str(search_status or status)
                self._lifecycle_apply_status(
                    conn, "search_runs", run_id,
                    current=str(search["status"] or ""),
                    requested=ledger_status,
                    error_code=error_code, error_reason="",
                    timestamp=timestamp, processed_count=None,
                    source_count=None, total_scraped=None,
                    current_stage=None,
                )

            row = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (run_id,),
            ).fetchone()
            if row is not None:
                return self._lifecycle_screening_row(row)
            row = conn.execute(
                "SELECT * FROM search_runs WHERE id = ?", (run_id,),
            ).fetchone()
            return self._run_row(row)

    def get_screening_run_outcome(self, run_id) -> dict | None:
        """Read the authoritative execution outcome for lifecycle consumers.

        047 C1：legacy ``store_runs._screening_run_row`` 继续保留 033 v2 的
        「终态成功/partial 不暴露旧暂停细节」投影（该模块不在本轮 047 允许
        边界内）。完整的执行段原因由本生命周期域拥有：partial 表示「结束但
        不完整」，其具体失败码/原因必须可供 task-state 与界面读取，不能
        被完整性主因或旧投影覆盖。
        """
        run_id = str(run_id or "").strip()
        if not run_id:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._lifecycle_screening_row(row)

    def _lifecycle_screening_row(self, row) -> dict:
        """Full legacy projection with the lifecycle-owned outcome restored.

        partial 的 `error_code/error_reason` 由本域按原始行读回；其余字段
        保持 `_screening_run_row` 的既有形状，调用方（task-state、闭包投影
        与界面）看到同一份完整执行事实。
        """
        run = self._screening_run_row(row)
        if str(run.get("status") or "") == "partial":
            keys = row.keys()
            run["error_code"] = row["error_code"]
            run["error_reason"] = row["error_reason"] if "error_reason" in keys else None
        return run

    def _lifecycle_apply_status(
        self,
        conn,
        table,
        run_id,
        *,
        current,
        requested,
        error_code,
        error_reason,
        timestamp,
        processed_count,
        source_count,
        total_scraped,
        current_stage,
    ) -> None:
        """Apply one legal transition; legacy failed->paused bridge preserved."""
        if requested != current:
            allowed = RUN_TRANSITIONS.get(current, set())
            if requested not in allowed:
                raise ValueError(
                    f"运行不能从 {current} 转换到 {requested} (table={table})"
                )
        sets = ["status = ?", "updated_at = ?"]
        params: list[object] = [requested, timestamp]
        if current_stage and table == "screening_runs":
            sets.append("current_stage = ?")
            params.append(str(current_stage))
        if error_code:
            sets.append("error_code = ?")
            params.append(str(error_code))
        if error_reason:
            sets.append("error_reason = ?")
            params.append(str(error_reason))
        if processed_count is not None and table == "screening_runs":
            sets.append("processed_count = ?")
            params.append(int(processed_count))
        if source_count is not None and table == "screening_runs":
            sets.append("source_count = ?")
            params.append(int(source_count))
        if total_scraped is not None:
            sets.append("total_scraped = ?" if table == "screening_runs" else "discovered_count = ?")
            params.append(int(total_scraped))
        params.append(run_id)
        conn.execute(
            f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params,
        )


__all__ = ["StoreRunLifecycleMixin"]
