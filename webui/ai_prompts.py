"""提示词组装（B062：简历分析字段填写说明书 + 精筛去掉第四层默认偏好）。

职责：接收运行时上下文（平台选项、画像、画像事实、特征清单文本），
把 webui/prompt_texts.py 的纯文本常量拼成最终 system prompt。
不持有 AI 调用逻辑，不触碰校验/重试。
B094：粗筛文本也在这里组装；两阶段共用同一套领域标签，但只有精筛拿到领域判定口径。
"""

from __future__ import annotations

from webui import ai_domain_policy as domain_policy
from webui.prompt_texts import (
    DOMAIN_CONSTRAINT_BLOCK,
    DOMAIN_MATCH_RULES,
    DOMAIN_ROUGH_NOTE_BLOCK,
    MATCH_FLAGS_CONTRACT,
    MATCH_FLAGS_TRAILING,
    MATCH_OPENING,
    MATCH_OUTPUT_CONTRACT,
    MATCH_RULES,
    RESUME_FACTS_INSTRUCTIONS,
    RESUME_OPENING,
    RESUME_SUMMARY_INSTRUCTIONS,
)


def build_domain_constraint(selection) -> str:
    """无领域选择时返回空串，保持既有输入与解析行为。"""
    if selection is None or not selection.has_selection:
        return ""
    return DOMAIN_CONSTRAINT_BLOCK.format(
        labels=domain_policy.prompt_labels_text(selection)
    ) + "\n"


def build_domain_rough_note(selection) -> str:
    """粗筛只防误排，不判领域；无领域选择时返回空串。"""
    if selection is None or not selection.has_selection:
        return ""
    return DOMAIN_ROUGH_NOTE_BLOCK.format(
        labels=domain_policy.prompt_labels_text(selection)
    ) + "\n"

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
    hard_fields_text: str | None = None,
    domain_text: str = "",
) -> str:
    """组装精筛 system prompt（B062 删除第四层默认偏好后版本）。

    - 第一层：已选硬条件（调用方注入）；行业按 B094 领域约束单独判断
    - 第二层：用户可编辑画像（调用方注入，代表当前意愿，优先于隐藏事实）
    - 第三层：隐藏画像字段（调用方注入，事实/计算结果/证据，不得覆盖画像）
    - 判断规则：宽松化 — 主观偏好不自动判不匹配，硬条件/高危 flag 照常硬约束
    """
    field_heading = (
        ("用户已选择的硬筛选字段（薪资/经验/学历/规模/融资）" if domain_text
         else "用户已选择的六类字段（薪资/经验/学历/规模/融资/行业）")
        if not hard_fields_text
        else f"用户已选择的硬筛选字段（{hard_fields_text}）" + ("，行业分类见下方领域条件" if domain_text else "")
    )
    return (
        MATCH_OPENING
        + f"【第一层·筛选条件】{field_heading}，"
        f"最高优先级，绝对硬约束：{criteria_desc}\n"
        + domain_text
        + f"【第二层·求职画像】候选人求职画像（用户可编辑，代表当前意愿；其中标记\"简历事实\"或\"系统计算\"的内容仅供核对，"
        f"不代表用户当前意愿；与第三层冲突时优先，"
        f"但不能推翻第一层已选字段）：{profile_summary}\n"
        + f"【第三层·隐藏画像字段】简历提取的客观事实、程序计算结果与证据（只作补充判断，不能覆盖第二层；"
        f"未列出的维度一律视为未体现，不得推断；"
        f"标注\"（默认）\"的是最大接受度放行值，非候选人真实偏好）：{facts_desc}\n\n"
        + (DOMAIN_MATCH_RULES if domain_text else MATCH_RULES)
        + MATCH_OUTPUT_CONTRACT
        + MATCH_FLAGS_CONTRACT
        + f"{features_prompt_text}\n"
        + MATCH_FLAGS_TRAILING
    )


def build_match_retry_messages(messages, *, criteria_desc=""):
    """只在既有单项无效回答重试中提醒已冻结的判断规则。"""
    correction = (
        "\n上次未获得有效判定，请重新核对第一层真实条件：空或不限不是已选区间，"
        "不能把画像期望当成已选薪资或经验区间；薪资已由程序核对，不再次据此拒绝。"
        "若 JD 真实硬要求、主业或技术栈冲突仍存在，按原规则拒绝并写真实依据；"
        "无冲突时才判匹配。保持原有输出格式。\n"
    )
    if criteria_desc:
        correction += (
            f"本轮第一层实际条件：{criteria_desc}\n"
            "第一层未列出的字段没有已选区间。画像和隐藏事实中的经验、学历或薪资"
            "不能冒充第一层选择；真实 JD 要求与候选人事实的冲突仍按原规则判断。\n"
        )
    return [{**messages[0], "content": messages[0]["content"] + correction}, *messages[1:]]


# 粗筛：screen_jobs 的 system prompt（B094 自 ai_screening 迁入，与精筛共用领域约束）
def build_screen_system_prompt(
    criteria_desc: str,
    hard_fields_text: str,
    input_note: str,
    domain_text: str = "",
) -> str:
    return (
        "你是求职初筛助手。只按候选人已确认的筛选字段，剔除【明显】不符的岗位。\n"
        f"{criteria_desc}\n\n"
        + domain_text
        + "判断规则（务必按常理，不要死板）：\n"
        "- 字段为空或未列出 = 不限，不得按该维度剔除；候选人画像只用于放宽，不能用来新增硬条件\n"
        "- 学历：已选学历为硬约束，岗位标签明确要求高于已选学历（如已选大专/本科而岗位硕士/博士）即剔除；未标学历保留\n"
        "- 求职类型：仅当岗位标题明确写'实习'且候选人画像明确写'全职'时，视为明显不符合；拿不准一律保留\n"
        "- 城市不判断（抓取阶段已保证城市）\n"
        "- 薪资：筛选区间为硬规则，岗位薪资与已选区间无重叠（高于或低于）即排除；'元/天'的实习计价综合判断\n"
        "- 经验：已选经验为硬约束，岗位标签明确经验下界高于已选范围（如已选1-3年而岗位3-5年）即剔除；未标经验保留\n"
        f"- 已选择的筛选字段是硬约束：岗位标签明确列出的{hard_fields_text}与已选条件冲突时，必须剔除；未选择或岗位未标明的字段不剔除\n"
        "- 岗位名称或类别（如客服、讲师、销售、内容制作、运营等）不得单独作为剔除理由\n"
        "- 求职画像放宽：候选人画像中明确表达放宽的维度（如\"东莞、深圳都可以\"\"不限\"\"接受兼职\"等）以画像表述为准放宽对应判断\n"
        "- 只排除【明显】不符合的；拿不准一律保留（宁可多留，不可错杀）\n\n"
        f"输入格式：每行一个岗位，``序号. 标题 | 薪资 | 城市 | 学历 | 规模``{input_note}。\n"
        "输出格式：只列出【要剔除】的岗位序号与理由，未列出的默认保留。严格输出JSON：\n"
        '{"dropped":[{"i":3,"reason":"经验5-10年>候选1-3年"},...]}\n'
        "i 为岗位序号。\n"
        "reason 必须具体，仅当字段已确认时使用「字段名+岗位值+比较符+候选人值」格式，禁止笼统表述。\n"
        "示例（仅当对应字段已确认时使用）：\n"
        '  经验已确认且岗位下界高于候选人上界：reason="经验5-10年>候选1-3年"\n'
        '  学历已确认且岗位要求高于候选人：reason="学历硕士>候选本科"\n'
        '  求职类型已确认且岗位为实习/全职冲突：reason="实习岗≠全职"\n'
        '  薪资已确认且岗位薪资明显低于期望：reason="薪资3-5K<期望8-10K"\n'
        "禁止使用「经验过高」「不符合」「不匹配」等笼统词汇。\n"
        "reason 限25字内。若无任何剔除，输出 {\"dropped\":[]}。"
    )
