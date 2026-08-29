"""桌面壳测试（spec003 tasks005）。

覆盖冻结合同 contracts/desktop-shell.md：
- T036 单实例：mutex 已存在提示并退出 0；锁在 Flask 启动前获取；mutex 工厂可注入
- T037 窗口状态文件：用例 029 起迁移至 tests/test_desktop_window_state.py
  （schema 3 契约见 specs/029-desktop-window-browsers/contracts/）
- T038 错误路径：端口选择失败、后端就绪超时、WebView2 初始化异常
  → MessageBox + 非零退出 + desktop.log 记录
- T039 主编排：依赖全部可注入；正常路径返回 0；closing 触发保存与取消
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# 确保项目根在 sys.path 前面，避免 site-packages 的 packaging 包遮蔽
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from packaging import desktop


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _FakeEvent:
    """模拟 pywebview window.events.closing 事件（支持 += 注册 handler）。"""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeWindow:
    """模拟 pywebview Window 对象。"""

    def __init__(self, **kwargs):
        self.width = kwargs.get("width", desktop.DEFAULT_WIDTH)
        self.height = kwargs.get("height", desktop.DEFAULT_HEIGHT)
        self.x = kwargs.get("x", 100)
        self.y = kwargs.get("y", 100)
        closing = _FakeEvent()
        self.events = type("Events", (), {"closing": closing})()


class _FakeWebview:
    """模拟 pywebview 模块。"""

    def __init__(self, start_raises=None, fire_closing=False):
        self.start_raises = start_raises
        self.fire_closing = fire_closing
        self.windows = []
        self.create_window_calls = []
        self.start_called = False

    def create_window(self, **kwargs):
        self.create_window_calls.append(kwargs)
        win = _FakeWindow(**kwargs)
        self.windows.append(win)
        return win

    def start(self, **kwargs):
        self.start_called = True
        if self.fire_closing and self.windows:
            for handler in list(self.windows[0].events.closing.handlers):
                handler()
        if self.start_raises:
            raise self.start_raises


class _FakeApp:
    """模拟 Flask app，带 config 和 run no-op。"""

    def __init__(self, runners=None):
        self.config = {}
        for key, runner in (runners or {}).items():
            self.config[key] = runner

    def run(self, **kwargs):
        pass


class _FakeRunner:
    """模拟 TaskRunner，带 _cancel_events。"""

    def __init__(self, events=None):
        self._cancel_events = events or {}


class _FakeCancelEvent:
    """模拟 threading.Event，记录 set() 调用。"""

    def __init__(self):
        self.set_called = False

    def set(self):
        self.set_called = True


class _RecordingMessageBox:
    """记录 MessageBox 调用。"""

    def __init__(self):
        self.calls = []

    def __call__(self, title, text):
        self.calls.append((title, text))


class _RecordingLogger:
    """记录 logger 调用。"""

    def __init__(self):
        self.calls = []

    def __call__(self, message):
        self.calls.append(message)


def _make_deps(**overrides):
    """构造 run_desktop_shell 的 deps，默认全部注入安全替身（正常路径）。"""
    deps = {
        "mutex_factory": lambda name: (object(), 0),  # 获得锁，无已存在
        "messagebox": _RecordingMessageBox(),
        "create_app": lambda config: _FakeApp(),
        "pick_free_port": lambda: 50000,
        "http_get": lambda url: (200, b"ok"),
        "logger": _RecordingLogger(),
        "state_dir": None,
        "workarea_provider": lambda: [(0, 0, 1920, 1080)],
        "version": "9.9.9",
        "webview_module": _FakeWebview(),
        "ready_timeout": 0.5,
    }
    deps.update(overrides)
    return deps


# ===========================================================================
# T036 单实例
# ===========================================================================
class SingleInstanceTests(unittest.TestCase):
    """单实例 mutex 行为（合同 §2）。"""

    def test_mutex_already_exists_returns_zero(self):
        """mutex 已存在 → MessageBox 提示 + 退出 0 + 不启动 Flask。"""
        create_app_calls = []

        def create_app(config):
            create_app_calls.append(config)
            return _FakeApp()

        messagebox = _RecordingMessageBox()
        deps = _make_deps(
            mutex_factory=lambda name: (object(), desktop.ERROR_ALREADY_EXISTS),
            messagebox=messagebox,
            create_app=create_app,
        )
        code = desktop.run_desktop_shell(deps)
        self.assertEqual(code, 0)
        self.assertEqual(len(messagebox.calls), 1)
        title, text = messagebox.calls[0]
        self.assertIn("Career Scout", title)
        self.assertIn("已在运行", text)
        self.assertEqual(create_app_calls, [])

    def test_mutex_acquired_before_flask(self):
        """锁必须在 Flask 启动前获取（合同 §2）。"""
        calls = []

        def mutex_factory(name):
            calls.append("mutex")
            return (object(), 0)

        def create_app(config):
            calls.append("create_app")
            return _FakeApp()

        deps = _make_deps(mutex_factory=mutex_factory, create_app=create_app)
        desktop.run_desktop_shell(deps)
        self.assertIn("mutex", calls)
        self.assertIn("create_app", calls)
        self.assertLess(calls.index("mutex"), calls.index("create_app"))

    def test_mutex_factory_is_injectable(self):
        """mutex 工厂可注入替身（合同 §2）。"""
        received_names = []

        def mutex_factory(name):
            received_names.append(name)
            return (object(), 0)

        deps = _make_deps(mutex_factory=mutex_factory)
        desktop.run_desktop_shell(deps)
        self.assertEqual(len(received_names), 1)
        self.assertEqual(received_names[0], desktop.MUTEX_NAME)

    def test_mutex_already_exists_does_not_write_data(self):
        """已有实例时不启动第二个 Flask、不选端口、不写入数据（合同 §2）。"""
        create_app_calls = []
        pick_port_calls = []

        def create_app(config):
            create_app_calls.append(config)
            return _FakeApp()

        def pick_port():
            pick_port_calls.append(1)
            return 50000

        deps = _make_deps(
            mutex_factory=lambda name: (object(), desktop.ERROR_ALREADY_EXISTS),
            create_app=create_app,
            pick_free_port=pick_port,
        )
        desktop.run_desktop_shell(deps)
        self.assertEqual(create_app_calls, [])
        self.assertEqual(pick_port_calls, [])


# ===========================================================================
# T038 错误路径
# ===========================================================================
class ErrorPathTests(unittest.TestCase):
    """错误路径：端口/就绪/WebView2 异常 → MessageBox + 非零退出 + log（合同 §7）。"""

    def test_port_selection_failure_returns_nonzero(self):
        """端口选择失败 → MessageBox + 非零退出 + log 记录。"""
        messagebox = _RecordingMessageBox()
        logger = _RecordingLogger()

        def pick_port():
            raise OSError("no free port")

        deps = _make_deps(
            pick_free_port=pick_port,
            messagebox=messagebox,
            logger=logger,
        )
        code = desktop.run_desktop_shell(deps)
        self.assertNotEqual(code, 0)
        self.assertEqual(len(messagebox.calls), 1)
        self.assertGreater(len(logger.calls), 0)
        self.assertTrue(any("端口" in msg for msg in logger.calls))

    def test_backend_ready_timeout_returns_nonzero(self):
        """后端就绪超时 → MessageBox + 非零退出 + log。"""
        messagebox = _RecordingMessageBox()
        logger = _RecordingLogger()

        deps = _make_deps(
            http_get=lambda url: None,  # 永远连接失败
            messagebox=messagebox,
            logger=logger,
            ready_timeout=0.3,
        )
        code = desktop.run_desktop_shell(deps)
        self.assertNotEqual(code, 0)
        self.assertEqual(len(messagebox.calls), 1)
        self.assertTrue(
            any("就绪" in msg or "超时" in msg for msg in logger.calls)
        )

    def test_webview_init_failure_returns_nonzero(self):
        """WebView2 初始化异常 → MessageBox + 非零退出 + log。"""
        messagebox = _RecordingMessageBox()
        logger = _RecordingLogger()
        webview_mod = _FakeWebview(start_raises=RuntimeError("WebView2 missing"))

        deps = _make_deps(
            webview_module=webview_mod,
            messagebox=messagebox,
            logger=logger,
        )
        code = desktop.run_desktop_shell(deps)
        self.assertNotEqual(code, 0)
        self.assertEqual(len(messagebox.calls), 1)
        self.assertTrue(
            any(
                "窗口" in msg or "WebView" in msg or "webview" in msg.lower()
                for msg in logger.calls
            )
        )

    def test_webview_init_failure_messagebox_includes_download_url(self):
        """合同 §7：WebView2 缺失提示必须给出微软下载指引。"""
        messagebox = _RecordingMessageBox()
        webview_mod = _FakeWebview(start_raises=RuntimeError("init failed"))
        deps = _make_deps(
            webview_module=webview_mod,
            messagebox=messagebox,
        )
        desktop.run_desktop_shell(deps)
        self.assertEqual(len(messagebox.calls), 1)
        _, text = messagebox.calls[0]
        self.assertIn("WebView2", text)
        self.assertIn("https://developer.microsoft.com/microsoft-edge/webview2/", text)

    def test_create_app_failure_returns_nonzero(self):
        """Flask 创建失败 → MessageBox + 非零退出 + log。"""
        messagebox = _RecordingMessageBox()
        logger = _RecordingLogger()

        def create_app(config):
            raise RuntimeError("Flask init error")

        deps = _make_deps(
            create_app=create_app,
            messagebox=messagebox,
            logger=logger,
        )
        code = desktop.run_desktop_shell(deps)
        self.assertNotEqual(code, 0)
        self.assertEqual(len(messagebox.calls), 1)
        self.assertTrue(
            any("后端" in msg or "Flask" in msg for msg in logger.calls)
        )

    def test_messagebox_is_injectable(self):
        """MessageBox 可注入替身断言（合同 §7）。"""
        messagebox = _RecordingMessageBox()
        deps = _make_deps(
            mutex_factory=lambda name: (object(), desktop.ERROR_ALREADY_EXISTS),
            messagebox=messagebox,
        )
        desktop.run_desktop_shell(deps)
        self.assertEqual(len(messagebox.calls), 1)

    def test_logger_is_injectable(self):
        """logger 可注入替身断言。"""
        logger = _RecordingLogger()
        deps = _make_deps(
            pick_free_port=lambda: (_ for _ in ()).throw(OSError("fail")),
            logger=logger,
        )
        desktop.run_desktop_shell(deps)
        self.assertGreater(len(logger.calls), 0)


# ===========================================================================
# 辅助：wait_for_backend_ready
# ===========================================================================
class WaitForBackendReadyTests(unittest.TestCase):
    """就绪轮询纯逻辑。"""

    def test_returns_true_on_200(self):
        result = desktop.wait_for_backend_ready(
            50000, timeout=1.0, http_get=lambda url: (200, b"ok")
        )
        self.assertTrue(result)

    def test_returns_false_on_timeout(self):
        result = desktop.wait_for_backend_ready(
            50000, timeout=0.2, http_get=lambda url: None, poll_interval=0.05
        )
        self.assertFalse(result)

    def test_returns_true_after_retries(self):
        """前几次失败，后续 200 → True。"""
        calls = []

        def http_get(url):
            calls.append(1)
            return None if len(calls) < 3 else (200, b"ok")

        result = desktop.wait_for_backend_ready(
            50000, timeout=2.0, http_get=http_get, poll_interval=0.02
        )
        self.assertTrue(result)
        self.assertGreaterEqual(len(calls), 3)


# ===========================================================================
# 辅助：cancel_running_tasks
# ===========================================================================
class CancelRunningTasksTests(unittest.TestCase):
    """抓取取消纯逻辑（合同 §6）。"""

    def test_sets_all_cancel_events(self):
        """遍历 TASK_RUNNER 和 WORKBENCH_RUNNER 的 _cancel_events，全部 set。"""
        event1 = _FakeCancelEvent()
        event2 = _FakeCancelEvent()
        runner1 = _FakeRunner(events={"t1": event1})
        runner2 = _FakeRunner(events={"r2": event2})
        app = _FakeApp(
            runners={"TASK_RUNNER": runner1, "WORKBENCH_RUNNER": runner2}
        )
        desktop.cancel_running_tasks(app)
        self.assertTrue(event1.set_called)
        self.assertTrue(event2.set_called)

    def test_handles_missing_runners(self):
        """app.config 无 runner → 不抛异常。"""
        desktop.cancel_running_tasks(_FakeApp())

    def test_handles_none_app(self):
        """app 为 None → 不抛异常。"""
        desktop.cancel_running_tasks(None)

    def test_handles_runner_without_cancel_events(self):
        """runner 无 _cancel_events 属性 → 不抛异常。"""

        class BareRunner:
            pass

        app = _FakeApp(runners={"TASK_RUNNER": BareRunner()})
        desktop.cancel_running_tasks(app)


# ===========================================================================
# T039 主编排：正常路径 + 接线点
# ===========================================================================
class ShellOrchestrationTests(unittest.TestCase):
    """主编排正常路径与窗口参数（合同 §1/§5/§6）。"""

    def test_normal_path_returns_zero(self):
        """正常路径返回 0。"""
        deps = _make_deps()
        code = desktop.run_desktop_shell(deps)
        self.assertEqual(code, 0)

    def test_window_title_contains_version(self):
        """窗口标题为 Career Scout v{version}（合同 §5）。"""
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, version="2.4.0")
        desktop.run_desktop_shell(deps)
        self.assertTrue(webview_mod.create_window_calls)
        self.assertEqual(
            webview_mod.create_window_calls[0]["title"], "Career Scout v2.4.0"
        )

    def test_window_min_size_is_1024_700(self):
        """min_size = (1024, 700)（合同 §5）。"""
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod)
        desktop.run_desktop_shell(deps)
        self.assertEqual(
            webview_mod.create_window_calls[0]["min_size"], (1024, 700)
        )

    def test_window_resizable_true(self):
        """resizable = True（合同 §5）。"""
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod)
        desktop.run_desktop_shell(deps)
        self.assertTrue(webview_mod.create_window_calls[0]["resizable"])

    def test_window_url_uses_random_port(self):
        """窗口 URL 使用随机端口（合同 §3）。"""
        webview_mod = _FakeWebview()
        deps = _make_deps(
            webview_module=webview_mod,
            pick_free_port=lambda: 54321,
        )
        desktop.run_desktop_shell(deps)
        self.assertEqual(
            webview_mod.create_window_calls[0]["url"], "http://127.0.0.1:54321"
        )

    def test_flask_config_exe_mode(self):
        """Flask config 传入 RUNTIME_MODE=exe, PYTHON_EXECUTABLE, START_TASKS（合同 §4）。"""
        received = []

        def create_app(config):
            received.append(config)
            return _FakeApp()

        deps = _make_deps(create_app=create_app)
        desktop.run_desktop_shell(deps)
        self.assertEqual(len(received), 1)
        cfg = received[0]
        self.assertEqual(cfg["RUNTIME_MODE"], "exe")
        self.assertTrue(cfg["START_TASKS"])
        self.assertIn("PYTHON_EXECUTABLE", cfg)

    def test_closing_cancels_running_tasks(self):
        """closing 事件取消抓取任务（合同 §6）。"""
        event = _FakeCancelEvent()
        runner = _FakeRunner(events={"t1": event})
        app = _FakeApp(runners={"TASK_RUNNER": runner})
        webview_mod = _FakeWebview(fire_closing=True)
        deps = _make_deps(
            webview_module=webview_mod,
            create_app=lambda config: app,
        )
        desktop.run_desktop_shell(deps)
        self.assertTrue(event.set_called)


# ===========================================================================
# 辅助：read_version
# ===========================================================================
class ReadVersionTests(unittest.TestCase):
    """版本读取（合同 §5）。"""

    def test_reads_version_from_pyproject(self):
        version = desktop.read_version()
        self.assertEqual(version, "1.8.3")

    def test_read_version_returns_string(self):
        self.assertIsInstance(desktop.read_version(), str)


if __name__ == "__main__":
    unittest.main()
