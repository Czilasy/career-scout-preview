"""批量重抓后台任务 runner（021 B4 外迁自 webui/app.py）。

补 JD + 重判的批量重抓执行体：冻结平台/浏览器身份激活、逐岗位详情抓取、
AI 重判、进度事件与检查点持久化、暂停/续跑。共享运行态与可 patch 符号
（ai_service/threading 等）经 ctx 取用；延迟 import（match_jds、
fetch_job_details 等）保持原位原语义。
"""

from __future__ import annotations

import time

from webui.diagnostics import record_failure
from webui.workbench import normalize_job_link_for_platform


def run_recrawl_task(ctx, task_id, job_ids, profile_summary, source_run_id="",
                      completed_job_ids=None, profile_facts=None,
                      execution_config=None):
    """批量重抓后台任务：补 JD + 重判，进度与结果通过 _pipeline_tasks 暴露。

    切片8：``source_run_id`` 用于持久化（recrawl task_id 不是 screening_runs 行）。
    暂停时写入 store（用 task_id 作为 run_id 占位），保存 checkpoint，
    服务重启后用户可点继续从 checkpoint 恢复。

    ``execution_config``: 可选 — 续跑时由调用方传入本轮刷新后的配置快照，
    不再回退到 legacy JSON 晚绑定读取；未提供时保持旧行为（向后兼容）。
    """
    from webui.ai import match_jds
    from webui.pipeline_exec import (
        close_debug_chrome,
        ensure_chrome_ready,
        failed_code_label,
        fetch_job_details,
    )

    # 画像兜底：前端刷新后传空，从落盘结果里恢复（跟本轮抓取绑定，下轮覆盖）
    if not profile_summary.strip():
        payload = ctx.store.load_latest_pipeline_result(source_run_id or None)
        if payload:
            profile_summary = str(
                (payload.get("result") or {}).get("profile_summary", "")
            )
            profile_facts = (
                (payload.get("result") or {}).get("profile_facts") or None
            )

    with ctx.lock:
        task = ctx.tasks.get(task_id)
        if task is None:
            task = {
                "kind": "recrawl", "status": "queued", "progress": {},
                "logs": [], "result": None, "error": "",
                "started_at": int(time.time() * 1000), "finished_at": None,
                "stop_event": ctx.threading.Event(),
            }
            ctx.tasks[task_id] = task
        if task.get("status") == "cancelled":
            return
        if ctx.is_user_finished(task_id):
            ctx.release_worker_resume_claims(task)
            return
        task["status"] = "running"
        stop_event = task.get("stop_event")

    # T403: 从 task dict 读取冻结平台/浏览器身份，fallback 到 run execution_params
    with ctx.lock:
        _t_ref = ctx.tasks.get(task_id, {})
        frozen_platform = _t_ref.get("platform")
        frozen_cdp_port = _t_ref.get("cdp_port")
        frozen_profile_key = _t_ref.get("profile_key")
        frozen_browser_account = _t_ref.get("browser_account")
    try:
        _run_ref = ctx.store.get_screening_run(task_id)
        _params_ref = (_run_ref or {}).get("execution_params") or {}
        frozen_platform = frozen_platform or _params_ref.get("platform") or "boss"
        frozen_cdp_port = frozen_cdp_port or _params_ref.get("cdp_port")
        frozen_profile_key = frozen_profile_key or _params_ref.get("profile_key")
        frozen_browser_account = (
            frozen_browser_account or _params_ref.get("browser_account")
        )
    except ctx.operational_errors:
        frozen_platform = frozen_platform or "boss"

    # A recrawl can be resumed after a server restart, when its in-memory
    # task is incomplete or absent.  Rebind after every DB fallback so the
    # CDP preflight below always compares port 9223 with the frozen Zhilian
    # profile, not the profile selected by a previous request.
    ctx.activate_task_browser(
        task_id,
        platform=str(frozen_platform or "boss"),
        browser_account=(
            str(frozen_browser_account) if frozen_browser_account else None
        ),
    )

    last_event_stage = None

    def emit(**kw):
        nonlocal last_event_stage
        stage = str(kw.get("stage", ""))
        current = int(kw.get("current") or 0)
        total = int(kw.get("total") or 0)
        kw["overall_percent"] = ctx.recrawl_overall_percent(stage, current, total)
        if not kw.get("message"):
            kw["message"] = ctx.screen_stage_messages.get(stage, "")
        event_stage = ctx.event_stage_names.get(stage)
        stage_events = []
        if stage == "done" and last_event_stage:
            stage_events.append(("stage_complete", {"stage": last_event_stage}))
            last_event_stage = None
        elif event_stage and event_stage != last_event_stage:
            if last_event_stage:
                stage_events.append(("stage_complete", {"stage": last_event_stage}))
            stage_events.append(("stage_start", {"stage": event_stage}))
            last_event_stage = event_stage
        if stage_events:
            ctx.store.append_task_events(task_id, stage_events)
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is None:
                return
            t["progress"] = kw

    def _stop_requested():
        return stop_event is not None and stop_event.is_set()

    def _pause_recrawl_source_unavailable(reason):
        """Persist a CDP-wide recrawl hard stop before exposing paused state."""
        code = "source_cdp_unavailable"
        completed = set(completed_job_ids or set()) | set(fetched_jd)
        failed_jobs = [
            {
                "job_id": str(job.get("job_id") or ""),
                "jd": "",
                "jd_failed_code": code,
                "jd_failed_reason": reason,
            }
            for job in no_jd
            if str(job.get("job_id") or "") not in completed
        ]
        ctx.persist_jd_job_failures(
            task_id,
            failed_jobs,
            stage="recrawl_fetch_jd",
            source_run_id=source_run_id,
            platform=frozen_platform,
        )
        ctx.store.save_checkpoint(task_id, "recrawl_jd", sorted(completed))
        ctx.write_run(
            task_id,
            status="paused",
            error_code=code,
            error_reason=reason,
            current_stage="recrawl_fetch_jd",
            processed_count=len(completed),
        )
        ctx.store.append_task_event(
            task_id,
            "pause",
            {
                "stage": "recrawl_fetch_jd",
                "code": code,
                "processed": len(completed),
                "total": len(no_jd),
            },
        )
        ctx.record_pause_failure(
            task_id, "recrawl_fetch_jd", code, reason,
            processed=len(completed), total=len(no_jd),
        )
        publish_recrawl_updates()
        with ctx.lock:
            current = ctx.tasks.get(task_id)
            if current is not None:
                current["status"] = "paused"
                current["error"] = reason
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))

    def _stop_mode():
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is None or t.get("stop_event") is None or not t["stop_event"].is_set():
                return None
            return "pause" if t.get("stop_mode") == "pause" else "cancel"

    def _mark_recrawl_paused(processed=0, stage="recrawl_ai"):
        """用户暂停重抓：保留 checkpoint 并写为 paused。"""
        ctx.write_run(
            task_id, status="paused", error_code="user_paused",
            current_stage=stage, processed_count=processed,
            error_reason="用户已暂停重抓，结果已保留",
        )
        ctx.store.append_task_event(task_id, "pause", {
            "stage": stage, "code": "user_paused", "processed": processed,
        })
        with ctx.lock:
            current = ctx.tasks.get(task_id)
            if current is not None:
                current["status"] = "paused"
                current["error"] = "重抓已暂停，结果已保留"
                ctx.release_worker_resume_claims(current)

    updates: dict = {}

    def publish_recrawl_updates():
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is not None:
                task["result"] = {"updates": dict(updates)}
    try:
        payload = ctx.store.load_latest_pipeline_result(source_run_id or None)
        # 017-US4: 重抓目标由调用方显式指定（source_run_id 必传），不再回退"最新"
        run_id = source_run_id
        jobs = (payload or {}).get("result", {}).get("jobs", []) if payload else []
        # 历史结果页可能没有把画像正文留在前端状态；重抓必须从来源
        # 轮次恢复画像，否则已有 JD 的岗位会被静默跳过 AI。
        if not profile_summary.strip():
            profile_summary = str(
                ((payload or {}).get("result") or {}).get("profile_summary")
                or ""
            ).strip()
        if not profile_summary.strip() and source_run_id:
            try:
                profile_summary = str(
                    (ctx.store.get_screening_run(source_run_id) or {}).get("profile_summary")
                    or ""
                ).strip()
            except ctx.operational_errors:
                pass
        by_id: dict[str, dict] = {}
        for j in jobs:
            if not isinstance(j, dict):
                continue
            pid = ctx.recrawl_job_key(j)
            if pid:
                by_id.setdefault(pid, j)
        targets = [by_id[jid] for jid in job_ids if jid in by_id]
        total = len(targets)
        emit(stage="fetch_jd", current=0, total=total,
             message=f"准备重抓 {total} 个待确认岗位…")
        if not targets:
            emit(stage="done", current=0, total=0, message="没有可重抓的岗位")
            ctx.write_run(
                task_id, status="failed",
                error_code="no_recrawlable_targets",
                error_reason="0 个可重抓岗位",
                current_stage="done",
            )
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "failed"
                    t["error"] = "0 个可重抓岗位"
                    t["result"] = {"updates": {}}
            ctx.schedule_pipeline_task_cleanup(task_id)
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return

        settings = ctx.store.get_ai_settings()
        cred_ref = ctx.store.get_credential_ref()
        api_key = ctx.ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
        endpoint = settings.get("endpoint_url", "")
        model = settings.get("model", "")
        has_ai = bool(api_key and endpoint)

        # 1) 缺 JD 的先补抓（复用详情 CDP 通道，内部已按 detail_batch_size 分批 + 冷却）
        no_jd = []
        for j in targets:
            if str(j.get("jd", "")).strip():
                continue
            # ``normalize_job_link`` only accepts BOSS URLs.  Using it
            # for an already-frozen Zhilian task erased every target URL
            # before fetch_job_details could invoke CDP.
            url = normalize_job_link_for_platform(
                j.get("source_url") or j.get("job_link") or j.get("canonical_url") or "",
                platform=frozen_platform,
            )
            if url:
                stable_id = ctx.recrawl_job_key(j)
                no_jd.append({
                    "platform": frozen_platform,
                    "platform_job_id": str(j.get("platform_job_id") or stable_id),
                    "job_id": stable_id,
                    # Zhilian's parallel detail runner accepts only jobs
                    # with canonical_url.  Recrawl previously rebuilt this
                    # payload without it, so every target was rejected
                    # locally as source_invalid_output before CDP/Chrome
                    # was ever invoked.
                    "source_url": url, "job_link": url,
                    "canonical_url": url,
                })
        fetched_jd: dict = {}
        detail_jobs: list = []
        actual_processed = 0
        if no_jd:
            chrome_ok, chrome_err = ensure_chrome_ready(
                frozen_cdp_port, minimize_after_launch=True,
            )
            if chrome_ok:
                source = ctx.make_cdp_source(
                    platform=frozen_platform,
                    browser_account=frozen_browser_account,
                    cdp_port=frozen_cdp_port,
                    profile_key=frozen_profile_key,
                    run_id=task_id,
                )
                if source is not None:
                    def _jd_progress(done, tot):
                        emit(stage="fetch_jd", current=min(done, total), total=total,
                             message=f"抓取 JD {min(done, total)}/{total}")
                    # 024：详情人形模拟随当前档位下发（custom/取不到时零仿真）
                    _simulation_mode = None
                    try:
                        _sel = ctx.store.get_advanced_config_state().get(
                            "active_selection"
                        )
                        if _sel in ("stable", "balanced", "extreme"):
                            _simulation_mode = _sel
                    except Exception:
                        _simulation_mode = None
                    detail = fetch_job_details(
                        no_jd, source, artifact_dir=ctx.app.config["RESULT_DIR"],
                        stop_event=stop_event, progress=_jd_progress,
                        completed_job_ids=completed_job_ids,
                        execution_config=execution_config,
                        simulation_mode=_simulation_mode,
                    )
                    detail_jobs = detail.get("jobs", [])
                    for j in detail_jobs:
                        jid = ctx.recrawl_job_key(j)
                        jd = str(j.get("jd", "")).strip()
                        if jid and jd:
                            fetched_jd[jid] = jd
                    completed_jd_ids = set(completed_job_ids or set()) | set(fetched_jd)
                    ctx.store.save_recrawl_jd_and_checkpoint(
                        run_id, task_id, fetched_jd, completed_jd_ids
                    )
                    for jid, jd in fetched_jd.items():
                        updates.setdefault(jid, {})["jd"] = jd
                    actual_processed += len(fetched_jd)
                    publish_recrawl_updates()
                    if detail.get("hard_stop"):
                        # 暂停，不关浏览器（用户需要它处理验证码/登录）
                        _hs_code = detail.get("hard_stop_code") or "source_blocked"
                        _hs_label = failed_code_label(_hs_code, frozen_platform)
                        _hs_hint = next((
                            str(job.get("jd_failed_reason") or "").strip()
                            for job in detail_jobs or []
                            if job.get("jd_failed_reason")
                        ), "")
                        _hs_reason = _hs_hint if _hs_hint and _hs_hint != _hs_label else _hs_label
                        ctx.persist_jd_job_failures(
                            task_id,
                            detail_jobs,
                            stage="recrawl_fetch_jd",
                            source_run_id=source_run_id,
                            platform=frozen_platform,
                        )
                        # 切片8：持久化暂停状态 + checkpoint（已抓 JD 的 job_id）
                        ctx.write_run(
                            task_id, status="paused", error_code=_hs_code,
                            current_stage="recrawl_fetch_jd",
                            processed_count=len(completed_jd_ids),
                            error_reason=_hs_reason)
                        ctx.store.append_task_event(
                            task_id, "pause",
                            {"stage": "recrawl_fetch_jd", "code": _hs_code,
                             "fetched": len(fetched_jd), "total": len(no_jd)})
                        ctx.record_pause_failure(
                            task_id, "recrawl_fetch_jd", _hs_code, _hs_reason,
                            processed=len(completed_jd_ids), total=len(no_jd),
                        )
                        with ctx.lock:
                            t = ctx.tasks.get(task_id)
                            if t is not None:
                                t["status"] = "paused"
                                t["error"] = (f"重抓 JD 时{_hs_reason}，已抓部分已保存；"
                                              "请在自动化浏览器中处理，完成后点「继续」")
                        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
                        return
                    if detail.get("stopped"):
                        close_debug_chrome(frozen_cdp_port)
                        if _stop_mode() == "pause":
                            _mark_recrawl_paused(
                                processed=len(completed_jd_ids),
                                stage="recrawl_fetch_jd")
                        else:
                            with ctx.lock:
                                t = ctx.tasks.get(task_id)
                                if t is not None:
                                    t["status"] = "cancelled"
                                    t["error"] = "用户已停止重抓"
                            ctx.schedule_pipeline_task_cleanup(task_id)
                            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
                        return
                    close_debug_chrome(frozen_cdp_port)
                else:
                    reason = "CDP 抓取源不可用，请确认调试浏览器后继续"
                    emit(stage="fetch_jd", current=0, total=total, message=reason)
                    _pause_recrawl_source_unavailable(reason)
                    return
            else:
                reason = f"调试浏览器未就绪（{chrome_err}），请处理后继续"
                emit(stage="fetch_jd", current=0, total=total, message=reason)
                _pause_recrawl_source_unavailable(reason)
                return

        # A recrawl must do real work before it can reach a terminal
        # success/partial state.  An empty CDP response used to fall
        # through to the AI branch with no jobs and was reported as
        # "completed" even though nothing was fetched or judged.
        if no_jd and not fetched_jd:
            reason = (
                f"重抓未处理任何岗位：浏览器未返回 JD（0/{len(no_jd)}），"
                "请检查自动化浏览器后点「继续」"
            )
            failed_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "jd": "",
                    "jd_failed_code": str(job.get("jd_failed_code") or "source_invalid_output"),
                    "jd_failed_reason": str(job.get("jd_failed_reason") or "浏览器未返回岗位详情"),
                }
                for job in (detail_jobs or no_jd)
            ]
            ctx.persist_jd_job_failures(
                task_id, failed_jobs, stage="recrawl_fetch_jd",
                source_run_id=source_run_id, platform=frozen_platform,
            )
            ctx.write_run(
                task_id, status="paused", error_code="recrawl_no_work",
                current_stage="recrawl_fetch_jd", processed_count=0,
                error_reason=reason,
            )
            ctx.store.append_task_event(task_id, "pause", {
                "stage": "recrawl_fetch_jd",
                "code": "recrawl_no_work",
                "processed": 0, "total": len(no_jd),
            })
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "paused"
                    t["error"] = reason
                    t["result"] = {"updates": updates}
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return

        # 补抓仍失败的岗位：把具体原因回写前端（验证码/限流等）
        for j in detail_jobs:
            jid = str(j.get("job_id", ""))
            code = j.get("jd_failed_code", "")
            if jid and code and jid not in fetched_jd:
                label = failed_code_label(code, frozen_platform)
                detail_reason = str(j.get("jd_failed_reason") or "").strip()
                reason = (
                    f"未抓到 JD（{detail_reason}），无法精筛"
                    if detail_reason else
                    (f"未抓到 JD（{label}），无法精筛" if label else
                     "未抓到 JD，无法精筛")
                )
                updates.setdefault(jid, {})["verdict_reason"] = reason
        publish_recrawl_updates()

        # 2) 有 JD 且有画像的，重跑 AI 精筛
        if not has_ai:
            reason = "AI 未配置，已保留补抓结果；配置 AI 后可继续判定"
            emit(stage="screen_b", current=0, total=total, message=reason)
            ctx.write_run(
                task_id, status="paused", error_code="ai_key_invalid",
                current_stage="recrawl_ai", processed_count=0,
                error_reason=reason,
            )
            ctx.store.save_checkpoint(task_id, "recrawl_ai", [])
            ctx.store.append_task_event(task_id, "pause", {
                "stage": "recrawl_ai", "code": "ai_key_invalid",
                "processed": 0, "total": total,
            })
            ctx.record_pause_failure(
                task_id, "recrawl_ai", "ai_key_invalid", reason,
                processed=0, total=total,
            )
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "paused"
                    t["error"] = reason
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return
        elif not profile_summary.strip():
            reason = "缺少本轮求职画像，无法进行 AI 重判；请回到本轮画像后继续"
            emit(stage="screen_b", current=0, total=total, message=reason)
            ctx.write_run(
                task_id, status="paused", error_code="recrawl_profile_missing",
                current_stage="recrawl_ai", processed_count=0,
                error_reason=reason,
            )
            ctx.store.append_task_event(task_id, "pause", {
                "stage": "recrawl_ai", "code": "recrawl_profile_missing",
                "processed": 0, "total": total,
            })
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "paused"
                    t["error"] = reason
                    t["result"] = {"updates": updates}
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return
        else:
            to_judge = []
            for j in targets:
                jid = ctx.recrawl_job_key(j)
                jd = str(j.get("jd", "")).strip() or fetched_jd.get(jid, "")
                if jd:
                    jj = dict(j)
                    jj["job_id"] = jid
                    to_judge.append(jj)
            if to_judge:
                _adv = ctx.load_legacy_advanced_settings()
                # 高级设置续跑生效：优先用刷新后的配置，不再靠 legacy JSON 兜底
                if execution_config is not None:
                    match_batch = int(execution_config.match_batch_size)
                else:
                    match_batch = int(_adv.get("match_batch_size") or 4)
                recrawl_completed_ids = set(completed_job_ids or set())
                verdicts: dict = ctx.store.load_screening_verdicts(task_id)
                to_judge = [
                    job for job in to_judge
                    if str(job.get("job_id", "")) not in recrawl_completed_ids
                ]
                _recrawl_ai_pause = False
                # 三通道：从源 run 快照取筛选条件与画像事实（老轮无画像事实则退化）
                recrawl_criteria = {}
                try:
                    _src_run = ctx.store.get_screening_run(run_id)
                    _frozen = (_src_run or {}).get("frozen_filters") or {}
                    if isinstance(_frozen, dict):
                        recrawl_criteria = {
                            k: v for k, v in _frozen.items()
                            if k != "profile_summary"
                        }
                except ctx.operational_errors:
                    recrawl_criteria = {}
                for start in range(0, len(to_judge), match_batch):
                    if _stop_requested():
                        break
                    chunk = to_judge[start:start + match_batch]
                    try:
                        res = match_jds(
                            chunk, profile_summary, endpoint, api_key,
                            model=model, raise_on_systemic=True,
                            criteria=recrawl_criteria,
                            profile_facts=profile_facts,
                            execution_config=execution_config,
                        )
                    except ctx.ai_service.AISecurityError as _ai_exc:
                        # 切片8：systemic 错误暂停（不批量变 uncertain 后完成）
                        from webui.ai import (
                            AISecurityError,
                            map_ai_error_to_block_code,
                        )
                        if isinstance(_ai_exc, AISecurityError):
                            _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                            if _block_code:
                                ctx.write_run(
                                    task_id, status="paused", error_code=_block_code,
                                    current_stage="recrawl_ai",
                                    processed_count=len(recrawl_completed_ids))
                                ctx.store.save_checkpoint(
                                    task_id, "recrawl_ai",
                                    sorted(recrawl_completed_ids),
                                )
                                ctx.store.append_task_event(
                                    task_id, "pause",
                                    {"stage": "recrawl_ai", "code": _block_code,
                                     "processed": len(recrawl_completed_ids),
                                     "total": len(targets)})
                                ctx.record_pause_failure(
                                    task_id, "recrawl_ai", _block_code, _block_code,
                                    processed=len(recrawl_completed_ids),
                                    total=len(targets), exception=_ai_exc,
                                )
                                with ctx.lock:
                                    t = ctx.tasks.get(task_id)
                                    if t is not None:
                                        t["status"] = "paused"
                                        t["error"] = (
                                            f"重抓 AI 重判被阻断（{_block_code}）："
                                            f"已判 {len(recrawl_completed_ids)}/{len(targets)} 条。"
                                            "处理完成后点「继续」"
                                        )
                                publish_recrawl_updates()
                                _recrawl_ai_pause = True
                                break
                        raise
                    batch_verdicts = res.get("verdicts", {})
                    verdicts.update(batch_verdicts)
                    recrawl_completed_ids.update(batch_verdicts)
                    actual_processed += len(batch_verdicts)
                    ctx.store.save_verdict_and_checkpoint_atomic(
                        task_id, "recrawl_ai", batch_verdicts,
                        sorted(recrawl_completed_ids),
                    )
                    emit(stage="screen_b", current=len(recrawl_completed_ids), total=total,
                         message=f"AI 重判 {len(recrawl_completed_ids)}/{total}")
                if _recrawl_ai_pause:
                    ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
                    return
                if _stop_mode() == "pause":
                    _mark_recrawl_paused(
                        processed=len(recrawl_completed_ids),
                        stage="recrawl_ai")
                    ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
                    return
                if run_id:
                    # 017-US2: 判定/JD 回写 + pending 移除 + 计数重算 + 定稿时间
                    # 刷新统一走 result_rounds.apply_recrawl_writeback。
                    from webui.result_rounds import apply_recrawl_writeback
                    apply_recrawl_writeback(
                        ctx.store, run_id, verdicts,
                        source_run_id=source_run_id or "",
                    )
                for jid, v in verdicts.items():
                    u = updates.setdefault(jid, {})
                    u["verdict"] = v.get("verdict")
                    u["verdict_reason"] = v.get("reason")
                    u["caveats"] = v.get("caveats", [])
                publish_recrawl_updates()

        if actual_processed == 0:
            reason = (
                f"重抓未处理任何岗位（0/{total}），未产生 JD 或 AI 判定；"
                "请检查自动化浏览器和 AI 配置后点「继续」"
            )
            ctx.write_run(
                task_id, status="paused", error_code="recrawl_no_work",
                current_stage="recrawl_ai", processed_count=0,
                error_reason=reason,
            )
            ctx.store.append_task_event(task_id, "pause", {
                "stage": "recrawl_ai",
                "code": "recrawl_no_work",
                "processed": 0, "total": total,
            })
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "paused"
                    t["error"] = reason
                    t["result"] = {"updates": updates}
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return

        recrawl_status = "succeeded"
        recount = None
        remaining_uncertain = 0
        if run_id:
            recount = ctx.store.recount_pipeline_result(run_id)
            try:
                latest_payload = ctx.store.load_latest_pipeline_result(run_id) or {}
                latest_jobs = ((latest_payload.get("result") or {}).get("jobs") or [])
                remaining_uncertain = sum(
                    1 for job in latest_jobs
                    if isinstance(job, dict)
                    and str(job.get("verdict") or "")
                    not in ("match", "not_match", "mismatch")
                )
            except ctx.operational_errors:
                remaining_uncertain = int((recount or {}).get("pending_count") or 0)
            if remaining_uncertain > 0:
                recrawl_status = "partial"
        emit(
            stage="done", current=total, total=total,
            message=(
                f"重抓完成，但仍有 {remaining_uncertain} 个岗位待确认"
                if recrawl_status == "partial" else "重抓完成"
            ),
        )
        ctx.store.append_task_events(task_id, [
            (
                "job_success" if update.get("verdict") in ("match", "not_match")
                or bool(update.get("jd")) else "job_fail",
                {
                    "stage": "recrawl", "job_id": str(job_id),
                    "verdict": update.get("verdict"),
                    "reason": update.get("verdict_reason", ""),
                },
            )
            for job_id, update in updates.items()
        ])
        ctx.write_run(
            task_id,
            status="cancelled" if _stop_requested() else recrawl_status,
            current_stage="done",
        )
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is not None:
                # Keep the in-memory task status aligned with the DB status.
                # The polling API prefers this live task over the DB row.
                t["status"] = "cancelled" if _stop_requested() else recrawl_status
                t["result"] = {"updates": updates}
        ctx.schedule_pipeline_task_cleanup(task_id)
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
    except Exception as exc:
        error_message = f"重抓异常：{type(exc).__name__}"
        if not ctx.is_user_finished(task_id):
            record_failure(
                ctx.store, task_id, stage="recrawl",
                error_code="internal_error", reason=error_message,
                correlation_id=task_id, diagnostics={},
                exception=exc, include_traceback=True,
            )
        persistence_error = None
        try:
            run = ctx.store.get_screening_run(task_id)
            if run and run.get("status") in ("queued", "running", "paused"):
                ctx.write_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=error_message,
                )
        except ctx.operational_errors as persist_exc:
            persistence_error = type(persist_exc).__name__
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is not None:
                if ctx.is_user_finished(task_id):
                    t["status"] = "cancelled"
                    t["error"] = ctx.msg_user_finished
                else:
                    t["status"] = "failed"
                    t["error"] = (
                        error_message if persistence_error is None
                        else f"{error_message}；状态保存失败：{persistence_error}"
                    )
        ctx.schedule_pipeline_task_cleanup(task_id)
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
