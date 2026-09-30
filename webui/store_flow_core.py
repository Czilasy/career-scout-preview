"""Shared helpers and vocabulary for B096 Flow/Track persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping

from webui.store_helpers import _decode_json, _now, _uuid
from webui.store_constants import MAX_DETAIL_BUDGET


FLOW_PLATFORMS = ("boss", "zhilian")
_FLOW_PLATFORM_SET = frozenset(FLOW_PLATFORMS)
_FLOW_SELECTIONS = frozenset(("all", *FLOW_PLATFORMS))
_ACTIVE_TRACK_STATUSES = frozenset(("queued", "running", "paused", "interrupted"))
_TERMINAL_TRACK_STATUSES = frozenset(("done", "succeeded", "failed", "stopped", "cancelled"))
_TRACK_STATUS_TRANSITIONS = {
    "queued": frozenset(("queued", "running", "paused", "done", "succeeded", "failed", "stopped", "cancelled")),
    "running": frozenset(("running", "paused", "done", "succeeded", "failed", "stopped", "cancelled")),
    "paused": frozenset(("paused", "running", "failed", "stopped", "cancelled")),
    "interrupted": frozenset(("interrupted", "running", "failed", "stopped", "cancelled")),
    "done": frozenset(("done",)),
    "succeeded": frozenset(("succeeded",)),
    "failed": frozenset(("failed",)),
    "stopped": frozenset(("stopped",)),
    "cancelled": frozenset(("cancelled",)),
}


class FlowConflictError(ValueError):
    """A new Flow conflicts with an active Flow or an idempotency key."""


class FlowStoreSupportMixin:
    @staticmethod
    def _flow_from_id(conn, flow_id):
            row = conn.execute("SELECT * FROM flows WHERE id = ?", (flow_id,)).fetchone()
            if row is None:
                raise KeyError(flow_id)
            # This helper is called only while the caller's transaction is open.
            return FlowStoreSupportMixin._flow_from_row(conn, row)

    @staticmethod
    def _flow_from_row(conn, row) -> dict:
            tracks = [
                FlowStoreSupportMixin._track_from_row(track)
                for track in conn.execute(
                    "SELECT * FROM flow_tracks WHERE flow_id = ? "
                    "ORDER BY CASE platform WHEN 'boss' THEN 0 ELSE 1 END",
                    (row["id"],),
                ).fetchall()
            ]
            return {
                "id": row["id"],
                "profile_id": row["profile_id"],
                "selection": row["selection"],
                "start_key": row["start_key"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "status": FlowStoreSupportMixin._derive_flow_status(tracks),
                "legacy": False,
                "tracks": tracks,
            }

    @staticmethod
    def _track_from_row(row) -> dict:
            try:
                filters = json.loads(row["confirmed_filters_snapshot"] or "{}")
            except (TypeError, ValueError) as exc:
                raise ValueError("flow track filter snapshot is invalid") from exc
            if not isinstance(filters, dict):
                raise ValueError("flow track filter snapshot must be an object")
            return {
                "id": row["id"],
                "flow_id": row["flow_id"],
                "platform": row["platform"],
                "scrape_run_id": row["scrape_run_id"],
                "screen_run_id": row["screen_run_id"],
                "result_run_id": row["result_run_id"],
                "confirmed_filters_snapshot": filters,
                "submission_snapshot": _decode_json(
                    row["submission_snapshot_json"]
                    if "submission_snapshot_json" in row.keys() else "{}",
                    {},
                ),
                "status": row["status"],
                "stage": row["stage"],
                "error_code": row["error_code"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _derive_flow_status(tracks) -> str:
            statuses = {track["status"] for track in tracks}
            if not statuses:
                return "empty"
            if "running" in statuses:
                return "running"
            if "paused" in statuses:
                return "paused"
            # 「排队中」只由真正尚未开始的轨道产生：interrupted 是没有活体工人的
            # 终态倾向状态，冒充成 queued 会让界面把死胡同说成「即将开始」，
            # 并把新一轮入口锁死。
            if "queued" in statuses:
                return "queued"
            if "interrupted" in statuses:
                return "interrupted"
            if statuses & {"failed"}:
                return "failed"
            if statuses & {"stopped", "cancelled"}:
                return "stopped"
            if statuses <= {"done", "succeeded"}:
                return "done"
            return "unknown"

    @staticmethod
    def _flow_filters(confirmed_filters, platforms) -> dict:
            if confirmed_filters is None:
                confirmed_filters = {}
            if not isinstance(confirmed_filters, Mapping):
                raise ValueError("confirmed_filters must be an object")
            unknown = set(confirmed_filters) - _FLOW_PLATFORM_SET
            if unknown:
                raise ValueError("confirmed_filters contains an unsupported platform")
            filters = {}
            for platform in platforms:
                value = confirmed_filters.get(platform, {})
                if not isinstance(value, Mapping):
                    raise ValueError(f"confirmed filters for {platform} must be an object")
                filters[platform] = dict(value)
            return filters

    @staticmethod
    def _find_flow_by_start_key(conn, profile_id, start_key):
            if not start_key:
                return None
            return conn.execute(
                "SELECT * FROM flows WHERE profile_id = ? AND start_key = ?",
                (profile_id, start_key),
            ).fetchone()

    def _find_flow_by_start_key_outside_transaction(self, profile_id, start_key):
            with self._connection() as conn:
                row = self._find_flow_by_start_key(conn, profile_id, start_key)
                return self._flow_from_row(conn, row) if row is not None else None

    @staticmethod
    def _flow_required_id(value, name):
            value = str(value or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            return value

    @staticmethod
    def _flow_optional_text(value):
            if value is None:
                return None
            value = str(value).strip()
            return value or None

    @staticmethod
    def _flow_selection(value):
            value = str(value or "").strip().lower()
            if value not in _FLOW_SELECTIONS:
                raise ValueError("selection must be all, boss, or zhilian")
            return value

    @staticmethod
    def _flow_platform(value):
            value = str(value or "").strip().lower()
            if value not in _FLOW_PLATFORM_SET:
                raise ValueError("platform must be boss or zhilian")
            return value

    @staticmethod
    def _flow_status(value):
            value = str(value or "").strip().lower()
            allowed = _ACTIVE_TRACK_STATUSES | _TERMINAL_TRACK_STATUSES
            if value not in allowed:
                raise ValueError(f"unsupported Flow Track status: {value}")
            return value
