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


# ---------------------------------------------------------------------------
# T009: 平台校验、schema 投影、岗位身份校验公共函数
# ---------------------------------------------------------------------------


def validate_platform(platform: str) -> str:
    """校验平台键已知；返回规范化键，未知抛 ValueError。

    委托 ``webui.platforms.validate_platform_key``，为 core.py 调用方
    提供统一入口。
    """
    from webui.platforms import validate_platform_key
    try:
        return validate_platform_key(platform)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def project_platform_filter_schema(platform: str) -> dict:
    """投影平台 AI 筛选 schema 为 API 响应。

    委托 ``webui.platforms.project_filter_schema``，为 core.py 调用方
    提供统一入口。
    """
    from webui.platforms import project_filter_schema
    return project_filter_schema(platform)


def validate_job_identity(job: dict, *, platform: str) -> tuple[str, str]:
    """校验岗位身份：platform + platform_job_id。

    返回 ``(platform, platform_job_id)``。岗位缺失 platform_job_id 时，
    对 BOSS 兼容回退使用 job_id；其它平台必须显式提供 platform_job_id。

    岗位的 platform 字段（如有）必须与期望平台一致，否则抛 ValueError。
    """
    if not isinstance(job, dict):
        raise ValueError("job 必须为 dict")
    norm_platform = validate_platform(platform)
    job_platform = str(job.get("platform") or "").strip()
    if job_platform and job_platform != norm_platform:
        raise ValueError(
            f"岗位平台不匹配: 期望 {norm_platform}, 实际 {job_platform}"
        )
    platform_job_id = str(job.get("platform_job_id") or "").strip()
    if not platform_job_id and norm_platform == "boss":
        # BOSS 兼容回退：存量岗位使用 job_id 作为身份。
        platform_job_id = str(job.get("job_id") or job.get("encrypt_job_id") or "").strip()
    if not platform_job_id:
        raise ValueError(
            f"岗位缺少 platform_job_id（平台 {norm_platform}）"
        )
    return norm_platform, platform_job_id


# ---------------------------------------------------------------------------
# T010: 搜索请求拒绝非空 AI filters
# ---------------------------------------------------------------------------

# AI 筛选字段集合：搜索请求（/api/execute-search）不得携带这些字段。
# BOSS 旧码（salary/stage/experience/degree/industry/scale）+
# 智联 company_nature + AI run 才用的 screening_fields/filter_schema_version。
_AI_FILTER_KEYS: frozenset[str] = frozenset({
    "salary", "experience", "degree", "industry", "scale", "stage",
    "company_nature", "screening_fields", "filter_schema_version",
    # 顶层 filters 字段也应为空（contracts/http-api.md）。
    "filters",
})


class SearchFiltersNotSupportedError(ValueError):
    """搜索请求携带非空 AI filters 时抛出。

    对应 ``contracts/http-api.md`` 中 ``/api/execute-search`` 的
    ``422 search_filters_not_supported`` 错误。搜索请求只接收
    keyword/city/pages，AI 筛选必须留到后续 AI run 阶段。
    """

    ERROR_CODE = "search_filters_not_supported"


def _is_non_empty_filter_value(value) -> bool:
    """判断 filter 字段值是否为非空（字符串/列表/字典/数字）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    # 数字/布尔等标量视为非空
    return True


def validate_search_request(raw, *, platform: str | None = None):
    """校验搜索请求：只接收 keyword/city/pages，拒绝非空 AI filters。

    - 复用 :func:`validate_search_params` 的 keyword/city/pages/detail/
      analysis/format 基础校验，保证与现有行为一致。
    - 在基础校验前先检查 AI filter 字段：任何非空 AI filter 抛
      :class:`SearchFiltersNotSupportedError`（零副作用，不写 DB、不
      启动任务、不读 profile）。
    - 返回的 dict 中 ``filters`` 固定为 ``{}``，防止下游误用。

    ``platform`` 参数保留为后续 T011 平台感知校验的入口；当前仅做
    透传到基础校验（BOSS 兼容），不改变拒绝行为。
    """
    del platform  # 保留为 T011 入口；当前透传基础校验
    raw = raw or {}
    # 1. 拒绝非空 AI filters（零副作用，先于任何写入操作）。
    offending = []
    for key in _AI_FILTER_KEYS:
        if key in raw and _is_non_empty_filter_value(raw[key]):
            offending.append(key)
    if offending:
        raise SearchFiltersNotSupportedError(
            "搜索请求不允许携带非空 AI filters: " + ", ".join(sorted(offending))
        )
    # 2. 复用基础校验（keyword/city/pages/detail/analysis/format）。
    #    validate_search_params 仍会解析 filters 字段，但因为我们在第 1 步
    # 已经把非空 filters 拒绝了，这里 filters 只会是空或全空值，最终
    # 返回的 filters 为 {}。
    validated = validate_search_params(raw)
    # 3. 强制 filters 为空，防止下游误用。
    validated["filters"] = {}
    return validated


# ---------------------------------------------------------------------------
# T011: legacy 平台参数解析与零副作用拒绝助手
# ---------------------------------------------------------------------------

class LegacyPlatformNotSupportedError(ValueError):
    """legacy BOSS-only 入口收到显式 ``zhilian`` 平台时抛出。

    对应 ``contracts/http-api.md`` 中 ``422 legacy_platform_not_supported``。
    路由层应在任务/对象查找和任何副作用前捕获此异常并返回 422，
    保证数据库、事件、artifact、浏览器、profile 和注册表零变化。

    本异常本身是纯信号，不携带任何副作用。
    """

    ERROR_CODE = "legacy_platform_not_supported"


def parse_legacy_platform(raw):
    """解析 legacy 入口的平台参数（零副作用纯函数）。

    合同（contracts/http-api.md 第 353 行）：
    - 省略或 ``None`` → 返回 ``"boss"``（兼容既有 BOSS 行为）。
    - 显式 ``"boss"`` → 返回 ``"boss"``（兼容）。
    - 显式 ``"zhilian"`` → 抛 :class:`LegacyPlatformNotSupportedError`，
      路由层映射为 ``422 legacy_platform_not_supported``。
    - 其它值 → 抛 :class:`UnknownPlatformError`，路由层映射为
      ``400 platform_validation_failed``。

    本函数不读取 DB、不启动任务、不触碰浏览器/profile/注册表；
    零副作用保证由路由层在捕获异常后不执行任何后续操作实现。
    不得从 URL、任务标题或当前 UI 猜平台（FR-013/SC-012）。
    """
    from webui.platforms import UnknownPlatformError

    if raw is None:
        return "boss"
    if not isinstance(raw, str):
        raise UnknownPlatformError(
            f"platform 必须是字符串，实际类型: {type(raw).__name__}"
        )
    key = raw.strip().lower()
    if not key:
        # 省略平台（空字符串视为省略）→ 兼容 BOSS
        return "boss"
    if key == "boss":
        return "boss"
    if key == "zhilian":
        raise LegacyPlatformNotSupportedError(
            "legacy BOSS-only 入口不支持智联平台；"
            "请使用 /api/execute-search 等多平台入口"
        )
    # 其它已知/未知键统一拒绝，不静默回退 BOSS
    raise UnknownPlatformError(f"未知平台键: {raw}")


def legacy_platform_guard(raw):
    """legacy 入口平台参数守卫：返回 ``"boss"`` 或抛异常。

    等价于 :func:`parse_legacy_platform`，语义别名，供路由层显式表达
    "此处是 legacy 入口的门禁"。路由层应在任何副作用前调用：

        try:
            platform = legacy_platform_guard(body.get("platform"))
        except LegacyPlatformNotSupportedError:
            return jsonify({"ok": False,
                            "error_code": "legacy_platform_not_supported",
                            "error": "..."}), 422
        except UnknownPlatformError:
            return jsonify({"ok": False,
                            "error_code": "platform_validation_failed",
                            "error": "..."}), 400
    """
    return parse_legacy_platform(raw)
