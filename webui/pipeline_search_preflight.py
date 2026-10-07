"""抓取执行域：源失败辅助与 Chrome/登录预检编排（047 结构前置自 pipeline_exec_search 搬运）。

只搬运既有实现：函数体、返回载荷与错误分类语义保持不变。
调用方把 facade、白箱 evidence、进度 emit、组合清单与 stop_event 显式注入，
预检失败返回原失败 payload，成功返回 None，由调用方继续原 _finish 路径。
"""

from __future__ import annotations

from webui.error_registry import SYSTEMIC_BLOCK_CODES as _HARD_STOP_CODES
from webui.error_registry import resolve_code
from webui.pipeline_exec_status import (
    classify_preflight_failure,
    user_visible_failure_reason,
)
from webui.source import SourceOutcome
from webui.whitebox import ScrapeEvidence


def _canonical_source_code(code: object) -> str | None:
    raw_code = str(code or "").strip()
    if not raw_code:
        return None
    resolved = resolve_code(raw_code, default="source_unknown_error")
    return resolved if resolved.startswith("source_") else None


def _is_source_hard_stop(code: object) -> bool:
    resolved = _canonical_source_code(code)
    return bool(resolved and resolved in _HARD_STOP_CODES)


def _record_source_hard_stop_evidence(
    evidence: ScrapeEvidence,
    combos: list[dict],
    code: object,
    diagnostic: object = "",
    *,
    excluded_keys: set[str] | None = None,
    platform: str = "",
) -> tuple[str, str] | None:
    """Project one source block across all not-yet-completed scrape units."""
    canonical = _canonical_source_code(code)
    if canonical is None:
        return None
    reason = user_visible_failure_reason(canonical, diagnostic, platform)
    failure = SourceOutcome.failure(
        failed_code=canonical,
        failed_reason=reason,
    )
    excluded = {str(key) for key in (excluded_keys or set())}
    for combo in combos:
        key = str(
            combo.get("combo_key")
            or f"{combo.get('keyword', '')}|{combo.get('city', '')}"
        )
        if key not in excluded:
            evidence.failed(key, failure, reason=reason)
    return canonical, reason


def run_source_preflight(
    *,
    source,
    facade,
    evidence: ScrapeEvidence,
    combos: list[dict],
    emit,
    stop_event,
    platform: str,
) -> dict | None:
    """Run the ensure-chrome/preflight boundary for one scrape run.

    Returns the original failure payload when the run must stop before any
    combination executes, or None when the boundary passed.  The caller
    keeps ownership of ``_finish`` so the whitebox conclusion is written
    exactly once on the original call path.
    """
    # Auto-launch the debug Chrome if it isn't running, so the user is shown
    # the browser instead of a raw infrastructure error.
    emit(stage="ensure_chrome", message="检查并启动调试浏览器…")
    cdp_port = getattr(source, "cdp_port", None)
    chrome_ok, chrome_err = facade.ensure_chrome_ready(
        cdp_port, minimize_after_launch=True, stop_event=stop_event,
    )
    if not chrome_ok:
        source_code, source_reason = _record_source_hard_stop_evidence(
            evidence, combos, "source_cdp_unavailable", chrome_err,
            platform=platform,
        )
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "hard_stop": True,
                "hard_stop_code": source_code,
                "error": source_reason}

    # Preflight: CDP connection + current platform login.
    emit(stage="preflight", message="检查当前平台登录状态…")
    pre = source.preflight()
    if not pre.ok:
        result = {"ok": False, "jobs": [], "total_scraped": 0,
                  "total_matched": 0, "combinations": len(combos),
                  **classify_preflight_failure(pre)}
        if result.get("hard_stop"):
            source_failure = _record_source_hard_stop_evidence(
                evidence, combos, result.get("hard_stop_code"),
                result.get("error"), platform=platform,
            )
            if source_failure is not None:
                result["hard_stop_code"], result["error"] = source_failure
        else:
            # 047 C1：unknown 等非 hard_stop 预检同样要在白箱里留下真实
            # 阻断原因；未执行单元保留「未执行」含义，不伪造完成/空结果。
            blocked_code = str(result.get("failed_code") or "source_unknown_error")
            blocked_reason = str(result.get("error") or blocked_code)
            evidence.blocked_before_start(blocked_code, blocked_reason)
        return result
    return None


__all__ = [
    "_canonical_source_code", "_is_source_hard_stop",
    "_record_source_hard_stop_evidence", "run_source_preflight",
]
