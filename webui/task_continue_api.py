"""任务续跑 / 暂停 / 取消 / 结束 API 路由（021 B6 T019 外迁自 webui/app.py）。
paused/中断 run 的续跑分发（抓取/筛选/重抓三向）、用户暂停与取消、
结束任务的部分快照定稿。路由体纯搬运：HTTP 契约零改动。
"""
from __future__ import annotations
import threading
import sqlite3
import time
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

_logger = get_logger(__name__)

#: 结束保存等待"当前 JD 批次"收尾的上限：单批 15~30 条 × 8~15 秒 + 余量。
_FINISH_BATCH_WAIT_TIMEOUT_S = 15 * 60
#: 批内信号清除发生在批次结果合并/断点落盘之前，清标记后再给一拍余量。
_FINISH_BATCH_SETTLE_GRACE_S = 1.5
#: 收尾区守卫：worker 正在写终态时的等待上限（收尾通常毫秒级，超时即回执）。
_FINALIZE_WAIT_TIMEOUT_S = 5.0


def _jd_batch_active(ctx, task_id: str) -> bool:
    """该任务的实时进度是否正处于 JD 抓取批次内（批内信号由 runner 上报）。"""
    with ctx.lock:
        task = ctx.tasks.get(task_id)
        progress = dict((task or {}).get("progress") or {})
    if str(progress.get("stage") or "") not in ("fetch_jd", "jd_detail"):
        return False
    batch = progress.get("jd_batch")
    return isinstance(batch, dict) and bool(batch)


def _wait_for_jd_batch_settle(ctx, task_id: str) -> bool:
    """结束保存前等当前 JD 批次返回并落盘；返回是否真的等待过。

    批次结果在 ``fetch_job_details`` 返回后才写入 JD 断点，直接结束保存会把
    这一整批已抓内容丢掉。停止信号已提前下达，批次返回后不再开新批，等待
    是协作式的，不会无限拖住（有上限）。
    """
    if not _jd_batch_active(ctx, task_id):
        return False
    deadline = time.monotonic() + _FINISH_BATCH_WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        if not _jd_batch_active(ctx, task_id):
            break
        time.sleep(0.5)
    time.sleep(_FINISH_BATCH_SETTLE_GRACE_S)
    return True


def register_task_continue_routes(app, ctx):
    def _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map, profile_summary,
            source_dropped=None, total_scraped=None, platform="",
            profile_facts=None, unfiltered=False):
        """Build a displayable result snapshot from persisted partial work."""
        pending_reasons = {}
        pending_codes = {}
        for item in pending_rows or []:
            jid = str(item.get("job_id") or "")
            if not jid:
                continue
            payload = item.get("ai_payload") or {}
            pending_reasons[jid] = str(
                payload.get("reason") or item.get("failed_code") or "")
            pending_codes[jid] = str(item.get("failed_code") or "")
        jobs = []
        dropped = []
        for job in source_jobs or []:
            if not isinstance(job, dict):
                continue
            jid = str(job.get("job_id") or job.get("source_url") or "")
            vobj = verdicts.get(jid) or {}
            verdict = str(vobj.get("verdict") or "")
            reason = str(vobj.get("reason") or job.get("verdict_reason") or "")
            if verdict == "dropped":
                dropped.append({
                    "platform": platform,
                    "platform_job_id": str(job.get("platform_job_id") or jid),
                    "job_id": str(job.get("job_id") or "") or None,
                    "title": job.get("title") or "", "reason": reason or "粗筛移除",
                    "canonical_url": job.get("source_url") or job.get("job_link") or "",
                })
                continue
            jd = str(jd_map.get(jid) or job.get("jd") or "").strip()
            caveats = (
                vobj.get("caveats") if isinstance(vobj.get("caveats"), list)
                else (job.get("caveats") if isinstance(job.get("caveats"), list) else [])
            )
            flags = (
                vobj.get("flags") if isinstance(vobj.get("flags"), list)
                else (job.get("flags") if isinstance(job.get("flags"), list) else [])
            )
            if verdict in ("match", "not_match", "mismatch"):
                final_verdict = "not_match" if verdict == "mismatch" else verdict
                final_reason = reason
            elif jd:
                final_verdict = "uncertain"
                final_reason = reason or "已抓取 JD，精筛未完成（提前结束）"
            else:
                final_verdict = "uncertain"
                final_reason = (
                    pending_reasons.get(jid)
                    or reason
                    or "未开始抓取 JD（提前结束）"
                )
            jobs.append({
                "platform": platform,
                "platform_job_id": str(job.get("platform_job_id") or jid),
                "job_id": str(job.get("job_id") or "") or None,
                "title": job.get("title") or "",
                "company": job.get("company") or job.get("boss_name") or "",
                "salary": job.get("salary") or "",
                "location": job.get("location") or "",
                "tags": job.get("tags") or "",
                "jd": jd,
                "source_url": job.get("source_url") or job.get("job_link") or "",
                "verdict": final_verdict,
                "verdict_reason": final_reason,
                "caveats": caveats,
                "flags": flags,
                "failed_code": pending_codes.get(jid) or "",
            })
        dropped_ids = {str(item.get("platform_job_id") or item.get("job_id") or "") for item in dropped}
        for item in source_dropped or []:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("platform_job_id") or item.get("job_id") or item.get("source_url") or "")
            if jid and jid in dropped_ids:
                continue
            dropped.append({
                "platform": platform,
                "platform_job_id": str(item.get("platform_job_id") or jid),
                "job_id": str(item.get("job_id") or "") or None,
                "title": item.get("title") or "",
                "reason": item.get("reason") or item.get("verdict_reason") or "粗筛移除",
                "canonical_url": item.get("canonical_url") or item.get("source_url") or "",
            })
        return {
            "ok": True,
            "jobs": jobs,
            "dropped": dropped,
            "total_scraped": (
                total_scraped if total_scraped is not None
                else len(source_jobs or []) + len(source_dropped or [])
            ),
            "total_kept": 0 if unfiltered else len(jobs),
            "total_matched": sum(1 for j in jobs if j.get("verdict") == "match"),
            "total_dropped": len(dropped),
            "profile_summary": profile_summary or "",
            "profile_facts": profile_facts,
            "error": "",
        }
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
                "detail": type(exc).__name__,
            }), 503
        except (KeyError, ValueError) as exc:
            return jsonify({
                "ok": False,
                "error": "resume_identity_persist_failed",
                "message": "继续任务身份未能保存，任务保持暂停，请重试",
                "status": "paused",
                "detail": type(exc).__name__,
            }), 503
        refreshed_config = None
        def _refresh_run_config():
            nonlocal refreshed_config
            refreshed_config = _refresh_paused_run_execution_config(run, ctx.store)
            if refreshed_config is not None:
                run["execution_params"]["execution_config"] = refreshed_config.to_dict()
        if continue_kind == "recrawl":
            _refresh_run_config()
            return ctx.continue_recrawl(
                run_id, _block_checked=True,
                account_switch_note=(
                    (auto_switch[1], auto_switch[2])
                    if auto_switch is not None and auto_switch[0] else None))
        if continue_kind == "scrape":
            # Scrape resumes the immutable execution snapshot captured by
            # this run.  Unlike AI/recrawl, changing current advanced
            # settings must not alter the search pacing or frozen scope of a
            # partially completed platform crawl.
            return ctx.continue_execute_search(
                run_id, _block_checked=True,
                account_switch_note=(
                    (auto_switch[1], auto_switch[2])
                    if auto_switch is not None and auto_switch[0] else None))
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
            future = ctx.executor.submit(
                run_after_claim_commits,
                task_id,
                run.get("frozen_filters") or {},
                profile_summary,
                scrape_task_id,
                run_id,
                profile_facts,
                refreshed_config,
            )
            ctx.store.append_task_event(run_id, "resume", {
                "backend_version": ctx.backend_version,
                "task_id": task_id,
            })
            if not ctx.store.claim_paused_screening_run(run_id):
                raise RuntimeError("resume_already_claimed")
            with ctx.lock:
                if ctx.tasks.get(task_id) is claimed_task:
                    claimed_task["status"] = "running"
        except (sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            ctx.release_resume_claim(run_id)
            reason = "继续任务提交失败，原任务已结束"
            try:
                ctx.store.update_screening_run(run_id, status="failed",
                                               error_code="submit_failed", error_reason=reason)
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
            return jsonify({
                "ok": False,
                "error": "resume_submit_failed",
                "message": reason,
            }), 500
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
            if task is not None and task.get("status") in {
                "queued", "running", "paused",
            }:
                stop_event = task.get("stop_event")
                if stop_event is not None:
                    request_stop(task, stop_event, STOP_MODE_CANCEL)
        run = ctx.store.get_screening_run(run_id)
        if run is None and task is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        cleanup_run = dict(run or task or {})
        if run is not None and run.get("status") == "interrupted" and run.get("error_code") == "user_finished":
            return jsonify({
                "ok": False, "error": "already_finished",
                "message": "任务已结束保存，无需取消",
            }), 409
        if run is not None:
            try:
                if run["status"] not in (
                    "succeeded", "partial", "failed", "interrupted",
                ):
                    ctx.store.update_screening_run(
                        run_id, status="cancelled",
                        error_code="user_cancelled",
                        error_reason="用户已取消",
                    )
                    ctx.store.save_interruption_kind(run_id, "user_cancelled")
                    append_task_event_best_effort(
                        ctx.store, run_id, "cancel", {"by": "user"},
                        logger=_logger,
                    )
            except ValueError as exc:
                latest = ctx.store.get_screening_run(run_id)
                if latest is None or latest.get("status") not in (
                    "succeeded", "partial", "failed", "interrupted",
                ):
                    return jsonify({
                        "ok": False,
                        "error": "cancel_state_conflict",
                        "detail": type(exc).__name__,
                    }), 409
            except ctx.operational_errors as exc:
                latest = ctx.store.get_screening_run(run_id)
                if latest is not None and latest.get("status") in (
                    "succeeded", "partial", "failed", "interrupted",
                ):
                    with ctx.lock:
                        current = ctx.tasks.get(run_id)
                        if current is not None:
                            current["status"] = _public_task_status(
                                latest["status"], latest.get("interruption_kind"))
                            current["error"] = "用户已取消"
                return jsonify({
                    "ok": False,
                    "error": "cancel_persistence_failed",
                    "detail": type(exc).__name__,
                }), 503
            run = ctx.store.get_screening_run(run_id)
        with ctx.lock:
            current = ctx.tasks.get(run_id)
            if current is not None:
                current["status"] = (
                    _public_task_status(run["status"], run.get("interruption_kind"))
                    if run is not None else "cancelled"
                )
                current["error"] = "用户已取消"
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
            "processed_count": int((run or {}).get("processed_count") or 0),
            "message": "任务已取消，已有结果保留",
            "cleanup": cleanup_payload,
        })
    @app.route("/api/task/finish/<run_id>", methods=["POST"])
    def api_task_finish(run_id: str):
        """T416: 结束可恢复任务并生成可展示的部分结果快照。
        允许 queued/running/paused/failed 以及 interrupted(process_restart/
        operator_stop)；user_cancelled 是终态，不能通过 finish 改写。
        """
        run = ctx.store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        # 收尾族：worker 正在写终态时先等它定稿，再按真实状态判断——已完成的
        # 任务不生成假的部分快照（旧行为会把成功轮结果顶成旧快照）。
        _finalize_deadline = time.monotonic() + _FINALIZE_WAIT_TIMEOUT_S
        while True:
            with ctx.lock:
                _live_task = ctx.tasks.get(run_id)
            if _live_task is None or not _live_task.get("finalizing"):
                break
            if time.monotonic() >= _finalize_deadline:
                return jsonify({
                    "ok": False, "error": "finalizing",
                    "message": "任务正在收尾，请稍候重试",
                }), 409
            time.sleep(0.05)
        run = ctx.store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        interruption_kind = run.get("interruption_kind") or ""
        if run["status"] == "interrupted" and run.get("error_code") == "user_finished":
            return jsonify({
                "ok": False, "error": "already_finished",
                "message": "任务已结束保存，请勿重复操作",
            }), 409
        if run["status"] == "interrupted" and interruption_kind == "user_cancelled":
            return jsonify({
                "ok": False, "error": "user_cancelled",
                "message": "用户已取消的任务不能通过 finish 改写",
            }), 409
        if run["status"] == "interrupted" and interruption_kind not in (
                "process_restart", "operator_stop",
        ):
            return jsonify({
                "ok": False, "error": "interrupted_not_restartable",
                "message": "该中断状态不能结束保存",
            }), 409
        if run["status"] in ("succeeded", "partial"):
            return jsonify({
                "ok": False, "error": "already_terminal",
                "status": _public_task_status(run["status"], run.get("interruption_kind")),
                "message": "任务已完成，无需结束保存",
            }), 409
        allowed_finish_statuses = {
            "queued", "running", "paused", "failed", "interrupted",
        }
        if run["status"] not in allowed_finish_statuses:
            return jsonify({
                "ok": False, "error": "not_paused",
                "status": _public_task_status(run["status"], run.get("interruption_kind")),
                "message": "当前任务状态不能结束并保存",
            }), 409
        params = run.get("execution_params") or {}
        scrape_task_id = str(params.get("scrape_task_id") or "")
        source_run_id = str(params.get("source_run_id") or "")
        platform = params.get("platform") or run.get("platform") or "boss"
        source_jobs = []
        verdicts = {}
        pending_rows = []
        jd_map = {}
        source_payload = None
        source_dropped = []
        source_total_scraped = None
        try:
            ctx.store.save_interruption_kind(run_id, "operator_stop")
        except ctx.operational_errors:
            pass
        flush_run_id = scrape_task_id or (
            run_id if str(run.get("current_stage") or "") == "scrape" else ""
        )
        if flush_run_id:
            with ctx.lock:
                task = ctx.tasks.get(flush_run_id)
                stop_event = task.get("stop_event") if task is not None else None
                flush_lock = task.get("page_flush_lock") if task is not None else None
            if stop_event is not None:
                request_stop(task, stop_event, STOP_MODE_FINISH)
            if flush_lock is not None:
                stable_since = time.monotonic()
                last_seq = None
                flush_deadline = time.monotonic() + 3.0
                while time.monotonic() < flush_deadline:
                    with ctx.lock:
                        task = ctx.tasks.get(flush_run_id)
                        seq = int((task or {}).get("page_persist_seq") or 0) if task is not None else 0
                    if seq != last_seq:
                        last_seq = seq
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= 0.2:
                        break
                    time.sleep(0.05)
                if flush_lock.acquire(timeout=3.0):
                    flush_lock.release()
        # 结束保存：先把停止信号交给本任务，再按需等当前 JD 批次收尾——批次
        # 结果会在返回后落进断点，等它落盘再取快照，数据更全（wait_for_batch）。
        with ctx.lock:
            own_task = ctx.tasks.get(run_id)
            own_stop_event = own_task.get("stop_event") if own_task is not None else None
        if own_stop_event is not None:
            request_stop(own_task, own_stop_event, STOP_MODE_FINISH)
        wait_for_batch = bool(
            (request.get_json(silent=True) or {}).get("wait_for_batch")
        )
        waited_for_batch = _wait_for_jd_batch_settle(ctx, run_id) if wait_for_batch else False
        if scrape_task_id:
            try:
                source_jobs = ctx.store.load_scrape_run_jobs(scrape_task_id)
            except ctx.operational_errors:
                source_jobs = []
            verdicts = ctx.store.load_screening_verdicts(run_id)
            pending_rows = ctx.store.load_screening_pending(run_id)
            try:
                jd_map = ctx.load_jd_checkpoint(
                    ctx.jd_checkpoint_path(app.config["RESULT_DIR"], run_id))
            except RuntimeError as exc:
                return jsonify({
                    "ok": False, "error": str(exc),
                    "message": "JD 断点文件损坏，无法生成部分结果",
                }), 503
        elif source_run_id:
            payload = ctx.store.load_latest_pipeline_result(source_run_id)
            source_payload = payload
            source_jobs = ((payload or {}).get("result") or {}).get("jobs") or []
            source_dropped = ((payload or {}).get("result") or {}).get("dropped") or []
            source_total_scraped = ((payload or {}).get("result") or {}).get("total_scraped") or None
            verdicts = ctx.store.load_screening_verdicts(source_run_id)
            pending_rows = ctx.store.load_screening_pending(run_id)
            if not pending_rows:
                pending_rows = ctx.store.load_screening_pending(source_run_id)
        elif not scrape_task_id and not source_run_id and str(run.get("current_stage") or "") == "scrape":
            try:
                source_jobs = ctx.store.load_scrape_run_jobs(run_id)
            except ctx.operational_errors:
                source_jobs = []
            verdicts = ctx.store.load_screening_verdicts(run_id)
            pending_rows = ctx.store.load_screening_pending(run_id)
        profile_summary = str(params.get("profile_summary") or "")
        if not profile_summary and source_run_id:
            if source_payload is None:
                source_payload = ctx.store.load_latest_pipeline_result(source_run_id)
            profile_summary = str(((source_payload or {}).get("result") or {}).get("profile_summary") or "")
        parent_scrape_task_id = scrape_task_id
        if not parent_scrape_task_id:
            parent_scrape_task_id = (
                run_id if str(run.get("current_stage") or "") == "scrape" else ""
            )
        profile_facts = params.get("profile_facts")
        if not isinstance(profile_facts, dict) or not profile_facts:
            profile_facts = None
            if scrape_task_id:
                try:
                    parent_run = ctx.store.get_screening_run(scrape_task_id)
                except ctx.operational_errors:
                    parent_run = None
                parent_facts = ((parent_run or {}).get("execution_params") or {}).get("profile_facts")
                if isinstance(parent_facts, dict) and parent_facts:
                    profile_facts = parent_facts
            if profile_facts is None and source_run_id:
                if source_payload is None:
                    source_payload = ctx.store.load_latest_pipeline_result(source_run_id)
                source_facts = ((source_payload or {}).get("result") or {}).get("profile_facts")
                if isinstance(source_facts, dict) and source_facts:
                    profile_facts = source_facts
        with ctx.lock:
            task = ctx.tasks.get(run_id)
            if task is not None and task.get("stop_event") is not None:
                request_stop(task, task["stop_event"], STOP_MODE_FINISH)
            ctx.resume_claims.discard(run_id)
        from webui import pipeline_exec as _facade
        from webui.frozen_browser_identity import close_frozen_run_browser
        result = _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map,
            profile_summary,
            source_dropped=source_dropped,
            total_scraped=source_total_scraped,
            platform=platform,
            profile_facts=profile_facts,
            unfiltered=str(run.get("current_stage") or "") == "scrape",
        )
        from webui.screen_flow import build_round_script_params
        from webui.result_rounds import save_finished_round
        snapshot_run_id = save_finished_round(
            ctx.store,
            result,
            build_round_script_params(ctx.store, run, run.get("frozen_filters") or {}, platform),
            scrape_task_id=parent_scrape_task_id,
            status="partial",
            execution_config=params.get("execution_config") or {},
            platform=platform,
            profile_summary=profile_summary,
            profile_facts=profile_facts,
            started_at=run.get("started_at"),
            finished_at=int(time.time() * 1000),
        )
        from webui.task_finish_whitebox import finalize_manual_partial_whitebox_or_none
        whitebox_integrity = finalize_manual_partial_whitebox_or_none(
            ctx.store, run, parent_owner_id=parent_scrape_task_id)
        if whitebox_integrity is None:
            return jsonify({
                "ok": False, "error": "whitebox_incomplete",
                "message": "部分结果已生成，但任务证据未能可靠终结，请重试结束保存",
            }), 503
        try:
            ctx.store.finish_screening_run(run_id)
        except DiscoveryStoreConflictError as exc:
            return jsonify({
                "ok": False, "error": str(exc),
                "message": {
                    "already_finished": "任务已结束保存，请勿重复操作",
                    "already_terminal": "任务已完成，无需结束保存",
                    "user_cancelled": "用户已取消的任务不能结束保存",
                }.get(str(exc), "任务状态已变化，无法结束保存"),
            }), 409
        except KeyError:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        ctx.clear_auto_screen(run_id)
        if scrape_task_id and scrape_task_id != run_id:
            ctx.clear_auto_screen(scrape_task_id)
        ctx.prune_history_best_effort()
        # 043：整条进出——结束保存后的定稿清理 + 无主兜底（服务内部 best-effort）。
        from webui import run_cleanup
        run_cleanup.prune_after_finalize(ctx.store, str(snapshot_run_id or run_id))
        append_task_event_best_effort(
            ctx.store, run_id, "finish", {
                "snapshot_run_id": snapshot_run_id,
                "stage": run.get("current_stage") or "", "jobs": len(result["jobs"]),
                "dropped": len(result["dropped"]),
            }, logger=_logger,
        )
        with ctx.lock:
            current = ctx.tasks.get(run_id)
            if current is not None:
                current["status"] = "completed_with_pending"
                current["error"] = "用户提前结束，已保存部分结果"
                current["result"] = result
                current["finished_at"] = int(time.time() * 1000)
        cleanup = close_frozen_run_browser(
            ctx.store, run,
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
                    current["error"] = "结果已保存，但浏览器清理失败"
        return jsonify({
            "ok": True, "run_id": run_id, "snapshot_run_id": snapshot_run_id,
            "platform": platform,
            "status": "completed_with_pending", "result": result,
            "integrity": whitebox_integrity,
            "scrape_task_id": parent_scrape_task_id,
            "waited_for_batch": waited_for_batch,
            "message": "任务已结束，已完成结果已保存",
            "cleanup": cleanup.as_dict(),
            **({"cleanup_error": "browser_cleanup_failed"}
               if not cleanup.ok else {}),
        })
