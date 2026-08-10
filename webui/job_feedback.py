"""Domain service for platform-neutral profile-job lifecycle actions.

The service owns command validation, UTC timestamp normalization, append-only
lifecycle events, idempotency receipts, and the dynamic reminder projection.
It deliberately does not call AI or infer a job identity from presentation data.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from webui.platforms import PlatformError, normalize_job_url


UTC = timezone.utc
REMINDER_THRESHOLD_HOURS = 720
REMINDER_THRESHOLD = timedelta(hours=REMINDER_THRESHOLD_HOURS)
ACTIONS = (
    "mark_read",
    "mark_applied",
    "correct_applied_at",
    "follow_up",
    "mark_stale",
    "restore_applied",
    "correct_status",
)
STATUSES = ("new", "interested", "read", "applied", "stale", "deleted")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


_MSG_APPLIED_AT_INVALID = "时间不可解析或缺少时区"
_MSG_PLATFORM_URL_MISMATCH = "岗位规范链接与平台不匹配"
_MSG_JOB_NOT_FOUND = "岗位不存在"


class JobFeedbackError(ValueError):
    """Stable domain error that can be mapped to the HTTP contract."""

    def __init__(self, code: str, message: str | None = None, *, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message or code)


@dataclass(frozen=True)
class _Identity:
    kind: str
    value: dict[str, str]


def _utc_now(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise JobFeedbackError("applied_at_invalid", "时间必须包含时区")
        return value.astimezone(UTC)
    return parse_rfc3339_utc(value)


def parse_rfc3339_utc(value: str) -> datetime:
    """Parse an explicit-offset RFC 3339 timestamp and return UTC."""
    if not isinstance(value, str) or not value.strip():
        raise JobFeedbackError("applied_at_invalid", _MSG_APPLIED_AT_INVALID)
    text = value.strip()
    if not _RFC3339_RE.fullmatch(text):
        raise JobFeedbackError("applied_at_invalid", _MSG_APPLIED_AT_INVALID)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise JobFeedbackError("applied_at_invalid", _MSG_APPLIED_AT_INVALID) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JobFeedbackError("applied_at_invalid", "时间必须包含时区")
    return parsed.astimezone(UTC)


def _safe_parse(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return parse_rfc3339_utc(str(value))
    except JobFeedbackError:
        return None


def _require_not_future(value: datetime, now: datetime) -> None:
    if value > now:
        raise JobFeedbackError("applied_at_in_future", "投递时间不能晚于当前时刻")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _insert_lifecycle_event(
    conn, *, profile_id: str, job_id: str, action: str,
    from_status: str | None, to_status: str | None,
    from_applied_at: str | None, to_applied_at: str | None,
    from_last_follow_up_at: str | None, to_last_follow_up_at: str | None,
    occurred_at: str,
) -> tuple[str, int]:
    event_id = _new_id()
    row = conn.execute(
        "INSERT INTO profile_job_events ("
        "id, profile_id, job_id, action, from_status, to_status, "
        "from_applied_at, to_applied_at, from_last_follow_up_at, "
        "to_last_follow_up_at, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING sequence",
        (event_id, profile_id, job_id, action, from_status, to_status,
         from_applied_at, to_applied_at, from_last_follow_up_at,
         to_last_follow_up_at, occurred_at),
    ).fetchone()
    return event_id, int(row["sequence"])


def _insert_command_receipt(
    conn, *, request_id: str, request_fingerprint: str, profile_id: str,
    job_id: str, action: str, changed: bool, event_id: str | None, created_at: str,
) -> None:
    conn.execute(
        "INSERT INTO profile_job_command_receipts (request_id, request_fingerprint, "
        "profile_id, job_id, action, changed, event_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, request_fingerprint, profile_id, job_id, action, int(changed),
         event_id, created_at),
    )


class JobFeedbackService:
    """Atomic lifecycle command and reminder projection service."""

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _check_action_payload(action: str, *, applied_at, target_status) -> None:
        if action not in ACTIONS:
            raise JobFeedbackError("invalid_action", "不支持的岗位操作")
        has_applied = applied_at is not None
        has_target = target_status is not None
        if action == "correct_applied_at" and not has_applied:
            raise JobFeedbackError("invalid_action_payload", "纠正投递时间必须提供 applied_at")
        if action == "correct_status" and not has_target:
            raise JobFeedbackError("invalid_action_payload", "纠正状态必须提供 target_status")
        if action == "correct_status" and target_status not in STATUSES:
            raise JobFeedbackError("invalid_action_payload", "目标状态无效")
        if action == "correct_status" and has_applied and target_status != "applied":
            raise JobFeedbackError("invalid_action_payload", "只有纠正为已投递时才能同时提供投递时间")
        if action not in ("mark_applied", "correct_applied_at", "correct_status") and (has_applied or has_target):
            raise JobFeedbackError("invalid_action_payload", "该操作不接受投递时间或目标状态字段")
        if action == "mark_applied" and has_target:
            raise JobFeedbackError("invalid_action_payload", "标记投递不接受 target_status")
        if action == "correct_applied_at" and has_target:
            raise JobFeedbackError("invalid_action_payload", "纠正投递时间不接受 target_status")

    @staticmethod
    def _canonical_identity(job: dict) -> _Identity:
        if not isinstance(job, dict):
            raise JobFeedbackError("job_identity_incomplete", "岗位身份不完整")
        job_id = job.get("job_id")
        triple_keys = ("platform", "platform_job_id", "canonical_url")
        triple_present = any(key in job and job.get(key) not in (None, "") for key in triple_keys)
        if job_id not in (None, ""):
            if triple_present:
                if not all(job.get(key) not in (None, "") for key in triple_keys):
                    raise JobFeedbackError("job_identity_incomplete", "岗位身份三元组不完整")
                platform = str(job["platform"])
                platform_job_id = str(job["platform_job_id"])
                try:
                    canonical_url = normalize_job_url(platform, str(job["canonical_url"]))
                except PlatformError as exc:
                    raise JobFeedbackError("platform_url_mismatch", _MSG_PLATFORM_URL_MISMATCH) from exc
                if not canonical_url:
                    raise JobFeedbackError("platform_url_mismatch", _MSG_PLATFORM_URL_MISMATCH)
                return _Identity("job_id_with_triple", {
                    "job_id": str(job_id),
                    "platform": platform,
                    "platform_job_id": platform_job_id,
                    "canonical_url": canonical_url,
                })
            return _Identity("job_id", {"job_id": str(job_id)})
        if not all(job.get(key) not in (None, "") for key in triple_keys):
            raise JobFeedbackError("job_identity_incomplete", "岗位身份三元组不完整")
        platform = str(job["platform"])
        try:
            canonical_url = normalize_job_url(platform, str(job["canonical_url"]))
        except PlatformError as exc:
            raise JobFeedbackError("platform_url_mismatch", _MSG_PLATFORM_URL_MISMATCH) from exc
        if not canonical_url:
            raise JobFeedbackError("platform_url_mismatch", _MSG_PLATFORM_URL_MISMATCH)
        return _Identity("triple", {
            "platform": platform,
            "platform_job_id": str(job["platform_job_id"]),
            "canonical_url": canonical_url,
        })

    @staticmethod
    def _fingerprint(profile_id: str, identity: _Identity, action: str, applied_at, target_status) -> str:
        normalized_applied = None
        if applied_at is not None:
            try:
                normalized_applied = parse_rfc3339_utc(str(applied_at)).isoformat()
            except JobFeedbackError:
                normalized_applied = str(applied_at)
        payload = {
            "profile_id": str(profile_id),
            "job_identity": identity.value,
            "action": action,
            "target_status": target_status,
            "applied_at": normalized_applied,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _profile_exists(self, conn, profile_id: str) -> None:
        if conn.execute("SELECT 1 FROM candidate_profiles WHERE id=?", (profile_id,)).fetchone() is None:
            raise JobFeedbackError("profile_not_found", "求职画像不存在")

    def _resolve_job(self, conn, identity: _Identity, job: dict) -> str:
        if identity.kind in ("job_id", "job_id_with_triple"):
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (identity.value["job_id"],)).fetchone()
            if row is None:
                raise JobFeedbackError("job_not_found", _MSG_JOB_NOT_FOUND)
            if identity.kind == "job_id_with_triple":
                stored_identity = (
                    str(row["platform"]), str(row["platform_job_id"]),
                    normalize_job_url(str(row["platform"]), str(row["canonical_url"] or "")),
                )
                requested_identity = (
                    identity.value["platform"], identity.value["platform_job_id"],
                    identity.value["canonical_url"],
                )
                if stored_identity != requested_identity:
                    raise JobFeedbackError("job_identity_conflict", "岗位身份不一致")
            return str(row["id"])

        result = self.store.upsert_job_with_connection(
            conn, platform=identity.value["platform"],
            platform_job_id=identity.value["platform_job_id"],
            canonical_url=identity.value["canonical_url"],
            title=str(job.get("title") or ""), company=str(job.get("company") or ""),
            salary=str(job.get("salary") or ""), location=str(job.get("location") or ""),
            jd=str(job.get("jd") or ""), experience=str(job.get("experience") or ""),
            degree=str(job.get("degree") or ""), extra=job.get("extra") or {},
            _validated_url=True,
        )
        if not result.get("ok"):
            raise JobFeedbackError(result.get("error_code") or "persistence_failed", "岗位身份无法可靠关联")
        return str(result["job_id"])

    @staticmethod
    def _ensure_profile_job(conn, profile_id: str, job_id: str, now: str) -> None:
        conn.execute(
            "INSERT INTO profile_jobs (profile_id, job_id, shown_at, status, applied_at, last_follow_up_at) "
            "VALUES (?, ?, ?, 'new', NULL, NULL) ON CONFLICT(profile_id, job_id) DO NOTHING",
            (profile_id, job_id, now),
        )

    @staticmethod
    def _read_profile_job(conn, profile_id: str, job_id: str):
        row = conn.execute(
            "SELECT * FROM profile_jobs WHERE profile_id=? AND job_id=?",
            (profile_id, job_id),
        ).fetchone()
        if row is None:
            raise JobFeedbackError("profile_job_not_found", "画像岗位关联不存在")
        return row

    @staticmethod
    def _revision(conn, profile_id: str, job_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS revision FROM profile_job_events WHERE profile_id=? AND job_id=?",
            (profile_id, job_id),
        ).fetchone()
        return int(row["revision"] or 0)

    def _state_from_row(self, conn, row, now: datetime) -> dict:
        applied = _safe_parse(row["applied_at"])
        follow = _safe_parse(row["last_follow_up_at"])
        baseline = None
        if row["status"] == "applied" and applied is not None:
            if row["last_follow_up_at"] not in (None, ""):
                baseline = follow
            else:
                baseline = applied
        eligible = bool(baseline is not None and now - baseline >= REMINDER_THRESHOLD)
        elapsed_seconds = int((now - baseline).total_seconds()) if baseline is not None else None
        reminder = {
            "eligible": eligible,
            "baseline_at": baseline.isoformat() if baseline else None,
            "elapsed_seconds": elapsed_seconds,
            "elapsed_days": int(elapsed_seconds // 86400) if elapsed_seconds is not None else None,
        }
        return {
            "profile_id": str(row["profile_id"]),
            "job_id": str(row["job_id"]),
            "status": row["status"],
            "applied_at": applied.isoformat() if applied else row["applied_at"],
            "last_follow_up_at": follow.isoformat() if follow else row["last_follow_up_at"],
            "revision": self._revision(conn, str(row["profile_id"]), str(row["job_id"])),
            "reminder": reminder,
        }

    def get_state(self, profile_id: str, job_id: str, *, now: datetime | str | None = None) -> dict:
        current_now = _utc_now(now)
        with self.store._connection() as conn:
            self._profile_exists(conn, str(profile_id))
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (str(job_id),)).fetchone() is None:
                raise JobFeedbackError("job_not_found", _MSG_JOB_NOT_FOUND)
            row = self._read_profile_job(conn, str(profile_id), str(job_id))
            return self._state_from_row(conn, row, current_now)

    def execute_action(
        self, *, request_id: str, profile_id: str, job: dict, action: str,
        applied_at: str | None = None, target_status: str | None = None,
        now: datetime | str | None = None,
    ) -> dict:
        if not request_id or not isinstance(request_id, str):
            raise JobFeedbackError("invalid_action_payload", "request_id 必须存在")
        self._check_action_payload(action, applied_at=applied_at, target_status=target_status)
        identity = self._canonical_identity(job)
        current_now = _utc_now(now)
        profile_id = str(profile_id)
        fingerprint = self._fingerprint(profile_id, identity, action, applied_at, target_status)

        with self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._profile_exists(conn, profile_id)
            receipt = conn.execute(
                "SELECT * FROM profile_job_command_receipts WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if receipt is not None:
                if receipt["request_fingerprint"] != fingerprint:
                    raise JobFeedbackError("idempotency_conflict", "request_id 已用于其他请求")
                current_row = self._read_profile_job(conn, profile_id, str(receipt["job_id"]))
                state = self._state_from_row(conn, current_row, current_now)
                event_sequence = None
                if receipt["event_id"]:
                    event = conn.execute(
                        "SELECT sequence FROM profile_job_events WHERE id=?",
                        (receipt["event_id"],),
                    ).fetchone()
                    event_sequence = int(event["sequence"]) if event else None
                return {
                    "replayed": True, "changed": bool(receipt["changed"]),
                    "event_id": receipt["event_id"], "event_sequence": event_sequence,
                    "state": state,
                }

            job_id = self._resolve_job(conn, identity, job)
            now_text = current_now.isoformat()
            self._ensure_profile_job(conn, profile_id, job_id, now_text)
            before = self._read_profile_job(conn, profile_id, job_id)
            old_status = before["status"]
            old_applied = before["applied_at"]
            old_follow = before["last_follow_up_at"]
            new_status, new_applied, new_follow = old_status, old_applied, old_follow
            explicit_applied = parse_rfc3339_utc(applied_at) if applied_at is not None else None
            if explicit_applied is not None:
                _require_not_future(explicit_applied, current_now)
                explicit_applied_text = explicit_applied.isoformat()
            else:
                explicit_applied_text = None

            existing_applied = _safe_parse(old_applied)
            existing_follow = _safe_parse(old_follow)
            if old_follow not in (None, "") and existing_follow is None:
                if action in ("mark_applied", "correct_applied_at", "correct_status"):
                    raise JobFeedbackError("applied_at_invalid", "已有跟进时间无效，无法安全修改投递事实")

            if action == "mark_read":
                new_status = "read"
            elif action == "mark_applied":
                new_status = "applied"
                if explicit_applied_text is not None:
                    new_applied = explicit_applied_text
                elif existing_applied is None:
                    new_applied = now_text
                if new_applied is None:
                    new_applied = now_text
            elif action == "correct_applied_at":
                if old_status not in ("applied", "stale"):
                    raise JobFeedbackError("state_precondition_failed", "当前状态不允许纠正投递时间")
                new_applied = explicit_applied_text
            elif action == "follow_up":
                if old_status != "applied" or existing_applied is None:
                    raise JobFeedbackError("state_precondition_failed", "只有已投递且有投递时间的岗位才能跟进")
                new_follow = now_text
            elif action == "mark_stale":
                new_status = "stale"
            elif action == "restore_applied":
                if old_status != "stale" or existing_applied is None:
                    raise JobFeedbackError("state_precondition_failed", "只有有投递事实的已荒废岗位才能恢复")
                new_status = "applied"
                new_follow = now_text
            elif action == "correct_status":
                if target_status not in STATUSES:
                    raise JobFeedbackError("invalid_action_payload", "目标状态无效")
                new_status = str(target_status)
                if new_status == "applied":
                    if explicit_applied_text is not None:
                        new_applied = explicit_applied_text
                    elif existing_applied is None:
                        raise JobFeedbackError("applied_at_required", "纠正为已投递必须提供真实投递时间")
                elif explicit_applied_text is not None:
                    new_applied = explicit_applied_text

            if new_applied is not None and new_follow not in (None, ""):
                follow_value = _safe_parse(new_follow)
                applied_value = _safe_parse(new_applied)
                if follow_value is not None and applied_value is not None and follow_value < applied_value:
                    raise JobFeedbackError("follow_up_before_application", "跟进时间不能早于投递时间")

            changed = (new_status != old_status or new_applied != old_applied or new_follow != old_follow)
            if action in ("follow_up", "restore_applied") and old_status in ("applied", "stale"):
                changed = True
            event_id = None
            event_sequence = None
            if changed:
                conn.execute(
                    "UPDATE profile_jobs SET status=?, applied_at=?, last_follow_up_at=? WHERE profile_id=? AND job_id=?",
                    (new_status, new_applied, new_follow, profile_id, job_id),
                )
                event_id, event_sequence = _insert_lifecycle_event(
                    conn, profile_id=profile_id, job_id=job_id, action=action,
                    from_status=old_status, to_status=new_status,
                    from_applied_at=old_applied, to_applied_at=new_applied,
                    from_last_follow_up_at=old_follow, to_last_follow_up_at=new_follow,
                    occurred_at=now_text,
                )
            _insert_command_receipt(
                conn, request_id=request_id, request_fingerprint=fingerprint,
                profile_id=profile_id, job_id=job_id, action=action,
                changed=changed, event_id=event_id, created_at=now_text,
            )
            state_row = self._read_profile_job(conn, profile_id, job_id)
            state = self._state_from_row(conn, state_row, current_now)
            return {
                "replayed": False, "changed": bool(changed),
                "event_id": event_id, "event_sequence": event_sequence,
                "state": state,
            }

    def list_events(self, profile_id: str, job_id: str, *, after_sequence: int = 0, limit: int = 100) -> list[dict]:
        if limit < 1 or limit > 200:
            raise JobFeedbackError("invalid_limit", "事件数量超出范围")
        with self.store._connection() as conn:
            self._profile_exists(conn, str(profile_id))
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (str(job_id),)).fetchone() is None:
                raise JobFeedbackError("job_not_found", _MSG_JOB_NOT_FOUND)
            rows = conn.execute(
                "SELECT sequence, id, action, from_status, to_status, from_applied_at, to_applied_at, "
                "from_last_follow_up_at, to_last_follow_up_at, occurred_at FROM profile_job_events "
                "WHERE profile_id=? AND job_id=? AND sequence>? ORDER BY sequence ASC LIMIT ?",
                (str(profile_id), str(job_id), int(after_sequence), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def _reminder_rows(self, conn, profile_id: str, now: datetime) -> list[dict]:
        rows = conn.execute(
            "SELECT pj.*, j.platform, j.platform_job_id, j.title, j.company, j.salary, j.location, j.canonical_url "
            "FROM profile_jobs pj JOIN jobs j ON j.id=pj.job_id "
            "WHERE pj.profile_id=? AND pj.status='applied' AND pj.applied_at IS NOT NULL",
            (profile_id,),
        ).fetchall()
        result = []
        for row in rows:
            applied = _safe_parse(row["applied_at"])
            if applied is None:
                continue
            if row["last_follow_up_at"] not in (None, ""):
                baseline = _safe_parse(row["last_follow_up_at"])
                if baseline is None:
                    continue
            else:
                baseline = applied
            elapsed = now - baseline
            if elapsed < REMINDER_THRESHOLD:
                continue
            elapsed_seconds = int(elapsed.total_seconds())
            try:
                normalized_url = normalize_job_url(
                    str(row["platform"]), str(row["canonical_url"] or "")
                )
            except PlatformError:
                normalized_url = ""
            result.append({
                "job_id": str(row["job_id"]),
                "platform": row["platform"],
                "platform_job_id": row["platform_job_id"],
                "title": row["title"],
                "company": row["company"],
                "salary": row["salary"],
                "location": row["location"],
                "canonical_url": normalized_url or None,
                "status": row["status"],
                "applied_at": applied.isoformat(),
                "last_follow_up_at": _safe_parse(row["last_follow_up_at"]).isoformat() if row["last_follow_up_at"] not in (None, "") and _safe_parse(row["last_follow_up_at"]) else row["last_follow_up_at"],
                "baseline_at": baseline.isoformat(),
                "elapsed_seconds": elapsed_seconds,
                "elapsed_days": int(elapsed_seconds // 86400),
                "can_open": bool(normalized_url),
            })
        result.sort(key=lambda item: (item["baseline_at"], item["job_id"]))
        return result

    def list_reminders(self, profile_id: str, *, now: datetime | str | None = None, limit: int = 100) -> dict:
        if limit < 1 or limit > 100:
            raise JobFeedbackError("invalid_limit", "提醒列表最多返回 100 条")
        current_now = _utc_now(now)
        with self.store._connection() as conn:
            self._profile_exists(conn, str(profile_id))
            items = self._reminder_rows(conn, str(profile_id), current_now)
        return {"profile_id": str(profile_id), "threshold_hours": REMINDER_THRESHOLD_HOURS, "total": len(items), "items": items[:limit]}

    def count_reminders(self, profile_id: str, *, now: datetime | str | None = None) -> int:
        return int(self.list_reminders(profile_id, now=now, limit=100)["total"])
