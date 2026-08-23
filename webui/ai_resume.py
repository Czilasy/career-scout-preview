"""AI 简历解析、统一字段校验与偏好更新（021 B7 自 ai.py 搬运）。

含 _resume_bytes_to_text / analyze_resume_to_fields / validate_* 与
parse_resume / rank_jds / update_preference / is_ai_available。
"""

from __future__ import annotations

import json

from webui.error_registry import ERROR_INVALID
from webui.profile_facts import validate_profile_facts
from webui.ai_prompts import build_resume_analysis_prompt

from webui.ai_client import DEFAULT_TIMEOUT, RANK_BATCH_SIZE
from webui.ai_errors import AISecurityError



# ---------------------------------------------------------------------------
# Resume → unified search fields (Stage 1 of the three-stage pipeline)
# ---------------------------------------------------------------------------

# The unified schema: AI outputs these fields, user confirms them, script
# consumes them.  No translation layer between stages.
UNIFIED_SEARCH_FIELDS = ("keyword", "city", "salary", "experience", "degree", "industry", "scale", "stage")





RESUME_SENTINEL_LABELS = frozenset({"不限", "全部"})



def _resume_platform_registry(platform: str):
    """读取平台注册项；缺失时由调用方映射为 platform_schema_unavailable。"""
    from webui.platforms import get_platform
    return get_platform(platform)




def _build_field_options_prompt(platform: str) -> str:
    """按平台 schema 构建简历分析提示词：列出当前平台允许的标签与稳定代码。"""
    reg = _resume_platform_registry(platform)
    schema = reg.filter_schema
    lines = []
    lines.append("keyword: 候选搜索关键词数组,约10个,覆盖不同岗位方向,格式 [{\"word\":\"Python后端\",\"recommended\":true},...],其中2-3个 recommended=true")
    lines.append("city: 不输出城市，城市由用户自行选择；未选择时默认全国")
    for field in schema.fields:
        options = {
            opt.label: opt.value for opt in field.options if opt.label not in RESUME_SENTINEL_LABELS
        }
        lines.append(f"{field.key}: {field.label}可选值(JSON对象,标签=代码): {json.dumps(options, ensure_ascii=False)}")
    return "\n".join(lines)



def _resume_bytes_to_text(file_bytes: bytes, fmt: str) -> str:
    """Convert resume file bytes to plain text for transport to the AI API.

    The AI endpoint only accepts text, so PDF/DOCX uploads are converted to
    text first.  This is pure transport preparation — no content
    understanding happens here; the AI does all the reading.
    """
    if fmt == "txt":
        return file_bytes.decode("utf-8", errors="replace")
    if fmt == "pdf":
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if fmt == "docx":
        import io

        from docx import Document

        doc = Document(io.BytesIO(file_bytes))
        return "\n".join(para.text for para in doc.paragraphs)
    raise ValueError(f"不支持的简历格式: {fmt}")




_FALLBACK_KEYWORDS_MAX = 10




def _fallback_keywords_from_facts(facts: dict) -> list[str]:
    """AI 漏返 keyword 时，用简历自身核心技能兜底成候选搜索词。

    只取简历里真实出现的技能词，不标 recommended，交给用户二次确认。
    """
    raw = facts.get("core_skills") if isinstance(facts, dict) else None
    if not isinstance(raw, list):
        return []
    words: list[str] = []
    for item in raw:
        word = str(item).strip()
        if not word or word in words:
            continue
        words.append(word)
        if len(words) >= _FALLBACK_KEYWORDS_MAX:
            break
    return words




def _normalize_ai_payload_keys(data):
    """归一化 AI 顶层键名：模型偶发把 profile_facts 写成 ./profile_facts、
    ".profile_facts" 等带 ./ 或 . 前缀的键，剥离前缀避免画像/关键词被丢。"""
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    for key in list(normalized):
        plain = str(key).lstrip("./")
        if plain and plain != key and plain not in normalized:
            normalized[plain] = normalized[key]
            normalized.pop(key, None)
    return normalized




def analyze_resume_to_fields(file_bytes: bytes, fmt: str, endpoint_url: str,
                             api_key: str, model: str = "",
                             platform: str = "boss", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Convert a resume (TXT/PDF/DOCX) to text and extract unified search fields.

    The resume is converted to plain text (transport preparation only), then
    sent directly as the user message.  The AI reads the content and outputs
    fields that map 1:1 to the scraper's CLI parameters.

    Returns a dict with keyword/city plus the platform schema's filter fields.
    Each value is projected to the platform's stable codes; invalid values
    are dropped.

    Raises :class:`AISecurityError` on transport/auth/parse failures.
    """
    from webui import ai as _facade
    resume_text = _facade._resume_bytes_to_text(file_bytes, fmt).strip()
    if not resume_text:
        raise ValueError("简历内容为空")

    field_options = _build_field_options_prompt(platform)
    system_prompt = build_resume_analysis_prompt(field_options)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": resume_text},
    ]

    data = _facade.call_ai(endpoint_url, api_key, messages, timeout=timeout, model=model)
    result = _validate_unified_fields(data, platform)
    data = _normalize_ai_payload_keys(data)

    # 城市不由 AI 管理：用户未选择时由执行层按全国兜底。
    result["city"] = []
    # profile_summary 是自由文本，不参与枚举校验，验证后附加返回
    summary = data.get("profile_summary", "") if isinstance(data, dict) else ""
    result["profile_summary"] = str(summary).strip()
    # profile_facts 隐藏画像事实：宽松验证，无效项丢弃不阻塞整体
    result["profile_facts"] = validate_profile_facts(
        data.get("profile_facts") if isinstance(data, dict) else None
    )
    # AI 可能漏返 keyword（模型没按提示词输出）：此时不得静默空着进
    # 确认页，先用简历自身技能兜底出可选项；完全无可用词才报错重试。
    if not result.get("keyword"):
        fallback = _fallback_keywords_from_facts(result.get("profile_facts") or {})
        if fallback:
            result["keyword"] = [
                {"word": word, "recommended": False} for word in fallback
            ]
        else:
            raise ValueError("未提取到搜索关键词，请重试或手动输入")
    return result




def _validate_unified_fields(data, platform: str = "boss") -> dict:
    """Validate AI/user fields against the requested platform schema.

    Supports multi-select: ``city`` is validated against the platform city
    catalog; filter fields accept a stable code or Chinese label and return
    the platform's stable codes.  Invalid and sentinel values are dropped.
    """
    if not isinstance(data, dict):
        raise AISecurityError(ERROR_INVALID)

    result = {}

    # keyword: spec 007 ③ 新结构 [{word, recommended}]，约 10 个；
    # 旧字符串格式兜底（老数据/老端点传 "Java,Python" 时按逗号分割成 recommended:false）。
    keyword = data.get("keyword", "")
    if isinstance(keyword, list):
        chips = []
        for item in keyword:
            if isinstance(item, dict):
                word = str(item.get("word", "")).strip()
                if not word:
                    continue
                rec = bool(item.get("recommended", False))
                chips.append({"word": word, "recommended": rec})
            elif isinstance(item, str) and item.strip():
                chips.append({"word": item.strip(), "recommended": False})
        result["keyword"] = chips
    elif isinstance(keyword, str) and keyword.strip():
        parts = [p.strip() for p in keyword.replace("，", ",").split(",") if p.strip()]
        result["keyword"] = [{"word": p, "recommended": False} for p in parts]
    else:
        result["keyword"] = []

    # city: split on commas (Chinese ， and English ,), validate each city
    city = data.get("city", "")
    if isinstance(city, list):
        city_parts = [str(c).strip() for c in city]
    else:
        city_parts = str(city).replace("，", ",").split(",")
    reg = _resume_platform_registry(platform)
    cities = [
        c.strip() for c in city_parts if reg.city_catalog.find(c.strip())
    ]
    result["city"] = cities

    # Filter fields: accept stable code or Chinese label, map to platform code.
    for field in reg.filter_schema.fields:
        val = data.get(field.key, "")
        if isinstance(val, list):
            parts = [str(v).strip() for v in val]
        else:
            parts = [str(val).strip()] if str(val).strip() else []
        label_to_code = {
            opt.label: opt.value for opt in field.options
            if opt.label not in RESUME_SENTINEL_LABELS
        }
        code_set = set(label_to_code.values())
        mapped = []
        for part in parts:
            if part in code_set:
                mapped.append(part)
            elif part in label_to_code:
                mapped.append(label_to_code[part])
        result[field.key] = mapped

    return result




# ---------------------------------------------------------------------------
# JSON validation helpers
# ---------------------------------------------------------------------------

def _require(data: dict, field: str, expected_type: type):
    """Return *data[field]* if it exists and matches *expected_type*, else raise."""
    if field not in data:
        raise ValueError(f"missing_field:{field}")
    if not isinstance(data[field], expected_type):
        raise ValueError(f"invalid_type:{field}")
    return data[field]




def _require_str_list(data: dict, field: str) -> list[str]:
    """Return *data[field]* as a list of strings, raising on non-string elements."""
    value = _require(data, field, list)
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"invalid_element:{field}")
    return value




# ---------------------------------------------------------------------------
# JSON validation — application-side contracts for AI outputs
# ---------------------------------------------------------------------------


def validate_resume_response(data) -> dict:
    """Validate the persistent workbench resume-extraction contract."""
    if not isinstance(data, dict):
        raise ValueError("invalid_response")

    profile_name = _require(data, "profile_name", str)
    city = _require(data, "city", str)
    roles = _require_str_list(data, "roles")
    skills = _require_str_list(data, "skills")
    keywords = _require_str_list(data, "keywords")
    suggestions = _require(data, "suggestions", list)

    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            raise ValueError("invalid_suggestion")
        if not isinstance(suggestion.get("field"), str):
            raise ValueError("invalid_suggestion")
        if not isinstance(suggestion.get("source"), str):
            raise ValueError("invalid_suggestion")
        if not isinstance(suggestion.get("uncertain"), bool):
            raise ValueError("invalid_suggestion")
        if "value" not in suggestion:
            raise ValueError("invalid_suggestion")

    return {
        "profile_name": profile_name,
        "city": city,
        "roles": roles,
        "skills": skills,
        "keywords": keywords,
        "suggestions": suggestions,
    }




def validate_rank_response(data, input_job_ids) -> list[str]:
    """Validate an AI JD ranking response.

    Returns the ranked job_ids list.  Raises :class:`ValueError` if any
    returned job_id was not in *input_job_ids*, or on type/structure errors.
    The AI cannot inject jobs that were not part of the input.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid_response")

    ranked = _require(data, "ranked_job_ids", list)
    input_set = set(input_job_ids)
    result: list[str] = []
    for jid in ranked:
        if not isinstance(jid, str):
            raise ValueError("invalid_element:ranked_job_ids")
        if jid not in input_set:
            raise ValueError("unknown_job_id")
        result.append(jid)
    return result




def validate_preference_response(data) -> dict:
    """Validate an AI preference update response.

    Returns a dict with ``positive_terms``, ``negative_terms``,
    ``keyword_weights`` and ``uncertain``.  Raises :class:`ValueError` on
    missing fields or type mismatches.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid_response")

    positive_terms = _require_str_list(data, "positive_terms")
    negative_terms = _require_str_list(data, "negative_terms")
    keyword_weights = _require(data, "keyword_weights", dict)
    uncertain = _require(data, "uncertain", list)

    for key, value in keyword_weights.items():
        if not isinstance(key, str):
            raise ValueError("invalid_keyword_weight_key")
        # bool is a subclass of int — reject it explicitly so True/False
        # are not accepted as numeric weights.
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("invalid_keyword_weight_value")

    return {
        "positive_terms": positive_terms,
        "negative_terms": negative_terms,
        "keyword_weights": keyword_weights,
        "uncertain": uncertain,
    }




# ---------------------------------------------------------------------------
# Resume-driven screening suggestions
# ---------------------------------------------------------------------------
# High-level AI operations
# ---------------------------------------------------------------------------


def parse_resume(resume_text: str, endpoint_url: str, api_key: str,
                 model: str = "") -> dict:
    """Parse résumé text for the persistent workbench and validate all fields."""
    from webui import ai as _facade
    messages = [
        {
            "role": "system",
            "content": (
                "你是简历解析助手。根据简历内容提取JSON："
                "profile_name(画像名,str), city(城市,str), "
                "roles(岗位方向,list[str]), skills(技能,list[str]), "
                "keywords(搜索关键词,list[str],最多3个), "
                "suggestions(建议,list[{field,value,source,uncertain}])。"
                "仅使用简历明确内容，无依据时返回空数组。"
                "禁止编造经历、学历、薪资、证书或项目。"
            ),
        },
        {"role": "user", "content": resume_text},
    ]
    data = _facade.call_ai(endpoint_url, api_key, messages, model=model)
    return validate_resume_response(data)




def rank_jds(confirmed_fields: dict, jobs_with_jd: list, endpoint_url: str, api_key: str, model: str = "") -> list[str]:
    """Call AI to rank jobs by relevance, in batches of at most 10.

    Returns a list of ranked job_ids.  Each batch is validated by
    :func:`validate_rank_response` to reject any job_id not present in
    the input — the AI cannot introduce jobs the caller did not supply.
    """
    from webui import ai as _facade
    if not jobs_with_jd:
        return []

    ranked: list[str] = []
    for i in range(0, len(jobs_with_jd), RANK_BATCH_SIZE):
        batch = jobs_with_jd[i:i + RANK_BATCH_SIZE]
        batch_job_ids = [job["job_id"] for job in batch]
        job_summaries = [
            {
                "job_id": job["job_id"],
                "title": job.get("title", ""),
                "jd": job.get("jd", ""),
            }
            for job in batch
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是岗位排序助手。根据画像与人工条件对给定JD按相关性排序。"
                    "返回JSON：{ranked_job_ids: [输入job_id的排序列表]}。"
                    "不得生成链接、改变人工条件或返回未输入的job_id。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "confirmed_fields": confirmed_fields,
                        "jobs": job_summaries,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        data = _facade.call_ai(endpoint_url, api_key, messages, model=model)
        batch_ranked = validate_rank_response(data, batch_job_ids)
        ranked.extend(batch_ranked)

    return ranked




def update_preference(profile: dict, feedback_events: list, endpoint_url: str, api_key: str, model: str = "") -> dict:
    """Call AI to update preferences based on recent feedback.

    Returns ``{positive_terms, negative_terms, keyword_weights, uncertain}``.
    The output is validated by :func:`validate_preference_response`.
    """
    from webui import ai as _facade
    messages = [
        {
            "role": "system",
            "content": (
                "你是偏好更新助手。根据画像与最近反馈更新求职偏好。"
                "返回JSON：{positive_terms(list[str]), "
                "negative_terms(list[str]), keyword_weights(dict[str,float]), "
                "uncertain(list)}。一次反馈不得形成永久黑名单。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "profile": profile,
                    "feedback_events": feedback_events,
                },
                ensure_ascii=False,
            ),
        },
    ]
    data = _facade.call_ai(endpoint_url, api_key, messages, model=model)
    return validate_preference_response(data)




# ---------------------------------------------------------------------------
# AI 语义相似度占位（T031）
# ---------------------------------------------------------------------------
# AI availability detection (T047, FR-031 / FR-032)
# ---------------------------------------------------------------------------

def is_ai_available(settings, credential_ref, api_key) -> bool:
    """检测 AI 服务是否可用（FR-031, FR-032）。

    纯函数：只检查入参是否齐全，不调 AI（call_ai）、不访凭据库（keyring）、
    不发 HTTP 请求（requests）。调用方负责从凭据库取 api_key 后传入。
    三条件全满足返回 True，任一不满足返回 False：

    1. *settings* 是 dict 且 ``settings["is_configured"]`` 为真；
    2. *credential_ref* 是非空字符串；
    3. *api_key* 是非空字符串。

    返回值恒为 bool，永不包含凭据本身——可安全用于日志与响应。
    """
    if not isinstance(settings, dict) or not settings.get("is_configured"):
        return False
    if not isinstance(credential_ref, str) or not credential_ref.strip():
        return False
    if not isinstance(api_key, str) or not api_key.strip():
        return False
    return True
