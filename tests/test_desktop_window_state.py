"""窗口状态域测试（029 自 test_desktop_shell.py 迁移 + 新增）。

覆盖：
- desktop_window.json schema 3 读写往返、maximized 标记
- schema 2 升级：正常记忆继承 / 污染记忆（全屏矩形）作废
- 读时工作区钳制（小屏、越界居中）、default_* 用户覆盖保留
- 无记忆 → 首开处理（默认普通矩形 + maximized=True）
- WindowStateTracker 状态转移与 snapshot_for_save 落盘双分支

契约：specs/029-desktop-window-browsers/contracts/desktop-window-state.md
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


def _state_dir(case):
    """为用例建立临时 state_dir 并登记清理（不写真实用户目录、不残留）。"""
    tmp = tempfile.TemporaryDirectory(prefix="cs-window-state-")
    case.addCleanup(tmp.cleanup)
    return Path(tmp.name)


def _write_state(state_dir, data):
    path = Path(state_dir) / ws.WINDOW_STATE_FILENAME
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _read_state(state_dir):
    return json.loads(
        (Path(state_dir) / ws.WINDOW_STATE_FILENAME).read_text(encoding="utf-8")
    )


# ===========================================================================
# 纯逻辑：load / save（schema 3 + schema 2 升级 + 钳制）
# ===========================================================================
class LoadSaveTests(unittest.TestCase):
    """窗口状态文件读写与校验（契约全部读取规则）。"""

    def setUp(self):
        self.state_dir = _state_dir(self)

    def test_reexport_compat_from_desktop(self):
        """desktop 模块 re-export 窗口状态符号（旧调用面兼容）。"""
        self.assertIs(desktop.load_window_state, ws.load_window_state)
        self.assertIs(desktop.save_window_state, ws.save_window_state)
        self.assertEqual(desktop.DEFAULT_WIDTH, 1400)
        self.assertEqual(desktop.DEFAULT_HEIGHT, 800)

    def test_save_load_roundtrip_normal(self):
        """写入后读取返回相同普通矩形 + maximized=False。"""
        ws.save_window_state(1400, 800, 200, 150, state_dir=self.state_dir)
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(result, (1400, 800, 200, 150, False))

    def test_save_load_roundtrip_maximized(self):
        """maximized=True 写读往返。"""
        ws.save_window_state(
            1400, 800, 50, 60, state_dir=self.state_dir, maximized=True
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(result, (1400, 800, 50, 60, True))

    def test_load_missing_file_first_open_maximized(self):
        """文件不存在 → 首开处理：默认普通矩形 + maximized=True。"""
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_load_invalid_json_first_open_maximized(self):
        """损坏 JSON → 首开处理。"""
        (self.state_dir / ws.WINDOW_STATE_FILENAME).write_text(
            "not json{", encoding="utf-8"
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_load_non_numeric_fields_first_open_maximized(self):
        """记忆字段非数字 → 视同无记忆。"""
        _write_state(
            self.state_dir, {"schema": 3, "width": "wide", "height": 800, "x": 0, "y": 0}
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_load_size_below_min_raised_to_min(self):
        """尺寸小于常规下限 → 抬到 1024×700（不再当作无记忆）。"""
        _write_state(
            self.state_dir, {"schema": 3, "width": 800, "height": 600, "x": 0, "y": 0}
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(result, (ws.MIN_WIDTH, ws.MIN_HEIGHT, 0, 0, False))

    def test_load_size_over_sane_bound_first_open_maximized(self):
        """尺寸超出理智上界（Win32 取值上限）→ 视同损坏记忆。"""
        _write_state(
            self.state_dir, {"schema": 3, "width": 99999, "height": 800, "x": 0, "y": 0}
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_load_wrong_schema_first_open_maximized(self):
        """未知 schema → 视同无记忆。"""
        _write_state(
            self.state_dir, {"schema": 999, "width": 1280, "height": 800, "x": 0, "y": 0}
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_schema1_file_first_open_maximized(self):
        """schema 1 旧文件 → 常量默认 + 首开处理。"""
        _write_state(
            self.state_dir, {"schema": 1, "width": 1280, "height": 800, "x": 0, "y": 0}
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_load_position_outside_workarea_centers(self):
        """位置越出工作区 → 主工作区居中（schema 3）。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1400, "height": 800, "x": 5000, "y": 5000},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1080)],
        )
        self.assertEqual(result, (1400, 800, 260, 140, False))

    def test_load_position_inside_workarea_kept(self):
        """位置在工作区内 → 保留。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1400, "height": 800, "x": 100, "y": 100},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1080)],
        )
        self.assertEqual(result, (1400, 800, 100, 100, False))

    def test_load_position_on_secondary_monitor_kept(self):
        """多显示器：副屏工作区位置保留，不拉回主屏。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1400, "height": 800, "x": 2000, "y": 200},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [
                (0, 0, 1920, 1040),
                (1920, 0, 1920, 1040),
            ],
        )
        self.assertEqual(result, (1400, 800, 2000, 200, False))

    def test_load_negative_position_centers(self):
        """位置为负且不在工作区 → 居中。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1400, "height": 800, "x": -9999, "y": -9999},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1080)],
        )
        self.assertEqual(result, (1400, 800, 260, 140, False))

    def test_no_provider_returns_raw(self):
        """无 workarea_provider → 不钳制不判越界，原样返回（含 maximized）。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1400, "height": 800, "x": -8, "y": -8,
             "maximized": True},
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(result, (1400, 800, -8, -8, True))

    def test_state_dir_injectable_no_real_user_dir(self):
        """目录可注入，测试不写真实 ~/.career-scout。"""
        isolated = _state_dir(self)
        ws.save_window_state(1400, 800, 50, 50, state_dir=isolated)
        self.assertTrue((isolated / ws.WINDOW_STATE_FILENAME).exists())
        result = ws.load_window_state(state_dir=isolated)
        self.assertEqual(result, (1400, 800, 50, 50, False))


class Schema2UpgradeTests(unittest.TestCase):
    """schema 2 升级规则（research D2：正常继承 / 污染作废）。"""

    def setUp(self):
        self.state_dir = _state_dir(self)

    def test_schema2_valid_memory_inherited_as_normal(self):
        """schema 2 正常记忆（装得进工作区）→ 继承为普通矩形，maximized=False。"""
        _write_state(
            self.state_dir,
            {"schema": 2, "width": 1400, "height": 800, "x": 100, "y": 100},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1080)],
        )
        self.assertEqual(result, (1400, 800, 100, 100, False))

    def test_schema2_polluted_memory_treated_as_first_open(self):
        """schema 2 全屏污染矩形（1936×1056 @ -8,-8）→ 视同无记忆，首开处理。"""
        _write_state(
            self.state_dir,
            {"schema": 2, "width": 1936, "height": 1056, "x": -8, "y": -8},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1040)],
        )
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_schema2_polluted_even_if_position_valid(self):
        """污染判定只看尺寸：位置合法但尺寸装不进 → 作废。"""
        _write_state(
            self.state_dir,
            {"schema": 2, "width": 2500, "height": 900, "x": 10, "y": 10},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1080)],
        )
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_schema2_valid_without_provider_kept(self):
        """无 provider 时无法判定污染 → schema 2 正常限内值原样继承。"""
        _write_state(
            self.state_dir,
            {"schema": 2, "width": 1400, "height": 800, "x": 10, "y": 10},
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(result, (1400, 800, 10, 10, False))

    def test_schema3_oversize_clamped_to_workarea(self):
        """schema 3 超出当前工作区 → 钳进工作区（不再作废记忆）。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 5000, "height": 3000, "x": 5000, "y": 5000},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1040)],
        )
        # 尺寸钳到工作区上限（1920×1040），位置越界 → 主工作区居中
        self.assertEqual(result, (1920, 1040, 0, 0, False))

    def test_schema2_missing_memory_uses_configured_default(self):
        """schema 2 缺记忆字段 → 用用户配置的 default 尺寸 + 首开最大化。"""
        _write_state(
            self.state_dir,
            {"schema": 2, "default_width": 1440, "default_height": 900},
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        # 036 v2：default 不再受 1400×800 静态上界约束，配置值被保留
        self.assertEqual(result, (1440, 900, None, None, True))

    def test_schema2_invalid_default_falls_back_constant(self):
        """default 字段非法 → 常量默认。"""
        _write_state(
            self.state_dir,
            {"schema": 2, "default_width": "wide", "default_height": 99999},
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )

    def test_save_preserves_configured_default_and_writes_schema3(self):
        """save 保留 default_*，文件升级为 schema 3 并带 maximized 字段。"""
        _write_state(
            self.state_dir,
            {
                "schema": 2,
                "default_width": 1400,
                "default_height": 800,
                "width": 1200,
                "height": 700,
                "x": 5,
                "y": 5,
            },
        )
        ws.save_window_state(1280, 720, 100, 100, state_dir=self.state_dir)
        data = _read_state(self.state_dir)
        self.assertEqual(data["schema"], 3)
        self.assertEqual(data["default_width"], 1400)
        self.assertEqual(data["default_height"], 800)
        self.assertEqual((data["width"], data["height"]), (1280, 720))
        self.assertFalse(data["maximized"])


class DefaultRectTests(unittest.TestCase):
    """默认普通矩形与默认尺寸钳制（US2 场景 3）。"""

    def test_default_normal_rect_without_provider(self):
        """无 provider → 常量尺寸 + (0,0) 位置。"""
        self.assertEqual(ws.default_normal_rect(None), (1400, 800, 0, 0))

    def test_default_normal_rect_centered(self):
        """有 provider → 默认尺寸 + 主工作区居中。"""
        rect = ws.default_normal_rect(lambda: [(0, 0, 1920, 1080)])
        self.assertEqual(rect, (1400, 800, 260, 140))

    def test_default_normal_rect_clamped_on_small_screen(self):
        """小屏 → 默认尺寸钳到工作区。"""
        rect = ws.default_normal_rect(lambda: [(0, 0, 1366, 728)])
        self.assertEqual(rect, (1366, 728, 0, 0))

    def test_default_size_clamped_on_load_no_memory(self):
        """无记忆 + 小屏 → 首开普通默认也钳到工作区。"""
        result = ws.load_window_state(
            state_dir=_state_dir(self),
            workarea_provider=lambda: [(0, 0, 1366, 728)],
        )
        self.assertEqual(result, (1366, 728, None, None, True))

    def test_default_rect_provider_raising_falls_back(self):
        """provider 抛异常 → 回退常量。"""
        def boom():
            raise RuntimeError("monitor gone")

        self.assertEqual(ws.default_normal_rect(boom), (1400, 800, 0, 0))


# ===========================================================================
# 036 v2 尺寸语义：默认 1400×800 / 常规下限 1024×700 / 上限 = 当前工作区
# ===========================================================================
class SizeSemanticsTests(unittest.TestCase):
    """替代旧的「min = max = 默认 1400×800」固定语义（spec FR-005/FR-006/FR-007）。"""

    def setUp(self):
        self.state_dir = _state_dir(self)

    def test_constants(self):
        self.assertEqual((ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT), (1400, 800))
        self.assertEqual((ws.MIN_WIDTH, ws.MIN_HEIGHT), (1024, 700))

    def test_min_size_memory_kept(self):
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1024, "height": 700, "x": 0, "y": 0},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1040)],
        )
        self.assertEqual(result, (1024, 700, 0, 0, False))

    def test_larger_than_default_memory_kept(self):
        """合法放大记忆（1600×900）不再被 1400×800 静态上界拒绝。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1600, "height": 900, "x": 40, "y": 40},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 2560, 1400)],
        )
        self.assertEqual(result, (1600, 900, 40, 40, False))

    def test_upper_bound_follows_current_workarea(self):
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 2500, "height": 1500, "x": 0, "y": 0},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 1920, 1040)],
        )
        self.assertEqual(result, (1920, 1040, 0, 0, False))

    def test_small_workarea_relaxes_min(self):
        """工作区比常规下限小 → 允许小于 1024×700，但不得大于工作区。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 1400, "height": 800, "x": 0, "y": 0},
        )
        result = ws.load_window_state(
            state_dir=self.state_dir,
            workarea_provider=lambda: [(0, 0, 900, 600)],
        )
        self.assertEqual(result, (900, 600, 0, 0, False))

    def test_below_min_without_provider_raised_to_min(self):
        """无 provider 时也要抬到常规下限（不放过小于下限的记忆）。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "width": 700, "height": 500, "x": 0, "y": 0},
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(result, (1024, 700, 0, 0, False))

    def test_configured_default_below_min_falls_back(self):
        """用户配置的 default 低于常规下限 → 回退常量默认。"""
        _write_state(
            self.state_dir,
            {"schema": 3, "default_width": 800, "default_height": 600},
        )
        result = ws.load_window_state(state_dir=self.state_dir)
        self.assertEqual(
            result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, None, None, True)
        )


# ===========================================================================
# 纯逻辑：WindowStateTracker（research D1）
# ===========================================================================
class TrackerTests(unittest.TestCase):
    """运行时普通矩形追踪与落盘快照。"""

    def setUp(self):
        self.default_rect = (1400, 800, 260, 140)
        self.tracker = ws.WindowStateTracker(
            default_rect_fn=lambda: self.default_rect
        )

    def test_initial_state(self):
        self.assertFalse(self.tracker.maximized)
        self.assertIsNone(self.tracker.last_normal)

    def test_resized_and_moved_record_normal_rect(self):
        self.tracker.on_resized(1200, 800)
        self.tracker.on_moved(50, 60)
        self.assertEqual(self.tracker.last_normal, (1200, 800, 50, 60))

    def test_resized_non_numeric_ignored(self):
        self.tracker.on_resized(None, 800)
        self.assertIsNone(self.tracker.last_normal)

    def test_resized_above_default_recorded(self):
        """拉伸到大于默认尺寸（1600×900）后，快照保留该尺寸（无静态上界）。"""
        self.tracker.on_resized(1600, 900)
        self.tracker.on_moved(80, 90)
        result = self.tracker.snapshot_for_save(1600, 900, 80, 90)
        self.assertEqual(result, (1600, 900, 80, 90, False))

    def test_maximized_freezes_normal_rect(self):
        self.tracker.on_resized(1200, 800)
        self.tracker.on_moved(50, 60)
        self.tracker.on_maximized()
        self.assertTrue(self.tracker.maximized)
        # 最大化期间 resized/moved 不改写普通矩形
        self.tracker.on_resized(1936, 1056)
        self.tracker.on_moved(-8, -8)
        self.assertEqual(self.tracker.last_normal, (1200, 800, 50, 60))

    def test_restored_resumes_from_window_rect(self):
        self.tracker.on_resized(1200, 800)
        self.tracker.on_maximized()
        self.tracker.on_restored(1200, 800, 50, 60)
        self.assertFalse(self.tracker.maximized)
        self.assertEqual(self.tracker.last_normal, (1200, 800, 50, 60))

    def test_restored_without_rect_keeps_frozen_rect(self):
        self.tracker.on_resized(1200, 800)
        self.tracker.on_maximized()
        self.tracker.on_restored()
        self.assertFalse(self.tracker.maximized)
        self.assertEqual(self.tracker.last_normal, (1200, 800, None, None))

    def test_size_guard_rejects_oversized_resized(self):
        """守卫拒绝装不进工作区的尺寸（macOS 全屏动画先于 maximized 的 resized）。"""
        tracker = ws.WindowStateTracker(
            default_rect_fn=lambda: self.default_rect,
            size_guard=lambda w, h: w <= 1920 and h <= 1040,
        )
        tracker.on_resized(1200, 800)
        # 全屏动画的中间/最终尺寸被守卫拒绝，普通矩形不被污染
        tracker.on_resized(1936, 1056)
        self.assertEqual(tracker.last_normal, (1200, 800, None, None))

    def test_size_guard_exception_allows_update(self):
        """守卫自身抛异常 → 放行（不误杀正常追踪）。"""
        tracker = ws.WindowStateTracker(
            default_rect_fn=lambda: self.default_rect,
            size_guard=lambda w, h: 1 / 0,
        )
        tracker.on_resized(1300, 850)
        self.assertEqual(tracker.last_normal, (1300, 850, None, None))

    def test_size_guard_not_set_accepts_everything(self):
        """未设守卫 → 行为与旧版一致（全部接受）。"""
        tracker = ws.WindowStateTracker(default_rect_fn=lambda: self.default_rect)
        tracker.on_resized(1936, 1056)
        self.assertEqual(tracker.last_normal, (1936, 1056, None, None))

    def test_snapshot_normal_uses_current_values(self):
        self.tracker.on_resized(1200, 800)
        result = self.tracker.snapshot_for_save(1300, 850, 20, 30)
        self.assertEqual(result, (1300, 850, 20, 30, False))

    def test_snapshot_normal_falls_back_to_last_normal(self):
        self.tracker.on_resized(1200, 800)
        self.tracker.on_moved(50, 60)
        result = self.tracker.snapshot_for_save(None, None, None, None)
        self.assertEqual(result, (1200, 800, 50, 60, False))

    def test_snapshot_normal_no_data_uses_default_rect(self):
        result = self.tracker.snapshot_for_save(None, None, None, None)
        self.assertEqual(result, (1400, 800, 260, 140, False))

    def test_snapshot_maximized_uses_frozen_rect(self):
        self.tracker.on_resized(1200, 800)
        self.tracker.on_moved(50, 60)
        self.tracker.on_maximized()
        result = self.tracker.snapshot_for_save(1936, 1056, -8, -8)
        # 全屏矩形不得写入：返回冻结的普通矩形 + maximized=True
        self.assertEqual(result, (1200, 800, 50, 60, True))

    def test_snapshot_maximized_without_normal_rect_uses_default(self):
        self.tracker.on_maximized()
        result = self.tracker.snapshot_for_save(1936, 1056, -8, -8)
        self.assertEqual(result, (1400, 800, 260, 140, True))

    def test_snapshot_maximized_partial_rect_uses_default(self):
        self.tracker.on_resized(1200, 800)  # 位置仍缺
        self.tracker.on_maximized()
        result = self.tracker.snapshot_for_save(1936, 1056, -8, -8)
        self.assertEqual(result, (1400, 800, 260, 140, True))

    def test_snapshot_without_default_fn_falls_back_constants(self):
        tracker = ws.WindowStateTracker()
        tracker.on_maximized()
        result = tracker.snapshot_for_save(1936, 1056, -8, -8)
        self.assertEqual(result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, 0, 0, True))

    def test_default_rect_fn_raising_falls_back_constants(self):
        tracker = ws.WindowStateTracker(default_rect_fn=lambda: 1 / 0)
        tracker.on_maximized()
        result = tracker.snapshot_for_save(1936, 1056, -8, -8)
        self.assertEqual(result, (ws.DEFAULT_WIDTH, ws.DEFAULT_HEIGHT, 0, 0, True))




if __name__ == "__main__":
    unittest.main()
