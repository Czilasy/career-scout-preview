"""Shared browser-loss recovery for CDP-dependent pipeline stages.

Lists and JD batches share one rule: when a stage confirms the debug browser
/CDP is gone, relaunch the browser at most once for that same loss event.
If the relaunch fails, or the very next attempt is still empty/lost, the
caller pauses and asks the user to continue.  A successful batch after a
relaunch counts as progress, so a later independent loss gets a fresh
relaunch opportunity.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from webui.runtime_audit import record_runtime_event


BROWSER_LOST_CODES = frozenset({"cdp_unavailable", "source_cdp_unavailable"})


@dataclass
class BrowserRecovery:
    """Stateful, per-loss-event browser relaunch helper.

    ``ensure_chrome_ready`` is resolved lazily from ``webui.pipeline_exec``
    when omitted, so tests can patch that module attribute without importing
    a concrete browser-launch dependency.
    """

    cdp_port: int | None = None
    platform: str = ""
    ensure_chrome_ready: Callable[..., tuple[bool, str]] | None = None
    on_restart: Callable[[], None] | None = None
    _restart_allowed: bool = field(default=True, init=False, repr=False)
    _restart_count: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @staticmethod
    def is_browser_lost(failed_code: str | None) -> bool:
        """True only for explicit CDP/browser-loss failure codes."""
        return str(failed_code or "") in BROWSER_LOST_CODES

    def try_restart(self) -> tuple[bool, str]:
        """Relaunch the debug browser once for the current loss event.

        Returns ``(True, "")`` on success.  A second call within the same
        event returns ``(False, msg)`` without launching again.
        """
        with self._lock:
            if not self._restart_allowed:
                return False, "同一失联事件已自动重启过一次，不再重复自动重启"
            if self.on_restart is not None:
                try:
                    self.on_restart()
                except Exception:
                    pass
            ensure = self.ensure_chrome_ready
            if ensure is None:
                from webui.pipeline_exec import ensure_chrome_ready
                ensure = ensure_chrome_ready
            ok, msg = ensure(self.cdp_port, minimize_after_launch=True)
            record_runtime_event(
                event="browser_restart", stage="browser_recovery",
                failed_code="" if ok else "source_cdp_unavailable",
                safe_hint=msg,
                extra={"result": "succeeded" if ok else "failed"},
            )
            # 无论成功或失败都消耗本次失联事件的重启机会，避免无限循环。
            self._restart_allowed = False
            if ok:
                self._restart_count += 1
            return ok, msg

    def mark_progress(self) -> None:
        """Record a successful batch, resetting the loss-event boundary."""
        with self._lock:
            self._restart_allowed = True
