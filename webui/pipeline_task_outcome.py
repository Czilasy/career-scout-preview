"""Durable scrape outcome classification for the pipeline runner facade."""

from __future__ import annotations

from webui.error_registry import is_recoverable_systemic_block, resolve_code
from webui.pipeline_exec_status import user_visible_failure_reason
from webui.task_pause_support import (
    STOP_MODE_FINISH,
    STOP_MODE_PAUSE,
    mark_scrape_paused,
    stop_mode_for_event,
)
from webui.task_runners import _classify_scrape_block


def durable_pause_guard(ctx, task_id, task) -> bool:
    """Return whether a committed pause owns the finalizing race."""
    if not isinstance(task, dict) or not task.get("finalizing"):
        return False
    try:
        run = ctx.store.get_screening_run(task_id) or {}
    except ctx.operational_errors:
        return False
    return str(run.get("status") or "") == "paused"


def finalize_scrape_outcome(
    ctx,
    task_id,
    result,
    *,
    stop_event,
    skip_combos,
    merged_total=None,
) -> dict:
    """Persist one scrape outcome and synchronize its in-memory task.

    The runner keeps browser execution and lifecycle cleanup in its facade;
    this module owns only the result-to-state transition.  The returned
    values are deliberately plain data so the facade can continue its Flow
    coordination without importing any store implementation details.
    """
    with ctx.lock:
        task = ctx.tasks.get(task_id)
        raw_integrity = result.get("integrity")
        integrity = raw_integrity if isinstance(raw_integrity, dict) else {}
        has_integrity = isinstance(raw_integrity, dict)
        conclusion = str(integrity.get("conclusion") or "unverifiable")
        stop_mode = stop_mode_for_event(stop_event, task)
        fact_complete = conclusion in {"succeeded", "empty"}
        durable_pause = durable_pause_guard(ctx, task_id, task)
        completed = list(result.get("completed_combos") or [])
        pause_code = ""
        if stop_mode == STOP_MODE_FINISH:
            # finish/terminate owns the durable transition and partial
            # snapshot; the worker must not rewrite it as pause/cancel.
            terminal_status = "finished"
        elif fact_complete and durable_pause:
            # A pause committed before the finalizing marker owns the durable
            # state.  The completion callback must not overwrite that legal
            # pause merely because its in-memory result is complete.
            pause_code = "user_paused"
            terminal_status = "paused"
        elif fact_complete:
            # Completion fact wins over a late stop intent: all source units
            # have finished and the run can safely close as succeeded.
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
            terminal_status = "done"
        elif stop_mode == STOP_MODE_PAUSE:
            completed = mark_scrape_paused(
                ctx, task_id,
                completed_combos=result.get("completed_combos"),
                skip_combos=skip_combos,
                source_count=result.get("combinations"),
                total_scraped=result.get("total_scraped"),
            )
            pause_code = "user_paused"
            terminal_status = "paused"
        elif stop_mode == "cancel":
            ctx.write_run(
                task_id, status="cancelled", current_stage="scrape",
                processed_count=len(result.get("completed_combos") or []),
                error_reason=ctx.msg_user_stopped_scrape,
            )
            terminal_status = "cancelled"
        elif conclusion == "partial":
            ctx.write_run(
                task_id, status="partial", current_stage="scrape",
                processed_count=len(completed),
                source_count=int(result.get("combinations") or len(completed)),
                error_code=integrity.get("primary_code"),
                error_reason=integrity.get("primary_reason") or result.get("error", ""),
                total_scraped=int(result.get("total_scraped") or 0),
            )
            ctx.store.append_task_event(task_id, "stage_partial", {
                "stage": "scrape", "conclusion": conclusion,
                "combinations": int(result.get("combinations") or len(completed)),
                "total_scraped": int(result.get("total_scraped") or 0),
            })
            terminal_status = "partial"
        else:
            # Systemic source blocks are recoverable pauses when the registry
            # says so; only explicit non-recoverable hard stops fail.
            err_msg = str(result.get("error", "") or "")
            pause_code = (
                str(result.get("hard_stop_code") or "")
                or _classify_scrape_block(err_msg)
            )
            if result.get("hard_stop") and pause_code:
                if is_recoverable_systemic_block(pause_code):
                    pause_code = resolve_code(pause_code, default=pause_code)
                    platform = str(
                        (ctx.store.get_screening_run(task_id) or {}).get(
                            "platform", "",
                        )
                    )
                    pause_reason = err_msg or user_visible_failure_reason(
                        pause_code, "", platform,
                    )
                    completed = mark_scrape_paused(
                        ctx, task_id,
                        completed_combos=completed,
                        source_count=int(result.get("combinations") or 0),
                        total_scraped=int(result.get("total_scraped") or 0),
                        reason=pause_reason,
                        error_code=pause_code,
                    )
                    ctx.record_pause_failure(
                        task_id, "scrape", pause_code, pause_reason,
                        processed=len(completed),
                        total=int(result.get("combinations") or 0),
                        extra={"platform": platform},
                    )
                    terminal_status = "paused"
                else:
                    ctx.write_run(
                        task_id, status="failed", error_code=pause_code,
                        current_stage="scrape",
                        processed_count=len(completed),
                        source_count=int(result.get("combinations") or 0),
                        error_reason=err_msg,
                        total_scraped=int(result.get("total_scraped") or 0),
                    )
                    ctx.store.save_checkpoint(task_id, "scrape", completed)
                    ctx.store.append_task_event(
                        task_id, "failure",
                        {"stage": "scrape", "code": pause_code,
                         "completed_combos": len(completed)},
                    )
                    ctx.record_pause_failure(
                        task_id, "scrape", pause_code, err_msg,
                        processed=len(completed),
                        total=int(result.get("combinations") or 0),
                    )
                    terminal_status = "failed"
            elif conclusion == "unverifiable" and has_integrity and not result.get("hard_stop"):
                ctx.store.append_task_event(task_id, "job_fail", {
                    "stage": "scrape",
                    "error": integrity.get("primary_reason") or err_msg,
                    "failed_code": integrity.get("primary_code") or "evidence_missing",
                })
                ctx.write_run(
                    task_id, status="partial", current_stage="scrape",
                    processed_count=len(completed),
                    source_count=int(result.get("combinations") or 0),
                    error_code=integrity.get("primary_code") or "evidence_missing",
                    error_reason=integrity.get("primary_reason") or err_msg,
                    total_scraped=int(result.get("total_scraped") or 0),
                )
                terminal_status = "partial"
            else:
                ctx.store.append_task_event(task_id, "job_fail", {
                    "stage": "scrape", "error": err_msg,
                    "failed_code": pause_code or "scrape_failed",
                })
                ctx.write_run(
                    task_id, status="failed", current_stage="scrape",
                    processed_count=len(completed),
                    source_count=int(result.get("combinations") or 0),
                    error_code=pause_code or "scrape_failed",
                    error_reason=err_msg,
                    total_scraped=int(result.get("total_scraped") or 0),
                )
                terminal_status = "failed"

        # DB state is authoritative even when the in-memory task was replaced
        # during resume; update memory only when that exact task still exists.
        if task is not None:
            task["result"] = result
            task["error"] = result.get("error", "")
            if merged_total is not None:
                progress = dict(task.get("progress") or {})
                progress["total_scraped"] = merged_total
                progress["message"] = f"完成：抓取 {merged_total} 条，去重 {merged_total} 条"
                progress["total_matched"] = merged_total
                task["progress"] = progress
            if terminal_status == "cancelled":
                task["status"] = "cancelled"
                task["error"] = ctx.msg_user_stopped_scrape
            elif terminal_status == "done":
                task["status"] = "done"
            elif terminal_status == "partial":
                task["status"] = "partial"
                task["error"] = integrity.get("primary_reason") or result.get("error", "")
            elif terminal_status == "paused":
                task["status"] = "paused"
                if pause_code == "user_paused":
                    task["error"] = (
                        f"已暂停：本轮已完成 {len(completed)} 个组合，"
                        "断点已保存；点「继续」接着抓"
                    )
                else:
                    task["error"] = (
                        f"列表抓取被阻断（{pause_code}）："
                        f"已完成 {len(completed)} 个组合，已保存断点。"
                        "在自动化浏览器中处理后点「继续」"
                    )
            elif terminal_status != "finished":
                task["status"] = "failed"
            task.pop("finalizing", None)

    return {
        "terminal_status": terminal_status,
        "integrity": integrity,
        "pause_code": pause_code,
        "completed": completed,
    }


__all__ = ["durable_pause_guard", "finalize_scrape_outcome"]
