"""列表抓取后台任务 runner（021 B5 外迁自 webui/app.py）。

组合抓取执行体：冻结运行时读取、CDP source 创建、逐组合抓取与页级
checkpoint 持久化、断点续抓合并、终态判定（成功/取消/阻断暂停/失败）与
内存任务状态同步。共享运行态经 ctx 取用；threading 模块级直连（031 B9
门面拆除）；延迟 import（run_search、expand_combinations）保持原位原语义。
"""

from __future__ import annotations
import threading

import time

from webui.diagnostics import record_failure
from webui.task_runners import _classify_scrape_block

def run_pipeline_task(ctx,
    task_id, script_params, execution_config=None, frozen_scope=None,
):
    from webui.pipeline_exec import expand_combinations, run_search
    with ctx.lock:
        task = ctx.tasks.get(task_id)
        if task is None:
            task = {
                "kind": "scrape", "status": "queued", "progress": {},
                "logs": [], "result": None, "error": "",
                "started_at": int(time.time() * 1000), "finished_at": None,
            }
            ctx.tasks[task_id] = task
        task["status"] = "running"
        task["script_params"] = script_params  # 断点续抓需要原始参数
        if execution_config is not None:
            task["config_digest"] = execution_config.config_digest
        if frozen_scope is not None:
            task["scope_digest"] = frozen_scope.scope_digest
        task.setdefault("page_flush_lock", threading.Lock())
        task.setdefault("page_persist_seq", 0)
        task.setdefault("last_page_snapshot_at", 0)
    if ctx.is_user_finished(task_id):
        with ctx.lock:
            current = ctx.tasks.get(task_id)
            if current is not None:
                current["status"] = "cancelled"
                current["error"] = ctx.msg_user_finished
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
        return
    ctx.activate_task_browser(task_id)

    def on_progress(snapshot):
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return
            task["progress"] = snapshot
            msg = snapshot.get("message")
            if msg:
                task["logs"].append(msg)

    try:
        # 取出停止信号传给 run_search；cancel 接口 set 它后，
        # run_search 会在下一个组合边界退出，或因浏览器被关而抛错。
        with ctx.lock:
            task_ref = ctx.tasks.get(task_id, {})
            stop_event = task_ref.get("stop_event")
            skip_combos = task_ref.get("skip_combos") or None
            old_jobs = task_ref.get("old_jobs") or []
            # T403: 从 task dict 读取冻结 runtime，不读当前 UI/活动账号/默认端口
            frozen_platform = task_ref.get("platform") or "boss"
            frozen_cdp_port = task_ref.get("cdp_port")
            frozen_profile_key = task_ref.get("profile_key")
            frozen_browser_account = task_ref.get("browser_account")
        if ctx.store.get_screening_run(task_id) is None:
            ctx.store.create_screening_run(
                task_id,
                source_count=len(expand_combinations(script_params)),
                execution_params={
                    "script_params": script_params,
                    "browser_account": ctx.account_for_run(),
                    "execution_config": (
                        execution_config.to_dict()
                        if execution_config is not None else None
                    ),
                    "frozen_scope": (
                        frozen_scope.to_dict() if frozen_scope is not None else None
                    ),
                },
                backend_version=ctx.backend_version,
            )
        ctx.write_run(
            task_id, status="running", current_stage="scrape"
        )
        ctx.store.append_task_event(task_id, "stage_start", {"stage": "scrape"})
        # T403: 从冻结 runtime 创建 source，禁止读取当前 UI/活动账号/默认端口
        source = ctx.make_cdp_source(
            platform=frozen_platform,
            browser_account=frozen_browser_account,
            cdp_port=frozen_cdp_port,
            profile_key=frozen_profile_key,
            run_id=task_id,
        )
        if source is None:
            completed = sorted(skip_combos or [])
            reason = "连不上调试浏览器，请启动 Chrome 调试端口后继续"
            ctx.write_run(
                task_id,
                status="paused",
                current_stage="scrape",
                error_code="source_cdp_unavailable",
                error_reason=reason,
                processed_count=len(completed),
            )
            ctx.store.save_checkpoint(task_id, "scrape", completed)
            ctx.store.append_task_event(task_id, "pause", {
                "stage": "scrape",
                "code": "source_cdp_unavailable",
                "completed_combos": len(completed),
            })
            ctx.record_pause_failure(
                task_id, "scrape", "source_cdp_unavailable", reason,
                processed=len(completed), total=len(completed),
            )
            with ctx.lock:
                task = ctx.tasks.get(task_id)
                if task is not None:
                    task["status"] = "paused"
                    task["error"] = reason
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return

        def on_combo_done(combo_key, jobs, completed_combos, *, outcome=None):
            # T404: 先持久化 source attempt，再推进 combo result。
            # 持久化失败时抛异常，run_search 会捕获并硬停止。
            attempt_no = 1
            try:
                latest = ctx.store.get_latest_source_attempt(task_id, combo_key)
                if latest is not None:
                    attempt_no = latest["attempt_no"] + 1
            except ctx.operational_errors:
                pass
            if outcome is not None:
                outcome_kind = "empty" if outcome.empty_result else "non_empty"
                ctx.store.append_source_attempt(
                    run_id=task_id,
                    platform=frozen_platform,
                    combo_key=combo_key,
                    attempt_no=attempt_no,
                    input_hash=outcome.input_hash,
                    outcome_kind=outcome_kind,
                    job_count=len(jobs),
                    empty_evidence=outcome.empty_evidence,
                )
            else:
                ctx.store.append_source_attempt(
                    run_id=task_id,
                    platform=frozen_platform,
                    combo_key=combo_key,
                    attempt_no=attempt_no,
                    outcome_kind="non_empty",
                    job_count=len(jobs),
                )
            ctx.store.save_scrape_combo_result(
                task_id, combo_key, jobs, completed_combos
            )
            ctx.store.append_task_events(task_id, [
                ("job_success", {
                    "stage": "scrape", "combo_key": combo_key,
                    "job_id": str(job.get("job_id") or job.get("source_url") or ""),
                })
                for job in jobs if isinstance(job, dict)
            ])

        def on_page_completed(event):
            """每完成一页原子保存岗位快照与页级 checkpoint。"""
            lock = None
            with ctx.lock:
                task_ref = ctx.tasks.get(task_id)
                if task_ref is not None:
                    lock = task_ref.get("page_flush_lock")
            if lock is not None:
                lock.acquire()
            try:
                ctx.store.save_scrape_page_progress(
                    task_id, str(event.get("combo_key") or ""), event)
            finally:
                if lock is not None:
                    lock.release()
            with ctx.lock:
                task_ref = ctx.tasks.get(task_id)
                if task_ref is not None:
                    task_ref["last_page_snapshot_at"] = time.time()
                    task_ref["page_persist_seq"] = int(
                        task_ref.get("page_persist_seq") or 0) + 1
                    task_ref["last_page_progress"] = dict(event)

        try:
            page_rows = ctx.store.load_scrape_page_progress(task_id)
        except ctx.operational_errors:
            page_rows = []
        skip_set = set(skip_combos or [])
        resume_pages = {
            row["combo_key"]: row["resume_page"]
            for row in page_rows if row["combo_key"] not in skip_set
        }
        resume_jobs = {}
        for row in page_rows:
            if row["combo_key"] in skip_set:
                continue
            try:
                resume_jobs[row["combo_key"]] = ctx.store.load_scrape_run_jobs(
                    task_id, combo_key=row["combo_key"])
            except ctx.operational_errors:
                resume_jobs[row["combo_key"]] = []

        def _record_combo_issue(combo_key, entry):
            """把运行中组合问题（如登录失效复核）写进任务事件日志。"""
            try:
                ctx.store.append_task_event(
                    task_id, "combo_issue",
                    {**(entry or {}), "combo_key": str(combo_key)},
                )
            except ctx.operational_errors:
                pass
        result = run_search(
            script_params, source,
            pages=(
                frozen_scope.pages_per_combination
                if frozen_scope is not None else int(script_params.get("pages") or 3)
            ), progress=on_progress,
            artifact_dir=ctx.app.config["RESULT_DIR"],
            stop_event=stop_event,
            skip_combos=skip_combos,
            on_combo_done=on_combo_done,
            execution_config=execution_config,
            on_page_completed=on_page_completed,
            on_issue=_record_combo_issue,
            resume_pages=resume_pages,
            resume_jobs=resume_jobs,
            task_id=task_id,
            task_event_store=ctx.store,
        )
        # 断点续抓：合并旧结果（按 job_id 去重）
        merged_total = None
        if old_jobs and result.get("ok"):
            existing_ids = {j.get("job_id") or j.get("source_url") or ""
                            for j in result["jobs"]}
            for job in old_jobs:
                jid = job.get("job_id") or job.get("source_url") or ""
                if jid and jid not in existing_ids:
                    result["jobs"].append(job)
                    existing_ids.add(jid)
            merged_total = len(result["jobs"])
            result["total_matched"] = merged_total
            result["total_scraped"] = merged_total
        # 终态先按真实结果判定并写 DB，不依赖内存 task 是否存活。
        # 续跑会用同一 run_id 重新注册内存任务；旧任务被清理/替换时
        # 内存字段可以跳过同步，但 DB 终态必须照常写入，否则 run 会
        # 永远停在 running（数据已齐却没收尾）。
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if stop_event is not None and stop_event.is_set():
                ctx.write_run(
                    task_id, status="cancelled", current_stage="scrape",
                    processed_count=len(result.get("completed_combos") or []),
                    error_reason=ctx.msg_user_stopped_scrape,
                )
                _terminal_status = "cancelled"
            elif result.get("ok"):
                completed = list(result.get("completed_combos") or [])
                ctx.write_run(
                    task_id, status="succeeded", current_stage="scrape",
                    processed_count=len(completed),
                    source_count=int(result.get("combinations") or len(completed)),
                    total_scraped=int(result.get("total_scraped") or 0),
                )
                ctx.store.append_task_event(task_id, "stage_complete", {
                    "stage": "scrape",
                    "combinations": int(result.get("combinations") or len(completed)),
                    "total_scraped": int(result.get("total_scraped") or 0),
                })
                _terminal_status = "done"
            else:
                # 切片4：列表抓取失败时区分"系统性阻断暂停" vs "真失败"
                # 部分组合已完成 + 错误含阻断关键字 → paused + checkpoint
                # 服务重启后用户点继续可从 DB checkpoint 恢复 completed_combos
                completed = list(result.get("completed_combos") or [])
                err_msg = str(result.get("error", "") or "")
                _pause_code = (
                    str(result.get("hard_stop_code") or "")
                    or _classify_scrape_block(err_msg)
                )
                if result.get("hard_stop") and _pause_code:
                    ctx.write_run(
                        task_id, status="paused", error_code=_pause_code,
                        current_stage="scrape",
                        processed_count=len(completed),
                        source_count=int(result.get("combinations") or 0),
                        error_reason=err_msg,
                        total_scraped=int(result.get("total_scraped") or 0))
                    ctx.store.save_checkpoint(task_id, "scrape", completed)
                    ctx.store.append_task_event(
                        task_id, "pause",
                        {"stage": "scrape", "code": _pause_code,
                         "completed_combos": len(completed)})
                    ctx.record_pause_failure(
                        task_id, "scrape", _pause_code, err_msg,
                        processed=len(completed),
                        total=int(result.get("combinations") or 0),
                    )
                    _terminal_status = "paused"
                else:
                    ctx.store.append_task_event(task_id, "job_fail", {
                        "stage": "scrape", "error": err_msg,
                        "failed_code": _pause_code or "scrape_failed",
                    })
                    ctx.write_run(
                        task_id, status="failed", current_stage="scrape",
                        processed_count=len(completed),
                        source_count=int(result.get("combinations") or 0),
                        error_reason=err_msg,
                        total_scraped=int(result.get("total_scraped") or 0),
                    )
                    _terminal_status = "failed"
            # 内存任务状态同步（task 可能已在续跑时被替换/清理；DB 终态
            # 已在上方保证写入，内存同步仅当对象仍指向本 run 时执行）。
            if task is not None:
                task["result"] = result
                task["error"] = result.get("error", "")
                if merged_total is not None:
                    progress = dict(task.get("progress") or {})
                    progress["total_scraped"] = merged_total
                    progress["message"] = (
                        f"完成：抓取 {merged_total} 条，去重 {merged_total} 条"
                    )
                    progress["total_matched"] = merged_total
                    task["progress"] = progress
                if _terminal_status == "cancelled":
                    task["status"] = "cancelled"
                    task["error"] = ctx.msg_user_stopped_scrape
                elif _terminal_status == "done":
                    task["status"] = "done"
                elif _terminal_status == "paused":
                    task["status"] = "paused"
                    task["error"] = (
                        f"列表抓取被阻断（{_pause_code}）："
                        f"已完成 {len(completed)} 个组合，已保存断点。"
                        "在自动化浏览器中处理后点「继续」"
                    )
                else:
                    task["status"] = "failed"
        if _terminal_status in ("cancelled", "failed"):
            ctx.clear_auto_screen(task_id)
        ctx.schedule_pipeline_task_cleanup(task_id)
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
    except Exception as exc:
        with ctx.lock:
            task = ctx.tasks.get(task_id)
        stop_event = task.get("stop_event") if task is not None else None
        cancelled = stop_event is not None and stop_event.is_set()
        error_message = (
            ctx.msg_user_stopped_scrape if cancelled
            else f"执行异常：{type(exc).__name__}"
        )
        if not cancelled:
            record_failure(
                ctx.store, task_id, stage="scrape",
                error_code="internal_error", reason=error_message,
                correlation_id=task_id,
                diagnostics={}, exception=exc, include_traceback=True,
            )
        persistence_error = None
        try:
            run = ctx.store.get_screening_run(task_id)
            if run and run.get("status") in ("queued", "running", "paused"):
                ctx.write_run(
                    task_id,
                    status="cancelled" if cancelled else "failed",
                    current_stage="scrape",
                    error_reason=error_message,
                )
        except ctx.operational_errors as persist_exc:
            persistence_error = type(persist_exc).__name__
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is not None:
                if ctx.is_user_finished(task_id):
                    task["status"] = "cancelled"
                    task["error"] = ctx.msg_user_finished
                else:
                    task["status"] = (
                        "cancelled" if cancelled and persistence_error is None else "failed"
                    )
                    task["error"] = (
                        error_message if persistence_error is None
                        else f"{error_message}；状态保存失败：{persistence_error}"
                    )
        ctx.schedule_pipeline_task_cleanup(task_id)
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
