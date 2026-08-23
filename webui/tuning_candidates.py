"""调优候选提案、收敛判定与硬停止/受控重试 mixin（021 B7 自 tuning.py 搬运）。"""

from __future__ import annotations

import json

class TuningCandidatesMixin:
    """候选提案/晋级淘汰、动态步长、边界分类、收敛与剩余时间、错误分级。"""

    # -- T028: 漏斗簿记：候选提案、动态步长、晋级/淘汰、边界分类、
    #         收敛检查和剩余时间预测 (FR-013~020/055, plan.md §4) -------------

    def validate_dynamic_step(
        self, *, current_value: float, proposed_value: float,
        step_size: float, boundary: tuple[int | float, int | float],
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
        del reason_evidence  # 兼容参数；路由层直接持久化证据
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
        requested_code = str(error_code or "").strip()
        if requested_code and requested_code not in {
            "hard_error", "unknown_hard_error",
        }:
            canonical_code = requested_code
            correlation_id = None
        else:
            hard_error = self.aggregate_hard_error(
                round_id, fallback_code=requested_code or None,
            )
            canonical_code = hard_error["code"]
            correlation_id = hard_error.get("correlation_id")
        blocked_reason = f"硬错误: {canonical_code}"
        if correlation_id:
            blocked_reason += f"（事件 {correlation_id}）"
        self._store.update_tuning_round_status(
            round_id, status="blocked", failure_code=canonical_code,
        )
        # 推进实验到 blocked 可达的状态（draft 不能直接 blocked）
        exp = self._store.get_tuning_experiment(round_rec["experiment_id"])
        if exp["status"] == "draft":
            self._store.update_tuning_experiment_status(
                round_rec["experiment_id"], status="preflight")
        try:
            self._store.update_tuning_experiment_status(
                round_rec["experiment_id"], status="blocked",
                blocked_code=canonical_code,
                blocked_reason=blocked_reason,
            )
        except ValueError:
            pass  # 实验已处于终态时忽略
        self._store.release_tuning_lease(owner_token=self._owner_token)
        return {
            "stopped": True,
            "round_id": round_id,
            "error_code": canonical_code,
            "correlation_id": correlation_id,
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
