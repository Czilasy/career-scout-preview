"""Workbench helpers: link validation, keywords, dedup, budget, cards, feedback.

This module is intentionally free of side effects — no database, no network.
It holds the pure validation, projection and aggregation logic that the
Flask layer and tests rely on.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse


# Only HTTPS and *.zhipin.com are accepted for job links.
ALLOWED_JOB_HOSTS = {"www.zhipin.com", "zhipin.com"}
# Maximum JD excerpt length shown on the fixed-height card.
JD_EXCERPT_LIMIT = 320
# Maximum number of search keywords per run.
MAX_KEYWORDS = 3
# Maximum total detail JDs per parent run.
MAX_DETAIL_BUDGET = 60


def normalize_job_link(raw: str) -> str:
    """Return a canonical HTTPS zhipin URL or ``""`` when unsafe.

    Strips query/fragment to avoid leaking tracking parameters, keeping only
    the scheme, host and path.  Anything that is not HTTPS on an expected
    BOSS domain is rejected as empty.
    """
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return ""
    if host not in ALLOWED_JOB_HOSTS and not host.endswith(".zhipin.com"):
        return ""
    # Re-emit only scheme + host + path, dropping query and fragment so the
    # frontend never opens a URL carrying attacker-controlled params.
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned)


def canonical_job_id(job: dict) -> str:
    """Derive a stable identity for a job, preferring the URL slug."""
    link = str(job.get("job_link") or "")
    match = re.search(r"/job_detail/([A-Za-z0-9_-]+)", link)
    if match:
        return match.group(1)
    return str(job.get("job_id") or "")


def select_keywords(*, manual_keywords, ai_keywords, confirmed_fields) -> list:
    """Pick up to 3 keywords, manual first, then AI suggestions.

    Raises ``ValueError`` when no city is available — the caller must ask
    the user to pick one instead of defaulting to a nationwide search.
    """
    manual = [str(k).strip() for k in (manual_keywords or []) if str(k).strip()]
    if manual:
        result = manual[:MAX_KEYWORDS]
    else:
        result = [str(k).strip() for k in (ai_keywords or []) if str(k).strip()][:MAX_KEYWORDS]
    # City is mandatory only when we actually have keywords to search.
    if result:
        city = str((confirmed_fields or {}).get("city") or "").strip()
        if not city:
            raise ValueError("缺少城市，请先选择城市再搜索")
    return result


def dedupe_jobs(jobs: list) -> list:
    """Remove duplicate jobs across queries by canonical identity."""
    seen = set()
    result = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        cid = canonical_job_id(job)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        result.append(job)
    return result


def allocate_detail_budget(query_count: int, total_budget: int = MAX_DETAIL_BUDGET) -> list:
    """Split the total detail budget evenly across queries, remainder to first."""
    count = max(1, int(query_count))
    total = int(total_budget)
    base = total // count
    remainder = total % count
    return [base + (1 if i < remainder else 0) for i in range(count)]


def _truncate(text: str, limit: int = JD_EXCERPT_LIMIT) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def project_card(job: dict, detail: dict | None, *, interest_state: str = "new") -> dict:
    """Project a job + detail into a frontend card with no AI internals.

    Accepts both the scraper's job dict (``job_link``) and the store's job
    row (``source_url`` / ``canonical_url``) so the card layer does not
    depend on a single source shape.
    """
    detail = detail or {}
    link = job.get("job_link") or job.get("source_url") or job.get("canonical_url") or ""
    jd_text = detail.get("jd") or job.get("jd") or ""
    return {
        "job_id": str(job.get("job_id") or job.get("id") or ""),
        "title": str(job.get("title") or "未命名岗位"),
        "company": str(job.get("boss_name") or job.get("company") or "公司未标注"),
        "salary": str(job.get("salary") or "薪资未标注"),
        "location": str(job.get("location") or "地点未标注"),
        "jd_excerpt": _truncate(jd_text),
        "canonical_url": normalize_job_link(link),
        "interest_state": interest_state,
    }


def aggregate_feedback_state(events: list) -> str:
    """Reduce feedback events to the current interest state for a profile+job."""
    effective = [
        e for e in (events or [])
        if isinstance(e, dict) and not e.get("revoked_at")
    ]
    if not effective:
        return "new"
    last = effective[-1]
    return last.get("action") or "new"


def merge_profile_fields(confirmed: dict, ai_suggestion: dict) -> dict:
    """Merge confirmed manual fields over AI suggestions.

    Manual fields always win; AI only fills gaps.  This is the single place
    that enforces FR-007 (人工条件永远优先).

    A key present in *confirmed* with an explicit empty value ("", []) is
    treated as a deliberate manual override that clears the AI suggestion for
    that key.  Only ``None`` (key not provided by the caller) is skipped.
    """
    confirmed = confirmed or {}
    ai = ai_suggestion or {}
    merged = dict(ai)
    for key, value in confirmed.items():
        if value is None:
            continue
        merged[key] = value
    return merged
