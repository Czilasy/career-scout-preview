"""提示词组装（B062：简历分析字段填写说明书 + 精筛去掉第四层默认偏好）。

职责：接收运行时上下文（平台选项、画像、画像事实、特征清单文本），
把 webui/prompt_texts.py 的纯文本常量拼成最终 system prompt。
不持有 AI 调用逻辑，不触碰校验/重试。
"""

from __future__ import annotations

from webui.prompt_texts import (
    MATCH_FLAGS_CONTRACT,
    MATCH_FLAGS_TRAILING,
    MATCH_OPENING,
    MATCH_OUTPUT_CONTRACT,
    MATCH_RULES,
    RESUME_FACTS_INSTRUCTIONS,
    RESUME_OPENING,
    RESUME_SUMMARY_INSTRUCTIONS,
)

# 简历分析：通用（不含字段选项，字段选项由调用方追加）
def build_resume_analysis_prompt(field_options: str) -> str:
    """组装简历分析 system prompt。

    ``field_options`` 由调用方按平台 schema 生成（keyword/city/过滤字段说明），
    插入静态文本之间；profile_facts 与 profile_summary 说明含 B062 新字段契约。
    """
    return (
        RESUME_OPENING
        + f"{field_options}\n\n"
        + "另外输出两部分：\n"
        + RESUME_FACTS_INSTRUCTIONS
        + RESUME_SUMMARY_INSTRUCTIONS
    )


# 精筛：match_jds 的 system prompt
def build_match_system_prompt(
    criteria_desc: str,
    profile_summary: str,
    facts_desc: str,
    features_prompt_text: str,
) -> str:
    """组装精筛 system prompt（B062 删除第四层默认偏好后版本）。

    - 第一层：已选六类硬条件（调用方注入）
    - 第二层：用户可编辑画像（调用方注入）
    - 第三层：隐藏画像字段（调用方注入，主观字段带「（默认）」标注时即最大接受度）
    - 判断规则：宽松化 — 主观偏好不自动判不匹配，六类硬条件/高危 flag 照常硬约束
    """
    return (
        MATCH_OPENING
        + f"【第一层·筛选条件】用户已选择的六类字段（薪资/经验/学历/规模/融资/行业），"
        f"最高优先级，绝对硬约束：{criteria_desc}\n"
        + f"【第二层·求职画像】候选人求职画像（用户可编辑，只能放宽未选择的维度，不能推翻已选字段）：{profile_summary}\n"
        + f"【第三层·隐藏画像字段】简历提取的客观事实与主观偏好（未列出的维度一律视为未体现，不得推断；"
        f"标注\"（默认）\"的是最大接受度放行值，非候选人真实偏好）：{facts_desc}\n\n"
        + MATCH_RULES
        + MATCH_OUTPUT_CONTRACT
        + MATCH_FLAGS_CONTRACT
        + f"{features_prompt_text}\n"
        + MATCH_FLAGS_TRAILING
    )