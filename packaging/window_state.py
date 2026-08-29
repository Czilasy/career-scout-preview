"""窗口状态域（029 自 packaging/desktop.py 分流，宪法原则 II/VI 预警线）。

``~/.career-scout/desktop_window.json`` schema 3 契约见
``specs/029-desktop-window-browsers/contracts/desktop-window-state.md``：

- ``width/height/x/y`` 语义为**普通矩形**（最大化关窗时 = 最后一次普通矩形，
  全屏矩形禁止写入）；
- ``maximized`` 标记上次关窗时窗口处于最大化，启动据此真最大化开窗；
- 无记忆 / 损坏 / schema 2 污染记忆（尺寸装不进任何工作区，即被 Bug 写成
  全屏矩形的）→ 一律按首开处理：默认普通矩形 + ``maximized=True``；
- 读时按当前工作区钳制（尺寸装不进任何工作区 → 钳主工作区；位置越出全部
  工作区 → 主屏居中），钳制只作用于返回值、不回写文件；
- :class:`WindowStateTracker` 在运行时维护"最后一次普通矩形 + 当前最大化
  态"，closing 时刻经 ``snapshot_for_save`` 产出落盘内容（research D1）。
"""

import json
import os
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

# 路径与常量（desktop.py re-export 保持旧调用面）
DEFAULT_STATE_DIR = Path(os.path.expanduser("~/.career-scout"))
WINDOW_STATE_FILENAME = "desktop_window.json"
# 默认窗口尺寸 = 普通态默认（首开直接最大化；从最大化还原落到该尺寸居中）
DEFAULT_WIDTH = 1545
DEFAULT_HEIGHT = 900
MIN_WIDTH = 1024
MIN_HEIGHT = 700
MAX_WIDTH = 8192
MAX_HEIGHT = 8192
_WINDOW_STATE_SCHEMA_VERSION = 3


class NormalRect(NamedTuple):
    """普通矩形（宽、高、位置）；Tracker 内部承载"最后一次普通矩形"。"""

    width: int
    height: int
    x: Optional[int]
    y: Optional[int]


def _centered(workarea, width, height):
    """在单个工作区 ``(x, y, w, h)`` 内居中的位置。"""
    ax, ay, aw, ah = workarea
    return (ax + max(0, (aw - width) // 2), ay + max(0, (ah - height) // 2))


# ---------------------------------------------------------------------------
# 路径与默认尺寸
# ---------------------------------------------------------------------------
def _window_state_path(state_dir):
    base = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    return base / WINDOW_STATE_FILENAME


def _read_default_size(state_dir):
    """读取用户配置的普通默认尺寸（default_width/default_height，schema 2/3 通用）。

    文件缺失/字段非法/越限 → 回退常量 DEFAULT_WIDTH/DEFAULT_HEIGHT。
    只读不写；用于「普通态默认开多大」的判定（契约 §5）。
    """
    path = _window_state_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT)
    if not isinstance(data, dict):
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT)
    try:
        width = int(data["default_width"])
        height = int(data["default_height"])
    except (KeyError, ValueError, TypeError):
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT)
    if not (MIN_WIDTH <= width <= MAX_WIDTH) or not (MIN_HEIGHT <= height <= MAX_HEIGHT):
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT)
    return (width, height)


def _clamp_size_to_workareas(width, height, workareas):
    """尺寸装不进任何工作区 → 钳到第一个工作区内；装得进 → 原样。"""
    for (_ax, _ay, aw, ah) in workareas:
        if width <= aw and height <= ah:
            return (width, height)
    if workareas:
        (_ax, _ay, aw, ah) = workareas[0]
        return (min(width, aw), min(height, ah))
    return (width, height)


def default_normal_rect(workarea_provider=None):
    """默认普通矩形 ``(w, h, x, y)``：默认尺寸过工作区钳制 + 主屏居中。

    workarea_provider 不可用 / 无工作区 → 位置 ``(0, 0)``（读取端会再兜底居中）。
    用于最大化且无普通记忆时的落盘回退（契约「首开处理」）。
    """
    width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
    if workarea_provider is not None:
        try:
            workareas = workarea_provider()
        except Exception:
            workareas = []
        if workareas:
            width, height = _clamp_size_to_workareas(width, height, workareas)
            x, y = _centered(workareas[0], width, height)
            return (width, height, x, y)
    return (width, height, 0, 0)


def size_fits_workareas(width, height, workarea_provider=None):
    """尺寸能否装进任一显示器工作区；provider 不可用/异常时放行（不误杀）。"""
    if workarea_provider is None:
        return True
    try:
        workareas = workarea_provider()
    except Exception:
        return True
    if not workareas:
        return True
    return any(width <= aw and height <= ah for (_ax, _ay, aw, ah) in workareas)


# ---------------------------------------------------------------------------
# 读取（含 schema 2 升级与读时钳制）
# ---------------------------------------------------------------------------
def load_window_state(state_dir=None, workarea_provider=None):
    """读取并校验窗口状态，返回 ``(width, height, x, y, maximized)``。

    - 文件缺失/JSON 非法/schema 不匹配/记忆字段无效 → 首开处理：
      ``(default_w, default_h, None, None, True)``，default 取文件内
      default_* 配置，未配置用常量；
    - schema 2 正常记忆（尺寸装得进任一工作区）→ 继承为普通矩形，
      ``maximized=False``；
    - schema 2 污染记忆（尺寸装不进任何工作区，即旧 Bug 写入的全屏
      矩形）→ 视同无记忆，按首开处理；
    - schema 3 记忆尺寸装不进任何工作区（外力污染/换小屏）→ 尺寸钳回
      主工作区，其余照常；
    - 位置越出全部工作区 → 主工作区居中；
    - workarea_provider 为 None → 不做钳制/越界判定，原样返回。
    """
    default_w, default_h = _read_default_size(state_dir)
    workareas = None
    if workarea_provider is not None:
        try:
            workareas = workarea_provider()
        except Exception:
            workareas = None
        if workareas:
            default_w, default_h = _clamp_size_to_workareas(
                default_w, default_h, workareas
            )
    no_memory = (default_w, default_h, None, None, True)

    path = _window_state_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return no_memory

    if not isinstance(data, dict):
        return no_memory
    schema = data.get("schema")
    if schema not in (2, _WINDOW_STATE_SCHEMA_VERSION):
        return no_memory

    try:
        width = int(data["width"])
        height = int(data["height"])
        x = int(data["x"])
        y = int(data["y"])
    except (KeyError, ValueError, TypeError):
        # 记忆字段不完整 → 无记忆，按首开处理
        return no_memory

    if not (MIN_WIDTH <= width <= MAX_WIDTH) or not (MIN_HEIGHT <= height <= MAX_HEIGHT):
        return no_memory

    maximized = bool(data.get("maximized")) if schema == _WINDOW_STATE_SCHEMA_VERSION else False

    if workareas is None:
        return (width, height, x, y, maximized)

    fits_any = any(width <= aw and height <= ah for (_ax, _ay, aw, ah) in workareas)
    if not fits_any:
        if schema == 2:
            # schema 2 污染记忆（旧 Bug 的全屏矩形）→ 视同无记忆
            return no_memory
        width, height = _clamp_size_to_workareas(width, height, workareas)

    inside_any = any(
        ax <= x < ax + aw and ay <= y < ay + ah for (ax, ay, aw, ah) in workareas
    )
    if inside_any:
        return (width, height, x, y, maximized)

    x, y = _centered(workareas[0], width, height)
    return (width, height, x, y, maximized)


# ---------------------------------------------------------------------------
# 保存（schema 3，单一写入口）
# ---------------------------------------------------------------------------
def save_window_state(width, height, x, y, state_dir=None, maximized=False):
    """保存窗口状态到 ``{state_dir}/desktop_window.json``（schema 3）。

    ``width/height/x/y`` 必须是普通矩形语义（调用方经
    :meth:`WindowStateTracker.snapshot_for_save` 产出，最大化关窗时传
    最后一次普通矩形，禁止传全屏矩形）；``maximized`` 记录关窗时窗口态。
    schema 2/3 既有 default_* 用户配置原样保留。
    """
    default_w, default_h = _read_default_size(state_dir)
    path = _window_state_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": _WINDOW_STATE_SCHEMA_VERSION,
                    "default_width": default_w,
                    "default_height": default_h,
                    "width": int(width),
                    "height": int(height),
                    "x": int(x),
                    "y": int(y),
                    "maximized": bool(maximized),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 运行时普通矩形追踪（research D1）
# ---------------------------------------------------------------------------
def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class WindowStateTracker:
    """运行时窗口态追踪：仅更新内存，落盘在 closing 时刻一次完成。

    事件源为 pywebview ``resized/moved/maximized/restored``；高频拖动/缩放
    不产生磁盘 IO。最大化期间普通矩形冻结（resized/moved 不改写），还原后
    以窗口实际矩形续记。
    """

    def __init__(
        self,
        default_rect_fn=None,
        size_guard: "Optional[Callable[[int, int], bool]]" = None,
    ):
        """``default_rect_fn() -> (w, h, x, y)``：最大化且无普通记忆时的落盘
        回退（默认接 :func:`default_normal_rect`）；不传用常量 ``(0, 0)`` 位置。

        ``size_guard(width, height) -> bool``：普通态尺寸守卫（029 审查修复）。
        macOS cocoa 后端在全屏动画期间先发 ``resized``（含全屏尺寸）后发
        ``maximized``——不设守卫时 ``last_normal`` 会被全屏矩形污染，核心 Bug
        在 macOS 复现；守卫拒绝装不进工作区的尺寸（desktop.py 接线注入）。"""
        self._last_normal: Optional[NormalRect] = None
        self._maximized = False
        self._default_rect_fn = default_rect_fn
        self._size_guard = size_guard

    @property
    def maximized(self):
        return self._maximized

    @property
    def last_normal(self):
        return self._last_normal

    def _default_rect(self):
        if self._default_rect_fn is not None:
            try:
                rect = self._default_rect_fn()
                if rect and all(_is_number(v) for v in rect):
                    return tuple(int(v) for v in rect)
            except Exception:
                pass
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT, 0, 0)

    def on_resized(self, width, height):
        """窗口尺寸变化。最大化期间忽略；守卫拒绝的尺寸（如全屏矩形）忽略。"""
        if self._maximized or not (_is_number(width) and _is_number(height)):
            return
        if self._size_guard is not None:
            try:
                if not self._size_guard(int(width), int(height)):
                    return
            except Exception:
                pass
        x = self._last_normal.x if self._last_normal else None
        y = self._last_normal.y if self._last_normal else None
        self._last_normal = NormalRect(int(width), int(height), x, y)

    def on_moved(self, x, y):
        """窗口位置变化。最大化期间忽略。"""
        if self._maximized or not (_is_number(x) and _is_number(y)):
            return
        w = self._last_normal.width if self._last_normal else None
        h = self._last_normal.height if self._last_normal else None
        self._last_normal = NormalRect(w, h, int(x), int(y))

    def on_maximized(self):
        """进入最大化：冻结当前普通矩形。"""
        self._maximized = True

    def on_restored(self, width=None, height=None, x=None, y=None):
        """还原为普通态：解除冻结；窗口实际矩形可得时以其续记。"""
        self._maximized = False
        if all(_is_number(v) for v in (width, height, x, y)):
            self._last_normal = NormalRect(int(width), int(height), int(x), int(y))

    def snapshot_for_save(self, current_w, current_h, current_x, current_y):
        """产出 closing 时刻的落盘内容 ``(w, h, x, y, maximized)``。

        - 最大化 → 最后普通矩形（无则默认普通矩形）+ ``True``；
        - 普通态 → 当前窗口值优先，缺项回退最后普通矩形，仍缺回退默认
          普通矩形（保证首启关闭也能写全）。
        """
        if self._maximized:
            rect = self._last_normal
            if rect is None or any(v is None for v in rect):
                rect = self._default_rect()
            w, h, x, y = rect
            return (int(w), int(h), int(x), int(y), True)

        rect = self._last_normal or (None, None, None, None)
        w = current_w if _is_number(current_w) else rect[0]
        h = current_h if _is_number(current_h) else rect[1]
        x = current_x if _is_number(current_x) else rect[2]
        y = current_y if _is_number(current_y) else rect[3]
        if w is None or h is None:
            dw, dh, dx, dy = self._default_rect()
            w = dw if w is None else w
            h = dh if h is None else h
            x = dx if x is None else x
            y = dy if y is None else y
        if x is None or y is None:
            dw, dh, dx, dy = self._default_rect()
            x = dx if x is None else x
            y = dy if y is None else y
        return (int(w), int(h), int(x), int(y), False)


# ---------------------------------------------------------------------------
# 平台适配：工作区枚举与窗口事件接线（窗口状态域的采集适配件）
# ---------------------------------------------------------------------------
def default_workarea_provider():
    """返回所有显示器工作区 ``[(x, y, w, h)]``；非 Windows 或失败返回 ``[]``。

    通过 ``EnumDisplayMonitors`` + ``GetMonitorInfoW`` 枚举每个显示器的
    ``rcWork``（排除任务栏的可用区域），覆盖副屏场景——窗口在副屏上
    不被误判为越界（合同 §5「任一可见显示器工作区」）。
    """
    if sys.platform != "win32":
        return []
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
        workareas: list[tuple[int, int, int, int]] = []

        monitor_enum_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        def _callback(hmonitor, hdc, rect_ptr, lparam):
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                rect = info.rcWork
                workareas.append(
                    (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                )
            return True

        user32.EnumDisplayMonitors(None, None, monitor_enum_proc(_callback), 0)
        return workareas
    except Exception:
        return []


def wire_window_events(events, window, tracker):
    """订阅 resized/moved/maximized/restored 驱动 Tracker（research D1）。

    pywebview 各后端事件回调参数不总是一致：优先取回调参数（数值），
    缺省回退读 window 属性。单个事件缺失只降级对应追踪，不阻断启动。
    """
    _num = _is_number

    def _on_resized(*args):
        w = args[0] if len(args) > 0 and _num(args[0]) else getattr(window, "width", None)
        h = args[1] if len(args) > 1 and _num(args[1]) else getattr(window, "height", None)
        tracker.on_resized(w, h)

    def _on_moved(*args):
        x = args[0] if len(args) > 0 and _num(args[0]) else getattr(window, "x", None)
        y = args[1] if len(args) > 1 and _num(args[1]) else getattr(window, "y", None)
        tracker.on_moved(x, y)

    def _on_maximized(*_args):
        tracker.on_maximized()

    def _on_restored(*args):
        if len(args) >= 4 and all(_num(v) for v in args[:4]):
            tracker.on_restored(args[0], args[1], args[2], args[3])
        else:
            tracker.on_restored(
                getattr(window, "width", None),
                getattr(window, "height", None),
                getattr(window, "x", None),
                getattr(window, "y", None),
            )

    for name, handler in (
        ("resized", _on_resized),
        ("moved", _on_moved),
        ("maximized", _on_maximized),
        ("restored", _on_restored),
    ):
        event = getattr(events, name, None)
        if event is not None:
            event += handler
