import logging
import tempfile
import unittest
from pathlib import Path

from webui.logging_setup import (
    bind_task_context,
    configure_logging,
    get_logger,
    redact,
)


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


if __name__ == "__main__":
    unittest.main()
