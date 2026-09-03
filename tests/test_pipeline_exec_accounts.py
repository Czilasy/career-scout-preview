"""Spec 038 B091 账号池配置 schema 测试。

覆盖（FR-021：旧 roles 字段全删不兼容）：
- ``pool``/``rate_limited`` 字段读写与归一化（缺字段补默认、配额 clamp）；
- 默认账号簿：内置账号默认进池/默认全选/默认配额取中值；
- ``update_account_pool`` 部分更新 + clamp + 不修改入参；
- ``resolve_account_for_role`` 降级返回池中第一个 selected 账号（role 参数忽略）；
- ``assign_account_role`` 兼容 stub（no-op，仅校验角色合法性）；
- ``account_for_role`` 优先级（run 冻结值 → 池解析 → 登录态检测 → fallback）；
- 新增账号自动进池、默认全选、默认配额（FR-018）；
- ``set_account_rate_limited`` 撞墙写 + 自愈清。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui.pipeline_exec_accounts import (
    DEFAULT_R1_QUOTA,
    DEFAULT_R2_QUOTA,
    R1_QUOTA_MAX,
    R1_QUOTA_MIN,
    R2_QUOTA_MAX,
    R2_QUOTA_MIN,
    _normalize_pool,
    _normalize_rate_limited,
    account_for_role,
    add_browser_account,
    assign_account_role,
    delete_browser_account,
    has_selected_account,
    load_browser_accounts,
    parse_bool,
    resolve_account_for_role,
    save_browser_accounts,
    set_account_rate_limited,
    update_account_pool,
)


def _accounts():
    """两个账号的简单 dict（不写盘），用于纯函数测试。"""
    return {
        "a": {"id": "a", "name": "账号A", "profile_dir": "/profiles/a",
              "builtin": True,
              "pool": {"selected": True, "order": 0,
                       "r1_quota": DEFAULT_R1_QUOTA, "r2_quota": DEFAULT_R2_QUOTA},
              "rate_limited": False},
        "b": {"id": "b", "name": "账号B", "profile_dir": "/profiles/b",
              "builtin": False,
              "pool": {"selected": True, "order": 1,
                       "r1_quota": DEFAULT_R1_QUOTA, "r2_quota": DEFAULT_R2_QUOTA},
              "rate_limited": False},
    }


class BrowserAccountPoolDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "browser_accounts.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_load_save_preserves_pool(self):
        save_browser_accounts(_accounts(), self.path)
        loaded = load_browser_accounts(self.path)
        self.assertEqual(loaded["b"]["pool"]["selected"], True)
        self.assertEqual(loaded["b"]["pool"]["r1_quota"], DEFAULT_R1_QUOTA)
        self.assertEqual(loaded["b"]["pool"]["r2_quota"], DEFAULT_R2_QUOTA)
        self.assertEqual(loaded["b"]["rate_limited"], False)
        self.assertIn("pool", loaded["a"])
        self.assertIn("rate_limited", loaded["a"])

    def test_legacy_file_without_pool_defaults_full_selection(self):
        # 旧 schema（roles 字段）文件直接被新 schema 覆盖：缺 pool 字段时
        # 归一为默认（全选 + 默认配额 + 按 dict 顺序 order）
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "a": {"id": "a", "name": "A", "profile_dir": "/profiles/a",
                      "builtin": True, "roles": ["R1"]},
                "b": {"id": "b", "name": "B", "profile_dir": "/profiles/b",
                      "builtin": False, "roles": []},
            }, f)
        accounts = load_browser_accounts(self.path)
        self.assertEqual(accounts["a"]["pool"]["selected"], True)
        self.assertEqual(accounts["a"]["pool"]["r1_quota"], DEFAULT_R1_QUOTA)
        self.assertEqual(accounts["b"]["pool"]["selected"], True)
        self.assertEqual(accounts["b"]["pool"]["order"], 1)
        # 旧 roles 字段不再保留
        self.assertNotIn("roles", accounts["a"])
        self.assertNotIn("roles", accounts["b"])

    def test_save_clamps_out_of_range_quota(self):
        accounts = _accounts()
        accounts["b"]["pool"] = {"selected": True, "order": 1,
                                 "r1_quota": 9999, "r2_quota": -5}
        save_browser_accounts(accounts, self.path)
        loaded = load_browser_accounts(self.path)
        self.assertEqual(loaded["b"]["pool"]["r1_quota"], R1_QUOTA_MAX)
        self.assertEqual(loaded["b"]["pool"]["r2_quota"], R2_QUOTA_MIN)

    def test_normalize_pool_defaults(self):
        p = _normalize_pool(None, 7)
        self.assertEqual(p, {"selected": True, "order": 7,
                             "r1_quota": DEFAULT_R1_QUOTA,
                             "r2_quota": DEFAULT_R2_QUOTA})
        # selected 显式 False 被保留
        self.assertEqual(_normalize_pool({"selected": False}, 0)["selected"], False)
        # 非 dict 入参归一默认
        self.assertEqual(_normalize_pool("garbage", 2)["order"], 2)

    def test_normalize_rate_limited(self):
        self.assertIs(_normalize_rate_limited(None), False)
        self.assertIs(_normalize_rate_limited(True), True)
        self.assertIs(_normalize_rate_limited(0), False)
        self.assertIs(_normalize_rate_limited("yes"), True)


class UpdateAccountPoolTests(unittest.TestCase):
    def test_update_partial_fields_preserves_others(self):
        accounts = _accounts()
        out = update_account_pool(accounts, "b",
                                  selected=False, r1_quota=10)
        # 仅改 b，a 不变
        self.assertIs(out["b"]["pool"]["selected"], False)
        self.assertEqual(out["b"]["pool"]["r1_quota"], 10)
        # r2_quota 未传 → 保留
        self.assertEqual(out["b"]["pool"]["r2_quota"], DEFAULT_R2_QUOTA)
        self.assertEqual(out["a"]["pool"]["selected"], True)
        # 入参未被修改
        self.assertEqual(accounts["b"]["pool"]["selected"], True)

    def test_update_clamps_quota(self):
        accounts = _accounts()
        out = update_account_pool(accounts, "a", r1_quota=9999)
        self.assertEqual(out["a"]["pool"]["r1_quota"], R1_QUOTA_MAX)
        out2 = update_account_pool(accounts, "a", r2_quota=-10)
        self.assertEqual(out2["a"]["pool"]["r2_quota"], R2_QUOTA_MIN)

    def test_update_unknown_account_raises(self):
        with self.assertRaises(KeyError):
            update_account_pool(_accounts(), "zzz", selected=False)


class ResolveAccountForRoleTests(unittest.TestCase):
    """Spec 038 FR-021：role 参数忽略，返回池中第一个 selected 账号。"""

    def test_returns_first_selected(self):
        accounts = _accounts()
        # 默认两账号都 selected → 第一个 a
        self.assertEqual(resolve_account_for_role(accounts, "R1"), "a")
        self.assertEqual(resolve_account_for_role(accounts, "R2"), "a")
        # 取消 a 选中 → 第一个变成 b
        accounts["a"]["pool"]["selected"] = False
        self.assertEqual(resolve_account_for_role(accounts, "R1"), "b")

    def test_returns_none_when_none_selected(self):
        accounts = _accounts()
        accounts["a"]["pool"]["selected"] = False
        accounts["b"]["pool"]["selected"] = False
        self.assertIsNone(resolve_account_for_role(accounts, "R1"))
        self.assertIsNone(resolve_account_for_role(accounts, "R3"))  # 兼容旧角色串

    def test_role_param_ignored_but_accepted(self):
        # role 任意字符串均不抛错（兼容旧调用方）
        accounts = _accounts()
        self.assertEqual(resolve_account_for_role(accounts, "R1"), "a")
        self.assertEqual(resolve_account_for_role(accounts, "R2"), "a")
        self.assertEqual(resolve_account_for_role(accounts, "any-role"), "a")

    def test_returns_selected_account_in_persisted_pool_order(self):
        accounts = _accounts()
        accounts["a"]["pool"]["order"] = 10
        accounts["b"]["pool"]["order"] = 2
        self.assertEqual(resolve_account_for_role(accounts, "R1"), "b")


class SelectedAccountGuardTests(unittest.TestCase):
    def test_has_selected_account_tracks_pool_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "browser_accounts.json")
            accounts = _accounts()
            save_browser_accounts(accounts, path)
            self.assertTrue(has_selected_account(path))
            accounts["a"]["pool"]["selected"] = False
            accounts["b"]["pool"]["selected"] = False
            save_browser_accounts(accounts, path)
            self.assertFalse(has_selected_account(path))

    def test_parse_bool_does_not_accept_false_string_as_true(self):
        self.assertIs(parse_bool(False), False)
        self.assertIs(parse_bool("false"), False)
        self.assertIs(parse_bool("true"), True)
        self.assertIsNone(parse_bool("not-a-bool"))


class AssignAccountRoleStubTests(unittest.TestCase):
    """FR-021：assign_account_role 为兼容 stub（no-op）。"""

    def test_returns_copy_unchanged_fields(self):
        accounts = _accounts()
        accounts["b"]["pool"]["selected"] = False
        out = assign_account_role(accounts, "R1", "b")
        # no-op：原 selected=False 不变（不再有角色互斥语义）
        self.assertIs(out["b"]["pool"]["selected"], False)
        self.assertNotIn("roles", out["b"])
        # 入参未被修改
        self.assertIs(accounts["b"]["pool"]["selected"], False)

    def test_invalid_role_still_rejected_for_api_contract(self):
        with self.assertRaises(ValueError):
            assign_account_role(_accounts(), "R3", "a")

    def test_none_target_returns_copy(self):
        accounts = _accounts()
        out = assign_account_role(accounts, "R1", None)
        self.assertEqual(out["b"]["pool"]["selected"], True)
        self.assertNotIn("roles", out["b"])


class AccountForRoleResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "browser_accounts.json")
        save_browser_accounts(_accounts(), self.path)

    def tearDown(self):
        self.temp.cleanup()

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value=None)
    def test_frozen_run_account_wins_over_pool_resolution(self, _state):
        # 续跑：run 冻结 browser_account 优先，不重新按池解析
        run = {"execution_params": {"browser_account": "b"}}
        self.assertEqual(account_for_role("R1", self.path, run=run, fallback="a"), "b")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value=None)
    def test_resolves_pool_first_selected_when_no_frozen_run(self, _state):
        # 默认 a 第一个 selected → 返回 a
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")
        self.assertEqual(account_for_role("R2", self.path, fallback="a"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value=None)
    def test_no_selected_account_falls_back_without_error(self, _state):
        accounts = load_browser_accounts(self.path)
        accounts["a"]["pool"]["selected"] = False
        accounts["b"]["pool"]["selected"] = False
        save_browser_accounts(accounts, self.path)
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")
        # fallback 不在账号簿 → 兜底内置账号 a
        self.assertEqual(account_for_role("R1", self.path, fallback="zzz"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value="not_logged_in")
    def test_login_missing_downgrades_to_current_account(self, _state):
        # a 第一个 selected 但登录态 not_logged_in → fallback=a（仍是 a）
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value="restricted")
    def test_restricted_downgrades_to_current_account(self, _state):
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")

    @mock.patch("scripts.login_state_cache.read_cached_state", return_value="logged_in")
    def test_logged_in_pool_account_is_kept(self, _state):
        # a 第一个 selected 且登录态 OK → 返回 a
        self.assertEqual(account_for_role("R1", self.path, fallback="a"), "a")


class AddAccountAutoEnrollsTests(unittest.TestCase):
    """Spec 038 FR-018：新增账号自动进池、默认全选、默认配额取中值。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "browser_accounts.json")
        save_browser_accounts(_accounts(), self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_new_account_default_pool_full_selection(self):
        new = add_browser_account("账号C", path=self.path)
        accounts = load_browser_accounts(self.path)
        added = accounts[new["id"]]
        self.assertTrue(added["pool"]["selected"])
        self.assertEqual(added["pool"]["r1_quota"], DEFAULT_R1_QUOTA)
        self.assertEqual(added["pool"]["r2_quota"], DEFAULT_R2_QUOTA)
        # order 排在已有账号末尾（已有 2 个 → order=2）
        self.assertEqual(added["pool"]["order"], 2)
        self.assertFalse(added["rate_limited"])


class RateLimitedPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "browser_accounts.json")
        save_browser_accounts(_accounts(), self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_set_true_persists(self):
        set_account_rate_limited("b", rate_limited=True, path=self.path)
        accounts = load_browser_accounts(self.path)
        self.assertIs(accounts["b"]["rate_limited"], True)
        self.assertIs(accounts["a"]["rate_limited"], False)

    def test_set_false_self_heals(self):
        set_account_rate_limited("b", rate_limited=True, path=self.path)
        set_account_rate_limited("b", rate_limited=False, path=self.path)
        accounts = load_browser_accounts(self.path)
        self.assertIs(accounts["b"]["rate_limited"], False)

    def test_set_same_value_is_noop(self):
        set_account_rate_limited("b", rate_limited=False, path=self.path)
        # 不抛错、不改盘（best-effort）
        accounts = load_browser_accounts(self.path)
        self.assertIs(accounts["b"]["rate_limited"], False)

    def test_unknown_account_silently_ignored(self):
        # 不抛错（best-effort）
        set_account_rate_limited("zzz", rate_limited=True, path=self.path)


if __name__ == "__main__":
    unittest.main()
