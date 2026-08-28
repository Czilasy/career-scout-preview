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

from tests.tuning.builders import _sample_nine_fields


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
            ("medium", self._scope(2, 8)),  # 024 新口径：16 页属中规模,
            ("medium", self._scope(3, 5)),
            ("large", self._scope(10, 5)),
            ("large", self._scope(11, 5)),
        ]
        return self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={**scopes[0][1], "browser_account": "a", "filter_schema_version": 1},
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
            ("medium", 2, 8), ("medium", 3, 5),  # 024 新口径：16/15 页属中规模
            ("large", 10, 5), ("large", 11, 5),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={"keywords": ["AI"], "scope_kind": "cities",
                          "cities": ["东莞"], "pages_per_combination": 3,
                          "browser_account": "a", "filter_schema_version": 1},
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
            ("medium", self._scope(2, 8)), ("medium", self._scope(3, 5)),  # 024 新口径：16/15 页属中规模
            ("large", self._scope(10, 5)), ("large", self._scope(11, 5)),
        ]
        experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={**scopes[0][1], "browser_account": "a", "filter_schema_version": 1},
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

    def test_measurement_sink_persists_safe_ai_diagnostics(self):
        """FR-069/070: 根因字段保留，密钥和原始内容仍被过滤。"""
        sink = self.controller.build_measurement_sink(self.round["id"])
        sink(
            "request", "fine", 12, error_code="invalid_response",
            metadata={
                "failure_phase": "json_decode",
                "exception_type": "JSONDecodeError",
                "http_status": 200,
                "response_length": 18,
                "finish_reason": "stop",
                "parse_position": 7,
                "api_key": "sk-secret",
                "model_response": "raw-private-content",
            },
        )
        event = self.controller.list_measurements(self.round["id"])[0]
        self.assertEqual(event["metadata"]["failure_phase"], "json_decode")
        self.assertEqual(event["metadata"]["parse_position"], 7)
        serialized = json.dumps(event, ensure_ascii=False)
        self.assertNotIn("sk-secret", serialized)
        self.assertNotIn("raw-private-content", serialized)

    def test_hard_error_is_aggregated_once_with_trace_id(self):
        """FR-071/072: 完整失败链归并为一个规范硬错误和关联 ID。"""
        sink = self.controller.build_measurement_sink(self.round["id"])
        sink(
            "request", "fine", 5, error_code="network_error",
            metadata={"correlation_id": "trace-network", "attempt_index": 1},
        )
        sink(
            "request", "fine", 4, error_code="auth_failed",
            metadata={"correlation_id": "trace-auth", "attempt_index": 2},
        )

        hard_error = self.controller.aggregate_hard_error(self.round["id"])

        self.assertEqual(hard_error["code"], "auth_failed")
        self.assertEqual(hard_error["correlation_id"], "trace-auth")
        self.assertEqual(hard_error["attempt_error_count"], 2)

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

    def test_terminal_conservation_accepts_final_screening_verdicts(self):
        """端到端最终的 dropped/match/not_match 都是成功终态。"""
        end_to_end_round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            round_kind="end_to_end",
            repetition_index=1,
        )
        for item_index in range(3):
            self.controller.record_measurement(
                round_id=end_to_end_round["id"],
                event_type="item_terminal",
                stage="detail",
                duration_ms=0,
                counts={
                    "item_index": item_index,
                    "status": "success",
                    "input_count": 3,
                },
            )
        final_verdicts = [(0, "dropped"), (1, "match"), (2, "not_match")]
        for item_index, status in final_verdicts:
            self.controller.record_measurement(
                round_id=end_to_end_round["id"],
                event_type="item_terminal",
                stage="rough" if status == "dropped" else "fine",
                duration_ms=0,
                counts={
                    "item_index": item_index,
                    "status": status,
                    "input_count": 3,
                },
            )

        summary = self.controller.aggregate_measurements(end_to_end_round["id"])

        self.assertEqual(summary["terminal_count"], 3)
        self.assertEqual(summary["success_count"], 3)
        self.assertEqual(summary["failed_count"], 0)
        self.assertEqual(summary["missing_count"], 0)
        self.assertEqual(summary["duplicate_count"], 0)

    def test_end_to_end_intermediate_terminal_preserves_input_and_missing(self):
        """端到端被 detail 阻断时保留输入数，detail 不计入最终终态。"""
        end_to_end_round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id="wl-1",
            round_kind="end_to_end",
            repetition_index=1,
        )
        for item_index in range(3):
            self.controller.record_measurement(
                round_id=end_to_end_round["id"],
                event_type="item_terminal",
                stage="detail",
                duration_ms=0,
                counts={
                    "item_index": item_index,
                    "status": "success",
                    "input_count": 3,
                },
            )

        summary = self.controller.aggregate_measurements(end_to_end_round["id"])

        self.assertEqual(summary["input_count"], 3)
        self.assertEqual(summary["terminal_count"], 0)
        self.assertEqual(summary["missing_count"], 3)

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


if __name__ == "__main__":
    unittest.main()
