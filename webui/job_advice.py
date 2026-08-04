"""Job advice adapter: read-only, non-persistent AI suggestion for a single job.

Constructs AI input from only JD/applied_at/last_follow_up_at/elapsed_days
(FR-021). Validates AI output against a strict allowlist of follow_up|review
(FR-022). Falls back to rule-based advice on any AI failure or invalid output
(FR-023). Never writes to the database (FR-025).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from webui.ai import AISecurityError, call_ai, is_ai_available


ADVICE_ACTIONS = ("follow_up", "review")
ADVICE_SOURCES = ("ai", "rule")

_RULE_FOLLOW_UP_REASON = "已超过 30 天没有新的跟进记录，建议先主动确认进展。"
_RULE_REVIEW_REASON = "岗位信息不足，建议人工复核后再决定是否跟进。"

_SYSTEM_PROMPT = (
    "你是求职跟进建议助手。根据岗位 JD、投递时间、最后跟进时间和经过天数，"
    "判断下一步行动方向。\n"
    '只输出 JSON：{"action": "follow_up" 或 "review", "reason": "简短解释"}\n'
    "- follow_up：建议主动跟进招聘进展\n"
    "- review：建议人工复核岗位信息或重新评估\n"
    "reason 必须是一句话中文解释，不超过 50 字，不得包含原始 JD 内容。\n"
    "禁止输出其他字段或其他行动方向。"
)


@dataclass(frozen=True)
class JobAdviceInput:
    """Minimal input DTO for advice generation (FR-021).

    Only carries JD, applied_at, last_follow_up_at, elapsed_days.
    Platform/title/company/canonical_url must never be included.
    """

    jd: str | None
    applied_at: str | None
    last_follow_up_at: str | None
    elapsed_days: int | None


@dataclass(frozen=True)
class JobAdviceResult:
    """Advice result: action in allowlist, cleaned reason, source ai|rule."""

    action: str
    reason: str
    source: str


def _has_jd(input_dto: JobAdviceInput) -> bool:
    return bool(input_dto.jd and input_dto.jd.strip())


def _rule_fallback(input_dto: JobAdviceInput) -> JobAdviceResult:
    """Rule-based advice: review if no JD, follow_up if JD exists (FR-023)."""
    if not _has_jd(input_dto):
        return JobAdviceResult(
            action="review",
            reason=_RULE_REVIEW_REASON,
            source="rule",
        )
    return JobAdviceResult(
        action="follow_up",
        reason=_RULE_FOLLOW_UP_REASON,
        source="rule",
    )


def _clean_reason(reason: Any) -> str:
    """Strip whitespace from reason; return empty string if not a valid string."""
    if not isinstance(reason, str):
        return ""
    return reason.strip()


def _build_ai_messages(input_dto: JobAdviceInput) -> list[dict]:
    """Build AI messages containing only FR-021 allowed fields."""
    user_payload = {
        "jd": input_dto.jd,
        "applied_at": input_dto.applied_at,
        "last_follow_up_at": input_dto.last_follow_up_at,
        "elapsed_days": input_dto.elapsed_days,
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _parse_ai_response(data: Any) -> JobAdviceResult | None:
    """Parse and validate AI response. Returns None if invalid (caller falls back)."""
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if action not in ADVICE_ACTIONS:
        return None
    reason = _clean_reason(data.get("reason"))
    if not reason:
        return None
    return JobAdviceResult(action=action, reason=reason, source="ai")


def generate_advice(
    input_dto: JobAdviceInput,
    *,
    ai_settings: Mapping | None = None,
    credential_ref: str | None = None,
    api_key: str | None = None,
    model: str = "",
    provider: Callable[..., dict] | None = None,
) -> JobAdviceResult:
    """Generate advice for a single job (FR-021 to FR-025).

    - Missing JD → fixed ``review`` (no AI call).
    - AI unconfigured/missing key → rule fallback.
    - AI timeout/network/auth/invalid/illegal action/empty reason → rule fallback.
    - With JD + AI failure → rule ``follow_up``.
    - Without JD → rule ``review`` (AI never called).
    - Never writes to the database.

    Args:
        input_dto: Minimal job advice input (JD, times, elapsed_days).
        ai_settings: AI settings dict with ``is_configured`` and ``endpoint_url``.
        credential_ref: Credential reference (hostname).
        api_key: API key string.
        model: Model name.
        provider: Optional callable ``(endpoint, key, messages, model=) -> dict``
            replacing ``call_ai`` for testing.

    Returns:
        :class:`JobAdviceResult` with action, reason, source.
    """
    # FR-023: 缺 JD 固定 review，不调 AI
    if not _has_jd(input_dto):
        return _rule_fallback(input_dto)

    # FR-023: AI 未配置/缺 key 规则兜底
    settings = ai_settings if isinstance(ai_settings, dict) else {}
    ref = credential_ref if isinstance(credential_ref, str) else ""
    key = api_key if isinstance(api_key, str) else ""
    if not is_ai_available(settings, ref, key):
        return _rule_fallback(input_dto)

    # FR-021: 只把允许字段送进 AI input
    messages = _build_ai_messages(input_dto)

    call = provider or call_ai
    endpoint_url = settings.get("endpoint_url", "")
    try:
        data = call(endpoint_url, key, messages, model=model)
    except AISecurityError:
        return _rule_fallback(input_dto)
    except Exception:
        return _rule_fallback(input_dto)

    # FR-022: 校验 action allowlist 和 reason 清洗
    result = _parse_ai_response(data)
    if result is None:
        return _rule_fallback(input_dto)
    return result
