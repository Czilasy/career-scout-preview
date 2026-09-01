"""白箱聚焦测试：关键异常路径必须在 career-scout.log 留痕（033-log-whitebox）。

验证 in-process 通用异常路径在日志中留下异常类型与现场，而非仅"抓取执行失败"。
"""

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui.logging_setup import configure_logging
from webui.source import BossCdpSource


def _close_logger():
    logger = logging.getLogger("career_scout")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


class LoggingWhiteboxTests(unittest.TestCase):
    def tearDown(self):
        _close_logger()

    def test_in_process_unknown_exception_leaves_type_in_log(self):
        """in-process 通用 except 必须在日志留异常类型与现场（而非仅'抓取执行失败'）。"""
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            source = BossCdpSource(in_process=True)
            command = [
                "python", "boss_cdp_raw.py",
                "--keyword", "python",
                "--city", "北京",
                "--output", str(Path(tmp) / "out.json"),
                "--no-detail",
            ]
            with mock.patch(
                "scripts.boss_cdp_raw.run_search_programmatic",
                side_effect=RuntimeError("boom-xyz"),
            ):
                code, captured = source._run_in_process(command, 10)
            self.assertEqual(code, -1)
            self.assertIn("抓取执行失败", captured)
            content = (Path(tmp) / "career-scout.log").read_text(encoding="utf-8")
            self.assertIn("in-process 抓取执行失败", content)
            self.assertIn("RuntimeError", content)
            self.assertIn("boom-xyz", content)
            _close_logger()

    def test_subprocess_failure_field_reaches_main_log(self):
        """白箱：抓取子进程非 0 退出时，现场原文必须进主日志（否则只剩对外文案）。"""
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            out = Path(tmp) / "list_out.json"
            source = BossCdpSource(artifact_root=tmp, run_id="task-field-1")
            plan = {
                "keyword": "python",
                "city": "北京",
                "input_hash": "hash-1",
                "list_output_path": str(out),
            }
            stderr = "Traceback (most recent call last):\nValueError: scraper-field-marker-42"
            with mock.patch.object(BossCdpSource, "_run_command", return_value=(1, stderr)):
                outcome = source.fetch_list(plan)
            self.assertFalse(outcome.ok)
            content = (Path(tmp) / "career-scout.log").read_text(encoding="utf-8")
            self.assertIn("抓取子进程异常退出", content)
            self.assertIn("scraper-field-marker-42", content)
            _close_logger()

    def test_batch_detail_subprocess_failure_reaches_main_log(self):
        """白箱：批量详情子进程非 0 退出时，现场原文进主日志（FR-010 全链路）。"""
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            out = Path(tmp) / "detail_out.json"
            source = BossCdpSource(artifact_root=tmp, run_id="task-batch-1")
            jobs = [{
                "job_id": "job-1",
                "source_url": "https://www.zhipin.com/job_detail/abc123.html",
            }]
            stderr = "Traceback (most recent call last):\nRuntimeError: batch-field-marker-99"
            with mock.patch.object(BossCdpSource, "_run_command", return_value=(1, stderr)):
                results = source.fetch_details_batch(jobs, detail_output_path=str(out))
            self.assertTrue(results)
            content = (Path(tmp) / "career-scout.log").read_text(encoding="utf-8")
            self.assertIn("抓取子进程异常退出", content)
            self.assertIn("batch-field-marker-99", content)
            _close_logger()

    def test_subprocess_env_carries_task_id(self):
        """子进程 env 必须带上任务编号，现场日志才能归属到具体任务。"""
        source = BossCdpSource(run_id="task-env-check")
        self.assertEqual(source.env.get("CAREER_SCOUT_TASK_ID"), "task-env-check")
        self.assertEqual(source.env.get("CAREER_SCOUT_CORRELATION_ID"), "task-env-check")

    def test_lazy_init_restores_task_id_from_env(self):
        """子进程侧懒初始化时，任务编号从 env 恢复到日志上下文。"""
        from webui import logging_setup

        logging_setup._task_id_var.set("")
        _close_logger()
        try:
            with mock.patch.dict(os.environ, {"CAREER_SCOUT_TASK_ID": "task-from-env"}):
                logging_setup.get_logger("task_id_probe")
                self.assertEqual(logging_setup._task_id_var.get(), "task-from-env")
        finally:
            logging_setup._task_id_var.set("")
            _close_logger()
            temp_dir = Path(tempfile.gettempdir()) / "career-scout-test-logs"
            for p in temp_dir.glob("career-scout.log*"):
                try:
                    p.unlink()
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
