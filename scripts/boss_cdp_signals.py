"""Pure, safe classifications for BOSS CDP runtime responses.

016-error-module-rework 后本模块是 BOSS 侧信号判定的单一来源：
- 平台风控/限流关键词表（从 boss_cdp_raw 迁入，避免三处口径漂移）；
- 结构化失败行（``__CAREERSCOUT_FAILED__``）的产出与解析契约；
- 列表页诊断的实锤分档（confirmed / retry / stop）。

分档原则（B069）：单次异常不定罪；验证码/风控页、平台明确请求受限、
重试后复现的访问拦截才算实锤；其余一律"暂时无法确认"。
"""

from __future__ import annotations

import re
from typing import Any

RISK_API_CODES = frozenset({31, 37})

# 风控/验证码特征词：命中即实锤（在 API 返回的错误样本或页面文本里找）
RISK_CONTROL_KEYWORDS = (
    "安全验证", "滑动验证", "滑块", "访问受限", "异常流量", "操作频繁",
    "captcha", "CAPTCHA", "verify-sliding", "waf",
)
# 泛化限流特征：仅用于从已知风控文本里提取提示语（extract_block_hint），
# 不作为判定入口。
RATE_LIMIT_KEYWORDS = (
    "操作频繁", "访问受限", "异常流量", "频繁", "限流", "rate limit",
    "too many", "429", "稍后再试", "账号受限", "解锁", "冻结",
)
# 详情页限流判定专用：只认高置信度的限流特征。
# 裸词“频繁/解锁/冻结”会命中页面 chrome（如“登录解锁更多职位”），
# 在 JD 提取失败时误判成限流页，把没被封的账号误停成“限流”（用户反馈回归）。
DETAIL_RATE_LIMIT_KEYWORDS = (
    "操作频繁", "访问受限", "异常流量", "限流", "rate limit",
    "too many", "429", "稍后再试", "账号受限",
)

# ---------------------------------------------------------------------------
# 结构化失败行（脚本 → webui 的唯一权威分类来源）
# ---------------------------------------------------------------------------

FAILURE_LINE_PREFIX = "__CAREERSCOUT_FAILED__"
_FAILURE_LINE_RE = re.compile(
    re.escape(FAILURE_LINE_PREFIX)
    + r"\s+code=(?P<code>[a-z0-9_]+)(?:\s+hint=(?P<hint>.*))?\s*$"
)


def emit_failure_line(code: str, hint: str = "") -> None:
    """打印结构化失败行；webui 只认这一行定类，输出其余内容不参与分类。"""
    safe_hint = " ".join(str(hint or "").split())[:120]
    print(f"{FAILURE_LINE_PREFIX} code={code} hint={safe_hint}")


def map_block_exception(exc: BaseException) -> tuple[str, int]:
    """账号级阻断异常 → (失败行 code, 退出码)。034 脚本主入口薄映射用。

    - ``RiskControlError``（风控/限流/验证码/登录失效）→ code 取异常自带码，
      缺码回退 ``source_status_unclear``，退出码 10；
    - ``LoginRequiredError`` → ``source_login_required``，退出码 1；
    - ``RequestLimitExceededError`` → ``source_request_limit_exceeded``，退出码 11。
    失败行是 webui 唯一权威分类来源，退出码只作缺失败行时的兜底。
    """
    from scripts.boss.exceptions import (
        LoginRequiredError, RequestLimitExceededError, RiskControlError,
    )
    if isinstance(exc, RiskControlError):
        code = getattr(exc, "code", "") or "source_status_unclear"
        return code, 10
    if isinstance(exc, LoginRequiredError):
        return "source_login_required", 1
    if isinstance(exc, RequestLimitExceededError):
        return "source_request_limit_exceeded", 11
    raise TypeError(f"unsupported block exception: {type(exc).__name__}")


def parse_failure_line(text: Any) -> tuple[str, str] | None:
    """从输出全文取最后一行失败标记；无标记返回 None。"""
    if not text:
        return None
    match = None
    for line in str(text).splitlines():
        found = _FAILURE_LINE_RE.search(line.strip())
        if found:
            match = found
    if match is None:
        return None
    return match.group("code"), (match.group("hint") or "").strip()[:120]


# ---------------------------------------------------------------------------
# 列表页诊断分档
# ---------------------------------------------------------------------------

# 实锤：立即停止并以该码硬退出
VERDICT_CONFIRMED = "confirmed"
# 单次可疑：调用方原地重试本页一次（单次不定罪）
VERDICT_RETRY = "retry"
# 重试后仍异常：停止翻页；有已抓数据则正常收尾，无数据按软失败退出
VERDICT_STOP = "stop"

# 单次即实锤的拦截状态码（401 是登录语义，另走 login）
_BLOCK_STATUS_CONFIRMED_ONCE = frozenset({401})
# 拦截类状态码：重试后复现才实锤（412/418 是 BOSS 风控拦截页常用状态）
_BLOCK_STATUS_REPEAT_CONFIRMED = frozenset({403, 412, 418, 429})
# 结构异常类诊断：重试后仍异常 → status_unclear
_STRUCTURE_ANOMALY_KINDS = frozenset({
    "parse_failed", "unexpected_shape", "js_exception", "empty_response",
})


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


def looks_like_risk_control(text: Any) -> bool:
    """文本里是否含风控/验证码特征词（只用于异常响应样本，不扫岗位正文）。"""
    if not text:
        return False
    return any(keyword in text for keyword in RISK_CONTROL_KEYWORDS)


def looks_like_rate_limited(text: Any) -> bool:
    """文本里是否含账号/操作频率限流特征词（仅提示语提取用途）。"""
    if not text:
        return False
    return any(keyword in text for keyword in RATE_LIMIT_KEYWORDS)


def looks_like_detail_rate_limited(text: Any) -> bool:
    """详情页专用：只匹配高置信度限流特征，避免页面 chrome 词汇误判。"""
    if not text:
        return False
    return any(keyword in text for keyword in DETAIL_RATE_LIMIT_KEYWORDS)


def classify_list_diagnosis(
    diagnosis: Any, *, repeated: bool = False,
) -> tuple[str | None, str, str]:
    """BOSS 列表页诊断 → (verdict, failed_code, hint)。

    verdict 为 None 表示正常（含正常空页），调用方按原流程继续。
    """
    if not diagnosis:
        return None, "", ""
    kind = diagnosis.get("kind", "")
    sample = str(diagnosis.get("sample", "") or "")

    if looks_like_risk_control(sample):
        return (
            VERDICT_CONFIRMED, "source_verification_required",
            f"返回内容出现验证码/风控特征：{sample[:80]}",
        )

    if kind == "api_code":
        code = diagnosis.get("code", 0)
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = 0
        if code == 31:
            return VERDICT_CONFIRMED, "source_rate_limited", api_code_hint(31, sample)
        if code == 37:
            if repeated:
                return VERDICT_STOP, "source_status_unclear", api_code_hint(37, sample)
            return VERDICT_RETRY, "source_status_unclear", api_code_hint(37, sample)
        return None, "", ""

    if kind == "http_error":
        try:
            status = int(diagnosis.get("status", 0))
        except (TypeError, ValueError):
            status = 0
        if status in _BLOCK_STATUS_CONFIRMED_ONCE:
            return VERDICT_CONFIRMED, "source_login_required", "列表接口返回 HTTP 401（登录态失效）"
        if status in _BLOCK_STATUS_REPEAT_CONFIRMED:
            if repeated:
                return VERDICT_CONFIRMED, "source_rate_limited", f"列表接口返回 HTTP {status}（重试后仍被拦截）"
            return VERDICT_RETRY, "source_status_unclear", f"列表接口返回 HTTP {status}，重试本页确认"

    if kind in _STRUCTURE_ANOMALY_KINDS:
        if repeated:
            return VERDICT_STOP, "source_status_unclear", f"页面结构异常（{kind}），重试后仍无法读取"
        return VERDICT_RETRY, "source_status_unclear", f"页面结构异常（{kind}），重试本页"

    return None, "", ""
