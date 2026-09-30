"""Status-aware durable closure for B096 worker boundaries.

All browser/submit failures go through this coordinator so the in-memory
task, screening rows, scrape search row, and Flow Track share one public
status.  The coordinator deliberately depends on the store/service boundary;
HTTP routes and runners do not update Flow rows themselves.
"""
from __future__ import annotations

from webui.error_registry import is_recoverable_systemic_block, resolve_code
from webui.flow_service import FLOW_ERROR_MESSAGES, public_flow_message
from webui.pipeline_exec_status import user_visible_failure_reason
from webui.diagnostics import record_failure
from webui.logging_setup import get_logger


_LOGGER = get_logger(__name__)


class FlowStateClosureError(RuntimeError):
    """Safe, observable failure while atomically closing a Flow boundary."""

    error_code = "flow_failure_compensation_failed"
    public_message = "流程失败状态收口失败，请查看任务状态后重试"

    def __init__(self, operation="Flow failure compensation"):
        super().__init__(str(operation or self.public_message))


def register_flow_state_error_handler(app):
    """Install one safe HTTP response for atomic Flow closure failures."""
    if getattr(app, "_b096_flow_state_error_handler", False):
        return
    from flask import jsonify

    @app.errorhandler(FlowStateClosureError)
    def _handle_flow_state_closure_error(_error):
        return jsonify({
            "ok": False,
            "error": FlowStateClosureError.error_code,
            "error_code": FlowStateClosureError.error_code,
            "message": FlowStateClosureError.public_message,
        }), 503

    app._b096_flow_state_error_handler = True
_RECOVERABLE_CODES = frozenset({
    "browser_busy",
    "source_cdp_unavailable",
})
_PUBLIC_CODES = frozenset(FLOW_ERROR_MESSAGES) | frozenset({
    "source_login_required",
    "source_verification_required",
    "source_rate_limited",
    "source_account_restricted",
    "source_blocked",
    "source_unreachable",
    "whitebox_incomplete",
    "internal_error",
})


def _safe_code(raw_code: object, default: str = "internal_error") -> str:
    raw = str(raw_code or "").strip()
    # Flow/Track codes predate the shared registry and are already bounded by
    # FLOW_ERROR_MESSAGES.  Preserve their stable public identity while still
    # normalizing every other value through the registry.
    if raw in _PUBLIC_CODES:
        return raw
    code = resolve_code(raw, default=default)
    return code if code in _PUBLIC_CODES else default


def _target_status(status: object, code: str) -> str:
    if str(status or "").strip() in {"paused", "failed"}:
        return str(status).strip()
    if code in _RECOVERABLE_CODES or is_recoverable_systemic_block(code):
        return "paused"
    return "failed"


def _write_screening_status(ctx, run_id: str, status: str, code: str, reason: str) -> None:
    if not run_id:
        return
    getter = getattr(ctx.store, "get_screening_run", None)
    current = getter(run_id) if callable(getter) else None
    if not current:
        return
    current_status = str(current.get("status") or "")
    if current_status in {"succeeded", "partial", "interrupted"}:
        return
    if status == "paused" and current_status == "queued":
        # The state graph intentionally disallows queued -> paused.  A worker
        # boundary has started, so make that fact explicit before pausing.
        ctx.write_run(run_id, status="running")
    ctx.write_run(
        run_id,
        status=status,
        error_code=code,
        error_reason=reason,
    )


def _write_search_status(ctx, run_id: str, status: str, code: str) -> None:
    if not run_id:
        return
    try:
        current = ctx.store.get_search_run(run_id)
    except (KeyError, AttributeError):
        return
    if not current or current.get("status") in {"succeeded", "partial", "interrupted"}:
        return
    if status == "paused" and current.get("status") == "queued":
        ctx.store.update_search_run(run_id, status="running")
    ctx.store.update_search_run(run_id, status=status, error_code=code)


def close_flow_task_state(
    ctx,
    *,
    task_id: str,
    scrape_task_id: str | None = None,
    flow_id: str | None = None,
    profile_id: str | None = None,
    status: str | None = None,
    error_code: str = "internal_error",
    reason: str = "",
    platform: str | None = None,
    stage: str = "ai",
) -> str:
    """Close every durable B096 participant with one status decision."""
    task_id = str(task_id or "").strip()
    scrape_task_id = str(scrape_task_id or "").strip()
    task_run = ctx.store.get_screening_run(task_id) or {}
    source_run = ctx.store.get_screening_run(scrape_task_id) or {}
    task_params = task_run.get("execution_params") or {}
    source_params = source_run.get("execution_params") or {}
    code = _safe_code(error_code)
    target = _target_status(status, code)
    target_platform = str(
        platform
        or task_run.get("platform")
        or task_params.get("platform")
        or source_run.get("platform")
        or source_params.get("platform")
    ).strip().lower()
    if not target_platform:
        raise FlowStateClosureError(
            "Flow task platform identity is missing; refusing implicit BOSS fallback"
        )
    safe_reason = (
        public_flow_message(code)
        if code in FLOW_ERROR_MESSAGES
        else user_visible_failure_reason(code, "", target_platform)
    )
    if not safe_reason:
        safe_reason = reason or ("任务已暂停，请处理平台问题后继续" if target == "paused" else "任务执行失败")
    flow_id = str(
        flow_id
        or source_params.get("flow_id")
        or task_params.get("flow_id")
        or ""
    ).strip()
    profile_id = (
        profile_id
        or source_run.get("profile_id")
        or task_run.get("profile_id")
        or source_params.get("profile_id")
        or task_params.get("profile_id")
    )
    if flow_id and profile_id:
        try:
            ctx.store.close_flow_task_state_atomic(
                flow_id,
                target_platform,
                profile_id,
                task_run_id=task_id,
                scrape_run_id=scrape_task_id,
                status=target,
                error_code=code,
                error_reason=safe_reason,
                stage=stage,
            )
        except Exception:
            _LOGGER.exception("Flow durable state closure failed")
            raise
    else:
        for run_id in dict.fromkeys((task_id, scrape_task_id)):
            try:
                _write_screening_status(ctx, run_id, target, code, safe_reason)
            except Exception:
                _LOGGER.exception("Legacy screening status closure failed")
                raise
        try:
            _write_search_status(ctx, scrape_task_id, target, code)
        except Exception:
            _LOGGER.exception("Legacy search status closure failed")
            raise

    with ctx.lock:
        for run_id in dict.fromkeys((task_id, scrape_task_id)):
            task = ctx.tasks.get(run_id)
            if task is not None and task.get("status") not in {"done", "cancelled"}:
                task["status"] = target
                task["error"] = safe_reason
    if target == "failed":
        try:
            record_failure(
                ctx.store,
                task_id,
                stage=stage,
                error_code=code,
                reason=safe_reason,
                correlation_id=task_id,
                diagnostics={},
            )
        except Exception as exc:  # noqa: BLE001 - durable state is primary
            _LOGGER.warning("Flow failure audit write failed (%s)", type(exc).__name__)
        try:
            ctx.clear_auto_screen(task_id)
        except Exception as exc:  # noqa: BLE001 - primary state is durable
            _LOGGER.debug("Flow auto-screen cleanup failed (%s)", type(exc).__name__)
    try:
        ctx.schedule_pipeline_task_cleanup(task_id)
    except Exception as exc:  # noqa: BLE001 - primary state is durable
        _LOGGER.debug("Flow task cleanup scheduling failed (%s)", type(exc).__name__)
    try:
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
    except Exception as exc:  # noqa: BLE001 - primary state is durable
        _LOGGER.debug("Flow resume claim release failed (%s)", type(exc).__name__)
    return target
