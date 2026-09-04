"""Safe identities and accounting for real R2 detail attempts."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import uuid
from typing import Iterable
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_.-]+")
class DetailAttemptError(ValueError):
    """An attempt terminal cannot satisfy the V4 accounting contract."""
@dataclass(frozen=True)
class DetailAttempt:
    task_id: str
    segment_id: str
    account_id: str
    attempt_id: str
    attempt_no: int
    input_count: int
    artifact_id: str
    artifact_path: str
    started: bool = True
class DetailAttemptTracker:
    """Track reservations, real calls, terminal facts, and unique successes."""
    def __init__(self, task_id: str, artifact_dir: str | Path):
        self.task_id = str(task_id or "")
        self.artifact_dir = Path(artifact_dir)
        self._segment_no = 0
        self._attempt_no: defaultdict[str, int] = defaultdict(int)
        self._terminal_ids: set[str] = set()
        self._started_ids: set[str] = set()
        self._account_order: list[str] = []
        self._usage: dict[str, dict[str, int]] = {}
        self._successful_job_owner: dict[str, str] = {}
        self._replay_incomplete = False
    @staticmethod
    def _token(value: object, fallback: str) -> str:
        raw = str(value or "").strip()
        return _SAFE_TOKEN.sub("_", raw)[:80] or fallback
    def _ensure_account(self, account_id: str) -> dict[str, int]:
        account = str(account_id or "").strip()
        if not account:
            raise DetailAttemptError("账号标识不能为空")
        if account not in self._usage:
            self._account_order.append(account)
            self._usage[account] = {
                key: 0 for key in (
                    "reserved_count", "request_started_count",
                    "unique_success_count", "failure_count", "unresolved_count",
                    "short_circuit_count", "handoff_in_count", "handoff_out_count",
                )
            }
        return self._usage[account]
    def register_accounts(self, account_ids: Iterable[object]) -> None:
        for account_id in account_ids:
            self._ensure_account(str(account_id))
    @classmethod
    def from_task_events(cls, task_id: str, artifact_dir: str | Path,
                         events: Iterable[dict] | None) -> "DetailAttemptTracker":
        """Rebuild V4 account facts before continuing a paused task."""
        tracker = cls(task_id, artifact_dir)
        for event in events or []:
            if not isinstance(event, dict):
                tracker._replay_incomplete = True
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("phase") not in (None, "R2"):
                continue
            kind = str(event.get("type") or "")
            try:
                if kind == "account_pool_snapshot":
                    tracker.register_accounts(item["account_id"] for item in payload.get("accounts", [])
                                             if isinstance(item, dict) and item.get("account_id"))
                elif kind == "account_allocation":
                    tracker._ensure_account(str(payload["account_id"]))["reserved_count"] += int(payload["count"])
                elif kind == "account_request_start":
                    tracker._replay_start(payload)
                elif kind == "account_request_terminal":
                    tracker._replay_terminal(payload)
                elif kind == "account_handoff":
                    to_account = str(payload.get("to_account") or "")
                    handoff_count = int(payload.get("remaining") or 0)
                    if to_account and handoff_count > 0:
                        tracker.record_handoff(str(payload["blocked_account"]), to_account, handoff_count)
            except (KeyError, TypeError, ValueError):
                tracker._replay_incomplete = True
        return tracker
    def _replay_start(self, payload: dict) -> None:
        attempt_id = str(payload["attempt_id"])
        if attempt_id in self._started_ids:
            return
        account = str(payload["account_id"])
        self._ensure_account(account)["request_started_count"] += int(payload["input_count"])
        self._started_ids.add(attempt_id)
        segment = str(payload.get("segment") or "legacy-segment")
        self._attempt_no[segment] = max(self._attempt_no[segment], int(payload.get("attempt_no") or 0))
    def _replay_terminal(self, payload: dict) -> None:
        attempt_id = str(payload["attempt_id"])
        if attempt_id in self._terminal_ids:
            return
        counts = tuple(int(payload.get(key) or 0) for key in
                       ("success_count", "failure_count", "short_circuit_count", "unresolved_count"))
        if any(value < 0 for value in counts) or sum(counts) != int(payload["input_count"]):
            raise DetailAttemptError("历史详情终态数量不守恒")
        usage = self._ensure_account(str(payload["account_id"]))
        usage["failure_count"] += counts[1]; usage["unresolved_count"] += counts[3]
        usage["short_circuit_count"] += counts[2]
        keys = payload.get("success_keys")
        if not isinstance(keys, list) or len(keys) != counts[0]:
            self._replay_incomplete = True
        else:
            for key in keys:
                safe_key = str(key or "").strip()
                if len(safe_key) != 64 or any(char not in "0123456789abcdef" for char in safe_key.lower()):
                    safe_key = self._success_key(safe_key)
                if safe_key and safe_key not in self._successful_job_owner:
                    self._successful_job_owner[safe_key] = str(payload["account_id"])
                    usage["unique_success_count"] += 1
        self._terminal_ids.add(attempt_id)
    def reserve(self, account_id: str, count: int, *, round_no: int = 0) -> str:
        count = self._nonnegative(count, "reserved_count")
        if count <= 0:
            raise DetailAttemptError("预留数量必须为正数")
        usage = self._ensure_account(account_id)
        usage["reserved_count"] += count
        self._segment_no += 1
        return f"{self._token(self.task_id, 'task')}-segment-{self._segment_no}"
    def new_attempt(self, segment_id: str, account_id: str, input_count: int,
                    *, started: bool = True) -> DetailAttempt:
        segment = str(segment_id or "").strip()
        if not segment:
            raise DetailAttemptError("详情尝试缺少分配段")
        input_count = self._nonnegative(input_count, "input_count")
        account = str(account_id or "").strip()
        self._ensure_account(account)
        self._attempt_no[segment] += 1
        attempt_no = self._attempt_no[segment]
        nonce = uuid.uuid4().hex
        task_token = self._token(self.task_id, "task")
        segment_token = self._token(segment, "segment")
        account_token = self._token(account, "account")
        artifact_id = f"{task_token}-{segment_token}-{account_token}-{attempt_no}-{nonce}"
        artifact_path = self.artifact_dir / f"pipeline_batch_{artifact_id}.json"
        attempt = DetailAttempt(
            task_id=self.task_id, segment_id=segment, account_id=account,
            attempt_id=f"{artifact_id}-attempt", attempt_no=attempt_no,
            input_count=input_count, artifact_id=artifact_id,
            artifact_path=str(artifact_path), started=bool(started),
        )
        if started:
            self._started_ids.add(attempt.attempt_id)
            self._usage[account]["request_started_count"] += input_count
        return attempt
    def record_terminal(self, attempt: DetailAttempt, *, success_count: int,
                        failure_count: int, short_circuit_count: int,
                        unresolved_count: int, failure_code: str = "",
                        success_job_ids: Iterable[object] = (),
                        handed_off: bool = False) -> None:
        del failure_code, handed_off  # retained in the caller-facing contract
        if attempt.attempt_id in self._terminal_ids:
            raise DetailAttemptError("详情尝试重复写入终态")
        counts = {"success_count": self._nonnegative(success_count, "success_count"),
                  "failure_count": self._nonnegative(failure_count, "failure_count"),
                  "short_circuit_count": self._nonnegative(short_circuit_count, "short_circuit_count"),
                  "unresolved_count": self._nonnegative(unresolved_count, "unresolved_count")}
        if sum(counts.values()) != attempt.input_count:
            raise DetailAttemptError("详情尝试终态数量不守恒")
        self._terminal_ids.add(attempt.attempt_id)
        usage = self._ensure_account(attempt.account_id)
        usage["failure_count"] += counts["failure_count"]
        usage["unresolved_count"] += counts["unresolved_count"]
        usage["short_circuit_count"] += counts["short_circuit_count"]
        for job_id in success_job_ids:
            safe_job_id = str(job_id or "").strip()
            success_key = self._success_key(safe_job_id)
            if not safe_job_id or success_key in self._successful_job_owner:
                continue
            self._successful_job_owner[success_key] = attempt.account_id
            usage["unique_success_count"] += 1
    def complete_attempt(self, attempt: DetailAttempt, entries: list[tuple],
                         outcomes: dict, batch_exception: object, *, is_success,
                         is_unresolved, local_short_circuit: bool = False) -> dict:
        if local_short_circuit:
            success_ids: list[str] = []
            failure_count = unresolved_count = 0
            short_count = len(entries)
        else:
            success_ids = [str(entry[1]) for entry in entries
                           if (outcome := outcomes.get(entry[1])) is not None
                           and is_success(outcome)]
            failure_count = unresolved_count = 0
            for entry in entries:
                outcome = outcomes.get(entry[1])
                if outcome is not None and is_success(outcome):
                    continue
                code = (getattr(outcome, "failed_code", "") if outcome is not None
                        else batch_exception)
                if is_unresolved(code):
                    unresolved_count += 1
                else:
                    failure_count += 1
            short_count = 0
        failure_code = str(batch_exception or next(
            (getattr(outcomes.get(entry[1]), "failed_code", "") for entry in entries
             if outcomes.get(entry[1]) is not None
             and getattr(outcomes.get(entry[1]), "failed_code", "")), "") or "")
        result = {"success_count": len(success_ids), "failure_count": failure_count,
                  "short_circuit_count": short_count, "unresolved_count": unresolved_count,
                  "failure_code": failure_code, "success_job_ids": success_ids,
                  "success_keys": [self._success_key(job_id) for job_id in success_ids],
                  "handed_off": False}
        self.record_terminal(
            attempt, success_count=result["success_count"],
            failure_count=result["failure_count"], short_circuit_count=result["short_circuit_count"],
            unresolved_count=result["unresolved_count"], failure_code=result["failure_code"],
            success_job_ids=success_ids, handed_off=False,
        )
        return result
    def record_handoff(self, from_account: str, to_account: str, count: int) -> None:
        count = self._nonnegative(count, "handoff_count")
        if count <= 0:
            return
        self._ensure_account(from_account)["handoff_out_count"] += count
        self._ensure_account(to_account)["handoff_in_count"] += count
    def summary(self, *, total_success: int | None = None) -> dict:
        """Return safe per-account usage and an explicit reconciliation result."""
        accounts = [{"account_id": account_id, **self._usage[account_id]}
                    for account_id in self._account_order]
        unique_success = len(self._successful_job_owner)
        reconciled = total_success is None or int(total_success) == unique_success
        return {"task_id": self.task_id, "accounts": accounts,
                "total_success": unique_success,
                "expected_total_success": None if total_success is None else int(total_success),
                "reconciled": reconciled,
                "whitebox_incomplete": not reconciled or self._replay_incomplete}
    @staticmethod
    def _success_key(job_id: object) -> str: return hashlib.sha256(str(job_id or "").encode("utf-8")).hexdigest()
    @property
    def terminal_count(self) -> int: return len(self._terminal_ids)
    @property
    def started_count(self) -> int: return len(self._started_ids)
    @staticmethod
    def _nonnegative(value: int, label: str) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise DetailAttemptError(f"{label} 必须是整数") from exc
        if normalized < 0: raise DetailAttemptError(f"{label} 不能为负数")
        return normalized
