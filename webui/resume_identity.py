"""Frozen identity resolution for resumed pipeline tasks.

Continue endpoints must restore the exact platform, browser account, CDP
port and profile captured when the task was frozen. Missing zhilian
identity is a hard block; no implicit BOSS fallback is allowed.
"""

from __future__ import annotations

from typing import Any


def resolve_frozen_identity(store, run: dict[str, Any]) -> dict[str, Any]:
    """Resolve frozen identity from the run and its parent scrape run."""
    params = dict(run.get("execution_params") or {})
    platform = str(run.get("platform") or params.get("platform") or "")
    browser_account = str(params.get("browser_account") or "")
    cdp_port = params.get("cdp_port")
    profile_key = str(params.get("profile_key") or "")
    scrape_task_id = str(params.get("scrape_task_id") or "")

    if scrape_task_id and (
        not platform or not browser_account or cdp_port is None or not profile_key
    ):
        try:
            parent = store.get_screening_run(scrape_task_id)
        except Exception:
            parent = None
        if parent:
            parent_params = dict(parent.get("execution_params") or {})
            platform = platform or str(parent.get("platform") or parent_params.get("platform") or "")
            browser_account = browser_account or str(parent_params.get("browser_account") or "")
            if cdp_port is None:
                cdp_port = parent_params.get("cdp_port")
            profile_key = profile_key or str(parent_params.get("profile_key") or "")

    return {
        "platform": platform,
        "browser_account": browser_account,
        "cdp_port": cdp_port,
        "profile_key": profile_key,
    }


def persist_frozen_identity(store, run_id: str, identity: dict[str, Any]) -> None:
    """Write non-empty identity fields back into the run execution params."""
    run = store.get_screening_run(run_id) or {}
    params = dict(run.get("execution_params") or {})
    for key, value in identity.items():
        if value not in (None, ""):
            params[key] = value
    store.update_screening_execution_params(run_id, params)


def invalidate_login_cache_for_resume(account_id: str, platform: str) -> None:
    """Drop the login cache so the next preflight performs a real probe."""
    if not account_id or not platform:
        return
    try:
        from scripts.login_state_cache import invalidate_login_state
        invalidate_login_state(str(account_id), str(platform))
    except Exception:
        pass
