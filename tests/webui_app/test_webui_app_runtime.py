"""webui.app 运行时与任务执行合同测试（027 自 tests/test_webui_app.py 拆出）。"""
import json
import pathlib
import sys
import tempfile
import threading
import uuid
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from webui.app import create_app


class ChromeAccountProfileSwitchTests(unittest.TestCase):
    """账号切换时，端口上旧账号的 Chrome 必须被替换而不是复用。"""

    def test_ensure_chrome_ready_replaces_wrong_profile(self):
        from webui import pipeline_exec
        from webui import pipeline_exec_chrome
        launched = mock.Mock()
        launched.poll.return_value = None
        # 已知账号簿必须封闭：默认实现读真实用户账号簿文件（~/.career-scout），
        # 在非主检出路径的 worktree 里与 BROWSER_ACCOUNTS 默认值不一致导致误判
        hermetic_accounts = {
            aid: {"profile_dir": str(info["profile_dir"])}
            for aid, info in pipeline_exec.BROWSER_ACCOUNTS.items()
        }
        with mock.patch.object(
            pipeline_exec_chrome, "load_browser_accounts",
            return_value=hermetic_accounts,
        ), mock.patch.object(
            pipeline_exec.boss, "is_cdp_ready", side_effect=[True, True],
        ), mock.patch.object(
            pipeline_exec.boss, "cdp_port_uses_profile", return_value=False,
        ) as uses, mock.patch.object(
            pipeline_exec.boss, "chrome_user_data_dirs_for_cdp_port",
            return_value=[pipeline_exec.BROWSER_ACCOUNTS["b"]["profile_dir"]],
        ), mock.patch(
            "webui.pipeline_exec_chrome.load_browser_accounts",
            return_value=dict(pipeline_exec.BROWSER_ACCOUNTS),
        ), mock.patch.object(
            pipeline_exec.boss, "close_cdp_chrome",
        ) as close, mock.patch.object(
            pipeline_exec.boss, "prepare_cdp_profile",
            return_value={"path": "C:/profiles/account-b"},
        ), mock.patch.object(
            pipeline_exec.boss, "stop_cdp_chrome",
        ), mock.patch.object(
            pipeline_exec.boss, "launch_chrome", return_value=launched,
        ):
            ok, _msg = pipeline_exec.ensure_chrome_ready(9333)
        self.assertTrue(ok)
        uses.assert_called_once()
        close.assert_called_once()

    def test_ensure_chrome_ready_refuses_unknown_profile(self):
        """端口被非 A/B 的 Chrome 占用时禁止自动关闭，避免误伤主 Chrome。"""
        from webui import pipeline_exec
        with mock.patch.object(
            pipeline_exec.boss, "is_cdp_ready", return_value=True,
        ), mock.patch.object(
            pipeline_exec.boss, "cdp_port_uses_profile", return_value=False,
        ), mock.patch.object(
            pipeline_exec.boss, "chrome_user_data_dirs_for_cdp_port",
            return_value=["C:/unknown/profile"],
        ), mock.patch.object(
            pipeline_exec.boss, "close_cdp_chrome",
        ) as close:
            ok, msg = pipeline_exec.ensure_chrome_ready(9333)
        self.assertFalse(ok)
        self.assertIn("避免误关", msg)
        close.assert_not_called()

    def test_ensure_chrome_ready_minimizes_only_when_requested(self):
        """R6: 实际启动 Chrome 且 minimize_after_launch=True 时最小化窗口；默认不最小化。"""
        from webui import pipeline_exec
        launched = mock.Mock()
        launched.poll.return_value = None
        base = [
            mock.patch.object(
                pipeline_exec.boss, "is_cdp_ready", side_effect=[False, True],
            ),
            mock.patch.object(
                pipeline_exec.boss, "prepare_cdp_profile",
                return_value={"path": "C:/profiles/r6"},
            ),
            mock.patch.object(pipeline_exec.boss, "stop_cdp_chrome"),
            mock.patch.object(
                pipeline_exec.boss, "launch_chrome", return_value=launched,
            ),
        ]
        # 默认参数：不最小化（登录空间等调用方需要窗口在前台）
        with base[0], base[1], base[2], base[3], mock.patch.object(
            pipeline_exec.boss, "minimize_chrome_window",
        ) as minimize:
            ok, _msg = pipeline_exec.ensure_chrome_ready(9444)
        self.assertTrue(ok)
        minimize.assert_not_called()
        # 任务路径：启动后立即最小化
        with base[0], base[1], base[2], base[3], mock.patch.object(
            pipeline_exec.boss, "minimize_chrome_window",
        ) as minimize:
            ok, _msg = pipeline_exec.ensure_chrome_ready(
                9444, minimize_after_launch=True,
            )
        self.assertTrue(ok)
        minimize.assert_called_once_with(9444)


class ChromeWindowMinimizeTests(unittest.TestCase):
    """R6: CDP Browser.setWindowBounds 最小化抓取浏览器窗口。"""

    def test_minimize_chrome_window_sends_browser_commands(self):
        from scripts import boss_cdp_raw as boss
        session = mock.Mock()
        session.send.side_effect = [
            {"id": 1, "result": {"windowId": 42}},
            {"id": 2, "result": {}},
        ]
        ok = boss.minimize_chrome_window(
            9222,
            session_factory=lambda _port: session,
            target_id_provider=lambda _port: "target-1",
        )
        self.assertTrue(ok)
        session.send.assert_any_call(
            "Browser.getWindowForTarget", {"targetId": "target-1"}, timeout=10,
        )
        session.send.assert_any_call("Browser.setWindowBounds", {
            "windowId": 42,
            "bounds": {"windowState": "minimized"},
        }, timeout=10)
        session.close.assert_called_once()

    def test_minimize_chrome_window_without_target_returns_false(self):
        from scripts import boss_cdp_raw as boss
        ok = boss.minimize_chrome_window(
            9222,
            session_factory=mock.Mock(),
            target_id_provider=lambda _port: None,
        )
        self.assertFalse(ok)

    def test_minimize_chrome_window_failure_is_silent(self):
        from scripts import boss_cdp_raw as boss
        session = mock.Mock()
        session.send.side_effect = RuntimeError("cdp closed")
        ok = boss.minimize_chrome_window(
            9222,
            session_factory=lambda _port: session,
            target_id_provider=lambda _port: "target-1",
        )
        self.assertFalse(ok)


class BrowserAccountApiTests(unittest.TestCase):
    """顶部栏浏览器账号管理接口。"""

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
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()
        from webui.pipeline_exec import reset_browser_accounts_path
        reset_browser_accounts_path()

    def test_list_add_activate_custom_account(self):
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(len(data["accounts"]), 2)
        self.assertEqual(data["active_account"], "a")
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        account = resp.get_json()["account"]
        self.assertFalse(account["builtin"])
        self.assertIn(".chrome-profiles", account["profile_dir"])
        activated = self.client.post(
            f"/api/browser-accounts/{account['id']}/activate"
        ).get_json()
        self.assertEqual(activated["active_account"], account["id"])
        settings = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(settings["settings"]["browser_account"], account["id"])

    def test_duplicate_name_rejected_and_delete_custom(self):
        self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        dup = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        self.assertEqual(dup.status_code, 422)
        builtin = self.client.delete("/api/browser-accounts/a")
        self.assertEqual(builtin.status_code, 409)
        data = self.client.get("/api/browser-accounts").get_json()
        custom = next(a for a in data["accounts"] if a["id"] not in ("a", "b"))
        self.client.post(f"/api/browser-accounts/{custom['id']}/activate")
        deleted = self.client.delete(f"/api/browser-accounts/{custom['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(data["active_account"], "a")
        self.assertNotIn(custom["id"], [a["id"] for a in data["accounts"]])

    def test_legacy_b_account_is_deletable_and_not_reseeded(self):
        data = self.client.get("/api/browser-accounts").get_json()
        b = next(a for a in data["accounts"] if a["id"] == "b")
        self.assertFalse(b["builtin"])
        deleted = self.client.delete("/api/browser-accounts/b")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertNotIn("b", [a["id"] for a in data["accounts"]])

    def test_pool_partial_update_and_clamp(self):
        """Spec 038 B091：账号池配置端点 PUT /api/browser-accounts/<id>/pool。

        FR-021：旧 /roles 端点已弃用，全删不兼容迁移。
        新端点支持部分更新（未传字段保持原值）；配额越界 clamp 到 [min, max]。
        """
        data = self.client.get("/api/browser-accounts").get_json()
        # 默认每账号都进池、默认全选、默认配额取中值
        self.assertTrue(all(a["pool"]["selected"] for a in data["accounts"]))
        self.assertTrue(all(a["pool"]["r1_quota"] == 25 for a in data["accounts"]))
        self.assertTrue(all(a["rate_limited"] is False for a in data["accounts"]))
        # 旧 roles 字段不再投影
        self.assertTrue(all("roles" not in a for a in data["accounts"]))

        # 取消 b 选中
        resp = self.client.put("/api/browser-accounts/b/pool",
                               json={"selected": False})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(resp.get_json()["pool"]["selected"])
        # r1_quota 未传 → 保留默认 25
        self.assertEqual(resp.get_json()["pool"]["r1_quota"], 25)
        data = self.client.get("/api/browser-accounts").get_json()
        b = next(a for a in data["accounts"] if a["id"] == "b")
        self.assertFalse(b["pool"]["selected"])

        # 改 b 的 r1_quota=10
        resp = self.client.put("/api/browser-accounts/b/pool",
                               json={"r1_quota": 10})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["pool"]["r1_quota"], 10)
        # selected 未传 → 仍为 False（保留原值）
        self.assertFalse(resp.get_json()["pool"]["selected"])

        # 越界 clamp：r1_quota=9999 → 50；r2_quota=-5 → 1
        resp = self.client.put("/api/browser-accounts/b/pool",
                               json={"r1_quota": 9999, "r2_quota": -5})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["pool"]["r1_quota"], 50)
        self.assertEqual(resp.get_json()["pool"]["r2_quota"], 1)

        # 校验：非整数 → 422；不存在账号 → 404
        bad = self.client.put("/api/browser-accounts/b/pool",
                              json={"r1_quota": "not-int"})
        self.assertEqual(bad.status_code, 422)
        missing = self.client.put("/api/browser-accounts/nope/pool",
                                  json={"selected": True})
        self.assertEqual(missing.status_code, 404)

    def test_clear_rate_limited_marker_persists_without_changing_pool(self):
        from webui.pipeline_exec import load_browser_accounts, save_browser_accounts

        path = self.app.config["BROWSER_ACCOUNTS_PATH"]
        accounts = load_browser_accounts(path)
        accounts["b"]["rate_limited"] = True
        original_pool = dict(accounts["b"]["pool"])
        save_browser_accounts(accounts, path)

        cleared = self.client.delete("/api/browser-accounts/b/rate-limited")
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        self.assertEqual(cleared.get_json(), {
            "ok": True, "account_id": "b", "rate_limited": False,
        })

        updated = load_browser_accounts(path)["b"]
        self.assertFalse(updated["rate_limited"])
        self.assertEqual(updated["pool"], original_pool)
        self.assertEqual(self.client.delete(
            "/api/browser-accounts/not-found/rate-limited").status_code, 404)

    def _seed_paused_run(self, account="b", run_id="busy-account-run", platform="boss"):
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"browser_account": account, "platform": platform},
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused")
        return run_id

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    @mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=True)
    def test_paused_open_other_account_allowed_and_closes_old_browser(
            self, mock_close, _ready):
        self._seed_paused_run(account="b")
        order = []
        mock_close.side_effect = lambda port: order.append(("close", port)) or True
        _ready.side_effect = lambda port: order.append(("ready", port)) or (True, "")
        resp = self.client.post("/api/browser-accounts/a/open")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(order, [("close", 9222), ("ready", 9222)])

    @mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=True)
    def test_paused_activate_allowed_and_closes_old_browser(self, mock_close):
        self._seed_paused_run(account="b")
        resp = self.client.post("/api/browser-accounts/a/activate")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["active_account"], "a")
        mock_close.assert_called_once_with(9222)
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(data["active_account"], "a")

    def test_running_activate_rejected(self):
        self.app.config["PIPELINE_TASKS"]["running-activate"] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
        }
        resp = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_open_browser_allows_paused_task_account(self, _ready):
        self._seed_paused_run(account="b")
        resp = self.client.post("/api/browser-accounts/b/open")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["ok"])
        self.assertIn("登录", resp.get_json()["message"])

    def test_list_exposes_paused_account_lock(self):
        self._seed_paused_run(account="b")
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertTrue(data["busy"])
        self.assertEqual(data["busy_kind"], "paused")
        self.assertEqual(data["locked_account"], "b")

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_open_browser_succeeds_when_idle(self, _ready):
        resp = self.client.post("/api/browser-accounts/a/open")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["ok"])

    def test_list_accounts_returns_platforms_without_profile_dir(self):
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(len(data["accounts"]), 2)
        for account in data["accounts"]:
            self.assertNotIn("profile_dir", account)
            self.assertIn("boss", account["platforms"])
            self.assertIn("zhilian", account["platforms"])
            self.assertEqual(account["platforms"]["boss"]["cdp_port"], 9222)
            self.assertEqual(account["platforms"]["zhilian"]["cdp_port"], 9223)

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_open_zhilian_uses_zhilian_port(self, _ready):
        resp = self.client.post("/api/browser-accounts/b/open", json={"platform": "zhilian"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertIn("智联", resp.get_json()["message"])
        _ready.assert_called_once_with(9223)

    def test_open_unknown_platform_returns_400(self):
        resp = self.client.post("/api/browser-accounts/a/open", json={"platform": "unknown"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error_code"], "platform_validation_failed")

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    @mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=True)
    def test_paused_open_any_platform_for_locked_account(self, mock_close, _ready):
        self._seed_paused_run(account="b", platform="zhilian")
        boss_open = self.client.post("/api/browser-accounts/b/open", json={"platform": "boss"})
        self.assertEqual(boss_open.status_code, 200, boss_open.get_json())
        self.assertEqual(_ready.call_args_list[-1], mock.call(9222))
        zhilian_open = self.client.post("/api/browser-accounts/b/open", json={"platform": "zhilian"})
        self.assertEqual(zhilian_open.status_code, 200, zhilian_open.get_json())
        self.assertEqual(_ready.call_args_list[-1], mock.call(9223))
        self.assertEqual(mock_close.call_count, 2)

    def test_delete_refuses_zhilian_browser_running(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 Z"})
        account = resp.get_json()["account"]
        zhilian_dir = account["profile_dir"].rstrip("/\\") + ".zhilian"
        with mock.patch("scripts.boss.browser.is_cdp_ready", side_effect=[False, True]), \
                mock.patch("scripts.boss.browser.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [] if port == 9222 else [zhilian_dir]):
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 409, deleted.get_json())
        self.assertEqual(deleted.get_json()["error"], "browser_in_use")

    def test_paused_delete_allowed(self):
        self._seed_paused_run(account="b")
        deleted = self.client.delete("/api/browser-accounts/b")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertNotIn("b", [a["id"] for a in data["accounts"]])

    def test_running_delete_rejected(self):
        self.app.config["PIPELINE_TASKS"]["running-delete"] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
        }
        resp = self.client.delete("/api/browser-accounts/b")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    def test_paused_delete_closes_open_target_browser(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 Z"})
        account = resp.get_json()["account"]
        profile_dir = account["profile_dir"]
        self._seed_paused_run(account="b")
        ready_calls = {"n": 0}
        def is_ready(port):
            ready_calls["n"] += 1
            return ready_calls["n"] == 1 and port == 9222
        with mock.patch("scripts.boss.browser.is_cdp_ready", side_effect=is_ready), \
                mock.patch("scripts.boss.browser.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [profile_dir] if port == 9222 else []), \
                mock.patch("webui.pipeline_exec.close_debug_chrome",
                           return_value=True) as mock_close:
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        mock_close.assert_called_once_with(9222)

    def test_paused_delete_close_failure_returns_409(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 Z"})
        account = resp.get_json()["account"]
        profile_dir = account["profile_dir"]
        self._seed_paused_run(account="b")
        with mock.patch("scripts.boss.browser.is_cdp_ready", side_effect=[True, False]), \
                mock.patch("scripts.boss.browser.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [profile_dir] if port == 9222 else []), \
                mock.patch("webui.pipeline_exec.close_debug_chrome",
                           return_value=False):
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 409, deleted.get_json())
        self.assertEqual(deleted.get_json()["error"], "browser_in_use")
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertIn(account["id"], [a["id"] for a in data["accounts"]])

    def test_resolve_browser_account_returns_custom_profile(self):
        from webui import pipeline_exec
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        account = resp.get_json()["account"]
        path = self.app.config["BROWSER_ACCOUNTS_PATH"]
        self.assertEqual(
            pipeline_exec.resolve_browser_account(account["id"], path),
            account["profile_dir"],
        )
        self.assertEqual(pipeline_exec.resolve_browser_account("missing", path), "")

    def test_add_rejects_duplicate_profile_dir(self):
        first = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        profile_dir = first.get_json()["account"]["profile_dir"]
        dup = self.client.post("/api/browser-accounts", json={
            "name": "账号 D", "profile_dir": profile_dir,
        })
        self.assertEqual(dup.status_code, 422, dup.get_json())
        self.assertIn("不能与其他账号重复", dup.get_json()["error"])

    def test_delete_refuses_when_browser_running(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        account = resp.get_json()["account"]
        profile_dir = account["profile_dir"]
        with mock.patch("scripts.boss.browser.is_cdp_ready", side_effect=[True, False]), \
                mock.patch("scripts.boss.browser.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [profile_dir] if port == 9222 else []):
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 409, deleted.get_json())
        self.assertEqual(deleted.get_json()["error"], "browser_in_use")


class AiResumeVerdictSplitTests(unittest.TestCase):
    """续跑时粗筛 kept 不得被当成精筛判定。"""

    def test_split_resume_verdicts_keeps_rough_out_of_fine(self):
        from webui.app import _split_resume_verdicts
        verdicts = {
            "job-kept": {"verdict": "kept", "reason": ""},
            "job-drop": {"verdict": "dropped", "reason": "粗筛移除"},
            "job-match": {"verdict": "match", "reason": "匹配"},
            "job-uncertain": {"verdict": "uncertain", "reason": "待确认"},
        }
        fine, rough = _split_resume_verdicts(verdicts)
        self.assertEqual(set(fine), {"job-match", "job-uncertain"})
        self.assertEqual(set(rough), {"job-kept", "job-drop"})

    def test_resume_dropped_reconstructed_from_raw_jobs(self):
        from webui.app import _resume_dropped_from_verdicts
        raw_jobs = [
            {"job_id": "j1", "title": "前端", "source_url": "https://a/j1.html"},
            {"job_id": "j2", "title": "后端", "source_url": "https://a/j2.html"},
        ]
        verdicts = {
            "j1": {"verdict": "kept"},
            "j2": {"verdict": "dropped", "reason": "经验不符"},
        }
        dropped = _resume_dropped_from_verdicts(raw_jobs, verdicts)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["job_id"], "j2")
        self.assertEqual(dropped[0]["reason"], "经验不符")


class RunSearchAllFailTests(unittest.TestCase):
    """所有组合全失败时 run_search 返回 ok:False。"""

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_all_combos_fail_returns_ok_false(self, _mock_chrome):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        # 构造一个 source，fetch_list 永远失败
        class _FailSource:
            def preflight(self):
                return SourceOutcome.success(safe_log="ok", input_hash="")
            def fetch_list(self, plan_item, *, on_page_completed=None):
                return SourceOutcome.failure(
                    failed_code="source_verification_required",
                    safe_log="list returncode=10 reason=验证码",
                )

        result = run_search(
            {"keyword": "Python", "city": ["上海"]},
            _FailSource(),
            pages=1,
            artifact_dir=self._tmp_dir(),
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("hard_stop"), result)
        self.assertEqual(result.get("hard_stop_code"), "source_verification_required")
        self.assertIn("验证码", result["error"])

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_partial_fail_returns_partial_not_ok(self, _mock_chrome):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        call_count = [0]

        class _MixedSource:
            def preflight(self):
                return SourceOutcome.success(safe_log="ok", input_hash="")
            def fetch_list(self, plan_item, *, on_page_completed=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    return SourceOutcome.failure(
                        failed_code="source_timeout", safe_log="reason=单组合超时")
                return SourceOutcome.success(
                    jobs=[{"job_id": "j1", "source_url": "u1"}],
                    safe_log="ok", input_hash=plan_item.get("input_hash", ""),
                    scope_complete=True, source_exhausted=True,
                    stop_reason="target_reached")

        result = run_search(
            {"keyword": "A,B", "city": ["X"]},
            _MixedSource(),
            pages=1,
            artifact_dir=self._tmp_dir(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("integrity", {}).get("conclusion"), "partial")
        self.assertEqual(result["total_scraped"], 1)

    @staticmethod
    def _tmp_dir():
        import tempfile
        return tempfile.mkdtemp(prefix="boss_test_")


class AdvancedSettingsContractTests(unittest.TestCase):
    """SPEC011 T009: 高级设置 GET/PUT/POST 端点契约测试。"""

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
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    def tearDown(self):
        self.temp.cleanup()

    def _preview_scope(self, *, pages=3):
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": ["AI应用开发"],
            "scope_kind": "cities",
            "cities": ["东莞"],
            "pages_per_combination": pages,
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()["scope"]

    def _complete_speed_settings(self):
        return {
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        }

    def test_get_returns_versioned_state(self):
        """GET 返回 selection、settings、config_schema_version。"""
        resp = self.client.get("/api/advanced-settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("selection", data)
        self.assertIn("settings", data)
        self.assertIn("config_schema_version", data)

    def test_advanced_settings_round_trip_browser_account(self):
        resp = self.client.post("/api/advanced-settings", json={
            "settings": {"browser_account": "b", "pages": 4},
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "b")
        bad = self.client.post("/api/advanced-settings", json={
            "settings": {"browser_account": "z"},
        })
        self.assertEqual(bad.status_code, 200)
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "a")

    def test_create_app_imports_legacy_settings_once(self):
        root = pathlib.Path(self.temp.name) / "legacy-app"
        legacy_path = root / "advanced_settings.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({
            "pages": 9,
            "inter_combo_delay": 18.0,
            "detail_batch_size": 8,
            "detail_interval": 3.0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 6.0,
            "screen_batch_size": 30,
            "screen_concurrency": 3,
            "match_batch_size": 3,
            "match_concurrency": 5,
        }), encoding="utf-8")
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "ADVANCED_SETTINGS_PATH": str(legacy_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        state = app.config["TASK_STORE"].get_advanced_config_state()
        self.assertEqual(state["last_custom_config"]["inter_combo_delay"], 18.0)
        self.assertIsNotNone(state["legacy_imported_at"])
        self.assertNotIn("pages", state["last_custom_config"])

    def test_put_custom_saves_complete_config(self):
        """PUT /custom 保存完整速度字段（含 JD 并发 Tab 数），返回 digest。"""
        resp = self.client.put("/api/advanced-settings/custom", json={
            "settings": {
                "inter_combo_delay": 10.0,
                "detail_batch_size": 15,
                "detail_interval": 2.0,
                "detail_reset_every": 4,
                "detail_batch_cooldown": 5.0,
                "detail_tab_pool_size": 5,
                "screen_batch_size": 50,
                "screen_concurrency": 5,
                "match_batch_size": 4,
                "match_concurrency": 10,
            }
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["selection"], "custom")
        self.assertIn("config_digest", data)
        self.assertEqual(data["settings"]["detail_tab_pool_size"], 5)

    def test_put_custom_preserves_active_browser_account(self):
        """保存速度设置后，当前浏览器账号不能被旧 JSON 兼容写覆盖。"""
        activated = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(activated.status_code, 200, activated.get_json())
        resp = self.client.put("/api/advanced-settings/custom", json={
            "settings": self._complete_speed_settings(),
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        persisted = json.loads(pathlib.Path(
            self.app.config["ADVANCED_SETTINGS_PATH"]
        ).read_text(encoding="utf-8"))
        self.assertEqual(persisted.get("browser_account"), "b")
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "b")
        accounts = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(accounts["active_account"], "b")

    def test_select_mode_preserves_active_browser_account(self):
        """切换执行模式后，当前浏览器账号不能被模式配置覆盖。"""
        activated = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(activated.status_code, 200, activated.get_json())
        scope = self._preview_scope(pages=3)
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable", "scope_digest": scope["scope_digest"],
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        persisted = json.loads(pathlib.Path(
            self.app.config["ADVANCED_SETTINGS_PATH"]
        ).read_text(encoding="utf-8"))
        self.assertEqual(persisted.get("browser_account"), "b")
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "b")
        accounts = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(accounts["active_account"], "b")

    def test_put_custom_rejects_partial(self):
        """PUT /custom 缺字段返回 400。"""
        resp = self.client.put("/api/advanced-settings/custom", json={
            "settings": {"inter_combo_delay": 10.0}
        })
        self.assertEqual(resp.status_code, 400)

    def test_select_mode_stable(self):
        """POST /select-mode stable 返回对应配置。"""
        scope = self._preview_scope(pages=3)
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable",
            "scope_digest": scope["scope_digest"],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["selection"], "stable")
        self.assertEqual(data["task_size"], "small")
        self.assertIn("settings", data)
        self.assertEqual(data["settings"]["pages"], 2)  # 稳定档默认每组翻页数

    def test_select_mode_uses_backend_preview_size(self):
        """客户端 task_size 不能覆盖 scope digest 对应的后端规模。"""
        scope = self._preview_scope(pages=15)  # 024 新口径：15 页属中规模
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "balanced",
            "scope_digest": scope["scope_digest"],
            "task_size": "small",
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["task_size"], "medium")

    def test_select_mode_rejects_unknown_scope_digest(self):
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable", "scope_digest": "sha256:unknown",
        })
        self.assertEqual(resp.status_code, 409)

    def test_select_mode_custom_recovers_last(self):
        """先存自定义，再选 custom 能恢复。"""
        self.client.put("/api/advanced-settings/custom", json={
            "settings": {
                "inter_combo_delay": 42.0,
                "detail_batch_size": 7,
                "detail_interval": 3.0,
                "detail_reset_every": 2,
                "detail_batch_cooldown": 8.0,
                "screen_batch_size": 25,
                "screen_concurrency": 3,
                "match_batch_size": 2,
                "match_concurrency": 4,
            }
        })
        scope = self._preview_scope(pages=3)
        # 切到 stable
        self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable", "scope_digest": scope["scope_digest"]
        })
        # 切回 custom
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "custom", "scope_digest": scope["scope_digest"]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["selection"], "custom")
        self.assertEqual(data["settings"]["inter_combo_delay"], 42.0)
        persisted = json.loads(pathlib.Path(
            self.app.config["ADVANCED_SETTINGS_PATH"]
        ).read_text(encoding="utf-8"))
        self.assertEqual(persisted["inter_combo_delay"], 42.0)
        self.assertEqual(
            self.app.config["TASK_STORE"].get_advanced_config_state()["active_selection"],
            "custom",
        )

    def test_rollback_requires_target(self):
        """POST /rollback 缺 target 返回 400。"""
        resp = self.client.post("/api/advanced-settings/mode-versions/rollback", json={})
        self.assertEqual(resp.status_code, 400)


# ======================================================================
# 契约合规补丁测试：覆盖本次 5 处契约违规修复（webui/app.py）
# ======================================================================
# 详见 contracts/http-api.md：
# - L219-229：cancel 合同（run_id + platform + status，DB 权威、内存快照兜底）
# - L247-251：/api/job-detail 成功响应含 platform + platform_job_id + jd
# - /api/pipeline/jobs/{platform_job_id}/jd：017-US4 起 source_run_id 必填，
#   缺失返回 409 missing_source_run_id（specs/017-*/contracts/http-api.md，
#   拒绝路径由 test_jd_refetch_without_source_run_id_rejected 覆盖）
# - L334-336：/api/check 显式平台解析；智联不得调旧 BOSS scraper


class ContractCompliancePatchTests(unittest.TestCase):
    """契约合规补丁：5 处修复的 HTTP 行为回归。"""

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
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- (a) /api/check ------------------------------------------------

    def test_check_default_returns_boss_platform(self):
        """契约 L334-336：省略 platform 只兼容 BOSS，返回 platform=boss。"""
        completed = type("Completed", (), {
            "returncode": 0,
            "output_tail": "",
            "failure_code": None,
            "ok": True,
        })()
        with mock.patch("webui.process_executor.ScraperExecutor.execute",
                        return_value=completed):
            resp = self.client.get("/api/check")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "boss")
        self.assertTrue(data["connected"])

    def test_check_zhilian_returns_preflight_without_boss_scraper(self):
        """契约 L334-336：智联检查走自身 preflight，不调用旧 BOSS scraper。"""
        with (
            mock.patch("webui.process_executor.ScraperExecutor.execute") as exec_mock,
            mock.patch("webui.source.ZhilianCdpSource") as source_cls,
        ):
            source_cls.return_value.preflight.return_value = mock.Mock(
                ok=True, failed_code="", failed_reason="")
            resp = self.client.get("/api/check?platform=zhilian")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "zhilian")
        self.assertTrue(data["connected"])
        # 关键：智联检查路径不得调用旧 BOSS scraper
        self.assertFalse(exec_mock.called)

    # -- (b) /api/execute-search/<id>/cancel ---------------------------

    def test_execute_search_cancel_returns_run_id_and_platform(self):
        """契约 L219-229：取消响应含 run_id + platform + status=cancelled。"""
        run_id = "test_exec_cancel_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        with mock.patch("webui.pipeline_exec.close_debug_chrome"):
            resp = self.client.post(f"/api/execute-search/{run_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["task_id"], run_id)
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["status"], "cancelled")

    # -- (c) /api/ai-screen/<id>/cancel --------------------------------

    def test_ai_screen_cancel_returns_run_id_and_platform(self):
        """契约 L219-229：AI 筛选取消响应含 run_id + platform + status。"""
        run_id = "test_ai_cancel_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "ai_screen", "status": "running", "progress": {},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        with mock.patch("webui.pipeline_exec.close_debug_chrome"):
            resp = self.client.post(f"/api/ai-screen/{run_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["status"], "cancelled")

    # -- (d) /api/job-detail success path ------------------------------

    def test_job_detail_success_includes_platform_and_platform_job_id(self):
        """契约 L247-251：成功响应含 platform + platform_job_id + jd。"""
        from webui.source import SourceOutcome
        fake_source = mock.MagicMock()
        fake_source.fetch_detail.return_value = SourceOutcome.success(
            detail={"jd": "岗位职责：负责后端业务开发与 API 设计。"},
            safe_log="detail ok",
        )
        with mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class",
                        return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")):
            resp = self.client.post("/api/job-detail", json={
                "job_id": "job-abc",
                "platform_job_id": "job-abc",
                "source_url": "https://www.zhipin.com/job/abc.html",
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["platform_job_id"], "job-abc")
        self.assertTrue(data["jd"])
        # _make_cdp_source 走 boss 分支（_BossCdpSource 被调用）
        self.assertTrue(fake_source.fetch_detail.called)

    def test_job_detail_zhilian_requires_source_run_id(self):
        """契约 L247-251：智联单 JD 不得只凭 URL 猜测来源。"""
        from webui.source import SourceOutcome
        fake_source = mock.MagicMock()
        fake_source.fetch_detail.return_value = SourceOutcome.success(
            detail={"jd": "岗位职责：负责后端业务开发与 API 设计。"},
            safe_log="detail ok",
        )
        with mock.patch("webui.source.ZhilianCdpSource",
                        return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")):
            resp = self.client.post("/api/job-detail", json={
                "job_id": "job-abc",
                "platform_job_id": "job-abc",
                "source_url": "https://www.zhaopin.com/jobdetail/job-abc.htm",
            })
        self.assertEqual(resp.status_code, 409, resp.get_json())
        self.assertEqual(resp.get_json().get("error_code"), "run_identity_conflict")
        self.assertFalse(fake_source.fetch_detail.called)

    # -- (e) /api/pipeline/jobs/<job_id>/jd fallback path --------------
    # 017-US4 起 fallback 的无 source_run_id 历史兼容路径已废除（统一
    # 409 missing_source_run_id），BOSS/智联 URL 均不再猜测平台身份；
    # 拒绝契约见 test_jd_refetch_without_source_run_id_rejected。


class Task008BackendIntegrationTests(unittest.TestCase):
    """Task 008：后端共享入口与兼容集成。

    覆盖：新 API 注册与认证、生命周期写入/失败保持/重启持久化、
    legacy PATCH 命令服务映射、pipeline 权威身份、跨平台提醒无过滤、
    相似岗位隔离、不安全 URL 和偏好反馈兼容。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.db_path = str(self.root / "state" / "webui.db")
        self.app = self._build_app(self.root / "app-1")
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.profile_id = self.client.post(
            "/api/profiles", json={"name": "t008", "confirmed_fields": {}},
        ).get_json()["id"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _build_app(self, subdir):
        return create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / subdir / "results"),
            "DB_PATH": self.db_path,
            "PYTHON_EXECUTABLE": sys.executable,
        })

    def _past(self, days):
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def _future(self, hours):
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    def _add_job(self, *, platform="boss", pid=None, url=None,
                 title="岗位", company="公司"):
        pid = pid or f"{platform}-pid-{uuid.uuid4().hex[:8]}"
        url = url or (
            f"https://www.zhipin.com/job_detail/{pid}.html"
            if platform == "boss"
            else f"https://www.zhaopin.com/jobdetail/{pid}.htm"
        )
        result = self.store.upsert_job(
            platform=platform, platform_job_id=pid, canonical_url=url,
            title=title, company=company, salary="20-30K", location="上海",
            jd="负责后端开发。",
        )
        assert result["ok"], result
        return result["job_id"], pid, url

    def _post_action(self, job_id, action, *, request_id=None,
                     applied_at=None, target_status=None, profile_id=None):
        return self.client.post("/api/profile-jobs/actions", json={
            "request_id": request_id or str(uuid.uuid4()),
            "profile_id": profile_id or self.profile_id,
            "job": {"job_id": job_id},
            "action": action,
            "applied_at": applied_at,
            "target_status": target_status,
        })

    def _state(self, job_id):
        resp = self.client.get("/api/profile-jobs/state", query_string={
            "profile_id": self.profile_id, "job_id": job_id,
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()

    def _table_counts(self):
        with self.store._connection() as conn:
            return {
                table: conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()["c"]
                for table in (
                    "jobs", "profile_jobs", "profile_job_events",
                    "profile_job_command_receipts", "feedback_events",
                )
            }

    # -- 1. 新 API 注册且保留认证 ----------------------------------------

    def test_new_feedback_routes_registered_and_auth_protected(self):
        job_id, _, _ = self._add_job()

        count = self.client.get("/api/job-reminders/count", query_string={
            "profile_id": self.profile_id,
        })
        self.assertEqual(count.status_code, 200)
        body = count.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["threshold_hours"], 720)

        reminders = self.client.get("/api/job-reminders", query_string={
            "profile_id": self.profile_id,
        })
        self.assertEqual(reminders.status_code, 200)
        self.assertEqual(reminders.get_json()["items"], [])

        state = self._state(job_id)
        self.assertTrue(state["ok"])
        self.assertFalse(state["exists"])

        events = self.client.get(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/events",
        )
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.get_json()["events"], [])

        advice = self.client.post(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/advice", json={},
        )
        self.assertEqual(advice.status_code, 409)
        self.assertEqual(advice.get_json()["error_code"], "reminder_not_eligible")

        # 写路由必须保留会话令牌防护
        anon = self.app.test_client()
        blocked = anon.post("/api/profile-jobs/actions", json={
            "request_id": str(uuid.uuid4()),
            "profile_id": self.profile_id,
            "job": {"job_id": job_id},
            "action": "mark_read",
        })
        self.assertEqual(blocked.status_code, 403)
        blocked_reminders_write = anon.post(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/advice", json={},
        )
        self.assertEqual(blocked_reminders_write.status_code, 403)

    # -- 2. 生命周期写入、失败保持原状态、重启持久化 -------------------

    def test_lifecycle_write_failure_keeps_state_and_survives_restart(self):
        job_id, _, _ = self._add_job()
        applied_at = self._past(40)

        ok = self._post_action(job_id, "mark_applied", applied_at=applied_at)
        self.assertEqual(ok.status_code, 200, ok.get_data(as_text=True))
        state = self._state(job_id)["state"]
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["applied_at"], applied_at)
        self.assertTrue(state["reminder"]["eligible"])

        bad = self._post_action(job_id, "correct_applied_at",
                                applied_at=self._future(2))
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.get_json()["error_code"], "applied_at_in_future")
        state = self._state(job_id)["state"]
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["applied_at"], applied_at)
        events = self.client.get(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/events",
        ).get_json()["events"]
        self.assertEqual(len(events), 1)

        # 模拟应用重启：同一 DB 重新构造应用
        restarted = self._build_app("app-2")
        client2 = restarted.test_client()
        client2.environ_base["HTTP_X_BOSS_TOKEN"] = client2.get(
            "/api/session").get_json()["token"]
        state2 = client2.get("/api/profile-jobs/state", query_string={
            "profile_id": self.profile_id, "job_id": job_id,
        }).get_json()
        self.assertTrue(state2["exists"])
        self.assertEqual(state2["state"]["status"], "applied")
        self.assertEqual(state2["state"]["applied_at"], applied_at)
        count2 = client2.get("/api/job-reminders/count", query_string={
            "profile_id": self.profile_id,
        }).get_json()
        self.assertEqual(count2["total"], 1)

    # -- 3. legacy PATCH 命令服务映射 ------------------------------------

    def test_legacy_patch_requires_request_id_and_maps_to_command_service(self):
        job_id, _, _ = self._add_job()
        self.store.link_profile_job(self.profile_id, job_id, None, None)
        url = f"/api/profile-jobs/{self.profile_id}/{job_id}"
        applied_at = self._past(40)

        missing = self.client.patch(url, json={
            "status": "applied", "applied_at": applied_at,
        })
        self.assertEqual(missing.status_code, 428)
        self.assertEqual(
            missing.get_json()["error_code"], "idempotency_key_required")
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["status"], "new")
        self.assertEqual(self._table_counts()["profile_job_events"], 0)

        request_id = str(uuid.uuid4())
        marked = self.client.patch(
            url,
            json={"status": "applied", "applied_at": applied_at,
                  "request_id": request_id},
        )
        self.assertEqual(marked.status_code, 200, marked.get_data(as_text=True))
        pj = self.store.get_profile_job(self.profile_id, job_id)
        self.assertEqual(pj["status"], "applied")
        self.assertEqual(pj["applied_at"], applied_at)
        counts = self._table_counts()
        self.assertEqual(counts["profile_job_events"], 1)
        self.assertEqual(counts["profile_job_command_receipts"], 1)

        # 同 request_id 同载荷重放：不产生第二次写入
        replay = self.client.patch(
            url,
            json={"status": "applied", "applied_at": applied_at,
                  "request_id": request_id},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.get_json()["replayed"])
        counts = self._table_counts()
        self.assertEqual(counts["profile_job_events"], 1)
        self.assertEqual(counts["profile_job_command_receipts"], 1)

        # Idempotency-Key 请求头同样是合法 request ID 来源
        via_header = self.client.patch(
            url,
            json={"status": "read"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(via_header.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["status"], "read")

        # 时间校验不得被 legacy 入口绕过
        future = self.client.patch(
            url,
            json={"status": "applied", "applied_at": self._future(3),
                  "request_id": str(uuid.uuid4())},
        )
        self.assertEqual(future.status_code, 422)
        self.assertEqual(
            future.get_json()["error_code"], "applied_at_in_future")
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["status"], "read")

    def test_legacy_patch_note_and_lifecycle_are_atomic(self):
        job_id, _, _ = self._add_job()
        self.store.link_profile_job(self.profile_id, job_id, None, None)
        url = f"/api/profile-jobs/{self.profile_id}/{job_id}"
        applied_at = self._past(40)

        mixed = self.client.patch(
            url,
            json={"status": "applied", "applied_at": applied_at,
                  "note": "混合备注",
                  "request_id": str(uuid.uuid4())},
        )
        self.assertEqual(mixed.status_code, 200, mixed.get_data(as_text=True))
        pj = self.store.get_profile_job(self.profile_id, job_id)
        self.assertEqual(pj["status"], "applied")
        self.assertEqual(pj["note"], "混合备注")
        self.assertEqual(self._table_counts()["profile_job_events"], 1)

        # 非法生命周期字段 + note：整体拒绝，note 不部分写入
        rejected = self.client.patch(
            url,
            json={"status": "nonsense", "note": "不应写入",
                  "request_id": str(uuid.uuid4())},
        )
        self.assertEqual(rejected.status_code, 400)
        pj = self.store.get_profile_job(self.profile_id, job_id)
        self.assertEqual(pj["note"], "混合备注")
        self.assertEqual(pj["status"], "applied")
        self.assertEqual(self._table_counts()["profile_job_events"], 1)

        # note-only 保持现有独立备注语义，无需 request ID
        note_only = self.client.patch(url, json={"note": "仅备注"})
        self.assertEqual(note_only.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["note"], "仅备注")

    # -- 4. pipeline interest/reject/cancel 权威三元组 -------------------

    def test_pipeline_feedback_uses_authoritative_triple_both_platforms(self):
        boss_job = {
            "platform": "boss",
            "platform_job_id": "boss-pid-1",
            "title": "Python 后端",
            "company": "甲公司",
            "job_link": "https://www.zhipin.com/job_detail/boss-pid-1.html",
        }
        zhilian_job = {
            "platform": "zhilian",
            "platform_job_id": "zl-pid-1",
            "title": "Python 后端",
            "company": "甲公司",
            "job_link": "https://www.zhaopin.com/jobdetail/zl-pid-1.htm",
        }

        marked = self.client.post("/api/pipeline/jobs/interest",
                                  json={"profile_id": self.profile_id,
                                        "job": boss_job})
        self.assertEqual(marked.status_code, 200, marked.get_data(as_text=True))
        boss_internal = marked.get_json()["job_id"]
        self.assertNotEqual(boss_internal, "boss-pid-1")
        boss_row = self.store.get_job(boss_internal)
        self.assertEqual(boss_row["platform"], "boss")
        self.assertEqual(boss_row["platform_job_id"], "boss-pid-1")

        marked_zl = self.client.post("/api/pipeline/jobs/interest",
                                     json={"profile_id": self.profile_id,
                                           "job": zhilian_job})
        self.assertEqual(marked_zl.status_code, 200)
        zl_internal = marked_zl.get_json()["job_id"]
        self.assertNotEqual(zl_internal, "zl-pid-1")
        self.assertEqual(self.store.get_job(zl_internal)["platform"], "zhilian")
        self.assertEqual(
            len(self.store.list_screening_interested(self.profile_id)), 2)

        rejected = self.client.post("/api/pipeline/jobs/reject",
                                    json={"profile_id": self.profile_id,
                                          "job": zhilian_job})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.get_json()["job_id"], zl_internal)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, zl_internal)["status"], "deleted")

        cancelled = self.client.post("/api/pipeline/jobs/interest/cancel",
                                     json={"profile_id": self.profile_id,
                                           "job": boss_job})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["job_id"], boss_internal)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, boss_internal)["status"], "new")

        cancelled_reject = self.client.post("/api/pipeline/jobs/reject/cancel",
                                             json={"profile_id": self.profile_id,
                                                   "job": zhilian_job})
        self.assertEqual(cancelled_reject.status_code, 200)
        self.assertEqual(cancelled_reject.get_json()["job_id"], zl_internal)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, zl_internal)["status"], "interested")

    def test_cancel_reject_restores_previous_interest(self):
        job = {
            "platform": "boss",
            "platform_job_id": "boss-pid-reject-restore",
            "title": "Python 后端",
            "company": "乙公司",
            "job_link": "https://www.zhipin.com/job_detail/boss-pid-reject-restore.html",
        }
        marked = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(marked.status_code, 200)
        internal = marked.get_json()["job_id"]
        rejected = self.client.post("/api/pipeline/jobs/reject", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, internal)["status"], "deleted")
        cancelled = self.client.post("/api/pipeline/jobs/reject/cancel", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, internal)["status"], "interested")

    def test_cancel_interest_with_internal_id_only_succeeds(self):
        """收藏抽屉取消收藏只传内部 job_id 必须成功（回归）。

        App 收藏抽屉此前附带 job_link 但缺 platform/platform_job_id，
        后端权威解析把「内部 ID + 部分三元组」判为身份不完整 → 422
        “岗位身份信息不完整”。修复后前端只传内部 job_id，走内部 ID
        解析，不再触发三元组校验。
        """
        job = {
            "platform": "boss",
            "platform_job_id": "fav-pid-1",
            "job_link": "https://www.zhipin.com/job_detail/fav-pid-1.html",
        }
        marked = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(marked.status_code, 200, marked.get_data(as_text=True))
        internal_id = marked.get_json()["job_id"]

        cancelled = self.client.post("/api/pipeline/jobs/interest/cancel", json={
            "profile_id": self.profile_id,
            "job": {"job_id": internal_id},
        })
        self.assertEqual(cancelled.status_code, 200, cancelled.get_data(as_text=True))
        self.assertEqual(cancelled.get_json()["job_id"], internal_id)
        self.assertEqual(
            self.store.get_profile_job(self.profile_id, internal_id)["status"], "new")

    def test_identity_failures_do_not_leak_internal_details(self):
        """身份错误文案面向用户，不裸露“三元组”等内部数据结构。"""
        resp = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "bare-pid",
                "job_link": "https://www.zhipin.com/job_detail/bare-pid.html",
            },
        })
        self.assertEqual(resp.status_code, 200)
        # 部分三元组 + 内部 ID 携带时后端必须拒绝，且文案不含内部术语。
        seeded = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "bare-pid",
                "job_link": "https://www.zhipin.com/job_detail/bare-pid.html",
            },
        })
        self.assertEqual(seeded.status_code, 200)
        internal_id = seeded.get_json()["job_id"]
        half = self.client.post("/api/pipeline/jobs/interest/cancel", json={
            "profile_id": self.profile_id,
            "job": {"job_id": internal_id,
                    "job_link": "https://www.zhipin.com/job_detail/bare-pid.html"},
        })
        self.assertEqual(half.status_code, 422)
        body = half.get_json()
        self.assertEqual(body["error_code"], "job_identity_incomplete")
        self.assertNotIn("三元组", body.get("user_message", ""))
        self.assertNotIn("内部岗位", body.get("user_message", ""))

    def test_pipeline_identity_failures_have_zero_side_effects(self):
        before = self._table_counts()

        incomplete = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform_job_id": "bare-pid",
                "job_link": "https://www.zhipin.com/job_detail/bare-pid.html",
            },
        })
        self.assertEqual(incomplete.status_code, 422)
        self.assertEqual(
            incomplete.get_json()["error_code"], "job_identity_incomplete")

        mismatch = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "mismatch-pid",
                "job_link": "https://www.zhaopin.com/jobdetail/mismatch-pid.htm",
            },
        })
        self.assertEqual(mismatch.status_code, 422)
        self.assertEqual(
            mismatch.get_json()["error_code"], "platform_url_mismatch")

        # 双索引冲突：(platform,pid) 与 canonical_url 命中不同记录
        self._add_job(platform="boss", pid="conflict-a",
                      url="https://www.zhipin.com/job_detail/conflict-a.html")
        self._add_job(platform="boss", pid="conflict-b",
                      url="https://www.zhipin.com/job_detail/conflict-b.html")
        after_seed = self._table_counts()
        conflict = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "conflict-a",
                "job_link": "https://www.zhipin.com/job_detail/conflict-b.html",
            },
        })
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.get_json()["error_code"], "job_identity_conflict")

        self.assertEqual(self._table_counts(), after_seed)
        self.assertGreaterEqual(after_seed["jobs"], before["jobs"] + 2)
        self.assertEqual(after_seed["profile_jobs"], before["profile_jobs"])
        self.assertEqual(
            after_seed["feedback_events"], before["feedback_events"])

    # -- 5. 相似岗位隔离 / 无平台过滤 / 不安全 URL / 偏好兼容 ----------

    def test_similar_jobs_are_isolated_by_internal_id(self):
        job_a, _, _ = self._add_job(
            platform="boss", title="高级后端工程师", company="同名公司")
        job_b, _, _ = self._add_job(
            platform="zhilian", title="高级后端工程师", company="同名公司")
        self.assertNotEqual(job_a, job_b)

        self.assertEqual(
            self._post_action(job_b, "mark_read").status_code, 200)
        self.assertEqual(
            self._post_action(job_a, "mark_applied",
                              applied_at=self._past(40)).status_code, 200)

        self.assertEqual(self._state(job_a)["state"]["status"], "applied")
        self.assertEqual(self._state(job_b)["state"]["status"], "read")
        total = self.client.get("/api/job-reminders/count", query_string={
            "profile_id": self.profile_id,
        }).get_json()["total"]
        self.assertEqual(total, 1)

    def test_reminders_mix_boss_and_zhilian_without_platform_filter(self):
        boss_job, _, _ = self._add_job(platform="boss")
        zhilian_job, _, _ = self._add_job(platform="zhilian")
        for job_id in (boss_job, zhilian_job):
            self.assertEqual(
                self._post_action(job_id, "mark_applied",
                                  applied_at=self._past(35)).status_code, 200)

        payload = self.client.get("/api/job-reminders", query_string={
            "profile_id": self.profile_id,
        }).get_json()
        self.assertEqual(payload["total"], 2)
        platforms = {item["platform"] for item in payload["items"]}
        self.assertEqual(platforms, {"boss", "zhilian"})
        for item in payload["items"]:
            self.assertTrue(item["can_open"])

    def test_invalid_canonical_url_blocks_open_but_not_lifecycle_data(self):
        unsafe_id = uuid.uuid4().hex
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, "
                "company, salary, location, jd, first_seen_at, last_seen_at, "
                "platform, platform_job_id, experience, degree, extra_json) "
                "VALUES (?, ?, '', '不安全链接', '公司', '20K', '上海', 'JD', "
                "datetime('now'), datetime('now'), 'boss', 'unsafe-pid', "
                "'', '', '{}')",
                (unsafe_id, "javascript:alert(1)"),
            )
        self.assertEqual(
            self._post_action(unsafe_id, "mark_applied",
                              applied_at=self._past(40)).status_code, 200)

        state = self._state(unsafe_id)
        self.assertTrue(state["exists"])
        self.assertEqual(state["state"]["status"], "applied")

        payload = self.client.get("/api/job-reminders", query_string={
            "profile_id": self.profile_id,
        }).get_json()
        items = [item for item in payload["items"]
                 if item["job_id"] == unsafe_id]
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["can_open"])
        self.assertIsNone(items[0]["canonical_url"])

    def test_history_preference_feedback_never_overrides_current_status(self):
        # search-runs 需要带城市的画像；生命周期断言仍用主画像
        run_profile = self.client.post("/api/profiles", json={
            "name": "t008-run",
            "confirmed_fields": {"city": "上海", "roles": ["Python"]},
        }).get_json()["id"]
        run = self.client.post("/api/search-runs", json={
            "profile_id": run_profile,
            "manual_keywords": ["Python"],
        })
        self.assertEqual(run.status_code, 202, run.get_data(as_text=True))
        run = run.get_json()
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/t008-compat.html",
            "https://www.zhipin.com/job_detail/t008-compat.html",
            "兼容岗位", "公司", "20K", "上海", "JD",
        )
        self.store.link_profile_job(
            run_profile, job["id"], run["id"], run["id"])

        self.assertEqual(self.client.post(
            f"/api/jobs/{job['id']}/feedback",
            json={"profile_id": run_profile, "action": "interested"},
        ).status_code, 200)
        cards = self.client.get(
            f"/api/search-runs/{run['id']}/jobs").get_json()["jobs"]
        self.assertEqual(cards[0]["interest_state"], "interested")

        self.assertEqual(
            self._post_action(job["id"], "mark_applied",
                              applied_at=self._past(40),
                              profile_id=run_profile).status_code, 200)

        cards = self.client.get(
            f"/api/search-runs/{run['id']}/jobs").get_json()["jobs"]
        self.assertEqual(len(cards), 1)
        # 历史 interested 事件不得把当前 applied 投影回 interested
        self.assertEqual(cards[0]["interest_state"], "applied")
        # 偏好事件仍保留（偏好学习语义不变）
        events = self.store.list_feedback(run_profile, job_id=job["id"])
        self.assertEqual(
            [e["action"] for e in events if not e["revoked_at"]],
            ["interested"],
        )


# ===========================================================================
# spec003 tasks004 T028/T029 — env_check EXE 模式 + _make_cdp_source EXE 接线
#
# 冻结合同：specs/003-desktop-exe/contracts/runtime-mode.md §2、
# contracts/inprocess-runner.md §4.3。
# ===========================================================================


class EnvCheckRuntimeModeTests(unittest.TestCase):
    """T028: env_check 响应含 runtime_mode 字段，EXE 模式检查项差异。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _make_app(self, runtime_mode="source"):
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / "results"),
            "DB_PATH": str(self.root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
            "RUNTIME_MODE": runtime_mode,
        })
        client = app.test_client()
        token = client.get("/api/session").get_json()["token"]
        client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        return app, client

    def _fake_check_items(self):
        return ([
            {"id": "browsers", "name": "Chromium 浏览器", "status": "ok",
             "detail": "找到 Chrome ✅", "fix": None},
            {"id": "deps", "name": "Python 依赖", "status": "ok",
             "detail": "requests / websocket 可导入", "fix": None},
            {"id": "cdp", "name": "专用浏览器已启动", "status": "fail",
             "detail": "无法连接 127.0.0.1:9222", "fix": "启动专用浏览器"},
            {"id": "boss_login", "name": "BOSS 登录状态", "status": "skip",
             "detail": "跳过", "fix": None},
        ], False)

    def test_source_mode_returns_runtime_mode_source(self):
        """源码模式：runtime_mode='source'，local 组无 webview2 项。"""
        app, client = self._make_app("source")
        with mock.patch("scripts.boss.smoke.collect_check_items",
                        return_value=self._fake_check_items()):
            payload = client.get("/api/env-check").get_json()
        self.assertEqual(payload["runtime_mode"], "source")
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        item_ids = [item["id"] for item in local_items]
        self.assertNotIn("webview2", item_ids)

    def test_exe_mode_returns_runtime_mode_exe(self):
        """EXE 模式：runtime_mode='exe'。"""
        app, client = self._make_app("exe")
        with mock.patch("scripts.boss.smoke.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": True, "available": True,
                                         "version": "120.0.0.0", "detail": "已安装"}):
            payload = client.get("/api/env-check").get_json()
        self.assertEqual(payload["runtime_mode"], "exe")

    def test_exe_mode_deps_item_is_builtin_runtime(self):
        """EXE 模式：deps 项名称改「内置运行时」，状态恒 ok，fix 为 null。"""
        app, client = self._make_app("exe")
        with mock.patch("scripts.boss.smoke.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": True, "available": True,
                                         "version": "120.0.0.0", "detail": "已安装"}):
            payload = client.get("/api/env-check").get_json()
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        deps_item = next(item for item in local_items if item["id"] == "deps")
        self.assertIn("内置运行时", deps_item["name"])
        self.assertEqual(deps_item["status"], "ok")
        self.assertIsNone(deps_item["fix"])

    def test_exe_mode_webview2_item_present(self):
        """EXE 模式：local 组含 webview2 项（注入检测替身）。"""
        app, client = self._make_app("exe")
        with mock.patch("scripts.boss.smoke.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": True, "available": True,
                                         "version": "120.0.0.0", "detail": "已安装 WebView2"}):
            payload = client.get("/api/env-check").get_json()
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        webview2_item = next(item for item in local_items if item["id"] == "webview2")
        self.assertEqual(webview2_item["status"], "ok")
        self.assertIn("WebView2", webview2_item["detail"])

    def test_exe_mode_webview2_not_installed(self):
        """EXE 模式：webview2 未安装时 status=fail，fix 文案含「安装 WebView2」。"""
        app, client = self._make_app("exe")
        with mock.patch("scripts.boss.smoke.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": False, "available": True,
                                         "version": None, "detail": "未检测到 WebView2"}):
            payload = client.get("/api/env-check").get_json()
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        webview2_item = next(item for item in local_items if item["id"] == "webview2")
        self.assertEqual(webview2_item["status"], "fail")
        self.assertIn("WebView2", webview2_item.get("fix") or "")

    def test_env_check_has_no_cooldown_payload(self):
        # 016：冷却功能删除；env-check 不再返回 cooldowns 字段
        app, client = self._make_app("source")
        with mock.patch("scripts.boss.smoke.collect_check_items",
                        return_value=self._fake_check_items()):
            payload = client.get("/api/env-check").get_json()
        self.assertNotIn("cooldowns", payload)


class MakeCdpSourceRuntimeModeTests(unittest.TestCase):
    """T029: _make_cdp_source EXE 模式 BOSS 传 in_process=True，智联不变。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _make_app(self, runtime_mode="source"):
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / "results"),
            "DB_PATH": str(self.root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
            "RUNTIME_MODE": runtime_mode,
        })
        return app

    def test_exe_mode_boss_source_gets_in_process_true(self):
        """EXE 模式：_make_cdp_source(platform=boss) 返回的 source.in_process=True。"""
        app = self._make_app("exe")
        factory = app.config["MAKE_CDP_SOURCE"]
        source = factory(platform="boss", cdp_port=9222, browser_account="acc")
        self.assertIsNotNone(source)
        self.assertTrue(getattr(source, "in_process", False))

    def test_source_mode_boss_source_in_process_false(self):
        """源码模式：_make_cdp_source(platform=boss) 返回的 source.in_process=False。"""
        app = self._make_app("source")
        factory = app.config["MAKE_CDP_SOURCE"]
        source = factory(platform="boss", cdp_port=9222, browser_account="acc")
        self.assertIsNotNone(source)
        self.assertFalse(getattr(source, "in_process", False))

    def test_exe_mode_zhilian_source_unchanged(self):
        """EXE 模式：_make_cdp_source(platform=zhilian) 智联构造不变（无 in_process）。"""
        app = self._make_app("exe")
        factory = app.config["MAKE_CDP_SOURCE"]
        source = factory(platform="zhilian", cdp_port=9223,
                         browser_account="acc", profile_key="zhilian:acc")
        self.assertIsNotNone(source)
        # 智联 source 不应有 in_process 属性，或属性为 False
        self.assertFalse(getattr(source, "in_process", False))


if __name__ == "__main__":
    unittest.main()
