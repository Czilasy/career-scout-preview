"""调优任务单签发、执行与报告校验渲染 mixin（021 B7 自 tuning.py 搬运）。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from webui.tuning_digest import _SHA256_PREFIX, sha256_path

class TuningManifestsMixin:
    """manifest 校验/签发/执行、执行者报告校验与 markdown 渲染。"""

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

    # execution_config 必填的速度字段（含 JD 并发 Tab 数）
    _EXEC_CONFIG_REQUIRED_FIELDS = frozenset({
        "schema_version", "inter_combo_delay", "detail_batch_size",
        "detail_interval", "detail_reset_every", "detail_batch_cooldown",
        "detail_tab_pool_size",
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

    @staticmethod
    def _validate_retry_policy(policy: object) -> None:
        """FR-021: manifest 重试策略与默认策略同构；缺失/空值视为回退默认。"""
        if policy is None:
            return
        if not isinstance(policy, dict):
            raise ValueError("retry_policy 必须是对象")
        if not policy:
            return
        from webui.ai_retry import normalize_retry_policy
        if normalize_retry_policy(policy) is None:
            raise ValueError("retry_policy 结构与默认重试策略不一致")

    def _validate_manifest(self, manifest: dict) -> None:
        """FR-044/045: 校验 manifest 完整性和合法性。"""
        # 1. 必填字段
        missing = self._MANIFEST_REQUIRED_FIELDS - set(manifest.keys())
        if missing:
            raise ValueError(f"manifest 缺少必填字段: {sorted(missing)}")
        self._validate_retry_policy(manifest.get("retry_policy"))
        # 2. execution_config 必填速度字段
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
        manifest_digest = _SHA256_PREFIX + hashlib.sha256(
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
        digest = _SHA256_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
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
        actual_digest = sha256_path(report_path)
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
            if (
                item.get("existence_requirement") == "required"
                or item.get("existence_required")
            )
        }
        reported_artifacts = {item.get("path"): item for item in artifacts}
        for path, required in required_artifacts.items():
            artifact = reported_artifacts.get(path)
            if artifact is None:
                raise ValueError(f"缺少必需产物: {path}")
            absolute = (self._workspace_root / path).resolve()
            if (
                expected_root not in absolute.parents
                or not (absolute.is_file() or absolute.is_dir())
            ):
                raise ValueError(f"必需产物不存在或越界: {path}")
            digest = sha256_path(absolute)
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
            if evidence["input_count"] <= 0:
                raise ValueError("completed 报告的 input_count 必须大于 0")
            conserved = (
                evidence["terminal_count"] == evidence["input_count"]
                and evidence["missing_count"] == 0
                and evidence["duplicate_count"] == 0
            )
            if not conserved:
                raise ValueError("程序证据终态守恒失败")
        # blocked 报告必须保留现场证据，即使阻断原因本身就是终态
        # 不守恒、重复或失败。它不会进入候选比较；强制 duplicate=0
        # 会让真实的危险边界无法通过状态机收口。
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
            "detail_tab_pool_size",
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
