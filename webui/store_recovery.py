"""恢复锁域（021 B2 拆分自 webui/store.py）：acquire/release/is_locked 与
恢复期写保护断言。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from webui.store_helpers import (
    _CST,
    _now,
)
from webui.store_constants import (
    _BEGIN_IMMEDIATE,
    _SQL_DELETE_EXPIRED_RECOVERY_LOCK,
)


class StoreRecoveryMixin:
    def acquire_recovery_lock(self, *, owner_token, maintenance=True,
                              ttl_seconds=300, wait_timeout=30):
        deadline = time.monotonic() + max(0, float(wait_timeout))
        while True:
            now = datetime.now(_CST)
            expires_at = (now + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
            active_workers = 0
            with self._connection() as conn:
                conn.execute(_BEGIN_IMMEDIATE)
                conn.execute(
                    _SQL_DELETE_EXPIRED_RECOVERY_LOCK, (now.isoformat(),)
                )
                row = conn.execute(
                    "SELECT owner_token FROM recovery_lock WHERE lock_id = 1"
                ).fetchone()
                if row is not None and row["owner_token"] != str(owner_token):
                    raise RuntimeError("recovery maintenance lock is already held")
                active_workers = self._active_worker_count(conn)
                if active_workers == 0:
                    conn.execute(
                        "INSERT INTO recovery_lock "
                        "(lock_id, owner_token, acquired_at, expires_at, maintenance) "
                        "VALUES (1, ?, ?, ?, ?) "
                        "ON CONFLICT(lock_id) DO UPDATE SET "
                        " owner_token = excluded.owner_token, acquired_at = excluded.acquired_at, "
                        " expires_at = excluded.expires_at, maintenance = excluded.maintenance",
                        (str(owner_token), now.isoformat(), expires_at, int(bool(maintenance))),
                    )
            if active_workers == 0:
                return True
            if time.monotonic() >= deadline:
                raise TimeoutError("recovery maintenance waiting for active workers")
            time.sleep(0.05)

    def release_recovery_lock(self, *, owner_token):
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM recovery_lock WHERE lock_id = 1 AND owner_token = ?",
                (str(owner_token),),
            )
        return cursor.rowcount > 0

    def is_recovery_locked(self) -> bool:
        now = _now()
        with self._connection() as conn:
            conn.execute(_SQL_DELETE_EXPIRED_RECOVERY_LOCK, (now,))
            row = conn.execute(
                "SELECT maintenance FROM recovery_lock WHERE lock_id = 1"
            ).fetchone()
        return bool(row is not None and row["maintenance"])

    def _assert_recovery_writes_allowed(self, conn=None):
        if conn is None:
            with self._connection() as owned_conn:
                return self._assert_recovery_writes_allowed(owned_conn)
        now = _now()
        conn.execute(_SQL_DELETE_EXPIRED_RECOVERY_LOCK, (now,))
        row = conn.execute(
            "SELECT maintenance FROM recovery_lock WHERE lock_id = 1"
        ).fetchone()
        if row is not None and row["maintenance"]:
            raise RuntimeError("recovery maintenance is active; new tasks are blocked")
