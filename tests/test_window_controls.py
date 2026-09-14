"""窗口控制原语聚焦测试（spec 036 B084 自绘标题栏）。

覆盖 contracts/desktop-window-controls.md §2/§3：
- minimize / maximize / restore / toggle_maximize 各原语对注入 window 的正确调用
- 最大化切换：非最大化 -> maximize()；最大化 -> restore()
- 错误路径：window 方法抛异常 -> {ok: False, error}，不向上抛
- 无 maximized 属性的替身窗口按未最大化处理（老版本/测试替身兼容）
"""

import sys
import unittest
from pathlib import Path

# 确保项目根在 sys.path 前面，避免 site-packages 的 packaging 包遮蔽
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from packaging import window_controls, window_interaction, window_metrics


class _FakeWindow:
    """模拟 pywebview Window 对象，记录各方法调用。"""

    def __init__(self, maximized=False, maximized_attr=True):
        self.maximized = maximized if maximized_attr else None
        self.calls = []

    def minimize(self):
        self.calls.append("minimize")

    def maximize(self):
        self.calls.append("maximize")

    def restore(self):
        self.calls.append("restore")


class _BoomWindow:
    """方法一律抛异常的替身，验证错误路径。"""

    def minimize(self):
        raise RuntimeError("boom-min")

    def maximize(self):
        raise RuntimeError("boom-max")

    def restore(self):
        raise RuntimeError("boom-restore")


class MinimizeTests(unittest.TestCase):
    def test_minimize_calls_window_minimize(self):
        win = _FakeWindow()
        result = window_controls.minimize(win)
        self.assertEqual(win.calls, ["minimize"])
        self.assertEqual(result, {"ok": True, "error": None})

    def test_minimize_error_returns_ok_false(self):
        result = window_controls.minimize(_BoomWindow())
        self.assertFalse(result["ok"])
        self.assertIn("boom-min", result["error"])


class RestoreTests(unittest.TestCase):
    def test_restore_calls_window_restore(self):
        win = _FakeWindow(maximized=True)
        result = window_controls.restore(win)
        self.assertEqual(win.calls, ["restore"])
        self.assertEqual(result, {"ok": True, "error": None})

    def test_restore_error_returns_ok_false(self):
        result = window_controls.restore(_BoomWindow())
        self.assertFalse(result["ok"])
        self.assertIn("boom-restore", result["error"])


class MaximizeTests(unittest.TestCase):
    def test_maximize_calls_window_maximize(self):
        win = _FakeWindow()
        result = window_controls.maximize(win)
        self.assertEqual(win.calls, ["maximize"])
        self.assertEqual(result, {"ok": True, "error": None})

    def test_maximize_error_returns_ok_false(self):
        result = window_controls.maximize(_BoomWindow())
        self.assertFalse(result["ok"])
        self.assertIn("boom-max", result["error"])


class IsMaximizedTests(unittest.TestCase):
    def test_is_maximized_reads_live_flag(self):
        """is_maximized 优先读实时标记（事件维护）。"""
        win = _FakeWindow(maximized=False)
        self.assertFalse(window_controls.is_maximized(win))
        window_controls.note_maximized(win, True)
        self.assertTrue(window_controls.is_maximized(win))

    def test_is_maximized_falls_back_to_snapshot(self):
        """未接线时回退读构造快照。"""
        win = _FakeWindow(maximized=True)
        self.assertTrue(window_controls.is_maximized(win))

    def test_is_maximized_no_attribute_treats_as_normal(self):
        """无 maximized 属性按普通态处理。"""
        win = _FakeWindow(maximized=True, maximized_attr=False)
        self.assertFalse(window_controls.is_maximized(win))


class ToggleMaximizeTests(unittest.TestCase):
    def test_toggle_from_normal_maximizes(self):
        win = _FakeWindow(maximized=False)
        result = window_controls.toggle_maximize(win)
        self.assertEqual(win.calls, ["maximize"])
        self.assertEqual(
            result, {"ok": True, "error": None, "maximized": True}
        )

    def test_toggle_from_maximized_restores(self):
        """T022 修复：以实时标记（事件维护）为准，最大化态切换走还原。"""
        win = _FakeWindow(maximized=False)
        window_controls.note_maximized(win, True)
        result = window_controls.toggle_maximize(win)
        self.assertEqual(win.calls, ["restore"])
        self.assertEqual(
            result, {"ok": True, "error": None, "maximized": False}
        )

    def test_live_state_overrides_constructor_snapshot(self):
        """实时标记优先于构造快照：快照说最大化但事件说已还原 → 走最大化。"""
        win = _FakeWindow(maximized=True)
        window_controls.note_maximized(win, False)
        result = window_controls.toggle_maximize(win)
        self.assertEqual(win.calls, ["maximize"])
        self.assertEqual(
            result, {"ok": True, "error": None, "maximized": True}
        )

    def test_note_maximized_sets_live_flag(self):
        win = _FakeWindow(maximized=False)
        window_controls.note_maximized(win, True)
        self.assertTrue(win._cs_maximized)
        window_controls.note_maximized(win, False)
        self.assertFalse(win._cs_maximized)

    def test_toggle_no_live_state_falls_back_to_snapshot(self):
        """未接线（无实时标记）时回退读构造快照，兼容老替身。"""
        win = _FakeWindow(maximized=True)
        result = window_controls.toggle_maximize(win)
        self.assertEqual(win.calls, ["restore"])
        self.assertEqual(
            result, {"ok": True, "error": None, "maximized": False}
        )

    def test_toggle_no_maximized_attribute_treats_as_normal(self):
        """无 maximized 属性的替身（老版本/测试）按未最大化处理。"""
        win = _FakeWindow(maximized=True, maximized_attr=False)
        result = window_controls.toggle_maximize(win)
        self.assertEqual(win.calls, ["maximize"])
        self.assertEqual(
            result, {"ok": True, "error": None, "maximized": True}
        )

    def test_toggle_error_returns_ok_false(self):
        result = window_controls.toggle_maximize(_BoomWindow())
        self.assertFalse(result["ok"])
        self.assertNotIn("maximized", result)


class DragThresholdTests(unittest.TestCase):
    """拖动阈值：点一下不算拖动（最大化标题带不还原、顶部不误最大化）。"""

    def test_drag_started_uses_threshold(self):
        self.assertFalse(window_metrics.drag_started(0, 4))
        self.assertFalse(window_metrics.drag_started(3, 4))
        self.assertTrue(window_metrics.drag_started(4, 4))
        self.assertTrue(window_metrics.drag_started(120, 4))

    def test_threshold_constants_are_positive(self):
        self.assertGreater(window_interaction.DRAG_START_THRESHOLD_CSS, 0)
        self.assertGreater(window_interaction.SNAP_TOP_MIN_TRAVEL_CSS, 0)
        self.assertGreaterEqual(
            window_interaction.SNAP_TOP_MIN_TRAVEL_CSS,
            window_interaction.DRAG_START_THRESHOLD_CSS,
        )


# ===========================================================================
# 036 v2：DPI 换算、逐轴钳制、拉伸/移动矩形、拖下还原与顶部释放（纯逻辑）
# ===========================================================================
class ScaleTests(unittest.TestCase):
    def test_physical_px_rounds_by_scale(self):
        self.assertEqual(window_metrics.physical_px(36, 1.0), 36)
        self.assertEqual(window_metrics.physical_px(36, 1.25), 45)
        self.assertEqual(window_metrics.physical_px(36, 1.5), 54)
        self.assertEqual(window_metrics.physical_px(6, 1.25), 8)

    def test_physical_px_never_zero_for_positive_input(self):
        """小数值高缩放下不得把命中宽度算成 0（否则边缘不可命中）。"""
        self.assertEqual(window_metrics.physical_px(1, 0.5), 1)


class MinTrackSizeTests(unittest.TestCase):
    def test_regular_case_is_1024x700(self):
        self.assertEqual(
            window_metrics.min_track_size(1920, 1040, 1.0), (1024, 700)
        )

    def test_scaled_by_dpi(self):
        self.assertEqual(
            window_metrics.min_track_size(3840, 2160, 1.5), (1536, 1050)
        )

    def test_small_workarea_relaxes_per_axis(self):
        """工作区比常规下限更小时，逐轴放宽到工作区尺寸。"""
        self.assertEqual(
            window_metrics.min_track_size(900, 600, 1.0), (900, 600)
        )
        self.assertEqual(
            window_metrics.min_track_size(1920, 600, 1.0), (1024, 600)
        )


class ResizeRectTests(unittest.TestCase):
    """拉伸矩形：对应边跟随、对边锚定、逐轴不越过下限与工作区上限。"""

    WORK = (0, 0, 1920, 1040)
    MIN = (1024, 700)
    START = (200, 100, 1200, 900)

    def test_right_edge_moves_only_right(self):
        rect = window_metrics.resize_rect(
            self.START, "R", 100, 999, self.MIN, self.WORK
        )
        self.assertEqual(rect, (200, 100, 1300, 900))

    def test_bottom_edge_moves_only_bottom(self):
        rect = window_metrics.resize_rect(
            self.START, "B", 999, 50, self.MIN, self.WORK
        )
        # 下边缘 1000+50=1050 超出工作区底边 1040 → 钳到 1040（高度 940）
        self.assertEqual(rect, (200, 100, 1200, 940))

    def test_left_edge_moves_left_and_keeps_right(self):
        rect = window_metrics.resize_rect(
            self.START, "L", 100, 0, self.MIN, self.WORK
        )
        self.assertEqual(rect, (300, 100, 1100, 900))

    def test_top_left_corner_moves_both(self):
        rect = window_metrics.resize_rect(
            self.START, "TL", -50, -30, self.MIN, self.WORK
        )
        self.assertEqual(rect, (150, 70, 1250, 930))

    def test_min_size_stops_shrinking_and_keeps_anchor(self):
        rect = window_metrics.resize_rect(
            self.START, "R", -5000, 0, self.MIN, self.WORK
        )
        self.assertEqual(rect, (200, 100, 1024, 900))
        rect_left = window_metrics.resize_rect(
            self.START, "L", 5000, 0, self.MIN, self.WORK
        )
        # 右边缘锚定在 1400：宽 = 1024 → 左 = 376
        self.assertEqual(rect_left, (376, 100, 1024, 900))

    def test_max_size_is_workarea(self):
        rect = window_metrics.resize_rect(
            self.START, "R", 5000, 0, self.MIN, self.WORK
        )
        self.assertEqual(rect, (200, 100, 1720, 900))
        rect_bottom = window_metrics.resize_rect(
            self.START, "B", 0, 5000, self.MIN, self.WORK
        )
        self.assertEqual(rect_bottom, (200, 100, 1200, 940))

    def test_never_moves_edge_outside_workarea_origin(self):
        """向左上拉伸时不得把移动边拉出工作区原点之外。"""
        rect = window_metrics.resize_rect(
            self.START, "TL", -5000, -5000, self.MIN, self.WORK
        )
        left, top, width, height = rect
        self.assertEqual(left, 0)
        self.assertEqual(top, 0)
        self.assertEqual(left + width, 1400)
        self.assertEqual(top + height, 1000)

    def test_disabled_size_in_small_workarea(self):
        """工作区比常规下限小时按工作区限制，不越界。"""
        work = (0, 0, 900, 600)
        rect = window_metrics.resize_rect(
            (0, 0, 900, 600), "R", 500, 500, (900, 600), work
        )
        self.assertEqual(rect, (0, 0, 900, 600))


class MoveRectTests(unittest.TestCase):
    def test_translation(self):
        rect = window_metrics.move_rect((100, 100, 800, 600), 60, -40)
        self.assertEqual(rect, (160, 60, 800, 600))

    def test_size_unchanged(self):
        rect = window_metrics.move_rect((0, 0, 1024, 700), 0, 0)
        self.assertEqual(rect, (0, 0, 1024, 700))


class RestoreGrabTests(unittest.TestCase):
    """最大化拖下还原：横向抓取比例保持。"""

    def test_grab_ratio_clamped(self):
        self.assertAlmostEqual(window_metrics.grab_ratio(1500, 1000, 1000), 0.5)
        self.assertAlmostEqual(window_metrics.grab_ratio(900, 1000, 1000), 0.0)
        self.assertAlmostEqual(window_metrics.grab_ratio(2500, 1000, 1000), 1.0)
        self.assertAlmostEqual(window_metrics.grab_ratio(1000, 1000, 0), 0.0)

    def test_restore_rect_keeps_ratio(self):
        rect = window_metrics.restore_rect_for_grab(
            pointer_x=900, pointer_y=300, normal_size=(1400, 800),
            ratio=0.25, title_h_px=36,
        )
        self.assertEqual(rect, (550, 282, 1400, 800))

    def test_restore_rect_center_ratio(self):
        rect = window_metrics.restore_rect_for_grab(
            pointer_x=960, pointer_y=500, normal_size=(1400, 800),
            ratio=0.5, title_h_px=36,
        )
        self.assertEqual(rect, (260, 482, 1400, 800))


class TopReleaseTests(unittest.TestCase):
    def test_release_at_workarea_top_maximizes(self):
        self.assertTrue(
            window_metrics.should_maximize_on_release(3, 0, threshold_px=8)
        )

    def test_release_below_threshold_does_not_maximize(self):
        self.assertFalse(
            window_metrics.should_maximize_on_release(40, 0, threshold_px=8)
        )

    def test_second_monitor_uses_its_own_workarea_top(self):
        self.assertTrue(
            window_metrics.should_maximize_on_release(-1075, -1080, threshold_px=8)
        )
        self.assertFalse(
            window_metrics.should_maximize_on_release(0, -1080, threshold_px=8)
        )

    def test_click_without_drag_does_not_maximize(self):
        """在顶部点一下（指针没移动过）不得最大化：只有真拖动到顶部才算（FR-010）。"""
        self.assertFalse(
            window_metrics.should_maximize_on_release(
                3, 0, threshold_px=8, travel_px=2, min_travel_px=8
            )
        )
        self.assertTrue(
            window_metrics.should_maximize_on_release(
                3, 0, threshold_px=8, travel_px=20, min_travel_px=8
            )
        )


class CanResizeTests(unittest.TestCase):
    def test_normal_state_allows_resize(self):
        self.assertTrue(window_metrics.can_resize(maximized=False))

    def test_maximized_state_forbids_resize(self):
        """最大化状态不提供边缘拉伸（spec FR-001 边界）。"""
        self.assertFalse(window_metrics.can_resize(maximized=True))


@unittest.skipUnless(sys.platform == "win32", "宿主交互引擎为 Windows 专属")
class NativePrimitiveTests(unittest.TestCase):
    """原生原语可用性：防止「Win32 调用被兜底吞掉、引擎静默退出」再次发生。"""

    def test_cursor_pos_returns_screen_point(self):
        """指针读取必须真的可用（曾因漏 import ctypes 静默返回 None，拖拽全失效）。"""
        point = window_interaction._cursor_pos()
        self.assertIsInstance(point, tuple)
        self.assertEqual(len(point), 2)
        self.assertTrue(all(isinstance(v, int) for v in point))

    def test_ctypes_is_imported(self):
        """模块必须自带 ctypes（Win32 入口的全部依赖）。"""
        self.assertTrue(hasattr(window_interaction, "ctypes"))

    def test_left_button_down_returns_bool(self):
        self.assertIsInstance(window_interaction._left_button_down(), bool)

    def test_apply_rect_rejects_invalid_rect(self):
        self.assertFalse(window_interaction.apply_rect(0, (0, 0, 0, 0)))


@unittest.skipUnless(sys.platform == "win32", "宿主交互引擎为 Windows 专属")
class BeginInteractionGuardTests(unittest.TestCase):
    """交互入口的守卫路径（不触发真实循环）。"""

    def setUp(self):
        window_interaction._INTERACTION["active"] = False
        self.addCleanup(window_interaction._INTERACTION.__setitem__, "active", False)

    def test_bad_direction_rejected(self):
        result = window_interaction.begin_resize(_FakeWindow(), "X", 10, 10)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad_direction")

    def test_resize_rejected_when_maximized(self):
        win = _FakeWindow(maximized=True)
        result = window_interaction.begin_resize(win, "R", 10, 10)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "maximized")

    def test_busy_rejects_second_interaction(self):
        window_interaction._INTERACTION["active"] = True
        result = window_interaction.begin_move(_FakeWindow(), 10, 10)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "busy")

    def test_normalized_direction_accepts_lowercase(self):
        self.assertEqual(window_metrics.normalize_direction("br"), "BR")
        self.assertIsNone(window_metrics.normalize_direction("nope"))
        self.assertIsNone(window_metrics.normalize_direction(None))


class _FakeHwndFinder:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.hwnd, "Career Scout v9.9.9"


@unittest.skipUnless(sys.platform == "win32", "宿主交互引擎为 Windows 专属")
class DesktopInteractionApiTests(unittest.TestCase):
    """js_api 入口与注入契约：DesktopJsApi 继承交互基类，只注入下划线属性。"""

    def test_mixin_exposes_begin_methods(self):
        from packaging import desktop

        api = desktop.DesktopJsApi()
        self.assertTrue(callable(getattr(api, "window_begin_move", None)))
        self.assertTrue(callable(getattr(api, "window_begin_resize", None)))

    def test_no_window_returns_error(self):
        from packaging import desktop

        api = desktop.DesktopJsApi()
        self.assertEqual(
            api.window_begin_move(1, 2), {"ok": False, "error": "no_window"}
        )
        self.assertEqual(
            api.window_begin_resize("R", 1, 2), {"ok": False, "error": "no_window"}
        )

    def test_wire_injects_private_refs_only(self):
        from packaging import desktop

        api = desktop.DesktopJsApi()
        window = _FakeWindow()
        marker = lambda _message: None  # noqa: E731 注入替身

        window_interaction.wire_desktop_api(api, window, "tracker", diag=marker)

        self.assertIs(api._window, window)
        self.assertEqual(api._tracker, "tracker")
        self.assertIs(api._diag, marker)
        # 注入只写私有属性：不得凭空长出公开字段（会被 pywebview 枚举爬取）
        for name in ("window", "tracker", "diag"):
            self.assertFalse(hasattr(api, name))

    def test_fallback_normal_rect_uses_default_size(self):
        """无记忆时的还原回退 = 默认普通尺寸（否则拖标题带会去移动最大化窗口）。"""
        rect = window_interaction._fallback_normal_rect(0)
        self.assertIsNotNone(rect)
        self.assertEqual((rect[0], rect[1]), (1400, 800))


@unittest.skipUnless(sys.platform == "win32", "窗口适配器为 Windows 专属")
class AdapterInstallTests(unittest.TestCase):
    """安装合同：幂等、稳定失败码、不新增第二条 hook。"""

    def test_install_reports_success_once(self):
        win = _FakeWindow()
        installs = []

        def hook_installer(window, hwnd, workarea_provider, state_dir, logger):
            installs.append(hwnd)
            return True

        report = window_controls.install_window_adapter(
            win, hwnd_finder=_FakeHwndFinder(4242), hook_installer=hook_installer
        )
        self.assertEqual(
            report,
            {"installed": True, "reason": None, "hwnd_found": True},
        )
        self.assertEqual(installs, [4242])

    def test_install_is_idempotent(self):
        win = _FakeWindow()
        installs = []

        def hook_installer(window, hwnd, workarea_provider, state_dir, logger):
            installs.append(hwnd)
            return True

        first = window_controls.install_window_adapter(
            win, hwnd_finder=_FakeHwndFinder(4242), hook_installer=hook_installer
        )
        second = window_controls.install_window_adapter(
            win, hwnd_finder=_FakeHwndFinder(4242), hook_installer=hook_installer
        )
        self.assertEqual(first, second)
        self.assertEqual(installs, [4242])

    def test_install_reports_hwnd_not_found(self):
        win = _FakeWindow()
        report = window_controls.install_window_adapter(
            win, hwnd_finder=_FakeHwndFinder(None), hook_installer=lambda *a: True
        )
        self.assertFalse(report["installed"])
        self.assertEqual(report["reason"], "hwnd_not_found")
        self.assertFalse(report["hwnd_found"])

    def test_install_reports_hook_failure(self):
        win = _FakeWindow()
        report = window_controls.install_window_adapter(
            win, hwnd_finder=_FakeHwndFinder(99), hook_installer=lambda *a: False
        )
        self.assertFalse(report["installed"])
        self.assertEqual(report["reason"], "hook_failed")

    def test_failed_install_can_retry(self):
        """失败安装不得留下半安装状态，允许下一次重试。"""
        win = _FakeWindow()
        results = [False, True]

        def hook_installer(*_args):
            return results.pop(0)

        first = window_controls.install_window_adapter(
            win, hwnd_finder=_FakeHwndFinder(7), hook_installer=hook_installer
        )
        second = window_controls.install_window_adapter(
            win, hwnd_finder=_FakeHwndFinder(7), hook_installer=hook_installer
        )
        self.assertFalse(first["installed"])
        self.assertTrue(second["installed"])


if __name__ == "__main__":
    unittest.main()
