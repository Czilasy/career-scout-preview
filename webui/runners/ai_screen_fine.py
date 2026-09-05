"""AI 筛选 Stage B 精筛段（021 B6 外迁自 webui/app.py）。

JD 精筛执行体：断点恢复已判定集合、无画像跳过精筛、分段精筛与
判定/checkpoint 原子落库、未抓到 JD 岗位的待定标记、systemic 阻断
暂停。共享运行态经 ctx 取用；ai_service 模块级直连（031 B9 门面拆除）。
"""

from __future__ import annotations
from webui import ai as ai_service


def run_fine_stage(ctx, task_id, enriched, profile_summary, criteria,
                   endpoint, api_key, model, profile_facts, execution_config,
                   resume_from_run_id, resume_fine_verdicts, frozen_platform,
                   emit, stop_requested, handle_user_stop):
    """Stage B：JD 精筛。返回 match_count；终止路径返回 None。"""
    from webui.ai import match_jds
    from webui.pipeline_exec import failed_code_label

    jobs_with_jd = [j for j in enriched if str(j.get("jd", "")).strip()]
    from webui.store_helpers import _now
    from webui.whitebox import WhiteboxService
    _wb = WhiteboxService(ctx.store)

    if not profile_summary.strip():
        # 无画像时跳过精筛，全部标记待人工确认
        for job in enriched:
            job["verdict"] = "uncertain"
            job["verdict_reason"] = "未填写求职画像，跳过 AI 精筛"
        match_count = 0
        # 跳过精筛：一条未判，进度必须从 0 起（写 len(jobs_with_jd)
        # 会造成 30/30 + 100% 假进度，且 task-state 的 max() 会钉死它）
        emit(stage="screen_b", current=0, total=len(jobs_with_jd),
             message="未填写求职画像，已跳过 AI 精筛")
        _wb.record_for_owner("screening", task_id, {
            "idempotency_key": f"ai-fine-skipped:{task_id}", "event_type": "unit_skipped",
            "occurred_at": _now(), "stage": "ai_fine", "unit_kind": "ai_stage",
            "unit_key": "ai_fine", "attempt_no": 1, "required_evidence": True,
            "severity": "warning", "payload": {"stop_reason": "profile_missing"},
        })
    else:
        if stop_requested():
            handle_user_stop()
            return
        # 分段精筛，每段判定落库（screening_results）+ 更新 processed_count：
        # 进程崩了已筛的判定不丢，重跑自动跳过。
        done_verdicts = dict(resume_fine_verdicts)
        # 切片6：从 checkpoint 恢复已判定 job_id（resume 场景）
        if resume_from_run_id:
            _fine_done = ctx.store.load_checkpoint(
                resume_from_run_id, "ai_fine"
            )
            if _fine_done:
                _extra = {
                    jid: {
                        "verdict": "uncertain",
                        "reason": "从断点恢复，待重新判定",
                    }
                    for jid in _fine_done if jid not in done_verdicts
                }
                done_verdicts.update(_extra)
        todo_match = [j for j in jobs_with_jd
                      if str(j.get("job_id", "")) not in done_verdicts]
        no_jd_pending = len(enriched) - len(jobs_with_jd)
        ctx.write_run(
            task_id, current_stage="ai_fine",
            processed_count=len(done_verdicts),
            pending_count=no_jd_pending,
        )
        emit(stage="screen_b",
             current=min(len(done_verdicts), len(jobs_with_jd)),
             total=len(jobs_with_jd),
             message="AI 精筛中（JD 对比简历画像）…")
        def _fine_progress(cur, tot):
            # cur 是 match_jds 本轮已处理数，progress 回调总在 on_batch_done
            # 之后，done_verdicts 已包含本批；直接用它做唯一进度源，避免
            # 续跑时「恢复判定 + 本轮新增」重复叠加导致显示超前。
            emit(stage="screen_b",
                 current=min(len(done_verdicts), len(jobs_with_jd)),
                 total=len(jobs_with_jd),
                 message=f"AI 精筛 {min(len(done_verdicts), len(jobs_with_jd))}/{len(jobs_with_jd)}")

        def _fine_batch_done(batch_verdicts, completed_job_ids):
            nonlocal done_verdicts
            next_verdicts = dict(done_verdicts)
            next_verdicts.update(batch_verdicts)
            # verdict 与 checkpoint 同事务提交。任一步失败都必须停止，
            # 不能继续下一批 AI 后再把内存结果伪装成可恢复进度。
            ctx.store.save_verdict_and_checkpoint_atomic(
                task_id, "ai_fine", batch_verdicts,
                list(next_verdicts.keys()),
            )
            ctx.write_run(
                task_id, processed_count=len(next_verdicts))
            done_verdicts = next_verdicts
            emit(stage="screen_b",
                 current=min(len(done_verdicts), len(jobs_with_jd)),
                 total=len(jobs_with_jd),
                 message=f"AI 精筛 {min(len(done_verdicts), len(jobs_with_jd))}/{len(jobs_with_jd)}")

        try:
            match_result = match_jds(
                todo_match, profile_summary, endpoint, api_key,
                model=model, raise_on_systemic=True,
                criteria=criteria, profile_facts=profile_facts,
                progress=_fine_progress,
                on_batch_done=_fine_batch_done,
                execution_config=execution_config,
                correlation_id=task_id)
            if match_result.get("degraded"):
                reasons = [str(reason or "ai_request_failed")
                           for reason in (match_result.get("fallback_reasons") or [])]
                if not reasons:
                    reasons = ["ai_request_failed"]
                for index, reason in enumerate(dict.fromkeys(reasons)):
                    _wb.record_for_owner("screening", task_id, {
                        "idempotency_key": f"ai-fine-request-failed:{task_id}:{index}:{reason}",
                        "event_type": "ai_request_failed", "occurred_at": _now(),
                        "stage": "ai_fine", "unit_kind": "ai_stage", "unit_key": "ai_fine",
                        "attempt_no": 1, "required_evidence": True, "severity": "warning",
                        "payload": {"reason_code": reason,
                                    "action": "keep_all_or_uncertain",
                                    "normal_screening_completed": False},
                    })
                _wb.record_for_owner("screening", task_id, {
                    "idempotency_key": f"ai-fine-fallback:{task_id}",
                    "event_type": "ai_keep_all_fallback", "occurred_at": _now(),
                    "stage": "ai_fine", "unit_kind": "ai_stage", "unit_key": "ai_fine",
                    "attempt_no": 1, "required_evidence": True, "severity": "warning",
                    "payload": {"normal_screening_completed": False,
                                "reasons": reasons,
                                "action": "keep_all_or_uncertain"},
                })
                _wb.record_for_owner("screening", task_id, {
                    "idempotency_key": f"ai-fine-incomplete:{task_id}",
                    "event_type": "unit_incomplete", "occurred_at": _now(),
                    "stage": "ai_fine", "unit_kind": "ai_stage", "unit_key": "ai_fine",
                    "attempt_no": 1, "required_evidence": True, "severity": "error",
                    "payload": {"stop_reason": "ai_keep_all_fallback"},
                })
        except ai_service.AISecurityError as _ai_exc:
            # 切片6：systemic 错误暂停整任务（不批量变 uncertain 后完成）
            from webui.ai import AISecurityError, map_ai_error_to_block_code
            if isinstance(_ai_exc, AISecurityError):
                _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                if _block_code:
                    ctx.write_run(
                        task_id, status="paused", error_code=_block_code,
                        current_stage="ai_fine",
                        processed_count=len(done_verdicts))
                    ctx.store.save_checkpoint(
                        task_id, "ai_fine", list(done_verdicts.keys()))
                    ctx.store.append_task_event(
                        task_id, "pause",
                        {"stage": "ai_fine", "code": _block_code,
                         "processed": len(done_verdicts),
                         "total": len(jobs_with_jd)})
                    ctx.record_pause_failure(
                        task_id, "ai_fine", _block_code,
                        failed_code_label(_block_code, frozen_platform) or _block_code,
                        processed=len(done_verdicts),
                        total=len(jobs_with_jd), exception=_ai_exc,
                    )
                    with ctx.lock:
                        t = ctx.tasks.get(task_id)
                        if t is not None:
                            t["status"] = "paused"
                            t["error"] = (
                                f"AI 精筛被阻断（{_block_code}）："
                                f"已判定 {len(done_verdicts)}/{len(jobs_with_jd)} 条。"
                                "处理完成后点「继续」"
                            )
                    ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
                    return
            raise  # 非 systemic，往上抛
        # 兜底：末轮重试等未触发 on_batch_done 的新判定仍须落库。
        _pending_fine_verdicts = {
            jid: verdict for jid, verdict in (match_result.get("verdicts") or {}).items()
            if jid not in done_verdicts
        }
        if _pending_fine_verdicts:
            _fine_batch_done(
                _pending_fine_verdicts,
                list(done_verdicts) + list(_pending_fine_verdicts))
        verdicts = done_verdicts
        for job in enriched:
            jid = str(job.get("job_id", ""))
            v = verdicts.get(jid)
            if v:
                job["verdict"] = v["verdict"]
                job["verdict_reason"] = v["reason"]
                # flags（靠谱判定）独立透传前端；中危降级项已并入 caveats
                job["caveats"] = v.get("caveats") or []
                job["flags"] = v.get("flags") or []
            else:
                # 未抓到 JD 的岗位无法精筛，标记待定（不红不绿）
                job["verdict"] = "uncertain"
                code = job.get("jd_failed_code", "")
                label = failed_code_label(code, frozen_platform)
                detail_reason = str(job.get("jd_failed_reason") or "").strip()
                if detail_reason:
                    job["verdict_reason"] = f"未抓到 JD（{detail_reason}），无法精筛"
                elif label:
                    job["verdict_reason"] = f"未抓到 JD（{label}），无法精筛"
                else:
                    job["verdict_reason"] = "未抓到 JD，无法精筛"
                job["ai_payload"] = {
                    "reason": str(job.get("verdict_reason") or ""),
                    "evidence": str(job.get("jd_failed_code") or ""),
                    "evidence_detail": str(job.get("jd_failed_evidence") or ""),
                    "next_action": "retry_jd",
                }

        match_count = sum(1 for j in enriched if j.get("verdict") == "match")
        if not match_result.get("degraded"):
            uncertain = sum(1 for j in enriched if j.get("verdict") == "uncertain")
            if uncertain:
                _wb.record_for_owner("screening", task_id, {
                    "idempotency_key": f"ai-fine-incomplete:{task_id}",
                    "event_type": "unit_incomplete", "occurred_at": _now(),
                    "stage": "ai_fine", "unit_kind": "ai_stage", "unit_key": "ai_fine",
                    "attempt_no": 1, "required_evidence": True, "severity": "warning",
                    "payload": {"stop_reason": "uncertain_verdict", "uncertain_count": uncertain},
                })
            else:
                _wb.record_for_owner("screening", task_id, {
                    "idempotency_key": f"ai-fine-complete:{task_id}",
                    "event_type": "scope_completed", "occurred_at": _now(),
                    "stage": "ai_fine", "unit_kind": "ai_stage", "unit_key": "ai_fine",
                    "attempt_no": 1, "required_evidence": True,
                    "payload": {"scope_complete": True, "returned_total_count": len(enriched),
                                "unit_unique_count": len(enriched),
                                "stop_reason": "explicit_empty" if not enriched else "target_reached"},
                })
                if not enriched:
                    _wb.record_for_owner("screening", task_id, {
                        "idempotency_key": f"ai-fine-empty:{task_id}",
                        "event_type": "explicit_empty", "occurred_at": _now(),
                        "stage": "ai_fine", "unit_kind": "ai_stage",
                        "unit_key": "ai_fine", "attempt_no": 1,
                        "required_evidence": True, "severity": "info",
                        "payload": {"empty_evidence": {
                            "kind": "stage_input_empty",
                            "reason": "no_survivors_after_jd_detail",
                        }},
                    })
    return match_count
