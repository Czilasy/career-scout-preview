"""Pure 033 V2 rules for reducing task evidence to one conclusion."""
from __future__ import annotations
from collections.abc import Iterable, Mapping
import json
from typing import Any
from webui.store_constants import WHITEBOX_CONCLUSIONS, WHITEBOX_CONCLUSION_LABELS
CONCLUSIONS = tuple(sorted(WHITEBOX_CONCLUSIONS))
CONCLUSION_LABELS = WHITEBOX_CONCLUSION_LABELS
_FAILURE_STATUSES = {"failed", "skipped", "incomplete"}
_UNKNOWN_STATUSES = {"planned", "running", "unverifiable", "unknown", ""}
_INTERRUPTED_ENDS = {"cancelled", "canceled", "stopped", "interrupted", "operator_stop"}

def _as_bool(value: Any) -> bool | None:
    if value is None: return None
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)) and value in (0, 1): return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}: return True
        if lowered in {"false", "0", "no"}: return False
    return None

def _plan_units(plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(plan, Mapping): return []
    raw = plan.get("units")
    if raw is None: raw = plan.get("planned_units")
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)): return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        value = dict(item) if isinstance(item, Mapping) else {"unit_key": str(item)}
        value.setdefault("unit_key", value.get("key") or f"unit-{index + 1}"); value["unit_key"] = str(value["unit_key"]); value["required"] = bool(value.get("required", True)); result.append(value)
    return result

def _unit_key(unit: Mapping[str, Any]) -> str:
    return str(unit.get("unit_key") or unit.get("key") or unit.get("id") or "")

def _output_count(unit: Mapping[str, Any]) -> int:
    for name in ("unit_unique_count", "output_count", "unique_count"):
        try:
            value = int(unit.get(name) or 0)
        except (TypeError, ValueError): value = 0
        if value: return max(0, value)
    try: return max(0, int(unit.get("returned_total_count") or 0))
    except (TypeError, ValueError): return 0

def _explicit_empty(unit: Mapping[str, Any]) -> bool:
    if str(unit.get("stop_reason") or "") == "explicit_empty":
        return True
    for name in ("explicit_empty", "empty_result", "empty_evidence"):
        value = unit.get(name)
        if isinstance(value, Mapping):
            if value: return True
        elif _as_bool(value) is True: return True
    return False

def _unit_evidence_complete(unit: Mapping[str, Any]) -> bool:
    explicit = _as_bool(unit.get("evidence_complete"))
    if explicit is not None: return explicit
    # A projection may provide the lower-level completion facts instead of a
    # precomputed flag.  Missing/unknown facts stay unverifiable.
    scope_complete = _as_bool(unit.get("scope_complete"))
    if scope_complete is not True: return False
    return str(unit.get("status") or "") in {"succeeded", "empty"}

def _safe_quality_counts(units: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for unit in units:
        value = unit.get("quality_counts")
        if isinstance(value, str):
            try: value = json.loads(value)
            except (TypeError, ValueError): value = {}
        if isinstance(value, Mapping):
            for key, raw in value.items():
                try: amount = max(0, int(raw))
                except (TypeError, ValueError): continue
                counts[str(key)] = counts.get(str(key), 0) + amount
    return counts

def _event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if event.get("payload") is not None else event.get("payload_json")
    if isinstance(payload, Mapping): return dict(payload)
    if isinstance(payload, str):
        try: decoded = json.loads(payload)
        except (TypeError, ValueError): return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}

def _whitebox_write_incomplete(events: Iterable[Mapping[str, Any]] | None) -> bool:
    """Return whether an unresolved required whitebox write was observed."""
    pending = False
    for event in events or ():
        if not isinstance(event, Mapping): continue
        event_type = str(event.get("event_type") or ""); payload = _event_payload(event); original_type = str(payload.get("original_event_type") or "")
        if event_type == "whitebox_incomplete" or (
            event_type == "emergency_record_imported"
            and original_type == "whitebox_incomplete"
        ):
            pending = True
        elif (
            event_type == "recovery_completed"
            and _as_bool(payload.get("whitebox_repaired")) is True
            and str(payload.get("repair_source") or "") == "whitebox_persistence"
            and _as_bool(payload.get("required_fact_persisted")) is True
        ):
            pending = False
    return pending

def _run_unique_count(units: Iterable[Mapping[str, Any]], fallback: int) -> int:
    ids: set[str] = set()
    for unit in units:
        jobs = unit.get("jobs") or unit.get("job_ids")
        if isinstance(jobs, Mapping): jobs = jobs.values()
        if isinstance(jobs, Iterable) and not isinstance(jobs, (str, bytes)):
            for job in jobs:
                value = (job.get("platform_job_id") or job.get("job_id") or job.get("id")) if isinstance(job, Mapping) else job
                if value: ids.add(str(value))
    return len(ids) if ids else max(0, int(fallback or 0))

def _primary_reason(code: str, *, failed: list[Mapping[str, Any]], unknown: list[Mapping[str, Any]]) -> str:
    reasons = {"plan_missing": "任务缺少冻结计划，无法确认是否完成", "legacy_evidence_missing": "历史证据不足，无法确认", "empty_evidence_missing": "结果为空但缺少明确空结果证据", "page_evidence_missing": "计划单元缺少页面或结束证据", "measurement_missing": "缺少逐项测量终态证据，无法确认是否完成", "unit_evidence_missing": "至少一个计划单元缺少完成证据"}
    if code in reasons: return reasons[code]
    if failed: return str(failed[0].get("error_reason") or failed[0].get("reason") or "计划单元执行失败")
    if unknown:
        return "至少一个计划单元的完成状态无法确认"
    return "任务完成证据不足"

def reduce_conclusion(
    plan: Mapping[str, Any] | None,
    units: Iterable[Mapping[str, Any]] | None,
    *,
    lifecycle_end: str | None = None,
    run_unique_count: int | None = None,
    events: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reduce a frozen plan and observed unit projections to one conclusion.

    The reducer is intentionally conservative: missing evidence wins over
    output counts, and no caller can pass an expected ``ok``/success flag.
    """
    planned = _plan_units(plan)
    observed = [dict(item) for item in (units or []) if isinstance(item, Mapping)]
    whitebox_incomplete = _whitebox_write_incomplete(events)
    by_key: dict[str, dict[str, Any]] = {}
    for item in observed:
        key = _unit_key(item)
        if key:
            by_key[key] = item

    if not planned:
        return _result(
            "unverifiable", False, bool(plan.get("degraded")) if isinstance(plan, Mapping) else False,
            "plan_missing", "任务缺少冻结计划，无法确认是否完成", [], [], 0, 0, {},
        )

    if str(lifecycle_end or "").strip().lower() in _INTERRUPTED_ENDS:
        degraded = any(bool(item.get("degraded")) for item in observed)
        return _result("interrupted", False, degraded, "interrupted", "任务因取消或停止而中断", [], [], 0, 0,
                       _safe_quality_counts(observed), completed_units=0)

    required_items: list[dict[str, Any]] = []
    missing_items: list[dict[str, Any]] = []
    for planned_item in planned:
        if not planned_item.get("required", True):
            continue
        key = _unit_key(planned_item)
        item = by_key.get(key)
        if item is None:
            missing_items.append({"unit_key": key, "status": "unknown"})
        else:
            required_items.append(item)

    failed_items = [item for item in required_items if str(item.get("status") or "") in _FAILURE_STATUSES]
    unknown_items = list(missing_items)
    for item in required_items:
        status = str(item.get("status") or "")
        if status in _UNKNOWN_STATUSES or not _unit_evidence_complete(item):
            # Known failures have an explicit terminal reason; they are not
            # reclassified as unknown merely because their evidence flag is 0.
            # Measurement absence is different: an executor result exists, but
            # there is no objective terminal count from which to decide.
            ai_fallback = str(item.get("stop_reason") or "") == "ai_keep_all_fallback"
            if (status not in _FAILURE_STATUSES
                    or item.get("error_code") == "measurement_missing"
                    or ai_fallback):
                unknown_items.append(item)
        if _output_count(item) == 0 and status in {"succeeded", "empty"} and not _explicit_empty(item):
            unknown_items.append({**item, "_empty_missing": True})

    output_sum = sum(_output_count(item) for item in required_items)
    unique_count = _run_unique_count(required_items, run_unique_count if run_unique_count is not None else output_sum)
    quality = _safe_quality_counts(required_items)
    degraded = any(bool(item.get("degraded")) for item in required_items) or bool((plan or {}).get("degraded"))

    if unknown_items:
        if str(lifecycle_end or "").strip().lower() == "failed":
            item = failed_items[0] if failed_items else {}
            code = str(item.get("error_code") or item.get("primary_code") or "task_failed")
            return _result(
                "failed", False, degraded, code,
                _primary_reason(code, failed=failed_items, unknown=unknown_items),
                failed_items, unknown_items, output_sum, unique_count, quality,
                completed_units=sum(
                    1 for observed_item in required_items
                    if str(observed_item.get("status") or "") in {"succeeded", "empty"}
                    and _unit_evidence_complete(observed_item)
                ),
            )
        code = "empty_evidence_missing" if any(item.get("_empty_missing") for item in unknown_items) else (
            "page_evidence_missing" if any(item.get("page_evidence_missing") for item in unknown_items) else "unit_evidence_missing"
        )
        if any(item.get("error_code") == "measurement_missing" for item in unknown_items):
            code = "measurement_missing"
        return _result("unverifiable", False, degraded, code, _primary_reason(code, failed=failed_items, unknown=unknown_items),
                       failed_items, unknown_items, output_sum, unique_count, quality,
                       completed_units=sum(1 for item in required_items
                                           if str(item.get("status") or "") in {"succeeded", "empty"}
                                           and _unit_evidence_complete(item)))

    if str(lifecycle_end or "").strip().lower() == "failed":
        item = failed_items[0] if failed_items else {}
        code = str(item.get("error_code") or item.get("primary_code") or "task_failed")
        return _result(
            "failed", False, degraded, code,
            _primary_reason(code, failed=failed_items, unknown=[]),
            failed_items, [], output_sum, unique_count, quality,
            completed_units=sum(
                1 for observed_item in required_items
                if str(observed_item.get("status") or "") in {"succeeded", "empty"}
                and _unit_evidence_complete(observed_item)
            ),
        )

    if failed_items:
        conclusion = "partial" if output_sum > 0 else "failed"
        item = failed_items[0]
        code = str(item.get("error_code") or item.get("primary_code") or "unit_failed")
        reason = _primary_reason(code, failed=failed_items, unknown=[])
        return _result(conclusion, True, degraded, code, reason, failed_items, [], output_sum, unique_count, quality,
                        completed_units=sum(1 for item in required_items
                                            if str(item.get("status") or "") in {"succeeded", "empty"}
                                            and _unit_evidence_complete(item)))

    if whitebox_incomplete:
        return _result(
            "unverifiable", False, degraded, "whitebox_incomplete",
            "必需白箱写入曾失败，无法确认任务是否完整完成",
            failed_items, [], output_sum, unique_count, quality,
            completed_units=sum(
                1 for observed_item in required_items
                if str(observed_item.get("status") or "") in {"succeeded", "empty"}
                and _unit_evidence_complete(observed_item)
            ),
        )

    # All required units have terminal evidence.  Empty is distinct from a
    # successful non-empty run and requires explicit empty facts on every unit.
    if output_sum == 0:
        if all(_explicit_empty(item) for item in required_items):
            return _result("empty", True, degraded, "explicit_empty", "已完成计划范围，但没有找到岗位", [], [], 0, 0, quality,
                           completed_units=len(required_items))
        return _result("unverifiable", False, degraded, "empty_evidence_missing", _primary_reason("empty_evidence_missing", failed=[], unknown=required_items),
                       [], required_items, 0, 0, quality)
    return _result("succeeded", True, degraded, None, "全部计划单元均已完成", [], [], output_sum, unique_count, quality,
                   completed_units=len(required_items))


def _result(
    conclusion: str,
    evidence_complete: bool,
    degraded: bool,
    primary_code: str | None,
    primary_reason: str,
    failed: list[Mapping[str, Any]],
    unknown: list[Mapping[str, Any]],
    output_sum: int,
    unique_count: int,
    quality: dict[str, int],
    completed_units: int | None = None,
) -> dict[str, Any]:
    if completed_units is None:
        completed_units = sum(
            1 for item in failed + unknown
            if str(item.get("status") or "") in {"succeeded", "empty"}
            and _unit_evidence_complete(item)
        )
    return {
        "conclusion": conclusion,
        "label": CONCLUSION_LABELS[conclusion],
        "degraded": bool(degraded),
        "evidence_complete": bool(evidence_complete),
        "primary_code": primary_code,
        "primary_reason": primary_reason,
        "summary": {
            "completed_units": max(0, int(completed_units)),
            "failed_units": len(failed),
            "unknown_units": len(unknown),
            "unit_output_sum": max(0, int(output_sum)),
            "run_unique_count": max(0, int(unique_count)),
            "quality_counts": quality,
        },
    }


# Descriptive aliases keep the pure module convenient for callers and tests.
compute_conclusion = reduce_conclusion
calculate_conclusion = reduce_conclusion
