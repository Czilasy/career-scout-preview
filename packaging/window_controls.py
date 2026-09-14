"""窗口控制 Win32 助手（spec 036 B084 自绘标题栏 / spec 042 R0 拆分 / 036 v2 拉伸）。

职责：本模块是原生窗口控制的唯一归属地——
- 无边框窗口的最小化/最大化/还原原语与最大化状态实时标记；
- 目标 HWND 定位（EnumWindows 纯 Win32）；
- 窗口适配器安装（顶层唯一 ``WM_GETMINMAXINFO`` 链：当前显示器工作区 +
  逐轴最小/最大跟踪尺寸，幂等、返回安装报告）；
- 无边框尺寸修正（WINDOWPLACEMENT 还原矩形）；
- **宿主交互引擎**（036 v2）：页面判定区域后一次调用，宿主用 Win32
  完成八方向拉伸、标题带移动、最大化拖下还原、顶部释放最大化。

``packaging/desktop.py`` 只负责接线（``create_window`` 参数 + ``js_api``
暴露 + ``events.shown`` 调用点），所有窗口操作逻辑集中在本模块，便于
纯逻辑单测。

设计约束（specs/036-titlebar-dynamic-island/contracts/desktop-window-interaction.md，
specs/042-desktop-window-controls-split/plan.md）：
- 仅依赖注入的 pywebview Window 对象，不直接 import webview；
- 不反向 import ``packaging.desktop``：工作区数据、诊断日志出口与目标
  尺寸一律由调用方注入；
- 最大化避让任务栏由本模块的适配器（顶层 `WM_GETMINMAXINFO` 链）保证：
  最大化矩形 = 当前显示器工作区，失败时静默降级、不阻断启动。
- 每个原语返回 ``{"ok": bool, "error": str|None}``，供前端 js_api 直出。

036 v2 关键事实（Research D9，2026-09-14 真机实测）：无边框窗体客户区被
WebView2 子窗口整块覆盖（含四边四角），顶层窗体收到的 ``WM_NCHITTEST``
为 0 次，且该子窗口属于 WebView2 运行时进程（跨进程钩子被拒绝）。因此
命中必须由页面（DOM 事件，CSS 像素）判定并一次上报，宿主只负责原生执行：
不新增 WndProc hook、不使用系统 ``SC_MOVE`` 循环（故无系统左右贴靠）、
不做前端逐帧回传。
"""

import ctypes
import os
import sys
from ctypes import wintypes

# 度量域（显示器工作区 / DPI / 尺寸换算）：036 v2 按宪法 75% 预警线分流独立
from packaging.window_metrics import (  # noqa: E402
    min_track_size,
    resolve_workarea,
    window_scale,
)

def _ok(error=None):
    """统一返回体：无错误 -> {ok: True}；有错误 -> {ok: False, error}。"""
    return {"ok": error is None, "error": error}


def _is_maximized(window):
    """读取窗口最大化状态。

    T022 修复：pywebview 6.x 的 ``Window.maximized`` 是构造参数快照、不随
    真实状态更新，直接读它会导致 toggle_maximize 永远走最大化、还原失效。
    优先读 ``_cs_maximized``（desktop.py 经 ``events.maximized/restored``
    维护的实时标记）；未接线（老替身/测试替身）时回退读构造快照。
    """
    live = getattr(window, "_cs_maximized", None)
    if live is not None:
        return bool(live)
    snapshot = getattr(window, "maximized", None)
    return bool(snapshot) if snapshot is not None else None


def note_maximized(window, value):
    """pywebview ``events.maximized``/``events.restored`` 回调入口。

    T022 修复：窗口真实最大化/还原后由 desktop.py 调用本函数刷新实时标记，
    toggle_maximize 以此为准做最大化 <-> 还原切换。
    """
    setattr(window, "_cs_maximized", bool(value))


def is_maximized(window):
    """查询窗口是否处于最大化（前端按钮图标切换用，FR-004）。

    与 ``_is_maximized`` 一致：优先读 ``_cs_maximized`` 实时标记（desktop.py
    经 ``events.maximized/restored`` 维护），未接线时回退读构造快照；
    两者皆缺视为普通态。
    """
    return bool(_is_maximized(window))


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
    """最大化 <-> 还原切换（标题栏双击/按钮共用）。

    返回体带 ``maximized`` 字段（切换后的真实状态），供前端同步
    最大化/还原按钮图标（spec FR-004）。
    """
    try:
        if _is_maximized(window):
            window.restore()
            maximized = False
        else:
            window.maximize()
            maximized = True
        return {"ok": True, "error": None, "maximized": maximized}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 诊断出口（spec 042 R0 自 desktop.py 迁入）
# ---------------------------------------------------------------------------
def log_desktop_error(message, state_dir=None, logger=None):
    """写桌面诊断日志：优先用注入 logger，否则延迟 import 桌面入口日志函数。

    延迟 import（而非模块级 import）保证本模块不反向依赖
    ``packaging.desktop``；被 import 的日志函数自身已对写盘失败静默，
    此处再兜一层异常，保证诊断永不阻断窗口操作。
    """
    if logger is not None:
        try:
            logger(message)
            return
        except Exception:
            pass
    try:
        from packaging.desktop import log_error as _log_error

        _log_error(message, state_dir=state_dir, logger=None)
    except Exception:
        pass


def diag_fn(state_dir=None, logger=None):
    """产出诊断记录函数；无任何诊断出口时返回 ``None``（调用点自会跳过）。"""
    if state_dir is None and logger is None:
        return None
    return lambda msg: log_desktop_error(msg, state_dir=state_dir, logger=logger)


# ---------------------------------------------------------------------------
# 目标 HWND 定位（spec 042 R0 自 desktop.py 迁入）
# ---------------------------------------------------------------------------
def find_main_hwnd_pid():
    """EnumWindows 找当前进程的可见顶层窗口 hwnd。

    纯 Win32（GetWindowThreadProcessId + IsWindowVisible），**不碰**
    ``window.native``——shown 回调跑在工作线程（pywebview Event.set
    用 threading.Thread），访问 ``native.Handle`` 会跨线程强制 WinForms
    句柄创建抛 InvalidOperationException，是旧版 hook=False 的根因
    （2026-09-03 _probe_hook.py 验证：改 EnumWindows 后 PASS）。

    标题含 "Career Scout" 的优先（多窗口兜底）；否则取第一个可见顶层。
    """
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    pid = os.getpid()
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        pid_out = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value[:32] if n else ""
        found.append((hwnd, title))
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    if not found:
        return None, ""
    # 标题含 Career Scout 优先
    for hwnd, title in found:
        if "Career Scout" in title:
            return hwnd, title
    return found[0][0], found[0][1]


# ---------------------------------------------------------------------------
# 窗口适配器安装（spec 042 R0 迁入；036 v2 扩展为当前显示器工作区 + 逐轴跟踪）
# ---------------------------------------------------------------------------
def install_window_adapter(window, workarea_provider=None, state_dir=None,
                           logger=None, hwnd_finder=None, hook_installer=None):
    """安装顶层窗口适配器（幂等），返回 ``{"installed", "reason", "hwnd_found"}``。

    036 v2 起本函数是窗口适配器的唯一安装入口，只安装**一条** WndProc 链
    （顶层 ``WM_GETMINMAXINFO``：最大化避让任务栏 + 逐轴最小/最大跟踪尺寸）。
    拉伸与移动由窗口交互域（``packaging/window_interaction.py``）的宿主循环
    完成，本模块不新增第二条 hook（Research D9）。

    幂等：同一 window 重复安装返回首次报告且不重复挂钩；安装失败不缓存，
    允许重试。失败不阻断启动，``reason`` 取值稳定：``unsupported_platform``
    / ``hwnd_not_found`` / ``hook_failed``，供 Gate 1 判定阻断。

    ``hwnd_finder`` / ``hook_installer`` 为测试注入点，``None`` 时用真实 Win32。
    """
    cached = getattr(window, "_cs_window_adapter", None)
    if isinstance(cached, dict) and cached.get("installed"):
        return dict(cached)
    if sys.platform != "win32":
        return {"installed": False, "reason": "unsupported_platform",
                "hwnd_found": False}

    finder = hwnd_finder or find_main_hwnd_pid
    try:
        found = finder() or (None, "")
    except Exception:
        found = (None, "")
    hwnd, _title = found if isinstance(found, (tuple, list)) else (found, "")
    if not hwnd:
        return {"installed": False, "reason": "hwnd_not_found", "hwnd_found": False}

    installer = hook_installer or _install_minmax_hook
    try:
        installed = bool(installer(window, hwnd, workarea_provider, state_dir, logger))
    except Exception:
        installed = False
    if not installed:
        diag = diag_fn(state_dir=state_dir, logger=logger)
        if diag is not None:
            diag(f"窗口适配器安装失败: hook_failed hwnd={hwnd}")
        return {"installed": False, "reason": "hook_failed", "hwnd_found": True}

    report = {"installed": True, "reason": None, "hwnd_found": True}
    try:
        window._cs_window_adapter = dict(report)
    except Exception:
        pass
    return report


def _install_minmax_hook(window, hwnd, workarea_provider=None,
                         state_dir=None, logger=None):
    """顶层唯一 WndProc 链：``WM_GETMINMAXINFO`` 按当前显示器工作区钳制。

    WinForms 无边框窗体最大化的默认矩形 = 整屏（物理 1920×1080）而非
    工作区（1920×1040），任务栏被盖住——「全屏大小但没对齐屏幕」根因。
    ``Form.MaximizedBounds`` 在 pywebview winforms frameless 上实测不
    生效（2026-09-03 diag：设后最大化尺寸仍 1920×1080）。改走 Win32：
    ``SetWindowLongPtrW`` 装自己的 WndProc，拦截 ``WM_GETMINMAXINFO`` 填
    当前显示器工作区与逐轴最小/最大跟踪尺寸，绕过所有 .NET/pywebview
    最大化机制；安装后 ``ShowWindowAsync`` 重切一次让消息重发读新值。

    时机：挂在 ``events.shown``（窗口显示后）。shown 回调跑在工作线程
    （pywebview Event.set 用 threading.Thread），但 **不碰 native 对象**
    ——hwnd 由调用方经 :func:`find_main_hwnd_pid`（EnumWindows 纯 Win32）
    取得，SetWindowLongPtrW/ShowWindowAsync 是 Win32 API，跨线程安全。

    ``workarea_provider`` 由调用方注入，仅在原生读取失败时兜底；本模块不
    反向依赖桌面入口。返回 ``True`` 表示钩子已安装并留下强引用。

    2026-09-03：``_probe_hook.py`` 独立验证 PASS（intercepted_count=2，
    最大化矩形钳到 1920×1040）。旧版 hook=False 根因即跨线程访问
    ``native.Handle``。
    """
    _diag = diag_fn(state_dir=state_dir, logger=logger)
    _log = _diag or (lambda _msg: None)
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_void_p
        ]
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindowAsync.restype = wintypes.BOOL

        GWLP_WNDPROC = -4
        WM_GETMINMAXINFO = 0x0024
        SW_RESTORE, SW_MAXIMIZE = 9, 3
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010

        provider = workarea_provider or _default_workarea_provider()
        area = resolve_workarea(hwnd=hwnd, workarea_provider=provider)
        if not area:
            _log("adapter FAIL: no workarea")
            return False
        _log(f"adapter install hwnd={hwnd} workarea={area}")

        class MINMAXINFO(ctypes.Structure):
            _fields_ = [
                ("ptReserved", wintypes.POINT),
                ("ptMaxSize", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT),
                ("ptMaxTrackSize", wintypes.POINT),
            ]

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM
        )
        orig_proc = user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)

        intercepted = {"count": 0}

        def _new_proc(h, msg, w, l):
            if msg == WM_GETMINMAXINFO:
                try:
                    current = resolve_workarea(hwnd=hwnd, workarea_provider=provider)
                    if current:
                        cx, cy, cw, ch = current
                        min_w, min_h = min_track_size(
                            cw, ch, window_scale(hwnd), *min_normal_size_css()
                        )
                        pmmi = ctypes.cast(l, ctypes.POINTER(MINMAXINFO))
                        pmmi.contents.ptMaxSize = wintypes.POINT(cw, ch)
                        pmmi.contents.ptMaxPosition = wintypes.POINT(cx, cy)
                        pmmi.contents.ptMaxTrackSize = wintypes.POINT(cw, ch)
                        pmmi.contents.ptMinTrackSize = wintypes.POINT(min_w, min_h)
                        intercepted["count"] += 1
                        return 0
                except Exception:
                    pass
            return user32.CallWindowProcW(orig_proc, h, msg, w, l)

        proc_obj = WNDPROC(_new_proc)
        # 保引用防 GC（窗口销毁前 proc_obj 必须存活，否则回调野指针崩进程）
        window._cs_maximize_hook = (proc_obj, orig_proc, intercepted)
        user32.SetWindowLongPtrW(
            hwnd, GWLP_WNDPROC, ctypes.cast(proc_obj, ctypes.c_void_p)
        )
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.IsZoomed.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        if user32.IsZoomed(hwnd):
            # 启动即最大化：同步重切一次让 WM_GETMINMAXINFO 带着本 hook 重发。
            # 必须同步（ShowWindowAsync 顺序不确定，实测重切后 intercepted 仍为 0，
            # 窗口停在整屏 1920×1080 盖住任务栏）。
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            rect = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
            if rect != (ax, ay, aw, ah):
                # 兜底：重切后仍未钳到工作区就直接把最大化矩形校正到工作区
                user32.SetWindowPos(
                    hwnd, None, ax, ay, aw, ah, SWP_NOZORDER | SWP_NOACTIVATE
                )
                rc = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rc))
                _log(
                    "adapter clamp fallback workarea="
                    f"({ax},{ay},{aw},{ah}) rect="
                    f"{rc.right - rc.left}x{rc.bottom - rc.top}@{rc.left},{rc.top}"
                )
            _log(
                f"adapter verify rect={rc.right - rc.left}x{rc.bottom - rc.top} "
                f"@{rc.left},{rc.top} intercepted={intercepted['count']}"
            )
        else:
            # 普通态（记忆为普通窗口）：不得为了装钩子把它切成最大化
            _log("adapter install: window is normal, skip maximize re-toggle")
        return True
    except Exception as e:
        _log(f"EXC {type(e).__name__}: {e}")
        return False


def _default_workarea_provider():
    """默认工作区提供者：窗口状态域的显示器工作区枚举（延迟 import 防环）。"""
    try:
        from packaging.window_state import default_workarea_provider

        return default_workarea_provider
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 无边框尺寸修正（spec 042 R0 自 desktop.py 迁入）
# ---------------------------------------------------------------------------
def fix_frameless_size(window, target_w, target_h, target_x=None, target_y=None,
                       state_dir=None, logger=None):
    """修正 pywebview WinForms frameless 窗口尺寸缩水。

    根因：pywebview winforms.py 先 ``self.Size = Size(w, h)``（此时窗口
    有系统边框+标题栏），后 ``FormBorderStyle = None``（去边框）。WinForms
    切换 FormBorderStyle 时保持客户区不变，边框消失后外框 = 客户区，
    比设入值小了边框+标题栏的量（约 16px×39px，见 desktop.log 1529×761）。

    修正：用 Win32 ``GetWindowPlacement`` / ``SetWindowPlacement`` 修正
    ``rcNormalPosition`` 到目标物理尺寸（含 DPI 缩放）。当前最大化时只
    更新还原矩形不改当前显示；当前普通态时窗口立即跳到正确尺寸。
    任何失败静默降级（不阻断启动）。

    036 v2：``target_w/target_h`` 由调用方传入**本次实际要用的普通矩形**
    （启动记忆或默认值），不再固定 1400×800——否则用户拉伸过的记忆会在
    每次启动被改回默认尺寸（FR-013/FR-015）。``target_x/target_y`` 给出时
    保持该位置（同样不复用固定居中，避免把窗口挪到别的显示器）。
    """
    import sys

    if sys.platform != "win32":
        return
    _diag = diag_fn(state_dir=state_dir, logger=logger)
    _log = _diag or (lambda _msg: None)
    try:
        user32 = ctypes.windll.user32

        hwnd, _title = find_main_hwnd_pid()
        if not hwnd:
            _log("find_hwnd FAIL")
            return

        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        dpi = user32.GetDpiForWindow(hwnd)
        scale = dpi / 96.0 if dpi else 1.0
        phys_w = int(target_w * scale)
        phys_h = int(target_h * scale)

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
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            _log("GetWindowPlacement FAIL")
            return

        rc = placement.rcNormalPosition
        cur_w = rc.right - rc.left
        cur_h = rc.bottom - rc.top
        if cur_w == phys_w and cur_h == phys_h:
            _log(f"already correct {phys_w}x{phys_h}")
            return

        if target_x is not None and target_y is not None:
            x, y = int(target_x), int(target_y)
        else:
            # 未给位置（无记忆）时保持窗口当前位置，不再按主屏居中：
            # 复用固定居中会把用户放在副屏的窗口拽回主屏。
            x, y = rc.left, rc.top

        placement.rcNormalPosition = wintypes.RECT(x, y, x + phys_w, y + phys_h)
        user32.SetWindowPlacement(hwnd, ctypes.byref(placement))
        _log(f"fixed {cur_w}x{cur_h} -> {phys_w}x{phys_h} @ {x},{y}")
    except Exception as e:
        _log(f"EXC {type(e).__name__}: {e}")
