"""B096 scrape-run identity and Flow ownership persistence."""

from __future__ import annotations

import json

from webui.store_helpers import _now
from webui.store_constants import MAX_DETAIL_BUDGET

# 进程重启时归一成可恢复中断的任务事实（与 _mark_stale_runs_interrupted 同一口径）。
_RESTART_INTERRUPTION_CODES = ("restart",)
_RESTART_INTERRUPTION_KINDS = ("process_restart",)
# 用户主动结束/暂停的 run 保留原有区分，不参与重启归一。
_USER_OWNED_INTERRUPTION_CODES = ("user_finished", "user_paused", "operator_stop")


class StoreFlowRunIdentityMixin:
    def create_scrape_search_run(
            self,
            run_id,
            profile_id,
            *,
            platform="",
            flow_id="",
            track_id="",
            profile_snapshot=None,
        ):
            """Register the durable search-side identity for a scrape task.

            The browser task still uses ``screening_runs`` for its historical
            progress/checkpoint contract, but a B096 Track binds that task through
            ``scrape_run_id``.  Keeping a row in ``search_runs`` makes that
            distinction real and satisfies the Flow foreign key instead of using
            the AI ``screen_run_id`` as a scrape placeholder.
            """
            run_id = str(run_id)
            profile_id = str(profile_id)
            snapshot = dict(profile_snapshot or {})
            if platform:
                snapshot.setdefault("platform", str(platform))
            if flow_id:
                snapshot.setdefault("flow_id", str(flow_id))
            ts = _now()
            with self._connection() as conn:
                self._assert_recovery_writes_allowed(conn)
                if flow_id and platform and not snapshot.get("track_id"):
                    track = conn.execute(
                        "SELECT id FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                        (str(flow_id), str(platform).strip().lower()),
                    ).fetchone()
                    if track is not None:
                        snapshot["track_id"] = str(track["id"])
                if track_id:
                    snapshot.setdefault("track_id", str(track_id))
                conn.execute(
                    "INSERT OR IGNORE INTO search_runs "
                    "(id, profile_id, profile_snapshot_json, mode, status, "
                    "total_detail_budget, discovered_count, completed_jd_count, "
                    "created_at, updated_at, error_code) "
                    "VALUES (?, ?, ?, 'scrape', 'queued', ?, 0, 0, ?, ?, NULL)",
                    (
                        run_id,
                        profile_id,
                        json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                        int(MAX_DETAIL_BUDGET),
                        ts,
                        ts,
                    ),
                )
            return self.get_search_run(run_id)

    def reconcile_flow_tracks_with_runs(self) -> int:
        """进程重启后把 Track 与它绑定的任务事实对齐（通用生命周期，不区分平台）。

        启动归一只写 run 投影：绑定的抓取/筛选 run 已经落到 interrupted 或
        failed，Track 却还停在 running，于是同一屏同时出现「已中断」与
        「抓取中」，计时继续走，还给不出注定送不达的暂停/停止。这里在树干补
        一次：先认领「自己声明属于该轨道却从未被绑定」的筛选 run（轨道没有
        可操作对象时轨道级动作只能失败），再把活动轨道归一到任务真实状态。

        用户主动结束或主动暂停的 run 保持原区分，不会被说成中断。
        """
        timestamp = _now()
        changed = 0
        with self._connection() as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'flow_tracks'"
            ).fetchone()
            if table is None:
                return 0
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                "UPDATE flow_tracks SET screen_run_id = ("
                "  SELECT r.id FROM screening_runs r"
                "   WHERE r.record_kind <> 'result_snapshot'"
                "     AND json_extract(r.execution_params_json, '$.flow_id') = flow_tracks.flow_id"
                "     AND json_extract(r.execution_params_json, '$.track_id') = flow_tracks.id"
                "     AND (flow_tracks.scrape_run_id IS NULL"
                "          OR json_extract(r.execution_params_json, '$.scrape_task_id')"
                "             = flow_tracks.scrape_run_id)"
                "   ORDER BY r.created_at DESC LIMIT 1"
                "), updated_at = ?"
                " WHERE screen_run_id IS NULL"
                "   AND EXISTS ("
                "     SELECT 1 FROM screening_runs r"
                "      WHERE r.record_kind <> 'result_snapshot'"
                "        AND json_extract(r.execution_params_json, '$.flow_id') = flow_tracks.flow_id"
                "        AND json_extract(r.execution_params_json, '$.track_id') = flow_tracks.id"
                "        AND (flow_tracks.scrape_run_id IS NULL"
                "             OR json_extract(r.execution_params_json, '$.scrape_task_id')"
                "                = flow_tracks.scrape_run_id))",
                (timestamp,),
            )

            rows = conn.execute(
                "SELECT ft.id AS track_id, ft.status AS track_status,"
                "       sr.status AS screen_status, sr.error_code AS screen_error,"
                "       sr.interruption_kind AS screen_kind,"
                "       cr.status AS scrape_status, cr.error_code AS scrape_error,"
                "       cr.interruption_kind AS scrape_kind"
                " FROM flow_tracks ft"
                " LEFT JOIN screening_runs sr ON sr.id = ft.screen_run_id"
                " LEFT JOIN screening_runs cr ON cr.id = ft.scrape_run_id"
                " WHERE ft.status IN ('queued', 'running')"
            ).fetchall()
            for row in rows:
                decision = self._track_restart_decision(row)
                if decision is None:
                    continue
                status, stage = decision
                conn.execute(
                    "UPDATE flow_tracks SET status = ?, stage = ?, updated_at = ?"
                    " WHERE id = ?",
                    (status, stage, timestamp, row["track_id"]),
                )
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ("
                    "  SELECT flow_id FROM flow_tracks WHERE id = ?)",
                    (timestamp, row["track_id"]),
                )
                changed += 1
        return changed

    @staticmethod
    def _track_restart_decision(row) -> tuple[str, str] | None:
        """Decide one Track's restart status from its bound run facts (AI lane first)."""
        def _restart_status(status, error_code, interruption_kind):
            current = str(status or "")
            code = str(error_code or "")
            if code in _USER_OWNED_INTERRUPTION_CODES:
                return None
            if current == "failed":
                return "failed"
            if current == "interrupted" and (
                code in _RESTART_INTERRUPTION_CODES
                or str(interruption_kind or "") in _RESTART_INTERRUPTION_KINDS
            ):
                return "interrupted"
            return None

        interrupted = _restart_status(
            row["screen_status"], row["screen_error"], row["screen_kind"],
        )
        if interrupted is not None:
            return interrupted, "ai"
        interrupted = _restart_status(
            row["scrape_status"], row["scrape_error"], row["scrape_kind"],
        )
        if interrupted is not None:
            return interrupted, "scrape"
        return None
