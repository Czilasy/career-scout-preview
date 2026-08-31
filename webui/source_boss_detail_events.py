"""BOSS 详情事件归类纯助手（034 拆分自 webui/source_boss_cdp_detail.py）。

fetch_details_batch 在子进程非零退出时按事件文件真实 safe_code 逐岗位归类：
账号级阻断（验证码/限流/登录失效/IP风控）与单条软失败由此区分，不再只用
退出码粗分类把真实原因二次丢弃。纯函数，不依赖 adapter 实例状态。
"""

from __future__ import annotations

from typing import Any


def index_events_by_url(events: list[Any], expected_urls: set[str]) -> dict[str, dict]:
    """校验 detail 事件并返回 ``{job_link: event}``（first wins）。

    事件文件的 ``job_id`` 即 ``job_link``（source_url）。只做基础校验
    （dict / kind=detail / job_id 在预期集合内 / 非重复），完整隐私校验
    在成功路径（returncode==0）由 ``_validate_detail_event`` 承担；非零
    退出是抢救场景，安全码后续统一经注册表 ``resolve_code`` 兜底。
    """
    matched: dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("kind") != "detail":
            continue
        job_id = event.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            continue
        if job_id not in expected_urls or job_id in matched:
            continue
        matched[job_id] = event
    return matched


def event_outcome_code(event: Any, fallback_code: str) -> str:
    """从事件取该岗位失败码；无事件/无法归类时回退 ``fallback_code``。

    - status=unavailable/failed → safe_code（如 source_rate_limited）；
    - status=cancelled → safe_code，空或 "ok" 时回退 source_unknown_error；
    - completed 事件（detail 已在抢救产物中标成功）或异常状态 → fallback。
    """
    if not isinstance(event, dict):
        return fallback_code
    status = event.get("status")
    safe_code = str(event.get("safe_code") or "")
    if status in ("unavailable", "failed"):
        return safe_code or fallback_code
    if status == "cancelled":
        return safe_code if safe_code and safe_code != "ok" else "source_unknown_error"
    return fallback_code
