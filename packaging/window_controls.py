"""窗口控制 Win32 助手（spec 036 B084 自绘标题栏）。

职责：无边框窗口的最小化/最大化/还原原语，以及「最大化避让任务栏」
适配位。``packaging/desktop.py`` 只负责接线（``create_window`` 参数 +
``js_api`` 暴露），所有窗口操作逻辑集中在本模块，便于纯逻辑单测。

设计约束（specs/036-titlebar-dynamic-island/contracts/desktop-window-controls.md §3）：
- 仅依赖注入的 pywebview Window 对象，不直接 import webview；
- 最大化优先依赖 pywebview 自身行为；若 Windows 真机验证（T001）发现
  frameless 最大化覆盖任务栏，启用 ``MAXIMIZE_WORKAREA_CLAMP`` 经 Win32
  钳制工作区；适配位失败静默降级，不阻断启动。
- 每个原语返回 ``{"ok": bool, "error": str|None}``，供前端 js_api 直出。
"""

MAXIMIZE_WORKAREA_CLAMP = False
"""最大化避让任务栏适配位开关（默认关闭，T001 待真机确认）。

pywebview 6.x WinForms 后端无边框窗口最大化默认填满工作区（不覆盖
任务栏）。若 Windows 真机验证发现覆盖任务栏，置 ``True`` 并在
``toggle_maximize`` / ``maximize`` 中启用 ``_clamp_to_workarea``。
"""


def _ok(error=None):
    """统一返回体：无错误 -> {ok: True}；有错误 -> {ok: False, error}。"""
    return {"ok": error is None, "error": error}


def _is_maximized(window):
    """读取窗口最大化状态；替身/旧版无该属性时返回 None（视为未最大化）。"""
    value = getattr(window, "maximized", None)
    return bool(value) if value is not None else None


def minimize(window):
    """最小化到任务栏。"""
    try:
        window.minimize()
        return _ok()
    except Exception as exc:  # noqa: BLE001 窗口句柄异常统一兜底
        return _ok(str(exc))


def restore(window):
    """从最大化还原为普通矩形。"""
    try:
        window.restore()
        return _ok()
    except Exception as exc:  # noqa: BLE001
        return _ok(str(exc))


def maximize(window):
    """最大化窗口。"""
    try:
        window.maximize()
        return _ok()
    except Exception as exc:  # noqa: BLE001
        return _ok(str(exc))


def toggle_maximize(window):
    """最大化 <-> 还原切换（标题栏双击/按钮共用）。"""
    try:
        if _is_maximized(window):
            window.restore()
        else:
            window.maximize()
        return _ok()
    except Exception as exc:  # noqa: BLE001
        return _ok(str(exc))


def _clamp_to_workarea(window):
    """最大化到工作区的 Win32 适配位（默认关闭，见 MAXIMIZE_WORKAREA_CLAMP）。

    真机验证发现 frameless 最大化覆盖任务栏时启用：此处经 Win32
    （``WM_GETMINMAXINFO`` 或 ``GetSystemMetrics`` + ``MoveWindow``）
    把窗口钳到工作区矩形。任何失败静默降级（契约 §3：不阻断启动）。

    当前为占位实现，具体钳制逻辑待 T001 真机结论落地。
    """
    return _ok()
