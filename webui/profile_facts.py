"""画像事实（profile_facts）契约、校验与描述（B062）。

第三层「隐藏画像字段」的数据模型：简历分析阶段由 AI 按「字段填写说明书」
提取，精筛阶段拼进 match_jds 提示词。本模块只承担字段契约与宽松校验，
不持有任何提示词文本（提示词在 webui/ai_prompts.py / prompt_texts.py）。

字段语义（spec 015 US2，FR-005~FR-008 冻结，补充履历判断字段）：
- 客观字段（core_skills/projects/employment_history/education_history/
  degree/graduation_date/languages）：简历明确写出才填，
  无证据不填（未体现），不推断、不编造。
- degree_type：只区分 统招 / 非统招；默认「统招」，仅当简历明确出现
  非统招标志（自考/成考/函授/夜校等）才填「非统招」；专升本属统招范畴。
- 主观字段（job_type/week_off/overtime）：简历未体现时按「最大接受度」
  归一化（job_type=不限雇佣形式、week_off=单休、overtime=能够加班），
  使匹配放宽、不因假设误杀岗位；匹配页仍以 caveats 提示用户自行确认。

修改本契约需同步：
- webui/ai_prompts.py 的字段填写说明书（提示词侧枚举/默认值说明）
- tests/test_profile_facts.py（本模块聚焦测试）
"""

from __future__ import annotations

from datetime import date
import math
import re
from typing import Any

# job_type 四值枚举（与旧 _PROFILE_FACT_JOB_TYPES 完全一致）
PROFILE_FACT_JOB_TYPES: tuple[str, ...] = ("全职", "实习", "兼职", "未体现")

# degree_type 两值：默认统招；仅明确非统招标志填非统招
PROFILE_FACT_DEGREE_TYPES: tuple[str, ...] = ("统招", "非统招")
_NON_TONGZHAO_MARKERS: tuple[str, ...] = ("自考", "成考", "函授", "夜校")

# 主观偏好字段：简历未体现时的「最大接受度」默认值
DEFAULT_WEEK_OFF = "单休"
DEFAULT_OVERTIME = "能够加班"
DEFAULT_JOB_TYPE = "不限雇佣形式"

_MAX_LIST_ITEMS = 10
_MAX_HISTORY_ITEMS = 12
_MAX_FACT_TEXT = 240
_PROJECT_KEYS = ("name", "role", "stack", "summary")
_CURRENT_DATE_MARKERS = ("至今", "现在", "目前", "present", "current")


def normalize_job_type(value: Any) -> str | None:
    """job_type 归一化：合法四值原样返回，未体现/缺失返回 None。"""
    if isinstance(value, str) and value.strip() in PROFILE_FACT_JOB_TYPES:
        return value.strip()
    if value in (None, "", "未体现"):
        return None
    return None


def normalize_degree_type(value: Any) -> str:
    """degree_type 归一化：默认「统招」，仅明确非统招标志填「非统招」。

    专升本/先专后本属统招范畴，不构成非统招信号；毕业时间仅两年也不
    作为非统招依据（FR-006/FR-007 冻结语义，事实认定而非匹配兜底）。
    显式的「非统招」字样原样保留。
    """
    if isinstance(value, str):
        text = value.strip()
        if text == "非统招":
            return "非统招"
        if any(marker in text for marker in _NON_TONGZHAO_MARKERS):
            return "非统招"
    return "统招"


def normalize_week_off(value: Any) -> str | None:
    """week_off 归一化：简历明确给出的休息节奏原样返回，未体现返回 None。

    返回 None 表示「简历未体现」，由调用方按最大接受度（默认单休）对待；
    本模块不把默认值直接写进 facts，避免把假设冒充成事实。
    """
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text in {"未体现", "未知", "不详", "无"}:
            return None
        return text
    return None


def normalize_overtime(value: Any) -> str | None:
    """overtime 归一化：简历明确给出的加班意愿原样返回，未体现返回 None。"""
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text in {"未体现", "未知", "不详", "无"}:
            return None
        return text
    return None


def normalize_fact_date(value: Any) -> str | None:
    """Return a stable YYYY or YYYY-MM value for an explicit resume date."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", "", value.strip())
    if not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in _CURRENT_DATE_MARKERS):
        return "至今"
    text = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace(".", "-")
        .replace("/", "-")
    )
    parts = [part for part in text.split("-") if part]
    if not parts or not re.fullmatch(r"\d{4}", parts[0]):
        return None
    year = int(parts[0])
    if year < 1900 or year > 2200:
        return None
    if len(parts) == 1:
        return f"{year:04d}"
    if not re.fullmatch(r"\d{1,2}", parts[1]):
        return None
    month = int(parts[1])
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def _clean_fact_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    return text[:_MAX_FACT_TEXT] if text else None


def _normalize_confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
        return None
    return round(float(value), 2)


def _normalize_experience_years(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*年?\s*", value)
        if not match:
            return None
        number = float(match.group(1))
    else:
        return None
    if not math.isfinite(number) or not 0 <= number <= 80:
        return None
    return round(number, 1)


def _normalize_employment_history(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict] = []
    for raw in value[:_MAX_HISTORY_ITEMS]:
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("company", "role", "evidence"):
            text = _clean_fact_text(raw.get(key))
            if text:
                item[key] = text
        for key in ("start_date", "end_date"):
            normalized = normalize_fact_date(raw.get(key))
            if normalized:
                item[key] = normalized
        confidence = _normalize_confidence(raw.get("confidence"))
        if confidence is not None:
            item["confidence"] = confidence
        if raw.get("current") is True:
            item["current"] = True
        if item.get("company") or item.get("role"):
            cleaned.append(item)
    return cleaned


def _normalize_education_history(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict] = []
    for raw in value[:_MAX_HISTORY_ITEMS]:
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("school", "degree", "major", "evidence"):
            text = _clean_fact_text(raw.get(key))
            if text:
                item[key] = text
        graduation_date = normalize_fact_date(raw.get("graduation_date"))
        if graduation_date and graduation_date != "至今":
            item["graduation_date"] = graduation_date
        confidence = _normalize_confidence(raw.get("confidence"))
        if confidence is not None:
            item["confidence"] = confidence
        if item.get("school") or item.get("degree") or item.get("major"):
            cleaned.append(item)
    return cleaned


def _reference_month(value: Any) -> tuple[int, int]:
    if isinstance(value, date):
        return value.year, value.month
    normalized = normalize_fact_date(value)
    if normalized and normalized != "至今":
        parts = normalized.split("-")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 12
    today = date.today()
    return today.year, today.month


def _date_month(
    value: Any,
    reference: tuple[int, int],
    *,
    end_of_year: bool = False,
) -> tuple[int, int] | None:
    normalized = normalize_fact_date(value)
    if not normalized:
        return None
    if normalized == "至今":
        return reference
    parts = normalized.split("-")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else (12 if end_of_year else 1)


def calculate_experience_years(
    employment_history: Any,
    *,
    as_of: Any = None,
) -> float | None:
    """Merge explicit employment intervals and return total months as years.

    The end month is counted so ``2022-01`` through ``2023-12`` is two years.
    Missing or reversed dates are ignored rather than guessed.
    """
    if not isinstance(employment_history, list):
        return None
    reference = _reference_month(as_of)
    intervals: list[tuple[int, int]] = []
    for item in employment_history:
        if not isinstance(item, dict):
            continue
        start = _date_month(item.get("start_date"), reference)
        end = _date_month(item.get("end_date"), reference, end_of_year=True)
        if end is None and item.get("current") is True:
            end = reference
        if start is None or end is None:
            continue
        start_index = start[0] * 12 + start[1] - 1
        end_index = end[0] * 12 + end[1] - 1
        if end_index < start_index:
            continue
        intervals.append((start_index, end_index))
    if not intervals:
        return None
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    months = sum(end - start + 1 for start, end in merged)
    return round(months / 12, 1)


def derive_profile_facts(data: Any, *, as_of: Any = None) -> dict:
    """Add deterministic judgments derived from validated hidden facts.

    Resume text remains the evidence source.  The derived fields are limited
    to date arithmetic and stable projections, so the model cannot manufacture
    an experience total or graduation year independently of its evidence.
    """
    if not isinstance(data, dict):
        return {}
    facts = dict(data)
    history = facts.get("employment_history")
    if isinstance(history, list):
        companies: list[str] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            company = str(item.get("company") or "").strip()
            if company and company not in companies:
                companies.append(company)
        if companies:
            facts["companies"] = companies[:_MAX_LIST_ITEMS]
        calculated = None
        if not (as_of is None and facts.get("experience_years_source") == "calculated"):
            calculated = calculate_experience_years(history, as_of=as_of)
        if calculated is not None:
            facts["experience_years"] = calculated
            facts["experience_years_source"] = "calculated"
    if "experience_years" in facts and "experience_years_source" not in facts:
        facts["experience_years_source"] = "resume_explicit"

    graduation_date = normalize_fact_date(facts.get("graduation_date"))
    if graduation_date and graduation_date != "至今":
        facts["graduation_date"] = graduation_date
    else:
        graduation_dates = []
        for item in facts.get("education_history") or []:
            if not isinstance(item, dict):
                continue
            value = normalize_fact_date(item.get("graduation_date"))
            if value and value != "至今":
                graduation_dates.append(value)
        if graduation_dates:
            facts["graduation_date"] = max(
                graduation_dates,
                key=lambda value: tuple(int(part) for part in value.split("-")),
            )
    if facts.get("graduation_date"):
        facts["graduation_year"] = int(str(facts["graduation_date"])[:4])
    return facts


def validate_profile_facts(data: Any) -> dict:
    """宽松验证 AI 提取的画像事实：类型 + 长度，无效项丢弃不阻塞。

    契约字段：core_skills[] / projects[{name,role,stack,summary}] /
    employment_history[] / education_history[] / job_type(四值) /
    degree(str) / degree_type(统招|非统招) / graduation_date / experience_years /
    languages[] / week_off(str) / overtime(str) / work_pattern(str)。缺失字段不
    写入（调用方按「未体现」语义处理）；列表只保留非空字符串，超长截断。
    """
    if not isinstance(data, dict):
        return {}
    facts: dict = {}

    skills = data.get("core_skills")
    if isinstance(skills, list):
        cleaned = [str(s).strip() for s in skills
                   if isinstance(s, str) and s.strip()]
        if cleaned:
            facts["core_skills"] = cleaned[:_MAX_LIST_ITEMS]

    projects = data.get("projects")
    if isinstance(projects, list):
        cleaned = []
        for project in projects:
            if not isinstance(project, dict):
                continue
            item = {}
            for key in _PROJECT_KEYS:
                value = project.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value.strip()
            if item.get("name"):
                cleaned.append(item)
        if cleaned:
            facts["projects"] = cleaned[:_MAX_LIST_ITEMS]

    employment_history = _normalize_employment_history(data.get("employment_history"))
    if employment_history:
        facts["employment_history"] = employment_history

    education_history = _normalize_education_history(data.get("education_history"))
    if education_history:
        facts["education_history"] = education_history

    job_type = normalize_job_type(data.get("job_type"))
    if job_type is not None:
        facts["job_type"] = job_type

    degree = data.get("degree")
    if isinstance(degree, str) and degree.strip():
        facts["degree"] = degree.strip()

    # B062：degree_type 只接受 AI 显式输出的「统招/非统招」；未输出不注入，
    # 由描述层 flex_degree_type 按「默认统招」呈现（事实认定在 match 侧生效）。
    degree_type = normalize_degree_type(data.get("degree_type"))
    if data.get("degree_type") is not None and degree_type:
        facts["degree_type"] = degree_type

    languages = data.get("languages")
    if isinstance(languages, list):
        cleaned = [str(lang).strip() for lang in languages
                   if isinstance(lang, str) and lang.strip()]
        if cleaned:
            facts["languages"] = cleaned[:_MAX_LIST_ITEMS]

    week_off = normalize_week_off(data.get("week_off"))
    if week_off is not None:
        facts["week_off"] = week_off

    overtime = normalize_overtime(data.get("overtime"))
    if overtime is not None:
        facts["overtime"] = overtime

    for key in ("graduation_date", "work_pattern"):
        if key == "graduation_date":
            value = normalize_fact_date(data.get(key))
            if value and value != "至今":
                facts[key] = value
        else:
            value = _clean_fact_text(data.get(key))
            if value:
                facts[key] = value

    experience_years = _normalize_experience_years(data.get("experience_years"))
    if experience_years is not None:
        facts["experience_years"] = experience_years

    return facts


def flex_degree_type(facts: dict) -> str:
    """匹配侧使用的 degree_type：有值用值，未体现默认「统招」。
    B062：专升本属统招范畴，只有明确非统招才按非统招对待。
    """
    value = facts.get("degree_type")
    if isinstance(value, str) and value.strip():
        return value
    return "统招"


def flex_job_type(facts: dict) -> str:
    """匹配侧使用的 job_type：有明确约束值用值，未体现/不限按最大接受度（不限雇佣形式）。"""
    value = facts.get("job_type")
    if isinstance(value, str) and value.strip() in PROFILE_FACT_JOB_TYPES:
        if value.strip() == "未体现":
            return DEFAULT_JOB_TYPE
        return value
    return DEFAULT_JOB_TYPE


def flex_week_off(facts: dict) -> str:
    """匹配侧使用的 week_off：有值用值，未体现按最大接受度（单休）。"""
    value = facts.get("week_off")
    if isinstance(value, str) and value.strip():
        return value
    return DEFAULT_WEEK_OFF


def flex_overtime(facts: dict) -> str:
    """匹配侧使用的 overtime：有值用值，未体现按最大接受度（能够加班）。"""
    value = facts.get("overtime")
    if isinstance(value, str) and value.strip():
        return value
    return DEFAULT_OVERTIME


def _format_employment_label(item: dict) -> str:
    company = str(item.get("company") or "").strip()
    role = str(item.get("role") or "").strip()
    start = str(item.get("start_date") or "").strip()
    end = str(item.get("end_date") or "").strip()
    if not company and not role:
        return ""
    label = company or "公司未体现"
    if end == "至今":
        period = f"{start or '时间未体现'} 至今"
    else:
        period = f"{start or '时间未体现'} 至 {end or '时间未体现'}"
    if role:
        detail = role + (f"，{period}" if start or end else "")
        return f"{label}（{detail}）"
    return f"{label}（{period}）" if start or end else label


def build_profile_facts_description(profile_facts: Any) -> str:
    """把画像事实拼成精筛提示词里的【第三层】描述。

    与旧 _build_profile_facts_description 兼容：无画像事实时返回
    「（无画像事实，按未体现处理）」；有事实时按行列出明确事实。
    主观字段（job_type/week_off/overtime）未体现时按最大接受度展示
    （job_type=不限雇佣形式、week_off=单休、overtime=能够加班），
    并标注「默认」，使匹配侧可区分简历事实与宽松默认。
    """
    if not isinstance(profile_facts, dict) or not profile_facts:
        return "（无画像事实，按未体现处理）"
    profile_facts = derive_profile_facts(profile_facts)
    lines: list[str] = []

    skills = profile_facts.get("core_skills")
    if isinstance(skills, list) and skills:
        lines.append("核心技能：" + "、".join(str(s) for s in skills))

    projects = profile_facts.get("projects")
    if isinstance(projects, list) and projects:
        parts = []
        for project in projects[:3]:
            if not isinstance(project, dict):
                continue
            name = str(project.get("name") or "").strip()
            role = str(project.get("role") or "").strip()
            stack = str(project.get("stack") or "").strip()
            if not name:
                continue
            detail = name
            if role:
                detail += f"（{role}）"
            if stack:
                detail += f"，技术栈：{stack}"
            parts.append(detail)
        if parts:
            lines.append("项目/工作经历：" + "；".join(parts))

    employment_history = profile_facts.get("employment_history")
    if isinstance(employment_history, list):
        parts = [
            label for item in employment_history[:5]
            if isinstance(item, dict)
            for label in [_format_employment_label(item)]
            if label
        ]
        if parts:
            lines.append("任职经历：" + "；".join(parts))

    experience_years = profile_facts.get("experience_years")
    if isinstance(experience_years, (int, float)) and not isinstance(experience_years, bool):
        source = profile_facts.get("experience_years_source")
        suffix = "（根据任职时间计算）" if source == "calculated" else ""
        lines.append(f"累计工作经验：{float(experience_years):.1f}年{suffix}")

    education_history = profile_facts.get("education_history")
    if isinstance(education_history, list):
        parts = []
        for item in education_history[:5]:
            if not isinstance(item, dict):
                continue
            school = str(item.get("school") or "").strip()
            degree_value = str(item.get("degree") or "").strip()
            major = str(item.get("major") or "").strip()
            graduation = str(item.get("graduation_date") or "").strip()
            label = school or "学校未体现"
            details = [value for value in (degree_value, major) if value]
            if graduation:
                details.append(f"毕业{graduation}")
            if details:
                label += "（" + "，".join(details) + "）"
            parts.append(label)
        if parts:
            lines.append("教育经历：" + "；".join(parts))

    graduation_date = profile_facts.get("graduation_date")
    if isinstance(graduation_date, str) and graduation_date.strip():
        lines.append(f"毕业时间：{graduation_date.strip()}")

    job_type = profile_facts.get("job_type")
    if job_type:
        lines.append(f"求职类型：{job_type}")

    degree = profile_facts.get("degree")
    if degree:
        lines.append(f"学历层次：{degree}")
    # degree_type：AI 未显式输出时按「默认统招」呈现（B062 事实认定），
    # 让匹配侧知道 JD 写「统招本科」时可按统招对待、仍留 caveats 复核。
    if profile_facts.get("degree") or profile_facts.get("degree_type"):
        degree_type = flex_degree_type(profile_facts)
        suffix = (
            "（默认）"
            if degree_type == "统招" and not profile_facts.get("degree_type")
            else ""
        )
        lines.append(f"学历类型：{degree_type}{suffix}")

    languages = profile_facts.get("languages")
    if isinstance(languages, list) and languages:
        lines.append("语言能力：" + "、".join(str(l) for l in languages))

    # 主观字段：简历明确写过就按原文展示；未体现时按最大接受度展示并标注默认，
    # 让 AI 知道这是宽松放行值、不代表候选人真实偏好，岗位仍可进匹配 + caveats。
    week_off = normalize_week_off(profile_facts.get("week_off"))
    preference_facts = dict(profile_facts)
    if week_off is None:
        preference_facts.pop("week_off", None)
    else:
        preference_facts["week_off"] = week_off
    if week_off:
        lines.append(f"作息：{week_off}")
    else:
        lines.append(f"作息：{flex_week_off(preference_facts)}（默认）")

    overtime = normalize_overtime(profile_facts.get("overtime"))
    if overtime is None:
        preference_facts.pop("overtime", None)
    else:
        preference_facts["overtime"] = overtime
    if overtime:
        lines.append(f"加班态度：{overtime}")
    else:
        lines.append(f"加班态度：{flex_overtime(preference_facts)}（默认）")

    work_pattern = profile_facts.get("work_pattern")
    if isinstance(work_pattern, str) and work_pattern.strip():
        lines.append(f"工作制度：{work_pattern.strip()}")

    return "；".join(lines)
