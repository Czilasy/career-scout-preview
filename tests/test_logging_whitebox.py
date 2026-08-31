"""白箱聚焦测试：关键异常路径必须在 career-scout.log 留痕（033-log-whitebox）。

验证 in-process 通用异常路径在日志中留下异常类型与现场，而非仅"抓取执行失败"。
"""

import logging
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


if __name__ == "__main__":
    unittest.main()
