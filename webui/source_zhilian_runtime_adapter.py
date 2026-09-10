"""智联 source 的增量运行时适配边界。

这里只承载本轮新增的 signal 映射扩展、批量降级归一和切号后预检回调；
抓取流程、状态机与公共错误语义仍由 ``source_zhilian_cdp`` 负责。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


_ZHILIAN_DETAIL_SIGNAL_ADDITIONS: Mapping[str, str] = {
    "cdp_unavailable": "source_cdp_unavailable",
    # 批次未跑完（worker 异常退出等）且无平台级信号：如实归类为「抓取中断」，
    # 不再兜底成 source_status_unclear（那会把「我们没抓」说成「平台状态不明」）。
    "worker_incomplete": "detail_incomplete",
}


def build_zhilian_detail_signal_map(
    base_mapping: Mapping[str, str | None],
) -> dict[str, str | None]:
    """合并智联新增详情 signal，保留旧映射对象的兼容内容。"""
    return {**dict(base_mapping), **_ZHILIAN_DETAIL_SIGNAL_ADDITIONS}


def normalize_zhilian_detail_signal(
    signal: Any,
    mapping: Mapping[str, str | None],
    *,
    fallback: str = "source_unknown_error",
) -> str:
    """将详情/批量降级 signal 归一为明确 failed_code。"""
    failed_code = mapping.get(str(signal or ""), fallback)
    return str(failed_code or fallback)


def run_zhilian_preflight_after_profile_switch(
    preflight: Callable[[], Any],
) -> Any:
    """执行当前冻结 source 的切号后预检，不创建第二套预检流程。"""
    return preflight()
