"""047 结构前置：失败/收口原子域（自 store_flow_state 搬运，纯搬运不改语义）。

close_flow_task_state_atomic 与 closure 辅助保持原事务顺序与状态优先级；
由 StoreFlowStateMixin 组合以保持旧 import 与 MRO 行为。
"""

from __future__ import annotations

import json

from webui.store_constants import RUN_TRANSITIONS
from webui.store_flow_core import _TERMINAL_TRACK_STATUSES
from webui.store_helpers import _now


class StoreFlowFailureMixin:
    """Persist screening/search/Track failure state in one transaction."""

    def close_flow_task_state_atomic(
        self, flow_id, platform, profile_id, **fields,
    ) -> dict:
        """Keep the existing Flow-only response contract for service callers."""
        return self.close_flow_task_state_outcome_atomic(
            flow_id, platform, profile_id, **fields,
        )["flow"]

    def close_flow_task_state_outcome_atomic(
        self,
        flow_id,
        platform,
        profile_id,
        *,
        task_run_id=None,
        scrape_run_id=None,
        status,
        error_code,
        error_reason,
        stage="ai",
    ) -> dict:
        flow_id = self._flow_required_id(flow_id, "flow_id")
        platform = self._flow_platform(platform)
        profile_id = self._flow_required_id(profile_id, "profile_id")
        status = self._flow_status(status)
        if status not in {"paused", "failed"}:
            raise ValueError("Flow failure closure must be paused or failed")
        run_ids = tuple(
            dict.fromkeys(
                str(run_id).strip()
                for run_id in (task_run_id, scrape_run_id)
                if str(run_id or "").strip()
            )
        )
        task_run_id = str(task_run_id or "").strip() or None
        scrape_run_id = str(scrape_run_id or "").strip() or None
        timestamp = _now()

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            flow = conn.execute(
                "SELECT * FROM flows WHERE id = ?", (flow_id,)
            ).fetchone()
            if flow is None or str(flow["profile_id"]) != profile_id:
                raise KeyError(flow_id)
            track = conn.execute(
                "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                (flow_id, platform),
            ).fetchone()
            if track is None:
                raise KeyError(f"{flow_id}:{platform}")

            if task_run_id:
                task_row = conn.execute(
                    "SELECT 1 FROM screening_runs WHERE id = ?",
                    (task_run_id,),
                ).fetchone()
                if task_row is not None:
                    self._assert_flow_run(
                        conn,
                        "screening_runs",
                        task_run_id,
                        profile_id,
                        platform,
                        check_platform=True,
                        flow_id=flow_id,
                        track_id=track["id"],
                        # A submission may fail between creating its durable
                        # run row and binding the Track.  Compensation is the
                        # boundary that closes that partially-created row, so
                        # require the exact Flow/platform/profile identity but
                        # allow a missing track_id in its frozen payload.
                        require_track=False,
                    )
            if scrape_run_id:
                scrape_screen_row = conn.execute(
                    "SELECT 1 FROM screening_runs WHERE id = ?",
                    (scrape_run_id,),
                ).fetchone()
                if scrape_screen_row is not None:
                    self._assert_flow_run(
                        conn,
                        "screening_runs",
                        scrape_run_id,
                        profile_id,
                        platform,
                        check_platform=True,
                        flow_id=flow_id,
                        track_id=track["id"],
                        require_track=False,
                    )
                scrape_search_row = conn.execute(
                    "SELECT 1 FROM search_runs WHERE id = ?",
                    (scrape_run_id,),
                ).fetchone()
                if scrape_search_row is not None:
                    self._assert_flow_run(
                        conn,
                        "search_runs",
                        scrape_run_id,
                        profile_id,
                        platform,
                        check_platform=False,
                        flow_id=flow_id,
                        track_id=track["id"],
                        require_track=False,
                    )

            # 047 US5/C3：写权核对必须与写入同一事务。旧尝试或 finish claim
            # 期间到达的收口请求只留安全诊断，不得改写当前 Track 事实。
            # 复核 P1：共享抓取输入不是当前阶段的写权。阶段分派按当前阶段
            # 实际拥有的执行 run 精确判定：AI 段只认 screen_run_id（旧 AI 回调
            # 即使带着仍然绑定在轨上的 scrape_run_id 也不能得到写权）；抓取段
            # 认 screen_run_id or scrape_run_id，与 retry CAS 的口径一致。
            late_blocked = False
            late_detail = ""
            stage_value = str(stage or track["stage"] or "ai").strip().lower()
            if stage_value == "ai":
                # AI 段只认 screen_run_id；共享的 scrape_run_id 不授予写权。
                current_stage_run = str(track["screen_run_id"] or "").strip()
            else:
                current_stage_run = str(
                    track["screen_run_id"] or track["scrape_run_id"] or ""
                ).strip()
            if current_stage_run:
                if current_stage_run not in run_ids:
                    late_blocked = True
                    late_detail = "superseded_attempt"
            else:
                # 尚无当前阶段执行 run（如提交补偿/首轮未绑定）：沿用原
                # 绑定交集判定；有绑定但无交集同样挡住。
                bound_ids = {
                    str(track["scrape_run_id"] or "").strip(),
                    str(track["screen_run_id"] or "").strip(),
                }
                bound_ids.discard("")
                if bound_ids and not (set(run_ids) & bound_ids):
                    late_blocked = True
                    late_detail = "superseded_attempt"
            if not late_blocked:
                for run_id in run_ids:
                    claim_row = conn.execute(
                        "SELECT status, error_code, interruption_kind "
                        "FROM screening_runs WHERE id = ?",
                        (run_id,),
                    ).fetchone()
                    if (
                        claim_row is not None
                        and str(claim_row["status"] or "") == "interrupted"
                        and str(claim_row["error_code"] or "") == "user_finished"
                        and str(claim_row["interruption_kind"] or "") == "user_finished"
                    ):
                        late_blocked = True
                        late_detail = "finish_pending"
                        break
            if late_blocked:
                self._append_flow_state_event(
                    conn,
                    run_ids[0] if run_ids else flow_id,
                    "late_callback",
                    {
                        "flow_id": flow_id,
                        "platform": platform,
                        "reason": late_detail,
                        "requested_status": status,
                        "error_code": str(error_code or ""),
                    },
                    timestamp,
                )
                return {"flow": self._flow_from_id(conn, flow_id), "applied": False}

            for run_id in run_ids:
                row = conn.execute(
                    "SELECT status FROM screening_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    continue
                target = self._closure_run_status(row["status"], status)
                if target is None:
                    continue
                if target == "paused" and row["status"] == "queued":
                    conn.execute(
                        "UPDATE screening_runs SET status = 'running', updated_at = ? "
                        "WHERE id = ?",
                        (timestamp, run_id),
                    )
                conn.execute(
                    "UPDATE screening_runs SET status = ?, error_code = ?, "
                    "error_reason = ?, updated_at = ? WHERE id = ?",
                    (target, str(error_code or "internal_error"),
                     str(error_reason or ""), timestamp, run_id),
                )

            if scrape_run_id:
                row = conn.execute(
                    "SELECT status FROM search_runs WHERE id = ?",
                    (scrape_run_id,),
                ).fetchone()
                if row is not None:
                    target = self._closure_run_status(row["status"], status)
                    if target is not None:
                        if target == "paused" and row["status"] == "queued":
                            conn.execute(
                                "UPDATE search_runs SET status = 'running', updated_at = ? "
                                "WHERE id = ?",
                                (timestamp, scrape_run_id),
                            )
                        conn.execute(
                            "UPDATE search_runs SET status = ?, error_code = ?, updated_at = ? "
                            "WHERE id = ?",
                            (target, str(error_code or "internal_error"),
                             timestamp, scrape_run_id),
                        )

            current_track_status = str(track["status"] or "")
            target_track_status = self._closure_track_status(
                current_track_status, status,
            )
            if target_track_status is not None:
                conn.execute(
                    "UPDATE flow_tracks SET status = ?, stage = ?, error_code = ?, "
                    "reason = ?, updated_at = ? WHERE flow_id = ? AND platform = ?",
                    (
                        target_track_status,
                        str(stage or track["stage"] or "ai"),
                        str(error_code or "internal_error"),
                        str(error_reason or ""),
                        timestamp,
                        flow_id,
                        platform,
                    ),
                )
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ?",
                    (timestamp, flow_id),
                )

            anchor = task_run_id or scrape_run_id or flow_id
            self._append_flow_state_event(
                conn,
                anchor,
                "flow_track_paused" if status == "paused" else "flow_track_failed",
                {
                    "flow_id": flow_id,
                    "platform": platform,
                    "error_code": str(error_code or "internal_error"),
                    "reason": str(error_reason or ""),
                },
                timestamp,
            )
            return {"flow": self._flow_from_id(conn, flow_id), "applied": True}

    @staticmethod
    def _closure_run_status(current, requested):
        current = str(current or "")
        if current in {"succeeded", "partial", "interrupted", "failed"}:
            return None if requested == "paused" or current == "failed" else None
        if current == requested:
            return current
        if requested == "paused" and current == "queued":
            # A submission failure has crossed the worker boundary even when
            # the search projection never left its initial queue.  Materialize
            # the intermediate running fact in the surrounding transaction,
            # then publish the requested paused state.
            return "paused"
        allowed = RUN_TRANSITIONS.get(current, set())
        if requested in allowed:
            return requested
        if requested == "paused" and "running" in allowed:
            return "paused"
        if requested == "failed" and "failed" in allowed:
            return "failed"
        raise ValueError(f"run cannot close from {current} to {requested}")

    @staticmethod
    def _closure_track_status(current, requested):
        current = str(current or "")
        if current in _TERMINAL_TRACK_STATUSES:
            return None
        if current == requested:
            return None
        return requested

    @staticmethod
    def _append_flow_state_event(conn, run_id, event_type, payload, timestamp):
        conn.execute(
            "INSERT OR IGNORE INTO tasks "
            "(id, kind, status, params_json, created_at, updated_at) "
            "VALUES (?, 'screening_event_log', 'logging', '{}', ?, ?)",
            (str(run_id), timestamp, timestamp),
        )
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM task_logs WHERE task_id = ?",
            (str(run_id),),
        ).fetchone()
        seq = int(row["next_seq"])
        conn.execute(
            "INSERT INTO task_logs (task_id, seq, created_at, line) VALUES (?, ?, ?, ?)",
            (
                str(run_id),
                seq,
                timestamp,
                json.dumps(
                    {"type": event_type, "payload": payload, "at": timestamp},
                    ensure_ascii=False,
                ),
            ),
        )
