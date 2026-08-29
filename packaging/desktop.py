"""桌面壳 —— EXE 进程入口（spec003 tasks005）。

T035 接线点（冻结合同 ``specs/003-desktop-exe/contracts/desktop-shell.md``）：

1. **单实例**（§2）：named mutex ``CareerScout-SingleInstance``（不带版本号），
   ``CreateMutexW`` + ``GetLastError()==183`` 判定已有实例 → MessageBox 提示 + 退出 0；
   锁必须在 Flask 启动**之前**获取。
2. **随机端口**（§3）：复用 ``webui.desktop_runtime.pick_free_port``，
   ``socket.bind(("127.0.0.1", 0))`` 获取 OS 分配端口；失败 → 错误退出。
3. **Flask 线程**（§4）：``create_app(config={RUNTIME_MODE="exe",
   PYTHON_EXECUTABLE=sys.executable, START_TASKS=True})``，
   ``app.run(use_reloader=False, threaded=True)`` 在独立 daemon 线程。
4. **就绪轮询**（§4）：``GET /api/session`` 直到 200，超时 ≤30s → 错误退出。
5. **窗口**（§5，029 修订）：普通态默认 1545×900 居中（小屏钳回），
   ``min_size=(1024,700)``，``resizable=True``；从 ``~/.career-scout/desktop_window.json``
   （schema 3）恢复普通矩形与最大化标记——无记忆/损坏/污染记忆一律首开
   最大化；事件维护普通矩形（窗口状态域 ``packaging/window_state.py``）。
6. **closing 生命周期**（§6）：``events.closing`` → 按 Tracker 快照落盘
   （最大化 → 最后普通矩形 + ``maximized:true``）→ ``cancel_running_tasks``
   → 返回退出码；``main()`` 调用方 ``os._exit(code)`` 兜底（合同 §6）。
7. **错误路径**（§7）：MessageBox + ``~/.career-scout/desktop.log`` 追加记录 + 非零退出。

所有外部依赖（mutex/messagebox/webview/create_app/pick_free_port/http_get/logger）
通过 ``run_desktop_shell(deps)`` 注入，便于纯逻辑单测；``main()`` 组装默认依赖。
"""

import os
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
# PyInstaller onefile 模式下，entry 脚本（desktop.py）被解压到 sys._MEIPASS 根目录
# （不保留 packaging/ 子目录），而 webui/、scripts/、data/、pyproject.toml 等
# 资源同样在 sys._MEIPASS 根目录；源码模式下 __file__ 位于 packaging/，_PROJECT_ROOT
# 是其父（项目根）。两种模式下资源根都是 _PROJECT_ROOT。
def _resolve_project_root():
    if getattr(sys, "frozen", False):
        # PyInstaller onefile/dir：资源根 = sys._MEIPASS
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    # 源码模式：__file__ = <root>/packaging/desktop.py
    return Path(__file__).resolve().parent.parent

_PROJECT_ROOT = _resolve_project_root()
LOG_FILENAME = "desktop.log"
MUTEX_NAME = "CareerScout-SingleInstance"
ERROR_ALREADY_EXISTS = 183
BACKEND_READY_TIMEOUT = 30.0
BACKEND_READY_POLL_INTERVAL = 0.2

# 窗口状态域（029 分流）：常量与读写实现全部在 window_state.py，
# 此处 re-export 保持旧调用面（tests/test_desktop_shell.py 等既有引用）。
try:  # 包导入（unittest/PyInstaller 模块分析）
    from packaging import window_state as _ws
except ImportError:  # 脚本直跑（python packaging/desktop.py）时的同目录回退
    import window_state as _ws  # type: ignore

DEFAULT_HEIGHT = _ws.DEFAULT_HEIGHT
DEFAULT_STATE_DIR = _ws.DEFAULT_STATE_DIR
DEFAULT_WIDTH = _ws.DEFAULT_WIDTH
MAX_HEIGHT = _ws.MAX_HEIGHT
MAX_WIDTH = _ws.MAX_WIDTH
MIN_HEIGHT = _ws.MIN_HEIGHT
MIN_WIDTH = _ws.MIN_WIDTH
WINDOW_STATE_FILENAME = _ws.WINDOW_STATE_FILENAME
WindowStateTracker = _ws.WindowStateTracker
default_normal_rect = _ws.default_normal_rect
default_workarea_provider = _ws.default_workarea_provider
size_fits_workareas = _ws.size_fits_workareas
load_window_state = _ws.load_window_state
save_window_state = _ws.save_window_state
wire_window_events = _ws.wire_window_events

_default_workarea_provider = default_workarea_provider  # 兼容别名（旧符号）


# ---------------------------------------------------------------------------
# 版本读取
# ---------------------------------------------------------------------------
def read_version():
    """从 ``pyproject.toml`` 读取版本号；读不到返回 ``"0.0.0"``。"""
    try:
        text = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                _, _, value = stripped.partition("=")
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return "0.0.0"


# ---------------------------------------------------------------------------
# 单实例（T036 / 合同 §2）
# ---------------------------------------------------------------------------
def _default_mutex_factory(name):
    """单实例锁工厂（跨平台）。返回 ``(handle, last_error)``。

    - Windows：named mutex（ctypes ``CreateMutexW``）；
    - macOS/Linux：``~/.career-scout/{name}.lock`` 文件锁（``fcntl.flock``
      非阻塞独占），已被占用时 ``last_error == ERROR_ALREADY_EXISTS``；
    - 平台锁机制不可用 → ``(None, 0)``，由调用方放行。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, name)
            last_error = kernel32.GetLastError()
            return (handle, last_error)
        except (OSError, AttributeError):
            return (None, 0)
    # POSIX（macOS/Linux）：文件锁实现单实例（进程退出 flock 自动释放）
    try:
        import fcntl

        lock_dir = Path(os.path.expanduser("~/.career-scout"))
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = open(lock_dir / f"{name}.lock", "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return (lock_file, 0)
        except (BlockingIOError, PermissionError, OSError):
            lock_file.close()
            return (True, ERROR_ALREADY_EXISTS)
    except (ImportError, OSError):
        return (None, 0)


def acquire_single_instance_mutex(mutex_factory=None):
    """获取单实例 mutex。

    返回 ``(handle, already_exists)``：
    - handle 非 None 且 already_exists=False：当前进程获得锁；
    - handle 非 None 且 already_exists=True：已有实例；
    - handle 为 None：平台锁机制不可用，放行。
    """
    factory = mutex_factory or _default_mutex_factory
    handle, last_error = factory(MUTEX_NAME)
    if handle is None:
        return (None, False)
    return (handle, last_error == ERROR_ALREADY_EXISTS)


def release_single_instance_mutex(handle):
    """释放单实例锁（进程退出时）。Windows 关闭 mutex 句柄；
    POSIX 关闭锁文件句柄（flock 随 fd 关闭自动释放）。"""
    if handle is None:
        return
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(handle)
        elif hasattr(handle, "close"):
            handle.close()
    except Exception:
        # 清理函数：任何异常（含 ctypes.ArgumentError 替身 handle）
        # 都不应向外抛，进程即将退出
        pass


# 窗口状态文件（T037/合同 §5）029 起迁移至 packaging/window_state.py（re-export 兼容）


# ---------------------------------------------------------------------------
# 错误日志（T038 / 合同 §7）
# ---------------------------------------------------------------------------
def _log_path(state_dir):
    base = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    return base / LOG_FILENAME


def log_error(message, state_dir=None, logger=None):
    """追加错误日志。注入 logger 时优先调用 logger(message)。"""
    if logger is not None:
        try:
            logger(message)
            return
        except Exception:
            pass
    try:
        path = _log_path(state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 就绪轮询（T038 / 合同 §4）
# ---------------------------------------------------------------------------
def _default_http_get(url):
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=2) as resp:
            return (resp.status, resp.read())
    except Exception:
        return None


def wait_for_backend_ready(port, timeout=BACKEND_READY_TIMEOUT, http_get=None,
                           poll_interval=BACKEND_READY_POLL_INTERVAL):
    """轮询 ``GET /api/session`` 直到 200 或超时。

    http_get 协议：``get(url) -> (status, body) | None``。
    """
    getter = http_get or _default_http_get
    url = f"http://127.0.0.1:{port}/api/session"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = getter(url)
        except Exception:
            result = None
        if result is not None:
            status = result[0] if isinstance(result, tuple) else getattr(
                result, "status", None
            )
            if status == 200:
                return True
        time.sleep(poll_interval)
    return False


# ---------------------------------------------------------------------------
# 抓取取消（T039 / 合同 §6）
# ---------------------------------------------------------------------------
def cancel_running_tasks(app):
    """触发所有运行中任务的取消事件。

    遍历 ``app.config["TASK_RUNNER"]`` 和 ``["WORKBENCH_RUNNER"]`` 的
    ``_cancel_events`` 字典，对每个 event 调用 ``set()``。不等待，只标记取消。
    """
    if app is None:
        return
    config = getattr(app, "config", None)
    if not config:
        return
    for key in ("TASK_RUNNER", "WORKBENCH_RUNNER"):
        runner = config.get(key) if hasattr(config, "get") else None
        if runner is None:
            continue
        cancel_events = getattr(runner, "_cancel_events", None)
        if not cancel_events:
            continue
        for task_id in list(cancel_events.keys()):
            event = cancel_events.get(task_id)
            if event is not None:
                try:
                    event.set()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 默认 MessageBox
# ---------------------------------------------------------------------------
def _default_messagebox(title, text):
    """跨平台消息框：Windows 用 ``MessageBoxW``，macOS 用 ``osascript``
    原生对话框；平台机制不可用时回退 print。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, title, 0x00000000)
            return
        except (OSError, AttributeError):
            pass
    elif sys.platform == "darwin":
        try:
            import subprocess

            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display dialog "{safe_text}" with title "{safe_title}"'
                    ' buttons {"好"} default button "好"',
                ],
                check=False,
                capture_output=True,
                timeout=120,
            )
            return
        except Exception:
            pass
    print(f"[{title}] {text}")


def _run_flask_server(app, port):
    """在独立线程跑 Flask；异常不向外抛（由就绪轮询超时捕获）。"""
    try:
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# pywebview JS API（前端 window.pywebview.api.*）
# ---------------------------------------------------------------------------
#: 外链域名白名单：只允许跳到项目仓库，防止被注入打开任意地址
EXTERNAL_LINK_HOSTS = ("github.com",)
EXTERNAL_REPO_URL = "https://github.com/Czilasy/career-scout-preview"


def is_allowed_external_url(url):
    """https + 白名单域名（含子域）才放行；其余一律拒绝。"""
    try:
        from urllib.parse import urlparse
        from webui.url_safety import is_safe_https_authority

        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    return is_safe_https_authority(
        parsed, allowed_hosts=EXTERNAL_LINK_HOSTS, allow_subdomains=True
    )


class DesktopJsApi:
    """暴露给 WebUI 前端的桌面壳能力。

    - ``open_external(url)``：系统默认浏览器打开白名单链接（桌面壳
      内直接跳转会吞掉应用页面，必须外抛到浏览器）；
    - ``quit_app()``：应用内更新重启前的优雅退出：先保存窗口
      状态、取消运行中任务，再销毁窗口。清理逻辑由
      ``run_desktop_shell`` 在窗口创建后注入 ``quit_handler``。
    """

    def __init__(self):
        # run_desktop_shell 创建窗口后注入（保存状态 + 取消任务 + 关窗）
        self.quit_handler = None

    def open_external(self, url=""):
        target = str(url or "").strip() or EXTERNAL_REPO_URL
        if not is_allowed_external_url(target):
            return {"ok": False, "error": "url_not_allowed"}
        try:
            import webbrowser

            webbrowser.open(target)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def quit_app(self):
        handler = getattr(self, "quit_handler", None)
        if callable(handler):
            try:
                handler()
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        try:
            import webview

            windows = list(getattr(webview, "windows", []) or [])
            if not windows:
                return {"ok": False, "error": "no_window"}
            windows[0].destroy()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 主编排（T039）
# ---------------------------------------------------------------------------
def run_desktop_shell(deps):
    """桌面壳主编排。所有外部依赖通过 ``deps`` 注入。

    返回退出码：
    - 0：单实例已存在（提示后退出）或正常关闭；
    - 非 0：错误路径（端口/Flask/就绪/窗口）。

    deps keys（全部可选，None 用默认）：
    - mutex_factory, messagebox, create_app, pick_free_port, http_get
    - logger, state_dir, workarea_provider, version, webview_module, ready_timeout
    """
    mutex_factory = deps.get("mutex_factory")
    messagebox = deps.get("messagebox") or _default_messagebox
    create_app = deps.get("create_app")
    pick_free_port = deps.get("pick_free_port")
    http_get = deps.get("http_get")
    logger = deps.get("logger")
    state_dir = deps.get("state_dir")
    workarea_provider = deps.get("workarea_provider") or _default_workarea_provider
    version = deps.get("version") or read_version()
    webview_module = deps.get("webview_module")
    ready_timeout = deps.get("ready_timeout")
    if ready_timeout is None:
        ready_timeout = BACKEND_READY_TIMEOUT

    # 1. 单实例检查（Flask 启动前）
    handle, already_exists = acquire_single_instance_mutex(mutex_factory=mutex_factory)
    if already_exists:
        messagebox("Career Scout", "Career Scout 已在运行")
        return 0

    # 2. 随机端口
    if pick_free_port is None:
        from webui.desktop_runtime import pick_free_port as _pfp

        pick_free_port = _pfp
    try:
        port = pick_free_port()
    except Exception as exc:
        log_error(f"端口选择失败: {exc}", state_dir=state_dir, logger=logger)
        messagebox("Career Scout", f"启动失败：无法选择空闲端口\n{exc}")
        return 1

    # 3. Flask 后端
    if create_app is None:
        from webui.app import create_app as _create_app

        create_app = _create_app
    try:
        app = create_app(
            config={
                "RUNTIME_MODE": "exe",
                "PYTHON_EXECUTABLE": sys.executable,
                "START_TASKS": True,
            }
        )
    except Exception as exc:
        log_error(f"Flask 创建失败: {exc}", state_dir=state_dir, logger=logger)
        messagebox("Career Scout", f"启动失败：后端初始化错误\n{exc}")
        return 1

    server_thread = threading.Thread(
        target=_run_flask_server,
        args=(app, port),
        name="career-scout-flask",
        daemon=True,
    )
    server_thread.start()

    # 4. 就绪轮询
    if not wait_for_backend_ready(
        port, timeout=ready_timeout, http_get=http_get
    ):
        log_error(f"后端就绪超时（端口 {port}）", state_dir=state_dir, logger=logger)
        messagebox(
            "Career Scout",
            f"启动失败：后端未在 {int(ready_timeout)}s 内就绪",
        )
        return 1

    # 5. 窗口状态（schema 3：普通矩形 + maximized 标记；无记忆 = 首开最大化）
    width, height, x, y, start_maximized = load_window_state(
        state_dir=state_dir, workarea_provider=workarea_provider
    )
    # macOS 全屏动画先发 resized（全屏尺寸）后发 maximized：守卫拒绝装不进
    # 工作区的尺寸，防止 last_normal 被全屏矩形污染（029 审查修复）
    tracker = WindowStateTracker(
        default_rect_fn=lambda: default_normal_rect(workarea_provider),
        size_guard=lambda w, h: size_fits_workareas(w, h, workarea_provider),
    )
    title = f"Career Scout v{version}"

    # 6. pywebview 窗口
    if webview_module is None:
        try:
            import webview as webview_module  # type: ignore
        except Exception as exc:
            log_error(
                f"pywebview 导入失败: {exc}", state_dir=state_dir, logger=logger
            )
            messagebox("Career Scout", "启动失败：缺少 pywebview 依赖")
            return 1

    js_api = deps.get("js_api") or DesktopJsApi()

    window_kwargs = {
        "url": f"http://127.0.0.1:{port}",
        "title": title,
        "width": width,
        "height": height,
        "resizable": True,
        "min_size": (MIN_WIDTH, MIN_HEIGHT),
        "background_color": "#0d1113",
        "shadow": False,
        # 记忆 maximized=True → 启动即真最大化（还原落回普通矩形参数）
        "maximized": bool(start_maximized),
        # 前端通过 window.pywebview.api 调用（外链打开/退出应用）
        "js_api": js_api,
    }
    if x is not None and y is not None:
        window_kwargs["x"] = x
        window_kwargs["y"] = y
    # 窗口/任务栏图标：pywebview 6.x 的 create_window 已移除 icon 参数
    # （传入会直接抛 TypeError），winforms 后端会自动从 EXE 资源提取图标
    # （即 spec 里 icon=career_scout.ico 的指南针），无需显式传入。

    def _on_closing():
        try:
            # Tracker 快照 = 普通矩形语义：最大化 → 冻结普通矩形，普通态 →
            # 当前窗口值（缺项逐级回退）；全屏矩形永不写入（029 契约）。
            save_w, save_h, save_x, save_y, was_maximized = (
                tracker.snapshot_for_save(
                    getattr(window, "width", None),
                    getattr(window, "height", None),
                    getattr(window, "x", None),
                    getattr(window, "y", None),
                )
            )
            save_window_state(
                save_w,
                save_h,
                save_x,
                save_y,
                state_dir=state_dir,
                maximized=was_maximized,
            )
        except Exception:
            pass
        try:
            cancel_running_tasks(app)
        except Exception:
            pass

    try:
        window = webview_module.create_window(**window_kwargs)
        # pywebview 6.x 事件 API：window.events.closing += handler
        events = getattr(window, "events", None)
        if events is not None and hasattr(events, "closing"):
            events.closing += _on_closing
            wire_window_events(events, window, tracker)
        else:
            # pywebview <6 没有 events API：窗口状态记忆与关闭时任务取消
            # 都会静默失效，必须明示（README 约束 pywebview>=6.0）
            log_error(
                "pywebview 事件 API 不可用（需 pywebview>=6.0）："
                "窗口状态记忆与关闭时任务取消将不会生效",
                state_dir=state_dir, logger=logger,
            )

        def _quit_and_cleanup():
            """js_api.quit_app 的优雅退出：复用 closing 同样的清理逻辑。"""
            try:
                _on_closing()
            except Exception:
                pass
            # 不能立即 destroy + os._exit：pywebview 还来不及把 {"ok": true}
            # 回传给前端 JS，用户会看到"退出失败"报错。这里保留窗口、先让
            # 返回值回传（毫秒级），再由 Timer 兜底强制退出进程。
            # 注：不调用 destroy，webview.start() 不会因本路径返回（WinForms
            # 事件循环不结束），因此必须由本 Timer 退出，替换脚本才能等到
            # 主进程 PID 消失并接棒替换。
            threading.Timer(1.5, lambda: os._exit(0)).start()

        js_api.quit_handler = _quit_and_cleanup
        webview_module.start()
    except Exception as exc:
        # 合同 §7：webview.start() 抛初始化异常 → 按平台给指引
        log_error(
            f"窗口初始化失败: {exc}", state_dir=state_dir, logger=logger
        )
        if sys.platform == "win32":
            failure_message = (
                "启动失败：WebView2 运行时缺失或初始化异常\n"
                "请安装 Microsoft Edge WebView2 Runtime\n"
                "下载地址：https://developer.microsoft.com/microsoft-edge/webview2/\n"
                f"详情：{exc}"
            )
        else:
            failure_message = (
                "启动失败：窗口初始化异常\n"
                "macOS 下 pywebview 使用系统 WebKit，请确认系统版本 ≥ macOS 11\n"
                f"详情：{exc}"
            )
        messagebox("Career Scout", failure_message)
        return 1

    # 7. 关闭后释放锁
    release_single_instance_mutex(handle)
    return 0


def main():
    """EXE 入口：组装默认依赖并启动壳；返回后 ``os._exit`` 兜底。"""
    code = run_desktop_shell({})
    # 合同 §6：webview.start() 返回后 os._exit(0) 兜底，确保无残留线程
    os._exit(code)


if __name__ == "__main__":
    main()
