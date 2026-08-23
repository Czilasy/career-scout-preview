"""调优实验生命周期与租约协调 mixin（021 B7 自 tuning.py 搬运）。"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from webui.store import TaskStore

_PROCESS_OWNER_TOKENS: dict[str, str] = {}


class TuningExperimentsMixin:
    """实验创建/输入冻结/状态推进/租约协调（__init__ 与 owner token 在此）。"""

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
        del workloads  # 旧版兼容参数；输入冻结走 create_experiment_with_input
        return self._store.create_tuning_experiment(
            spec_version=spec_version, source_scope=source_scope,
        )

    def create_experiment_with_input(
        self, *, spec_version: str, source_scope: dict,
        workloads: list[dict], quality_context: dict,
    ) -> dict:
        """Create the HTTP experiment draft with its proposed input bundle.

        T606: 显式冻结 platform、规范城市解析、filter_schema_version、
        browser_account、cdp_port、profile_key、scope/task digest。
        见 data-model.md 第 230 行：新 experiment 创建时冻结 browser_account、
        cdp_port、profile_key 与 filter_schema_version 到 source scope。
        """
        frozen_scope = self._freeze_experiment_source_scope(source_scope)
        return self._store.create_tuning_experiment_with_input(
            spec_version=spec_version,
            source_scope=frozen_scope,
            workloads=workloads,
            quality_context=quality_context,
            workspace_root=self._workspace_root,
        )

    _EXPERIMENT_RUNTIME_FIELDS = (
        "browser_account", "cdp_port", "profile_key", "filter_schema_version",
    )

    def _freeze_experiment_source_scope(self, source_scope: dict) -> dict:
        """T606: 校验并冻结 platform 和 runtime 字段到 source_scope。

        - platform 必须是已知已注册平台（不回退 BOSS）
        - browser_account 必须非空字符串
        - cdp_port 必须与平台默认端口一致
        - profile_key 必须为 '<platform>:<account>' 形式
        - filter_schema_version 必须为正整数
        - task_input_digest 由 _build_task_input_digest 计算

        见 data-model.md 第 230 行。
        """
        from webui.platforms import (
            get_platform,
            validate_platform_key,
        )
        platform = source_scope.get("platform", "boss")
        validate_platform_key(platform)
        registry = get_platform(platform)
        browser_account = source_scope.get("browser_account")
        if not isinstance(browser_account, str) or not browser_account.strip():
            raise ValueError("source_scope.browser_account 不能为空")
        browser_account = browser_account.strip()
        cdp_port = source_scope.get("cdp_port", registry.default_cdp_port)
        if cdp_port != registry.default_cdp_port:
            raise ValueError(
                f"cdp_port={cdp_port} 与平台 {platform} 默认端口"
                f" {registry.default_cdp_port} 不一致"
            )
        expected_profile_key = f"{platform}:{browser_account}"
        profile_key = source_scope.get("profile_key", expected_profile_key)
        if profile_key != expected_profile_key:
            raise ValueError(
                f"profile_key={profile_key!r} 必须为 "
                f"{expected_profile_key!r}"
            )
        filter_schema_version = source_scope.get("filter_schema_version")
        if not isinstance(filter_schema_version, int) or filter_schema_version < 1:
            raise ValueError("filter_schema_version 必须为正整数")
        frozen = dict(source_scope)
        frozen.update({
            "platform": platform,
            "browser_account": browser_account,
            "cdp_port": cdp_port,
            "profile_key": profile_key,
            "filter_schema_version": filter_schema_version,
        })
        return frozen

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
        del experiment_id  # 旧版兼容参数；完整模式版本不挂实验
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
