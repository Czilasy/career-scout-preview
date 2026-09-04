"""Task-level R2 rotation state and its recoverable checkpoint."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
from typing import Any, Callable
from webui.account_round_robin import DetailRobin, PoolEntry
from webui.logging_setup import get_logger
_logger = get_logger(__name__)

class R2RotationSnapshotError(ValueError):
    """A checkpoint cannot be safely applied to the current task."""
class R2RotationSession:
    """Keep one DetailRobin alive for one logical R2 stage."""

    SNAPSHOT_VERSION = 1

    def __init__(self, source: Any, entries: list[PoolEntry], *, task_id: str = "",
                 platform: str = "", on_account_switch: Callable[[str, str], None] | None = None,
                 switch_event_store: Any = None, completed_count: int = 0,
                 robin: DetailRobin | None = None):
        if not entries:
            raise ValueError("R2RotationSession 需至少一个账号")
        self._task_id = str(task_id or "")
        self._platform = str(platform or getattr(source, "platform", "boss") or "boss")
        self._account_order = tuple(str(entry.account_id) for entry in entries)
        if len(set(self._account_order)) != len(self._account_order):
            raise ValueError("R2RotationSession 账号不得重复")
        self._quotas = {str(entry.account_id): max(1, int(entry.quota)) for entry in entries}
        self._robin = robin or DetailRobin(
            source, list(entries), run_id=self._task_id,
            on_account_switch=on_account_switch, switch_event_store=switch_event_store,
        )
        self._completed_count = max(0, int(completed_count))
        self._completed_digest = ""
    @classmethod
    def from_robin(cls, source: Any, robin: DetailRobin, *, task_id: str = "",
                   platform: str = "") -> "R2RotationSession":
        queue = robin._queue
        entries = list(queue._entries) + list(queue._blocked)
        if not entries:
            raise ValueError("R2RotationSession 账号池为空")
        return cls(source, entries, task_id=task_id, platform=platform, robin=robin)
    @classmethod
    def from_snapshot(cls, source: Any, snapshot: dict[str, Any], *, task_id: str = "",
                      platform: str = "", entries: list[PoolEntry] | None = None,
                      on_account_switch: Callable[[str, str], None] | None = None,
                      switch_event_store: Any = None) -> "R2RotationSession":
        entries = entries or cls._entries_from_snapshot(snapshot)
        session = cls(
            source, entries, task_id=task_id,
            platform=platform or str(getattr(source, "platform", "boss") or "boss"),
            on_account_switch=on_account_switch, switch_event_store=switch_event_store,
        )
        session.restore_snapshot(snapshot)
        return session
    @staticmethod
    def _entries_from_snapshot(snapshot: dict[str, Any]) -> list[PoolEntry]:
        if not isinstance(snapshot, dict):
            raise R2RotationSnapshotError("R2 快照不是对象")
        order, quotas = snapshot.get("account_order"), snapshot.get("quotas")
        if not isinstance(order, list) or not isinstance(quotas, dict):
            raise R2RotationSnapshotError("R2 快照缺少冻结账号池")
        try:
            return [PoolEntry(str(account_id), int(quotas[account_id])) for account_id in order]
        except (KeyError, TypeError, ValueError) as exc:
            raise R2RotationSnapshotError("R2 快照账号配额损坏") from exc
    @property
    def robin(self) -> DetailRobin:
        return self._robin
    @property
    def task_id(self) -> str:
        return self._task_id
    @property
    def platform(self) -> str:
        return self._platform

    @property
    def account_order(self) -> tuple[str, ...]:
        return self._account_order

    @property
    def active_account(self) -> str | None:
        return self._robin._queue.head_account

    @property
    def remaining_quota(self) -> int:
        remaining = self._robin._queue._remaining
        return int(remaining[0]) if remaining else 0

    @property
    def last_round(self) -> int:
        return int(self._robin._queue.last_round)

    @property
    def blocked_accounts(self) -> list[str]:
        return list(self._robin.blocked_accounts)

    @property
    def completed_count(self) -> int:
        return self._completed_count
    def set_completed_count(self, value: int) -> None:
        self._completed_count = max(0, int(value))
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._robin, name)
    def override_active_account(self, account_id: str) -> None:
        wanted = str(account_id or "").strip()
        queue = self._robin._queue
        if wanted not in [entry.account_id for entry in queue._entries]:
            raise R2RotationSnapshotError("显式恢复账号不在可用冻结账号池")
        index = next(i for i, entry in enumerate(queue._entries)
                     if entry.account_id == wanted)
        if index:
            queue._entries = queue._entries[index:] + queue._entries[:index]
            queue._remaining = queue._remaining[index:] + queue._remaining[:index]
            self._robin._pending_switch_reason = "explicit_resume"

    def export_snapshot(self, *, completed_count: int | None = None) -> dict[str, Any]:
        queue = self._robin._queue
        return {
            "version": self.SNAPSHOT_VERSION, "task_id": self._task_id,
            "platform": self._platform, "account_order": list(self._account_order),
            "quotas": dict(self._quotas),
            "round_no": int(queue.last_round if queue._entries else queue._round),
            "active_account": queue.head_account,
            "remaining_quota": self.remaining_quota,
            "blocked_accounts": list(queue.blocked_accounts),
            "completed_count": self._completed_count if completed_count is None else max(0, int(completed_count)),
            "completed_digest": self._completed_digest,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "round_seen": sorted(str(value) for value in queue._round_seen),
            "next_round_pending": bool(queue._next_round_pending),
        }

    def restore_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._validate_snapshot(snapshot)
        queue = self._robin._queue
        blocked = [str(value) for value in snapshot["blocked_accounts"]]
        available = [account_id for account_id in self._account_order if account_id not in blocked]
        active = str(snapshot["active_account"]) if snapshot["active_account"] is not None else None
        if active is None:
            if available:
                raise R2RotationSnapshotError("R2 快照没有当前账号但队列仍有可用账号")
            queue._entries, queue._remaining = [], []
        else:
            start = available.index(active)
            ordered = available[start:] + available[:start]
            queue._entries = [PoolEntry(account_id, self._quotas[account_id]) for account_id in ordered]
            queue._remaining = [int(snapshot["remaining_quota"])] + [
                self._quotas[account_id] for account_id in ordered[1:]
            ]
        queue._blocked = [PoolEntry(account_id, self._quotas[account_id]) for account_id in blocked]
        queue._round = queue._last_round = int(snapshot["round_no"])
        queue._last_remaining = int(snapshot["remaining_quota"])
        queue._round_seen = set(str(value) for value in snapshot.get("round_seen", []))
        queue._next_round_pending = bool(snapshot.get("next_round_pending", False))
        self._completed_count = int(snapshot["completed_count"])
        self._completed_digest = str(snapshot.get("completed_digest") or "")

    def _validate_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            raise R2RotationSnapshotError("R2 快照不是对象")
        required = {"version", "task_id", "platform", "account_order", "quotas",
                    "round_no", "active_account", "remaining_quota",
                    "blocked_accounts", "completed_count", "completed_digest", "saved_at"}
        missing = sorted(required - set(snapshot))
        if missing:
            raise R2RotationSnapshotError(f"R2 快照缺少字段: {','.join(missing)}")
        if snapshot["version"] != self.SNAPSHOT_VERSION:
            raise R2RotationSnapshotError("R2 快照版本不支持")
        if str(snapshot["task_id"]) != self._task_id:
            raise R2RotationSnapshotError("R2 快照任务身份不一致")
        if str(snapshot["platform"]) != self._platform:
            raise R2RotationSnapshotError("R2 快照平台身份不一致")
        if snapshot["account_order"] != list(self._account_order):
            raise R2RotationSnapshotError("R2 快照账号顺序与冻结池不一致")
        if snapshot["quotas"] != self._quotas:
            raise R2RotationSnapshotError("R2 快照账号配额与冻结池不一致")
        blocked = snapshot["blocked_accounts"]
        if (not isinstance(blocked, list) or len(set(blocked)) != len(blocked)
                or any(str(value) not in self._account_order for value in blocked)):
            raise R2RotationSnapshotError("R2 快照阻断账号损坏")
        active = snapshot["active_account"]
        if active is not None and (not isinstance(active, str) or active not in self._account_order):
            raise R2RotationSnapshotError("R2 快照当前账号不在冻结池")
        if active is not None and active in blocked:
            raise R2RotationSnapshotError("R2 快照当前账号已被阻断")
        try:
            round_no, remaining, completed = (int(snapshot[key]) for key in
                                               ("round_no", "remaining_quota", "completed_count"))
        except (TypeError, ValueError) as exc:
            raise R2RotationSnapshotError("R2 快照计数损坏") from exc
        if round_no < 1 or completed < 0:
            raise R2RotationSnapshotError("R2 快照轮次或完成数无效")
        if active is None and remaining != 0:
            raise R2RotationSnapshotError("R2 空队列剩余配额必须为 0")
        if active is not None and not 0 <= remaining <= self._quotas[active]:
            raise R2RotationSnapshotError("R2 当前账号剩余配额越界")
        if not isinstance(snapshot["saved_at"], str) or not snapshot["saved_at"].strip():
            raise R2RotationSnapshotError("R2 快照保存时间损坏")
        if not isinstance(snapshot["completed_digest"], str):
            raise R2RotationSnapshotError("R2 快照完成集合摘要损坏")

    def save_checkpoint(self, store: Any, *, completed_count: int | None = None,
                        completed_ids: list[str] | None = None) -> dict[str, Any]:
        if completed_ids is not None:
            self._completed_digest = self.completed_digest(completed_ids)
        snapshot = self.export_snapshot(completed_count=completed_count)
        if store is None or not self._task_id:
            return snapshot
        try:
            store.append_task_event(self._task_id, "r2_rotation_checkpoint", snapshot)
        except Exception as exc:
            _logger.warning("R2 轮询断点写入失败 task=%s", self._task_id, exc_info=True)
            raise R2RotationSnapshotError("R2 轮询断点写入失败") from exc
        return snapshot

    @staticmethod
    def completed_digest(job_ids: list[str]) -> str:
        joined = "\x1f".join(sorted(str(job_id) for job_id in job_ids))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def emit_account_summary(self, tracker: Any, *, total_success: int) -> dict:
        summary = tracker.summary(total_success=total_success)
        whitebox = getattr(self._robin, "_whitebox", None)
        if whitebox is not None:
            whitebox.account_summary(
                summaries=summary["accounts"], total_success=summary["total_success"],
                reconciled=summary["reconciled"],
                whitebox_incomplete=summary["whitebox_incomplete"],
            )
        return summary

    @staticmethod
    def latest_checkpoint(store: Any, task_id: str) -> dict[str, Any] | None:
        if store is None or not hasattr(store, "list_task_events"):
            return None
        try:
            events = store.list_task_events(str(task_id))
        except Exception as exc:
            _logger.warning("R2 轮询断点读取失败 task=%s", task_id, exc_info=True)
            raise R2RotationSnapshotError("R2 轮询断点读取失败") from exc
        for event in reversed(events or []):
            if isinstance(event, dict) and event.get("type") == "r2_rotation_checkpoint":
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    raise R2RotationSnapshotError("R2 快照不是对象")
                return dict(payload)
        return None
