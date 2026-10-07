"""047 US2：failed 单轨 retry 的 CAS-claim 与绑定事务。

每次实际 retry 使用新 run id；旧失败 run/白箱/日志保持原样。claim 事务内
核对精确画像/轨道、当前 failed 版本（expected_track_id + expected_run_id +
expected_updated_at）与当前 Flow 活动门禁；兄弟轨道字段完全不动。新尝试的
execution_params 记录 retry_of_run_id（既有 JSON 列，无迁移）。

阶段分派：
- ``stage="scrape"``：新抓取 run 同时落 screening_runs（执行投影）与
  search_runs（Track 的 scrape_run_id 外键账本）。
- ``stage="ai"``：新 AI run 只落 screening_runs，Track 换 screen_run_id，
  既有 scrape_run_id（来源抓取输入）与 result_run_id（旧结果指针）完全不删。
"""

from __future__ import annotations

import json

from webui.store_flow import FlowConflictError
from webui.store_helpers import _now, _uuid

_RETRY_STAGES = frozenset(("scrape", "ai"))


class StoreFlowRetryMixin:
    """CAS-claim one failed Track and bind its fresh attempt atomically."""

    def claim_flow_track_retry(
        self,
        flow_id,
        platform,
        *,
        profile_id,
        expected_run_id="",
        expected_updated_at=None,
        expected_track_id=None,
        stage="scrape",
        attempt_params=None,
        frozen_filters=None,
        source_count=0,
    ) -> dict:
        flow_id = self._flow_required_id(flow_id, "flow_id")
        platform = self._flow_platform(platform)
        profile_id = self._flow_required_id(profile_id, "profile_id")
        expected_run_id = str(expected_run_id or "").strip()
        expected_track_id = str(expected_track_id or "").strip()
        stage = str(stage or "scrape").strip().lower()
        if stage not in _RETRY_STAGES:
            raise ValueError("retry stage must be scrape or ai")

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_recovery_writes_allowed(conn)
            flow = conn.execute(
                "SELECT * FROM flows WHERE id = ?", (flow_id,),
            ).fetchone()
            if flow is None or str(flow["profile_id"]) != profile_id:
                raise KeyError(flow_id)
            track = conn.execute(
                "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
                (flow_id, platform),
            ).fetchone()
            if track is None:
                raise KeyError(f"{flow_id}:{platform}")
            if expected_track_id and str(track["id"] or "") != expected_track_id:
                raise FlowConflictError("平台运行线已变化，请刷新后重试")
            if str(track["status"] or "") != "failed":
                raise FlowConflictError("只有失败的平台运行线可以单独重试")
            if expected_updated_at is not None:
                if str(track["updated_at"] or "") != str(expected_updated_at):
                    raise FlowConflictError("平台运行线状态已变化，请刷新后重试")
            if stage == "ai":
                current_run_id = str(track["screen_run_id"] or "").strip()
            else:
                current_run_id = str(
                    track["screen_run_id"] or track["scrape_run_id"] or ""
                ).strip()
            if current_run_id != expected_run_id:
                raise FlowConflictError("任务阶段已变化，请刷新后操作当前任务")
            # 同一画像的当前 Flow 门禁：不能让历史旧 Flow 抢走当前页面。
            newest = conn.execute(
                "SELECT id FROM flows WHERE profile_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
            if newest is not None and str(newest["id"]) != flow_id:
                raise FlowConflictError("该流程已不是当前流程，无法重试")

            timestamp = _now()
            new_run_id = _uuid()
            resolved_params = dict(attempt_params or {})
            resolved_params["retry_of_run_id"] = current_run_id
            resolved_params.setdefault("platform", platform)
            resolved_params.setdefault("flow_id", flow_id)
            resolved_params.setdefault("track_id", str(track["id"]))
            resolved_params.setdefault("profile_id", profile_id)
            params_json = json.dumps(resolved_params, ensure_ascii=False)
            initial_status = "running" if stage == "ai" else "queued"
            # 新执行记录严格 INSERT 新 id（禁止 REPLACE 旧 attempt）。
            conn.execute(
                "INSERT INTO screening_runs "
                "(id, platform, frozen_filters_json, status, source_count, match_count, "
                "mismatch_count, created_at, updated_at, started_at, error_code, resume_id, "
                "pending_count, processed_count, source_cursor, parse_failure_count, "
                "parse_failures_json, profile_id, execution_params_json, record_kind) "
                "VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, NULL, NULL, 0, 0, 0, 0, '{}', ?, ?, "
                "'process_log')",
                (
                    new_run_id,
                    platform,
                    json.dumps(frozen_filters or {}, ensure_ascii=False),
                    initial_status,
                    int(source_count or 0),
                    timestamp,
                    timestamp,
                    timestamp,
                    profile_id,
                    params_json,
                ),
            )
            if stage == "scrape":
                # Track 的抓取绑定走 search_runs（Flow FK 指向它）；同事务建立，
                # 保证「新尝试与当前绑定」一起可见或一起不可见。
                snapshot = {
                    "platform": platform,
                    "flow_id": flow_id,
                    "track_id": str(track["id"]),
                    "retry_of_run_id": current_run_id,
                }
                conn.execute(
                    "INSERT OR IGNORE INTO search_runs "
                    "(id, profile_id, profile_snapshot_json, mode, status, "
                    "total_detail_budget, discovered_count, completed_jd_count, "
                    "created_at, updated_at, error_code) "
                    "VALUES (?, ?, ?, 'scrape', 'queued', 60, 0, 0, ?, ?, NULL)",
                    (
                        new_run_id, profile_id,
                        json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                        timestamp, timestamp,
                    ),
                )
                updated = conn.execute(
                    "UPDATE flow_tracks SET scrape_run_id = ?, status = 'running', "
                    "stage = 'scrape', error_code = NULL, reason = NULL, updated_at = ? "
                    "WHERE flow_id = ? AND platform = ? AND status = 'failed' "
                    "AND COALESCE(screen_run_id, scrape_run_id, '') = ? AND updated_at = ?",
                    (
                        new_run_id, timestamp, flow_id, platform, expected_run_id,
                        str(track["updated_at"] or ""),
                    ),
                )
            else:
                # AI retry：只换 screen_run_id；来源抓取绑定与旧结果指针保留，
                # 旧失败 AI 记录（含白箱/日志）原样可追踪。
                updated = conn.execute(
                    "UPDATE flow_tracks SET screen_run_id = ?, status = 'running', "
                    "stage = 'ai', error_code = NULL, reason = NULL, updated_at = ? "
                    "WHERE flow_id = ? AND platform = ? AND status = 'failed' "
                    "AND COALESCE(screen_run_id, '') = ? AND updated_at = ?",
                    (
                        new_run_id, timestamp, flow_id, platform, expected_run_id,
                        str(track["updated_at"] or ""),
                    ),
                )
            if updated.rowcount != 1:
                raise FlowConflictError("平台运行线状态已变化，请刷新后重试")
            conn.execute(
                "UPDATE flows SET updated_at = ? WHERE id = ?", (timestamp, flow_id),
            )
            return {
                "claimed": True,
                "flow_id": flow_id,
                "platform": platform,
                "track_id": str(track["id"]),
                "new_run_id": new_run_id,
                "retry_of_run_id": current_run_id,
                "stage": stage,
            }


__all__ = ["StoreFlowRetryMixin"]