"""Safe runtime audit events for local backend diagnostics."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from webui.logging_setup import bind_task_context, get_logger, is_configured, redact
from webui.url_safety import clean_https_url, parse_https_url

_log = get_logger("runtime_audit")
_MAX_SAFE_HINT = 2000
_HTTPS_URL = re.compile(r"https://[^\s'\"<>]+", re.IGNORECASE)


def safe_runtime_hint(value: Any) -> str:
    """Redact, remove HTTPS URL queries, and bound runtime diagnostic text."""
    text = redact(str(value or ""))

    def _clean_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        parsed = parse_https_url(raw)
        return clean_https_url(parsed, drop_params=True) if parsed else raw

    return _HTTPS_URL.sub(_clean_url, text)[:_MAX_SAFE_HINT]


def record_runtime_event(
    *,
    event: str,
    stage: str,
    failed_code: str = "",
    safe_hint: Any = "",
    task_id: str = "",
    correlation_id: str = "",
    store: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one safe local audit line and optionally persist a task event."""
    payload = {
        "event": str(event),
        "stage": str(stage),
        "failed_code": str(failed_code or ""),
        "safe_hint": safe_runtime_hint(safe_hint),
        **{str(key): safe_runtime_hint(value) for key, value in (extra or {}).items()},
    }
    if is_configured():
        with bind_task_context(task_id, correlation_id):
            _log.warning("runtime_audit=%s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        # 白箱：日志未就绪时不得静默跳过——显式降级标记到 stderr。
        import sys
        sys.stderr.write(
            f"runtime_audit degraded (log not configured) event={event} stage={stage}\n"
        )
    if store is not None and task_id:
        try:
            store.append_task_event(task_id, "runtime_audit", payload)
        except Exception:
            if is_configured():
                with bind_task_context(task_id, correlation_id):
                    _log.warning("runtime_audit_event_persist_failed event=%s", event)
    return payload