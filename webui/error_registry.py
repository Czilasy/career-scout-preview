"""Single error code registry for Career Scout.

All stable codes, user-facing messages, blocking/retryable semantics and
legacy aliases live here.  Existing modules import derived views from this
module instead of redefining code sets.  Unknown codes fail validation in
tests; runtime keeps an explicit visible fallback instead of silent
mapping.
"""

from __future__ import annotations

from typing import Any

from webui.logging_setup import get_logger

# AI internal error codes kept as module-level names for compatibility.
ERROR_TIMEOUT = "timeout"
ERROR_AUTH = "auth_failed"
ERROR_NETWORK = "network_error"
ERROR_INVALID = "invalid_response"
ERROR_RATE_LIMIT = "rate_limited"
ERROR_TRUNCATED = "truncated"
ERROR_NOT_CONFIGURED = "ai_not_configured"
ERROR_QUOTA_EXHAUSTED = "quota_exhausted"
ERROR_SERVER = "server_error"


class UnknownErrorCode(ValueError):
    """Raised when a code is not present in the registry."""

_LOGGER = get_logger("error_registry")


def _entry(
    code: str,
    category: str,
    *,
    blocking: bool = False,
    retryable: bool = False,
    user_message: str = "",
    impact: str = "independent",
    reason: str = "",
    resume_condition: str = "",
    aliases: tuple[str, ...] = (),
) -> dict[str, Any]:
    message = user_message or code
    return {
        "code": code,
        "category": category,
        "blocking": blocking,
        "retryable": retryable,
        "user_message": message,
        "impact": impact,
        "reason": reason or message,
        "resume_condition": resume_condition,
        "aliases": sorted(aliases),
    }


_SOURCE_CODES: dict[str, dict[str, Any]] = {
    "source_cdp_unavailable": _entry(
        "source_cdp_unavailable", "source",
        blocking=True, retryable=True,
        user_message="连不上调试浏览器", impact="systemic",
        resume_condition="启动 Chrome 调试端口后点继续",
        aliases=("cdp_unavailable",),
    ),
    "source_request_limit_exceeded": _entry(
        "source_request_limit_exceeded", "source",
        blocking=True, retryable=True,
        user_message="本轮抓取请求数已达上限", impact="systemic",
        reason="单次抓取运行累计请求超过 999 上限，已停止避免触发平台风控",
        resume_condition="请求计数已按轮次隔离，下一轮可继续", aliases=(),
    ),
    "source_login_required": _entry(
        "source_login_required", "source",
        blocking=True, retryable=True,
        user_message="登录已失效，需重新登录", impact="systemic",
        resume_condition="重新登录后点继续",
        aliases=("login_expired",),
    ),
    "source_verification_required": _entry(
        "source_verification_required", "source",
        blocking=True, retryable=True,
        user_message="触发验证码/滑块，需手动完成", impact="systemic",
        resume_condition="完成验证码后点继续",
        aliases=("captcha_required",),
    ),
    "source_rate_limited": _entry(
        "source_rate_limited", "source",
        blocking=True, retryable=True,
        user_message="账号/操作频繁被限流", impact="systemic",
        resume_condition="等待限流解除后点继续",
    ),
    "source_account_restricted": _entry(
        "source_account_restricted", "source",
        blocking=True, retryable=True,
        user_message="确认账号/平台受限", impact="systemic",
        resume_condition="到自动化浏览器查看提示并处理后点继续",
    ),
    "source_status_unclear": _entry(
        "source_status_unclear", "source",
        retryable=True,
        user_message="暂时无法确认平台状态", impact="independent",
        resume_condition="可直接重试或稍后再试",
    ),
    "source_blocked": _entry(
        "source_blocked", "source",
        blocking=True, retryable=True,
        user_message="IP 级风控拦截", impact="systemic",
        resume_condition="更换网络或等待后点继续",
        aliases=("ip_risk_control",),
    ),
    "source_unreachable": _entry(
        "source_unreachable", "source",
        blocking=True, retryable=True,
        user_message="抓取脚本不可用", impact="systemic",
        resume_condition="检查脚本或环境后点继续",
    ),
    "source_not_found": _entry(
        "source_not_found", "source",
        user_message="岗位不存在或已下架",
    ),
    "source_invalid_output": _entry(
        "source_invalid_output", "source",
        retryable=True,
        user_message="输入校验失败或页面解析异常",
        resume_condition="可单条补抓重试",
    ),
    "source_input_drift": _entry(
        "source_input_drift", "source",
        retryable=True,
        user_message="输入快照与执行不一致",
        resume_condition="重新确认搜索范围后重试",
    ),
    "source_timeout": _entry(
        "source_timeout", "source",
        retryable=True,
        user_message="抓取超时",
        resume_condition="可重试抓取",
    ),
    "source_unknown_error": _entry(
        "source_unknown_error", "source",
        retryable=True,
        user_message="未知抓取错误",
    ),
    "source_result_write_failed": _entry(
        "source_result_write_failed", "source",
        retryable=True,
        user_message="结果文件写入失败",
        impact="independent",
        resume_condition="可重试抓取",
    ),
}


_TAXONOMY_CODES: dict[str, dict[str, Any]] = {
    # 016-error-module-rework：平台侧重复码（captcha_required/login_expired/
    # ip_risk_control/cdp_unavailable）已收敛为 source_* 正名码的别名，
    # 历史数据展示经 ALIAS_TO_CODE 解析。
    "ai_rate_limited": _entry(
        "ai_rate_limited", "ai",
        blocking=True, retryable=True,
        user_message="AI 服务限流，请求过于频繁", impact="systemic",
        resume_condition="等待限流解除后点继续",
    ),
    "ai_quota_exhausted": _entry(
        "ai_quota_exhausted", "ai",
        blocking=True,
        user_message="AI 额度已耗尽", impact="systemic",
        resume_condition="充值或更换密钥后点继续",
    ),
    "ai_key_invalid": _entry(
        "ai_key_invalid", "ai",
        blocking=True,
        user_message="AI 密钥失效或鉴权失败", impact="systemic",
        resume_condition="更换有效密钥后点继续",
    ),
    "ai_network_error": _entry(
        "ai_network_error", "ai",
        blocking=True, retryable=True,
        user_message="AI 网络或服务故障", impact="systemic",
        resume_condition="网络恢复后点继续",
    ),
    "job_offline": _entry(
        "job_offline", "job",
        user_message="岗位已下架",
        resume_condition="无需继续，该岗位进入待确认",
    ),
    "detail_timeout": _entry(
        "detail_timeout", "job",
        retryable=True,
        user_message="单岗位详情抓取超时",
        resume_condition="可单条补抓重试",
    ),
    "detail_invalid": _entry(
        "detail_invalid", "job",
        user_message="详情结构无效（登录墙/导航壳/空壳）",
        resume_condition="可单条补抓",
    ),
    "ai_missing_job": _entry(
        "ai_missing_job", "ai",
        retryable=True,
        user_message="AI 漏回单个岗位判定",
        resume_condition="可单条补抓重试",
    ),
    "internal_error": _entry(
        "internal_error", "internal",
        blocking=True,
        user_message="内部状态或持久化错误", impact="systemic",
        resume_condition="需人工排查日志",
    ),
}


_AI_INTERNAL_CODES: dict[str, dict[str, Any]] = {
    ERROR_TIMEOUT: _entry(
        ERROR_TIMEOUT, "ai",
        blocking=True, retryable=True,
        user_message="AI 响应超时，请稍后重试",
        aliases=("ERROR_TIMEOUT",),
    ),
    ERROR_AUTH: _entry(
        ERROR_AUTH, "ai",
        blocking=True,
        user_message="API 密钥无效或已过期，请检查 AI 设置",
        aliases=("ERROR_AUTH",),
    ),
    ERROR_NETWORK: _entry(
        ERROR_NETWORK, "ai",
        blocking=True, retryable=True,
        user_message="无法连接 AI 服务，请检查网络与地址配置",
        aliases=("ERROR_NETWORK",),
    ),
    ERROR_INVALID: _entry(
        ERROR_INVALID, "ai",
        retryable=True,
        user_message="AI 返回了无法解析的内容，请重试",
        aliases=("ERROR_INVALID",),
    ),
    ERROR_RATE_LIMIT: _entry(
        ERROR_RATE_LIMIT, "ai",
        blocking=True, retryable=True,
        user_message="AI 服务限流（免费额度），请稍候再试",
        aliases=("ERROR_RATE_LIMIT",),
    ),
    ERROR_TRUNCATED: _entry(
        ERROR_TRUNCATED, "ai",
        retryable=True,
        user_message="AI 返回内容被截断，请重试（可减小单批数量）",
        aliases=("ERROR_TRUNCATED",),
    ),
    ERROR_NOT_CONFIGURED: _entry(
        ERROR_NOT_CONFIGURED, "ai",
        blocking=True,
        user_message="AI 未配置，请先设置 API 地址和密钥",
        aliases=("ERROR_NOT_CONFIGURED",),
    ),
    ERROR_QUOTA_EXHAUSTED: _entry(
        ERROR_QUOTA_EXHAUSTED, "ai",
        blocking=True,
        user_message="AI 额度已用完，请明天再试或更换 API 密钥",
        aliases=("ERROR_QUOTA_EXHAUSTED",),
    ),
    ERROR_SERVER: _entry(
        ERROR_SERVER, "ai",
        blocking=True, retryable=True,
        user_message="AI 服务暂时不可用，请稍后重试",
        aliases=("ERROR_SERVER",),
    ),
}


_CONTROL_CODES: dict[str, dict[str, Any]] = {
    "resumed": _entry(
        "resumed", "internal",
        user_message="任务已恢复",
    ),
    "user_finished": _entry(
        "user_finished", "internal",
        user_message="用户主动结束",
    ),
}


_PLATFORM_CODES: dict[str, dict[str, Any]] = {
    code: _entry(code, "platform", user_message=code)
    for code in (
        "platform_validation_failed", "platform_disabled",
        "platform_schema_unavailable", "filter_schema_version_mismatch",
        "filter_snapshot_incompatible", "city_mapping_unavailable",
        "city_mapping_missing", "search_filters_not_supported",
        "job_identity_conflict", "platform_url_mismatch",
        "task_input_mismatch", "run_identity_conflict",
        "mixed_platform_jobs", "non_pending_platform_job_ids",
        "result_not_clearable", "login_space_conflict",
        "legacy_platform_not_supported", "tuning_platform_mismatch",
        "migration_backup_failed", "migration_failed",
    )
}


_FEEDBACK_CODES: dict[str, dict[str, Any]] = {
    code: _entry(code, "api", user_message=code)
    for code in (
        "invalid_action", "invalid_action_payload",
        "profile_not_found", "job_not_found", "idempotency_conflict",
        "state_precondition_failed", "job_identity_incomplete",
        "applied_at_required", "applied_at_invalid",
        "applied_at_in_future", "follow_up_before_application",
        "persistence_failed", "not_found", "invalid_limit",
        "reminder_not_eligible", "unknown_error",
    )
}


REGISTRY: dict[str, dict[str, Any]] = {}
for _group in (
    _SOURCE_CODES, _TAXONOMY_CODES, _AI_INTERNAL_CODES,
    _CONTROL_CODES, _PLATFORM_CODES, _FEEDBACK_CODES,
):
    REGISTRY.update(_group)

ERROR_CODES = frozenset(REGISTRY)

SAFE_FAILURE_CODES = frozenset(_SOURCE_CODES)
ERROR_TAXONOMY = {
    code: dict(entry) for code, entry in REGISTRY.items()
    if entry["category"] in ("source", "ai", "platform", "internal", "job")
}
FAILED_CODE_LABELS = {
    code: str(entry["user_message"])
    for code, entry in REGISTRY.items()
}
# 016-error-module-rework：阻断集合改由注册表标记推导（blocking 且 systemic），
# 实际定义移至 ALIAS_TO_CODE 之后（集合需吸收历史别名闭包，见下）。占位的
# 旧手工并集已删除。
INDEPENDENT_FAILURE_CODES = frozenset({
    "job_offline", "detail_timeout", "detail_invalid", "ai_missing_job",
    "source_status_unclear",
})
SYSTEMIC_AI_ERROR_CODES = frozenset({
    ERROR_RATE_LIMIT, ERROR_QUOTA_EXHAUSTED, ERROR_AUTH,
    ERROR_NETWORK, ERROR_TIMEOUT, ERROR_SERVER,
})
ERROR_USER_MESSAGES = {
    code: str(entry["user_message"])
    for code, entry in REGISTRY.items()
}
AI_TAXONOMY_TARGETS = {
    ERROR_RATE_LIMIT: "ai_rate_limited",
    ERROR_QUOTA_EXHAUSTED: "ai_quota_exhausted",
    ERROR_AUTH: "ai_key_invalid",
    ERROR_NETWORK: "ai_network_error",
    ERROR_TIMEOUT: "ai_network_error",
    ERROR_SERVER: "ai_network_error",
}

# 历史码 → 唯一正名码（016-error-module-rework：双套码收敛后的兼容层）。
ALIAS_TO_CODE: dict[str, str] = {}
for _code, _entry_data in REGISTRY.items():
    for _alias in _entry_data.get("aliases", ()):
        ALIAS_TO_CODE[_alias] = _code


def _derived_systemic_block_codes() -> frozenset[str]:
    """blocking 且 systemic 的正名码，外加目标为阻断码的历史别名闭包。

    消费方（store/app/pipeline）对 DB 与事件流里的旧码做成员判定时无需
    先归一；文案与展示仍走正名码。
    """
    canonical = frozenset({
        code for code, entry in REGISTRY.items()
        if entry["blocking"] and entry["impact"] == "systemic"
    })
    aliases = frozenset({
        alias for alias, target in ALIAS_TO_CODE.items()
        if target in canonical
    })
    return canonical | aliases


SYSTEMIC_BLOCK_CODES = _derived_systemic_block_codes()


def validate_code(code: str) -> str:
    """Return the canonical code or raise for unknown codes."""
    if not isinstance(code, str) or not code:
        raise UnknownErrorCode(code)
    if code not in REGISTRY:
        raise UnknownErrorCode(code)
    return code


def resolve_code(code: object, *, default: str = "internal_error") -> str:
    """Normalize a runtime error code with a visible fallback.

    Known codes pass through unchanged; registered aliases (historical
    duplicate codes) resolve to their canonical code.  Unknown or empty
    codes emit a warning with the original value and return ``default``
    so runtime paths never silently keep an unregistered code.
    """
    if isinstance(code, str):
        if code in REGISTRY:
            return code
        alias_target = ALIAS_TO_CODE.get(code)
        if alias_target:
            return alias_target
    original = str(code or "")
    _LOGGER.warning(
        "runtime error code not in registry; falling back to %s: %r",
        default, original,
        extra={"structured_data": {
            "event": "unknown_error_code", "error_code": original,
            "fallback": default,
        }},
    )
    return default


def to_json() -> dict[str, dict[str, Any]]:
    """Stable JSON projection used by the frontend mirror test."""
    return {
        code: dict(entry)
        for code, entry in sorted(REGISTRY.items())
    }
