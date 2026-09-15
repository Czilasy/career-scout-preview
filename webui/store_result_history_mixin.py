"""Result history data access for the task store.

This mixin keeps every history read/write on the store's own SQLite
connection so recovery locks and transaction semantics stay consistent
with the rest of ``webui.store.TaskStore``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from webui.store_helpers import _now

_RESULT_SNAPSHOT = "result_snapshot"


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

    def _run_closure_ids(self, run_id: str, *, upward: bool = True) -> list[str]:
        """043：流程闭包收集（轮 → 根账本 → 派生过程记录）。

        以 ``execution_params_json.scrape_task_id`` 为唯一持久关联键，
        ``source_run_id`` 为补充（重抓指向目标轮）；``upward=True`` 先向上
        收根账本（整条进出用），``upward=False`` 只向下收派生（局部清中间档用）。
        """
        target = str(run_id or "")
        if not target:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, execution_params_json FROM screening_runs"
            ).fetchall()
        edges: dict[str, list[str]] = {}
        parents: dict[str, list[str]] = {}
        for row in rows:
            try:
                params = json.loads(row["execution_params_json"] or "{}")
            except (TypeError, ValueError):
                params = {}
            child = str(row["id"])
            for key in ("scrape_task_id", "source_run_id"):
                parent = str(params.get(key) or "")
                if not parent:
                    continue
                edges.setdefault(parent, []).append(child)
                parents.setdefault(child, []).append(parent)
        collected = {target}
        frontier = [target]
        # 向上：把本流程的根账本（轮 → 账本）收进来（upward=False 时跳过）
        while upward and frontier:
            current = frontier.pop()
            for parent in parents.get(current, []):
                if parent not in collected:
                    collected.add(parent)
                    frontier.append(parent)
        # 再向下：收全部派生过程记录（账本 → AI/重抓中间档）
        frontier = list(collected)
        while frontier:
            current = frontier.pop()
            for child in edges.get(current, []):
                if child not in collected:
                    collected.add(child)
                    frontier.append(child)
        return list(collected)

    def _delete_run_ids(self, ids: list[str]) -> bool:
        """删除给定 run id 集合的全部足迹（白箱 → 日志 → 主行级联）。

        FR-020：集合内存在未结束任务（queued/running/paused）时拒绝。
        """
        if not ids:
            return False
        placeholders = ",".join("?" for _ in ids)
        with self._connection() as conn:
            active = conn.execute(
                f"SELECT COUNT(*) AS n FROM screening_runs WHERE id IN ({placeholders}) "
                "AND status IN ('queued','running','paused')",
                tuple(ids),
            ).fetchone()
        if int(active["n"] or 0) > 0:
            return False
        for owner_id in ids:
            for owner_kind in ("scrape", "screening", "recrawl"):
                self.delete_whitebox_runs_for_owner(owner_kind, owner_id)
        for task_id in ids:
            self.delete_task_with_logs(task_id)
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                f"DELETE FROM screening_runs WHERE id IN ({placeholders})",
                tuple(ids),
            )
        return True

    def delete_run_closure(
        self, run_id: str, profile_id: str | None = None,
    ) -> bool:
        """043：整条删除一条流程的全部足迹（删除/淘汰共用）。

        闭包 = 轮 + 根账本 + 全部派生过程记录（AI 筛选/重抓中间档）；
        白箱证据与任务日志按 owner/task 显式清（FR-021：日志随流程清除，
        不再保留）；六张从表由外键级联自动清。删除顺序先子后主，
        中途失败时主行仍在，重跑幂等。

        FR-020：闭包内存在未结束任务（queued/running/paused）时拒绝删除。
        Spec041：传入 ``profile_id`` 时校验归属，禁止跨画像删除；无归属
        （NULL/空）老数据对所有画像可见可管。
        """
        target = str(run_id or "")
        if not target:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, profile_id FROM screening_runs WHERE id = ?",
                (target,),
            ).fetchone()
        if row is None:
            return False
        if profile_id and row["profile_id"] and str(row["profile_id"]) != str(profile_id):
            return False
        return self._delete_run_ids(self._run_closure_ids(target))

    def delete_run_subtree(self, run_id: str) -> bool:
        """043 FR-022：只删除该过程记录及其向下派生（不动根账本与轮）。

        整条进出用于流程级删除；本方法用于流程内部的中间档瘦身
        （定稿后只保留最新一次筛选/重抓中间档）。
        """
        target = str(run_id or "")
        if not target:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM screening_runs WHERE id = ?", (target,),
            ).fetchone()
        if row is None:
            return False
        return self._delete_run_ids(self._run_closure_ids(target, upward=False))

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
            if self.delete_run_closure(run_id):
                deleted.append(run_id)
        return deleted

    # ------------------------------------------------------------------
    # 043：未收尾流程的一次性提醒（记号与水位）
    # ------------------------------------------------------------------

    def run_notice_watermark(self, profile_id: str | None = None) -> str | None:
        """画像内最近一次提醒时间；作为"更旧的未收尾一律沉默"的水位。

        043 FR-004：多枚未收尾只认最新一枚、更旧者永不接力。水位持久化在
        ``run_notice_state``（036）：删除已提醒的流程行后记忆仍在，更旧的
        未收尾依旧沉默；表/列缺失（冻结版测试库停在 035 之前）时回退行级
        记号，仍不可得则按"无水位"处理。
        """
        try:
            with self._connection() as conn:
                if profile_id:
                    row = conn.execute(
                        "SELECT MAX(watermark) AS w FROM run_notice_state "
                        "WHERE profile_key IN (?, '__global__')",
                        (str(profile_id),),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT MAX(watermark) AS w FROM run_notice_state"
                    ).fetchone()
            if row is not None and row["w"]:
                return str(row["w"])
        except sqlite3.OperationalError:
            pass
        # 兼容回退：036 之前的老库按行级记号推导水位；两处都缺时返回 None。
        where = "notice_sent_at IS NOT NULL"
        params: list[Any] = []
        if profile_id:
            where += " AND (profile_id = ? OR profile_id IS NULL OR profile_id = '')"
            params.append(str(profile_id))
        try:
            with self._connection() as conn:
                row = conn.execute(
                    f"SELECT MAX(notice_sent_at) AS w FROM screening_runs WHERE {where}",
                    params,
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        return str(row["w"]) if row is not None and row["w"] else None

    def _bump_run_notice_watermark(self, conn, run_id: str, stamp: str) -> None:
        """推进持久水位（按画像分行；无归属记 __global__），只增不减。"""
        row = conn.execute(
            "SELECT profile_id FROM screening_runs WHERE id = ?", (run_id,)
        ).fetchone()
        key = str(row["profile_id"]) if row is not None and row["profile_id"] else "__global__"
        current = conn.execute(
            "SELECT watermark FROM run_notice_state WHERE profile_key = ?", (key,)
        ).fetchone()
        old = str(current["watermark"]) if current is not None and current["watermark"] else ""
        if stamp > old:
            conn.execute(
                "INSERT INTO run_notice_state (profile_key, watermark) VALUES (?, ?) "
                "ON CONFLICT(profile_key) DO UPDATE SET watermark = excluded.watermark",
                (key, stamp),
            )

    def mark_run_notice_sent(self, run_id: str) -> bool:
        """把一枚流程标记为"提醒已发出"（幂等；仅对未标记行生效）。

        同时推进持久水位（不随行删除，见 FR-004 不接力）。维护期写锁或
        冻结版缺列时不抛错、返回 False —— 提醒本身照常发出，记号没记上
        也只影响下一次启动会再判一次，不阻断主流程（FR-014）。
        """
        if not run_id:
            return False
        now = _now()
        try:
            with self._connection() as conn:
                self._assert_recovery_writes_allowed(conn)
                cursor = conn.execute(
                    "UPDATE screening_runs SET notice_sent_at = ? "
                    "WHERE id = ? AND notice_sent_at IS NULL",
                    (now, str(run_id)),
                )
                if cursor.rowcount > 0:
                    try:
                        self._bump_run_notice_watermark(conn, str(run_id), now)
                    except sqlite3.OperationalError:
                        pass  # 036 未跑的老库：退化为行级记号推导水位
        except (sqlite3.OperationalError, RuntimeError):
            return False
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # 043：无主账本兜底 / 派生记录查询 / 孤儿明细回收
    # ------------------------------------------------------------------

    def list_derived_runs_for_source(self, source_id: str) -> list[dict[str, Any]]:
        """挂在某 source（根账本/轮）下的派生过程记录，新 → 旧。

        043 FR-022：只保留最新一次的中间过程档；本查询供清理服务挑选候选。
        """
        target = str(source_id or "")
        if not target:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, platform, status, created_at FROM screening_runs "
                "WHERE record_kind = 'process_log' AND ("
                "json_extract(execution_params_json, '$.scrape_task_id') = ? "
                "OR json_extract(execution_params_json, '$.source_run_id') = ?) "
                "ORDER BY created_at DESC, rowid DESC",
                (target, target),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_unowned_run_closures(
        self, limit: int = 30, *, dry_run: bool = False,
    ) -> list[str]:
        """043 FR-011：无主账本按每平台最近 N 条保留，超出者整条清除。

        无主 = 根账本（自身无 scrape_task_id / source_run_id 父键）没有被任何
        结果轮引用；被未结束任务引用的流程不淘汰；派生记录随根走、不单独计数。
        ``dry_run=True`` 只返回候选不删除（启动兜底据此决定是否先备份）。
        """
        limit = max(1, int(limit))
        overflow: list[str] = []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, platform, status, execution_params_json FROM screening_runs "
                "WHERE record_kind = 'process_log' "
                "ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
            active_ids = {
                str(item["id"]) for item in conn.execute(
                    "SELECT id FROM screening_runs "
                    "WHERE status IN ('queued','running','paused')"
                )
            }
            referenced: set[str] = set()
            for item in conn.execute(
                "SELECT execution_params_json FROM screening_runs "
                "WHERE record_kind = 'result_snapshot'"
            ):
                try:
                    params = json.loads(item["execution_params_json"] or "{}")
                except (TypeError, ValueError):
                    params = {}
                for key in ("scrape_task_id", "source_run_id"):
                    value = str(params.get(key) or "")
                    if value:
                        referenced.add(value)
            by_platform: dict[str, list[str]] = {}
            for row in rows:
                rid = str(row["id"])
                try:
                    params = json.loads(row["execution_params_json"] or "{}")
                except (TypeError, ValueError):
                    params = {}
                if str(params.get("scrape_task_id") or "") or str(params.get("source_run_id") or ""):
                    continue
                if rid in referenced or rid in active_ids:
                    continue
                if any(cid in active_ids for cid in self._run_closure_ids(rid)):
                    continue
                by_platform.setdefault(str(row["platform"] or ""), []).append(rid)
            for _platform, ids in by_platform.items():
                overflow.extend(ids[limit:])
        if dry_run:
            return overflow
        deleted: list[str] = []
        for rid in overflow:
            if self.delete_run_closure(rid):
                deleted.append(rid)
        return deleted

    _ORPHAN_SCOPES = (
        ("screening_results", "run_id", "screening_runs"),
        ("screening_pending_results", "run_id", "screening_runs"),
        ("pipeline_checkpoints", "run_id", "screening_runs"),
        ("scrape_run_jobs", "run_id", "screening_runs"),
        ("scrape_page_progress", "run_id", "screening_runs"),
        ("task_logs", "task_id", "tasks"),
    )

    def count_orphan_rows(self) -> dict[str, int]:
        """043 启动兜底：父行已不存在的孤儿明细计数（存量核对用）。"""
        counts: dict[str, int] = {}
        with self._connection() as conn:
            for table, column, parent in self._ORPHAN_SCOPES:
                counts[table] = int(conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} "
                    f"WHERE {column} NOT IN (SELECT id FROM {parent})"
                ).fetchone()["n"] or 0)
        return counts

    def delete_orphan_rows(self) -> dict[str, int]:
        """043 启动兜底：清除父行已不存在的孤儿明细（已删轮/任务的残留）。"""
        removed: dict[str, int] = {}
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for table, column, parent in self._ORPHAN_SCOPES:
                cursor = conn.execute(
                    f"DELETE FROM {table} "
                    f"WHERE {column} NOT IN (SELECT id FROM {parent})"
                )
                removed[table] = int(cursor.rowcount or 0)
        return removed
