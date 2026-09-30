"""取消搜索路由的内部实现。

047：提取任务停止与浏览器清理的 HTTP 壳，继续复用既有 context 能力。
"""

from __future__ import annotations

from flask import jsonify

from webui.constants import _MSG_TASK_NOT_FOUND, _MSG_USER_STOPPED_SCRAPE
from webui.task_pause_support import STOP_MODE_CANCEL, request_stop


def register_cancel_execute_search(app, ctx):
    @app.route("/api/execute-search/<task_id>/cancel", methods=["POST"])
    def cancel_execute_search(task_id):
        """停止正在运行的抓取任务并清理任务所属浏览器。"""
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            if task["status"] not in ("queued", "running"):
                return jsonify({
                    "ok": False,
                    "error": f"任务已结束，无法取消（当前状态：{task['status']}）",
                }), 400
            stop_event = task.get("stop_event")
            cancel_platform = task.get("platform")
            task_snapshot = dict(task)
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            MissingPlatformIdentityError,
            persist_task_cancelled,
        )
        try:
            persisted_run = persist_task_cancelled(ctx, task_id)
        except MissingPlatformIdentityError:
            return jsonify({
                "ok": False,
                "run_id": task_id,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法取消关联运行线",
            }), 409
        except FlowTaskOperationError:
            return jsonify({
                "ok": False,
                "run_id": task_id,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "任务取消失败，请刷新任务状态后重试",
            }), 503
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            if stop_event is not None:
                request_stop(task, stop_event, STOP_MODE_CANCEL)
            task["status"] = "cancelled"
            task["error"] = _MSG_USER_STOPPED_SCRAPE
            task.setdefault("logs", []).append("用户取消任务")
            cancel_platform = task.get("platform") or cancel_platform
        from webui import pipeline_exec as _facade
        from webui.frozen_browser_identity import cleanup_frozen_task_browser

        cleanup = cleanup_frozen_task_browser(
            ctx.store, task_id, task,
            accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
            activate=_facade.set_active_cdp_data_dir,
            close=_facade.close_debug_chrome,
        )
        if not cleanup.ok:
            with ctx.lock:
                current = ctx.tasks.get(task_id)
                if current is not None:
                    current["error"] = "用户已取消，但浏览器清理失败"
        ctx.clear_auto_screen(task_id)
        db_run = persisted_run
        if not cancel_platform:
            try:
                db_run = ctx.store.get_screening_run(task_id)
                cancel_platform = (db_run or {}).get("platform")
            except ctx.operational_errors:
                pass
        return jsonify({
            "ok": cleanup.ok,
            **({"error": "browser_cleanup_failed"} if not cleanup.ok else {}),
            "run_id": task_id,
            "task_id": task_id,
            "platform": cancel_platform,
            "status": "cancelled",
            "cleanup": cleanup.as_dict(),
        })

    return cancel_execute_search
