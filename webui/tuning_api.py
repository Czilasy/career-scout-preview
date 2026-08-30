"""调优实验 / manifest / decision API 路由（021 B6 T019 外迁自 webui/app.py）。

SPEC011 控制者与执行者路由：实验生命周期、manifest 签发与执行、轮次
证据上报与决策落库。路由体纯搬运，HTTP 契约零改动；store / executor /
manifest 子任务入口经 ctx 取用。
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import jsonify, request

from webui.constants import _MSG_EXPERIMENT_NOT_FOUND, _MSG_MANIFEST_NOT_FOUND

def register_tuning_routes(app, ctx):
    @app.route("/api/tuning/experiments", methods=["POST"])
    def tuning_create_experiment():
        """POST /api/tuning/experiments

        创建 draft 状态的实验。不启动压力工作。
        """
        body = request.get_json(silent=True) or {}
        spec_version = body.get("spec_version")
        source_scope = body.get("source_scope")
        quality_context = body.get("quality_context")
        if not spec_version or not source_scope or not quality_context:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": (
                    "缺少 spec_version、source_scope 或 quality_context"
                ),
            }), 400
        if not isinstance(source_scope, dict):
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "source_scope 必须是对象",
            }), 400
        from webui.tuning import TuningController
        controller = TuningController(ctx.store)
        try:
            experiment = controller.create_experiment_with_input(
                spec_version=spec_version,
                source_scope=source_scope,
                workloads=body.get("workloads") or [],
                quality_context=quality_context,
            )
        except (ValueError, TypeError) as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": str(exc),
            }), 400
        return jsonify({
            "ok": True,
            "experiment_id": experiment["id"],
            "status": experiment["status"],
        }), 201

    @app.route("/api/tuning/experiments/<experiment_id>",
               methods=["GET"])
    def tuning_get_experiment(experiment_id: str):
        """GET /api/tuning/experiments/{id}

        返回实验持久化快照。
        """
        try:
            exp = ctx.store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        from webui.tuning import TuningController
        controller = TuningController(ctx.store)
        # 计算进度和剩余时间
        progress = controller.project_remaining_time(experiment_id)
        if progress is None:
            progress = {
                "confirmed_rounds": 0,
                "remaining_required_rounds": 0,
                "estimated_remaining_seconds": 0,
            }
        # 计算可执行操作
        status = exp["status"]
        can_cancel = status not in ("cancelled", "failed", "completed")
        can_resume = status == "blocked"
        can_apply = status == "completed"
        return jsonify({
            "ok": True,
            "experiment": {
                "id": exp["id"],
                "status": status,
                "spec_version": exp["spec_version"],
                "current_stage": exp.get("current_stage"),
                "current_candidate_id": exp.get("current_candidate_id"),
                "input_version_id": exp.get("input_version_id"),
                "quality_reference_id": exp.get("quality_reference_id"),
                "blocked_code": exp.get("blocked_code"),
                "blocked_reason": exp.get("blocked_reason"),
                "source_scope": exp.get("source_scope", {}),
                "created_at": exp.get("created_at"),
                "progress": progress,
                "can_cancel": can_cancel,
                "can_resume": can_resume,
                "can_apply": can_apply,
            },
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/cancel",
               methods=["POST"])
    def tuning_cancel_experiment(experiment_id: str):
        """POST /api/tuning/experiments/{id}/cancel

        取消实验，保留已确认证据，释放租约。
        """
        try:
            ctx.store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        from webui.tuning import TuningController
        controller = TuningController(ctx.store)
        try:
            controller.cancel_experiment(experiment_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            "status": "cancelled",
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/confirm-input",
               methods=["POST"])
    def tuning_confirm_input(experiment_id: str):
        """POST /api/tuning/experiments/{id}/confirm-input

        冻结输入版本，推进实验到 preflight。
        """
        try:
            exp = ctx.store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        if exp["status"] != "draft":
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": f"实验状态不是 draft: {exp['status']}",
            }), 409
        from webui.tuning import TuningController
        controller = TuningController(ctx.store)
        try:
            result = controller.confirm_input(experiment_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "input_incomplete",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            **result,
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/resume",
               methods=["POST"])
    def tuning_resume_experiment(experiment_id: str):
        """POST /api/tuning/experiments/{id}/resume

        从 blocked 状态恢复到 awaiting_instruction。
        只允许 blocked 状态恢复，不自动选择新候选。
        """
        try:
            exp = ctx.store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        if exp["status"] != "blocked":
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": f"只有 blocked 状态才能恢复，当前: {exp['status']}",
            }), 409
        try:
            ctx.store.update_tuning_experiment_status(
                experiment_id, status="awaiting_instruction",
            )
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            "status": "awaiting_instruction",
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/result",
               methods=["GET"])
    def tuning_get_experiment_result(experiment_id: str):
        """Return safe candidate/evidence summary with an objective apply gate."""
        from webui.tuning import TuningController
        controller = TuningController(ctx.store)
        try:
            result = controller.get_experiment_result(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        return jsonify({"ok": True, **result}), 200

    @app.route("/api/tuning/experiments/<experiment_id>/apply",
               methods=["POST"])
    def tuning_apply_experiment_result(experiment_id: str):
        """Apply one exact complete nine-slot candidate after explicit request."""
        body = request.get_json(silent=True) or {}
        digest = body.get("candidate_mode_version_digest")
        if not digest:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "缺少 candidate_mode_version_digest",
            }), 400
        from webui.tuning import TuningController
        controller = TuningController(ctx.store)
        try:
            version = controller.apply_candidate_mode_version(
                experiment_id=experiment_id, version_digest=str(digest),
            )
        except KeyError:
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "result_not_applicable",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True, "mode_version_id": version["id"],
            "version_digest": version["version_digest"],
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/manifests",
               methods=["POST"])
    def tuning_issue_manifest(experiment_id: str):
        """POST /api/tuning/experiments/{id}/manifests

        控制者签发一份不可变任务单。
        """
        from webui.tuning import TuningController

        body = request.get_json(silent=True) or {}
        # 确保路径参数与 body 一致
        body["experiment_id"] = experiment_id
        controller = TuningController(ctx.store)
        # 校验实验存在且处于 awaiting_instruction
        try:
            exp = ctx.store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        if exp["status"] != "awaiting_instruction":
            return jsonify({
                "ok": False, "error_code": "invalid_experiment_status",
                "error": f"实验状态不是 awaiting_instruction: {exp['status']}",
            }), 409
        try:
            result = controller.issue_manifest(body)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "manifest_validation_failed",
                "error": str(exc),
            }), 422
        return jsonify({"ok": True, **result}), 201

    @app.route("/api/tuning/manifests/<manifest_id>", methods=["GET"])
    def tuning_get_manifest(manifest_id: str):
        """GET /api/tuning/manifests/{id}

        返回安全结构化 manifest，不含凭据。
        """
        try:
            record = ctx.store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": _MSG_MANIFEST_NOT_FOUND,
            }), 404
        # 不返回凭据/敏感字段
        safe_manifest = dict(record["manifest"])
        # 移除可能的敏感字段
        for sensitive in ("api_key", "credentials", "password", "token"):
            safe_manifest.pop(sensitive, None)
        return jsonify({
            "ok": True,
            "manifest_id": record["id"],
            "manifest": safe_manifest,
            "manifest_digest": record["manifest_digest"],
            "rendered_task_path": record["rendered_task_path"],
            "status": record["status"],
        }), 200

    @app.route("/api/tuning/manifests/<manifest_id>/execute",
               methods=["POST"])
    def tuning_execute_manifest(manifest_id: str):
        """POST /api/tuning/manifests/{id}/execute

        启动 manifest 对应的轮次。重新校验摘要、产物、租约。
        """
        from webui.tuning import TuningController

        try:
            record = ctx.store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": _MSG_MANIFEST_NOT_FOUND,
            }), 404
        controller = TuningController(ctx.store)
        try:
            started = controller.execute_manifest(manifest_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "round_state_conflict",
                "error": str(exc),
            }), 409
        round_id = started["round_id"]
        child_task_id = record["manifest"].get("task_id") or round_id
        if app.config.get("START_TASKS"):
            try:
                ctx.executor.submit(ctx.run_tuning_manifest_child, manifest_id)
            except RuntimeError as exc:
                return jsonify({
                    "ok": False, "error_code": "submit_failed", "error": str(exc),
                }), 503
        return jsonify({
            "ok": True,
            "child_task_id": child_task_id,
            "round_id": round_id,
            "status": "running",
            "status_url": f"/api/tuning/rounds/{round_id}",
        }), 202

    @app.route("/api/tuning/rounds/<round_id>", methods=["GET"])
    def tuning_get_round(round_id: str):
        """GET /api/tuning/rounds/{id}

        返回轮次的程序状态。
        """
        try:
            round_rec = ctx.store.get_tuning_round(round_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "round_not_found",
                "error": "轮次不存在",
            }), 404
        return jsonify({
            "ok": True,
            "round": {
                "id": round_rec["id"],
                "status": round_rec["status"],
                "experiment_id": round_rec["experiment_id"],
                "candidate_id": round_rec["candidate_id"],
                "round_kind": round_rec["round_kind"],
                "repetition_index": round_rec["repetition_index"],
                "manifest_id": round_rec.get("manifest_id"),
                "started_at": round_rec.get("started_at"),
                "finished_at": round_rec.get("finished_at"),
                "confirmed_at": round_rec.get("confirmed_at"),
                "failure_code": round_rec.get("failure_code"),
            },
        }), 200

    @app.route("/api/tuning/manifests/<manifest_id>/report",
               methods=["POST"])
    def tuning_submit_report(manifest_id: str):
        """POST /api/tuning/manifests/{id}/report

        接受一份执行者报告，校验并更新轮次状态。
        """
        from webui.tuning import TuningController

        body = request.get_json(silent=True) or {}
        try:
            _ = ctx.store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": _MSG_MANIFEST_NOT_FOUND,
            }), 404
        try:
            saved = TuningController(ctx.store).accept_report(
                manifest_id=manifest_id, report=body)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "report_validation_failed",
                "error": str(exc),
                "validation_status": "rejected",
            }), 422
        return jsonify({
            "ok": True,
            "report_id": saved["report_id"],
            "validation_status": "accepted",
            "round_status": saved["round_status"],
            "experiment_status": saved["experiment_status"],
        }), 201

    @app.route("/api/tuning/rounds/<round_id>/evidence", methods=["GET"])
    def tuning_get_evidence(round_id: str):
        """GET /api/tuning/rounds/{id}/evidence

        返回安全聚合证据，不含凭据/原始简历/原始模型响应。
        """
        from webui.tuning import TuningController

        try:
            _ = ctx.store.get_tuning_round(round_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "round_not_found",
                "error": "轮次不存在",
            }), 404
        controller = TuningController(ctx.store)
        try:
            summary = controller.aggregate_measurements(round_id)
        except (KeyError, ValueError):
            summary = {}
        # 不返回敏感字段
        safe_summary = {
            "total_duration_ms": summary.get("total_duration_ms", 0),
            "stage_durations_ms": summary.get("stage_durations_ms", {}),
            "wait_duration_ms": summary.get("wait_duration_ms", 0),
            "retry_duration_ms": summary.get("retry_duration_ms", 0),
            "attempt_count": summary.get("attempt_count", 0),
            "retry_count": summary.get("retry_count", 0),
            "input_count": summary.get("input_count", 0),
            "terminal_count": summary.get("terminal_count", 0),
            "success_count": summary.get("success_count", 0),
            "failed_count": summary.get("failed_count", 0),
            "missing_count": summary.get("missing_count", 0),
            "duplicate_count": summary.get("duplicate_count", 0),
            "error_counts": summary.get("error_counts", {}),
            "error_correlation_id": summary.get("error_correlation_id"),
        }
        return jsonify({
            "ok": True,
            "evidence": safe_summary,
            "round_id": round_id,
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/decisions",
               methods=["POST"])
    def tuning_post_decision(experiment_id: str):
        """POST /api/tuning/experiments/{id}/decisions

        控制者对候选做出 promote/reject/refine 决策。
        执行者 AI 不能调用此路由。
        """
        body = request.get_json(silent=True) or {}
        candidate_id = body.get("candidate_id")
        decision = body.get("decision")
        if not candidate_id or not decision:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "缺少 candidate_id 或 decision",
            }), 400
        if decision not in ("promote", "reject", "refine"):
            return jsonify({
                "ok": False, "error_code": "invalid_decision",
                "error": f"未知决策类型: {decision}",
            }), 422
        # 校验实验存在
        try:
            _ = ctx.store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        # 校验候选存在且属于该实验
        try:
            candidate = ctx.store.get_tuning_candidate(candidate_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "candidate_not_found",
                "error": "候选不存在",
            }), 404
        if candidate["experiment_id"] != experiment_id:
            return jsonify({
                "ok": False, "error_code": "candidate_mismatch",
                "error": "候选不属于该实验",
            }), 422
        # 校验 evidence ownership
        reason_evidence = body.get("reason_evidence", [])
        if not isinstance(reason_evidence, list):
            reason_evidence = []
        now = datetime.now().isoformat()
        # 应用决策
        if decision == "promote":
            with ctx.store._connection() as conn:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = 'promoted', promotion_reason = ?, "
                    "    updated_at = ? WHERE id = ?",
                    (json.dumps(reason_evidence, ensure_ascii=False),
                     now, candidate_id),
                )
        elif decision == "reject":
            code = body.get("code", "rejected")
            with ctx.store._connection() as conn:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = 'rejected', rejection_code = ?, "
                    "    updated_at = ? WHERE id = ?",
                    (code, now, candidate_id),
                )
        elif decision == "refine":
            next_config = body.get("next_config")
            if not next_config:
                return jsonify({
                    "ok": False, "error_code": "missing_next_config",
                    "error": "refine 决策必须提供 next_config",
                }), 422
            # 创建新的候选
            new_candidate = ctx.store.save_tuning_candidate(
                experiment_id=experiment_id,
                stage=candidate["stage"],
                strategy_step=candidate["strategy_step"],
                config=next_config,
                parent_candidate_id=candidate_id,
            )
            return jsonify({
                "ok": True,
                "decision": "refine",
                "new_candidate_id": new_candidate["id"],
            }), 200
        return jsonify({
            "ok": True,
            "decision": decision,
            "candidate_id": candidate_id,
        }), 200
