import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui.logging_setup import (
    bind_task_context,
    configure_logging,
    get_logger,
    redact,
)
from webui.logging_setup import is_configured


class LoggingSetupTests(unittest.TestCase):
    def tearDown(self):
        logger = logging.getLogger("career_scout")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    def _close_logger(self):
        self.tearDown()

    def test_configure_logging_writes_rotating_file_without_sensitive_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            logger = get_logger("test")
            logger.error(
                "failure token=sk-abcdef1234567890 bearer=abc.def.ghi "
                "api_key=supersecret"
            )
            log_path = Path(tmp) / "career-scout.log"
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("failure", content)
            self.assertIn("[REDACTED]", content)
            self.assertNotIn("sk-abcdef1234567890", content)
            self.assertNotIn("supersecret", content)
            self._close_logger()

    def test_task_context_is_bound_to_log_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            logger = get_logger("test")
            with bind_task_context("task-1", "corr-1"):
                logger.error("boom")
            log_path = Path(tmp) / "career-scout.log"
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("task=task-1", content)
            self.assertIn("corr=corr-1", content)
            self._close_logger()

    def test_redact_removes_common_credential_shapes(self):
        text = "key=sk-abcdef1234567890 Authorization: Bearer abc.def"
        result = redact(text)
        self.assertNotIn("sk-abcdef1234567890", result)
        self.assertNotIn("abc.def", result)
        self.assertIn("[REDACTED]", result)

    def test_is_configured_reflects_actual_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            self.assertTrue(is_configured())
            self._close_logger()
            self.assertFalse(is_configured())

    def test_get_logger_lazily_configures_in_test_context(self):
        """白箱：未配置时 get_logger 自动开账本；测试上下文写系统临时目录。"""
        self._close_logger()
        logger = get_logger("lazy_test")
        self.assertTrue(is_configured())
        logger.warning("lazy-init-marker")
        temp_dir = Path(tempfile.gettempdir()) / "career-scout-test-logs"
        log_path = temp_dir / "career-scout.log"
        self.assertTrue(log_path.is_file(), f"lazy init should write {log_path}")
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("lazy-init-marker", content)
        self._close_logger()
        for p in temp_dir.glob("career-scout.log*"):
            try:
                p.unlink()
            except OSError:
                pass

    def test_lazy_init_respects_existing_configuration(self):
        """已配置后 get_logger 不重复配置、不覆盖既有 handler 目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            get_logger("lazy_existing")
            handlers = logging.getLogger("career_scout").handlers
            self.assertEqual(len(handlers), 1)
            base = getattr(handlers[0], "baseFilename", "")
            self.assertIn("career-scout.log", str(base))
            self.assertIn(str(tmp), str(base))
            self._close_logger()

    @unittest.skipUnless(os.name != "nt", "Windows 下日志文件被 handler 占用句柄，无法模拟删除（Unix 专属场景）")
    def test_safe_handler_rebuilds_deleted_log_file(self):
        """日志文件被外部删除后，下一次写入自动重建（不崩溃）。"""
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            logger = get_logger("handler_test")
            log_path = Path(tmp) / "career-scout.log"
            logger.warning("before-delete")
            self.assertTrue(log_path.is_file())
            log_path.unlink()
            logger.warning("after-delete")
            self.assertTrue(log_path.is_file(), "deleted log file should be rebuilt")
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("after-delete", content)
            self._close_logger()

    def test_default_level_reads_from_env(self):
        """CAREER_SCOUT_LOG_LEVEL 控制默认级别：INFO 时 debug 不落盘。"""
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"CAREER_SCOUT_LOG_LEVEL": "INFO"}, clear=False):
            configure_logging(tmp, force=True)
            logger = get_logger("level_test")
            logger.debug("debug-hidden")
            logger.info("info-visible")
            content = (Path(tmp) / "career-scout.log").read_text(encoding="utf-8")
            self.assertIn("info-visible", content)
            self.assertNotIn("debug-hidden", content)
            self._close_logger()

    def test_safe_handler_rotates_and_keeps_backups(self):
        """小容量触发轮转，产生备份文件且不崩溃。"""
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True, max_bytes=200, backup_count=2)
            logger = get_logger("rotate_test")
            for i in range(30):
                logger.warning("rotate-line-%02d %s", i, "x" * 80)
            backups = sorted(Path(tmp).glob("career-scout.log.*"))
            self.assertGreaterEqual(len(backups), 1)
            self._close_logger()


if __name__ == "__main__":
    unittest.main()
