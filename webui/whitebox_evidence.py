"""抓取结果白箱适配器。

该适配器保持 ``webui.whitebox.ScrapeEvidence`` 的公开导入兼容，
但把抓取循环的证据投影从白箱服务核心中隔离出来。
"""

from __future__ import annotations

from typing import Any

from webui.whitebox import WhiteboxService, WhiteboxWriteError, _now
from webui.whitebox_rules import reduce_conclusion


class ScrapeEvidence:
    def __init__(self, store: Any, owner_id: Any, combos: list[dict], pages: int):
        self.store = store
        self.owner_id = str(owner_id or "")
        self.plan = {"stages": ["scrape_list"], "units": []}
        self.units: dict[str, dict[str, Any]] = {}
        self.attempts: dict[str, int] = {}
        for item in combos:
            key = str(item.get("combo_key") or f"{item.get('keyword', '')}|{item.get('city', '')}")
            self.plan["units"].append({
                "unit_key": key,
                "unit_kind": "keyword_city",
                "stage": "scrape_list",
                "planned_pages": pages,
                "required": True,
            })
            self.units[key] = {
                "unit_key": key,
                "status": "planned",
                "evidence_complete": False,
                "unit_unique_count": 0,
            }
        self.service = WhiteboxService(store) if owner_id and hasattr(store, "create_whitebox_run") else None
        self.run_ids: set[str] = set()
        self.ref = None
        self.startup_error = None
        if self.service is not None:
            try:
                self.ref = self.service.begin("scrape", self.owner_id, self.plan)
                self.attempts = {
                    str(unit.get("unit_key") or ""): int(unit.get("attempt_no") or 1)
                    for unit in self.store.list_whitebox_units(self.ref.id)
                    if unit.get("unit_key")
                }
                self._record(
                    "task_started",
                    "scrape",
                    {"planned_units": len(self.units)},
                    required=False,
                    idem=f"task-started:{self.owner_id}",
                )
            except Exception as exc:
                self.startup_error = exc

    def _record(
        self,
        event_type: str,
        stage: str,
        payload: dict,
        *,
        key: str | None = None,
        required: bool = True,
        idem: str | None = None,
        attempt: int = 1,
        severity: str = "info",
    ) -> None:
        if self.service is None or self.ref is None:
            return
        self.service.record(self.ref, {
            "idempotency_key": idem or f"{event_type}:{key or stage}:{attempt}",
            "event_type": event_type,
            "occurred_at": _now(),
            "stage": stage,
            "unit_kind": "keyword_city" if key else None,
            "unit_key": key,
            "attempt_no": attempt,
            "required_evidence": required,
            "severity": severity,
            "payload": payload,
        })

    def record_fact(self, fact: dict[str, Any]) -> None:
        self._record(
            str(fact.get("event_type") or ""),
            str(fact.get("stage") or "task"),
            dict(fact.get("payload") or {}),
            key=fact.get("unit_key"),
            required=bool(fact.get("required_evidence", False)),
            idem=fact.get("idempotency_key"),
            attempt=int(fact.get("attempt_no") or 1),
            severity=str(fact.get("severity") or "info"),
        )

    def unit_started(self, key: str, pages: int, start_page: int) -> None:
        key = str(key)
        previous = self.attempts.get(key, 1)
        existing = (
            [unit for unit in self.store.list_whitebox_units(self.ref.id) if str(unit.get("unit_key") or "") == key]
            if self.service is not None and self.ref is not None
            else []
        )
        attempt = (
            max(int(unit.get("attempt_no") or 1) for unit in existing) + 1
            if existing and any(str(unit.get("status") or "planned") != "planned" for unit in existing)
            else previous
        )
        self.attempts[key] = attempt
        self.units[key]["status"] = "running"
        self._record(
            "unit_started",
            "scrape_list",
            {"planned_pages": pages, "start_page": start_page},
            key=key,
            required=False,
            idem=f"unit-started:{key}:{attempt}",
            attempt=attempt,
        )

    def page(self, key: str, event: dict, target: int) -> None:
        key = str(key)
        attempt = self.attempts.get(key, max(1, int(event.get("attempt_no") or 1)))
        page = max(0, int(event.get("page") or 0))
        returned = event.get("returned_count", event.get("jobs_delta", event.get("jobs_count", 0)))
        unique = event.get("new_unique_count", event.get("jobs_delta", 0))
        self._record(
            "page_completed",
            "scrape_list",
            {
                "page": page,
                "planned_pages": target,
                "returned_count": max(0, int(returned or 0)),
                "new_unique_count": max(0, int(unique or 0)),
                "has_more": event.get("has_more"),
                "resume_page": int(event.get("resume_page") or page + 1),
                "scope_complete": event.get("scope_complete"),
                "source_exhausted": event.get("source_exhausted"),
                "stop_reason": event.get("stop_reason"),
            },
            key=key,
            idem=f"page:{key}:{attempt}:{page}",
            attempt=attempt,
        )

    def skip(self, key: str, *, reason: str = "unknown") -> None:
        key = str(key)
        completed = False
        if self.service is not None and self.ref is not None:
            completed = any(
                unit.get("unit_key") == key and unit.get("status") in {"succeeded", "empty"}
                for unit in self.store.list_whitebox_units(self.ref.id)
            )
        if not completed:
            attempt = self.attempts.get(key, 1)
            prior = (
                [unit for unit in self.store.list_whitebox_units(self.ref.id) if str(unit.get("unit_key") or "") == key]
                if self.service is not None and self.ref is not None
                else []
            )
            if prior and any(str(unit.get("status") or "planned") != "planned" for unit in prior):
                attempt = max(int(unit.get("attempt_no") or 1) for unit in prior) + 1
            self.attempts[key] = attempt
            self._record(
                "unit_skipped",
                "scrape_list",
                {"stop_reason": reason, "reason": "恢复时缺少完成证据"},
                key=key,
                idem=f"skip:{key}:{attempt}",
                attempt=attempt,
            )
            self.units[key].update(status="skipped", evidence_complete=False, error_code="resume_evidence_missing")

    def failed(self, key: str, outcome: Any, *, skipped: bool = False, reason: str = "") -> None:
        key = str(key)
        attempt = self.attempts.get(key, 1)
        code = str(getattr(outcome, "failed_code", None) or "source_unknown_error")
        reason = reason or str(getattr(outcome, "failed_reason", "") or "") or code
        event = "unit_skipped" if skipped else "unit_failed"
        self.units[key].update(
            status="skipped" if skipped else "failed",
            evidence_complete=False,
            error_code=code,
            error_reason=reason,
        )
        self._record(
            event,
            "scrape_list",
            {
                "error_code": code,
                "error_reason": reason,
                "reason": reason,
                "stop_reason": "unknown" if skipped else "soft_failure",
            },
            key=key,
            severity="error" if not skipped else "warning",
            idem=f"{event}:{key}:{attempt}:{code}",
            attempt=attempt,
        )

    def completed(self, key: str, outcome: Any) -> None:
        key = str(key)
        attempt = self.attempts.get(key, 1)
        jobs = [job for job in getattr(outcome, "jobs", None) or [] if isinstance(job, dict)]
        ids = {str(job.get("platform_job_id") or job.get("job_id") or job.get("source_url") or "") for job in jobs}
        ids.discard("")
        self.run_ids.update(ids)
        scope = getattr(outcome, "scope_complete", None)
        empty = bool(getattr(outcome, "empty_result", False))
        page_rows = [
            item
            for item in (getattr(outcome, "page_evidence", None) or [])
            if isinstance(item, dict) and str(item.get("event_type") or item.get("kind") or "") == "page_completed"
        ]
        returned_total = sum(
            max(0, int(item.get("returned_count") or item.get("jobs_delta") or item.get("jobs_count") or 0))
            for item in page_rows
        )
        page_new_unique = [
            max(0, int(item.get("new_unique_count") or 0))
            for item in page_rows
            if "new_unique_count" in item
        ]
        page_unique = (
            sum(page_new_unique)
            if page_new_unique
            else max([max(0, int(item.get("unit_unique_count") or 0)) for item in page_rows] or [0])
        )
        if returned_total <= 0 and jobs:
            returned_total = len(jobs)
        unique_count = max(page_unique, len(ids))
        quality: dict[str, int] = dict(getattr(outcome, "quality_counts", {}) or {})
        for job in jobs:
            source = str(job.get("salary_source") or "")
            if source:
                quality[f"salary_source.{source}"] = quality.get(f"salary_source.{source}", 0) + 1
        self.units[key].update(
            status="empty" if empty else "succeeded",
            evidence_complete=bool(scope),
            scope_complete=scope,
            source_exhausted=getattr(outcome, "source_exhausted", None),
            returned_total_count=returned_total,
            unit_unique_count=unique_count,
            explicit_empty=empty,
            quality_counts=quality,
        )
        self._record(
            "scope_completed",
            "scrape_list",
            {
                "scope_complete": scope,
                "source_exhausted": getattr(outcome, "source_exhausted", None),
                "stop_reason": getattr(outcome, "stop_reason", None),
                "returned_total_count": returned_total,
                "unit_unique_count": unique_count,
                "quality_counts": quality,
            },
            key=key,
            idem=f"scope-completed:{key}:{attempt}",
            attempt=attempt,
        )
        if empty:
            self._record(
                "explicit_empty",
                "scrape_list",
                {"empty_evidence": getattr(outcome, "empty_evidence", {})},
                key=key,
                idem=f"explicit-empty:{key}:{attempt}",
                attempt=attempt,
            )

    def incomplete(self, key: str, reason: str, code: str = "persistence_failed") -> None:
        key = str(key)
        attempt = self.attempts.get(key, 1)
        self.units[key].update(status="incomplete", evidence_complete=False, error_code=code, error_reason=reason)
        self._record(
            "unit_incomplete",
            "scrape_list",
            {"stop_reason": code, "reason": reason},
            key=key,
            severity="error",
            idem=f"incomplete:{key}:{attempt}:{code}",
            attempt=attempt,
        )

    def finish(self, payload: dict, *, lifecycle_end: str | None = None) -> dict:
        try:
            if self.service is not None:
                self.service._run_unique_count = len(self.run_ids)
            integrity = (
                self.service.finalize(self.ref, lifecycle_end=lifecycle_end)
                if self.service is not None and self.ref is not None
                else reduce_conclusion(
                    self.plan,
                    list(self.units.values()),
                    lifecycle_end=lifecycle_end,
                    run_unique_count=len(payload.get("jobs") or []),
                )
            )
        except WhiteboxWriteError as exc:
            integrity = {
                "conclusion": "unverifiable",
                "label": "无法确认",
                "evidence_complete": False,
                "primary_code": "whitebox_incomplete",
                "primary_reason": "任务证据白箱写入失败",
            }
            payload.update(
                hard_stop=True,
                hard_stop_code="internal_error",
                error=f"{payload.get('error') or '任务证据不足'}；白箱写入失败（{type(exc).__name__}）",
            )
        payload["integrity"] = integrity
        payload["ok"] = integrity.get("conclusion") in {"succeeded", "empty"}
        return payload
