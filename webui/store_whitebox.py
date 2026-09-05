"""SQLite persistence mixin for the 033 V2 task evidence whitebox."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Any

from webui.logging_setup import redact
from webui.store_helpers import _now


_OWNER_KINDS = {"scrape", "screening", "recrawl", "workbench", "legacy_task", "tuning"}


def _stable_id(prefix: str, *parts: Any) -> str:
    """Build an opaque, deterministic whitebox row id.

    Whitebox rows already have a unique business identity (or an event
    idempotency key), so a deterministic opaque id avoids introducing a
    second source of randomness into callers and keeps retries safe.  It also
    means a caller temporarily patching ``uuid.uuid4`` cannot break the
    required evidence write path.
    """
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _json(value: Any, default: Any) -> str:
    try:
        return json.dumps(value if value is not None else default, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps(default, ensure_ascii=False, sort_keys=True)


def _decode(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return parsed


def _canonical_plan(plan: Any) -> str:
    return _json(plan if isinstance(plan, dict) else {}, {})


def _safe_payload(value: Any, *, key: str = "") -> Any:
    sensitive_names = {
        "key", "token", "secret", "password", "cookie", "authorization", "api_key",
        "apikey", "resume_text", "resume_body", "jd", "jd_body", "prompt", "raw_response",
        "model_response",
    }
    if isinstance(value, dict):
        return {
            str(name): ("[REDACTED]" if str(name).lower() in sensitive_names else _safe_payload(item, key=str(name)))
            for name, item in value.items()
            if str(name).lower() not in {"full_resume", "full_jd", "credential"}
        }
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item, key=key) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))[:4000]


class StoreWhiteboxMixin:
    """Data-access methods only; conclusion rules live in ``whitebox_rules``."""

    def create_whitebox_run(self, owner_kind: str, owner_id: str, plan: dict,
                            parent_owner_id: str | None = None) -> dict[str, Any]:
        owner_kind = str(owner_kind or "").strip()
        owner_id = str(owner_id or "").strip()
        if owner_kind not in _OWNER_KINDS or not owner_id:
            raise ValueError("invalid whitebox owner")
        if not isinstance(plan, dict) or not isinstance(plan.get("units"), list) or not plan.get("units"):
            raise ValueError("whitebox plan must contain units")
        plan_json = _canonical_plan(plan)
        now = _now()
        with self._connection() as conn:
            if hasattr(self, "_assert_recovery_writes_allowed"):
                self._assert_recovery_writes_allowed(conn)
            existing = conn.execute(
                "SELECT * FROM whitebox_runs WHERE owner_kind=? AND owner_id=?",
                (owner_kind, owner_id),
            ).fetchone()
            if existing is not None:
                if str(existing["plan_json"]) != plan_json:
                    raise ValueError("whitebox plan conflict")
                return dict(existing)
            run_id = _stable_id("wb", owner_kind, owner_id)
            conn.execute(
                "INSERT INTO whitebox_runs (id, owner_kind, owner_id, parent_owner_id, plan_json, "
                "planned_unit_count, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, owner_kind, owner_id, parent_owner_id, plan_json,
                 len(plan["units"]), now, now),
            )
            units = []
            for index, item in enumerate(plan["units"]):
                item = dict(item) if isinstance(item, dict) else {"unit_key": str(item)}
                key = str(item.get("unit_key") or item.get("key") or f"unit-{index + 1}")
                stage = str(item.get("stage") or (plan.get("stages") or ["task"])[0])
                kind = str(item.get("unit_kind") or item.get("kind") or "unit")
                units.append((
                    _stable_id("wbu", run_id, stage, kind, key, 1), run_id, stage, kind, key, 1,
                    item.get("planned_pages"), now,
                ))
            conn.executemany(
                "INSERT INTO whitebox_units (id, whitebox_run_id, stage, unit_kind, unit_key, "
                "attempt_no, planned_pages, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                units,
            )
            row = conn.execute("SELECT * FROM whitebox_runs WHERE id=?", (run_id,)).fetchone()
            return dict(row)

    def get_whitebox_run(self, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM whitebox_runs WHERE owner_kind=? AND owner_id=?",
                (str(owner_kind), str(owner_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_whitebox_run_by_id(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM whitebox_runs WHERE id=?", (str(run_id),)).fetchone()
        return dict(row) if row is not None else None

    def list_whitebox_units(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM whitebox_units WHERE whitebox_run_id=? "
                "ORDER BY unit_key, attempt_no, id", (str(run_id),)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["quality_counts"] = _decode(item.pop("quality_counts_json", "{}"), {})
            for name in ("scope_complete", "source_exhausted"):
                if item.get(name) is not None:
                    item[name] = bool(item[name])
            for name in ("degraded", "evidence_complete"):
                item[name] = bool(item.get(name))
            result.append(item)
        return result

    def upsert_whitebox_unit(self, run_id: str, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("whitebox unit must be an object")
        stage = str(values.get("stage") or "task")
        unit_kind = str(values.get("unit_kind") or "unit")
        unit_key = str(values.get("unit_key") or values.get("key") or "")
        attempt_no = max(1, int(values.get("attempt_no") or 1))
        if not unit_key:
            raise ValueError("whitebox unit_key is required")
        now = _now()
        allowed = {
            "recovered_from_unit_id", "planned_pages", "completed_pages", "last_completed_page",
            "scope_complete", "source_exhausted", "returned_total_count", "unit_unique_count",
            "stop_reason", "status", "degraded", "evidence_complete", "error_code", "error_reason",
            "started_at", "finished_at", "quality_counts",
        }
        with self._connection() as conn:
            if hasattr(self, "_assert_recovery_writes_allowed"):
                self._assert_recovery_writes_allowed(conn)
            row = conn.execute(
                "SELECT id FROM whitebox_units WHERE whitebox_run_id=? AND stage=? AND unit_kind=? "
                "AND unit_key=? AND attempt_no=?",
                (str(run_id), stage, unit_kind, unit_key, attempt_no),
            ).fetchone()
            quality = values.get("quality_counts")
            if isinstance(quality, str):
                quality = _decode(quality, {})
            quality_json = _json(quality if isinstance(quality, dict) else {}, {})
            params = {
                name: values[name] for name in allowed if name in values
            }
            if row is None:
                unit_id = str(values.get("id") or _stable_id("wbu", run_id, stage, unit_kind, unit_key, attempt_no))
                conn.execute(
                    "INSERT INTO whitebox_units (id, whitebox_run_id, stage, unit_kind, unit_key, attempt_no, "
                    "recovered_from_unit_id, planned_pages, completed_pages, last_completed_page, scope_complete, "
                    "source_exhausted, returned_total_count, unit_unique_count, stop_reason, status, degraded, "
                    "evidence_complete, quality_counts_json, error_code, error_reason, started_at, finished_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (unit_id, str(run_id), stage, unit_kind, unit_key, attempt_no,
                     params.get("recovered_from_unit_id"), params.get("planned_pages"),
                     int(params.get("completed_pages") or 0), params.get("last_completed_page"),
                     _nullable_bool(params.get("scope_complete")), _nullable_bool(params.get("source_exhausted")),
                     max(0, int(params.get("returned_total_count") or 0)), max(0, int(params.get("unit_unique_count") or 0)),
                     params.get("stop_reason"), params.get("status", "planned"), int(bool(params.get("degraded", False))),
                     int(bool(params.get("evidence_complete", False))), quality_json,
                     params.get("error_code"), params.get("error_reason"), params.get("started_at"),
                     params.get("finished_at"), now),
                )
            else:
                unit_id = str(row["id"])
                sets = []
                values_sql: list[Any] = []
                for name, value in params.items():
                    column = "quality_counts_json" if name == "quality_counts" else name
                    if name == "quality_counts":
                        value = quality_json
                    if name in {"scope_complete", "source_exhausted"}:
                        value = _nullable_bool(value)
                    sets.append(f"{column}=?")
                    values_sql.append(value)
                if sets:
                    sets.append("updated_at=?")
                    values_sql.append(now)
                    values_sql.append(unit_id)
                    conn.execute(f"UPDATE whitebox_units SET {', '.join(sets)} WHERE id=?", values_sql)
            updated = conn.execute("SELECT * FROM whitebox_units WHERE id=?", (unit_id,)).fetchone()
            return dict(updated)

    def _append_whitebox_event_conn(self, conn, run_id: str, fact: dict[str, Any], *, origin: str = "primary") -> dict[str, Any]:
        idem = str(fact.get("idempotency_key") or "").strip()
        event_type = str(fact.get("event_type") or "").strip()
        stage = str(fact.get("stage") or "").strip()
        if not idem or not event_type or not stage:
            raise ValueError("whitebox fact missing idempotency_key, event_type or stage")
        now = _now()
        payload = _safe_payload(fact.get("payload") or {})
        existing = conn.execute(
            "SELECT * FROM whitebox_events WHERE whitebox_run_id=? AND idempotency_key=?",
            (str(run_id), idem),
        ).fetchone()
        if existing is not None:
            result = dict(existing)
            result["_duplicate"] = True
            return result
        sequence = int(conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM whitebox_events WHERE whitebox_run_id=?",
            (str(run_id),),
        ).fetchone()[0])
        event_id = _stable_id("wbe", run_id, sequence, idem)
        conn.execute(
            "INSERT INTO whitebox_events (id, whitebox_run_id, sequence, idempotency_key, stage, unit_kind, "
            "unit_key, attempt_no, event_type, required_evidence, severity, payload_json, origin, occurred_at, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, str(run_id), sequence, idem, stage, fact.get("unit_kind"), fact.get("unit_key"),
             fact.get("attempt_no"), event_type, int(bool(fact.get("required_evidence", False))),
             str(fact.get("severity") or "info"), _json(payload, {}), str(origin or "primary"),
             str(fact.get("occurred_at") or now), now),
        )
        row = conn.execute("SELECT * FROM whitebox_events WHERE id=?", (event_id,)).fetchone()
        return dict(row)

    def append_whitebox_event(self, run_id: str, fact: dict[str, Any], *, origin: str = "primary") -> dict[str, Any]:
        if not isinstance(fact, dict):
            raise ValueError("whitebox fact must be an object")
        idem = str(fact.get("idempotency_key") or "").strip()
        event_type = str(fact.get("event_type") or "").strip()
        stage = str(fact.get("stage") or "").strip()
        if not idem or not event_type or not stage:
            raise ValueError("whitebox fact missing idempotency_key, event_type or stage")
        now = _now()
        payload = _safe_payload(fact.get("payload") or {})
        with self._connection() as conn:
            if hasattr(self, "_assert_recovery_writes_allowed"):
                self._assert_recovery_writes_allowed(conn)
            existing = conn.execute(
                "SELECT * FROM whitebox_events WHERE whitebox_run_id=? AND idempotency_key=?",
                (str(run_id), idem),
            ).fetchone()
            if existing is not None:
                result = dict(existing)
                result["_duplicate"] = True
                return result
            sequence = int(conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM whitebox_events WHERE whitebox_run_id=?",
                (str(run_id),),
            ).fetchone()[0])
            event_id = _stable_id("wbe", run_id, sequence, idem)
            conn.execute(
                "INSERT INTO whitebox_events (id, whitebox_run_id, sequence, idempotency_key, stage, unit_kind, "
                "unit_key, attempt_no, event_type, required_evidence, severity, payload_json, origin, occurred_at, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, str(run_id), sequence, idem, stage, fact.get("unit_kind"), fact.get("unit_key"),
                 fact.get("attempt_no"), event_type, int(bool(fact.get("required_evidence", False))),
                 str(fact.get("severity") or "info"), _json(payload, {}), str(origin or "primary"),
                 str(fact.get("occurred_at") or now), now),
            )
            row = conn.execute("SELECT * FROM whitebox_events WHERE id=?", (event_id,)).fetchone()
            return dict(row)

    def list_whitebox_events(self, run_id: str, *, after_sequence: int = 0,
                             limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM whitebox_events WHERE whitebox_run_id=? AND sequence>? ORDER BY sequence ASC"
        params: list[Any] = [str(run_id), max(0, int(after_sequence))]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, min(1000, int(limit))))
        with self._connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def update_whitebox_summary(self, run_id: str, conclusion: dict[str, Any], *, lifecycle_status: str = "terminal",
                                finalized_at: str | None = None) -> dict[str, Any]:
        summary = conclusion.get("summary") if isinstance(conclusion, dict) else {}
        summary = summary if isinstance(summary, dict) else {}
        now = _now()
        with self._connection() as conn:
            if hasattr(self, "_assert_recovery_writes_allowed"):
                self._assert_recovery_writes_allowed(conn)
            current_row = conn.execute("SELECT * FROM whitebox_runs WHERE id=?", (str(run_id),)).fetchone()
            current = dict(current_row) if current_row is not None else None
            if current is None:
                raise KeyError(run_id)
            desired = {
                "lifecycle_status": lifecycle_status,
                "conclusion": conclusion.get("conclusion"),
                "evidence_complete": int(bool(conclusion.get("evidence_complete"))),
                "degraded": int(bool(conclusion.get("degraded"))),
                "observed_unit_count": int(summary.get("observed_units", 0)),
                "completed_unit_count": int(summary.get("completed_units", 0)),
                "failed_unit_count": int(summary.get("failed_units", 0)),
                "unknown_unit_count": int(summary.get("unknown_units", 0)),
                "unit_output_sum": int(summary.get("unit_output_sum", 0)),
                "run_unique_count": int(summary.get("run_unique_count", 0)),
                "quality_counts_json": _json(summary.get("quality_counts") or {}, {}),
                "primary_code": conclusion.get("primary_code"),
                "primary_reason": conclusion.get("primary_reason"),
            }
            unchanged = all(current.get(name) == value for name, value in desired.items())
            if unchanged and current.get("finalized_at"):
                return current
            revision = int(current.get("revision") or 0) + 1
            conn.execute(
                "UPDATE whitebox_runs SET lifecycle_status=?, conclusion=?, evidence_complete=?, degraded=?, "
                "observed_unit_count=?, completed_unit_count=?, failed_unit_count=?, unknown_unit_count=?, "
                "unit_output_sum=?, run_unique_count=?, quality_counts_json=?, primary_code=?, primary_reason=?, "
                "revision=?, finalized_at=COALESCE(?, finalized_at), updated_at=? WHERE id=?",
                (desired["lifecycle_status"], desired["conclusion"], desired["evidence_complete"],
                 desired["degraded"], desired["observed_unit_count"], desired["completed_unit_count"],
                 desired["failed_unit_count"], desired["unknown_unit_count"], desired["unit_output_sum"],
                 desired["run_unique_count"], desired["quality_counts_json"], desired["primary_code"],
                 desired["primary_reason"], revision,
                 finalized_at or now, now, str(run_id)),
            )
            row = conn.execute("SELECT * FROM whitebox_runs WHERE id=?", (str(run_id),)).fetchone()
            return dict(row)

    def finalize_whitebox(self, run_id: str, conclusion: dict[str, Any], *, final_event: dict[str, Any],
                          lifecycle_status: str = "terminal") -> dict[str, Any]:
        summary = conclusion.get("summary") if isinstance(conclusion, dict) else {}
        summary = summary if isinstance(summary, dict) else {}
        now = _now()
        with self._connection() as conn:
            if hasattr(self, "_assert_recovery_writes_allowed"):
                self._assert_recovery_writes_allowed(conn)
            current_row = conn.execute("SELECT * FROM whitebox_runs WHERE id=?", (str(run_id),)).fetchone()
            if current_row is None:
                raise KeyError(run_id)
            current = dict(current_row)
            desired = {
                "lifecycle_status": lifecycle_status, "conclusion": conclusion.get("conclusion"),
                "evidence_complete": int(bool(conclusion.get("evidence_complete"))),
                "degraded": int(bool(conclusion.get("degraded"))),
                "observed_unit_count": int(summary.get("observed_units", 0)),
                "completed_unit_count": int(summary.get("completed_units", 0)),
                "failed_unit_count": int(summary.get("failed_units", 0)),
                "unknown_unit_count": int(summary.get("unknown_units", 0)),
                "unit_output_sum": int(summary.get("unit_output_sum", 0)),
                "run_unique_count": int(summary.get("run_unique_count", 0)),
                "quality_counts_json": _json(summary.get("quality_counts") or {}, {}),
                "primary_code": conclusion.get("primary_code"),
                "primary_reason": conclusion.get("primary_reason"),
            }
            if all(current.get(name) == value for name, value in desired.items()) and current.get("finalized_at"):
                return current
            revision = int(current.get("revision") or 0) + 1
            conn.execute(
                "UPDATE whitebox_runs SET lifecycle_status=?, conclusion=?, evidence_complete=?, degraded=?, "
                "observed_unit_count=?, completed_unit_count=?, failed_unit_count=?, unknown_unit_count=?, "
                "unit_output_sum=?, run_unique_count=?, quality_counts_json=?, primary_code=?, primary_reason=?, "
                "revision=?, finalized_at=?, updated_at=? WHERE id=?",
                (desired["lifecycle_status"], desired["conclusion"], desired["evidence_complete"],
                 desired["degraded"], desired["observed_unit_count"], desired["completed_unit_count"],
                 desired["failed_unit_count"], desired["unknown_unit_count"], desired["unit_output_sum"],
                 desired["run_unique_count"], desired["quality_counts_json"], desired["primary_code"],
                 desired["primary_reason"], revision, now, now, str(run_id)),
            )
            event = dict(final_event)
            payload = dict(event.get("payload") or {})
            payload["revision"] = revision
            event["payload"] = payload
            self._append_whitebox_event_conn(conn, run_id, event)
            row = conn.execute("SELECT * FROM whitebox_runs WHERE id=?", (str(run_id),)).fetchone()
            return dict(row)

    def mark_whitebox_incomplete(self, run_id: str, *, stage: str = "unknown", reason: str = "write_failed") -> dict[str, Any]:
        return self.append_whitebox_event(run_id, {
            "idempotency_key": f"whitebox-incomplete:{stage}:{reason}",
            "event_type": "whitebox_incomplete", "occurred_at": _now(), "stage": stage,
            "required_evidence": True, "severity": "error", "payload": {"reason": reason},
        })

    @staticmethod
    def append_whitebox_emergency(path: os.PathLike[str] | str, record: dict[str, Any]) -> bool:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe = _safe_payload(record)
        idem = str(safe.get("idempotency_key") or "") if isinstance(safe, dict) else ""
        existing: set[str] = set()
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and parsed.get("idempotency_key"):
                        existing.add(str(parsed["idempotency_key"]))
            except (OSError, ValueError):
                pass
        if idem and idem in existing:
            return False
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")
        return True

    def import_whitebox_emergency(self, path: os.PathLike[str] | str, run_id: str, *,
                                  owner_kind: str | None = None, owner_id: str | None = None) -> int:
        path = Path(path)
        if not path.exists():
            return 0
        current = self.get_whitebox_run_by_id(run_id)
        resolved_owner_kind = str(owner_kind or (current or {}).get("owner_kind") or "")
        resolved_owner_id = str(owner_id or (current or {}).get("owner_id") or "")
        imported = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            record_run_id = str(record.get("run_id") or record.get("whitebox_run_id") or "")
            record_owner_id = str(record.get("owner_id") or "")
            record_owner_kind = str(record.get("owner_kind") or "")
            if record_run_id and record_run_id != str(run_id):
                continue
            if not record_run_id and record_owner_id:
                matches_run_owner = (
                    record_owner_id == str(run_id)
                    and (not record_owner_kind or record_owner_kind == resolved_owner_kind)
                )
                matches_business_owner = (
                    record_owner_kind == resolved_owner_kind
                    and record_owner_id == resolved_owner_id
                )
                if not (matches_run_owner or matches_business_owner):
                    continue
            if not record_run_id and not record_owner_id:
                continue
            event_type = str(record.get("event_type") or "whitebox_incomplete")
            idem = str(record.get("idempotency_key") or f"emergency:{hash(line)}")
            if any(str(event.get("idempotency_key") or "") == idem
                   for event in self.list_whitebox_events(run_id)):
                continue
            try:
                self.append_whitebox_event(run_id, {
                    "idempotency_key": idem,
                    "event_type": "emergency_record_imported",
                    "occurred_at": str(record.get("occurred_at") or _now()),
                    "stage": str(record.get("stage") or "unknown"),
                    "unit_kind": record.get("unit_kind"), "unit_key": record.get("unit_key"),
                    "attempt_no": record.get("attempt_no"), "required_evidence": True,
                    "severity": "error", "payload": {"original_event_type": event_type, "record": record},
                }, origin="emergency")
            except Exception:
                continue
            imported += 1
        return imported


def _nullable_bool(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"unknown", "null", ""}:
            return None
        if lowered in {"true", "1", "yes"}:
            return 1
        if lowered in {"false", "0", "no"}:
            return 0
    return int(bool(value))
