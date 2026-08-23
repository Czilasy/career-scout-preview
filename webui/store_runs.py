"""搜索/筛选 run 域（021 B2 拆分自 webui/store.py）：search_runs 与
screening_runs 生命周期、CAS 冲突、流程事件、verdict 持久化与断点续筛查询。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import json

from webui.store_helpers import (
    latest_screening_run_for_source,
    _now,
    _uuid,
)
from webui.error_registry import SYSTEMIC_BLOCK_CODES
from webui.store_constants import (
    DiscoveryStoreConflictError,
    MAX_DETAIL_BUDGET,
    RUN_STATUSES,
    RUN_TRANSITIONS,
    _BEGIN_IMMEDIATE,
    _ERROR_CODE_SET_CLAUSE,
    _STATUS_SET_CLAUSE,
    _UPDATED_AT_SET_CLAUSE,
)


class StoreRunsMixin:
    def create_search_run(self, profile_id, profile_snapshot, mode, total_detail_budget=MAX_DETAIL_BUDGET):
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO search_runs (id, profile_id, profile_snapshot_json, mode, status, total_detail_budget, discovered_count, completed_jd_count, created_at, updated_at, error_code) "
                "VALUES (?, ?, ?, ?, 'queued', ?, 0, 0, ?, ?, NULL)",
                (rid, str(profile_id), json.dumps(profile_snapshot, ensure_ascii=False), mode, int(total_detail_budget), ts, ts),
            )
        return self.get_search_run(rid)

    def get_search_run(self, run_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM search_runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_row(row)

    def update_search_run(self, run_id, status=None, discovered_count=None, completed_jd_count=None, error_code=None):
        current = self.get_search_run(run_id)
        if status and status not in RUN_STATUSES:
            raise ValueError(f"未知运行状态: {status}")
        if status and status not in RUN_TRANSITIONS[current["status"]]:
            raise ValueError(f"运行不能从 {current['status']} 转换到 {status}")
        sets = []
        params = []
        if status:
            sets.append(_STATUS_SET_CLAUSE)
            params.append(status)
        if discovered_count is not None:
            sets.append("discovered_count = ?")
            params.append(int(discovered_count))
        if completed_jd_count is not None:
            sets.append("completed_jd_count = ?")
            params.append(int(completed_jd_count))
        if error_code is not None:
            sets.append(_ERROR_CODE_SET_CLAUSE)
            params.append(error_code)
        sets.append(_UPDATED_AT_SET_CLAUSE)
        params.append(_now())
        params.append(str(run_id))
        with self._connection() as conn:
            conn.execute(f"UPDATE search_runs SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_search_run(run_id)

    def list_search_runs(self, profile_id=None, limit=30):
        with self._connection() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT * FROM search_runs WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(profile_id), max(1, int(limit))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM search_runs ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
                ).fetchall()
        return [self._run_row(row) for row in rows]

    def _run_row(self, row) -> dict:
        return {
            "id": row["id"],
            "profile_id": row["profile_id"],
            "profile_snapshot": json.loads(row["profile_snapshot_json"] or "{}"),
            "mode": row["mode"],
            "status": row["status"],
            "total_detail_budget": row["total_detail_budget"],
            "discovered_count": row["discovered_count"],
            "completed_jd_count": row["completed_jd_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "error_code": row["error_code"],
        }

    # -- screening runs（AI 筛选任务持久化：进度落库 + 断点续筛） ----------

    def create_screening_run(self, run_id, *, frozen_filters=None, source_count=0,
                             profile_id=None, execution_params=None,
                             backend_version=None):
        """登记一个 AI 筛选任务（网页两段式筛选）。

        表是 migration_004/007/010 建好的（此前无写入方），本方法是启用入口。
        run_id 直接用任务 id，便于与内存任务/前端轮询对齐。
        """
        execution_params = dict(execution_params or {})
        execution_params.setdefault("correlation_id", str(run_id))
        ts = _now()
        profile_facts = (execution_params or {}).get("profile_facts")
        profile_facts_json = (
            json.dumps(profile_facts, ensure_ascii=False, sort_keys=True)
            if profile_facts is not None else None
        )
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, frozen_filters_json, status, source_count, match_count, mismatch_count, "
                "created_at, updated_at, started_at, error_code, resume_id, pending_count, "
                "processed_count, source_cursor, parse_failure_count, parse_failures_json, "
                "profile_id, execution_params_json, record_kind, backend_version, profile_facts_json) "
                "VALUES (?, ?, ?, 'queued', ?, 0, 0, ?, ?, ?, NULL, NULL, 0, 0, 0, 0, '{}', ?, ?, 'process_log', ?, ?)",
                (
                    str(run_id),
                    str((execution_params or {}).get("platform") or "boss"),
                    json.dumps(frozen_filters or {}, ensure_ascii=False),
                    int(source_count), ts, ts, ts,
                    str(profile_id) if profile_id else None,
                    json.dumps(execution_params or {}, ensure_ascii=False),
                    str(backend_version) if backend_version else None,
                    profile_facts_json,
                ),
            )
        return self.get_screening_run(run_id)

    def get_screening_run(self, run_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        if row is None:
            return None
        return self._screening_run_row(row)

    def update_screening_run(self, run_id, *, status=None, processed_count=None,
                             source_cursor=None, match_count=None, mismatch_count=None,
                             error_code=None, pending_count=None,
                             current_stage=None, error_reason=None,
                             backend_version=None, total_dropped=None,
                             total_kept=None, total_scraped=None,
                             source_count=None):
        """更新 screening_run，含状态机校验（FR-005）。

        状态必须按 RUN_TRANSITIONS 合法路径迁移。非法迁移抛 ValueError。
        新增字段（migration_020）：current_stage / error_reason / backend_version。
        守恒字段（migration_018）：total_dropped / total_kept / total_scraped。
        """
        if status is not None:
            # 向后兼容映射（app.py 历史用 done/cancelled，统一到 RUN_STATUSES）
            _status_aliases = {"done": "succeeded", "cancelled": "interrupted"}
            status = _status_aliases.get(status, status)
            if status not in RUN_STATUSES:
                raise ValueError(f"未知运行状态: {status}")
        sets = []
        params = []
        terminal_success = status in {"succeeded", "partial"}
        if status is not None:
            sets.append(_STATUS_SET_CLAUSE)
            params.append(str(status))
            # A terminal success/partial result is authoritative.  Clear any
            # stale pause/failure detail from an earlier attempt so the UI
            # cannot show an old CDP error alongside the final result.
            if terminal_success:
                sets.extend(["error_code = NULL", "error_reason = NULL"])
        if processed_count is not None:
            sets.append("processed_count = ?")
            params.append(int(processed_count))
        if source_cursor is not None:
            sets.append("source_cursor = ?")
            params.append(int(source_cursor))
        if match_count is not None:
            sets.append("match_count = ?")
            params.append(int(match_count))
        if mismatch_count is not None:
            sets.append("mismatch_count = ?")
            params.append(int(mismatch_count))
        if error_code is not None and not terminal_success:
            sets.append(_ERROR_CODE_SET_CLAUSE)
            params.append(str(error_code))
        if pending_count is not None:
            sets.append("pending_count = ?")
            params.append(int(pending_count))
        if current_stage is not None:
            sets.append("current_stage = ?")
            params.append(str(current_stage))
        if error_reason is not None and not terminal_success:
            sets.append("error_reason = ?")
            params.append(str(error_reason))
        if backend_version is not None:
            sets.append("backend_version = ?")
            params.append(str(backend_version))
        if total_dropped is not None:
            sets.append("total_dropped = ?")
            params.append(int(total_dropped))
        if total_kept is not None:
            sets.append("total_kept = ?")
            params.append(int(total_kept))
        if total_scraped is not None:
            sets.append("total_scraped = ?")
            params.append(int(total_scraped))
        if source_count is not None:
            sets.append("source_count = ?")
            params.append(int(source_count))
        if not sets:
            return
        sets.append(_UPDATED_AT_SET_CLAUSE)
        params.append(_now())
        params.append(str(run_id))
        with self._connection() as conn:
            # 状态校验和写入必须共享同一个立即事务，否则两个线程可同时读到
            # running，并分别把取消/成功两个互斥终态写入，后写者覆盖先写者。
            conn.execute(_BEGIN_IMMEDIATE)
            self._assert_recovery_writes_allowed(conn)
            if status is not None:
                current = conn.execute(
                    "SELECT status, error_code FROM screening_runs WHERE id = ?", (str(run_id),)
                ).fetchone()
                if current is None:
                    raise KeyError(run_id)
                cur_status = current["status"]
                if (
                    cur_status == "interrupted"
                    and current["error_code"] == "user_finished"
                    and status not in (None, "interrupted")
                ):
                    raise DiscoveryStoreConflictError("user_finished")
                if (
                    status is not None
                    and status != cur_status
                    and status not in RUN_TRANSITIONS.get(cur_status, set())
                ):
                    raise ValueError(f"运行不能从 {cur_status} 转换到 {status}")
            conn.execute(
                f"UPDATE screening_runs SET {', '.join(sets)} WHERE id = ?", params
            )

    def finish_screening_run(self, run_id):
        """原子标记用户主动结束并保存（interrupted + user_finished）。

        允许 queued/running/paused/failed 以及 interrupted(process_restart/
        operator_stop) 进入该终态；user_cancelled 与 succeeded/partial 拒绝改写。
        """
        ts = _now()
        with self._connection() as conn:
            conn.execute(_BEGIN_IMMEDIATE)
            self._assert_recovery_writes_allowed(conn)
            row = conn.execute(
                "SELECT status, error_code, interruption_kind FROM screening_runs "
                "WHERE id = ?", (str(run_id),),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            cur_status = row["status"]
            interruption_kind = row["interruption_kind"] or ""
            if cur_status == "interrupted" and interruption_kind == "user_cancelled":
                raise DiscoveryStoreConflictError("user_cancelled")
            if cur_status == "interrupted" and row["error_code"] == "user_finished":
                raise DiscoveryStoreConflictError("already_finished")
            if cur_status in ("succeeded", "partial"):
                raise DiscoveryStoreConflictError("already_terminal")
            if cur_status == "interrupted" and interruption_kind not in (
                "process_restart", "operator_stop"
            ):
                raise DiscoveryStoreConflictError("interrupted_not_restartable")
            conn.execute(
                "UPDATE screening_runs SET status = 'interrupted', "
                "error_code = 'user_finished', error_reason = ?, "
                "interruption_kind = 'user_finished', current_stage = 'done', "
                "updated_at = ? WHERE id = ?",
                ("用户提前结束，已保存部分结果", ts, str(run_id)),
            )
        return self.get_screening_run(run_id)

    def downgrade_succeeded_if_no_result_round(
        self, run_id, *, error_code: str, error_reason: str,
    ) -> bool:
        """条件降级 succeeded → failed（020 US7 写轮失败救援出口）。

        事务内校验，同时满足才允许降级：
        1. run 当前仍为 succeeded；
        2. 同流程（scrape_task_id + platform）不存在任何可见 result_snapshot
           轮（done/partial/scraped_only）。
        任一不满足返回 False、终态不动（调用方落诊断事件）。
        """
        ts = _now()
        with self._connection() as conn:
            conn.execute(_BEGIN_IMMEDIATE)
            self._assert_recovery_writes_allowed(conn)
            row = conn.execute(
                "SELECT status, platform, execution_params_json "
                "FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            if row is None or str(row["status"] or "") != "succeeded":
                return False
            try:
                params = json.loads(row["execution_params_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                params = {}
            params = params if isinstance(params, dict) else {}
            scrape_task_id = str(params.get("scrape_task_id") or "")
            platform = str(row["platform"] or params.get("platform") or "")
            if not scrape_task_id or not platform:
                return False
            visible_round = conn.execute(
                "SELECT id FROM screening_runs "
                "WHERE record_kind = 'result_snapshot' "
                "AND status IN ('done', 'partial', 'scraped_only') "
                "AND json_extract(execution_params_json, '$.scrape_task_id') = ? "
                "AND platform = ? LIMIT 1",
                (scrape_task_id, platform),
            ).fetchone()
            if visible_round is not None:
                return False
            conn.execute(
                "UPDATE screening_runs SET status = 'failed', "
                "error_code = ?, error_reason = ?, updated_at = ? "
                "WHERE id = ?",
                (str(error_code), str(error_reason), ts, str(run_id)),
            )
        return True

    def update_screening_execution_params(self, run_id, params: dict) -> None:
        """Replace the JSON execution params for a screening run."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET execution_params_json = ?, updated_at = ? "
                "WHERE id = ?", (
                    json.dumps(params or {}, ensure_ascii=False), _now(), str(run_id),
                ),
            )

    def claim_paused_screening_run(self, run_id) -> bool:
        """Atomically claim one paused run for in-place continuation.

        Unlike the general status updater, this operation is deliberately not
        idempotent: exactly one caller may change ``paused`` to ``running``.
        """
        with self._connection() as conn:
            conn.execute(_BEGIN_IMMEDIATE)
            self._assert_recovery_writes_allowed(conn)
            cursor = conn.execute(
                "UPDATE screening_runs SET status = 'running', error_code = NULL, "
                "error_reason = NULL, updated_at = ? "
                "WHERE id = ? AND status = 'paused'",
                (_now(), str(run_id)),
            )
            return cursor.rowcount == 1

    def finalize_run_status(self, run_id):
        """根据当前进度判定最终状态（FR-016, FR-036）。

        - 存在未开始岗位 OR 系统性阻断 → paused
        - 存在待确认岗位（独立失败）但无阻断 → partial（completed_with_pending）
        - 全部处理且无待确认 → succeeded（completed）
        """
        run = self.get_screening_run(run_id)
        if run is None:
            raise KeyError(run_id)
        cur_status = run["status"]
        # 终态不再重新判定
        if cur_status in ("succeeded", "partial", "failed", "interrupted"):
            return cur_status
        source_count = run.get("source_count", 0) or 0
        processed = run.get("processed_count", 0) or 0
        pending = run.get("pending_count", 0) or 0
        error_code = run.get("error_code")
        # 系统性阻断 → paused
        if error_code and error_code in SYSTEMIC_BLOCK_CODES:
            if cur_status != "paused":
                self.update_screening_run(run_id, status="paused")
            return "paused"
        # 存在未开始岗位 → paused（不得伪装完成）
        dropped = run.get("total_dropped", 0) or 0
        total_accounted = processed + pending + dropped
        if total_accounted < source_count:
            if cur_status != "paused":
                self.update_screening_run(run_id, status="paused")
            return "paused"
        # 有待确认但无阻断 → partial（completed_with_pending）
        if pending > 0:
            if cur_status != "partial":
                self.update_screening_run(run_id, status="partial")
            return "partial"
        # 全部处理且无待确认 → succeeded（completed）
        if cur_status != "succeeded":
            self.update_screening_run(run_id, status="succeeded")
        return "succeeded"
    # -- task events（FR-038） ---------------------------------------------

    def append_task_event(self, run_id, event_type, payload=None):
        """追加一条流程事件到 task_logs（FR-038）。

        事件类型：stage_start / stage_complete / job_success / job_fail /
        pause / resume / cancel / block_check。
        line 字段存 JSON：{"type":..., "payload":..., "at":...}。

        task_logs 有 FOREIGN KEY (task_id) REFERENCES tasks(id)，但 screening
        任务的 run_id 不在 tasks 表中。先 INSERT OR IGNORE 一个占位 tasks 行
        满足外键约束（status='logging' 表示仅用于事件日志锚点）。
        """
        return self.append_task_events(
            run_id, [(event_type, payload or {})]
        )[0]

    def append_task_events(self, run_id, events):
        """Append multiple structured events with consecutive sequence numbers."""
        normalized = [
            (str(event_type), payload if isinstance(payload, dict) else {})
            for event_type, payload in events
        ]
        if not normalized:
            return []
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            # 占位 tasks 行（已存在则忽略）
            conn.execute(
                "INSERT OR IGNORE INTO tasks (id, kind, status, params_json, created_at, updated_at) "
                "VALUES (?, 'screening_event_log', 'logging', '{}', ?, ?)",
                (str(run_id), ts, ts),
            )
            cur = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM task_logs WHERE task_id = ?",
                (str(run_id),),
            )
            first_seq = int(cur.fetchone()["next_seq"])
            rows = []
            result = []
            for offset, (event_type, payload) in enumerate(normalized):
                seq = first_seq + offset
                at = _now()
                line = json.dumps(
                    {"type": event_type, "payload": payload, "at": at},
                    ensure_ascii=False,
                )
                rows.append((str(run_id), seq, at, line))
                result.append({
                    "task_id": str(run_id), "seq": seq, "type": event_type,
                    "payload": payload, "at": at,
                })
            conn.executemany(
                "INSERT INTO task_logs (task_id, seq, created_at, line) VALUES (?, ?, ?, ?)",
                rows,
            )
            return result

    def list_task_events(self, run_id, after_seq=0):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT task_id, seq, created_at, line FROM task_logs "
                "WHERE task_id = ? AND seq > ? ORDER BY seq ASC",
                (str(run_id), int(after_seq)),
            ).fetchall()
        events = []
        for r in rows:
            try:
                data = json.loads(r["line"])
            except (json.JSONDecodeError, TypeError):
                data = {"type": "raw", "payload": {"text": r["line"]}, "at": r["created_at"]}
            events.append({
                "seq": int(r["seq"]), "type": data.get("type", "raw"),
                "payload": data.get("payload", {}), "at": data.get("at", r["created_at"]),
            })
        return events

    def latest_screening_run_for_source(self, source_task_id, *, statuses=None):
        """找同一抓取任务最近一次 AI 筛选 run（供断点续筛）。"""
        with self._connection() as conn:
            row = latest_screening_run_for_source(
                conn, source_task_id, statuses=statuses,
            )
        return self._screening_run_row(row) if row is not None else None

    def latest_interrupted_screening_run(self):
        """进程重启后被标记 interrupted 的最近一次筛选（供恢复提示）。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_runs WHERE status = 'interrupted' "
                "AND error_code = 'restart' "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return self._screening_run_row(row) if row is not None else None

    def save_screening_verdicts(self, run_id, verdicts):
        """每批精筛判定落盘（upsert）：进程崩了也能从 screening_results 续。"""
        if not verdicts:
            return
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            run_row = conn.execute(
                "SELECT platform FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            platform = str(run_row["platform"] or "boss") if run_row is not None else "boss"
            for job_id, verdict in verdicts.items():
                if isinstance(verdict, dict):
                    verdict_value = str(verdict.get("verdict") or "")
                    reason = str(verdict.get("reason") or "")
                    caveats = verdict.get("caveats") if isinstance(verdict.get("caveats"), list) else []
                    flags = verdict.get("flags") if isinstance(verdict.get("flags"), list) else []
                else:
                    verdict_value = str(verdict or "")
                    reason = ""
                    caveats = []
                    flags = []
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, platform, platform_job_id, verdict, verdict_reason, caveats_json, flags_json, is_dropped, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                    " platform = excluded.platform, "
                    " verdict = excluded.verdict, "
                    " verdict_reason = excluded.verdict_reason, "
                    " caveats_json = excluded.caveats_json, "
                    " flags_json = excluded.flags_json, "
                    " is_dropped = excluded.is_dropped",
                    (
                        _uuid(), str(run_id), platform, str(job_id), verdict_value, reason,
                        json.dumps(caveats, ensure_ascii=False),
                        json.dumps(flags, ensure_ascii=False),
                        1 if verdict_value == "dropped" else 0, ts,
                    ),
                )

    def save_verdict_and_checkpoint_atomic(
            self, run_id, stage, verdicts, completed_job_ids):
        """Persist one AI batch and advance its checkpoint in one transaction."""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            run_row = conn.execute(
                "SELECT platform FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            platform = str(run_row["platform"] or "boss") if run_row is not None else "boss"
            for job_id, verdict in (verdicts or {}).items():
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, platform, platform_job_id, verdict, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET platform = excluded.platform, verdict = excluded.verdict",
                    (
                        _uuid(), str(run_id), platform, str(job_id),
                        json.dumps(verdict, ensure_ascii=False), ts,
                    ),
                )
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(run_id), str(stage),
                    json.dumps(list(completed_job_ids or []), ensure_ascii=False), ts,
                ),
            )

    def load_screening_verdicts(self, run_id):
        """载入某次筛选已落盘的判定 {job_id: verdict}（断点续筛用）。

        同时支持 JSON verdict（精筛）和纯字符串 verdict（粗筛）。
        - JSON verdict：返回完整 dict
        - 纯字符串 verdict：返回 {"verdict": "match"/"not_match"/...}
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT platform_job_id, verdict, verdict_reason, caveats_json, flags_json "
                "FROM screening_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchall()
        out = {}
        for row in rows:
            v = row["verdict"] or ""
            reason = row["verdict_reason"] or ""
            try:
                caveats = json.loads(row["caveats_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                caveats = []
            try:
                flags = json.loads(row["flags_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                flags = []
            try:
                value = json.loads(v)
                if isinstance(value, dict):
                    if not value.get("reason"):
                        value["reason"] = reason
                    if "caveats" not in value:
                        value["caveats"] = caveats
                    if "flags" not in value:
                        value["flags"] = flags
                    out[str(row["platform_job_id"])] = value
                else:
                    out[str(row["platform_job_id"])] = {
                        "verdict": str(value), "reason": reason,
                        "caveats": caveats, "flags": flags,
                    }
            except (json.JSONDecodeError, TypeError):
                # 纯字符串 verdict（如 match/not_match/uncertain/dropped）
                if v:
                    out[str(row["platform_job_id"])] = {
                        "verdict": v, "reason": reason,
                        "caveats": caveats, "flags": flags,
                    }
        return out

    def load_screening_pending(self, run_id):
        """Return per-job pending failures for a screening run."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT platform_job_id, failure_stage, retryable, attempts, failed_code, "
                " ai_payload_json, last_failed_at FROM screening_pending_results "
                "WHERE run_id = ?", (str(run_id),),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["ai_payload"] = json.loads(item.get("ai_payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["ai_payload"] = {}
            item.pop("ai_payload_json", None)
            out.append(item)
        return out

    def _screening_run_row(self, row) -> dict:
        keys = row.keys()
        status = row["status"]
        # Terminal results are authoritative.  Older runs may have retained
        # a pause error from an earlier attempt; never expose that stale
        # detail as if it were the reason for a successful/partial result.
        terminal_error_code = None if status in {"succeeded", "partial"} else row["error_code"]
        terminal_error_reason = None if status in {"succeeded", "partial"} else (row["error_reason"] if "error_reason" in keys else None)
        return {
            "id": row["id"],
            "status": status,
            "frozen_filters": json.loads(row["frozen_filters_json"] or "{}"),
            "source_count": row["source_count"],
            "match_count": row["match_count"],
            "mismatch_count": row["mismatch_count"],
            "processed_count": row["processed_count"],
            "source_cursor": row["source_cursor"],
            "error_code": terminal_error_code,
            "profile_id": row["profile_id"],
            "execution_params": json.loads(row["execution_params_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"] if "started_at" in keys else None,
            "finished_at": row["finished_at"] if "finished_at" in keys else None,
            "record_kind": row["record_kind"],
            # FR-016/SC-018 守恒字段（migration_007/018 加的列，必须读出来）
            "pending_count": row["pending_count"],
            "parse_failure_count": row["parse_failure_count"],
            "parse_failures": json.loads(row["parse_failures_json"] or "{}"),
            "resume_id": row["resume_id"],
            "total_scraped": row["total_scraped"],
            "total_kept": row["total_kept"],
            "total_dropped": row["total_dropped"],
            "search_params": json.loads(row["search_params_json"] or "{}"),
            "profile_summary": row["profile_summary"],
            "profile_facts": (
                json.loads(row["profile_facts_json"])
                if "profile_facts_json" in keys and row["profile_facts_json"]
                else None
            ),
            # FR-005/FR-037 新增字段（migration_020 加的列）
            "current_stage": row["current_stage"] if "current_stage" in keys else None,
            "error_reason": terminal_error_reason,
            "backend_version": row["backend_version"] if "backend_version" in keys else None,
            # migration 27 平台身份字段（T405: 进度/状态接口返回）
            "platform": row["platform"] if "platform" in keys else None,
            "task_input_digest": row["task_input_digest"] if "task_input_digest" in keys else None,
            "interruption_kind": row["interruption_kind"] if "interruption_kind" in keys else None,
            "archived_at": row["archived_at"] if "archived_at" in keys else None,
        }

    def append_search_event(self, run_id, event_type, payload=None):
        self.get_search_run(run_id)
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO search_run_events (run_id, type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (str(run_id), str(event_type), json.dumps(payload or {}, ensure_ascii=False), _now()),
            )
            event_id = cursor.lastrowid
        return {"id": event_id, "run_id": str(run_id), "type": str(event_type), "payload": payload or {}}

    def list_search_events(self, run_id, after=0):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, run_id, type, payload_json, created_at FROM search_run_events "
                "WHERE run_id = ? AND id > ? ORDER BY id ASC",
                (str(run_id), int(after or 0)),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"] or "{}"), "payload_json": None} for row in rows]
