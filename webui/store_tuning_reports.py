"""调优测量与报告域（021 B2 拆分自 webui/store.py）：测量事件、质量
参照、任务单原子签发/启动与执行者报告。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from webui.store_helpers import (
    _decode_json,
    _now,
    _uuid,
)
from webui.store_constants import (
    _BEGIN_IMMEDIATE,
    _SQL_TUNING_EXPERIMENT_STATUS,
    _SQL_TUNING_MANIFEST,
)


class StoreTuningReportsMixin:

    def _validate_measurement_payload(self, payload: dict) -> None:
        """校验测量事件 payload 不包含敏感字段。"""
        if not isinstance(payload, dict):
            return
        for key in payload:
            key_lower = str(key).lower()
            if key_lower in self._MEASUREMENT_FORBIDDEN_KEYS:
                raise ValueError(f"测量事件禁止包含敏感字段: {key}")

    def save_tuning_measurement_event(
        self, *, round_id: str, event_type: str, stage: str,
        duration_ms: int, started_monotonic_ms: int | None = None,
        counts: dict | None = None, error_code: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """持久化一条测量事件。

        data-model.md 2.9: 禁止凭据、原始简历、原始模型响应和 JD 正文。
        """
        # 校验敏感字段
        if counts:
            self._validate_measurement_payload(counts)
        if metadata:
            self._validate_measurement_payload(metadata)
        if duration_ms < 0:
            raise ValueError("duration_ms 必须非负")
        if started_monotonic_ms is not None and started_monotonic_ms < 0:
            raise ValueError("started_monotonic_ms 必须非负")

        now = _now()
        counts_json = json.dumps(counts, ensure_ascii=False, sort_keys=True) if counts else None
        metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else None

        with self._connection() as conn:
            # 先取得 SQLite 写锁，使 MAX(seq)+INSERT 成为一个不可交错的分配事务。
            conn.execute(_BEGIN_IMMEDIATE)
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                "FROM tuning_measurement_events WHERE round_id = ?",
                (round_id,),
            ).fetchone()
            next_seq = row["next_seq"]
            if started_monotonic_ms is None:
                started_monotonic_ms = 0
            conn.execute(
                "INSERT INTO tuning_measurement_events "
                "(round_id, seq, event_type, stage, started_monotonic_ms, "
                " duration_ms, counts_json, error_code, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (round_id, next_seq, event_type, stage, started_monotonic_ms,
                 duration_ms, counts_json, error_code, metadata_json, now),
            )
        return {
            "round_id": round_id, "seq": next_seq, "event_type": event_type,
            "stage": stage, "started_monotonic_ms": started_monotonic_ms,
            "duration_ms": duration_ms, "counts": counts,
            "error_code": error_code, "metadata": metadata,
        }

    def list_tuning_measurement_events(self, round_id: str) -> list[dict]:
        """列出某轮次的全部测量事件，按 seq 升序。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tuning_measurement_events "
                "WHERE round_id = ? ORDER BY seq ASC",
                (round_id,),
            ).fetchall()
        result = []
        for row in rows:
            counts = json.loads(row["counts_json"]) if row["counts_json"] else None
            metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else None
            result.append({
                "round_id": row["round_id"], "seq": row["seq"],
                "event_type": row["event_type"], "stage": row["stage"],
                "started_monotonic_ms": row["started_monotonic_ms"],
                "duration_ms": row["duration_ms"],
                "counts": counts, "error_code": row["error_code"],
                "metadata": metadata, "created_at": row["created_at"],
            })
        return result

    # -- T020: 质量参考 CRUD (data-model.md 2.4) ------------------------

    def save_quality_reference(
        self, *, experiment_id: str, input_version_id: str,
        item_results: dict, variation_summary: dict,
        reference_digest: str,
    ) -> dict:
        """创建一条 building 状态的质量参考记录。"""
        ref_id = _uuid()
        now = _now()
        item_results_json = json.dumps(item_results, ensure_ascii=False, sort_keys=True)
        variation_json = json.dumps(variation_summary, ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_quality_references "
                "(id, experiment_id, input_version_id, status, "
                " item_results_json, variation_summary_json, reviewed_item_ids_json, "
                " reference_digest, created_at, confirmed_at) "
                "VALUES (?, ?, ?, 'building', ?, ?, NULL, ?, ?, NULL)",
                (ref_id, experiment_id, input_version_id,
                 item_results_json, variation_json,
                 reference_digest, now),
            )
        return {
            "id": ref_id, "experiment_id": experiment_id,
            "input_version_id": input_version_id, "status": "building",
            "item_results": item_results,
            "variation_summary": variation_summary,
            "reviewed_item_ids": [],
            "reference_digest": reference_digest,
            "created_at": now, "confirmed_at": None,
        }

    def get_quality_reference(self, reference_id: str) -> dict:
        """返回质量参考记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_quality_references WHERE id = ?",
                (reference_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"质量参考不存在: {reference_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "input_version_id": row["input_version_id"],
            "status": row["status"],
            "item_results": _decode_json(row["item_results_json"], {"items": []}),
            "variation_summary": _decode_json(row["variation_summary_json"], {}),
            "reviewed_item_ids": _decode_json(row["reviewed_item_ids_json"], []),
            "reference_digest": row["reference_digest"],
            "created_at": row["created_at"],
            "confirmed_at": row["confirmed_at"],
        }

    def list_quality_references(self, experiment_id: str) -> list[dict]:
        """列出实验的全部质量参考，按创建时间降序。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tuning_quality_references "
                "WHERE experiment_id = ? ORDER BY created_at DESC",
                (experiment_id,),
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "experiment_id": row["experiment_id"],
                "input_version_id": row["input_version_id"],
                "status": row["status"],
                "item_results": _decode_json(row["item_results_json"], {"items": []}),
                "variation_summary": _decode_json(row["variation_summary_json"], {}),
                "reviewed_item_ids": _decode_json(row["reviewed_item_ids_json"], []),
                "reference_digest": row["reference_digest"],
                "created_at": row["created_at"],
                "confirmed_at": row["confirmed_at"],
            })
        return result

    def update_quality_reference_status(
        self, reference_id: str, *, status: str,
        reviewed_item_ids: list | None = None,
    ) -> dict:
        """更新质量参考状态。

        合法状态：building → confirmed → review_required → confirmed
                         或 confirmed → superseded
        """
        valid_statuses = {"building", "confirmed", "review_required", "superseded"}
        if status not in valid_statuses:
            raise ValueError(f"非法质量参考状态: {status}")
        now = _now()
        confirmed_at = now if status == "confirmed" else None
        reviewed_json = None
        if reviewed_item_ids is not None:
            reviewed_json = json.dumps(reviewed_item_ids, ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            if reviewed_json is not None:
                conn.execute(
                    "UPDATE tuning_quality_references "
                    "SET status = ?, reviewed_item_ids_json = ?, "
                    "    confirmed_at = COALESCE(?, confirmed_at) "
                    "WHERE id = ?",
                    (status, reviewed_json, confirmed_at, reference_id),
                )
            else:
                conn.execute(
                    "UPDATE tuning_quality_references "
                    "SET status = ?, confirmed_at = COALESCE(?, confirmed_at) "
                    "WHERE id = ?",
                    (status, confirmed_at, reference_id),
                )
        return self.get_quality_reference(reference_id)

    def supersede_quality_references(
        self, experiment_id: str, except_id: str,
    ) -> None:
        """将实验的全部 confirmed/review_required 参考标记为 superseded，
        保留 except_id 不变。"""
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_quality_references "
                "SET status = 'superseded' "
                "WHERE experiment_id = ? AND id != ? "
                "  AND status IN ('confirmed', 'review_required', 'building')",
                (experiment_id, except_id),
            )

    def set_experiment_quality_reference(
        self, experiment_id: str, reference_id: str,
    ) -> None:
        """设置实验的活动质量参考。"""
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_experiments "
                "SET quality_reference_id = ?, updated_at = ? WHERE id = ?",
                (reference_id, now, experiment_id),
            )

    # -- T022: 任务单与报告持久化 (data-model.md 2.7/2.8) ----------------

    def save_task_manifest(
        self, *, experiment_id: str, candidate_id: str, round_id: str,
        manifest_version: int, manifest_json: str, manifest_digest: str,
        rendered_task_path: str,
    ) -> dict:
        """持久化一份已签发的任务单。"""
        manifest_id = _uuid()
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_task_manifests "
                "(id, experiment_id, candidate_id, round_id, manifest_version, "
                " manifest_json, manifest_digest, rendered_task_path, "
                " status, issued_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'issued', ?, ?)",
                (manifest_id, experiment_id, candidate_id, round_id,
                 manifest_version, manifest_json, manifest_digest,
                 rendered_task_path, now, now),
            )
        return {
            "manifest_id": manifest_id,
            "manifest_digest": manifest_digest,
            "rendered_task_path": rendered_task_path,
            "status": "issued",
        }

    def issue_task_manifest_atomic(
        self, *, experiment_id: str, candidate_id: str, round_id: str,
        manifest_version: int, manifest_json: str, manifest_digest: str,
        rendered_task_path: str, owner_token: str,
    ) -> dict:
        """在一个 IMMEDIATE 事务中 claim 租约并签发唯一任务单。"""
        token_digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        manifest_id = _uuid()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_until = (now_dt + timedelta(seconds=self._LEASE_TTL_SECONDS)).isoformat()
        with self._connection() as conn:
            conn.execute(_BEGIN_IMMEDIATE)
            experiment = conn.execute(
                _SQL_TUNING_EXPERIMENT_STATUS, (experiment_id,),
            ).fetchone()
            round_row = conn.execute(
                "SELECT status, experiment_id, candidate_id FROM tuning_rounds WHERE id = ?",
                (round_id,),
            ).fetchone()
            lease = conn.execute(
                "SELECT owner_token_digest FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if experiment is None or experiment["status"] != "awaiting_instruction":
                raise ValueError("实验状态已变化，任务单签发中止")
            if (
                round_row is None or round_row["status"] != "planned"
                or round_row["experiment_id"] != experiment_id
                or round_row["candidate_id"] != candidate_id
            ):
                raise ValueError("轮次状态或归属已变化，任务单签发中止")
            if lease is None or lease["owner_token_digest"] is not None:
                raise ValueError("独占执行租约被占用，不能签发任务单")
            conn.execute(
                "UPDATE tuning_execution_lease SET owner_experiment_id = ?, "
                "owner_round_id = ?, owner_token_digest = ?, lease_until = ?, "
                "heartbeat_at = ?, updated_at = ? WHERE id = 1",
                (experiment_id, round_id, token_digest, lease_until, now, now),
            )
            conn.execute(
                "INSERT INTO tuning_task_manifests "
                "(id, experiment_id, candidate_id, round_id, manifest_version, "
                "platform, manifest_json, manifest_digest, rendered_task_path, "
                "status, issued_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, json_extract(?, '$.fixed_fields.platform'), "
                "?, ?, ?, 'issued', ?, ?)",
                (manifest_id, experiment_id, candidate_id, round_id,
                 manifest_version, manifest_json, manifest_json,
                 manifest_digest, rendered_task_path, now, now),
            )
            conn.execute(
                "UPDATE tuning_rounds SET status = 'issued', manifest_id = ? WHERE id = ?",
                (manifest_id, round_id),
            )
            conn.execute(
                "UPDATE tuning_experiments SET status = 'queued', updated_at = ? WHERE id = ?",
                (now, experiment_id),
            )
        return {
            "manifest_id": manifest_id, "manifest_digest": manifest_digest,
            "rendered_task_path": rendered_task_path, "status": "issued",
        }

    def get_task_manifest(self, manifest_id: str) -> dict:
        """返回任务单记录。"""
        with self._connection() as conn:
            row = conn.execute(
                _SQL_TUNING_MANIFEST,
                (manifest_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"任务单不存在: {manifest_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "candidate_id": row["candidate_id"],
            "round_id": row["round_id"],
            "manifest_version": row["manifest_version"],
            "platform": row["platform"],
            "manifest": _decode_json(row["manifest_json"], {}),
            "manifest_digest": row["manifest_digest"],
            "rendered_task_path": row["rendered_task_path"],
            "status": row["status"],
            "issued_at": row["issued_at"],
            "updated_at": row["updated_at"],
        }

    def update_task_manifest_status(
        self, manifest_id: str, *, status: str,
    ) -> None:
        """更新任务单状态。"""
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_task_manifests "
                "SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, manifest_id),
            )

    def start_task_manifest_atomic(self, manifest_id: str, *, owner_token: str) -> dict:
        """核对签发租约并原子推进 queued/issued → running。"""
        token_digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = _now()
        with self._connection() as conn:
            conn.execute(_BEGIN_IMMEDIATE)
            manifest = conn.execute(
                _SQL_TUNING_MANIFEST, (manifest_id,),
            ).fetchone()
            if manifest is None:
                raise KeyError(f"任务单不存在: {manifest_id}")
            round_row = conn.execute(
                "SELECT status FROM tuning_rounds WHERE id = ?", (manifest["round_id"],),
            ).fetchone()
            experiment = conn.execute(
                _SQL_TUNING_EXPERIMENT_STATUS,
                (manifest["experiment_id"],),
            ).fetchone()
            lease = conn.execute(
                "SELECT owner_token_digest, owner_experiment_id, owner_round_id "
                "FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if manifest["status"] != "issued":
                raise ValueError("任务单不是 issued 状态")
            if round_row is None or round_row["status"] != "issued":
                raise ValueError("轮次不是 issued 状态")
            if experiment is None or experiment["status"] != "queued":
                raise ValueError("实验不是 queued 状态")
            if (
                lease is None or lease["owner_token_digest"] != token_digest
                or lease["owner_experiment_id"] != manifest["experiment_id"]
                or lease["owner_round_id"] != manifest["round_id"]
            ):
                raise ValueError("任务单未持有匹配的应用租约")
            conn.execute(
                "UPDATE tuning_rounds SET status = 'running', started_at = ? WHERE id = ?",
                (now, manifest["round_id"]),
            )
            conn.execute(
                "UPDATE tuning_experiments SET status = 'running', updated_at = ? WHERE id = ?",
                (now, manifest["experiment_id"]),
            )
            conn.execute(
                "UPDATE tuning_task_manifests SET status = 'running', updated_at = ? WHERE id = ?",
                (now, manifest_id),
            )
        return {
            "manifest_id": manifest_id, "round_id": manifest["round_id"],
            "experiment_id": manifest["experiment_id"], "status": "running",
        }

    def save_executor_report(
        self, *, manifest_id: str, report_version: int,
        report_json: str, reported_manifest_digest: str,
        evidence_digest: str, validation_status: str,
        validation_errors: list | None = None,
    ) -> dict:
        """持久化一份执行者报告。"""
        report_id = _uuid()
        now = _now()
        errors_json = json.dumps(
            validation_errors or [], ensure_ascii=False, sort_keys=True,
        )
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_executor_reports "
                "(id, manifest_id, report_version, report_json, "
                " reported_manifest_digest, evidence_digest, "
                " validation_status, validation_errors_json, "
                " created_at, validated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, manifest_id, report_version, report_json,
                 reported_manifest_digest, evidence_digest,
                 validation_status, errors_json, now, now),
            )
        return {
            "report_id": report_id,
            "manifest_id": manifest_id,
            "validation_status": validation_status,
        }

    def save_executor_report_atomic(
        self, *, manifest_id: str, report_version: int, report_json: str,
        reported_manifest_digest: str, evidence_digest: str,
        validation_status: str, validation_errors: list | None,
        report_status: str | None, owner_token: str,
    ) -> dict:
        """原子保存报告、推进轮次/实验并释放应用持有的租约。"""
        report_id = _uuid()
        now = _now()
        token_digest = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        errors_json = json.dumps(
            validation_errors or [], ensure_ascii=False, sort_keys=True,
        )
        parsed = _decode_json(report_json, {})
        with self._connection() as conn:
            conn.execute(_BEGIN_IMMEDIATE)
            manifest = conn.execute(
                _SQL_TUNING_MANIFEST, (manifest_id,),
            ).fetchone()
            if manifest is None:
                raise KeyError(f"任务单不存在: {manifest_id}")
            round_row = conn.execute(
                "SELECT status FROM tuning_rounds WHERE id = ?", (manifest["round_id"],),
            ).fetchone()
            experiment = conn.execute(
                _SQL_TUNING_EXPERIMENT_STATUS,
                (manifest["experiment_id"],),
            ).fetchone()
            lease = conn.execute(
                "SELECT owner_token_digest, owner_round_id FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if (
                round_row is None or experiment is None or lease is None
                or lease["owner_token_digest"] != token_digest
                or lease["owner_round_id"] != manifest["round_id"]
            ):
                raise ValueError("报告接收时租约或状态归属不一致")
            if round_row["status"] not in ("running", "reported"):
                raise ValueError(f"轮次状态 {round_row['status']} 不能接收报告")
            conn.execute(
                "INSERT INTO tuning_executor_reports "
                "(id, manifest_id, report_version, report_json, "
                "reported_manifest_digest, evidence_digest, validation_status, "
                "validation_errors_json, created_at, validated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, manifest_id, report_version, report_json,
                 reported_manifest_digest, evidence_digest, validation_status,
                 errors_json, now, now),
            )
            if validation_status == "accepted" and report_status == "completed":
                if experiment["status"] != "running":
                    raise ValueError("实验不在 running，不能确认完成报告")
                conn.execute(
                    "UPDATE tuning_rounds SET status = 'confirmed', metrics_json = ?, "
                    "evidence_manifest_json = ?, finished_at = ?, confirmed_at = ? WHERE id = ?",
                    (json.dumps(parsed.get("program_evidence", {}), ensure_ascii=False,
                                sort_keys=True),
                     json.dumps({"artifacts": parsed.get("artifacts", [])},
                                ensure_ascii=False, sort_keys=True),
                     now, now, manifest["round_id"]),
                )
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'evaluating', updated_at = ? WHERE id = ?",
                    (now, manifest["experiment_id"]),
                )
                final_round_status, final_experiment_status = "confirmed", "evaluating"
            elif validation_status == "accepted" and report_status == "blocked":
                conn.execute(
                    "UPDATE tuning_rounds SET status = 'blocked', failure_code = ?, "
                    "metrics_json = ?, evidence_manifest_json = ?, finished_at = ? WHERE id = ?",
                    (parsed.get("stop_reason") or "executor_blocked",
                     json.dumps(parsed.get("program_evidence", {}), ensure_ascii=False,
                                sort_keys=True),
                     json.dumps({"artifacts": parsed.get("artifacts", [])},
                                ensure_ascii=False, sort_keys=True),
                     now, manifest["round_id"]),
                )
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'blocked', blocked_code = ?, "
                    "blocked_reason = ?, updated_at = ? WHERE id = ?",
                    (parsed.get("stop_reason") or "executor_blocked",
                     "执行者按任务单停止条件阻断", now, manifest["experiment_id"]),
                )
                final_round_status, final_experiment_status = "blocked", "blocked"
            else:
                conn.execute(
                    "UPDATE tuning_rounds SET status = 'invalid', "
                    "failure_code = 'report_validation_failed', finished_at = ? WHERE id = ?",
                    (now, manifest["round_id"]),
                )
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'blocked', "
                    "blocked_code = 'report_validation_failed', blocked_reason = ?, "
                    "updated_at = ? WHERE id = ?",
                    ("; ".join(validation_errors or []), now, manifest["experiment_id"]),
                )
                final_round_status, final_experiment_status = "invalid", "blocked"
            conn.execute(
                "UPDATE tuning_task_manifests SET status = ?, updated_at = ? WHERE id = ?",
                ("reported" if validation_status == "accepted" else "rejected", now, manifest_id),
            )
            conn.execute(
                "UPDATE tuning_execution_lease SET owner_experiment_id = NULL, "
                "owner_round_id = NULL, owner_token_digest = NULL, lease_until = NULL, "
                "heartbeat_at = NULL, updated_at = ? WHERE id = 1",
                (now,),
            )
        return {
            "report_id": report_id, "manifest_id": manifest_id,
            "validation_status": validation_status,
            "round_status": final_round_status,
            "experiment_status": final_experiment_status,
        }
