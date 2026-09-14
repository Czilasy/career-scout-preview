"""窗口度量域（spec 036 v2）：显示器工作区、DPI 与尺寸换算。

单一归属：所有「按当前显示器 / 当前 DPI 换算尺寸与工作区」的原生查询都在
本模块，供窗口适配器（``window_controls``，``WM_GETMINMAXINFO`` 逐消息钳制）
与窗口交互引擎（``window_interaction``，拉伸/移动前的逐轴钳制）共用。

为什么独立成模块（2026-09-14 依据宪法原则 VI「75% 预警线分流」）：
``window_controls.py`` 在加入这些能力后越过 600 行预警线，按规则必须开新模块
分流，不得继续增长至 800 行红线。

依赖方向：本模块只依赖标准库与 ``packaging.window_state``（延迟 import，
仅取常规最小尺寸常量），不反向依赖任何窗口模块。
"""

import ctypes
import sys
from ctypes import wintypes


# 036 v2 纯逻辑：DPI 换算与逐轴最小跟踪尺寸（拉伸/移动几何在 window_interaction）
# ---------------------------------------------------------------------------
def physical_px(css_value, scale):
    """CSS 像素 → 物理像素（向上取整，保证正值不被算成 0）。"""
    try:
        value = float(css_value) * float(scale)
    except (TypeError, ValueError):
        return 0
    rounded = int(round(value))
    if rounded < 1 and float(css_value) > 0:
        return 1
    return rounded


def min_track_size(work_w, work_h, scale, min_w_css=1024, min_h_css=700):
    """逐轴最小跟踪尺寸（物理像素）。

    常规下限按当前 DPI 换算；工作区某一轴更小时，该轴放宽到工作区尺寸
    （spec FR-007：狭小工作区优先保证窗口完整可见）。适配器钩子与交互
    引擎共用本函数，保证「窗口下限」只有一个口径。
    """
    min_w = physical_px(min_w_css, scale)
    min_h = physical_px(min_h_css, scale)
    return (min(min_w, int(work_w)), min(min_h, int(work_h)))


# ---------------------------------------------------------------------------
# 原生共享助手（适配器与交互引擎共用）
# ---------------------------------------------------------------------------
def min_normal_size_css():
    """常规最小普通尺寸（逻辑像素）：窗口状态域是唯一来源，延迟 import 防环。"""
    try:
        from packaging.window_state import MIN_HEIGHT, MIN_WIDTH

        return (int(MIN_WIDTH), int(MIN_HEIGHT))
    except Exception:
        return (1024, 700)


def window_scale(hwnd):
    """窗口当前 DPI 缩放比（物理像素 / 逻辑像素）。"""
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes as _ctypes
        from ctypes import wintypes as _wt

        user32 = _ctypes.windll.user32
        user32.GetDpiForWindow.argtypes = [_wt.HWND]
        user32.GetDpiForWindow.restype = _wt.UINT
        dpi = user32.GetDpiForWindow(_wt.HWND(hwnd))
        return (dpi / 96.0) if dpi else 1.0
    except Exception:
        return 1.0




# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------


# 显示器工作区解析（036 v2：当前显示器优先，注入提供者兜底）
# ---------------------------------------------------------------------------
def _workarea_for_monitor(hmonitor):
    """显示器句柄 → 工作区 ``(x, y, w, h)``（排除任务栏）；失败返回 ``None``。"""
    try:
        import ctypes
        from ctypes import wintypes

        class _MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return None
        rc = info.rcWork
        return (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
    except Exception:
        return None


def monitor_workarea_for_window(hwnd):
    """窗口所在显示器的工作区（``MONITOR_DEFAULTTONEAREST``）；失败返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        hmonitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
        return _workarea_for_monitor(hmonitor)
    except Exception:
        return None


def monitor_workarea_for_point(x, y):
    """屏幕点所在显示器的工作区；失败返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        hmonitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
        return _workarea_for_monitor(hmonitor)
    except Exception:
        return None


def _primary_workarea():
    """主屏工作区（``SPI_GETWORKAREA``）；失败返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        return None
    return None


def _normalize_workarea(area):
    if not area:
        return None
    try:
        values = tuple(int(v) for v in area[:4])
    except (TypeError, ValueError):
        return None
    return values if values[2] > 0 and values[3] > 0 else None


def resolve_workarea(hwnd=None, point=None, workarea_provider=None):
    """按「指针所在显示器 → 窗口所在显示器 → 注入提供者首项 → 主屏」解析工作区。

    036 v2 起工作区不再固定取列表第一项；注入 ``workarea_provider`` 只在
    原生读取失败时兜底，保持 R0 的注入面与「不反向依赖桌面入口」不变。
    """
    if point is not None:
        area = _normalize_workarea(monitor_workarea_for_point(*point))
        if area:
            return area
    if hwnd is not None:
        area = _normalize_workarea(monitor_workarea_for_window(hwnd))
        if area:
            return area
    if workarea_provider is not None:
        try:
            areas = workarea_provider() or []
        except Exception:
            areas = []
        if areas:
            area = _normalize_workarea(areas[0])
            if area:
                return area
    return _normalize_workarea(_primary_workarea())


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 036 v2 几何与判定（纯函数）：拉伸/移动矩形、抓取还原、拖动阈值、顶部释放
# ---------------------------------------------------------------------------
RESIZE_DIRECTIONS = ("L", "R", "T", "B", "TL", "TR", "BL", "BR")
"""八方向拉伸：四边 + 四角（页面侧区域判定与宿主执行共用同一套方向名）。"""


def normalize_direction(direction):
    """归一化拉伸方向；非法方向返回 ``None``。"""
    if not isinstance(direction, str):
        return None
    upper = direction.strip().upper()
    return upper if upper in RESIZE_DIRECTIONS else None


def can_resize(maximized):
    """最大化状态不提供边缘拉伸（spec FR-001 边界）。"""
    return not bool(maximized)


def resize_rect(start, direction, dx, dy, min_size, work):
    """按方向拉伸：对应边跟随位移，对边锚定；不越过下限与工作区上限。

    ``start``/返回值为 ``(x, y, w, h)`` 物理像素；``min_size`` 为
    ``(min_w, min_h)``（已按 DPI 换算且已应用小工作区例外）；``work`` 为
    ``(work_x, work_y, work_w, work_h)``。钳制只作用于移动中的边，锚定边
    始终不动。
    """
    x, y, w, h = (int(v) for v in start)
    min_w, min_h = (int(v) for v in min_size)
    work_x, work_y, work_w, work_h = (int(v) for v in work)
    left, top, right, bottom = x, y, x + w, y + h

    if "L" in direction:
        left = max(x + int(dx), work_x, right - work_w)
        if left > right - min_w:
            left = right - min_w
    elif "R" in direction:
        right = min(x + w + int(dx), work_x + work_w)
        if right < x + min_w:
            right = x + min_w

    if "T" in direction:
        top = max(y + int(dy), work_y, bottom - work_h)
        if top > bottom - min_h:
            top = bottom - min_h
    elif "B" in direction:
        bottom = min(y + h + int(dy), work_y + work_h)
        if bottom < y + min_h:
            bottom = y + min_h

    return (left, top, right - left, bottom - top)


def move_rect(start, dx, dy):
    """移动矩形：纯平移，尺寸不变（窗口始终跟随指针，不会失去可操作区域）。"""
    x, y, w, h = (int(v) for v in start)
    return (x + int(dx), y + int(dy), w, h)


def grab_ratio(pointer_x, win_x, win_w):
    """指针在窗口横向的抓取比例，钳到 ``[0, 1]``（窗口宽为 0 时取 0）。"""
    width = int(win_w)
    if width <= 0:
        return 0.0
    ratio = (float(pointer_x) - float(win_x)) / float(width)
    return max(0.0, min(1.0, ratio))


def restore_rect_for_grab(pointer_x, pointer_y, normal_size, ratio, title_h_px):
    """最大化拖下还原：普通大小 + 按抓取比例横向定位，指针落在标题带中部。

    返回 ``(x, y, w, h)``；横向比例保持是 spec FR-009 的核心要求。
    """
    normal_w, normal_h = (int(v) for v in normal_size)
    x = int(round(float(pointer_x) - float(ratio) * normal_w))
    y = int(pointer_y) - max(0, int(title_h_px) // 2)
    return (x, y, normal_w, normal_h)


def should_maximize_on_release(pointer_y, work_top, threshold_px,
                               travel_px=None, min_travel_px=0):
    """释放点是否应最大化：在工作区顶部阈值内，且按压期间确实拖动过。

    ``travel_px`` 为按压期间指针的最大位移；提供时要求不小于
    ``min_travel_px``——否则「窗口已在屏幕顶部时点一下标题栏」会被误判为
    「拖到顶部」，违反 spec FR-010「仅经过顶部不最大化」。
    """
    if travel_px is not None and int(travel_px) < int(min_travel_px):
        return False
    return int(pointer_y) <= int(work_top) + int(threshold_px)



def drag_started(travel_px, threshold_px):
    """按压期间的位移是否已越过「开始拖动」阈值。

    用于两处判定：最大化窗只在真正拖动后才还原（点一下不还原）、
    释放到顶部也要求确实拖动过（spec FR-010 的「仅经过顶部」）。
    """
    return int(travel_px) >= int(threshold_px)
