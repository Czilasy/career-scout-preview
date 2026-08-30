"""create_app 管线支撑工厂（021 B6 T019 外迁自 webui/app.py）。

任务表/锁/执行器、调优 runner、任务声明与租约、终态安全写入、暂停
失败记录、清理定时、账号激活、auto_screen 消费、续跑阻断断言、JD
失败持久化、浏览器锁助手与 PipelineContext 组装。闭包语义原样搬运；
可 patch 符号（threading / ai_service）经 webui.app 模块属性动态取用，
保住 patch("webui.app.X") 面。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import jsonify

from webui.constants import (
    _MSG_USER_FINISHED,
    _MSG_USER_STOPPED_SCRAPE,
    _MSG_USER_STOPPED_SCREEN,
    _OPERATIONAL_ERRORS,
)
from webui.task_status import (
    _recrawl_overall_percent,
    _screen_overall_percent,
)
from webui.browser_support import build_browser_support
from webui.diagnostics import record_failure
from webui.error_registry import resolve_code
from webui.pipeline_context import PipelineContext
from webui.store import SYSTEMIC_BLOCK_CODES, DiscoveryStoreConflictError

from webui.logging_setup import get_logger

_logger = get_logger(__name__)



def build_app_support(app, store, runner, workbench_runner,
                      job_feedback_service, history_service, resume_service,
                      _prune_history_best_effort, _load_legacy_advanced_settings,
                      _save_legacy_advanced_settings, _make_cdp_source,
                      scope_previews, _runtime_mode):
    import webui.app as _app_module  # 可 patch 符号动态门面

    _pipeline_tasks = {}
    _pipeline_lock = _app_module.threading.RLock()
    _resume_claims = set()
    _pipeline_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-pipeline")
    app.config["PIPELINE_TASKS"] = _pipeline_tasks
    app.config["PIPELINE_EXECUTOR"] = _pipeline_executor
    from webui.pipeline_exec import TuningRoundRunner

    def _tuning_ai_settings():
        settings = store.get_ai_settings()
        credential_ref = store.get_credential_ref()
        api_key = (
            _app_module.ai_service.retrieve_api_key(credential_ref) if credential_ref else ""
        )
        return {**settings, "api_key": api_key}

    _tuning_round_runner = TuningRoundRunner(
        workspace_root=Path(store.db_path).resolve().parent.parent,
        source_factory=_make_cdp_source,
        ai_settings_provider=_tuning_ai_settings,
    )
    app.config["TUNING_ROUND_RUNNER"] = _tuning_round_runner

    def _run_tuning_manifest_child(manifest_id: str):
        from webui.runners.tuning_manifest import run_tuning_manifest_child

        return run_tuning_manifest_child(ctx, manifest_id)


    app.config["RUN_TUNING_MANIFEST_CHILD"] = _run_tuning_manifest_child

    def _new_pipeline_task(kind, *, source_task_id=None):
        task = {
            "kind": kind,
            "status": "queued",
            "progress": {},
            "logs": [],
            "result": None,
            "error": "",
            # 任务创建即记开始，终态时补结束（前端计时器从快照读取，
            # 不再依赖组件存活期间的本地时钟，组件销毁重建/刷新后不再归零）。
            "started_at": int(time.time() * 1000),
            "finished_at": None,
            # 停止信号：cancel 接口 set 它，run_search 循环检查到后退出。
            # 不放进 task 的 JSON 序列化里（threading.Event 不可序列化），
            # 只在服务进程内存中存活。
            "stop_event": _app_module.threading.Event(),
        }
        if source_task_id:
            task["source_task_id"] = source_task_id
        return task

    def _register_pipeline_task(task_id, kind, *, source_task_id=None):
        task = _new_pipeline_task(
            kind, source_task_id=source_task_id
        )
        with _pipeline_lock:
            _pipeline_tasks[task_id] = task
        return task

    def _claim_recrawl_start(task_id, source_run_id):
        """Atomically reserve one active recrawl per source run."""
        with _pipeline_lock:
            for existing_id, existing in _pipeline_tasks.items():
                if (
                    existing.get("kind") == "recrawl"
                    and existing.get("source_run_id") == source_run_id
                    and existing.get("status") in ("queued", "running")
                ):
                    return None, existing_id
            task = _new_pipeline_task("recrawl")
            task["source_run_id"] = source_run_id
            _pipeline_tasks[task_id] = task
            return task, None

    def _check_tuning_lease_conflict():
        """SPEC011 T015/FR-035: 检查实验租约是否被持有。

        租约被持有时返回 (False, error_response)，调用方应直接 return 该响应。
        无租约时返回 (True, None)。

        普通任务和实验任务共用此门禁，确保任意时刻只有一个压力任务运行。
        """
        try:
            lease = store.get_tuning_lease()
        except _OPERATIONAL_ERRORS:
            # 数据库异常时不阻断普通任务（租约检查是安全网，不是硬门禁）
            return True, None
        if lease.get("owner_experiment_id") is None:
            return True, None
        return False, (jsonify({
            "ok": False,
            "error": "tuning_lease_held",
            "error_code": "tuning_lease_held",
            "message": "深度实验正在独占执行环境，请等待实验结束后再启动普通任务",
            "owner_experiment_id": lease.get("owner_experiment_id"),
            "retryable": False,
            "required_action": "等待实验结束或取消实验后再启动普通任务",
        }), 409)

    def _claim_pipeline_task_id(task_id, kind, *, started_at=None):
        """Atomically reserve a concrete task id for continuation."""
        with _pipeline_lock:
            previous = _pipeline_tasks.get(task_id)
            if previous is not None and previous.get("status") in (
                "queued", "running",
            ):
                return None, previous
            task = _new_pipeline_task(kind)
            preserved_started_at = started_at
            if preserved_started_at is None and previous is not None:
                preserved_started_at = previous.get("started_at")
            if preserved_started_at is not None:
                task["started_at"] = int(preserved_started_at)
            _pipeline_tasks[task_id] = task
            return task, previous

    def _release_pipeline_claim(task_id, claimed_task, previous_task=None):
        """Remove only the reservation created by the current request."""
        with _pipeline_lock:
            if _pipeline_tasks.get(task_id) is claimed_task:
                if previous_task is None:
                    _pipeline_tasks.pop(task_id, None)
                else:
                    _pipeline_tasks[task_id] = previous_task

    def _claim_resume(run_id):
        """Atomically reserve a paused run while a continuation is scheduled."""
        with _pipeline_lock:
            if run_id in _resume_claims:
                return False
            _resume_claims.add(run_id)
            return True

    def _release_resume_claim(run_id):
        with _pipeline_lock:
            _resume_claims.discard(run_id)

    def _is_user_finished(run_id):
        """判断 run 是否已被用户结束保存（interrupted + user_finished）。"""
        try:
            run = store.get_screening_run(run_id)
        except _OPERATIONAL_ERRORS:
            return False
        return bool(
            run and run.get("status") == "interrupted"
            and run.get("error_code") == "user_finished"
        )

    def _write_run_unless_finished(run_id, **kwargs):
        """Worker 写 DB 前的统一守卫：用户已结束时跳过，绝不覆盖终态。"""
        if _is_user_finished(run_id):
            return False
        try:
            store.update_screening_run(run_id, **kwargs)
            return True
        except DiscoveryStoreConflictError:
            return False


    def _record_pause_failure(task_id, stage, code, reason, *, processed=0, total=0,
                              extra=None, exception=None, include_traceback=False):
        """Write the durable failure event for systemic pause paths."""
        diagnostics = {"stage": stage, "processed": int(processed), "total": int(total)}
        if extra:
            diagnostics.update(extra)
        record_failure(
            store, task_id, stage=stage,
            error_code=code or "internal_error",
            reason=reason or code or "任务被阻断",
            correlation_id=task_id, diagnostics=diagnostics,
            exception=exception, include_traceback=include_traceback,
        )

    def _release_worker_resume_claims(task):
        """worker 终态/暂停时释放续跑接管标记（B027 卡死点）。"""
        with _pipeline_lock:
            claim_id = (task or {}).get("resuming_from") or (task or {}).get("resumed_from")
        if claim_id:
            _release_resume_claim(str(claim_id))

    def _schedule_pipeline_task_cleanup(task_id):
        """30 分钟后自动移除已完成任务，避免内存泄漏。

        定时器只删除排程时刻捕获的那个任务对象：续跑会用同一 run_id
        重新注册新任务，旧暂停任务的定时器到点不能误删仍在运行的新任务。
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)

        def _cleanup():
            with _pipeline_lock:
                if _pipeline_tasks.get(task_id) is task:
                    _pipeline_tasks.pop(task_id, None)

        timer = _app_module.threading.Timer(30 * 60, _cleanup)
        timer.daemon = True
        timer.start()

    app.config["SCHEDULE_PIPELINE_CLEANUP"] = _schedule_pipeline_task_cleanup

    app.config["SCHEDULE_PIPELINE_TASK_CLEANUP"] = _schedule_pipeline_task_cleanup

    # -----------------------------------------------------------------------
    # AI 筛选阶段百分比与文案
    # -----------------------------------------------------------------------
    _SCREEN_STAGE_MESSAGES: dict[str, str] = {
        "resume": "正在恢复上次进度…",
        "screen_a": "AI 粗筛中…",
        "screen_a_done": "粗筛完成，准备抓取 JD…",
        "ensure_chrome": "启动浏览器，准备抓取 JD…",
        "fetch_jd": "抓取 JD 中…",
        "screen_b": "AI 精筛中…",
        "done": "筛选完成",
        "cancelled": "运行已取消",
    }
    _EVENT_STAGE_NAMES = {
        "screen_a": "ai_rough",
        "screen_a_done": "ai_rough",
        "fetch_jd": "jd_detail",
        "screen_b": "ai_fine",
    }

    def _account_for_run(run=None) -> str:
        """Resolve the browser account for a run or the current advanced setting."""
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        if isinstance(run, dict):
            params = run.get("execution_params") or {}
            if isinstance(params, dict):
                account = str(params.get("browser_account") or "")
                if account in accounts:
                    return account
        account = str((_load_legacy_advanced_settings() or {}).get("browser_account") or "a")
        return account if account in accounts else "a"

    def _invalidate_login_cache(account_id: str, platform: str) -> None:
        """打开浏览器登录窗口时失效该账号该平台的登录态缓存（D3 信号）。

        用户可能刚完成登录；失效后下次 preflight / env-check 重新真实探测，
        避免沿用登录前的旧状态（如缓存里的 not_logged_in 挡住任务提交）。
        """
        try:
            from scripts.login_state_cache import invalidate_login_state
            invalidate_login_state(str(account_id), str(platform))
        except Exception:
            _logger.debug("登录态缓存失效操作失败（best-effort 忽略）", exc_info=True)


    def _activate_run_browser(run=None) -> None:
        """Point the shared CDP helper at the selected profile."""
        from webui.pipeline_exec import resolve_browser_account, set_active_cdp_data_dir
        from webui.platforms import derive_zhilian_profile_dir, resolve_login_space
        account = str((run or {}).get("browser_account") or (run or {}).get("execution_params", {}).get("browser_account") or "") or _account_for_run(run)
        platform = str((run or {}).get("platform") or (run or {}).get("execution_params", {}).get("platform") or "boss")
        boss_dir = resolve_browser_account(account, app.config["BROWSER_ACCOUNTS_PATH"]) or ""
        resolve_login_space(platform, account, boss_profile_dir=boss_dir or "unresolved")
        profile_dir = boss_dir if platform == "boss" else derive_zhilian_profile_dir(boss_dir)
        set_active_cdp_data_dir(profile_dir)

    def _activate_task_browser(task_id: str, *, platform: str | None = None,
                               browser_account: str | None = None) -> None:
        """Bind CDP helpers to a task's frozen browser identity.

        ``ensure_chrome_ready`` reads a process-wide active profile.  A
        background recrawl must therefore rebind it from its frozen task
        identity immediately before checking CDP, rather than inheriting the
        profile last selected by a request or another task.
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id) or {}
            account = str(browser_account or task.get("browser_account") or "")
        from webui.pipeline_exec import resolve_browser_account, set_active_cdp_data_dir
        profile_dir = resolve_browser_account(
            account, app.config["BROWSER_ACCOUNTS_PATH"])
        if profile_dir:
            resolved_platform = str(platform or task.get("platform") or "boss")
            from webui.platforms import resolve_login_space
            _ = resolve_login_space(
                resolved_platform, account or "a", boss_profile_dir=profile_dir
            )
            from webui.platforms import derive_zhilian_profile_dir
            set_active_cdp_data_dir(
                profile_dir if resolved_platform == "boss"
                else derive_zhilian_profile_dir(profile_dir)
            )
        else:
            _activate_run_browser()

    def _ensure_scrape_source(scrape_task_id: str) -> dict | None:
        """Return a scrape source snapshot, rebuilding it from DB after a restart."""
        with _pipeline_lock:
            source_task = _pipeline_tasks.get(scrape_task_id)
            if source_task is not None:
                source_result = source_task.get("result") or {}
                if source_task.get("status") == "done" and source_result.get("ok"):
                    return dict(source_task)
        source_jobs = store.load_scrape_run_jobs(scrape_task_id)
        if not source_jobs:
            return None
        try:
            source_run = store.get_screening_run(scrape_task_id)
        except _OPERATIONAL_ERRORS:
            source_run = None
        # 结束保存/失败/重启中断的父抓取任务只要岗位仍持久化，就允许
        # 从 scrape_run_jobs 重建只读来源快照（B027：03 页不得报缺任务）。
        if source_run is None or source_run.get("status") not in ("succeeded", "partial", "failed", "interrupted"):
            return None
        snapshot = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {
                "ok": True, "jobs": source_jobs,
                "total_scraped": len(source_jobs), "total_matched": len(source_jobs),
                "completed_combos": sorted(store.load_checkpoint(scrape_task_id, "scrape")),
                "error": "",
            },
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": _app_module.threading.Event(),
            "auto_screen": bool((source_run.get("execution_params") or {}).get("auto_screen")),
        }
        with _pipeline_lock:
            _pipeline_tasks[scrape_task_id] = snapshot
        return dict(snapshot)


    def _clear_auto_screen(task_id: str) -> None:
        """清除一键链路的 auto_screen 标记（内存与 DB execution_params）。"""
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is not None:
                task["auto_screen"] = False
        try:
            run = store.get_screening_run(task_id)
        except _OPERATIONAL_ERRORS:
            return
        if run is None:
            return
        params = dict(run.get("execution_params") or {})
        if params.get("auto_screen"):
            params["auto_screen"] = False
            try:
                store.update_screening_execution_params(task_id, params)
            except _OPERATIONAL_ERRORS:
                pass

    def _consume_auto_screen(task_id: str) -> None:
        """AI 筛选入口消费标记；调用后刷新不再自动重试。"""
        _clear_auto_screen(task_id)

    def _check_resume_block(run: dict) -> tuple[bool, str, str]:
        """Verify the paused dependency before submitting resumed work."""
        _activate_run_browser(run)
        checker = app.config.get("RESUME_BLOCK_CHECKER")
        if callable(checker):
            passed, code, reason = checker(run)
        else:
            _raw_code = str(run.get("error_code") or "")
            code = resolve_code(_raw_code) if _raw_code else ""
            reason = ""
            passed = True
            _ai_resume_codes = {
                "ai_rate_limited", "ai_quota_exhausted",
                "ai_key_invalid", "ai_network_error",
            }
            try:
                if code in SYSTEMIC_BLOCK_CODES and code not in _ai_resume_codes:
                    from webui.pipeline_exec import ensure_chrome_ready, taxonomy_reason
                    # T403: 从 run 继承冻结平台/浏览器身份
                    _resume_params = run.get("execution_params") or {}
                    _resume_platform = (
                        run.get("platform")
                        or _resume_params.get("platform")
                        or "boss"
                    )
                    chrome_ok, chrome_err = ensure_chrome_ready(_resume_params.get("cdp_port"))
                    if not chrome_ok:
                        passed = False
                        code = "source_cdp_unavailable"
                        reason = f"调试浏览器尚未就绪：{chrome_err}"
                    else:
                        source = _make_cdp_source(
                            platform=_resume_platform,
                            browser_account=_resume_params.get("browser_account"),
                            cdp_port=_resume_params.get("cdp_port"),
                            profile_key=_resume_params.get("profile_key"),
                            run_id=str(run.get("id") or ""),
                        )
                        outcome = source.preflight() if source is not None else None
                        if outcome is None or not outcome.ok:
                            passed = False
                            source_code = getattr(outcome, "failed_code", "")
                            code = resolve_code(source_code) if source_code else (
                                code or "source_blocked")
                            reason = taxonomy_reason(
                                code, _resume_platform, fallback="阻断条件尚未解除"
                            )
                elif code in {
                    "ai_key_invalid", "ai_quota_exhausted",
                    "ai_rate_limited", "ai_network_error",
                }:
                    from webui.pipeline_exec import taxonomy_reason
                    settings = store.get_ai_settings()
                    credential_ref = store.get_credential_ref()
                    api_key = (
                        _app_module.ai_service.retrieve_api_key(credential_ref)
                        if credential_ref else ""
                    )
                    if not _app_module.ai_service.is_ai_available(settings, credential_ref, api_key):
                        passed = False
                        reason = "AI 配置或额度问题尚未处理，请更新后再继续"
                    else:
                        capability = _app_module.ai_service.test_connection(
                            str(settings.get("endpoint_url") or ""),
                            api_key,
                            model=str(settings.get("model") or ""),
                        )
                        if not capability.get("ok"):
                            passed = False
                            reason = taxonomy_reason(
                                code, "", fallback="AI 阻断条件尚未解除"
                            )
                elif code == "internal_error":
                    passed = False
                    reason = "内部错误尚未解除，请先检查日志或重启服务"
            except (OSError, RuntimeError, ValueError) as exc:
                passed = False
                code = code or "internal_error"
                reason = f"阻断复核失败：{type(exc).__name__}"
        store.append_task_event(run["id"], "block_check", {
            "passed": bool(passed), "stage": run.get("current_stage"),
            "error_code": code, "reason": reason,
        })
        if not passed:
            store.update_screening_run(
                run["id"], error_code=code or "internal_error",
                error_reason=reason or "阻断条件尚未解除",
            )
        return bool(passed), str(code or ""), str(reason or "")

    def _persist_jd_job_failures(
            task_run_id: str, jobs: list[dict], *, stage: str,
            source_run_id: str = "", platform: str = "") -> None:
        """Persist per-job JD failures before a systemic pause returns."""
        from webui.pipeline_exec import (
            ERROR_TAXONOMY,
            failed_code_label,
            taxonomy_reason,
        )

        target_run_ids = [str(task_run_id)]
        if source_run_id and str(source_run_id) not in target_run_ids:
            target_run_ids.append(str(source_run_id))
        events = []
        for job in jobs or []:
            if not isinstance(job, dict) or str(job.get("jd") or "").strip():
                continue
            job_id = str(job.get("platform_job_id") or job.get("job_id") or job.get("id") or "").strip()
            failed_code = str(job.get("jd_failed_code") or "").strip()
            if not job_id or not failed_code:
                continue
            taxonomy_code = resolve_code(failed_code)
            taxonomy = ERROR_TAXONOMY.get(taxonomy_code, {})
            reason = str(job.get("jd_failed_reason") or "").strip()
            if not reason:
                reason = taxonomy_reason(
                    taxonomy_code, platform,
                    fallback=failed_code_label(failed_code, platform) or "JD 抓取失败",
                )
            for run_id in target_run_ids:
                existing = store.get_pending_result(run_id, job_id)
                store.insert_pending_result(
                    run_id,
                    job_id,
                    failure_stage=stage,
                    retryable=bool(taxonomy.get("retryable", True)),
                    attempts=int((existing or {}).get("attempts") or 0) + 1,
                    origin_zone=str((existing or {}).get("origin_zone") or "kept"),
                    ai_payload_json={
                        "reason": reason,
                        "evidence": failed_code,
                        "evidence_detail": str(job.get("jd_failed_evidence") or ""),
                        "next_action": "retry_jd",
                    },
                    failed_code=failed_code,
                    platform=platform,
                )
            events.append(("job_fail", {
                "stage": stage,
                "job_id": job_id,
                "failed_code": failed_code,
                "reason": reason,
                "evidence_detail": str(job.get("jd_failed_evidence") or ""),
            }))
        if events:
            store.append_task_events(task_run_id, events)

    (_browser_lock, _browser_busy, _latest_paused_run_for_browser_close,
     _close_paused_run_browser, _has_active_pipeline_task,
     _project_browser_accounts) = build_browser_support(
        store, _pipeline_tasks, _pipeline_lock,
        _account_for_run, _activate_run_browser)

    # 022：JD 抓取卡死防护（独立监控线程，批次经 fetch_job_details 登记）
    from webui.pipeline_guard import PipelineGuard
    _pipeline_guard = PipelineGuard(
        write_run=_write_run_unless_finished, store=store,
        tasks=_pipeline_tasks, lock=_pipeline_lock,
        record_pause_failure=_record_pause_failure,
        release_worker_resume_claims=_release_worker_resume_claims,
    )

    ctx = PipelineContext(
        app=app,
        store=store,
        tasks=_pipeline_tasks,
        lock=_pipeline_lock,
        resume_claims=_resume_claims,
        executor=_pipeline_executor,
        write_run=_write_run_unless_finished,
        make_cdp_source=_make_cdp_source,
        tuning_round_runner=_tuning_round_runner,
        is_user_finished=_is_user_finished,
        release_worker_resume_claims=_release_worker_resume_claims,
        record_pause_failure=_record_pause_failure,
        account_for_run=_account_for_run,
        activate_task_browser=_activate_task_browser,
        clear_auto_screen=_clear_auto_screen,
        schedule_pipeline_task_cleanup=_schedule_pipeline_task_cleanup,
        persist_jd_job_failures=_persist_jd_job_failures,
        load_legacy_advanced_settings=_load_legacy_advanced_settings,
        event_stage_names=_EVENT_STAGE_NAMES,
        screen_stage_messages=_SCREEN_STAGE_MESSAGES,
        operational_errors=_OPERATIONAL_ERRORS,
        msg_user_finished=_MSG_USER_FINISHED,
        msg_user_stopped_scrape=_MSG_USER_STOPPED_SCRAPE,
        msg_user_stopped_screen=_MSG_USER_STOPPED_SCREEN,
        recrawl_overall_percent=_recrawl_overall_percent,
        screen_overall_percent=_screen_overall_percent,
        prune_history_best_effort=_prune_history_best_effort,
        runtime_mode=_runtime_mode,
        run_tuning_manifest_child=_run_tuning_manifest_child,
        save_legacy_advanced_settings=_save_legacy_advanced_settings,
        invalidate_login_cache=_invalidate_login_cache,
        activate_run_browser=_activate_run_browser,
        scope_previews=scope_previews,
        check_resume_block=_check_resume_block,
        check_tuning_lease_conflict=_check_tuning_lease_conflict,
        claim_pipeline_task_id=_claim_pipeline_task_id,
        release_pipeline_claim=_release_pipeline_claim,
        register_pipeline_task=_register_pipeline_task,
        claim_recrawl_start=_claim_recrawl_start,
        claim_resume=_claim_resume,
        release_resume_claim=_release_resume_claim,
        ensure_scrape_source=_ensure_scrape_source,
        consume_auto_screen=_consume_auto_screen,
        runner=runner,
        workbench_runner=workbench_runner,
        job_feedback_service=job_feedback_service,
        history_service=history_service,
        resume_service=resume_service,
        pipeline_guard=_pipeline_guard,
    )

    # 021 B6：定义晚于组装点，原地补绑定（浏览器锁共享助手）
    ctx.browser_lock = _browser_lock
    ctx.browser_busy = _browser_busy
    ctx.close_paused_run_browser = _close_paused_run_browser
    ctx.has_active_pipeline_task = _has_active_pipeline_task
    ctx.project_browser_accounts = _project_browser_accounts

    return ctx
