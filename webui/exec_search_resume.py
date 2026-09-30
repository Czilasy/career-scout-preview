"""断点续抓 HTTP 路由的内部实现。

047：将执行搜索门面中的恢复/续跑职责单独承载，保持原路由、参数和
``ctx.continue_execute_search`` 注入契约；该模块不改变产品行为。
"""
from __future__ import annotations

import sqlite3
import threading

from flask import jsonify, request

from webui.constants import _MSG_TASK_ALREADY_RUNNING
from webui.error_registry import (
    ALIAS_TO_CODE,
    ERROR_TAXONOMY,
    is_recoverable_systemic_block,
    resolve_code,
)
from webui.exec_search_whitebox import mark_scrape_submission_failed
from webui.logging_setup import get_logger
from webui.pipeline_exec_status import user_visible_failure_reason
from webui.flow_service import submit_platform_task
from webui.flow_future import attach_scrape_future_failure
from webui.flow_task_coordinator import (
    FlowTaskOperationError,
    MissingPlatformIdentityError,
    resolve_flow_binding,
    sync_flow_track_for_run,
)
from webui.flow_task_state import close_flow_task_state
from webui.resume_identity import (
    activate_frozen_identity_candidate,
    append_account_switch_log_line,
    commit_continue_identity,
    invalidate_login_cache_for_resume,
    prepare_continue_identity,
)
from webui.task_pause_support import (
    ScrapeCheckpointReadError,
    normalize_recoverable_failed_run,
    scrape_checkpoint,
)
from webui.task_runners import _iso_epoch_ms
from webui.task_status import _public_task_status, is_resume_eligible_run

_logger = get_logger(__name__)


def _is_recoverable_resume_failure(code: object) -> bool:
    """Resume-time source failures stay paused, including non-systemic retries."""
    canonical = resolve_code(code, default="")
    taxonomy = ERROR_TAXONOMY.get(canonical) or {}
    return bool(
        is_recoverable_systemic_block(canonical)
        or (
            taxonomy.get("category") == "source"
            and taxonomy.get("retryable")
        )
    )


def _public_failure_details(
        code: object, diagnostic: object = "", platform: str = "",
) -> tuple[str, str]:
    """Return a canonical code and safe public reason for one failure."""
    raw_code = str(code or "").strip()
    canonical = raw_code
    if raw_code.startswith("source_") or raw_code in ALIAS_TO_CODE:
        canonical = resolve_code(raw_code, default="source_unknown_error")
    is_source = canonical.startswith("source_")
    if not is_source:
        return canonical, str(diagnostic or raw_code or "")
    reason = user_visible_failure_reason(canonical, "", platform)
    return canonical, reason


def register_search_continue(app, ctx):
    @app.route("/api/execute-search/continue/<old_task_id>", methods=["POST"])
    def continue_execute_search(old_task_id, _block_checked=False,
                                account_switch_note=None):
        """断点续抓：从上次失败的组合接着跑，跳过已完成的组合。
        切片4：支持 paused 状态继续（FR-020）。优先从 DB checkpoint 恢复
        completed_combos（服务重启后内存丢失也能恢复），回退到内存 task.result。
        同时检查阻断是否解除（如登录已恢复、验证码已过）。
        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp
        with ctx.lock:
            old_task = ctx.tasks.get(old_task_id)
            old_snapshot = dict(old_task) if old_task else None
        db_run = None
        try:
            db_run = ctx.store.get_screening_run(old_task_id)
        except ctx.operational_errors:
            db_run = None
        if old_snapshot is None and db_run is None:
            return jsonify({"ok": False, "error": "原任务不存在或已过期"}), 404
        try:
            flow_binding = resolve_flow_binding(ctx, old_task_id)
        except MissingPlatformIdentityError:
            return jsonify({
                "ok": False,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法继续关联运行线",
                "status": "paused",
            }), 409
        except FlowTaskOperationError:
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "任务关联运行线无效，请刷新后重试",
                "status": "paused",
            }), 409
        snapshot_params = (old_snapshot or {}).get("execution_params") or {}
        if flow_binding is None and str(snapshot_params.get("flow_id") or "").strip():
            if not str(snapshot_params.get("platform") or "").strip():
                return jsonify({
                    "ok": False,
                    "error": "platform_identity_missing",
                    "error_code": "platform_identity_missing",
                    "message": "任务缺少平台身份，无法继续关联运行线",
                    "status": "paused",
                }), 409
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "任务关联运行线无效，请刷新后重试",
                "status": "paused",
            }), 409
        if flow_binding is not None and db_run is not None:
            # A historical screening row may have defaulted its platform
            # column to BOSS.  Flow resume must use the durable Track identity
            # for both login activation and the platform execution lane.
            db_run = dict(db_run)
            flow_params = dict(db_run.get("execution_params") or {})
            flow_params.update({
                "flow_id": flow_binding["flow_id"],
                "track_id": flow_binding["track_id"],
                "platform": flow_binding["platform"],
            })
            db_run["execution_params"] = flow_params
            db_run["platform"] = flow_binding["platform"]
        mem_status = old_snapshot.get("status") if old_snapshot else None
        db_status = db_run.get("status") if db_run else None
        effective_status = db_status or mem_status
        if not is_resume_eligible_run(db_run or old_snapshot):
            return jsonify({
                "ok": False,
                "error": "not_paused",
                "status": _public_task_status(effective_status),
                "message": "只有 paused 状态的任务才能继续",
            }), 409

        def _finish_resume_failure(
                error_code, reason, exception=None, *, platform=""):
            """Keep recoverable resume blocks paused; fail only terminal errors."""
            code, message = _public_failure_details(
                error_code or "internal_error", reason, platform,
            )
            message = message or code
            recoverable = _is_recoverable_resume_failure(code)
            try:
                if flow_binding is not None:
                    close_flow_task_state(
                        ctx,
                        task_id=old_task_id,
                        scrape_task_id=str(
                            ((db_run or {}).get("execution_params") or {}).get(
                                "scrape_task_id"
                            ) or old_task_id
                        ).strip() or None,
                        flow_id=flow_binding["flow_id"],
                        profile_id=flow_binding["profile_id"],
                        status="paused" if recoverable else "failed",
                        error_code=code,
                        reason=message,
                        platform=flow_binding["platform"],
                        stage="scrape",
                    )
                else:
                    ctx.write_run(
                        old_task_id,
                        status="paused" if recoverable else "failed",
                        current_stage="scrape",
                        error_code=code,
                        error_reason=message,
                    )
            except ctx.operational_errors as persist_exc:
                _logger.warning(
                    "续跑失败状态写入失败 error_type=%s",
                    type(persist_exc).__name__,
                )
            except Exception as persist_exc:
                _logger.warning(
                    "Flow续跑失败状态收口失败 error_type=%s",
                    type(persist_exc).__name__,
                )
            recorder = getattr(ctx, "record_pause_failure", None)
            if callable(recorder):
                try:
                    recorder(
                        old_task_id, "scrape", code, message,
                        exception=exception, include_traceback=exception is not None,
                    )
                except Exception:
                    _logger.warning("续跑失败审计写入失败", exc_info=True)
            with ctx.lock:
                current = ctx.tasks.get(old_task_id)
                if current is not None:
                    current["status"] = "paused" if recoverable else "failed"
                    current["error"] = message
            if not recoverable:
                ctx.clear_auto_screen(old_task_id)
            ctx.schedule_pipeline_task_cleanup(old_task_id)
            ctx.release_worker_resume_claims(ctx.tasks.get(old_task_id))
            return code, message, "paused" if recoverable else "failed"

        resume_snapshot = old_snapshot.get("result") if old_snapshot else {}
        resume_completed = (
            resume_snapshot.get("completed_combos")
            if isinstance(resume_snapshot, dict) else None
        )
        if db_run is not None and db_run.get("status") == "failed":
            db_run = normalize_recoverable_failed_run(ctx, db_run)
        resume_identity = None
        resume_auto_switch = None
        resume_run = db_run or old_snapshot
        if db_run is not None and not _block_checked:
            continue_body = request.get_json(silent=True) or {}
            target_account = (
                str(continue_body.get("target_account") or "").strip()
                if isinstance(continue_body, dict) else ""
            )
            plan = prepare_continue_identity(
                ctx.store,
                db_run,
                target_account=target_account,
                current_account=ctx.load_legacy_advanced_settings,
                fallback_account=lambda: ctx.account_for_run(db_run),
                accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
            )
            if plan["status"] != "ok":
                return jsonify(plan["body"]), plan["http_status"]
            resume_identity = plan["identity"]
            resume_auto_switch = plan.get("auto_switch")
            activation = activate_frozen_identity_candidate(
                ctx.activate_run_browser, db_run, resume_identity,
            )
            if not activation["ok"]:
                code, reason, resume_status = _finish_resume_failure(
                    activation.get("error_code") or activation.get("error")
                    or "source_cdp_unavailable",
                    "", exception=None,
                    platform=str(resume_identity.get("platform") or ""),
                )
                return jsonify({
                    "ok": False,
                    "error": code,
                    "error_code": code,
                    "status": resume_status,
                    "message": reason,
                    "error_reason": reason,
                }), 409
            resume_run = activation["run"]
        if resume_run is not None and not _block_checked:
            try:
                if resume_identity is None:
                    ctx.activate_run_browser(resume_run)
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                platform = str(
                    resume_run.get("platform")
                    or (resume_run.get("execution_params") or {}).get("platform")
                    or ""
                )
                code, reason, resume_status = _finish_resume_failure(
                    getattr(exc, "error_code", "")
                    or getattr(exc, "failed_code", "")
                    or "source_cdp_unavailable",
                    "", exception=exc, platform=platform,
                )
                return jsonify({
                    "ok": False,
                    "error": code,
                    "error_code": code,
                    "status": resume_status,
                    "message": reason,
                    "error_reason": reason,
                }), 409
            resume_params = resume_run.get("execution_params") or {}
            invalidate_login_cache_for_resume(
                str(resume_params.get("browser_account") or ""),
                str(resume_run.get("platform") or resume_params.get("platform") or ""),
            )
            passed, code, reason = ctx.check_resume_block(resume_run)
            if not passed:
                platform = str(
                    resume_run.get("platform")
                    or (resume_run.get("execution_params") or {}).get("platform")
                    or ""
                )
                code, reason, resume_status = _finish_resume_failure(
                    code, reason, platform=platform,
                )
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": resume_status,
                }), 409
        try:
            # The fresh browser/login checks above must pass before the strict
            # checkpoint read.  A corrupt payload is not equivalent to an
            # empty checkpoint: reject before identity commit, claims, or
            # worker submission so resume can never restart from zero or
            # overwrite the raw bytes.
            completed = set(scrape_checkpoint(
                ctx, old_task_id, completed_combos=resume_completed,
            ))
        except ScrapeCheckpointReadError as exc:
            return jsonify({
                "ok": False,
                "error": exc.error_code,
                "error_code": exc.error_code,
                "error_reason": exc.public_reason,
                "message": exc.public_reason,
                "status": "failed",
            }), 409
        if resume_identity is not None:
            try:
                commit_continue_identity(
                    ctx.store, old_task_id, resume_identity, resume_auto_switch,
                )
                db_run = ctx.store.get_screening_run(old_task_id) or resume_run
                resume_run = db_run
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
        if account_switch_note is None and resume_auto_switch is not None \
                and resume_auto_switch[0]:
            account_switch_note = (
                resume_auto_switch[1], resume_auto_switch[2],
            )
        script_params = (old_snapshot or {}).get("script_params")
        if not script_params and db_run:
            try:
                ep = db_run.get("execution_params") or {}
                script_params = ep.get("script_params") or ep
            except (AttributeError, TypeError):
                script_params = None
        if not script_params:
            return jsonify({"ok": False, "error": "原任务参数丢失，无法继续"}), 400
        try:
            old_jobs = ctx.store.load_scrape_run_jobs(old_task_id)
        except ctx.operational_errors:
            old_jobs = []
        if not old_jobs and old_snapshot:
            old_result = old_snapshot.get("result") or {}
            old_jobs = old_result.get("jobs") or []
        if not ctx.claim_resume(old_task_id):
            return jsonify({
                "ok": False, "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        task_id = old_task_id
        claimed_task, previous_task = ctx.claim_pipeline_task_id(
            task_id, "scrape",
            started_at=_iso_epoch_ms((db_run or {}).get("started_at")),
        )
        if claimed_task is None:
            ctx.release_resume_claim(old_task_id)
            return jsonify({
                "ok": False, "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        if account_switch_note:
            append_account_switch_log_line(
                claimed_task,
                from_account=account_switch_note[0],
                to_account=account_switch_note[1])
        db_ep = (db_run or {}).get("execution_params") or {}
        from webui.execution_config import (
            ExecutionConfigSnapshot,
            FrozenTaskScope,
        )
        resume_config = None
        resume_scope = None
        try:
            if db_ep.get("execution_config"):
                resume_config = ExecutionConfigSnapshot.from_dict(db_ep["execution_config"])
            if db_ep.get("frozen_scope"):
                resume_scope = FrozenTaskScope.from_dict(db_ep["frozen_scope"])
        except (KeyError, TypeError, ValueError):
            resume_config = None
            resume_scope = None
        with ctx.lock:
            task = ctx.tasks[task_id]
            task["skip_combos"] = completed
            task["old_jobs"] = old_jobs
            task["resuming_from"] = old_task_id
            task["browser_account"] = (
                db_ep.get("browser_account") or ctx.account_for_run(db_run)
            )
            task["platform"] = (
                (flow_binding or {}).get("platform")
                or db_ep.get("platform")
                or (db_run or {}).get("platform")
                or "boss"
            )
            task["cdp_port"] = db_ep.get("cdp_port")
            task["profile_key"] = db_ep.get("profile_key")
            task["task_input_digest"] = db_ep.get("task_input_digest")
            task["auto_screen"] = bool(db_ep.get("auto_screen"))
        start_gate = threading.Event()
        abort_start = threading.Event()
        def run_after_claim_commits():
            start_gate.wait()
            if not abort_start.is_set():
                ctx.run_pipeline_task(task_id, script_params, resume_config, resume_scope)
        try:
            resume_flow_id = str(
                (flow_binding or {}).get("flow_id")
                or db_ep.get("flow_id")
                or ""
            ).strip()
            resume_platform = str(
                (flow_binding or {}).get("platform")
                or db_ep.get("platform")
                or (db_run or {}).get("platform")
                or (resume_run or {}).get("platform")
                or "boss"
            ).strip().lower()
            if resume_flow_id and not resume_platform:
                raise MissingPlatformIdentityError(
                    f"Flow resume {old_task_id} has no platform identity"
                )
            future = submit_platform_task(
                ctx, resume_flow_id, resume_platform, run_after_claim_commits,
            )
            if db_run is not None:
                ctx.store.append_task_event(old_task_id, "resume", {"task_id": task_id})
                if not ctx.store.claim_paused_screening_run(old_task_id):
                    raise RuntimeError("resume_already_claimed")
            if flow_binding is not None:
                sync_flow_track_for_run(
                    ctx,
                    old_task_id,
                    status="running",
                    stage="scrape",
                )
            # Register the Future failure closure only after the durable Run
            # claim and exact Track sync have both succeeded.  Rollback paths
            # cancel the still-gated Future; registering earlier would turn
            # that intentional cancellation into a terminal Flow failure.
            attach_scrape_future_failure(
                future,
                ctx,
                task_id=task_id,
                flow_id=resume_flow_id or None,
                platform=resume_platform,
                profile_id=str((db_run or resume_run or {}).get("profile_id") or "").strip() or None,
            )
            with ctx.lock:
                if ctx.tasks.get(task_id) is claimed_task:
                    claimed_task["status"] = "running"
        except MissingPlatformIdentityError:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            ctx.release_resume_claim(old_task_id)
            return jsonify({
                "ok": False,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法继续关联运行线",
            }), 409
        except FlowTaskOperationError as exc:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            ctx.release_resume_claim(old_task_id)
            try:
                close_flow_task_state(
                    ctx,
                    task_id=old_task_id,
                    scrape_task_id=old_task_id,
                    flow_id=flow_binding["flow_id"],
                    profile_id=flow_binding["profile_id"],
                    status="paused",
                    error_code="resume_track_sync_failed",
                    reason="继续任务运行线状态未能保存，请重试",
                    platform=flow_binding["platform"],
                    stage="scrape",
                )
            except Exception as closure_exc:
                _logger.warning(
                    "Flow续跑运行线回滚失败 error_type=%s",
                    type(closure_exc).__name__,
                )
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "继续任务运行线状态保存失败，请重试",
            }), 503
        except (sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            ctx.release_resume_claim(old_task_id)
            reason = "继续任务提交失败，任务已结束"
            try:
                if flow_binding is not None:
                    # External submission is retryable.  Restore the exact
                    # Flow Run/Track to paused instead of making a transient
                    # lane failure terminal.
                    close_flow_task_state(
                        ctx,
                        task_id=old_task_id,
                        scrape_task_id=old_task_id,
                        flow_id=flow_binding["flow_id"],
                        profile_id=flow_binding["profile_id"],
                        status="paused",
                        error_code="submit_failed",
                        reason=reason,
                        platform=flow_binding["platform"],
                        stage="scrape",
                    )
                else:
                    ctx.store.update_screening_run(
                        task_id, status="failed", error_code="submit_failed", error_reason=reason)
                mark_scrape_submission_failed(ctx.store, task_id, script_params, reason, stage="resume_submit", pages=int(resume_scope.pages_per_combination) if resume_scope else 0)
            except Exception as _marker_exc:
                _logger.warning("继续任务提交失败白箱记录失败: %s", type(_marker_exc).__name__)
            return jsonify({
                "ok": False, "error": "resume_submit_failed",
                "message": "继续任务提交失败，任务已结束",
            }), 500
        start_gate.set()
        return jsonify({"ok": True, "task_id": task_id,
                        "skipped": len(completed), "old_jobs": len(old_jobs),
                        "resumed_from": old_task_id})
    ctx.continue_execute_search = continue_execute_search
    return continue_execute_search
