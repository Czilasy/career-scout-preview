"""调优实验域（021 B2 拆分自 webui/store.py）：实验创建、输入 bundle、
确认、状态机流转与完成度检查。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from webui.store_helpers import (
    _decode_json,
    _now,
    _uuid,
)
from webui.store_constants import (
    _SHA256_PREFIX,
)


class StoreTuningExperimentsMixin:
    # -- SPEC011 tuning experiment persistence ---------------------------

    # Experiment state machine legal transitions (state-machine.md section 1)
    _EXPERIMENT_TERMINAL_STATES = frozenset({"cancelled", "failed", "completed"})
    _EXPERIMENT_LEGAL_TRANSITIONS = {
        "draft": {"preflight", "cancelled"},
        "preflight": {"awaiting_instruction", "blocked", "cancelled"},
        "awaiting_instruction": {"queued", "blocked", "cancelled"},
        "queued": {"running", "blocked", "cancelled"},
        "running": {"evaluating", "blocked", "failed", "cancelled"},
        "evaluating": {"awaiting_instruction", "blocked", "failed", "cancelled", "completed"},
        "blocked": {"awaiting_instruction", "cancelled"},
    }

    def create_tuning_experiment(self, *, spec_version: str, source_scope: dict) -> dict:
        """创建 draft 状态的实验记录。不启动压力工作。"""
        exp_id = _uuid()
        now = _now()
        scope_json = json.dumps(source_scope, ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_experiments "
                "(id, spec_version, status, source_scope_json, created_at, updated_at) "
                "VALUES (?, ?, 'draft', ?, ?, ?)",
                (exp_id, spec_version, scope_json, now, now),
            )
        return {"id": exp_id, "status": "draft"}

    def create_tuning_experiment_with_input(
        self, *, spec_version: str, source_scope: dict,
        workloads: list[dict], quality_context: dict,
        workspace_root: str | os.PathLike,
    ) -> dict:
        """Atomically create a draft experiment and its proposed input bundle."""
        from webui.execution_config import preview_scope

        if not isinstance(quality_context, dict):
            raise ValueError("quality_context 必须是对象")
        profile_summary = quality_context.get("profile_summary")
        screening_fields = quality_context.get("screening_fields")
        profile_ref = quality_context.get("profile_ref")
        if not isinstance(profile_summary, str) or not profile_summary.strip():
            raise ValueError("quality_context.profile_summary 不能为空")
        if not isinstance(screening_fields, dict):
            raise ValueError("quality_context.screening_fields 必须是对象")
        if not isinstance(profile_ref, str) or not profile_ref.strip():
            raise ValueError("quality_context.profile_ref 不能为空")
        normalized_quality_context = {
            "profile_summary": profile_summary.strip(),
            "screening_fields": json.loads(json.dumps(
                screening_fields, ensure_ascii=False, sort_keys=True,
            )),
            "profile_ref": profile_ref.strip(),
        }
        quality_context_bytes = json.dumps(
            normalized_quality_context, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        quality_context_digest = (
            _SHA256_PREFIX + hashlib.sha256(quality_context_bytes).hexdigest()
        )

        source_result = preview_scope(
            keywords=source_scope.get("keywords"),
            scope_kind=source_scope.get("scope_kind", "cities"),
            cities=source_scope.get("cities", []),
            pages_per_combination=int(source_scope.get("pages_per_combination", 0)),
            platform=source_scope.get("platform", "boss"),
        )
        normalized_source = source_result["scope"]
        # T606: 保留 controller 冻结的 runtime 字段（preview_scope 不返回它们）
        for runtime_field in (
            "browser_account", "cdp_port", "profile_key",
            "filter_schema_version", "task_input_digest",
        ):
            if runtime_field in source_scope:
                normalized_source[runtime_field] = source_scope[runtime_field]
        normalized_workloads = []
        for raw in workloads:
            if not isinstance(raw, dict):
                raise ValueError("workload 必须是对象")
            claimed_size = raw.get("task_size")
            structure_index = raw.get("structure_index")
            if claimed_size not in ("small", "medium", "large"):
                raise ValueError("workload task_size 无效")
            if not isinstance(structure_index, int) or structure_index < 1:
                raise ValueError("workload structure_index 必须是正整数")
            workload_scope = raw.get("scope") or source_scope
            scope_result = preview_scope(
                keywords=workload_scope.get("keywords"),
                scope_kind=workload_scope.get("scope_kind", "cities"),
                cities=workload_scope.get("cities", []),
                pages_per_combination=int(
                    workload_scope.get("pages_per_combination", 0)
                ),
            )
            frozen_scope = scope_result["scope"]
            if frozen_scope["task_size"] != claimed_size:
                raise ValueError(
                    "workload task_size 与后端计算结果不一致: "
                    f"{claimed_size} != {frozen_scope['task_size']}"
                )
            normalized_workloads.append((
                claimed_size, structure_index, frozen_scope,
            ))

        exp_id = _uuid()
        input_version_id = _uuid()
        artifact_records = []
        for task_size, structure_index, frozen_scope in normalized_workloads:
            workload_id = _uuid()
            artifact_path = f"tuning/{exp_id}/input/{workload_id}.json"
            # T606: workload artifact manifest 必须保存 platform/runtime
            # 见 data-model.md 第 233-249 行
            artifact_manifest = {
                "schema_version": 1,
                "artifact_manifest_path": artifact_path,
                "experiment_id": exp_id,
                "input_version_id": input_version_id,
                "workload_id": workload_id,
                "task_size": task_size,
                "structure_index": structure_index,
                "scope": frozen_scope,
                "scope_digest": frozen_scope["scope_digest"],
                "planned_pages": frozen_scope["planned_pages"],
                "expected_raw_jobs": frozen_scope["planned_pages"] * 40,
                "quality_context": normalized_quality_context,
                "quality_context_digest": quality_context_digest,
                "platform": normalized_source.get("platform", "boss"),
                "browser_account": normalized_source.get("browser_account"),
                "cdp_port": normalized_source.get("cdp_port"),
                "profile_key": normalized_source.get("profile_key"),
                "filter_schema_version": normalized_source.get("filter_schema_version"),
                "task_input_digest": normalized_source.get("task_input_digest"),
            }
            artifact_bytes = json.dumps(
                artifact_manifest, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            artifact_records.append({
                "id": workload_id,
                "task_size": task_size,
                "structure_index": structure_index,
                "scope": frozen_scope,
                "path": artifact_path,
                "manifest": artifact_manifest,
                "bytes": artifact_bytes,
                "digest": _SHA256_PREFIX + hashlib.sha256(artifact_bytes).hexdigest(),
            })

        root = Path(workspace_root).resolve()
        tuning_root = (root / "tuning").resolve()
        if root not in tuning_root.parents:
            raise ValueError("tuning artifact 根目录越过 workspace")
        experiment_root = (tuning_root / exp_id).resolve()
        if tuning_root not in experiment_root.parents:
            raise ValueError("实验 artifact 目录越过 tuning 根目录")
        expected_parent = (experiment_root / "input").resolve()
        if experiment_root.exists():
            raise ValueError("实验 artifact 目录已存在")
        written_paths: list[Path] = []
        temporary_paths: list[Path] = []
        created_artifact_tree = False
        try:
            expected_parent.mkdir(parents=True, exist_ok=False)
            created_artifact_tree = True
            for record in artifact_records:
                artifact_path = (root / record["path"]).resolve()
                if artifact_path.parent != expected_parent:
                    raise ValueError("artifact manifest 路径越过实验 input 目录")
                temporary_path = artifact_path.with_name(
                    artifact_path.name + f".{_uuid()}.tmp"
                )
                temporary_paths.append(temporary_path)
                with temporary_path.open("xb") as handle:
                    handle.write(record["bytes"])
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, artifact_path)
                temporary_paths.remove(temporary_path)
                written_paths.append(artifact_path)
                persisted = artifact_path.read_bytes()
                if persisted != record["bytes"] or (
                    _SHA256_PREFIX + hashlib.sha256(persisted).hexdigest()
                    != record["digest"]
                ):
                    raise OSError("artifact manifest 原子写入后校验失败")

            now = _now()
            with self._connection() as conn:
                scope_json_str = json.dumps(
                    normalized_source, ensure_ascii=False, sort_keys=True,
                )
                conn.execute(
                    "INSERT INTO tuning_experiments "
                    "(id, spec_version, status, input_version_id, platform, "
                    " source_scope_json, created_at, updated_at) "
                    "VALUES (?, ?, 'draft', ?, json_extract(?, '$.platform'), ?, ?, ?)",
                    (exp_id, spec_version, input_version_id,
                     scope_json_str, scope_json_str, now, now),
                )
                conn.execute(
                    "INSERT INTO tuning_input_versions "
                    "(id, experiment_id, scope_json, scope_digest, "
                    " quality_context_json, quality_context_digest, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                    (input_version_id, exp_id,
                     json.dumps(normalized_source, ensure_ascii=False, sort_keys=True),
                     normalized_source["scope_digest"],
                     quality_context_bytes.decode("utf-8"),
                     quality_context_digest, now),
                )
                for record in artifact_records:
                    frozen_scope = record["scope"]
                    conn.execute(
                        "INSERT INTO tuning_workloads "
                        "(id, input_version_id, task_size, structure_index, "
                        " frozen_scope_json, planned_pages, expected_raw_jobs, "
                        " artifact_manifest_json, artifact_digest, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                        (record["id"], input_version_id, record["task_size"],
                         record["structure_index"],
                         json.dumps(frozen_scope, ensure_ascii=False, sort_keys=True),
                         frozen_scope["planned_pages"],
                         frozen_scope["planned_pages"] * 40,
                         record["bytes"].decode("utf-8"), record["digest"]),
                    )
        except Exception:
            for temporary_path in temporary_paths:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            for artifact_path in written_paths:
                try:
                    artifact_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if created_artifact_tree:
                for directory in (expected_parent, experiment_root):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            raise
        return {
            "id": exp_id, "status": "draft",
            "input_version_id": input_version_id,
        }

    def get_tuning_input_bundle(self, experiment_id: str) -> dict:
        """Return the persisted input version and ordered workload structures."""
        with self._connection() as conn:
            input_row = conn.execute(
                "SELECT * FROM tuning_input_versions WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if input_row is None:
                raise KeyError(f"实验没有输入版本: {experiment_id}")
            workload_rows = conn.execute(
                "SELECT * FROM tuning_workloads WHERE input_version_id = ? "
                "ORDER BY CASE task_size WHEN 'small' THEN 1 WHEN 'medium' THEN 2 "
                "ELSE 3 END, structure_index",
                (input_row["id"],),
            ).fetchall()
        workloads = []
        for row in workload_rows:
            artifact_manifest = _decode_json(row["artifact_manifest_json"], {})
            workloads.append({
                "id": row["id"], "task_size": row["task_size"],
                "structure_index": row["structure_index"],
                "scope": _decode_json(row["frozen_scope_json"], {}),
                "planned_pages": row["planned_pages"],
                "artifact_manifest": artifact_manifest,
                "artifact_manifest_path": artifact_manifest.get(
                    "artifact_manifest_path"
                ),
                "artifact_digest": row["artifact_digest"],
                "status": row["status"],
            })
        return {
            "input_version": {
                "id": input_row["id"],
                "experiment_id": input_row["experiment_id"],
                "scope": _decode_json(input_row["scope_json"], {}),
                "scope_digest": input_row["scope_digest"],
                "quality_context": _decode_json(
                    input_row["quality_context_json"], None
                ),
                "quality_context_digest": input_row["quality_context_digest"],
                "status": input_row["status"],
                "confirmed_at": input_row["confirmed_at"],
            },
            "workloads": workloads,
        }

    def confirm_tuning_input(
        self, experiment_id: str, *, workspace_root: str | os.PathLike,
    ) -> dict:
        """Freeze a complete two-structures-per-size input bundle atomically."""
        now = _now()
        with self._connection() as conn:
            experiment = conn.execute(
                "SELECT status, input_version_id, source_scope_json "
                "FROM tuning_experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                raise KeyError(f"实验不存在: {experiment_id}")
            if experiment["status"] != "draft":
                raise ValueError("只有 draft 实验可以确认输入")
            input_version_id = experiment["input_version_id"]
            if not input_version_id:
                raise ValueError("实验缺少输入版本")
            # T606: 读取 experiment source_scope 用于 workload artifact manifest 校验
            experiment_scope = _decode_json(
                experiment["source_scope_json"], {}
            )
            rows = conn.execute(
                "SELECT id, task_size, structure_index, frozen_scope_json, "
                "planned_pages, expected_raw_jobs, artifact_manifest_json, "
                "artifact_digest "
                "FROM tuning_workloads WHERE input_version_id = ?",
                (input_version_id,),
            ).fetchall()
            input_row = conn.execute(
                "SELECT scope_digest, quality_context_json, "
                "quality_context_digest FROM tuning_input_versions WHERE id = ?",
                (input_version_id,),
            ).fetchone()
            quality_context = _decode_json(
                input_row["quality_context_json"], None
            )
            quality_context_digest = input_row["quality_context_digest"]
            if not isinstance(quality_context, dict) or not quality_context:
                raise ValueError("冻结质量上下文缺失")
            if not isinstance(quality_context_digest, str) or not quality_context_digest:
                raise ValueError("冻结质量上下文摘要缺失")
            quality_bytes = json.dumps(
                quality_context, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if _SHA256_PREFIX + hashlib.sha256(quality_bytes).hexdigest() != quality_context_digest:
                raise ValueError("冻结质量上下文摘要不匹配")
            structures = {size: set() for size in ("small", "medium", "large")}
            structure_digests = {
                size: set() for size in ("small", "medium", "large")
            }
            workload_digests = []
            workspace = Path(workspace_root).resolve()
            tuning_root = (workspace / "tuning").resolve()
            if workspace not in tuning_root.parents:
                raise ValueError("tuning artifact 根目录越过 workspace")
            expected_input_root = (
                tuning_root / experiment_id / "input"
            ).resolve()
            if tuning_root not in expected_input_root.parents:
                raise ValueError("workload artifact input 目录越界")
            for row in rows:
                structures[row["task_size"]].add(row["structure_index"])
                scope = _decode_json(row["frozen_scope_json"], {})
                digest = scope.get("scope_digest")
                workload_digests.append(digest)
                if digest:
                    structure_digests[row["task_size"]].add(digest)
                artifact_manifest = _decode_json(
                    row["artifact_manifest_json"], None
                )
                artifact_digest = row["artifact_digest"]
                if not isinstance(artifact_manifest, dict) or not artifact_manifest:
                    raise ValueError("workload artifact manifest 缺失")
                artifact_path = artifact_manifest.get("artifact_manifest_path")
                expected_relative = (
                    f"tuning/{experiment_id}/input/{row['id']}.json"
                )
                if artifact_path != expected_relative:
                    raise ValueError("workload artifact manifest 路径不匹配或越界")
                absolute_path = (workspace / artifact_path).resolve()
                if absolute_path.parent != expected_input_root:
                    raise ValueError("workload artifact manifest 路径越界")
                expected_manifest = {
                    "schema_version": 1,
                    "artifact_manifest_path": expected_relative,
                    "experiment_id": experiment_id,
                    "input_version_id": input_version_id,
                    "workload_id": row["id"],
                    "task_size": row["task_size"],
                    "structure_index": row["structure_index"],
                    "scope": scope,
                    "scope_digest": digest,
                    "planned_pages": row["planned_pages"],
                    "expected_raw_jobs": row["expected_raw_jobs"],
                    "quality_context": quality_context,
                    "quality_context_digest": quality_context_digest,
                    "platform": experiment_scope.get("platform", "boss"),
                    "browser_account": experiment_scope.get("browser_account"),
                    "cdp_port": experiment_scope.get("cdp_port"),
                    "profile_key": experiment_scope.get("profile_key"),
                    "filter_schema_version": experiment_scope.get(
                        "filter_schema_version"
                    ),
                    "task_input_digest": experiment_scope.get("task_input_digest"),
                }
                if artifact_manifest != expected_manifest:
                    raise ValueError("workload artifact manifest 身份或内容不匹配")
                if not isinstance(artifact_digest, str) or not artifact_digest:
                    raise ValueError("workload artifact digest 缺失")
                if not absolute_path.is_file():
                    raise ValueError("workload artifact 产物不存在")
                try:
                    artifact_bytes = absolute_path.read_bytes()
                    persisted_manifest = json.loads(artifact_bytes.decode("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("workload artifact 产物不可读或非 JSON") from exc
                actual_digest = (
                    _SHA256_PREFIX + hashlib.sha256(artifact_bytes).hexdigest()
                )
                if actual_digest != artifact_digest:
                    raise ValueError("workload artifact digest 摘要不匹配")
                if persisted_manifest != artifact_manifest:
                    raise ValueError("workload artifact manifest 内容与数据库不一致")
            incomplete = [
                size for size, indexes in structures.items() if len(indexes) < 2
            ]
            if incomplete:
                raise ValueError(
                    "每个任务规模至少需要两种结构，缺少: " + ", ".join(incomplete)
                )
            duplicate_sizes = [
                size for size, digests in structure_digests.items()
                if len(digests) < 2
            ]
            if duplicate_sizes:
                raise ValueError(
                    "每个任务规模需要两种内容不同的结构，重复: "
                    + ", ".join(duplicate_sizes)
                )
            conn.execute(
                "UPDATE tuning_input_versions SET status = 'confirmed', "
                "confirmed_at = ? WHERE id = ?",
                (now, input_version_id),
            )
            conn.execute(
                "UPDATE tuning_experiments SET status = 'preflight', updated_at = ? "
                "WHERE id = ?",
                (now, experiment_id),
            )
        return {
            "input_version_id": input_version_id,
            "scope_digest": input_row["scope_digest"],
            "workload_digests": workload_digests,
            "status": "preflight",
        }

    def get_tuning_experiment(self, experiment_id: str) -> dict:
        """返回实验记录。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tuning_experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"实验不存在: {experiment_id}")
        return {
            "id": row["id"],
            "platform": row["platform"],
            "spec_version": row["spec_version"],
            "status": row["status"],
            "input_version_id": row["input_version_id"],
            "quality_reference_id": row["quality_reference_id"],
            "baseline_config": _decode_json(row["baseline_config_json"], None),
            "baseline_config_digest": row["baseline_config_digest"],
            "current_stage": row["current_stage"],
            "current_candidate_id": row["current_candidate_id"],
            "estimated_remaining_seconds": row["estimated_remaining_seconds"],
            "blocked_code": row["blocked_code"],
            "blocked_reason": row["blocked_reason"],
            "source_scope": _decode_json(row["source_scope_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def update_tuning_experiment_status(
        self, experiment_id: str, *, status: str,
        blocked_code: str | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        """更新实验状态，强制合法转换。"""
        current = self.get_tuning_experiment(experiment_id)
        current_status = current["status"]
        if current_status in self._EXPERIMENT_TERMINAL_STATES:
            raise ValueError(
                f"实验已处于终态 {current_status}，不能转为 {status}"
            )
        legal = self._EXPERIMENT_LEGAL_TRANSITIONS.get(current_status, set())
        if status not in legal and status != current_status:
            raise ValueError(
                f"非法状态转换: {current_status} → {status}"
            )
        if status == "completed":
            issues = self.get_tuning_completion_issues(experiment_id)
            if issues:
                raise ValueError("实验最终门禁未通过: " + "; ".join(issues))
        now = _now()
        completed_at = now if status == "completed" else None
        with self._connection() as conn:
            conn.execute(
                "UPDATE tuning_experiments "
                "SET status = ?, blocked_code = ?, blocked_reason = ?, "
                "    completed_at = COALESCE(?, completed_at), updated_at = ? "
                "WHERE id = ?",
                (status, blocked_code, blocked_reason, completed_at, now, experiment_id),
            )

    def get_tuning_completion_issues(self, experiment_id: str) -> list[str]:
        """客观核验九槽版本和 2 结构 × 3 次端到端最终证据。"""
        import math

        from webui.execution_config import ExecutionConfigSnapshot

        issues: list[str] = []
        with self._connection() as conn:
            version_row = conn.execute(
                "SELECT matrix_json FROM mode_config_versions "
                "WHERE source_experiment_id = ? ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            input_row = conn.execute(
                "SELECT id, status FROM tuning_input_versions "
                "WHERE experiment_id = ? ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            candidate_rows = conn.execute(
                "SELECT id, config_digest FROM tuning_candidates WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchall()
            round_rows = conn.execute(
                "SELECT r.*, q.status AS reference_status "
                "FROM tuning_rounds r LEFT JOIN tuning_quality_references q "
                "ON q.id = r.quality_reference_id "
                "WHERE r.experiment_id = ? AND r.round_kind = 'end_to_end'",
                (experiment_id,),
            ).fetchall()
            workloads = [] if input_row is None else conn.execute(
                "SELECT id, task_size, frozen_scope_json FROM tuning_workloads "
                "WHERE input_version_id = ? ORDER BY task_size, structure_index",
                (input_row["id"],),
            ).fetchall()

        if version_row is None:
            issues.append("missing_candidate_mode_version")
            return issues
        try:
            matrix = _decode_json(version_row["matrix_json"], {})
            required_modes = ("stable", "balanced", "extreme")
            required_sizes = ("small", "medium", "large")
            slot_keys: set[tuple[str, str]] = set()
            for mode in required_modes:
                for size in required_sizes:
                    config = matrix[mode][size]
                    snapshot = ExecutionConfigSnapshot.create(config)
                    slot_keys.add((snapshot.config_digest, size))
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(f"invalid_candidate_mode_version:{exc}")
            return issues

        if input_row is None or input_row["status"] != "confirmed":
            issues.append("input_version_not_confirmed")
        workload_by_size: dict[str, list[Any]] = {
            size: [] for size in ("small", "medium", "large")
        }
        for workload in workloads:
            workload_by_size.setdefault(workload["task_size"], []).append(workload)
        for size, size_workloads in workload_by_size.items():
            digests = {
                _decode_json(row["frozen_scope_json"], {}).get("scope_digest")
                for row in size_workloads
            }
            digests.discard(None)
            if len(size_workloads) < 2 or len(digests) < 2:
                issues.append(f"insufficient_workload_structures:{size}")

        candidate_ids_by_digest: dict[str, set[str]] = {}
        for candidate in candidate_rows:
            candidate_ids_by_digest.setdefault(
                candidate["config_digest"], set(),
            ).add(candidate["id"])

        required_metrics = {
            "total_duration_ms", "work_duration_ms", "wait_duration_ms",
            "retry_duration_ms", "input_count", "terminal_count",
            "missing_count", "duplicate_count", "quality_diff_count",
        }
        for config_digest, size in sorted(slot_keys):
            candidate_ids = candidate_ids_by_digest.get(config_digest, set())
            if not candidate_ids:
                issues.append(f"missing_candidate_for_slot:{size}:{config_digest}")
                continue
            for workload in workload_by_size.get(size, []):
                matching = [
                    row for row in round_rows
                    if row["candidate_id"] in candidate_ids
                    and row["workload_id"] == workload["id"]
                    and row["status"] == "confirmed"
                ]
                repetitions = {row["repetition_index"] for row in matching}
                if len(repetitions) < 3:
                    issues.append(
                        f"insufficient_confirmed_repetitions:{size}:{workload['id']}"
                    )
                    continue
                for row in matching:
                    metrics = _decode_json(row["metrics_json"], {})
                    if not required_metrics.issubset(metrics):
                        issues.append(f"missing_round_metrics:{row['id']}")
                        continue
                    numeric = [metrics[key] for key in required_metrics]
                    if any(
                        isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value < 0
                        for value in numeric
                    ):
                        issues.append(f"invalid_round_metrics:{row['id']}")
                        continue
                    if (
                        metrics["terminal_count"] != metrics["input_count"]
                        or metrics["missing_count"] != 0
                        or metrics["duplicate_count"] != 0
                    ):
                        issues.append(f"terminal_conservation_failed:{row['id']}")
                    accounted = (
                        metrics["work_duration_ms"] + metrics["wait_duration_ms"]
                        + metrics["retry_duration_ms"]
                    )
                    if metrics["total_duration_ms"] != accounted:
                        issues.append(f"duration_not_accounted:{row['id']}")
                    if metrics["quality_diff_count"] != 0:
                        issues.append(f"quality_gate_failed:{row['id']}")
                    if row["quality_reference_id"] is None or row["reference_status"] != "confirmed":
                        issues.append(f"quality_reference_not_confirmed:{row['id']}")
        return issues
