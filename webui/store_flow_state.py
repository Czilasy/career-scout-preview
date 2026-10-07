"""Atomic durable state closure for B096 Flow-owned run failures.

047 结构前置：取消与失败/收口原子域分别搬到 store_flow_cancel、
store_flow_failure；本 mixin 继续组装它们并保留 finish/claim/restore 原有公开入口。
"""

from __future__ import annotations

import json

from webui.store_constants import DiscoveryStoreConflictError, RUN_TRANSITIONS
from webui.store_flow_core import _TERMINAL_TRACK_STATUSES
from webui.store_flow_cancel import StoreFlowCancelMixin
from webui.store_flow_failure import StoreFlowFailureMixin
from webui.store_helpers import _now


class StoreFlowStateMixin(StoreFlowCancelMixin, StoreFlowFailureMixin):
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
