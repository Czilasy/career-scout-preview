"""AI 筛选条件构建与硬不匹配判定助手（021 B7 自 ai.py 搬运）。

供 screen_jobs / match_jds 组装筛选描述与本地硬过滤。
"""

from __future__ import annotations

from scripts import boss_cdp_raw as boss
from webui import recruiter_activity
from webui.core import salary_monthly_bounds
from webui.ai_platform_adapter import (
    _COMBINED_EXPERIENCE_CODES,
    resolve_platform_ai_adapter,
)




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


def _resolve_platform(platform=None, job=None):
    """Resolve the source platform without falling back across field maps."""
    candidate = platform
    if not candidate and isinstance(job, dict):
        candidate = job.get("platform")
    normalized = str(candidate or "boss").strip().lower()
    return normalized if normalized in {"boss", "zhilian"} else "boss"


def _platform_option_maps(platform, field):
    """Return ``label -> code`` and ``code -> label`` for one platform field."""
    return resolve_platform_ai_adapter(platform).option_maps(field)


def _platform_value_code(value, platform, field):
    """Normalize a schema label or code into that platform's stable code."""
    text = str(value or "").strip()
    if not text:
        return ""
    label_to_code, _code_to_label = _platform_option_maps(platform, field)
    return str(label_to_code.get(text, text))


def _platform_value_label(value, platform, field):
    """Normalize a schema label or code into that platform's display label."""
    text = str(value or "").strip()
    if not text:
        return ""
    _label_to_code, code_to_label = _platform_option_maps(platform, field)
    return str(code_to_label.get(text, text))


def _criteria_codes_for_platform(values, platform, field):
    return {
        code
        for value in values or []
        if (code := _platform_value_code(value, platform, field))
    }


def _criteria_labels_for_platform(values, platform, field):
    labels = []
    seen = set()
    for value in values or []:
        label = _platform_value_label(value, platform, field)
        if label and label not in seen:
            labels.append(label)
            seen.add(label)
    return labels


def _job_field_text(job, field, platform=None):
    """Read one normalized job field at the platform adapter boundary."""
    return resolve_platform_ai_adapter(platform, job).job_field_text(job, field)


def _is_unrestricted_code(platform, field, code):
    return resolve_platform_ai_adapter(platform).is_unrestricted(field, code)


def _screen_hard_fields_text(platform):
    return resolve_platform_ai_adapter(platform).screen_hard_fields_text()


def _screen_input_note(platform):
    return resolve_platform_ai_adapter(platform).screen_input_note()


def _screen_fields(job, platform=None):
    """Return the adapter-owned compact list fields for the shared screen flow."""
    return resolve_platform_ai_adapter(platform, job).screen_fields(job)


def _detail_fields(job, platform=None):
    """Return the adapter-owned detail fields for the shared match flow."""
    return resolve_platform_ai_adapter(platform, job).detail_fields(job)




def _degree_code_label(code, platform="boss"):
    """学历码反查中文标签（用于拼 AI 提示词）。"""
    return resolve_platform_ai_adapter(platform).degree_code_label(code)




def _build_criteria_description(criteria, platform="boss"):
    """把候选人标准（画像摘要 + 确认的筛选字段）转成自然语言给 AI 读。"""
    return resolve_platform_ai_adapter(platform).build_criteria_description(criteria)




def _salary_selected_bounds(selected_codes, platform="boss"):
    """把已选薪资码映射为 (low, high) 月薪区间；high=None 表示无上限。"""
    return resolve_platform_ai_adapter(platform).salary_selected_bounds(selected_codes)




def _salary_hard_mismatch(salary_text, selected_codes, platform="boss"):
    """薪资筛选是硬规则：已知薪资与全部已选区间都无重叠时返回 True。"""
    return resolve_platform_ai_adapter(platform).salary_hard_mismatch(
        salary_text, selected_codes,
    )




_FILTER_FIELD_LABELS = {
    "experience": "经验",
    "degree": "学历",
    "scale": "公司规模",
    "stage": "融资阶段",
    "industry": "行业",
    "company_nature": "公司性质",
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




def _job_experience_codes(job, platform=None):
    """从列表标签提取经验码；"在校/应届"按在校生+应届生处理。"""
    return resolve_platform_ai_adapter(platform, job).job_experience_codes(job)




def _job_degree_codes(job, platform=None):
    """从列表标签提取学历码。"""
    return resolve_platform_ai_adapter(platform, job).job_degree_codes(job)




def _job_scale_codes(job, platform=None):
    return resolve_platform_ai_adapter(platform, job).job_scale_codes(job)




def _job_stage_codes(job, platform=None):
    return resolve_platform_ai_adapter(platform, job).job_stage_codes(job)




def _job_industry_codes(job, platform=None):
    return resolve_platform_ai_adapter(platform, job).job_industry_codes(job)


def _job_company_nature_codes(job, platform=None):
    return resolve_platform_ai_adapter(platform, job).job_company_nature_codes(job)




_FILTER_CODE_READERS = {
    "experience": _job_experience_codes,
    "degree": _job_degree_codes,
    "scale": _job_scale_codes,
    "stage": _job_stage_codes,
    "industry": _job_industry_codes,
}




def _job_value_label(job, field, platform=None):
    """取岗位在该筛选字段上的可读值，用于剔除理由。"""
    return resolve_platform_ai_adapter(platform, job).job_value_label(job, field)




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




def _job_criteria_hard_mismatch(job, criteria, platform=None):
    """已选筛选字段与岗位明确值冲突时返回 (field, reason)，未知/未选字段不误杀。"""
    if not isinstance(criteria, dict):
        return None, ""
    adapter = resolve_platform_ai_adapter(platform, job)
    platform = adapter.key
    for field in adapter.filter_fields:
        selected = adapter.criteria_codes(criteria.get(field), field)
        if not selected:
            continue
        selected = {
            code for code in selected
            if not _is_unrestricted_code(platform, field, code)
        }
        if not selected:
            continue
        job_codes = adapter.job_codes(job, field)
        if job_codes and not (job_codes & selected):
            return field, f"{_FILTER_FIELD_LABELS[field]}{_job_value_label(job, field, platform)}不在筛选范围"
    salary_codes = adapter.criteria_codes(criteria.get("salary"), "salary")
    if salary_codes and _salary_hard_mismatch(
        job.get("salary"), salary_codes, platform
    ):
        return "salary", f"薪资{job.get('salary', '')}不在筛选范围"
    return None, ""



def job_hard_mismatch(job, criteria, *, include_recruiter=False, platform=None):
    """组合硬规则入口：六类码值冲突 +（仅精筛）第 7 类招聘者活跃。

    第 7 类事实读 ``job["extra"]["recruiter_activity"]``（028 详情抓取产出）。
    粗筛在列表阶段拿不到活跃数据，MUST 恒以 include_recruiter=False 调用
    （028 FR-008）。返回 (field, reason)；field="recruiter_activity" 表示
    第 7 类命中，reason 由判定域模板生成（「负责人上次活跃X，超过要求的Y」）。
    """
    field, reason = _job_criteria_hard_mismatch(
        job, criteria, platform=platform
    )
    if field:
        return field, reason
    if not include_recruiter or not isinstance(criteria, dict):
        return None, ""
    selected = criteria.get(recruiter_activity.FIELD_KEY)
    codes = selected if isinstance(selected, list) else [selected]
    days = next(
        (recruiter_activity.THRESHOLD_DAYS[str(c)] for c in codes
         if str(c) in recruiter_activity.THRESHOLD_DAYS),
        None,
    )
    if days is None:
        return None, ""
    extra = job.get("extra")
    fact = extra.get(recruiter_activity.FIELD_KEY) if isinstance(extra, dict) else None
    verdict = recruiter_activity.evaluate(fact, days)
    if verdict:
        return recruiter_activity.FIELD_KEY, verdict["reason"]
    return None, ""
