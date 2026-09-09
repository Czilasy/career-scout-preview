"""Best-effort task audit event writes.

Audit persistence is useful evidence but must not turn a completed user
operation into an HTTP failure.  This helper keeps the failure boundary small:
the caller supplies its existing logger and receives a boolean, while the
exception type and safe task/event context are recorded without payloads.
"""

from __future__ import annotations

from typing import Any


def append_task_event_best_effort(
        store: Any,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        logger: Any,
        context: str = "task audit event write failed",
) -> bool:
    """Append one event without overriding the caller's primary operation."""
    try:
        store.append_task_event(run_id, event_type, payload)
        return True
    except Exception as exc:
        logger.warning(
            "%s task=%s event=%s error=%s",
            context,
            str(run_id or ""),
            str(event_type or ""),
            type(exc).__name__,
        )
        return False


__all__ = ["append_task_event_best_effort"]
