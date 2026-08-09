"""Static regression checks for tools/start.bat safety rules."""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
START_BAT = ROOT / "tools" / "start.bat"


class StartBatTests(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(START_BAT.is_file(), "tools/start.bat 不存在")

    def test_no_blind_taskkill_of_port_listener(self):
        text = START_BAT.read_text(encoding="utf-8")
        self.assertNotIn("taskkill", text, "启动脚本不得对端口监听进程使用 taskkill")

    def test_matches_career_scout_command_line_only(self):
        text = START_BAT.read_text(encoding="utf-8")
        self.assertIn(r"webui[\\/]app\.py", text)
        self.assertIn("CareerScout", text)
        self.assertIn("Win32_Process", text)
        self.assertIn("Stop-Process", text)

    def test_waits_for_health_check_and_timeout_exit(self):
        text = START_BAT.read_text(encoding="utf-8")
        self.assertIn("/api/session", text)
        self.assertIn("未就绪", text)
        self.assertIn("exit /b 1", text)

    def test_unrelated_process_gets_port_occupied_message(self):
        text = START_BAT.read_text(encoding="utf-8")
        self.assertIn("其它程序占用", text)
        self.assertIn("未关闭任何进程", text)


if __name__ == "__main__":
    unittest.main()
