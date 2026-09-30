"""Atomic durable state closure for B096 Flow-owned run failures."""

from __future__ import annotations

import json

from webui.store_constants import DiscoveryStoreConflictError, RUN_TRANSITIONS
from webui.store_flow_core import _TERMINAL_TRACK_STATUSES
from webui.store_helpers import _now


class StoreFlowStateMixin:
    """Persist screening/search/Track failure state in one SQLite transaction."""

    def claim_flow_finish(
        self,
        flow_id,
        platform,
        *,
        profile_id,
        task_run_id,
        error_reason="用户提前结束，已保存部分结果",
    ) -> dict:
        """CAS-claim one Flow finish before any result snapshot is written.

        The claim reserves the exact screening Run while the owning Track is
        still active.  A caller that cannot later publish a snapshot can use
        :meth:`restore_flow_finish_claim`; a concurrent terminal Track always
        wins and is never overwritten.
        """
        flow_id = self._flow_required_id(flow_id, "flow_id")
        platform = self._flow_platform(platform)
        profile_id = self._flow_required_id(profile_id, "profile_id")
        task_run_id = self._flow_required_id(task_run_id, "task_run_id")
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
            task = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (task_run_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_run_id)
            self._assert_flow_run(
                conn,
                "screening_runs",
                task_run_id,
                profile_id,
                platform,
                check_platform=True,
                flow_id=flow_id,
                track_id=track["id"],
                require_track=True,
            )

            current_status = str(task["status"] or "")
            interruption_kind = str(task["interruption_kind"] or "")
            track_status = str(track["status"] or "")
            existing_result = str(track["result_run_id"] or "")
            if (
                current_status == "interrupted"
                and task["error_code"] == "user_finished"
                and interruption_kind == "user_finished"
            ):
                if track_status == "stopped" and existing_result:
                    return {
                        "claimed": False,
                        "already_finished": True,
                        "result_run_id": existing_result,
                    }
                if track_status in _TERMINAL_TRACK_STATUSES:
                    raise DiscoveryStoreConflictError("already_terminal")
                # A prior attempt may have signalled the worker but failed
                # before binding its immutable snapshot.  Keep that CAS claim
                # durable and let a retry complete it; restoring the task to
                # active would permit a late worker to race the finish path.
                return {
                    "claimed": True,
                    "already_claimed": True,
                    "finish_pending": True,
                    "flow_id": flow_id,
                    "platform": platform,
                    "profile_id": profile_id,
                    "task_run_id": task_run_id,
                    "track_id": str(track["id"]),
                }
            if current_status in {
                "succeeded", "partial", "failed", "cancelled", "done", "stopped",
            }:
                raise DiscoveryStoreConflictError("already_terminal")
            if (
                current_status == "interrupted"
                and interruption_kind == "user_cancelled"
            ):
                raise DiscoveryStoreConflictError("user_cancelled")
            if (
                current_status == "interrupted"
                and interruption_kind not in {"", "process_restart", "operator_stop"}
            ):
                raise DiscoveryStoreConflictError("interrupted_not_restartable")
            if track_status in _TERMINAL_TRACK_STATUSES:
                raise DiscoveryStoreConflictError("already_terminal")
            prior = {
                name: task[name]
                for name in (
                    "status", "error_code", "error_reason", "interruption_kind",
                    "current_stage", "finished_at", "updated_at",
                )
            }
            conn.execute(
                "UPDATE screening_runs SET status = 'interrupted', "
                "error_code = 'user_finished', error_reason = ?, "
                "interruption_kind = 'user_finished', current_stage = 'done', "
                "finished_at = COALESCE(finished_at, ?), updated_at = ? "
                "WHERE id = ?",
                (
                    str(error_reason or "用户提前结束，已保存部分结果"),
                    timestamp,
                    timestamp,
                    task_run_id,
                ),
            )
            return {
                "claimed": True,
                "already_finished": False,
                "flow_id": flow_id,
                "platform": platform,
                "profile_id": profile_id,
                "task_run_id": task_run_id,
                "track_id": str(track["id"]),
                "prior": prior,
            }

    def restore_flow_finish_claim(self, claim: dict) -> bool:
        """Restore a claimed Flow finish only while its Track stayed active."""
        if (
            not isinstance(claim, dict)
            or not claim.get("claimed")
            or claim.get("finish_pending")
        ):
            return False
        flow_id = self._flow_required_id(claim.get("flow_id"), "flow_id")
        platform = self._flow_platform(claim.get("platform"))
        profile_id = self._flow_required_id(claim.get("profile_id"), "profile_id")
        task_run_id = self._flow_required_id(claim.get("task_run_id"), "task_run_id")
        prior = claim.get("prior") or {}
        timestamp = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            flow = conn.execute(
                "SELECT profile_id FROM flows WHERE id = ?", (flow_id,)
            ).fetchone()
            track = conn.execute(
                "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                (flow_id, platform),
            ).fetchone()
            task = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (task_run_id,)
            ).fetchone()
            if (
                flow is None or str(flow["profile_id"]) != profile_id
                or track is None or task is None
            ):
                return False
            if str(track["status"] or "") in _TERMINAL_TRACK_STATUSES:
                return False
            if not (
                str(task["status"] or "") == "interrupted"
                and str(task["error_code"] or "") == "user_finished"
                and str(task["interruption_kind"] or "") == "user_finished"
            ):
                return False
            conn.execute(
                "UPDATE screening_runs SET status = ?, error_code = ?, "
                "error_reason = ?, interruption_kind = ?, current_stage = ?, "
                "finished_at = ?, updated_at = ? WHERE id = ?",
                (
                    prior.get("status"),
                    prior.get("error_code"),
                    prior.get("error_reason"),
                    prior.get("interruption_kind"),
                    prior.get("current_stage"),
                    prior.get("finished_at"),
                    timestamp,
                    task_run_id,
                ),
            )
            return True

    def discard_unbound_result_snapshot(
        self, result_run_id, *, flow_id=None, track_id=None, profile_id=None,
    ) -> bool:
        """Delete only a result snapshot that no Flow Track references."""
        result_run_id = self._flow_required_id(result_run_id, "result_run_id")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            row = conn.execute(
                "SELECT record_kind, profile_id, execution_params_json "
                "FROM screening_runs WHERE id = ?", (result_run_id,)
            ).fetchone()
            if row is None or row["record_kind"] != "result_snapshot":
                return False
            if profile_id is not None and str(row["profile_id"] or "") != str(profile_id):
                return False
            referenced = conn.execute(
                "SELECT 1 FROM flow_tracks WHERE result_run_id = ? LIMIT 1",
                (result_run_id,),
            ).fetchone()
            if referenced is not None:
                return False
            if flow_id is not None or track_id is not None:
                try:
                    params = json.loads(row["execution_params_json"] or "{}")
                except (TypeError, ValueError):
                    params = {}
                stored_flow_id = str(params.get("flow_id") or "")
                stored_track_id = str(params.get("track_id") or "")
                if stored_flow_id and flow_id is not None and stored_flow_id != str(flow_id):
                    return False
                if stored_track_id and track_id is not None and stored_track_id != str(track_id):
                    return False
            conn.execute("DELETE FROM screening_results WHERE run_id = ?", (result_run_id,))
            conn.execute("DELETE FROM screening_runs WHERE id = ?", (result_run_id,))
            return True

    def cancel_task_atomic(
        self,
        task_run_id,
        *,
        flow_binding=None,
        sync_track=True,
        track_status="cancelled",
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

    def finish_flow_task_atomic(
        self,
        flow_id,
        platform,
        *,
        profile_id,
        task_run_id,
        result_run_id=None,
        error_reason="用户提前结束，已保存部分结果",
    ) -> dict:
        """Publish a user-finished task and its Flow Track together.

        ``save_finished_round`` creates the immutable result snapshot before
        this boundary is entered.  This method is the durable publication
        boundary: a task can be retried safely, the exact snapshot is bound to
        its owning Track, and the Track is moved out of the active gate in the
        same SQLite transaction.  Sibling Tracks are never touched.
        """
        flow_id = self._flow_required_id(flow_id, "flow_id")
        platform = self._flow_platform(platform)
        profile_id = self._flow_required_id(profile_id, "profile_id")
        task_run_id = self._flow_required_id(task_run_id, "task_run_id")
        result_run_id = self._flow_optional_text(result_run_id)
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

            task = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (task_run_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_run_id)
            self._assert_flow_run(
                conn,
                "screening_runs",
                task_run_id,
                profile_id,
                platform,
                check_platform=True,
                flow_id=flow_id,
                track_id=track["id"],
                require_track=True,
            )

            if result_run_id:
                result = conn.execute(
                    "SELECT * FROM screening_runs WHERE id = ?",
                    (result_run_id,),
                ).fetchone()
                if result is None or result["record_kind"] != "result_snapshot":
                    raise ValueError("result run is not a result snapshot")
                self._assert_flow_run(
                    conn,
                    "screening_runs",
                    result_run_id,
                    profile_id,
                    platform,
                    check_platform=True,
                    flow_id=flow_id,
                    track_id=track["id"],
                )
                existing_result = str(track["result_run_id"] or "")
                if existing_result and existing_result != result_run_id:
                    raise DiscoveryStoreConflictError("result_already_bound")

            current_status = str(task["status"] or "")
            interruption_kind = str(task["interruption_kind"] or "")
            already_finished = (
                current_status == "interrupted"
                and task["error_code"] == "user_finished"
                and interruption_kind == "user_finished"
            )
            if not already_finished:
                if current_status in {
                    "succeeded", "partial", "failed", "cancelled", "done", "stopped",
                }:
                    raise DiscoveryStoreConflictError("already_terminal")
                if (
                    current_status == "interrupted"
                    and interruption_kind == "user_cancelled"
                ):
                    raise DiscoveryStoreConflictError("user_cancelled")
                if (
                    current_status == "interrupted"
                    and interruption_kind not in {"", "process_restart", "operator_stop"}
                ):
                    raise DiscoveryStoreConflictError("interrupted_not_restartable")
                conn.execute(
                    "UPDATE screening_runs SET status = 'interrupted', "
                    "error_code = 'user_finished', error_reason = ?, "
                    "interruption_kind = 'user_finished', current_stage = 'done', "
                    "finished_at = COALESCE(finished_at, ?), updated_at = ? "
                    "WHERE id = ?",
                    (str(error_reason or "用户提前结束，已保存部分结果"),
                     timestamp, timestamp, task_run_id),
                )

            existing_track_result = str(track["result_run_id"] or "")
            if result_run_id and existing_track_result not in {"", result_run_id}:
                raise DiscoveryStoreConflictError("result_already_bound")
            track_status = str(track["status"] or "")
            if already_finished and track_status == "stopped":
                # A repeated finish is idempotent only after the same result
                # has been durably published on the same stopped Track.  A
                # half-published task is represented by the CAS claim with an
                # active Track and is completed below after its snapshot is
                # available; a terminal Track with a different result still
                # rejects the late request.
                if result_run_id and existing_track_result != result_run_id:
                    raise DiscoveryStoreConflictError("state_conflict")
            elif track_status in _TERMINAL_TRACK_STATUSES:
                raise DiscoveryStoreConflictError("already_terminal")
            conn.execute(
                "UPDATE flow_tracks SET "
                "result_run_id = COALESCE(?, result_run_id), "
                "status = 'stopped', stage = 'complete', "
                "updated_at = ? WHERE flow_id = ? AND platform = ?",
                (result_run_id, timestamp, flow_id, platform),
            )
            conn.execute(
                "UPDATE flows SET updated_at = ? WHERE id = ?",
                (timestamp, flow_id),
            )
            return self._flow_from_id(conn, flow_id)

    def close_flow_task_state_atomic(
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
            return self._flow_from_id(conn, flow_id)

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
