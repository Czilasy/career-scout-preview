"""043：未收尾流程的一次性提醒（判定、记号、文案）。

规则（Spec 043 FR-001~006）：
- 未收尾流程最多被主动提醒一次（按应用打开次数派生）；
- 只认最新一枚，更旧的沉默、不接力（以提醒水位实现：不晚于水位的流程一律沉默）；
- 提醒只约束"主动弹出"（灵动岛一行字 + 结果接回），不影响数据留存，
  也不影响用户从历史等主动入口查看。
"""

from __future__ import annotations

from typing import Any


def is_notice_silent(store, run: dict[str, Any] | None, profile_id: str | None = None) -> bool:
    """该流程本次启动是否保持沉默（已提醒过，或比提醒水位更旧）。"""
    if not run:
        return True
    if str(run.get("notice_sent_at") or ""):
        return True
    watermark = store.run_notice_watermark(
        profile_id or str(run.get("profile_id") or "") or None
    )
    if not watermark:
        return False
    formed_at = str(run.get("created_at") or run.get("updated_at") or "")
    return bool(formed_at) and formed_at <= str(watermark)


def mark_notice_sent(store, run_id: str) -> bool:
    """写入"提醒已发出"记号（幂等；水位随之推进）。"""
    if not run_id:
        return False
    return bool(store.mark_run_notice_sent(str(run_id)))


def build_notice_payload(run: dict[str, Any] | None, job_count: int) -> dict[str, Any] | None:
    """灵动岛一行提醒载荷（含来历：时间与岗位数口径）。"""
    if not run:
        return None
    count = max(0, int(job_count or 0))
    formed = str(run.get("created_at") or "")
    date_label = ""
    if len(formed) >= 10:
        try:
            date_label = f"{int(formed[5:7])}月{int(formed[8:10])}日"
        except ValueError:
            date_label = formed[:10]
    message = f"上次有 1 轮只抓取、未筛选（{date_label} · {count} 个岗位），已为你接回"
    return {
        "kind": "run_notice",
        "run_id": str(run.get("id") or ""),
        "date_label": date_label,
        "job_count": count,
        "message": message,
    }
