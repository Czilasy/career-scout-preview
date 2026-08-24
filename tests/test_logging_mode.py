"""日志运行模式可用性测试（022-jd-stall-guard US3，T012）。

验证 configure_logging 在源码模式与模拟 EXE（sys.frozen 注入）下都
产生可写、可读的 career-scout.log；并确认 create_app 非 TESTING 路径
无条件调用 configure_logging（源码/EXE 运行模式日志均可用）。
"""

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui.logging_setup import configure_logging, get_logger, is_configured


def _close_logger():
    logger = logging.getLogger("career_scout")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


class LoggingModeTests(unittest.TestCase):
    def tearDown(self):
        _close_logger()

    def test_source_mode_logging_produces_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            logger = get_logger("pipeline_guard")
            logger.info("stall batch=b1 task=t1 attempt=1 result=kill_worker")
            log_path = Path(tmp) / "career-scout.log"
            self.assertTrue(log_path.is_file())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("stall", content)
            self.assertIn("batch=b1", content)
            _close_logger()

    def test_exe_mode_logging_produces_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "_MEIPASS", "/fake/meipass", create=True):
                configure_logging(tmp, force=True)
                logger = get_logger("pipeline_guard")
                logger.error("giveup batch=b2 task=t2 attempt=3 result=divert_environment")
            log_path = Path(tmp) / "career-scout.log"
            self.assertTrue(log_path.is_file())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("giveup", content)
            self.assertIn("batch=b2", content)
            _close_logger()

    def test_create_app_non_testing_configures_logging(self):
        """create_app 的非 TESTING 路径 MUST 调用 configure_logging（所有运行模式）。"""
        import inspect
        import webui.app as app_module
        source = inspect.getsource(app_module.create_app)
        self.assertIn("configure_logging()", source)
        self.assertIn('app.config.get("TESTING")', source)


if __name__ == "__main__":
    unittest.main()
