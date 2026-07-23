"""AI adapter: credential management, connection testing, JSON-validated AI calls.

All errors are sanitized to safe classification codes.  API keys, request
bodies and raw responses never appear in exceptions, logs or return values.
The application validates every AI output on its own side — the AI never
decides task status.
"""

from __future__ import annotations

import copy
import json
import time
from urllib.parse import urlparse

import requests
import keyring

from scripts import boss_cdp_raw as boss
from webui.candidate import (
    canonicalize_resume_text_v2,
    redact_pii,
    resolve_evidence_quote,
    CANDIDATE_ANALYSIS_V3_CONTRACT,
    build_empty_candidate_analysis,
    normalize_candidate_analysis,
)


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
ERROR_RATE_LIMIT = "rate_limited"

# Free-tier endpoints throttle aggressively (HTTP 429).  Retry a few times
# with increasing backoff so a transient limit doesn't fail the whole step.
RATE_LIMIT_ATTEMPTS = 4
RATE_LIMIT_BACKOFF_SECONDS = (5, 15, 30)


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


# 面向用户的错误文案（端点用它替代裸 error_code，给出可操作的提示）
ERROR_USER_MESSAGES = {
    ERROR_TIMEOUT: "AI 响应超时，请稍后重试",
    ERROR_AUTH: "API 密钥无效或已过期，请检查 AI 设置",
    ERROR_NETWORK: "无法连接 AI 服务，请检查网络与地址配置",
    ERROR_INVALID: "AI 返回了无法解析的内容，请重试",
    ERROR_RATE_LIMIT: "AI 服务限流（免费额度），请稍候再试",
}


def user_facing_error(error_code: str) -> str:
    """Return a user-friendly Chinese message for a safe error code."""
    return ERROR_USER_MESSAGES.get(error_code, f"AI 调用失败（{error_code}）")


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

def test_connection(endpoint_url: str, api_key: str, model: str = "") -> dict:
    """Probe the real candidate-v3 extraction capability with fictional data."""
    fictional_resume = "虚构候选人：具备 Python 后端经验，负责演示订单系统。"
    messages = DiscoveryAIProvider._build_analyze_messages(fictional_resume)
    try:
        response = call_ai(
            endpoint_url, api_key, messages, model=model or "auto",
            timeout=CONNECTION_TIMEOUT,
        )
    except AISecurityError as exc:
        transport = "failed" if exc.error_code in {ERROR_TIMEOUT, ERROR_AUTH, ERROR_NETWORK} else "ready"
        return {"ok": False, "transport": transport, "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [exc.error_code]}
    try:
        parsed = cleanup_candidate_analysis_response(response)
        normalized = normalize_candidate_analysis(parsed, fictional_resume)
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"ok": False, "transport": "ready", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_INVALID]}
    quality = normalized["quality"]
    warning_codes = list(dict.fromkeys(item["code"] for item in quality["warnings"]))
    return {"ok": True, "transport": "ready", "generation": "ready",
            "candidate_contract": quality["status"], "warning_codes": warning_codes}


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
    response = None
    for attempt in range(RATE_LIMIT_ATTEMPTS):
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
        if response.status_code != 429:
            break
        # 429 限流是瞬时的：退避后重试，耗尽次数才报 rate_limited
        if attempt < RATE_LIMIT_ATTEMPTS - 1:
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS[min(attempt, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)])
    if response is not None and response.status_code == 429:
        raise AISecurityError(ERROR_RATE_LIMIT)

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
# Resume → unified search fields (Stage 1 of the three-stage pipeline)
# ---------------------------------------------------------------------------

# The unified schema: AI outputs these fields, user confirms them, script
# consumes them.  No translation layer between stages.
UNIFIED_SEARCH_FIELDS = ("keyword", "city", "salary", "experience", "degree", "industry", "scale", "stage")


def _build_field_options_prompt() -> str:
    """Build a prompt fragment listing all valid values for each filter field."""
    lines = []
    lines.append("keyword: 候选搜索关键词数组,约10个,覆盖不同岗位方向,格式 [{\"word\":\"Python后端\",\"recommended\":true},...],其中2-3个 recommended=true")
    lines.append(f"city: 城市名,必须是以下之一: {', '.join(list(boss.CITY_MAP.keys())[:50])}...等")
    lines.append(f"salary: 薪资段代码,可选值: {json.dumps(boss.SALARY_MAP, ensure_ascii=False)}")
    lines.append(f"experience: 经验要求代码,可选值: {json.dumps(boss.EXPERIENCE_MAP, ensure_ascii=False)}")
    lines.append(f"degree: 学历代码,可选值: {json.dumps(boss.DEGREE_MAP, ensure_ascii=False)}")
    lines.append(f"industry: 行业代码,可选值: {json.dumps(boss.INDUSTRY_MAP, ensure_ascii=False)}")
    lines.append(f"scale: 公司规模代码,可选值: {json.dumps(boss.SCALE_MAP, ensure_ascii=False)}")
    lines.append(f"stage: 融资阶段代码,可选值: {json.dumps(boss.STAGE_MAP, ensure_ascii=False)}")
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


def analyze_resume_to_fields(file_bytes: bytes, fmt: str, endpoint_url: str,
                             api_key: str, model: str = "",
                             timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Convert a resume (TXT/PDF/DOCX) to text and extract unified search fields.

    The resume is converted to plain text (transport preparation only), then
    sent directly as the user message.  The AI reads the content and outputs
    fields that map 1:1 to the scraper's CLI parameters.

    Returns a dict with keys: keyword, city, salary, experience, degree,
    industry, scale, stage.  Each value is validated against the script's
    enum maps; invalid values are coerced to empty string.

    Raises :class:`AISecurityError` on transport/auth/parse failures.
    """
    resume_text = _resume_bytes_to_text(file_bytes, fmt).strip()
    if not resume_text:
        raise ValueError("简历内容为空")

    field_options = _build_field_options_prompt()
    system_prompt = (
        "你是简历分析助手。阅读用户的简历内容，提取求职搜索参数。\n"
        "严格按以下字段输出JSON，每个字段的值必须是对应可选值之一：\n"
        f"{field_options}\n\n"
        "规则：\n"
        "- keyword: 根据简历中的技能、经历、求职意向，给出约10个候选搜索关键词（覆盖不同岗位方向），"
        "格式 [{\"word\":\"...\",\"recommended\":true/false},...]，其中2-3个 recommended=true 标记最推荐\n"
        "- city: 从简历中的期望城市/工作地点/当前所在城市推断\n"
        "- salary: 从期望薪资推断，无法确定则留空字符串\n"
        "- experience: 从工作年限/毕业时间推断\n"
        "- degree: 从最高学历推断\n"
        "- industry: 从行业经历/求职意向推断\n"
        "- scale/stage: 从简历中对公司规模的偏好推断，无法确定则留空字符串\n"
        "- 无法从简历确定的字段一律返回空字符串，禁止编造\n"
        "- 代码值必须精确匹配可选值中的代码(如'405'而非'10-20K')\n"
        "- profile_summary: 用2-3句话概括候选人画像，须包含：工作年限/应届与否、"
        "求职类型(全职/实习)、目标岗位方向、核心技能。供后续判断岗位是否匹配时使用，"
        "要具体、贴合简历，不要空话套话"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": resume_text},
    ]

    data = call_ai(endpoint_url, api_key, messages, timeout=timeout, model=model)
    result = _validate_unified_fields(data)
    # profile_summary 是自由文本，不参与枚举校验，验证后附加返回
    summary = data.get("profile_summary", "") if isinstance(data, dict) else ""
    result["profile_summary"] = str(summary).strip()
    return result


def _validate_unified_fields(data) -> dict:
    """Validate AI/user fields against the script's enum maps.

    Supports multi-select: ``city`` is split on Chinese/English commas and
    each city is validated; enum fields accept a single value or a list and
    return a validated list of codes.  Invalid values are dropped so the
    downstream script simply skips them.  ``keyword`` stays a free-text
    string.
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
    cities = [c.strip() for c in city_parts if c.strip() in boss.CITY_MAP]
    result["city"] = cities

    # Enum code fields: accept single value or list, validate each code
    enum_fields = {
        "salary": boss.SALARY_MAP,
        "experience": boss.EXPERIENCE_MAP,
        "degree": boss.DEGREE_MAP,
        "industry": boss.INDUSTRY_MAP,
        "scale": boss.SCALE_MAP,
        "stage": boss.STAGE_MAP,
    }
    for field, mapping in enum_fields.items():
        val = data.get(field, "")
        if isinstance(val, list):
            parts = [str(v).strip() for v in val]
        else:
            parts = [str(val).strip()] if str(val).strip() else []
        valid_codes = set(mapping.values())
        result[field] = [v for v in parts if v in valid_codes]

    return result


# ---------------------------------------------------------------------------
# 两段式 AI 筛选（pipeline 结果精炼）
# ---------------------------------------------------------------------------
# Stage A：字段粗筛。脚本列表结果不含 JD，AI 先按列表字段（薪资/城市/学历等）
#           筛掉"明显"不符合的；学历按常理向下兼容（候选人本科则大专岗也符合）。
# Stage B：JD 精筛。对粗筛留下的岗位批量抓 JD 后，AI 对比候选人画像判 match/not_match。

SCREEN_BATCH_SIZE = 50   # Stage A 每批送 AI 的岗位数（默认值，可被高级设置覆盖）
SCREEN_CONCURRENCY = 1   # Stage A 并发批次数（默认值，可被高级设置覆盖）
MATCH_BATCH_SIZE = 4     # Stage B 每批送 AI 的岗位数（默认值，可被高级设置覆盖）


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
        lines.append(f"候选人画像：{summary}")
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


def screen_jobs(jobs, criteria, endpoint_url, api_key, model="",
                batch_size=None, progress=None,
                concurrency=None):
    """Stage A 粗筛：AI 逐条核对岗位列表字段，移除"明显"不符合的。

    ``jobs``: 脚本抓回的岗位列表（仅列表字段，无 JD）。
    ``criteria``: {"profile_summary": str, "city": [...], "degree": [...], ...}。
    ``concurrency``: 并发批次数，默认 1（串行）。spec 007 ⑥⑦：免费端点实测并发=1；
        换不限流端点可调大。>1 时用线程池并发提交批次，结果按批次顺序合并。

    学历向下兼容、实习/全职不符、城市不符、薪资严重偏低视为明显不符；
    拿不准的一律保留（宁可多留不可错杀）。AI 调用失败的批次全部保留。

    输入格式（spec 007 ⑥）：一行一个紧凑格式 ``i. 标题 | 薪资 | 城市 | 学历 | 规模``，
    省 JSON 包装省 token。输出格式：只列剔除名单
    ``{"dropped":[{"i":3,"reason":...}]}``，未列出的默认保留——防错杀、省输出 token、
    避免 50 条输出截断。

    返回 {"kept": [job_id...], "dropped": [{"job_id","title","reason"}...],
    "verdicts": {job_id: {"verdict","reason"}}}。
    """
    if batch_size is None:
        batch_size = int(_adv_setting("screen_batch_size", SCREEN_BATCH_SIZE))
    if concurrency is None:
        concurrency = int(_adv_setting("screen_concurrency", SCREEN_CONCURRENCY))
    kept, dropped, verdicts = [], [], {}
    if not jobs:
        return {"kept": kept, "dropped": dropped, "verdicts": verdicts}

    criteria_desc = _build_criteria_description(criteria)
    system_prompt = (
        "你是求职初筛助手。根据候选人标准，判断每个岗位是否【明显不符合】候选人条件。\n"
        f"{criteria_desc}\n\n"
        "判断规则（务必按常理，不要死板）：\n"
        "- 学历向下兼容：候选人学历不低于岗位要求即符合（如候选人本科、岗位要求大专，应保留）；"
        "仅当岗位要求学历高于候选人时排除\n"
        "- 岗位标题含'实习'而候选人找全职（或反之），视为明显不符合\n"
        "- 工作城市与期望城市不一致，视为明显不符合\n"
        "- 薪资明显低于期望视为明显不符合；'元/天'的实习计价综合判断\n"
        "- 经验要求：岗位经验段下界高于候选人经验段上界时排除（如岗位5-10年、候选人1-3年）；"
        "岗位下界≤候选人上界时保留（如岗位3-5年、候选人1-3年，给边界机会）\n"
        "- 只排除【明显】不符合的；拿不准一律保留（宁可多留，不可错杀）\n\n"
        "输入格式：每行一个岗位，``序号. 标题 | 薪资 | 城市 | 学历 | 规模``。\n"
        "输出格式：只列出【要剔除】的岗位序号与理由，未列出的默认保留。严格输出JSON：\n"
        '{"dropped":[{"i":3,"reason":"经验5-10年>候选1-3年"},...]}\n'
        "i 为岗位序号。\n"
        "reason 必须具体，格式为「字段名+岗位值+比较符+候选人值」，禁止笼统表述。\n"
        "示例：\n"
        '  经验不符：reason="经验5-10年>候选1-3年"\n'
        '  学历不符：reason="学历硕士>候选本科"\n'
        '  城市不符：reason="城市深圳≠期望广州"\n'
        '  实习不符：reason="实习岗≠全职"\n'
        '  薪资不符：reason="薪资3-5K<期望8-10K"\n'
        "禁止使用「经验过高」「不符合」「不匹配」等笼统词汇。\n"
        "reason 限25字内。若无任何剔除，输出 {\"dropped\":[]}。"
    )

    # 切批
    batches = []
    for start in range(0, len(jobs), batch_size):
        batches.append(jobs[start:start + batch_size])

    def _process_batch(batch):
        """处理单个批次，返回 (batch_dropped, batch_verdicts)。

        batch_dropped: [{"job_id","title","reason"}...]
        batch_verdicts: {job_id: {"verdict","reason"}}
        默认全保留；AI 返回的 dropped 名单扣掉。
        """
        # 紧凑文本输入：i. 标题 | 薪资 | 城市 | 学历 | 规模
        lines = []
        for idx, job in enumerate(batch):
            parts = [
                job.get("title", ""),
                job.get("salary", ""),
                job.get("location", ""),
                job.get("job_labels", "") or "",  # 学历/经验标签
                job.get("company_scale", "") or "",
            ]
            lines.append(f"{idx}. " + " | ".join(str(p) for p in parts if p))
        user_content = "\n".join(lines)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            data = call_ai(endpoint_url, api_key, messages, model=model)
            dropped_list = data.get("dropped", []) if isinstance(data, dict) else []
            by_i = {r.get("i"): r for r in dropped_list if isinstance(r, dict)}
        except AISecurityError:
            by_i = {}  # 调用失败：该批全部保留，防错杀

        b_dropped, b_verdicts = [], {}
        for idx, job in enumerate(batch):
            jid = str(job.get("job_id", ""))
            r = by_i.get(idx)
            if r:
                reason = str(r.get("reason", "")).strip()
                b_dropped.append({
                    "job_id": jid,
                    "title": job.get("title", ""),
                    "reason": reason,
                    "canonical_url": job.get("canonical_url", "") or job.get("source_url", "") or job.get("url", "") or "",
                })
                b_verdicts[jid] = {"verdict": "dropped", "reason": reason}
            else:
                b_verdicts[jid] = {"verdict": "kept", "reason": ""}
        return b_dropped, b_verdicts

    if concurrency <= 1:
        # 串行（默认，免费端点并发=1）
        processed = 0
        for batch in batches:
            b_dropped, b_verdicts = _process_batch(batch)
            dropped.extend(b_dropped)
            verdicts.update(b_verdicts)
            processed += len(batch)
            if progress is not None:
                try:
                    progress(min(processed, len(jobs)), len(jobs))
                except Exception:
                    pass
    else:
        # 并发（换不限流端点时启用）
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        lock = threading.Lock()
        processed_counter = [0]

        def _safe_progress(n_done):
            if progress is None:
                return
            with lock:
                processed_counter[0] += n_done
                cur = min(processed_counter[0], len(jobs))
            try:
                progress(cur, len(jobs))
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_process_batch, batch): batch for batch in batches}
            for fut in as_completed(futures):
                b_dropped, b_verdicts = fut.result()
                with lock:
                    dropped.extend(b_dropped)
                    verdicts.update(b_verdicts)
                _safe_progress(len(futures[fut]))

    kept = [str(j.get("job_id", "")) for j in jobs
            if str(j.get("job_id", "")) not in {d["job_id"] for d in dropped}]
    return {"kept": kept, "dropped": dropped, "verdicts": verdicts}


def match_jds(jobs_with_jd, profile_summary, endpoint_url, api_key, model="",
              batch_size=None, progress=None):
    """Stage B 精筛：AI 逐条对比岗位 JD 与候选人画像，判 match/not_match。

    ``jobs_with_jd``: [{"job_id","title","salary","location","jd"}...]。
    返回 {"verdicts": {job_id: {"verdict": "match"/"not_match", "reason"}}}。
    AI 调用失败或漏回结果的岗位标记为 uncertain，保留给用户人工确认，
    不能把未完成的判定伪装成已匹配。
    """
    if batch_size is None:
        batch_size = int(_adv_setting("match_batch_size", MATCH_BATCH_SIZE))
    verdicts = {}
    if not jobs_with_jd:
        return {"verdicts": verdicts}
    summary = (profile_summary or "").strip() or "（无候选人画像）"
    system_prompt = (
        "你是求职匹配度评估助手。根据候选人画像，判断每个岗位的JD工作内容是否适合候选人。\n"
        f"候选人画像：{summary}\n\n"
        "判断要点：岗位职责与候选人技能/方向的契合度；岗位性质(全职/实习)与候选人诉求是否一致。\n"
        "match 只看核心能力匹配（岗位职责与技能/方向契合）；"
        "JD 中'优先/加分/plus/熟悉'类软性要求（如行业经验、英语等级、证书）不得影响 match，"
        "应写入 caveats 数组（每项一句话，如'优先英语六级，候选人未提供'）。"
        "只有 JD 明确标注'必须/要求/need'的硬性项且候选人未满足时，才 match=false。\n"
        "行业经验不足不得作为 match=false 的理由，除非 JD 明确'必须有X行业经验'。\n"
        "对每个岗位输出判定。严格输出JSON：\n"
        '{"results":[{"i":0,"match":true,"reason":"一句话理由","caveats":["软性提醒"]},...]}\n'
        "i 为岗位序号；match=true 适合，false 不适合；reason 简短（20字内）；caveats 可为空数组。"
    )
    for start in range(0, len(jobs_with_jd), batch_size):
        batch = jobs_with_jd[start:start + batch_size]
        batch_desc = [
            {
                "i": idx,
                "title": job.get("title", ""),
                "salary": job.get("salary", ""),
                "location": job.get("location", ""),
                "jd": str(job.get("jd", ""))[:1500],
            }
            for idx, job in enumerate(batch)
        ]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(batch_desc, ensure_ascii=False)},
        ]
        try:
            data = call_ai(endpoint_url, api_key, messages, model=model)
            results = data.get("results", []) if isinstance(data, dict) else []
            by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
        except AISecurityError:
            by_i = None
        for idx, job in enumerate(batch):
            jid = str(job.get("job_id", ""))
            if by_i is None:
                verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": "AI 精筛失败，待人工确认",
                }
                continue
            r = by_i.get(idx)
            if not isinstance(r, dict) or not isinstance(r.get("match"), bool):
                verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": "AI 未返回判定，待人工确认",
                }
                continue
            match = r["match"]
            reason = str(r.get("reason", "")).strip()
            caveats = [str(c).strip() for c in r.get("caveats") or [] if isinstance(c, str) and c.strip()]
            verdicts[jid] = {
                "verdict": "match" if match else "not_match",
                "reason": reason,
                "caveats": caveats,
            }
        if progress is not None:
            try:
                progress(min(start + batch_size, len(jobs_with_jd)), len(jobs_with_jd))
            except Exception:
                pass
    return {"verdicts": verdicts}


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


# ---------------------------------------------------------------------------
# T099: DiscoveryAIProvider — feature 004 real AI provider
# ---------------------------------------------------------------------------

# Feature-safe error code mapping (ai_* prefix per openapi.yaml Error schema).
# Low-level call_ai raises AISecurityError with codes like "timeout";
# DiscoveryAIProvider re-raises with feature-safe codes like "ai_timeout".
_PROVIDER_ERROR_MAP = {
    ERROR_TIMEOUT: "ai_timeout",
    ERROR_AUTH: "ai_auth_failed",
    ERROR_NETWORK: "ai_network_error",
    ERROR_INVALID: "ai_invalid_output",
}


def cleanup_candidate_analysis_response(response):
    """Public, testable deterministic cleanup for candidate-v3 responses."""
    return DiscoveryAIProvider._clean_candidate_response(response)


def _map_provider_error(exc: AISecurityError) -> AISecurityError:
    """Map a low-level AISecurityError to a feature-safe error code."""
    mapped = _PROVIDER_ERROR_MAP.get(exc.error_code, exc.error_code)
    return AISecurityError(mapped)


class DiscoveryAIProvider:
    """Feature 004 AI provider for candidate analysis and job assessment.

    Holds only endpoint/model/api_key. Does not read or write TaskStore.
    Maps low-level AI errors to feature-safe error codes (ai_* prefix).
    Allows one corrective request for a normalized partial/manual response.
    Never persists or returns raw model responses, prompts or API keys.
    """

    def __init__(self, endpoint: str, model: str, api_key: str):
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key

    # -- Candidate analysis v3 --

    def analyze(self, *, resume_text: str, contract_version: str = "v3") -> dict:
        """Return a normalized v3 candidate analysis.

        Unparseable output and transport failures are terminal. A parseable
        partial/manual result gets one corrective request containing only safe
        warning codes and structural paths. Backend-owned locator, excerpt and
        quality fields are derived by the normalizer; raw provider output is
        never returned or persisted.
        """
        if contract_version != "v3":
            raise ValueError(f"unsupported candidate analysis contract: {contract_version}")

        messages = self._build_analyze_messages(resume_text)
        original = None
        for attempt in range(2):
            try:
                response = call_ai(
                    self.endpoint, self.api_key, messages, model=self.model,
                    timeout=120,
                )
            except AISecurityError as exc:
                raise _map_provider_error(exc) from None
            try:
                parsed = self._clean_candidate_response(response)
            except (ValueError, TypeError, json.JSONDecodeError):
                raise AISecurityError("ai_invalid_output") from None
            normalized = normalize_candidate_analysis(parsed, resume_text)
            if original is None:
                original = normalized
            if normalized["quality"]["status"] == "complete":
                return normalized
            if attempt == 0:
                warnings = normalized["quality"]["warnings"]
                corrective = messages + [{"role":"assistant", "content": json.dumps(parsed, ensure_ascii=False)}, {"role":"user", "content": "仅修正以下契约问题并返回JSON：" + json.dumps([{"code": w["code"], "path": w["path"]} for w in warnings], ensure_ascii=False)}]
                messages = corrective
                continue
            return normalized if self._quality_score(normalized) > self._quality_score(original) else original
        return original or build_empty_candidate_analysis()

    @staticmethod
    def _quality_score(result):
        q = result.get("quality", {})
        rank = {"manual_required": 0, "partial": 1, "complete": 2}
        useful = (len(result.get("evidence", [])) + len(result.get("facts", [])) + len(result.get("directions", [])) +
                  len(result.get("summary", {}).get("strengths", [])) +
                  bool(result.get("summary", {}).get("headline")))
        return (rank.get(q.get("status"), 0), useful)

    @staticmethod
    def _clean_candidate_response(response):
        if isinstance(response, dict):
            obj = response
        elif isinstance(response, str):
            text = response.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if (len(lines) < 3 or lines[0].strip() not in ("```", "```json")
                        or lines[-1].strip() != "```"):
                    raise ValueError("fence")
                if any(line.strip().startswith("```") for line in lines[1:-1]):
                    raise ValueError("fence")
                text = "\n".join(lines[1:-1]).strip()
            obj = json.loads(text)
        else:
            raise ValueError("json")
        if not isinstance(obj, dict): raise ValueError("object")
        envelopes = [key for key in ("data", "result") if key in obj]
        if len(envelopes) > 1:
            raise ValueError("envelope")
        if envelopes:
            key = envelopes[0]
            if len(obj) != 1 or not isinstance(obj[key], dict): raise ValueError("envelope")
            obj = obj[key]
            if any(key in obj for key in ("data", "result")):
                raise ValueError("envelope")
        known = set(CANDIDATE_ANALYSIS_V3_CONTRACT.get("top", {}))
        if not any(key in obj for key in known):
            raise ValueError("unknown")
        return obj

    @staticmethod
    def _enrich_v2_locators(response: dict, canonical_text: str) -> dict | None:
        """Enrich a v2 response with program-generated locators.

        Returns a deep-copied enriched dict, or ``None`` if any quote cannot
        be resolved. Never mutates the original response.
        """
        try:
            result = copy.deepcopy(response)
        except Exception:
            return None
        evidence_list = result.get("evidence")
        if not isinstance(evidence_list, list):
            return None
        for ev in evidence_list:
            if not isinstance(ev, dict):
                return None
            quote = ev.get("source_quote")
            if not isinstance(quote, str) or not quote:
                return None
            try:
                locator = resolve_evidence_quote(quote, canonical_text)
            except ValueError:
                return None
            # Overwrite any model-provided locator with program locator
            ev["source_locator"] = locator
            # Derive safe_excerpt locally (redacted)
            ev["safe_excerpt"] = redact_pii(quote)
        return result

    # -- Job-direction assessment v1 (T114) --

    def assess_job(
        self,
        *,
        candidate_summary: dict | None = None,
        direction: dict | None = None,
        evidence: list | None = None,
        job_snapshot: dict | None = None,
        candidate_profile: dict | None = None,
        directions: list | None = None,
        contract_version: str = "v1",
    ) -> dict:
        """Call AI to assess one job against one direction.

        Constructs the v1 assessment prompt with sanitized input, calls
        call_ai, and maps errors to feature-safe codes.

        T114: 允许一次纠正性重试（结构性失败时）。第二次仍失败抛
        AISecurityError("ai_invalid_output")。
        """
        if contract_version == "job_assessment_v2":
            if not isinstance(directions, list) or not 1 <= len(directions) <= 2:
                raise ValueError("job-assessment v2 requires one or two directions")
            return self._assess_job_v2(candidate_profile, directions, job_snapshot)
        if contract_version != "v1":
            raise ValueError(f"unsupported job assessment contract: {contract_version}")
        messages = self._build_assess_messages(
            candidate_summary, direction, evidence, job_snapshot,
        )
        for attempt in range(2):
            try:
                response = call_ai(
                    self.endpoint, self.api_key, messages, model=self.model,
                )
            except AISecurityError as exc:
                raise _map_provider_error(exc) from None
            if self._is_structurally_valid_assessment(response):
                return response
            if attempt == 0:
                continue
            raise AISecurityError("ai_invalid_output") from None
        raise AISecurityError("ai_invalid_output") from None

    @staticmethod
    def _is_structurally_valid_assessment(response) -> bool:
        """Return True iff response has the minimal v1 assessment structure."""
        if not isinstance(response, dict):
            return False
        dims = response.get("dimensions")
        if not isinstance(dims, dict):
            return False
        required = {"direction_alignment", "skill_coverage", "experience_match", "industry_relevance"}
        if not required.issubset(dims.keys()):
            return False
        for name in required:
            item = dims[name]
            if not isinstance(item, dict):
                return False
            if "score" not in item:
                return False
        if "match_score" not in response:
            return False
        if "confidence" not in response:
            return False
        if "proposed_band" not in response:
            return False
        return True

    # -- Job-direction assessment v2 (T056) --

    _V2_DIMS = (
        "direction_alignment", "skill_coverage",
        "experience_match", "industry_relevance",
    )
    _V2_BANDS = frozenset({"high", "adjacent", "growth", "unsuitable", "uncertain"})

    def _assess_job_v2(self, candidate_profile, directions, job_snapshot) -> dict:
        """Run one job-assessment v2 request chain with at most one correction.

        One request evaluates one job and up to two relevant directions. Each
        direction is validated independently; an invalid direction is
        quarantined without polluting a valid sibling. If the envelope is
        parseable and only some directions are invalid, at most one corrective
        call targets just the invalid direction ids with safe validation paths.
        Raw provider output is validated in memory and discarded.
        """
        direction_scope = {}
        for d in directions or []:
            if isinstance(d, dict) and d.get("id"):
                direction_scope[d["id"]] = {
                    "fact_refs": set(d.get("fact_refs") or []),
                    "evidence_refs": set(d.get("evidence_refs") or []),
                }
        job_fields = set()
        if isinstance(job_snapshot, dict):
            fields = job_snapshot.get("fields")
            if isinstance(fields, dict):
                job_fields = set(fields.keys())

        messages = self._build_assess_v2_messages(candidate_profile, directions, job_snapshot)
        provider_call_count = 0
        valid: dict = {}
        quarantined: dict = {}

        for attempt in range(2):
            try:
                response = call_ai(
                    self.endpoint, self.api_key, messages, model=self.model,
                    timeout=120,
                )
                provider_call_count += 1
            except AISecurityError as exc:
                raise _map_provider_error(exc) from None
            try:
                parsed = self._clean_assessment_v2_response(response)
            except (ValueError, TypeError, json.JSONDecodeError):
                raise AISecurityError("ai_invalid_output") from None

            seen_ids = set()
            for assessment in parsed.get("assessments", []):
                if not isinstance(assessment, dict):
                    continue
                direction_id = assessment.get("direction_id")
                if direction_id not in direction_scope or direction_id in seen_ids:
                    continue
                seen_ids.add(direction_id)
                ok, reason, cleaned = self._validate_assessment_v2(
                    assessment, direction_scope[direction_id], job_fields,
                )
                if ok:
                    valid[direction_id] = cleaned
                    quarantined.pop(direction_id, None)
                elif direction_id not in valid:
                    quarantined[direction_id] = reason

            if set(direction_scope) <= set(valid):
                break
            if attempt == 0:
                invalid_ids = [d for d in direction_scope if d not in valid]
                if not invalid_ids:
                    break
                safe_warnings = [
                    {"direction_id": d, "code": quarantined.get(d, "invalid"),
                     "path": f"assessments[{d}]"}
                    for d in invalid_ids
                ]
                prior = {
                    "contract_version": "job_assessment_v2",
                    "assessments": [
                        a for a in parsed.get("assessments", [])
                        if isinstance(a, dict) and a.get("direction_id") in invalid_ids
                    ],
                }
                messages = messages + [
                    {"role": "assistant", "content": json.dumps(prior, ensure_ascii=False)},
                    {"role": "user", "content": (
                        "仅修正以下无效方向并返回完整 job_assessment_v2 JSON："
                        + json.dumps(safe_warnings, ensure_ascii=False)
                    )},
                ]
                continue
            break

        assessments = [valid[d] for d in valid]
        quarantined_list = [{"direction_id": d, "reason": r} for d, r in quarantined.items()]
        total = len(direction_scope)
        n_valid = len(valid)
        if n_valid == total:
            status = "complete"
        elif n_valid > 0:
            status = "partial"
        else:
            status = "manual_required"
        warnings = [{"code": r, "direction_id": d} for d, r in quarantined.items()]
        return {
            "contract_version": "job_assessment_v2",
            "assessments": assessments,
            "quarantined": quarantined_list,
            "quality": {"status": status, "warnings": warnings},
            "metrics": {"provider_call_count": provider_call_count},
        }

    @classmethod
    def _validate_assessment_v2(cls, assessment, scope, job_fields):
        """Validate one direction's assessment.

        Returns ``(ok, reason, cleaned)``. ``ok`` is True only when all four
        dimensions exist with integer 0-100 scores, candidate refs stay within
        the direction's supplied refs, and job refs name supplied snapshot
        fields. Positive items lacking bilateral (candidate-side and job-side)
        evidence are dropped, not fatal.
        """
        dims = assessment.get("dimensions")
        if not isinstance(dims, dict):
            return False, "missing_dimensions", None
        cleaned_dims = {}
        for name in cls._V2_DIMS:
            item = dims.get(name)
            if not isinstance(item, dict):
                return False, f"missing_dimension:{name}", None
            score = item.get("score")
            if not cls._is_int_score(score):
                return False, f"non_integer_score:{name}", None
            fact_refs = item.get("candidate_fact_refs") or []
            evidence_refs = item.get("candidate_evidence_refs") or []
            job_refs = item.get("job_evidence_refs") or []
            if any(ref not in scope["fact_refs"] for ref in fact_refs):
                return False, f"cross_direction_fact_ref:{name}", None
            if any(ref not in scope["evidence_refs"] for ref in evidence_refs):
                return False, f"cross_direction_evidence_ref:{name}", None
            if job_fields and any(ref not in job_fields for ref in job_refs):
                return False, f"unknown_job_ref:{name}", None
            cleaned_dims[name] = {
                "score": int(score),
                "candidate_fact_refs": list(fact_refs),
                "candidate_evidence_refs": list(evidence_refs),
                "job_evidence_refs": list(job_refs),
            }
        for key in ("match_score", "confidence"):
            if not cls._is_int_score(assessment.get(key)):
                return False, f"non_integer_{key}", None

        positive = []
        for item in assessment.get("positive") or []:
            if not isinstance(item, dict):
                continue
            cand = item.get("candidate_evidence_refs") or []
            job = item.get("job_evidence_refs") or []
            if not cand or not job:
                continue  # positive must carry bilateral evidence
            if any(ref not in scope["evidence_refs"] for ref in cand):
                continue
            if job_fields and any(ref not in job_fields for ref in job):
                continue
            positive.append({
                "text": str(item.get("text", "")),
                "candidate_fact_refs": list(item.get("candidate_fact_refs") or []),
                "candidate_evidence_refs": list(cand),
                "job_evidence_refs": list(job),
            })

        gaps = []
        for item in assessment.get("gaps") or []:
            if not isinstance(item, dict):
                continue
            fact_refs = item.get("candidate_fact_refs") or []
            job_refs = item.get("job_evidence_refs") or []
            if any(ref not in scope["fact_refs"] for ref in fact_refs):
                continue
            if job_fields and any(ref not in job_fields for ref in job_refs):
                continue
            gaps.append({
                "text": str(item.get("text", "")),
                "candidate_fact_refs": list(fact_refs),
                "job_evidence_refs": list(job_refs),
            })

        band = assessment.get("proposed_band")
        if band not in cls._V2_BANDS:
            band = "uncertain"

        caveats = []
        for item in assessment.get("caveats") or []:
            if isinstance(item, str) and item.strip():
                caveats.append(item.strip())

        cleaned = {
            "direction_id": assessment.get("direction_id"),
            "dimensions": cleaned_dims,
            "match_score": int(assessment["match_score"]),
            "confidence": int(assessment["confidence"]),
            "positive": positive,
            "gaps": gaps,
            "caveats": caveats,
            "proposed_band": band,
        }
        return True, "", cleaned

    @staticmethod
    def _is_int_score(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100

    @staticmethod
    def _clean_assessment_v2_response(response):
        if isinstance(response, dict):
            obj = response
        elif isinstance(response, str):
            text = response.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if (len(lines) < 3 or lines[0].strip() not in ("```", "```json")
                        or lines[-1].strip() != "```"):
                    raise ValueError("fence")
                text = "\n".join(lines[1:-1]).strip()
            obj = json.loads(text)
        else:
            raise ValueError("json")
        if not isinstance(obj, dict):
            raise ValueError("object")
        envelopes = [key for key in ("data", "result") if key in obj]
        if len(envelopes) > 1:
            raise ValueError("envelope")
        if envelopes:
            key = envelopes[0]
            if len(obj) != 1 or not isinstance(obj[key], dict):
                raise ValueError("envelope")
            obj = obj[key]
        if not isinstance(obj.get("assessments"), list):
            raise ValueError("assessments")
        return obj

    @staticmethod
    def _build_assess_v2_messages(candidate_profile, directions, job_snapshot):
        profile = candidate_profile or {}
        allowed_facts: set = set()
        allowed_evidence: set = set()
        for d in directions or []:
            if isinstance(d, dict):
                allowed_facts.update(d.get("fact_refs") or [])
                allowed_evidence.update(d.get("evidence_refs") or [])
        facts = [
            f for f in profile.get("facts", [])
            if isinstance(f, dict) and f.get("client_ref") in allowed_facts
        ]
        evidence = [
            e for e in profile.get("evidence", [])
            if isinstance(e, dict) and e.get("client_ref") in allowed_evidence
        ]
        payload = {
            "contract_version": "job_assessment_v2",
            "candidate": {
                "profile_version_id": profile.get("profile_version_id"),
                "summary": profile.get("summary", {}),
                "facts": facts,
                "evidence": evidence,
            },
            "job": job_snapshot or {},
            "directions": directions or [],
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是岗位评估助手。一次只评估一个岗位的最多两个相关方向，按 direction_id 分隔返回 "
                    "job_assessment_v2 JSON。结构：assessments[{direction_id, "
                    "dimensions{direction_alignment,skill_coverage,experience_match,industry_relevance}"
                    "{score,candidate_fact_refs,candidate_evidence_refs,job_evidence_refs}, "
                    "match_score, confidence, positive[{text,candidate_fact_refs,"
                    "candidate_evidence_refs,job_evidence_refs}], "
                    "gaps[{text,candidate_fact_refs,job_evidence_refs}], "
                    "caveats[string], proposed_band}]。"
                    "score/match_score/confidence 必须是 0-100 的整数。"
                    "candidate_fact_refs/candidate_evidence_refs 只能引用该方向 supplied 的 ID；"
                    "job_evidence_refs 只能命名岗位字段。positive 必须同时含候选侧与岗位侧证据。"
                    "proposed_band 只能是 high/adjacent/growth/unsuitable/uncertain，仅为建议。"
                    "gaps 只能放 JD 中明确标注为'必须/要求/need'的硬性项且候选人未满足；"
                    "JD 中'优先/加分/plus/熟悉'类软性要求不得放入 gaps，也不得因此降低 match_score 或 proposed_band，"
                    "应写入 caveats 数组（每项一句话，如'优先英语六级，候选人未提供'）。"
                    "行业经验不足不得作为降级理由，除非 JD 明确'必须有X行业经验'；'有X行业经验优先'类一律进 caveats。"
                    "caveats 不影响 proposed_band。"
                    "禁止输出原始响应、凭据、locator 或完整简历文本。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    # -- Prompt construction (T106/T114 will refine) --

    @staticmethod
    def _build_analyze_messages(resume_text: str) -> list:
        # Provider-owned canonical schema; quality and generated locator fields
        # are intentionally omitted from this section.
        contract = CANDIDATE_ANALYSIS_V3_CONTRACT
        provider_contract = {
            "version": contract["version"],
            "top": {
                key: copy.deepcopy(spec)
                for key, spec in contract["top"].items()
                if spec.get("provider_owned", True)
            },
            "summary": copy.deepcopy(contract["summary"]),
            "evidence": {
                key: copy.deepcopy(spec)
                for key, spec in contract["evidence"].items()
                if spec.get("provider_owned", True)
            },
            "unknown": copy.deepcopy(contract["unknown"]),
            "direction": copy.deepcopy(contract["direction"]),
        }
        schema = json.dumps(provider_contract, ensure_ascii=False, default=list)
        example = json.dumps({k: v for k, v in build_empty_candidate_analysis().items() if k != "quality"}, ensure_ascii=False)
        return [
            {
                "role": "system",
                "content": (
                    "你是候选人分析助手。严格按以下v3契约返回JSON。仅输出provider拥有字段；quality由程序生成，禁止输出source_locator和safe_excerpt。字段缺失使用契约typed-empty。CANONICAL_CANDIDATE_V3_SCHEMA_BEGIN\n" + schema + "\nCANONICAL_CANDIDATE_V3_SCHEMA_END\n示例:" + example + "。"
                ),
            },
            {"role": "user", "content": resume_text},
        ]

    @staticmethod
    def _build_assess_messages(candidate_summary, direction, evidence, job_snapshot):
        payload = {
            "candidate_summary": candidate_summary,
            "direction": direction,
            "evidence": evidence,
            "job": job_snapshot,
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是岗位评估助手。根据候选人证据与岗位详情返回JSON："
                    "dimensions{direction_alignment,skill_coverage,"
                    "experience_match,industry_relevance}"
                    "{score,candidate_evidence_refs,job_evidence_refs}，"
                    "match_score,confidence,gaps[{text,job_evidence_refs}],"
                    "caveats[string],proposed_band。score、match_score、confidence 必须是 0-100 的整数。"
                    "proposed_band 只能是 high/adjacent/growth/unsuitable/uncertain。"
                    "证据引用必须使用输入中已有的 ID；没有证据时返回空数组[]，禁止创造 ID。"
                    "岗位级别必须与候选人经验匹配；实习/校招/应届岗位与多年全职经历明显冲突时，"
                    "不得给出 high 或 adjacent，应降低 experience_match 和 match_score。"
                    "gaps 只能放 JD 中明确标注为'必须/要求/need'的硬性项且候选人未满足；"
                    "JD 中'优先/加分/plus/熟悉'类软性要求不得放入 gaps，也不得因此降低 match_score 或 proposed_band，"
                    "应写入 caveats 数组（每项一句话，如'优先英语六级，候选人未提供'）。"
                    "行业经验不足不得作为降级理由，除非 JD 明确'必须有X行业经验'；'有X行业经验优先'类一律进 caveats。"
                    "caveats 不影响 proposed_band。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]

    # -- Minimal structural check (T105/T107 will strengthen) --

    @staticmethod
    def _is_structurally_valid_v2(response) -> bool:
        """Return True iff response has the minimal v2 top-level structure."""
        if not isinstance(response, dict):
            return False
        if not isinstance(response.get("summary"), dict):
            return False
        if not isinstance(response.get("evidence"), list):
            return False
        if not isinstance(response.get("unknowns"), list):
            return False
        if not isinstance(response.get("directions"), list):
            return False
        return True
