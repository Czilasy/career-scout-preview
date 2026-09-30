"""Legacy run ownership checks and read-only Flow projections."""

from __future__ import annotations

import json

from webui.store_helpers import _now
from webui.store_constants import MAX_DETAIL_BUDGET
from webui.store_flow_core import _FLOW_PLATFORM_SET, FlowStoreSupportMixin


class StoreFlowLegacyMixin:
    def _assert_flow_run(
            self, conn, table, run_id, profile_id, platform, *,
            check_platform, flow_id=None, track_id=None, require_track=False,
        ):
            if run_id is None:
                return
            if table == "search_runs":
                columns = "profile_id, profile_snapshot_json"
            else:
                columns = "profile_id, execution_params_json"
            if check_platform:
                columns += ", platform"
            row = conn.execute(
                f"SELECT {columns} FROM {table} WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            # The scrape checkpoint table historically points at screening_runs,
            # while newer search-run callers use search_runs.  Accept either
            # persisted owner without weakening profile/platform checks.
            if row is None and table == "search_runs":
                row = conn.execute(
                    "SELECT profile_id, execution_params_json"
                    + (", platform" if check_platform else "")
                    + " FROM screening_runs WHERE id = ?",
                    (str(run_id),),
                ).fetchone()
            if row is None and table == "screening_runs":
                # New Flow scrape bindings have a real search_runs FK while the
                # browser checkpoint/result tables still use the same run id.
                # Read the immutable profile snapshot as the compatibility
                # execution-parameter source without accepting an unowned run.
                row = conn.execute(
                    "SELECT profile_id, profile_snapshot_json AS execution_params_json, "
                    "'' AS platform FROM search_runs WHERE id = ?",
                    (str(run_id),),
                ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if flow_id and (
                row["profile_id"] is None
                or str(row["profile_id"]) != str(profile_id)
            ):
                raise ValueError("run belongs to a different profile")
            if not flow_id and row["profile_id"] is not None and str(row["profile_id"]) != str(profile_id):
                raise ValueError("run belongs to a different profile")
            params = {}
            if table == "search_runs":
                raw = row["profile_snapshot_json"] if "profile_snapshot_json" in row.keys() else "{}"
            else:
                raw = row["execution_params_json"] if "execution_params_json" in row.keys() else "{}"
            try:
                decoded = json.loads(raw or "{}")
                params = decoded if isinstance(decoded, dict) else {}
            except (TypeError, ValueError):
                params = {}
            stored_platform = str(
                row["platform"] if "platform" in row.keys() else ""
            ).strip().lower()
            if not stored_platform:
                stored_platform = str(params.get("platform") or "").strip().lower()
            if check_platform and stored_platform != str(platform).strip().lower():
                raise ValueError("run belongs to a different platform")
            if flow_id:
                try:
                    params = params if isinstance(params, dict) else {}
                except Exception:
                    params = {}
                bound_flow_id = str(params.get("flow_id") or "").strip()
                if bound_flow_id != str(flow_id):
                    raise ValueError("run belongs to a different Flow")
                bound_track_id = str(params.get("track_id") or "").strip()
                if require_track and not str(track_id or "").strip():
                    raise ValueError("run is missing its Flow Track")
                if bound_track_id and bound_track_id != str(track_id or ""):
                    raise ValueError("run belongs to a different Flow Track")
                if require_track and bound_track_id != str(track_id or "").strip():
                    raise ValueError("run belongs to a different Flow Track")

    @staticmethod
    def _ensure_legacy_scrape_search_row(
            conn, run_id, *, flow_id, profile_id, platform, track_id=None,
        ):
            """Materialize the search FK for v1 tracks that stored scrape in screen."""
            if not run_id:
                return
            existing = conn.execute(
                "SELECT 1 FROM search_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
            if existing is not None:
                return
            legacy = conn.execute(
                "SELECT profile_id, execution_params_json, status, created_at, updated_at "
                "FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            if legacy is None:
                return
            try:
                params = json.loads(legacy["execution_params_json"] or "{}")
            except (TypeError, ValueError):
                params = {}
            if not isinstance(params, dict):
                params = {}
            bound_flow_id = str(params.get("flow_id") or "").strip()
            if bound_flow_id and bound_flow_id != str(flow_id):
                raise ValueError("legacy scrape run belongs to a different Flow")
            stored_platform = str(params.get("platform") or "").strip().lower()
            if stored_platform and stored_platform != str(platform).strip().lower():
                raise ValueError("legacy scrape run belongs to a different platform")
            if legacy["profile_id"] is not None and str(legacy["profile_id"]) != str(profile_id):
                raise ValueError("legacy scrape run belongs to a different profile")
            params.setdefault("platform", platform)
            params.setdefault("flow_id", str(flow_id))
            if track_id:
                params.setdefault("track_id", str(track_id))
            timestamp = legacy["updated_at"] or legacy["created_at"] or _now()
            conn.execute(
                "INSERT OR IGNORE INTO search_runs "
                "(id, profile_id, profile_snapshot_json, mode, status, "
                "total_detail_budget, discovered_count, completed_jd_count, "
                "created_at, updated_at, error_code) "
                "VALUES (?, ?, ?, 'scrape', 'queued', ?, 0, 0, ?, ?, NULL)",
                (
                    str(run_id),
                    str(legacy["profile_id"] or profile_id),
                    json.dumps(params, ensure_ascii=False, sort_keys=True),
                    int(MAX_DETAIL_BUDGET),
                    legacy["created_at"] or timestamp,
                    timestamp,
                ),
            )

    @staticmethod
    def _legacy_flow_row(conn, flow_id, *, profile_id=None):
            sql = (
                "SELECT * FROM screening_runs WHERE id = ? "
                "AND record_kind = 'result_snapshot'"
            )
            params = [flow_id]
            if profile_id is not None:
                sql += " AND profile_id = ?"
                params.append(str(profile_id))
            row = conn.execute(sql, params).fetchone()
            return StoreFlowLegacyMixin._legacy_flow_from_screening_row(row) if row else None

    @staticmethod
    def _legacy_flow_from_screening_row(row) -> dict:
            platform = str(row["platform"] or "boss")
            if platform not in _FLOW_PLATFORM_SET:
                platform = "boss"
            filter_json = row["filter_snapshot_json"] or row["frozen_filters_json"] or "{}"
            try:
                filters = json.loads(filter_json)
            except (TypeError, ValueError):
                filters = {}
            if not isinstance(filters, dict):
                filters = {}
            status = str(row["status"] or "done")
            if status == "succeeded":
                status = "done"
            track = {
                "id": f"legacy:{row['id']}:{platform}",
                "flow_id": row["id"],
                "platform": platform,
                "scrape_run_id": None,
                "screen_run_id": row["id"],
                "result_run_id": row["id"],
                "confirmed_filters_snapshot": filters,
                "status": status,
                "stage": "complete" if status == "done" else str(row["current_stage"] or "screen"),
                "error_code": row["error_code"],
                "reason": row["error_reason"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            return {
                "id": row["id"],
                "profile_id": row["profile_id"],
                "selection": platform,
                "start_key": None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "status": FlowStoreSupportMixin._derive_flow_status([track]),
                "legacy": True,
                "tracks": [track],
            }
