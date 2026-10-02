"""Durable failure compensation for AI screen worker boundaries."""
from __future__ import annotations

from webui.flow_task_state import (
    FlowStateClosureError,
    close_flow_task_state,
)


_PUBLIC_FAILURE_CODES = {
    "source_cdp_unavailable",
    "browser_busy",
    "ai_unavailable",
    "whitebox_incomplete",
    "submit_failed",
    "internal_error",
    "flow_result_incomplete",
    "flow_result_empty",
    "flow_result_save_failed",
    # B094：领域判定口径变化，保留登记分类不被泛化成 internal_error。
    "screening_policy_incompatible",
    "filter_snapshot_incompatible",
}


def mark_flow_failure(ctx, task_id, scrape_task_id, error_code, reason, platform=None):
    """Atomically close the Flow boundary for an AI worker error."""
    parent = ctx.store.get_screening_run(scrape_task_id) or {}
    params = parent.get("execution_params") or {}
    flow_id = params.get("flow_id")
    target = platform or params.get("platform") or parent.get("platform")
    profile_id = parent.get("profile_id")
    if not flow_id or not target or not profile_id:
        return None
    try:
        return close_flow_task_state(
            ctx,
            task_id=task_id,
            scrape_task_id=scrape_task_id,
            flow_id=flow_id,
            profile_id=profile_id,
            status="failed",
            error_code=error_code,
            reason=reason,
            platform=target,
            stage="ai",
        )
    except Exception as exc:  # noqa: BLE001 - preserve safe observable failure
        raise FlowStateClosureError() from exc


def persist_ai_worker_failure(
    ctx,
    task_id,
    scrape_task_id,
    error_code="internal_error",
    reason="AI 筛选启动失败",
    *,
    platform=None,
    status=None,
):
    """Close task, screening/search runs and Flow Track with one status."""
    safe_code = str(error_code or "internal_error")
    if safe_code not in _PUBLIC_FAILURE_CODES:
        safe_code = "internal_error"
    return close_flow_task_state(
        ctx,
        task_id=task_id,
        scrape_task_id=scrape_task_id,
        status=status,
        error_code=safe_code,
        reason=reason or "AI 筛选启动失败",
        platform=platform,
        stage="ai",
    )
