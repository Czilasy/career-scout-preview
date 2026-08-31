"""深度调优任务单与评估报告合同测试（027 自 tests/test_tuning.py 拆出）。"""

from __future__ import annotations
import hashlib
import json
import pathlib
import tempfile
import unittest
from webui.store import TaskStore

from tests.tuning.builders import _scope, _sample_nine_fields, _expected_path_digest, _make_valid_manifest_payload, _make_valid_report_payload, _CleanContextFakeExecutor


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
            ("small", _scope(1, 3)),
            ("small", _scope(2, 3)),
            ("medium", _scope(2, 8)),  # 024 新口径：16 页属中规模,
            ("medium", _scope(3, 5)),
            ("large", _scope(10, 5)),
            ("large", _scope(11, 5)),
        ]
        self.experiment = self.controller.create_experiment_with_input(
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

    def _make_canonical_artifact_manifest(self) -> tuple[dict, pathlib.Path, str]:
        manifest = self._make_manifest()
        program_artifact = manifest["required_artifacts"][0]
        program_artifact.pop("existence_required", None)
        program_artifact.pop("digest_required", None)
        program_artifact["existence_requirement"] = "required"
        program_artifact["digest_requirement"] = "sha256"

        stage_relative_path = (
            f"tuning/{self.experiment['id']}/artifacts/{self.round['id']}/"
        )
        stage_path = self.controller._workspace_root / stage_relative_path
        stage_path.mkdir(parents=True, exist_ok=True)
        (stage_path / "stage.json").write_text(
            '{"round_kind":"list"}', encoding="utf-8",
        )
        manifest["required_artifacts"].append({
            "artifact_type": "stage_result",
            "path": stage_relative_path,
            "producer": "application",
            "existence_requirement": "required",
            "digest_requirement": "sha256",
            "minimum_fields": ["round_kind"],
            "absence_makes": "invalid",
        })
        return manifest, stage_path, stage_relative_path

    def test_canonical_required_file_and_directory_digests_are_verified(self):
        """canonical required artifacts accept actual file and directory digests."""
        manifest, stage_path, stage_relative_path = (
            self._make_canonical_artifact_manifest()
        )
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["artifacts"].append({
            "artifact_type": "stage_result",
            "path": stage_relative_path,
            "digest": _expected_path_digest(stage_path),
            "exists": True,
        })

        result = self.controller.validate_report(
            manifest_id=issued["manifest_id"], report=report,
        )

        self.assertTrue(result["valid"])

    def test_canonical_required_artifacts_reject_nonmatching_digests(self):
        """canonical required files and directories reject forged digests."""
        manifest, stage_path, stage_relative_path = (
            self._make_canonical_artifact_manifest()
        )
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["artifacts"].append({
            "artifact_type": "stage_result",
            "path": stage_relative_path,
            "digest": "sha256:directory-artifact",
            "exists": True,
        })

        with self.assertRaisesRegex(ValueError, "摘要"):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["artifacts"].append({
            "artifact_type": "stage_result",
            "path": stage_relative_path,
            "digest": _expected_path_digest(stage_path),
            "exists": True,
        })
        report["artifacts"][0]["digest"] = "sha256:forged-file"

        with self.assertRaisesRegex(ValueError, "摘要"):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

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

    def test_manifest_invalid_retry_policy_rejected(self):
        """FR-021: retry_policy 与默认策略同构，非法结构在签发时被拒绝。"""
        invalid_policies = [
            {"network_error": {"max_retries": -1}},
            {"network_error": {"max_retries": "x"}},
            {"network_error": {"max_retries": 1, "backoff_seconds": []}},
            {"network_error": "retry twice"},
        ]
        for policy in invalid_policies:
            with self.subTest(policy=policy):
                manifest = self._make_manifest()
                manifest["retry_policy"] = policy
                with self.assertRaisesRegex(ValueError, "retry_policy"):
                    self.controller.issue_manifest(manifest)

    def test_manifest_valid_retry_policy_accepted(self):
        """FR-021: 合法 retry_policy 可签发。"""
        manifest = self._make_manifest()
        manifest["retry_policy"] = {
            "network_error": {
                "max_retries": 2, "backoff_seconds": [1, 2],
            },
        }
        issued = self.controller.issue_manifest(manifest)
        self.assertTrue(issued["manifest_id"])

    def test_manifest_missing_execution_config_field_rejected(self):
        """FR-044: execution_config 缺少速度字段之一被拒绝。"""
        config_fields = [
            "inter_combo_delay", "detail_batch_size", "detail_interval",
            "detail_reset_every", "detail_batch_cooldown",
            "detail_tab_pool_size",
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

    def test_completed_zero_input_report_is_rejected(self):
        """SC-021: completed 轮次不能用 0 输入/0 终态伪装成守恒。"""
        manifest = self._make_manifest()
        path = pathlib.Path(self.temp.name) / manifest["required_artifacts"][0]["path"]
        persisted = json.loads(path.read_text(encoding="utf-8"))
        for key in ("input_count", "terminal_count", "success_count"):
            persisted[key] = 0
        encoded = json.dumps(
            persisted, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        path.write_bytes(encoded)
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        manifest["required_artifacts"][0]["digest"] = digest
        issued = self.controller.issue_manifest(manifest)
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["program_evidence"] = {**persisted, "program_report_digest": digest}
        report["artifacts"][0]["digest"] = digest
        with self.assertRaisesRegex(ValueError, "input_count 必须大于 0"):
            self.controller.validate_report(
                manifest_id=issued["manifest_id"], report=report,
            )

    def test_zero_input_rejected_report_transitions_round_to_invalid(self):
        """AXIS-4: accept_report 拒绝 zero-input completed 报告后，
        round 必须进入 invalid（非停留 reported），experiment 进入 blocked。
        状态机契约：reported → invalid（contract/evidence mismatch）。
        """
        manifest = self._make_manifest()
        path = pathlib.Path(self.temp.name) / manifest["required_artifacts"][0]["path"]
        persisted = json.loads(path.read_text(encoding="utf-8"))
        for key in ("input_count", "terminal_count", "success_count"):
            persisted[key] = 0
        encoded = json.dumps(
            persisted, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        path.write_bytes(encoded)
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        manifest["required_artifacts"][0]["digest"] = digest
        issued = self.controller.issue_manifest(manifest)
        # 推进到 running（模拟执行器启动）
        self.controller.execute_manifest(issued["manifest_id"])
        round_before = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_before["status"], "running")
        # 构造 zero-input completed 报告
        report = _make_valid_report_payload(
            manifest=manifest, manifest_digest=issued["manifest_digest"],
        )
        report["program_evidence"] = {**persisted, "program_report_digest": digest}
        report["artifacts"][0]["digest"] = digest
        # accept_report 应抛 ValueError 且原子更新状态
        with self.assertRaisesRegex(ValueError, "input_count 必须大于 0"):
            self.controller.accept_report(
                manifest_id=issued["manifest_id"], report=report,
            )
        # 验证 round → invalid
        round_after = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_after["status"], "invalid")
        self.assertEqual(round_after["failure_code"], "report_validation_failed")
        # 验证 experiment → blocked
        exp_after = self.store.get_tuning_experiment(self.experiment["id"])
        self.assertEqual(exp_after["status"], "blocked")
        self.assertEqual(exp_after["blocked_code"], "report_validation_failed")
        # 验证租约已释放
        lease = self.store.get_tuning_lease()
        self.assertIsNone(lease["owner_round_id"])

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
                "platform": "boss", "keywords": ["AI"], "scope_kind": "cities",
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

    def test_list_stage_failure_preserves_safe_hard_stop_code(self):
        from unittest import mock
        from webui.pipeline_exec import TuningRoundRunner

        runner = TuningRoundRunner(
            workspace_root=self.root, source_factory=lambda **_: object(),
            ai_settings_provider=lambda: {},
        )
        with mock.patch("webui.pipeline_exec.run_search", return_value={
            "ok": False,
            "hard_stop_code": "source_cdp_unavailable",
            "error": "系统性阻断：调试浏览器不可用",
        }):
            with self.assertRaisesRegex(RuntimeError, "调试浏览器") as raised:
                runner.execute(self._manifest("list"))

        self.assertEqual(raised.exception.error_code, "source_cdp_unavailable")

    def test_manifest_retry_policy_is_passed_to_tuning_ai_stages(self):
        from unittest import mock
        from webui.pipeline_exec import TuningRoundRunner

        runner = TuningRoundRunner(
            workspace_root=self.root, source_factory=lambda **_: object(),
            ai_settings_provider=lambda: {
                "endpoint_url": "https://example.invalid",
                "api_key": "test-key",
                "model": "test-model",
            },
        )
        with mock.patch("webui.ai.screen_jobs", return_value={
            "kept": ["j1"], "dropped": [],
            "verdicts": {"j1": {"verdict": "kept"}},
        }) as rough_stage, mock.patch(
            "webui.ai.match_jds",
            return_value={"verdicts": {"j1": {"verdict": "match"}}},
        ) as fine_stage:
            for kind, stage in (("rough", rough_stage), ("fine", fine_stage)):
                manifest = self._manifest(kind)
                manifest["retry_policy"] = {
                    "network_error": {
                        "max_retries": 2,
                        "backoff_seconds": [1, 2],
                    },
                }
                runner.execute(manifest)
                self.assertEqual(
                    dict(stage.call_args.kwargs["retry_limits"]),
                    {"network_error": 2},
                )

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
            pathlib.Path(captured["artifact_root"]).resolve(),
            (self.root / "tuning" / "exp-run" / "artifacts" / "round-list").resolve(),
        )
        self.assertEqual(captured.get("platform"), "boss")

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


class ConsistencyValidationBeforeExecutionTests(unittest.TestCase):
    """T614: 外层与 JSON 一致性校验阻断。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _create_experiment(self, platform="boss"):
        from webui.tuning import TuningController
        controller = TuningController(self.store)
        return controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
                "platform": platform,
                "browser_account": "a",
                "cdp_port": 9222,
                "profile_key": "boss:a",
                "filter_schema_version": 1,
            },
            workloads=[
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
            quality_context={
                "screening_fields": {"salary": ["403"]},
                "profile_summary": "Python developer",
                "profile_ref": "user-confirmed:test",
            },
        )

    def test_consistent_manifest_passes(self):
        """manifest 外层与 JSON 一致时通过。"""
        from webui.tuning import TuningController
        controller = TuningController(self.store)
        experiment = self._create_experiment("boss")
        bundle = self.store.get_tuning_input_bundle(experiment["id"])
        workload = bundle["workloads"][0]
        self.store.update_tuning_experiment_status(
            experiment["id"], status="preflight",
        )
        self.store.update_tuning_experiment_status(
            experiment["id"], status="awaiting_instruction",
        )
        candidate = controller.add_candidate(
            experiment_id=experiment["id"],
            stage="list", strategy_step="single_field",
            config=_sample_nine_fields(),
        )
        round_rec = controller.create_round(
            experiment_id=experiment["id"],
            candidate_id=candidate["id"],
            workload_id=workload["id"],
            round_kind="list", repetition_index=1,
        )
        manifest = {
            "experiment_id": experiment["id"],
            "round_id": round_rec["id"],
            "round_kind": "list",
            "fixed_fields": {
                "platform": "boss",
                "keywords": ["AI"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "frozen_input": {},
            "execution_config": {},
        }
        manifest_json = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True,
        )
        manifest_digest = "sha256:" + hashlib.sha256(
            manifest_json.encode("utf-8")).hexdigest()
        issued = self.store.issue_task_manifest_atomic(
            experiment_id=experiment["id"],
            candidate_id=candidate["id"],
            round_id=round_rec["id"],
            manifest_version=1,
            manifest_json=manifest_json,
            manifest_digest=manifest_digest,
            rendered_task_path="tasks/tuning/exp-round.json",
            owner_token="test-token",
        )
        result = controller.validate_consistency_before_execution(
            manifest_id=issued["manifest_id"],
        )
        self.assertTrue(result["consistent"])

    def test_platform_mismatch_blocks(self):
        """manifest 外层 platform 与 JSON 不一致时阻断。"""
        from webui.tuning import TuningController
        controller = TuningController(self.store)
        experiment = self._create_experiment("boss")
        bundle = self.store.get_tuning_input_bundle(experiment["id"])
        workload = bundle["workloads"][0]
        self.store.update_tuning_experiment_status(
            experiment["id"], status="preflight",
        )
        self.store.update_tuning_experiment_status(
            experiment["id"], status="awaiting_instruction",
        )
        candidate = controller.add_candidate(
            experiment_id=experiment["id"],
            stage="list", strategy_step="single_field",
            config=_sample_nine_fields(),
        )
        round_rec = controller.create_round(
            experiment_id=experiment["id"],
            candidate_id=candidate["id"],
            workload_id=workload["id"],
            round_kind="list", repetition_index=1,
        )
        manifest = {
            "experiment_id": experiment["id"],
            "round_id": round_rec["id"],
            "round_kind": "list",
            "fixed_fields": {
                "platform": "zhilian",
                "keywords": ["AI"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "frozen_input": {},
            "execution_config": {},
        }
        manifest_json = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True,
        )
        manifest_digest = "sha256:" + hashlib.sha256(
            manifest_json.encode("utf-8")).hexdigest()
        issued = self.store.issue_task_manifest_atomic(
            experiment_id=experiment["id"],
            candidate_id=candidate["id"],
            round_id=round_rec["id"],
            manifest_version=1,
            manifest_json=manifest_json,
            manifest_digest=manifest_digest,
            rendered_task_path="tasks/tuning/exp-round.json",
            owner_token="test-token",
        )
        with self.assertRaisesRegex(ValueError, "平台.*不一致"):
            controller.validate_consistency_before_execution(
                manifest_id=issued["manifest_id"],
            )


if __name__ == "__main__":
    unittest.main()
