"""智联默认 runner 与失败原因映射（021 拆分自 webui/source.py）。

调用 scripts/zhilian/ 域模块（search 的 preflight/fetch_list、detail 的
fetch_detail/scrape_details_batch）真实函数的默认 runner，以及智联
failed_code → 用户可读原因的映射。测试通过向 ZhilianCdpSource 注入替身
绕开真实 CDP；用户可见失败文案统一来自中央错误注册表。
"""

from __future__ import annotations

from webui.error_registry import ERROR_USER_MESSAGES, resolve_code


# ---------------------------------------------------------------------------
# 默认 runner：调用 scripts/zhilian/ 域模块的真实函数（031 B6 起直连域模块，
# 不再经 scripts/zhilian_cdp_raw.py 兼容门面）。
# preflight/list/detail 分别调用真实登录判定、搜索 API 与详情页抓取。
# ---------------------------------------------------------------------------

def _default_zhilian_preflight_runner(cdp_port: int) -> str:
    """默认 preflight runner：调用 zhilian.search.preflight。

    preflight 返回稳定 signal；本函数把 None 转为 "unreachable"，
    避免伪造成功。
    """
    try:
        from scripts.zhilian.search import preflight
    except ImportError:
        return "unreachable"
    result = preflight(cdp_port=cdp_port)
    if result is None:
        return "unreachable"
    return str(result)


def _default_zhilian_list_runner(plan_item: dict) -> tuple[str, list[dict], dict | None]:
    """默认 list runner：调用 zhilian.search.fetch_list 真实分支。"""
    try:
        from scripts.zhilian.search import fetch_list
    except ImportError:
        return "unreachable", [], None
    result = fetch_list(
        plan_item,
        on_page_completed=plan_item.get("on_page_completed"),
    )
    if len(result) == 2:
        signal, jobs = result
        evidence = None
    else:
        signal, jobs, evidence = result
    if signal is None:
        return "invalid_output", [], None
    return str(signal), list(jobs or []), evidence


def _default_zhilian_detail_runner(job: dict, *, detail_output_path: str | None = None) -> tuple[str, dict]:
    """默认 detail runner：调用 zhilian.detail.fetch_detail。

    fetch_detail 返回真实 signal；本函数把 None 转为 "not_found"，
    不伪造 JD。
    """
    try:
        from scripts.zhilian.detail import fetch_detail
    except ImportError:
        return "unreachable", {}
    signal, detail = fetch_detail(job, detail_output_path=detail_output_path)
    if signal is None:
        return "not_found", {}
    return str(signal), dict(detail or {})


def _default_zhilian_batch_detail_runner(
    list_data: dict, *,
    cdp_port: int, tab_pool_size: int,
    inter_job_gap_range: tuple[float, float], reset_every: int,
    event_callback=None, cancel_event=None,
) -> tuple[list[tuple[str, dict]], str | None]:
    """默认 batch runner：调用 zhilian.detail.scrape_details_batch 并行分支。

    返回 ``(per_item, degrade_signal)``；ImportError（环境缺脚本）时全部按
    skipped + unreachable 降级，不伪造成功。
    ``event_callback``：逐条完成心跳（026），透传给 scraper 的 worker。
    ``cancel_event``：025 立即停止取消信号，透传给 scraper 的 worker 检查点。
    """
    try:
        from scripts.zhilian.detail import scrape_details_batch
    except ImportError:
        count = len(list_data.get("jobs", []))
        return [("skipped", {})] * count, "unreachable"
    per_item, degrade_signal = scrape_details_batch(
        list_data,
        cdp_port=cdp_port,
        tab_pool_size=tab_pool_size,
        inter_job_gap_range=inter_job_gap_range,
        reset_every=reset_every,
        event_callback=event_callback,
        cancel_event=cancel_event,
    )
    normalized = []
    for signal, detail in per_item:
        sig = str(signal or "invalid_output")
        normalized.append((sig, dict(detail or {})))
    return normalized, degrade_signal


def _zhilian_failed_reason(failed_code: str) -> str:
    """把 source failed_code 委托给中央注册表生成用户可见文案。"""
    resolved = resolve_code(failed_code) if failed_code else ""
    return ERROR_USER_MESSAGES.get(resolved, "未知错误")
