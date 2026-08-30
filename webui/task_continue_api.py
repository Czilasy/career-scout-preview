"""任务续跑 / 暂停 / 取消 / 结束 API 路由（021 B6 T019 外迁自 webui/app.py）。

paused/中断 run 的续跑分发（抓取/筛选/重抓三向）、用户暂停与取消、
结束任务的部分快照定稿。路由体纯搬运：HTTP 契约零改动。
"""

from __future__ import annotations

import sqlite3
import time

from flask import jsonify, request

from webui.app import (
    _MSG_TASK_ALREADY_RUNNING,
    _MSG_TASK_NOT_FOUND,
    _public_task_status,
    _refresh_paused_run_execution_config,
)
from webui.resume_identity import (
    append_account_switch_log_line,
    apply_continue_account_switch,
    decide_auto_account_switch,
)
from webui.store import DiscoveryStoreConflictError
from webui.task_pause_support import cancel_task_cleanup, pause_with_mode
from webui.task_runners import _iso_epoch_ms

def register_task_continue_routes(app, ctx):
    def _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map, profile_summary,
            source_dropped=None, total_scraped=None, platform="",
            profile_facts=None):
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
            "total_kept": len(jobs),
            "total_matched": sum(1 for j in jobs if j.get("verdict") == "match"),
            "total_dropped": len(dropped),
            "profile_summary": profile_summary or "",
            "profile_facts": profile_facts,
            "error": "",
        }

    @app.route("/api/task/continue/<run_id>", methods=["POST"])
    def api_task_continue(run_id: str):
        """FR-020/FR-022：统一继续接口。

        允许 paused 状态调用；running 状态拒绝（防止重复继续）。
        继续前检查阻断条件是否解除（由各阶段 handler 自行实现）。

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp
        run = ctx.store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        if run["status"] != "paused":
            return jsonify({
                "ok": False,
                "error": "not_paused",
                "status": _public_task_status(run["status"], run.get("interruption_kind")),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
        # B057：限流后可指定另一个已登录账号继续同一断点（显式指定，行为不变）。
        # 030：未显式指定时收紧为双门槛自动换号——仅当用户暂停期间主动换过
        # 全局账号（当前 ≠ 创建时快照）且暂停码非 AI 类；快照缺失（存量任务）
        # 一律沿用冻结身份。决策/留痕在续跑身份域，此处只接线。
        _continue_body = request.get_json(silent=True) or {}
        target_account = str(_continue_body.get("target_account") or "").strip()
        auto_switch: tuple[bool, str, str] | None = None
        if not target_account:
            active_account = str(
                (ctx.load_legacy_advanced_settings() or {}).get("browser_account") or "a"
            )
            auto_switch = decide_auto_account_switch(
                run, current_active_account=active_account)
            if auto_switch[0]:
                target_account = auto_switch[2]
        if target_account:
            # 030：目标账号校验/登录空间/阻断检查/持久化/留痕整段收口到续跑身份域
            applied = apply_continue_account_switch(
                ctx.store, run, run_id=run_id, target_account=target_account,
                auto_switch=auto_switch,
                accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
                check_resume_block=ctx.check_resume_block)
            if applied["status"] != "ok":
                return jsonify(applied["body"]), applied["http_status"]
            run = applied["run"]
        # FR-022：检查内存中是否已有该 run 的工作
        ctx.activate_run_browser(run)
        with ctx.lock:
            existing = ctx.tasks.get(run_id)
            if existing is not None and existing.get("status") == "running":
                return jsonify({
                    "ok": False,
                    "error": "already_running",
                    "message": "该任务正在运行，请勿重复点击继续",
                }), 409
        # 版本校验（FR-039）
        run_version = run.get("backend_version")
        if run_version and run_version != ctx.backend_version:
            return jsonify({
                "ok": False,
                "error": "version_mismatch",
                "message": "后端版本已变更，请刷新页面后重试",
                "run_version": run_version,
                "current_version": ctx.backend_version,
            }), 409
        from webui.resume_identity import (
            ensure_frozen_browser_account,
            invalidate_login_cache_for_resume,
            persist_frozen_identity,
            resolve_frozen_identity,
        )
        identity = resolve_frozen_identity(ctx.store, run)
        # 030：存量 run 缺冻结账号时按角色感知兜底（BOSS=R2，智联=当前账号），
        # 与筛选提交续跑同口径并写回；不再借父抓取 run 的 R1 账号或静默全局回退。
        if not str((run.get("execution_params") or {}).get("browser_account") or ""):
            effective_account = ensure_frozen_browser_account(
                ctx.store, run_id, run,
                platform=str(identity.get("platform") or ""),
                fallback_account=ctx.account_for_run(run),
                accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
                role="R2")
            if effective_account:
                identity["browser_account"] = effective_account
        missing = [
            key for key in ("platform", "browser_account", "cdp_port", "profile_key")
            if identity.get(key) in (None, "")
        ]
        if not identity.get("platform") or (
            identity["platform"] == "zhilian" and missing
        ):
            return jsonify({
                "ok": False, "error": "missing_frozen_identity",
                "message": "继续任务缺少冻结的账号或浏览器身份，无法安全恢复", "status": "paused",
                "missing_fields": missing,
            }), 409
        invalidate_login_cache_for_resume(
            identity["browser_account"], identity["platform"])
        persist_frozen_identity(ctx.store, run_id, identity)
        run["platform"] = identity["platform"]
        run["execution_params"] = dict(run.get("execution_params") or {})
        run["execution_params"].update(
            {k: v for k, v in identity.items() if v not in (None, "")})
        # 高级设置续跑生效：block 检查通过后才按当前 active 配置刷新该 run 的
        # execution_config 并写回 DB（三条续跑路径统一从刷新后的配置读取）。
        # pages/frozen_scope 保持冻结不变；block 未解除时不提前改写 DB 快照。
        stage = str(run.get("current_stage") or "")
        refreshed_config = None

        def _refresh_run_config():
            nonlocal refreshed_config
            refreshed_config = _refresh_paused_run_execution_config(run, ctx.store)
            if refreshed_config is not None:
                run["execution_params"]["execution_config"] = refreshed_config.to_dict()

        if stage.startswith("recrawl_"):
            passed, code, reason = ctx.check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            _refresh_run_config()
            return ctx.continue_recrawl(
                run_id, _block_checked=True,
                account_switch_note=(
                    (auto_switch[1], auto_switch[2])
                    if auto_switch is not None and auto_switch[0] else None))
        if stage == "scrape":
            passed, code, reason = ctx.check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            _refresh_run_config()
            return ctx.continue_execute_search(
                run_id, _block_checked=True,
                account_switch_note=(
                    (auto_switch[1], auto_switch[2])
                    if auto_switch is not None and auto_switch[0] else None))

        passed, code, reason = ctx.check_resume_block(run)
        if not passed:
            return jsonify({
                "ok": False, "error": "block_not_resolved",
                "error_code": code, "error_reason": reason,
                "status": "paused",
            }), 409

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
        _refresh_run_config()

        if not ctx.claim_resume(run_id):
            return jsonify({
                "ok": False,
                "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409

        # 服务重启后内存来源丢失：从逐组合持久化结果重建只读来源快照。
        with ctx.lock:
            ctx.tasks[scrape_task_id] = {
                "kind": "scrape", "status": "done", "progress": {}, "logs": [],
                "result": {
                    "ok": True, "jobs": source_jobs,
                    "total_scraped": len(source_jobs), "total_matched": len(source_jobs),
                    "completed_combos": sorted(ctx.store.load_checkpoint(scrape_task_id, "scrape")),
                    "error": "",
                },
                "error": "", "started_at": None, "finished_at": None,
                "stop_event": ctx.threading.Event(),
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
            # 030 FR-005：自动换号在续跑启动日志留一行中文说明
            append_account_switch_log_line(
                claimed_task,
                from_account=auto_switch[1], to_account=auto_switch[2])
        start_gate = ctx.threading.Event()
        abort_start = ctx.threading.Event()

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
                # 高级设置续跑生效：优先使用本轮刷新后的配置，而非父抓取 run 的旧冻结值
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
            return jsonify({
                "ok": False,
                "error": "resume_submit_failed",
                "message": f"继续任务提交失败：{type(exc).__name__}",
            }), 500
        start_gate.set()
        return jsonify({
            "ok": True,
            "run_id": task_id,
            "task_id": task_id,
            "resumed_from": run_id,
            "status": "running",
            "message": "AI 筛选已从断点继续",
            # T412 契约 http-api.md L216：成功响应增加 platform 和
            # task_input_digest。平台不由客户端选择，从原 run 读取。
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
        # 任务注册先于后台线程创建 DB run，因此取消必须同时支持纯内存窗口。
        with ctx.lock:
            task = ctx.tasks.get(run_id)
            if task is not None and task.get("status") in {
                "queued", "running", "paused",
            }:
                stop_event = task.get("stop_event")
                if stop_event is not None:
                    stop_event.set()
        run = ctx.store.get_screening_run(run_id)
        if run is None and task is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404

        if run is not None and run.get("status") == "interrupted" and run.get("error_code") == "user_finished":
            return jsonify({
                "ok": False, "error": "already_finished",
                "message": "任务已结束保存，无需取消",
            }), 409

        # 有 DB 身份时先提交 durable cancel，再发布内存状态。写入失败时
        # 保持内存原状态，避免页面显示 cancelled 而数据库仍在 running。
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
                    ctx.store.append_task_event(run_id, "cancel", {"by": "user"})
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
        if task is not None:
            try:
                from webui.pipeline_exec import close_debug_chrome
                if run is not None:
                    ctx.activate_run_browser(run)
                close_debug_chrome()
            except (OSError, RuntimeError):
                pass  # best-effort 关闭浏览器；取消状态已经可靠提交。
        # 025：取消也清理 guard 批次登记（避免「继续」后被误判卡死重抓）
        cancel_task_cleanup(ctx, run_id)
        return jsonify({
            "ok": True,
            "run_id": run_id,
            "platform": (run or {}).get("platform"),
            "status": (
                _public_task_status(run["status"], run.get("interruption_kind")) if run is not None else "cancelled"
            ),
            "processed_count": int((run or {}).get("processed_count") or 0),
            "message": "任务已取消，已有结果保留",
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
        # T416: 检查 interruption_kind
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
        # 先落“用户正在收尾”标记：worker 即使抢先写 cancelled，也保留 operator_stop，
        # 不会变成 finish 无法收尾的空 kind 中断。
        try:
            ctx.store.save_interruption_kind(run_id, "operator_stop")
        except ctx.operational_errors:
            pass
        # 先发停止信号并等待当前页原子落库稳定，再从页级快照生成部分结果。
        flush_run_id = scrape_task_id or (
            run_id if str(run.get("current_stage") or "") == "scrape" else ""
        )
        if flush_run_id:
            with ctx.lock:
                task = ctx.tasks.get(flush_run_id)
                stop_event = task.get("stop_event") if task is not None else None
                flush_lock = task.get("page_flush_lock") if task is not None else None
            if stop_event is not None:
                stop_event.set()
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
        # 快照可构建性校验完成后，才停止后台工作并原子标记 user_finished；
        # 无快照时保持原状态，避免把任务永久写成无法恢复的终态（B027）。
        with ctx.lock:
            task = ctx.tasks.get(run_id)
            if task is not None and task.get("stop_event") is not None:
                task["stop_event"].set()
            # B027：陈旧续跑接管标记不阻断结束保存；先兜底释放，再收尾。
            ctx.resume_claims.discard(run_id)
        try:
            from webui.pipeline_exec import close_debug_chrome
            ctx.activate_run_browser(run)
            close_debug_chrome()
        except (OSError, RuntimeError):
            pass
        result = _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map,
            profile_summary,
            source_dropped=source_dropped,
            total_scraped=source_total_scraped,
            platform=platform,
            profile_facts=profile_facts,
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
        # 快照先落库，再原子标记 user_finished：保存失败时任务仍可重试，
        # 不会留下“已结束但无结果”的死状态；worker 已收到停止信号。
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
        ctx.store.append_task_event(run_id, "finish", {
            "snapshot_run_id": snapshot_run_id,
            "stage": run.get("current_stage") or "", "jobs": len(result["jobs"]),
            "dropped": len(result["dropped"]),
        })
        with ctx.lock:
            current = ctx.tasks.get(run_id)
            if current is not None:
                current["status"] = "cancelled"
                current["error"] = "用户提前结束，已保存部分结果"
                current["result"] = result
                current["finished_at"] = int(time.time() * 1000)
        try:
            from webui.pipeline_exec import close_debug_chrome
            ctx.activate_run_browser(run)
            close_debug_chrome()
        except (OSError, RuntimeError):
            pass
        return jsonify({
            "ok": True, "run_id": run_id, "snapshot_run_id": snapshot_run_id,
            "platform": platform,
            "status": "completed_with_pending", "result": result,
            "scrape_task_id": parent_scrape_task_id,
            "message": "任务已结束，已完成结果已保存",
        })
