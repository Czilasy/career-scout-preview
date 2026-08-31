"""环境检查（浏览器双探测 + collect_check_items + /api/env-check）测试。

覆盖：
- detect_chromium_browsers：Chrome/Edge 双链探测与缺失兜底
- get_default_chrome_path：Chrome 优先
- collect_check_items：结构化检查项，CLI 与 Web 共用
- /api/env-check：三组分组返回
"""

import importlib.util
import pathlib
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock
from scripts.boss import constants as boss_constants
from scripts.boss import login, runtime, smoke

import requests as real_requests

SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "boss_cdp_raw.py"


def load_module():
    sys.modules.setdefault("websocket", mock.Mock())
    sys.modules.setdefault("requests", mock.Mock())
    spec = importlib.util.spec_from_file_location("boss_cdp_raw", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BrowserDetectionTests(unittest.TestCase):
    """D1: 浏览器双探测（Chrome + Edge，都找到优先 Chrome）。"""

    ENV_BOTH = {
        "LOCALAPPDATA": r"C:\Users\demo\AppData\Local",
        "PROGRAMFILES": r"C:\Program Files",
        "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
    }
    CHROME_LOCAL = r"C:\Users\demo\AppData\Local\Google\Chrome\Application\chrome.exe"
    CHROME_PF = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    EDGE_PF = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    EDGE_LOCAL = r"C:\Users\demo\AppData\Local\Microsoft\Edge\Application\msedge.exe"

    def _within_windows_env(self, module, exists_map):
        def exists(path):
            return exists_map.get(path, False)
        stack = ExitStack()
        stack.enter_context(mock.patch.object(module.platform, "system", return_value="Windows"))
        stack.enter_context(mock.patch.dict(module.os.environ, self.ENV_BOTH, clear=False))
        stack.enter_context(mock.patch.object(module.os.path, "exists", side_effect=exists))
        return stack

    def _detect(self, module, exists_map):
        with self._within_windows_env(module, exists_map):
            return module.detect_chromium_browsers()

    def _default_path(self, module, exists_map):
        with self._within_windows_env(module, exists_map):
            return module.get_default_chrome_path()

    def test_detects_both_browsers(self):
        module = load_module()
        found = self._detect(module, {
            self.CHROME_LOCAL: True, self.EDGE_PF: True,
        })
        self.assertEqual(found, {"chrome": self.CHROME_LOCAL, "edge": self.EDGE_PF})

    def test_chrome_only(self):
        module = load_module()
        found = self._detect(module, {self.CHROME_PF: True})
        self.assertEqual(found, {"chrome": self.CHROME_PF, "edge": None})

    def test_edge_only(self):
        module = load_module()
        found = self._detect(module, {self.EDGE_LOCAL: True})
        self.assertEqual(found, {"chrome": None, "edge": self.EDGE_LOCAL})

    def test_neither_found(self):
        module = load_module()
        found = self._detect(module, {})
        self.assertEqual(found, {"chrome": None, "edge": None})

    def test_macos_both(self):
        module = load_module()
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        edge = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        with mock.patch.object(module.platform, "system", return_value="Darwin"), \
                mock.patch.object(module.os.path, "exists", side_effect=lambda p: p in (chrome, edge)):
            found = module.detect_chromium_browsers()
        self.assertEqual(found, {"chrome": chrome, "edge": edge})

    def test_default_path_prefers_chrome_over_edge(self):
        module = load_module()
        path = self._default_path(module, {
            self.CHROME_LOCAL: True, self.EDGE_LOCAL: True,
        })
        self.assertEqual(path, self.CHROME_LOCAL)

    def test_default_path_falls_back_to_edge(self):
        module = load_module()
        path = self._default_path(module, {self.EDGE_LOCAL: True})
        self.assertEqual(path, self.EDGE_LOCAL)

    def test_default_path_none_when_missing(self):
        module = load_module()
        path = self._default_path(module, {})
        self.assertIsNone(path)

    def test_not_found_hint_message(self):
        module = load_module()
        self.assertIn("Chrome", module.BROWSER_NOT_FOUND_HINT)
        self.assertIn("Edge", module.BROWSER_NOT_FOUND_HINT)


class CollectCheckItemsTests(unittest.TestCase):
    """D5: run_check 重构后，检查逻辑与终端打印分离。"""

    def _check(self, module, *, cdp_ok=True, logged_in=True, deps_ok=True):
        class FakeRequests:
            ConnectionError = real_requests.ConnectionError
            Timeout = real_requests.Timeout

            @staticmethod
            def get(url, timeout=5):
                if cdp_ok:
                    class _Resp:
                        def json(self):
                            return {"Browser": "Chrome/130.0"}
                    return _Resp()
                raise real_requests.ConnectionError("no")

        patchers = [
            mock.patch.object(runtime, "require_runtime_dependencies", return_value=deps_ok),
            mock.patch.object(runtime, "requests", FakeRequests),
            mock.patch.object(boss_constants, "detect_chromium_browsers",
                              return_value={"chrome": "C:/chrome.exe", "edge": None}),
            mock.patch.object(login, "check_login_state_tri",
                              return_value="logged_in" if logged_in else "not_logged_in"),
        ]
        for patcher in patchers:
            patcher.start()
        try:
            return module.collect_check_items(cdp_port=9333)
        finally:
            for patcher in patchers:
                patcher.stop()

    def test_all_ok_returns_structured_items(self):
        module = load_module()
        items, all_pass = self._check(module)
        self.assertTrue(all_pass)
        self.assertEqual([item["id"] for item in items],
                         ["browsers", "deps", "cdp", "boss_login"])
        for item in items:
            self.assertIn(item["status"], ("ok", "fail", "skip"))
            self.assertIn("name", item)
            self.assertIn("detail", item)
            self.assertIn("fix", item)
        self.assertEqual(items[0]["detail"], "找到 Chrome ✅")

    def test_cdp_down_fails_and_skips_login(self):
        module = load_module()
        items, all_pass = self._check(module, cdp_ok=False)
        self.assertFalse(all_pass)
        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id["cdp"]["status"], "fail")
        # cdp 项只读展示：不再提供一键启动按钮（启动任务时自动拉起浏览器）
        self.assertIsNone(by_id["cdp"]["fix"])
        self.assertEqual(by_id["boss_login"]["status"], "skip")

    def test_not_logged_in_fails_with_guidance(self):
        module = load_module()
        items, all_pass = self._check(module, logged_in=False)
        self.assertFalse(all_pass)
        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id["boss_login"]["status"], "fail")
        self.assertIn("登录", by_id["boss_login"]["fix"])

    def test_missing_browser_fails_with_hint(self):
        load_module()
        module2 = load_module()
        with mock.patch.object(runtime, "require_runtime_dependencies", return_value=True), \
                mock.patch.object(boss_constants, "detect_chromium_browsers",
                                  return_value={"chrome": None, "edge": None}), \
                mock.patch.object(runtime, "requests", mock.Mock()), \
                mock.patch.object(login, "check_login_state_tri", return_value="logged_in"):
            items, all_pass = module2.collect_check_items(cdp_port=9333)
        self.assertFalse(all_pass)
        self.assertEqual(items[0]["status"], "fail")
        self.assertIn("Edge", items[0]["fix"])

    def test_run_check_prints_items_and_returns_exit_code(self):
        module = load_module()
        with mock.patch.object(smoke, "collect_check_items",
                               return_value=([{
                                   "id": "browsers", "name": "Chromium 浏览器",
                                   "status": "ok", "detail": "找到 Chrome ✅", "fix": None,
                               }], True)):
            with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                rc = module.run_check(cdp_port=9333)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("Chromium 浏览器", text)
        self.assertIn("所有检查通过", text)


class EnvCheckApiTests(unittest.TestCase):
    """D5: GET /api/env-check 返回三组结构化检查项。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        from webui.app import create_app
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token

    def tearDown(self):
        self.temp.cleanup()

    def test_env_check_returns_grouped_items(self):
        with mock.patch("scripts.boss.smoke.collect_check_items", return_value=([
            {"id": "browsers", "name": "Chromium 浏览器", "status": "ok",
             "detail": "找到 Chrome ✅", "fix": None},
            {"id": "deps", "name": "Python 依赖", "status": "ok",
             "detail": "requests / websocket 可导入", "fix": None},
            {"id": "cdp", "name": "专用浏览器已启动", "status": "fail",
             "detail": "无法连接 127.0.0.1:9222", "fix": "启动专用浏览器"},
            {"id": "boss_login", "name": "BOSS 登录状态", "status": "skip",
             "detail": "跳过", "fix": None},
        ], False)):
            payload = self.client.get("/api/env-check").get_json()

        self.assertTrue(payload["ok"])
        groups = {group["id"]: group for group in payload["groups"]}
        self.assertEqual(set(groups), {"browser", "ai", "local"})
        self.assertEqual(
            [item["id"] for item in groups["browser"]["items"]],
            ["browsers", "cdp", "boss_login"],
        )
        self.assertEqual(groups["ai"]["items"][0]["id"], "ai_key")
        self.assertEqual(
            [item["id"] for item in groups["local"]["items"]],
            ["data_dir", "webui_dist", "deps"],
        )
        self.assertIsInstance(payload["checked_at"], int)


class ZhilianLoginProbeTests(unittest.TestCase):
    """D4: 智联登录态 DOM marker 探测（fixture 兜底，真实冒烟结论见代码注释）。

    031 B6 起探测实现在 ``scripts/zhilian/search.py``：patch 必须落域模块——
    ``scripts/zhilian_cdp_raw.py`` 已改为只做读取代理的兼容门面，patch
    门面不会影响域内互调（同 boss B5）。
    """

    def _probe(self, body="", url="https://www.zhaopin.com/sou/", loaded=True):
        from scripts.zhilian import search
        with mock.patch.object(search, "_connect", return_value=object()), \
                mock.patch.object(search, "_navigate"), \
                mock.patch.object(search, "_wait_expression", return_value=loaded), \
                mock.patch.object(search, "_evaluate", side_effect=[body, url]):
            return search.check_login_state_tri(9223)

    def test_login_marker_means_not_logged_in(self):
        self.assertEqual(
            self._probe(body="职位列表\n请登录后查看完整职位信息"),
            "not_logged_in",
        )

    def test_passport_redirect_means_not_logged_in(self):
        self.assertEqual(
            self._probe(body="正在跳转...", url="https://passport.zhaopin.com/login"),
            "not_logged_in",
        )

    def test_verify_marker_means_restricted(self):
        self.assertEqual(
            self._probe(body="请完成验证以确认安全性\nProtected by Tencent Cloud EdgeOne"),
            "restricted",
        )

    def test_rate_marker_means_restricted(self):
        self.assertEqual(
            self._probe(body="访问过于频繁，请稍后再试"),
            "restricted",
        )

    def test_no_marker_means_logged_in(self):
        self.assertEqual(
            self._probe(body="Java 开发工程师\n上海\n20-40K"),
            "logged_in",
        )

    def test_cdp_failure_means_unknown(self):
        from scripts.zhilian import search
        with mock.patch.object(search, "_connect", side_effect=RuntimeError("down")):
            self.assertEqual(search.check_login_state_tri(9223), "unknown")

    def test_page_not_loaded_means_unknown(self):
        self.assertEqual(self._probe(body="", loaded=False), "unknown")

    def test_api_city_code_nationwide_is_empty(self):
        """全国（jl0）映射为空串：fe-api 不传城市字段才是全国搜索。"""
        from scripts.zhilian import search
        self.assertEqual(search._api_city_code("jl0"), "")
        self.assertEqual(search._api_city_code("779"), "779")

    def test_search_expression_omits_city_for_nationwide(self):
        from scripts.zhilian import search
        expr = search._search_fetch_expression("Java 开发", "", 1)
        self.assertNotIn("S_SOU_WORK_CITY", expr)

    def test_search_expression_includes_specific_city(self):
        from scripts.zhilian import search
        expr = search._search_fetch_expression("Java 开发", "779", 1)
        self.assertIn("S_SOU_WORK_CITY", expr)
        self.assertIn("779", expr)


if __name__ == "__main__":
    unittest.main()