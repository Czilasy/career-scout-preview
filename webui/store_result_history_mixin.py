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
from webui.run_cleanup import HistoryDeletionBlocked

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

    @staticmethod
    def _history_closure_ids(rows, seeds) -> set[str]:
        """Collect the persisted parent/child run graph without leaving the transaction."""
        edges: dict[str, set[str]] = {}
        parents: dict[str, set[str]] = {}
        for row in rows:
            try:
                params = json.loads(row["execution_params_json"] or "{}")
            except (TypeError, ValueError):
                params = {}
            if not isinstance(params, dict):
                continue
            child = str(row["id"])
            for key in ("scrape_task_id", "source_run_id"):
                parent = str(params.get(key) or "")
                if parent:
                    edges.setdefault(parent, set()).add(child)
                    parents.setdefault(child, set()).add(parent)
        collected = {str(seed) for seed in seeds if seed}
        frontier = list(collected)
        while frontier:
            for related in parents.get(frontier.pop(), ()):
                if related not in collected:
                    collected.add(related)
                    frontier.append(related)
        frontier = list(collected)
        while frontier:
            for related in edges.get(frontier.pop(), ()):
                if related not in collected:
                    collected.add(related)
                    frontier.append(related)
        return collected

    def delete_run_closure(self, run_id: str, profile_id: str | None = None) -> bool:
        """User deletion only: atomically remove one history round and its track.

        An empty/failed track may be addressed by its track id. The other tracks
        in an aggregate stay intact; the outer flow disappears with its last track.
        Running/queued/paused tasks and foreign profile/shared run ownership refuse
        deletion before any writes. User job assets are outside this closure.
        """
        target = str(run_id or "")
        if not target:
            return False
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            track = conn.execute(
                "SELECT ft.*, f.profile_id FROM flow_tracks ft "
                "JOIN flows f ON f.id = ft.flow_id "
                "WHERE ft.id = ? OR ft.result_run_id = ?",
                (target, target),
            ).fetchone()
            row = conn.execute("SELECT * FROM screening_runs WHERE id = ?", (target,)).fetchone()
            if track is None and row is None:
                return False
            owner = (track if track is not None else row)["profile_id"]
            if profile_id and owner and str(owner) != str(profile_id):
                return False
            active = {"queued", "running", "paused"}
            if track is not None and track["status"] in active:
                raise HistoryDeletionBlocked("请先结束或取消流程，再删除历史轮次")
            rows = conn.execute("SELECT * FROM screening_runs").fetchall()
            seeds = {target} if row is not None else set()
            if track is not None:
                seeds.update(track[key] for key in ("scrape_run_id", "screen_run_id", "result_run_id") if track[key])
                search_rows = conn.execute("SELECT * FROM search_runs").fetchall()
                for item in [*rows, *search_rows]:
                    try:
                        field = "execution_params_json" if "execution_params_json" in item.keys() else "profile_snapshot_json"
                        params = json.loads(item[field] or "{}")
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(params, dict):
                        continue
                    if params.get("track_id") == track["id"] or (
                        params.get("flow_id") == track["flow_id"]
                        and (item["platform"] if "platform" in item.keys() else params.get("platform")) == track["platform"]
                    ):
                        seeds.add(item["id"])
            ids = sorted(self._history_closure_ids(rows, seeds))
            if ids:
                marks = ",".join("?" for _ in ids)
                for table in ("screening_runs", "search_runs", "tasks"):
                    related = conn.execute(f"SELECT * FROM {table} WHERE id IN ({marks})", ids).fetchall()
                    for item in related:
                        if item["status"] in active:
                            raise HistoryDeletionBlocked("请先结束或取消流程，再删除历史轮次")
                        if "profile_id" in item.keys() and item["profile_id"] and item["profile_id"] != (owner or profile_id):
                            return False
                # Never erase a run still owned by another platform track.
                shared = conn.execute(
                    f"SELECT id FROM flow_tracks WHERE (scrape_run_id IN ({marks}) "
                    f"OR screen_run_id IN ({marks}) OR result_run_id IN ({marks}))",
                    ids * 3,
                ).fetchall()
                if any(track is None or item["id"] != track["id"] for item in shared):
                    return False
                conn.execute(
                    f"DELETE FROM whitebox_runs WHERE owner_id IN ({marks}) OR parent_owner_id IN ({marks})",
                    ids * 2,
                )
                conn.execute(f"DELETE FROM task_logs WHERE task_id IN ({marks})", ids)
                conn.execute(f"DELETE FROM tasks WHERE id IN ({marks})", ids)
                conn.execute(f"DELETE FROM screening_runs WHERE id IN ({marks})", ids)
                conn.execute(f"DELETE FROM search_runs WHERE id IN ({marks})", ids)
            if track is not None:
                conn.execute("DELETE FROM flow_tracks WHERE id = ?", (track["id"],))
                conn.execute(
                    "DELETE FROM flows WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM flow_tracks WHERE flow_id = ?)",
                    (track["flow_id"], track["flow_id"]),
                )
        return True

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
