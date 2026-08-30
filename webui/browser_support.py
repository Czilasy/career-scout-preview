"""浏览器锁共享助手（021 B6 T019 外迁自 webui/app.py）。

活动任务锁口径（running/queued 锁全部账号、paused 锁冻结账号）、
暂停 run 浏览器 best-effort 关闭、账号投影。闭包语义原样搬运；
可 patch 符号（boss）经 webui.app 模块属性动态取用。
"""

from __future__ import annotations

import sqlite3

from webui.constants import _OPERATIONAL_ERRORS


def build_browser_support(store, tasks, lock, account_for_run, activate_run_browser):
    import webui.app as _app_module  # 可 patch 符号动态门面

    def _browser_lock() -> tuple[str | None, str | None, str | None]:
        """Return the active browser lock as (kind, account id).

        Running/queued tasks lock every account; a paused run locks only the
        account frozen into its execution params (or the current fallback)."""
        with lock:
            for _task_id, task in reversed(list(tasks.items())):
                if task.get("status") in ("running", "queued"):
                    return ("running", str(task.get("browser_account") or ""),
                            str(task.get("platform") or "boss"))
        try:
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT id FROM screening_runs WHERE status = 'paused' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        except (sqlite3.Error, RuntimeError):
            row = None
        if row is None:
            return None, None, None
        try:
            run = store.get_screening_run(row["id"]) or {}
            account = account_for_run(run)
        except _OPERATIONAL_ERRORS:
            return "paused", None, None
        params = run.get("execution_params") or {}
        if not isinstance(params, dict):
            params = {}
        platform = str(params.get("platform") or run.get("platform") or "boss")
        return "paused", account, platform

    def _browser_busy() -> bool:
        return _browser_lock()[0] is not None

    def _latest_paused_run_for_browser_close() -> tuple[dict | None, int | None]:
        """Return the latest paused run and its frozen CDP port, if any."""
        try:
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT id FROM screening_runs WHERE status = 'paused' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        except (sqlite3.Error, RuntimeError):
            return None, None
        if row is None:
            return None, None
        try:
            run = store.get_screening_run(row["id"]) or {}
        except _OPERATIONAL_ERRORS:
            return None, None
        params = run.get("execution_params") or {}
        if not isinstance(params, dict):
            params = {}
        raw_port = params.get("cdp_port")
        try:
            frozen_port = int(raw_port) if raw_port not in (None, "") else None
        except (TypeError, ValueError):
            frozen_port = None
        return run, frozen_port

    def _close_paused_run_browser() -> None:
        """Best-effort close the automation browser frozen by the paused run."""
        from webui.pipeline_exec import close_debug_chrome
        run, frozen_port = _latest_paused_run_for_browser_close()
        if run is None:
            return
        try:
            activate_run_browser(run)
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            close_debug_chrome(
                frozen_port if frozen_port is not None else _app_module.boss.DEFAULT_CDP_PORT)
        except (OSError, RuntimeError):
            pass

    def _has_active_pipeline_task() -> bool:
        """Only in-memory running/queued tasks block new task starts."""
        with lock:
            return any(
                task.get("status") in ("queued", "running")
                for task in tasks.values()
            )
    def _project_browser_accounts(accounts: dict) -> list[dict]:
        """Project accounts to the non-sensitive API shape (http-api.md L319)."""
        from webui.platforms import get_platform, list_platform_keys
        projected = []
        for acc in accounts.values():
            platforms = {}
            for key in list_platform_keys():
                reg = get_platform(key)
                platforms[key] = {"cdp_port": reg.default_cdp_port}
            projected.append({
                "id": str(acc.get("id") or ""),
                "name": str(acc.get("name") or ""),
                "builtin": bool(acc.get("builtin", False)),
                "roles": list(acc.get("roles") or []),
                "platforms": platforms,
            })
        return projected

    return (
        _browser_lock, _browser_busy, _latest_paused_run_for_browser_close,
        _close_paused_run_browser, _has_active_pipeline_task,
        _project_browser_accounts,
    )
