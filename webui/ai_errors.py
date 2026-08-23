"""AI 错误分类与测量遥测事件（021 B7 自 ai.py 搬运）。

AISecurityError 只携带安全错误分类；事件发射器把请求/批次/重试/终态事件
写入测量 sink；供应商错误提取只暴露安全分类字段，不泄露响应体。
"""

from __future__ import annotations

import time

from webui.error_registry import AI_TAXONOMY_TARGETS, ERROR_USER_MESSAGES
from webui.error_registry import resolve_code




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






def user_facing_error(error_code: str) -> str:
    """Return a user-friendly Chinese message for a safe error code."""
    message = ERROR_USER_MESSAGES.get(error_code)
    if message is None:
        resolve_code(error_code)  # 未知码可见告警，不改变既有兜底文案
        return "AI 服务调用失败，请检查设置后重试"
    return message






def map_ai_error_to_block_code(error_code: str) -> str:
    """把 ai.py 的内部错误码映射到 pipeline_exec.ERROR_TAXONOMY 的阻断码。

    用于 _run_ai_screen_task 暂停时写入 screening_runs.error_code。
    非 systemic 错误返回空串。
    """
    return AI_TAXONOMY_TARGETS.get(error_code, "")




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
