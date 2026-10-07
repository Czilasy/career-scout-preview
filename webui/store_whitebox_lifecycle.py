"""047 US5：白箱证据写入事务内的 owner/终结/attempt 守卫。

策略集中在本模块，`store_whitebox` 只在既有写事务里做薄接线：
- 已终结（terminal / 已定稿）白箱的迟到 unit/page 事实只记 late_callback
  审计，不改结论、摘要、revision；
- resume 之后到达的更旧 attempt 事实同样只记诊断，不归一成新 attempt，
  不污染新证据；
- 正常 resume 重开（lifecycle 回到 running、结论清空）后仍接收新证据。
"""

from __future__ import annotations

# 会被投影进 whitebox_units 的 unit/page 事实类型。
_PROJECTED_EVENT_TYPES = frozenset({
    "unit_started",
    "page_started",
    "page_completed",
    "scope_completed",
    "source_exhausted",
    "explicit_empty",
    "unit_failed",
    "unit_incomplete",
    "unit_skipped",
    "submission_failed",
})

# 正常收尾（用户结束保存）的终态结论：迟到 worker 失败不得覆盖。
_PROTECTED_TERMINAL_CONCLUSIONS = frozenset({"interrupted"})


def is_projected_fact(event_type: object) -> bool:
    return str(event_type or "").strip() in _PROJECTED_EVENT_TYPES


def late_write_verdict(conn, run_id, *, event_type="", unit_key="", attempt_no=0):
    """Judge one incoming whitebox fact inside its write transaction.

    Returns ``(reason, detail)`` where reason is ``""`` when the write may
    proceed, ``"whitebox_terminal"`` when the owner already reached a
    finalized terminal state, or ``"stale_attempt"`` when the fact belongs
    to an attempt older than what this owner has already recorded.
    """
    run_id = str(run_id or "")
    if not run_id:
        return "", ""
    row = conn.execute(
        "SELECT * FROM whitebox_runs WHERE id = ?", (run_id,),
    ).fetchone()
    if row is None:
        return "", ""
    lifecycle = str(row["lifecycle_status"] or "")
    conclusion = str(row["conclusion"] or "").strip().lower()
    finalized_at = row["finalized_at"] if "finalized_at" in row.keys() else None
    if lifecycle == "terminal" or (finalized_at and conclusion):
        return "whitebox_terminal", conclusion or "terminal"

    key = str(unit_key or "").strip()
    try:
        requested = int(attempt_no or 0)
    except (TypeError, ValueError):
        requested = 0
    if key and requested > 0:
        prior = conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) AS max_attempt "
            "FROM whitebox_events WHERE whitebox_run_id = ? AND unit_key = ? "
            "AND attempt_no IS NOT NULL",
            (run_id, key),
        ).fetchone()
        max_attempt = int((prior["max_attempt"] if prior is not None else 0) or 0)
        if max_attempt > requested:
            return "stale_attempt", f"recorded={max_attempt} incoming={requested}"
    return "", ""


def guard_finalize_conclusion(current, desired):
    """Protect a normal interrupted closing from a late worker failure.

    Returns ``(blocked, detail)``; ``blocked`` is True when the stored
    conclusion must win over the incoming one.
    """
    current_conclusion = str((current or {}).get("conclusion") or "").strip().lower()
    current_lifecycle = str((current or {}).get("lifecycle_status") or "")
    desired_conclusion = str((desired or {}).get("conclusion") or "").strip().lower()
    if (
        current_lifecycle == "terminal"
        and current_conclusion in _PROTECTED_TERMINAL_CONCLUSIONS
        and desired_conclusion == "failed"
    ):
        return True, f"{current_conclusion}<-{desired_conclusion}"
    return False, ""


def record_late_fact(conn, run_id, *, event_type, unit_key, attempt_no, reason, detail, idem=""):
    """Append (idempotent) one safe late_callback diagnostic event."""
    run_id = str(run_id or "")
    if not run_id:
        return None
    original = str(event_type or "unknown")
    key = str(unit_key or "")
    try:
        attempt = max(0, int(attempt_no or 0))
    except (TypeError, ValueError):
        attempt = 0
    from webui.store_helpers import _now

    idempotency_key = (
        f"late-callback:{run_id}:{original}:{key}:{attempt}:"
        f"{str(idem or '')[:64]}"[:180]
    )
    existing = conn.execute(
        "SELECT * FROM whitebox_events WHERE whitebox_run_id = ? AND idempotency_key = ?",
        (run_id, idempotency_key),
    ).fetchone()
    if existing is not None:
        return dict(existing)
    now = _now()
    import json as _json

    sequence = int(conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM whitebox_events WHERE whitebox_run_id = ?",
        (run_id,),
    ).fetchone()[0])
    event_id = f"wbe-late-{run_id[-24:]}-{sequence}"
    conn.execute(
        "INSERT INTO whitebox_events (id, whitebox_run_id, sequence, idempotency_key, stage, "
        "unit_kind, unit_key, attempt_no, event_type, required_evidence, severity, payload_json, "
        "origin, occurred_at, recorded_at) "
        "VALUES (?, ?, ?, ?, 'late', NULL, ?, ?, 'late_callback', 0, 'warning', ?, "
        "'late_callback', ?, ?)",
        (
            event_id, run_id, sequence, idempotency_key, key or None, attempt or None,
            _json.dumps({"original_event_type": original, "reason": str(reason or ""),
                         "detail": str(detail or "")[:200]}, ensure_ascii=False),
            now, now,
        ),
    )
    row = conn.execute("SELECT * FROM whitebox_events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row is not None else None


__all__ = [
    "is_projected_fact", "late_write_verdict",
    "guard_finalize_conclusion", "record_late_fact",
]
