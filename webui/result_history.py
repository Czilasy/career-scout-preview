"""Result history service: metadata, detail and retention semantics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

_CST = timezone(timedelta(hours=8))
_PREVIEW_LENGTH = 120


def _decode_json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, dict) else fallback


def _iso_epoch_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CST)
    return int(parsed.timestamp() * 1000)


def _keyword_summary(row: dict[str, Any]) -> str:
    search = _decode_json(row.get("search_params_json"), {})
    screening = search.get("screening") if isinstance(search.get("screening"), dict) else {}
    raw_keyword = (
        search.get("keyword") or search.get("keywords")
        or screening.get("keyword") or screening.get("keywords") or ""
    )
    if isinstance(raw_keyword, list):
        keyword = ", ".join(str(item) for item in raw_keyword if item)
    else:
        keyword = str(raw_keyword or "").strip()
    raw_city = (
        search.get("city") or search.get("cities")
        or screening.get("city") or screening.get("cities") or []
    )
    if isinstance(raw_city, str):
        cities = [raw_city]
    elif isinstance(raw_city, list):
        cities = [str(item) for item in raw_city if item]
    else:
        cities = []
    summary = keyword or "未记录关键词"
    if cities:
        summary += " / " + " ".join(cities)
    return summary


def _preview(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= _PREVIEW_LENGTH:
        return text
    return text[:_PREVIEW_LENGTH] + "…"


class ResultHistoryService:
    """Application-level history assembly and lifecycle operations."""

    def __init__(self, store):
        self.store = store

    def list_history(self, platform: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list_history_rounds(platform)
        latest_ids: dict[str, str] = {}
        for row in rows:
            platform_key = str(row.get("platform") or "")
            if platform_key and row.get("archived_at") is None and platform_key not in latest_ids:
                latest_ids[platform_key] = str(row["id"])
        items = []
        for row in rows:
            platform_key = str(row.get("platform") or "")
            items.append({
                "run_id": str(row["id"]),
                "platform": row.get("platform"),
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "total_scraped": int(row.get("total_scraped") or 0),
                "total_kept": int(row.get("total_kept") or 0),
                "total_matched": int(row.get("match_count") or 0),
                "total_dropped": int(row.get("total_dropped") or 0),
                "pending_count": int(row.get("pending_count") or 0),
                "keyword_summary": _keyword_summary(row),
                "profile_summary_preview": _preview(row.get("profile_summary")),
                "archived_at": row.get("archived_at"),
                "is_latest": bool(latest_ids.get(platform_key) == str(row["id"])),
            })
        return items

    def get_round(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.get_screening_run(run_id)
        if run is None or run.get("record_kind") != "result_snapshot":
            return None
        payload = self.store.load_latest_pipeline_result(run_id)
        if payload is None:
            return None
        result = payload.get("result") or {}
        if not (result.get("jobs") or result.get("dropped")):
            return None
        raw_status = str(run.get("status") or "done")
        return {
            "ok": True,
            "has_result": True,
            "source_run_id": str(run_id),
            "platform": run.get("platform"),
            "status": raw_status,
            "saved_at": payload.get("saved_at"),
            "started_at": _iso_epoch_ms(payload.get("started_at")),
            "finished_at": _iso_epoch_ms(payload.get("finished_at")),
            "script_params": payload.get("script_params") or {},
            "execution_config": payload.get("execution_config") or {},
            "scrape_task_id": str(payload.get("scrape_task_id") or ""),
            "source_summary": {},
            "source_outcomes": [],
            "result": result,
        }

    def archive_latest(self) -> list[str]:
        return self.store.archive_all_current_results()

    def delete_round(self, run_id: str) -> bool:
        return self.store.delete_history_result_preserving_logs(run_id)

    def prune_retention(self, limit: int = 30) -> list[str]:
        return self.store.prune_result_history(limit)
