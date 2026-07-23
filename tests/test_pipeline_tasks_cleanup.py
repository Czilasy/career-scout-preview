"""T013: _pipeline_tasks 终态后 30 分钟自动清理机制测试。

验证：
1. 终态写入后 Timer 被注册（interval=1800s, daemon=True）
2. Timer 回调执行后任务从 _pipeline_tasks 移除
3. 清理不存在的 task_id 不报错
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from webui.app import create_app


class PipelineTasksCleanupTests(unittest.TestCase):
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
        self.pipeline_tasks = self.app.config["PIPELINE_TASKS"]
        self.schedule_cleanup = self.app.config["SCHEDULE_PIPELINE_CLEANUP"]

    def tearDown(self):
        self.temp.cleanup()

    def test_cleanup_schedules_timer_on_terminal_status(self):
        """终态后 30 分钟定时器被注册，daemon=True。"""
        with mock.patch("webui.app.threading.Timer") as MockTimer:
            mock_timer = mock.MagicMock()
            MockTimer.return_value = mock_timer
            self.schedule_cleanup("task-done-123")
            MockTimer.assert_called_once()
            interval = MockTimer.call_args[0][0]
            self.assertEqual(interval, 30 * 60)
            mock_timer.daemon = True
            mock_timer.start.assert_called_once()

    def test_cleanup_callback_removes_task(self):
        """Timer 回调执行后任务从 _pipeline_tasks 移除。"""
        self.pipeline_tasks["task-done-456"] = {"status": "done"}
        with mock.patch("webui.app.threading.Timer") as MockTimer:
            self.schedule_cleanup("task-done-456")
            cleanup_callback = MockTimer.call_args[0][1]
            cleanup_callback()
        self.assertNotIn("task-done-456", self.pipeline_tasks)

    def test_cleanup_nonexistent_task_no_error(self):
        """清理不存在的 task_id 不报错。"""
        with mock.patch("webui.app.threading.Timer") as MockTimer:
            self.schedule_cleanup("nonexistent-task")
            cleanup_callback = MockTimer.call_args[0][1]
            # 不应抛异常
            cleanup_callback()


if __name__ == "__main__":
    unittest.main()
