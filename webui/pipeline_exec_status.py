"""失败码口径、taxonomy 理由与抓取进度权重（021 B7 自 pipeline_exec.py 搬运）。"""

from __future__ import annotations

from webui.pipeline_exec_settings import _MSG_ZHILIAN_LOGIN_REQUIRED
from webui.error_registry import ERROR_TAXONOMY, FAILED_CODE_LABELS as _FAILED_CODE_LABELS
from webui.error_registry import resolve_code




# ---------------------------------------------------------------------------
# 进度百分比与阶段文案
# ---------------------------------------------------------------------------
_SCRAPE_STAGE_WEIGHTS: dict[str, tuple[int, int]] = {
    "ensure_chrome": (1, 1),
    "preflight": (1, 1),
    "searching": (0, 100),
    "combo_done": (0, 100),
    "combo_failed": (0, 100),
    "waiting": (0, 100),
    "risk_warning": (0, 100),
    "closing_chrome": (0, 100),
    "done": (100, 100),
    "cancelled": (0, 0),
    "hard_stop": (0, 100),
}



_SCRAPE_STAGE_MESSAGES: dict[str, str] = {
    "ensure_chrome": "检查并启动调试浏览器…",
    "preflight": "检查平台登录状态…",
    "searching": "列表页抓取中…",
    "combo_done": "列表页抓取中…",
    "combo_failed": "部分组合抓取失败，继续中…",
    "waiting": "防限流等待中…",
    "risk_warning": "所有组合均失败，请检查登录、网络或平台提示…",
    "closing_chrome": "正在关闭调试浏览器…",
    "done": "抓取完成",
    "cancelled": "运行已取消",
}




# 平台专属文案覆盖（B013）：默认字典兼容 BOSS 语义，智联只覆盖登录类文案，
# 避免智联任务任何路径出现“BOSS 登录”等 BOSS 专属内容。
_PLATFORM_LABEL_OVERRIDES: dict[str, dict[str, str]] = {
    "zhilian": {
        "source_login_required": "智联登录已失效",
    },
}


_PLATFORM_TAXONOMY_OVERRIDES: dict[str, dict[str, str]] = {
    "zhilian": {
        "source_login_required": _MSG_ZHILIAN_LOGIN_REQUIRED,
    },
}



def failed_code_label(code: str, platform: str = "") -> str:
    """按平台返回 failed_code 的用户可读文案（别名先归一，016）。"""
    resolved = resolve_code(code) if code else code
    override = _PLATFORM_LABEL_OVERRIDES.get(str(platform or ""), {}).get(resolved)
    if override:
        return override
    return _FAILED_CODE_LABELS.get(resolved, resolved or "")



def taxonomy_reason(code: str, platform: str = "", fallback: str = "任务被阻断") -> str:
    """按平台返回 ERROR_TAXONOMY.reason，缺失时用 fallback（别名先归一，016）。"""
    resolved = resolve_code(code) if code else code
    override = _PLATFORM_TAXONOMY_OVERRIDES.get(str(platform or ""), {}).get(resolved)
    if override:
        return override
    taxonomy = ERROR_TAXONOMY.get(resolved, {})
    return str(taxonomy.get("reason") or fallback)







def _classify_detail_batch_exception(exc: Exception) -> str:
    """Map a batch-level detail failure to a systemic, user-visible code."""
    text = f"{type(exc).__name__}: {exc}".lower()
    cdp_markers = (
        "cdp_", "devtools", "websocket", "chrome", "browser", "session",
        "connection", "disconnected", "target closed",
    )
    if isinstance(exc, (ConnectionError, TimeoutError)) or any(
            marker in text for marker in cdp_markers):
        return "source_cdp_unavailable"
    return "internal_error"




def _scrape_overall_percent(stage: str, current: int, total: int) -> int:
    """把抓取 pipeline 的当前阶段映射到整体百分比（0-100）。

    ``current`` 必须是已确认真实完成的组合数；准备阶段最多象征 1%，
    搜索/等待/失败/收尾只反映已完成的真实组合，不使用时间或阶段权重假爬。"""
    start, end = _SCRAPE_STAGE_WEIGHTS.get(stage, (0, 100))
    if total <= 0:
        return start
    ratio = min(1.0, max(0.0, current / total))
    return min(100, round(start + (end - start) * ratio))




def _scrape_page_overall_percent(
    stage: str, current: int, total: int, page_progress: float | None = None,
) -> int:
    """按页级真实进度计算抓取整体百分比。

    ``page_progress`` 为当前组合已翻页比例（0-1）；列表阶段最多占 90%，
    剩余 10% 只由 closing_chrome/done 推进，避免任何阶段提前 100%。"""
    if stage in ("done", "closing_chrome"):
        return 100
    if stage == "cancelled":
        return 0
    if total <= 0:
        return 0
    if page_progress is None:
        return _scrape_overall_percent(stage, current, total)
    completed = min(1.0, max(0.0, current / total))
    page_share = (1.0 / total) * min(1.0, max(0.0, page_progress))
    ratio = completed + page_share
    return min(90, round(ratio * 90))
