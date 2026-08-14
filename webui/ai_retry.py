"""Default AI retry policy and tuning overrides (B045).

The default policy is per error code: bounded backoff + jitter + a total
wait cap between retries.  ``invalid_response`` is not retried by the
transport layer; the fine-screen single-job path owns its single retry.
Explicit ``retry_limits`` from a tuning manifest still wins over the
default plan.
"""

from __future__ import annotations

import random
from copy import deepcopy
from typing import Any, Mapping

DEFAULT_AI_RETRY_MAX_ATTEMPTS = 4
DEFAULT_AI_RETRY_DELAY_SECONDS = 1.0
DEFAULT_AI_RETRY_TOTAL_WAIT_SECONDS = 60.0

FINE_SINGLE_INVALID_RESPONSE_RETRIES = 1
FINE_SINGLE_INVALID_RESPONSE_DELAY_SECONDS = 1.0

# manifest 里能覆盖 AI 传输层重试的错误码；其余码只影响对应阶段。
AI_TRANSPORT_RETRY_CODES = frozenset({
    "network_error", "rate_limited", "timeout", "server_error",
})

DEFAULT_RETRY_POLICY: dict[str, dict[str, Any]] = {
    "network_error": {
        "max_retries": 3,
        "backoff_seconds": (2.0, 4.0, 8.0),
        "jitter_seconds": 1.0,
    },
    "rate_limited": {
        "max_retries": 3,
        "backoff_seconds": (5.0, 15.0, 30.0),
        "jitter_seconds": 1.0,
    },
    # timeout/server_error 在注册表中归类为 ai_network_error，沿用网络退避。
    "timeout": {
        "max_retries": 3,
        "backoff_seconds": (2.0, 4.0, 8.0),
        "jitter_seconds": 1.0,
    },
    "server_error": {
        "max_retries": 3,
        "backoff_seconds": (2.0, 4.0, 8.0),
        "jitter_seconds": 1.0,
    },
}


def effective_retry_plan(retry_limits: Mapping[str, int] | None) -> dict[str, Any]:
    """Return the retry plan for a call.

    ``None`` returns the default per-code policy. A mapping returns a
    tuning plan that preserves per-error budgets for the caller.
    """
    if retry_limits is None:
        return {
            "mode": "default",
            "policy": deepcopy(DEFAULT_RETRY_POLICY),
            "total_wait_seconds": DEFAULT_AI_RETRY_TOTAL_WAIT_SECONDS,
        }
    return {
        "mode": "tuning",
        "retry_limits": dict(retry_limits),
        "total_wait_seconds": DEFAULT_AI_RETRY_TOTAL_WAIT_SECONDS,
    }


def retry_delay_seconds(
    error_code: str,
    retry_index: int,
    plan: dict[str, Any],
) -> float:
    """Return the delay before retry ``retry_index`` (0-based)."""
    policy = (plan.get("policy") or {}).get(error_code)
    if not policy:
        return 0.0
    backoffs = tuple(policy.get("backoff_seconds") or (1.0,))
    base = float(backoffs[min(int(retry_index), len(backoffs) - 1)])
    jitter = float(policy.get("jitter_seconds") or 0.0)
    return base + random.uniform(0.0, jitter)


def normalize_retry_policy(policy: Any) -> dict[str, dict[str, Any]] | None:
    """Normalize a manifest retry policy against the default shape.

    Accepts the current per-code shape and the legacy
    ``{"recoverable_codes": [...], "max_retries": N}`` shape.  Returns
    ``None`` when the policy is missing/empty or any entry is malformed;
    callers then fall back to the default policy.
    """
    if not isinstance(policy, dict) or not policy:
        return None
    # 兼容旧 manifest：可恢复码列表 + 统一重试次数。
    if "recoverable_codes" in policy:
        codes = policy.get("recoverable_codes")
        if not isinstance(codes, list) or not codes:
            return None
        if not all(isinstance(code, str) and code for code in codes):
            return None
        try:
            max_retries = int(policy.get("max_retries", 0))
        except (TypeError, ValueError):
            return None
        if max_retries < 0:
            return None
        return {str(code): {"max_retries": max_retries} for code in codes}
    normalized: dict[str, dict[str, Any]] = {}
    for code, entry in policy.items():
        if not isinstance(code, str) or not code or not isinstance(entry, dict):
            return None
        try:
            max_retries = int(entry.get("max_retries", 0))
        except (TypeError, ValueError):
            return None
        if max_retries < 0:
            return None
        backoff = entry.get("backoff_seconds")
        if backoff is not None:
            # 旧结构允许单个数字退避；新结构使用序列。
            if isinstance(backoff, (int, float)) and not isinstance(backoff, bool):
                backoff = [backoff]
            if (not isinstance(backoff, (list, tuple)) or not backoff
                    or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                           or v <= 0 for v in backoff)):
                return None
        jitter = entry.get("jitter_seconds")
        if jitter is not None:
            if (isinstance(jitter, bool) or not isinstance(jitter, (int, float))
                    or jitter < 0):
                return None
        item: dict[str, Any] = {"max_retries": max_retries}
        if backoff is not None:
            item["backoff_seconds"] = [float(v) for v in backoff]
        if jitter is not None:
            item["jitter_seconds"] = float(jitter)
        normalized[str(code)] = item
    return normalized
