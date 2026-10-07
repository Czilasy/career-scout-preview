"""047 结构前置：取消原子域（自 store_flow_state 搬运，纯搬运不改语义）。

cancel_task_atomic 保持原事务顺序、状态优先级与返回结构；
由 StoreFlowStateMixin 组合以保持旧 import 与 MRO 行为。
"""

from __future__ import annotations

from webui.store_helpers import _now


class StoreFlowCancelMixin:
    """Cancel one task and its exact Flow Track in one transaction."""

    def cancel_task_atomic(
        self,
        task_run_id,
        *,
        flow_binding=None,
        sync_track=True,
        track_status="cancelled",
        allow_interrupted_projection=False,
    ):
        """Cancel one task and its exact Flow Track in one SQLite transaction.

        A legacy process can be visible before either durable run projection is
        created.  In that window this method deliberately returns ``None`` so
        the caller can signal the process-local worker without manufacturing a
        fake cancelled Run.  Once a Run exists, every durable projection and
        its owning Track are validated before the first write; fully settled
        projections return an explicit no-op outcome (while closing only a
        stale active Track), and SQLite rollback leaves the durable state
        unchanged when a real cancellation write fails.

        ``allow_interrupted_projection`` is used only by the locked cold-worker
        boundary: restart can settle the search ledger while leaving its screen
        projection paused. The settled ledger stays intact; successful/failed
        projections and exact binding conflicts are still rejected.
        """
        task_run_id = self._flow_required_id(task_run_id, "task_run_id")
        binding = dict(flow_binding or {})
        desired_track_status = str(track_status or "cancelled").strip().lower()
        if desired_track_status not in {"cancelled", "stopped"}:
            raise ValueError("unsupported cancellation Track status")
        timestamp = _now()

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            screening = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?",
                (task_run_id,),
            ).fetchone()
            search = conn.execute(
                "SELECT * FROM search_runs WHERE id = ?",
                (task_run_id,),
            ).fetchone()
            if screening is None and search is None:
                return None

            track = None
            if binding:
                flow_id = self._flow_required_id(binding.get("flow_id"), "flow_id")
                track_id = self._flow_required_id(binding.get("track_id"), "track_id")
                platform = self._flow_platform(binding.get("platform"))
                profile_id = self._flow_required_id(
                    binding.get("profile_id"), "profile_id"
                )
                track = conn.execute(
                    "SELECT * FROM flow_tracks WHERE flow_id = ? AND id = ?",
                    (flow_id, track_id),
                ).fetchone()
                if track is None or str(track["platform"]) != platform:
                    raise ValueError("Flow Track binding is missing")
                flow = conn.execute(
                    "SELECT profile_id FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if flow is None or str(flow["profile_id"]) != profile_id:
                    raise ValueError("Flow profile binding is invalid")
                if task_run_id not in {
                    str(track["scrape_run_id"] or ""),
                    str(track["screen_run_id"] or ""),
                }:
                    raise ValueError("task is not bound to the selected Flow Track")
            else:
                track = conn.execute(
                    "SELECT ft.* FROM flow_tracks ft "
                    "WHERE ft.scrape_run_id = ? OR ft.screen_run_id = ? "
                    "ORDER BY CASE WHEN ft.screen_run_id = ? THEN 0 ELSE 1 END "
                    "LIMIT 1",
                    (task_run_id, task_run_id, task_run_id),
                ).fetchone()
                if track is not None:
                    # A durable Flow owner exists even when the caller did not
                    # pass the resolved binding; require the same identity
                    # checks used by Flow routes instead of guessing a lane.
                    raise ValueError("Flow task binding must be resolved before cancel")

            if binding:
                flow_id = str(binding["flow_id"])
                track_id = str(binding["track_id"])
                platform = str(binding["platform"])
                profile_id = str(binding["profile_id"])
                if screening is not None:
                    self._assert_flow_run(
                        conn, "screening_runs", task_run_id, profile_id,
                        platform, check_platform=True, flow_id=flow_id,
                        track_id=track_id, require_track=True,
                    )
                if search is not None:
                    self._assert_flow_run(
                        conn, "search_runs", task_run_id, profile_id,
                        platform, check_platform=False, flow_id=flow_id,
                        track_id=track_id, require_track=True,
                    )

            def _check_run(row, label):
                if row is None:
                    return
                status = str(row["status"] or "")
                if status in {"succeeded", "partial", "failed"}:
                    raise ValueError(f"{label} run is already terminal")
                if status == "interrupted":
                    if allow_interrupted_projection:
                        return
                    raise ValueError(f"{label} run is already terminal")
                if status not in {"queued", "running", "paused", "interrupted"}:
                    raise ValueError(f"{label} run has unsupported status")

            def _run_is_non_cancelable(row):
                if row is None:
                    return True
                status = str(row["status"] or "")
                if status in {"succeeded", "partial", "failed", "cancelled", "done", "stopped"}:
                    return True
                # A process-restart/operator-stop interruption has no live
                # worker to signal.  user_cancelled is also already settled;
                # the route's user_finished special case remains unchanged.
                return status == "interrupted"

            def _all_bound_runs_non_cancelable():
                """Only close a stale Track when every durable sibling is settled.

                The requested id can be a terminal projection while the other
                projection on the same Track still owns a live worker.  Read
                both durable ids inside this transaction before allowing the
                no-op path; an unknown sibling is treated as a strict conflict
                rather than as proof that cancellation is unnecessary.
                """
                if screening is None and search is None:
                    return False
                if not all(
                    _run_is_non_cancelable(row)
                    for row in (screening, search)
                    if row is not None
                ):
                    return False
                if track is None:
                    return True
                sibling_ids = {
                    str(track["scrape_run_id"] or "").strip(),
                    str(track["screen_run_id"] or "").strip(),
                }
                sibling_ids.discard("")
                for sibling_id in sibling_ids:
                    if sibling_id == task_run_id:
                        sibling_rows = (screening, search)
                    else:
                        sibling_rows = (
                            conn.execute(
                                "SELECT * FROM screening_runs WHERE id = ?",
                                (sibling_id,),
                            ).fetchone(),
                            conn.execute(
                                "SELECT * FROM search_runs WHERE id = ?",
                                (sibling_id,),
                            ).fetchone(),
                        )
                    if (
                        sibling_rows[0] is None
                        and sibling_rows[1] is None
                    ) or not all(
                        _run_is_non_cancelable(row)
                        for row in sibling_rows
                        if row is not None
                    ):
                        return False
                return True

            def _not_required_result():
                # A recoverable legacy failure may leave its Flow Track in
                # ``paused`` while the bound Run is already durably failed.
                # There is no worker left to cancel, but leaving that Track
                # active would keep the whole Flow as an occupant and block
                # the user's new-round reset.  Close only the stale active
                # Track; terminal Run facts and their diagnostics stay intact.
                if (
                    sync_track and track is not None
                    and str(track["status"] or "") in {
                        "queued", "running", "paused", "interrupted",
                    }
                    and str(track["status"] or "") != desired_track_status
                ):
                    conn.execute(
                        "UPDATE flow_tracks SET status = ?, updated_at = ? "
                        "WHERE flow_id = ? AND id = ?",
                        (
                            desired_track_status,
                            timestamp,
                            str(track["flow_id"]),
                            str(track["id"]),
                        ),
                    )
                    conn.execute(
                        "UPDATE flows SET updated_at = ? WHERE id = ?",
                        (timestamp, str(track["flow_id"])),
                    )
                row = screening or search
                result = (
                    self._screening_run_row(row)
                    if screening is not None
                    else self._run_row(row)
                )
                result["_cancel_outcome"] = "not_required"
                return result

            # Cancellation is a command for a live worker, not a rewrite of a
            # durable terminal fact.  Refresh/reset can legitimately present
            # a stale historical ID without a local snapshot; acknowledge the
            # no-op explicitly so callers can clear their local round.  Any
            # mixed projection (one live row plus one terminal row) remains a
            # strict conflict and still fails below instead of hiding a real
            # persistence problem.
            if _all_bound_runs_non_cancelable():
                return _not_required_result()

            _check_run(screening, "screening")
            _check_run(search, "search")
            if track is not None:
                current_track_status = str(track["status"] or "")
                if current_track_status in {"done", "succeeded", "failed"}:
                    raise ValueError("Flow Track is already terminal")
                if current_track_status == "stopped" and current_track_status != desired_track_status:
                    raise ValueError("Flow Track is already terminal")
                if current_track_status == "cancelled" and desired_track_status != "cancelled":
                    raise ValueError("Flow Track is already terminal")
                if current_track_status not in {
                    "queued", "running", "paused", "interrupted", "cancelled",
                }:
                    raise ValueError("Flow Track has unsupported status")

            if screening is not None and str(screening["status"] or "") != "interrupted":
                conn.execute(
                    "UPDATE screening_runs SET status = 'interrupted', "
                    "error_code = 'user_cancelled', error_reason = ?, "
                    "interruption_kind = 'user_cancelled', updated_at = ? "
                    "WHERE id = ?",
                    ("用户已取消", timestamp, task_run_id),
                )
            if search is not None and str(search["status"] or "") != "interrupted":
                conn.execute(
                    "UPDATE search_runs SET status = 'interrupted', "
                    "error_code = 'user_cancelled', updated_at = ? WHERE id = ?",
                    (timestamp, task_run_id),
                )
            if (
                sync_track and track is not None
                and str(track["status"] or "") != desired_track_status
            ):
                conn.execute(
                    "UPDATE flow_tracks SET status = ?, "
                    "stage = COALESCE(stage, 'scrape'), "
                    "error_code = 'user_cancelled', "
                    "reason = ?, updated_at = ? WHERE flow_id = ? AND id = ?",
                    (
                        desired_track_status,
                        "用户已取消",
                        timestamp,
                        str(track["flow_id"]),
                        str(track["id"]),
                    ),
                )
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ?",
                    (timestamp, str(track["flow_id"])),
                )

            if screening is not None:
                row = conn.execute(
                    "SELECT * FROM screening_runs WHERE id = ?", (task_run_id,)
                ).fetchone()
                return self._screening_run_row(row)
            row = conn.execute(
                "SELECT * FROM search_runs WHERE id = ?", (task_run_id,)
            ).fetchone()
            return self._run_row(row)
