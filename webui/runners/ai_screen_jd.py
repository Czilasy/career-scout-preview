"""AI 筛选 JD 抓取段（021 B6 外迁自 webui/app.py）。

分段抓取幸存岗位 JD：调试浏览器就绪检查、CDP source 创建、按
detail_batch_size 分批抓取与 JD 断点文件落盘、源级硬信号暂停。
共享运行态经 ctx 取用；pipeline_exec 延迟 import 保持原语义。
"""

from __future__ import annotations

from webui.logging_setup import get_logger


_logger = get_logger(__name__)


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

    def _pause_for_rotation_snapshot(reason: str):
        """Pause instead of silently restarting a damaged R2 checkpoint."""
        checkpoint_error = ""
        if jd_map:
            try:
                save_jd_checkpoint(jd_path, jd_map)
            except Exception as exc:
                checkpoint_error = f"；JD 断点写入失败（{type(exc).__name__}）"
                _logger.warning(
                    "R2 异常暂停时 JD 断点写入失败 task=%s",
                    task_id, exc_info=True,
                )
        reason = f"{reason}{checkpoint_error}"
        ctx.write_run(
            task_id, status="paused", error_code="r2_rotation_checkpoint_invalid",
            current_stage="jd_detail", processed_count=len(jd_map),
            error_reason=reason,
        )
        ctx.store.save_checkpoint(task_id, "jd_detail", sorted(jd_map))
        ctx.store.append_task_event(task_id, "r2_rotation_checkpoint_invalid", {
            "stage": "jd_detail", "platform": frozen_platform,
            "reason": str(reason), "recoverable": True,
        })
        ctx.store.append_task_event(task_id, "pause", {
            "stage": "jd_detail", "code": "r2_rotation_checkpoint_invalid",
            "processed": len(jd_map), "total": len(survivors),
        })
        ctx.record_pause_failure(
            task_id, "jd_detail", "r2_rotation_checkpoint_invalid", reason,
            processed=len(jd_map), total=len(survivors),
        )
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is not None:
                t["status"] = "paused"
                t["error"] = reason
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
        return None

    def _clear_resolved_jd_pending(job_id: str) -> None:
        """Remove only pending rows written by the JD failure stage."""
        if not job_id or not hasattr(ctx.store, "delete_pending_result"):
            return
        try:
            if hasattr(ctx.store, "list_pending_results"):
                rows = ctx.store.list_pending_results(task_id) or []
                for row in rows:
                    if str(row.get("job_id") or "") != job_id:
                        continue
                    stage = str(row.get("failure_stage") or "")
                    if stage == "jd_detail" or stage.startswith("jd_"):
                        ctx.store.delete_pending_result(task_id, job_id)
            elif hasattr(ctx.store, "get_pending_result"):
                row = ctx.store.get_pending_result(task_id, job_id) or {}
                stage = str(row.get("failure_stage") or "")
                if stage == "jd_detail" or stage.startswith("jd_"):
                    ctx.store.delete_pending_result(task_id, job_id)
            else:
                _logger.warning(
                    "已取得有效 JD 但无法确认待处理阶段，跳过清理 task=%s job=%s",
                    task_id, job_id,
                )
        except Exception:
            _logger.warning(
                "已取得有效 JD 但待处理清理失败 task=%s job=%s",
                task_id, job_id, exc_info=True,
            )

    for job_id, jd in jd_map.items():
        if str(jd or "").strip():
            _clear_resolved_jd_pending(str(job_id))

    if survivors:
        emit(stage="ensure_chrome", message="启动调试浏览器，准备抓取 JD…")
        # 030：抓 JD 前把浏览器身份重绑到任务冻结账号——粗筛阶段耗时较长，
        # 期间全局活动目录可能被其它请求改写，此处重绑消除污染窗口
        ctx.activate_task_browser(task_id)
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

        from webui.account_round_robin import PoolEntry, make_detail_robin
        from webui.detail_attempts import DetailAttemptTracker
        from webui.r2_rotation_session import (
            R2RotationSession,
            R2RotationSnapshotError,
        )
        initial_robin = make_detail_robin(
            source, run_id=str(task_id), switch_event_store=ctx.store,
        )
        r2_session = None

        def _completed_job_ids() -> list[str]:
            return [
                str(job_id) for job_id, jd in jd_map.items()
                if str(jd or "").strip()
            ]

        def _save_rotation_checkpoint() -> bool:
            if r2_session is None:
                return True
            try:
                completed_ids = _completed_job_ids()
                r2_session.set_completed_count(len(completed_ids))
                r2_session.save_checkpoint(
                    ctx.store, completed_count=len(completed_ids),
                    completed_ids=completed_ids,
                )
            except R2RotationSnapshotError as exc:
                _pause_for_rotation_snapshot(f"R2 轮询断点写入失败：{exc}")
                return False
            return True

        attempt_tracker = DetailAttemptTracker(
            str(task_id), ctx.app.config["RESULT_DIR"]
        )
        task_events = None
        if hasattr(ctx.store, "list_task_events"):
            try:
                task_events = ctx.store.list_task_events(task_id) or []
            except Exception:
                task_events = None
        if task_events is not None:
            attempt_tracker = DetailAttemptTracker.from_task_events(
                str(task_id), ctx.app.config["RESULT_DIR"], task_events
            )
        try:
            checkpoint = R2RotationSession.latest_checkpoint(ctx.store, task_id)
        except R2RotationSnapshotError as exc:
            return _pause_for_rotation_snapshot(f"R2 轮询断点读取失败：{exc}")
        prior_r2 = any(
            isinstance(event, dict)
            and event.get("type") == "account_pool_snapshot"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("phase") == "R2"
            for event in (task_events or [])
        )
        prior_pause = any(
            isinstance(event, dict)
            and event.get("type") == "pause"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("stage") == "jd_detail"
            for event in (task_events or [])
        )
        if initial_robin is not None:
            r2_session = R2RotationSession.from_robin(
                source, initial_robin, task_id=str(task_id),
                platform=frozen_platform,
            )
            if checkpoint is not None:
                try:
                    checkpoint_order = checkpoint["account_order"]
                    checkpoint_quotas = checkpoint["quotas"]
                    if (not isinstance(checkpoint_order, list)
                            or not isinstance(checkpoint_quotas, dict)):
                        raise R2RotationSnapshotError(
                            "R2 快照缺少冻结账号池"
                        )
                    live_entries = list(initial_robin._queue._entries) + list(
                        initial_robin._queue._blocked
                    )
                    live_quotas = {
                        str(entry.account_id): max(1, int(entry.quota))
                        for entry in live_entries
                    }
                    snapshot_quotas = {
                        str(account_id): max(1, int(checkpoint_quotas[account_id]))
                        for account_id in checkpoint_order
                    }
                    if live_quotas != snapshot_quotas:
                        raise R2RotationSnapshotError(
                            "R2 快照账号池与当前冻结池不一致"
                        )
                    if list(r2_session.account_order) != checkpoint_order:
                        r2_session = R2RotationSession(
                            source,
                            [PoolEntry(str(account_id), snapshot_quotas[str(account_id)])
                             for account_id in checkpoint_order],
                            task_id=str(task_id), platform=frozen_platform,
                            robin=initial_robin,
                        )
                    r2_session.restore_snapshot(checkpoint)
                    completed_ids = _completed_job_ids()
                    expected_digest = R2RotationSession.completed_digest(
                        completed_ids
                    )
                    if (int(checkpoint["completed_count"]) != len(completed_ids)
                            or checkpoint["completed_digest"] != expected_digest):
                        raise R2RotationSnapshotError(
                            "R2 轮询断点与 JD 断点不一致"
                        )
                    initial_account = str(
                        (checkpoint.get("account_order") or [frozen_browser_account])[0]
                    )
                    # continue API persists an explicitly selected account in
                    # the frozen identity; the unchanged initial account means
                    # ordinary checkpoint recovery and must not override it.
                    if (frozen_browser_account
                            and str(frozen_browser_account) != initial_account):
                        r2_session.override_active_account(frozen_browser_account)
                except (KeyError, TypeError, ValueError, R2RotationSnapshotError) as exc:
                    return _pause_for_rotation_snapshot(
                        f"R2 轮询断点无法安全恢复：{exc}"
                    )
            elif prior_r2 and prior_pause:
                return _pause_for_rotation_snapshot(
                    "R2 轮询断点缺失，无法安全确定接续位置"
                )
            else:
                if not _save_rotation_checkpoint():
                    return
        elif checkpoint is not None or (prior_r2 and prior_pause):
            return _pause_for_rotation_snapshot(
                "R2 账号池或轮询断点与任务不一致，无法安全恢复"
            )

        todo_jd = [j for j in survivors
                   if str(j.get("job_id", "")) not in jd_map]
        emit(stage="fetch_jd", current=len(jd_map), total=len(survivors),
             message=f"抓取 JD（{len(jd_map)}/{len(survivors)}）…")
        DETAIL_CHUNK = max(1, int(execution_config.detail_batch_size))
        for chunk_start in range(0, len(todo_jd), DETAIL_CHUNK):
            if stop_requested():
                if r2_session is not None:
                    r2_session.emit_account_summary(
                        attempt_tracker, total_success=len(jd_map)
                    )
                close_debug_chrome(frozen_cdp_port)
                handle_user_stop()
                return
            chunk = todo_jd[chunk_start:chunk_start + DETAIL_CHUNK]
            _jd_base = len(jd_map)

            def _jd_progress(done, total, _base=_jd_base):
                cur = min(_base + done, len(survivors))
                emit(stage="fetch_jd", current=cur, total=len(survivors),
                     message=f"抓取 JD {cur}/{len(survivors)}")

            # 025：批内信号（前端暂停弹窗判定「正处抓 JD 批次中」）；
            # cur_batch=None 表示批结束/停止，信号清除。
            # 批计数用跨 chunk 全局值：fetch_job_details 回报的是 chunk 内值
            # （每 chunk 恰一个批次，恒为 1/1），加 chunk 偏移才是用户可感知的
            # 「第几批/共几批」。
            _chunk_index = chunk_start // DETAIL_CHUNK
            _total_chunks = (len(todo_jd) + DETAIL_CHUNK - 1) // DETAIL_CHUNK

            def _jd_batch_progress(cur_batch, total_batches,
                                   _base=_jd_base, _chunk=_chunk_index,
                                   _chunks=_total_chunks):
                _cur = min(_base, len(survivors))
                # 026 修复：批开始/结束的 emit 都带数字 message，前端持续显示
                # 「抓取 JD n/total」，而不是 fallback 成「抓取 JD 中…」（原行为：
                # 无 message → 前端轮询基本看不到中间数字，只有最后一刻闪现）。
                if cur_batch is None:
                    emit(stage="fetch_jd", current=_cur,
                         total=len(survivors),
                         message=f"抓取 JD {_cur}/{len(survivors)}",
                         jd_batch=None)
                else:
                    emit(stage="fetch_jd", current=_cur,
                         total=len(survivors),
                         message=f"抓取 JD {_cur}/{len(survivors)}",
                         jd_batch={"current": min(_chunk + max(cur_batch, 1), _chunks),
                                   "total": _chunks})

            # 024：详情人形模拟随当前档位下发（custom 档零仿真，不传参）
            _active_selection = None
            try:
                _active_selection = (
                    ctx.store.get_advanced_config_state().get("active_selection")
                )
            except Exception:
                _active_selection = None
            _simulation_mode = (
                _active_selection
                if _active_selection in ("stable", "balanced", "extreme")
                else None
            )
            detail_result = fetch_job_details(
                chunk, source,
                artifact_dir=ctx.app.config["RESULT_DIR"],
                stop_event=stop_event,
                progress=_jd_progress,
                execution_config=execution_config,
                guard=guard,
                batch_key_prefix=f"jd-{task_id}-{chunk_start}",
                task_id=task_id,
                simulation_mode=_simulation_mode,
                batch_progress=_jd_batch_progress,
                store=ctx.store,
                r2_session=r2_session,
                attempt_tracker=attempt_tracker,
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
                if r2_session is not None:
                    if jd_map:
                        save_jd_checkpoint(jd_path, jd_map)
                    if not _save_rotation_checkpoint():
                        return
                    r2_session.emit_account_summary(
                        attempt_tracker, total_success=len(jd_map)
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
                    _clear_resolved_jd_pending(jid)
                elif jid and j.get("jd_failed_code"):
                    jd_failures[jid] = {
                        "code": str(j.get("jd_failed_code")),
                        "reason": str(j.get("jd_failed_reason") or ""),
                        "evidence": str(j.get("jd_failed_evidence") or ""),
                    }
            # 025 B077：暂停返回时绝不把空结果写进断点（无已抓则跳过落盘）
            if jd_map:
                save_jd_checkpoint(jd_path, jd_map)
            if not _save_rotation_checkpoint():
                return
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
                if r2_session is not None:
                    r2_session.emit_account_summary(
                        attempt_tracker, total_success=len(jd_map)
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
                if r2_session is not None:
                    r2_session.emit_account_summary(
                        attempt_tracker, total_success=len(jd_map)
                    )
                close_debug_chrome(frozen_cdp_port)
                handle_user_stop()
                return
        close_debug_chrome(frozen_cdp_port)
        if r2_session is not None:
            r2_session.emit_account_summary(
                attempt_tracker, total_success=len(jd_map)
            )
    for job in enriched:
        jid = str(job.get("job_id", ""))
        job["jd"] = jd_map.get(jid, "")
        failure = jd_failures.get(jid)
        if not job["jd"] and failure:
            job["jd_failed_code"] = failure["code"]
            job["jd_failed_reason"] = failure["reason"]
            job["jd_failed_evidence"] = failure.get("evidence", "")

    return jd_map, jd_failures
