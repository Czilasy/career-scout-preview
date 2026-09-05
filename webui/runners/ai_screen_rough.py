"""AI 筛选 Stage A 粗筛段（021 B6 外迁自 webui/app.py）。

字段粗筛执行体：断点恢复已判定集合、跨平台去重幸存者过滤、分批粗筛
与判定/checkpoint 原子落库、systemic 阻断暂停。共享运行态经 ctx 取用；
ai_service 模块级直连（031 B9 门面拆除）；screen_jobs 延迟 import 保持原语义。
"""

from __future__ import annotations
from webui import ai as ai_service

from webui.task_runners import _resume_dropped_from_verdicts


def run_rough_stage(ctx, task_id, raw_jobs, criteria, endpoint, api_key,
                    model, execution_config, resume_from_run_id,
                    resume_verdicts, _dup_ids, _dup_entries, frozen_platform,
                    emit, stop_requested, handle_user_stop):
    """Stage A：字段粗筛。返回 (survivors, dropped)；任务终止路径返回 None。"""
    from webui.ai import screen_jobs
    from webui.pipeline_exec import failed_code_label

    # 3) Stage A：字段粗筛（移除明显不符，学历向下兼容）
    emit(stage="screen_a", current=0, total=len(raw_jobs),
         message="AI 粗筛中（对照筛选字段）…")

    def _a_progress(cur, tot):
        emit(stage="screen_a", current=cur, total=tot,
             message=f"AI 粗筛 {cur}/{tot}")

    # 切片6：粗筛继续时跳过已判定的 job_id（从 checkpoint + DB 真实 verdict 恢复）
    _rough_done_ids: set[str] = set()
    _resume_verdicts: dict[str, str] = {}  # job_id → verdict（真实判定）
    if resume_from_run_id:
        _rough_done_ids = ctx.store.load_checkpoint(
            resume_from_run_id, "ai_rough"
        )
        # 判定取同源链合并结果（resume_verdicts）：完整判定可能挂在
        # 链上更早的 run 名下（018 事故），只读最近一条 run 会看不见
        # 全部 dropped/精筛判定。值形态兼容 dict 与早期纯字符串。
        for _jid, _vobj in (resume_verdicts or {}).items():
            _resume_verdicts[str(_jid)] = (
                _vobj.get("verdict", "") if isinstance(_vobj, dict)
                else str(_vobj or "")
            )
    _rough_todo = [j for j in raw_jobs
                   if str(j.get("job_id", "")) not in _rough_done_ids
                   and str(j.get("job_id", "")) not in _dup_ids] if _rough_done_ids else [
        j for j in raw_jobs
        if str(j.get("job_id", "")) not in _dup_ids]
    # 018：断点内的岗位默认保留，仅判定链上明确 dropped 才移除；
    # 判定记录缺失的岗位不再被静默丢弃（旧数据纯字符串与早期
    # 误写的 "match" 在该语义下自动兼容）。
    _rough_kept_from_resume = [
        j for j in raw_jobs
        if str(j.get("job_id", "")) in _rough_done_ids
        and _resume_verdicts.get(str(j.get("job_id", "")), "") != "dropped"
        # 020 US3：本轮命中跨平台去重的断点岗位只归剔除侧，
        # 不得同时进保留/幸存者（019 SC-003 续跑反向边）。
        and str(j.get("job_id", "")) not in _dup_ids
    ] if _rough_done_ids else []
    _rough_completed_ids = set(_rough_done_ids)
    # 护栏（020 US6 覆盖口径）：断点内仍有岗位没有任何判定记录时
    # 只记事件供排查（负载记缺失岗位数），不阻断续跑。
    _missing_verdict_ids = (
        set(_rough_done_ids) - set(_resume_verdicts)
    ) if _rough_done_ids else set()
    if resume_from_run_id and _missing_verdict_ids:
        ctx.store.append_task_event(task_id, "resume_inconsistent", {
            "verdicts": len(_resume_verdicts),
            "checkpoint": len(_rough_done_ids),
            "missing": len(_missing_verdict_ids),
        })

    try:
        def _rough_batch_done(batch_verdicts, completed_job_ids):
            completed_snapshot = _rough_completed_ids | set(completed_job_ids)
            ctx.store.save_verdict_and_checkpoint_atomic(
                task_id, "ai_rough", batch_verdicts,
                sorted(completed_snapshot),
            )
            _rough_completed_ids.clear()
            _rough_completed_ids.update(completed_snapshot)

        screen_result = screen_jobs(_rough_todo, criteria, endpoint, api_key,
                                    model=model, progress=_a_progress,
                                    raise_on_systemic=True,
                                    on_batch_done=_rough_batch_done,
                                    execution_config=execution_config,
                                    correlation_id=task_id)
        from webui.store_helpers import _now
        from webui.whitebox import WhiteboxService
        _wb = WhiteboxService(ctx.store)
        if screen_result.get("degraded"):
            reasons = [str(reason or "ai_request_failed")
                       for reason in (screen_result.get("fallback_reasons") or [])]
            if not reasons:
                reasons = ["ai_request_failed"]
            for index, reason in enumerate(dict.fromkeys(reasons)):
                _wb.record_for_owner("screening", task_id, {
                    "idempotency_key": f"ai-rough-request-failed:{task_id}:{index}:{reason}",
                    "event_type": "ai_request_failed", "occurred_at": _now(),
                    "stage": "ai_rough", "unit_kind": "ai_stage", "unit_key": "ai_rough",
                    "attempt_no": 1, "required_evidence": True, "severity": "warning",
                    "payload": {"reason_code": reason, "action": "keep_all",
                                "normal_screening_completed": False},
                })
            _wb.record_for_owner("screening", task_id, {
                "idempotency_key": f"ai-rough-fallback:{task_id}",
                "event_type": "ai_keep_all_fallback", "occurred_at": _now(),
                "stage": "ai_rough", "unit_kind": "ai_stage", "unit_key": "ai_rough",
                "attempt_no": 1, "required_evidence": True, "severity": "warning",
                "payload": {"normal_screening_completed": False,
                            "reasons": reasons, "action": "keep_all"},
            })
            _wb.record_for_owner("screening", task_id, {
                "idempotency_key": f"ai-rough-incomplete:{task_id}",
                "event_type": "unit_incomplete", "occurred_at": _now(),
                "stage": "ai_rough", "unit_kind": "ai_stage", "unit_key": "ai_rough",
                "attempt_no": 1, "required_evidence": True, "severity": "error",
                "payload": {"stop_reason": "ai_keep_all_fallback"},
            })
        else:
            _wb.record_for_owner("screening", task_id, {
                "idempotency_key": f"ai-rough-complete:{task_id}",
                "event_type": "scope_completed", "occurred_at": _now(),
                "stage": "ai_rough", "unit_kind": "ai_stage", "unit_key": "ai_rough",
                "attempt_no": 1, "required_evidence": True,
                "payload": {
                    "scope_complete": True,
                    "returned_total_count": len(_rough_todo) + len(_rough_kept_from_resume),
                    "unit_unique_count": len(
                        set(screen_result.get("kept") or [])
                        | {str(job.get("job_id") or "") for job in _rough_kept_from_resume}
                    ),
                    "stop_reason": (
                        "explicit_empty"
                        if not (_rough_todo or _rough_kept_from_resume)
                        else "target_reached"
                    ),
                },
            })
            if not (_rough_todo or _rough_kept_from_resume):
                _wb.record_for_owner("screening", task_id, {
                    "idempotency_key": f"ai-rough-empty:{task_id}",
                    "event_type": "explicit_empty", "occurred_at": _now(),
                    "stage": "ai_rough", "unit_kind": "ai_stage",
                    "unit_key": "ai_rough", "attempt_no": 1,
                    "required_evidence": True, "severity": "info",
                    "payload": {"empty_evidence": {
                        "kind": "stage_input_empty",
                        "reason": "all_input_removed_before_ai_rough",
                        "input_count": len(raw_jobs),
                        "dropped_count": len(_dup_ids),
                    }},
                })
        ctx.store.save_screening_verdicts(
            task_id, screen_result.get("verdicts") or {})
    except (ai_service.AISecurityError, ai_service.AICheckpointError) as _ai_exc:
        # AISecurityError（systemic）：暂停整任务，保存 checkpoint
        from webui.ai import (
            AICheckpointError,
            AISecurityError,
            map_ai_error_to_block_code,
        )
        _block_code = ""
        if isinstance(_ai_exc, AICheckpointError):
            _block_code = "internal_error"
        elif isinstance(_ai_exc, AISecurityError):
            _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
        if _block_code:
            # 暂停状态、真实进度、checkpoint 和事件必须全部可靠落库；
            # 任一步失败都交给外层 internal_error 路径，不能只改内存。
            _done_keys = sorted(_rough_completed_ids)
            ctx.write_run(
                task_id, status="paused", error_code=_block_code,
                current_stage="ai_rough",
                processed_count=len(_done_keys))
            ctx.store.save_checkpoint(task_id, "ai_rough", _done_keys)
            ctx.store.append_task_event(
                task_id, "pause",
                {"stage": "ai_rough", "code": _block_code,
                 "processed": len(_done_keys), "total": len(raw_jobs)})
            ctx.record_pause_failure(
                task_id, "ai_rough", _block_code,
                failed_code_label(_block_code, frozen_platform) or _block_code,
                processed=len(_done_keys), total=len(raw_jobs),
                exception=_ai_exc,
            )
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t["status"] = "paused"
                    t["error"] = (
                        f"AI 粗筛被阻断（{_block_code}）："
                        f"已处理 {len(_rough_completed_ids)}/{len(raw_jobs)} 条。"
                        "处理完成后点「继续」"
                    )
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return
        raise  # 非 systemic，往上抛
    if stop_requested():
        handle_user_stop()
        return
    # 合并 resume 已判定的结果（resume 的岗位默认 kept，因为上次没被 drop）
    kept_ids = set(screen_result["kept"]) | {str(j.get("job_id", "")) for j in _rough_kept_from_resume}
    # 粗筛成功完成：保存全部已判定 job_id（用于未来继续时跳过）
    dropped_by_id = {
        str(d.get("job_id") or ""): d for d in (screen_result.get("dropped") or [])
    }
    if resume_from_run_id:
        for item in _resume_dropped_from_verdicts(raw_jobs, resume_verdicts):
            _drop_jid = str(item.get("job_id") or "")
            # 断点塌缩后部分岗位会重新粗筛；本轮判 kept 的不并入
            # dropped（重筛结果新于链上旧判定），避免同一岗位双列表。
            if _drop_jid not in kept_ids:
                dropped_by_id.setdefault(_drop_jid, item)
    # 019：跨平台剔除条目显式赋值（覆盖链上无 extra 的重建行，保住追溯）。
    for _dup_entry in _dup_entries:
        dropped_by_id[str(_dup_entry.get("job_id") or "")] = _dup_entry
    dropped = list(dropped_by_id.values())
    ctx.store.save_checkpoint(
        task_id,
        "ai_rough",
        list(kept_ids | {str(d.get("job_id") or "") for d in dropped}),
    )
    survivors = [j for j in raw_jobs if str(j.get("job_id", "")) in kept_ids]
    emit(stage="screen_a_done", kept=len(survivors), dropped=len(dropped),
         message=f"粗筛完成：保留 {len(survivors)} 条，移除 {len(dropped)} 条")
    ctx.write_run(
        task_id, status="running", source_cursor=0,
        total_kept=len(survivors), total_dropped=len(dropped),
    )

    return survivors, dropped
