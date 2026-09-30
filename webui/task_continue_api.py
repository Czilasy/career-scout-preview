"""任务续跑 / 暂停 / 取消 / 结束 API 路由（021 B6 T019 外迁自 webui/app.py）。
paused/中断 run 的续跑分发（抓取/筛选/重抓三向）、用户暂停与取消、
结束任务的部分快照定稿。路由体纯搬运：HTTP 契约零改动。
"""
from __future__ import annotations
import threading
import sqlite3
import time
import sys
from flask import jsonify, request
from webui.constants import (
    _MSG_TASK_ALREADY_RUNNING,
    _MSG_TASK_NOT_FOUND,
)
from webui.task_status import (
    _public_task_status,
    is_resume_eligible_run,
    _refresh_paused_run_execution_config,
)
from webui.resume_identity import (
    append_account_switch_log_line,
    activate_frozen_identity_candidate,
    commit_continue_identity,
    invalidate_login_cache_for_resume,
    prepare_continue_identity,
)
from webui.store import DiscoveryStoreConflictError
from webui.error_registry import resolve_code
from webui.pipeline_exec_status import user_visible_failure_reason
from webui.task_pause_support import (
    STOP_MODE_CANCEL,
    STOP_MODE_FINISH,
    ScrapeCheckpointReadError,
    cancel_task_cleanup,
    pause_with_mode,
    request_stop,
    continue_task_kind,
    normalize_recoverable_failed_run,
    scrape_checkpoint,
    scrape_completion_evidence,
)
from webui.task_runners import _iso_epoch_ms
from webui.logging_setup import get_logger
from webui.task_event_audit import append_task_event_best_effort
from webui.flow_service import submit_platform_task
from webui.flow_future import attach_ai_future_failure
from webui.task_continue_results import build_partial_pipeline_result
from webui.task_continue_support import (
    _FINALIZE_WAIT_TIMEOUT_S,
    jd_batch_active,
    wait_for_jd_batch_settle,
)
_logger = get_logger(__name__)

_FINISH_BATCH_WAIT_TIMEOUT_S, _FINISH_BATCH_SETTLE_GRACE_S = 15 * 60, 1.5
_jd_batch_active = jd_batch_active


def _wait_for_jd_batch_settle(ctx, task_id):
    return wait_for_jd_batch_settle(
        ctx,
        task_id,
        timeout_s=_FINISH_BATCH_WAIT_TIMEOUT_S,
        settle_grace_s=_FINISH_BATCH_SETTLE_GRACE_S,
    )


def _sync_flow_track_after_continue(
    ctx, run_id: str, run: dict | None, response=None,
):
    """Publish a successful legacy continuation to its owning Track."""
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if status_code is None and isinstance(response, tuple) and len(response) > 1:
            status_code = response[1]
        if status_code is not None and int(status_code) >= 400:
            return None
    from webui.flow_task_coordinator import (
        FlowTaskOperationError,
        MissingPlatformIdentityError,
        _ensure_operation_succeeded,
        sync_flow_track_for_run,
    )

    try:
        if response is not None:
            _ensure_operation_succeeded(response)
        sync_flow_track_for_run(
            ctx,
            run_id,
            status="running",
            stage=str((run or {}).get("current_stage") or "scrape"),
        )
    except MissingPlatformIdentityError:
        return jsonify({
            "ok": False,
            "run_id": run_id,
            "error": "platform_identity_missing",
            "error_code": "platform_identity_missing",
            "message": "任务缺少平台身份，无法继续关联运行线",
        }), 409
    except FlowTaskOperationError:
        return jsonify({
            "ok": False,
            "run_id": run_id,
            "error": "flow_task_operation_failed",
            "error_code": "flow_task_operation_failed",
            "message": "关联运行线继续失败，请刷新任务状态后重试",
        }), 503
    return None


def register_task_continue_routes(app, ctx):
    @app.route("/api/task/continue/<run_id>", methods=["POST"])
    def api_task_continue(run_id: str):
        """FR-020/FR-022：统一继续接口。
        允许 paused 及修复前遗留的可恢复 failed 状态调用；running 状态拒绝
        （防止重复继续）。
        继续前检查阻断条件是否解除（由各阶段 handler 自行实现）。
        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp
        run = ctx.store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            MissingPlatformIdentityError,
            ensure_ai_run_bound_to_track,
            resolve_flow_binding,
        )
        # 继续前先确认轨道指名这条 run：历史数据里轨道可能只有抓取 id，
        # 此时轨道级动作与继续都找不到可操作对象。
        try:
            ensure_ai_run_bound_to_track(ctx, run_id)
        except FlowTaskOperationError:
            pass
        try:
            flow_binding = resolve_flow_binding(ctx, run_id)
        except MissingPlatformIdentityError:
            return jsonify({
                "ok": False,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法继续关联运行线",
            }), 409
        except FlowTaskOperationError:
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "任务关联运行线无效，请刷新后重试",
            }), 409
        if flow_binding is not None:
            # The durable Track is the only platform authority for a Flow
            # resume.  Feed that identity into the candidate builder as a
            # local overlay so a historical screening row whose platform
            # column defaulted to BOSS cannot activate the wrong login space.
            run = dict(run)
            resume_params = dict(run.get("execution_params") or {})
            resume_params["flow_id"] = flow_binding["flow_id"]
            resume_params["track_id"] = flow_binding["track_id"]
            resume_params["platform"] = flow_binding["platform"]
            run["execution_params"] = resume_params
            run["platform"] = flow_binding["platform"]
        if not is_resume_eligible_run(run):
            return jsonify({
                "ok": False,
                "error": "not_paused",
                "status": _public_task_status(run["status"], run.get("interruption_kind")),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
        # Resolve legacy rows before building the candidate identity.  The
        # continuation order is deliberately identity -> fresh CDP/login
        # check -> strict checkpoint read -> durable commit -> dispatch.
        continue_kind = continue_task_kind(ctx, run_id, run)
        if continue_kind == "scrape" and scrape_completion_evidence(ctx, run_id):
            # 收尾族：任务其实已完整完成（旧收尾竞态把它误写成 paused），
            # 按事实定稿为完成并明确答复，不再去连已关闭的浏览器。
            try:
                ctx.store.settle_paused_run_as_completed(
                    run_id,
                    processed_count=int(run.get("processed_count") or 0),
                    source_count=int(run.get("source_count") or 0),
                    current_stage="scrape",
                )
            except ctx.operational_errors:
                pass
            with ctx.lock:
                _settled_task = ctx.tasks.get(run_id)
                if _settled_task is not None:
                    _settled_task["status"] = "done"
                    _settled_task["error"] = ""
            return jsonify({
                "ok": False, "error": "already_completed",
                "status": "completed",
                "message": "任务已完整完成，无需继续",
            }), 409
        if run.get("status") == "failed":
            run = normalize_recoverable_failed_run(ctx, run)
        # Reject stale/repeated requests before binding or persisting a new
        # identity.  These checks are intentionally side-effect free so a
        # failed retry keeps the durable run paused and unmodified.
        with ctx.lock:
            existing = ctx.tasks.get(run_id)
            if existing is not None and existing.get("status") == "running":
                return jsonify({
                    "ok": False,
                    "error": "already_running",
                    "message": "该任务正在运行，请勿重复点击继续",
                }), 409
        run_version = run.get("backend_version")
        if run_version and run_version != ctx.backend_version:
            return jsonify({
                "ok": False,
                "error": "version_mismatch",
                "message": "后端版本已变更，请刷新页面后重试",
                "run_version": run_version,
                "current_version": ctx.backend_version,
            }), 409
        _continue_body = request.get_json(silent=True) or {}
        target_account = str(_continue_body.get("target_account") or "").strip()
        plan = prepare_continue_identity(
            ctx.store,
            run,
            target_account=target_account,
            current_account=ctx.load_legacy_advanced_settings,
            fallback_account=lambda: ctx.account_for_run(run),
            accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
        )
        if plan["status"] != "ok":
            return jsonify(plan["body"]), plan["http_status"]
        identity = plan["identity"]
        auto_switch = plan.get("auto_switch")
        activation = activate_frozen_identity_candidate(
            ctx.activate_run_browser, run, identity)
        if not activation["ok"]:
            error_code = resolve_code(
                activation.get("error_code") or activation.get("error"),
                default="source_cdp_unavailable",
            )
            error_reason = user_visible_failure_reason(
                error_code, "", str(identity.get("platform") or ""),
            )
            try:
                ctx.write_run(
                    run_id, status="paused",
                    current_stage=str(run.get("current_stage") or "scrape"),
                    error_code=error_code,
                    error_reason=error_reason,
                )
                append_task_event_best_effort(
                    ctx.store, run_id, "resume_activation_failed", {
                        "stage": str(run.get("current_stage") or "scrape"),
                        "error_code": error_code,
                        "error_reason": error_reason,
                    },
                    logger=_logger,
                    context="resume activation failure audit write failed",
                )
                if flow_binding is not None:
                    from webui.flow_task_state import close_flow_task_state

                    activation_source_id = str(
                        ((run.get("execution_params") or {}).get("scrape_task_id") or "")
                    ).strip() or None
                    close_flow_task_state(
                        ctx,
                        task_id=run_id,
                        scrape_task_id=activation_source_id,
                        flow_id=flow_binding["flow_id"],
                        profile_id=flow_binding["profile_id"],
                        status="paused",
                        error_code=error_code,
                        reason=error_reason,
                        platform=flow_binding["platform"],
                        stage=str(run.get("current_stage") or "ai"),
                    )
            except ctx.operational_errors as exc:
                _logger.warning(
                    "继续激活失败状态写入失败 error_type=%s",
                    type(exc).__name__,
                )
            return jsonify({
                "ok": False,
                "error": error_code,
                "error_code": error_code,
                "error_reason": error_reason,
                "message": error_reason,
                "status": activation["status"],
            }), 409
        run = activation["run"]
        invalidate_login_cache_for_resume(
            identity["browser_account"], identity["platform"])
        # The candidate is already bound; this is the single fresh CDP/login
        # check before any identity commit or pipeline claim.
        passed, code, reason = ctx.check_resume_block(run)
        if not passed:
            return jsonify({
                "ok": False, "error": "block_not_resolved",
                "error_code": code, "error_reason": reason,
                "status": "paused",
            }), 409
        if continue_kind == "scrape":
            with ctx.lock:
                current_task = ctx.tasks.get(run_id)
                current_result = (current_task or {}).get("result") or {}
            completed_combos = (
                current_result.get("completed_combos")
                if isinstance(current_result, dict) else None
            )
            try:
                scrape_checkpoint(
                    ctx, run_id, completed_combos=completed_combos,
                )
            except ScrapeCheckpointReadError as exc:
                return jsonify({
                    "ok": False,
                    "error": exc.error_code,
                    "error_code": exc.error_code,
                    "error_reason": exc.public_reason,
                    "message": exc.public_reason,
                    "status": "failed",
                }), 409
        try:
            commit_continue_identity(ctx.store, run_id, identity, auto_switch)
        except ctx.operational_errors as exc:
            return jsonify({
                "ok": False,
                "error": "resume_identity_persist_failed",
                "message": "继续任务身份未能保存，任务保持暂停，请重试",
                "status": "paused",
            }), 503
        except (KeyError, ValueError) as exc:
            return jsonify({
                "ok": False,
                "error": "resume_identity_persist_failed",
                "message": "继续任务身份未能保存，任务保持暂停，请重试",
                "status": "paused",
            }), 503
        refreshed_config = None
        def _refresh_run_config():
            nonlocal refreshed_config
            refreshed_config = _refresh_paused_run_execution_config(run, ctx.store)
            if refreshed_config is not None:
                run["execution_params"]["execution_config"] = refreshed_config.to_dict()
        if continue_kind == "recrawl":
            _refresh_run_config()
            response = ctx.continue_recrawl(
                run_id, _block_checked=True,
                account_switch_note=(
                    (auto_switch[1], auto_switch[2])
                    if auto_switch is not None and auto_switch[0] else None))
            sync_error = _sync_flow_track_after_continue(ctx, run_id, run, response)
            return sync_error or response
        if continue_kind == "scrape":
            # Scrape resumes the immutable execution snapshot captured by
            # this run.  Unlike AI/recrawl, changing current advanced
            # settings must not alter the search pacing or frozen scope of a
            # partially completed platform crawl.
            response = ctx.continue_execute_search(
                run_id, _block_checked=True,
                account_switch_note=(
                    (auto_switch[1], auto_switch[2])
                    if auto_switch is not None and auto_switch[0] else None))
            sync_error = _sync_flow_track_after_continue(ctx, run_id, run, response)
            return sync_error or response
        params = run.get("execution_params") or {}
        scrape_task_id = str(params.get("scrape_task_id") or "")
        profile_summary = str(params.get("profile_summary") or "")
        profile_facts = params.get("profile_facts") or None
        if not scrape_task_id:
            return jsonify({"ok": False, "error": "missing_scrape_task_id"}), 409
        source_jobs = ctx.store.load_scrape_run_jobs(scrape_task_id)
        if not source_jobs:
            return jsonify({
                "ok": False,
                "error": "missing_scrape_snapshot",
                "message": "抓取岗位快照缺失，无法安全继续 AI 筛选",
            }), 409
        from webui.task_finish_whitebox import source_integrity_for_resume
        source_integrity = source_integrity_for_resume(ctx.store, scrape_task_id)
        source_ok = bool(source_integrity.get("evidence_complete") and source_integrity.get("conclusion") in {"succeeded", "empty"})
        _refresh_run_config()
        if not ctx.claim_resume(run_id):
            return jsonify({
                "ok": False,
                "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        with ctx.lock:
            ctx.tasks[scrape_task_id] = {
                "kind": "scrape", "status": "done", "progress": {}, "logs": [],
                "result": {
                    "ok": source_ok, "jobs": source_jobs,
                    "total_scraped": len(source_jobs), "total_matched": len(source_jobs),
                    "completed_combos": sorted(ctx.store.load_checkpoint(scrape_task_id, "scrape")),
                    "error": "" if source_ok else source_integrity.get("primary_reason", "无法确认是否完成"),
                    "integrity": source_integrity,
                },
                "error": "", "started_at": None, "finished_at": None,
                "stop_event": threading.Event(),
            }
        task_id = run_id
        claimed_task, previous_task = ctx.claim_pipeline_task_id(
            task_id, "ai_screen",
            started_at=_iso_epoch_ms(run.get("started_at")),
        )
        if claimed_task is None:
            ctx.release_resume_claim(run_id)
            return jsonify({
                "ok": False,
                "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        claimed_task["source_task_id"] = scrape_task_id
        claimed_task["resumed_from"] = run_id
        # Spec041：续跑任务继承被续跑 run 的画像身份。
        claimed_task["profile_id"] = str(run.get("profile_id") or "") or None
        resume_params = dict(run.get("execution_params") or {})
        claimed_task["platform"] = identity["platform"]
        claimed_task["cdp_port"] = identity.get("cdp_port")
        claimed_task["profile_key"] = identity.get("profile_key")
        claimed_task["browser_account"] = identity["browser_account"]
        for key in ("platform", "browser_account", "cdp_port", "profile_key"):
            if resume_params.get(key) in (None, ""):
                resume_params[key] = identity.get(key)
        ctx.store.update_screening_execution_params(run_id, resume_params)
        if auto_switch is not None and auto_switch[0]:
            append_account_switch_log_line(
                claimed_task,
                from_account=auto_switch[1], to_account=auto_switch[2])
        start_gate = threading.Event()
        abort_start = threading.Event()
        def run_after_claim_commits(
                task_id, frozen_filters, frozen_profile, source_task_id,
                resume_from_run_id, frozen_facts, execution_config):
            start_gate.wait()
            if not abort_start.is_set():
                ctx.run_ai_screen_task(
                    task_id,
                    frozen_filters,
                    frozen_profile,
                    source_task_id,
                    resume_from_run_id,
                    frozen_facts,
                    execution_config=execution_config,
                )
        try:
            resume_flow_id = str(
                (flow_binding or {}).get("flow_id")
                or (run.get("execution_params") or {}).get("flow_id")
                or ""
            ).strip()
            resume_platform = str(
                (flow_binding or {}).get("platform")
                or run.get("platform")
                or (run.get("execution_params") or {}).get("platform")
                or "boss"
            ).strip().lower()
            future = submit_platform_task(
                ctx, resume_flow_id, resume_platform, run_after_claim_commits,
                task_id,
                run.get("frozen_filters") or {},
                profile_summary,
                scrape_task_id,
                run_id,
                profile_facts,
                refreshed_config,
            )
            if not ctx.store.claim_paused_screening_run(run_id):
                raise RuntimeError("resume_already_claimed")
            if flow_binding is not None:
                # The worker remains behind ``start_gate`` until the exact
                # durable Run claim and Track transition have both succeeded.
                # A failed sync therefore cannot race a running AI worker.
                from webui.flow_task_coordinator import sync_flow_track_for_run

                sync_flow_track_for_run(
                    ctx,
                    run_id,
                    status="running",
                    stage=str(run.get("current_stage") or "ai"),
                )
            ctx.store.append_task_event(run_id, "resume", {
                "backend_version": ctx.backend_version,
                "task_id": task_id,
            })
            with ctx.lock:
                if ctx.tasks.get(task_id) is claimed_task:
                    claimed_task["status"] = "running"
            # Register the Future callback only after all pre-gate durable
            # writes succeed.  Rollback cancellation must not be interpreted
            # as a worker failure that closes the Flow permanently.
            if resume_flow_id:
                attach_ai_future_failure(
                    future,
                    ctx,
                    task_id=task_id,
                    scrape_task_id=scrape_task_id,
                    platform=resume_platform,
                )
        except (sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            ctx.release_resume_claim(run_id)
            reason = "继续任务提交失败，任务保持暂停，请重试"
            try:
                if flow_binding is not None:
                    from webui.flow_task_state import close_flow_task_state

                    close_flow_task_state(
                        ctx,
                        task_id=run_id,
                        scrape_task_id=scrape_task_id,
                        flow_id=flow_binding["flow_id"],
                        profile_id=flow_binding["profile_id"],
                        status="paused",
                        error_code="submit_failed",
                        reason=reason,
                        platform=flow_binding["platform"],
                        stage=str(run.get("current_stage") or "ai"),
                    )
                else:
                    ctx.store.update_screening_run(
                        run_id, status="failed",
                        error_code="submit_failed", error_reason=reason,
                    )
                    ctx.store.append_task_event(run_id, "submission_failed", {
                        "error_code": "submit_failed", "error_reason": reason,
                    })
                from webui.task_finish_whitebox import mark_resume_submission_failed
                mark_resume_submission_failed(
                    ctx.store, run_id, reason, parent_owner_id=scrape_task_id)
            except Exception as marker_exc:
                from webui.logging_setup import get_logger
                get_logger(__name__).warning(
                    "resume submission whitebox marker failed: %s",
                    type(marker_exc).__name__,
                )
            operation_failed = (
                flow_binding is not None
                and isinstance(exc, FlowTaskOperationError)
            )
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed"
                if operation_failed else "resume_submit_failed",
                "error_code": "flow_task_operation_failed"
                if operation_failed else "resume_submit_failed",
                "message": reason,
            }), 503 if operation_failed else 500
        start_gate.set()
        return jsonify({
            "ok": True,
            "run_id": task_id,
            "task_id": task_id,
            "resumed_from": run_id,
            "status": "running",
            "message": "AI 筛选已从断点继续",
            "platform": run.get("platform"),
            "task_input_digest": run.get("task_input_digest"),
        })
    @app.route("/api/task/pause/<run_id>", methods=["POST"])
    def api_task_pause(run_id: str):
        """013：安全暂停 AI 筛选任务（025：支持 mode=immediate 批中立即停止）。
        body 可选 ``{"mode": "immediate" | "graceful"}``，缺省 graceful。
        编排逻辑在 task_pause_support（本文件超行数预警线，api 层只做组装）。
        """
        body = request.get_json(silent=True) or {}
        return pause_with_mode(
            ctx, run_id, str(body.get("mode") or "graceful"))
    @app.route("/api/task/cancel/<run_id>", methods=["POST"])
    def api_task_cancel(run_id: str):
        """FR-024：取消任务，保留已有结果，不自动恢复。"""
        # 收尾族：worker 正在写终态时先等它定稿（通常毫秒级），再按真实状态
        # 处理——已完成的任务不该被改写成「已取消」。
        _finalize_deadline = time.monotonic() + _FINALIZE_WAIT_TIMEOUT_S
        while True:
            with ctx.lock:
                _live_task = ctx.tasks.get(run_id)
            if _live_task is None or not _live_task.get("finalizing"):
                break
            if time.monotonic() >= _finalize_deadline:
                return jsonify({
                    "ok": True, "run_id": run_id, "status": "finalizing",
                    "message": "任务正在收尾，取消未执行",
                }), 200
            time.sleep(0.05)
        with ctx.lock:
            task = ctx.tasks.get(run_id)
        run = ctx.store.get_screening_run(run_id)
        if run is None and task is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        had_durable_run = run is not None
        cleanup_run = dict(run or task or {})
        if run is not None and run.get("status") == "interrupted" and run.get("error_code") == "user_finished":
            return jsonify({
                "ok": False, "error": "already_finished",
                "message": "任务已结束保存，无需取消",
            }), 409
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            MissingPlatformIdentityError,
            persist_task_cancelled,
            resolve_flow_binding,
        )
        try:
            # Resolve the durable Flow identity before touching either the
            # process-local task or its run projections.  The coordinator is
            # the single cancellation write path for legacy and Flow APIs.
            resolve_flow_binding(ctx, run_id)
            persisted_run = persist_task_cancelled(ctx, run_id)
        except MissingPlatformIdentityError:
            return jsonify({
                "ok": False,
                "run_id": run_id,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法取消关联运行线",
            }), 409
        except FlowTaskOperationError:
            return jsonify({
                "ok": False,
                "run_id": run_id,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "关联运行线取消失败，请刷新任务状态后重试",
            }), 503
        cancellation_outcome = (
            persisted_run.pop("_cancel_outcome", None)
            if isinstance(persisted_run, dict) else None
        ) or "cancelled"
        run = ctx.store.get_screening_run(run_id) or persisted_run or run
        if run is None:
            # A legacy task can be cancelled in the pre-persistence window.
            # Keep this response process-local; do not manufacture a durable
            # Run merely to make the public status formatter happy.
            run = {
                **cleanup_run,
                "status": "interrupted",
                "error_code": "user_cancelled",
                "interruption_kind": "user_cancelled",
            }
        with ctx.lock:
            current = ctx.tasks.get(run_id)
            if current is not None:
                stop_event = current.get("stop_event")
                if current.get("status") in {"queued", "running", "paused"} and stop_event is not None:
                    request_stop(current, stop_event, STOP_MODE_CANCEL)
                current["status"] = _public_task_status(
                    run["status"], run.get("interruption_kind"))
                if cancellation_outcome != "not_required":
                    current["error"] = "用户已取消"
        if cancellation_outcome != "not_required" and (had_durable_run or persisted_run is not None):
            append_task_event_best_effort(
                ctx.store, run_id, "cancel", {"by": "user"}, logger=_logger,
            )
        _parent_scrape = str(((run or {}).get("execution_params") or {}).get("scrape_task_id") or "")
        ctx.clear_auto_screen(run_id)
        if _parent_scrape and _parent_scrape != run_id:
            ctx.clear_auto_screen(_parent_scrape)
        cleanup = None
        if run is not None or task is not None:
            from webui import pipeline_exec as _facade
            from webui.frozen_browser_identity import close_frozen_run_browser
            cleanup = close_frozen_run_browser(
                ctx.store, cleanup_run,
                accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
                activate=_facade.set_active_cdp_data_dir,
                close=_facade.close_debug_chrome,
            )
            if not cleanup.ok:
                append_task_event_best_effort(
                    ctx.store, run_id, "browser_cleanup_failed",
                    cleanup.as_dict(), logger=_logger,
                    context="browser cleanup audit event write failed",
                )
                with ctx.lock:
                    current = ctx.tasks.get(run_id)
                    if current is not None:
                        current["error"] = "用户已取消，但浏览器清理失败"
        cancel_task_cleanup(ctx, run_id)
        cleanup_payload = cleanup.as_dict() if cleanup is not None else None
        cleanup_failed = cleanup is not None and not cleanup.ok
        return jsonify({
            "ok": not cleanup_failed,
            **({"error": "browser_cleanup_failed"} if cleanup_failed else {}),
            "run_id": run_id,
            "platform": (run or {}).get("platform"),
            "status": (
                _public_task_status(run["status"], run.get("interruption_kind")) if run is not None else "cancelled"
            ),
            "cancellation": cancellation_outcome,
            "processed_count": int((run or {}).get("processed_count") or 0),
            "message": "任务已取消，已有结果保留",
            "cleanup": cleanup_payload,
        })
    from webui.task_continue_finish import register_finish_route
    register_finish_route(app, ctx, sys.modules[__name__])
