"""任务状态 / 诊断 / 恢复预览 API 路由（021 B6 T019 外迁自 webui/app.py）。

run 诊断摘要、task-state 聚合快照（011 healthy-pipeline 统一接口层）、
恢复三段（preview/prepare/execute）。路由体纯搬运：HTTP 契约零改动；
共享断言/任务表经 ctx 取用。
"""

from __future__ import annotations

import sqlite3
import time

from flask import jsonify, request

from webui.constants import (
    LOG_TAIL_LINES,
    _MSG_TASK_NOT_FOUND,
)
from webui.task_status import (
    _active_elapsed_ms,
    _pipeline_kind_for_stage,
    _public_task_status,
    _recrawl_overall_percent,
    _screen_overall_percent,
)
from webui.diagnostics import build_diagnostic_payload
from webui.error_registry import resolve_code
from webui.store import SYSTEMIC_BLOCK_CODES
from webui.task_runners import _iso_epoch_ms

def register_task_state_routes(app, ctx):
    @app.route("/api/runs/<run_id>/diagnostics")
    def run_diagnostics(run_id: str):
        """Return a safe diagnostic summary for a pipeline run."""
        try:
            run = ctx.store.get_screening_run(run_id)
        except ctx.operational_errors:
            run = None
        if run is None:
            return jsonify({
                "ok": False, "error_code": "not_found",
                "user_message": _MSG_TASK_NOT_FOUND,
            }), 404
        try:
            events = ctx.store.list_task_events(run_id)
        except ctx.operational_errors:
            events = []
        params = run.get("execution_params") or {}
        correlation_id = str(params.get("correlation_id") or "")
        correlation_id = correlation_id or run_id
        from webui.pipeline_exec import taxonomy_reason
        code = str(run.get("error_code") or "")
        next_action = taxonomy_reason(
            code, str(run.get("platform") or ""), fallback=""
        ) if code else ""
        payload = build_diagnostic_payload(
            run_id=run_id, run=run, events=events,
            correlation_id=correlation_id, next_action=next_action,
        )
        return jsonify({"ok": True, **payload})

    @app.route("/api/task-state/<run_id>", methods=["GET"])
    def api_task_state(run_id: str):
        """FR-037：统一任务状态接口。

        返回完整状态画面：status/stage/progress/success_count/fail_count/
        unstarted_count/total/pause_info(含 error_code/error_reason)。
        前端 3 个 snapshot 统一从此接口拉取。
        """
        from webui.pipeline_exec import (
            _scrape_overall_percent,
            _scrape_page_overall_percent,
            failed_code_label,
        )

        with ctx.lock:
            task = ctx.tasks.get(run_id)
            if task is not None:
                # T405: 内存 task 与 DB run 身份一致性校验
                _, conflict = ctx.check_run_identity_conflict(run_id, task)
                if conflict is not None:
                    return conflict
                if task["status"] in ("done", "partial", "failed", "cancelled") and task.get(
                    "finished_at"
                ) is None:
                    task["finished_at"] = int(time.time() * 1000)
                live = {
                    "kind": task.get("kind", ""),
                    "status": task.get("status", "running"),
                    "progress": dict(task.get("progress") or {}),
                    "logs": list((task.get("logs") or [])[-LOG_TAIL_LINES:]),
                    "error": task.get("error", ""),
                    "result": task.get("result"),
                    "started_at": task.get("started_at"),
                    "finished_at": task.get("finished_at"),
                    "auto_screen": task.get("auto_screen"),
                }
            else:
                live = None
        run = ctx.store.get_screening_run(run_id)
        if run is None and live is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        # T405: 按 combo 最新 attempt 汇总 source outcomes
        source_summary, source_outcomes = ctx.build_source_summary_and_outcomes(run_id)
        live_progress = (live or {}).get("progress") or {}
        source = int((run or {}).get("source_count") or live_progress.get("total") or 0)
        # 只有 AI 筛选/补抓任务的 live current 是“条数”语义，可作计数兜底；
        # scrape 任务的 searching/waiting/combo_* 阶段 current 是组合序号，
        # 混入成功数会显示「已完成 3 / 127 岗位」这类错误计数。
        live_kind = str((live or {}).get("kind") or "")
        count_live = live_kind in ("ai_screen", "recrawl")
        live_current = int(live_progress.get("current") or 0)
        if not count_live:
            live_current = 0
        stage = (
            (run or {}).get("current_stage")
            or live_progress.get("stage")
            or "unknown"
        )
        processed_db = int((run or {}).get("processed_count") or 0)
        durable_completed = 0
        if live_kind == "scrape" or str(stage) == "scrape":
            # A resumed scrape can finish combinations after the last run
            # projection write (or while the process is being refreshed). The
            # checkpoint is committed with each combo and is therefore the
            # durable floor for the user-facing progress counter.
            try:
                durable_completed = len(ctx.store.load_checkpoint(run_id, "scrape"))
            except ctx.operational_errors:
                durable_completed = 0
        processed_db = max(processed_db, durable_completed)
        # DB processed_count 是批次粒度（智联详情每批 15 条才落库一次），
        # 为空时用实时 live current 兜底，保证进度按条前进且跨阶段不回退。
        processed = processed_db if processed_db > 0 else live_current
        match = int((run or {}).get("match_count") or 0)
        mismatch = int((run or {}).get("mismatch_count") or 0)
        pending = int((run or {}).get("pending_count") or 0)
        dropped = int((run or {}).get("total_dropped") or 0)
        kept = int((run or {}).get("total_kept") or 0)
        if kept <= 0:
            kept = max(0, source - dropped)
        exec_params = (run or {}).get("execution_params") or {}
        scraped_count_source = str(exec_params.get("scrape_task_id") or "") or run_id
        try:
            scraped_count = ctx.store.count_scrape_run_jobs(scraped_count_source)
        except ctx.operational_errors:
            scraped_count = 0
        error_code = (run or {}).get("error_code")
        error_reason = (run or {}).get("error_reason")
        progress_kind = live_kind or _pipeline_kind_for_stage(stage)
        # Recrawl runs persist their final stage as ``done``.  Once the live
        # task has been cleaned up, recover the kind from the durable run id
        # so refreshes retain recrawl-specific messaging/count semantics.
        if progress_kind == "ai_screen" and str(run_id).startswith("recrawl-"):
            progress_kind = "recrawl"
        # processed_count 只记录已成功完成的当前阶段工作单元；pending
        # 是已失败并进入待确认的独立工作单元，两者不能互相扣减。
        # JD 详情/精筛阶段只处理粗筛保留的岗位；原始列表里的 dropped
        # 已经作为独立结果展示，不能继续混进当前阶段的成功/失败/未开始。
        jd_stage = stage in ("jd_detail", "fetch_jd", "ai_fine", "screen_b", "done")
        stage_total = kept if jd_stage and kept > 0 else source
        # processed_count 只记录已成功完成的当前阶段工作单元；pending
        # 是已失败并进入待确认的独立工作单元，两者不能互相扣减。
        fail_count = pending
        # success_count 必须单调且实时：live_current（条数语义）与 DB 计数
        # 取最大值，保证智联详情逐条推进、跨阶段切换不回退。
        # 精筛阶段（ai_fine/screen_b）的 match+mismatch 仍是粗筛/详情阶段的
        # 累计值，混入会把成功数钉死在上一阶段完成数（假 30/30 + 100% 干等）；
        # 该阶段成功数只算精筛自己的进度：processed 在精筛开始时已重置为
        # 已判定数，live_current 是精筛实时推送的 current。
        if stage in ("ai_fine", "screen_b"):
            success_count = max(processed, live_current)
        else:
            success_count = max(match + mismatch, processed, live_current)
        completed_count = min(stage_total, success_count + fail_count)
        unstarted = max(0, stage_total - completed_count)
        if progress_kind == "recrawl":
            overall_percent = _recrawl_overall_percent(
                stage, completed_count, stage_total,
            )
        elif progress_kind == "ai_screen":
            overall_percent = _screen_overall_percent(
                stage, completed_count, stage_total,
            )
        else:
            overall_percent = _scrape_overall_percent(
                stage, completed_count, stage_total,
            )
        progress = dict(live_progress)
        page_rows = []
        try:
            page_rows = ctx.store.load_scrape_page_progress(scraped_count_source)
        except ctx.operational_errors:
            page_rows = []
        if page_rows:
            latest_page = page_rows[0]
            progress.setdefault("page", latest_page["completed_pages"])
            progress.setdefault("target_pages", latest_page["target_pages"])
            progress.setdefault("resume_page", latest_page["resume_page"])
            progress.setdefault("has_more", bool(latest_page["has_more"]))
            progress.setdefault("scraped", latest_page["jobs_count"])
            if "overall_percent" not in progress:
                page_ratio = min(
                    1.0, latest_page["completed_pages"] / max(1, latest_page["target_pages"]))
                progress["overall_percent"] = _scrape_page_overall_percent(
                    stage, completed_count, stage_total, page_ratio)
        progress.setdefault("overall_percent", overall_percent)
        if not count_live:
            # A browser refresh may leave an in-memory snapshot behind the
            # durable checkpoint. Do not let that stale current/percent mask
            # the reconciled combo count.
            progress["current"] = max(
                int(progress.get("current") or 0),
                success_count if jd_stage else completed_count,
            )
            if durable_completed > live_current:
                progress["overall_percent"] = overall_percent
        else:
            progress.setdefault("current", success_count if jd_stage else completed_count)
        progress.setdefault("total", stage_total)
        # A persisted terminal task has no in-memory live progress after a
        # refresh. Reconstruct the user-facing result message from durable
        # status/counts so a partial recrawl never falls back to generic
        # success text.
        if (
            progress_kind == "recrawl"
            and str((run or {}).get("status") or "") == "partial"
            and pending > 0
        ):
            progress.setdefault("message", f"重抓完成，但仍有 {pending} 个岗位待确认")
        pause_info = None
        effective_status = _public_task_status(
            str(live.get("status")) if live is not None else str(run["status"]),
            (run or {}).get("interruption_kind"),
        )
        _resolved_error_code = (
            resolve_code(error_code) if error_code else "")
        if effective_status == "paused" or (
                effective_status == "failed" and error_code) or (
                _resolved_error_code and _resolved_error_code in SYSTEMIC_BLOCK_CODES):
            pause_info = {
                "error_code": error_code,
                "error_reason": error_reason or failed_code_label(
                    error_code, str((run or {}).get("platform") or (live or {}).get("platform") or "")
                ) or error_code or "",
            }
        if effective_status == "interrupted":
            progress.setdefault(
                "message", "任务因服务重启被中断，已保存进度")
        started_at = _iso_epoch_ms((live or {}).get("started_at"))
        if started_at is None:
            started_at = _iso_epoch_ms((run or {}).get("started_at"))
        finished_at = _iso_epoch_ms((live or {}).get("finished_at"))
        if finished_at is None:
            finished_at = _iso_epoch_ms((run or {}).get("finished_at"))
        # 暂停不计时：从 task_logs 的 pause/resume 事件推导累计实际运行时长。
        # 刷新页面后仍有效（事件已持久化）；无事件或无法计算时回退 None。
        try:
            task_events = ctx.store.list_task_events(run_id)
        except ctx.operational_errors:
            task_events = []
        active_elapsed_ms = _active_elapsed_ms(started_at, finished_at, task_events)
        # 016：软失败组合留痕（combo_issue/kind=combo_failed），倒序取最近 20 条；
        # 文案来自统一注册表，前端只展示不猜码。
        combo_issues = []
        _platform_label = str((run or {}).get("platform") or (live or {}).get("platform") or "")
        for _event in reversed(task_events):
            _payload = _event.get("payload") or {}
            if (
                _event.get("type") == "combo_issue"
                and _payload.get("kind") == "combo_failed"
            ):
                _code = resolve_code(
                    str(_payload.get("failed_code") or "source_unknown_error"))
                combo_issues.append({
                    "combo_key": str(_payload.get("combo_key") or ""),
                    "code": _code,
                    "code_text": failed_code_label(_code, _platform_label) or _code,
                    "reason": str(_payload.get("reason") or ""),
                    "ts": _payload.get("ts") or _event.get("at") or "",
                })
            if len(combo_issues) >= 20:
                break
        return jsonify({
            "ok": True,
            "run_id": run_id,
            "kind": (live or {}).get("kind", ""),
            "status": effective_status,
            "db_status": (run or {}).get("status"),
            "stage": stage,
            "progress": progress,
            "logs": (live or {}).get("logs", []),
            "error": (live or {}).get("error") or error_reason or "",
            "success_count": success_count,
            "fail_count": fail_count,
            "unstarted_count": unstarted,
            "pending_count": pending,
            "total": stage_total,
            "source_total": source,
            "processed": processed,
            "scraped_count": scraped_count,
            "match_count": match,
            "mismatch_count": mismatch,
            "dropped_count": dropped,
            "kept_count": kept,
            "pause_info": pause_info,
            "execution_config": (
                (run or {}).get("execution_params") or {}
            ).get("execution_config") or {},
            "backend_version": (run or {}).get("backend_version"),
            "current_version": ctx.backend_version,
            "version_match": (
                not (run or {}).get("backend_version")
                or (run or {}).get("backend_version") == ctx.backend_version
            ),
            "updated_at": (run or {}).get("updated_at"),
            "started_at": started_at,
            "finished_at": finished_at,
            "active_elapsed_ms": active_elapsed_ms,
            # T405: 平台身份与 source outcomes 汇总
            "platform": (run or {}).get("platform") or (live or {}).get("platform"),
            "task_input_digest": (run or {}).get("task_input_digest"),
            "auto_screen": bool((live or {}).get("auto_screen", exec_params.get("auto_screen"))),
            "source_summary": source_summary,
            "source_outcomes": source_outcomes,
            "combo_issues": combo_issues,
            **(
                {"result": live["result"]}
                if live is not None and live.get("result") is not None else {}
            ),
        })

    @app.route("/api/recovery/preview/<run_id>", methods=["GET"])
    def api_recovery_preview(run_id: str):
        """FR-041：历史恢复只读预演接口。run_id 参数仅作占位，
        实际预演两个历史 run（15847d27 + e6250f0e）。
        """
        from webui.historical_recovery import (
            FINE_RUN_ID,
            ROUGH_RUN_ID,
            preview_recovery,
        )
        try:
            result = preview_recovery(
                ctx.store,
                rough_run_id=ROUGH_RUN_ID,
                fine_run_id=FINE_RUN_ID,
                result_dir=app.config["RESULT_DIR"],
            )
            return jsonify({"ok": True, "preview": result})
        except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            return jsonify({"ok": False, "error": str(exc),
                            "error_type": type(exc).__name__}), 500

    @app.route("/api/recovery/prepare/<run_id>", methods=["POST"])
    def api_recovery_prepare(run_id: str):
        """Create the server-owned SQLite backup and manifest for recovery."""
        from webui.historical_recovery import prepare_recovery
        try:
            prepared = prepare_recovery(ctx.store)
            return jsonify({"ok": True, **prepared}), 201
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            return jsonify({
                "ok": False, "error": str(exc),
                "error_type": type(exc).__name__,
            }), 409

    @app.route("/api/recovery/execute/<run_id>", methods=["POST"])
    def api_recovery_execute(run_id: str):
        """Execute a prepared recovery by opaque server-generated backup id."""
        from webui.historical_recovery import execute_recovery
        body = request.get_json(silent=True) or {}
        backup_id = str(body.get("backup_id") or "").strip()
        if not backup_id:
            return jsonify({
                "ok": False, "error": "missing_backup_id",
                "message": "请先调用 prepare 接口创建恢复备份",
            }), 400
        try:
            result = execute_recovery(backup_id, store=ctx.store)
            status = 200 if result.get("ok") else 409
            return jsonify({"ok": result.get("ok", False), "result": result}), status
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc),
                            "error_type": type(exc).__name__}), 500
