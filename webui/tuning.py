"""SPEC011: 深度调优实验控制模块。

提供实验生命周期、候选管理、租约协调和跨重启恢复的深模块接口。
所有方法只操作实验表族，永不修改 advanced_config_state（FR-042/SC-014）。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from webui.store import TaskStore


# Allowlisted event types for the measurement sink (data-model.md 2.9).
ALLOWED_EVENT_TYPES = frozenset({
    "stage", "batch", "request", "wait", "retry", "item_terminal",
})

# Allowlisted count keys (safe to persist; no credentials/resume/JD body).
ALLOWED_COUNT_KEYS = frozenset({
    "input_count", "output_count", "batch_size", "batch_index",
    "item_index", "status", "attempt", "kept", "dropped",
    "match", "not_match", "uncertain", "error_count", "retry_count",
    "concurrency", "total_items", "processed_items",
})

# Allowlisted metadata keys.
ALLOWED_METADATA_KEYS = frozenset({
    "stage", "step", "batch_index", "truncated_split", "backoff_ms",
    "error_code", "transport_failed", "recovered",
})

_PROCESS_OWNER_TOKENS: dict[str, str] = {}


class MeasurementSink:
    """Allowlisted measurement sink bound to a single round.

    Passed to pipeline_exec / ai.py / boss_cdp_raw so they can record
    measurement events without a direct dependency on TuningController.

    FR-030/SC-006: all waits, cooldowns, retries and recovery time counted.
    SC-007: terminal conservation enforced at aggregation time.
    data-model.md 2.9: credentials, raw resume, raw model response and
    JD body are forbidden — the sink strips/ rejects them before persisting.
    """

    __slots__ = ("_controller", "_round_id", "_monotonic_base")

    def __init__(self, controller: "TuningController", round_id: str):
        self._controller = controller
        self._round_id = round_id
        self._monotonic_base = time.monotonic()

    @property
    def round_id(self) -> str:
        return self._round_id

    def monotonic_ms(self) -> int:
        """Return elapsed monotonic milliseconds since sink creation."""
        return int((time.monotonic() - self._monotonic_base) * 1000)

    def __call__(
        self, event_type: str, stage: str, duration_ms: int,
        *, counts: dict | None = None, error_code: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Record one measurement event (allowlisted keys only)."""
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"不允许的事件类型: {event_type}")
        if duration_ms < 0:
            raise ValueError("duration_ms 必须非负")
        safe_counts = self._filter_counts(counts) if counts else None
        safe_metadata = self._filter_metadata(metadata) if metadata else None
        return self._controller.record_measurement(
            round_id=self._round_id,
            event_type=event_type,
            stage=stage,
            duration_ms=duration_ms,
            started_monotonic_ms=self.monotonic_ms(),
            counts=safe_counts,
            error_code=error_code,
            metadata=safe_metadata,
        )

    def record(
        self, event_type: str, stage: str, duration_ms: int,
        **kwargs,
    ) -> dict:
        """Alias for __call__ accepting kwargs for ergonomic call sites."""
        return self(event_type, stage, duration_ms, **kwargs)

    @staticmethod
    def _filter_counts(counts: dict) -> dict:
        """Keep only allowlisted count keys; drop anything else silently."""
        if not isinstance(counts, dict):
            return {}
        safe = {}
        for key, value in counts.items():
            key_lower = str(key).lower()
            if key_lower in ALLOWED_COUNT_KEYS:
                safe[key_lower] = value
        return safe

    @staticmethod
    def _filter_metadata(metadata: dict) -> dict:
        """Keep only allowlisted metadata keys."""
        if not isinstance(metadata, dict):
            return {}
        safe = {}
        for key, value in metadata.items():
            key_lower = str(key).lower()
            if key_lower in ALLOWED_METADATA_KEYS:
                safe[key_lower] = value
        return safe


class TuningController:
    """实验控制面：管理实验、候选、轮次和租约。

    不持有可变全局状态；所有持久化通过 TaskStore 完成。
    """

    def __init__(self, store: TaskStore):
        self._store = store
        # 每次 controller 实例化时生成一个 owner token 用于租约操作
        self._owner_token = _PROCESS_OWNER_TOKENS.setdefault(
            store.db_path, secrets.token_hex(16),
        )
        self._workspace_root = Path(store.db_path).resolve().parent.parent

    # -- 实验生命周期 ---------------------------------------------------

    def create_experiment(
        self, *, spec_version: str, source_scope: dict,
        workloads: list[dict] | None = None,
    ) -> dict:
        """创建 draft 状态的实验。不启动压力工作（FR-042）。"""
        return self._store.create_tuning_experiment(
            spec_version=spec_version, source_scope=source_scope,
        )

    def create_experiment_with_input(
        self, *, spec_version: str, source_scope: dict,
        workloads: list[dict], quality_context: dict,
    ) -> dict:
        """Create the HTTP experiment draft with its proposed input bundle."""
        return self._store.create_tuning_experiment_with_input(
            spec_version=spec_version,
            source_scope=source_scope,
            workloads=workloads,
            quality_context=quality_context,
            workspace_root=self._workspace_root,
        )

    def confirm_input(self, experiment_id: str) -> dict:
        """Freeze a complete representative workload matrix."""
        result = self._store.confirm_tuning_input(
            experiment_id, workspace_root=self._workspace_root,
        )
        lease = self._store.get_tuning_lease()
        if lease.get("owner_experiment_id") is not None:
            self._store.update_tuning_experiment_status(
                experiment_id, status="blocked",
                blocked_code="execution_conflict",
                blocked_reason="执行环境正被其他调优轮次占用",
            )
            return {**result, "status": "blocked"}
        if self._store._active_worker_count() > 0:
            self._store.update_tuning_experiment_status(
                experiment_id, status="blocked",
                blocked_code="ordinary_task_active",
                blocked_reason="普通任务仍在运行",
            )
            return {**result, "status": "blocked"}
        self._store.update_tuning_experiment_status(
            experiment_id, status="awaiting_instruction",
        )
        return {**result, "status": "awaiting_instruction"}

    def add_candidate(
        self, *, experiment_id: str, stage: str, strategy_step: str,
        config: dict, parent_candidate_id: str | None = None,
    ) -> dict:
        """添加候选配置。配置存入实验表，不写入 advanced_config_state。"""
        return self._store.save_tuning_candidate(
            experiment_id=experiment_id, stage=stage,
            strategy_step=strategy_step, config=config,
            parent_candidate_id=parent_candidate_id,
        )

    def cancel_experiment(self, experiment_id: str) -> None:
        """取消实验。保留证据，不覆盖用户配置（SC-014）。"""
        self._store.update_tuning_experiment_status(
            experiment_id, status="cancelled",
        )
        # 释放租约（如果持有）
        self._store.release_tuning_lease(owner_token=self._owner_token)

    def fail_experiment(
        self, experiment_id: str, *, blocked_code: str,
    ) -> None:
        """标记实验失败。不覆盖用户配置（SC-014）。"""
        # 必须先到达 running 才能转 failed
        exp = self._store.get_tuning_experiment(experiment_id)
        if exp["status"] not in ("running", "evaluating", "blocked"):
            # 按合法路径推进到 running
            self._advance_to_running(experiment_id)
        self._store.update_tuning_experiment_status(
            experiment_id, status="failed", blocked_code=blocked_code,
        )
        self._store.release_tuning_lease(owner_token=self._owner_token)

    def _advance_to_running(self, experiment_id: str) -> None:
        """按合法路径推进实验到 running 状态。"""
        exp = self._store.get_tuning_experiment(experiment_id)
        status = exp["status"]
        if status == "draft":
            self._store.update_tuning_experiment_status(
                experiment_id, status="preflight",
            )
            status = "preflight"
        if status == "preflight":
            self._store.update_tuning_experiment_status(
                experiment_id, status="awaiting_instruction",
            )
            status = "awaiting_instruction"
        if status == "awaiting_instruction":
            self._store.update_tuning_experiment_status(
                experiment_id, status="queued",
            )
            status = "queued"
        if status == "queued":
            self._store.update_tuning_experiment_status(
                experiment_id, status="running",
            )

    def recover_after_restart(self) -> None:
        """重启恢复：running 轮次变 uncertain，释放租约。

        不修改 advanced_config_state（SC-014）。
        """
        self._store.reconcile_tuning_after_restart()

    def apply_completed_version(
        self, *, experiment_id: str, matrix: dict,
    ) -> str:
        """应用完整模式版本。不覆盖最近自定义配置（FR-066）。"""
        version_id = self._store.create_mode_version(
            matrix=matrix, manual_ranges={},
        )
        self._store.apply_mode_version(version_id)
        return version_id

    def create_candidate_mode_version(
        self, *, experiment_id: str, matrix: dict,
        manual_ranges: dict | None = None,
    ) -> dict:
        """Persist one complete nine-slot candidate linked to its experiment."""
        experiment = self._store.get_tuning_experiment(experiment_id)
        if experiment["status"] not in ("running", "evaluating"):
            raise ValueError("只有 running/evaluating 实验可生成候选模式版本")
        validation = self.validate_mode_matrix(matrix)
        if not validation["valid"]:
            raise ValueError(
                f"模式矩阵不完整: {validation['missing_slots']}"
            )
        version_id = self._store.create_mode_version(
            matrix=matrix,
            manual_ranges=manual_ranges or {},
            source_experiment_id=experiment_id,
        )
        return self._store.get_mode_version(version_id)

    def get_experiment_result(self, experiment_id: str) -> dict:
        """Return safe persisted result/evidence; never infer missing gates."""
        experiment = self._store.get_tuning_experiment(experiment_id)
        version = self._store.get_experiment_mode_version(experiment_id)
        with self._store._connection() as conn:
            candidate_rows = conn.execute(
                "SELECT id, stage, strategy_step, status, pressure_rank, "
                "rejection_code, aggregate_metrics_json "
                "FROM tuning_candidates WHERE experiment_id = ? "
                "ORDER BY created_at, id",
                (experiment_id,),
            ).fetchall()
            round_rows = conn.execute(
                "SELECT id, candidate_id, round_kind, repetition_index, status, "
                "metrics_json, evidence_manifest_json, failure_code "
                "FROM tuning_rounds WHERE experiment_id = ? "
                "ORDER BY created_at, id",
                (experiment_id,),
            ).fetchall()
        candidates = []
        for row in candidate_rows:
            try:
                metrics = json.loads(row["aggregate_metrics_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                metrics = {}
            candidates.append({
                "id": row["id"], "stage": row["stage"],
                "strategy_step": row["strategy_step"],
                "status": row["status"], "pressure_rank": row["pressure_rank"],
                "rejection_code": row["rejection_code"], "metrics": metrics,
            })
        evidence = []
        for row in round_rows:
            try:
                metrics = json.loads(row["metrics_json"] or "{}")
                manifest = json.loads(row["evidence_manifest_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                metrics, manifest = {}, {}
            evidence.append({
                "id": row["id"], "candidate_id": row["candidate_id"],
                "round_kind": row["round_kind"],
                "repetition_index": row["repetition_index"],
                "status": row["status"], "failure_code": row["failure_code"],
                "total_duration_ms": metrics.get("total_duration_ms"),
                "artifact_digest": metrics.get("artifact_digest"),
                "artifacts": manifest.get("artifacts", []),
            })
        completion_issues = self._store.get_tuning_completion_issues(experiment_id)
        can_apply = bool(
            experiment["status"] == "completed" and version
            and not completion_issues
        )
        return {
            "experiment_id": experiment_id,
            "status": experiment["status"],
            "can_apply": can_apply,
            "candidate_mode_version_id": version["id"] if version else None,
            "candidate_mode_version_digest": (
                version["version_digest"] if version else None
            ),
            "completion_issues": completion_issues,
            "candidate_summary": candidates,
            "evidence": evidence,
            "block": {
                "code": experiment.get("blocked_code"),
                "reason": experiment.get("blocked_reason"),
            } if experiment.get("blocked_code") else None,
        }

    def apply_candidate_mode_version(
        self, *, experiment_id: str, version_digest: str,
    ) -> dict:
        """Atomically apply the exact complete candidate after completion."""
        result = self.get_experiment_result(experiment_id)
        if not result["can_apply"]:
            raise ValueError("实验结果尚未完整，不能应用")
        if version_digest != result["candidate_mode_version_digest"]:
            raise ValueError("候选模式版本摘要不匹配")
        version_id = result["candidate_mode_version_id"]
        self._store.apply_mode_version(version_id)
        return self._store.get_mode_version(version_id)

    # -- 租约协调 -------------------------------------------------------

    def claim_lease(
        self, *, experiment_id: str, round_id: str,
    ) -> dict:
        """原子 claim 独占租约。"""
        return self._store.claim_tuning_lease(
            experiment_id=experiment_id, round_id=round_id,
            owner_token=self._owner_token,
        )

    def release_lease(self) -> None:
        """释放租约。"""
        self._store.release_tuning_lease(owner_token=self._owner_token)

    def heartbeat_lease(self) -> None:
        """延长租约心跳。"""
        self._store.heartbeat_tuning_lease(owner_token=self._owner_token)

    def get_lease_state(self) -> dict:
        """返回当前租约状态。"""
        return self._store.get_tuning_lease()

    def check_ordinary_task_allowed(self) -> bool:
        """检查普通任务是否可以启动（FR-035）。

        租约被持有时普通任务必须被阻止。
        """
        lease = self._store.get_tuning_lease()
        return lease.get("owner_experiment_id") is None

    # -- 轮次管理 -------------------------------------------------------

    def create_round(
        self, *, experiment_id: str, candidate_id: str, workload_id: str,
        round_kind: str, repetition_index: int,
    ) -> dict:
        """创建 planned 状态的轮次。"""
        return self._store.create_tuning_round(
            experiment_id=experiment_id, candidate_id=candidate_id,
            workload_id=workload_id, round_kind=round_kind,
            repetition_index=repetition_index,
        )

    def start_round(self, round_id: str) -> None:
        """按 issued + lease 门禁开始轮次，禁止跳过审计状态。"""
        round_record = self._store.get_tuning_round(round_id)
        if round_record["status"] == "confirmed":
            return
        if round_record["status"] == "planned":
            self._store.update_tuning_round_status(round_id, status="issued")
        elif round_record["status"] != "issued":
            raise ValueError(f"轮次状态 {round_record['status']} 不能开始")
        claimed = self.claim_lease(
            experiment_id=round_record["experiment_id"], round_id=round_id,
        )
        if not claimed.get("ok"):
            raise ValueError("独占租约被占用，轮次不能开始")
        try:
            self._advance_to_running(round_record["experiment_id"])
            self._store.update_tuning_round_status(round_id, status="running")
        except Exception:
            self.release_lease()
            raise

    def confirm_round(
        self, round_id: str, *, metrics: dict | None = None,
    ) -> None:
        """沿 running → reported → confirmed 原子门禁确认轮次。"""
        round_record = self._store.get_tuning_round(round_id)
        if round_record["status"] == "confirmed":
            return
        if round_record["status"] in ("planned", "issued"):
            self.start_round(round_id)
            round_record = self._store.get_tuning_round(round_id)
        if round_record["status"] == "running":
            self._store.update_tuning_round_status(round_id, status="reported")
        elif round_record["status"] != "reported":
            raise ValueError(f"轮次状态 {round_record['status']} 不能确认")
        if metrics:
            self._save_round_metrics(round_id, metrics)
        self._store.update_tuning_round_status(round_id, status="confirmed")
        experiment = self._store.get_tuning_experiment(round_record["experiment_id"])
        if experiment["status"] == "running":
            self._store.update_tuning_experiment_status(
                round_record["experiment_id"], status="evaluating",
            )
        self.release_lease()

    def get_round(self, round_id: str) -> dict:
        """返回轮次状态。"""
        return self._store.get_tuning_round(round_id)

    def persist_stage_artifact(
        self, *, round_id: str, stage: str, payload: dict,
        source_artifact_id: str | None = None,
    ) -> dict:
        """Persist one append-only stage result under the experiment root."""
        return self._store.save_tuning_stage_artifact(
            round_id=round_id, stage=stage, payload=payload,
            workspace_root=self._workspace_root,
            source_artifact_id=source_artifact_id,
        )

    def create_rerun_for_uncertain(self, round_id: str) -> dict | None:
        """为 uncertain 轮次创建重跑（新 repetition）。

        FR-039: 不确定轮次只重跑一次。
        """
        original = self._store.get_tuning_round(round_id)
        if original["status"] != "uncertain":
            return None
        # 创建新的 repetition（索引+1）
        new_repetition = original["repetition_index"] + 1
        with self._store._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM tuning_rounds WHERE candidate_id = ? "
                "AND workload_id = ? AND round_kind = ? AND repetition_index = ?",
                (original["candidate_id"], original["workload_id"],
                 original["round_kind"], new_repetition),
            ).fetchone()
        if existing is not None:
            return None
        new_round = self._store.create_tuning_round(
            experiment_id=original["experiment_id"],
            candidate_id=original["candidate_id"],
            workload_id=original["workload_id"],
            round_kind=original["round_kind"],
            repetition_index=new_repetition,
        )
        # 返回包含 repetition_index 的完整信息
        return {
            "id": new_round["id"],
            "status": new_round["status"],
            "repetition_index": new_repetition,
        }

    def _save_round_metrics(self, round_id: str, metrics: dict) -> None:
        """保存轮次指标到数据库。"""
        metrics_json = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
        now = self._store._now() if hasattr(self._store, '_now') else None
        with self._store._connection() as conn:
            conn.execute(
                "UPDATE tuning_rounds SET metrics_json = ? WHERE id = ?",
                (metrics_json, round_id),
            )

    # -- 测量事件 (data-model.md 2.9, T016/T017) -----------------------

    def record_measurement(
        self, *, round_id: str, event_type: str, stage: str,
        duration_ms: int, started_monotonic_ms: int | None = None,
        counts: dict | None = None, error_code: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """记录一条测量事件。

        FR-030/SC-006: 所有等待、冷却、重试、恢复时间计入总耗时。
        SC-007: 终态守恒。
        data-model.md 2.9: 禁止凭据、原始简历、原始模型响应和 JD 正文。
        """
        return self._store.save_tuning_measurement_event(
            round_id=round_id, event_type=event_type, stage=stage,
            duration_ms=duration_ms,
            started_monotonic_ms=started_monotonic_ms,
            counts=counts, error_code=error_code, metadata=metadata,
        )

    def list_measurements(self, round_id: str) -> list[dict]:
        """返回轮次的全部测量事件，按 seq 升序。"""
        return self._store.list_tuning_measurement_events(round_id)

    def aggregate_measurements(self, round_id: str) -> dict:
        """聚合轮次的测量摘要（MeasurementSummary）。

        FR-030: total_duration_ms 包含工作、等待、冷却、重试和恢复。
        SC-007: terminal_count == input_count, missing=0, duplicate=0。
        """
        events = self.list_measurements(round_id)
        total_duration_ms = 0
        stage_durations_ms: dict[str, int] = {}
        wait_duration_ms = 0
        retry_duration_ms = 0
        attempt_count = 0
        retry_count = 0
        error_counts: dict[str, int] = {}
        input_count = 0
        terminal_count = 0
        success_count = 0
        failed_count = 0
        seen_item_indices: set = set()
        duplicate_count = 0

        for ev in events:
            ev_type = ev["event_type"]
            stage = ev["stage"] or "unknown"
            dur = ev["duration_ms"] or 0
            counts = ev.get("counts") or {}
            error_code = ev.get("error_code")

            # 总耗时 = 所有事件时长之和（工作+等待+重试）
            total_duration_ms += dur
            stage_durations_ms[stage] = stage_durations_ms.get(stage, 0) + dur

            if ev_type == "wait":
                wait_duration_ms += dur
            elif ev_type == "retry":
                retry_duration_ms += dur
                retry_count += 1
                attempt_count += 1
            elif ev_type == "request":
                attempt_count += 1
                if error_code:
                    error_counts[error_code] = error_counts.get(error_code, 0) + 1
            elif ev_type == "item_terminal":
                terminal_count += 1
                item_idx = counts.get("item_index")
                if item_idx is not None:
                    if item_idx in seen_item_indices:
                        duplicate_count += 1
                    else:
                        seen_item_indices.add(item_idx)
                status = counts.get("status", "")
                if status == "success":
                    success_count += 1
                elif status in ("failed", "unavailable"):
                    failed_count += 1
                if "input_count" in counts:
                    input_count = max(input_count, int(counts["input_count"]))
            elif ev_type == "stage":
                if "input_count" in counts:
                    input_count = max(input_count, int(counts["input_count"]))

        # 如果 input_count 已知，missing = input - terminal
        missing_count = 0
        if input_count > 0:
            missing_count = max(0, input_count - terminal_count)

        return {
            "total_duration_ms": total_duration_ms,
            "stage_durations_ms": stage_durations_ms,
            "work_duration_ms": total_duration_ms - wait_duration_ms - retry_duration_ms,
            "wait_duration_ms": wait_duration_ms,
            "retry_duration_ms": retry_duration_ms,
            "attempt_count": attempt_count,
            "retry_count": retry_count,
            "error_counts": error_counts,
            "input_count": input_count,
            "terminal_count": terminal_count,
            "success_count": success_count,
            "failed_count": failed_count,
            "missing_count": missing_count,
            "duplicate_count": duplicate_count,
            "quality_diff_count": 0,
        }

    def build_measurement_sink(self, round_id: str) -> MeasurementSink:
        """构建绑定到指定轮次的 allowlisted measurement sink。

        T017: pipeline_exec / ai.py / boss_cdp_raw 通过此 sink 记录测量事件，
        无需直接依赖 TuningController 内部方法。sink 在写入前过滤敏感字段。
        """
        return MeasurementSink(self, round_id)

    def aggregate_round_evidence(self, round_id: str) -> dict:
        """聚合轮次证据：MeasurementSummary + artifact_digest。

        T017: 执行报告只能引用这些证据及其摘要（plan.md §3）。
        返回的 artifact_digest 基于 MeasurementSummary 的规范 JSON。
        """
        summary = self.aggregate_measurements(round_id)
        # 规范化 JSON 摘要用于计算 digest
        canonical = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        import hashlib
        artifact_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            "round_id": round_id,
            "summary": summary,
            "artifact_digest": artifact_digest,
            "event_count": len(self.list_measurements(round_id)),
        }

    # -- T020: 质量参考与逐项比较 (FR-026/027/028/034) -------------------

    def build_quality_reference(
        self, *, experiment_id: str, input_version_id: str,
        baseline_round_results: list[list[dict]],
    ) -> dict:
        """FR-026: 通过低压力配置的重复运行建立质量参考。

        从多轮基线的逐项结果计算共识和波动范围。
        baseline_round_results: 每个元素是一轮的逐项结果列表。
        """
        if not baseline_round_results:
            raise ValueError("baseline_round_results 不能为空")
        repetition_count = len(baseline_round_results)
        # 收集每个 item_index 的全部 verdict
        item_verdicts: dict[int, list[str]] = {}
        for rep in baseline_round_results:
            for item in rep:
                idx = item["item_index"]
                verdict = item["verdict"]
                item_verdicts.setdefault(idx, []).append(verdict)
        # 计算共识和稳定性
        item_results_list = []
        per_item_stability = {}
        items_with_variation = []
        for idx in sorted(item_verdicts.keys()):
            verdicts = item_verdicts[idx]
            # 共识 = 出现次数最多的 verdict
            verdict_counts: dict[str, int] = {}
            for v in verdicts:
                verdict_counts[v] = verdict_counts.get(v, 0) + 1
            consensus = max(verdict_counts, key=verdict_counts.get)
            # 稳定性 = 与共识一致的重复次数 / 总重复次数
            agreement = verdict_counts[consensus]
            stability = agreement / repetition_count
            per_item_stability[idx] = stability
            if stability < 1.0:
                items_with_variation.append(idx)
            item_results_list.append({
                "item_index": idx, "verdict": consensus, "stability": stability,
            })
        average_stability = (
            sum(per_item_stability.values()) / len(per_item_stability)
            if per_item_stability else 0.0
        )
        item_results = {"items": item_results_list}
        variation_summary = {
            "repetition_count": repetition_count,
            "item_count": len(item_verdicts),
            "per_item_stability": per_item_stability,
            "average_stability": average_stability,
            "items_with_variation": items_with_variation,
        }
        # 计算 reference_digest
        canonical = json.dumps(item_results, ensure_ascii=False, sort_keys=True)
        reference_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self._store.save_quality_reference(
            experiment_id=experiment_id,
            input_version_id=input_version_id,
            item_results=item_results,
            variation_summary=variation_summary,
            reference_digest=reference_digest,
        )

    def confirm_quality_reference(self, reference_id: str) -> dict:
        """FR-026: 确认质量参考。

        确认时将实验内其他 confirmed/review_required 参考标记为 superseded。
        """
        ref = self._store.get_quality_reference(reference_id)
        if ref["status"] not in ("building", "review_required"):
            raise ValueError(
                f"只有 building 或 review_required 状态的参考才能确认，当前: {ref['status']}"
            )
        # supersede 同实验的其他参考
        self._store.supersede_quality_references(
            ref["experiment_id"], except_id=reference_id,
        )
        # 确认当前参考
        updated = self._store.update_quality_reference_status(
            reference_id, status="confirmed",
        )
        # 设置为实验的活动参考
        self._store.set_experiment_quality_reference(
            ref["experiment_id"], reference_id,
        )
        return updated

    def get_quality_reference(self, reference_id: str) -> dict:
        """返回质量参考记录。"""
        return self._store.get_quality_reference(reference_id)

    def get_active_quality_reference(self, experiment_id: str) -> dict | None:
        """返回实验的活动质量参考（最近的 confirmed 版本）。"""
        refs = self._store.list_quality_references(experiment_id)
        for ref in refs:
            if ref["status"] == "confirmed":
                return ref
        return None

    def enforce_reference_digest_match(
        self, *, reference_id: str, expected_digest: str,
    ) -> bool:
        """data-model 2.4: 候选只能与 manifest 中记录的参考摘要匹配的参考比较。

        digest 不匹配时抛出 ValueError。
        """
        ref = self._store.get_quality_reference(reference_id)
        if ref["reference_digest"] != expected_digest:
            raise ValueError(
                "参考摘要不匹配：候选 manifest 中的参考摘要与活动参考不一致"
            )
        return True

    def compare_results_against_reference(
        self, *, candidate_item_results: list[dict],
        reference_id: str,
        expected_digest: str | None = None,
    ) -> dict:
        """FR-027: 逐项比较候选结果与参考。

        如果提供了 expected_digest，先校验摘要匹配（data-model 2.4）。
        只有用 confirmed 状态的参考才能比较。
        """
        ref = self._store.get_quality_reference(reference_id)
        if ref["status"] != "confirmed":
            raise ValueError(
                f"只能与 confirmed 状态的参考比较，当前: {ref['status']}"
            )
        if expected_digest is not None:
            self.enforce_reference_digest_match(
                reference_id=reference_id, expected_digest=expected_digest,
            )
        # 构建 reference verdict 字典
        ref_verdicts = {
            item["item_index"]: item["verdict"]
            for item in ref["item_results"].get("items", [])
        }
        candidate_verdicts = {}
        for item in candidate_item_results:
            idx = item["item_index"]
            if idx in candidate_verdicts:
                raise ValueError(f"候选结果包含重复 item_index: {idx}")
            candidate_verdicts[idx] = item["verdict"]
        differing_items = []
        matching_count = 0
        all_indexes = sorted(set(ref_verdicts) | set(candidate_verdicts))
        for idx in all_indexes:
            ref_verdict = ref_verdicts.get(idx)
            candidate_verdict = candidate_verdicts.get(idx)
            if candidate_verdict == ref_verdict:
                matching_count += 1
            else:
                differing_items.append({
                    "item_index": idx,
                    "reference_verdict": ref_verdict,
                    "candidate_verdict": candidate_verdict,
                })
        total_items = len(all_indexes)
        return {
            "total_items": total_items,
            "matching_items": matching_count,
            "differing_items": differing_items,
            "diff_count": len(differing_items),
        }

    def classify_quality_differences(
        self, *, diffs: list[dict], reference_id: str,
    ) -> dict:
        """FR-028/FR-034: 将差异分类为正常波动内或需审核。

        - 如果差异项在基线中本身就有波动（stability < 1.0）→ within_variation
        - 如果差异项在基线中完全稳定（stability == 1.0）→ review_required
        """
        ref = self._store.get_quality_reference(reference_id)
        variation = ref["variation_summary"]
        items_with_variation = set(variation.get("items_with_variation", []))
        within_variation = []
        review_required = []
        for diff in diffs:
            idx = diff["item_index"]
            if idx in items_with_variation:
                within_variation.append(diff)
            else:
                review_required.append(diff)
        return {
            "within_variation": within_variation,
            "review_required": review_required,
            "review_count": len(review_required),
        }

    def mark_review_required(
        self, *, reference_id: str, reviewed_item_ids: list[int],
    ) -> dict:
        """FR-034: 将参考标记为 review_required，记录需审核的 item。"""
        return self._store.update_quality_reference_status(
            reference_id, status="review_required",
            reviewed_item_ids=reviewed_item_ids,
        )

    def resolve_reviewed_differences(
        self, *, reference_id: str,
        resolved_item_results: list[dict],
    ) -> dict:
        """FR-034: 用户复核后创建新参考版本。

        旧参考被 superseded，新参考直接为 confirmed 状态。
        """
        old_ref = self._store.get_quality_reference(reference_id)
        # 用解决后的 item_results 创建新参考
        item_results = {"items": resolved_item_results}
        # 保留原 variation_summary
        variation_summary = old_ref["variation_summary"]
        canonical = json.dumps(item_results, ensure_ascii=False, sort_keys=True)
        reference_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        new_ref = self._store.save_quality_reference(
            experiment_id=old_ref["experiment_id"],
            input_version_id=old_ref["input_version_id"],
            item_results=item_results,
            variation_summary=variation_summary,
            reference_digest=reference_digest,
        )
        # supersede 旧参考
        self._store.supersede_quality_references(
            old_ref["experiment_id"], except_id=new_ref["id"],
        )
        # 确认新参考
        confirmed = self._store.update_quality_reference_status(
            new_ref["id"], status="confirmed",
        )
        self._store.set_experiment_quality_reference(
            old_ref["experiment_id"], new_ref["id"],
        )
        return confirmed

    # -- T022: 任务单签发、报告校验与渲染 (FR-043~049) -------------------

    # manifest 必填字段（不含 server 生成的 manifest_digest/issued_at）
    _MANIFEST_REQUIRED_FIELDS = frozenset({
        "schema_version", "task_id", "experiment_id", "candidate_id",
        "round_id", "spec_version", "objective", "round_kind",
        "strategy_step", "repetition_index", "preconditions",
        "frozen_input", "execution_config", "fixed_fields",
        "execution_steps", "monitoring", "retry_policy",
        "stop_conditions", "allowed_writes", "required_artifacts",
        "forbidden_actions", "report_contract",
    })

    # execution_config 必填的九字段
    _EXEC_CONFIG_REQUIRED_FIELDS = frozenset({
        "schema_version", "inter_combo_delay", "detail_batch_size",
        "detail_interval", "detail_reset_every", "detail_batch_cooldown",
        "screen_batch_size", "screen_concurrency",
        "match_batch_size", "match_concurrency",
    })

    # 禁止的占位符和自由裁量语言
    _PLACEHOLDER_PATTERNS = [
        "<placeholder>", "<tbd>", "<value>", "<参数>", "<parameter>",
        "as appropriate", "if needed", "as needed", "choose as",
        "根据情况选择", "酌情", "视情况",
    ]

    # 合法的停止条件动作（单一明确动作）
    _VALID_STOP_ACTIONS = frozenset({
        "stop_new_work_and_block_report",
        "execute_named_retry",
        "block_and_report",
        "stop",
    })

    # 报告必填字段
    _REPORT_REQUIRED_FIELDS = frozenset({
        "schema_version", "report_id", "task_id", "experiment_id",
        "candidate_id", "round_id", "manifest_digest", "status",
        "preflight", "steps", "program_evidence", "artifacts",
        "stop_reason", "unexecuted_steps", "started_at", "finished_at",
    })

    # 报告禁止的执行者字段
    _REPORT_FORBIDDEN_FIELDS = frozenset({
        "parameter_suggestions", "candidate_ranking",
        "next_candidate", "mode_recommendation",
    })

    # 执行者禁止动作关键词（出现在 notes 中时拒绝）
    # FR-046: 执行者只能机械执行任务单，禁止自行修改代码、调整参数、
    # 覆盖结果、选择候选或越界写入。
    _FORBIDDEN_ACTION_KEYWORDS = [
        # 修改源代码或任何 .py 文件
        "修改源代码", "修改了", "edit source", "modify source",
        "alter acceptance", "change_acceptance",
        # 调整参数/超时/验收（执行者无权自行调整）
        "调整验收", "修改验收", "调整超时", "调整参数", "调整配置",
        # 选择其他候选或覆盖先前结果
        "select_another", "overwrite_prior", "覆盖结果", "覆盖先前",
        # 越界写入
        "write_outside", "越界写入",
        # 自行排名或推荐
        "候选排名", "parameter_suggestion", "建议参数",
    ]

    # 禁止动作文件扩展名（出现在 notes 中时拒绝，FR-046）
    _FORBIDDEN_ACTION_FILE_HINTS = [
        ".py", "pipeline_exec", "source.py", "ai.py", "app.py",
        "tuning.py", "store.py", "execution_config",
    ]

    def _validate_manifest(self, manifest: dict) -> None:
        """FR-044/045: 校验 manifest 完整性和合法性。"""
        # 1. 必填字段
        missing = self._MANIFEST_REQUIRED_FIELDS - set(manifest.keys())
        if missing:
            raise ValueError(f"manifest 缺少必填字段: {sorted(missing)}")
        # 2. execution_config 九字段
        config = manifest.get("execution_config", {})
        config_missing = self._EXEC_CONFIG_REQUIRED_FIELDS - set(config.keys())
        if config_missing:
            raise ValueError(
                f"execution_config 缺少必填字段: {sorted(config_missing)}"
            )
        from webui.execution_config import ExecutionConfigSnapshot
        config_snapshot = ExecutionConfigSnapshot.from_dict(config)
        experiment = self._store.get_tuning_experiment(manifest["experiment_id"])
        candidate = self._store.get_tuning_candidate(manifest["candidate_id"])
        round_record = self._store.get_tuning_round(manifest["round_id"])
        if experiment["status"] != "awaiting_instruction":
            raise ValueError("实验不处于 awaiting_instruction，不能签发任务单")
        if candidate["experiment_id"] != experiment["id"]:
            raise ValueError("候选不属于 manifest 实验")
        if (
            round_record["experiment_id"] != experiment["id"]
            or round_record["candidate_id"] != candidate["id"]
        ):
            raise ValueError("轮次归属与 manifest 不一致")
        if round_record["status"] != "planned":
            raise ValueError("只有 planned 轮次可以签发任务单")
        if (
            round_record["round_kind"] != manifest["round_kind"]
            or round_record["repetition_index"] != manifest["repetition_index"]
        ):
            raise ValueError("轮次类型或重复序号与持久化记录不一致")
        if experiment["spec_version"] != manifest["spec_version"]:
            raise ValueError("spec_version 与实验不一致")
        if (
            config_snapshot.config_digest != candidate["config_digest"]
            or config_snapshot.to_dict() != candidate["config"]
        ):
            raise ValueError("execution_config 与候选冻结配置不一致")
        bundle = self._store.get_tuning_input_bundle(experiment["id"])
        frozen = manifest["frozen_input"]
        workload = next(
            (item for item in bundle["workloads"] if item["id"] == round_record["workload_id"]),
            None,
        )
        if workload is None:
            raise ValueError("轮次 workload 不属于实验输入版本")
        expected_frozen = {
            "input_version_id": bundle["input_version"]["id"],
            "workload_id": workload["id"], "task_size": workload["task_size"],
            "structure_index": workload["structure_index"],
            "scope_digest": workload["scope"]["scope_digest"],
            "artifact_digest": workload["artifact_digest"],
            "quality_context_digest": bundle["input_version"][
                "quality_context_digest"
            ],
            "planned_pages": workload["planned_pages"],
        }
        for key, value in expected_frozen.items():
            if frozen.get(key) != value:
                raise ValueError(f"frozen_input.{key} 与冻结工作负载不一致")
        source_rules = {
            "detail": "list", "rough": "list", "fine": "detail",
        }
        source_artifact_id = frozen.get("source_artifact_id")
        if manifest["round_kind"] in source_rules:
            if not source_artifact_id:
                raise ValueError("复用阶段轮次缺少 source_artifact_id")
            try:
                source_artifact = self._store.get_tuning_stage_artifact(
                    str(source_artifact_id)
                )
            except KeyError as exc:
                raise ValueError("source_artifact 阶段产物不存在") from exc
            if (
                source_artifact["experiment_id"] != experiment["id"]
                or source_artifact["input_version_id"]
                != bundle["input_version"]["id"]
                or source_artifact["workload_id"] != workload["id"]
                or source_artifact["status"] != "ready"
            ):
                raise ValueError("source_artifact 阶段产物身份不匹配")
            if source_artifact["stage"] != source_rules[manifest["round_kind"]]:
                raise ValueError("source_artifact 阶段类型不满足复用规则")
            if (
                frozen.get("source_artifact_path")
                != source_artifact["artifact_path"]
                or frozen.get("source_artifact_digest")
                != source_artifact["artifact_digest"]
            ):
                raise ValueError("source_artifact 路径或摘要与持久化记录不一致")
        elif any(frozen.get(key) for key in (
            "source_artifact_id", "source_artifact_path",
            "source_artifact_digest",
        )):
            raise ValueError("list/end_to_end 轮次不得复用阶段产物")
        fixed = manifest["fixed_fields"]
        for key in (
            "keywords", "scope_kind", "cities", "pages_per_combination",
            "planned_pages", "task_size",
        ):
            if fixed.get(key) != workload["scope"].get(key):
                raise ValueError(f"fixed_fields.{key} 与冻结工作负载不一致")
        if not manifest["preconditions"] or not manifest["execution_steps"]:
            raise ValueError("preconditions 和 execution_steps 不能为空")
        if not manifest["required_artifacts"]:
            raise ValueError("required_artifacts 不能为空")
        # 3. 禁止占位符和自由裁量语言
        manifest_text = json.dumps(manifest, ensure_ascii=False)
        for pattern in self._PLACEHOLDER_PATTERNS:
            if pattern.lower() in manifest_text.lower():
                raise ValueError(
                    f"manifest 包含禁止的占位符或自由裁量语言: {pattern}"
                )
        # 4. 路径包含性
        experiment_root = f"tuning/{manifest['experiment_id']}/"
        all_paths = list(manifest.get("allowed_writes", []))
        all_paths.extend(
            artifact.get("path", "") for artifact in manifest.get("required_artifacts", [])
        )
        all_paths.extend([
            manifest.get("monitoring", {}).get("final_artifact_path", ""),
            manifest.get("frozen_input", {}).get("artifact_manifest_path", ""),
        ])
        source_path = manifest.get("frozen_input", {}).get(
            "source_artifact_path"
        )
        if source_path:
            all_paths.append(source_path)
        for write_path in all_paths:
            normalized = str(write_path).replace("\\", "/")
            if (not self._is_safe_experiment_path(str(write_path))
                    or not normalized.startswith(experiment_root)):
                raise ValueError(
                    f"manifest 包含实验根目录外路径: {write_path}"
                )
        # 5. 停止条件唯一动作
        for cond in manifest.get("stop_conditions", []):
            action = cond.get("action", "")
            if action not in self._VALID_STOP_ACTIONS:
                raise ValueError(
                    f"停止条件 {cond.get('code')} 的动作不合法或模糊: {action}"
                )
        # 6. 步骤不能让执行者编辑源代码或选择候选
        for step in manifest.get("execution_steps", []):
            instruction = step.get("instruction", "").lower()
            if any(kw in instruction for kw in [
                "edit source", "modify source", "select candidate",
                "choose next", "编辑源代码", "选择候选",
            ]):
                raise ValueError(
                    f"步骤 {step.get('seq')} 包含禁止的执行者动作"
                )

    def _is_safe_experiment_path(self, path: str) -> bool:
        """检查路径是否安全（在实验根目录内，不是绝对路径，不含 ..）。"""
        if not path:
            return False
        # 绝对路径不安全
        if len(path) > 1 and path[1] == ":":
            return False
        if path.startswith("/"):
            return False
        # 含 .. 的路径不安全
        parts = path.replace("\\", "/").split("/")
        if ".." in parts:
            return False
        # 必须以 tuning/ 开头
        if not path.replace("\\", "/").startswith("tuning/"):
            return False
        return True

    def issue_manifest(self, manifest_payload: dict) -> dict:
        """FR-043/044: 校验并签发一份不可变任务单。

        签发后 manifest_digest 不可篡改，轮次状态更新为 issued。
        """
        # 校验
        self._validate_manifest(manifest_payload)
        # 计算摘要（不含 manifest_digest 字段本身）
        canonical = json.dumps(
            {k: v for k, v in manifest_payload.items()
             if k != "manifest_digest"},
            ensure_ascii=False, sort_keys=True,
        )
        manifest_digest = "sha256:" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        # 渲染路径
        exp_id = manifest_payload["experiment_id"]
        task_id = manifest_payload["task_id"]
        rendered_path = f"tuning/{exp_id}/tasks/{task_id}.md"
        # 持久化
        manifest_json = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True)
        try:
            result = self._store.issue_task_manifest_atomic(
                experiment_id=exp_id,
                candidate_id=manifest_payload["candidate_id"],
                round_id=manifest_payload["round_id"],
                manifest_version=manifest_payload["schema_version"],
                manifest_json=manifest_json,
                manifest_digest=manifest_digest,
                rendered_task_path=rendered_path,
                owner_token=self._owner_token,
            )
            markdown = self.render_manifest_markdown(result["manifest_id"])
            absolute_path = (self._workspace_root / rendered_path).resolve()
            expected_root = (self._workspace_root / "tuning" / exp_id).resolve()
            if expected_root not in absolute_path.parents:
                raise ValueError("渲染任务单路径越过实验根目录")
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = absolute_path.with_suffix(absolute_path.suffix + ".tmp")
            temporary_path.write_text(markdown, encoding="utf-8")
            temporary_path.replace(absolute_path)
        except Exception:
            self.release_lease()
            raise
        return result

    def get_manifest(self, manifest_id: str) -> dict:
        """返回已签发的任务单。"""
        record = self._store.get_task_manifest(manifest_id)
        return {
            "manifest_id": record["id"],
            "manifest": record["manifest"],
            "manifest_digest": record["manifest_digest"],
            "rendered_task_path": record["rendered_task_path"],
            "status": record["status"],
            "issued_at": record["issued_at"],
        }

    def execute_manifest(self, manifest_id: str) -> dict:
        """重新核验不可变摘要，并原子开始已签发轮次。"""
        record = self._store.get_task_manifest(manifest_id)
        canonical = json.dumps(
            {key: value for key, value in record["manifest"].items()
             if key != "manifest_digest"},
            ensure_ascii=False, sort_keys=True,
        )
        digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if digest != record["manifest_digest"]:
            raise ValueError("manifest 摘要与签发记录不一致")
        return self._store.start_task_manifest_atomic(
            manifest_id, owner_token=self._owner_token,
        )

    def accept_report(self, *, manifest_id: str, report: dict) -> dict:
        """校验后在一个事务中保存报告、推进状态并释放租约。"""
        validation_errors: list[str] = []
        try:
            self.validate_report(manifest_id=manifest_id, report=report)
            validation_status = "accepted"
        except ValueError as exc:
            validation_status = "rejected"
            validation_errors = [str(exc)]
        saved = self._store.save_executor_report_atomic(
            manifest_id=manifest_id, report_version=1,
            report_json=json.dumps(report, ensure_ascii=False, sort_keys=True),
            reported_manifest_digest=report.get("manifest_digest", ""),
            evidence_digest=report.get("program_evidence", {}).get(
                "program_report_digest", ""),
            validation_status=validation_status,
            validation_errors=validation_errors,
            report_status=report.get("status"), owner_token=self._owner_token,
        )
        if validation_errors:
            raise ValueError(validation_errors[0])
        return saved

    def validate_report(
        self, *, manifest_id: str, report: dict,
    ) -> dict:
        """FR-048/049: 校验执行者报告。

        返回 {"valid": True/False, "errors": [...]}。
        校验失败时抛出 ValueError。
        """
        manifest_record = self._store.get_task_manifest(manifest_id)
        manifest = manifest_record["manifest"]
        errors = []
        # 1. 必填字段
        missing = self._REPORT_REQUIRED_FIELDS - set(report.keys())
        if missing:
            raise ValueError(f"报告缺少必填字段: {sorted(missing)}")
        # 2. 禁止的执行者字段
        for field in self._REPORT_FORBIDDEN_FIELDS:
            if field in report:
                raise ValueError(f"报告包含禁止的执行者字段: {field}")
        # 3. manifest_digest 匹配
        if report["manifest_digest"] != manifest_record["manifest_digest"]:
            raise ValueError("报告中的 manifest_digest 与签发的不一致")
        # 4. ID 匹配
        if report["task_id"] != manifest["task_id"]:
            raise ValueError("报告中的 task_id 与 manifest 不一致")
        if report["experiment_id"] != manifest["experiment_id"]:
            raise ValueError("报告中的 experiment_id 与 manifest 不一致")
        if report["candidate_id"] != manifest["candidate_id"]:
            raise ValueError("报告中的 candidate_id 与 manifest 不一致")
        if report["round_id"] != manifest["round_id"]:
            raise ValueError("报告中的 round_id 与 manifest 不一致")
        if report["status"] not in ("completed", "blocked"):
            raise ValueError("报告 status 只能是 completed 或 blocked")
        try:
            started_at = datetime.fromisoformat(report["started_at"].replace("Z", "+00:00"))
            finished_at = datetime.fromisoformat(report["finished_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("报告时间戳必须是 ISO-8601") from exc
        if finished_at < started_at:
            raise ValueError("报告 finished_at 不能早于 started_at")
        expected_preflight = [item.get("id") for item in manifest["preconditions"]]
        actual_preflight = [item.get("id") for item in report["preflight"]]
        if actual_preflight != expected_preflight:
            raise ValueError("报告 preflight 与 manifest 顺序或标识不一致")
        expected_steps = [item.get("seq") for item in manifest["execution_steps"]]
        actual_steps = [item.get("seq") for item in report["steps"]]
        if report["status"] == "completed":
            steps_match = actual_steps == expected_steps
        else:
            unexecuted_steps = report.get("unexecuted_steps", [])
            steps_match = (
                actual_steps + unexecuted_steps == expected_steps
                and not (set(actual_steps) & set(unexecuted_steps))
            )
        if not steps_match:
            raise ValueError("报告 steps 与 manifest 顺序或编号不一致")
        # 5. blocked 报告需要 stop_reason
        if report["status"] == "blocked":
            if not report.get("stop_reason"):
                raise ValueError("blocked 报告必须包含 stop_reason")
            if not report.get("unexecuted_steps"):
                pass  # unexecuted_steps 可以为空列表，但不能缺失
        # 6. 检测禁止动作（FR-046）
        notes_text = " ".join(str(n) for n in report.get("executor_notes", []))
        notes_lower = notes_text.lower()
        for keyword in self._FORBIDDEN_ACTION_KEYWORDS:
            if keyword.lower() in notes_lower:
                raise ValueError(
                    f"执行者报告透露了禁止动作: {keyword}"
                )
        # 6b. 检测禁止动作文件扩展名（如提及修改 .py 文件）
        # 只有当 notes 同时包含"修改/edit/modify"等动词时才触发
        edit_verbs = ["修改", "edit", "modify", "alter", "change", "调整"]
        if any(verb.lower() in notes_lower for verb in edit_verbs):
            for hint in self._FORBIDDEN_ACTION_FILE_HINTS:
                if hint.lower() in notes_lower:
                    raise ValueError(
                        f"执行者报告透露了修改源代码: {hint}"
                    )
        # 7. program_evidence 完整性
        evidence = report.get("program_evidence", {})
        required_evidence_fields = [
            "program_report_path", "program_report_digest",
            "config_digest", "scope_digest", "input_artifact_digest",
            "total_duration_ms", "terminal_count",
        ]
        for field in required_evidence_fields:
            if field not in evidence:
                errors.append(f"program_evidence 缺少字段: {field}")
        if errors:
            raise ValueError("; ".join(errors))
        # 8. FR-049: 冻结摘要、实际文件和报告产物三方一致。
        if evidence.get("config_digest") != manifest["execution_config"].get("config_digest"):
            raise ValueError("program_evidence.config_digest 与 manifest 不一致")
        frozen_input = manifest["frozen_input"]
        if evidence.get("scope_digest") != frozen_input.get("scope_digest"):
            raise ValueError("program_evidence.scope_digest 与 manifest 不一致")
        if evidence.get("input_artifact_digest") != frozen_input.get("artifact_digest"):
            raise ValueError("program_evidence.input_artifact_digest 与 manifest 不一致")
        program_report_digest = evidence.get("program_report_digest")
        program_report_path = evidence.get("program_report_path")
        artifacts = report.get("artifacts", [])
        matching_artifact = None
        for art in artifacts:
            if art.get("artifact_type") == "program_report":
                matching_artifact = art
                break
            # 也通过路径匹配
            if (program_report_path
                    and art.get("path") == program_report_path):
                matching_artifact = art
                break
        if matching_artifact is None:
            raise ValueError("报告 artifacts 缺少 program_report")
        if matching_artifact.get("digest") != program_report_digest:
            raise ValueError("program_evidence 摘要与 artifacts 不一致")
        expected_root = (self._workspace_root / "tuning" / manifest["experiment_id"]).resolve()
        report_path = (self._workspace_root / str(program_report_path)).resolve()
        if expected_root not in report_path.parents:
            raise ValueError("program_report_path 越过实验根目录")
        if not report_path.is_file():
            raise ValueError("program_report_path 指向的程序证据文件不存在")
        try:
            raw_evidence = report_path.read_bytes()
            persisted_evidence = json.loads(raw_evidence.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("程序证据文件不可读或不是有效 JSON") from exc
        actual_digest = "sha256:" + hashlib.sha256(raw_evidence).hexdigest()
        if actual_digest != program_report_digest:
            raise ValueError("程序证据实际文件摘要不一致")
        report_evidence_without_digest = {
            key: value for key, value in evidence.items()
            if key != "program_report_digest"
        }
        if persisted_evidence != report_evidence_without_digest:
            raise ValueError("程序证据文件内容与报告 evidence 不一致")
        required_artifacts = {
            item["path"]: item for item in manifest["required_artifacts"]
            if item.get("existence_required")
        }
        reported_artifacts = {item.get("path"): item for item in artifacts}
        for path, required in required_artifacts.items():
            artifact = reported_artifacts.get(path)
            if artifact is None:
                raise ValueError(f"缺少必需产物: {path}")
            absolute = (self._workspace_root / path).resolve()
            if expected_root not in absolute.parents or not absolute.is_file():
                raise ValueError(f"必需产物不存在或越界: {path}")
            digest = "sha256:" + hashlib.sha256(absolute.read_bytes()).hexdigest()
            if artifact.get("digest") != digest:
                raise ValueError(f"必需产物摘要不匹配: {path}")
            signed_digest = required.get("digest")
            if signed_digest and signed_digest != digest:
                raise ValueError(f"必需产物与签发摘要不匹配: {path}")
        numeric_fields = (
            "total_duration_ms", "work_duration_ms", "wait_duration_ms",
            "retry_duration_ms", "input_count", "terminal_count",
            "missing_count", "duplicate_count", "quality_diff_count",
        )
        if any(
            isinstance(evidence.get(key), bool)
            or not isinstance(evidence.get(key), (int, float))
            or evidence[key] < 0 for key in numeric_fields
        ):
            raise ValueError("程序证据计数或时长缺失/无效")
        if report["status"] == "completed":
            conserved = (
                evidence["terminal_count"] == evidence["input_count"]
                and evidence["missing_count"] == 0
                and evidence["duplicate_count"] == 0
            )
        else:
            conserved = (
                evidence["terminal_count"] + evidence["missing_count"]
                == evidence["input_count"]
                and evidence["duplicate_count"] == 0
            )
        if not conserved:
            raise ValueError("程序证据终态守恒失败")
        if evidence["total_duration_ms"] != (
            evidence["work_duration_ms"] + evidence["wait_duration_ms"]
            + evidence["retry_duration_ms"]
        ):
            raise ValueError("程序证据总时长未完整核算")
        return {"valid": True, "errors": []}

    def render_manifest_markdown(self, manifest_id: str) -> str:
        """将 manifest 渲染为自包含 Markdown 任务单。

        不包含凭据；只包含执行者需要的信息。
        """
        record = self._store.get_task_manifest(manifest_id)
        m = record["manifest"]
        lines = [
            f"# 实验任务单: {m['task_id']}",
            "",
            f"**实验**: {m['experiment_id']}",
            f"**候选**: {m['candidate_id']}",
            f"**轮次**: {m['round_id']}",
            f"**摘要**: {record['manifest_digest']}",
            f"**目标**: {m['objective']}",
            f"**轮次类型**: {m['round_kind']}",
            f"**策略步骤**: {m['strategy_step']}",
            f"**重复索引**: {m['repetition_index']}",
            "",
            "## 执行配置",
            "",
            "| 字段 | 值 |",
            "|---|---|",
        ]
        config = m.get("execution_config", {})
        for field in [
            "inter_combo_delay", "detail_batch_size", "detail_interval",
            "detail_reset_every", "detail_batch_cooldown",
            "screen_batch_size", "screen_concurrency",
            "match_batch_size", "match_concurrency",
        ]:
            lines.append(f"| {field} | {config.get(field)} |")
        lines.extend([
            "",
            "## 固定字段",
            "",
            f"- 关键词: {', '.join(m.get('fixed_fields', {}).get('keywords', []))}",
            f"- 搜索范围: {m.get('fixed_fields', {}).get('scope_kind')}",
            f"- 城市: {', '.join(m.get('fixed_fields', {}).get('cities', []))}",
            f"- 每组合页数: {m.get('fixed_fields', {}).get('pages_per_combination')}",
            f"- 计划总页数: {m.get('fixed_fields', {}).get('planned_pages')}",
            f"- 任务规模: {m.get('fixed_fields', {}).get('task_size')}",
            "",
            "## 执行步骤",
            "",
        ])
        for step in m.get("execution_steps", []):
            lines.append(f"{step['seq']}. **{step['action']}**: {step['instruction']}")
            lines.append(f"   - 预期状态: {step['expected_status']}")
            lines.append(f"   - 超时: {step['timeout_seconds']}秒")
            lines.append(f"   - 超时动作: {step['on_timeout']}")
            lines.append(f"   - 证据字段: {step['evidence_field']}")
            lines.append("")
        lines.extend([
            "## 停止条件",
            "",
        ])
        for cond in m.get("stop_conditions", []):
            lines.append(f"- **{cond['code']}** (severity: {cond['severity']}): {cond['action']}")
        lines.extend([
            "",
            "## 允许写入路径",
            "",
        ])
        for path in m.get("allowed_writes", []):
            lines.append(f"- `{path}`")
        lines.extend([
            "",
            "## 禁止动作",
            "",
        ])
        for action in m.get("forbidden_actions", []):
            lines.append(f"- {action}")
        lines.extend([
            "",
            "## 报告格式",
            "",
            "完成后必须返回以下固定格式：",
            "",
            "```",
            "# Execution Result",
            f"Task ID: {m['task_id']}",
            "Status: completed | blocked | invalid | cancelled",
            f"Manifest digest: {record['manifest_digest']}",
            "Program evidence: path + digest",
            "Executor report: path + digest",
            "",
            "## Completed Steps",
            "[ordered IDs only]",
            "",
            "## Stop Reason",
            "[exact code and observed fact, or none]",
            "",
            "## Unexecuted Steps",
            "[ordered IDs only, or none]",
            "```",
        ])
        return "\n".join(lines)


    # -- T028: 漏斗簿记：候选提案、动态步长、晋级/淘汰、边界分类、
    #         收敛检查和剩余时间预测 (FR-013~020/055, plan.md §4) -------------

    def validate_dynamic_step(
        self, *, current_value: int | float, proposed_value: int | float,
        step_size: int | float, boundary: tuple[int | float, int | float],
    ) -> bool:
        """FR-013/FR-014: 校验动态步长是否合法。

        - 步长超过 step_size → False（防止跳跃过大）。
        - 超出物理边界 → False。
        - 零步长（无变化）→ False。
        - 边界值本身有效（闭区间）。
        """
        lo, hi = boundary
        if proposed_value < lo or proposed_value > hi:
            return False
        step = abs(proposed_value - current_value)
        if step == 0:
            return False
        if step > step_size:
            return False
        return True

    def propose_candidate(
        self, *, experiment_id: str, stage: str, strategy_step: str,
        parent_id: str | None, config: dict,
    ) -> dict:
        """FR-016: 创建候选并返回包含 pressure_rank 的记录。

        parent_id 非空时建立父子链接，pressure_rank 为父 rank + 1。
        """
        pressure_rank = 0
        if parent_id:
            parent = self._store.get_tuning_candidate(parent_id)
            pressure_rank = int(parent.get("pressure_rank", 0)) + 1
        candidate = self._store.save_tuning_candidate(
            experiment_id=experiment_id, stage=stage,
            strategy_step=strategy_step, config=config,
            parent_candidate_id=parent_id, pressure_rank=pressure_rank,
        )
        candidate["pressure_rank"] = pressure_rank
        return candidate

    def _list_confirmed_rounds_for_candidate(self, candidate_id: str) -> list[dict]:
        """返回候选的全部 confirmed 轮次，按 repetition_index 升序。"""
        with self._store._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tuning_rounds "
                "WHERE candidate_id = ? AND status = 'confirmed' "
                "ORDER BY repetition_index ASC",
                (candidate_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def aggregate_candidate_metrics(self, candidate_id: str) -> dict:
        """FR-018/FR-031: 聚合候选的多轮指标，使用中位数（非最好成绩）。

        返回 median_duration_ms、tail_duration_ms、retry_count 等。
        """
        rounds = self._list_confirmed_rounds_for_candidate(candidate_id)
        if not rounds:
            return {
                "median_duration_ms": 0,
                "tail_duration_ms": 0,
                "retry_count": 0,
                "round_count": 0,
                "durations": [],
            }
        durations = []
        total_retry = 0
        for r in rounds:
            metrics = r.get("metrics_json")
            if isinstance(metrics, str):
                try:
                    metrics = json.loads(metrics)
                except (json.JSONDecodeError, TypeError):
                    metrics = {}
            elif metrics is None:
                metrics = {}
            durations.append(int(metrics.get("total_duration_ms", 0)))
            total_retry += int(metrics.get("retry_count", 0))
        durations_sorted = sorted(durations)
        n = len(durations_sorted)
        if n % 2 == 1:
            median = durations_sorted[n // 2]
        else:
            median = (durations_sorted[n // 2 - 1] + durations_sorted[n // 2]) // 2
        tail = max(durations_sorted)
        return {
            "median_duration_ms": median,
            "tail_duration_ms": tail,
            "retry_count": total_retry,
            "round_count": n,
            "durations": durations_sorted,
        }

    def promote_candidate(
        self, candidate_id: str, *, reason_evidence: list[str],
    ) -> dict:
        """FR-016/FR-031: 漏斗晋级决策。

        与父候选比较：median → tail → retry 逐级 tie-break。
        - 明显更慢（median 更大）→ rejected, no_gain。
        - 明显更快（median 更小）→ promising。
        - median 相同：tail 更小 → promising；tail 相同：retry 更少 → promising。
        - 无父候选 → promising（首个候选，无比较基准）。
        """
        candidate = self._store.get_tuning_candidate(candidate_id)
        parent_id = candidate.get("parent_candidate_id")
        if not parent_id:
            self._set_candidate_status(candidate_id, "promising")
            return {"status": "promising", "candidate_id": candidate_id}
        child_agg = self.aggregate_candidate_metrics(candidate_id)
        parent_agg = self.aggregate_candidate_metrics(parent_id)
        if parent_agg["round_count"] == 0:
            # 父候选无 confirmed 轮次，无法比较 → 晋级
            self._set_candidate_status(candidate_id, "promising")
            return {"status": "promising", "candidate_id": candidate_id}
        child_median = child_agg["median_duration_ms"]
        parent_median = parent_agg["median_duration_ms"]
        if child_median < parent_median:
            self._set_candidate_status(candidate_id, "promising")
            return {"status": "promising", "candidate_id": candidate_id}
        if child_median > parent_median:
            self._set_candidate_status(
                candidate_id, "rejected", rejection_code="no_gain")
            return {"status": "rejected", "rejection_code": "no_gain",
                    "candidate_id": candidate_id}
        # median 相同 → 比较 tail
        if child_agg["tail_duration_ms"] < parent_agg["tail_duration_ms"]:
            self._set_candidate_status(candidate_id, "promising")
            return {"status": "promising", "candidate_id": candidate_id}
        if child_agg["tail_duration_ms"] > parent_agg["tail_duration_ms"]:
            self._set_candidate_status(
                candidate_id, "rejected", rejection_code="no_gain")
            return {"status": "rejected", "rejection_code": "no_gain",
                    "candidate_id": candidate_id}
        # tail 相同 → 比较 retry
        if child_agg["retry_count"] <= parent_agg["retry_count"]:
            self._set_candidate_status(candidate_id, "promising")
            return {"status": "promising", "candidate_id": candidate_id}
        self._set_candidate_status(
            candidate_id, "rejected", rejection_code="no_gain")
        return {"status": "rejected", "rejection_code": "no_gain",
                "candidate_id": candidate_id}

    def _set_candidate_status(
        self, candidate_id: str, status: str, *,
        rejection_code: str | None = None,
    ) -> None:
        """更新候选状态。"""
        from webui.store import _now
        now = _now()
        with self._store._connection() as conn:
            if rejection_code:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = ?, rejection_code = ?, updated_at = ? "
                    "WHERE id = ?",
                    (status, rejection_code, now, candidate_id),
                )
            else:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, candidate_id),
                )

    def classify_boundary(
        self, candidate_id: str, *, is_acceptable: bool,
    ) -> dict:
        """FR-015: 分类边界候选。

        不可接受的候选标记为 boundary（终态，不可应用为模式槽位）。
        可接受的候选保持当前状态不变。
        """
        if not is_acceptable:
            self._set_candidate_status(candidate_id, "boundary")
            return {"status": "boundary", "candidate_id": candidate_id}
        return {"status": "proposed", "candidate_id": candidate_id}

    def check_convergence(self, candidate_id: str) -> dict:
        """FR-017/FR-020: 检查候选是否收敛。

        收敛条件：至少 3 次重复，且波动在正常范围内。
        波动指标：(max - min) / median < 0.1。
        """
        agg = self.aggregate_candidate_metrics(candidate_id)
        round_count = agg["round_count"]
        if round_count < 3:
            return {"converged": False, "round_count": round_count,
                    "reason": "insufficient_repetitions"}
        durations = agg["durations"]
        median = agg["median_duration_ms"]
        if median <= 0:
            return {"converged": False, "round_count": round_count,
                    "reason": "invalid_median"}
        variation = (max(durations) - min(durations)) / median
        if variation < 0.1:
            return {"converged": True, "round_count": round_count,
                    "variation": variation}
        return {"converged": False, "round_count": round_count,
                "variation": variation, "reason": "high_variation"}

    def project_remaining_time(self, experiment_id: str) -> dict | None:
        """FR-019: 预估剩余实验时间。

        基于已确认轮次的平均耗时和预估剩余轮次数计算。
        """
        with self._store._connection() as conn:
            rows = conn.execute(
                "SELECT metrics_json FROM tuning_rounds "
                "WHERE experiment_id = ? AND status = 'confirmed'",
                (experiment_id,),
            ).fetchall()
        confirmed_rounds = len(rows)
        if confirmed_rounds == 0:
            return None
        total_duration_ms = 0
        for row in rows:
            metrics = row["metrics_json"]
            if isinstance(metrics, str):
                try:
                    metrics = json.loads(metrics)
                except (json.JSONDecodeError, TypeError):
                    metrics = {}
            elif metrics is None:
                metrics = {}
            total_duration_ms += int(metrics.get("total_duration_ms", 0))
        avg_duration_ms = total_duration_ms / confirmed_rounds
        # 预估剩余轮次：粗略估计每阶段需 ~10 轮，共 4 阶段
        estimated_total_rounds = 40
        remaining_required_rounds = max(
            0, estimated_total_rounds - confirmed_rounds)
        estimated_remaining_seconds = (
            avg_duration_ms * remaining_required_rounds / 1000)
        return {
            "confirmed_rounds": confirmed_rounds,
            "remaining_required_rounds": remaining_required_rounds,
            "estimated_remaining_seconds": int(estimated_remaining_seconds),
        }

    def validate_mode_matrix(self, matrix: dict) -> dict:
        """FR-055/FR-065: 校验模式矩阵包含全部 9 个槽位。

        允许不同槽位引用同一配置（共享槽位）。
        """
        required_modes = {"stable", "balanced", "extreme"}
        required_sizes = {"small", "medium", "large"}
        missing_slots = []
        for mode in required_modes:
            if mode not in matrix:
                missing_slots.extend(
                    [f"{mode}/{size}" for size in required_sizes])
                continue
            for size in required_sizes:
                if size not in matrix[mode]:
                    missing_slots.append(f"{mode}/{size}")
        if missing_slots:
            return {"valid": False, "missing_slots": missing_slots,
                    "shared_slot_count": 0}
        # 计算共享槽位（相同 config_digest）
        from webui.execution_config import ExecutionConfigSnapshot
        digest_to_slots: dict[str, list[str]] = {}
        for mode in required_modes:
            for size in required_sizes:
                config = matrix[mode][size]
                snapshot = ExecutionConfigSnapshot.create(config)
                digest_to_slots.setdefault(
                    snapshot.config_digest, []).append(f"{mode}/{size}")
        shared_slot_count = sum(
            len(slots) - 1 for slots in digest_to_slots.values()
            if len(slots) > 1)
        return {"valid": True, "missing_slots": [],
                "shared_slot_count": shared_slot_count}

    # -- T029: 硬停止与受控重试 (FR-029/032/033/062) ----------------------

    # 硬错误码集合：命中即立即停止候选（FR-029/FR-033）
    _HARD_ERROR_CODES = frozenset({
        "data_missing", "item_mapping_error", "result_unparseable",
        "state_corrupted", "quality_out_of_range",
        "login_expired", "captcha_required", "source_blocked",
        "data_integrity_risk", "internal_error",
        "ai_quota_exhausted", "ai_key_invalid",
    })

    # 可恢复错误码集合：按任务单规定重试（FR-032）
    _RECOVERABLE_ERROR_CODES = frozenset({
        "detail_timeout", "ai_rate_limited", "ai_network_error",
        "ip_risk_control", "cdp_unavailable", "ai_missing_job",
    })

    # 单候选最大可恢复重试次数（FR-032 持续错误达条件后停止）
    _MAX_RECOVERABLE_RETRIES = 3

    def handle_hard_stop(
        self, *, round_id: str, error_code: str,
    ) -> dict:
        """FR-029/FR-033: 硬错误立即停止候选并阻断新工作。

        - 轮次标记为 blocked，记录 failure_code。
        - 实验状态转为 blocked，记录 blocked_code。
        - 释放租约（停止新工作）。
        - 不自动降档（FR-062）。
        - 保留已采集的证据（不删除已 confirmed 轮次）。
        """
        round_rec = self._store.get_tuning_round(round_id)
        self._store.update_tuning_round_status(
            round_id, status="blocked", failure_code=error_code,
        )
        # 推进实验到 blocked 可达的状态（draft 不能直接 blocked）
        exp = self._store.get_tuning_experiment(round_rec["experiment_id"])
        if exp["status"] == "draft":
            self._store.update_tuning_experiment_status(
                round_rec["experiment_id"], status="preflight")
        try:
            self._store.update_tuning_experiment_status(
                round_rec["experiment_id"], status="blocked",
                blocked_code=error_code,
                blocked_reason=f"硬错误: {error_code}",
            )
        except ValueError:
            pass  # 实验已处于终态时忽略
        self._store.release_tuning_lease(owner_token=self._owner_token)
        return {
            "stopped": True,
            "round_id": round_id,
            "error_code": error_code,
            "experiment_id": round_rec["experiment_id"],
        }

    def handle_recoverable_retry(
        self, *, round_id: str, error_code: str,
    ) -> dict:
        """FR-032: 单次可恢复错误按任务单规定重试。

        - 记录重试事件。
        - 持续错误达到 _MAX_RECOVERABLE_RETRIES 后停止候选。
        - 不自动降档（FR-062）。
        - 保留在途证据。
        """
        round_rec = self._store.get_tuning_round(round_id)
        # 统计当前轮次已有的 retry 事件数
        events = self._store.list_tuning_measurement_events(round_id)
        retry_count = sum(
            1 for ev in events if ev.get("event_type") == "retry")
        retried = retry_count + 1
        # 记录 retry 事件
        self._store.save_tuning_measurement_event(
            round_id=round_id, event_type="retry",
            stage=round_rec.get("round_kind", "unknown"),
            duration_ms=0, error_code=error_code,
            counts={"attempt": retried, "status": "retrying"},
            metadata={"error_code": error_code, "recovered": False},
        )
        if retried >= self._MAX_RECOVERABLE_RETRIES:
            # 达到最大重试次数 → 停止候选
            self._store.update_tuning_round_status(
                round_id, status="blocked", failure_code=error_code,
            )
            try:
                self._store.update_tuning_experiment_status(
                    round_rec["experiment_id"], status="blocked",
                    blocked_code=error_code,
                    blocked_reason=(
                        f"可恢复错误重试达上限 ({retried}次): {error_code}"),
                )
            except ValueError:
                pass
            self._store.release_tuning_lease(
                owner_token=self._owner_token)
            return {
                "retried": retried,
                "stopped": True,
                "round_id": round_id,
                "error_code": error_code,
                "reason": "max_retries_reached",
            }
        return {
            "retried": retried,
            "stopped": False,
            "round_id": round_id,
            "error_code": error_code,
            "remaining_retries": self._MAX_RECOVERABLE_RETRIES - retried,
        }

    def is_hard_error(self, error_code: str) -> bool:
        """判断是否为硬错误（FR-029/FR-033）。"""
        return error_code in self._HARD_ERROR_CODES

    def is_recoverable_error(self, error_code: str) -> bool:
        """判断是否为可恢复错误（FR-032）。"""
        return error_code in self._RECOVERABLE_ERROR_CODES


class RoundAdapter:
    """分阶段轮次适配器：按轮次类型创建轮次并校验阶段输入复用规则（T026）。

    research.md Decision 7 / FR-024 / FR-025：
    - list: 真实抓取，第一阶段，不复用阶段输入。
    - detail: 可复用 list 结果（同 input_version）。
    - rough: 可复用 list 字段（同 input_version）。
    - fine: 可复用 JD（来自 detail，同 input_version）。
    - end_to_end: 必须从任务起点完整执行，不复用中间结果。

    被测阶段 MUST 真实执行；只允许复用该阶段之前的固定输入。
    """

    # 每种轮次允许复用的前置阶段（空集表示不允许复用阶段输入）。
    _ALLOWED_REUSE_STAGES: dict[str, frozenset[str]] = {
        "list": frozenset(),              # 列表是第一阶段
        "detail": frozenset({"list"}),    # 详情可复用列表结果
        "rough": frozenset({"list"}),     # 粗筛可复用列表字段
        "fine": frozenset({"detail"}),    # 精筛可复用 JD
        "end_to_end": frozenset(),        # 端到端不复用
    }

    def __init__(self, controller: "TuningController"):
        self._controller = controller

    def validate_stage_input_reuse(
        self, round_kind: str,
        source_input_version: str, target_input_version: str,
    ) -> bool:
        """校验阶段输入复用是否合法。

        - 未知 round_kind → 拒绝。
        - end_to_end / list → 不允许复用阶段输入。
        - detail / rough / fine → 仅当 source_input_version == target_input_version
          时允许（跨版本 digest 拒绝，data-model.md 不变量）。

        Raises:
            ValueError: 复用不合法时抛出，含明确原因。
        """
        if round_kind not in self._ALLOWED_REUSE_STAGES:
            raise ValueError(f"未知轮次类型: {round_kind}")
        if not self._ALLOWED_REUSE_STAGES[round_kind]:
            raise ValueError(
                f"轮次类型 {round_kind} 不允许复用阶段输入"
            )
        if source_input_version != target_input_version:
            raise ValueError(
                f"跨版本复用被拒绝: source={source_input_version} "
                f"!= target={target_input_version}"
            )
        return True

    def create_list_round(
        self, *, experiment_id: str, candidate_id: str,
        workload_id: str, repetition_index: int,
    ) -> dict:
        """创建 list 轮次。list 是第一阶段，真实抓取，不复用阶段输入。"""
        return self._controller.create_round(
            experiment_id=experiment_id, candidate_id=candidate_id,
            workload_id=workload_id, round_kind="list",
            repetition_index=repetition_index,
        )

    def create_detail_round(
        self, *, experiment_id: str, candidate_id: str,
        workload_id: str, repetition_index: int,
        source_input_version: str, target_input_version: str,
    ) -> dict:
        """创建 detail 轮次。可复用 list 结果（同 input_version）。"""
        self.validate_stage_input_reuse(
            "detail", source_input_version, target_input_version,
        )
        return self._controller.create_round(
            experiment_id=experiment_id, candidate_id=candidate_id,
            workload_id=workload_id, round_kind="detail",
            repetition_index=repetition_index,
        )

    def create_rough_round(
        self, *, experiment_id: str, candidate_id: str,
        workload_id: str, repetition_index: int,
        source_input_version: str, target_input_version: str,
    ) -> dict:
        """创建 rough 轮次。可复用 list 字段（同 input_version）。"""
        self.validate_stage_input_reuse(
            "rough", source_input_version, target_input_version,
        )
        return self._controller.create_round(
            experiment_id=experiment_id, candidate_id=candidate_id,
            workload_id=workload_id, round_kind="rough",
            repetition_index=repetition_index,
        )

    def create_fine_round(
        self, *, experiment_id: str, candidate_id: str,
        workload_id: str, repetition_index: int,
        source_input_version: str, target_input_version: str,
    ) -> dict:
        """创建 fine 轮次。可复用 JD（来自 detail，同 input_version）。"""
        self.validate_stage_input_reuse(
            "fine", source_input_version, target_input_version,
        )
        return self._controller.create_round(
            experiment_id=experiment_id, candidate_id=candidate_id,
            workload_id=workload_id, round_kind="fine",
            repetition_index=repetition_index,
        )

    def create_end_to_end_round(
        self, *, experiment_id: str, candidate_id: str,
        workload_id: str, repetition_index: int,
    ) -> dict:
        """创建 end_to_end 轮次。必须从头执行，不复用中间结果。"""
        return self._controller.create_round(
            experiment_id=experiment_id, candidate_id=candidate_id,
            workload_id=workload_id, round_kind="end_to_end",
            repetition_index=repetition_index,
        )
