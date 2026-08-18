"""画像事实（profile_facts）契约、校验与描述（B062）。

第三层「隐藏画像字段」的数据模型：简历分析阶段由 AI 按「字段填写说明书」
提取，精筛阶段拼进 match_jds 提示词。本模块只承担字段契约与宽松校验，
不持有任何提示词文本（提示词在 webui/ai_prompts.py / prompt_texts.py）。

字段语义（spec 015 US2，FR-005~FR-008 冻结）：
- 客观字段（core_skills/projects/degree/languages）：简历明确写出才填，
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
_PROJECT_KEYS = ("name", "role", "stack", "summary")


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
        return value.strip()
    return None


def normalize_overtime(value: Any) -> str | None:
    """overtime 归一化：简历明确给出的加班意愿原样返回，未体现返回 None。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def validate_profile_facts(data: Any) -> dict:
    """宽松验证 AI 提取的画像事实：类型 + 长度，无效项丢弃不阻塞。

    契约字段：core_skills[] / projects[{name,role,stack,summary}] /
    job_type(四值) / degree(str) / degree_type(统招|非统招) /
    languages[] / week_off(str) / overtime(str)。缺失字段不写入（调用方按
    「未体现」语义处理）；列表只保留非空字符串，超长截断。
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

    job_type = profile_facts.get("job_type")
    if job_type:
        lines.append(f"求职类型：{job_type}")

    degree = profile_facts.get("degree")
    if degree:
        lines.append(f"学历层次：{degree}")
    # degree_type：AI 未显式输出时按「默认统招」呈现（B062 事实认定），
    # 让匹配侧知道 JD 写「统招本科」时可按统招对待、仍留 caveats 复核。
    if profile_facts.get("degree") or profile_facts.get("degree_type"):
        lines.append(f"学历类型：{flex_degree_type(profile_facts)}（默认）")

    languages = profile_facts.get("languages")
    if isinstance(languages, list) and languages:
        lines.append("语言能力：" + "、".join(str(l) for l in languages))

    # 主观字段：简历明确写过就按原文展示；未体现时按最大接受度展示并标注默认，
    # 让 AI 知道这是宽松放行值、不代表候选人真实偏好，岗位仍可进匹配 + caveats。
    week_off = profile_facts.get("week_off")
    if week_off:
        lines.append(f"作息：{week_off}")
    else:
        lines.append(f"作息：{flex_week_off(profile_facts)}（默认）")

    overtime = profile_facts.get("overtime")
    if overtime:
        lines.append(f"加班态度：{overtime}")
    else:
        lines.append(f"加班态度：{flex_overtime(profile_facts)}（默认）")

    return "；".join(lines)