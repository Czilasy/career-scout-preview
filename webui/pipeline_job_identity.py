"""Authoritative pipeline job identity resolution (spec 002 Task 003).

Every pipeline entrypoint (lifecycle actions, interest/reject feedback)
must identify a job through this module.  An identity is either:

1. the internal ``job_id`` (``jobs.id``); if the request additionally
   carries the authoritative triple, every field must match the stored
   row exactly, or the request is rejected; or
2. the complete authoritative triple ``platform + platform_job_id +
   canonical_url`` plus optional display fields; the canonical URL must
   validate against the declared platform before the dual-index upsert
   runs inside the caller's transaction.

BOSS and zhilian share the exact same protocol; ``platform`` only guards
URL/identity validation and never participates in reminders, lifecycle
or AI business rules.

Hard prohibitions enforced here (and mirrored by the tests):

- no guessing a missing ``platform`` from the URL host or from any UI
  "current platform" hint carried in the payload;
- no resolving identity by title, company, JD similarity or a bare
  ``platform_job_id``;
- ``platform_job_id`` is never treated as the internal ``job_id``;
- any identity failure raises before the caller performs association
  writes, so the caller transaction stays free of related side effects.

The dual-index upsert itself stays in the store (Task 001).  This module
only depends on the connection-aware helper protocol
``upsert_job_with_connection`` so it can be verified with fakes before
the real app assembly lands in Task 008.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from webui.platforms import (
    PlatformError,
    normalize_job_url,
    validate_platform_key,
)

__all__ = [
    "JobIdentityError",
    "JobIdentityIncompleteError",
    "PlatformUrlMismatchError",
    "JobIdentityConflictError",
    "JobNotFoundError",
    "JobDisplay",
    "JobIdentityRequest",
    "ResolvedJobIdentity",
    "ConnectionJobUpsertStore",
    "normalize_display_fields",
    "parse_identity_payload",
    "resolve_job_identity",
    "resolve_job_identity_then",
    "project_safe_job",
]


_TRIPLE_FIELDS = ("platform", "platform_job_id", "canonical_url")
_IDENTITY_FIELDS = ("job_id",) + _TRIPLE_FIELDS
_DISPLAY_TEXT_FIELDS = (
    "title", "company", "salary", "location", "jd", "experience", "degree",
)


# ---------------------------------------------------------------------------
# Domain errors (codes frozen by contracts/http-api.md)
# ---------------------------------------------------------------------------

class JobIdentityError(ValueError):
    """Base class for authoritative job identity failures."""

    code = "job_identity_error"
    http_status = 500

    def __init__(self, message: str | None = None, *, details: dict | None = None):
        self.details = details or {}
        super().__init__(message or self.code)


class JobIdentityIncompleteError(JobIdentityError):
    """422: the authoritative triple (or internal id) is not complete."""

    code = "job_identity_incomplete"
    http_status = 422


class PlatformUrlMismatchError(JobIdentityError):
    """422: canonical URL does not belong to the declared platform."""

    code = "platform_url_mismatch"
    http_status = 422


class JobIdentityConflictError(JobIdentityError):
    """409: dual-index conflict or internal id vs triple mismatch."""

    code = "job_identity_conflict"
    http_status = 409


class JobNotFoundError(JobIdentityError):
    """404: the internal job id does not exist."""

    code = "job_not_found"
    http_status = 404


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JobDisplay:
    """Normalized display-only fields; never used for identity matching."""

    title: str = ""
    company: str = ""
    salary: str = ""
    location: str = ""
    jd: str = ""
    experience: str = ""
    degree: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobIdentityRequest:
    """Parsed identity payload: internal id and/or authoritative triple."""

    job_id: str | None = None
    platform: str | None = None
    platform_job_id: str | None = None
    canonical_url: str | None = None
    display: JobDisplay = field(default_factory=JobDisplay)

    def triple_present(self) -> bool:
        return any(
            getattr(self, key) not in (None, "") for key in _TRIPLE_FIELDS
        )


@dataclass(frozen=True)
class ResolvedJobIdentity:
    """Outcome of a successful resolution inside the caller transaction."""

    job_id: str
    platform: str
    platform_job_id: str | None
    canonical_url: str
    created: bool
    job: Mapping[str, Any] | None = None


class ConnectionJobUpsertStore(Protocol):
    """Store protocol: connection-aware dual-index upsert helper (Task 001)."""

    def upsert_job_with_connection(
        self, conn, *, platform: str, platform_job_id: str | None,
        canonical_url: str, title: str = "", company: str = "",
        salary: str = "", location: str = "", jd: str = "",
        experience: str = "", degree: str = "", extra: dict | None = None,
        _validated_url: bool = False,
    ) -> dict:
        ...


# ---------------------------------------------------------------------------
# Parsing and normalization
# ---------------------------------------------------------------------------

def _clean_text_or_none(value: Any, key: str, invalid: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        invalid.append(key)
        return None
    text = value.strip()
    return text or None


def normalize_display_fields(raw: Mapping[str, Any] | None) -> JobDisplay:
    """Normalize display fields to stripped strings; never used for matching."""
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise JobIdentityIncompleteError(
            "岗位身份载荷必须为对象", details={"invalid_fields": ["payload"]},
        )
    values: dict[str, str] = {}
    for key in _DISPLAY_TEXT_FIELDS:
        value = raw.get(key)
        if value is None:
            values[key] = ""
        else:
            values[key] = str(value).strip()
    extra = raw.get("extra")
    if extra is None:
        extra = {}
    if not isinstance(extra, Mapping):
        raise JobIdentityIncompleteError(
            "展示字段 extra 必须为对象", details={"invalid_fields": ["extra"]},
        )
    return JobDisplay(extra=dict(extra), **values)


def parse_identity_payload(payload: Any) -> JobIdentityRequest:
    """Parse an identity payload strictly; unknown keys are ignored.

    Unknown keys (e.g. ``ui_platform`` hints) are deliberately discarded:
    they must never fill a missing ``platform`` or bias resolution.
    """
    if not isinstance(payload, Mapping):
        raise JobIdentityIncompleteError(
            "岗位身份载荷必须为对象", details={"missing_fields": list(_TRIPLE_FIELDS)},
        )
    invalid: list[str] = []
    cleaned = {
        key: _clean_text_or_none(payload.get(key), key, invalid)
        for key in _IDENTITY_FIELDS
    }
    if invalid:
        raise JobIdentityIncompleteError(
            "岗位身份字段必须为字符串", details={"invalid_fields": invalid},
        )
    return JobIdentityRequest(
        job_id=cleaned["job_id"],
        platform=cleaned["platform"],
        platform_job_id=cleaned["platform_job_id"],
        canonical_url=cleaned["canonical_url"],
        display=normalize_display_fields(payload),
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_platform(platform: str) -> None:
    """Block unknown/unregistered platforms using frozen contract codes.

    ``PlatformError`` (unknown or unregistered platform) maps to
    ``platform_url_mismatch`` exactly like the frozen Task 001 behavior:
    no canonical URL can belong to a platform outside the enum.
    """
    try:
        validate_platform_key(platform)
        normalize_job_url(platform, "https://identity-check.invalid/")
    except PlatformError as exc:
        raise PlatformUrlMismatchError(
            f"声明平台不可用: {platform}", details={"platform": platform},
        ) from exc


def _normalize_canonical_url(platform: str, canonical_url: str) -> str:
    try:
        normalized = normalize_job_url(platform, canonical_url)
    except PlatformError as exc:
        raise PlatformUrlMismatchError(
            "岗位规范链接与平台不匹配",
            details={"platform": platform},
        ) from exc
    if not normalized:
        raise PlatformUrlMismatchError(
            "岗位规范链接与平台不匹配",
            details={"platform": platform, "canonical_url": canonical_url},
        )
    return normalized


def _raise_store_error(result: Mapping[str, Any]) -> None:
    error_code = result.get("error_code") or "job_identity_conflict"
    if error_code == PlatformUrlMismatchError.code:
        raise PlatformUrlMismatchError("岗位规范链接与平台不匹配")
    if error_code == JobIdentityIncompleteError.code:
        raise JobIdentityIncompleteError("岗位身份不完整")
    raise JobIdentityConflictError(
        "岗位身份冲突", details={"error_code": error_code},
    )


def _fetch_job_row(conn, job_id: str):
    return conn.execute(
        "SELECT * FROM jobs WHERE id=?", (job_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve_by_internal_id(conn, request: JobIdentityRequest) -> ResolvedJobIdentity:
    row = _fetch_job_row(conn, request.job_id)
    if row is None:
        # 裸 platform_job_id 传入此处同样只会得到 job_not_found：
        # 本模块绝不用平台 ID 反查内部岗位。
        raise JobNotFoundError(
            "岗位不存在", details={"job_id": request.job_id},
        )
    if request.triple_present():
        missing = [
            key for key in _TRIPLE_FIELDS
            if getattr(request, key) in (None, "")
        ]
        if missing:
            raise JobIdentityIncompleteError(
                "内部岗位 ID 附带的身份三元组不完整",
                details={"missing_fields": missing},
            )
        _validate_platform(request.platform)
        normalized_url = _normalize_canonical_url(
            request.platform, request.canonical_url,
        )
        stored_platform = str(row["platform"])
        try:
            stored_url = normalize_job_url(
                stored_platform, str(row["canonical_url"] or ""),
            )
        except PlatformError:
            stored_url = ""
        stored_identity = (stored_platform, row["platform_job_id"], stored_url)
        requested_identity = (
            request.platform, request.platform_job_id, normalized_url,
        )
        if stored_identity != requested_identity:
            raise JobIdentityConflictError(
                "岗位身份不一致",
                details={
                    "job_id": str(row["id"]),
                    "requested": list(requested_identity),
                },
            )
    return ResolvedJobIdentity(
        job_id=str(row["id"]),
        platform=str(row["platform"]),
        platform_job_id=row["platform_job_id"],
        canonical_url=str(row["canonical_url"] or ""),
        created=False,
        job=dict(row),
    )


def _resolve_by_triple(
    conn, store: ConnectionJobUpsertStore, request: JobIdentityRequest,
) -> ResolvedJobIdentity:
    missing = [
        key for key in _TRIPLE_FIELDS
        if getattr(request, key) in (None, "")
    ]
    if missing:
        raise JobIdentityIncompleteError(
            "岗位身份三元组不完整", details={"missing_fields": missing},
        )
    _validate_platform(request.platform)
    normalized_url = _normalize_canonical_url(
        request.platform, request.canonical_url,
    )
    existed = conn.execute(
        "SELECT 1 FROM jobs WHERE (platform=? AND platform_job_id=?) "
        "OR canonical_url=? LIMIT 1",
        (request.platform, request.platform_job_id, normalized_url),
    ).fetchone()
    display = request.display
    result = store.upsert_job_with_connection(
        conn,
        platform=request.platform,
        platform_job_id=request.platform_job_id,
        canonical_url=normalized_url,
        title=display.title,
        company=display.company,
        salary=display.salary,
        location=display.location,
        jd=display.jd,
        experience=display.experience,
        degree=display.degree,
        extra=dict(display.extra),
        _validated_url=True,
    )
    if not result.get("ok"):
        _raise_store_error(result)
    job_id = str(result["job_id"])
    row = _fetch_job_row(conn, job_id)
    if row is None:  # pragma: no cover - defensive, upsert just wrote it
        raise JobIdentityConflictError("岗位身份写入后无法读回")
    return ResolvedJobIdentity(
        job_id=job_id,
        platform=str(row["platform"]),
        platform_job_id=row["platform_job_id"],
        canonical_url=str(row["canonical_url"] or ""),
        created=existed is None,
        job=dict(row),
    )


def resolve_job_identity(
    conn, store: ConnectionJobUpsertStore, request: JobIdentityRequest,
) -> ResolvedJobIdentity:
    """Resolve the authoritative identity inside the caller transaction.

    ``conn`` is the caller-owned SQLite connection; every read/write here
    belongs to the caller's transaction.  Any identity failure raises a
    ``JobIdentityError`` subclass before association writes may happen.
    """
    if not isinstance(request, JobIdentityRequest):
        raise JobIdentityIncompleteError(
            "岗位身份载荷必须为对象", details={"missing_fields": list(_TRIPLE_FIELDS)},
        )
    if request.job_id not in (None, ""):
        return _resolve_by_internal_id(conn, request)
    return _resolve_by_triple(conn, store, request)


def resolve_job_identity_then(
    conn,
    store: ConnectionJobUpsertStore,
    payload: Mapping[str, Any] | JobIdentityRequest,
    action: Callable[[ResolvedJobIdentity], Any],
) -> Any:
    """Resolve first, then run ``action`` only when the identity is valid.

    On any identity failure the exception propagates before ``action``
    runs, guaranteeing zero association/event writes for blocked
    identities (the caller rolls back its own transaction).
    """
    request = (
        payload if isinstance(payload, JobIdentityRequest)
        else parse_identity_payload(payload)
    )
    resolved = resolve_job_identity(conn, store, request)
    return action(resolved)


# ---------------------------------------------------------------------------
# Safe projection
# ---------------------------------------------------------------------------

def project_safe_job(row: Mapping[str, Any], *, include_jd: bool = False) -> dict:
    """Project a jobs row into the safe API shape (no raw JD by default)."""
    platform = str(row["platform"])
    canonical = str(row["canonical_url"] or "")
    try:
        normalized = normalize_job_url(platform, canonical)
    except PlatformError:
        normalized = ""
    projected = {
        "job_id": str(row["id"]),
        "platform": platform,
        "platform_job_id": row["platform_job_id"],
        "canonical_url": normalized or None,
        "can_open": bool(normalized),
        "title": str(row["title"] or ""),
        "company": str(row["company"] or ""),
        "salary": str(row["salary"] or ""),
        "location": str(row["location"] or ""),
        "experience": str(row["experience"] or ""),
        "degree": str(row["degree"] or ""),
    }
    if include_jd:
        projected["jd"] = str(row["jd"] or "")
    return projected
