"""Pure validation, normalization, and explainable job matching helpers."""

from __future__ import annotations

import re
from copy import deepcopy

from scripts import boss_cdp_raw as boss


FILTER_MAPS = {
    "scale": boss.SCALE_MAP,
    "stage": boss.STAGE_MAP,
    "salary": boss.SALARY_MAP,
    "experience": boss.EXPERIENCE_MAP,
    "degree": boss.DEGREE_MAP,
    "industry": boss.INDUSTRY_MAP,
}


def build_filter_options():
    return {
        name: [{"label": "不限", "value": ""}]
        + [{"label": label, "value": value} for label, value in mapping.items() if value != "0"]
        for name, mapping in FILTER_MAPS.items()
    }


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def validate_search_params(raw):
    raw = raw or {}
    keyword = str(raw.get("keyword") or "").strip()
    if not keyword:
        raise ValueError("keyword 不能为空")
    if len(keyword) > 80:
        raise ValueError("keyword 不能超过 80 个字符")

    city = str(raw.get("city") or boss.DEFAULT_CITY_INPUT).strip()
    try:
        pages = int(raw.get("pages", 3))
    except (TypeError, ValueError):
        raise ValueError("pages 必须是整数") from None
    if not 1 <= pages <= boss.MAX_PAGES:
        raise ValueError(f"pages 必须在 1-{boss.MAX_PAGES} 之间")

    output_format = str(raw.get("format") or "json").lower()
    if output_format not in {"json", "csv"}:
        raise ValueError("format 必须是 json 或 csv")

    filters = {}
    for name, mapping in FILTER_MAPS.items():
        value = str(raw.get(name) or "").strip()
        if not value:
            continue
        if value not in set(mapping.values()):
            raise ValueError(f"{name} 包含未知筛选代码: {value}")
        filters[name] = value

    return {
        "keyword": keyword,
        "city": city,
        "pages": pages,
        "detail": _as_bool(raw.get("detail"), True),
        "analysis": _as_bool(raw.get("analysis"), True),
        "format": output_format,
        "filters": filters,
    }


def _split_values(value):
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = re.split(r"[,，;；|\n]", str(value or ""))
    result = []
    seen = set()
    for part in parts:
        text = str(part).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def normalize_profile(raw=None):
    raw = raw or {}
    try:
        min_salary = float(raw.get("min_salary") or 0)
    except (TypeError, ValueError):
        raise ValueError("min_salary 必须是数字") from None
    if min_salary < 0:
        raise ValueError("min_salary 不能小于 0")
    return {
        "target_titles": _split_values(raw.get("target_titles")),
        "must_skills": _split_values(raw.get("must_skills")),
        "nice_skills": _split_values(raw.get("nice_skills")),
        "exclude_keywords": _split_values(raw.get("exclude_keywords")),
        "blacklist_companies": _split_values(raw.get("blacklist_companies")),
        "districts": _split_values(raw.get("districts")),
        "min_salary": min_salary,
    }


def salary_monthly_bounds(raw):
    text = str(raw or "")
    monthly = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*K", text, re.I)
    if monthly:
        return float(monthly.group(1)), float(monthly.group(2))
    daily = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元/天", text)
    if daily:
        low = round(float(daily.group(1)) * 21.75 / 1000 + 1e-9, 2)
        high = round(float(daily.group(2)) * 21.75 / 1000 + 1e-9, 2)
        return low, high
    return None


def _contains(text, term):
    normalized = str(term or "").strip()
    if not normalized:
        return False
    if re.fullmatch(r"[A-Za-z0-9.+#-]+", normalized) and re.search(r"[A-Za-z]", normalized):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])"
        return re.search(pattern, str(text or ""), flags=re.IGNORECASE) is not None
    return normalized.lower() in str(text or "").lower()


def _job_text(job, detail):
    values = [
        job.get("title"), job.get("boss_name"), job.get("company"),
        job.get("skills"), job.get("job_labels"), job.get("tags"),
        detail.get("jd"), detail.get("skill_tags"),
    ]
    return " ".join(
        " ".join(map(str, value)) if isinstance(value, list) else str(value or "")
        for value in values
    )


def _matching_terms(text, terms):
    return [term for term in terms if _contains(text, term)]


def _score_job(job, detail, profile):
    text = _job_text(job, detail)
    title = str(job.get("title") or "")
    company = str(job.get("boss_name") or job.get("company") or "")
    location = str(job.get("location") or "")
    reasons = []
    risks = []
    eligible = True
    score = 0.0

    title_hits = _matching_terms(title, profile["target_titles"])
    if title_hits:
        score += 25
        reasons.append(f"岗位名称命中：{', '.join(title_hits)}")

    matched_must = _matching_terms(text, profile["must_skills"])
    missing = [term for term in profile["must_skills"] if term not in matched_must]
    if profile["must_skills"]:
        score += 35 * len(matched_must) / len(profile["must_skills"])
        if matched_must:
            reasons.append(f"必备技能命中 {len(matched_must)}/{len(profile['must_skills'])}")
        if missing:
            risks.append(f"缺少技能证据：{', '.join(missing)}")

    matched_nice = _matching_terms(text, profile["nice_skills"])
    if profile["nice_skills"]:
        score += 15 * len(matched_nice) / len(profile["nice_skills"])
        if matched_nice:
            reasons.append(f"加分技能：{', '.join(matched_nice)}")

    excluded = _matching_terms(text, profile["exclude_keywords"])
    if excluded:
        eligible = False
        risks.append(f"命中排除词：{', '.join(excluded)}")

    blacklisted = _matching_terms(company, profile["blacklist_companies"])
    if blacklisted:
        eligible = False
        risks.append(f"公司黑名单：{', '.join(blacklisted)}")

    if profile["districts"]:
        districts = _matching_terms(location, profile["districts"])
        if districts:
            score += 10
            reasons.append(f"地区符合：{', '.join(districts)}")
        else:
            eligible = False
            risks.append("地区不符合目标范围")

    if profile["min_salary"]:
        bounds = salary_monthly_bounds(job.get("salary"))
        if not bounds:
            eligible = False
            risks.append("薪资无法确认")
        elif bounds[1] < profile["min_salary"]:
            eligible = False
            risks.append(f"薪资上限低于 {profile['min_salary']:g}K")
        elif bounds[0] >= profile["min_salary"]:
            score += 15
            reasons.append("薪资下限符合预期")
        else:
            score += 8
            risks.append("薪资区间下限低于预期")

    matched_skills = []
    for term in matched_must + matched_nice:
        if term not in matched_skills:
            matched_skills.append(term)
    if not eligible:
        score = min(score, 39)
    return {
        "eligible": eligible,
        "match_score": round(min(score, 100)),
        "matched_skills": matched_skills,
        "missing_skills": missing,
        "match_reasons": reasons,
        "risk_flags": risks,
    }


def match_jobs(jobs, details=None, profile=None):
    profile = normalize_profile(profile)
    details_by_id = {
        str(item.get("job_id")): item
        for item in (details or [])
        if isinstance(item, dict) and item.get("job_id")
    }
    ranked = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        item = deepcopy(job)
        detail = details_by_id.get(str(item.get("job_id")), {})
        item["jd"] = detail.get("jd", "")
        item["detail_skills"] = detail.get("skill_tags", [])
        item.update(_score_job(item, detail, profile))
        ranked.append(item)
    ranked.sort(key=lambda item: (-int(item["eligible"]), -item["match_score"], str(item.get("title") or "")))
    return ranked
