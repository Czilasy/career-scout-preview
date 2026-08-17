"""Pure, safe classifications for BOSS CDP runtime responses."""

from __future__ import annotations

from typing import Any

RISK_API_CODES = frozenset({31, 37})


def api_code_diagnosis(code: Any, message: Any = "") -> dict[str, Any]:
    """Normalize a non-success BOSS API code without retaining full payloads."""
    try:
        normalized_code = int(code)
    except (TypeError, ValueError):
        normalized_code = 0
    return {
        "kind": "api_code",
        "code": normalized_code,
        "sample": str(message or "")[:160],
    }


def is_risk_api_code(code: Any) -> bool:
    """Return whether a BOSS API code is an explicit platform risk signal."""
    try:
        return int(code) in RISK_API_CODES
    except (TypeError, ValueError):
        return False


def api_code_hint(code: Any, message: Any = "") -> str:
    """Return a short operator-facing hint for an explicit BOSS API failure."""
    try:
        normalized_code = int(code)
    except (TypeError, ValueError):
        normalized_code = 0
    if normalized_code == 37:
        return "BOSS 返回 code:37（环境存在异常）"
    if normalized_code == 31:
        return "BOSS 返回 code:31（请求受限）"
    detail = str(message or "").strip()
    return f"BOSS 返回 code:{normalized_code}" + (f"（{detail[:80]}）" if detail else "")


def detail_page_hint(url: Any) -> str:
    """Classify a terminal detail-page URL without retaining its query string."""
    normalized = str(url or "").strip().lower()
    if normalized == "about:blank":
        return "详情页停留在 about:blank"
    return ""