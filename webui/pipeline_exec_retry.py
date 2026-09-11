"""偶发失败一次重试的判定与白箱事实构造（039）。

纯逻辑模块：只判定失败码是否属于偶发可重试白名单，构造白箱
``retry_scheduled`` 事实与断点续抓字段，不做 IO、不依赖平台 adapter。
由 ``webui/pipeline_exec_search`` 单向调用。
"""

from __future__ import annotations

from typing import Any

from webui.source_breaker import PageEventPersistenceError, SourceOutcome

# 偶发可重试失败码（注册表正名码）：超时、网络波动、页面临时异常与本地写入失败。
# 浏览器连接中断（source_cdp_unavailable）由既有失联重启通道优先接走，不在此列；
# 登录失效、验证码、限流、封禁、请求上限、输入漂移等需人工介入的失败也不在此列。
TRANSIENT_RETRY_CODES: frozenset[str] = frozenset({
    "source_timeout",
    "source_unreachable",
    "source_invalid_output",
    "source_unknown_error",
    "source_result_write_failed",
})


def is_transient_retryable(failed_code: object, *, retry_used: bool) -> bool:
    """失败码属偶发白名单且本组合尚未使用重试额度时返回 True。"""
    if retry_used:
        return False
    return str(failed_code or "").strip() in TRANSIENT_RETRY_CODES


def retry_event_payload(*, failed_code: object, reason: object) -> dict[str, Any]:
    """构造白箱 retry_scheduled 事件的 payload（简短原因，不含敏感内容）。"""
    return {
        "failed_code": str(failed_code or "source_unknown_error"),
        "error_reason": str(reason or "").strip()[:200],
        "retry_policy": "once",
    }


def retry_event_fact(combo_key: str, outcome: Any) -> dict[str, Any]:
    """构造 record_fact 可写入的 retry_scheduled 事实（info、非必需证据）。

    每组合每轮最多一条：幂等键不随尝试号变化，重复调用不会追加第二条。
    """
    reason = str(getattr(outcome, "failed_reason", "") or "").strip()
    safe_log = str(getattr(outcome, "safe_log", "") or "")
    if not reason and "reason=" in safe_log:
        reason = safe_log.split("reason=", 1)[1].strip()
    return {
        "idempotency_key": f"retry-scheduled:{combo_key}",
        "event_type": "retry_scheduled",
        "stage": "scrape_list",
        "unit_kind": "keyword_city",
        "unit_key": combo_key,
        "attempt_no": 1,
        "required_evidence": False,
        "severity": "info",
        "payload": retry_event_payload(
            failed_code=getattr(outcome, "failed_code", None),
            reason=reason,
        ),
    }


def retry_resume_fields(
    resume_pages: dict[str, int] | None,
    resume_jobs: dict[str, list[dict]] | None,
    combo_key: str,
    pages: int,
) -> dict[str, Any]:
    """重试从断点续抓：给出恢复起始页与已抓岗位快照，不重抓已完成页。"""
    resume_page = max(1, int((resume_pages or {}).get(combo_key, 1)))
    return {
        "start_page": min(resume_page, max(1, int(pages))),
        "existing_jobs": (resume_jobs or {}).get(combo_key),
    }


def retry_failure_outcome(exc: BaseException) -> SourceOutcome:
    """重试抓取本身抛出的编排层异常：转成内部错误失败结果，走既有硬停分类。"""
    if isinstance(exc, PageEventPersistenceError):
        reason = "页级快照持久化失败"
    else:
        reason = f"抓取执行失败（{type(exc).__name__}）"
    return SourceOutcome.failure(failed_code="internal_error", failed_reason=reason)
