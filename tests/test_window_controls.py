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

from packaging import window_controls


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


class WorkareaClampFlagTests(unittest.TestCase):
    def test_clamp_flag_defaults_false(self):
        """最大化避让任务栏适配位默认关闭（真机验证前依赖 pywebview 默认行为）。"""
        self.assertFalse(window_controls.MAXIMIZE_WORKAREA_CLAMP)

    def test_clamp_placeholder_is_ok(self):
        """适配位占位实现返回 ok（真机结论落地前不阻断）。"""
        result = window_controls._clamp_to_workarea(_FakeWindow())
        self.assertEqual(result, {"ok": True, "error": None})


if __name__ == "__main__":
    unittest.main()
