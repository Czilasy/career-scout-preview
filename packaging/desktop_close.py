"""045 B102：桌面关闭前的未完成流程保存决策域。

桌面壳只接线本模块；HTTP 与用户确认均可注入。规则：
- 无未结束流程：直接关闭；
- 未结束且岗位数为 0：静默关闭，不落空轮；
- 未结束且有岗位：确认后先结束保存，成功才关闭；取消/失败不关闭。
"""

from __future__ import annotations

from typing import Any, Callable

CLOSE_CONFIRM_TEXT = "还有未完成的流程，是否保存到结果页？"
CLOSE_CONFIRM_TITLE = "Career Scout"


def _job_count(latest: dict[str, Any]) -> int:
    for key in ("job_count", "scraped_count"):
        try:
            value = int(latest.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def decide_close(
    host,
    *,
    confirm: Callable[[], bool],
) -> bool:
    """Return True only when the window may continue closing.

    ``host`` needs ``latest_running_task()``, ``finish_run(run_id)`` and
    ``save_window_state()``. Any state-query failure must keep the window
    open rather than risking a silent unfinished round.
    """
    try:
        latest = host.latest_running_task()
    except Exception:
        return False
    if not latest or not latest.get("has_task"):
        host.save_window_state()
        return True
    if _job_count(latest) <= 0:
        host.save_window_state()
        return True
    try:
        if not confirm():
            return False
    except Exception:
        return False
    run_id = str(latest.get("task_id") or "")
    if not run_id:
        return False
    try:
        finish = host.finish_run(run_id)
    except Exception:
        return False
    ok = bool(finish.get("ok") if isinstance(finish, dict) else finish)
    # 结束保存接口对已终态轮返回 already_finished/already_terminal；此时
    # 该流程已有历史轮，关闭不应被重复保存语义卡住。
    saved = bool(ok) or str(finish.get("error") or "") in (
        "already_finished", "already_terminal",
    )
    if not saved:
        return False
    host.save_window_state()
    return True
