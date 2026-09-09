"""Source/profile IO helpers for the shared account round-robin scheduler.

The scheduler owns queue state; this module owns only account switching and
platform source cloning.  It calls the public platform/source seams and never
creates a second round-robin lifecycle.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


def switch_browser_account(
    account_id: str,
    platform: str,
    cdp_port: object,
    *,
    preflight: Callable[[], Any] | None = None,
) -> bool:
    """Bind a registered account profile and ensure its Chrome session."""
    from webui.pipeline_exec_accounts import (
        resolve_browser_account, set_active_cdp_data_dir,
    )
    from webui import pipeline_exec as _facade
    profile = resolve_browser_account(account_id) or ""
    if not profile:
        return False
    if str(platform or "boss") == "zhilian":
        from webui.platforms import derive_zhilian_profile_dir
        profile = derive_zhilian_profile_dir(profile)
    set_active_cdp_data_dir(profile)
    port = int(cdp_port) if cdp_port else None
    ok, _err = _facade.ensure_chrome_ready(port, minimize_after_launch=True)
    if not ok:
        return False
    if preflight is not None:
        try:
            outcome = preflight()
            return bool(getattr(outcome, "ok", outcome))
        except Exception:
            return False
    return True


def clone_source(source: Any, account_id: str, *, run_id: str = "") -> Any:
    """Clone a source for another account on the same platform and port."""
    platform = str(getattr(source, "platform", "boss") or "boss")
    cancel_event = getattr(source, "cancel_event", None)
    if platform == "zhilian":
        from webui.source import ZhilianCdpSource
        return ZhilianCdpSource(
            browser_account=str(account_id),
            cdp_port=int(getattr(source, "cdp_port", 9223) or 9223),
            profile_key=f"zhilian:{account_id}",
            breaker=None,
            preflight_runner=getattr(source, "_preflight_runner", None),
            list_runner=getattr(source, "_list_runner", None),
            detail_runner=getattr(source, "_detail_runner", None),
            batch_detail_runner=getattr(source, "_batch_detail_runner", None),
            run_id=str(run_id or getattr(source, "run_id", "") or ""),
            cancel_event=cancel_event,
        )
    cls = type(source)
    kwargs: dict[str, Any] = {
        "browser_account": str(account_id),
        "run_id": str(run_id or getattr(source, "run_id", "") or ""),
        "cancel_event": cancel_event,
    }
    cdp_port = getattr(source, "cdp_port", None)
    if cdp_port is not None:
        kwargs["cdp_port"] = int(cdp_port)
    try:
        parameters = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        parameters = {}
    aliases = {
        "executor": "_executor",
        "runner": "_runner",
    }
    for name, parameter in parameters.items():
        if name in {"self", "browser_account", "run_id", "cancel_event", "cdp_port"}:
            continue
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
            continue
        attr = aliases.get(name, name)
        if not hasattr(source, attr):
            continue
        if name == "runner" and getattr(source, "_use_default_runner", False):
            continue
        value = getattr(source, attr)
        if name == "breaker":
            value = None
        if name == "executor" and value is not None:
            from webui.process_executor import ScraperExecutor
            value = ScraperExecutor(
                max_output_bytes=getattr(value, "max_output_bytes", 1_000_000),
                poll_seconds=getattr(value, "poll_seconds", 0.05),
            )
        if name == "env" and isinstance(value, dict) and run_id:
            value = {
                **value,
                "CAREER_SCOUT_CORRELATION_ID": str(run_id),
                "CAREER_SCOUT_TASK_ID": str(run_id),
            }
        if value is not None:
            kwargs[name] = value
    return cls(**kwargs)


__all__ = ["clone_source", "switch_browser_account"]
