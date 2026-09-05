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

from webui.logging_setup import get_logger

_logger = get_logger(__name__)



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
    task_id: str = ""
    unit_key: str = ""
    attempt: int = 1
    store: object | None = None
    ensure_chrome_ready: Callable[..., tuple[bool, str]] | None = None
    on_restart: Callable[[], None] | None = None
    _restart_allowed: bool = field(default=True, init=False, repr=False)
    _restart_count: int = field(default=0, init=False, repr=False)
    _loss_event_no: int = field(default=0, init=False, repr=False)
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
                self._record_whitebox_event(
                    "retry_abandoned",
                    {"result": "failed", "reason": "same_loss_event_already_restarted"},
                    severity="warning",
                    idempotency_suffix="giveup",
                )
                return False, "同一失联事件已自动重启过一次，不再重复自动重启"
            self._loss_event_no += 1
            if self.on_restart is not None:
                try:
                    self.on_restart()
                except Exception:
                    _logger.warning("浏览器重启回调执行失败", exc_info=True)

            ensure = self.ensure_chrome_ready
            if ensure is None:
                from webui.pipeline_exec import ensure_chrome_ready
                ensure = ensure_chrome_ready
            ok, msg = ensure(self.cdp_port, minimize_after_launch=True)
            record_runtime_event(
                event="browser_restart", stage="browser_recovery",
                failed_code="" if ok else "source_cdp_unavailable",
                safe_hint=msg,
                task_id=self.task_id, correlation_id=self.task_id,
                store=self.store,
                extra={"result": "succeeded" if ok else "failed",
                       "unit_key": self.unit_key, "attempt_no": self.attempt},
            )
            self._record_whitebox_event(
                "browser_restarted",
                {"result": "succeeded" if ok else "failed", "reason": msg},
                severity="warning",
            )
            # 无论成功或失败都消耗本次失联事件的重启机会，避免无限循环。
            self._restart_allowed = False
            if ok:
                self._restart_count += 1
            return ok, msg

    def mark_progress(self) -> None:
        """Record a successful batch, resetting the loss-event boundary."""
        with self._lock:
            if self._loss_event_no:
                self._record_whitebox_event(
                    "recovery_completed",
                    {"result": "succeeded", "repair_source": "browser_recovery"},
                    severity="info",
                )
            self._restart_allowed = True

    def _record_whitebox_event(
        self,
        event_type: str,
        payload: dict,
        *,
        severity: str,
        idempotency_suffix: str = "",
    ) -> None:
        """Best-effort operational fact with the current recovery context.

        Browser recovery is used by scrape and screening detail stages.  The
        task id is therefore resolved against whichever whitebox owner exists
        instead of assuming every caller is a scrape run.
        """
        if self.store is None or not self.task_id:
            return
        try:
            from webui.store_helpers import _now
            from webui.whitebox import WhiteboxService

            owner_kind = None
            get_run = getattr(self.store, "get_whitebox_run", None)
            if callable(get_run):
                for candidate in ("scrape", "screening", "recrawl", "legacy_task", "workbench"):
                    try:
                        if get_run(candidate, self.task_id):
                            owner_kind = candidate
                            break
                    except Exception:
                        continue
            if owner_kind is None:
                return
            suffix = idempotency_suffix or event_type
            idem = (
                f"browser-recovery:{self.task_id}:{self.unit_key}:"
                f"{max(1, int(self.attempt))}:{self._loss_event_no}:{suffix}"
            )
            WhiteboxService(self.store).record_for_owner(owner_kind, self.task_id, {
                "idempotency_key": idem,
                "event_type": event_type,
                "occurred_at": _now(),
                "stage": "browser_recovery",
                "unit_kind": "detail_batch",
                "unit_key": self.unit_key or None,
                "attempt_no": max(1, int(self.attempt)),
                "required_evidence": event_type in {"browser_restarted", "recovery_completed"},
                "severity": severity,
                "payload": {
                    **payload,
                    "task_id": self.task_id,
                    "stage": "browser_recovery",
                    "unit_key": self.unit_key,
                    "attempt_no": max(1, int(self.attempt)),
                    "loss_event_no": self._loss_event_no,
                },
            })
        except Exception:
            _logger.warning("浏览器恢复白箱写入失败", exc_info=True)
