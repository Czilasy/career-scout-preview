"""岗位靠谱判定特征清单与分级判定（B033）。

精筛 prompt 只引用本模块的常量清单；后续新增/调整骗局特征只改这里，
不动 ai.py 的 prompt 结构与输出契约。

分级规则（spec 009 用户故事 3）：
- 命中高危（high）≥1 条 → 输出 flags，岗位强制 not_match；
- 命中中危（medium）≥2 条 → 输出 flags，只标记不改判定；
- 仅命中 1 条中危 → 降级为 caveats 文本（不输出 flags）。

\"岗位常年挂着\"类需要时间维度的特征不纳入（依赖在线巡检 B005）。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 特征清单（20 条）：code / level / 判定依据
# A 中介包装（medium）| B 销售话术（medium）| C 收费培训（high）
# D 薪资雇佣关系（high）| E 信息矛盾（high）| F 异常行为（混合）
# ---------------------------------------------------------------------------
FLAG_FEATURES: list[dict] = [
    # -- A 中介包装 --
    {"code": "A1", "level": "medium", "name": "劳务派遣/外包包装",
     "basis": "岗位由劳务公司代招、写\"派遣/外包\"但包装成正编岗位"},
    {"code": "A2", "level": "medium", "name": "中介代招",
     "basis": "招聘方为中介机构，JD 未写明实际用工企业"},
    {"code": "A3", "level": "medium", "name": "小公司大角色",
     "basis": "岗位头衔与公司规模/业务明显不匹配（如初创小公司招\"区域总监\"）"},
    {"code": "A4", "level": "medium", "name": "招聘与公司业务无关",
     "basis": "JD 岗位内容与公司主营业务毫无关联"},

    # -- B 销售话术 --
    {"code": "B1", "level": "medium", "name": "销售话术标题",
     "basis": "标题使用\"无责底薪\"\"月入X万\"\"轻松月入过万\"等引流话术"},
    {"code": "B2", "level": "medium", "name": "薪资含提成",
     "basis": "薪资主要由提成/绩效构成且未说明底薪"},
    {"code": "B3", "level": "medium", "name": "高薪面议",
     "basis": "薪资写\"面议\"但岗位预期收入明显高于同类岗位"},

    # -- C 收费培训 --
    {"code": "C1", "level": "high", "name": "培训收费",
     "basis": "要求入职前参加收费培训（含\"免费培训后上岗\"的变相收费）"},
    {"code": "C2", "level": "high", "name": "押金/服装费/体检费",
     "basis": "要求缴纳押金、服装费、体检费或任何形式的入职前费用"},
    {"code": "C3", "level": "high", "name": "强制办卡/贷款",
     "basis": "要求办信用卡、网贷或分期支付培训/装备费用"},

    # -- D 薪资雇佣关系 --
    {"code": "D1", "level": "high", "name": "门槛极低薪资极高",
     "basis": "零经验/无学历要求却承诺远高于市场水平的薪资"},
    {"code": "D2", "level": "high", "name": "薪资单位异常",
     "basis": "薪资按\"元/天\"\"元/单\"\"元/件\"等非常规单位计量"},
    {"code": "D3", "level": "high", "name": "无底薪纯提成",
     "basis": "JD 明确无底薪、收入完全依赖成单"},

    # -- E 信息矛盾 --
    {"code": "E1", "level": "high", "name": "标题与 JD 明显不符",
     "basis": "岗位标题与 JD 职责内容明显对不上（如标题\"文员\"正文是销售）"},
    {"code": "E2", "level": "high", "name": "岗位名称与职责不符",
     "basis": "职责内容与岗位名称定义不符（如\"运营\"实为地推/电销）"},
    {"code": "E3", "level": "high", "name": "公司信息矛盾",
     "basis": "公司名称、行业与 JD 描述互相矛盾"},

    # -- F 异常行为 --
    {"code": "F1", "level": "high", "name": "JD 留个人联系方式",
     "basis": "JD 正文直接留个人微信/QQ/电话，绕过平台沟通"},
    {"code": "F2", "level": "high", "name": "要求先交钱/转账",
     "basis": "入职前要求交钱、转账或购买指定物品"},
    {"code": "F3", "level": "medium", "name": "试用期模糊",
     "basis": "试用期时长不写或明显超长，试用期薪资描述含糊"},
    {"code": "F4", "level": "high", "name": "承诺收益夸大",
     "basis": "承诺\"一单X万起\"\"轻松月入数万\"等明显夸大的收益数字"},
]

FLAG_FEATURES_BY_CODE: dict[str, dict] = {
    item["code"]: item for item in FLAG_FEATURES
}

VALID_FLAG_LEVELS = ("high", "medium")


def build_features_prompt_text() -> str:
    """把特征清单渲染成精筛 prompt 段落（ai.py 只引用本函数，不感知清单内部结构）。"""
    lines = []
    for item in FLAG_FEATURES:
        level = "高危" if item["level"] == "high" else "中危"
        lines.append(
            f"- {item['code']}（{level}）{item['name']}：{item['basis']}"
        )
    return "\n".join(lines)


def clean_flags(raw) -> list[dict]:
    """清洗 AI 输出的 flags。

    只保留结构化项 {code, level, reason}：level 必须是 high/medium、
    reason 为非空字符串。code 不在清单内的项保留（前端按 level 渲染，
    不依赖 code 映射），字符串等旧格式项丢弃。
    """
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "")
        if level not in VALID_FLAG_LEVELS:
            continue
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        code = str(item.get("code") or "").strip() or "UNKNOWN"
        cleaned.append({"code": code, "level": level, "reason": reason})
    return cleaned


def flag_to_caveat_text(flag: dict) -> str:
    """把单条中危 flag 降级成 caveats 文本（与旧版\"需留意：…\"格式一致）。"""
    reason = str(flag.get("reason") or "").strip()
    name = FLAG_FEATURES_BY_CODE.get(
        str(flag.get("code") or ""), {}
    ).get("name") or ""
    if name and not reason.startswith(name):
        text = f"{name}：{reason}" if reason else name
    else:
        text = reason or name or "需留意可疑岗位"
    return f"需留意：{text}"


def decide_flags(flags: list[dict]) -> dict:
    """分级判定：高危≥1 或 中危≥2 → 输出 flags；中危仅 1 条 → 降级 caveats。

    入参为已清洗的 flags；返回 {\"flags\": [...], \"caveats\": [...]}，
    两组互斥。caveats 为文本列表，直接并入精筛 caveats 输出。
    """
    high = [f for f in flags if f.get("level") == "high"]
    medium = [f for f in flags if f.get("level") == "medium"]
    if high or len(medium) >= 2:
        return {"flags": list(flags), "caveats": []}
    if len(medium) == 1:
        return {"flags": [], "caveats": [flag_to_caveat_text(medium[0])]}
    return {"flags": [], "caveats": []}