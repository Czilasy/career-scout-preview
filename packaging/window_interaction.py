"""窗口交互引擎（spec 036 v2）：页面声明区域，宿主原生执行。

为什么不在 ``window_controls``（Research D9，2026-09-14 真机实测）：无边框窗体
客户区被 WebView2 子窗口整块覆盖（含四边四角），顶层窗体收到的
``WM_NCHITTEST`` 为 0 次，且该子窗口属于 WebView2 运行时的独立进程（跨进程
钩子被系统拒绝）。命中因此只能由页面（DOM 事件，CSS 像素）判定并一次上报，
宿主侧负责原生执行：本模块的循环读指针、算矩形、用 ``SetWindowPos`` 写回。

设计约束（contracts/desktop-window-interaction.md §2/§3）：
- 只单向依赖 ``window_controls``（原语与适配器），不反向、不导入桌面入口；
- 不使用系统 ``SC_MOVE`` 循环，故不会产生系统左右/四角贴靠；
- 不做前端逐帧回传：一次调用跑完整个按压周期，不记录逐帧日志；
- 循环有安全上限，任何异常都不向上抛，退出时清理 ``busy`` 状态。

拆分说明：本模块由 036 v2 线门禁触发（引擎若留在 ``window_controls`` 会让该
文件到 1100 行，越过项目 800 行红线）；职责边界：那边装钩子、工作区与窗口
状态原语，这边执行页面声明的拉伸/移动。
"""

import ctypes
import sys
import threading
import time
from ctypes import wintypes

from packaging.window_controls import (
    find_main_hwnd_pid,
    is_maximized,
    maximize,
)
from packaging.window_metrics import (
    can_resize,
    drag_started,
    grab_ratio,
    min_normal_size_css,
    min_track_size,
    move_rect,
    normalize_direction,
    physical_px,
    resize_rect,
    resolve_workarea,
    restore_rect_for_grab,
    should_maximize_on_release,
    window_scale,
)


# ---------------------------------------------------------------------------
# 036 v2 交互常量与运行时状态
# ---------------------------------------------------------------------------
TITLE_H_CSS = 36
"""标题栏高度（CSS 像素）：仅用于拖下还原时把指针落在标题带中部。

视觉真值在 ``webui/src/components/WindowTitleBar.vue`` 的 36px；边缘命中宽度与
按钮区宽度由页面判定（本模块不再重复定义）。"""

SNAP_TOP_THRESHOLD_CSS = 8
"""顶部释放最大化的判定阈值（CSS 像素）：指针进入工作区顶边该范围即视为「拖到顶部」。"""

SNAP_TOP_MIN_TRAVEL_CSS = 8
"""顶部释放最大化所需的最小拖动距离（CSS 像素）：区分「拖到顶部」与「点一下」。"""

DRAG_START_THRESHOLD_CSS = 4
"""判定「真的开始拖动」的位移阈值（CSS 像素）：最大化标题带点一下不还原窗口。"""

INTERACTION_TICK_SECONDS = 0.008
"""交互循环轮询间隔（秒）：约 120Hz 读指针 + 写窗口矩形，无逐帧日志。"""

INTERACTION_MAX_SECONDS = 90.0
"""交互循环安全上限（秒）：超时自动退出，避免任何形式的输入残留。"""

_INTERACTION = {"active": False, "thread": None}
"""宿主交互运行时状态：同一时刻只允许一个拉伸/移动循环（``busy`` 守卫）。

测试可直接读写本字典（内部状态，不对外暴露 API）。
"""


def _cursor_pos():
    """当前指针屏幕坐标；失败返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        point = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return (int(point.x), int(point.y))
    except Exception:
        pass
    return None


def _left_button_down():
    """左键是否仍被按下（``GetAsyncKeyState``）：交互循环的唯一退出信号。"""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False


def _is_window(hwnd):
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.IsWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False


def _window_maximized(hwnd):
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.IsZoomed(wintypes.HWND(hwnd)))
    except Exception:
        return False


def window_rect(hwnd):
    """窗口矩形 ``(x, y, w, h)``（物理像素）；失败返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        return None


def apply_rect(hwnd, rect):
    """按物理像素写回窗口矩形（不改 Z 序、不抢焦点）。"""
    if sys.platform != "win32":
        return False
    try:
        x, y, w, h = (int(v) for v in rect)
        if w <= 0 or h <= 0:
            return False
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        swp_nozorder, swp_noactivate = 0x0004, 0x0010
        return bool(user32.SetWindowPos(
            wintypes.HWND(hwnd), None, x, y, w, h, swp_nozorder | swp_noactivate
        ))
    except Exception:
        return False


def restore_to_rect(hwnd, rect):
    """一步完成「还原 + 定位」：``WINDOWPLACEMENT.showCmd=SW_NORMAL`` + 目标普通矩形。

    比「先 SW_RESTORE 再 SetWindowPos」少一次可见跳变；同时把该矩形登记为
    后续最小化/还原的还原位（与用户手动还原语义一致）。
    """
    if sys.platform != "win32":
        return False
    try:
        x, y, w, h = (int(v) for v in rect)
        if w <= 0 or h <= 0:
            return False
        user32 = ctypes.windll.user32

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.UINT),
                ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT),
            ]

        user32.GetWindowPlacement.argtypes = [
            wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)
        ]
        user32.GetWindowPlacement.restype = wintypes.BOOL
        user32.SetWindowPlacement.argtypes = [
            wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)
        ]
        user32.SetWindowPlacement.restype = wintypes.BOOL

        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(placement)):
            return False
        placement.showCmd = 1  # SW_NORMAL
        placement.rcNormalPosition = wintypes.RECT(x, y, x + w, y + h)
        return bool(user32.SetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(placement)))
    except Exception:
        return False


def _normal_rect(value):
    """校验最近普通矩形 ``(w, h, x, y)``；不合法返回 ``None``。"""
    try:
        w, h, x, y = (int(v) for v in tuple(value)[:4])
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (w, h, x, y)


def _fallback_normal_rect(hwnd):
    """无普通矩形记忆时的还原回退：默认尺寸 + 按窗口所在显示器工作区居中。

    没有它，「首开最大化且尚无记忆」时拖标题带会去移动一个最大化窗口
    （界面会出现莫名位移），违反 spec FR-009 的还原语义。
    """
    try:
        from packaging.window_state import default_normal_rect  # 延迟 import 防环

        area = resolve_workarea(hwnd=hwnd)
        provider = (lambda: [area]) if area else None
        rect = default_normal_rect(provider)
        return _normal_rect(rect)
    except Exception:
        return None


def _css_to_pointer(hwnd, css, scale):
    """``GetCursorPos`` 不可用时的兜底：由页面上报的 CSS 坐标换算屏幕物理坐标。"""
    rect = window_rect(hwnd)
    if rect is None or not css:
        return None
    try:
        css_x, css_y = css
        return (
            rect[0] + physical_px(css_x, scale),
            rect[1] + physical_px(css_y, scale),
        )
    except (TypeError, ValueError):
        return None


def _start_interaction(loop, window, hwnd, ctx, diag=None):
    """在独立线程跑交互循环（不阻塞 js_api 调用线程、不阻塞 UI 线程）。"""
    def _runner():
        try:
            loop(window, hwnd, ctx, diag)
        except Exception as exc:  # noqa: BLE001 交互循环兜底：绝不向上抛
            if diag is not None:
                try:
                    diag(f"[interaction] EXC {type(exc).__name__}: {exc}")
                except Exception:
                    pass
        finally:
            _INTERACTION["active"] = False
            _INTERACTION["thread"] = None

    _INTERACTION["active"] = True
    thread = threading.Thread(
        target=_runner, name="career-scout-window-interaction", daemon=True
    )
    _INTERACTION["thread"] = thread
    thread.start()


def _loop_step(hwnd, base_pointer):
    """交互循环一轮：仍在按下 → 返回 ``(指针, 位移)``；否则返回 ``None``（结束）。"""
    if not _left_button_down():
        return None
    if not _is_window(hwnd):
        return None
    pointer = _cursor_pos()
    if pointer is None:
        return None
    return (pointer, (pointer[0] - base_pointer[0], pointer[1] - base_pointer[1]))


def _fail(diag, action, reason, hwnd):
    """交互失败节点：只写一行摘要（安装/门禁节点，不记逐帧消息）。"""
    if diag is None:
        return
    try:
        diag(f"[interaction] {action} FAIL {reason} hwnd={hwnd}")
    except Exception:
        pass


def _drive(hwnd, base_rect, base_pointer, move_fn, on_step=None):
    """跑完整的按压周期：按 ``move_fn(基准矩形, dx, dy)`` 写回矩形直到释放/超时。

    返回 ``(released, last_rect, travel)``：``released=True`` 表示正常松手结束，
    ``False`` 表示超时、窗口失效或指针不可读（不做释放后的收尾动作）；
    ``travel`` 是按压期间指针离按下点的最大位移（物理像素）。

    ``on_step(now, travel) -> (新基准矩形, 新基准指针) | None``：可选钩子，
    用于在越过阈值时换基准（最大化拖下还原）；返回新基准后位移从该点重新起算。
    """
    base = base_rect
    origin = base_pointer
    last = base
    travel = 0
    deadline = time.monotonic() + INTERACTION_MAX_SECONDS
    while True:
        step = _loop_step(hwnd, origin)
        if step is None:
            return (not _left_button_down(), last, travel)
        now, (dx, dy) = step
        travel = max(travel, abs(int(dx)), abs(int(dy)))
        if on_step is not None:
            switched = on_step(now, travel)
            if switched is not None:
                base, origin = switched
                last = base
                dx = dy = 0
        rect = move_fn(base, dx, dy)
        if rect != last:
            if not apply_rect(hwnd, rect):
                return (False, last, travel)
            last = rect
        if time.monotonic() >= deadline:
            return (False, last, travel)
        time.sleep(INTERACTION_TICK_SECONDS)


def _move_loop(window, hwnd, ctx, diag):
    """标题带移动循环：普通态平移；最大化态先还原再跟随；顶部释放最大化。"""
    scale = window_scale(hwnd)
    rect = window_rect(hwnd)
    if rect is None:
        _fail(diag, "move", "rect_unavailable", hwnd)
        return
    pointer = _cursor_pos() or _css_to_pointer(hwnd, ctx.get("css"), scale)
    if pointer is None:
        _fail(diag, "move", "pointer_unavailable", hwnd)
        return

    if diag is not None:
        diag(f"[interaction] move start hwnd={hwnd} rect={rect} pointer={pointer}")

    # 最大化拖下还原：只在真正拖动（越过阈值）后执行——在最大化标题带上
    # 点一下不还原（与系统标题栏一致），否则双击/单击会先闪一下普通窗口。
    normal = _normal_rect(ctx.get("normal_rect")) or _fallback_normal_rect(hwnd)
    press_ratio = grab_ratio(pointer[0], rect[0], rect[2])
    restore_threshold = physical_px(DRAG_START_THRESHOLD_CSS, scale)
    pending = {"restore": _window_maximized(hwnd), "rect": rect, "pointer": pointer}

    def _maybe_restore(now, travel):
        if not pending["restore"] or not drag_started(travel, restore_threshold):
            return None
        pending["restore"] = False
        target = restore_rect_for_grab(
            now[0], now[1], (normal[0], normal[1]), press_ratio,
            physical_px(TITLE_H_CSS, scale),
        )
        if not restore_to_rect(hwnd, target):
            _fail(diag, "move", "restore_failed", hwnd)
            return None
        pending["rect"] = target
        pending["pointer"] = now
        if diag is not None:
            diag(f"[interaction] restore->{target} hwnd={hwnd}")
        return (target, now)

    released, last, travel = _drive(
        hwnd, rect, pointer,
        lambda base, dx, dy: move_rect(base, dx, dy),
        on_step=_maybe_restore,
    )
    if diag is not None:
        diag(
            f"[interaction] move end released={released} travel={travel} "
            f"rect={last} hwnd={hwnd}"
        )
    if not released or pending["restore"]:
        # 没松手、或一直没拖动（最大化态纯点击）：不做释放收尾
        return

    now = _cursor_pos()
    work = resolve_workarea(
        point=now, hwnd=hwnd, workarea_provider=ctx.get("workarea_provider")
    )
    if (
        now is not None
        and work is not None
        and should_maximize_on_release(
            now[1], work[1], physical_px(SNAP_TOP_THRESHOLD_CSS, scale),
            travel_px=travel,
            min_travel_px=physical_px(SNAP_TOP_MIN_TRAVEL_CSS, scale),
        )
    ):
        maximize(window)
        if diag is not None:
            diag(f"[interaction] top-release maximize hwnd={hwnd} work={work}")


def _resize_loop(window, hwnd, ctx, diag):
    """八方向拉伸循环：对应边跟随指针，对边锚定，逐轴钳制。"""
    direction = ctx["direction"]
    scale = window_scale(hwnd)
    rect = window_rect(hwnd)
    pointer = _cursor_pos() or _css_to_pointer(hwnd, ctx.get("css"), scale)
    if rect is None:
        _fail(diag, f"resize {direction}", "rect_unavailable", hwnd)
        return
    if pointer is None:
        _fail(diag, f"resize {direction}", "pointer_unavailable", hwnd)
        return
    work = resolve_workarea(
        point=pointer, hwnd=hwnd, workarea_provider=ctx.get("workarea_provider")
    )
    if work is None:
        _fail(diag, f"resize {direction}", "workarea_unavailable", hwnd)
        return
    min_w, min_h = min_track_size(work[2], work[3], scale, *min_normal_size_css())
    if diag is not None:
        diag(
            f"[interaction] resize {direction} start hwnd={hwnd} rect={rect} "
            f"pointer={pointer} min=({min_w},{min_h}) work={work}"
        )
    released, last, _travel = _drive(
        hwnd, rect, pointer,
        lambda base, dx, dy: resize_rect(
            base, direction, dx, dy, (min_w, min_h), work
        ),
    )
    if diag is not None:
        diag(
            f"[interaction] resize {direction} end released={released} "
            f"rect={last} hwnd={hwnd}"
        )


def wire_desktop_api(js_api, window, tracker=None, diag=None):
    """给桌面壳 js_api 注入窗口交互所需的私有引用（安装后调用一次）。

    属性名必须下划线开头：pywebview 页面加载完成后会递归枚举 js_api 的
    **公开**属性生成前端 API 清单（``webview/util.py get_functions``），公开
    持有 Window 会让枚举爬进整个窗口/.NET 窗体对象图（036 真机实测：页面
    加载 20 秒起步、有概率永久卡死）。下划线属性被枚举跳过，注入与调用不受
    影响。只写对象本身已有的属性，避免给自定义替身凭空加字段。
    """
    for attr, value in (("_window", window), ("_tracker", tracker), ("_diag", diag)):
        if hasattr(js_api, attr):
            setattr(js_api, attr, value)


class DesktopWindowInteractionApi:
    """桌面壳 js_api 的窗口交互入口（``DesktopJsApi`` 继承本类）。

    方法本身只做参数转发与守卫，命中判定在页面、执行在宿主循环：
    ``desktop.py`` 因此只保留接线，不必持有交互细节。
    """

    _window = None
    _tracker = None
    _diag = None

    def window_begin_move(self, x=0, y=0):
        """标题栏按下：交给交互引擎跑完整个按压周期（页面一次调用）。

        最大化态由引擎先还原到最近普通矩形（Tracker 冻结值）再继续跟随；
        松手在顶部（且确实拖动过）才最大化。返回体沿用 ``{"ok", "error"}``。
        """
        win = self._window
        if win is None:
            return {"ok": False, "error": "no_window"}
        tracker = self._tracker
        normal = getattr(tracker, "last_normal", None) if tracker is not None else None
        return begin_move(
            win, x, y, normal_rect=normal, diag=self._diag
        )

    def window_begin_resize(self, direction="R", x=0, y=0):
        """页面边缘按下：交给交互引擎八方向拉伸（页面一次调用）。"""
        win = self._window
        if win is None:
            return {"ok": False, "error": "no_window"}
        return begin_resize(win, direction, x, y, diag=self._diag)


def begin_move(window, css_x=0, css_y=0, normal_rect=None, workarea_provider=None,
               diag=None):
    """页面标题带按下 → 宿主接管移动（一次调用，宿主循环执行）。

    - 普通态：窗口按指针位移移动，尺寸不变；
    - 最大化态：先按最近普通矩形还原并保持抓取横向比例，同一次按压继续跟随；
    - 松手：指针位于当前显示器工作区顶部阈值内才最大化，其余保持普通态。

    返回 ``{"ok", "error", ...}``；失败码稳定：``unsupported_platform`` /
    ``busy`` / ``hwnd_not_found``。
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "unsupported_platform"}
    if _INTERACTION["active"]:
        return {"ok": False, "error": "busy"}
    hwnd, _title = find_main_hwnd_pid()
    if not hwnd:
        return {"ok": False, "error": "hwnd_not_found"}
    ctx = {
        "css": (css_x, css_y),
        "normal_rect": normal_rect,
        "workarea_provider": workarea_provider,
    }
    _start_interaction(_move_loop, window, hwnd, ctx, diag=diag)
    return {"ok": True, "error": None, "mode": "move"}


def begin_resize(window, direction, css_x=0, css_y=0, workarea_provider=None,
                 diag=None):
    """页面边缘按下 → 宿主接管拉伸（一次调用，宿主循环执行）。

    最大化状态不提供边缘拉伸（返回 ``maximized``）；方向非法返回
    ``bad_direction``；同时只允许一个交互（``busy``）。
    """
    normalized = normalize_direction(direction)
    if normalized is None:
        return {"ok": False, "error": "bad_direction"}
    if sys.platform != "win32":
        return {"ok": False, "error": "unsupported_platform"}
    if not can_resize(is_maximized(window)):
        return {"ok": False, "error": "maximized"}
    if _INTERACTION["active"]:
        return {"ok": False, "error": "busy"}
    hwnd, _title = find_main_hwnd_pid()
    if not hwnd:
        return {"ok": False, "error": "hwnd_not_found"}
    ctx = {
        "direction": normalized,
        "css": (css_x, css_y),
        "workarea_provider": workarea_provider,
    }
    _start_interaction(_resize_loop, window, hwnd, ctx, diag=diag)
    return {"ok": True, "error": None, "mode": "resize", "direction": normalized}
