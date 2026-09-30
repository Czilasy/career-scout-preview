"""046 第五轮：任务不在活动态时 active_elapsed_ms 必须定格。

现场：抓取/AI run 已经跑完（或进程重启后被标 interrupted），但
screening_runs.finished_at 为空，/api/task-state 每次轮询返回的
active_elapsed_ms 都在增长，界面"完整成功"的阶段因此一秒秒走表。

锁死两条口径：
1. 只有真的有 worker 在跑的状态走活表（running；"正在暂停"在后端不落状态，
   行状态仍是 running，所以同样在飞）。
2. 非在跑状态一律定格：结束锚点按真实 finished_at → 最后一条任务事件时间 →
   run 行 updated_at 取，三次都取不到才回退"现在"；两次求值必须一致，
   不随当前时间增长。queued/waiting（尚未开始）属于这一族——排队意味着
   还没开始干活，不得凭空给出一个增长中的时长（没有事件时定格为 0）。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from webui.app import create_app
from webui.task_status import _active_elapsed_ms

_CST = timezone(timedelta(hours=8))
_BASE = datetime(2026, 8, 1, 10, 0, 0, tzinfo=_CST)


def _iso(seconds):
    return (_BASE + timedelta(seconds=seconds)).isoformat()


def _epoch_ms(seconds):
    return int((_BASE + timedelta(seconds=seconds)).timestamp() * 1000)


def _fake_clock(seconds):
    """把 webui.task_status 里的"现在"钉成 _BASE + seconds。"""
    return SimpleNamespace(time=lambda: _epoch_ms(seconds) / 1000.0)


class TaskStateElapsedFreezeTests(unittest.TestCase):
    """/api/task-state 出口：已完成/已中断 run 的时长不随轮询增长。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _make_run(self, run_id, status, *, stopped_at=650):
        """建 run：started_at = _BASE，finished_at 留空，updated_at = stopped_at。

        queued 用例会保持"建行后没人再写过这行"的现场：排队中意味着还没有
        worker 动过它，updated_at 与 started_at 同值，定格时长就该是 0。
        """
        with mock.patch("webui.store_runs._now", return_value=_iso(0)):
            self.store.create_screening_run(run_id, source_count=1)
        if status not in ("queued",):
            with mock.patch("webui.store_runs._now", return_value=_iso(0)):
                self.store.update_screening_run(run_id, status="running")
        if status not in ("queued", "running"):
            with mock.patch("webui.store_runs._now", return_value=_iso(stopped_at)):
                self.store.update_screening_run(run_id, status=status)

    def _add_event(self, run_id, at):
        with mock.patch("webui.store_runs._now", return_value=_iso(at)):
            self.store.append_task_events(run_id, [("progress", {})])

    def _elapsed_at(self, run_id, now_seconds):
        with mock.patch("webui.task_status.time", _fake_clock(now_seconds)):
            data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertTrue(data["ok"])
        return data

    def test_completed_run_without_finished_at_freezes_at_last_event(self):
        run_id = "freeze-completed"
        self._make_run(run_id, "succeeded", stopped_at=650)
        self._add_event(run_id, 600)
        first = self._elapsed_at(run_id, 36_000)
        # 3.2 秒后再取一次：与现场实测同样的间隔
        second = self._elapsed_at(run_id, 36_003.2)
        self.assertEqual(first["db_status"], "succeeded")
        self.assertEqual(first["active_elapsed_ms"], 600_000)
        self.assertEqual(second["active_elapsed_ms"], first["active_elapsed_ms"])

    def test_interrupted_run_without_finished_at_freezes(self):
        run_id = "freeze-interrupted"
        self._make_run(run_id, "interrupted", stopped_at=700)
        self._add_event(run_id, 600)
        first = self._elapsed_at(run_id, 36_000)
        second = self._elapsed_at(run_id, 36_003.2)
        self.assertEqual(first["db_status"], "interrupted")
        self.assertEqual(first["active_elapsed_ms"], 600_000)
        self.assertEqual(second["active_elapsed_ms"], first["active_elapsed_ms"])

    def test_completed_run_without_events_freezes_at_run_updated_at(self):
        run_id = "freeze-no-events"
        self._make_run(run_id, "succeeded", stopped_at=300)
        first = self._elapsed_at(run_id, 36_000)
        second = self._elapsed_at(run_id, 36_003.2)
        self.assertEqual(first["active_elapsed_ms"], 300_000)
        self.assertEqual(second["active_elapsed_ms"], first["active_elapsed_ms"])

    def test_real_finished_at_still_wins_over_events_and_updated_at(self):
        run_id = "freeze-finished-at"
        self._make_run(run_id, "succeeded", stopped_at=650)
        self._add_event(run_id, 600)
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET finished_at = ? WHERE id = ?",
                (_iso(900), run_id),
            )
        first = self._elapsed_at(run_id, 36_000)
        second = self._elapsed_at(run_id, 36_003.2)
        self.assertEqual(first["active_elapsed_ms"], 900_000)
        self.assertEqual(second["active_elapsed_ms"], first["active_elapsed_ms"])

    def test_running_run_still_grows_with_the_clock(self):
        """反向回归：真正在跑的任务必须继续计时。"""
        run_id = "freeze-running"
        self._make_run(run_id, "running")
        self._add_event(run_id, 60)
        first = self._elapsed_at(run_id, 600)
        second = self._elapsed_at(run_id, 603.2)
        self.assertEqual(first["db_status"], "running")
        self.assertEqual(first["active_elapsed_ms"], 600_000)
        self.assertEqual(
            second["active_elapsed_ms"] - first["active_elapsed_ms"], 3_200)

    def test_queued_run_freezes_until_the_worker_starts(self):
        """排队中＝还没开始干活：时长定格（无事件即 0），不随轮询一格一格涨。"""
        run_id = "freeze-queued"
        self._make_run(run_id, "queued")
        first = self._elapsed_at(run_id, 600)
        second = self._elapsed_at(run_id, 603.2)
        self.assertEqual(first["db_status"], "queued")
        self.assertEqual(first["active_elapsed_ms"], 0)
        self.assertEqual(second["active_elapsed_ms"], 0)

    def test_queued_run_with_events_freezes_at_the_last_event(self):
        """排队期间确有留痕时，定格在最后一条事件，而不是当前时间。"""
        run_id = "freeze-queued-event"
        self._make_run(run_id, "queued")
        self._add_event(run_id, 30)
        first = self._elapsed_at(run_id, 36_000)
        second = self._elapsed_at(run_id, 36_003.2)
        self.assertEqual(first["active_elapsed_ms"], 30_000)
        self.assertEqual(second["active_elapsed_ms"], 30_000)


class ActiveElapsedAnchorUnitTests(unittest.TestCase):
    """_active_elapsed_ms 锚点优先级与旧调用兼容（纯函数，不碰 DB）。"""

    _EVENTS = [
        {"type": "progress", "at": _iso(420)},
        {"type": "progress", "at": _iso(600)},
    ]

    def test_non_active_anchor_priority_finished_event_updated_at(self):
        self.assertEqual(
            _active_elapsed_ms(
                _epoch_ms(0), _epoch_ms(900), self._EVENTS,
                status="succeeded", updated_at_ms=_epoch_ms(650),
                now_ms=_epoch_ms(36_000),
            ),
            900_000,
        )
        self.assertEqual(
            _active_elapsed_ms(
                _epoch_ms(0), None, self._EVENTS,
                status="interrupted", updated_at_ms=_epoch_ms(650),
                now_ms=_epoch_ms(36_000),
            ),
            600_000,
        )
        self.assertEqual(
            _active_elapsed_ms(
                _epoch_ms(0), None, [],
                status="paused", updated_at_ms=_epoch_ms(650),
                now_ms=_epoch_ms(36_000),
            ),
            650_000,
        )

    def test_running_is_the_only_live_clock_status(self):
        """反向保护：running（含"正在暂停"窗口，后端不落独立状态）仍走活表。"""
        self.assertEqual(
            _active_elapsed_ms(
                _epoch_ms(0), None, self._EVENTS,
                status="running", updated_at_ms=_epoch_ms(650),
                now_ms=_epoch_ms(36_000),
            ),
            36_000_000,
        )

    def test_not_started_queued_uses_durable_anchor_like_terminal_states(self):
        """queued/waiting（尚未开始）与终态同族：定格在锚点，不随 now 增长。"""
        for status in ("queued", "waiting", "paused", "interrupted", "succeeded"):
            with self.subTest(status=status):
                frozen = _active_elapsed_ms(
                    _epoch_ms(0), None, self._EVENTS,
                    status=status, updated_at_ms=_epoch_ms(650),
                    now_ms=_epoch_ms(36_000),
                )
                later = _active_elapsed_ms(
                    _epoch_ms(0), None, self._EVENTS,
                    status=status, updated_at_ms=_epoch_ms(650),
                    now_ms=_epoch_ms(72_000),
                )
                self.assertEqual(frozen, 600_000)
                self.assertEqual(later, frozen)

    def test_missing_all_anchors_falls_back_to_now(self):
        """finished_at/事件/updated_at 三者都取不到才允许回退"现在"。"""
        self.assertEqual(
            _active_elapsed_ms(
                _epoch_ms(0), None, [], status="succeeded",
                now_ms=_epoch_ms(36_000),
            ),
            36_000_000,
        )

    def test_legacy_three_argument_call_is_unchanged(self):
        """webui.app 兼容 re-export 的旧调用（不传状态）保持既有随时间行为。"""
        with mock.patch("webui.task_status.time", _fake_clock(36_000)):
            self.assertEqual(
                _active_elapsed_ms(_epoch_ms(0), None, self._EVENTS),
                36_000_000,
            )


if __name__ == "__main__":
    unittest.main()
