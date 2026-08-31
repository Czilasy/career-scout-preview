"""AI 传输层：URL 构建、JSON POST、密钥环、连通性测试与流式 call_ai（021 B7 自 ai.py 搬运）。

可 patch 符号（_post_ai_json / keyring / call_ai 等）经 webui.ai 门面
在调用时动态取用，保持旧 patch 面不变。
"""

from __future__ import annotations

import json
import time
import uuid

import requests

from collections.abc import Mapping
from urllib.parse import urlparse
from webui.ai_retry import effective_retry_plan, retry_delay_seconds
from webui.error_registry import ERROR_AUTH, ERROR_INVALID, ERROR_NETWORK, ERROR_QUOTA_EXHAUSTED, ERROR_RATE_LIMIT, ERROR_SERVER, ERROR_TIMEOUT, ERROR_TRUNCATED

from webui.ai_errors import (
    AISecurityError,
    _emit_retry_event,
    _extract_provider_error,
    _is_quota_exhausted_response,
    _looks_truncated,
)
from webui.logging_setup import get_logger

_logger = get_logger(__name__)



KEYRING_SERVICE = "boss-workbench"


DEFAULT_TIMEOUT = 300


CONNECTION_TIMEOUT = 15


STREAM_IDLE_TIMEOUT = 30  # 流式模式下，连续 N 秒没收到任何数据即判定连接已死


STREAM_TOTAL_TIMEOUT = 180  # 流式模式下，从请求发出算起的总时长上限（防慢吐丝卡死）


FINE_BATCH_TIMEOUT = 180  # 精筛单批 AI 请求的总时长上限（秒）：超时即放弃该批，不无限等


RANK_BATCH_SIZE = 10




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




def _post_ai_json(
    url: str, api_key: str, payload: dict, *, timeout,
    stream: bool, fallback_timeout_seconds: int,
) -> requests.Response:
    from webui import ai as _facade
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        return requests.post(
            url, json=payload, headers=headers, timeout=timeout, stream=stream,
        )
    except requests.exceptions.SSLError:
        return _facade._windows_schannel_post(
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
    from webui import ai as _facade
    host = _host_from_url(endpoint_url)
    _facade.keyring.set_password(KEYRING_SERVICE, host, api_key)
    return host




def retrieve_api_key(credential_ref: str) -> str:
    """Retrieve the api_key associated with *credential_ref* from the credential store."""
    from webui import ai as _facade
    return _facade.keyring.get_password(KEYRING_SERVICE, credential_ref)




def delete_api_key(credential_ref: str) -> None:
    """Delete the api_key associated with *credential_ref* from the credential store."""
    from webui import ai as _facade
    _facade.keyring.delete_password(KEYRING_SERVICE, credential_ref)




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
    from webui import ai as _facade
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
        response = _facade._post_ai_json(
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
        # reasoning_content/reasoning while leaving content empty when
        # max_tokens is tight. We accept either field as proof the chat
        # completions pipeline works.
        content = str(message.get("content") or "").strip()
        reasoning = str(
            message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        ).strip()
        if not content and not reasoning:
            raise ValueError("empty reply")
    except Exception:
        return {"ok": False, "transport": "ready", "generation": "failed",
                "candidate_contract": "manual_required", "warning_codes": [ERROR_INVALID]}

    return {"ok": True, "transport": "ready", "generation": "ready",
            "candidate_contract": "manual_required", "warning_codes": []}




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




def _read_stream_with_timeout(response, budget):
    """读取流式响应，受总时长 budget 约束（覆盖 iter_lines 阻塞的极端场景）。

    ``_read_stream`` 的 STREAM_TOTAL_TIMEOUT 检查只在 iter_lines 循环体内执行；
    若 AI 端点连接保持但不返回任何行，iter_lines 会无限阻塞在 readline 上
    （requests 的 read timeout 对流式响应不保证生效），循环体永不执行，总时长
    检查形同虚设。这里用线程 + join(budget) 兜底：超时即关闭连接并抛 Timeout，
    由外层 call_ai 接住转为 ERROR_TIMEOUT，避免任务无限卡死。
    """
    import threading
    box: dict = {}

    def _run():
        try:
            box["result"] = _read_stream(response)
        except BaseException as exc:  # noqa: BLE001 - 桥接任意读取异常
            box["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(max(0.0, float(budget)))
    if thread.is_alive():
        try:
            response.close()
        except Exception:
            _logger.debug("HTTP 响应清理关闭失败（best-effort 忽略）", exc_info=True)

        raise requests.Timeout(
            f"流式响应总时长超过 {budget}s 上限（读取线程仍阻塞）"
        )
    if "error" in box:
        raise box["error"]
    return box["result"]




def call_ai(endpoint_url: str, api_key: str, messages: list, timeout: int = DEFAULT_TIMEOUT,
            temperature: float = 0.3, model: str = "", *,
            measurement_callback=None, measurement_stage: str = "ai",
            retry_limits: Mapping[str, int] | None = None,
            correlation_id: str = "") -> dict:
    """Call an OpenAI-compatible chat completions endpoint and return parsed JSON.

    使用流式（stream=True）模式：AI 每生成几个字就推送一小段，本地实时接收。
    如果连续 STREAM_IDLE_TIMEOUT（20）秒没收到任何数据，判定连接已死，
    立即超时重试——不再傻等 60 秒。

    Raises :class:`AISecurityError` with a safe error_code on any failure.
    The exception never contains the API key, request body or raw response,
    and the original exception is suppressed so tracebacks stay clean.

    重试策略：429 限流按 5/15/30s 退避，5xx/超时/连接错误按 2/4/8s 退避，
    每次加抖动；重试之间累计等待不超过 60s（不包含单次请求耗时）。
    配额耗尽（insufficient_quota）救不活，立即抛 ERROR_QUOTA_EXHAUSTED。
    401/403 密钥错与返回格式错不重试，行为与之前一致。
    """
    from webui import ai as _facade
    payload = {
        "model": model or "auto",
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "stream": True,
    }
    # (连接超时, 读取超时)：连接 15s 内必须建立；建立后每 20s 内必须收到数据
    stream_timeout = (CONNECTION_TIMEOUT, STREAM_IDLE_TIMEOUT)
    correlation_id = correlation_id or uuid.uuid4().hex

    def emit_attempt(attempt_index: int, started_at: float, *,
                     error_code: str | None = None,
                     metadata: dict | None = None):
        if metadata is not None:
            metadata.setdefault("correlation_id", correlation_id)
            metadata.setdefault("attempt_index", attempt_index)
        if measurement_callback is None:
            return
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
            _logger.debug("指标回调执行失败（不阻断重试流程）", exc_info=True)


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
            response = _facade._post_ai_json(
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
                    content, finish_reason = _read_stream_with_timeout(response, budget)
                    _facade.record_raw_ai_response(
                        correlation_id, attempt_index, content,
                        operation=measurement_stage or "",
                    )
                    if not content.strip():
                        # B063：HTTP 200 但流式内容为空 → 按可重试传输故障处理。
                        # 不 break，而是设 last_error + response=None，让本循环
                        # 按 invalid_response(empty_response) 的退避/次数重试；
                        # json_decode 型 invalid 仍在循环外原地抛，不做重试。
                        last_error = AISecurityError(
                            ERROR_INVALID,
                            {
                                "failure_phase": "empty_response",
                                "response_empty": True,
                                "response_length": 0,
                                "finish_reason": finish_reason or None,
                            },
                        )
                        response = None
                        emit_attempt(attempt_index, attempt_started_at,
                                     error_code=last_error.error_code,
                                     metadata=last_error.diagnostics)
                    else:
                        last_error = None
                        break  # 成功拿到非空内容，退出重试循环
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

        # 默认路径按错误码退避 + 抖动 + 60s 总上限；调优 manifest 仍按 error_code 预算。
        retry_plan = effective_retry_plan(retry_limits)
        retry_error_code = (
            last_error.error_code if last_error is not None else ERROR_NETWORK
        )
        used_retries = retry_counts.get(retry_error_code, 0)
        if retry_plan["mode"] == "default":
            policy = retry_plan["policy"].get(retry_error_code)
            if policy is None or used_retries >= int(policy["max_retries"]):
                break
            retry_counts[retry_error_code] = used_retries + 1
            delay = retry_delay_seconds(
                retry_error_code, used_retries, retry_plan)
        else:
            try:
                allowed_retries = max(
                    0, int(retry_limits.get(retry_error_code, 0))
                )
            except (TypeError, ValueError):
                allowed_retries = 0
            if used_retries >= allowed_retries:
                break
            retry_counts[retry_error_code] = used_retries + 1
            if response is None:
                delay = float(NETWORK_BACKOFF_SECONDS[min(attempt, len(NETWORK_BACKOFF_SECONDS) - 1)])
            elif response.status_code == 429:
                delay = float(RATE_LIMIT_BACKOFF_SECONDS[min(attempt, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)])
            else:
                delay = float(SERVER_ERROR_BACKOFF_SECONDS[min(attempt, len(SERVER_ERROR_BACKOFF_SECONDS) - 1)])
        if waited + delay > float(retry_plan["total_wait_seconds"]):
            break  # 总等待上限只计算重试之间的等待
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
        _logger.warning(
            "AI 调用失败（重试耗尽）type=%s code=%s",
            type(last_error).__name__,
            getattr(last_error, "error_code", "") or "",
        )
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
