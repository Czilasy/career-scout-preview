"""AI 筛选 JD 抓取段（021 B6 外迁自 webui/app.py）。

分段抓取幸存岗位 JD：调试浏览器就绪检查、CDP source 创建、按
detail_batch_size 分批抓取与 JD 断点文件落盘、源级硬信号暂停。
共享运行态经 ctx 取用；pipeline_exec 延迟 import 保持原语义。
"""

from __future__ import annotations


def run_jd_stage(ctx, task_id, enriched, survivors, resume_jd, jd_path,
                 frozen_platform, frozen_cdp_port, frozen_profile_key,
                 frozen_browser_account, execution_config, stop_event,
                 emit, stop_requested, handle_user_stop, save_jd_checkpoint):
    """Stage 中段：分段抓 JD。返回 (jd_map, jd_failures)；终止路径返回 None。"""
    from webui.pipeline_exec import (
        close_debug_chrome,
        ensure_chrome_ready,
        failed_code_label,
        fetch_job_details,
    )

    jd_map = dict(resume_jd)
    jd_failures: dict[str, dict[str, str]] = {}
    # 022：卡死防护（app_support 创建并挂 ctx；无 guard 时行为与旧版一致）
    guard = getattr(ctx, "pipeline_guard", None)
    if survivors:
        emit(stage="ensure_chrome", message="启动调试浏览器，准备抓取 JD…")
        chrome_ok, chrome_err = ensure_chrome_ready(
            frozen_cdp_port, minimize_after_launch=True,
        )
        if not chrome_ok:
            reason = f"调试浏览器未就绪（{chrome_err}），请处理后继续"
            save_jd_checkpoint(jd_path, jd_map)
            ctx.write_run(
                task_id, status="paused", error_code="source_cdp_unavailable",
                current_stage="jd_detail", processed_count=len(jd_map),
                error_reason=reason,
            )
            ctx.store.save_checkpoint(
                task_id, "jd_detail", sorted(jd_map)
            )
            ctx.store.append_task_event(task_id, "pause", {
                "stage": "jd_detail", "code": "source_cdp_unavailable",
                "processed": len(jd_map), "total": len(survivors),
            })
            ctx.record_pause_failure(
                task_id, "jd_detail", "source_cdp_unavailable", reason,
                processed=len(jd_map), total=len(survivors),
            )
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "paused"
                    t["error"] = reason
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return
        source = ctx.make_cdp_source(
            platform=frozen_platform,
            browser_account=frozen_browser_account,
            cdp_port=frozen_cdp_port,
            profile_key=frozen_profile_key,
            run_id=task_id,
        )
        if source is None:
            reason = "CDP 抓取源不可用，请确认调试浏览器后继续"
            save_jd_checkpoint(jd_path, jd_map)
            ctx.write_run(
                task_id, status="paused", error_code="source_cdp_unavailable",
                current_stage="jd_detail", processed_count=len(jd_map),
                error_reason=reason,
            )
            ctx.store.save_checkpoint(
                task_id, "jd_detail", sorted(jd_map)
            )
            ctx.store.append_task_event(task_id, "pause", {
                "stage": "jd_detail", "code": "source_cdp_unavailable",
                "processed": len(jd_map), "total": len(survivors),
            })
            ctx.record_pause_failure(
                task_id, "jd_detail", "source_cdp_unavailable", reason,
                processed=len(jd_map), total=len(survivors),
            )
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "paused"
                    t["error"] = reason
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return

        todo_jd = [j for j in survivors
                   if str(j.get("job_id", "")) not in jd_map]
        emit(stage="fetch_jd", current=len(jd_map), total=len(survivors),
             message=f"抓取 JD（{len(jd_map)}/{len(survivors)}）…")
        DETAIL_CHUNK = max(1, int(execution_config.detail_batch_size))
        for chunk_start in range(0, len(todo_jd), DETAIL_CHUNK):
            if stop_requested():
                close_debug_chrome(frozen_cdp_port)
                handle_user_stop()
                return
            chunk = todo_jd[chunk_start:chunk_start + DETAIL_CHUNK]
            _jd_base = len(jd_map)

            def _jd_progress(done, total, _base=_jd_base):
                cur = min(_base + done, len(survivors))
                emit(stage="fetch_jd", current=cur, total=len(survivors),
                     message=f"抓取 JD {cur}/{len(survivors)}")

            detail_result = fetch_job_details(
                chunk, source,
                artifact_dir=ctx.app.config["RESULT_DIR"],
                stop_event=stop_event,
                progress=_jd_progress,
                execution_config=execution_config,
                guard=guard,
                batch_key_prefix=f"jd-{task_id}-{chunk_start}",
            )
            # 022：卡死 3 次失败分流收场（环境级暂停 / 偶发跳过进待确认）
            _stall_divert = detail_result.get("stall_divert")
            _stall_attempts = detail_result.get("stall_attempts") or 0
            if _stall_divert == "environment":
                # 环境级：暂停 + 报错模块接管 + 断点保留可续跑（复用 hard_stop
                # 暂停路径：不关浏览器，用户处理后点「继续」从断点续跑）。
                _hs_code = detail_result.get("stall_code") or "internal_error"
                _hs_label = failed_code_label(_hs_code, frozen_platform) or _hs_code
                _hs_reason = (
                    f"抓取批次连续 {_stall_attempts} 次无响应，检测到环境问题"
                    f"（{_hs_label}）"
                )
                ctx.persist_jd_job_failures(
                    task_id, detail_result.get("jobs") or [],
                    stage="jd_detail", platform=frozen_platform,
                )
                ctx.write_run(
                    task_id, status="paused", error_code=_hs_code,
                    current_stage="jd_detail",
                    processed_count=len(jd_map), error_reason=_hs_reason,
                )
                ctx.record_pause_failure(
                    task_id, "jd_detail", _hs_code, _hs_reason,
                    processed=len(jd_map), total=len(survivors),
                )
                with ctx.lock:
                    t = ctx.tasks.get(task_id)
                    if t is not None:
                        t["status"] = "paused"
                        t["error"] = (
                            f"抓取 JD 时{_hs_reason}：已抓 "
                            f"{len(jd_map)}/{len(survivors)} 条（已保存）。"
                            "请在自动化浏览器中处理，完成后点「继续」"
                        )
                ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
                return
            if _stall_divert == "sporadic":
                # 单批偶发：该批岗位进待确认（补抓机制复用既有 pending），继续下一批
                ctx.persist_jd_job_failures(
                    task_id, detail_result.get("jobs") or [],
                    stage="jd_detail", platform=frozen_platform,
                )
                emit(stage="fetch_jd",
                     current=min(len(jd_map), len(survivors)), total=len(survivors),
                     message=f"一批岗位连续 {_stall_attempts} 次无响应已跳过，可在待确认中补抓")
            for j in detail_result["jobs"]:
                jid = str(j.get("job_id", ""))
                jd = str(j.get("jd", "")).strip()
                if jid and jd:
                    jd_map[jid] = jd
                    jd_failures.pop(jid, None)
                elif jid and j.get("jd_failed_code"):
                    jd_failures[jid] = {
                        "code": str(j.get("jd_failed_code")),
                        "reason": str(j.get("jd_failed_reason") or ""),
                        "evidence": str(j.get("jd_failed_evidence") or ""),
                    }
            save_jd_checkpoint(jd_path, jd_map)
            ctx.write_run(
                task_id, source_cursor=len(jd_map),
                processed_count=len(jd_map), current_stage="jd_detail",
            )
            emit(stage="fetch_jd",
                 current=min(len(jd_map), len(survivors)), total=len(survivors),
                 message=f"抓取 JD {min(len(jd_map), len(survivors))}/{len(survivors)}")
            if detail_result.get("hard_stop"):
                # 源级硬信号：暂停，不关浏览器（用户需要它处理验证码/登录）
                _hs_code = detail_result.get("hard_stop_code") or "source_blocked"
                _hs_label = failed_code_label(_hs_code, frozen_platform)
                _hs_hint = next((
                    str(job.get("jd_failed_reason") or "").strip()
                    for job in detail_result.get("jobs") or []
                    if job.get("jd_failed_reason")
                ), "")
                _hs_reason = _hs_hint if _hs_hint and _hs_hint != _hs_label else _hs_label
                ctx.persist_jd_job_failures(
                    task_id,
                    detail_result.get("jobs") or [],
                    stage="jd_detail",
                    platform=frozen_platform,
                )
                ctx.write_run(
                    task_id, status="paused", error_code=_hs_code,
                    current_stage="jd_detail",
                    processed_count=len(jd_map), error_reason=_hs_reason,
                )
                ctx.record_pause_failure(
                    task_id, "jd_detail", _hs_code, _hs_reason,
                    processed=len(jd_map), total=len(survivors),
                )
                with ctx.lock:
                    t = ctx.tasks.get(task_id)
                    if t is not None:
                        t["status"] = "paused"
                        t["error"] = (
                            f"抓取 JD 时{_hs_reason}：已抓 "
                            f"{len(jd_map)}/{len(survivors)} 条（已保存）。"
                            "请在自动化浏览器中处理，完成后点「继续」"
                        )
                ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
                return
            if detail_result.get("stopped"):
                close_debug_chrome(frozen_cdp_port)
                handle_user_stop()
                return
        close_debug_chrome(frozen_cdp_port)
    for job in enriched:
        jid = str(job.get("job_id", ""))
        job["jd"] = jd_map.get(jid, "")
        failure = jd_failures.get(jid)
        if not job["jd"] and failure:
            job["jd_failed_code"] = failure["code"]
            job["jd_failed_reason"] = failure["reason"]
            job["jd_failed_evidence"] = failure.get("evidence", "")

    return jd_map, jd_failures
