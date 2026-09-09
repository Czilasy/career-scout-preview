"""求职画像摘要的固定五段归一化与事实补充。

简历分析返回的 ``profile_summary`` 是用户可编辑的当前意愿。这个模块只
负责把不可信的模型文本整理为固定五段展示格式，并将有证据的隐藏事实补
到缺失段落；已有的用户意愿始终保留为最高优先级。
"""

from __future__ import annotations

import re

from webui.profile_facts import derive_profile_facts


PROFILE_SUMMARY_SECTIONS = (
    (1, "求职方向"),
    (2, "核心能力"),
    (3, "工作与项目经历"),
    (4, "学历与基本条件"),
    (5, "岗位偏好与排除项"),
)
_PROFILE_SUMMARY_LINE_RE = re.compile(r"^\s*([1-5])\s*[.．、)]\s*(.*)$")


def normalize_profile_summary(summary: object) -> str:
    """Keep the user-facing profile summary in its fixed five-section format."""
    text = str(summary or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    labels = dict(PROFILE_SUMMARY_SECTIONS)
    sections: dict[int, list[str]] = {}
    unnumbered: list[str] = []
    current: int | None = None
    found_numbered = False
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = _PROFILE_SUMMARY_LINE_RE.match(line)
        if match:
            found_numbered = True
            current = int(match.group(1))
            body = match.group(2).strip()
            label = labels[current]
            for prefix in (f"{label}：", f"{label}:"):
                if body.startswith(prefix):
                    body = body[len(prefix):].strip()
                    break
            if body == label:
                body = ""
            sections.setdefault(current, [])
            if body:
                sections[current].append(body)
            continue
        if found_numbered and current is not None:
            sections.setdefault(current, []).append(line)
        else:
            unnumbered.append(line)

    if not found_numbered:
        sections[1] = [" ".join(unnumbered)]
    return "\n".join(
        f"{number}. {label}：{' '.join(sections.get(number, [])).strip() or '无'}"
        for number, label in PROFILE_SUMMARY_SECTIONS
    )


def enrich_profile_summary(summary: object, profile_facts: object) -> str:
    """Append reliable hidden judgments without rewriting user-facing intent."""
    normalized = normalize_profile_summary(summary)
    if not normalized or not isinstance(profile_facts, dict) or not profile_facts:
        return normalized
    facts = derive_profile_facts(profile_facts)
    lines = normalized.split("\n")
    labels = dict(PROFILE_SUMMARY_SECTIONS)

    def append_to(number: int, text: str, markers: tuple[str, ...]) -> None:
        index = number - 1
        if not text or not 0 <= index < len(lines):
            return
        current = lines[index]
        body = current.split("：", 1)[1] if "：" in current else current
        if any(marker in body for marker in markers):
            return
        body = text if not body.strip() or body.strip() == "无" else (
            f"{body.rstrip('；')}；{text}"
        )
        lines[index] = f"{number}. {labels[number]}：{body}"

    employment = facts.get("employment_history")
    companies: list[str] = []
    if isinstance(employment, list):
        for item in employment:
            if not isinstance(item, dict):
                continue
            company = str(item.get("company") or "").strip()
            if not company:
                continue
            start = str(item.get("start_date") or "").strip()
            end = str(item.get("end_date") or "").strip()
            period = f"（{start}至{end or '时间未体现'}）" if start else ""
            entry = f"{company}{period}"
            if entry not in companies:
                companies.append(entry)
    if companies:
        append_to(3, "简历事实：曾任职于" + "、".join(companies), ("简历事实：曾任职于", "公司"))

    years = facts.get("experience_years")
    if isinstance(years, (int, float)) and not isinstance(years, bool):
        append_to(3, f"系统计算：累计工作经验：{float(years):.1f}年", ("系统计算：累计工作经验", "累计工作经验", "年经验"))

    graduation_date = str(facts.get("graduation_date") or "").strip()
    if graduation_date:
        append_to(4, f"简历事实：毕业时间：{graduation_date}", ("简历事实：毕业时间", "毕业时间"))

    preferences: list[str] = []
    for key, label in (("week_off", "作息"), ("overtime", "加班"), ("work_pattern", "工作制度")):
        value = facts.get(key)
        if isinstance(value, str) and value.strip():
            preferences.append(f"{label}{value.strip()}")
    if preferences:
        append_to(5, "简历事实：" + "；".join(preferences), ("简历事实：", "作息", "加班", "工作制度", "996", "双休", "单休"))
    return "\n".join(lines)
