"""日志读取 API 测试（022-jd-stall-guard US4，T016）。

覆盖：读尾部 / 分页取更早 / 轮询偏移 / 轮转后切换新文件 / 会话令牌
保护 / 文件不存在空态。日志文件在临时目录，经 app.config 注入
CAREER_SCOUT_LOG_DIR，不污染真实用户日志目录。
"""

import pathlib
import sys
import tempfile
import unittest

from webui.app import create_app


def _write_log(log_dir, lines):
    path = pathlib.Path(log_dir) / "career-scout.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class LogApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.log_dir = self.root / "logs"
        self.log_dir.mkdir(exist_ok=True)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / "results"),
            "DB_PATH": str(self.root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
            "CAREER_SCOUT_LOG_DIR": str(self.log_dir),
        })
        self.client = self.app.test_client()
        self.token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token

    def tearDown(self):
        self.temp.cleanup()

    def test_tail_returns_last_n_lines_oldest_first(self):
        _write_log(self.log_dir, [f"line{i}" for i in range(1, 11)])
        resp = self.client.get("/api/logs?tail=3")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["lines"], ["line8", "line9", "line10"])
        self.assertEqual(data["start"], 8)
        self.assertEqual(data["end"], 10)
        self.assertEqual(data["total"], 10)

    def test_offset_paginates_earlier_lines(self):
        _write_log(self.log_dir, [f"line{i}" for i in range(1, 11)])
        resp = self.client.get("/api/logs?offset=8&tail=3")
        data = resp.get_json()
        self.assertEqual(data["lines"], ["line5", "line6", "line7"])
        self.assertEqual(data["start"], 5)
        self.assertEqual(data["end"], 7)

    def test_since_returns_only_new_lines(self):
        _write_log(self.log_dir, [f"line{i}" for i in range(1, 11)])
        first = self.client.get("/api/logs?tail=5").get_json()
        self.assertEqual(first["end"], 10)
        _write_log(self.log_dir, [f"line{i}" for i in range(1, 13)])
        resp = self.client.get(f"/api/logs?since={first['end']}")
        data = resp.get_json()
        self.assertEqual(data["lines"], ["line11", "line12"])
        self.assertEqual(data["start"], 11)
        self.assertEqual(data["end"], 12)

    def test_rotation_detected_and_switches_to_new_file(self):
        _write_log(self.log_dir, [f"line{i}" for i in range(1, 6)])
        first = self.client.get("/api/logs?tail=5").get_json()
        identity = first["identity"]
        self.assertTrue(identity)
        # 模拟轮转：原文件被新文件替换（内容不同 → 身份变化）
        _write_log(self.log_dir, ["fresh1", "fresh2", "fresh3"])
        resp = self.client.get(f"/api/logs?tail=5&identity={identity}")
        data = resp.get_json()
        self.assertTrue(data["rotated"])
        self.assertEqual(data["lines"], ["fresh1", "fresh2", "fresh3"])

    def test_missing_file_returns_empty_state(self):
        resp = self.client.get("/api/logs?tail=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["lines"], [])
        self.assertEqual(data["total"], 0)
        self.assertTrue(data["empty"])

    def test_requires_session_token(self):
        resp = self.app.test_client().get("/api/logs")
        self.assertEqual(resp.status_code, 403)

    def test_clamps_tail_to_500(self):
        _write_log(self.log_dir, [f"line{i}" for i in range(1, 520)])
        data = self.client.get("/api/logs?tail=9999").get_json()
        self.assertLessEqual(len(data["lines"]), 500)


if __name__ == "__main__":
    unittest.main()
