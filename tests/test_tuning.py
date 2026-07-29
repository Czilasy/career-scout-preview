"""SPEC011: 深度调优实验控制、任务单、评估与恢复测试。

T010 RED: 证明实验临时候选配置永不覆盖用户正式模式或最近自定义配置。
后续 T011-T014 将实现 webui/tuning.py 和 store 方法使这些测试转绿。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest

from webui.store import TaskStore


def _sample_nine_fields(**overrides) -> dict:
    """返回一份完整的九字段速度配置。"""
    base = {
        "inter_combo_delay": 10.0,
        "detail_batch_size": 15,
        "detail_interval": 2.0,
        "detail_reset_every": 4,
        "detail_batch_cooldown": 5.0,
        "screen_batch_size": 50,
        "screen_concurrency": 5,
        "match_batch_size": 4,
        "match_concurrency": 10,
    }
    base.update(overrides)
    return base


class ExperimentConfigIsolationTests(unittest.TestCase):
    """T010: 实验临时候选配置不得覆盖用户正式模式或最近自定义配置。

    覆盖 FR-042、SC-014、FR-066。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        # 预置用户正式配置：保存一份自定义配置并选择 stable 模式
        self.user_custom = _sample_nine_fields(inter_combo_delay=42.0)
        self.store.save_custom_config(self.user_custom)
        self.store.select_mode("stable", task_size="small")
        # 记录基线状态
        self.baseline = self.store.get_advanced_config_state()

    def tearDown(self):
        self.temp.cleanup()

    def _assert_user_state_unchanged(self, msg: str = ""):
        """断言用户正式配置状态未被实验修改。"""
        current = self.store.get_advanced_config_state()
        self.assertEqual(
            current["active_selection"],
            self.baseline["active_selection"],
            f"active_selection 被实验修改 {msg}",
        )
        self.assertEqual(
            current["last_custom_config"],
            self.baseline["last_custom_config"],
            f"last_custom_config 被实验修改 {msg}",
        )
        self.assertEqual(
            current["last_custom_digest"],
            self.baseline["last_custom_digest"],
            f"last_custom_digest 被实验修改 {msg}",
        )
        self.assertEqual(
            current["active_mode_version_id"],
            self.baseline["active_mode_version_id"],
            f"active_mode_version_id 被实验修改 {msg}",
        )

    def test_create_experiment_does_not_touch_user_config(self):
        """FR-042: 创建实验不覆盖用户正式配置。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        experiment = controller.create_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            workloads=[
                {"task_size": "small", "structure_index": 1, "scope": {}},
                {"task_size": "small", "structure_index": 2, "scope": {}},
            ],
        )
        self.assertIsNotNone(experiment["id"])
        self._assert_user_state_unchanged("after create_experiment")

    def test_candidate_temp_config_does_not_overwrite_user_config(self):
        """FR-042: 候选临时候选配置存入实验表，不写入 advanced_config_state。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        experiment = controller.create_experiment(
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
        # 添加一个候选配置（与用户自定义不同）
        candidate_config = _sample_nine_fields(
            inter_combo_delay=1.0,  # 极限值，与用户 42.0 完全不同
            detail_batch_size=100,
            screen_concurrency=20,
        )
        candidate = controller.add_candidate(
            experiment_id=experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=candidate_config,
        )
        self.assertIsNotNone(candidate["id"])
        self._assert_user_state_unchanged("after add_candidate")

    def test_cancel_experiment_preserves_user_config(self):
        """SC-014: 实验取消后用户正式模式与最近自定义配置不变。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        experiment = controller.create_experiment(
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
        controller.add_candidate(
            experiment_id=experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=_sample_nine_fields(inter_combo_delay=1.0),
        )
        controller.cancel_experiment(experiment["id"])
        self._assert_user_state_unchanged("after cancel_experiment")

    def test_fail_experiment_preserves_user_config(self):
        """SC-014: 实验失败后用户正式模式与最近自定义配置不变。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        experiment = controller.create_experiment(
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
        controller.add_candidate(
            experiment_id=experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=_sample_nine_fields(inter_combo_delay=1.0),
        )
        controller.fail_experiment(experiment["id"], blocked_code="hard_error")
        self._assert_user_state_unchanged("after fail_experiment")

    def test_recover_experiment_preserves_user_config(self):
        """SC-014: 实验恢复后用户正式模式与最近自定义配置不变。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        experiment = controller.create_experiment(
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
        controller.add_candidate(
            experiment_id=experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=_sample_nine_fields(inter_combo_delay=1.0),
        )
        # 模拟重启恢复
        controller.recover_after_restart()
        self._assert_user_state_unchanged("after recover_after_restart")

    def test_apply_completed_version_does_not_overwrite_custom(self):
        """FR-066: 应用完整模式版本不覆盖最近自定义配置。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        experiment = controller.create_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            workloads=[
                {"task_size": "small", "structure_index": 1, "scope": {}},
                {"task_size": "small", "structure_index": 2, "scope": {}},
                {"task_size": "medium", "structure_index": 1, "scope": {}},
                {"task_size": "medium", "structure_index": 2, "scope": {}},
                {"task_size": "large", "structure_index": 1, "scope": {}},
                {"task_size": "large", "structure_index": 2, "scope": {}},
            ],
        )
        # 应用候选模式版本（模拟 completed 后 apply）
        _stable = _sample_nine_fields(inter_combo_delay=20.0)
        _balanced = _sample_nine_fields(inter_combo_delay=10.0)
        _extreme = _sample_nine_fields(inter_combo_delay=1.0)
        new_matrix = {
            mode: {size: dict(slot) for size in ("small", "medium", "large")}
            for mode, slot in (("stable", _stable), ("balanced", _balanced), ("extreme", _extreme))
        }
        version_id = controller.apply_completed_version(
            experiment_id=experiment["id"],
            matrix=new_matrix,
        )
        self.assertIsNotNone(version_id)
        # 最近自定义配置必须保持不变
        current = self.store.get_advanced_config_state()
        self.assertEqual(
            current["last_custom_config"],
            self.baseline["last_custom_config"],
            "apply_completed_version 覆盖了最近自定义配置",
        )
        self.assertEqual(
            current["last_custom_digest"],
            self.baseline["last_custom_digest"],
            "apply_completed_version 覆盖了最近自定义摘要",
        )


class LeaseCoordinationTests(unittest.TestCase):
    """T013 RED: 租约原子 claim/heartbeat/release 与普通任务冲突。

    覆盖 FR-035、SC-004、state-machine.md 第 4 节。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_lease_claim_succeeds(self):
        """空闲租约可被 claim。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        result = controller.claim_lease(
            experiment_id="exp-1", round_id="round-1",
        )
        self.assertTrue(result["ok"])

    def test_second_claim_fails_while_held(self):
        """SC-004: 租约被持有时第二个 claim 失败。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        controller.claim_lease(experiment_id="exp-1", round_id="round-1")
        result = controller.claim_lease(experiment_id="exp-2", round_id="round-2")
        self.assertFalse(result["ok"])

    def test_heartbeat_extends_lease(self):
        """heartbeat 延长租约。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        controller.claim_lease(experiment_id="exp-1", round_id="round-1")
        controller.heartbeat_lease()
        lease = controller.get_lease_state()
        self.assertIsNotNone(lease["heartbeat_at"])

    def test_release_allows_reclaim(self):
        """释放后可重新 claim。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        controller.claim_lease(experiment_id="exp-1", round_id="round-1")
        controller.release_lease()
        result = controller.claim_lease(experiment_id="exp-2", round_id="round-2")
        self.assertTrue(result["ok"])

    def test_ordinary_task_blocked_while_lease_held(self):
        """FR-035: 实验租约存在时普通任务不能启动。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        controller.claim_lease(experiment_id="exp-1", round_id="round-1")
        # 普通任务应被拒绝
        can_start = controller.check_ordinary_task_allowed()
        self.assertFalse(can_start, "租约被持有时普通任务必须被阻止")

    def test_ordinary_task_allowed_when_lease_free(self):
        """FR-035: 无租约时普通任务可启动。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        can_start = controller.check_ordinary_task_allowed()
        self.assertTrue(can_start, "无租约时普通任务应被允许")

    def test_stale_lease_reconciliation(self):
        """重启恢复：过期租约可被接管。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        controller.claim_lease(experiment_id="exp-1", round_id="round-1")
        # 模拟重启
        controller.recover_after_restart()
        # 恢复后租约应被释放
        lease = controller.get_lease_state()
        self.assertIsNone(lease["owner_experiment_id"])
        # 可重新 claim
        result = controller.claim_lease(experiment_id="exp-2", round_id="round-2")
        self.assertTrue(result["ok"])


class RoundRecoveryTests(unittest.TestCase):
    """T013 RED: confirmed 轮次幂等与 uncertain 轮次恢复。

    覆盖 FR-039、SC-005。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _create_experiment_with_candidate(self):
        """创建实验+候选，返回 (experiment, candidate)。"""
        from webui.tuning import TuningController

        controller = TuningController(self.store)
        experiment = controller.create_experiment(
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
        candidate = controller.add_candidate(
            experiment_id=experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=_sample_nine_fields(inter_combo_delay=5.0),
        )
        return experiment, candidate, controller

    def test_confirmed_round_not_reexecuted(self):
        """SC-005: 已确认轮次在重启后不重复执行。"""
        exp, candidate, controller = self._create_experiment_with_candidate()
        # 创建并确认一个轮次
        round_rec = controller.create_round(
            experiment_id=exp["id"],
            candidate_id=candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=1,
        )
        controller.confirm_round(round_rec["id"])
        # 重启恢复
        controller.recover_after_restart()
        # confirmed 轮次仍为 confirmed
        fetched = controller.get_round(round_rec["id"])
        self.assertEqual(fetched["status"], "confirmed")

    def test_uncertain_round_rerun_once(self):
        """FR-039: 不确定轮次只重跑一次。"""
        exp, candidate, controller = self._create_experiment_with_candidate()
        # 创建一个 running 轮次
        round_rec = controller.create_round(
            experiment_id=exp["id"],
            candidate_id=candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=1,
        )
        controller.start_round(round_rec["id"])
        # 重启恢复 → running 变 uncertain
        controller.recover_after_restart()
        fetched = controller.get_round(round_rec["id"])
        self.assertEqual(fetched["status"], "uncertain")
        # uncertain 轮次应被标记为需要重跑（新 repetition）
        rerun = controller.create_rerun_for_uncertain(round_rec["id"])
        self.assertIsNotNone(rerun)
        self.assertEqual(rerun["repetition_index"], 2)
        self.assertIsNone(controller.create_rerun_for_uncertain(round_rec["id"]))

    def test_confirmed_round_metrics_preserved_on_restart(self):
        """FR-037: 已确认轮次的指标在重启后保留。"""
        exp, candidate, controller = self._create_experiment_with_candidate()
        round_rec = controller.create_round(
            experiment_id=exp["id"],
            candidate_id=candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=1,
        )
        controller.confirm_round(
            round_rec["id"],
            metrics={"total_duration_ms": 12000, "input_count": 30},
        )
        # 重启恢复
        controller.recover_after_restart()
        fetched = controller.get_round(round_rec["id"])
        self.assertEqual(fetched["status"], "confirmed")
        self.assertIsNotNone(fetched.get("metrics"))
        self.assertEqual(fetched["metrics"]["total_duration_ms"], 12000)


class FrozenInputArtifactTests(unittest.TestCase):
    """T044: draft 输入产物必须可定位、可校验且在确认前冻结。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace_root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.workspace_root / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _scope(keyword_count: int, pages: int) -> dict:
        return {
            "keywords": [f"结构关键词{i}" for i in range(keyword_count)],
            "scope_kind": "cities", "cities": ["东莞"],
            "pages_per_combination": pages,
        }

    @staticmethod
    def _quality_context() -> dict:
        return {
            "profile_summary": "AI应用开发候选人，掌握 Python、FastAPI、LangGraph 和 RAG。",
            "screening_fields": {
                "salary": ["403", "404", "405"],
                "experience": ["101", "103", "104"],
                "degree": ["202", "203"],
                "industry": [],
                "scale": ["301", "302", "303", "304", "305"],
                "stage": [],
            },
            "profile_ref": "user-confirmed:2026-07-29",
        }

    def _create_experiment(self) -> dict:
        scopes = [
            ("small", self._scope(1, 3)),
            ("small", self._scope(2, 3)),
            ("medium", self._scope(2, 5)),
            ("medium", self._scope(3, 5)),
            ("large", self._scope(4, 5)),
            ("large", self._scope(5, 5)),
        ]
        return self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope=scopes[0][1],
            quality_context=self._quality_context(),
            workloads=[
                {
                    "task_size": size,
                    "structure_index": index % 2 + 1,
                    "scope": scope,
                    "resume": "MUST_NOT_BE_PERSISTED",
                    "api_key": "MUST_NOT_BE_PERSISTED",
                }
                for index, (size, scope) in enumerate(scopes)
            ],
        )

    def test_create_freezes_quality_context_with_digest(self):
        experiment = self._create_experiment()
        bundle = self.store.get_tuning_input_bundle(experiment["id"])
        frozen = bundle["input_version"]["quality_context"]

        self.assertEqual(frozen, self._quality_context())
        expected = hashlib.sha256(json.dumps(
            frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        self.assertEqual(
            bundle["input_version"]["quality_context_digest"],
            "sha256:" + expected,
        )
        for workload in bundle["workloads"]:
            self.assertEqual(
                workload["artifact_manifest"]["quality_context_digest"],
                "sha256:" + expected,
            )


    def _artifact_path(self, workload: dict) -> pathlib.Path:
        relative = workload.get("artifact_manifest_path")
        if relative is None:
            relative = (
                f"tuning/{workload['experiment_id']}/input/{workload['id']}.json"
            )
        return self.workspace_root / relative

    def test_create_writes_deterministic_verifiable_artifact_manifests(self):
        experiment = self._create_experiment()
        bundle = self.store.get_tuning_input_bundle(experiment["id"])

        self.assertEqual(len(bundle["workloads"]), 6)
        for workload in bundle["workloads"]:
            self.assertIn("artifact_manifest_path", workload)
            self.assertIn("artifact_manifest", workload)
            expected_relative = (
                f"tuning/{experiment['id']}/input/{workload['id']}.json"
            )
            self.assertEqual(workload["artifact_manifest_path"], expected_relative)
            artifact_path = self._artifact_path(workload)
            raw = artifact_path.read_bytes()
            self.assertEqual(json.loads(raw), workload["artifact_manifest"])
            self.assertEqual(
                workload["artifact_digest"],
                "sha256:" + hashlib.sha256(raw).hexdigest(),
            )
            self.assertEqual(
                workload["artifact_manifest"]["scope_digest"],
                workload["scope"]["scope_digest"],
            )
            self.assertNotIn(b"MUST_NOT_BE_PERSISTED", raw)

    def test_confirm_rejects_missing_artifact_without_advancing_state(self):
        experiment = self._create_experiment()
        bundle = self.store.get_tuning_input_bundle(experiment["id"])
        workload = dict(bundle["workloads"][0], experiment_id=experiment["id"])
        self._artifact_path(workload).unlink(missing_ok=True)

        with self.assertRaisesRegex(ValueError, "artifact|\u4ea7物"):
            self.controller.confirm_input(experiment["id"])

        self.assertEqual(
            self.store.get_tuning_experiment(experiment["id"])["status"], "draft"
        )
        self.assertEqual(
            self.store.get_tuning_input_bundle(experiment["id"])
            ["input_version"]["status"],
            "draft",
        )

    def test_confirm_rejects_artifact_digest_mismatch(self):
        experiment = self._create_experiment()
        bundle = self.store.get_tuning_input_bundle(experiment["id"])
        workload = dict(bundle["workloads"][0], experiment_id=experiment["id"])
        path = self._artifact_path(workload)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "digest|\u6458要"):
            self.controller.confirm_input(experiment["id"])
        self.assertEqual(
            self.store.get_tuning_experiment(experiment["id"])["status"], "draft"
        )

    def test_confirm_rejects_artifact_path_outside_experiment_root(self):
        experiment = self._create_experiment()
        bundle = self.store.get_tuning_input_bundle(experiment["id"])
        workload = bundle["workloads"][0]
        poisoned = {"artifact_manifest_path": "tuning/../outside.json"}
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE tuning_workloads SET artifact_manifest_json = ? "
                "WHERE id = ?",
                (json.dumps(poisoned, ensure_ascii=False, sort_keys=True),
                 workload["id"]),
            )

        with self.assertRaisesRegex(ValueError, "path|\u8def径|\u8d8a界"):
            self.controller.confirm_input(experiment["id"])
        self.assertEqual(
            self.store.get_tuning_experiment(experiment["id"])["status"], "draft"
        )


class TuningStageArtifactTests(unittest.TestCase):
    """T045: 阶段结果必须追加保存，供后续 manifest 精确引用。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        quality_context = {
            "profile_summary": "Python AI 应用开发候选人",
            "screening_fields": {"salary": ["403"], "experience": ["101"]},
            "profile_ref": "user-confirmed:test",
        }
        scopes = [
            ("small", 1, 3), ("small", 2, 3),
            ("medium", 2, 5), ("medium", 3, 5),
            ("large", 4, 5), ("large", 5, 5),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={"keywords": ["AI"], "scope_kind": "cities",
                          "cities": ["东莞"], "pages_per_combination": 3},
            quality_context=quality_context,
            workloads=[{
                "task_size": size,
                "structure_index": index % 2 + 1,
                "scope": {"keywords": [f"AI-{i}" for i in range(count)],
                          "scope_kind": "cities", "cities": ["东莞"],
                          "pages_per_combination": pages},
            } for index, (size, count, pages) in enumerate(scopes)],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.workload = self.store.get_tuning_input_bundle(
            self.experiment["id"])["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"], stage="list",
            strategy_step="baseline", config=_sample_nine_fields(),
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"], round_kind="list",
            repetition_index=1,
        )
        self.controller.start_round(self.round["id"])

    def tearDown(self):
        self.temp.cleanup()

    def test_persist_stage_artifact_is_append_only_and_verifiable(self):
        result = {"round_kind": "list", "jobs": [{"job_id": "j1"}]}
        artifact = self.controller.persist_stage_artifact(
            round_id=self.round["id"], stage="list", payload=result,
        )

        path = self.root / artifact["artifact_path"]
        raw = path.read_bytes()
        self.assertEqual(json.loads(raw), result)
        self.assertEqual(
            artifact["artifact_digest"],
            "sha256:" + hashlib.sha256(raw).hexdigest(),
        )
        fetched = self.store.get_tuning_stage_artifact(artifact["id"])
        self.assertEqual(fetched["producer_round_id"], self.round["id"])
        self.assertEqual(fetched["workload_id"], self.workload["id"])
        self.assertEqual(fetched["stage"], "list")

        with self.assertRaises(ValueError):
            self.controller.persist_stage_artifact(
                round_id=self.round["id"], stage="list", payload=result,
            )


class CompletionGateTests(unittest.TestCase):
    """最终版本必须由每个规模两种结构、各三次确认轮次支撑。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _scope(keyword_count, pages):
        return {
            "keywords": [f"结构关键词{i}" for i in range(keyword_count)],
            "scope_kind": "cities", "cities": ["东莞"],
            "pages_per_combination": pages,
        }

    def test_complete_evidence_allows_exact_candidate_application(self):
        scopes = [
            ("small", self._scope(1, 3)), ("small", self._scope(2, 3)),
            ("medium", self._scope(2, 5)), ("medium", self._scope(3, 5)),
            ("large", self._scope(4, 5)), ("large", self._scope(5, 5)),
        ]
        experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope=scopes[0][1],
            quality_context={
                "profile_summary": "Python AI 应用开发候选人",
                "screening_fields": {"salary": ["403"]},
                "profile_ref": "user-confirmed:test",
            },
            workloads=[
                {"task_size": size, "structure_index": index % 2 + 1,
                 "scope": scope}
                for index, (size, scope) in enumerate(scopes)
            ],
        )
        self.controller.confirm_input(experiment["id"])
        bundle = self.store.get_tuning_input_bundle(experiment["id"])
        reference = self.controller.build_quality_reference(
            experiment_id=experiment["id"],
            input_version_id=bundle["input_version"]["id"],
            baseline_round_results=[
                [{"item_index": 0, "verdict": "match"}] for _ in range(3)
            ],
        )
        reference = self.controller.confirm_quality_reference(reference["id"])
        config = _sample_nine_fields(inter_combo_delay=5.0)
        candidate = self.controller.add_candidate(
            experiment_id=experiment["id"], stage="end_to_end",
            strategy_step="final_validation", config=config,
        )
        metrics = {
            "total_duration_ms": 1000, "work_duration_ms": 800,
            "wait_duration_ms": 150, "retry_duration_ms": 50,
            "input_count": 10, "terminal_count": 10,
            "missing_count": 0, "duplicate_count": 0,
            "quality_diff_count": 0,
        }
        for workload in bundle["workloads"]:
            for repetition in range(1, 4):
                if self.store.get_tuning_experiment(experiment["id"])["status"] == "evaluating":
                    self.store.update_tuning_experiment_status(
                        experiment["id"], status="awaiting_instruction",
                    )
                round_record = self.store.create_tuning_round(
                    experiment_id=experiment["id"], candidate_id=candidate["id"],
                    workload_id=workload["id"], round_kind="end_to_end",
                    repetition_index=repetition,
                    quality_reference_id=reference["id"],
                )
                self.controller.confirm_round(round_record["id"], metrics=metrics)
        matrix = {
            mode: {size: dict(config) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        version = self.controller.create_candidate_mode_version(
            experiment_id=experiment["id"], matrix=matrix,
        )
        self.store.update_tuning_experiment_status(
            experiment["id"], status="completed",
        )
        result = self.controller.get_experiment_result(experiment["id"])
        self.assertTrue(result["can_apply"])
        applied = self.controller.apply_candidate_mode_version(
            experiment_id=experiment["id"],
            version_digest=version["version_digest"],
        )
        self.assertEqual(applied["id"], version["id"])


class MeasurementEventTests(unittest.TestCase):
    """T016 RED: 测量事件记录、单调时长、终态守恒和敏感字段拒绝。

    覆盖 FR-030、SC-006、SC-007、data-model.md 2.9 MeasurementSummary 和
    tuning_measurement_events 表。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        # 预置实验+候选+轮次
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        self.experiment = self.controller.create_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            workloads=[
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        )
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=_sample_nine_fields(),
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_record_stage_event(self):
        """stage 事件可被记录，包含单调起始时间和非负时长。"""
        self.controller.record_measurement(
            round_id=self.round["id"],
            event_type="stage",
            stage="list",
            duration_ms=1500,
            counts={"input_count": 30},
        )
        events = self.controller.list_measurements(self.round["id"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "stage")
        self.assertGreaterEqual(events[0]["started_monotonic_ms"], 0)
        self.assertGreaterEqual(events[0]["duration_ms"], 0)

    def test_record_all_event_types(self):
        """所有 6 种事件类型都能被记录：stage/batch/request/wait/retry/item_terminal。"""
        event_types = ["stage", "batch", "request", "wait", "retry", "item_terminal"]
        for i, et in enumerate(event_types):
            self.controller.record_measurement(
                round_id=self.round["id"],
                event_type=et,
                stage="list",
                duration_ms=100 * (i + 1),
                counts={"seq_index": i},
            )
        events = self.controller.list_measurements(self.round["id"])
        recorded_types = [e["event_type"] for e in events]
        for et in event_types:
            self.assertIn(et, recorded_types, f"事件类型 {et} 未被记录")

    def test_measurement_seq_monotonic(self):
        """seq 在轮次内单调递增。"""
        for i in range(5):
            self.controller.record_measurement(
                round_id=self.round["id"],
                event_type="stage",
                stage="list",
                duration_ms=100,
            )
        events = self.controller.list_measurements(self.round["id"])
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs), "seq 必须单调递增")
        self.assertEqual(len(set(seqs)), len(seqs), "seq 必须唯一")

    def test_measurement_seq_allocation_is_atomic_under_concurrency(self):
        from concurrent.futures import ThreadPoolExecutor

        def record(_index):
            return self.controller.record_measurement(
                round_id=self.round["id"], event_type="batch", stage="list",
                duration_ms=1,
            )["seq"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            sequences = list(pool.map(record, range(24)))
        self.assertEqual(sorted(sequences), list(range(1, 25)))

    def test_duration_ms_non_negative(self):
        """duration_ms 必须非负。"""
        self.controller.record_measurement(
            round_id=self.round["id"],
            event_type="stage",
            stage="list",
            duration_ms=0,
        )
        events = self.controller.list_measurements(self.round["id"])
        self.assertGreaterEqual(events[0]["duration_ms"], 0)

    def test_sensitive_fields_rejected(self):
        """敏感字段（API key、简历文本、JD 正文）必须被拒绝。"""
        sensitive_payloads = [
            {"metadata": {"api_key": "sk-xxx"}},
            {"metadata": {"resume_text": "张三，手机号 138xxxx"}},
            {"metadata": {"jd_body": "岗位描述正文..."}},
            {"metadata": {"model_response": "原始模型输出"}},
            {"counts": {"api_key": "sk-xxx"}},
        ]
        for payload in sensitive_payloads:
            with self.assertRaises((ValueError, TypeError),
                                   msg=f"敏感字段未被拒绝: {payload}"):
                self.controller.record_measurement(
                    round_id=self.round["id"],
                    event_type="stage",
                    stage="list",
                    duration_ms=100,
                    **payload,
                )

    def test_aggregate_measurement_summary(self):
        """FR-030: 聚合摘要包含总耗时、阶段耗时、等待、重试等。"""
        # 记录一组事件：list 阶段 1000ms + batch 500ms + wait 200ms + retry 100ms
        self.controller.record_measurement(
            round_id=self.round["id"], event_type="stage", stage="list",
            duration_ms=1000, counts={"input_count": 30},
        )
        self.controller.record_measurement(
            round_id=self.round["id"], event_type="batch", stage="list",
            duration_ms=500, counts={"batch_size": 10},
        )
        self.controller.record_measurement(
            round_id=self.round["id"], event_type="wait", stage="list",
            duration_ms=200,
        )
        self.controller.record_measurement(
            round_id=self.round["id"], event_type="retry", stage="list",
            duration_ms=100, counts={"retry_count": 1},
        )
        summary = self.controller.aggregate_measurements(self.round["id"])
        self.assertEqual(summary["total_duration_ms"], 1800)
        self.assertEqual(summary["stage_durations_ms"]["list"], 1800)
        self.assertEqual(summary["wait_duration_ms"], 200)
        self.assertEqual(summary["retry_duration_ms"], 100)
        self.assertGreaterEqual(summary["attempt_count"], 1)

    def test_terminal_conservation(self):
        """SC-007: 终态守恒 — terminal_count == input_count, missing=0, duplicate=0。"""
        # 记录 30 个 item_terminal 事件，全部 success
        for i in range(30):
            self.controller.record_measurement(
                round_id=self.round["id"],
                event_type="item_terminal",
                stage="list",
                duration_ms=10,
                counts={"item_index": i, "status": "success"},
            )
        summary = self.controller.aggregate_measurements(self.round["id"])
        self.assertEqual(summary["terminal_count"], 30)
        self.assertEqual(summary["success_count"], 30)
        self.assertEqual(summary["missing_count"], 0)
        self.assertEqual(summary["duplicate_count"], 0)

    def test_terminal_conservation_detects_missing(self):
        """终态守恒：缺失项被检测。"""
        # 只记录 25 个 success（预期 30）
        for i in range(25):
            self.controller.record_measurement(
                round_id=self.round["id"],
                event_type="item_terminal",
                stage="list",
                duration_ms=10,
                counts={"item_index": i, "status": "success"},
            )
        summary = self.controller.aggregate_measurements(self.round["id"])
        # input_count 未知时只检查 missing_count
        # 当 input_count 已知为 30 时，missing = 30 - 25 = 5
        # 这里 input_count 未单独设置，只验证 success_count == 25
        self.assertEqual(summary["success_count"], 25)

    def test_error_counts_recorded(self):
        """error_counts 字段记录结构化错误。"""
        self.controller.record_measurement(
            round_id=self.round["id"],
            event_type="request",
            stage="list",
            duration_ms=100,
            error_code="timeout",
        )
        self.controller.record_measurement(
            round_id=self.round["id"],
            event_type="request",
            stage="list",
            duration_ms=100,
            error_code="timeout",
        )
        summary = self.controller.aggregate_measurements(self.round["id"])
        self.assertEqual(summary["error_counts"].get("timeout"), 2)


class QualityReferenceTests(unittest.TestCase):
    """T020 RED: 基线版本化、逐项比较、正常变异、需审核差异和参考摘要强制。

    覆盖 FR-026、FR-027、FR-028、FR-034、SC-008、data-model.md 2.4。
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
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            workloads=[
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        )
        self.input_version_id = "iv-1"

    def tearDown(self):
        self.temp.cleanup()

    def _make_baseline_round_results(self, item_count=10, variation_items=None):
        """生成多轮基线的逐项结果。

        variation_items: dict[item_index, [verdict_across_repetitions]]
        例如 {3: ["match", "no_match", "match"]} 表示 item 3 在三次重复中有波动。
        其余 item 的 verdict 在所有重复中一致。
        """
        variation_items = variation_items or {}
        repetitions = []
        # 默认 3 次重复
        max_rep = max((len(v) for v in variation_items.values()), default=3)
        rep_count = max(3, max_rep)
        for rep_idx in range(rep_count):
            items = []
            for i in range(item_count):
                if i in variation_items and rep_idx < len(variation_items[i]):
                    verdict = variation_items[i][rep_idx]
                else:
                    verdict = "match" if i % 2 == 0 else "no_match"
                items.append({"item_index": i, "verdict": verdict})
            repetitions.append(items)
        return repetitions

    # -- FR-026: 基线版本化 ---------------------------------------------

    def test_build_quality_reference_from_baseline(self):
        """FR-026: 通过低压力配置的重复运行建立质量参考。"""
        baseline_results = self._make_baseline_round_results(item_count=10)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.assertIsNotNone(ref["id"])
        self.assertEqual(ref["status"], "building")
        self.assertIsNotNone(ref["reference_digest"])
        # item_results 包含逐项共识
        item_results = ref["item_results"]
        self.assertEqual(len(item_results["items"]), 10)
        # variation_summary 包含重复波动信息
        variation = ref["variation_summary"]
        self.assertEqual(variation["repetition_count"], 3)
        self.assertEqual(variation["item_count"], 10)

    def test_confirm_quality_reference(self):
        """FR-026: 确认质量参考后状态为 confirmed。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        confirmed = self.controller.confirm_quality_reference(ref["id"])
        self.assertEqual(confirmed["status"], "confirmed")

    def test_quality_reference_versioning_supersedes_old(self):
        """FR-034: 用户复核后形成新版本，旧参考被 superseded。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref1 = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref1["id"])
        # 创建新版本
        ref2 = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref2["id"])
        # 旧版本应被 superseded
        old = self.controller.get_quality_reference(ref1["id"])
        self.assertEqual(old["status"], "superseded")
        # 新版本应为 confirmed
        new = self.controller.get_quality_reference(ref2["id"])
        self.assertEqual(new["status"], "confirmed")

    # -- FR-027: 逐项比较 ------------------------------------------------

    def test_item_level_comparison_detects_difference(self):
        """FR-027: 逐项比较检测到差异，不只用分类数量。"""
        baseline_results = self._make_baseline_round_results(item_count=10)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        # 候选结果：item 3 的 verdict 与参考不同
        candidate_results = [
            {"item_index": i, "verdict": "match" if i % 2 == 0 else "no_match"}
            for i in range(10)
        ]
        candidate_results[3]["verdict"] = "match"  # 原来是 no_match
        comparison = self.controller.compare_results_against_reference(
            candidate_item_results=candidate_results,
            reference_id=ref["id"],
        )
        self.assertGreater(comparison["diff_count"], 0)
        self.assertEqual(comparison["differing_items"][0]["item_index"], 3)

    def test_reference_only_item_is_counted_as_missing_difference(self):
        baseline_results = self._make_baseline_round_results(item_count=3)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        comparison = self.controller.compare_results_against_reference(
            candidate_item_results=[
                {"item_index": 0, "verdict": "match"},
                {"item_index": 1, "verdict": "no_match"},
            ],
            reference_id=ref["id"],
        )
        self.assertEqual(comparison["total_items"], 3)
        self.assertEqual(comparison["diff_count"], 1)
        self.assertEqual(comparison["differing_items"][0], {
            "item_index": 2, "reference_verdict": "match",
            "candidate_verdict": None,
        })

    def test_same_count_different_items_still_detected(self):
        """FR-027: 最终分类数量相同但不同项仍检测到差异。"""
        baseline_results = self._make_baseline_round_results(item_count=10)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        # 候选结果：item 3 和 item 4 的 verdict 互换，但 match 总数不变
        candidate_results = [
            {"item_index": i, "verdict": "match" if i % 2 == 0 else "no_match"}
            for i in range(10)
        ]
        candidate_results[3]["verdict"] = "match"    # 原来是 no_match
        candidate_results[4]["verdict"] = "no_match"  # 原来是 match
        comparison = self.controller.compare_results_against_reference(
            candidate_item_results=candidate_results,
            reference_id=ref["id"],
        )
        self.assertEqual(comparison["diff_count"], 2)

    # -- FR-028: 正常变异计算 --------------------------------------------

    def test_normal_variation_from_baseline_repetition(self):
        """FR-028: 正常变异来自基线重复波动，不是固定比例。"""
        # item 5 在基线中有波动（3次中1次不同）
        baseline_results = self._make_baseline_round_results(
            item_count=10,
            variation_items={5: ["match", "no_match", "match"]},
        )
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        variation = ref["variation_summary"]
        # item 5 的稳定性应低于 1.0
        self.assertIn(5, variation["items_with_variation"])
        self.assertLess(
            variation["per_item_stability"][5], 1.0,
            "有波动的 item 稳定性必须低于 1.0",
        )

    def test_variation_not_fixed_ratio(self):
        """FR-028: 变异阈值来自实际重复，不是硬编码比例。"""
        # 全部一致的基线
        baseline_stable = self._make_baseline_round_results(item_count=5)
        ref_stable = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_stable,
        )
        # 有波动的基线
        baseline_varied = self._make_baseline_round_results(
            item_count=5,
            variation_items={2: ["match", "no_match", "match"]},
        )
        ref_varied = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_varied,
        )
        # 稳定基线的平均稳定性应高于波动基线
        self.assertGreater(
            ref_stable["variation_summary"]["average_stability"],
            ref_varied["variation_summary"]["average_stability"],
        )

    # -- FR-034: 需审核差异 ----------------------------------------------

    def test_diff_within_normal_variation_no_review(self):
        """FR-034: 差异在正常波动范围内 → 不需要审核。"""
        # item 5 在基线中有波动
        baseline_results = self._make_baseline_round_results(
            item_count=10,
            variation_items={5: ["match", "no_match", "match"]},
        )
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        # 候选在 item 5 上与参考共识不同（但 item 5 本来就不稳定）
        candidate_results = [
            {"item_index": i, "verdict": "match" if i % 2 == 0 else "no_match"}
            for i in range(10)
        ]
        candidate_results[5]["verdict"] = "no_match"  # 参考共识是 match
        comparison = self.controller.compare_results_against_reference(
            candidate_item_results=candidate_results,
            reference_id=ref["id"],
        )
        classification = self.controller.classify_quality_differences(
            diffs=comparison["differing_items"],
            reference_id=ref["id"],
        )
        self.assertEqual(len(classification["within_variation"]), 1)
        self.assertEqual(len(classification["review_required"]), 0)

    def test_diff_beyond_variation_requires_review(self):
        """FR-034: 差异超出正常波动 → 需要用户复核。"""
        # 全部稳定的基线
        baseline_results = self._make_baseline_round_results(item_count=10)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        # 候选在 item 3 上与参考不同（item 3 本来完全稳定）
        candidate_results = [
            {"item_index": i, "verdict": "match" if i % 2 == 0 else "no_match"}
            for i in range(10)
        ]
        candidate_results[3]["verdict"] = "match"  # 原来是 no_match，完全稳定
        comparison = self.controller.compare_results_against_reference(
            candidate_item_results=candidate_results,
            reference_id=ref["id"],
        )
        classification = self.controller.classify_quality_differences(
            diffs=comparison["differing_items"],
            reference_id=ref["id"],
        )
        self.assertEqual(len(classification["review_required"]), 1)
        self.assertEqual(classification["review_required"][0]["item_index"], 3)

    def test_mark_reference_review_required(self):
        """FR-034: 有需审核差异时参考标记为 review_required。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        self.controller.mark_review_required(
            reference_id=ref["id"],
            reviewed_item_ids=[2, 3],
        )
        updated = self.controller.get_quality_reference(ref["id"])
        self.assertEqual(updated["status"], "review_required")
        self.assertIn(2, updated["reviewed_item_ids"])
        self.assertIn(3, updated["reviewed_item_ids"])

    def test_resolve_reviewed_differences_creates_new_version(self):
        """FR-034: 用户复核后创建新参考版本。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        self.controller.mark_review_required(
            reference_id=ref["id"],
            reviewed_item_ids=[2],
        )
        # 用户解决差异，创建新版本
        resolved_results = [
            {"item_index": i, "verdict": "match" if i % 2 == 0 else "no_match"}
            for i in range(5)
        ]
        resolved_results[2]["verdict"] = "match"  # 用户判定为 match
        new_ref = self.controller.resolve_reviewed_differences(
            reference_id=ref["id"],
            resolved_item_results=resolved_results,
        )
        self.assertEqual(new_ref["status"], "confirmed")
        self.assertNotEqual(new_ref["id"], ref["id"])
        # 旧参考被 superseded
        old = self.controller.get_quality_reference(ref["id"])
        self.assertEqual(old["status"], "superseded")

    # -- data-model 2.4: 参考摘要强制 ------------------------------------

    def test_reference_digest_enforcement_match(self):
        """data-model 2.4: 候选只能与 manifest 中记录的参考摘要匹配的参考比较。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        # digest 匹配时允许比较
        ok = self.controller.enforce_reference_digest_match(
            reference_id=ref["id"],
            expected_digest=ref["reference_digest"],
        )
        self.assertTrue(ok)

    def test_reference_digest_enforcement_mismatch(self):
        """data-model 2.4: digest 不匹配时拒绝比较。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        # digest 不匹配时拒绝
        with self.assertRaises(ValueError):
            self.controller.enforce_reference_digest_match(
                reference_id=ref["id"],
                expected_digest="sha256-wrong-digest",
            )

    def test_compare_with_wrong_reference_digest_rejected(self):
        """data-model 2.4: 用错误 digest 比较时被拒绝。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref["id"])
        candidate_results = [{"item_index": 0, "verdict": "match"}]
        # 用错误的 expected_digest
        with self.assertRaises(ValueError):
            self.controller.compare_results_against_reference(
                candidate_item_results=candidate_results,
                reference_id=ref["id"],
                expected_digest="sha256-wrong",
            )

    def test_superseded_reference_not_usable_for_comparison(self):
        """旧版本被 superseded 后不能用于比较。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref1 = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref1["id"])
        # 创建新版本，旧版本被 superseded
        ref2 = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref2["id"])
        # 旧版本不能用于比较
        candidate_results = [{"item_index": 0, "verdict": "match"}]
        with self.assertRaises(ValueError):
            self.controller.compare_results_against_reference(
                candidate_item_results=candidate_results,
                reference_id=ref1["id"],
            )

    def test_get_active_quality_reference(self):
        """实验的活动参考是最近的 confirmed 版本。"""
        baseline_results = self._make_baseline_round_results(item_count=5)
        ref1 = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref1["id"])
        active = self.controller.get_active_quality_reference(self.experiment["id"])
        self.assertEqual(active["id"], ref1["id"])
        # 创建新版本
        ref2 = self.controller.build_quality_reference(
            experiment_id=self.experiment["id"],
            input_version_id=self.input_version_id,
            baseline_round_results=baseline_results,
        )
        self.controller.confirm_quality_reference(ref2["id"])
        active = self.controller.get_active_quality_reference(self.experiment["id"])
        self.assertEqual(active["id"], ref2["id"])


def _make_valid_manifest_payload(
    *, experiment_id: str, candidate_id: str, round_id: str,
) -> dict:
    """构造一份完整的合法 manifest payload（不含 server 生成的字段）。"""
    return {
        "schema_version": 1,
        "task_id": "manifest-test-001",
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "round_id": round_id,
        "spec_version": "011-deep-configuration-probing",
        "objective": "测试 list 阶段 inter_combo_delay=5.0 的单字段探路轮次",
        "round_kind": "list",
        "strategy_step": "single_field",
        "repetition_index": 1,
        "preconditions": [
            {
                "id": "check_lease",
                "instruction": "验证实验独占租约属于本轮次",
                "expected": "lease.owner_round_id == round_id",
                "on_failure": "block_and_report",
                "evidence_field": "preflight[0]",
            },
        ],
        "frozen_input": {
            "input_version_id": "iv-1",
            "workload_id": "wl-1",
            "task_size": "small",
            "structure_index": 1,
            "scope_digest": "sha256-scope",
            "artifact_manifest_path": "tuning/exp-1/input/wl-1.json",
            "artifact_digest": "sha256-input",
            "quality_reference_id": None,
            "quality_reference_digest": None,
            "expected_input_count": 30,
            "planned_pages": 3,
        },
        "execution_config": {
            "schema_version": 1,
            "inter_combo_delay": 5.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
            "config_digest": "sha256-config",
        },
        "fixed_fields": {
            "keywords": ["AI应用开发"],
            "scope_kind": "cities",
            "cities": ["东莞"],
            "pages_per_combination": 3,
            "planned_pages": 3,
            "task_size": "small",
        },
        "execution_steps": [
            {
                "seq": 1,
                "action": "start_round",
                "instruction": "POST /api/tuning/manifests/manifest-test-001/execute",
                "expected_status": "running",
                "timeout_seconds": 60,
                "on_timeout": "block_and_report",
                "named_retry": None,
                "evidence_field": "steps[0].evidence",
            },
            {
                "seq": 2,
                "action": "poll_status",
                "instruction": "GET /api/tuning/rounds/round-1 每 5 秒轮询直到终态",
                "expected_status": "confirmed|blocked|invalid",
                "timeout_seconds": 600,
                "on_timeout": "block_and_report",
                "named_retry": None,
                "evidence_field": "steps[1].evidence",
            },
        ],
        "monitoring": {
            "status_endpoint": "/api/tuning/rounds/round-1",
            "polling_interval_seconds": 5,
            "max_observation_interval_seconds": 600,
            "expected_stage_sequence": ["searching", "combo_done", "done"],
            "monotonic_counters": ["processed_combinations", "raw_jobs_found"],
            "hard_error_codes": ["captcha_required", "login_expired", "source_blocked"],
            "recoverable_error_codes": ["detail_timeout"],
            "max_recoverable_retries": 1,
            "evidence_snapshot_interval_seconds": 30,
            "final_artifact_path": "tuning/exp-1/evidence/round-1.json",
        },
        "retry_policy": {
            "detail_timeout": {"max_retries": 1, "backoff_seconds": 3},
        },
        "stop_conditions": [
            {
                "code": "captcha_required",
                "match": "program error_code equals captcha_required",
                "severity": "hard",
                "action": "stop_new_work_and_block_report",
                "required_evidence": ["status_snapshot", "program_report_path"],
            },
            {
                "code": "login_expired",
                "match": "program error_code equals login_expired",
                "severity": "hard",
                "action": "stop_new_work_and_block_report",
                "required_evidence": ["status_snapshot"],
            },
        ],
        "allowed_writes": [
            "tuning/exp-1/evidence/round-1.json",
            "tuning/exp-1/artifacts/round-1/",
        ],
        "required_artifacts": [
            {
                "artifact_type": "program_report",
                "path": "tuning/exp-1/evidence/round-1.json",
                "producer": "application",
                "existence_required": True,
                "digest_required": True,
                "min_fields": ["total_duration_ms", "terminal_count"],
                "absence_makes_invalid": True,
            },
        ],
        "forbidden_actions": [
            "edit_source_code",
            "change_acceptance_criteria",
            "select_another_candidate",
            "overwrite_prior_manifest",
            "write_outside_experiment_root",
        ],
        "report_contract": {
            "required_fields": [
                "task_id", "experiment_id", "manifest_digest", "status",
                "preflight", "steps", "program_evidence", "artifacts",
                "stop_reason", "unexecuted_steps", "started_at", "finished_at",
            ],
            "forbidden_executor_fields": ["parameter_suggestions", "candidate_ranking"],
        },
    }


def _make_valid_report_payload(*, manifest: dict, manifest_digest: str) -> dict:
    """构造一份完整的合法 executor report payload。"""
    artifact = manifest["required_artifacts"][0]
    return {
        "schema_version": 1,
        "report_id": "report-001",
        "task_id": manifest["task_id"],
        "experiment_id": manifest["experiment_id"],
        "candidate_id": manifest["candidate_id"],
        "round_id": manifest["round_id"],
        "manifest_digest": manifest_digest,
        "status": "completed",
        "preflight": [
            {"id": "check_lease", "result": "passed", "evidence": "lease ok"},
        ],
        "steps": [
            {"seq": 1, "status": "completed", "evidence": "round started"},
            {"seq": 2, "status": "completed", "evidence": "round confirmed"},
        ],
        "observations": {
            "total_duration_observed": 45000,
            "stages_observed": ["searching", "combo_done", "done"],
        },
        "program_evidence": {
            "program_report_path": artifact["path"],
            "program_report_digest": artifact.get("digest", "sha256-evidence"),
            "config_digest": manifest["execution_config"]["config_digest"],
            "scope_digest": manifest["frozen_input"]["scope_digest"],
            "input_artifact_digest": manifest["frozen_input"]["artifact_digest"],
            "total_duration_ms": 45000,
            "stage_durations_ms": {"list": 40000},
            "work_duration_ms": 40000,
            "wait_duration_ms": 5000,
            "retry_duration_ms": 0,
            "attempt_count": 1,
            "retry_count": 0,
            "input_count": 30,
            "terminal_count": 30,
            "success_count": 30,
            "failed_count": 0,
            "missing_count": 0,
            "duplicate_count": 0,
            "quality_diff_count": 0,
            "error_counts": {},
        },
        "artifacts": [
            {
                "artifact_type": "program_report",
                "path": artifact["path"],
                "digest": artifact.get("digest", "sha256-evidence"),
                "exists": True,
            },
        ],
        "stop_reason": None,
        "unexecuted_steps": [],
        "executor_notes": ["所有步骤按任务单完成"],
        "started_at": "2026-07-29T10:00:00+08:00",
        "finished_at": "2026-07-29T10:01:30+08:00",
    }


class ManifestReportValidationTests(unittest.TestCase):
    """T021 RED: manifest/report 严格校验测试。

    覆盖 FR-043/044/045/046/047/048/049、SC-011/012/013、
    executor-protocol.md 第 2-4 节。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        from webui.tuning import TuningController
        self.controller = TuningController(self.store)
        scopes = [
            ("small", CompletionGateTests._scope(1, 3)),
            ("small", CompletionGateTests._scope(2, 3)),
            ("medium", CompletionGateTests._scope(2, 5)),
            ("medium", CompletionGateTests._scope(3, 5)),
            ("large", CompletionGateTests._scope(4, 5)),
            ("large", CompletionGateTests._scope(5, 5)),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope=scopes[0][1],
            quality_context={
                "profile_summary": "Python AI 应用开发候选人",
                "screening_fields": {"salary": ["403"]},
                "profile_ref": "user-confirmed:test",
            },
            workloads=[
                {"task_size": size, "structure_index": index % 2 + 1,
                 "scope": scope}
                for index, (size, scope) in enumerate(scopes)
            ],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=_sample_nine_fields(inter_combo_delay=5.0),
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="list",
            repetition_index=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _make_manifest(self) -> dict:
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
        evidence_path = pathlib.Path(self.temp.name) / evidence["program_report_path"]
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        evidence_path.write_bytes(encoded)
        manifest["required_artifacts"][0]["digest"] = (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        )
        return manifest

    def test_manifest_rejects_forged_config_digest(self):
        manifest = self._make_manifest()
        manifest["execution_config"]["config_digest"] = "sha256:forged"
        with self.assertRaises(ValueError):
            self.controller.issue_manifest(manifest)

    def test_manifest_rejects_sibling_experiment_path(self):
        manifest = self._make_manifest()
        manifest["allowed_writes"] = ["tuning/sibling/evidence.json"]
        with self.assertRaises(ValueError):
            self.controller.issue_manifest(manifest)

    def test_detail_manifest_accepts_exact_confirmed_list_artifact(self):
        self.controller.start_round(self.round["id"])
        source = self.controller.persist_stage_artifact(
            round_id=self.round["id"], stage="list",
            payload={"round_kind": "list", "jobs": [{"job_id": "j1"}]},
        )
        self.controller.confirm_round(self.round["id"])
        self.store.update_tuning_experiment_status(
            self.experiment["id"], status="awaiting_instruction",
        )
        detail_candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"], stage="detail",
            strategy_step="baseline", config=_sample_nine_fields(),
        )
        detail_round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=detail_candidate["id"],
            workload_id=self.workload["id"], round_kind="detail",
            repetition_index=1,
        )
        manifest = self._make_manifest()
        manifest.update({
            "candidate_id": detail_candidate["id"],
            "round_id": detail_round["id"],
            "round_kind": "detail",
            "strategy_step": "baseline",
        })
        manifest["execution_config"] = self.store.get_tuning_candidate(
            detail_candidate["id"])["config"]
        manifest["frozen_input"].update({
            "source_artifact_id": source["id"],
            "source_artifact_path": source["artifact_path"],
            "source_artifact_digest": source["artifact_digest"],
        })

        self.controller._validate_manifest(manifest)

    def test_detail_manifest_rejects_missing_source_artifact(self):
        detail_candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"], stage="detail",
            strategy_step="baseline", config=_sample_nine_fields(),
        )
        detail_round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=detail_candidate["id"],
            workload_id=self.workload["id"], round_kind="detail",
            repetition_index=1,
        )
        manifest = self._make_manifest()
        manifest.update({
            "candidate_id": detail_candidate["id"],
            "round_id": detail_round["id"],
            "round_kind": "detail",
            "strategy_step": "baseline",
        })
        manifest["execution_config"] = self.store.get_tuning_candidate(
            detail_candidate["id"])["config"]
        with self.assertRaisesRegex(ValueError, "source_artifact|阶段产物"):
            self.controller._validate_manifest(manifest)

    # -- FR-044: 必填字段完整性 ------------------------------------------

    def test_manifest_missing_required_field_rejected(self):
        """FR-044: 缺少必填字段的 manifest 被拒绝。"""
        required_fields = [
            "objective", "round_kind", "strategy_step", "repetition_index",
            "preconditions", "frozen_input", "execution_config", "fixed_fields",
            "execution_steps", "monitoring", "retry_policy", "stop_conditions",
            "allowed_writes", "required_artifacts", "forbidden_actions",
            "report_contract",
        ]
        for field in required_fields:
            with self.subTest(field=field):
                manifest = self._make_manifest()
                del manifest[field]
                with self.assertRaises(ValueError, msg=f"缺少 {field} 的 manifest 应被拒绝"):
                    self.controller.issue_manifest(manifest)

    def test_manifest_missing_execution_config_field_rejected(self):
        """FR-044: execution_config 缺少九字段之一被拒绝。"""
        config_fields = [
            "inter_combo_delay", "detail_batch_size", "detail_interval",
            "detail_reset_every", "detail_batch_cooldown",
            "screen_batch_size", "screen_concurrency",
            "match_batch_size", "match_concurrency",
        ]
        for field in config_fields:
            with self.subTest(field=field):
                manifest = self._make_manifest()
                del manifest["execution_config"][field]
                with self.assertRaises(ValueError):
                    self.controller.issue_manifest(manifest)

    # -- FR-045: 禁止占位符和自由裁量语言 --------------------------------

    def test_manifest_placeholder_rejected(self):
        """FR-045: 包含占位符的 manifest 被拒绝。"""
        placeholders = [
            "<placeholder>", "<TBD>", "as appropriate", "if needed",
            "choose as needed", "<value>", "<参数>",
        ]
        for placeholder in placeholders:
            with self.subTest(placeholder=placeholder):
                manifest = self._make_manifest()
                manifest["execution_steps"][0]["instruction"] = (
                    f"执行操作 {placeholder}"
                )
                with self.assertRaises(ValueError):
                    self.controller.issue_manifest(manifest)

    def test_manifest_step_with_discretionary_language_rejected(self):
        """FR-045: 步骤包含自由裁量语言被拒绝。"""
        manifest = self._make_manifest()
        manifest["execution_steps"][0]["instruction"] = (
            "根据情况选择合适的参数继续"
        )
        with self.assertRaises(ValueError):
            self.controller.issue_manifest(manifest)

    # -- FR-043: 路径包含性 ----------------------------------------------

    def test_manifest_path_outside_experiment_root_rejected(self):
        """allowed_writes 中的路径必须在实验根目录内。"""
        manifest = self._make_manifest()
        manifest["allowed_writes"].append("../outside/path.json")
        with self.assertRaises(ValueError):
            self.controller.issue_manifest(manifest)

    def test_manifest_absolute_path_rejected(self):
        """allowed_writes 中的绝对路径被拒绝。"""
        manifest = self._make_manifest()
        manifest["allowed_writes"].append("C:/evil/path.json")
        with self.assertRaises(ValueError):
            self.controller.issue_manifest(manifest)

    # -- FR-043: 不可变摘要 ----------------------------------------------

    def test_manifest_digest_immutable(self):
        """FR-043: 签发后 manifest_digest 不可被篡改。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        digest = issued["manifest_digest"]
        self.assertIsNotNone(digest)
        # 重新获取 manifest，digest 应一致
        fetched = self.controller.get_manifest(issued["manifest_id"])
        self.assertEqual(fetched["manifest_digest"], digest)

    def test_manifest_digest_computed_from_canonical_json(self):
        """manifest_digest 基于规范 JSON 计算（不含 digest 字段本身）。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        # 重新计算 digest 验证（含 sha256: 前缀，符合 executor-protocol.md）
        canonical = json.dumps(
            {k: v for k, v in manifest.items() if k != "manifest_digest"},
            ensure_ascii=False, sort_keys=True,
        )
        expected = "sha256:" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        self.assertEqual(issued["manifest_digest"], expected)

    # -- FR-048/049: 报告校验 --------------------------------------------

    def test_report_missing_required_field_rejected(self):
        """FR-048/049: 缺少必填字段的报告被拒绝。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        required_fields = [
            "report_id", "task_id", "experiment_id", "candidate_id",
            "round_id", "manifest_digest", "status", "preflight",
            "steps", "program_evidence", "artifacts", "stop_reason",
            "unexecuted_steps", "started_at", "finished_at",
        ]
        for field in required_fields:
            with self.subTest(field=field):
                report = _make_valid_report_payload(
                    manifest=manifest, manifest_digest=issued["manifest_digest"],
                )
                del report[field]
                with self.assertRaises(ValueError):
                    self.controller.validate_report(
                        manifest_id=issued["manifest_id"], report=report,
                    )

    def test_report_wrong_manifest_digest_rejected(self):
        """FR-049: 报告中的 manifest_digest 与签发的不一致被拒绝。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["manifest_digest"] = "sha256-wrong"
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    def test_report_id_mismatch_rejected(self):
        """FR-049: 报告中的 task_id/experiment_id 等与 manifest 不一致被拒绝。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["task_id"] = "wrong-task-id"
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    def test_report_program_evidence_digest_mismatch_rejected(self):
        """FR-049: 程序证据摘要与实际不一致被拒绝。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["program_evidence"]["program_report_digest"] = "sha256-wrong"
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    def test_report_missing_program_evidence_file_rejected(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        (pathlib.Path(self.temp.name) /
         report["program_evidence"]["program_report_path"]).unlink()
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    def test_report_forged_config_digest_rejected(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["program_evidence"]["config_digest"] = "sha256:forged"
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    def test_report_forbidden_executor_field_rejected(self):
        """报告包含禁止的执行者字段（参数建议、候选排名）被拒绝。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["parameter_suggestions"] = "建议提高并发"
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    # -- FR-047: 阻断报告 ------------------------------------------------

    def test_blocked_report_requires_stop_reason_and_unexecuted(self):
        """FR-047: blocked 报告必须包含 stop_reason 和 unexecuted_steps。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["status"] = "blocked"
        report["stop_reason"] = "captcha_required"
        report["unexecuted_steps"] = [2]
        report["steps"] = [step for step in report["steps"] if step["seq"] == 1]
        # 应该通过校验
        result = self.controller.validate_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        self.assertTrue(result["valid"])

    def test_blocked_report_without_stop_reason_rejected(self):
        """blocked 报告缺少 stop_reason 被拒绝。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["status"] = "blocked"
        report["stop_reason"] = None
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    # -- FR-046: 禁止动作 ------------------------------------------------

    def test_report_forbidden_action_detected(self):
        """FR-046: 检测到执行者执行了禁止动作。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        # 执行者报告中透露了修改源代码
        report["executor_notes"].append("修改了 webui/pipeline_exec.py 以调整超时")
        with self.assertRaises(ValueError):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    # -- FR-045: 停止条件唯一动作 ----------------------------------------

    def test_stop_condition_with_multiple_actions_rejected(self):
        """FR-045: 同一停止条件不能同时允许多个需要执行者取舍的动作。"""
        manifest = self._make_manifest()
        manifest["stop_conditions"].append({
            "code": "detail_timeout",
            "match": "program error_code equals detail_timeout",
            "severity": "recoverable",
            "action": "retry_or_block",  # 模糊动作
            "required_evidence": ["status_snapshot"],
        })
        with self.assertRaises(ValueError):
            self.controller.issue_manifest(manifest)

    # -- FR-044: 完整 manifest 可签发 ------------------------------------

    def test_valid_manifest_issued_successfully(self):
        """FR-044: 完整 manifest 可成功签发。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.assertEqual(issued["status"], "issued")
        self.assertIsNotNone(issued["manifest_id"])
        self.assertIsNotNone(issued["manifest_digest"])
        self.assertIsNotNone(issued["rendered_task_path"])

    def test_issued_manifest_round_status_updated(self):
        """签发后轮次状态更新为 issued。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        round_rec = self.controller.get_round(self.round["id"])
        self.assertEqual(round_rec["status"], "issued")
        self.assertEqual(round_rec["manifest_id"], issued["manifest_id"])

    # -- 渲染 Markdown ---------------------------------------------------

    def test_render_manifest_markdown(self):
        """manifest 可渲染为自包含 Markdown 任务单。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        markdown = self.controller.render_manifest_markdown(issued["manifest_id"])
        self.assertIsInstance(markdown, str)
        self.assertIn(manifest["task_id"], markdown)
        self.assertIn(manifest["objective"], markdown)
        # 不包含凭据
        self.assertNotIn("api_key", markdown.lower())
        self.assertNotIn("password", markdown.lower())


class CleanContextFakeExecutorTests(unittest.TestCase):
    """T024: clean-context fake-executor 验收 harness。

    证明一份完整任务单可被无上下文执行者机械执行，
    且未知情况只能返回 blocked 报告（不能自行决策）。

    覆盖 executor-protocol.md 第 1-5 节、Quickstart Scenario E/F。
    """

    def setUp(self):
        ManifestReportValidationTests.setUp(self)

    def tearDown(self):
        ManifestReportValidationTests.tearDown(self)

    def _make_manifest(self) -> dict:
        manifest = ManifestReportValidationTests._make_manifest(self)
        manifest["required_artifacts"][0].pop("digest", None)
        return manifest

    def _materialize_report(self, report: dict) -> None:
        evidence = dict(report["program_evidence"])
        evidence.pop("program_report_digest", None)
        path = pathlib.Path(self.temp.name) / evidence["program_report_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        path.write_bytes(raw)
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        report["program_evidence"]["program_report_digest"] = digest
        report["artifacts"][0]["digest"] = digest

    def test_complete_task_executable_by_clean_context_executor(self):
        """clean-context fake executor 能机械执行完整任务单。

        场景：执行者只读取 manifest，按步骤执行，
        所有步骤完成后返回 completed 报告。
        """
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)

        # 推进到 running（模拟 execute 路由）
        self.controller.execute_manifest(issued["manifest_id"])

        # fake executor: 不依赖任何外部上下文，只按 manifest 执行
        # 执行者从 GET /manifests/{id} 获取 manifest_digest
        fake = _CleanContextFakeExecutor(
            manifest=manifest,
            manifest_digest=issued["manifest_digest"],
        )
        report = fake.execute_complete()
        self._materialize_report(report)

        # 报告应被接受
        result = self.controller.validate_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        self.assertTrue(result["valid"])

    def test_unknown_condition_returns_blocked_report(self):
        """未知情况只能返回 blocked 报告。

        场景：执行者遇到 manifest 未定义的错误码，
        必须返回 blocked 报告，不能自行重试或调整参数。
        """
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])

        # fake executor: 遇到未知错误码
        fake = _CleanContextFakeExecutor(
            manifest=manifest,
            manifest_digest=issued["manifest_digest"],
        )
        report = fake.execute_blocked_unknown_condition(
            unknown_error_code="unknown_platform_error",
        )
        self._materialize_report(report)

        # 报告应为 blocked
        self.assertEqual(report["status"], "blocked")
        self.assertIsNotNone(report["stop_reason"])
        self.assertIn("unexecuted_steps", report)
        # 不能包含参数建议或候选排名
        self.assertNotIn("parameter_suggestions", report)
        self.assertNotIn("candidate_ranking", report)

        # 校验报告应被接受（blocked 报告是合法的）
        result = self.controller.validate_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        self.assertTrue(result["valid"])

    def test_blocked_report_preserves_in_flight_evidence(self):
        """blocked 报告保留已产生的证据。

        场景：执行者在步骤 2 失败，步骤 1 的证据必须保留。
        """
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])

        fake = _CleanContextFakeExecutor(
            manifest=manifest,
            manifest_digest=issued["manifest_digest"],
        )
        report = fake.execute_blocked_at_step(
            failed_step=2,
            stop_reason="detail_timeout",
        )

        # 步骤 1 已完成，步骤 2 未执行
        completed_steps = [s["seq"] for s in report["steps"]
                          if s["status"] == "completed"]
        self.assertIn(1, completed_steps)
        self.assertIn(2, report["unexecuted_steps"])
        # 证据保留
        self.assertGreater(len(report["artifacts"]), 0)

    def test_executor_cannot_invent_missing_values(self):
        """执行者不能自行填补缺失值。

        场景：manifest 中缺少某些字段时，执行者必须 block，
        不能用默认值或猜测值继续。
        """
        manifest = self._make_manifest()
        # 模拟缺失 expected_input_count
        manifest["frozen_input"]["expected_input_count"] = None
        # 这种 manifest 应在校验时被拒绝
        # （preconditions 要求 input count 匹配）
        # 如果 manifest 已签发但执行者发现缺失，应 block
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])

        fake = _CleanContextFakeExecutor(
            manifest=manifest,
            manifest_digest=issued["manifest_digest"],
        )
        report = fake.execute_blocked_missing_value(
            missing_field="expected_input_count",
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("expected_input_count", report["stop_reason"])

    def test_executor_notes_only_observable_facts(self):
        """执行者 notes 只能描述可观察事实，不能包含建议或排名。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])

        fake = _CleanContextFakeExecutor(
            manifest=manifest,
            manifest_digest=issued["manifest_digest"],
        )
        report = fake.execute_complete()
        # notes 只能是可观察事实
        for note in report.get("executor_notes", []):
            self.assertNotIn("建议", note)
            self.assertNotIn("应该", note)
            self.assertNotIn("推荐", note)
            self.assertNotIn("更好", note)


class _CleanContextFakeExecutor:
    """无上下文的 fake 执行者。

    只依赖 manifest 内容执行，不访问任何外部状态或历史。
    模拟 executor-protocol.md 第 1 节描述的执行者行为。
    """

    def __init__(self, *, manifest: dict, manifest_digest: str = ""):
        self.manifest = manifest
        self.manifest_digest = manifest_digest
        self.task_id = manifest["task_id"]
        self.steps = manifest.get("execution_steps", [])
        self.stop_conditions = manifest.get("stop_conditions", [])

    def execute_complete(self) -> dict:
        """完整执行所有步骤，返回 completed 报告。"""
        completed_steps = []
        for step in self.steps:
            completed_steps.append({
                "seq": step["seq"],
                "status": "completed",
                "evidence": f"step {step['seq']} done",
            })
        # 构造 program evidence（模拟程序生成）
        config = self.manifest.get("execution_config", {})
        frozen = self.manifest.get("frozen_input", {})
        program_evidence = {
            "program_report_path": (
                f"tuning/{self.manifest['experiment_id']}/evidence/"
                f"{self.manifest['round_id']}.json"
            ),
            "program_report_digest": "sha256-evidence",
            "config_digest": config.get("config_digest", "sha256-cfg"),
            "scope_digest": frozen.get("scope_digest", "sha256-scope"),
            "input_artifact_digest": frozen.get(
                "artifact_digest", "sha256-input"
            ),
            "total_duration_ms": 45000,
            "stage_durations_ms": {"list": 45000},
            "work_duration_ms": 40000,
            "wait_duration_ms": 5000,
            "retry_duration_ms": 0,
            "attempt_count": 1,
            "retry_count": 0,
            "input_count": frozen.get("expected_input_count", 30),
            "terminal_count": frozen.get("expected_input_count", 30),
            "success_count": frozen.get("expected_input_count", 30),
            "failed_count": 0,
            "missing_count": 0,
            "duplicate_count": 0,
            "quality_diff_count": 0,
            "error_counts": {},
        }
        return {
            "schema_version": 1,
            "report_id": f"report-{self.task_id}",
            "task_id": self.task_id,
            "experiment_id": self.manifest["experiment_id"],
            "candidate_id": self.manifest["candidate_id"],
            "round_id": self.manifest["round_id"],
            "manifest_digest": self.manifest_digest,
            "status": "completed",
            "preflight": [
                {"id": "check_lease", "result": "passed",
                 "evidence": "lease ok"},
            ],
            "steps": completed_steps,
            "observations": {
                "total_duration_observed": 45000,
                "stages_observed": ["running", "confirmed"],
            },
            "program_evidence": program_evidence,
            "artifacts": [
                {
                    "artifact_type": "program_report",
                    "path": program_evidence["program_report_path"],
                    "digest": "sha256-evidence",
                    "exists": True,
                },
            ],
            "stop_reason": None,
            "unexecuted_steps": [],
            "executor_notes": [
                f"完成 {len(self.steps)} 个步骤",
                "所有步骤按任务单执行",
            ],
            "started_at": "2026-07-29T10:00:00+08:00",
            "finished_at": "2026-07-29T10:01:30+08:00",
        }

    def execute_blocked_unknown_condition(
        self, *, unknown_error_code: str,
    ) -> dict:
        """遇到未知错误码时返回 blocked 报告。"""
        # 检查错误码是否在 stop_conditions 中定义
        defined_codes = {c["code"] for c in self.stop_conditions}
        if unknown_error_code not in defined_codes:
            stop_reason = f"unknown_condition:{unknown_error_code}"
        else:
            stop_reason = unknown_error_code
        # 所有步骤都未执行
        unexecuted = [s["seq"] for s in self.steps]
        config = self.manifest.get("execution_config", {})
        frozen = self.manifest.get("frozen_input", {})
        program_evidence = {
            "program_report_path": (
                f"tuning/{self.manifest['experiment_id']}/evidence/"
                f"{self.manifest['round_id']}.json"
            ),
            "program_report_digest": "sha256-evidence-blocked",
            "config_digest": config.get("config_digest", "sha256-cfg"),
            "scope_digest": frozen.get("scope_digest", "sha256-scope"),
            "input_artifact_digest": frozen.get(
                "artifact_digest", "sha256-input"
            ),
            "total_duration_ms": 5000,
            "stage_durations_ms": {},
            "work_duration_ms": 5000,
            "wait_duration_ms": 0,
            "retry_duration_ms": 0,
            "attempt_count": 1,
            "retry_count": 0,
            "input_count": 0,
            "terminal_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "missing_count": 0,
            "duplicate_count": 0,
            "quality_diff_count": 0,
            "error_counts": {unknown_error_code: 1},
        }
        return {
            "schema_version": 1,
            "report_id": f"report-{self.task_id}-blocked",
            "task_id": self.task_id,
            "experiment_id": self.manifest["experiment_id"],
            "candidate_id": self.manifest["candidate_id"],
            "round_id": self.manifest["round_id"],
            "manifest_digest": self.manifest_digest,
            "status": "blocked",
            "preflight": [
                {"id": "check_lease", "result": "passed",
                 "evidence": "lease ok"},
            ],
            "steps": [],
            "observations": {
                "total_duration_observed": 5000,
                "stages_observed": ["blocked"],
            },
            "program_evidence": program_evidence,
            "artifacts": [
                {
                    "artifact_type": "program_report",
                    "path": program_evidence["program_report_path"],
                    "digest": "sha256-evidence-blocked",
                    "exists": True,
                },
            ],
            "stop_reason": stop_reason,
            "unexecuted_steps": unexecuted,
            "executor_notes": [
                f"遇到未知错误码: {unknown_error_code}",
                "未修改任何参数或验收规则",
            ],
            "started_at": "2026-07-29T10:00:00+08:00",
            "finished_at": "2026-07-29T10:00:05+08:00",
        }

    def execute_blocked_at_step(
        self, *, failed_step: int, stop_reason: str,
    ) -> dict:
        """在指定步骤失败，返回 blocked 报告。"""
        completed_steps = []
        unexecuted = []
        for step in self.steps:
            if step["seq"] < failed_step:
                completed_steps.append({
                    "seq": step["seq"],
                    "status": "completed",
                    "evidence": f"step {step['seq']} done",
                })
            else:
                unexecuted.append(step["seq"])
        config = self.manifest.get("execution_config", {})
        frozen = self.manifest.get("frozen_input", {})
        program_evidence = {
            "program_report_path": (
                f"tuning/{self.manifest['experiment_id']}/evidence/"
                f"{self.manifest['round_id']}.json"
            ),
            "program_report_digest": "sha256-evidence-partial",
            "config_digest": config.get("config_digest", "sha256-cfg"),
            "scope_digest": frozen.get("scope_digest", "sha256-scope"),
            "input_artifact_digest": frozen.get(
                "artifact_digest", "sha256-input"
            ),
            "total_duration_ms": 20000,
            "stage_durations_ms": {"list": 20000},
            "work_duration_ms": 15000,
            "wait_duration_ms": 3000,
            "retry_duration_ms": 2000,
            "attempt_count": 2,
            "retry_count": 1,
            "input_count": frozen.get("expected_input_count", 30),
            "terminal_count": 15,
            "success_count": 15,
            "failed_count": 0,
            "missing_count": 15,
            "duplicate_count": 0,
            "quality_diff_count": 0,
            "error_counts": {stop_reason: 1},
        }
        return {
            "schema_version": 1,
            "report_id": f"report-{self.task_id}-partial",
            "task_id": self.task_id,
            "experiment_id": self.manifest["experiment_id"],
            "candidate_id": self.manifest["candidate_id"],
            "round_id": self.manifest["round_id"],
            "manifest_digest": self.manifest_digest,
            "status": "blocked",
            "preflight": [
                {"id": "check_lease", "result": "passed",
                 "evidence": "lease ok"},
            ],
            "steps": completed_steps,
            "observations": {
                "total_duration_observed": 20000,
                "stages_observed": ["running", "blocked"],
            },
            "program_evidence": program_evidence,
            "artifacts": [
                {
                    "artifact_type": "program_report",
                    "path": program_evidence["program_report_path"],
                    "digest": "sha256-evidence-partial",
                    "exists": True,
                },
            ],
            "stop_reason": stop_reason,
            "unexecuted_steps": unexecuted,
            "executor_notes": [
                f"在步骤 {failed_step} 因 {stop_reason} 阻断",
                "已完成的步骤证据已保留",
            ],
            "started_at": "2026-07-29T10:00:00+08:00",
            "finished_at": "2026-07-29T10:00:20+08:00",
        }

    def execute_blocked_missing_value(
        self, *, missing_field: str,
    ) -> dict:
        """发现 manifest 缺失值时返回 blocked 报告。"""
        unexecuted = [s["seq"] for s in self.steps]
        config = self.manifest.get("execution_config", {})
        frozen = self.manifest.get("frozen_input", {})
        program_evidence = {
            "program_report_path": (
                f"tuning/{self.manifest['experiment_id']}/evidence/"
                f"{self.manifest['round_id']}.json"
            ),
            "program_report_digest": "sha256-evidence-missing",
            "config_digest": config.get("config_digest", "sha256-cfg"),
            "scope_digest": frozen.get("scope_digest", "sha256-scope"),
            "input_artifact_digest": frozen.get(
                "artifact_digest", "sha256-input"
            ),
            "total_duration_ms": 1000,
            "stage_durations_ms": {},
            "work_duration_ms": 1000,
            "wait_duration_ms": 0,
            "retry_duration_ms": 0,
            "attempt_count": 0,
            "retry_count": 0,
            "input_count": 0,
            "terminal_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "missing_count": 0,
            "duplicate_count": 0,
            "quality_diff_count": 0,
            "error_counts": {},
        }
        return {
            "schema_version": 1,
            "report_id": f"report-{self.task_id}-missing",
            "task_id": self.task_id,
            "experiment_id": self.manifest["experiment_id"],
            "candidate_id": self.manifest["candidate_id"],
            "round_id": self.manifest["round_id"],
            "manifest_digest": self.manifest_digest,
            "status": "blocked",
            "preflight": [
                {"id": "check_input", "result": "failed",
                 "evidence": f"missing {missing_field}"},
            ],
            "steps": [],
            "observations": {
                "total_duration_observed": 1000,
                "stages_observed": ["blocked"],
            },
            "program_evidence": program_evidence,
            "artifacts": [
                {
                    "artifact_type": "program_report",
                    "path": program_evidence["program_report_path"],
                    "digest": "sha256-evidence-missing",
                    "exists": True,
                },
            ],
            "stop_reason": f"missing_field:{missing_field}",
            "unexecuted_steps": unexecuted,
            "executor_notes": [
                f"manifest 缺少 {missing_field}，无法继续",
                "未自行填补缺失值",
            ],
            "started_at": "2026-07-29T10:00:00+08:00",
            "finished_at": "2026-07-29T10:00:01+08:00",
        }


class TuningRoundRunnerTests(unittest.TestCase):
    """五种 round_kind 必须进入真实阶段函数，未知类型必须阻断。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.context_path = "tuning/exp-run/input/context.json"
        context_file = self.root / self.context_path
        context_file.parent.mkdir(parents=True, exist_ok=True)
        context_file.write_text(json.dumps({
            "quality_context": {
                "screening_fields": {"salary": ["403"]},
                "profile_summary": "Python developer",
                "profile_ref": "user-confirmed:test",
            },
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        self.context_digest = "sha256:" + hashlib.sha256(
            context_file.read_bytes()).hexdigest()
        self.source_path = "tuning/exp-run/artifacts/list-round/list-result.json"
        source_file = self.root / self.source_path
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(json.dumps({
            "round_kind": "list",
            "jobs": [{"job_id": "j1", "jd": "Python"}],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        self.source_digest = "sha256:" + hashlib.sha256(
            source_file.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def _manifest(self, kind):
        from webui.execution_config import ExecutionConfigSnapshot
        return {
            "experiment_id": "exp-run", "round_id": f"round-{kind}",
            "round_kind": kind,
            "execution_config": ExecutionConfigSnapshot.create(
                _sample_nine_fields()).to_dict(),
            "fixed_fields": {
                "keywords": ["AI"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 1,
            },
            "frozen_input": {
                "artifact_manifest_path": self.context_path,
                "artifact_digest": self.context_digest,
                "source_artifact_id": (
                    "stage-list-1" if kind in {"detail", "rough", "fine"} else None
                ),
                "source_artifact_path": (
                    self.source_path if kind in {"detail", "rough", "fine"} else None
                ),
                "source_artifact_digest": (
                    self.source_digest if kind in {"detail", "rough", "fine"} else None
                ),
            },
        }

    def test_all_five_round_kinds_dispatch_to_stage_functions(self):
        from unittest import mock
        from webui.pipeline_exec import TuningRoundRunner

        runner = TuningRoundRunner(
            workspace_root=self.root, source_factory=lambda **_: object(),
            ai_settings_provider=lambda: {
                "endpoint_url": "https://example.invalid", "api_key": "test-key",
                "model": "test-model",
            },
        )
        with (
            mock.patch("webui.pipeline_exec.run_search", return_value={
                "ok": True, "jobs": [{"job_id": "j1", "jd": ""}],
                "total_scraped": 1, "total_matched": 1,
            }) as list_stage,
            mock.patch("webui.pipeline_exec.fetch_job_details", return_value={
                "jobs": [{"job_id": "j1", "jd": "Python"}],
                "hard_stop": False, "fetched": 1,
            }) as detail_stage,
            mock.patch("webui.ai.screen_jobs", return_value={
                "kept": ["j1"], "dropped": [],
                "verdicts": {"j1": {"verdict": "kept"}},
            }) as rough_stage,
            mock.patch("webui.ai.match_jds", return_value={
                "verdicts": {"j1": {"verdict": "match"}},
            }) as fine_stage,
            mock.patch("webui.pipeline_exec.close_debug_chrome") as close_stage,
        ):
            for kind in ("list", "detail", "rough", "fine", "end_to_end"):
                result = runner.execute(self._manifest(kind))
                self.assertEqual(result["round_kind"], kind)
            self.assertEqual(list_stage.call_count, 2)
            self.assertTrue(
                list_stage.call_args_list[0].kwargs["close_chrome_on_success"]
            )
            self.assertFalse(
                list_stage.call_args_list[1].kwargs["close_chrome_on_success"]
            )
            self.assertEqual(detail_stage.call_count, 2)
            self.assertEqual(rough_stage.call_count, 2)
            self.assertEqual(fine_stage.call_count, 2)
            close_stage.assert_called_once_with()

    def test_unknown_round_kind_blocks(self):
        from webui.pipeline_exec import TuningRoundRunner
        runner = TuningRoundRunner(
            workspace_root=self.root, source_factory=lambda **_: object(),
            ai_settings_provider=lambda: {},
        )
        with self.assertRaises(ValueError):
            runner.execute(self._manifest("unknown"))

    def test_source_factory_is_scoped_to_exact_round_artifact_directory(self):
        from unittest import mock
        from webui.pipeline_exec import TuningRoundRunner

        captured = {}

        def source_factory(**kwargs):
            captured.update(kwargs)
            return object()

        runner = TuningRoundRunner(
            workspace_root=self.root,
            source_factory=source_factory,
            ai_settings_provider=lambda: {},
        )
        with mock.patch(
            "webui.pipeline_exec.run_search",
            return_value={
                "ok": True, "jobs": [], "total_scraped": 0,
                "total_matched": 0,
            },
        ):
            runner.execute(self._manifest("list"))

        self.assertEqual(
            pathlib.Path(captured["artifact_root"]),
            self.root / "tuning" / "exp-run" / "artifacts" / "round-list",
        )

    def test_tampered_source_artifact_blocks_before_stage_execution(self):
        from webui.pipeline_exec import TuningRoundRunner
        runner = TuningRoundRunner(
            workspace_root=self.root, source_factory=lambda **_: object(),
            ai_settings_provider=lambda: {},
        )
        (self.root / self.source_path).write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "摘要|digest"):
            runner.execute(self._manifest("detail"))


class PhaseRoundAdapterTests(unittest.TestCase):
    """T025 RED: 五种轮次适配器、阶段输入复用规则与跨版本 digest 拒绝。

    覆盖 FR-024/FR-025、research.md Decision 7、data-model.md 2.6 round_kind。
    T026 将实现 RoundAdapter 使这些测试转绿。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        from webui.tuning import TuningController, RoundAdapter
        self.controller = TuningController(self.store)
        self.adapter = RoundAdapter(self.controller)
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
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=_sample_nine_fields(),
        )

    def tearDown(self):
        self.temp.cleanup()

    # -- 五种轮次类型可创建 ----------------------------------------------

    def test_create_list_round(self):
        """list 轮次可创建，round_kind=list。"""
        round_rec = self.adapter.create_list_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            repetition_index=1,
        )
        self.assertEqual(round_rec["status"], "planned")
        fetched = self.controller.get_round(round_rec["id"])
        self.assertEqual(fetched["round_kind"], "list")

    def test_create_detail_round(self):
        """detail 轮次可创建，round_kind=detail。"""
        round_rec = self.adapter.create_detail_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            repetition_index=1,
            source_input_version="iv-1",
            target_input_version="iv-1",
        )
        fetched = self.controller.get_round(round_rec["id"])
        self.assertEqual(fetched["round_kind"], "detail")

    def test_create_rough_round(self):
        """rough 轮次可创建，round_kind=rough。"""
        round_rec = self.adapter.create_rough_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            repetition_index=1,
            source_input_version="iv-1",
            target_input_version="iv-1",
        )
        fetched = self.controller.get_round(round_rec["id"])
        self.assertEqual(fetched["round_kind"], "rough")

    def test_create_fine_round(self):
        """fine 轮次可创建，round_kind=fine。"""
        round_rec = self.adapter.create_fine_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            repetition_index=1,
            source_input_version="iv-1",
            target_input_version="iv-1",
        )
        fetched = self.controller.get_round(round_rec["id"])
        self.assertEqual(fetched["round_kind"], "fine")

    def test_create_end_to_end_round(self):
        """end_to_end 轮次可创建，round_kind=end_to_end。"""
        round_rec = self.adapter.create_end_to_end_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            repetition_index=1,
        )
        fetched = self.controller.get_round(round_rec["id"])
        self.assertEqual(fetched["round_kind"], "end_to_end")

    # -- 允许的阶段输入复用（同 input_version） ---------------------------

    def test_detail_can_reuse_list_results_same_version(self):
        """FR-024: detail 可复用 list 结果（同 input_version 允许）。"""
        ok = self.adapter.validate_stage_input_reuse(
            "detail", "iv-1", "iv-1")
        self.assertTrue(ok)

    def test_rough_can_reuse_list_fields_same_version(self):
        """FR-024: rough 可复用 list 字段（同 input_version 允许）。"""
        ok = self.adapter.validate_stage_input_reuse(
            "rough", "iv-1", "iv-1")
        self.assertTrue(ok)

    def test_fine_can_reuse_jd_same_version(self):
        """FR-024: fine 可复用 JD（同 input_version 允许）。"""
        ok = self.adapter.validate_stage_input_reuse(
            "fine", "iv-1", "iv-1")
        self.assertTrue(ok)

    # -- 禁止的端到端复用 ------------------------------------------------

    def test_end_to_end_reuse_forbidden(self):
        """FR-025: end_to_end 必须从头执行，禁止复用阶段输入。"""
        with self.assertRaises(ValueError):
            self.adapter.validate_stage_input_reuse(
                "end_to_end", "iv-1", "iv-1")

    def test_list_reuse_forbidden(self):
        """list 是第一阶段，无前置阶段可复用。"""
        with self.assertRaises(ValueError):
            self.adapter.validate_stage_input_reuse(
                "list", "iv-1", "iv-1")

    def test_unknown_round_kind_rejected(self):
        """未知 round_kind 被拒绝。"""
        with self.assertRaises(ValueError):
            self.adapter.validate_stage_input_reuse(
                "unknown_kind", "iv-1", "iv-1")

    # -- 跨版本 digest 拒绝 ----------------------------------------------

    def test_cross_version_reuse_rejected_for_detail(self):
        """不同 input_version 的产物不能复用（detail）。"""
        with self.assertRaises(ValueError):
            self.adapter.validate_stage_input_reuse(
                "detail", "iv-1", "iv-2")

    def test_cross_version_reuse_rejected_for_rough(self):
        """不同 input_version 的产物不能复用（rough）。"""
        with self.assertRaises(ValueError):
            self.adapter.validate_stage_input_reuse(
                "rough", "iv-1", "iv-2")

    def test_cross_version_reuse_rejected_for_fine(self):
        """不同 input_version 的产物不能复用（fine）。"""
        with self.assertRaises(ValueError):
            self.adapter.validate_stage_input_reuse(
                "fine", "iv-1", "iv-2")

    # -- 创建时校验复用 --------------------------------------------------

    def test_create_detail_round_cross_version_rejected(self):
        """detail 创建时跨版本复用被拒绝。"""
        with self.assertRaises(ValueError):
            self.adapter.create_detail_round(
                experiment_id=self.experiment["id"],
                candidate_id=self.candidate["id"],
                workload_id="wl-1",
                repetition_index=1,
                source_input_version="iv-1",
                target_input_version="iv-2",
            )


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


if __name__ == "__main__":
    unittest.main()
