"""AI adapter: credential management, connection testing, JSON-validated AI calls.

All errors are sanitized to safe classification codes.  API keys, request
bodies and raw responses never appear in exceptions, logs or return values.
The application validates every AI output on its own side — the AI never
decides task status.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Mapping
from urllib.parse import urlparse

import keyring
import requests

from scripts import boss_cdp_raw as boss
from webui.ai_retry import effective_retry_plan
from webui.flag_features import (
    build_features_prompt_text,
    clean_flags,
    decide_flags,
)

KEYRING_SERVICE = "boss-workbench"
DEFAULT_TIMEOUT = 300
CONNECTION_TIMEOUT = 15
STREAM_IDLE_TIMEOUT = 30  # 流式模式下，连续 N 秒没收到任何数据即判定连接已死
STREAM_TOTAL_TIMEOUT = 180  # 流式模式下，从请求发出算起的总时长上限（防慢吐丝卡死）
RANK_BATCH_SIZE = 10

# Safe error classifications returned to callers.  Never include raw
# exception text, API keys or response bodies.
ERROR_TIMEOUT = "timeout"
ERROR_AUTH = "auth_failed"
ERROR_NETWORK = "network_error"
ERROR_INVALID = "invalid_response"
ERROR_RATE_LIMIT = "rate_limited"
ERROR_TRUNCATED = "truncated"
ERROR_NOT_CONFIGURED = "ai_not_configured"
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


_CHAT_COMPLETIONS_PATH = "/chat/completions"
_MODELS_PATH = "/models"
_AI_CHECKPOINT_FAILED = "AI batch checkpoint failed"


class AISecurityError(Exception):
    """AI call failure carrying only a safe error classification.

    The string form is the error_code alone, so it is safe to log or
    surface to users — it never contains the API key, request body or
    raw response.  The original exception is suppressed via ``from None``
    so tracebacks do not leak sensitive details either.
    """

    def __init__(self, error_code: str, diagnostics: dict | None = None):
        self.error_code = error_code
        self.diagnostics = dict(diagnostics or {})
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
    ERROR_NOT_CONFIGURED: "AI 未配置，请先设置 API 地址和密钥",
    ERROR_QUOTA_EXHAUSTED: "AI 额度已用完，请明天再试或更换 API 密钥",
    ERROR_SERVER: "AI 服务暂时不可用，请稍后重试",
}


def user_facing_error(error_code: str) -> str:
    """Return a user-friendly Chinese message for a safe error code."""
    return ERROR_USER_MESSAGES.get(error_code, "AI 服务调用失败，请检查设置后重试")


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
# T019: Measurement event emission helpers (FR-030/SC-006/SC-007)
# ---------------------------------------------------------------------------

def _emit_request_event(callback, stage: str, t0: float, *,
                        error_code: str | None = None,
                        counts: dict | None = None,
                        metadata: dict | None = None):
    """Emit a ``request`` measurement event if a callback is attached.

    The callback receives only safe fields — never the API key, request
    body, or raw response (data-model.md 2.9).
    """
    if callback is None:
        return
    duration_ms = max(0, int((time.time() - t0) * 1000))
    try:
        callback("request", stage=stage, duration_ms=duration_ms,
                 counts=counts, error_code=error_code, metadata=metadata)
    except Exception:
        pass  # measurement must never break the pipeline


def _emit_batch_event(callback, stage: str, *,
                      input_count: int, output_count: int,
                      error_code: str | None = None,
                      extra_counts: dict | None = None):
    """Emit a ``batch`` measurement event if a callback is attached."""
    if callback is None:
        return
    counts = {"input_count": input_count, "output_count": output_count}
    if extra_counts:
        counts.update(extra_counts)
    try:
        callback("batch", stage=stage, duration_ms=0,
                 counts=counts, error_code=error_code)
    except Exception:
        pass


def _emit_retry_event(callback, stage: str, backoff_ms: int, *,
                      metadata: dict | None = None):
    """Emit a ``retry`` measurement event for backoff / re-attempt."""
    if callback is None:
        return
    try:
        callback("retry", stage=stage, duration_ms=max(0, int(backoff_ms)),
                 metadata=metadata)
    except Exception:
        pass


def _emit_item_terminal_event(callback, stage: str, *,
                              item_index: int, status: str,
                              input_count: int):
    """Emit an ``item_terminal`` event for SC-007 terminal conservation."""
    if callback is None:
        return
    try:
        callback("item_terminal", stage=stage, duration_ms=0,
                 counts={"item_index": item_index, "status": status,
                         "input_count": input_count})
    except Exception:
        pass


def _measurement_item_index(job: dict, fallback: int,
                            measurement_indices: dict[int, int]) -> int:
    """Return the runner-assigned original index when one is available."""
    assigned = job.get("_tuning_measurement_index")
    if isinstance(assigned, int) and assigned >= 0:
        return assigned
    return measurement_indices.get(id(job), fallback)


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
    if url.endswith(_CHAT_COMPLETIONS_PATH):
        return url
    return url + _CHAT_COMPLETIONS_PATH


_SCHANNEL_POST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $headers = @{ Authorization = ('Bearer ' + $inputData.api_key) }
    $body = $inputData.payload | ConvertTo-Json -Depth 30 -Compress
    $response = Invoke-WebRequest -UseBasicParsing `
        -Uri $inputData.url -Method Post -Headers $headers `
        -ContentType 'application/json' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
        -TimeoutSec ([int]$inputData.timeout_seconds)
    @{ ok = $true; status = [int]$response.StatusCode; body = $response.Content } |
        ConvertTo-Json -Compress -Depth 5
} catch {
    $webResponse = $_.Exception.Response
    if ($null -ne $webResponse) {
        $reader = [IO.StreamReader]::new($webResponse.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        $reader.Dispose()
        @{ ok = $true; status = [int]$webResponse.StatusCode; body = $responseBody } |
            ConvertTo-Json -Compress -Depth 5
    } else {
        @{ ok = $false } | ConvertTo-Json -Compress
    }
}
"""


def _windows_schannel_post(
    url: str, api_key: str, payload: dict, *, timeout_seconds: int,
) -> requests.Response:
    """POST through Windows Schannel without exposing credentials in argv."""
    request_input = json.dumps({
        "url": url,
        "api_key": api_key,
        "payload": payload,
        "timeout_seconds": max(1, int(timeout_seconds)),
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-Command", _SCHANNEL_POST_SCRIPT,
            ],
            input=request_input,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)) + 10,
            check=False,
            **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}),
        )
    except subprocess.TimeoutExpired:
        raise requests.Timeout("Schannel fallback timed out") from None
    except OSError:
        raise requests.ConnectionError("Schannel fallback unavailable") from None
    if completed.returncode != 0:
        raise requests.ConnectionError("Schannel fallback failed")
    try:
        envelope = json.loads(completed.stdout.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise requests.ConnectionError("Schannel fallback returned invalid data") from None
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        raise requests.ConnectionError("Schannel fallback failed")
    body = envelope.get("body")
    if not isinstance(body, str):
        body = ""
    response = requests.Response()
    response.status_code = int(envelope.get("status") or 0)
    response._content = body.encode("utf-8")
    response.encoding = "utf-8"
    response.url = url
    return response


def _post_ai_json(
    url: str, api_key: str, payload: dict, *, timeout,
    stream: bool, fallback_timeout_seconds: int,
) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        return requests.post(
            url, json=payload, headers=headers, timeout=timeout, stream=stream,
        )
    except requests.exceptions.SSLError:
        return _windows_schannel_post(
            url, api_key, payload,
            timeout_seconds=fallback_timeout_seconds,
        )


def list_models(endpoint_url: str, api_key: str) -> list[str]:
    """GET /models 拉取可用模型列表。

    endpoint_url 填到 /v1 这一级。返回 model id 字符串列表，按字母序排序。
    失败时抛 AISecurityError（携带安全错误码，不含原始响应）。
    """
    base = (endpoint_url or "").rstrip("/")
    if not base:
        raise AISecurityError(ERROR_NETWORK)
    if not base.endswith(_MODELS_PATH):
        if base.endswith(_CHAT_COMPLETIONS_PATH):
            base = base[: -len(_CHAT_COMPLETIONS_PATH)] + _MODELS_PATH
        else:
            base = base + _MODELS_PATH
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
    try:
        response = _post_ai_json(
            url, api_key, payload, timeout=CONNECTION_TIMEOUT, stream=False,
            fallback_timeout_seconds=CONNECTION_TIMEOUT,
        )
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

def _extract_provider_error(response) -> dict:
    """从非 200 响应体安全提取供应商错误类型/代码（不泄露完整响应体）。

    返回 {"provider_error_type": ..., "provider_error_code": ...} 的子集，
    解析失败或字段不存在时返回空 dict。
    """
    try:
        body = response.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        return {}
    error = body.get("error")
    if not isinstance(error, dict):
        return {}
    result = {}
    err_type = error.get("type")
    err_code = error.get("code")
    if err_type and isinstance(err_type, str):
        result["provider_error_type"] = err_type
    if err_code and isinstance(err_code, str):
        result["provider_error_code"] = err_code
    return result


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
        except ValueError:
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
            temperature: float = 0.3, model: str = "", *,
            measurement_callback=None, measurement_stage: str = "ai",
            retry_limits: Mapping[str, int] | None = None) -> dict:
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
    # (连接超时, 读取超时)：连接 15s 内必须建立；建立后每 20s 内必须收到数据
    stream_timeout = (CONNECTION_TIMEOUT, STREAM_IDLE_TIMEOUT)
    correlation_id = uuid.uuid4().hex

    def emit_attempt(attempt_index: int, started_at: float, *,
                     error_code: str | None = None,
                     metadata: dict | None = None):
        if measurement_callback is None:
            if metadata is not None:
                metadata.setdefault("correlation_id", correlation_id)
                metadata.setdefault("attempt_index", attempt_index)
            return
        if metadata is not None:
            metadata.setdefault("correlation_id", correlation_id)
            metadata.setdefault("attempt_index", attempt_index)
        details = {
            "correlation_id": correlation_id,
            "attempt_index": attempt_index,
            "outcome": "failed" if error_code else "success",
            **(metadata or {}),
        }
        try:
            measurement_callback(
                "request", stage=measurement_stage,
                duration_ms=max(0, int((time.monotonic() - started_at) * 1000)),
                counts={"attempt": attempt_index}, error_code=error_code,
                metadata=details,
            )
        except Exception:
            pass

    content = ""
    finish_reason = ""
    last_error = None
    waited = 0.0
    budget = float(timeout) if timeout else float(DEFAULT_TIMEOUT)

    retry_counts: dict[str, int] = {}
    attempt = 0
    while True:
        attempt_index = attempt + 1
        attempt_started_at = time.monotonic()
        response = None
        try:
            response = _post_ai_json(
                _chat_completions_url(endpoint_url), api_key, payload,
                timeout=stream_timeout, stream=True,
                fallback_timeout_seconds=STREAM_TOTAL_TIMEOUT,
            )
        except requests.exceptions.SSLError:
            last_error = AISecurityError(
                ERROR_NETWORK,
                {"failure_phase": "tls", "exception_type": "SSLError"},
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=last_error.error_code,
                         metadata=last_error.diagnostics)
        except requests.Timeout:
            last_error = AISecurityError(
                ERROR_TIMEOUT,
                {"failure_phase": "connect", "exception_type": "Timeout"},
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=last_error.error_code,
                         metadata=last_error.diagnostics)
        except requests.ConnectionError:
            last_error = AISecurityError(
                ERROR_NETWORK,
                {"failure_phase": "connect", "exception_type": "ConnectionError"},
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=last_error.error_code,
                         metadata=last_error.diagnostics)
        except requests.RequestException:
            error = AISecurityError(
                ERROR_NETWORK,
                {"failure_phase": "request", "exception_type": "RequestException"},
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=error.error_code, metadata=error.diagnostics)
            if retry_limits is None:
                raise error from None
            last_error = error
        except Exception:
            error = AISecurityError(
                ERROR_INVALID,
                {"failure_phase": "request", "exception_type": "UnexpectedError"},
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=error.error_code, metadata=error.diagnostics)
            raise error from None

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
                    last_error = AISecurityError(
                        ERROR_TIMEOUT,
                        {"failure_phase": "stream", "exception_type": "Timeout"},
                    )
                    response = None
                    emit_attempt(attempt_index, attempt_started_at,
                                 error_code=last_error.error_code,
                                 metadata=last_error.diagnostics)
                except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
                    last_error = AISecurityError(
                        ERROR_NETWORK,
                        {"failure_phase": "stream", "exception_type": "ConnectionError"},
                    )
                    response = None
                    emit_attempt(attempt_index, attempt_started_at,
                                 error_code=last_error.error_code,
                                 metadata=last_error.diagnostics)
                except Exception:
                    error = AISecurityError(
                        ERROR_INVALID,
                        {"failure_phase": "stream", "exception_type": "UnexpectedError"},
                    )
                    emit_attempt(attempt_index, attempt_started_at,
                                 error_code=error.error_code,
                                 metadata=error.diagnostics)
                    raise error from None
            else:
                # 不可重试 4xx（401/403 等），出循环走后续错误处理
                last_error = None
                break

        if response is not None:
            # 可重试状态码：429 先查配额耗尽（救不活，立即停）
            if response.status_code == 429 and _is_quota_exhausted_response(response):
                error = AISecurityError(
                    ERROR_QUOTA_EXHAUSTED,
                    {"failure_phase": "http", "http_status": 429,
                     "provider_error_type": "insufficient_quota",
                     "provider_error_code": "insufficient_quota"},
                )
                emit_attempt(attempt_index, attempt_started_at,
                             error_code=error.error_code,
                             metadata=error.diagnostics)
                raise error
            provider_info = _extract_provider_error(response)
            last_error = AISecurityError(
                ERROR_RATE_LIMIT if response.status_code == 429 else ERROR_SERVER)
            emit_attempt(
                attempt_index, attempt_started_at,
                error_code=last_error.error_code,
                metadata={
                    "failure_phase": "http",
                    "http_status": response.status_code,
                    **provider_info,
                },
            )

        # 默认路径使用统一 3 次/30 秒策略；调优 manifest 仍按 error_code 预算。
        retry_plan = effective_retry_plan(retry_limits)
        if retry_limits is None:
            if attempt >= int(retry_plan["max_attempts"]) - 1:
                break
            delay = float(retry_plan["delay_seconds"])
        else:
            retry_error_code = (
                last_error.error_code if last_error is not None else ERROR_NETWORK
            )
            try:
                allowed_retries = max(
                    0, int(retry_limits.get(retry_error_code, 0))
                )
            except (TypeError, ValueError):
                allowed_retries = 0
            used_retries = retry_counts.get(retry_error_code, 0)
            if used_retries >= allowed_retries:
                break
            retry_counts[retry_error_code] = used_retries + 1
            if response is None:
                delay = NETWORK_BACKOFF_SECONDS[min(attempt, len(NETWORK_BACKOFF_SECONDS) - 1)]
            elif response.status_code == 429:
                delay = RATE_LIMIT_BACKOFF_SECONDS[min(attempt, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)]
            else:
                delay = SERVER_ERROR_BACKOFF_SECONDS[min(attempt, len(SERVER_ERROR_BACKOFF_SECONDS) - 1)]
            if waited + delay > budget:
                break  # 调优路径保留原 timeout 预算保护
        time.sleep(delay)
        waited += delay
        _emit_retry_event(
            measurement_callback, measurement_stage, int(delay * 1000),
            metadata={
                "correlation_id": correlation_id,
                "attempt_index": attempt_index,
                "retry_decision": "retry",
            },
        )
        attempt += 1

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
            provider_info = _extract_provider_error(response)
            error = AISecurityError(
                ERROR_AUTH,
                {"failure_phase": "http", "http_status": response.status_code,
                 **provider_info},
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=error.error_code, metadata=error.diagnostics)
            raise error
        if response.status_code >= 400:
            provider_info = _extract_provider_error(response)
            error = AISecurityError(
                ERROR_INVALID,
                {"failure_phase": "http", "http_status": response.status_code,
                 **provider_info},
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=error.error_code, metadata=error.diagnostics)
            raise error

    # 流式拿到的 content 为空（端点返回了 200 但没出字）
    if not content.strip():
        error = AISecurityError(
            ERROR_INVALID,
            {
                "failure_phase": "empty_response",
                "response_empty": True,
                "response_length": 0,
                "finish_reason": finish_reason or None,
            },
        )
        emit_attempt(attempt_index, attempt_started_at,
                     error_code=error.error_code, metadata=error.diagnostics)
        raise error from None

    try:
        parsed = json.loads(content)
        emit_attempt(
            attempt_index, attempt_started_at,
            metadata={
                "response_empty": False,
                "response_length": len(content),
                "finish_reason": finish_reason or None,
            },
        )
        return parsed
    except (json.JSONDecodeError, TypeError) as exc:
        # 传输层截断单独识别（finish_reason==length 或 JSON 尾部不闭合），
        # 与"返回无效"区分开：上层拿到 truncated 可缩小批次重跑。
        if finish_reason == "length" or _looks_truncated(content):
            error = AISecurityError(
                ERROR_TRUNCATED,
                {
                    "failure_phase": "truncated",
                    "response_empty": False,
                    "response_length": len(content),
                    "finish_reason": finish_reason or None,
                },
            )
            emit_attempt(attempt_index, attempt_started_at,
                         error_code=error.error_code, metadata=error.diagnostics)
            raise error from None
        error = AISecurityError(
            ERROR_INVALID,
            {
                "failure_phase": "json_decode",
                "response_empty": False,
                "response_length": len(content),
                "finish_reason": finish_reason or None,
                "parse_error": type(exc).__name__,
                "parse_position": getattr(exc, "pos", None),
            },
        )
        emit_attempt(attempt_index, attempt_started_at,
                     error_code=error.error_code, metadata=error.diagnostics)
        raise error from None


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
    city_names = [e.name for e in reg.city_catalog.entries]
    lines.append(f"city: 城市名,必须是以下之一: {', '.join(city_names[:80])}...等")
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
    resume_text = _resume_bytes_to_text(file_bytes, fmt).strip()
    if not resume_text:
        raise ValueError("简历内容为空")

    field_options = _build_field_options_prompt(platform)
    system_prompt = (
        "你是简历分析助手。读用户简历，帮用户填好求职搜索字段。\n"
        "规则只有两条：简历里明确写了就填，没写的字段留空，不要编造。\n"
        f"{field_options}\n\n"
        "另外输出两部分：\n"
        "- profile_facts: 简历里明确写出的客观事实（隐藏，不展示给用户），输出JSON对象：\n"
        "  {\"core_skills\":[\"Python\",\"Django\"],\"projects\":[{\"name\":\"xx系统\",\"role\":\"后端开发\",\"stack\":\"Python/Django\",\"summary\":\"负责订单模块\"}],\"job_type\":\"全职|实习|兼职|未体现\",\"degree\":\"本科\",\"languages\":[\"英语\"]}\n"
        "  core_skills 只列简历明确列出的技能（最多10个）；languages 只列简历明确的语言能力（无则空数组）；projects 只列简历明确的项目/工作经历，每项 name 必填，role/stack/summary 有则填（无项目则空数组），summary 可写职责、实现方式和量化成果，但必须是简历明确写出的内容；job_type 只能输出 全职/实习/兼职/未体现 之一；degree 只填简历明确写出的最高学历（如\"本科\"），没明确写出就省略；简历没写的输出\"未体现\"或空数组，禁止推断、补全或编造\n"
        "- profile_summary: 用自然语言写一段求职画像（给用户看、可编辑），像人写的段落，不列字段，不要出现'系统建议/自动补充'等说明性标记；先用3-5句话写求职方向、期望城市/薪资、求职类型、核心技能、项目经历和放宽意愿（简历写了什么就写什么，没写的不补）；项目经历只写项目方向、个人角色和所用技术栈，一句话概括即可，不写实现过程、量化指标或细节，详细项目事实放 profile_facts.projects；若简历体现了以下偏好且画像里还没有，用候选说法随机挑1-3个自然补充，不一次全塞，最终总共5-10句，说法、顺序、数量随机且自然：\n"
        "  - 求职类型：只找全职，兼职/外包/按单结算不考虑；远程全职可接受\n"
        "  - 双休：期望双休\n"
        "  - 远程全职可接受：接受远程全职\n"
        "  - 加班强度：不接受996\n"
        "  画像里已有该偏好就不重复；简历没体现的不补；简历推断内容（如学历层次）可写进画像自然语言或 profile_facts，不确定时不得写成事实\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": resume_text},
    ]

    data = call_ai(endpoint_url, api_key, messages, timeout=timeout, model=model)
    result = _validate_unified_fields(data, platform)
    # profile_summary 是自由文本，不参与枚举校验，验证后附加返回
    summary = data.get("profile_summary", "") if isinstance(data, dict) else ""
    result["profile_summary"] = str(summary).strip()
    # profile_facts 隐藏画像事实：宽松验证，无效项丢弃不阻塞整体
    result["profile_facts"] = _validate_profile_facts(
        data.get("profile_facts") if isinstance(data, dict) else None
    )
    return result


# 画像事实 job_type 四值枚举（与精筛 prompt 契约一致）
_PROFILE_FACT_JOB_TYPES = ("全职", "实习", "兼职", "未体现")


def _validate_profile_facts(data) -> dict:
    """宽松验证 AI 提取的画像事实：类型 + 长度，无效项丢弃不阻塞。

    契约字段：core_skills[] / projects[{name,role,stack,summary}] /
    job_type(四值) / degree(str) / languages[]。缺失字段不写入（调用方按\"未体现\"
    语义处理）；列表只保留非空字符串，超长截断。
    """
    if not isinstance(data, dict):
        return {}
    facts: dict = {}

    skills = data.get("core_skills")
    if isinstance(skills, list):
        cleaned = [str(s).strip() for s in skills
                   if isinstance(s, str) and s.strip()]
        if cleaned:
            facts["core_skills"] = cleaned[:10]

    projects = data.get("projects")
    if isinstance(projects, list):
        cleaned = []
        for project in projects:
            if not isinstance(project, dict):
                continue
            item = {}
            for key in ("name", "role", "stack", "summary"):
                value = project.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value.strip()
            if item.get("name"):
                cleaned.append(item)
        if cleaned:
            facts["projects"] = cleaned[:10]

    job_type = data.get("job_type")
    if isinstance(job_type, str) and job_type.strip() in _PROFILE_FACT_JOB_TYPES:
        facts["job_type"] = job_type.strip()

    degree = data.get("degree")
    if isinstance(degree, str) and degree.strip():
        facts["degree"] = degree.strip()

    languages = data.get("languages")
    if isinstance(languages, list):
        cleaned = [str(lang).strip() for lang in languages
                   if isinstance(lang, str) and lang.strip()]
        if cleaned:
            facts["languages"] = cleaned[:10]

    return facts


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
# 两段式 AI 筛选（pipeline 结果精炼）
# ---------------------------------------------------------------------------
# Stage A：字段粗筛。脚本列表结果不含 JD，AI 先按列表字段（薪资/城市/学历等）
#           筛掉"明显"不符合的；学历按常理向下兼容（候选人本科则大专岗也符合）。
# Stage B：JD 精筛。对粗筛留下的岗位批量抓 JD 后，AI 对比候选人画像判 match/not_match。

SCREEN_BATCH_SIZE = 50   # Stage A 每批送 AI 的岗位数（默认值，可被高级设置覆盖）
SCREEN_CONCURRENCY = 1   # Stage A 并发批次数（默认值，可被高级设置覆盖）
MATCH_BATCH_SIZE = 4     # Stage B 每批送 AI 的岗位数（默认值，可被高级设置覆盖）
MATCH_CONCURRENCY = 1    # Stage B 并发批次数（默认值，可被高级设置覆盖）
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


def _build_profile_facts_description(profile_facts) -> str:
    """把画像事实 dict 转成精筛 prompt 自然语言段落（缺失维度不输出）。"""
    if not isinstance(profile_facts, dict) or not profile_facts:
        return "（无画像事实，按未体现处理）"
    lines = []
    skills = profile_facts.get("core_skills")
    if skills:
        lines.append("核心技能：" + "、".join(str(s) for s in skills))
    projects = profile_facts.get("projects")
    if projects:
        parts = []
        for project in projects[:3]:
            name = str(project.get("name") or "未命名项目")
            role = str(project.get("role") or "").strip()
            stack = str(project.get("stack") or "").strip()
            summary = str(project.get("summary") or "").strip()
            detail = name
            if role:
                detail += f"（{role}）"
            if stack:
                detail += f"，技术栈：{stack}"
            if summary:
                detail += f"，{summary}"
            parts.append(detail)
        lines.append("项目经历：" + "；".join(parts))
    job_type = profile_facts.get("job_type")
    if job_type:
        lines.append(f"求职类型：{job_type}")
    degree = profile_facts.get("degree")
    if degree:
        lines.append(f"学历：{degree}")
    languages = profile_facts.get("languages")
    if languages:
        lines.append("语言能力：" + "、".join(str(l) for l in languages))
    return "\n".join(lines) if lines else "（无画像事实，按未体现处理）"


def screen_jobs(jobs, criteria, endpoint_url, api_key, model="",
                batch_size=None, progress=None,
                concurrency=None, raise_on_systemic=False,
                completed_verdicts=None, on_batch_done=None,
                execution_config=None,
                measurement_callback=None, emit_kept_terminal=True,
                measurement_input_count=None, retry_limits=None):
    """Stage A 粗筛：AI 逐条核对岗位列表字段，移除"明显"不符合的。

    ``jobs``: 脚本抓回的岗位列表（仅列表字段，无 JD）。
    ``criteria``: {"profile_summary": str, "city": [...], "degree": [...], ...}。
    ``concurrency``: 并发批次数，默认 1（串行）。spec 007 ⑥⑦：免费端点实测并发=1；
        换不限流端点可调大。>1 时用线程池并发提交批次，结果按批次顺序合并。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 ``screen_batch_size``/``screen_concurrency``，不读 JSON。

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
        if execution_config is not None:
            batch_size = int(execution_config.screen_batch_size)
        else:
            batch_size = int(_adv_setting("screen_batch_size", SCREEN_BATCH_SIZE))
    if concurrency is None:
        if execution_config is not None:
            concurrency = int(execution_config.screen_concurrency)
        else:
            concurrency = int(_adv_setting("screen_concurrency", SCREEN_CONCURRENCY))
    kept, dropped, verdicts = [], [], {}
    measurement_indices = {id(job): index for index, job in enumerate(jobs)}
    terminal_input_count = (
        int(measurement_input_count)
        if measurement_input_count is not None else len(jobs)
    )
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
        "你是求职初筛助手。只按候选人已确认的筛选字段，剔除【明显】不符的岗位。\n"
        f"{criteria_desc}\n\n"
        "判断规则（务必按常理，不要死板）：\n"
        "- 字段为空或未列出 = 不限，不得按该维度剔除；候选人画像只用于放宽，不能用来新增硬条件\n"
        "- 学历：仅当学历已确认且岗位要求高于候选人时排除；候选人学历不低于岗位要求即保留\n"
        "- 求职类型：仅当岗位标题明确写'实习'且候选人画像明确写'全职'时，视为明显不符合；拿不准一律保留\n"
        "- 城市不判断（抓取阶段已保证城市）\n"
        "- 薪资：仅当薪资已确认且岗位薪资明显低于期望时排除；'元/天'的实习计价综合判断\n"
        "- 经验：仅当经验已确认且岗位经验下界高于候选人上界时排除；岗位下界≤候选人上界时保留\n"
        "- 岗位名称或类别（如客服、讲师、销售、内容制作、运营等）不得单独作为剔除理由\n"
        "- 求职画像放宽：候选人画像中明确表达放宽的维度（如\"东莞、深圳都可以\"\"不限\"\"接受兼职\"等）以画像表述为准放宽对应判断\n"
        "- 只排除【明显】不符合的；拿不准一律保留（宁可多留，不可错杀）\n\n"
        "输入格式：每行一个岗位，``序号. 标题 | 薪资 | 城市 | 学历 | 规模``。\n"
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
        _t0 = time.time()
        _req_error_code = None
        try:
            data = call_ai(
                endpoint_url, api_key, messages, model=model,
                measurement_callback=measurement_callback,
                measurement_stage="rough",
                retry_limits=retry_limits,
            )
            dropped_list = data.get("dropped", []) if isinstance(data, dict) else []
            by_i = {r.get("i"): r for r in dropped_list if isinstance(r, dict)}
        except AISecurityError as exc:
            # 切片6：systemic 错误（限流/额度/密钥/网络）立即抛，让调用方暂停
            if raise_on_systemic and exc.error_code in SYSTEMIC_AI_ERROR_CODES:
                _req_error_code = exc.error_code
                raise
            if exc.error_code == ERROR_TRUNCATED and len(batch) > 1:
                # 返回被截断：拆半重跑这批，还截断就继续拆（到单条为止）
                _req_error_code = exc.error_code
                _emit_retry_event(
                    measurement_callback, "rough", 0,
                    metadata={"truncated_split": 1},
                )
                mid = len(batch) // 2
                d1, v1 = _process_batch(batch[:mid])
                d2, v2 = _process_batch(batch[mid:])
                v1.update(v2)
                return d1 + d2, v1
            _req_error_code = exc.error_code
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
            if r or emit_kept_terminal:
                _emit_item_terminal_event(
                    measurement_callback, "rough",
                    item_index=_measurement_item_index(
                        job, idx, measurement_indices),
                    status="dropped" if r else "kept",
                    input_count=terminal_input_count,
                )
        # 批次事件：记录输入/输出数量
        _emit_batch_event(measurement_callback, "rough",
                          input_count=len(batch),
                          output_count=len(b_dropped),
                          error_code=_req_error_code)
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
                    raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
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
                        raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
                _safe_progress(len(futures[fut]))

    kept = [str(j.get("job_id", "")) for j in jobs
            if str(j.get("job_id", "")) not in {d["job_id"] for d in dropped}]
    return {"kept": kept, "dropped": dropped, "verdicts": verdicts}


def match_jds(jobs_with_jd, profile_summary, endpoint_url, api_key, model="",
              criteria=None, profile_facts=None,
              batch_size=None, progress=None, completed_verdicts=None,
              concurrency=None, raise_on_systemic=False,
              execution_config=None,
              on_batch_done=None,
              measurement_callback=None, measurement_input_count=None,
              missing_result_retry_budget=0, retry_limits=None):
    """Stage B 精筛：AI 逐条对比岗位 JD 与候选人画像，判 match/not_match。

    ``jobs_with_jd``: [{"job_id","title","salary","location","jd"}...]。
    ``profile_summary``: 求职画像（用户可编辑，三通道中优先级最高）。
    ``criteria``: 可选，筛选条件 dict（学历/经验/薪资/城市等，作硬性基线）。
    ``profile_facts``: 可选，画像事实 dict（core_skills/projects/job_type/languages，
        缺失维度按"未体现"处理，不得推断）。
    返回 {"verdicts": {job_id: {"verdict": "match"/"not_match", "reason",
    "caveats", "flags"}}}。
    AI 调用失败或漏回结果的岗位标记为 uncertain，保留给用户人工确认，
    不能把未完成的判定伪装成已匹配。

    ``completed_verdicts``: 可选，已完成的判定 {job_id: verdict}（断点续筛）。
    这些岗位跳过不重复调用 AI，原样并入返回；默认 None 时行为与之前一致。

    ``concurrency``: 并发批次数，默认 1（串行）。spec 007 ⑥⑦：免费端点实测并发=1；
        换不限流端点可调大。>1 时用线程池并发提交批次，结果按完成顺序合并。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 ``match_batch_size``/``match_concurrency``，不读 JSON。

    ``on_batch_done``: 可选回调 (batch_verdicts, completed_job_ids)，每批判定落库后
    调用；回调抛异常会转成 AICheckpointError，防止内存进度领先于可恢复进度。

    切片6（FR-020/SC-008）：``raise_on_systemic=True`` 时，AI 命中限流/额度/密钥/
    网络等 systemic 错误立即抛 ``AISecurityError``，调用方应捕获并暂停整任务，
    而不是批量变 uncertain 后完成。默认 False 保持向后兼容。
    """
    if batch_size is None:
        if execution_config is not None:
            batch_size = int(execution_config.match_batch_size)
        else:
            batch_size = int(_adv_setting("match_batch_size", MATCH_BATCH_SIZE))
    if concurrency is None:
        if execution_config is not None:
            concurrency = int(execution_config.match_concurrency)
        else:
            concurrency = int(_adv_setting("match_concurrency", MATCH_CONCURRENCY))
    verdicts = {}
    measurement_indices = {
        id(job): index for index, job in enumerate(jobs_with_jd)
    }
    import threading
    terminal_lock = threading.Lock()
    emitted_terminal_indices: set[int] = set()
    failed_batch_metadata: dict[tuple[str, ...], dict] = {}

    def _batch_key(batch: list[dict]) -> tuple[str, ...]:
        return tuple(str(job.get("job_id", "")) for job in batch)

    def _emit_final_terminal(job: dict, fallback: int, status: str) -> None:
        item_index = _measurement_item_index(job, fallback, measurement_indices)
        with terminal_lock:
            if item_index in emitted_terminal_indices:
                return
            emitted_terminal_indices.add(item_index)
        _emit_item_terminal_event(
            measurement_callback, "fine", item_index=item_index,
            status=status, input_count=terminal_input_count,
        )
    if completed_verdicts:
        done_ids = {str(k) for k in completed_verdicts}
        verdicts.update(completed_verdicts)
        jobs_with_jd = [j for j in jobs_with_jd
                        if str(j.get("job_id", "")) not in done_ids]
    if not jobs_with_jd:
        return {"verdicts": verdicts}
    terminal_input_count = (
        int(measurement_input_count)
        if measurement_input_count is not None else len(jobs_with_jd)
    )
    missing_retry_budget = [max(0, int(missing_result_retry_budget))]
    summary = (profile_summary or "").strip() or "（无候选人画像）"
    criteria_desc = ""
    if criteria:
        criteria_desc = _build_criteria_description(
            {k: v for k, v in criteria.items() if k != "profile_summary"}
        )
    criteria_desc = criteria_desc or "（无明确标准，宽松判断）"
    facts_desc = _build_profile_facts_description(profile_facts)
    system_prompt = (
        "你是求职匹配度评估助手。根据候选人的完整信息包，判断每个岗位的JD工作内容是否适合候选人。\n"
        "候选人信息包（同一轮流程内所有判断共用，优先级从高到低）：\n"
        f"【第一层·求职意愿】候选人求职画像（用户可编辑，最高优先级）：{summary}\n"
        f"【第二层·筛选条件】用户确认的筛选条件：{criteria_desc}\n"
        f"【第三层·画像事实】简历提取的客观事实（未列出的维度一律视为未体现）：{facts_desc}\n\n"
        f"【第四层·未确认偏好】以下重要偏好若未在以上三层明确说明，一律标记为未填写/未确认：求职类型（只找全职，兼职/外包/按单结算不考虑；远程全职可接受）、双休、远程全职、加班强度（不接受996）。\n\n"
        "判断规则：\n"
        "- 判断是参考不是法律：匹配从宽只适用于候选人没有约束的维度；候选人画像、筛选条件或画像事实已明确的维度，冲突即判 match=false，不再放宽。\n"
        "- 以候选人自己的主业方向为锚：画像明确写了方向（如开发、运营、设计、产品、销售、培训等），岗位属于同一职业链路才可 match；明显跨链路的岗位默认 match=false。\n"
        "- 画像未明确写方向时，从核心技能和经历判断主业；岗位明显不属于该链路且候选人能力支撑不了，默认 match=false。\n"
        "- 用户明确写'不限/都可以/接受xx'时，按用户意愿放宽，覆盖默认不匹配。\n"
        "- 岗位类别不能只按标题判断，以 JD 主责为准；混合岗（如售前、解决方案）按主责归入对应链路。\n"
        "- 意愿与基线冲突时以意愿为准；意愿未提及的维度回退用筛选条件与画像事实判断；包里没有的维度（如薪资未填、学历未体现）默认匹配，不得写'候选人未知'。\n"
        "- 对【第四层·未确认偏好】标记的维度：JD 有明确要求而候选人未确认时，不得当作默认匹配，不得写'候选人可接受'，必须把'未填写/未确认'写入 caveats（如'求职类型未确认，JD 为兼职'）让用户自行确认；候选人明确写'不限/都可以/接受xx'时按意愿放宽。\n"
        "- 城市由抓取阶段已保证，不再作为不匹配理由。\n"
        "- 只有 JD 明确写'必须/要求'且候选人在包里明确达不到时才 match=false；学历、经验这类硬性项明确达不到才排除，拿不准一律保留，不再出现'候选人未知/未体现'式空想理由。\n"
        "- JD 中'优先/加分/plus/熟悉/了解'类软性要求（如行业经验、英语等级、证书、技能未列出）不得影响 match，应写入 caveats 数组（每项一句话，如'优先英语六级，候选人未提供'）；除非该维度是用户已确认的硬约束。\n"
        "- 不得把 AI 已识别出的方向冲突、硬性不满足只写进 caveats 后仍判 match；这类冲突必须反映到 match=false。\n"
        "对每个岗位输出判定。严格输出JSON：\n"
        '{"results":[{"i":0,"match":true,"reason":"一句话理由","caveats":["软性提醒"],"flags":[{"code":"A1","level":"medium","reason":"命中证据"}]},...]}\n'
        "i 为岗位序号；match=true 适合，false 不适合；reason 简短（20字内）；caveats 可为空数组。\n"
        "岗位靠谱判定：按下列特征清单核对岗位标题与JD正文是否命中可疑特征（\n"
        f"{build_features_prompt_text()}\n"
        "）。flags 为必填字段，无命中输出空数组 []，不得省略：\n"
        "  [{\"code\":\"特征code\",\"level\":\"high或medium\",\"reason\":\"引用标题/JD原文证据\"}]\n"
        "- level 只能是 high 或 medium；reason 必须引用岗位标题或JD正文的具体证据。\n"
        "- 命中任一高危（level=high）特征：该岗位强制 match=false，reason 以\"疑似骗局：\"开头并说明命中特征。\n"
        "- 中危特征逐条如实输出（命中几条输出几条）；拿不准的不要输出。\n"
        "- 不需要时间维度的特征（如\"岗位挂多久\"）一律不判断。\n"
        "- 不涉及上述清单的可疑迹象可写入 caveats（措辞\"需留意：…\"）。"
    )
    def _match_one_batch(batch, *, allow_transport_terminal=False):
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
        _t0 = time.time()
        _req_error_code = None
        try:
            data = call_ai(
                endpoint_url, api_key, messages, model=model,
                measurement_callback=measurement_callback,
                measurement_stage="fine",
                retry_limits=retry_limits,
            )
            results = data.get("results", []) if isinstance(data, dict) else []
            by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
        except AISecurityError as exc:
            # 切片6：systemic 错误立即抛，让调用方暂停（不批量变 uncertain 后完成）
            if raise_on_systemic and exc.error_code in SYSTEMIC_AI_ERROR_CODES:
                _req_error_code = exc.error_code
                for fallback, pending_job in enumerate(jobs_with_jd):
                    _emit_final_terminal(pending_job, fallback, "uncertain")
                raise
            if exc.error_code == ERROR_TRUNCATED and len(batch) > 1:
                _req_error_code = exc.error_code
                _emit_retry_event(
                    measurement_callback, "fine", 0,
                    metadata={"truncated_split": 1},
                )
                mid = len(batch) // 2
                sub, f1 = _match_one_batch(
                    batch[:mid], allow_transport_terminal=allow_transport_terminal)
                sub2, f2 = _match_one_batch(
                    batch[mid:], allow_transport_terminal=allow_transport_terminal)
                sub.update(sub2)
                return sub, f1 or f2
            _req_error_code = exc.error_code
            by_i = None
            transport_failed = True
            fail_reason = user_facing_error(exc.error_code)
            with terminal_lock:
                failed_batch_metadata[_batch_key(batch)] = dict(exc.diagnostics)
        batch_verdicts = {}
        for idx, job in enumerate(batch):
            jid = str(job.get("job_id", ""))
            if by_i is None:
                batch_verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": f"{fail_reason}，待人工确认" if fail_reason else "AI 精筛失败，待人工确认",
                }
                if allow_transport_terminal:
                    _emit_final_terminal(job, idx, "uncertain")
                continue
            r = by_i.get(idx)
            if not isinstance(r, dict) or not isinstance(r.get("match"), bool):
                if missing_retry_budget[0] > 0:
                    missing_retry_budget[0] -= 1
                    _emit_retry_event(measurement_callback, "fine", 0)
                    retried, retry_failed = _match_one_batch(
                        [job],
                        allow_transport_terminal=allow_transport_terminal,
                    )
                    batch_verdicts.update(retried)
                    transport_failed = transport_failed or retry_failed
                    continue
                batch_verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": "AI 未返回该岗位判定，待人工确认",
                }
                _emit_final_terminal(job, idx, "uncertain")
                continue
            match = r["match"]
            reason = str(r.get("reason", "")).strip()
            caveats = [str(c).strip() for c in r.get("caveats") or []
                       if isinstance(c, str) and c.strip()]
            # flags 结构化解析：清洗（code/level/reason 校验）+ 分级判定
            # （高危≥1 或 中危≥2 → 输出 flags；中危仅 1 条 → 降级 caveats）
            decided = decide_flags(clean_flags(r.get("flags")))
            flags = decided["flags"]
            caveats.extend(decided["caveats"])
            verdict = "match" if match else "not_match"
            # 高危命中强制 not_match，reason 以"疑似骗局："开头
            if any(f.get("level") == "high" for f in flags):
                verdict = "not_match"
                reason = reason or "命中高危可疑特征"
                if not reason.startswith("疑似骗局："):
                    reason = "疑似骗局：" + reason
            batch_verdicts[jid] = {
                "verdict": verdict,
                "reason": reason,
                "caveats": caveats,
                "flags": flags,
            }
            _emit_final_terminal(job, idx, "match" if match else "not_match")
        _emit_batch_event(measurement_callback, "fine",
                          input_count=len(batch),
                          output_count=len(batch_verdicts),
                          error_code=_req_error_code)
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
            if on_batch_done is not None:
                try:
                    on_batch_done(dict(batch_verdicts), list(verdicts.keys()))
                except Exception as exc:
                    raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
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
                    completed_snapshot = list(verdicts.keys())
                if on_batch_done is not None:
                    try:
                        on_batch_done(dict(batch_verdicts), completed_snapshot)
                    except Exception as exc:
                        raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
                _safe_progress(len(futures[fut]))

    # 末尾补一轮：传输层失败的批次统一重试一次（网络抖动恢复后大概率成功）
    if failed_batches:
        for batch in failed_batches:
            with terminal_lock:
                failure_metadata = dict(
                    failed_batch_metadata.get(_batch_key(batch), {})
                )
            retry_metadata = {"retry_decision": "batch_final_retry"}
            if failure_metadata.get("correlation_id"):
                retry_metadata["correlation_id"] = failure_metadata[
                    "correlation_id"
                ]
            _emit_retry_event(
                measurement_callback, "fine", 0, metadata=retry_metadata,
            )
            batch_verdicts, _ = _match_one_batch(
                batch, allow_transport_terminal=True)
            # 无论完全恢复还是部分恢复，都以最终尝试的逐项结果为准。
            verdicts.update(batch_verdicts)
            if on_batch_done is not None:
                try:
                    on_batch_done(dict(batch_verdicts), list(verdicts.keys()))
                except Exception as exc:
                    raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc

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
