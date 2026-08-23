"""调优轮次域（021 B2 拆分自 webui/store.py）：candidate、round、
stage artifact、执行租约与重启对账。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from webui.store_helpers import (
    _decode_json,
    _now,
    _uuid,
)
from webui.store_constants import (
    _SHA256_PREFIX,
)


class StoreTuningRoundsMixin:

    def save_tuning_candidate(
        self, *, experiment_id: str, stage: str, strategy_step: str,
        config: dict, parent_candidate_id: str | None = None,
        pressure_rank: int = 0,
    ) -> dict:
        """保存候选配置到实验表（不写入 advanced_config_state）。"""
        from webui.execution_config import ExecutionConfigSnapshot
        snapshot = ExecutionConfigSnapshot.create(config)
        candidate_id = _uuid()
        now = _now()
        config_json = json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_candidates "
                "(id, experiment_id, stage, strategy_step, parent_candidate_id, "
                " config_json, config_digest, status, pressure_rank, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)",
                (candidate_id, experiment_id, stage, strategy_step,
                 parent_candidate_id, config_json, snapshot.config_digest,
                 pressure_rank, now, now),
            )
        return {"id": candidate_id, "config_digest": snapshot.config_digest}

    def get_tuning_candidate(self, candidate_id: str) -> dict:
        """返回候选记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"候选不存在: {candidate_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "stage": row["stage"],
            "strategy_step": row["strategy_step"],
            "parent_candidate_id": row["parent_candidate_id"],
            "config": _decode_json(row["config_json"], {}),
            "config_digest": row["config_digest"],
            "status": row["status"],
            "pressure_rank": row["pressure_rank"],
            "promotion_reason": _decode_json(row["promotion_reason"], None),
            "rejection_code": row["rejection_code"],
        }

    def create_tuning_round(
        self, *, experiment_id: str, candidate_id: str, workload_id: str,
        round_kind: str, repetition_index: int,
        quality_reference_id: str | None = None,
    ) -> dict:
        """创建 planned 状态的轮次。"""
        round_id = _uuid()
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_rounds "
                "(id, experiment_id, candidate_id, workload_id, quality_reference_id, "
                " round_kind, repetition_index, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)",
                (round_id, experiment_id, candidate_id, workload_id,
                 quality_reference_id, round_kind, repetition_index, now),
            )
        return {"id": round_id, "status": "planned"}

    def get_tuning_round(self, round_id: str) -> dict:
        """返回轮次记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_rounds WHERE id = ?", (round_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"轮次不存在: {round_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "candidate_id": row["candidate_id"],
            "workload_id": row["workload_id"],
            "round_kind": row["round_kind"],
            "repetition_index": row["repetition_index"],
            "status": row["status"],
            "manifest_id": row["manifest_id"],
            "metrics": _decode_json(row["metrics_json"], None),
            "failure_code": row["failure_code"],
        }

    def update_tuning_round_status(
        self, round_id: str, *, status: str,
        failure_code: str | None = None,
    ) -> None:
        """更新轮次状态，强制 state-machine.md 的合法转换。"""
        current = self.get_tuning_round(round_id)
        current_status = current["status"]
        legal_transitions = {
            "planned": {"issued", "cancelled"},
            "issued": {"running", "uncertain", "blocked", "cancelled"},
            "running": {"reported", "uncertain", "blocked", "cancelled"},
            "reported": {"confirmed", "invalid", "blocked"},
        }
        if status != current_status and status not in legal_transitions.get(
            current_status, set()
        ):
            raise ValueError(
                f"非法轮次状态转换: {current_status} → {status}"
            )
        if status == "running":
            lease = self.get_tuning_lease()
            if (
                lease.get("owner_experiment_id") != current["experiment_id"]
                or lease.get("owner_round_id") != round_id
            ):
                raise ValueError("轮次未持有独占租约，不能进入 running")
        now = _now()
        finished_at = now if status in ("confirmed", "invalid", "blocked", "cancelled") else None
        confirmed_at = now if status == "confirmed" else None
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_rounds "
                "SET status = ?, failure_code = COALESCE(?, failure_code), "
                "    finished_at = COALESCE(?, finished_at), "
                "    confirmed_at = COALESCE(?, confirmed_at) "
                "WHERE id = ?",
                (status, failure_code, finished_at, confirmed_at, round_id),
            )

    def save_tuning_stage_artifact(
        self, *, round_id: str, stage: str, payload: dict,
        workspace_root: str | os.PathLike,
        source_artifact_id: str | None = None,
    ) -> dict:
        """Append one immutable, digest-verified stage result for a round."""
        if not isinstance(payload, dict):
            raise ValueError("阶段产物 payload 必须是对象")
        round_record = self.get_tuning_round(round_id)
        if round_record["status"] not in ("running", "reported"):
            raise ValueError("只有 running/reported 轮次可以保存阶段产物")
        if stage != round_record["round_kind"]:
            raise ValueError("阶段产物类型与轮次类型不一致")
        if stage not in ("list", "detail", "rough", "fine", "end_to_end"):
            raise ValueError("阶段产物类型无效")

        with self._connection() as conn:
            workload = conn.execute(
                "SELECT input_version_id FROM tuning_workloads WHERE id = ?",
                (round_record["workload_id"],),
            ).fetchone()
            if workload is None:
                raise KeyError("轮次 workload 不存在")
            # T607: 从 experiment 读取 platform/scope_digest/task_input_digest
            # 写入 stage artifact 外层列
            experiment_row = conn.execute(
                "SELECT platform, source_scope_json FROM tuning_experiments "
                "WHERE id = ?",
                (round_record["experiment_id"],),
            ).fetchone()
            artifact_platform = experiment_row["platform"] if experiment_row else None
            source_scope_json = (
                experiment_row["source_scope_json"] if experiment_row else None
            )
            source_scope = _decode_json(source_scope_json, {})
            scope_digest = source_scope.get("scope_digest")
            task_input_digest = source_scope.get("task_input_digest")
            if source_artifact_id is not None:
                source = conn.execute(
                    "SELECT experiment_id, input_version_id, workload_id, "
                    "platform, status "
                    "FROM tuning_stage_artifacts WHERE id = ?",
                    (source_artifact_id,),
                ).fetchone()
                if source is None:
                    raise ValueError("上游阶段产物不存在")
                if (
                    source["experiment_id"] != round_record["experiment_id"]
                    or source["input_version_id"] != workload["input_version_id"]
                    or source["workload_id"] != round_record["workload_id"]
                    or source["status"] != "ready"
                ):
                    raise ValueError("上游阶段产物身份不匹配")
                # T611: detail 只接受同平台 list artifact
                if source["platform"] != artifact_platform:
                    raise ValueError(
                        f"上游阶段产物平台 {source['platform']!r} 与当前实验 "
                        f"平台 {artifact_platform!r} 不一致"
                    )

        artifact_id = _uuid()
        relative_path = (
            f"tuning/{round_record['experiment_id']}/artifacts/"
            f"{round_id}/{stage}-{artifact_id}.json"
        )
        artifact_bytes = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        artifact_digest = (
            _SHA256_PREFIX + hashlib.sha256(artifact_bytes).hexdigest()
        )
        workspace = Path(workspace_root).resolve()
        experiment_root = (
            workspace / "tuning" / round_record["experiment_id"]
        ).resolve()
        absolute_path = (workspace / relative_path).resolve()
        if experiment_root not in absolute_path.parents:
            raise ValueError("阶段产物路径越过实验根目录")
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = absolute_path.with_name(
            absolute_path.name + f".{_uuid()}.tmp"
        )
        try:
            with temporary_path.open("xb") as handle:
                handle.write(artifact_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, absolute_path)
            persisted = absolute_path.read_bytes()
            if persisted != artifact_bytes:
                raise OSError("阶段产物原子写入后内容不一致")
            jobs = payload.get("jobs")
            verdicts = payload.get("verdicts")
            if isinstance(jobs, list):
                item_count = len(jobs)
            elif isinstance(verdicts, dict):
                item_count = len(verdicts)
            else:
                item_count = 0
            with self._connection() as conn:
                # T609: source_artifact_kind 只有 list/detail 可复用
                source_artifact_kind = stage if stage in ("list", "detail") else None
                conn.execute(
                    "INSERT INTO tuning_stage_artifacts "
                    "(id, experiment_id, input_version_id, workload_id, "
                    " producer_round_id, stage, platform, source_artifact_kind, "
                    " scope_digest, task_input_digest, source_artifact_id, "
                    " artifact_path, artifact_digest, item_count, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)",
                    (artifact_id, round_record["experiment_id"],
                     workload["input_version_id"], round_record["workload_id"],
                     round_id, stage, artifact_platform, source_artifact_kind,
                     scope_digest, task_input_digest, source_artifact_id,
                     relative_path, artifact_digest, item_count, _now()),
                )
        except sqlite3.IntegrityError as exc:
            temporary_path.unlink(missing_ok=True)
            absolute_path.unlink(missing_ok=True)
            raise ValueError("同一轮次的阶段产物已经存在") from exc
        except Exception:
            temporary_path.unlink(missing_ok=True)
            absolute_path.unlink(missing_ok=True)
            raise
        return self.get_tuning_stage_artifact(artifact_id)

    def get_tuning_stage_artifact(self, artifact_id: str) -> dict:
        """Return one immutable stage artifact record."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_stage_artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"阶段产物不存在: {artifact_id}")
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "input_version_id": row["input_version_id"],
            "workload_id": row["workload_id"],
            "producer_round_id": row["producer_round_id"],
            "stage": row["stage"],
            "platform": row["platform"],
            "source_artifact_kind": row["source_artifact_kind"],
            "scope_digest": row["scope_digest"],
            "task_input_digest": row["task_input_digest"],
            "source_artifact_id": row["source_artifact_id"],
            "artifact_path": row["artifact_path"],
            "artifact_digest": row["artifact_digest"],
            "item_count": row["item_count"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    # -- SPEC011 tuning execution lease ----------------------------------

    _LEASE_TTL_SECONDS = 300  # 5 分钟心跳超时

    def claim_tuning_lease(
        self, *, experiment_id: str, round_id: str, owner_token: str,
        allow_stale_takeover: bool = False,
    ) -> dict:
        """原子 claim 独占租约。"""
        import hashlib as _hashlib
        token_digest = _hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=self._LEASE_TTL_SECONDS)).isoformat()
        now_iso = now.isoformat()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT owner_token_digest, lease_until FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            is_free = row["owner_token_digest"] is None
            is_stale = False
            if not is_free and row["lease_until"]:
                try:
                    lease_time = datetime.fromisoformat(
                        row["lease_until"].replace("Z", "+00:00")
                    )
                    is_stale = lease_time < now
                except (ValueError, TypeError):
                    is_stale = True
            if is_free or (is_stale and allow_stale_takeover):
                conn.execute(
                    "UPDATE tuning_execution_lease "
                    "SET owner_experiment_id = ?, owner_round_id = ?, "
                    "    owner_token_digest = ?, lease_until = ?, "
                    "    heartbeat_at = ?, updated_at = ? "
                    "WHERE id = 1",
                    (experiment_id, round_id, token_digest,
                     lease_until, now_iso, now_iso),
                )
                return {"ok": True, "experiment_id": experiment_id, "round_id": round_id}
            return {"ok": False, "reason": "lease_held"}

    def release_tuning_lease(self, *, owner_token: str) -> None:
        """释放租约（仅持有者可释放）。"""
        import hashlib as _hashlib
        token_digest = _hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = _now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT owner_token_digest FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
            if row and row["owner_token_digest"] == token_digest:
                conn.execute(
                    "UPDATE tuning_execution_lease "
                    "SET owner_experiment_id = NULL, owner_round_id = NULL, "
                    "    owner_token_digest = NULL, lease_until = NULL, "
                    "    heartbeat_at = NULL, updated_at = ? "
                    "WHERE id = 1",
                    (now,),
                )

    def heartbeat_tuning_lease(self, *, owner_token: str) -> None:
        """延长租约心跳。"""
        import hashlib as _hashlib
        token_digest = _hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=self._LEASE_TTL_SECONDS)).isoformat()
        now_iso = now.isoformat()
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_execution_lease "
                "SET heartbeat_at = ?, lease_until = ?, updated_at = ? "
                "WHERE id = 1 AND owner_token_digest = ?",
                (now_iso, lease_until, now_iso, token_digest),
            )

    def get_tuning_lease(self) -> dict:
        """返回当前租约状态。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"owner_experiment_id": None, "owner_round_id": None}
        return {
            "owner_experiment_id": row["owner_experiment_id"],
            "owner_round_id": row["owner_round_id"],
            "lease_until": row["lease_until"],
            "heartbeat_at": row["heartbeat_at"],
        }

    def reconcile_tuning_after_restart(self) -> None:
        """原子恢复中断轮次、阻断所属实验，然后释放旧进程租约。

        不修改 advanced_config_state（FR-042/SC-014）。
        """
        now = _now()
        with self._connection() as conn:
            interrupted = conn.execute(
                "SELECT id, experiment_id FROM tuning_rounds "
                "WHERE status IN ('running', 'issued', 'reported') "
                "ORDER BY created_at, id"
            ).fetchall()
            by_experiment: dict[str, list[str]] = {}
            for row in interrupted:
                by_experiment.setdefault(row["experiment_id"], []).append(row["id"])
            conn.execute(
                "UPDATE tuning_rounds SET status = 'uncertain', "
                "failure_code = COALESCE(failure_code, 'restart_interrupted_round'), "
                "finished_at = COALESCE(finished_at, ?) "
                "WHERE status IN ('running', 'issued', 'reported')",
                (now,),
            )
            for experiment_id, round_ids in by_experiment.items():
                reason = "重启中断了未原子确认的轮次: " + ", ".join(round_ids)
                conn.execute(
                    "UPDATE tuning_experiments SET status = 'blocked', "
                    "blocked_code = 'restart_interrupted_round', "
                    "blocked_reason = ?, updated_at = ? "
                    "WHERE id = ? AND status IN ('queued', 'running')",
                    (reason, now, experiment_id),
                )
            # 状态对账完成后才释放租约；新进程必须重新签发轮次。
            conn.execute(
                "UPDATE tuning_execution_lease "
                "SET owner_experiment_id = NULL, owner_round_id = NULL, "
                "    owner_token_digest = NULL, lease_until = NULL, "
                "    heartbeat_at = NULL, updated_at = ? "
                "WHERE id = 1",
                (now,),
            )

    # -- 测量事件持久化 (data-model.md 2.9) ---------------------------

    # 敏感字段黑名单：这些键名或值内容不得进入测量事件
    _MEASUREMENT_FORBIDDEN_KEYS = frozenset({
        "api_key", "apikey", "secret", "token", "password", "credential",
        "resume_text", "resume", "jd_body", "jd", "model_response",
        "raw_response", "authorization",
    })
