# -*- coding: utf-8 -*-
"""024 详情抓取人形模拟行为聚焦测试（detail_simulation）。"""

import random
import unittest

from scripts.boss.detail_simulation import (
    SIMULATION_PARAMS,
    resolve_params,
    simulate_after_load,
)


class _FakeSession:
    """记录 eval_js / send 调用的假 CDP 会话。"""

    def __init__(self):
        self.scrolls: list[str] = []
        self.mouse_moves: list[dict] = []
        self.sends: list[tuple] = []

    def eval_js(self, js, sid=None):
        self.scrolls.append(js)
        return None

    def send(self, method, params, sid=None):
        self.sends.append((method, params))
        if method == "Input.dispatchMouseEvent":
            self.mouse_moves.append(params)


def _make_sleeper(record):
    def sleeper(seconds, label=None):
        record.append((seconds, label))
    return sleeper


class SimulationParamsTableTests(unittest.TestCase):
    """024 冻结表 #12-#14 参数断言。"""

    def test_stable_params_match_frozen_table(self):
        self.assertEqual(SIMULATION_PARAMS["stable"]["wait_range"], (5.0, 10.0))
        self.assertEqual(SIMULATION_PARAMS["stable"]["scroll_range"], (3, 7))
        self.assertEqual(SIMULATION_PARAMS["stable"]["mouse_prob"], 0.5)

    def test_balanced_params_match_frozen_table(self):
        self.assertEqual(SIMULATION_PARAMS["balanced"]["wait_range"], (3.0, 6.0))
        self.assertEqual(SIMULATION_PARAMS["balanced"]["scroll_range"], (2, 4))
        self.assertEqual(SIMULATION_PARAMS["balanced"]["mouse_prob"], 0.3)

    def test_extreme_params_match_frozen_table(self):
        self.assertEqual(SIMULATION_PARAMS["extreme"]["wait_range"], (1.0, 2.0))
        self.assertEqual(SIMULATION_PARAMS["extreme"]["scroll_range"], (0, 1))
        self.assertEqual(SIMULATION_PARAMS["extreme"]["mouse_prob"], 0.0)

    def test_resolve_params_returns_table_entry(self):
        self.assertIs(resolve_params("stable"), SIMULATION_PARAMS["stable"])

    def test_resolve_params_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            resolve_params("custom")  # custom 不启用模拟行为（零仿真）
        with self.assertRaises(ValueError):
            resolve_params("nope")


class SimulateAfterLoadTests(unittest.TestCase):
    """simulate_after_load 行为断言（注入随机种子保证稳定）。"""

    def _run(self, params, seed=42):
        session = _FakeSession()
        waits: list[tuple] = []
        random.seed(seed)
        simulate_after_load(
            session, "sid1", params=params,
            sleeper=_make_sleeper(waits), label_prefix="[tab1] ",
        )
        return session, waits

    def test_wait_falls_in_mode_range(self):
        session, waits = self._run(resolve_params("stable"))
        self.assertTrue(waits, "应至少有一次等待")
        first_wait = waits[0][0]
        self.assertGreaterEqual(first_wait, 5.0)
        self.assertLessEqual(first_wait, 10.0)
        self.assertEqual(waits[0][1], "[tab1] sim_load_wait")

    def test_scroll_count_within_range(self):
        session, _ = self._run(resolve_params("stable"))
        self.assertGreaterEqual(len(session.scrolls), 3)
        self.assertLessEqual(len(session.scrolls), 7)

    def test_extreme_scroll_bounded(self):
        session, waits = self._run(resolve_params("extreme"), seed=7)
        self.assertLessEqual(len(session.scrolls), 1)
        first_wait = waits[0][0]
        self.assertGreaterEqual(first_wait, 1.0)
        self.assertLessEqual(first_wait, 2.0)

    def test_zero_mouse_prob_no_mouse_move(self):
        params = dict(resolve_params("balanced"), mouse_prob=0.0)
        session, _ = self._run(params, seed=123)
        self.assertEqual(session.mouse_moves, [])

    def test_one_mouse_prob_moves(self):
        params = dict(resolve_params("stable"), mouse_prob=1.0)
        session, _ = self._run(params, seed=123)
        self.assertEqual(len(session.mouse_moves), 1)
        self.assertIn("x", session.mouse_moves[0])
        self.assertIn("y", session.mouse_moves[0])

    def test_fake_session_errors_do_not_break_simulation(self):
        class _BrokenSession:
            def eval_js(self, js, sid=None):
                raise RuntimeError("eval failed")

            def send(self, method, params, sid=None):
                raise RuntimeError("send failed")

        waits: list[tuple] = []
        random.seed(1)
        simulate_after_load(
            _BrokenSession(), "sid", params=resolve_params("stable"),
            sleeper=_make_sleeper(waits),
        )
        self.assertTrue(waits)  # 等待仍执行，滚动/鼠标失败被吞掉


if __name__ == "__main__":
    unittest.main()
