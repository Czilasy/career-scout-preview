# -*- coding: utf-8 -*-
"""030 续跑账号身份聚焦测试：快照、双门槛自动换号判定、换号留痕、兜底口径。"""

import unittest
from unittest import mock

from webui.resume_identity import (
    ACTIVE_ACCOUNT_AT_FREEZE_KEY,
    AI_PAUSE_CODES,
    account_display_name,
    append_account_switch_log_line,
    decide_auto_account_switch,
    ensure_frozen_browser_account,
    freeze_active_account_snapshot,
    record_account_switch_event,
)


def _run(*, browser_account="b", snapshot="d", error_code="source_rate_limited",
         extra=None):
    """构造统一继续接口读到的 paused run 形状（R2=b、全局=d 的默认场景）。"""
    params = {"browser_account": browser_account}
    if snapshot is not None:
        params[ACTIVE_ACCOUNT_AT_FREEZE_KEY] = snapshot
    run = {"execution_params": params, "error_code": error_code}
    if extra:
        run.update(extra)
    return run


class _FakeStore:
    def __init__(self, error=None):
        self.events = []
        self.params_writes = []
        self._error = error

    def append_task_event(self, run_id, kind, payload):
        if self._error:
            raise self._error
        self.events.append((run_id, kind, payload))

    def update_screening_execution_params(self, run_id, params):
        self.params_writes.append((run_id, dict(params)))


class FreezeSnapshotTests(unittest.TestCase):
    """FR-001：创建点快照助手。"""

    def test_snapshot_key_written_and_returned(self):
        params = {"browser_account": "b"}
        frozen = freeze_active_account_snapshot(params, "d")
        self.assertIs(frozen, params)
        self.assertEqual(params[ACTIVE_ACCOUNT_AT_FREEZE_KEY], "d")

    def test_empty_current_account_recorded_as_empty(self):
        params = {}
        freeze_active_account_snapshot(params, "")
        self.assertEqual(params[ACTIVE_ACCOUNT_AT_FREEZE_KEY], "")


class DecideAutoSwitchTests(unittest.TestCase):
    """FR-002/FR-003：双门槛自动换号判定。"""

    def test_untouched_global_no_switch(self):
        """用户没动过全局账号（当前=快照）→ 不换，沿用冻结 b。"""
        switched, _, _ = decide_auto_account_switch(
            _run(), current_active_account="d")
        self.assertFalse(switched)

    def test_user_switched_global_switches(self):
        """暂停期间把全局 d 换成 e（快照=d、当前=e）→ 换到 e（B057 语义）。"""
        switched, from_account, to_account = decide_auto_account_switch(
            _run(), current_active_account="e")
        self.assertTrue(switched)
        self.assertEqual((from_account, to_account), ("b", "e"))

    def test_missing_snapshot_no_switch(self):
        """存量任务无快照 → 一律不自动换（FR-003）。"""
        switched, _, _ = decide_auto_account_switch(
            _run(snapshot=None), current_active_account="e")
        self.assertFalse(switched)

    def test_ai_pause_code_no_switch(self):
        """暂停码为 AI 类阻断 → 换浏览器账号无意义，不换。"""
        for code in sorted(AI_PAUSE_CODES):
            switched, _, _ = decide_auto_account_switch(
                _run(error_code=code), current_active_account="e")
            self.assertFalse(switched, code)

    def test_non_ai_pause_code_switches(self):
        """浏览器/账号类阻断（如源限流）+ 用户换过号 → 换。"""
        switched, _, _ = decide_auto_account_switch(
            _run(error_code="source_rate_limited"),
            current_active_account="e")
        self.assertTrue(switched)

    def test_current_equals_frozen_no_switch(self):
        """当前全局账号与冻结账号相同 → 无事可做。"""
        switched, _, _ = decide_auto_account_switch(
            _run(browser_account="e"), current_active_account="e")
        self.assertFalse(switched)

    def test_empty_current_account_no_switch(self):
        switched, _, _ = decide_auto_account_switch(
            _run(), current_active_account="")
        self.assertFalse(switched)

    def test_missing_execution_params_no_switch(self):
        switched, _, _ = decide_auto_account_switch(
            {"error_code": ""}, current_active_account="e")
        self.assertFalse(switched)


class AccountSwitchAuditTests(unittest.TestCase):
    """FR-005：换号事件 + 续跑日志行。"""

    def test_event_written_with_display_names(self):
        store = _FakeStore()
        record_account_switch_event(
            store, "run-1", from_account="b", to_account="e",
            accounts={"b": {"name": "Mom"}, "e": {"name": "账号E"}})
        self.assertEqual(len(store.events), 1)
        run_id, kind, payload = store.events[0]
        self.assertEqual((run_id, kind), ("run-1", "account_switch"))
        self.assertEqual(payload["from_account"], "b")
        self.assertEqual(payload["to_account"], "e")
        self.assertEqual(payload["from_name"], "Mom")
        self.assertEqual(payload["to_name"], "账号E")

    def test_event_failure_swallowed(self):
        store = _FakeStore(error=sqlite3_error())
        record_account_switch_event(
            store, "run-1", from_account="b", to_account="e")
        self.assertEqual(store.events, [])

    def test_log_line_appended_with_fallback_id(self):
        task = {"logs": []}
        append_account_switch_log_line(
            task, from_account="b", to_account="zzz", accounts={})
        self.assertEqual(
            task["logs"], ["本次从账号「b」切换到账号「zzz」继续"])

    def test_log_line_creates_missing_logs(self):
        task = {}
        append_account_switch_log_line(task, from_account="b", to_account="e")
        self.assertEqual(len(task["logs"]), 1)

    def test_log_line_none_task_noop(self):
        append_account_switch_log_line(None, from_account="b", to_account="e")

    def test_display_name_prefers_book_name(self):
        self.assertEqual(
            account_display_name("b", {"b": {"name": "Mom"}}), "Mom")
        self.assertEqual(account_display_name("ghost", {}), "ghost")


def sqlite3_error():
    import sqlite3
    return sqlite3.OperationalError("db locked")


class EnsureFrozenAccountTests(unittest.TestCase):
    """FR-007：缺冻结账号时的角色感知兜底与写回。"""

    def test_existing_frozen_returned_without_write(self):
        store = _FakeStore()
        run = {"execution_params": {"browser_account": "b"}}
        resolved = ensure_frozen_browser_account(
            store, "run-1", run, platform="boss", fallback_account="d",
            role="R2")
        self.assertEqual(resolved, "b")
        self.assertEqual(store.params_writes, [])

    def test_boss_missing_uses_r2_role_and_writes_back(self):
        store = _FakeStore()
        run = {"execution_params": {}}
        with mock.patch("webui.pipeline_exec_accounts.account_for_role",
                        return_value="b") as role:
            resolved = ensure_frozen_browser_account(
                store, "run-1", run, platform="boss", fallback_account="d",
                accounts_path="/tmp/accounts.json", role="R2")
        self.assertEqual(resolved, "b")
        role.assert_called_once_with("R2", "/tmp/accounts.json", run=run,
                                     fallback="d")
        self.assertEqual(store.params_writes, [("run-1", {"browser_account": "b"})])

    def test_zhilian_missing_falls_back_without_role(self):
        store = _FakeStore()
        run = {"execution_params": {}}
        with mock.patch("webui.pipeline_exec_accounts.account_for_role") as role:
            resolved = ensure_frozen_browser_account(
                store, "run-1", run, platform="zhilian",
                fallback_account="d")
        self.assertEqual(resolved, "d")
        role.assert_not_called()
        self.assertEqual(store.params_writes, [("run-1", {"browser_account": "d"})])

    def test_boss_without_role_falls_back(self):
        store = _FakeStore()
        run = {"execution_params": {}}
        with mock.patch("webui.pipeline_exec_accounts.account_for_role") as role:
            resolved = ensure_frozen_browser_account(
                store, "run-1", run, platform="boss", fallback_account="d")
        self.assertEqual(resolved, "d")
        role.assert_not_called()

    def test_unresolvable_returns_empty_without_write(self):
        store = _FakeStore()
        run = {"execution_params": {}}
        with mock.patch("webui.pipeline_exec_accounts.account_for_role",
                        return_value=""):
            resolved = ensure_frozen_browser_account(
                store, "run-1", run, platform="boss", fallback_account="",
                role="R2")
        self.assertEqual(resolved, "")
        self.assertEqual(store.params_writes, [])


if __name__ == "__main__":
    unittest.main()
