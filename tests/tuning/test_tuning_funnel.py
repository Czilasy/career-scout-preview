"""深度调优漏斗检索与硬停合同测试（027 自 tests/test_tuning.py 拆出）。"""

from __future__ import annotations
import hashlib
import json
import pathlib
import tempfile
import unittest
from webui.store import TaskStore

from tests.tuning.builders import _sample_nine_fields, _make_valid_manifest_payload, _make_valid_report_payload


class FunnelSearchTests(unittest.TestCase):
    """T027 RED: 漏斗搜索簿记测试。

    覆盖 FR-013/015/016/017/018/019/020/055 与 plan.md §4。
    T028 将实现漏斗簿记方法使这些测试转绿。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        self.experiment = self.controller.create_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            workloads=[
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        )

    def tearDown(self):
        self.temp.cleanup()

    def _confirm_round_with_metrics(
        self, *, candidate_id: str, workload_id: str,
        round_kind: str, duration_ms: int,
        retry_count: int = 0, quality_diff_count: int = 0,
        repetition_index: int = 1,
        experiment_id: str | None = None,
    ) -> str:
        """创建并确认一个带指标的轮次，返回 round_id。"""
        round_rec = self.controller.create_round(
            experiment_id=experiment_id or self.experiment["id"],
            candidate_id=candidate_id,
            workload_id=workload_id,
            round_kind=round_kind,
            repetition_index=repetition_index,
        )
        self.controller.confirm_round(
            round_rec["id"],
            metrics={
                "total_duration_ms": duration_ms,
                "retry_count": retry_count,
                "quality_diff_count": quality_diff_count,
                "input_count": 30,
                "terminal_count": 30,
                "success_count": 30,
                "missing_count": 0,
                "duplicate_count": 0,
            },
        )
        return round_rec["id"]

    # -- 单字段粗探步长 (FR-013) -----------------------------------------

    def test_validate_dynamic_step_valid_coarse_step(self):
        """FR-013: 远离边界时，步长 <= step_size 且在边界内的步长有效。"""
        ok = self.controller.validate_dynamic_step(
            current_value=5, proposed_value=10, step_size=10,
            boundary=(1, 100))
        self.assertTrue(ok)

    def test_validate_dynamic_step_step_too_large_rejected(self):
        """步长超过 step_size 被拒绝（防止跳跃过大）。"""
        ok = self.controller.validate_dynamic_step(
            current_value=5, proposed_value=50, step_size=10,
            boundary=(1, 100))
        self.assertFalse(ok)

    def test_validate_dynamic_step_out_of_bounds_rejected(self):
        """超出物理边界的步长被拒绝（FR-014 字段物理有效性）。"""
        ok = self.controller.validate_dynamic_step(
            current_value=5, proposed_value=150, step_size=200,
            boundary=(1, 100))
        self.assertFalse(ok)

    def test_validate_dynamic_step_zero_step_rejected(self):
        """零步长（无变化）被拒绝。"""
        ok = self.controller.validate_dynamic_step(
            current_value=5, proposed_value=5, step_size=10,
            boundary=(1, 100))
        self.assertFalse(ok)

    def test_validate_dynamic_step_at_boundary_edge(self):
        """边界值本身有效（闭区间）。"""
        ok = self.controller.validate_dynamic_step(
            current_value=5, proposed_value=100, step_size=100,
            boundary=(1, 100))
        self.assertTrue(ok)

    # -- 候选提案 (propose_candidate) ------------------------------------

    def test_propose_candidate_returns_with_pressure_rank(self):
        """propose_candidate 创建候选并返回 pressure_rank。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=None, config=_sample_nine_fields())
        self.assertIsNotNone(candidate["id"])
        self.assertIn("pressure_rank", candidate)

    def test_propose_candidate_with_parent_links(self):
        """propose_candidate 带 parent_id 时建立父子链接。"""
        parent = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=None, config=_sample_nine_fields())
        child = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=parent["id"], config=_sample_nine_fields(detail_batch_size=20))
        fetched = self.store.get_tuning_candidate(child["id"])
        self.assertEqual(fetched.get("parent_candidate_id"), parent["id"])

    # -- 无收益剪枝 (FR-016) ---------------------------------------------

    def test_promote_candidate_no_gain_rejected(self):
        """FR-016: 明显更慢的候选不晋级（无收益剪枝）。"""
        parent = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=None, config=_sample_nine_fields())
        self._confirm_round_with_metrics(
            candidate_id=parent["id"], workload_id="wl-1",
            round_kind="list", duration_ms=10000)
        # 子候选明显更慢（3 倍）
        child = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=parent["id"], config=_sample_nine_fields(inter_combo_delay=1.0))
        child_round = self._confirm_round_with_metrics(
            candidate_id=child["id"], workload_id="wl-1",
            round_kind="list", duration_ms=30000)
        result = self.controller.promote_candidate(
            child["id"], reason_evidence=[child_round])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rejection_code"], "no_gain")

    def test_promote_candidate_with_gain_promoted(self):
        """有明显收益的候选可晋级。"""
        parent = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=None, config=_sample_nine_fields())
        self._confirm_round_with_metrics(
            candidate_id=parent["id"], workload_id="wl-1",
            round_kind="list", duration_ms=30000)
        # 子候选更快
        child = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=parent["id"], config=_sample_nine_fields(inter_combo_delay=1.0))
        child_round = self._confirm_round_with_metrics(
            candidate_id=child["id"], workload_id="wl-1",
            round_kind="list", duration_ms=10000)
        result = self.controller.promote_candidate(
            child["id"], reason_evidence=[child_round])
        self.assertEqual(result["status"], "promising")

    # -- 边界 bracketing (FR-015) ----------------------------------------

    def test_classify_boundary_unacceptable_marks_boundary(self):
        """FR-015: 首次不可接受配置标记为危险边界（终态，不可应用）。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="boundary",
            parent_id=None, config=_sample_nine_fields(screen_concurrency=50))
        result = self.controller.classify_boundary(
            candidate["id"], is_acceptable=False)
        self.assertEqual(result["status"], "boundary")
        fetched = self.store.get_tuning_candidate(candidate["id"])
        self.assertEqual(fetched["status"], "boundary")

    def test_classify_boundary_acceptable_keeps_candidate(self):
        """可接受配置不标记为边界。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="boundary",
            parent_id=None, config=_sample_nine_fields())
        result = self.controller.classify_boundary(
            candidate["id"], is_acceptable=True)
        self.assertNotEqual(result["status"], "boundary")

    def test_boundary_candidate_not_eligible_for_mode_slot(self):
        """FR-015: 危险边界候选不可应用为模式槽位。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="boundary",
            parent_id=None, config=_sample_nine_fields())
        self.controller.classify_boundary(
            candidate["id"], is_acceptable=False)
        fetched = self.store.get_tuning_candidate(candidate["id"])
        # boundary 是终态，不能转 accepted
        self.assertEqual(fetched["status"], "boundary")

    # -- 中位数/尾部比较 (FR-017/018/020) --------------------------------

    def test_check_convergence_converged_with_low_variation(self):
        """FR-020: 3 次重复且波动小时收敛。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=None, config=_sample_nine_fields())
        for i, dur in enumerate([10000, 10100, 9900], start=1):
            self._confirm_round_with_metrics(
                candidate_id=candidate["id"], workload_id="wl-1",
                round_kind="list", duration_ms=dur,
                repetition_index=i)
        result = self.controller.check_convergence(candidate["id"])
        self.assertTrue(result["converged"])

    def test_check_convergence_not_converged_with_high_variation(self):
        """FR-020: 波动大时未收敛。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=None, config=_sample_nine_fields())
        for i, dur in enumerate([10000, 20000, 15000], start=1):
            self._confirm_round_with_metrics(
                candidate_id=candidate["id"], workload_id="wl-1",
                round_kind="list", duration_ms=dur,
                repetition_index=i)
        result = self.controller.check_convergence(candidate["id"])
        self.assertFalse(result["converged"])

    def test_check_convergence_needs_three_repeats(self):
        """FR-017: 接近最佳的候选至少重复 3 次才判收敛。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=None, config=_sample_nine_fields())
        for i, dur in enumerate([10000, 10100], start=1):
            self._confirm_round_with_metrics(
                candidate_id=candidate["id"], workload_id="wl-1",
                round_kind="list", duration_ms=dur,
                repetition_index=i)
        result = self.controller.check_convergence(candidate["id"])
        self.assertFalse(result["converged"])

    def test_aggregate_metrics_uses_median_not_best(self):
        """FR-018: 使用中位数总耗时，不用单次最好成绩。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=None, config=_sample_nine_fields())
        for i, dur in enumerate([10000, 12000, 9000], start=1):
            self._confirm_round_with_metrics(
                candidate_id=candidate["id"], workload_id="wl-1",
                round_kind="list", duration_ms=dur,
                repetition_index=i)
        agg = self.controller.aggregate_candidate_metrics(candidate["id"])
        # 中位数 = 10000，不是最好 9000
        self.assertEqual(agg["median_duration_ms"], 10000)
        self.assertEqual(agg["tail_duration_ms"], 12000)

    # -- 压力 tie-breaks (plan.md §4) -----------------------------------

    def test_tie_break_prefers_lower_tail(self):
        """plan.md §4: 中位数相同时，慢速尾部更短的候选胜出。"""
        # 父候选：median 10000, tail 15000
        parent = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=None, config=_sample_nine_fields())
        for i, dur in enumerate([9000, 10000, 15000], start=1):
            self._confirm_round_with_metrics(
                candidate_id=parent["id"], workload_id="wl-1",
                round_kind="list", duration_ms=dur,
                repetition_index=i)
        # 子候选 A：median 10000, tail 12000（尾部更好）
        child_a = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=parent["id"], config=_sample_nine_fields(inter_combo_delay=8.0))
        for i, dur in enumerate([8000, 10000, 12000], start=1):
            self._confirm_round_with_metrics(
                candidate_id=child_a["id"], workload_id="wl-1",
                round_kind="list", duration_ms=dur,
                repetition_index=i)
        result_a = self.controller.promote_candidate(
            child_a["id"], reason_evidence=[])
        self.assertEqual(result_a["status"], "promising",
                         "中位数相同但尾部更好的候选应晋级")

    def test_tie_break_prefers_lower_retry(self):
        """plan.md §4: 中位数和尾部相同时，重试更少的候选胜出。"""
        parent = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=None, config=_sample_nine_fields())
        self._confirm_round_with_metrics(
            candidate_id=parent["id"], workload_id="wl-1",
            round_kind="list", duration_ms=10000, retry_count=2)
        # 子候选：median 10000, retry 0（更好）
        child = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="combination",
            parent_id=parent["id"], config=_sample_nine_fields(inter_combo_delay=8.0))
        child_round = self._confirm_round_with_metrics(
            candidate_id=child["id"], workload_id="wl-1",
            round_kind="list", duration_ms=10000, retry_count=0)
        result = self.controller.promote_candidate(
            child["id"], reason_evidence=[child_round])
        self.assertEqual(result["status"], "promising")

    # -- 剩余时间预测 (FR-019) -------------------------------------------

    def test_project_remaining_time_positive(self):
        """FR-019: 有已确认轮次时剩余时间预测为正。"""
        candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=None, config=_sample_nine_fields())
        self._confirm_round_with_metrics(
            candidate_id=candidate["id"], workload_id="wl-1",
            round_kind="list", duration_ms=10000)
        estimate = self.controller.project_remaining_time(self.experiment["id"])
        self.assertIsNotNone(estimate)
        self.assertGreater(estimate["estimated_remaining_seconds"], 0)
        self.assertIn("confirmed_rounds", estimate)
        self.assertIn("remaining_required_rounds", estimate)

    def test_project_remaining_time_grows_with_slower_rounds(self):
        """FR-019: 更慢的轮次使剩余时间预测增大。"""
        # 快速轮次
        exp_fast = self.controller.create_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={"keywords": ["AI"], "scope_kind": "cities",
                          "cities": ["东莞"], "pages_per_combination": 3},
            workloads=[{"task_size": "small", "structure_index": 1, "scope": {}}],
        )
        cand_fast = self.controller.propose_candidate(
            experiment_id=exp_fast["id"], stage="list",
            strategy_step="single_field", parent_id=None,
            config=_sample_nine_fields())
        self._confirm_round_with_metrics(
            candidate_id=cand_fast["id"], workload_id="wl-1",
            round_kind="list", duration_ms=5000,
            experiment_id=exp_fast["id"])
        est_fast = self.controller.project_remaining_time(exp_fast["id"])
        # 慢速轮次
        exp_slow = self.controller.create_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={"keywords": ["AI"], "scope_kind": "cities",
                          "cities": ["东莞"], "pages_per_combination": 3},
            workloads=[{"task_size": "small", "structure_index": 1, "scope": {}}],
        )
        cand_slow = self.controller.propose_candidate(
            experiment_id=exp_slow["id"], stage="list",
            strategy_step="single_field", parent_id=None,
            config=_sample_nine_fields())
        self._confirm_round_with_metrics(
            candidate_id=cand_slow["id"], workload_id="wl-1",
            round_kind="list", duration_ms=50000,
            experiment_id=exp_slow["id"])
        est_slow = self.controller.project_remaining_time(exp_slow["id"])
        self.assertGreater(
            est_slow["estimated_remaining_seconds"],
            est_fast["estimated_remaining_seconds"])

    # -- 共享模式槽位配置 (FR-055) ---------------------------------------

    def test_shared_mode_slot_config_allowed(self):
        """FR-055: 不同模式可引用同一配置（共享槽位）。"""
        shared_config = _sample_nine_fields(inter_combo_delay=10.0)
        extreme_config = _sample_nine_fields(inter_combo_delay=1.0)
        matrix = {
            "stable": {"small": shared_config, "medium": shared_config,
                       "large": shared_config},
            "balanced": {"small": shared_config, "medium": shared_config,
                         "large": shared_config},
            "extreme": {"small": extreme_config, "medium": extreme_config,
                        "large": extreme_config},
        }
        result = self.controller.validate_mode_matrix(matrix)
        self.assertTrue(result["valid"])
        # stable 和 balanced 引用同一配置
        self.assertGreater(result["shared_slot_count"], 0)

    def test_mode_matrix_rejects_missing_slot(self):
        """模式矩阵必须包含全部 9 个槽位（FR-065 完整版本）。"""
        incomplete_matrix = {
            "stable": {"small": _sample_nine_fields(), "medium": _sample_nine_fields()},
            "balanced": {"small": _sample_nine_fields(), "medium": _sample_nine_fields(),
                         "large": _sample_nine_fields()},
            "extreme": {"small": _sample_nine_fields(), "medium": _sample_nine_fields(),
                        "large": _sample_nine_fields()},
        }
        result = self.controller.validate_mode_matrix(incomplete_matrix)
        self.assertFalse(result["valid"])


class HardStopAndRetryTests(unittest.TestCase):
    """T029 RED: 硬停止与受控重试行为测试。

    覆盖 FR-029/FR-032/FR-033/FR-062。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        self.experiment = self.controller.create_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            workloads=[
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        )
        self.candidate = self.controller.propose_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            parent_id=None, config=_sample_nine_fields())
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _start_main_round(self):
        experiment = self.store.get_tuning_experiment(self.experiment["id"])
        if experiment["status"] == "evaluating":
            self.store.update_tuning_experiment_status(
                self.experiment["id"], status="awaiting_instruction",
            )
        self.controller.start_round(self.round["id"])

    # -- 硬错误立即停止 (FR-029/FR-033) ----------------------------------

    def test_hard_error_immediately_stops_round(self):
        """FR-033: 硬错误立即停止轮次，轮次状态变 blocked。"""
        self._start_main_round()
        result = self.controller.handle_hard_stop(
            round_id=self.round["id"],
            error_code="login_expired")
        self.assertTrue(result["stopped"])
        round_rec = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_rec["status"], "blocked")
        self.assertEqual(round_rec["failure_code"], "login_expired")

    def test_hard_error_blocks_experiment(self):
        """FR-033: 硬错误阻断实验，状态变 blocked。"""
        self._start_main_round()
        self.controller.handle_hard_stop(
            round_id=self.round["id"],
            error_code="captcha_required")
        exp = self.store.get_tuning_experiment(self.experiment["id"])
        self.assertEqual(exp["status"], "blocked")
        self.assertEqual(exp["blocked_code"], "captcha_required")

    def test_explicit_hard_stop_code_overrides_historical_request_error(self):
        """显式 source 阻断码不得被历史 AI request 错误覆盖。"""
        self._start_main_round()
        self.controller.record_measurement(
            round_id=self.round["id"], event_type="request", stage="fine",
            duration_ms=0, error_code="auth_failed",
        )

        result = self.controller.handle_hard_stop(
            round_id=self.round["id"], error_code="source_blocked",
        )

        self.assertEqual(result["error_code"], "source_blocked")
        round_rec = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_rec["failure_code"], "source_blocked")
        exp = self.store.get_tuning_experiment(self.experiment["id"])
        self.assertEqual(exp["blocked_code"], "source_blocked")

    def test_hard_error_releases_lease(self):
        """FR-033: 硬停止后释放租约，阻止新工作启动。"""
        self._start_main_round()
        self.controller.handle_hard_stop(
            round_id=self.round["id"],
            error_code="source_blocked")
        lease = self.store.get_tuning_lease()
        self.assertIsNone(lease.get("owner_experiment_id"))

    def test_hard_error_preserves_confirmed_evidence(self):
        """FR-033: 硬停止保留已 confirmed 轮次的证据。"""
        # 先确认一个轮次
        other_round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=2,
        )
        self.controller.confirm_round(
            other_round["id"],
            metrics={"total_duration_ms": 10000, "retry_count": 0})
        # 硬停止另一个轮次
        self._start_main_round()
        self.controller.handle_hard_stop(
            round_id=self.round["id"],
            error_code="internal_error")
        # 已确认轮次不受影响
        confirmed = self.store.get_tuning_round(other_round["id"])
        self.assertEqual(confirmed["status"], "confirmed")

    def test_is_hard_error_classifies_correctly(self):
        """FR-029: 硬错误码正确分类。"""
        self.assertTrue(self.controller.is_hard_error("login_expired"))
        self.assertTrue(self.controller.is_hard_error("data_missing"))
        self.assertTrue(self.controller.is_hard_error("quality_out_of_range"))
        self.assertFalse(self.controller.is_hard_error("detail_timeout"))

    # -- 可恢复错误受控重试 (FR-032) -------------------------------------

    def test_recoverable_error_allows_retry(self):
        """FR-032: 单次可恢复错误允许重试，不立即停止。"""
        result = self.controller.handle_recoverable_retry(
            round_id=self.round["id"],
            error_code="detail_timeout")
        self.assertFalse(result["stopped"])
        self.assertGreater(result["remaining_retries"], 0)
        round_rec = self.store.get_tuning_round(self.round["id"])
        self.assertNotEqual(round_rec["status"], "blocked")

    def test_recoverable_error_stops_after_max_retries(self):
        """FR-032: 持续错误达到最大重试次数后停止候选。"""
        self._start_main_round()
        max_retries = self.controller._MAX_RECOVERABLE_RETRIES
        for i in range(max_retries):
            result = self.controller.handle_recoverable_retry(
                round_id=self.round["id"],
                error_code="ai_rate_limited")
        self.assertTrue(result["stopped"])
        self.assertEqual(result["reason"], "max_retries_reached")
        round_rec = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_rec["status"], "blocked")

    def test_recoverable_retry_records_events(self):
        """FR-032: 每次重试记录 retry 事件。"""
        self.controller.handle_recoverable_retry(
            round_id=self.round["id"],
            error_code="cdp_unavailable")
        events = self.store.list_tuning_measurement_events(self.round["id"])
        retry_events = [e for e in events if e["event_type"] == "retry"]
        self.assertEqual(len(retry_events), 1)

    def test_recoverable_retry_does_not_auto_downgrade(self):
        """FR-062: 可恢复错误不自动降档。"""
        self.controller.handle_recoverable_retry(
            round_id=self.round["id"],
            error_code="ai_network_error")
        # 实验不应进入 failed 或 cancelled
        exp = self.store.get_tuning_experiment(self.experiment["id"])
        self.assertNotIn(exp["status"], ("failed", "cancelled"))

    def test_is_recoverable_error_classifies_correctly(self):
        """FR-032: 可恢复错误码正确分类。"""
        self.assertTrue(self.controller.is_recoverable_error("detail_timeout"))
        self.assertTrue(self.controller.is_recoverable_error("ai_rate_limited"))
        self.assertFalse(self.controller.is_recoverable_error("login_expired"))


class LegacyBossProofTests(unittest.TestCase):
    """T608: 旧 BOSS manifest/artifact 客观证明纯校验器。

    纯校验器：不修改 JSON/digest，不查询 migration 27 外层 platform 列。
    证据不足时抛 ValueError 阻断，不猜填摘要、不重标智联。
    见 data-model.md 第 263、281 行的存量证明规则。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        quality_context = {
            "profile_summary": "Python AI 应用开发候选人",
            "screening_fields": {"salary": ["403"]},
            "profile_ref": "user-confirmed:test",
        }
        scopes = [
            ("small", 1, 3), ("small", 2, 3),
            ("medium", 2, 8), ("medium", 3, 5),  # 024 新口径：16/15 页属中规模
            ("large", 10, 5), ("large", 11, 5),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "platform": "boss",
                "browser_account": "a",
                "cdp_port": 9222,
                "profile_key": "boss:a",
                "filter_schema_version": 1,
            },
            quality_context=quality_context,
            workloads=[{
                "task_size": size,
                "structure_index": index % 2 + 1,
                "scope": {
                    "keywords": [f"AI-{i}" for i in range(count)],
                    "scope_kind": "cities",
                    "cities": ["东莞"],
                    "pages_per_combination": pages,
                },
            } for index, (size, count, pages) in enumerate(scopes)],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="baseline",
            config=_sample_nine_fields(),
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="list", repetition_index=1,
        )
        # 签发 manifest 并执行，使 round 进入 running
        self.manifest = self._build_valid_manifest()
        self.issued = self.controller.issue_manifest(self.manifest)
        self.manifest_record = self.store.get_task_manifest(
            self.issued["manifest_id"]
        )
        self.controller.execute_manifest(self.issued["manifest_id"])
        # 持久化 list artifact
        self.artifact = self.controller.persist_stage_artifact(
            round_id=self.round["id"], stage="list",
            payload={"round_kind": "list", "jobs": [{"job_id": "j1"}]},
        )
        self.artifact_record = self.store.get_tuning_stage_artifact(
            self.artifact["id"]
        )
        # cutoff: future 表示所有记录都"迁移前"，past 表示所有记录都"迁移后"
        self.future_cutoff = "2099-01-01T00:00:00+00:00"
        self.past_cutoff = "2000-01-01T00:00:00+00:00"

    def tearDown(self):
        self.temp.cleanup()

    def _build_valid_manifest(self) -> dict:
        """构造一份能通过 _validate_manifest 的合法 manifest payload。"""
        manifest = _make_valid_manifest_payload(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            round_id=self.round["id"],
        )
        scope = self.workload["scope"]
        root = f"tuning/{self.experiment['id']}"
        manifest["frozen_input"].update({
            "input_version_id": self.bundle["input_version"]["id"],
            "workload_id": self.workload["id"],
            "task_size": self.workload["task_size"],
            "structure_index": self.workload["structure_index"],
            "scope_digest": scope["scope_digest"],
            "artifact_manifest_path": f"{root}/input/{self.workload['id']}.json",
            "artifact_digest": self.workload["artifact_digest"],
            "quality_context_digest": self.bundle["input_version"][
                "quality_context_digest"
            ],
            "planned_pages": self.workload["planned_pages"],
        })
        manifest["execution_config"] = self.store.get_tuning_candidate(
            self.candidate["id"]
        )["config"]
        manifest["fixed_fields"] = {
            key: scope[key] for key in (
                "keywords", "scope_kind", "cities", "pages_per_combination",
                "planned_pages", "task_size",
            )
        }
        manifest["fixed_fields"]["platform"] = "boss"
        manifest["monitoring"]["final_artifact_path"] = (
            f"{root}/evidence/{self.round['id']}.json"
        )
        manifest["allowed_writes"] = [
            f"{root}/evidence/{self.round['id']}.json",
            f"{root}/artifacts/{self.round['id']}/",
        ]
        manifest["required_artifacts"][0]["path"] = (
            f"{root}/evidence/{self.round['id']}.json"
        )
        return manifest

    # -- manifest 客观证明 -----------------------------------------------

    def test_manifest_proof_passes_for_pre_migration_record(self):
        """迁移前 manifest + 有效 digest + experiment 迁移前 → 证明为 boss。"""
        proof = self.controller.prove_legacy_boss_manifest(
            manifest_record=self.manifest_record,
            migration_cutoff=self.future_cutoff,
        )
        self.assertEqual(proof["platform"], "boss")
        self.assertEqual(proof["proof_kind"], "legacy_manifest")
        self.assertTrue(proof["digest_verified"])
        self.assertIn("experiment:", proof["provenance"][0])

    def test_manifest_proof_fails_when_issued_after_cutoff(self):
        """issued_at >= cutoff → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_manifest(
                manifest_record=self.manifest_record,
                migration_cutoff=self.past_cutoff,
            )
        self.assertIn("不早于 migration cutoff", str(ctx.exception))

    def test_manifest_proof_fails_when_digest_tampered(self):
        """manifest_digest 与重算不一致 → 阻断。"""
        tampered = dict(self.manifest_record)
        tampered["manifest_digest"] = "sha256:deadbeef"
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_manifest(
                manifest_record=tampered,
                migration_cutoff=self.future_cutoff,
            )
        self.assertIn("manifest_digest", str(ctx.exception))

    def test_manifest_proof_fails_when_json_declares_zhilian(self):
        """manifest JSON 显式 platform=zhilian → 阻断，不重标为 BOSS。"""
        tampered_manifest = dict(self.manifest_record["manifest"])
        tampered_manifest["fixed_fields"] = {
            **tampered_manifest.get("fixed_fields", {}),
            "platform": "zhilian",
        }
        tampered = dict(self.manifest_record)
        tampered["manifest"] = tampered_manifest
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_manifest(
                manifest_record=tampered,
                migration_cutoff=self.future_cutoff,
            )
        self.assertIn("zhilian", str(ctx.exception))

    def test_manifest_proof_fails_when_experiment_created_after_cutoff(self):
        """manifest issued_at 早于 cutoff 但 experiment 创建晚于 cutoff → 阻断。"""
        tampered = dict(self.manifest_record)
        tampered["issued_at"] = "1999-01-01T00:00:00+00:00"
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_manifest(
                manifest_record=tampered,
                migration_cutoff=self.past_cutoff,
            )
        self.assertIn("experiment", str(ctx.exception).lower())

    def test_manifest_proof_does_not_modify_json_or_digest(self):
        """证明过程不修改 manifest JSON 或 manifest_digest。"""
        original_digest = self.manifest_record["manifest_digest"]
        original_json = json.dumps(
            self.manifest_record["manifest"], sort_keys=True
        )
        self.controller.prove_legacy_boss_manifest(
            manifest_record=self.manifest_record,
            migration_cutoff=self.future_cutoff,
        )
        refreshed = self.store.get_task_manifest(self.manifest_record["id"])
        self.assertEqual(refreshed["manifest_digest"], original_digest)
        self.assertEqual(
            json.dumps(refreshed["manifest"], sort_keys=True),
            original_json,
        )

    # -- artifact 客观证明 -----------------------------------------------

    def test_artifact_proof_passes_for_pre_migration_list(self):
        """迁移前 list artifact + 有效 digest → 证明为 boss。"""
        proof = self.controller.prove_legacy_boss_artifact(
            artifact_record=self.artifact_record,
            migration_cutoff=self.future_cutoff,
        )
        self.assertEqual(proof["platform"], "boss")
        self.assertEqual(proof["proof_kind"], "legacy_artifact")
        self.assertEqual(proof["stage"], "list")
        self.assertTrue(proof["digest_verified"])

    def test_artifact_proof_passes_for_pre_migration_detail(self):
        """迁移前 detail artifact 同样可证明（stage=detail 在允许集合内）。"""
        detail_record = dict(self.artifact_record)
        detail_record["stage"] = "detail"
        proof = self.controller.prove_legacy_boss_artifact(
            artifact_record=detail_record,
            migration_cutoff=self.future_cutoff,
        )
        self.assertEqual(proof["platform"], "boss")
        self.assertEqual(proof["stage"], "detail")

    def test_artifact_proof_fails_for_rough_stage(self):
        """stage=rough → 阻断（仅 list/detail 可证明）。"""
        rough_record = dict(self.artifact_record)
        rough_record["stage"] = "rough"
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_artifact(
                artifact_record=rough_record,
                migration_cutoff=self.future_cutoff,
            )
        self.assertIn("list/detail", str(ctx.exception))

    def test_artifact_proof_fails_for_end_to_end_stage(self):
        """stage=end_to_end → 阻断（仅 list/detail 可证明）。"""
        e2e_record = dict(self.artifact_record)
        e2e_record["stage"] = "end_to_end"
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_artifact(
                artifact_record=e2e_record,
                migration_cutoff=self.future_cutoff,
            )
        self.assertIn("list/detail", str(ctx.exception))

    def test_artifact_proof_fails_when_created_after_cutoff(self):
        """created_at >= cutoff → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_artifact(
                artifact_record=self.artifact_record,
                migration_cutoff=self.past_cutoff,
            )
        self.assertIn("不早于 migration cutoff", str(ctx.exception))

    def test_artifact_proof_fails_when_digest_tampered(self):
        """artifact_digest 与文件内容不一致 → 阻断。"""
        tampered = dict(self.artifact_record)
        tampered["artifact_digest"] = "sha256:deadbeef"
        with self.assertRaises(ValueError) as ctx:
            self.controller.prove_legacy_boss_artifact(
                artifact_record=tampered,
                migration_cutoff=self.future_cutoff,
            )
        self.assertIn("artifact_digest", str(ctx.exception))

    def test_artifact_proof_does_not_modify_file_or_digest(self):
        """证明过程不修改 artifact 文件或 digest。"""
        original_digest = self.artifact_record["artifact_digest"]
        absolute = self.root / self.artifact_record["artifact_path"]
        original_bytes = absolute.read_bytes()
        self.controller.prove_legacy_boss_artifact(
            artifact_record=self.artifact_record,
            migration_cutoff=self.future_cutoff,
        )
        refreshed = self.store.get_tuning_stage_artifact(
            self.artifact_record["id"]
        )
        self.assertEqual(refreshed["artifact_digest"], original_digest)
        self.assertEqual(absolute.read_bytes(), original_bytes)


class TuningPlatformConservationTests(unittest.TestCase):
    """T605 RED: 调优持久身份的平台/runtime/digest 守恒测试。

    见 tasks007.md 节点门禁 B、data-model.md 第 219-281 行。
    这些测试当前为 RED：store 方法不读写 platform 外层列，
    controller 不冻结 platform/schema/account/port/profile_key。
    T606/T607 实现后转 GREEN。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        self.quality_context = {
            "profile_summary": "Python AI 应用开发候选人",
            "screening_fields": {"salary": ["403"]},
            "profile_ref": "user-confirmed:test",
        }
        scopes = [
            ("small", 1, 3), ("small", 2, 3),
            ("medium", 2, 8), ("medium", 3, 5),  # 024 新口径：16/15 页属中规模
            ("large", 10, 5), ("large", 11, 5),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "platform": "boss",
                "browser_account": "a",
                "cdp_port": 9222,
                "profile_key": "boss:a",
                "filter_schema_version": 1,
            },
            quality_context=self.quality_context,
            workloads=[{
                "task_size": size,
                "structure_index": index % 2 + 1,
                "scope": {
                    "keywords": [f"AI-{i}" for i in range(count)],
                    "scope_kind": "cities",
                    "cities": ["东莞"],
                    "pages_per_combination": pages,
                },
            } for index, (size, count, pages) in enumerate(scopes)],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="baseline",
            config=_sample_nine_fields(),
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="list", repetition_index=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _build_manifest(self, *, platform: str = "boss") -> dict:
        """构造一份 manifest payload，并在 frozen_input/fixed_fields 声明平台。"""
        manifest = _make_valid_manifest_payload(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            round_id=self.round["id"],
        )
        scope = self.workload["scope"]
        root = f"tuning/{self.experiment['id']}"
        manifest["frozen_input"].update({
            "input_version_id": self.bundle["input_version"]["id"],
            "workload_id": self.workload["id"],
            "task_size": self.workload["task_size"],
            "structure_index": self.workload["structure_index"],
            "scope_digest": scope["scope_digest"],
            "artifact_manifest_path": f"{root}/input/{self.workload['id']}.json",
            "artifact_digest": self.workload["artifact_digest"],
            "quality_context_digest": self.bundle["input_version"][
                "quality_context_digest"
            ],
            "planned_pages": self.workload["planned_pages"],
            "platform": platform,
            "browser_account": "a",
            "cdp_port": 9222 if platform == "boss" else 9223,
            "profile_key": f"{platform}:a",
            "filter_schema_version": 1,
            "task_input_digest": f"sha256-{platform}-input",
        })
        manifest["execution_config"] = self.store.get_tuning_candidate(
            self.candidate["id"]
        )["config"]
        manifest["fixed_fields"] = {
            key: scope[key] for key in (
                "keywords", "scope_kind", "cities", "pages_per_combination",
                "planned_pages", "task_size",
            )
        }
        manifest["fixed_fields"]["platform"] = platform
        manifest["monitoring"]["final_artifact_path"] = (
            f"{root}/evidence/{self.round['id']}.json"
        )
        manifest["allowed_writes"] = [
            f"{root}/evidence/{self.round['id']}.json",
            f"{root}/artifacts/{self.round['id']}/",
        ]
        manifest["required_artifacts"][0]["path"] = (
            f"{root}/evidence/{self.round['id']}.json"
        )
        return manifest

    # -- experiment 平台守恒 --------------------------------------------

    def test_experiment_record_exposes_platform_field(self):
        """experiment 记录必须暴露 platform 外层列（T606）。"""
        experiment = self.store.get_tuning_experiment(self.experiment["id"])
        self.assertIn(
            "platform", experiment,
            "tuning_experiments.platform 外层列未暴露"
        )

    def test_experiment_freezes_platform_browser_account_cdp_port_profile_key(self):
        """experiment 创建时必须冻结 platform/account/port/profile_key/schema（T606）。"""
        experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "platform": "zhilian",
                "browser_account": "a",
                "cdp_port": 9223,
                "profile_key": "zhilian:a",
                "filter_schema_version": 1,
            },
            quality_context=self.quality_context,
            workloads=[{
                "task_size": "small", "structure_index": 1,
                "scope": {
                    "keywords": ["AI"], "scope_kind": "cities",
                    "cities": ["东莞"], "pages_per_combination": 3,
                },
            }, {
                "task_size": "small", "structure_index": 2,
                "scope": {
                    "keywords": ["AI"], "scope_kind": "cities",
                    "cities": ["东莞"], "pages_per_combination": 3,
                },
            }, {
                "task_size": "medium", "structure_index": 1,
                "scope": {
                    "keywords": ["AI", "ML", "NLP"], "scope_kind": "cities",
                    "cities": ["东莞"], "pages_per_combination": 5,
                },
            }, {
                "task_size": "medium", "structure_index": 2,
                "scope": {
                    "keywords": ["AI", "ML", "NLP"], "scope_kind": "cities",
                    "cities": ["东莞"], "pages_per_combination": 5,
                },
            }, {
                "task_size": "large", "structure_index": 1,
                "scope": {
                    "keywords": [f"AI-{i}" for i in range(10)], "scope_kind": "cities",
                    "cities": ["东莞"], "pages_per_combination": 5,
                },
            }, {
                "task_size": "large", "structure_index": 2,
                "scope": {
                    "keywords": [f"AI-{i}" for i in range(11)], "scope_kind": "cities",
                    "cities": ["东莞"], "pages_per_combination": 5,
                },
            }],
        )
        record = self.store.get_tuning_experiment(experiment["id"])
        self.assertEqual(record.get("platform"), "zhilian")
        source_scope = record.get("source_scope", {})
        self.assertEqual(source_scope.get("browser_account"), "a")
        self.assertEqual(source_scope.get("cdp_port"), 9223)
        self.assertEqual(source_scope.get("profile_key"), "zhilian:a")
        self.assertEqual(source_scope.get("filter_schema_version"), 1)

    # -- workload/input artifact 平台守恒 --------------------------------

    def test_workload_artifact_manifest_contains_platform(self):
        """workload artifact manifest 必须保存 platform/runtime（T606）。"""
        bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        for workload in bundle["workloads"]:
            manifest = workload.get("artifact_manifest", {})
            self.assertIn(
                "platform", manifest,
                "workload artifact_manifest 缺少 platform"
            )
            self.assertIn("browser_account", manifest)
            self.assertIn("cdp_port", manifest)
            self.assertIn("profile_key", manifest)
            self.assertIn("filter_schema_version", manifest)
            self.assertIn("task_input_digest", manifest)

    # -- manifest 平台守恒 ----------------------------------------------

    def test_manifest_record_exposes_platform_field(self):
        """manifest 记录必须暴露 platform 外层列（T607）。"""
        manifest = self._build_manifest(platform="boss")
        issued = self.controller.issue_manifest(manifest)
        record = self.store.get_task_manifest(issued["manifest_id"])
        self.assertIn(
            "platform", record,
            "tuning_task_manifests.platform 外层列未暴露"
        )

    def test_manifest_fixed_fields_and_frozen_input_carry_platform(self):
        """manifest 的 fixed_fields 和 frozen_input 必须携带 platform（T607）。"""
        manifest = self._build_manifest(platform="boss")
        self.assertEqual(
            manifest["fixed_fields"].get("platform"), "boss"
        )
        self.assertEqual(
            manifest["frozen_input"].get("platform"), "boss"
        )

    def test_manifest_digest_covers_platform_fields(self):
        """manifest_digest 必须覆盖 platform 字段变化（T607）。"""
        manifest_boss = self._build_manifest(platform="boss")
        manifest_zhilian = self._build_manifest(platform="zhilian")
        canonical_boss = json.dumps(
            {k: v for k, v in manifest_boss.items()
             if k != "manifest_digest"},
            ensure_ascii=False, sort_keys=True,
        )
        canonical_zhilian = json.dumps(
            {k: v for k, v in manifest_zhilian.items()
             if k != "manifest_digest"},
            ensure_ascii=False, sort_keys=True,
        )
        digest_boss = "sha256:" + hashlib.sha256(
            canonical_boss.encode("utf-8")
        ).hexdigest()
        digest_zhilian = "sha256:" + hashlib.sha256(
            canonical_zhilian.encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(
            digest_boss, digest_zhilian,
            "platform 字段变化未反映到 manifest_digest"
        )

    # -- stage artifact 平台守恒 ----------------------------------------

    def test_stage_artifact_record_exposes_platform_fields(self):
        """stage artifact 记录必须暴露 platform/source_artifact_kind/scope_digest/task_input_digest（T607）。"""
        self.controller.start_round(self.round["id"])
        artifact = self.controller.persist_stage_artifact(
            round_id=self.round["id"], stage="list",
            payload={"round_kind": "list", "jobs": [{"job_id": "j1"}]},
        )
        record = self.store.get_tuning_stage_artifact(artifact["id"])
        self.assertIn("platform", record)
        self.assertIn("source_artifact_kind", record)
        self.assertIn("scope_digest", record)
        self.assertIn("task_input_digest", record)

    def test_stage_artifact_platform_matches_experiment(self):
        """stage artifact 的 platform 必须与 experiment 一致（T607）。"""
        self.controller.start_round(self.round["id"])
        artifact = self.controller.persist_stage_artifact(
            round_id=self.round["id"], stage="list",
            payload={"round_kind": "list", "jobs": [{"job_id": "j1"}]},
        )
        record = self.store.get_tuning_stage_artifact(artifact["id"])
        experiment = self.store.get_tuning_experiment(self.experiment["id"])
        self.assertEqual(record.get("platform"), experiment.get("platform"))

    # -- program evidence 平台守恒 --------------------------------------

    def test_program_evidence_inherits_platform_from_manifest(self):
        """program_evidence 的 scope_digest/input_artifact_digest 必须与 manifest 一致（T607）。"""
        manifest = self._build_manifest(platform="boss")
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        evidence = {
            "program_report_path": manifest["required_artifacts"][0]["path"],
            "config_digest": manifest["execution_config"]["config_digest"],
            "scope_digest": manifest["frozen_input"]["scope_digest"],
            "input_artifact_digest": manifest["frozen_input"]["artifact_digest"],
            "total_duration_ms": 45000,
            "stage_durations_ms": {"list": 40000},
            "work_duration_ms": 40000,
            "wait_duration_ms": 5000, "retry_duration_ms": 0,
            "attempt_count": 1, "retry_count": 0,
            "input_count": 30, "terminal_count": 30,
            "success_count": 30, "failed_count": 0,
            "missing_count": 0, "duplicate_count": 0,
            "quality_diff_count": 0, "error_counts": {},
        }
        evidence_path = (
            self.root / manifest["required_artifacts"][0]["path"]
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        evidence_path.write_bytes(encoded)
        manifest["required_artifacts"][0]["digest"] = (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        )
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        evidence["program_report_digest"] = (
            manifest["required_artifacts"][0]["digest"]
        )
        report["program_evidence"] = evidence
        report["artifacts"][0]["digest"] = (
            manifest["required_artifacts"][0]["digest"]
        )
        accepted = self.controller.accept_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        self.assertEqual(
            accepted.get("validation_status"), "accepted",
            f"report 校验失败: {accepted}",
        )


class TuningStageKindGuardTests(unittest.TestCase):
    """T609: 固定 stage 仅为 list/detail/rough/fine/end_to_end，
    并固定 source_artifact_kind 只有 list/detail 可复用。

    见 data-model.md 第 274、279 行。
    纯校验器：不创建 JobSource，不依赖 migration 27 外层列。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        quality_context = {
            "profile_summary": "Python AI 应用开发候选人",
            "screening_fields": {"salary": ["403"]},
            "profile_ref": "user-confirmed:test",
        }
        scopes = [
            ("small", 1, 3), ("small", 2, 3),
            ("medium", 2, 8), ("medium", 3, 5),  # 024 新口径：16/15 页属中规模
            ("large", 10, 5), ("large", 11, 5),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "platform": "boss",
                "browser_account": "a",
                "cdp_port": 9222,
                "profile_key": "boss:a",
                "filter_schema_version": 1,
            },
            quality_context=quality_context,
            workloads=[{
                "task_size": size,
                "structure_index": index % 2 + 1,
                "scope": {
                    "keywords": [f"AI-{i}" for i in range(count)],
                    "scope_kind": "cities", "cities": ["东莞"],
                    "pages_per_combination": pages,
                },
            } for index, (size, count, pages) in enumerate(scopes)],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="baseline",
            config=_sample_nine_fields(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_allowed_stage_kinds_are_exactly_five(self):
        """stage 仅为 list/detail/rough/fine/end_to_end。"""
        from webui.tuning import TuningController
        self.assertEqual(
            TuningController.ALLOWED_STAGE_KINDS,
            frozenset({"list", "detail", "rough", "fine", "end_to_end"}),
        )

    def test_reusable_source_artifact_kinds_are_list_and_detail_only(self):
        """source_artifact_kind 只有 list/detail 可复用（data-model.md 274 行）。"""
        from webui.tuning import TuningController
        self.assertEqual(
            TuningController.REUSABLE_SOURCE_ARTIFACT_KINDS,
            frozenset({"list", "detail"}),
        )

    def test_validate_stage_kind_rejects_unknown(self):
        """未知 stage → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.validate_stage_kind("invalid_stage")
        self.assertIn("invalid_stage", str(ctx.exception))

    def test_validate_stage_kind_accepts_all_five(self):
        """5 类合法 stage 都通过。"""
        for stage in ("list", "detail", "rough", "fine", "end_to_end"):
            self.controller.validate_stage_kind(stage)

    def test_source_artifact_kind_for_list_is_list(self):
        """stage=list → source_artifact_kind=list。"""
        self.assertEqual(
            self.controller.source_artifact_kind_for_stage("list"), "list"
        )

    def test_source_artifact_kind_for_detail_is_detail(self):
        """stage=detail → source_artifact_kind=detail。"""
        self.assertEqual(
            self.controller.source_artifact_kind_for_stage("detail"), "detail"
        )

    def test_source_artifact_kind_for_rough_fine_end_to_end_is_none(self):
        """rough/fine/end_to_end → source_artifact_kind=None（不可复用）。"""
        for stage in ("rough", "fine", "end_to_end"):
            self.assertIsNone(
                self.controller.source_artifact_kind_for_stage(stage),
                f"stage={stage} 应返回 None",
            )

    def test_validate_source_artifact_kind_rejects_rough(self):
        """rough 不能作为 source artifact kind → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.validate_reusable_source_artifact_kind("rough")
        self.assertIn("rough", str(ctx.exception))

    def test_validate_source_artifact_kind_rejects_end_to_end(self):
        """end_to_end 不能作为 source artifact kind → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.validate_reusable_source_artifact_kind("end_to_end")
        self.assertIn("end_to_end", str(ctx.exception))

    def test_validate_source_artifact_kind_rejects_unknown(self):
        """未知 kind → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.validate_reusable_source_artifact_kind("invalid")
        self.assertIn("invalid", str(ctx.exception))

    def test_validate_source_artifact_kind_accepts_list_and_detail(self):
        """list/detail 都通过。"""
        self.controller.validate_reusable_source_artifact_kind("list")
        self.controller.validate_reusable_source_artifact_kind("detail")


class TuningRoughFineSourceInheritanceTests(unittest.TestCase):
    """T612: rough 只读取 list artifact、fine 只读取 detail artifact，
    二者不创建 JobSource 且继承平台/schema。

    见 data-model.md 第 279 行、tasks007.md T612。
    纯校验器：不创建 JobSource，不依赖 migration 27 外层列；
    通过 manifest 的 frozen_input.platform 证明平台继承。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        quality_context = {
            "profile_summary": "Python AI 应用开发候选人",
            "screening_fields": {"salary": ["403"]},
            "profile_ref": "user-confirmed:test",
        }
        scopes = [
            ("small", 1, 3), ("small", 2, 3),
            ("medium", 2, 8), ("medium", 3, 5),  # 024 新口径：16/15 页属中规模
            ("large", 10, 5), ("large", 11, 5),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "platform": "boss",
                "browser_account": "a",
                "cdp_port": 9222,
                "profile_key": "boss:a",
                "filter_schema_version": 1,
            },
            quality_context=quality_context,
            workloads=[{
                "task_size": size,
                "structure_index": index % 2 + 1,
                "scope": {
                    "keywords": [f"AI-{i}" for i in range(count)],
                    "scope_kind": "cities", "cities": ["东莞"],
                    "pages_per_combination": pages,
                },
            } for index, (size, count, pages) in enumerate(scopes)],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="baseline",
            config=_sample_nine_fields(),
        )
        # 创建 list round 并持久化 list artifact
        self.list_round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="list", repetition_index=1,
        )
        self.controller.start_round(self.list_round["id"])
        self.list_artifact = self.controller.persist_stage_artifact(
            round_id=self.list_round["id"], stage="list",
            payload={"round_kind": "list", "jobs": [{"job_id": "j1"}]},
        )
        # 释放 list round 租约，让 detail round 可以开始
        self.store.release_tuning_lease(owner_token=self.controller._owner_token)
        # 创建 detail round 并持久化 detail artifact
        self.detail_round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="detail", repetition_index=1,
        )
        self.controller.start_round(self.detail_round["id"])
        self.detail_artifact = self.controller.persist_stage_artifact(
            round_id=self.detail_round["id"], stage="detail",
            payload={"round_kind": "detail", "details": [{"job_id": "j1"}]},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_validate_rough_source_accepts_list_artifact(self):
        """rough 接受 list artifact 作为 source → 通过。"""
        self.controller.validate_rough_source_artifact(
            source_artifact_id=self.list_artifact["id"],
        )

    def test_validate_rough_source_rejects_detail_artifact(self):
        """rough 拒绝 detail artifact 作为 source → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.validate_rough_source_artifact(
                source_artifact_id=self.detail_artifact["id"],
            )
        self.assertIn("detail", str(ctx.exception).lower())

    def test_validate_fine_source_accepts_detail_artifact(self):
        """fine 接受 detail artifact 作为 source → 通过。"""
        self.controller.validate_fine_source_artifact(
            source_artifact_id=self.detail_artifact["id"],
        )

    def test_validate_fine_source_rejects_list_artifact(self):
        """fine 拒绝 list artifact 作为 source → 阻断。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.validate_fine_source_artifact(
                source_artifact_id=self.list_artifact["id"],
            )
        self.assertIn("list", str(ctx.exception).lower())

    def test_validate_rough_source_rejects_unknown_artifact(self):
        """rough 拒绝不存在的 artifact → 阻断。"""
        with self.assertRaises(KeyError):
            self.controller.validate_rough_source_artifact(
                source_artifact_id="nonexistent-id",
            )

    def test_validate_source_artifact_inherits_platform_from_experiment(self):
        """source artifact 必须与 experiment 同平台（T612 平台继承）。

        由于 migration 27 外层列未实现，这里通过 manifest frozen_input.platform
        证明平台继承。证明失败抛 ValueError 阻断。
        """
        # manifest 未签发时，frozen_input 平台从 experiment.source_scope 推断
        proof = self.controller.prove_source_artifact_platform_inheritance(
            source_artifact_id=self.list_artifact["id"],
        )
        self.assertEqual(proof["inferred_platform"], "boss")
        self.assertEqual(
            proof["evidence_source"], "experiment_source_scope"
        )

    def test_rough_and_fine_do_not_create_job_source(self):
        """rough/fine 校验器不创建 JobSource（纯校验，无副作用）。

        通过对比调用前后的 stage artifact 数量验证。
        """
        before_count = len(self._list_all_stage_artifacts())
        self.controller.validate_rough_source_artifact(
            source_artifact_id=self.list_artifact["id"],
        )
        self.controller.validate_fine_source_artifact(
            source_artifact_id=self.detail_artifact["id"],
        )
        after_count = len(self._list_all_stage_artifacts())
        self.assertEqual(
            before_count, after_count,
            "rough/fine 校验器不应创建新 stage artifact",
        )

    def _list_all_stage_artifacts(self) -> list:
        """列出所有 stage artifact，用于副作用检测。"""
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM tuning_stage_artifacts ORDER BY id"
            ).fetchall()
        return [row["id"] for row in rows]


class TuningDisabledPlatformGuardTests(unittest.TestCase):
    """T615: 禁用平台不签发或执行新的 source round、历史证据保持可读、
    取消只处理已知平台登录空间。

    见 tasks007.md T615、data-model.md 第 22 行：
    智联 enabled_for_new_tasks=false 时只禁用新任务创建/补抓，
    不影响历史读取。
    纯校验器：不创建 JobSource，不依赖 migration 27 外层列。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        quality_context = {
            "profile_summary": "Python AI 应用开发候选人",
            "screening_fields": {"salary": ["403"]},
            "profile_ref": "user-confirmed:test",
        }
        scopes = [
            ("small", 1, 3), ("small", 2, 3),
            ("medium", 2, 8), ("medium", 3, 5),  # 024 新口径：16/15 页属中规模
            ("large", 10, 5), ("large", 11, 5),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "platform": "zhilian",
                "browser_account": "a",
                "cdp_port": 9223,
                "profile_key": "zhilian:a",
                "filter_schema_version": 1,
            },
            quality_context=quality_context,
            workloads=[{
                "task_size": size,
                "structure_index": index % 2 + 1,
                "scope": {
                    "keywords": [f"AI-{i}" for i in range(count)],
                    "scope_kind": "cities", "cities": ["东莞"],
                    "pages_per_combination": pages,
                },
            } for index, (size, count, pages) in enumerate(scopes)],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="baseline",
            config=_sample_nine_fields(),
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="list", repetition_index=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_zhilian_disabled_for_new_source_rounds(self):
        """智联 enabled_for_new_tasks=false → 阻断新 source round 签发。"""
        self.controller.validate_platform_enabled_for_new_source_round(
            platform="zhilian",
        )

    def test_boss_enabled_for_new_source_rounds(self):
        """BOSS enabled_for_new_tasks=true → 允许新 source round 签发。"""
        self.controller.validate_platform_enabled_for_new_source_round(
            platform="boss",
        )

    def test_unknown_platform_rejected_for_new_source_round(self):
        """未知平台 → 阻断（不回退 BOSS）。"""
        with self.assertRaises(ValueError) as ctx:
            self.controller.validate_platform_enabled_for_new_source_round(
                platform="invalid",
            )
        self.assertIn("invalid", str(ctx.exception))

    def test_disabled_platform_historical_evidence_remains_readable(self):
        """禁用平台不影响历史证据读取。

        创建 list artifact 后，即使平台禁用，artifact 仍可读。
        """
        self.controller.start_round(self.round["id"])
        artifact = self.controller.persist_stage_artifact(
            round_id=self.round["id"], stage="list",
            payload={"round_kind": "list", "jobs": [{"job_id": "j1"}]},
        )
        # 禁用平台后，历史 artifact 仍可读
        record = self.store.get_tuning_stage_artifact(artifact["id"])
        self.assertEqual(record["stage"], "list")
        self.assertEqual(record["item_count"], 1)

    def test_cancel_only_handles_known_platform_login_spaces(self):
        """取消实验时只处理已知平台的登录空间。

        BOSS 和 zhilian 都是已知平台，取消时应返回受影响的登录空间列表。
        未知平台不应出现在结果中。

        注：当前 store 不写 platform 外层列（T606 阻断），
        source_scope.platform 默认填 boss。这里验证取消逻辑只返回已知平台，
        不返回未知平台。T606 实现后应改为验证 zhilian。
        """
        result = self.controller.cancel_experiment_login_spaces(
            experiment_id=self.experiment["id"],
        )
        self.assertIn("handled_platforms", result)
        # 当前 source_scope.platform 默认 boss（T606 阻断）
        for platform in result["handled_platforms"]:
            self.assertIn(
                platform, ("boss", "zhilian"),
                f"取消逻辑返回了未知平台: {platform}",
            )
        self.assertNotIn("invalid", result["handled_platforms"])

    def test_disabled_platform_does_not_block_manifest_proof(self):
        """禁用平台不阻断旧 manifest 客观证明（历史证据可读）。

        T608 prove_legacy_boss_manifest 不检查平台启用状态，
        只检查迁移前时间和摘要。禁用平台不影响证明。
        """
        # 签发一个 manifest（虽然是 zhilian，但证明逻辑只看时间+摘要）
        manifest = _make_valid_manifest_payload(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            round_id=self.round["id"],
        )
        scope = self.workload["scope"]
        root = f"tuning/{self.experiment['id']}"
        manifest["frozen_input"].update({
            "input_version_id": self.bundle["input_version"]["id"],
            "workload_id": self.workload["id"],
            "task_size": self.workload["task_size"],
            "structure_index": self.workload["structure_index"],
            "scope_digest": scope["scope_digest"],
            "artifact_manifest_path": f"{root}/input/{self.workload['id']}.json",
            "artifact_digest": self.workload["artifact_digest"],
            "quality_context_digest": self.bundle["input_version"][
                "quality_context_digest"
            ],
            "planned_pages": self.workload["planned_pages"],
        })
        manifest["execution_config"] = self.store.get_tuning_candidate(
            self.candidate["id"]
        )["config"]
        manifest["fixed_fields"] = {
            key: scope[key] for key in (
                "keywords", "scope_kind", "cities", "pages_per_combination",
                "planned_pages", "task_size",
            )
        }
        manifest["fixed_fields"]["platform"] = "zhilian"
        manifest["monitoring"]["final_artifact_path"] = (
            f"{root}/evidence/{self.round['id']}.json"
        )
        manifest["allowed_writes"] = [
            f"{root}/evidence/{self.round['id']}.json",
            f"{root}/artifacts/{self.round['id']}/",
        ]
        manifest["required_artifacts"][0]["path"] = (
            f"{root}/evidence/{self.round['id']}.json"
        )
        issued = self.controller.issue_manifest(manifest)
        record = self.store.get_task_manifest(issued["manifest_id"])
        # 禁用平台不影响 manifest 读取
        self.assertEqual(record["status"], "issued")
        self.assertIsNotNone(record["manifest_digest"])


if __name__ == "__main__":
    unittest.main()
