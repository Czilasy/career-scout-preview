"""Structured failure diagnostics shared by pipeline execution paths.

The helper writes one local log line and one durable task event per
failure.  Payloads only contain safe fields; raw API keys, cookies,
resume text and JD bodies must never be passed here.
"""

from __future__ import annotations

import json
import traceback
from typing import Any

from webui.logging_setup import bind_task_context, get_logger, redact
from webui.logging_setup import is_configured

_log = get_logger("diagnostics")


def record_failure(
    store,
    task_id: str,
    *,
    stage: str,
    error_code: str,
    reason: str,
    correlation_id: str = "",
    diagnostics: dict[str, Any] | None = None,
    exception: BaseException | None = None,
    include_traceback: bool = False,
    whitebox_unit: str | None = None,
) -> dict[str, Any] | None:
    """Log and persist one structured failure event.

    Returns the persisted event payload, or ``None`` when the event store
    is unavailable.  Logging is always attempted first.
    """
    safe_diagnostics = _safe_diagnostics(diagnostics)
    traceback_text = ""
    if exception is not None:
        safe_diagnostics.setdefault("exception_type", type(exception).__name__)
        safe_diagnostics.setdefault(
            "exception_message", redact(str(exception))[:2000])
        if include_traceback:
            traceback_text = redact(
                "".join(traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                ))
            )[:8000]
    if is_configured():
        with bind_task_context(task_id, correlation_id):
            _log.error(
                "failure stage=%s error_code=%s reason=%s diagnostics=%s%s",
                stage,
                error_code,
                reason,
                json.dumps(
                    safe_diagnostics, ensure_ascii=False, default=str, sort_keys=True
                ),
                f"\ntraceback:\n{traceback_text}" if traceback_text else "",
            )
    payload = {
        "stage": stage,
        "error_code": error_code,
        "reason": reason,
        "correlation_id": correlation_id or "",
        "diagnostics": safe_diagnostics,
    }
    def _whitebox_failure() -> bool:
        if not hasattr(store, "get_whitebox_run"):
            return False
        try:
            from webui.store_helpers import _now
            from webui.whitebox import WhiteboxService
            service = WhiteboxService(store)
            unit_key = str(whitebox_unit or stage)
            fact = {"idempotency_key": f"diagnostic:{task_id}:{stage}:{error_code}",
                    "event_type": "unit_failed", "occurred_at": _now(), "stage": stage,
                    "unit_kind": "diagnostic", "unit_key": unit_key, "attempt_no": 1,
                    "required_evidence": True, "severity": "error", "payload": payload}
            for kind in ("scrape", "screening", "recrawl", "workbench", "tuning"):
                if service.record_for_owner(kind, task_id, fact):
                    return True
        except Exception as exc:
            _log.warning("whitebox diagnostic persist failed: %s", type(exc).__name__)
        return False
    try:
        store.append_task_event(task_id, "failure", payload)
        if not _whitebox_failure():
            payload["whitebox_incomplete"] = True
    except Exception as exc:  # diagnostics must never break the task flow
        if is_configured():
            with bind_task_context(task_id, correlation_id):
                _log.warning("failure event persist failed: %s", type(exc).__name__)
        payload["whitebox_incomplete"] = not _whitebox_failure()
        return None
    return payload


def build_diagnostic_payload(
    *,
    run_id: str,
    run: dict[str, Any],
    events: list[dict[str, Any]],
    correlation_id: str = "",
    next_action: str = "",
) -> dict[str, Any]:
    """Build the safe diagnostic summary returned to the frontend."""
    return {
        "run_id": str(run_id),
        "status": run.get("status") or "",
        "stage": run.get("current_stage") or "",
        "error_code": run.get("error_code") or "",
        "error_reason": run.get("error_reason") or "",
        "correlation_id": correlation_id or "",
        "next_action": next_action or "",
        "events": list(events[-20:]),
    }


def _safe_diagnostics(diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(diagnostics, dict):
        return {}
    return {str(key): _safe_value(value) for key, value in diagnostics.items()}


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    text = redact(str(value))
    return text[:2000]
