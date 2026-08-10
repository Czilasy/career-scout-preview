"""Shared low-level helpers for webui.store persistence layers."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

_CST = timezone(timedelta(hours=8))  # 东八区


def _now():
    return datetime.now(_CST).isoformat()


def _to_iso_timestamp(value):
    """Normalize epoch milliseconds or ISO text to local ISO text."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, _CST).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CST)
    return parsed.astimezone(_CST).isoformat()


def _uuid():
    return uuid.uuid4().hex[:16]


def _opt_str(value):
    """把 None 转为 SQL NULL（None），其他值转 str。"""
    return None if value is None else str(value)


def _now_minus_days(days):
    """返回 N 天前的 ISO 时间字符串（用于清理阈值）。"""
    return (datetime.now(_CST) - timedelta(days=int(days))).isoformat()


def _safe_quality_warnings(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("code"), str) and isinstance(item.get("path"), str):
            result.append({"code": item["code"], "path": item["path"]})
    return result


def _decode_json(value, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _build_pipeline_result_rows(rows: list) -> tuple[list, list]:
    """把 screening_results 行转换为结果快照的 jobs/dropped。

    两个加载函数（load_latest_pipeline_result / _for_platform）共用，
    避免复制粘贴导致的字段漂移（曾漏 verdict/caveats/tags/extra，
    使刷新后平台查询结果全部落入前端“待确认”）。
    """
    jobs = []
    dropped = []
    for row in rows:
        row = dict(row)
        if row.get("is_dropped"):
            raw_verdict = row.get("verdict") or ""
            reason = row.get("verdict_reason") or ""
            try:
                parsed = json.loads(raw_verdict)
                if isinstance(parsed, dict):
                    reason = str(parsed.get("reason") or reason)
            except (json.JSONDecodeError, TypeError):
                pass
            dropped.append({
                "platform": row.get("platform"),
                "platform_job_id": row["platform_job_id"],
                "job_id": row.get("job_id"),
                "title": row["title"],
                "company": row["company"],
                "salary": row["salary"],
                "location": row["location"],
                "experience": row.get("experience") or "",
                "degree": row.get("degree") or "",
                "extra": _decode_json(row.get("extra_json"), {}),
                "reason": reason,
                "canonical_url": row["source_url"],
            })
        else:
            raw_verdict = row.get("verdict") or ""
            verdict = raw_verdict
            verdict_reason = row.get("verdict_reason") or ""
            caveats = _decode_json(row.get("caveats_json"), [])
            try:
                parsed = json.loads(raw_verdict)
                if isinstance(parsed, dict):
                    verdict = str(parsed.get("verdict") or raw_verdict)
                    verdict_reason = str(parsed.get("reason") or verdict_reason)
                    if isinstance(parsed.get("caveats"), list):
                        caveats = parsed["caveats"]
            except (json.JSONDecodeError, TypeError):
                pass
            jobs.append({
                "platform": row.get("platform"),
                "platform_job_id": row["platform_job_id"],
                "job_id": row.get("job_id"),
                "title": row["title"],
                "company": row["company"],
                "salary": row["salary"],
                "location": row["location"],
                "experience": row.get("experience") or "",
                "degree": row.get("degree") or "",
                "extra": _decode_json(row.get("extra_json"), {}),
                "tags": row["tags"],
                "jd": row["jd"],
                "source_url": row["source_url"],
                "canonical_url": row["source_url"],
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                "caveats": caveats,
            })
    return jobs, dropped


def _candidate_profile_content_hash(summary, unknowns, facts) -> str:
    normalized_facts = []
    for fact in facts or []:
        normalized_facts.append({
            "stable_key": fact.get("stable_key", ""),
            "fact_type": fact.get("fact_type", ""),
            "value": fact.get("value", {}),
            "normalized_value": fact.get("normalized_value", ""),
            "source_kind": fact.get("source_kind", ""),
            "assertion_type": fact.get("assertion_type", ""),
            "confidence": fact.get("confidence", 0),
            "verification_status": fact.get("verification_status", ""),
            "evidence_ids": sorted(fact.get("evidence_ids", []) or []),
        })
    normalized_facts.sort(key=lambda item: (item["stable_key"], item["fact_type"], item["normalized_value"]))
    blob = json.dumps(
        {"summary": summary or {}, "unknowns": unknowns or [], "facts": normalized_facts},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
