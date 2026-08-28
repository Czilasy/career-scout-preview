"""暂停编排助手（025-screen-pause-round-reset，B076/B077）。

`webui/task_continue_api.py` 存量超行数预警线，暂停 API 的编排逻辑外迁至此，
`task_continue_api.py` 只保留路由/参数校验与响应组装（宪法：api 层只做组装）。

- ``pause_with_mode``：暂停 API 全量编排——校验 + graceful/immediate 分支 +
  幂等 + guard 联动（终止活动批子进程并清理批次登记）。
- ``cancel_task_cleanup``：取消路径清理 guard 批次登记（避免「继续」后被
  旧登记误判卡死触发额外重抓）。

引用方向：`task_continue_api.py → task_pause_support.py → ctx.pipeline_guard`
（经 ctx 注入，本文件不反向 import app / store / task_continue_api）。
"""

from __future__ import annotations

from flask import jsonify

from webui.logging_setup import get_logger

_logger = get_logger("task_pause_support")

#: 与 task_continue_api 一致的文案（外迁复制，避免反向 import 造成循环）。
_MSG_TASK_NOT_FOUND = "任务不存在或已被移除"


class ImmediateOnlyCancelEvent:
    """把任务 stop_event 适配成抓取源的 cancel_event：仅立即停止时视为置位。

    in-process（EXE）模式没有子进程，guard 杀不到，scrape_details 的逐条
    检查点是批内唯一中断手段——信号必须接到 source.cancel_event。graceful
    （等这批抓完）不置位，批次照常跑完批边界停止，语义不变。

    ``set()`` 刻意不回写 stop_event：run_with_deadline 超时路径会调 set()
    请求协作停止，超时≠用户暂停，保持 no-op 与历史行为（cancel_event=None）一致。
    """

    def __init__(self, stop_event):
        self._stop_event = stop_event

    def is_set(self) -> bool:
        return bool(
            self._stop_event is not None
            and self._stop_event.is_set()
            and getattr(self._stop_event, "immediate", False)
        )

    def set(self) -> None:
        return None


def pause_with_mode(ctx, run_id: str, mode: str):
    """暂停 AI 筛选/重抓任务（025：支持 mode=immediate 批中立即停止）。

    ``mode`` 取值：
    - ``graceful``（缺省）：现状行为——stop_mode="pause" + stop_event.set()，
      worker 在安全边界（批间/批完）停止，数据完整。
    - ``immediate``：立即停止——任务落「已暂停」语义（非取消）、stop_event
      携带 immediate 信号（fetch_job_details 据此作废当前批）、终止活动批
      子进程并清理 guard 批次登记；已暂停/已 immediate 幂等返回 ok（不 409）。
    """
    with ctx.lock:
        task = ctx.tasks.get(run_id)
        if task is None:
            return jsonify({
                "ok": False, "error": "run_not_found",
                "message": _MSG_TASK_NOT_FOUND,
            }), 404
        if task.get("kind") not in ("ai_screen", "recrawl"):
            return jsonify({
                "ok": False, "error": "not_pausable_task",
                "message": "只有 AI 筛选或重抓任务可以暂停",
            }), 409
        if task["status"] not in ("queued", "running"):
            if mode == "immediate":
                # 025：已暂停/已终态再点立即停止 → 幂等不报错
                return jsonify({
                    "ok": True, "run_id": run_id, "status": "paused",
                }), 200
            return jsonify({
                "ok": False, "error": "task_not_active",
                "message": f"任务当前状态（{task['status']}）不能暂停",
            }), 409
        run = ctx.store.get_screening_run(run_id)
        if run is not None and run.get("status") not in ("queued", "running"):
            if mode == "immediate":
                return jsonify({
                    "ok": True, "run_id": run_id, "status": "paused",
                }), 200
            return jsonify({
                "ok": False, "error": "task_not_active",
                "message": f"任务当前状态（{run.get('status')}）不能暂停",
            }), 409
        stop_event = task.get("stop_event")
        if stop_event is None:
            return jsonify({
                "ok": False, "error": "stop_signal_unavailable",
                "message": "任务缺少停止信号，无法暂停",
            }), 409
        if task.get("immediate_stop"):
            # 025：已 immediate 再调 → 幂等（不重复清理）
            return jsonify({
                "ok": True, "run_id": run_id, "status": "pausing",
            }), 200
        task["stop_mode"] = "pause"
        if mode == "immediate":
            task["immediate_stop"] = True
            stop_event.immediate = True  # fetch_job_details 据此作废当前批
        stop_event.set()
    if mode == "immediate":
        # 025：终止活动批子进程 + 清理批次登记（锁外，可能耗时）
        guard = getattr(ctx, "pipeline_guard", None)
        if guard is not None:
            try:
                guard.immediate_stop_task(run_id)
            except Exception:
                # 清理失败不阻断暂停（幂等兜底）
                _logger.exception("immediate_stop_task 异常（已忽略）")
    return jsonify({"ok": True, "run_id": run_id, "status": "pausing"})


def cancel_task_cleanup(ctx, run_id: str) -> None:
    """取消路径清理 guard 批次登记（025 B076，best-effort）。"""
    guard = getattr(ctx, "pipeline_guard", None)
    if guard is not None:
        try:
            guard.immediate_stop_task(run_id)
        except Exception:
            _logger.exception("cancel_task_cleanup 异常（已忽略）")
