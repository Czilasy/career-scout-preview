from __future__ import annotations
import hashlib
import json
import pathlib


def _scope(keyword_count, pages):
    return {
        "keywords": [f"结构关键词{i}" for i in range(keyword_count)],
        "scope_kind": "cities", "cities": ["东莞"],
        "pages_per_combination": pages,
    }


def _sample_nine_fields(**overrides) -> dict:
    """返回一份完整的速度字段配置（含 JD 并发 Tab 数）。"""
    base = {
        "inter_combo_delay": 10.0,
        "detail_batch_size": 15,
        "detail_interval": 2.0,
        "detail_reset_every": 4,
        "detail_batch_cooldown": 5.0,
        "detail_tab_pool_size": 5,
        "screen_batch_size": 50,
        "screen_concurrency": 5,
        "match_batch_size": 4,
        "match_concurrency": 10,
    }
    base.update(overrides)
    return base


def _expected_path_digest(path: pathlib.Path) -> str:
    if path.is_file():
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    files = sorted(
        (item for item in path.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(path).as_posix(),
    )
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(item.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


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
            "detail_tab_pool_size": 5,
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
