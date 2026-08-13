"""Workbench helpers: link validation, keywords, dedup, budget, cards, feedback.

This module is intentionally free of side effects — no database, no network.
It holds the pure validation, projection and aggregation logic that the
Flask layer and tests rely on.
"""

from __future__ import annotations

import re

from webui.url_safety import clean_https_url, is_safe_https_authority, parse_https_url


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

    保留为 BOSS 兼容入口；平台感知调用方应使用
    :func:`normalize_job_link_for_platform`。
    """
    parsed = parse_https_url(raw)
    if parsed is None:
        return ""
    if not is_safe_https_authority(
        parsed, allowed_hosts=ALLOWED_JOB_HOSTS, allow_subdomains=True
    ):
        return ""
    # Re-emit only scheme + host + path, dropping query and fragment so the
    # frontend never opens a URL carrying attacker-controlled params.
    return clean_https_url(parsed)


def normalize_job_link_for_platform(raw: str, *, platform: str | None = None) -> str:
    """按平台规则规范化岗位链接（T009 平台感知入口）。

    - ``platform=None`` 或 ``"boss"``：退化为 BOSS 兼容规则，与
      :func:`normalize_job_link` 完全一致，保证现有调用方行为不变。
    - ``platform="zhilian"``：委托 ``webui.platforms.normalize_job_url``，
      按 zhaopin.com / jobdetail/<id>.htm 规则归一化。
    - 未知平台：抛 ``ValueError``（由 ``webui.platforms.validate_platform_key``
      映射为 ``platform_validation_failed``），不静默回退 BOSS。

    智联平台未注册时（真实 fixture 未核验），抛
    ``PlatformNotRegisteredError``；调用方应在上层转为
    ``503 platform_schema_unavailable`` 或等价错误，不得改走 BOSS。
    """
    if platform is None or platform == "boss":
        return normalize_job_link(raw)
    # 延迟导入避免 workbench.py 在被 platforms.py 间接导入时形成循环。
    from webui.platforms import normalize_job_url as _platform_normalize
    return _platform_normalize(platform, raw)


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


def project_card(
    job: dict,
    detail: dict | None,
    *,
    interest_state: str = "new",
    platform: str | None = None,
) -> dict:
    """Project a job + detail into a frontend card with no AI internals.

    Accepts both the scraper's job dict (``job_link``) and the store's job
    row (``source_url`` / ``canonical_url``) so the card layer does not
    depend on a single source shape.

    ``platform`` 为可选平台感知参数：省略或 ``"boss"`` 时退化为 BOSS
    兼容链接归一化（保持现有调用方行为）；显式传入其它平台时按该平台
    规则归一化。岗位自身携带的 ``platform`` 字段优先于本参数，以便
    历史结果刷新时保持来源平台身份。
    """
    detail = detail or {}
    link = job.get("job_link") or job.get("source_url") or job.get("canonical_url") or ""
    jd_text = detail.get("jd") or job.get("jd") or ""
    # 岗位自身 platform 优先于调用方 platform 参数（FR-030/FR-012）。
    job_platform = str(job.get("platform") or "").strip() or platform
    if job_platform:
        canonical = normalize_job_link_for_platform(link, platform=job_platform)
    else:
        canonical = normalize_job_link(link)
    return {
        "job_id": str(job.get("job_id") or job.get("id") or ""),
        "title": str(job.get("title") or "未命名岗位"),
        "company": str(job.get("boss_name") or job.get("company") or "公司未标注"),
        "salary": str(job.get("salary") or "薪资未标注"),
        "location": str(job.get("location") or "地点未标注"),
        "jd_excerpt": _truncate(jd_text),
        "canonical_url": canonical,
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
