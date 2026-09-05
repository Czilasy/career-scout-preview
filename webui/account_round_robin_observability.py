"""038 多账号轮询的安全白箱事件适配器。

只负责把调度 seam 的摘要写入既有 task_logs 和可选白箱，不参与账号选择或抓取控制。
"""

from __future__ import annotations

from typing import Any

from webui.logging_setup import get_logger

_logger = get_logger("webui.account_round_robin")


class RoundRobinWhitebox:
    """为一个 R1/R2 轮询器记录可核对、无敏感正文的任务事件。"""

    def __init__(self, store: Any, run_id: str, *, phase: str,
                 platform: str, entries: list[Any]):
        self._store = store
        self._run_id = str(run_id or "")
        self._phase = str(phase)
        self._platform = str(platform or "boss")
        self._segment = 0
        self._incomplete = False
        self._whitebox = None
        if store is not None and hasattr(store, "get_whitebox_run"):
            from webui.whitebox import WhiteboxService
            self._whitebox = WhiteboxService(store)
        self._emit("account_pool_snapshot", {
            "phase": self._phase,
            "platform": self._platform,
            "accounts": [
                {"account_id": str(entry.account_id), "order": index,
                 "quota": max(1, int(entry.quota))}
                for index, entry in enumerate(entries, start=1)
            ],
        })

    def _emit(self, event_type: str, payload: dict[str, Any]) -> bool:
        if self._store is None or not self._run_id:
            return True
        try:
            self._store.append_task_event(self._run_id, event_type, dict(payload))
            if self._whitebox is not None:
                from webui.store_helpers import _now
                self._whitebox.record_for_owner("scrape", self._run_id, {
                    "idempotency_key": f"account:{self._run_id}:{self._phase}:{event_type}:{self._segment}",
                    "event_type": event_type, "occurred_at": _now(), "stage": self._phase,
                    "unit_kind": "account_pool", "unit_key": self._phase,
                    "attempt_no": 1, "required_evidence": False, "payload": payload,
                })
            return True
        except Exception:
            self._incomplete = True
            _logger.warning(
                "白箱事件写入失败 task=%s event=%s；主流程继续但记录不完整",
                self._run_id, event_type,
            )
            try:
                self._store.append_task_event(self._run_id, "whitebox_incomplete", {
                    "event_type": str(event_type), "reason": "write_failed",
                })
            except Exception:
                _logger.warning(
                    "白箱不完整标记写入失败 task=%s event=%s",
                    self._run_id, event_type,
                )
            return False

    def allocation(self, account_id: str, *, round_no: int, count: int,
                   remaining: int, start_page: int | None = None,
                   end_page: int | None = None,
                   pending_remaining: int | None = None) -> None:
        self._segment += 1
        payload: dict[str, Any] = {
            "phase": self._phase, "platform": self._platform,
            "fact_kind": "reservation",
            "account_id": str(account_id), "segment": self._segment,
            "round": int(round_no), "count": int(count),
            "remaining": max(0, int(remaining)),
        }
        if start_page is not None:
            payload["start_page"] = int(start_page)
        if end_page is not None:
            payload["end_page"] = int(end_page)
        if pending_remaining is not None:
            payload["pending_remaining"] = max(0, int(pending_remaining))
        self._emit("account_allocation", payload)

    def request_start(self, *, account_id: str, segment: int | str,
                      attempt_id: str, attempt_no: int, input_count: int,
                      artifact_id: str) -> None:
        self._emit("account_request_start", {
            "fact_kind": "request_start", "phase": self._phase,
            "platform": self._platform, "account_id": str(account_id),
            "segment": segment, "attempt_id": str(attempt_id),
            "attempt_no": int(attempt_no), "input_count": int(input_count),
            "artifact_id": str(artifact_id), "started": True,
        })

    def request_terminal(
            self, *, account_id: str, segment: int | str, attempt_id: str,
            attempt_no: int, input_count: int, success_count: int,
            failure_count: int, short_circuit_count: int,
            unresolved_count: int, failure_code: str = "",
            handed_off: bool = False, success_keys: list[str] | None = None) -> None:
        counts = (int(success_count), int(failure_count),
                  int(short_circuit_count), int(unresolved_count))
        if any(value < 0 for value in counts) or sum(counts) != int(input_count):
            raise ValueError("详情请求终态数量不守恒")
        payload: dict[str, Any] = {
            "fact_kind": "request_terminal", "phase": self._phase,
            "platform": self._platform, "account_id": str(account_id),
            "segment": segment, "attempt_id": str(attempt_id),
            "attempt_no": int(attempt_no), "input_count": int(input_count),
            "success_count": counts[0], "failure_count": counts[1],
            "short_circuit_count": counts[2], "unresolved_count": counts[3],
            "failure_code": str(failure_code or ""),
            "handed_off": bool(handed_off),
        }
        if success_keys is not None:
            payload["success_keys"] = [str(key) for key in success_keys]
        self._emit("account_request_terminal", payload)

    def account_summary(self, *, summaries: list[dict[str, Any]],
                        total_success: int, reconciled: bool = True,
                        whitebox_incomplete: bool = False) -> None:
        self._emit("account_usage_summary", {
            "fact_kind": "account_summary", "phase": self._phase,
            "platform": self._platform, "accounts": [dict(item) for item in summaries],
            "total_success": int(total_success),
            "reconciled": bool(reconciled),
            "whitebox_incomplete": bool(whitebox_incomplete or self._incomplete),
        })

    def switch(self, *, from_account: str, to_account: str,
               reason: str, result: str) -> bool:
        if self._store is None or not self._run_id:
            return True
        from webui.resume_identity import record_account_switch_event
        ok = bool(record_account_switch_event(
            self._store, self._run_id,
            from_account=str(from_account), to_account=str(to_account),
            phase=self._phase, reason=str(reason), result=str(result),
        ))
        if ok and self._whitebox is not None:
            from webui.store_helpers import _now
            self._whitebox.record_for_owner("scrape", self._run_id, {
                "idempotency_key": f"account:{self._run_id}:{self._phase}:switch:{from_account}:{to_account}",
                "event_type": "account_switch", "occurred_at": _now(), "stage": self._phase,
                "unit_kind": "account_pool", "unit_key": self._phase, "attempt_no": 1,
                "required_evidence": False, "payload": {"from": str(from_account), "to": str(to_account),
                                                          "reason": str(reason), "result": str(result)},
            })
        return ok

    def handoff(self, *, blocked_account: str, to_account: str,
                remaining: int, result: str, blocked_reason: str | None = None,
                source_attempt_id: str | None = None) -> None:
        payload: dict[str, Any] = {
            "phase": self._phase, "platform": self._platform,
            "blocked_account": str(blocked_account),
            "to_account": str(to_account or ""),
            "remaining": max(0, int(remaining or 0)),
            "result": str(result),
        }
        if blocked_reason:
            payload["blocked_reason"] = str(blocked_reason)
        if source_attempt_id:
            payload["source_attempt_id"] = str(source_attempt_id)
        self._emit("account_handoff", payload)
