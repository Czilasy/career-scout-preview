"""AI 筛选条件构建与硬不匹配判定助手（021 B7 自 ai.py 搬运）。

供 screen_jobs / match_jds 组装筛选描述与本地硬过滤。
"""

from __future__ import annotations

from scripts import boss_cdp_raw as boss
from webui.core import salary_monthly_bounds




# ---------------------------------------------------------------------------
# 两段式 AI 筛选（pipeline 结果精炼）
# ---------------------------------------------------------------------------
# Stage A：字段粗筛。脚本列表结果不含 JD，AI 先按列表字段（薪资/城市/学历等）
#           筛掉"明显"不符合的；学历按常理向下兼容（候选人本科则大专岗也符合）。
# Stage B：JD 精筛。对粗筛留下的岗位批量抓 JD 后，AI 对比候选人画像判 match/not_match。

SCREEN_BATCH_SIZE = 50   # Stage A 每批送 AI 的岗位数（默认值，可被高级设置覆盖）


SCREEN_CONCURRENCY = 1   # Stage A 并发批次数（默认值，可被高级设置覆盖）


MATCH_BATCH_SIZE = 4     # Stage B 每批送 AI 的岗位数（默认值，可被高级设置覆盖）


MATCH_CONCURRENCY = 1    # Stage B 并发批次数（默认值，可被高级设置覆盖）


# 精筛熔断：连续整批 AI 无有效判定（空响应/截断/无效 JSON 等非 systemic 失败）
# 达到阈值即判定端点系统性故障，抛 server_error 让调用方暂停整任务，
# 避免故障时拆半递归放大请求、长期空转。仅 raise_on_systemic=True 时生效。
AI_CONSECUTIVE_FAILURE_LIMIT = 3


# 岗位靠谱判定（B033）：特征清单与分级规则在 webui/flag_features.py，
# 高危≥1 或 中危≥2 → 输出 flags；中危仅 1 条 → 降级 caveats。本模块不再持有阈值。


def _adv_setting(key, default):
    """从 pipeline_exec 的高级设置读取值，读不到用默认。"""
    try:
        from webui.pipeline_exec import load_advanced_settings
        return load_advanced_settings().get(key, default)
    except Exception:
        return default




def _degree_code_label(code):
    """学历码反查中文标签（用于拼 AI 提示词）。"""
    for label, c in boss.DEGREE_MAP.items():
        if c == code:
            return label
    return code




def _build_criteria_description(criteria):
    """把候选人标准（画像摘要 + 确认的筛选字段）转成自然语言给 AI 读。"""
    lines = []
    summary = (criteria.get("profile_summary") or "").strip()
    if summary:
        lines.append(f"候选人画像（仅用于放宽，不作为硬条件）：{summary}")
    if criteria.get("city"):
        lines.append("期望城市：" + "、".join(criteria["city"]))
    if criteria.get("degree"):
        lines.append("候选人学历：" + "、".join(_degree_code_label(c) for c in criteria["degree"]))
    _label_fields = (
        ("salary", boss.SALARY_MAP, "期望薪资"),
        ("experience", boss.EXPERIENCE_MAP, "经验要求"),
        ("industry", boss.INDUSTRY_MAP, "期望行业"),
        ("scale", boss.SCALE_MAP, "期望公司规模"),
        ("stage", boss.STAGE_MAP, "期望融资阶段"),
    )
    for key, mapping, label in _label_fields:
        codes = criteria.get(key) or []
        if codes:
            names = [l for l, c in mapping.items() if c in codes]
            if names:
                lines.append(f"{label}：" + "、".join(names))
    return "\n".join(lines) if lines else "（无明确标准，宽松判断）"




def _salary_selected_bounds(selected_codes):
    """把已选薪资码映射为 (low, high) 月薪区间；high=None 表示无上限。"""
    bounds = []
    for label, code in boss.SALARY_MAP.items():
        if code not in selected_codes or label == "不限":
            continue
        if label == "3K以下":
            bounds.append((0.0, 3.0))
        elif label == "3-5K":
            bounds.append((3.0, 5.0))
        elif label == "5-10K":
            bounds.append((5.0, 10.0))
        elif label == "10-20K":
            bounds.append((10.0, 20.0))
        elif label == "20-50K":
            bounds.append((20.0, 50.0))
        elif label == "50K以上":
            bounds.append((50.0, None))
    return bounds




def _salary_hard_mismatch(salary_text, selected_codes):
    """薪资筛选是硬规则：已知薪资与全部已选区间都无重叠时返回 True。"""
    if not selected_codes:
        return False
    job_bounds = salary_monthly_bounds(salary_text)
    if job_bounds is None:
        return False  # 面议/无法解析：不按硬规则误杀，交给 AI 判断
    job_low, job_high = job_bounds
    for band_low, band_high in _salary_selected_bounds(selected_codes):
        upper = band_high if band_high is not None else float("inf")
        if job_low < upper and job_high >= band_low:
            return False
    return True




_FILTER_FIELD_LABELS = {
    "experience": "经验",
    "degree": "学历",
    "scale": "公司规模",
    "stage": "融资阶段",
    "industry": "行业",
}



# BOSS 列表经验标签有时合并为"在校/应届"，对应在校生+应届生两个码。
_COMBINED_EXPERIENCE_CODES = {
    "在校/应届": ("108", "102"),
}




def _job_filter_tag_text(job):
    """合并 tags/job_labels 等列表标签字段，便于解析结构化经验/学历。"""
    return " | ".join(
        str(value).strip() for value in (
            job.get("tags"), job.get("job_labels"),
            job.get("jobExperience"), job.get("jobDegree"),
            job.get("tags_list"),
        ) if str(value or "").strip()
    )




def _job_experience_codes(job):
    """从列表标签提取经验码；"在校/应届"按在校生+应届生处理。"""
    codes = set()
    for part in _job_filter_tag_text(job).split("|"):
        token = part.strip()
        if token in boss.EXPERIENCE_MAP:
            codes.add(boss.EXPERIENCE_MAP[token])
        elif token in _COMBINED_EXPERIENCE_CODES:
            codes.update(_COMBINED_EXPERIENCE_CODES[token])
    return codes




def _job_degree_codes(job):
    """从列表标签提取学历码。"""
    codes = set()
    for part in _job_filter_tag_text(job).split("|"):
        token = part.strip()
        if token in boss.DEGREE_MAP:
            codes.add(boss.DEGREE_MAP[token])
    return codes




def _job_scale_codes(job):
    code = boss.SCALE_MAP.get((job.get("company_scale") or "").strip())
    return {code} if code else set()




def _job_stage_codes(job):
    code = boss.STAGE_MAP.get((job.get("company_stage") or "").strip())
    return {code} if code else set()




def _job_industry_codes(job):
    industry = (job.get("company_industry") or "").strip()
    if industry in boss.INDUSTRY_MAP:
        return {boss.INDUSTRY_MAP[industry]}
    for name, code in boss.INDUSTRY_MAP.items():
        if name and name in industry:
            return {code}
    return set()




_FILTER_CODE_READERS = {
    "experience": _job_experience_codes,
    "degree": _job_degree_codes,
    "scale": _job_scale_codes,
    "stage": _job_stage_codes,
    "industry": _job_industry_codes,
}




def _job_value_label(job, field):
    """取岗位在该筛选字段上的可读值，用于剔除理由。"""
    if field in ("experience", "degree"):
        mapping = boss.EXPERIENCE_MAP if field == "experience" else boss.DEGREE_MAP
        codes = _job_experience_codes(job) if field == "experience" else _job_degree_codes(job)
        parts = []
        for token in (part.strip() for part in _job_filter_tag_text(job).split("|")):
            is_combined_exp = field == "experience" and token in _COMBINED_EXPERIENCE_CODES
            if is_combined_exp or mapping.get(token) in codes:
                parts.append(token)
        return "、".join(dict.fromkeys(parts))
    return str(job.get(f"company_{field}") or "").strip()




_FILTER_CODE_MAPS = {
    "experience": boss.EXPERIENCE_MAP,
    "degree": boss.DEGREE_MAP,
    "scale": boss.SCALE_MAP,
    "stage": boss.STAGE_MAP,
    "industry": boss.INDUSTRY_MAP,
}




def _criteria_codes(values, mapping):
    """把已选筛选值统一成内部代码，中文标签也兼容。"""
    codes = set()
    for value in values or []:
        text = str(value).strip()
        if text:
            codes.add(str(mapping.get(text, text)))
    return codes




def _job_criteria_hard_mismatch(job, criteria):
    """已选筛选字段与岗位明确值冲突时返回 (field, reason)，未知/未选字段不误杀。"""
    if not isinstance(criteria, dict):
        return None, ""
    for field, reader in _FILTER_CODE_READERS.items():
        selected = _criteria_codes(criteria.get(field), _FILTER_CODE_MAPS[field])
        if not selected or "0" in selected:
            continue
        job_codes = reader(job)
        if job_codes and not (job_codes & selected):
            return field, f"{_FILTER_FIELD_LABELS[field]}{_job_value_label(job, field)}不在筛选范围"
    salary_codes = _criteria_codes(criteria.get("salary"), boss.SALARY_MAP)
    if salary_codes and _salary_hard_mismatch(job.get("salary"), salary_codes):
        return "salary", f"薪资{job.get('salary', '')}不在筛选范围"
    return None, ""
