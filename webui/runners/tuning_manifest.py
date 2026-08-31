"""调优任务单子进程 runner（021 B4 外迁自 webui/app.py）。

执行单张调优任务单：一致性校验 → 调优轮次执行 → 测量聚合 → 证据落盘 →
轮次状态回写。共享运行态经 ctx 访问；ai_service 模块级直连（031 B9
门面拆除，patch webui.ai）。
"""

from __future__ import annotations
from webui import ai as ai_service

import json
from pathlib import Path


def run_tuning_manifest_child(ctx, manifest_id: str):
    from webui.tuning import TuningController

    controller = TuningController(ctx.store)
    record = ctx.store.get_task_manifest(manifest_id)
    manifest = record["manifest"]
    round_id = record["round_id"]
    sink = controller.build_measurement_sink(round_id)

    def measured(*args, **kwargs):
        controller.heartbeat_lease()
        return sink(*args, **kwargs)

    error_code = None
    try:
        # T614: 在 source/AI 执行前校验一致性，错配时阻断
        controller.validate_consistency_before_execution(
            manifest_id=manifest_id,
        )
        result = ctx.tuning_round_runner.execute(
            manifest, measurement_callback=measured,
        )
    except (
        OSError, RuntimeError, ValueError, KeyError, TypeError,
        ai_service.AISecurityError,
    ) as exc:
        result = None
        error_code = (
            exc.error_code
            if isinstance(exc, ai_service.AISecurityError)
            else getattr(exc, "error_code", type(exc).__name__.lower())
        )
    if isinstance(result, dict):
        controller.persist_stage_artifact(
            round_id=round_id,
            stage=manifest["round_kind"],
            payload=result,
            source_artifact_id=manifest["frozen_input"].get(
                "source_artifact_id"
            ),
        )
    summary = controller.aggregate_measurements(round_id)
    if not summary.get("input_count") and isinstance(result, dict):
        jobs = result.get("jobs")
        verdicts = result.get("verdicts")
        if isinstance(jobs, list):
            summary["input_count"] = len(jobs)
            summary["terminal_count"] = len(jobs)
            summary["success_count"] = len(jobs)
        elif isinstance(verdicts, dict):
            summary["input_count"] = len(verdicts)
            summary["terminal_count"] = len(verdicts)
            summary["success_count"] = len(verdicts)
    summary["work_duration_ms"] = (
        summary["total_duration_ms"] - summary["wait_duration_ms"]
        - summary["retry_duration_ms"]
    )
    evidence_path = manifest["monitoring"]["final_artifact_path"]
    evidence = {
        "program_report_path": evidence_path,
        "config_digest": manifest["execution_config"]["config_digest"],
        "scope_digest": manifest["frozen_input"]["scope_digest"],
        "input_artifact_digest": manifest["frozen_input"].get("artifact_digest"),
        **summary,
    }
    if error_code:
        evidence["error_counts"] = {
            **evidence.get("error_counts", {}), error_code: 1,
        }
    absolute = (Path(ctx.store.db_path).resolve().parent.parent / evidence_path).resolve()
    expected_root = (
        Path(ctx.store.db_path).resolve().parent.parent / "tuning"
        / manifest["experiment_id"]
    ).resolve()
    if expected_root not in absolute.parents:
        raise ValueError("程序证据输出路径越过实验根目录")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ), encoding="utf-8")
    controller._save_round_metrics(round_id, evidence)
    ctx.store.update_tuning_round_status(
        round_id, status="reported", failure_code=error_code,
    )
