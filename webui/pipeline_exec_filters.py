"""搜索参数展开与本地岗位过滤匹配（021 B7 自 pipeline_exec.py 搬运）。"""

from __future__ import annotations

import re

from scripts import boss_cdp_raw as boss




# ---------------------------------------------------------------------------
# Search combination expansion
# ---------------------------------------------------------------------------

def split_keywords(keyword: str) -> list[str]:
    """Split a keyword string on Chinese/English commas into distinct terms."""
    if not keyword:
        return []
    parts = str(keyword).replace("，", ",").split(",")
    return [p.strip() for p in parts if p.strip()]




def expand_combinations(params: dict) -> list[dict]:
    """Expand confirmed params into a list of single keyword×city searches.

    ``params`` has ``keyword`` (comma string), ``city`` (list) and
    ``filters`` (dict of lists).  Returns one entry per (keyword, city)
    pair, each carrying the full multi-select ``filters`` for post-filtering.
    """
    if params.get("locations"):
        from webui.location_scope import expand_location_combinations as _expand_locations
        return _expand_locations(params)
    keywords = split_keywords(params.get("keyword", ""))
    cities = params.get("city") or []
    if isinstance(cities, str):
        cities = [c.strip() for c in cities.replace("，", ",").split(",") if c.strip()]
    filters = params.get("filters") or {}
    combos = []
    for kw in keywords:
        for city in cities:
            combos.append({"keyword": kw, "city": city, "filters": filters})
    return combos




# ---------------------------------------------------------------------------
# Local post-filter: match a job against multi-select filter codes
# ---------------------------------------------------------------------------

def _job_scale_code(job: dict) -> str:
    return boss.SCALE_MAP.get((job.get("company_scale") or "").strip(), "")




def _job_stage_code(job: dict) -> str:
    return boss.STAGE_MAP.get((job.get("company_stage") or "").strip(), "")




def _job_industry_code(job: dict) -> str:
    industry = (job.get("company_industry") or "").strip()
    if industry in boss.INDUSTRY_MAP:
        return boss.INDUSTRY_MAP[industry]
    # Industry strings may be longer ("互联网 · 电商"); try prefix match.
    for name, code in boss.INDUSTRY_MAP.items():
        if name and name in industry:
            return code
    return ""




def _job_exp_degree_codes(job: dict) -> tuple[str, str]:
    """Extract experience and degree codes from the ``tags`` field.

    The scraper joins ``jobExperience`` and ``jobDegree`` into ``tags`` as
    e.g. ``"1-3年 | 本科"``.
    """
    tags = job.get("tags") or ""
    parts = [p.strip() for p in tags.split("|")]
    exp = ""
    deg = ""
    for p in parts:
        if p in boss.EXPERIENCE_MAP:
            exp = boss.EXPERIENCE_MAP[p]
        if p in boss.DEGREE_MAP:
            deg = boss.DEGREE_MAP[p]
    return exp, deg




def _job_salary_code(job: dict) -> str:
    """Best-effort mapping of a plaintext salary string to a SALARY_MAP code.

    Returns "" when the salary is unparseable (e.g. "面议"); callers treat
    an empty code as "unknown" and keep the job rather than dropping it.
    """
    salary = job.get("salary") or ""
    # 1. Direct substring match against band labels ("10-20K·13薪" -> "10-20K").
    for label, code in boss.SALARY_MAP.items():
        if label != "不限" and label in salary:
            return code
    # 2. Numeric fallback: use the lower bound of the first number found.
    nums = re.findall(r"\d+(?:\.\d+)?", salary)
    if not nums:
        return ""
    try:
        low = float(nums[0])
    except ValueError:
        return ""
    if low < 3:
        return "402"
    if low < 5:
        return "403"
    if low < 10:
        return "404"
    if low < 20:
        return "405"
    if low < 50:
        return "406"
    return "407"




def job_matches(job: dict, filters: dict) -> bool:
    """Return True iff *job* satisfies every selected multi-select filter.

    A filter dimension that the user left empty imposes no constraint.  A job
    whose value for a dimension is unknown/empty is kept (we avoid dropping
    jobs on missing data).
    """
    if not filters:
        return True

    scale_sel = filters.get("scale") or []
    if scale_sel:
        code = _job_scale_code(job)
        if code and code not in scale_sel:
            return False

    stage_sel = filters.get("stage") or []
    if stage_sel:
        code = _job_stage_code(job)
        if code and code not in stage_sel:
            return False

    industry_sel = filters.get("industry") or []
    if industry_sel:
        code = _job_industry_code(job)
        if code and code not in industry_sel:
            return False

    exp_sel = filters.get("experience") or []
    deg_sel = filters.get("degree") or []
    if exp_sel or deg_sel:
        exp_code, deg_code = _job_exp_degree_codes(job)
        if exp_sel and exp_code and exp_code not in exp_sel:
            return False
        if deg_sel and deg_code and deg_code not in deg_sel:
            return False

    salary_sel = filters.get("salary") or []
    if salary_sel:
        code = _job_salary_code(job)
        if code and code not in salary_sel:
            return False

    return True
