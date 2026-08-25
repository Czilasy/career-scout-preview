"""B073 账号角色（R1/R2）数据层与任务账号解析测试。

覆盖：roles 读写/旧文件兼容/清洗、resolve_account_for_role 首账号匹配、
assign_account_role 互斥打标、account_for_role 优先级（run 冻结值 →
角色解析 → 登录态检测 → fallback 降级，不报错不阻断）。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui.pipeline_exec_accounts import (
    account_for_role,
    assign_account_role,
    load_browser_accounts,
    resolve_account_for_role,
    save_browser_accounts,
)


def _accounts():
    return {
        "a": {"id": "a", "name": "账号A", "profile_dir": "/profiles/a", "builtin": True, "roles": []},
        "b": {"id": "b", "name": "账号B", "profile_dir": "/profiles/b", "builtin": False, "roles": []},
    }


class BrowserAccountRolesDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "browser_accounts.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_load_save_preserves_roles(self):
        save_browser_accounts(_accounts(), self.path)
        accounts = load_browser_accounts(self.path)
        accounts["b"]["roles"] = ["R1", "R2"]
        save_browser_accounts(accounts, self.path)
        loaded = load_browser_accounts(self.path)
        self.assertEqual(loaded["b"]["roles"], ["R1", "R2"])
        self.assertEqual(loaded["a"]["roles"], [])
        self.assertIn("roles", loaded["a"])

    def test_legacy_file_without_roles_defaults_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "a": {"id": "a", "name": "A", "profile_dir": "/profiles/a", "builtin": True},
                "b": {"id": "b", "name": "B", "profile_dir": "/profiles/b", "builtin": False},
            }, f)
        accounts = load_browser_accounts(self.path)
        self.assertEqual(accounts["a"]["roles"], [])
        self.assertEqual(accounts["b"]["roles"], [])

    def test_save_sanitizes_invalid_roles(self):
        accounts = _accounts()
        accounts["b"]["roles"] = ["R1", "R3", 123, "R2", "R1"]
        save_browser_accounts(accounts, self.path)
        loaded = load_browser_accounts(self.path)
        self.assertEqual(loaded["b"]["roles"], ["R1", "R2"])

    def test_resolve_account_for_role_returns_first_match(self):
        accounts = _accounts()
        accounts["a"]["roles"] = ["R2"]
        accounts["b"]["roles"] = ["R1", "R2"]
        self.assertEqual(resolve_account_for_role(accounts, "R1"), "b")
        self.assertEqual(resolve_account_for_role(accounts, "R2"), "a")
        self.assertIsNone(resolve_account_for_role(_accounts(), "R1"))
        self.assertIsNone(resolve_account_for_role(accounts, "R3"))

    def test_assign_account_role_is_mutually_exclusive(self):
        accounts = assign_account_role(_accounts(), "R1", "b")
        self.assertEqual(accounts["b"]["roles"], ["R1"])
        self.assertEqual(accounts["a"]["roles"], [])
        # 换选 a → b 的 R1 被清（角色→账号一对一）
        accounts = assign_account_role(accounts, "R1", "a")
        self.assertEqual(accounts["a"]["roles"], ["R1"])
        self.assertEqual(accounts["b"]["roles"], [])
        # None → 清空该角色（回退未指定）
        accounts = assign_account_role(accounts, "R1", None)
        self.assertEqual(accounts["a"]["roles"], [])
        # 同一账号可同时承担 R1 + R2
        accounts = assign_account_role(accounts, "R1", "b")
        accounts = assign_account_role(accounts, "R2", "b")
        self.assertEqual(accounts["b"]["roles"], ["R1", "R2"])
        # 非法角色拒绝
        with self.assertRaises(ValueError):
            assign_account_role(accounts, "R3", "a")


class AccountForRoleResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "browser_accounts.json")
        accounts = _accounts()
        accounts["b"]["roles"] = ["R1"]
        accounts["a"]["roles"] = ["R2"]
        save_browser_accounts(accounts, self.path)

    def tearDown(self):
        self.temp.cleanup()

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value=None)
    def test_frozen_run_account_wins_over_role_resolution(self, _state):
        # 续跑：run 冻结 browser_account 优先，不按角色重新解析（角色在创建时冻结）
        run = {"execution_params": {"browser_account": "a"}}
        self.assertEqual(account_for_role("R1", self.path, run=run, fallback="a"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value=None)
    def test_resolves_role_account_when_no_frozen_run(self, _state):
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "b")
        self.assertEqual(account_for_role("R2", self.path, fallback="a"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value=None)
    def test_unassigned_role_falls_back_without_error(self, _state):
        accounts = _accounts()
        save_browser_accounts(accounts, self.path)
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")
        # fallback 不在账号簿 → 兜底内置账号 a，不报错
        self.assertEqual(account_for_role("R1", self.path, fallback="zzz"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value="not_logged_in")
    def test_login_missing_downgrades_to_current_account(self, _state):
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value="restricted")
    def test_restricted_downgrades_to_current_account(self, _state):
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value="logged_in")
    def test_logged_in_role_account_is_kept(self, _state):
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "b")


if __name__ == "__main__":
    unittest.main()
