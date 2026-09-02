"""桌面壳窗口状态编排接线测试（029 自 test_desktop_window_state.py 拆分）。

覆盖 run_desktop_shell 层的窗口状态行为：事件驱动 Tracker、closing 落盘
（最大化 → 最后普通矩形）、启动 maximized 参数。纯逻辑单测见
tests/test_desktop_window_state.py。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from packaging import desktop
from packaging import window_state as ws


# ---------------------------------------------------------------------------
# 测试替身（编排层用；扩展版：全部窗口事件可 fire）
# ---------------------------------------------------------------------------
class _FakeEvent:
    """模拟 pywebview window.events.* 事件（支持 += 注册与 fire）。"""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in list(self.handlers):
            handler(*args)


_EVENT_NAMES = ("closing", "resized", "moved", "maximized", "restored")


class _FakeWindow:
    """模拟 pywebview Window 对象（属性可变，事件可 fire）。"""

    def __init__(self, **kwargs):
        self.width = kwargs.get("width", desktop.DEFAULT_WIDTH)
        self.height = kwargs.get("height", desktop.DEFAULT_HEIGHT)
        self.x = kwargs.get("x", 100)
        self.y = kwargs.get("y", 100)
        self.events = type(
            "Events", (), {name: _FakeEvent() for name in _EVENT_NAMES}
        )()


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
    """模拟 Flask app。"""

    def __init__(self, runners=None):
        self.config = {}
        for key, runner in (runners or {}).items():
            self.config[key] = runner

    def run(self, **kwargs):
        pass


class _RecordingMessageBox:
    def __init__(self):
        self.calls = []

    def __call__(self, title, text):
        self.calls.append((title, text))


class _RecordingLogger:
    def __init__(self):
        self.calls = []

    def __call__(self, message):
        self.calls.append(message)


def _make_deps(**overrides):
    """构造 run_desktop_shell 的 deps，默认全部注入安全替身（正常路径）。"""
    deps = {
        "mutex_factory": lambda name: (object(), 0),
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


def _write_state(state_dir, data):
    path = Path(state_dir) / ws.WINDOW_STATE_FILENAME
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _read_state(state_dir):
    return json.loads(
        (Path(state_dir) / ws.WINDOW_STATE_FILENAME).read_text(encoding="utf-8")
    )


# ===========================================================================
# 编排层：桌面壳事件接线与启动 maximized（US1/US2）
# ===========================================================================
class ClosingSaveOrchestrationTests(unittest.TestCase):
    """closing 落盘接线（US1 场景 1/3 + 迁移用例）。"""

    def test_closing_saves_window_state(self):
        """普通态 closing 保存当前窗口矩形（迁移自 test_desktop_shell）。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview(fire_closing=True)
        original_create = webview_mod.create_window

        def create_window(**kwargs):
            win = original_create(**kwargs)
            win.width = 1400
            win.height = 900
            win.x = 200
            win.y = 150
            return win

        webview_mod.create_window = create_window
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        result = desktop.load_window_state(state_dir=state_dir)
        self.assertEqual(result, (1400, 900, 200, 150, False))

    def test_closing_saves_state_when_window_xy_none(self):
        """首启（window.x/y 为 None）closing 也能保存：缺项回退默认普通矩形位置。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview(fire_closing=True)
        original_create = webview_mod.create_window

        def create_window(**kwargs):
            win = original_create(**kwargs)
            win.x = None
            win.y = None
            win.width = 1366
            win.height = 768
            return win

        webview_mod.create_window = create_window
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        result = desktop.load_window_state(state_dir=state_dir)
        # 位置缺项 → default_normal_rect 居中 (187, 140)
        self.assertEqual(result, (1366, 768, 187, 140, False))

    def test_maximized_close_saves_last_normal_not_fullscreen(self):
        """US1 核心场景：拖好 → 最大化 → 关窗，落盘为普通矩形而非全屏矩形。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        win = webview_mod.windows[0]

        # 用户拖成 1200×800 @ (50,60)
        win.width, win.height, win.x, win.y = 1200, 800, 50, 60
        win.events.resized.fire()
        win.events.moved.fire()
        # 最大化（全屏矩形 1936×1056 @ -8,-8）
        win.width, win.height, win.x, win.y = 1936, 1056, -8, -8
        win.events.maximized.fire()
        # closing
        for handler in list(win.events.closing.handlers):
            handler()

        data = _read_state(state_dir)
        self.assertEqual(data["width"], 1200)
        self.assertEqual(data["height"], 800)
        self.assertEqual((data["x"], data["y"]), (50, 60))
        self.assertTrue(data["maximized"])

    def test_maximized_never_dragged_saves_default_normal(self):
        """US1 场景 7：最大化且未拖过 → 落盘默认普通矩形 + maximized=True。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        win = webview_mod.windows[0]
        win.width, win.height, win.x, win.y = 1936, 1056, -8, -8
        win.events.maximized.fire()
        for handler in list(win.events.closing.handlers):
            handler()
        result = desktop.load_window_state(state_dir=state_dir)
        self.assertEqual(result, (1545, 800, 187, 140, True))

    def test_restore_back_to_normal_saves_normal_rect(self):
        """US1 场景 2：最大化 → 还原 → 关窗，落盘普通矩形 maximized=False。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        win = webview_mod.windows[0]
        win.width, win.height, win.x, win.y = 1200, 800, 50, 60
        win.events.resized.fire()
        win.events.moved.fire()
        win.events.maximized.fire()
        # 还原回普通矩形
        win.width, win.height, win.x, win.y = 1200, 800, 50, 60
        win.events.restored.fire()
        for handler in list(win.events.closing.handlers):
            handler()
        result = desktop.load_window_state(state_dir=state_dir)
        self.assertEqual(result, (1200, 800, 50, 60, False))

    def test_quit_handler_injected_reuses_closing_path(self):
        """应用内更新重启路径（quit_app）注入 quit_handler，复用 closing 落盘。"""

        class _CapturingApi:
            quit_handler = None

        js_api = _CapturingApi()
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        deps = _make_deps(
            webview_module=webview_mod, state_dir=state_dir, js_api=js_api
        )
        desktop.run_desktop_shell(deps)
        self.assertTrue(callable(js_api.quit_handler))
        # quit_handler 内部即 closing 同一落盘逻辑：行为由 closing 用例覆盖
        win = webview_mod.windows[0]
        win.width, win.height, win.x, win.y = 1300, 850, 20, 30
        win.events.resized.fire()
        win.events.moved.fire()
        for handler in list(win.events.closing.handlers):
            handler()
        result = desktop.load_window_state(state_dir=state_dir)
        self.assertEqual(result, (1300, 850, 20, 30, False))


class StartupMaximizedOrchestrationTests(unittest.TestCase):
    """启动 maximized 接线（US2 场景 1/4/5 + US1 场景 1 重启段）。"""

    def test_first_open_no_memory_maximized_default_size(self):
        """无记忆 → 1545×800 + maximized=True，位置交给窗口管理器居中。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        kwargs = webview_mod.create_window_calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (1545, 800))
        self.assertTrue(kwargs["maximized"])
        self.assertNotIn("x", kwargs)
        self.assertNotIn("y", kwargs)

    def test_startup_maximized_from_memory(self):
        """记忆 maximized=True → 按普通矩形开窗并最大化（可还原回该矩形）。"""
        state_dir = tempfile.mkdtemp()
        ws.save_window_state(
            1200, 800, 50, 60, state_dir=state_dir, maximized=True
        )
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        kwargs = webview_mod.create_window_calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (1200, 800))
        self.assertEqual((kwargs["x"], kwargs["y"]), (50, 60))
        self.assertTrue(kwargs["maximized"])

    def test_startup_normal_memory_not_maximized(self):
        """普通记忆 → maximized=False 显式传入。"""
        state_dir = tempfile.mkdtemp()
        ws.save_window_state(1400, 900, 200, 150, state_dir=state_dir)
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        kwargs = webview_mod.create_window_calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (1400, 900))
        self.assertEqual((kwargs["x"], kwargs["y"]), (200, 150))
        self.assertFalse(kwargs["maximized"])

    def test_startup_schema2_polluted_treated_as_first_open(self):
        """schema 2 污染记忆 → 按首开处理（1545×800 最大化）。"""
        state_dir = tempfile.mkdtemp()
        _write_state(
            state_dir,
            {"schema": 2, "width": 1936, "height": 1056, "x": -8, "y": -8},
        )
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        kwargs = webview_mod.create_window_calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (1545, 800))
        self.assertTrue(kwargs["maximized"])

    def test_first_open_small_screen_default_clamped(self):
        """小屏首开 → 默认普通矩形钳到工作区（仍最大化）。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        deps = _make_deps(
            webview_module=webview_mod,
            state_dir=state_dir,
            workarea_provider=lambda: [(0, 0, 1366, 728)],
        )
        desktop.run_desktop_shell(deps)
        kwargs = webview_mod.create_window_calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (1366, 728))
        self.assertTrue(kwargs["maximized"])

    def test_full_cycle_drag_maximize_close_restart_restore(self):
        """US1 端到端：拖好 → 最大化 → 关 → 重启（最大化）→ 还原段参数正确。"""
        state_dir = tempfile.mkdtemp()
        # 第一段生命周期：拖好 + 最大化 + 关窗
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        win = webview_mod.windows[0]
        win.width, win.height, win.x, win.y = 1200, 800, 50, 60
        win.events.resized.fire()
        win.events.moved.fire()
        win.events.maximized.fire()
        for handler in list(win.events.closing.handlers):
            handler()
        # 第二段生命周期：重启 → 应最大化 + 普通参数为拖好的矩形
        webview_mod2 = _FakeWebview()
        deps2 = _make_deps(webview_module=webview_mod2, state_dir=state_dir)
        desktop.run_desktop_shell(deps2)
        kwargs = webview_mod2.create_window_calls[0]
        self.assertEqual((kwargs["width"], kwargs["height"]), (1200, 800))
        self.assertEqual((kwargs["x"], kwargs["y"]), (50, 60))
        self.assertTrue(kwargs["maximized"])

    def test_macos_fullscreen_animation_does_not_pollute_normal_rect(self):
        """审查修复回归：cocoa 全屏动画先发 resized（全屏尺寸）后发 maximized，
        守卫拒绝全屏尺寸，关窗落盘的是用户真实普通矩形而非全屏矩形。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, state_dir=state_dir)
        desktop.run_desktop_shell(deps)
        win = webview_mod.windows[0]
        # 用户拖好普通矩形
        win.width, win.height, win.x, win.y = 1200, 800, 50, 60
        win.events.resized.fire()
        win.events.moved.fire()
        # macOS 顺序：全屏动画期间 resized 先到（全屏尺寸），maximized 后到
        win.width, win.height, win.x, win.y = 1936, 1056, -8, -8
        win.events.resized.fire()  # 守卫拒绝（1936×1056 装不进 1920×1080 工作区）
        win.events.maximized.fire()
        for handler in list(win.events.closing.handlers):
            handler()
        result = desktop.load_window_state(state_dir=state_dir)
        self.assertEqual(result, (1200, 800, 50, 60, True))

    def test_events_partial_only_closing_still_saves(self):
        """仅 closing 可用（部分事件缺失）→ 其余事件降级跳过，closing 兜底仍保存。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview(fire_closing=True)
        original_create = webview_mod.create_window

        def create_window(**kwargs):
            win = original_create(**kwargs)
            # 模拟事件面不全：只有 closing
            win.events = type("Events", (), {"closing": _FakeEvent()})()
            win.width, win.height, win.x, win.y = 1300, 850, 20, 30
            return win

        webview_mod.create_window = create_window
        logger = _RecordingLogger()
        deps = _make_deps(
            webview_module=webview_mod, state_dir=state_dir, logger=logger
        )
        code = desktop.run_desktop_shell(deps)
        self.assertEqual(code, 0)
        result = desktop.load_window_state(state_dir=state_dir)
        self.assertEqual(result, (1300, 850, 20, 30, False))

    def test_events_api_missing_logs_warning(self):
        """events API 整体不可用（<6.0）→ 记日志明示，不阻断启动。"""
        state_dir = tempfile.mkdtemp()
        webview_mod = _FakeWebview()
        original_create = webview_mod.create_window

        def create_window(**kwargs):
            win = original_create(**kwargs)
            win.events = None  # 模拟 pywebview<6 无事件 API
            return win

        webview_mod.create_window = create_window
        logger = _RecordingLogger()
        deps = _make_deps(
            webview_module=webview_mod, state_dir=state_dir, logger=logger
        )
        code = desktop.run_desktop_shell(deps)
        self.assertEqual(code, 0)
        self.assertTrue(any("事件" in msg for msg in logger.calls))


# ===========================================================================
# 036 窗口控制 js_api 接线（B084 自绘标题栏）
# ===========================================================================
class WindowControlJsApiTests(unittest.TestCase):
    """自绘标题栏三按钮的 js_api 接线（契约 §2/§3）。"""

    def _run_with_api(self, js_api=None):
        js_api = js_api or desktop.DesktopJsApi()
        webview_mod = _FakeWebview()
        deps = _make_deps(webview_module=webview_mod, js_api=js_api)
        desktop.run_desktop_shell(deps)
        return js_api, webview_mod.windows[0]

    def test_window_reference_injected(self):
        """窗口创建后 js_api._window 注入真实 window 引用（下划线属性，
        防 pywebview API 枚举爬取窗口对象图导致加载卡死）。"""
        js_api, win = self._run_with_api()
        self.assertIs(js_api._window, win)

    def test_window_minimize_delegates_to_window(self):
        """window_minimize → window.minimize()。"""
        js_api, win = self._run_with_api()
        win.calls = []
        win.minimize = lambda: win.calls.append("minimize")
        result = js_api.window_minimize()
        self.assertEqual(result, {"ok": True, "error": None})
        self.assertEqual(win.calls, ["minimize"])

    def test_window_toggle_maximize_from_normal(self):
        """普通态 window_toggle_maximize → maximize()，返回 maximized=True。"""
        js_api, win = self._run_with_api()
        win.calls = []
        win.maximized = False
        win.maximize = lambda: win.calls.append("maximize")
        win.restore = lambda: win.calls.append("restore")
        result = js_api.window_toggle_maximize()
        self.assertEqual(
            result, {"ok": True, "error": None, "maximized": True}
        )
        self.assertEqual(win.calls, ["maximize"])

    def test_window_toggle_maximize_from_maximized(self):
        """最大化态 window_toggle_maximize → restore()，返回 maximized=False。"""
        js_api, win = self._run_with_api()
        win.calls = []
        win.maximized = True
        win.maximize = lambda: win.calls.append("maximize")
        win.restore = lambda: win.calls.append("restore")
        result = js_api.window_toggle_maximize()
        self.assertEqual(
            result, {"ok": True, "error": None, "maximized": False}
        )
        self.assertEqual(win.calls, ["restore"])

    def test_window_is_maximized_reads_window_state(self):
        """window_is_maximized 返回窗口当前最大化状态（FR-004 图标切换）。"""
        js_api, win = self._run_with_api()
        win.maximized = True
        self.assertEqual(js_api.window_is_maximized(), {"ok": True, "maximized": True})
        win.maximized = False
        self.assertEqual(js_api.window_is_maximized(), {"ok": True, "maximized": False})

    def test_window_control_without_window_returns_no_window(self):
        """未注入 window → {ok: False, error: no_window}。"""
        js_api = desktop.DesktopJsApi()
        js_api._window = None
        self.assertEqual(
            js_api.window_minimize(), {"ok": False, "error": "no_window"}
        )
        self.assertEqual(
            js_api.window_toggle_maximize(), {"ok": False, "error": "no_window"}
        )
        self.assertEqual(
            js_api.window_is_maximized(), {"ok": False, "error": "no_window"}
        )

    def test_window_close_reuses_quit_handler(self):
        """window_close → 复用 quit_handler 优雅退出链路（等价关闭按钮）。"""
        called = []
        js_api = desktop.DesktopJsApi()
        js_api._window = object()
        js_api.quit_handler = lambda: called.append("quit")
        result = js_api.window_close()
        self.assertEqual(result, {"ok": True})
        self.assertEqual(called, ["quit"])

    def test_window_close_without_handler_returns_error(self):
        js_api = desktop.DesktopJsApi()
        js_api._window = object()
        js_api.quit_handler = None
        self.assertEqual(
            js_api.window_close(), {"ok": False, "error": "no_quit_handler"}
        )


if __name__ == "__main__":
    unittest.main()
