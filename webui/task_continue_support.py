"""Small state helpers shared by task continuation routes."""
from __future__ import annotations

import time


_FINISH_BATCH_WAIT_TIMEOUT_S = 15 * 60
_FINISH_BATCH_SETTLE_GRACE_S = 1.5
_FINALIZE_WAIT_TIMEOUT_S = 5.0


def jd_batch_active(ctx, task_id: str) -> bool:
    """Return whether a task is inside a JD detail batch."""
    with ctx.lock:
        task = ctx.tasks.get(task_id)
        progress = dict((task or {}).get("progress") or {})
    if str(progress.get("stage") or "") not in ("fetch_jd", "jd_detail"):
        return False
    batch = progress.get("jd_batch")
    return isinstance(batch, dict) and bool(batch)


def wait_for_jd_batch_settle(
    ctx,
    task_id: str,
    *,
    timeout_s: float = _FINISH_BATCH_WAIT_TIMEOUT_S,
    settle_grace_s: float = _FINISH_BATCH_SETTLE_GRACE_S,
) -> bool:
    """Wait for the current JD batch to merge its checkpoint before finishing."""
    if not jd_batch_active(ctx, task_id):
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not jd_batch_active(ctx, task_id):
            break
        time.sleep(0.5)
    time.sleep(settle_grace_s)
    return True
