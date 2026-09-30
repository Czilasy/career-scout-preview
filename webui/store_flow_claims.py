"""Atomic B096 Flow/Track creation, claims and transitions."""

from __future__ import annotations

import json
import sqlite3

from webui.store_helpers import _decode_json, _now, _uuid
from webui.store_flow_core import (
    _ACTIVE_TRACK_STATUSES, _FLOW_PLATFORM_SET, _TERMINAL_TRACK_STATUSES,
    _TRACK_STATUS_TRANSITIONS, FlowConflictError,
)


class StoreFlowClaimsMixin:
    def create_flow(
            self,
            *,
            profile_id,
            selection,
            confirmed_filters=None,
            start_key=None,
        ) -> dict:
            """Create one Flow and its platform Tracks in one SQLite transaction.

            ``start_key`` is scoped to ``profile_id``.  Retrying the same key with
            the same selection returns the existing Flow, including while it is
            active; a different profile may use the same key independently.
            """
            profile_id = self._flow_required_id(profile_id, "profile_id")
            selection = self._flow_selection(selection)
            platforms = ("boss", "zhilian") if selection == "all" else (selection,)
            filters = self._flow_filters(confirmed_filters, platforms)
            start_key = self._flow_optional_text(start_key)

            try:
                with self._connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    profile = conn.execute(
                        "SELECT id FROM candidate_profiles WHERE id = ?", (profile_id,)
                    ).fetchone()
                    if profile is None:
                        raise KeyError(profile_id)

                    existing = self._find_flow_by_start_key(conn, profile_id, start_key)
                    if existing is not None:
                        existing_platforms = tuple(
                            row["platform"]
                            for row in conn.execute(
                                "SELECT platform FROM flow_tracks WHERE flow_id = ? "
                                "ORDER BY CASE platform WHEN 'boss' THEN 0 ELSE 1 END",
                                (existing["id"],),
                            )
                        )
                        if existing["selection"] != selection or existing_platforms != platforms:
                            raise FlowConflictError(
                                "start_key is already bound to a different Flow selection"
                            )
                        return self._flow_from_row(conn, existing)

                    active = conn.execute(
                        "SELECT 1 FROM flow_tracks ft "
                        "JOIN flows f ON f.id = ft.flow_id "
                        "WHERE f.profile_id = ? AND ft.status IN (?, ?, ?, ?) LIMIT 1",
                        (profile_id, *_ACTIVE_TRACK_STATUSES),
                    ).fetchone()
                    if active is not None:
                        raise FlowConflictError(
                            "an active Flow already exists for this profile"
                        )

                    timestamp = _now()
                    flow_id = _uuid()
                    conn.execute(
                        "INSERT INTO flows "
                        "(id, profile_id, selection, start_key, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (flow_id, profile_id, selection, start_key, timestamp, timestamp),
                    )
                    for platform in platforms:
                        conn.execute(
                            "INSERT INTO flow_tracks "
                            "(id, flow_id, platform, confirmed_filters_snapshot, status, stage, "
                            "created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', 'pending', ?, ?)",
                            (
                                _uuid(),
                                flow_id,
                                platform,
                                json.dumps(filters[platform], ensure_ascii=False, sort_keys=True),
                                timestamp,
                                timestamp,
                            ),
                        )
                    return self._flow_from_id(conn, flow_id)
            except sqlite3.IntegrityError:
                # Another process may have won the scoped start-key race between
                # the lookup and INSERT.  Re-read only the idempotency case.
                if start_key:
                    existing = self._find_flow_by_start_key_outside_transaction(
                        profile_id, start_key
                    )
                    if existing is not None and existing["selection"] == selection:
                        return existing
                raise

    def get_flow(self, flow_id, *, profile_id=None) -> dict:
            flow_id = self._flow_required_id(flow_id, "flow_id")
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT * FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if row is not None:
                    if profile_id is not None and str(row["profile_id"]) != str(profile_id):
                        raise KeyError(flow_id)
                    return self._flow_from_row(conn, row)

                # Legacy result rows have no Flow row.  Keep their original run ID
                # as the virtual Flow ID so old history links remain addressable.
                legacy = self._legacy_flow_row(conn, flow_id, profile_id=profile_id)
                if legacy is None:
                    raise KeyError(flow_id)
                return legacy

    def get_current_flow(self, profile_id) -> dict | None:
            """Return the latest owned Flow, including completed Flows.

            ``/api/flows/current`` is a read boundary for the current page.  It
            must retain a completed Flow so refresh cannot fall back to a global
            latest-result query.  New-round gating is deliberately separate and
            remains ``flow_has_active_tracks`` below.
            """
            profile_id = self._flow_required_id(profile_id, "profile_id")
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT f.* FROM flows f "
                    "WHERE f.profile_id = ? "
                    "ORDER BY f.updated_at DESC, f.created_at DESC LIMIT 1",
                    (profile_id,),
                ).fetchone()
                return self._flow_from_row(conn, row) if row is not None else None

    def flow_has_active_tracks(self, profile_id) -> bool:
            profile_id = self._flow_required_id(profile_id, "profile_id")
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM flows f JOIN flow_tracks ft ON ft.flow_id = f.id "
                    "WHERE f.profile_id = ? AND ft.status IN (?, ?, ?, ?) LIMIT 1",
                    (profile_id, *_ACTIVE_TRACK_STATUSES),
                ).fetchone()
            return row is not None

    def save_flow_track_submission(
        self,
        flow_id,
        platform,
        *,
        profile_id=None,
        snapshot=None,
    ) -> dict:
        """Save a credential-free request snapshot for a preflight retry.

        This is deliberately separate from the confirmed filter snapshot.  It
        contains only the immutable search scope and Flow-owned UI values, so
        a paused Track with no external run can be revalidated through the
        normal execute-search boundary after a refresh.
        """
        flow_id = self._flow_required_id(flow_id, "flow_id")
        platform = self._flow_platform(platform)
        if snapshot is None:
            snapshot = {}
        if not isinstance(snapshot, dict):
            raise ValueError("submission snapshot must be an object")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            flow = conn.execute(
                "SELECT * FROM flows WHERE id = ?", (flow_id,)
            ).fetchone()
            if flow is None or (
                profile_id is not None and str(flow["profile_id"]) != str(profile_id)
            ):
                raise KeyError(flow_id)
            track = conn.execute(
                "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                (flow_id, platform),
            ).fetchone()
            if track is None:
                raise KeyError(f"{flow_id}:{platform}")
            if any(track[key] for key in ("scrape_run_id", "screen_run_id", "result_run_id")):
                raise FlowConflictError("该平台运行线已有任务，不能覆盖搜索快照")
            if str(track["status"] or "") not in {"queued", "paused", "failed"}:
                raise FlowConflictError("该平台运行线已领取，不能覆盖搜索快照")
            incoming_json = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True,
            )
            existing_raw = track["submission_snapshot_json"]
            expected_updated_at = track["updated_at"]
            existing = _decode_json(existing_raw, None)
            if existing is None:
                raise FlowConflictError("已有搜索快照损坏，拒绝覆盖")
            if existing:
                existing_json = json.dumps(
                    existing, ensure_ascii=False, sort_keys=True,
                )
                if existing_json != incoming_json:
                    raise FlowConflictError("搜索快照已冻结，拒绝重放覆盖")
                return self._flow_from_id(conn, flow_id)
            timestamp = _now()
            updated = conn.execute(
                "UPDATE flow_tracks SET submission_snapshot_json = ?, "
                "updated_at = ? WHERE flow_id = ? AND platform = ? "
                "AND status IN ('queued', 'paused', 'failed') "
                "AND scrape_run_id IS NULL AND screen_run_id IS NULL "
                "AND result_run_id IS NULL AND submission_snapshot_json = ? "
                "AND updated_at = ?",
                (
                    incoming_json,
                    timestamp,
                    flow_id,
                    platform,
                    existing_raw,
                    expected_updated_at,
                ),
            )
            if updated.rowcount != 1:
                raise FlowConflictError("平台运行线状态已变化，不能写入搜索快照")
            conn.execute(
                "UPDATE flows SET updated_at = ? WHERE id = ?",
                (timestamp, flow_id),
            )
            return self._flow_from_id(conn, flow_id)

    def claim_flow_track_submission(
            self,
            flow_id,
            platform,
            *,
            profile_id=None,
        ) -> dict:
            """Atomically claim one Track before an external task is submitted.

            The claim is the SQLite transaction boundary for the two-request
            ``create Flow -> execute-search`` path.  A second request observes the
            running claim and cannot create another external task.
            """
            flow_id = self._flow_required_id(flow_id, "flow_id")
            platform = self._flow_platform(platform)
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                flow = conn.execute(
                    "SELECT * FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if flow is None or (
                    profile_id is not None and str(flow["profile_id"]) != str(profile_id)
                ):
                    raise KeyError(flow_id)
                track = conn.execute(
                    "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                    (flow_id, platform),
                ).fetchone()
                if track is None:
                    raise KeyError(f"{flow_id}:{platform}")
                if (
                    track["status"] not in {"queued", "failed", "paused"}
                    or any(track[key] for key in ("scrape_run_id", "screen_run_id", "result_run_id"))
                ):
                    raise FlowConflictError("该平台运行线已提交或正在运行")
                timestamp = _now()
                updated = conn.execute(
                    "UPDATE flow_tracks SET status = 'running', stage = 'scrape', "
                    "updated_at = ? WHERE flow_id = ? AND platform = ? "
                    "AND status IN ('queued', 'failed', 'paused') "
                    "AND scrape_run_id IS NULL AND screen_run_id IS NULL "
                    "AND result_run_id IS NULL",
                    (timestamp, flow_id, platform),
                )
                if updated.rowcount != 1:
                    raise FlowConflictError("该平台运行线已提交或正在运行")
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ?",
                    (timestamp, flow_id),
                )
                return self._flow_from_id(conn, flow_id)

    def stop_flow_track_without_run(
            self,
            flow_id,
            platform,
            *,
            profile_id=None,
    ) -> dict:
        """Atomically stop a queued/paused Track before submission claim.

        A submission claim changes the Track to ``running`` before the worker
        Run is bound.  The stop action must use the same SQLite lock and reject
        that in-between state; otherwise it could publish ``stopped`` while
        the claimant continues creating a worker.
        """
        flow_id = self._flow_required_id(flow_id, "flow_id")
        platform = self._flow_platform(platform)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            flow = conn.execute(
                "SELECT * FROM flows WHERE id = ?", (flow_id,)
            ).fetchone()
            if flow is None or (
                profile_id is not None and str(flow["profile_id"]) != str(profile_id)
            ):
                raise KeyError(flow_id)
            track = conn.execute(
                "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                (flow_id, platform),
            ).fetchone()
            if track is None:
                raise KeyError(f"{flow_id}:{platform}")
            if any(track[key] for key in ("scrape_run_id", "screen_run_id", "result_run_id")):
                raise FlowConflictError("该平台运行线已有任务，不能直接停止")
            current_status = str(track["status"] or "")
            if current_status == "stopped":
                return self._flow_from_id(conn, flow_id)
            if current_status not in {"queued", "paused"}:
                raise FlowConflictError("该平台运行线正在提交，不能直接停止")
            timestamp = _now()
            updated = conn.execute(
                "UPDATE flow_tracks SET status = 'stopped', updated_at = ? "
                "WHERE flow_id = ? AND platform = ? "
                "AND status IN ('queued', 'paused') "
                "AND scrape_run_id IS NULL AND screen_run_id IS NULL "
                "AND result_run_id IS NULL",
                (timestamp, flow_id, platform),
            )
            if updated.rowcount != 1:
                raise FlowConflictError("该平台运行线正在提交，不能直接停止")
            conn.execute(
                "UPDATE flows SET updated_at = ? WHERE id = ?",
                (timestamp, flow_id),
            )
            return self._flow_from_id(conn, flow_id)

    def claim_flow_track_ai(
            self,
            flow_id,
            platform,
            *,
            profile_id=None,
            screen_run_id,
        ) -> dict:
            """Atomically claim and bind one Flow Track's AI run.

            The source run must carry the exact Flow identity in its frozen
            execution parameters.  The legacy scrape-in-screen slot is migrated
            in this same transaction, so two AI submissions cannot both observe
            an empty ``screen_run_id`` and create competing bindings.
            """
            flow_id = self._flow_required_id(flow_id, "flow_id")
            platform = self._flow_platform(platform)
            screen_run_id = self._flow_required_id(screen_run_id, "screen_run_id")
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                flow = conn.execute(
                    "SELECT * FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if flow is None or (
                    profile_id is not None and str(flow["profile_id"]) != str(profile_id)
                ):
                    raise KeyError(flow_id)
                track = conn.execute(
                    "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                    (flow_id, platform),
                ).fetchone()
                if track is None:
                    raise KeyError(f"{flow_id}:{platform}")

                self._assert_flow_run(
                    conn, "screening_runs", screen_run_id,
                    flow["profile_id"], platform,
                    check_platform=True, flow_id=flow_id,
                    track_id=track["id"], require_track=True,
                )
                if track["result_run_id"]:
                    raise FlowConflictError("该平台运行线已完成 AI 筛选")
                current_status = str(track["status"] or "")
                if current_status in _TERMINAL_TRACK_STATUSES:
                    raise FlowConflictError("已结束的平台运行线不能再次启动 AI")
                if current_status not in {"queued", "running", "paused", "interrupted"}:
                    raise FlowConflictError("该平台运行线当前不能启动 AI")

                legacy_scrape_id = None
                if (
                    track["stage"] == "scrape"
                    and track["screen_run_id"]
                    and not track["scrape_run_id"]
                ):
                    legacy_scrape_id = str(track["screen_run_id"])
                    self._ensure_legacy_scrape_search_row(
                        conn, legacy_scrape_id, flow_id=flow_id,
                        profile_id=flow["profile_id"], platform=platform,
                        track_id=track["id"],
                    )
                    self._assert_flow_run(
                        conn, "search_runs", legacy_scrape_id,
                        flow["profile_id"], platform,
                        check_platform=False, flow_id=flow_id,
                        track_id=track["id"],
                    )
                elif track["screen_run_id"]:
                    if str(track["screen_run_id"]) == screen_run_id:
                        return self._flow_from_id(conn, flow_id)
                    raise FlowConflictError("该平台运行线已有 AI 任务")

                timestamp = _now()
                updated = conn.execute(
                    "UPDATE flow_tracks SET scrape_run_id = COALESCE(?, scrape_run_id), "
                    "screen_run_id = ?, status = 'running', stage = 'ai', "
                    "updated_at = ? WHERE flow_id = ? AND platform = ? "
                    "AND (screen_run_id IS NULL OR (stage = 'scrape' AND scrape_run_id IS NULL)) "
                    "AND result_run_id IS NULL "
                    "AND status IN ('queued', 'running', 'paused', 'interrupted')",
                    (
                        legacy_scrape_id,
                        screen_run_id,
                        timestamp,
                        flow_id,
                        platform,
                    ),
                )
                if updated.rowcount != 1:
                    raise FlowConflictError("该平台运行线已被其他 AI 请求领取")
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ?",
                    (timestamp, flow_id),
                )
                return self._flow_from_id(conn, flow_id)

    def delete_unbound_flow_ai_run(self, run_id, *, flow_id) -> bool:
            """Remove a newly-created, unbound AI run after a failed claim.

            Only a process-log run carrying the caller's exact Flow identity is
            eligible.  Existing result snapshots or runs already referenced by a
            Track are never deleted.
            """
            run_id = self._flow_required_id(run_id, "run_id")
            flow_id = self._flow_required_id(flow_id, "flow_id")
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT record_kind, execution_params_json FROM screening_runs "
                    "WHERE id = ?", (run_id,)
                ).fetchone()
                if row is None or row["record_kind"] == "result_snapshot":
                    return False
                params = _decode_json(row["execution_params_json"], {})
                if not isinstance(params, dict) or str(params.get("flow_id") or "") != flow_id:
                    return False
                referenced = conn.execute(
                    "SELECT 1 FROM flow_tracks WHERE screen_run_id = ? "
                    "OR result_run_id = ? LIMIT 1",
                    (run_id, run_id),
                ).fetchone()
                if referenced is not None:
                    return False
                deleted = conn.execute(
                    "DELETE FROM screening_runs WHERE id = ?", (run_id,)
                )
                return deleted.rowcount == 1

    def complete_flow_track(
            self,
            flow_id,
            platform,
            *,
            profile_id=None,
            result_run_id,
        ) -> dict:
            """Atomically bind a result snapshot and publish Track completion."""
            flow_id = self._flow_required_id(flow_id, "flow_id")
            platform = self._flow_platform(platform)
            result_run_id = self._flow_required_id(result_run_id, "result_run_id")
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                flow = conn.execute(
                    "SELECT * FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if flow is None or (
                    profile_id is not None and str(flow["profile_id"]) != str(profile_id)
                ):
                    raise KeyError(flow_id)
                track = conn.execute(
                    "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                    (flow_id, platform),
                ).fetchone()
                if track is None:
                    raise KeyError(f"{flow_id}:{platform}")
                if (
                    track["status"] in {"failed", "stopped", "cancelled"}
                    or (
                        track["status"] in {"done", "succeeded"}
                        and str(track["result_run_id"] or "") != result_run_id
                    )
                ):
                    raise FlowConflictError("已结束的平台运行线不能发布新结果")
                self._assert_flow_run(
                    conn, "screening_runs", result_run_id,
                    flow["profile_id"], platform,
                    check_platform=True, flow_id=flow_id,
                    track_id=track["id"],
                )
                row = conn.execute(
                    "SELECT record_kind FROM screening_runs WHERE id = ?",
                    (result_run_id,),
                ).fetchone()
                if row is None or row["record_kind"] != "result_snapshot":
                    raise ValueError("result run is not a result snapshot")
                timestamp = _now()
                conn.execute(
                    "UPDATE flow_tracks SET result_run_id = ?, status = 'done', "
                    "stage = 'complete', updated_at = ? "
                    "WHERE flow_id = ? AND platform = ?",
                    (result_run_id, timestamp, flow_id, platform),
                )
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ?",
                    (timestamp, flow_id),
                )
                return self._flow_from_id(conn, flow_id)

    def list_flows(self, profile_id, *, include_legacy=True) -> list[dict]:
            profile_id = self._flow_required_id(profile_id, "profile_id")
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM flows WHERE profile_id = ? "
                    "ORDER BY created_at DESC, id DESC",
                    (profile_id,),
                ).fetchall()
                flows = [self._flow_from_row(conn, row) for row in rows]
                if include_legacy:
                    legacy_rows = conn.execute(
                        "SELECT * FROM screening_runs WHERE profile_id = ? "
                        "AND record_kind = 'result_snapshot' "
                        "ORDER BY created_at DESC, id DESC",
                        (profile_id,),
                    ).fetchall()
                    real_ids = {flow["id"] for flow in flows}
                    flows.extend(
                        self._legacy_flow_from_screening_row(row)
                        for row in legacy_rows
                        if row["id"] not in real_ids
                    )
            return sorted(
                flows,
                key=lambda flow: (str(flow.get("created_at") or ""), str(flow["id"])),
                reverse=True,
            )

    def update_flow_track(
            self,
            flow_id,
            platform,
            *,
            profile_id=None,
            status=None,
            stage=None,
            error_code=None,
            reason=None,
            scrape_run_id=None,
            screen_run_id=None,
            result_run_id=None,
        ) -> dict:
            """Update one Track without changing any sibling Track."""
            flow_id = self._flow_required_id(flow_id, "flow_id")
            platform = self._flow_platform(platform)
            if status is not None:
                status = self._flow_status(status)
            if stage is not None:
                stage = str(stage)
            if error_code is not None:
                error_code = str(error_code)
            if reason is not None:
                reason = str(reason)

            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                flow = conn.execute(
                    "SELECT * FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if flow is None or (
                    profile_id is not None and str(flow["profile_id"]) != str(profile_id)
                ):
                    raise KeyError(flow_id)
                track = conn.execute(
                    "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                    (flow_id, platform),
                ).fetchone()
                if track is None:
                    raise KeyError(f"{flow_id}:{platform}")

                current_status = str(track["status"] or "")
                if status is not None and status not in _TRACK_STATUS_TRANSITIONS.get(
                    current_status, frozenset()
                ):
                    raise FlowConflictError(
                        f"运行线不能从 {current_status} 转换到 {status}"
                    )
                if result_run_id is not None:
                    target_status = status or current_status
                    if current_status in {"failed", "stopped", "cancelled"}:
                        raise FlowConflictError("已结束的平台运行线不能绑定结果")
                    if target_status not in {"done", "succeeded"}:
                        raise FlowConflictError("结果绑定必须与终态迁移一起提交")
                    if (
                        current_status in {"done", "succeeded"}
                        and track["result_run_id"]
                        and str(track["result_run_id"]) != str(result_run_id)
                    ):
                        raise FlowConflictError("已完成的平台运行线已有其他结果")

                assignments = []
                values = []
                for name, value in (
                    ("status", status),
                    ("stage", stage),
                    ("error_code", error_code),
                    ("reason", reason),
                    ("scrape_run_id", scrape_run_id),
                    ("screen_run_id", screen_run_id),
                    ("result_run_id", result_run_id),
                ):
                    if value is not None:
                        assignments.append(f"{name} = ?")
                        values.append(str(value))
                if assignments:
                    timestamp = _now()
                    assignments.append("updated_at = ?")
                    values.append(timestamp)
                    values.extend((flow_id, platform))
                    conn.execute(
                        "UPDATE flow_tracks SET " + ", ".join(assignments) +
                        " WHERE flow_id = ? AND platform = ?",
                        values,
                    )
                    conn.execute(
                        "UPDATE flows SET updated_at = ? WHERE id = ?",
                        (timestamp, flow_id),
                    )
                return self._flow_from_id(conn, flow_id)

    def bind_flow_track_runs(
            self,
            flow_id,
            platform,
            *,
            profile_id=None,
            scrape_run_id=None,
            screen_run_id=None,
            result_run_id=None,
        ) -> dict:
            """Bind only runs owned by the same profile/platform to one Track."""
            flow_id = self._flow_required_id(flow_id, "flow_id")
            platform = self._flow_platform(platform)
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                flow = conn.execute(
                    "SELECT * FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if flow is None or (
                    profile_id is not None and str(flow["profile_id"]) != str(profile_id)
                ):
                    raise KeyError(flow_id)
                track = conn.execute(
                    "SELECT id FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                    (flow_id, platform),
                ).fetchone()
                if track is None:
                    raise KeyError(f"{flow_id}:{platform}")

                self._ensure_legacy_scrape_search_row(
                    conn, scrape_run_id, flow_id=flow_id,
                    profile_id=flow["profile_id"], platform=platform,
                    track_id=track["id"],
                )
                self._assert_flow_run(
                    conn, "search_runs", scrape_run_id, flow["profile_id"], platform,
                    check_platform=False, flow_id=flow_id,
                    track_id=track["id"],
                )
                self._assert_flow_run(
                    conn, "screening_runs", screen_run_id, flow["profile_id"], platform,
                    check_platform=True, flow_id=flow_id,
                    track_id=track["id"],
                )
                self._assert_flow_run(
                    conn, "screening_runs", result_run_id, flow["profile_id"], platform,
                    check_platform=True, flow_id=flow_id,
                    track_id=track["id"],
                )
                timestamp = _now()
                conn.execute(
                    "UPDATE flow_tracks SET scrape_run_id = COALESCE(?, scrape_run_id), "
                    "screen_run_id = COALESCE(?, screen_run_id), "
                    "result_run_id = COALESCE(?, result_run_id), updated_at = ? "
                    "WHERE flow_id = ? AND platform = ?",
                    (
                        scrape_run_id,
                        screen_run_id,
                        result_run_id,
                        timestamp,
                        flow_id,
                        platform,
                    ),
                )
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ?",
                    (timestamp, flow_id),
                )
                return self._flow_from_id(conn, flow_id)
