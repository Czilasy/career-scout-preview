"""AI adapter: credential management, connection testing, JSON-validated AI calls.

All errors are sanitized to safe classification codes.  API keys, request
bodies and raw responses never appear in exceptions, logs or return values.
The application validates every AI output on its own side — the AI never
decides task status.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import requests
import keyring

from scripts import boss_cdp_raw as boss


KEYRING_SERVICE = "boss-workbench"
DEFAULT_TIMEOUT = 60
CONNECTION_TIMEOUT = 15
RANK_BATCH_SIZE = 10

# Safe error classifications returned to callers.  Never include raw
# exception text, API keys or response bodies.
ERROR_TIMEOUT = "timeout"
ERROR_AUTH = "auth_failed"
ERROR_NETWORK = "network_error"
ERROR_INVALID = "invalid_response"


class AISecurityError(Exception):
    """AI call failure carrying only a safe error classification.

    The string form is the error_code alone, so it is safe to log or
    surface to users — it never contains the API key, request body or
    raw response.  The original exception is suppressed via ``from None``
    so tracebacks do not leak sensitive details either.
    """

    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)


# ---------------------------------------------------------------------------
# Credential management
# ---------------------------------------------------------------------------

def _host_from_url(endpoint_url: str) -> str:
    """Extract the hostname from *endpoint_url*, falling back to the raw string."""
    return urlparse(endpoint_url).hostname or endpoint_url


def _chat_completions_url(endpoint_url: str) -> str:
    """补全 chat completions 路径。

    用户填到 /v1 这一级（如 https://api.openai.com/v1 或
    https://opencode.ai/zen/v1），代码自动补 /chat/completions。
    如果用户已经填了 /chat/completions 则不再补。
    """
    url = (endpoint_url or "").rstrip("/")
    if not url:
        return url
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def list_models(endpoint_url: str, api_key: str) -> list[str]:
    """GET /models 拉取可用模型列表。

    endpoint_url 填到 /v1 这一级。返回 model id 字符串列表，按字母序排序。
    失败时抛 AISecurityError（携带安全错误码，不含原始响应）。
    """
    base = (endpoint_url or "").rstrip("/")
    if not base:
        raise AISecurityError(ERROR_NETWORK)
    if not base.endswith("/models"):
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")] + "/models"
        else:
            base = base + "/models"
    try:
        response = requests.get(
            base,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=CONNECTION_TIMEOUT,
        )
    except requests.Timeout:
        raise AISecurityError(ERROR_TIMEOUT) from None
    except requests.RequestException:
        raise AISecurityError(ERROR_NETWORK) from None
    except Exception:
        raise AISecurityError(ERROR_INVALID) from None

    if response.status_code in (401, 403):
        raise AISecurityError(ERROR_AUTH)
    if response.status_code >= 400:
        raise AISecurityError(ERROR_INVALID)

    try:
        data = response.json()
    except ValueError:
        raise AISecurityError(ERROR_INVALID) from None

    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise AISecurityError(ERROR_INVALID)

    models = []
    for item in data["data"]:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            models.append(item["id"])
    return sorted(set(models))


def store_api_key(endpoint_url: str, api_key: str) -> str:
    """Store *api_key* in the system credential store and return a credential_ref.

    The credential_ref is the hostname of *endpoint_url*, used as the
    keyring username.  The service name is fixed to ``boss-workbench``.
    """
    host = _host_from_url(endpoint_url)
    keyring.set_password(KEYRING_SERVICE, host, api_key)
    return host


def retrieve_api_key(credential_ref: str) -> str:
    """Retrieve the api_key associated with *credential_ref* from the credential store."""
    return keyring.get_password(KEYRING_SERVICE, credential_ref)


def delete_api_key(credential_ref: str) -> None:
    """Delete the api_key associated with *credential_ref* from the credential store."""
    keyring.delete_password(KEYRING_SERVICE, credential_ref)


# ---------------------------------------------------------------------------
# Connection testing
# ---------------------------------------------------------------------------

def test_connection(endpoint_url: str, api_key: str, model: str = "") -> tuple[bool, str | None]:
    """Send a minimal chat completions request to verify connectivity.

    Returns ``(True, None)`` on success, or ``(False, error_code)`` where
    *error_code* is one of: ``timeout``, ``auth_failed``, ``network_error``,
    ``invalid_response``.  Never includes raw error details, the API key
    or response body.
    """
    payload = {
        "model": model or "auto",
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            _chat_completions_url(endpoint_url), json=payload, headers=headers, timeout=CONNECTION_TIMEOUT
        )
    except requests.Timeout:
        return (False, ERROR_TIMEOUT)
    except requests.RequestException:
        return (False, ERROR_NETWORK)
    except Exception:
        return (False, ERROR_INVALID)

    if response.status_code in (401, 403):
        return (False, ERROR_AUTH)
    if response.status_code >= 400:
        return (False, ERROR_INVALID)
    return (True, None)


# ---------------------------------------------------------------------------
# AI call
# ---------------------------------------------------------------------------

def call_ai(endpoint_url: str, api_key: str, messages: list, timeout: int = DEFAULT_TIMEOUT,
            temperature: float = 0.3, model: str = "") -> dict:
    """Call an OpenAI-compatible chat completions endpoint and return parsed JSON.

    Raises :class:`AISecurityError` with a safe error_code on any failure.
    The exception never contains the API key, request body or raw response,
    and the original exception is suppressed so tracebacks stay clean.
    """
    payload = {
        "model": model or "auto",
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            _chat_completions_url(endpoint_url), json=payload, headers=headers, timeout=timeout
        )
    except requests.Timeout:
        raise AISecurityError(ERROR_TIMEOUT) from None
    except requests.RequestException:
        raise AISecurityError(ERROR_NETWORK) from None
    except Exception:
        raise AISecurityError(ERROR_INVALID) from None

    if response.status_code in (401, 403):
        raise AISecurityError(ERROR_AUTH)
    if response.status_code >= 400:
        raise AISecurityError(ERROR_INVALID)

    try:
        data = response.json()
    except ValueError:
        raise AISecurityError(ERROR_INVALID) from None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AISecurityError(ERROR_INVALID) from None

    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise AISecurityError(ERROR_INVALID) from None


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
    """Validate an AI resume parsing response.

    Returns a dict with ``profile_name``, ``city``, ``roles``, ``skills``,
    ``keywords`` and ``suggestions``.  Raises :class:`ValueError` on missing
    fields or type mismatches — the AI does not decide what is valid.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid_response")

    profile_name = _require(data, "profile_name", str)
    city = _require(data, "city", str)
    roles = _require_str_list(data, "roles")
    skills = _require_str_list(data, "skills")
    keywords = _require_str_list(data, "keywords")
    suggestions = _require(data, "suggestions", list)

    for sug in suggestions:
        if not isinstance(sug, dict):
            raise ValueError("invalid_suggestion")
        if "field" not in sug or not isinstance(sug["field"], str):
            raise ValueError("invalid_suggestion")
        if "source" not in sug or not isinstance(sug["source"], str):
            raise ValueError("invalid_suggestion")
        if "uncertain" not in sug or not isinstance(sug["uncertain"], bool):
            raise ValueError("invalid_suggestion")
        if "value" not in sug:
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
# High-level AI operations
# ---------------------------------------------------------------------------

def parse_resume(resume_text: str, endpoint_url: str, api_key: str, model: str = "") -> dict:
    """Call AI to parse a resume and return validated fields.

    Returns ``{profile_name, city, roles, skills, keywords, suggestions}``.
    The output is validated by :func:`validate_resume_response`.
    """
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
    data = call_ai(endpoint_url, api_key, messages, model=model)
    return validate_resume_response(data)


def suggest_screening_filters(resume_text: str, endpoint_url: str, api_key: str,
                              timeout: int = DEFAULT_TIMEOUT, model: str = "") -> dict:
    """Call AI to read a resume and suggest BOSS filter values.

    Returns ``{city, salary, experience, degree, scale, stage, industry}``
    where each value is a valid code or empty string. Invalid codes are
    coerced to empty so downstream merging skips them. "0" (不限) is treated
    as empty to match build_screening_filter_options.

    Raises :class:`AISecurityError` on timeout/network/invalid responses.
    The exception never contains the resume text or API key.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是简历筛选助手。根据简历内容给出BOSS直聘筛选项建议值，返回JSON："
                "city(城市名,str,如上海), salary(薪资段代码,str,如405代表10-20K), "
                "experience(经验代码,str,如105代表3-5年), degree(学历代码,str,如203代表本科), "
                "scale(公司规模代码,str,如303代表100-499人), "
                "stage(融资阶段代码,str,如804代表B轮), "
                "industry(行业代码,str,如1001代表互联网)。"
                "无法从简历提取的字段返回空字符串。禁止编造。"
            ),
        },
        {"role": "user", "content": resume_text},
    ]
    data = call_ai(endpoint_url, api_key, messages, timeout=timeout, model=model)
    return _validate_suggest_response(data)


def _validate_suggest_response(data) -> dict:
    """Validate AI suggest response: keep only valid codes, coerce invalid to empty."""
    if not isinstance(data, dict):
        raise AISecurityError(ERROR_INVALID)
    valid_sets = {
        name: {v for v in mapping.values() if v != "0"}
        for name, mapping in (
            ("salary", boss.SALARY_MAP),
            ("experience", boss.EXPERIENCE_MAP),
            ("degree", boss.DEGREE_MAP),
            ("scale", boss.SCALE_MAP),
            ("stage", boss.STAGE_MAP),
            ("industry", boss.INDUSTRY_MAP),
        )
    }
    valid_sets["city"] = {n for n in boss.CITY_MAP if n != "全国"}
    result = {}
    for field in ("city", "salary", "experience", "degree", "scale", "stage", "industry"):
        val = data.get(field, "")
        if not isinstance(val, str):
            val = ""
        val = val.strip()
        result[field] = val if val in valid_sets[field] else ""
    return result


SCREENING_FIELDS = ("city", "salary", "experience", "degree", "scale", "stage", "industry")
SUGGESTION_CONFIDENCE_THRESHOLD = 70


def _screening_valid_sets():
    valid_sets = {
        name: {value for value in mapping.values() if value != "0"}
        for name, mapping in (
            ("salary", boss.SALARY_MAP),
            ("experience", boss.EXPERIENCE_MAP),
            ("degree", boss.DEGREE_MAP),
            ("scale", boss.SCALE_MAP),
            ("stage", boss.STAGE_MAP),
            ("industry", boss.INDUSTRY_MAP),
        )
    }
    valid_sets["city"] = {name for name in boss.CITY_MAP if name != "全国"}
    return valid_sets


def validate_cautious_screening_suggestions(data, confirmed_fields=None,
                                             threshold=SUGGESTION_CONFIDENCE_THRESHOLD):
    """Apply enum, confidence and user-lock gates to screening suggestions."""
    raw = data if isinstance(data, dict) else {}
    confirmed = confirmed_fields if isinstance(confirmed_fields, dict) else {}
    valid_sets = _screening_valid_sets()
    values = {}
    meta = {}
    for field in SCREENING_FIELDS:
        locked = confirmed.get(field)
        if isinstance(locked, str) and locked.strip():
            values[field] = locked.strip()
            meta[field] = {"status": "user_confirmed", "confidence": None}
            continue
        item = raw.get(field)
        value = item.get("value", "") if isinstance(item, dict) else ""
        confidence = item.get("confidence") if isinstance(item, dict) else None
        valid_confidence = (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and 0 <= confidence <= 100
        )
        valid_value = isinstance(value, str) and value.strip() in valid_sets[field]
        if valid_value and valid_confidence and confidence >= threshold:
            values[field] = value.strip()
            meta[field] = {"status": "ai_suggested", "confidence": confidence}
        else:
            values[field] = ""
            meta[field] = {
                "status": "pending_confirmation",
                "confidence": confidence if valid_confidence else None,
            }
    return {"values": values, "meta": meta}


def suggest_screening_filters_cautious(resume_text, endpoint_url, api_key,
                                       confirmed_fields=None, timeout=DEFAULT_TIMEOUT, model=""):
    """Generate deterministic, confidence-bearing suggestions and validate them."""
    unlocked = [
        field for field in SCREENING_FIELDS
        if not str((confirmed_fields or {}).get(field) or "").strip()
    ]
    messages = [{
        "role": "system",
        "content": (
            "根据简历为未锁定的 BOSS 筛选字段给建议。仅返回 JSON；每个字段必须是"
            "{value:string, confidence:number 0-100}。没有明确依据时 value 为空且低置信度。"
            f"未锁定字段：{','.join(unlocked)}。禁止输出近似枚举或改写已确认字段。"
        ),
    }, {"role": "user", "content": resume_text}]
    data = call_ai(
        endpoint_url, api_key, messages, timeout=timeout, temperature=0, model=model,
    )
    return validate_cautious_screening_suggestions(data, confirmed_fields=confirmed_fields)


def rank_jds(confirmed_fields: dict, jobs_with_jd: list, endpoint_url: str, api_key: str, model: str = "") -> list[str]:
    """Call AI to rank jobs by relevance, in batches of at most 10.

    Returns a list of ranked job_ids.  Each batch is validated by
    :func:`validate_rank_response` to reject any job_id not present in
    the input — the AI cannot introduce jobs the caller did not supply.
    """
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
        data = call_ai(endpoint_url, api_key, messages, model=model)
        batch_ranked = validate_rank_response(data, batch_job_ids)
        ranked.extend(batch_ranked)

    return ranked


def update_preference(profile: dict, feedback_events: list, endpoint_url: str, api_key: str, model: str = "") -> dict:
    """Call AI to update preferences based on recent feedback.

    Returns ``{positive_terms, negative_terms, keyword_weights, uncertain}``.
    The output is validated by :func:`validate_preference_response`.
    """
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
    data = call_ai(endpoint_url, api_key, messages, model=model)
    return validate_preference_response(data)


# ---------------------------------------------------------------------------
# AI 语义相似度占位（T031）
# ---------------------------------------------------------------------------

def assess_semantic_similarity(
    resume_text, jd_text, *, ai_available=False, endpoint_url="", api_key="",
    timeout=DEFAULT_TIMEOUT, model="",
) -> dict:
    """Return a program-validated semantic verdict.

    The default keeps the established no-AI degradation path. Callers that
    enable AI must provide the configured endpoint and key; raw model output
    is validated and sanitized by :mod:`webui.semantic`.
    """
    from webui.semantic import assess_semantic_similarity_formal

    call_fn = None
    if ai_available and endpoint_url and api_key:
        def call_fn(prompt):
            try:
                return call_ai(
                    endpoint_url,
                    api_key,
                    [{"role": "system", "content": prompt}],
                    timeout=timeout,
                    model=model,
                )
            except AISecurityError as exc:
                if exc.error_code == ERROR_TIMEOUT:
                    raise TimeoutError from None
                if exc.error_code == ERROR_NETWORK:
                    raise ConnectionError from None
                raise RuntimeError from None

    return assess_semantic_similarity_formal(
        resume_text,
        jd_text,
        ai_available=bool(ai_available and call_fn),
        call_ai_fn=call_fn,
    )


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
