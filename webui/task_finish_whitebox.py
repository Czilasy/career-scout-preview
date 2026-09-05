"""Whitebox finalization for the manual partial-result finish action."""

from __future__ import annotations

from typing import Any

from webui.store_helpers import _now
from webui.whitebox import WhiteboxService
from webui.logging_setup import get_logger


def source_integrity_for_resume(store: Any, scrape_task_id: str) -> dict[str, Any]:
    try:
        return WhiteboxService(store).report("scrape", scrape_task_id)["integrity"]
    except Exception:
        return {"conclusion": "unverifiable", "label": "无法确认", "evidence_complete": False,
                "primary_code": "legacy_evidence_missing", "primary_reason": "历史证据不足，无法确认",
                "recommendation": "建议重新执行", "revision": 0}


def mark_resume_submission_failed(store: Any, run_id: str, reason: str, *, parent_owner_id: str = "") -> None:
    plan = {"stages": ["ai_rough", "jd_detail", "ai_fine"], "units": [
        {"unit_key": key, "unit_kind": "ai_stage", "stage": stage, "required": True}
        for key, stage in (("ai_rough", "ai_rough"), ("jd_detail", "jd_detail"), ("ai_fine", "ai_fine"))
    ]}
    WhiteboxService(store).mark_submission_failed(
        "screening", run_id, plan, reason, parent_owner_id=parent_owner_id, stage="resume_submit")


def finalize_manual_partial_whitebox_or_none(store: Any, run: dict[str, Any], *, parent_owner_id: str = "") -> dict[str, Any] | None:
    try:
        return finalize_manual_partial_whitebox(store, run, parent_owner_id=parent_owner_id)
    except Exception as exc:
        get_logger(__name__).warning("manual finish whitebox finalization failed: %s", type(exc).__name__)
        return None


def finalize_manual_partial_whitebox(store: Any, run: dict[str, Any], *, parent_owner_id: str = "") -> dict[str, Any]:
    """Close a manually saved partial run as an interrupted terminal fact."""
    owner_id = str(run.get("id") or "")
    stage = str(run.get("current_stage") or "task_finish")
    owner_kind = "recrawl" if stage.startswith("recrawl_") else "scrape" if stage == "scrape" else "screening"
    service = WhiteboxService(store)
    whitebox_run = store.get_whitebox_run(owner_kind, owner_id)
    if whitebox_run is None:
        unit_key = "manual_finish"
        service.begin(owner_kind, owner_id, {
            "stages": [stage],
            "units": [{"unit_key": unit_key, "unit_kind": "manual_finish", "stage": stage, "required": True}],
        }, parent_owner_id=parent_owner_id or None)
        whitebox_run = store.get_whitebox_run(owner_kind, owner_id)
    if not whitebox_run:
        raise RuntimeError("manual finish whitebox run unavailable")
    service.record(whitebox_run["id"], {
        "idempotency_key": f"manual-finish-interrupted:{owner_kind}:{owner_id}",
        "event_type": "task_interrupted", "occurred_at": _now(), "stage": stage,
        "required_evidence": True, "severity": "warning",
        "payload": {"stop_reason": "operator_stop", "snapshot_saved": True},
    })
    return service.finalize(whitebox_run["id"], lifecycle_end="interrupted")
