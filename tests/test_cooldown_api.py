"""风控冷却 API（D6）集成测试：提交拒绝 / 连坐提醒 / 手动解除 / env-check 展示。

覆盖：
- POST /api/tasks：同账号冷却 → 409 account_in_cooldown
- POST /api/tasks：其他账号冷却 → 202 + warning（连坐提醒，不拒绝）
- POST /api/cooldown/clear：手动解除后同账号可提交
- GET /api/env-check：返回 cooldowns / active_account
"""

import pathlib
import tempfile
import unittest

from webui import cooldown as cd


class CooldownApiTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        # 登录态缓存路径隔离：D3 缓存优先会读 login-state.json，测试不得
        # 命中 ~/.career-scout 下的真实数据，也不得向真实文件写入。
        from scripts import login_state_cache as cache
        cache.set_login_state_path(root / "state" / "login-state.json")
        from webui.app import create_app
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": __import__("sys").executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token

    def tearDown(self):
        cd.reset_cooldown_path()
        from scripts.login_state_cache import reset_login_state_path
        reset_login_state_path()
        self.temp.cleanup()

    def _submit(self, **overrides):
        body = {
            "keyword": "python",
            "city": "101010100",
            "pages": 1,
            "format": "json",
        }
        body.update(overrides)
        return self.client.post("/api/tasks", json=body)

    def test_same_account_cooldown_rejects_submit(self):
        cd.mark_cooldown("a", "boss", "操作频繁，请稍后再试", seconds=3600)
        resp = self._submit()
        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "account_in_cooldown")
        self.assertGreater(payload["remaining_seconds"], 0)
        self.assertIn("建议等待至", payload["user_message"])

    def test_other_account_cooldown_warns_but_submits(self):
        cd.mark_cooldown("b", "boss", "操作频繁，请稍后再试", seconds=3600)
        resp = self._submit()
        self.assertEqual(resp.status_code, 202)
        payload = resp.get_json()
        self.assertIn("task", payload)
        warning = payload["warning"]
        self.assertEqual(warning["code"], "other_account_cooldown")
        self.assertEqual(warning["cooldowns"][0]["account_id"], "b")

    def test_clear_cooldown_allows_submit_again(self):
        cd.mark_cooldown("a", "boss", "操作频繁", seconds=3600)
        self.assertEqual(self._submit().status_code, 409)
        resp = self.client.post("/api/cooldown/clear", json={"account_id": "a", "platform": "boss"})
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(self._submit().status_code, 202)

    def test_clear_requires_account_id(self):
        resp = self.client.post("/api/cooldown/clear", json={})
        self.assertEqual(resp.status_code, 400)

    def test_clear_does_not_touch_login_cache(self):
        """解除冷却不改变登录态缓存（D6 明确要求）。"""
        from scripts import login_state_cache as cache
        cache.write_login_state("a", "boss", "restricted")
        cd.mark_cooldown("a", "boss", "x", seconds=3600)
        self.client.post("/api/cooldown/clear", json={"account_id": "a", "platform": "boss"})
        self.assertEqual(cache.read_cached_state("a", "boss"), "restricted")

    def test_env_check_reports_cooldowns(self):
        cd.mark_cooldown("b", "zhilian", "2099-01-01 10:00 后可重试", seconds=3600)
        from unittest import mock
        with mock.patch("webui.app.boss.collect_check_items", return_value=([
            {"id": "browsers", "name": "Chromium 浏览器", "status": "ok",
             "detail": "找到 Chrome ✅", "fix": None},
            {"id": "deps", "name": "Python 依赖", "status": "ok",
             "detail": "ok", "fix": None},
            {"id": "cdp", "name": "专用浏览器已启动", "status": "skip",
             "detail": "跳过", "fix": None},
            {"id": "boss_login", "name": "BOSS 登录状态", "status": "skip",
             "detail": "跳过", "fix": None},
        ], False)):
            payload = self.client.get("/api/env-check").get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["active_account"], "a")
        cooldowns = payload["cooldowns"]
        self.assertEqual(len(cooldowns), 1)
        self.assertEqual(cooldowns[0]["account_id"], "b")
        self.assertEqual(cooldowns[0]["platform"], "zhilian")
        self.assertIn("until_text", cooldowns[0])

    def test_env_check_boss_login_reads_cache(self):
        """D5：BOSS 登录状态优先读激活账号缓存，命中后不再真实探测。"""
        from scripts import login_state_cache as cache
        from unittest import mock
        cache.write_login_state("a", "boss", "logged_in")
        with mock.patch("webui.app.boss.collect_check_items") as collect:
            collect.return_value = ([
                {"id": "browsers", "name": "Chromium 浏览器", "status": "ok",
                 "detail": "找到 Chrome ✅", "fix": None},
                {"id": "deps", "name": "Python 依赖", "status": "ok",
                 "detail": "ok", "fix": None},
                {"id": "cdp", "name": "专用浏览器已启动", "status": "ok",
                 "detail": "就绪", "fix": None},
                {"id": "boss_login", "name": "BOSS 登录状态", "status": "fail",
                 "detail": "未登录 — 真实探测结果", "fix": None},
            ], False)
            payload = self.client.get("/api/env-check").get_json()
        groups = {g["id"]: g for g in payload["groups"]}
        boss_item = groups["browser"]["items"][2]
        self.assertEqual(boss_item["status"], "ok")
        self.assertIn("缓存", boss_item["detail"])


if __name__ == "__main__":
    unittest.main()