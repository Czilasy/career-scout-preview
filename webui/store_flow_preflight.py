from __future__ import annotations

from webui.store_helpers import _now
class StoreFlowPreflightMixin:
    def record_flow_track_preflight_failure(
            self,
            flow_id,
            platform,
            *,
            profile_id=None,
            status="failed",
            error_code="track_submit_failed",
            reason="",
        ) -> tuple[dict, bool]:
            """Atomically close only a Track that has not been claimed.

            A preflight response can race with a second execute-search request.
            The conditional update therefore owns the decision: once a scrape,
            AI, or result run is present, the response is observational and
            cannot overwrite the running/claimed Track.
            """
            flow_id = self._flow_required_id(flow_id, "flow_id")
            platform = self._flow_platform(platform)
            status = self._flow_status(status)
            if status not in {"paused", "failed"}:
                raise ValueError("preflight failure must be paused or failed")
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                flow = conn.execute(
                    "SELECT * FROM flows WHERE id = ?", (flow_id,)
                ).fetchone()
                if flow is None or (
                    profile_id is not None
                    and str(flow["profile_id"]) != str(profile_id)
                ):
                    raise KeyError(flow_id)
                track = conn.execute(
                    "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                    (flow_id, platform),
                ).fetchone()
                if track is None:
                    raise KeyError(f"{flow_id}:{platform}")
                updated = conn.execute(
                    "UPDATE flow_tracks SET status = ?, stage = 'scrape', "
                    "error_code = ?, reason = ?, updated_at = ? "
                    "WHERE flow_id = ? AND platform = ? "
                    "AND status IN ('queued', 'paused', 'interrupted') "
                    "AND scrape_run_id IS NULL AND screen_run_id IS NULL "
                    "AND result_run_id IS NULL",
                    (
                        status,
                        str(error_code or "track_submit_failed"),
                        str(reason or ""),
                        _now(),
                        flow_id,
                        platform,
                    ),
                )
                if updated.rowcount != 1:
                    return self._flow_from_id(conn, flow_id), False
                timestamp = _now()
                conn.execute(
                    "UPDATE flows SET updated_at = ? WHERE id = ?",
                    (timestamp, flow_id),
                )
                return self._flow_from_id(conn, flow_id), True
