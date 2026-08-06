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
5. **窗口**（§5）：标题 ``Career Scout v{version}``（pyproject.toml 读版本），
   默认 1280×800，``min_size=(1024,700)``，``resizable=True``，
   从 ``~/.career-scout/desktop_window.json`` 恢复状态（越界回退居中）。
6. **closing 生命周期**（§6）：``events.closing`` → 保存窗口状态 →
   ``cancel_running_tasks``（set 所有 ``_cancel_events``）→
   ``run_desktop_shell`` 返回退出码；``main()`` 调用方负责 ``os._exit(code)``
   兜底（合同 §6 要求 ``webview.start()`` 返回后 ``os._exit`` 确保无残留线程）。
7. **错误路径**（§7）：MessageBox + ``~/.career-scout/desktop.log`` 追加记录 + 非零退出。

所有外部依赖（mutex/messagebox/webview/create_app/pick_free_port/http_get/logger）
通过 ``run_desktop_shell(deps)`` 注入，便于纯逻辑单测；``main()`` 组装默认依赖。
"""

import json
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
DEFAULT_STATE_DIR = Path(os.path.expanduser("~/.career-scout"))
WINDOW_STATE_FILENAME = "desktop_window.json"
LOG_FILENAME = "desktop.log"
MUTEX_NAME = "CareerScout-SingleInstance"
ERROR_ALREADY_EXISTS = 183
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MIN_WIDTH = 1024
MIN_HEIGHT = 700
MAX_WIDTH = 8192
MAX_HEIGHT = 8192
BACKEND_READY_TIMEOUT = 30.0
BACKEND_READY_POLL_INTERVAL = 0.2
_WINDOW_STATE_SCHEMA_VERSION = 1


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
    """Windows named mutex 工厂（ctypes）。返回 ``(handle, last_error)``。

    非 Windows 或 ctypes 不可用 → ``(None, 0)``，由调用方放行。
    """
    if sys.platform != "win32":
        return (None, 0)
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, name)
        last_error = kernel32.GetLastError()
        return (handle, last_error)
    except (OSError, AttributeError):
        return (None, 0)


def acquire_single_instance_mutex(mutex_factory=None):
    """获取单实例 mutex。

    返回 ``(handle, already_exists)``：
    - handle 非 None 且 already_exists=False：当前进程获得锁；
    - handle 非 None 且 already_exists=True：已有实例；
    - handle 为 None：非 Windows 或 ctypes 不可用，放行。
    """
    factory = mutex_factory or _default_mutex_factory
    handle, last_error = factory(MUTEX_NAME)
    if handle is None:
        return (None, False)
    return (handle, last_error == ERROR_ALREADY_EXISTS)


def release_single_instance_mutex(handle):
    """释放单实例 mutex（进程退出时）。"""
    if handle is None or sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        # 清理函数：任何异常（含 ctypes.ArgumentError 替身 handle）
        # 都不应向外抛，进程即将退出
        pass


# ---------------------------------------------------------------------------
# 窗口状态文件（T037 / 合同 §5）
# ---------------------------------------------------------------------------
def _window_state_path(state_dir):
    base = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    return base / WINDOW_STATE_FILENAME


def load_window_state(state_dir=None, workarea_provider=None):
    """读取并校验窗口状态。

    返回 ``(width, height, x, y)``：
    - 文件缺失/JSON 非法/字段非法/尺寸越界 → 默认 ``(1280, 800, None, None)``；
    - 位置越出显示器工作区 → 回退居中（用第一个工作区）；
    - workarea_provider 为 None → 不做越界检查，直接返回原始 x/y。
    """
    default = (DEFAULT_WIDTH, DEFAULT_HEIGHT, None, None)
    path = _window_state_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

    if not isinstance(data, dict) or data.get("schema") != _WINDOW_STATE_SCHEMA_VERSION:
        return default

    try:
        width = int(data["width"])
        height = int(data["height"])
        x = int(data["x"])
        y = int(data["y"])
    except (KeyError, ValueError, TypeError):
        return default

    if not (MIN_WIDTH <= width <= MAX_WIDTH) or not (MIN_HEIGHT <= height <= MAX_HEIGHT):
        return default

    if workarea_provider is None:
        return (width, height, x, y)

    try:
        workareas = workarea_provider()
    except Exception:
        return (width, height, x, y)

    for (ax, ay, aw, ah) in workareas:
        if ax <= x < ax + aw and ay <= y < ay + ah:
            return (width, height, x, y)

    if workareas:
        ax, ay, aw, ah = workareas[0]
        return (width, height, ax + (aw - width) // 2, ay + (ah - height) // 2)
    return (width, height, x, y)


def save_window_state(width, height, x, y, state_dir=None):
    """保存窗口状态到 ``{state_dir}/desktop_window.json``。"""
    path = _window_state_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": _WINDOW_STATE_SCHEMA_VERSION,
                    "width": int(width),
                    "height": int(height),
                    "x": int(x),
                    "y": int(y),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


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
    if sys.platform != "win32":
        print(f"[{title}] {text}")
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x00000000)
    except (OSError, AttributeError):
        print(f"[{title}] {text}")


def _default_workarea_provider():
    """返回主屏工作区 ``[(x, y, w, h)]``；非 Windows 或失败返回 ``[]``。"""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return [(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)]
    except Exception:
        return []


def _run_flask_server(app, port):
    """在独立线程跑 Flask；异常不向外抛（由就绪轮询超时捕获）。"""
    try:
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
    except Exception:
        pass


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

    # 5. 窗口状态
    width, height, x, y = load_window_state(
        state_dir=state_dir, workarea_provider=workarea_provider
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

    window_kwargs = {
        "url": f"http://127.0.0.1:{port}",
        "title": title,
        "width": width,
        "height": height,
        "resizable": True,
        "min_size": (MIN_WIDTH, MIN_HEIGHT),
        "background_color": "#0d1113",
        "shadow": False,
    }
    if x is not None and y is not None:
        window_kwargs["x"] = x
        window_kwargs["y"] = y

    def _on_closing():
        try:
            save_window_state(
                getattr(window, "width", width),
                getattr(window, "height", height),
                getattr(window, "x", x or 0),
                getattr(window, "y", y or 0),
                state_dir=state_dir,
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
        webview_module.start()
    except Exception as exc:
        # 合同 §7：webview.start() 抛初始化异常 → 提示安装 WebView2 + 微软下载指引
        log_error(
            f"窗口初始化失败: {exc}", state_dir=state_dir, logger=logger
        )
        messagebox(
            "Career Scout",
            "启动失败：WebView2 运行时缺失或初始化异常\n"
            "请安装 Microsoft Edge WebView2 Runtime\n"
            "下载地址：https://developer.microsoft.com/microsoft-edge/webview2/\n"
            f"详情：{exc}",
        )
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
