"""045 B102：桌面关闭确认与未完成流程保存域（v2 两段式）。

桌面壳只接线本模块；查询、收尾、前端确认与关窗动作全部可注入。

v2 相对 v1 的变化：
- 确认框由前端自绘组件承担，壳侧不再弹任何系统原生框；
- 关闭改两段式——``closing`` 立即取消本次关闭并通知前端，用户决定后回调
  收尾再关窗；
- 运行中场景先立即暂停再落轮，未运行场景直接落轮。

两段式是必须的：``closing`` 处理器跑在 UI 线程且必须同步返回，而前端弹框
需要 WebView 消息泵运转；若在处理期里阻塞等待，页面会被冻结、用户永远点不
到按钮（见 research D1）。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

#: 关闭场景
SCENARIO_NONE = "none"                      # 无流程或零岗位：不打扰，直接关
SCENARIO_IDLE_SAVE = "idle_save"            # 未运行但有岗位：结束并保存
SCENARIO_RUNNING_STOP = "running_stop"      # 运行中且有岗位：立即结束

#: 前端确认入口 resolve 出的动作
ACTION_CONFIRM = "confirm"
ACTION_CANCEL = "cancel"

#: 判定为「正在运行」的状态
_RUNNING_STATUSES = ("running", "queued")

#: 落轮接口对已终态轮的返回；这些不算失败，不该卡住关闭
_ALREADY_SAVED_ERRORS = ("already_finished", "already_terminal")


#: 前端确认入口的全局函数名（契约见 contracts/close-confirm-bridge.md）
FRONTEND_ENTRY = "__csCloseConfirm"


def build_confirm_script(scenario: str) -> str:
    """生成调用前端确认入口的脚本；入口不存在时求值为 undefined。"""
    payload = json.dumps({"scenario": scenario})
    return f"window.{FRONTEND_ENTRY} ? window.{FRONTEND_ENTRY}({payload}) : undefined"


def request_frontend_confirm(window, scenario: str, callback, log=None) -> None:
    """在后台线程调用前端确认入口，结果经 pywebview 异步回调送回。

    ``evaluate_js`` 会阻塞等待脚本结果（EdgeChromium 用信号量实现），在
    ``closing`` 处理器所在的 UI 线程里调用必然死锁，因此必须挪到后台线程。
    """
    script = build_confirm_script(scenario)

    def _invoke():
        try:
            window.evaluate_js(script, callback)
        except Exception as exc:
            if callable(log):
                log(f"前端确认入口调用失败: {exc}")
            callback(ACTION_CANCEL)

    threading.Thread(target=_invoke, name="cs-close-ask", daemon=True).start()


def close_window(window, log=None) -> None:
    """真正关窗。

    ``destroy()`` 会再次触发 ``closing``，由桥上的守卫放行；WinForms 下事件
    循环未必随之结束，故保留进程退出兜底（窗口状态与落轮在此之前已完成）。
    """
    try:
        window.destroy()
    except Exception as exc:
        if callable(log):
            log(f"destroy 失败: {exc}")
    os._exit(0)


class CloseHost:
    """关闭域的宿主动作集合。

    桌面壳只提供查询、暂停、落轮、窗口状态、取消任务、日志与窗口对象；
    后台线程、关窗、前端确认这些通用实现由本类统一提供，避免它们散落在
    已经超出规模红线的 ``desktop.py`` 里。
    """

    def __init__(self, *, latest_running_task, pause_run, finish_run,
                 save_window_state, cancel_tasks, log, window, run_async=None):
        self._latest_running_task = latest_running_task
        self._pause_run = pause_run
        self._finish_run = finish_run
        self._save_window_state = save_window_state
        self._cancel_tasks = cancel_tasks
        self._log = log
        self._window = window
        self._run_async = run_async

    def latest_running_task(self):
        return self._latest_running_task()

    def pause_run(self, run_id):
        return self._pause_run(run_id)

    def finish_run(self, run_id):
        return self._finish_run(run_id)

    def save_window_state(self):
        self._save_window_state()

    def cancel_tasks(self):
        self._cancel_tasks()

    def log(self, message):
        self._log(message)

    def run_async(self, fn):
        if self._run_async is not None:
            self._run_async(fn)
            return
        threading.Thread(target=fn, name="cs-close-finish", daemon=True).start()

    def close_window(self):
        close_window(self._window, log=self._log)

    def request_frontend_confirm(self, scenario, callback):
        request_frontend_confirm(self._window, scenario, callback, log=self._log)

    def notify_failure(self, payload):
        self._log(f"落轮失败，已保持现场: {payload}")


def _job_count(latest: dict[str, Any]) -> int:
    for key in ("job_count", "scraped_count"):
        try:
            value = int(latest.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def classify_close_scenario(latest: dict[str, Any] | None) -> str:
    """按最新流程快照判定关闭场景（纯函数，可脱离宿主单测）。

    - 无流程或零岗位 → ``SCENARIO_NONE``；
    - 运行中且有岗位 → ``SCENARIO_RUNNING_STOP``；
    - 其余有岗位 → ``SCENARIO_IDLE_SAVE``。
    """
    if not latest or not latest.get("has_task"):
        return SCENARIO_NONE
    if _job_count(latest) <= 0:
        return SCENARIO_NONE
    if str(latest.get("status") or "") in _RUNNING_STATUSES:
        return SCENARIO_RUNNING_STOP
    return SCENARIO_IDLE_SAVE


class CloseConfirmBridge:
    """关闭确认的两段式编排。

    ``host`` 需提供：``latest_running_task()``、``pause_run(run_id)``、
    ``finish_run(run_id)``、``save_window_state()``、``run_async(fn)``、
    ``close_window()``、``request_frontend_confirm(scenario, callback)``、
    ``log(message)``；``notify_failure(payload)`` 可选。

    时序：

    1. ``on_closing()`` 命中守卫 → 放行（阻断 ``destroy()`` 二次触发弹框）；
    2. 查询最新流程 → 无流程 / 零岗位 / 查询失败 → 放行；
    3. 有岗位 → 通知前端并立即返回 False 取消本次关闭；
    4. 前端回调 confirm → 后台收尾（运行中先暂停）→ 置守卫 → 关窗；
       回调 cancel 或落轮失败 → 保持现场。
    """

    def __init__(self, host):
        self._host = host
        self._decided = False

    @property
    def decided(self) -> bool:
        """收尾已完成、允许真正关窗。"""
        return self._decided

    def on_closing(self) -> bool:
        """返回 True 表示放行本次关闭。"""
        if self._decided:
            return True
        try:
            latest = self._host.latest_running_task()
        except Exception as exc:
            self._log(f"关闭前查询失败，直接关闭: {exc}")
            return True
        scenario = classify_close_scenario(latest)
        if scenario == SCENARIO_NONE:
            self._host.save_window_state()
            return True
        run_id = str((latest or {}).get("task_id") or "")
        try:
            self._host.request_frontend_confirm(
                scenario,
                lambda action: self._on_confirm(action, scenario, run_id),
            )
        except Exception as exc:
            self._log(f"关闭确认不可用，直接关闭: {exc}")
            return True
        return False

    def _on_confirm(self, action: Any, scenario: str, run_id: str) -> None:
        if action != ACTION_CONFIRM:
            return
        self._host.run_async(lambda: self._finish_and_close(scenario, run_id))

    def _finish_and_close(self, scenario: str, run_id: str) -> None:
        if not run_id:
            self._log("关闭收尾缺少流程标识，保持现场")
            return
        if scenario == SCENARIO_RUNNING_STOP:
            try:
                self._host.pause_run(run_id)
            except Exception as exc:
                self._log(f"立即暂停失败，继续落轮: {exc}")
        try:
            finish = self._host.finish_run(run_id)
        except Exception as exc:
            finish = {"ok": False, "error": str(exc)}
        already_saved = str(finish.get("error") or "") in _ALREADY_SAVED_ERRORS
        if not (bool(finish.get("ok")) or already_saved):
            self._log(f"落轮失败，保持现场: {finish}")
            self._notify_failure(finish)
            return
        self._decided = True
        self._host.save_window_state()
        self._host.close_window()

    def finalize_close(self) -> None:
        """放行关闭后的收尾：落盘窗口状态并取消运行中任务。

        与 :meth:`on_closing` 的放行分支等价，供桌面壳在无需弹框时复用，
        避免收尾逻辑在壳里复制一份。
        """
        self._host.save_window_state()
        self._host.cancel_tasks()

    def finish_silently(self) -> None:
        """不弹确认直接收尾，供应用内更新重启路径使用（FR-017）。

        用户已经点过「立即更新」，再弹一次确认既多余又会卡住等待主进程
        退出的替换脚本；这里有岗位就按场景静默落轮，没有就什么都不做。
        失败只记日志，不阻断退出。
        """
        try:
            latest = self._host.latest_running_task()
        except Exception as exc:
            self._log(f"静默收尾查询失败: {exc}")
            return
        scenario = classify_close_scenario(latest)
        if scenario == SCENARIO_NONE:
            return
        run_id = str((latest or {}).get("task_id") or "")
        if not run_id:
            self._log("静默收尾缺少流程标识，跳过落轮")
            return
        if scenario == SCENARIO_RUNNING_STOP:
            try:
                self._host.pause_run(run_id)
            except Exception as exc:
                self._log(f"静默收尾暂停失败: {exc}")
        try:
            finish = self._host.finish_run(run_id)
        except Exception as exc:
            self._log(f"静默收尾落轮失败: {exc}")
            return
        if not bool(finish.get("ok")) and str(finish.get("error") or "") not in _ALREADY_SAVED_ERRORS:
            self._log(f"静默收尾落轮未完成: {finish}")

    def _log(self, message: str) -> None:
        log = getattr(self._host, "log", None)
        if callable(log):
            log(message)

    def _notify_failure(self, payload: Any) -> None:
        notify = getattr(self._host, "notify_failure", None)
        if callable(notify):
            notify(payload)
