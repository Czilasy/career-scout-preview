"""调优任务单子进程 runner（021 B4 外迁自 webui/app.py）。

执行单张调优任务单：一致性校验 → 调优轮次执行 → 测量聚合 → 证据落盘 →
轮次状态回写。共享运行态经 ctx 访问；ai_service 模块级直连（031 B9
门面拆除，patch webui.ai）。
"""

from __future__ import annotations
from webui import ai as ai_service

import json
from pathlib import Path

from webui.store_helpers import _now


def run_tuning_manifest_child(ctx, manifest_id: str):
    from webui.tuning import TuningController
    from webui.whitebox import WhiteboxService, build_tuning_plan

    controller = TuningController(ctx.store)
    record = ctx.store.get_task_manifest(manifest_id)
    manifest = record["manifest"]
    round_id = record["round_id"]
    round_kind = str(manifest.get("round_kind") or record.get("round_kind") or "unknown")
    whitebox = WhiteboxService(ctx.store) if hasattr(ctx.store, "create_whitebox_run") else None
    whitebox_ref = None
    whitebox_key = f"round:{round_kind}"
    if whitebox is not None:
        try:
            whitebox_ref = whitebox.begin(
                "tuning", round_id, build_tuning_plan(round_kind),
                parent_owner_id=manifest_id,
            )
            whitebox.record(whitebox_ref, {
                "idempotency_key": f"task-started:{round_id}",
                "event_type": "task_started", "occurred_at": _now(),
                "stage": round_kind, "required_evidence": False,
                "payload": {"planned_units": 1},
            })
            whitebox.record(whitebox_ref, {
                "idempotency_key": f"unit-started:{round_id}:{whitebox_key}",
                "event_type": "unit_started", "occurred_at": _now(),
                "stage": round_kind, "unit_kind": "tuning_round",
                "unit_key": whitebox_key, "attempt_no": 1,
                "required_evidence": False, "payload": {},
            })
        except Exception:
            try:
                ctx.store.update_tuning_round_status(
                    round_id, status="blocked", failure_code="whitebox_incomplete",
                )
            except Exception as marker_exc:
                from webui.logging_setup import get_logger
                get_logger(__name__).warning(
                    "tuning whitebox initialization rollback failed: %s",
                    type(marker_exc).__name__,
                )
            return
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
        try:
            controller.persist_stage_artifact(
                round_id=round_id,
                stage=manifest["round_kind"],
                payload=result,
                source_artifact_id=manifest["frozen_input"].get(
                    "source_artifact_id"
                ),
            )
        except Exception as exc:
            if whitebox_ref is not None:
                try:
                    whitebox.record(whitebox_ref, {
                        "idempotency_key": f"unit-incomplete:{round_id}:artifact",
                        "event_type": "unit_incomplete", "occurred_at": _now(),
                        "stage": round_kind, "unit_kind": "tuning_round",
                        "unit_key": whitebox_key, "attempt_no": 1,
                        "required_evidence": True, "severity": "error",
                        "payload": {"error_code": "artifact_persistence_failed",
                                    "reason": type(exc).__name__},
                    })
                    whitebox.finalize(whitebox_ref)
                except Exception as marker_exc:
                    from webui.logging_setup import get_logger
                    get_logger(__name__).warning(
                        "tuning artifact whitebox finalization failed: %s",
                        type(marker_exc).__name__,
                    )
            raise
    summary = controller.aggregate_measurements(round_id)
    measurement_complete = bool(
        summary.get("input_count", 0) > 0
        and summary.get("terminal_count", 0) == summary.get("input_count", 0)
        and summary.get("missing_count", 0) == 0
        and summary.get("duplicate_count", 0) == 0
    )
    if whitebox_ref is not None:
        try:
            if error_code:
                whitebox.record(whitebox_ref, {
                    "idempotency_key": f"unit-failed:{round_id}:{error_code}",
                    "event_type": "unit_failed", "occurred_at": _now(),
                    "stage": round_kind, "unit_kind": "tuning_round",
                    "unit_key": whitebox_key, "attempt_no": 1,
                    "required_evidence": True, "severity": "error",
                    "payload": {"error_code": error_code,
                                "error_reason": "调参阶段执行失败"},
                })
            elif measurement_complete:
                whitebox.record(whitebox_ref, {
                    "idempotency_key": f"scope-completed:{round_id}",
                    "event_type": "scope_completed", "occurred_at": _now(),
                    "stage": round_kind, "unit_kind": "tuning_round",
                    "unit_key": whitebox_key, "attempt_no": 1,
                    "required_evidence": True,
                    "payload": {"scope_complete": True,
                                "returned_total_count": summary.get("terminal_count", 0),
                                "unit_unique_count": summary.get("terminal_count", 0),
                                "quality_counts": {"measurement_events": len(controller.list_measurements(round_id))}},
                })
            else:
                whitebox.record(whitebox_ref, {
                    "idempotency_key": f"unit-incomplete:{round_id}:measurement",
                    "event_type": "unit_incomplete", "occurred_at": _now(),
                    "stage": round_kind, "unit_kind": "tuning_round",
                    "unit_key": whitebox_key, "attempt_no": 1,
                    "required_evidence": True, "severity": "warning",
                    "payload": {"error_code": "measurement_missing",
                                "reason": "缺少逐项测量终态证据"},
                })
        except Exception:
            error_code = error_code or "whitebox_incomplete"
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
    if whitebox_ref is not None:
        try:
            whitebox.finalize(whitebox_ref)
        except Exception:
            error_code = error_code or "whitebox_incomplete"
    ctx.store.update_tuning_round_status(
        round_id, status="reported", failure_code=error_code,
    )
