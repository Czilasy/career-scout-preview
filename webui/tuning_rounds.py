"""调优轮次管理、阶段产物证明与分阶段轮次适配器（021 B7 自 tuning.py 搬运）。"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from webui.tuning_digest import _SHA256_PREFIX

if TYPE_CHECKING:
    from webui.tuning import TuningController


class TuningRoundsMixin:
    """轮次创建/确认、旧 BOSS 证明纯校验器、stage kind 与产物继承守卫。"""

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

    # -- T614: 外层与 JSON 一致性校验（在 source/AI 前阻断） ---------------

    def validate_consistency_before_execution(
        self, *, manifest_id: str,
    ) -> dict:
        """T614: 校验 experiment/workload/artifact/manifest 外层与 JSON 一致性。

        在 source 或 AI 执行前调用：
        - manifest 外层 platform 与 manifest JSON fixed_fields.platform 一致
        - manifest 外层 experiment_id 与 manifest JSON experiment_id 一致
        - experiment 外层 platform 与 manifest 外层 platform 一致
        - 若 manifest 有 source_artifact_id，其平台与实验平台一致

        任一项错配时抛 ValueError 阻断，不在 source 或 AI 后报错。
        """
        record = self._store.get_task_manifest(manifest_id)
        manifest = record["manifest"]
        outer_platform = record.get("platform")
        json_platform = (
            manifest.get("fixed_fields", {}).get("platform")
        )
        if outer_platform and json_platform and outer_platform != json_platform:
            raise ValueError(
                f"manifest 外层平台 {outer_platform!r} 与 JSON 平台 "
                f"{json_platform!r} 不一致"
            )
        outer_experiment_id = record.get("experiment_id")
        json_experiment_id = manifest.get("experiment_id")
        if (
            outer_experiment_id and json_experiment_id
            and outer_experiment_id != json_experiment_id
        ):
            raise ValueError(
                f"manifest 外层 experiment_id {outer_experiment_id!r} 与 JSON "
                f"{json_experiment_id!r} 不一致"
            )
        # 校验 experiment 外层 platform 与 manifest 一致
        experiment = self._store.get_tuning_experiment(
            outer_experiment_id or json_experiment_id or "",
        )
        exp_platform = experiment.get("platform")
        manifest_platform = outer_platform or json_platform
        if exp_platform and manifest_platform and exp_platform != manifest_platform:
            raise ValueError(
                f"实验外层平台 {exp_platform!r} 与 manifest 平台 "
                f"{manifest_platform!r} 不一致"
            )
        source_artifact_id = manifest.get("frozen_input", {}).get(
            "source_artifact_id"
        )
        if source_artifact_id:
            try:
                source = self._store.get_tuning_stage_artifact(
                    source_artifact_id
                )
            except KeyError:
                raise ValueError(
                    f"manifest 引用的上游产物 {source_artifact_id} 不存在"
                )
            source_platform = source.get("platform")
            if source_platform and exp_platform and source_platform != exp_platform:
                raise ValueError(
                    f"上游产物平台 {source_platform!r} 与实验平台 "
                    f"{exp_platform!r} 不一致"
                )
        return {
            "manifest_id": manifest_id,
            "outer_platform": outer_platform,
            "json_platform": json_platform,
            "experiment_platform": exp_platform,
            "consistent": True,
        }

    # -- T608: 旧 BOSS manifest/artifact 客观证明纯校验器 -----------------

    _LEGACY_PROOFABLE_STAGES = frozenset({"list", "detail"})
    _LEGACY_BOSS_PLATFORM = "boss"

    def prove_legacy_boss_manifest(
        self, *, manifest_record: dict, migration_cutoff: str,
    ) -> dict:
        """T608: 客观证明旧 manifest 为迁移前 BOSS。

        纯校验器：不修改 manifest_json 或 manifest_digest，不查询 migration 27
        才有的外层 platform 列。证据不足抛 ValueError，由调用方阻断。

        见 data-model.md 第 263 行：存量已签发 manifest 不修改 JSON 和摘要；
        外层列仅将可证明为迁移前记录的条目回填 boss。
        """
        issued_at = manifest_record.get("issued_at")
        if not issued_at or issued_at >= migration_cutoff:
            raise ValueError(
                f"manifest issued_at={issued_at!r} 不早于 migration cutoff"
                f"={migration_cutoff!r}"
            )
        manifest = manifest_record["manifest"]
        fixed_platform = manifest.get("fixed_fields", {}).get("platform")
        frozen_platform = manifest.get("frozen_input", {}).get("platform")
        if fixed_platform == "zhilian" or frozen_platform == "zhilian":
            raise ValueError(
                "manifest JSON 显式声明 platform=zhilian，不能证明为 BOSS"
            )
        canonical = json.dumps(
            {k: v for k, v in manifest.items() if k != "manifest_digest"},
            ensure_ascii=False, sort_keys=True,
        )
        recomputed = _SHA256_PREFIX + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        if recomputed != manifest_record["manifest_digest"]:
            raise ValueError(
                "manifest_digest 与重算值不一致，无法客观证明"
            )
        experiment = self._store.get_tuning_experiment(
            manifest_record["experiment_id"]
        )
        if experiment["created_at"] >= migration_cutoff:
            raise ValueError(
                f"experiment created_at={experiment['created_at']!r}"
                f" 不早于 migration cutoff"
            )
        return {
            "platform": self._LEGACY_BOSS_PLATFORM,
            "proof_kind": "legacy_manifest",
            "manifest_id": manifest_record["id"],
            "issued_at": issued_at,
            "migration_cutoff": migration_cutoff,
            "digest_verified": True,
            "provenance": [
                f"experiment:{experiment['id']}@{experiment['created_at']}",
                f"manifest:{manifest_record['id']}@{issued_at}",
            ],
        }

    def prove_legacy_boss_artifact(
        self, *, artifact_record: dict, migration_cutoff: str,
    ) -> dict:
        """T608: 客观证明旧 stage artifact 为迁移前 BOSS。

        纯校验器：不修改 artifact JSON 或 digest。仅 stage=list/detail 可证明。
        证据不足抛 ValueError，由调用方阻断。

        见 data-model.md 第 281 行：仅当记录创建时间早于 migration 27、
        所属 experiment/input version/workload/manifest 均可证明为迁移前 BOSS、
        原摘要有效，且 stage 为 list/detail 时，才可解释为 BOSS source artifact。
        """
        created_at = artifact_record.get("created_at")
        if not created_at or created_at >= migration_cutoff:
            raise ValueError(
                f"artifact created_at={created_at!r} 不早于 migration cutoff"
                f"={migration_cutoff!r}"
            )
        stage = artifact_record["stage"]
        if stage not in self._LEGACY_PROOFABLE_STAGES:
            raise ValueError(
                f"artifact stage={stage!r} 不可证明（仅 list/detail 允许）"
            )
        stored_digest = artifact_record.get("artifact_digest")
        if not stored_digest:
            raise ValueError("artifact 缺少 artifact_digest，不猜填")
        artifact_path = artifact_record["artifact_path"]
        absolute = (self._workspace_root / artifact_path).resolve()
        experiment_root = (
            self._workspace_root
            / "tuning"
            / artifact_record["experiment_id"]
        ).resolve()
        if experiment_root not in absolute.parents:
            raise ValueError("artifact 路径越过实验根目录")
        if not absolute.is_file():
            raise ValueError(f"artifact 文件不存在: {artifact_path}")
        recomputed = _SHA256_PREFIX + hashlib.sha256(
            absolute.read_bytes()
        ).hexdigest()
        if recomputed != stored_digest:
            raise ValueError(
                "artifact_digest 与重算值不一致，无法客观证明"
            )
        experiment = self._store.get_tuning_experiment(
            artifact_record["experiment_id"]
        )
        if experiment["created_at"] >= migration_cutoff:
            raise ValueError(
                f"experiment created_at={experiment['created_at']!r}"
                f" 不早于 migration cutoff"
            )
        round_record = self._store.get_tuning_round(
            artifact_record["producer_round_id"]
        )
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT iv.created_at AS iv_created_at "
                "FROM tuning_workloads w "
                "JOIN tuning_input_versions iv ON iv.id = w.input_version_id "
                "WHERE w.id = ?",
                (round_record["workload_id"],),
            ).fetchone()
        if row is None:
            raise ValueError("artifact 所属 workload/input_version 不存在")
        if row["iv_created_at"] >= migration_cutoff:
            raise ValueError(
                f"input_version created_at={row['iv_created_at']!r}"
                f" 不早于 migration cutoff"
            )
        provenance = [
            f"experiment:{experiment['id']}@{experiment['created_at']}",
            f"input_version@{row['iv_created_at']}",
            f"round:{round_record['id']}",
            f"artifact:{artifact_record['id']}@{created_at}",
        ]
        manifest_id = round_record.get("manifest_id")
        if manifest_id:
            manifest_record = self._store.get_task_manifest(manifest_id)
            self.prove_legacy_boss_manifest(
                manifest_record=manifest_record,
                migration_cutoff=migration_cutoff,
            )
            provenance.append(
                f"manifest:{manifest_id}@{manifest_record['issued_at']}"
            )
        return {
            "platform": self._LEGACY_BOSS_PLATFORM,
            "proof_kind": "legacy_artifact",
            "artifact_id": artifact_record["id"],
            "stage": stage,
            "created_at": created_at,
            "migration_cutoff": migration_cutoff,
            "digest_verified": True,
            "provenance": provenance,
        }

    # -- T609: stage kind 与 source_artifact_kind 固定枚举守卫 ---------------

    ALLOWED_STAGE_KINDS = frozenset({
        "list", "detail", "rough", "fine", "end_to_end",
    })
    REUSABLE_SOURCE_ARTIFACT_KINDS = frozenset({"list", "detail"})
    _STAGE_TO_SOURCE_ARTIFACT_KIND = {
        "list": "list", "detail": "detail",
        "rough": None, "fine": None, "end_to_end": None,
    }

    def validate_stage_kind(self, stage: str) -> None:
        """T609: 校验 stage 仅为 list/detail/rough/fine/end_to_end。

        见 data-model.md 第 273 行。未知 stage 抛 ValueError，由调用方阻断。
        """
        if stage not in self.ALLOWED_STAGE_KINDS:
            raise ValueError(
                f"未知 stage: {stage!r}，仅允许 "
                f"{sorted(self.ALLOWED_STAGE_KINDS)}"
            )

    def source_artifact_kind_for_stage(
        self, stage: str,
    ) -> str | None:
        """T609: 返回 stage 对应的 source_artifact_kind。

        见 data-model.md 第 274 行：
        - stage=list → 'list'（可作为 rough 的 source 输入）
        - stage=detail → 'detail'（可作为 fine 的 source 输入）
        - rough/fine/end_to_end → None（不可被复用）
        """
        self.validate_stage_kind(stage)
        return self._STAGE_TO_SOURCE_ARTIFACT_KIND[stage]

    def validate_reusable_source_artifact_kind(self, kind: str) -> None:
        """T609: 校验 source_artifact_kind 仅为 list/detail。

        见 data-model.md 第 274、279 行：
        只有 list/detail 产物可作为 rough/fine 的 source 输入；
        end_to_end 输出 source_artifact_kind=NULL，不得被 rough/fine 复用。
        """
        if kind not in self.REUSABLE_SOURCE_ARTIFACT_KINDS:
            raise ValueError(
                f"不可复用的 source_artifact_kind: {kind!r}，仅允许 "
                f"{sorted(self.REUSABLE_SOURCE_ARTIFACT_KINDS)}"
            )

    # -- T612: rough/fine source artifact 继承校验 ------------------------

    def validate_rough_source_artifact(
        self, *, source_artifact_id: str,
    ) -> dict:
        """T612: rough 只接受 list artifact 作为 source。

        纯校验器：不创建 JobSource，不修改 artifact。
        见 data-model.md 第 279 行：rough 只接受 source_artifact_kind=list。
        """
        record = self._store.get_tuning_stage_artifact(source_artifact_id)
        if record["stage"] != "list":
            raise ValueError(
                f"rough 只接受 list artifact 作为 source，"
                f"实际 stage={record['stage']!r}"
            )
        return record

    def validate_fine_source_artifact(
        self, *, source_artifact_id: str,
    ) -> dict:
        """T612: fine 只接受 detail artifact 作为 source。

        纯校验器：不创建 JobSource，不修改 artifact。
        见 data-model.md 第 279 行：fine 只接受 source_artifact_kind=detail。
        """
        record = self._store.get_tuning_stage_artifact(source_artifact_id)
        if record["stage"] != "detail":
            raise ValueError(
                f"fine 只接受 detail artifact 作为 source，"
                f"实际 stage={record['stage']!r}"
            )
        return record

    def prove_source_artifact_platform_inheritance(
        self, *, source_artifact_id: str,
    ) -> dict:
        """T612: 证明 source artifact 的平台继承自 experiment。

        纯校验器：不查询 migration 27 外层 platform 列（尚未实现）。
        证据链：artifact → round → experiment → source_scope.platform。

        由于 store 不暴露 platform 外层列，当前从 experiment.source_scope.platform
        推断。T606/T607 实现后，应改为从 experiment/platform 外层列读取。
        见 data-model.md 第 271 行：stage artifact 与 experiment 平台一致。
        """
        artifact = self._store.get_tuning_stage_artifact(source_artifact_id)
        experiment = self._store.get_tuning_experiment(
            artifact["experiment_id"]
        )
        source_scope = experiment.get("source_scope", {})
        platform = source_scope.get("platform")
        if not platform:
            raise ValueError(
                f"experiment {experiment['id']} source_scope 缺少 platform，"
                f"无法证明 artifact 平台继承"
            )
        return {
            "inferred_platform": platform,
            "evidence_source": "experiment_source_scope",
            "experiment_id": experiment["id"],
            "artifact_id": artifact["id"],
            "artifact_stage": artifact["stage"],
        }

    # -- T615: 禁用平台守卫与取消登录空间 --------------------------------

    def validate_platform_enabled_for_new_source_round(
        self, *, platform: str,
    ) -> None:
        """T615: 校验平台是否允许签发新 source round。

        见 data-model.md 第 22 行：enabled_for_new_tasks=false 时
        只禁用新任务创建/补抓，不影响历史读取。
        见 tasks007.md T615：禁用平台不签发或执行新的 source round。
        """
        from webui.platforms import (
            get_platform_or_none,
            is_known_platform_key,
        )
        if not is_known_platform_key(platform):
            raise ValueError(
                f"未知平台: {platform!r}，不能签发新 source round"
            )
        registry = get_platform_or_none(platform)
        if registry is None:
            raise ValueError(
                f"平台 {platform!r} 已知但未注册，不能签发新 source round"
            )
        if not registry.enabled_for_new_tasks:
            raise ValueError(
                f"平台 {platform!r} 当前禁用新任务"
                f"（{registry.availability_reason}）"
            )

    def cancel_experiment_login_spaces(
        self, *, experiment_id: str,
    ) -> dict:
        """T615: 取消实验时只处理已知平台的登录空间。

        见 tasks007.md T615：取消只处理已知平台登录空间。
        从 experiment.source_scope.platform 读取平台，
        只返回已知平台的登录空间列表，未知平台不处理。
        """
        experiment = self._store.get_tuning_experiment(experiment_id)
        source_scope = experiment.get("source_scope", {})
        platform = source_scope.get("platform")
        handled_platforms: list[str] = []
        if platform:
            from webui.platforms import is_known_platform_key
            if is_known_platform_key(platform):
                handled_platforms.append(platform)
        return {
            "experiment_id": experiment_id,
            "handled_platforms": handled_platforms,
            "platform_from_source_scope": platform,
        }

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
        with self._store._connection() as conn:
            conn.execute(
                "UPDATE tuning_rounds SET metrics_json = ? WHERE id = ?",
                (metrics_json, round_id),
            )


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

    def __init__(self, controller: TuningController):
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
