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
STREAM_IDLE_TIMEOUT = 20  # 流式模式下，连续 N 秒没收到任何数据即判定连接已死
STREAM_TOTAL_TIMEOUT = 60  # 流式模式下，从请求发出算起的总时长上限（防慢吐丝卡死）
RANK_BATCH_SIZE = 10

# Safe error classifications returned to callers.  Never include raw
# exception text, API keys or response bodies.
ERROR_TIMEOUT = "timeout"
ERROR_AUTH = "auth_failed"
ERROR_NETWORK = "network_error"
ERROR_INVALID = "invalid_response"
ERROR_RATE_LIMIT = "rate_limited"
ERROR_TRUNCATED = "truncated"
ERROR_QUOTA_EXHAUSTED = "quota_exhausted"
ERROR_SERVER = "server_error"

# Free-tier endpoints throttle aggressively (HTTP 429).  Retry a few times
# with increasing backoff so a transient limit doesn't fail the whole step.
RATE_LIMIT_ATTEMPTS = 2
RATE_LIMIT_BACKOFF_SECONDS = (5,)

# 可重试的传输层故障：429 限流 + 5xx 服务端临时故障；超时/连接错误也可重试。
# 不同故障不同退避：429 用上面的长退避，5xx 短退避，超时/网络中等退避。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
SERVER_ERROR_BACKOFF_SECONDS = (3,)
NETWORK_BACKOFF_SECONDS = (3,)


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


class AICheckpointError(RuntimeError):
    """Raised when a completed AI batch cannot be durably checkpointed."""


# 面向用户的错误文案（端点用它替代裸 error_code，给出可操作的提示）
ERROR_USER_MESSAGES = {
    ERROR_TIMEOUT: "AI 响应超时，请稍后重试",
    ERROR_AUTH: "API 密钥无效或已过期，请检查 AI 设置",
    ERROR_NETWORK: "无法连接 AI 服务，请检查网络与地址配置",
    ERROR_INVALID: "AI 返回了无法解析的内容，请重试",
    ERROR_RATE_LIMIT: "AI 服务限流（免费额度），请稍候再试",
    ERROR_TRUNCATED: "AI 返回内容被截断，请重试（可减小单批数量）",
    ERROR_QUOTA_EXHAUSTED: "AI 额度已用完，请明天再试或更换 API 密钥",
    ERROR_SERVER: "AI 服务暂时不可用，请稍后重试",
}


def user_facing_error(error_code: str) -> str:
    """Return a user-friendly Chinese message for a safe error code."""
    return ERROR_USER_MESSAGES.get(error_code, f"AI 调用失败（{error_code}）")


# 切片6：systemic 错误码集合（命中即应暂停整任务，FR-020/SC-006/SC-007）
# 与 pipeline_exec.ERROR_TAXONOMY 中 impact=systemic 的 AI 类码对齐
SYSTEMIC_AI_ERROR_CODES = frozenset({
    ERROR_RATE_LIMIT,        # ai_rate_limited
    ERROR_QUOTA_EXHAUSTED,   # ai_quota_exhausted
    ERROR_AUTH,              # ai_key_invalid
    ERROR_NETWORK,           # ai_network_error
    ERROR_TIMEOUT,           # ai_network_error（归一）
    ERROR_SERVER,            # ai_network_error（归一）
})


def map_ai_error_to_block_code(error_code: str) -> str:
    """把 ai.py 的内部错误码映射到 pipeline_exec.ERROR_TAXONOMY 的阻断码。

    用于 _run_ai_screen_task 暂停时写入 screening_runs.error_code。
    非 systemic 错误返回空串。
    """
    if error_code == ERROR_RATE_LIMIT:
        return "ai_rate_limited"
    if error_code == ERROR_QUOTA_EXHAUSTED:
        return "ai_quota_exhausted"
    if error_code == ERROR_AUTH:
        return "ai_key_invalid"
    if error_code in (ERROR_NETWORK, ERROR_TIMEOUT, ERROR_SERVER):
        return "ai_network_error"
    return ""


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
    """Ping the chat completions endpoint to verify connectivity and auth.

    Sends a minimal request and only checks that the endpoint responds with a
    non-empty assistant reply.  This intentionally avoids the heavy candidate-v3
    contract validation and the retry/backoff logic in ``call_ai`` so that the
    "测试连接" button returns in seconds rather than tens of seconds.
    """
    url = _chat_completions_url(endpoint_url)
    if not url:
        return {"ok": False, "transport": "failed", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_NETWORK]}

    payload = {
        "model": model or "auto",
        "messages": [{"role": "user", "content": "reply with exactly: pong"}],
        "temperature": 0.3,
        "max_tokens": 24,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=CONNECTION_TIMEOUT)
    except requests.Timeout:
        return {"ok": False, "transport": "failed", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_TIMEOUT]}
    except requests.RequestException:
        return {"ok": False, "transport": "failed", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_NETWORK]}
    except Exception:
        return {"ok": False, "transport": "failed", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_INVALID]}

    if response.status_code in (401, 403):
        return {"ok": False, "transport": "failed", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_AUTH]}
    if response.status_code >= 500:
        return {"ok": False, "transport": "failed", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_SERVER]}
    if response.status_code >= 400:
        return {"ok": False, "transport": "failed", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_INVALID]}

    try:
        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        # Reasoning models (DeepSeek, GLM-5.2, etc.) may put tokens into
        # reasoning_content while leaving content empty when max_tokens is tight.
        # We accept either field as proof the chat completions pipeline works.
        content = str(message.get("content") or "").strip()
        reasoning = str(message.get("reasoning_content") or "").strip()
        if not content and not reasoning:
            raise ValueError("empty reply")
    except Exception:
        return {"ok": False, "transport": "ready", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_INVALID]}

    return {"ok": True, "transport": "ready", "generation": "ready",
            "candidate_contract": "manual_required", "warning_codes": []}


# ---------------------------------------------------------------------------
# AI call
# ---------------------------------------------------------------------------

def _is_quota_exhausted_response(response) -> bool:
    """429 响应体里是否配额耗尽特征（只提取特征字段，不泄露响应体）。

    额度耗尽与瞬时限流共用 429，但前者救不活：退避重试只是白白空撞，
    必须立刻停。
    """
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if not isinstance(error, dict):
        return False
    error_type = str(error.get("type", ""))
    error_code = str(error.get("code", ""))
    return "insufficient_quota" in (error_type, error_code)


def _looks_truncated(content) -> bool:
    """JSON 文本尾部不闭合的启发式判断（传输层截断的典型表现）。

    主判定是 finish_reason=="length"；本函数兜底那些不返回
    finish_reason 的端点。误判代价低（错误码从 invalid 变 truncated，
    文案更准确），无安全影响。
    """
    if not isinstance(content, str):
        return False
    tail = content.rstrip()
    if not tail:
        return False
    if tail.endswith((",", ":")):
        return True
    opens = tail.count("{") + tail.count("[")
    closes = tail.count("}") + tail.count("]")
    return opens > closes


def _read_stream(response) -> tuple[str, str]:
    """从流式响应中逐块读取，拼接完整 content 并提取 finish_reason。

    双重超时保护：
    1. 空闲超时（STREAM_IDLE_TIMEOUT）：由 requests 的 read timeout 驱动，
       连续 20 秒没收到任何字节 → 底层 socket 抛 Timeout。
    2. 总时长超时（STREAM_TOTAL_TIMEOUT）：首字到了但后续出字极慢，
       从请求发出算起超过 60 秒仍未收完 → 主动抛 Timeout。

    两种超时都由外层 call_ai 的重试逻辑接住（ERROR_TIMEOUT，最多重试 2 次）。

    返回 (content, finish_reason)。
    """
    t0 = time.time()
    content_parts: list[str] = []
    finish_reason = ""
    # 强制 UTF-8 解码：AI 端点返回 text/event-stream 时通常不带 charset，
    # requests 默认回退 Latin-1 导致中文乱码。
    response.encoding = "utf-8"
    for line in response.iter_lines(decode_unicode=True):
        if time.time() - t0 > STREAM_TOTAL_TIMEOUT:
            raise requests.Timeout(
                f"流式响应总时长超过 {STREAM_TOTAL_TIMEOUT}s 上限"
            )
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except (json.JSONDecodeError, ValueError):
            continue
        choices = chunk.get("choices")
        if not choices or not isinstance(choices, list):
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        text = delta.get("content") or ""
        if text:
            content_parts.append(text)
        fr = choice.get("finish_reason")
        if fr:
            finish_reason = str(fr)
    return "".join(content_parts), finish_reason


def call_ai(endpoint_url: str, api_key: str, messages: list, timeout: int = DEFAULT_TIMEOUT,
            temperature: float = 0.3, model: str = "") -> dict:
    """Call an OpenAI-compatible chat completions endpoint and return parsed JSON.

    使用流式（stream=True）模式：AI 每生成几个字就推送一小段，本地实时接收。
    如果连续 STREAM_IDLE_TIMEOUT（20）秒没收到任何数据，判定连接已死，
    立即超时重试——不再傻等 60 秒。

    Raises :class:`AISecurityError` with a safe error_code on any failure.
    The exception never contains the API key, request body or raw response,
    and the original exception is suppressed so tracebacks stay clean.

    重试策略：429 限流 / 5xx 服务端故障 / 超时 / 连接错误都会退避重试；
    退避等待累计不超过单次 timeout（只计 sleep 等待、不计请求耗时——
    请求耗时有单次 timeout 兜底，否则慢超时一次就占满预算永远重试不了）。
    配额耗尽（insufficient_quota）救不活，立即抛 ERROR_QUOTA_EXHAUSTED。
    401/403 密钥错与返回格式错不重试，行为与之前一致。
    """
    payload = {
        "model": model or "auto",
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # (连接超时, 读取超时)：连接 15s 内必须建立；建立后每 20s 内必须收到数据
    stream_timeout = (CONNECTION_TIMEOUT, STREAM_IDLE_TIMEOUT)

    content = ""
    finish_reason = ""
    last_error = None
    waited = 0.0
    budget = float(timeout) if timeout else float(DEFAULT_TIMEOUT)

    for attempt in range(RATE_LIMIT_ATTEMPTS):
        response = None
        try:
            response = requests.post(
                _chat_completions_url(endpoint_url), json=payload, headers=headers,
                timeout=stream_timeout, stream=True,
            )
        except requests.Timeout:
            last_error = AISecurityError(ERROR_TIMEOUT)
        except requests.ConnectionError:
            last_error = AISecurityError(ERROR_NETWORK)
        except requests.RequestException:
            raise AISecurityError(ERROR_NETWORK) from None
        except Exception:
            raise AISecurityError(ERROR_INVALID) from None

        # HTTP 状态码在流式 body 之前就到达，429/5xx 检测逻辑不变
        if response is not None and response.status_code not in RETRYABLE_STATUS:
            # 200 或不可重试 4xx：读取流式内容
            if response.status_code == 200:
                try:
                    content, finish_reason = _read_stream(response)
                    last_error = None
                    break  # 成功拿到内容，退出重试循环
                except requests.Timeout:
                    # 流式读取中途 20s 无数据：连接已死，重试
                    last_error = AISecurityError(ERROR_TIMEOUT)
                    response = None
                except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
                    last_error = AISecurityError(ERROR_NETWORK)
                    response = None
                except Exception:
                    raise AISecurityError(ERROR_INVALID) from None
            else:
                # 不可重试 4xx（401/403 等），出循环走后续错误处理
                last_error = None
                break

        if response is not None:
            # 可重试状态码：429 先查配额耗尽（救不活，立即停）
            if response.status_code == 429 and _is_quota_exhausted_response(response):
                raise AISecurityError(ERROR_QUOTA_EXHAUSTED)
            last_error = AISecurityError(
                ERROR_RATE_LIMIT if response.status_code == 429 else ERROR_SERVER)

        # 决定是否再试一次：还有剩余次数 && 退避等待累计不超预算
        if attempt >= RATE_LIMIT_ATTEMPTS - 1:
            break
        if response is None:
            delay = NETWORK_BACKOFF_SECONDS[min(attempt, len(NETWORK_BACKOFF_SECONDS) - 1)]
        elif response.status_code == 429:
            delay = RATE_LIMIT_BACKOFF_SECONDS[min(attempt, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)]
        else:
            delay = SERVER_ERROR_BACKOFF_SECONDS[min(attempt, len(SERVER_ERROR_BACKOFF_SECONDS) - 1)]
        if waited + delay > budget:
            break  # 退避等待累计已逼近单次 timeout，不再拖延
        time.sleep(delay)
        waited += delay

    # 重试耗尽仍未拿到内容
    if not content and last_error is not None:
        raise last_error from None
    if not content and response is None:
        raise AISecurityError(ERROR_NETWORK) from None

    # 不可重试 4xx 错误处理（与之前一致）
    if response is not None and response.status_code != 200:
        if response.status_code in RETRYABLE_STATUS:
            raise (last_error or AISecurityError(ERROR_SERVER)) from None
        if response.status_code in (401, 403):
            raise AISecurityError(ERROR_AUTH)
        if response.status_code >= 400:
            raise AISecurityError(ERROR_INVALID)

    # 流式拿到的 content 为空（端点返回了 200 但没出字）
    if not content.strip():
        raise AISecurityError(ERROR_INVALID) from None

    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # 传输层截断单独识别（finish_reason==length 或 JSON 尾部不闭合），
        # 与"返回无效"区分开：上层拿到 truncated 可缩小批次重跑。
        if finish_reason == "length" or _looks_truncated(content):
            raise AISecurityError(ERROR_TRUNCATED) from None
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
MATCH_CONCURRENCY = 1    # Stage B 并发批次数（默认值，可被高级设置覆盖）


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
                concurrency=None, raise_on_systemic=False,
                completed_verdicts=None, on_batch_done=None):
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

    切片6（FR-020/SC-006）：``raise_on_systemic=True`` 时，AI 命中限流/额度/密钥/
    网络等 systemic 错误立即抛 ``AISecurityError``，调用方应捕获并暂停整任务，
    而不是默认全部保留并继续。默认 False 保持向后兼容。

    返回 {"kept": [job_id...], "dropped": [{"job_id","title","reason"}...],
    "verdicts": {job_id: {"verdict","reason"}}}。
    """
    if batch_size is None:
        batch_size = int(_adv_setting("screen_batch_size", SCREEN_BATCH_SIZE))
    if concurrency is None:
        concurrency = int(_adv_setting("screen_concurrency", SCREEN_CONCURRENCY))
    kept, dropped, verdicts = [], [], {}
    completed_verdicts = completed_verdicts or {}
    completed_ids = {str(job_id) for job_id in completed_verdicts}
    verdicts.update(completed_verdicts)
    for job in jobs:
        job_id = str(job.get("job_id", ""))
        verdict = completed_verdicts.get(job_id) or {}
        if verdict.get("verdict") == "dropped":
            dropped.append({
                "job_id": job_id,
                "title": job.get("title", ""),
                "reason": verdict.get("reason", ""),
                "canonical_url": job.get("canonical_url", "")
                or job.get("source_url", "") or job.get("url", ""),
            })
    jobs_to_process = [
        job for job in jobs
        if str(job.get("job_id", "")) not in completed_ids
    ]
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
    for start in range(0, len(jobs_to_process), batch_size):
        batches.append(jobs_to_process[start:start + batch_size])

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
        except AISecurityError as exc:
            # 切片6：systemic 错误（限流/额度/密钥/网络）立即抛，让调用方暂停
            if raise_on_systemic and exc.error_code in SYSTEMIC_AI_ERROR_CODES:
                raise
            if exc.error_code == ERROR_TRUNCATED and len(batch) > 1:
                # 返回被截断：拆半重跑这批，还截断就继续拆（到单条为止）
                mid = len(batch) // 2
                d1, v1 = _process_batch(batch[:mid])
                d2, v2 = _process_batch(batch[mid:])
                v1.update(v2)
                return d1 + d2, v1
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
            completed_ids.update(b_verdicts)
            if on_batch_done is not None:
                try:
                    on_batch_done(dict(b_verdicts), list(completed_ids))
                except Exception as exc:
                    raise AICheckpointError("AI batch checkpoint failed") from exc
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
                    completed_ids.update(b_verdicts)
                    completed_snapshot = list(completed_ids)
                if on_batch_done is not None:
                    try:
                        on_batch_done(dict(b_verdicts), completed_snapshot)
                    except Exception as exc:
                        raise AICheckpointError("AI batch checkpoint failed") from exc
                _safe_progress(len(futures[fut]))

    kept = [str(j.get("job_id", "")) for j in jobs
            if str(j.get("job_id", "")) not in {d["job_id"] for d in dropped}]
    return {"kept": kept, "dropped": dropped, "verdicts": verdicts}


def match_jds(jobs_with_jd, profile_summary, endpoint_url, api_key, model="",
              batch_size=None, progress=None, completed_verdicts=None,
              concurrency=None, raise_on_systemic=False):
    """Stage B 精筛：AI 逐条对比岗位 JD 与候选人画像，判 match/not_match。

    ``jobs_with_jd``: [{"job_id","title","salary","location","jd"}...]。
    返回 {"verdicts": {job_id: {"verdict": "match"/"not_match", "reason"}}}。
    AI 调用失败或漏回结果的岗位标记为 uncertain，保留给用户人工确认，
    不能把未完成的判定伪装成已匹配。

    ``completed_verdicts``: 可选，已完成的判定 {job_id: verdict}（断点续筛）。
    这些岗位跳过不重复调用 AI，原样并入返回；默认 None 时行为与之前一致。

    ``concurrency``: 并发批次数，默认 1（串行）。spec 007 ⑥⑦：免费端点实测并发=1；
        换不限流端点可调大。>1 时用线程池并发提交批次，结果按完成顺序合并。

    切片6（FR-020/SC-008）：``raise_on_systemic=True`` 时，AI 命中限流/额度/密钥/
    网络等 systemic 错误立即抛 ``AISecurityError``，调用方应捕获并暂停整任务，
    而不是批量变 uncertain 后完成。默认 False 保持向后兼容。
    """
    if batch_size is None:
        batch_size = int(_adv_setting("match_batch_size", MATCH_BATCH_SIZE))
    if concurrency is None:
        concurrency = int(_adv_setting("match_concurrency", MATCH_CONCURRENCY))
    verdicts = {}
    if completed_verdicts:
        done_ids = {str(k) for k in completed_verdicts}
        verdicts.update(completed_verdicts)
        jobs_with_jd = [j for j in jobs_with_jd
                        if str(j.get("job_id", "")) not in done_ids]
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
    def _match_one_batch(batch):
        """单批精筛，返回 {jid: verdict}。

        返回被截断（ERROR_TRUNCATED）时拆半重跑，还截断就继续拆到单条；
        单条仍失败才标 uncertain（不伪装成已匹配）。

        返回 (verdicts_dict, transport_failed)：transport_failed=True 表示
        整批因网络/超时/限流失败（可末尾补一轮），区别于 AI 返回了但漏了某条。
        """
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
        transport_failed = False
        fail_reason = ""
        try:
            data = call_ai(endpoint_url, api_key, messages, model=model)
            results = data.get("results", []) if isinstance(data, dict) else []
            by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
        except AISecurityError as exc:
            # 切片6：systemic 错误立即抛，让调用方暂停（不批量变 uncertain 后完成）
            if raise_on_systemic and exc.error_code in SYSTEMIC_AI_ERROR_CODES:
                raise
            if exc.error_code == ERROR_TRUNCATED and len(batch) > 1:
                mid = len(batch) // 2
                sub, f1 = _match_one_batch(batch[:mid])
                sub2, f2 = _match_one_batch(batch[mid:])
                sub.update(sub2)
                return sub, f1 or f2
            by_i = None
            transport_failed = True
            fail_reason = user_facing_error(exc.error_code)
        batch_verdicts = {}
        for idx, job in enumerate(batch):
            jid = str(job.get("job_id", ""))
            if by_i is None:
                batch_verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": f"{fail_reason}，待人工确认" if fail_reason else "AI 精筛失败，待人工确认",
                }
                continue
            r = by_i.get(idx)
            if not isinstance(r, dict) or not isinstance(r.get("match"), bool):
                batch_verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": "AI 未返回该岗位判定，待人工确认",
                }
                continue
            match = r["match"]
            reason = str(r.get("reason", "")).strip()
            caveats = [str(c).strip() for c in r.get("caveats") or [] if isinstance(c, str) and c.strip()]
            batch_verdicts[jid] = {
                "verdict": "match" if match else "not_match",
                "reason": reason,
                "caveats": caveats,
            }
        return batch_verdicts, transport_failed

    batches = []
    for start in range(0, len(jobs_with_jd), batch_size):
        batches.append(jobs_with_jd[start:start + batch_size])

    failed_batches = []  # 传输层失败的批次，末尾补一轮

    if concurrency <= 1 or len(batches) <= 1:
        # 串行（默认，免费端点并发=1）
        processed = 0
        for batch in batches:
            batch_verdicts, transport_failed = _match_one_batch(batch)
            verdicts.update(batch_verdicts)
            if transport_failed:
                failed_batches.append(batch)
            processed += len(batch)
            if progress is not None:
                try:
                    progress(min(processed, len(jobs_with_jd)), len(jobs_with_jd))
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
                cur = min(processed_counter[0], len(jobs_with_jd))
            try:
                progress(cur, len(jobs_with_jd))
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_match_one_batch, batch): batch for batch in batches}
            for fut in as_completed(futures):
                batch_verdicts, transport_failed = fut.result()
                with lock:
                    verdicts.update(batch_verdicts)
                    if transport_failed:
                        failed_batches.append(futures[fut])
                _safe_progress(len(futures[fut]))

    # 末尾补一轮：传输层失败的批次统一重试一次（网络抖动恢复后大概率成功）
    if failed_batches:
        for batch in failed_batches:
            batch_verdicts, still_failed = _match_one_batch(batch)
            if not still_failed:
                # 重试成功：用新判定覆盖之前的 uncertain
                verdicts.update(batch_verdicts)

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
