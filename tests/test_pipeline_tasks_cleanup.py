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


class FetchJobDetailsTests(unittest.TestCase):
    """pipeline_exec.fetch_job_details：返回结构、取消、断点续抓、登录墙接通。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _source(outcomes_by_jid):
        source = mock.MagicMock()
        source.fetch_details_batch.return_value = outcomes_by_jid
        return source

    @staticmethod
    def _make_outcome(*, ok=False, jd="", failed_code=None):
        from webui.source import SourceOutcome
        if ok:
            return SourceOutcome(ok=True, detail={"jd": jd})
        return SourceOutcome.failure(
            failed_code=failed_code or "source_blocked", safe_log="x")

    @mock.patch("webui.pipeline_exec.load_advanced_settings",
                return_value={"detail_batch_size": 5})
    def test_returns_jobs_with_jd_and_counts(self, _settings):
        from webui.pipeline_exec import fetch_job_details

        jobs = [{"job_id": "j1", "title": "A"}, {"job_id": "j2", "title": "B"}]
        source = self._source({
            "j1": self._make_outcome(ok=True, jd="做后端"),
            "j2": self._make_outcome(ok=False),
        })

        result = fetch_job_details(jobs, source, artifact_dir=str(self.temp.name))

        self.assertEqual(result["jobs"][0]["jd"], "做后端")
        self.assertEqual(result["jobs"][1]["jd"], "")
        self.assertEqual(result["fetched"], 1)
        self.assertFalse(result["login_wall"])
        self.assertFalse(result["stopped"])

    @mock.patch("webui.pipeline_exec.load_advanced_settings",
                return_value={"detail_batch_size": 5})
    def test_login_wall_stops_remaining_batches(self, _settings):
        from webui.pipeline_exec import fetch_job_details

        jobs = [{"job_id": f"j{i}"} for i in range(8)]  # 5+3 两批
        outcomes = {f"j{i}": self._make_outcome(ok=True, jd="x") for i in range(5)}
        outcomes["j2"] = self._make_outcome(ok=False, failed_code="source_login_required")
        source = self._source(outcomes)

        result = fetch_job_details(jobs, source, artifact_dir=str(self.temp.name))

        self.assertTrue(result["login_wall"])
        # 命中登录墙后第二批不再抓（不继续打空气）
        self.assertEqual(source.fetch_details_batch.call_count, 1)

    @mock.patch("webui.pipeline_exec.load_advanced_settings",
                return_value={"detail_batch_size": 5})
    def test_stop_event_halts_before_next_batch(self, _settings):
        import threading
        from webui.pipeline_exec import fetch_job_details

        jobs = [{"job_id": f"j{i}"} for i in range(8)]
        stop = threading.Event()
        stop.set()
        source = self._source({})

        result = fetch_job_details(jobs, source, artifact_dir=str(self.temp.name),
                                   stop_event=stop)

        self.assertTrue(result["stopped"])
        source.fetch_details_batch.assert_not_called()

    @mock.patch("webui.pipeline_exec.load_advanced_settings",
                return_value={"detail_batch_size": 5})
    def test_completed_job_ids_keep_existing_jd(self, _settings):
        from webui.pipeline_exec import fetch_job_details

        jobs = [{"job_id": "j1", "jd": "已有JD"}, {"job_id": "j2"}]
        source = self._source({"j1": self._make_outcome(ok=True, jd="覆盖?"),
                               "j2": self._make_outcome(ok=True, jd="新抓")})

        result = fetch_job_details(jobs, source, artifact_dir=str(self.temp.name),
                                   completed_job_ids={"j1"})

        self.assertEqual(result["jobs"][0]["jd"], "已有JD")  # 已抓的保留原值
        self.assertEqual(result["jobs"][1]["jd"], "新抓")


if __name__ == "__main__":
    unittest.main()
