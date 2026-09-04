"""Spec 038 B091 轮询分摊调度域聚焦测试。

覆盖：plan_round_robin 配额分摊/多轮覆盖/勾选顺序/末轮零头；
RotationQueue 撞墙换号顺次接力；is_wall_code 判定；ListRobin 子范围+hash
重算+撞墙换号；DetailRobin advance/switch_next；engagement 规则保护既有
替身零行为变更。

纯调度部分不触碰文件系统/浏览器；IO 编排部分用替身 source + monkeypatch
切换/克隆，断言调度行为而非真实 CDP。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import webui.account_round_robin as robin_mod
from webui.account_round_robin import (
    DEFAULT_R1_QUOTA,
    DEFAULT_R2_QUOTA,
    DetailRobin,
    ListRobin,
    PoolEntry,
    RotationQueue,
    is_wall_code,
    make_detail_robin,
    make_list_robin,
    plan_round_robin,
)


# ---------------------------------------------------------------------------
# 纯调度：plan_round_robin
# ---------------------------------------------------------------------------

class PlanRoundRobinTests(unittest.TestCase):
    """FR-003/007/008：配额分摊、勾选顺序、末轮零头、多轮覆盖。"""

    def _accounts(self, ids: list[str], quota: int = 25) -> list[PoolEntry]:
        return [PoolEntry(a, quota) for a in ids]

    def test_basic_split_follows_check_order(self):
        segs = plan_round_robin(60, self._accounts(["a", "b", "c"]))
        self.assertEqual([(s.account_id, s.count) for s in segs],
                         [("a", 25), ("b", 25), ("c", 10)])
        # 连续无重叠、覆盖 0..60
        self.assertEqual(segs[0].start, 0)
        for prev, cur in zip(segs, segs[1:]):
            self.assertEqual(cur.start, prev.start + prev.count)
        self.assertEqual(segs[-1].start + segs[-1].count, 60)

    def test_multi_round_wraps_to_first(self):
        # 3 账号 ×25=75 一轮，80 需第二轮，从 a 开始抓 5 零头
        segs = plan_round_robin(80, self._accounts(["a", "b", "c"]))
        self.assertEqual([(s.account_id, s.count) for s in segs],
                         [("a", 25), ("b", 25), ("c", 25), ("a", 5)])

    def test_single_account_multi_round(self):
        segs = plan_round_robin(60, self._accounts(["a"]))
        self.assertEqual([(s.account_id, s.count) for s in segs],
                         [("a", 25), ("a", 25), ("a", 10)])

    def test_tail_partial_to_next_account(self):
        # 30 < 50（一轮容量），零头归 b
        segs = plan_round_robin(30, self._accounts(["a", "b"]))
        self.assertEqual([(s.account_id, s.count) for s in segs],
                         [("a", 25), ("b", 5)])

    def test_empty_inputs(self):
        self.assertEqual(plan_round_robin(0, self._accounts(["a"])), [])
        self.assertEqual(plan_round_robin(10, []), [])

    def test_heterogeneous_quotas(self):
        segs = plan_round_robin(40, [PoolEntry("a", 10), PoolEntry("b", 30)])
        # a10 → b30（一轮 40，b 抓 30）；无零头
        self.assertEqual([(s.account_id, s.count) for s in segs],
                         [("a", 10), ("b", 30)])


# ---------------------------------------------------------------------------
# RotationQueue：撞墙换号顺次接力
# ---------------------------------------------------------------------------

class RotationQueueTests(unittest.TestCase):

    def test_reserve_rotates_on_quota_exhaustion(self):
        q = RotationQueue([PoolEntry("a", 25), PoolEntry("b", 25)])
        e1, t1 = q.reserve(30)
        self.assertEqual((e1.account_id, t1), ("a", 25))
        # a 配额耗尽轮转到队尾，b 成队首
        self.assertEqual(q.head_account, "b")
        e2, t2 = q.reserve(10)
        self.assertEqual((e2.account_id, t2), ("b", 10))

    def test_block_head_removes_walled_account(self):
        q = RotationQueue([PoolEntry("a", 25), PoolEntry("b", 25), PoolEntry("c", 25)])
        self.assertTrue(q.has_alternative())
        removed = q.block_head()
        self.assertEqual(removed.account_id, "a")
        self.assertEqual(q.head_account, "b")
        self.assertIn("a", q.blocked_accounts)

    def test_block_account_removes_reserved_account_not_current_head(self):
        q = RotationQueue([PoolEntry("a", 25), PoolEntry("b", 25), PoolEntry("c", 25)])
        reserved, taken = q.reserve(25)
        self.assertEqual((reserved.account_id, taken), ("a", 25))
        self.assertEqual(q.head_account, "b")
        removed = q.block_account("a")
        self.assertEqual(removed.account_id, "a")
        self.assertEqual(q.head_account, "b")
        self.assertIn("a", q.blocked_accounts)

    def test_block_until_empty_signals_all_walled(self):
        q = RotationQueue([PoolEntry("a", 25), PoolEntry("b", 25)])
        q.block_head()  # a 撞墙
        self.assertEqual(q.head_account, "b")
        q.block_head()  # b 撞墙
        self.assertIsNone(q.head_account)
        self.assertFalse(q.has_alternative())

    def test_single_account_keeps_rotation(self):
        # 单账号：配额耗尽轮转回自己（多轮）；调用方按剩余工作量取号
        q = RotationQueue([PoolEntry("a", 5)])
        e, t = q.reserve(8)        # 剩余 8，a 取 5（配额上限）
        self.assertEqual((e.account_id, t), ("a", 5))
        e2, t2 = q.reserve(3)      # 剩余 3，a 取 3（零头）
        self.assertEqual((e2.account_id, t2), ("a", 3))


# ---------------------------------------------------------------------------
# is_wall_code
# ---------------------------------------------------------------------------

class WallCodeTests(unittest.TestCase):

    def test_systemic_block_is_wall(self):
        # source_rate_limited / source_verification_required 属系统性阻断
        self.assertTrue(is_wall_code("source_rate_limited"))
        self.assertTrue(is_wall_code("source_verification_required"))

    def test_browser_lost_not_wall(self):
        # 浏览器失联交 BrowserRecovery，不经账号切换
        self.assertFalse(is_wall_code("source_cdp_unavailable"))
        self.assertFalse(is_wall_code("cdp_unavailable"))

    def test_empty_or_soft_not_wall(self):
        self.assertFalse(is_wall_code(""))
        self.assertFalse(is_wall_code(None))
        self.assertFalse(is_wall_code("source_timeout"))


class CloneSourceTests(unittest.TestCase):
    def test_zhilian_clone_keeps_runners_and_uses_new_profile_key(self):
        from webui.source import ZhilianCdpSource

        preflight = mock.Mock()
        list_runner = mock.Mock()
        detail_runner = mock.Mock()
        batch_runner = mock.Mock()
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=preflight, list_runner=list_runner,
            detail_runner=detail_runner, batch_detail_runner=batch_runner,
        )
        clone = robin_mod.clone_source(source, "b", run_id="run-b")
        self.assertEqual(clone.browser_account, "b")
        self.assertEqual(clone.profile_key, "zhilian:b")
        self.assertIsNot(clone.breaker, source.breaker)
        self.assertIs(clone._preflight_runner, preflight)
        self.assertIs(clone._list_runner, list_runner)
        self.assertIs(clone._detail_runner, detail_runner)
        self.assertIs(clone._batch_detail_runner, batch_runner)

    def test_boss_clone_keeps_non_default_constructor_configuration(self):
        from webui.source import BossCdpSource

        runner = mock.Mock()
        source = BossCdpSource(
            browser_account="a", cdp_port=9222, run_id="run-a",
            cwd="C:\\custom-cwd", scraper_path="C:\\custom-scraper.py",
            env={"CUSTOM": "1"}, timeout_seconds=17, runner=runner,
            artifact_root="C:\\artifacts", max_artifact_bytes=1234,
            in_process=True,
        )
        clone = robin_mod.clone_source(source, "b", run_id="run-b")
        self.assertEqual(clone.browser_account, "b")
        self.assertEqual(clone.cdp_port, 9222)
        self.assertEqual(clone.cwd, source.cwd)
        self.assertEqual(clone.scraper_path, source.scraper_path)
        self.assertEqual(clone.env["CUSTOM"], "1")
        self.assertEqual(clone.env["CAREER_SCOUT_TASK_ID"], "run-b")
        self.assertEqual(clone.timeout_seconds, 17)
        self.assertIs(clone._runner, runner)
        self.assertIsNot(clone._executor, source._executor)
        self.assertEqual(clone.max_artifact_bytes, 1234)
        self.assertTrue(clone.in_process)


# ---------------------------------------------------------------------------
# ListRobin：子范围 + hash 重算 + 撞墙换号（替身 source + monkeypatch）
# ---------------------------------------------------------------------------

class _FakeOutcome:
    def __init__(self, ok, *, jobs=None, failed_code=None):
        self.ok = ok
        self.jobs = jobs or []
        self.failed_code = failed_code
        self.safe_log = ""
        self.input_hash = None


class _FakeSource:
    """替身 source：记录每次 fetch_list 的 plan_item，按脚本返回 outcome。"""

    def __init__(self, account_id, outcomes, platform="boss", cdp_port=9222):
        self.browser_account = account_id
        self.platform = platform
        self.cdp_port = cdp_port
        self.run_id = "run-x"
        self.calls = []
        self._outcomes = list(outcomes)
        self.cancel_event = None

    def fetch_list(self, plan_item, *, on_page_completed=None):
        self.calls.append(dict(plan_item))
        if not self._outcomes:
            return _FakeOutcome(True, jobs=[])
        return self._outcomes.pop(0)


class _EventStore:
    """仅收集结构化任务事件，验证 038 白箱摘要，不落真实数据库。"""

    def __init__(self):
        self.events = []

    def append_task_event(self, run_id, event_type, payload=None):
        self.events.append((str(run_id), str(event_type), dict(payload or {})))


class _TempBook:
    """临时账号簿：set_path 时写 pool/rate_limited schema，结束后清理。
    accounts 属性可先 mutate（如取消某账号选中）再 set_path。"""

    def __init__(self, accounts):
        self.dir = tempfile.mkdtemp(prefix="cs_robin_")
        self.path = os.path.join(self.dir, "browser_accounts.json")
        self.accounts = dict(accounts)

    def set_path(self):
        import json
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, ensure_ascii=False)
        set_browser_accounts_path(self.path)
        return self.path

    def cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


def _make_book(*accounts, r1=DEFAULT_R1_QUOTA, r2=DEFAULT_R2_QUOTA, selected=True):
    """accounts: [(id, name), ...] → 写临时账号簿（pool schema）并返回 _TempBook。"""
    out = {}
    for i, (aid, name) in enumerate(accounts):
        out[aid] = {
            "id": aid, "name": name,
            "profile_dir": os.path.join(tempfile.gettempdir(), f"profile_{aid}"),
            "builtin": aid == "a",
            "pool": {"selected": selected, "order": i,
                     "r1_quota": r1, "r2_quota": r2},
            "rate_limited": False,
        }
    return _TempBook(out)


class ListRobinTests(unittest.TestCase):

    def setUp(self):
        self._restore = robin_mod._switch_browser_account
        # 切换全程打桩：返回 True，避免真实 CDP
        robin_mod._switch_browser_account = lambda aid, plat, port: True
        self._clone_restore = robin_mod.clone_source
        # clone：用 _FakeSource 替身，记账号
        def _clone(template, account_id, *, run_id=""):
            return _FakeSource(account_id, [], platform=template.platform,
                               cdp_port=getattr(template, "cdp_port", 9222))
        robin_mod.clone_source = _clone
        # rate_limited 持久化打桩（避免写盘）
        robin_mod.mark_account_rate_limited = lambda *a, **k: None
        robin_mod.clear_account_rate_limited = lambda *a, **k: None

    def tearDown(self):
        robin_mod._switch_browser_account = self._restore
        robin_mod.clone_source = self._clone_restore

    def _source_in_pool(self, aid="a", outcomes=None, platform="boss", port=9222):
        return _FakeSource(aid, outcomes or [], platform=platform, cdp_port=port)

    def test_single_subrange_unchanged_when_quota_covers_whole(self):
        # 配额 25 ≥ 页数 3 → 单子范围，hash 与原一致，source 不切换
        book = _make_book(("a", "A"), ("b", "B"))
        self.addCleanup(book.cleanup)
        path = book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())

        # a 抓 1 页成功即结束（quota 25 远超 target 1）
        src = self._source_in_pool("a", [_FakeOutcome(True, jobs=[{"job_id": "j1"}])])
        entries = robin_mod._engaged_entries(src, "R1")
        self.assertEqual([e.account_id for e in entries], ["a", "b"])
        r = ListRobin(src, entries, run_id="r")
        plan = {"keyword": "前端", "city": "上海", "combo_key": "前端|上海",
                "start_page": 1, "target_pages": 1, "source_filters": {},
                "input_hash": "H", "platform": "boss"}
        out = r.fetch_list(src, plan)
        self.assertTrue(out.ok)
        # 只调了一次 fetch_list（a），b 未被克隆/调用
        self.assertEqual(len(src.calls), 1)
        # 子范围 start/target 与原一致
        self.assertEqual(src.calls[0]["start_page"], 1)
        self.assertEqual(src.calls[0]["target_pages"], 1)

    def test_subrange_split_across_accounts_by_quota(self):
        # 2 账号各配额 2，target 5 → a:2, b:2, a:1（多轮零头回 a）
        book = _make_book(("a", "A"), ("b", "B"), r1=2)
        self.addCleanup(book.cleanup)
        path = book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())

        src = self._source_in_pool("a", [
            _FakeOutcome(True, jobs=[{"job_id": "p1"}]),   # a 子范围1
            _FakeOutcome(True, jobs=[{"job_id": "p1"}, {"job_id": "p3"}]),  # a 子范围2（多轮）
        ])
        entries = robin_mod._engaged_entries(src, "R1")
        r = ListRobin(src, entries, run_id="r")
        plan = {"keyword": "k", "city": "c", "combo_key": "k|c",
                "start_page": 1, "target_pages": 5, "source_filters": {},
                "input_hash": "H", "platform": "boss"}
        with mock.patch.object(robin_mod, "clone_source",
                               side_effect=lambda t, aid, **k: _FakeSource(
                                   aid, [_FakeOutcome(True, jobs=[{"job_id": "p1"},{"job_id":"p2"}])],
                                   platform="boss", cdp_port=9222)) as mc:
            out = r.fetch_list(src, plan)
        self.assertTrue(out.ok)
        # a 调了 2 次（首段 + 末轮零头），b 被克隆调 1 次
        self.assertEqual(len(src.calls), 2)
        self.assertEqual(mc.call_count, 1)
        # 子范围 target 递进：a[1..2], b[3..4], a[5..5]
        self.assertEqual(src.calls[0]["start_page"], 1)
        self.assertEqual(src.calls[0]["target_pages"], 2)
        self.assertEqual(src.calls[1]["start_page"], 5)
        self.assertEqual(src.calls[1]["target_pages"], 5)

    def test_wall_switches_to_next_account_relays_remainder(self):
        # a 撞墙（source_rate_limited）→ 切 b 接力同 start
        book = _make_book(("a", "A"), ("b", "B"), r1=25)
        self.addCleanup(book.cleanup)
        path = book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())

        src = self._source_in_pool("a", [
            _FakeOutcome(False, failed_code="source_rate_limited"),  # a 撞墙
        ])
        entries = robin_mod._engaged_entries(src, "R1")
        r = ListRobin(src, entries, run_id="r")
        plan = {"keyword": "k", "city": "c", "combo_key": "k|c",
                "start_page": 1, "target_pages": 3, "source_filters": {},
                "input_hash": "H", "platform": "boss"}
        b_src = _FakeSource("b", [_FakeOutcome(True, jobs=[{"job_id": "j1"}])])
        with mock.patch.object(robin_mod, "clone_source",
                               return_value=b_src):
            out = r.fetch_list(src, plan)
        self.assertTrue(out.ok)
        # b 接力同 start_page=1（剩余份额不丢）
        self.assertEqual(b_src.calls[0]["start_page"], 1)

    def test_cached_source_rebinds_profile_when_account_is_reused(self):
        book = _make_book(("a", "A"), ("b", "B"), r1=1)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())

        src = self._source_in_pool("a", [
            _FakeOutcome(True, jobs=[{"job_id": "a1"}]),
            _FakeOutcome(True, jobs=[{"job_id": "a2"}]),
        ])
        entries = robin_mod._engaged_entries(src, "R1")
        r = ListRobin(src, entries, run_id="r")
        switches = []
        with mock.patch.object(
            robin_mod, "_switch_browser_account",
            side_effect=lambda aid, _platform, _port: switches.append(aid) or True,
        ), mock.patch.object(
            robin_mod, "clone_source",
            return_value=_FakeSource("b", [_FakeOutcome(True, jobs=[{"job_id": "b1"}])]),
        ):
            out = r.fetch_list(src, {
                "keyword": "k", "city": "c", "combo_key": "k|c",
                "start_page": 1, "target_pages": 3, "source_filters": {},
                "input_hash": "H", "platform": "boss",
            })
        self.assertTrue(out.ok)
        self.assertEqual(switches, ["b", "a"])

    def test_whitebox_records_r1_segments_and_quota_switches(self):
        book = _make_book(("a", "A"), ("b", "B"), r1=1)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        store = _EventStore()
        src = self._source_in_pool("a", [
            _FakeOutcome(True, jobs=[{"job_id": "a1"}]),
            _FakeOutcome(True, jobs=[{"job_id": "a3"}]),
        ])
        robin = ListRobin(
            src, robin_mod._engaged_entries(src, "R1"), run_id="run-r1",
            switch_event_store=store,
        )
        out = robin.fetch_list(src, {
            "keyword": "k", "city": "c", "combo_key": "k|c",
            "start_page": 1, "target_pages": 3, "source_filters": {},
            "input_hash": "H", "platform": "boss",
        })
        self.assertTrue(out.ok)
        whitebox = [event for event in store.events if event[1] != "whitebox_incomplete"]
        self.assertEqual(whitebox[0][1], "account_pool_snapshot")
        allocations = [event[2] for event in whitebox if event[1] == "account_allocation"]
        self.assertEqual([(item["account_id"], item["count"], item["round"])
                          for item in allocations],
                         [("a", 1, 1), ("b", 1, 1), ("a", 1, 2)])
        switches = [event[2] for event in whitebox if event[1] == "account_switch"]
        self.assertEqual([(item["from_account"], item["to_account"], item["reason"], item["result"])
                          for item in switches],
                         [("a", "b", "quota", "succeeded"),
                          ("b", "a", "quota", "succeeded")])
        for _, _, payload in whitebox:
            self.assertNotIn("Cookie", repr(payload))
            self.assertNotIn("token", repr(payload).lower())

    def test_whitebox_write_failure_is_explicit_but_does_not_break_r1(self):
        class _BrokenStore:
            def append_task_event(self, *_args, **_kwargs):
                raise RuntimeError("disk full")

        book = _make_book(("a", "A"), ("b", "B"), r1=1)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = self._source_in_pool("a", [_FakeOutcome(True, jobs=[{"job_id": "a1"}])])
        with self.assertLogs("career_scout.webui.account_round_robin", level="WARNING") as logs:
            robin = ListRobin(
                src, robin_mod._engaged_entries(src, "R1"), run_id="run-broken",
                switch_event_store=_BrokenStore(),
            )
            out = robin.fetch_list(src, {
                "keyword": "k", "city": "c", "combo_key": "k|c",
                "start_page": 1, "target_pages": 1, "source_filters": {},
                "input_hash": "H", "platform": "boss",
            })
        self.assertTrue(out.ok)
        self.assertTrue(any("白箱" in line for line in logs.output))

    def test_wall_retry_starts_after_pages_already_checkpointed(self):
        book = _make_book(("a", "A"), ("b", "B"), r1=3)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())

        class _PartialSource(_FakeSource):
            def fetch_list(self, plan_item, *, on_page_completed=None):
                self.calls.append(dict(plan_item))
                if self.browser_account == "a":
                    on_page_completed({"page": 1, "resume_page": 2})
                    return _FakeOutcome(False, failed_code="source_rate_limited")
                return _FakeOutcome(True, jobs=[{"job_id": "j1"}])

        source_a = _PartialSource("a", [])
        source_b = _PartialSource("b", [])
        r = ListRobin(source_a, robin_mod._engaged_entries(source_a, "R1"), run_id="r")
        with mock.patch.object(robin_mod, "clone_source", return_value=source_b):
            out = r.fetch_list(source_a, {
                "keyword": "k", "city": "c", "combo_key": "k|c",
                "start_page": 1, "target_pages": 3, "source_filters": {},
                "input_hash": "H", "platform": "boss",
            }, on_page_completed=lambda _event: None)
        self.assertTrue(out.ok)
        self.assertEqual(source_b.calls[0]["start_page"], 2)

    def test_profile_switch_failure_is_not_marked_as_rate_limited(self):
        book = _make_book(("a", "A"), ("b", "B"), r1=1)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        source_a = self._source_in_pool("a", [
            _FakeOutcome(True, jobs=[{"job_id": "j1"}]),
        ])
        r = ListRobin(source_a, robin_mod._engaged_entries(source_a, "R1"), run_id="r")
        with mock.patch.object(robin_mod, "_switch_browser_account", return_value=False), \
                mock.patch.object(robin_mod, "mark_account_rate_limited") as mark:
            out = r.fetch_list(source_a, {
                "keyword": "k", "city": "c", "combo_key": "k|c",
                "start_page": 1, "target_pages": 2, "source_filters": {},
                "input_hash": "H", "platform": "boss",
            })
        self.assertTrue(out.ok)
        mark.assert_not_called()

    def test_all_walled_returns_failure_for_pause(self):
        # 全撞完 → 返回失败 outcome 交既有暂停路径（FR-013）
        book = _make_book(("a", "A"), ("b", "B"), r1=25)
        self.addCleanup(book.cleanup)
        path = book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())

        src = self._source_in_pool("a", [
            _FakeOutcome(False, failed_code="source_rate_limited"),
        ])
        entries = robin_mod._engaged_entries(src, "R1")
        r = ListRobin(src, entries, run_id="r")
        plan = {"keyword": "k", "city": "c", "combo_key": "k|c",
                "start_page": 1, "target_pages": 3, "source_filters": {},
                "input_hash": "H", "platform": "boss"}
        b_src = _FakeSource("b", [
            _FakeOutcome(False, failed_code="source_verification_required"),
        ])
        with mock.patch.object(robin_mod, "clone_source", return_value=b_src):
            out = r.fetch_list(src, plan)
        self.assertFalse(out.ok)
        # 全撞完返回最后一个真实失败 outcome（b 的 source_verification_required），
        # 交既有暂停路径处理（FR-013：系统性阻断走现有"暂停"，不新增报错字段）
        self.assertEqual(out.failed_code, "source_verification_required")


# ---------------------------------------------------------------------------
# DetailRobin：advance + switch_next
# ---------------------------------------------------------------------------

class DetailRobinTests(unittest.TestCase):

    def setUp(self):
        self._sw = robin_mod._switch_browser_account
        self._cl = robin_mod.clone_source
        self._clear = robin_mod.clear_account_rate_limited
        robin_mod._switch_browser_account = lambda aid, plat, port: True
        robin_mod.mark_account_rate_limited = lambda *a, **k: None

    def tearDown(self):
        robin_mod._switch_browser_account = self._sw
        robin_mod.clone_source = self._cl
        robin_mod.clear_account_rate_limited = self._clear

    def test_current_source_uses_template_for_head(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=100)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        entries = robin_mod._engaged_entries(src, "R2")
        r = DetailRobin(src, entries, run_id="r")
        # 队首=a → 直返传入 source，不克隆
        self.assertIs(r.current_source(), src)

    def test_switch_next_advances_to_next_blocked_set(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=100)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        entries = robin_mod._engaged_entries(src, "R2")
        r = DetailRobin(src, entries, run_id="r")
        self.assertTrue(r.switch_next())  # a→b
        b_src = _FakeSource("b", [], platform="boss", cdp_port=9222)
        with mock.patch.object(robin_mod, "clone_source", return_value=b_src):
            self.assertIs(r.current_source(), b_src)
        self.assertIn("a", r.blocked_accounts)
        # b 再撞墙 → 全撞完
        self.assertFalse(r.switch_next())

    def test_switch_next_reports_event_through_callback(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=100)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        events = []
        r = DetailRobin(
            src, robin_mod._engaged_entries(src, "R2"), run_id="r",
            on_account_switch=lambda from_id, to_id: events.append((from_id, to_id)),
        )
        self.assertTrue(r.switch_next())
        self.assertEqual(events, [("a", "b")])

    def test_factory_records_wall_switch_event_after_actual_binding(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=100)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        store = mock.Mock()
        with mock.patch(
            "webui.resume_identity.record_account_switch_event"
        ) as record:
            r = make_detail_robin(src, run_id="run-1", switch_event_store=store)
            self.assertIsNotNone(r)
            self.assertTrue(r.switch_next())
            with mock.patch.object(robin_mod, "clone_source",
                                   return_value=_FakeSource("b", [])):
                self.assertIsNotNone(r.current_source())
        record.assert_called_once_with(
            store, "run-1", from_account="a", to_account="b",
            phase="R2", reason="wall", result="succeeded",
        )

    def test_whitebox_records_r2_segments_and_quota_switch(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=2)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        store = _EventStore()
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        robin = DetailRobin(
            src, robin_mod._engaged_entries(src, "R2"), run_id="run-r2",
            switch_event_store=store,
        )
        first = robin.allocate(["j1", "j2", "j3"])
        self.assertEqual(first.account_id, "a")
        self.assertEqual(len(first.entries), 2)
        second = robin.allocate(["j3", "j4"])
        self.assertEqual(second.account_id, "b")
        self.assertEqual(len(second.entries), 2)
        with mock.patch.object(robin_mod, "clone_source",
                               return_value=_FakeSource("b", [])):
            robin.source_for(second.account_id)
        allocations = [event[2] for event in store.events
                       if event[1] == "account_allocation"]
        self.assertEqual([(item["account_id"], item["count"], item["round"])
                          for item in allocations],
                         [("a", 2, 1), ("b", 2, 1)])
        switches = [event[2] for event in store.events if event[1] == "account_switch"]
        self.assertEqual([(item["from_account"], item["to_account"], item["reason"])
                          for item in switches], [("a", "b", "quota")])

    def test_advance_rotates_on_quota_exhaustion(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=2)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        entries = robin_mod._engaged_entries(src, "R2")
        r = DetailRobin(src, entries, run_id="r")
        r.advance(2)  # a 配额耗尽 → 轮转 b 成队首
        self.assertEqual(r._queue.head_account, "b")

    def test_advance_spans_accounts_when_n_exceeds_quota(self):
        # advance(3) 配额 a=2,b=2 → a 扣2轮转, b 扣1，跨账号累计扣完
        book = _make_book(("a", "A"), ("b", "B"), r2=2)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        entries = robin_mod._engaged_entries(src, "R2")
        r = DetailRobin(src, entries, run_id="r")
        r.advance(3)  # a:2 耗尽轮转, b:扣1
        self.assertEqual(r._queue.head_account, "b")
        # b 还剩 1 配额（2-1），再 advance(1) 扣完轮转回 a
        r.advance(1)
        self.assertEqual(r._queue.head_account, "a")

    def test_reserve_caps_batch_to_current_account_quota(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=2)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        r = DetailRobin(src, robin_mod._engaged_entries(src, "R2"), run_id="r")
        entry, take = r.reserve(5)
        self.assertEqual((entry.account_id, take), ("a", 2))
        self.assertEqual(r._queue.head_account, "b")

    def test_cached_source_rebinds_profile_when_account_is_reused(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=1)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        r = DetailRobin(src, robin_mod._engaged_entries(src, "R2"), run_id="r")
        switches = []
        with mock.patch.object(
            robin_mod, "_switch_browser_account",
            side_effect=lambda aid, _platform, _port: switches.append(aid) or True,
        ), mock.patch.object(
            robin_mod, "clone_source",
            return_value=_FakeSource("b", [], platform="boss", cdp_port=9222),
        ):
            r.advance(1)
            r.current_source()
            r.advance(1)
            r.current_source()
        self.assertEqual(switches, ["b", "a"])

    def test_mark_success_clears_the_account_used_for_the_batch(self):
        book = _make_book(("a", "A"), ("b", "B"), r2=1)
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        r = DetailRobin(src, robin_mod._engaged_entries(src, "R2"), run_id="r")
        with mock.patch.object(robin_mod, "clear_account_rate_limited") as clear:
            r.mark_success("a")
        clear.assert_called_once_with("a")


# ---------------------------------------------------------------------------
# engagement 规则：保护既有替身零行为变更
# ---------------------------------------------------------------------------

class EngagementTests(unittest.TestCase):

    def test_no_browser_account_disengages(self):
        # 既有测试替身无 browser_account → 不轮询（legacy）
        class _Bare:
            platform = "boss"
            cdp_port = 9222
        self.assertIsNone(make_list_robin(_Bare()))
        self.assertIsNone(make_detail_robin(_Bare()))

    def test_account_not_in_pool_disengages(self):
        # source 账号不在选中池 → legacy
        book = _make_book(("a", "A"), ("b", "B"))
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("zzz", [], platform="boss", cdp_port=9222)
        self.assertIsNone(make_list_robin(src))
        self.assertIsNone(make_detail_robin(src))

    def test_single_selected_disengages(self):
        # 仅 1 选中账号 → 不轮询（无第二个可换）
        book = _make_book(("a", "A"), ("b", "B"))
        # b 取消选中
        book.accounts["b"]["pool"]["selected"] = False
        self.addCleanup(book.cleanup)
        book.set_path()
        self.addCleanup(lambda: __import__(
            "webui.pipeline_exec_accounts", fromlist=["reset_browser_accounts_path"]
        ).reset_browser_accounts_path())
        src = _FakeSource("a", [], platform="boss", cdp_port=9222)
        self.assertIsNone(make_list_robin(src))


class WiringTests(unittest.TestCase):
    """R1/R2 接线契约：防轮询入口被误删回归（Spec 038 收口）。"""

    def test_fetch_job_details_reserves_detail_quota(self):
        """契约：R2 每次请求先按当前账号配额预留，避免整窗绕过限额。"""
        import inspect
        from webui import pipeline_exec_details
        src = inspect.getsource(pipeline_exec_details.fetch_job_details)
        self.assertIn(
            "detail_robin.allocate", src,
            "R2 接线必须按当前账号配额领取请求（Spec 038 FR-003/005）",
        )

    def test_search_wiring_uses_list_robin(self):
        """契约：R1 列表抓取接线 make_list_robin 调用。"""
        import inspect
        from webui import pipeline_exec_search
        src = inspect.getsource(pipeline_exec_search)
        self.assertIn("make_list_robin", src, "R1 接线必须调 make_list_robin")

    def test_search_wiring_initializes_list_robin_once_per_task(self):
        import inspect
        from webui import pipeline_exec_search
        src = inspect.getsource(pipeline_exec_search.run_search)
        self.assertLess(
            src.index("list_robin = make_list_robin"),
            src.index("for idx, combo in enumerate(combos)"),
        )

    def test_detail_wiring_uses_resume_identity_switch_events(self):
        import inspect
        from webui import account_round_robin
        src = inspect.getsource(account_round_robin.RoundRobinWhitebox.switch)
        self.assertIn("resume_identity", src)
        self.assertIn("record_account_switch_event", src)


class DetailPipelineRoundRobinTests(unittest.TestCase):
    """R2 quota allocation and mixed wall/success retry behavior."""

    def test_detail_batch_is_split_by_quota_and_retries_only_wall_items(self):
        from webui.source import SourceOutcome
        from webui.pipeline_exec_details import fetch_job_details

        jobs = [{"job_id": "j1"}, {"job_id": "j2"}, {"job_id": "j3"}]

        class _DetailSource:
            platform = "boss"
            cdp_port = 9222

            def __init__(self, account_id, behavior):
                self.browser_account = account_id
                self.behavior = behavior
                self.calls = []

            def fetch_details_batch(self, batch, **_kwargs):
                ids = [job["job_id"] for job in batch]
                self.calls.append(ids)
                return self.behavior(ids)

        def a_behavior(ids):
            return {
                jid: SourceOutcome.success(detail={"jd": f"JD-{jid}"})
                for jid in ids
            }

        def b_behavior(ids):
            return {
                jid: SourceOutcome.failure(
                    failed_code="source_rate_limited", safe_log="rate limited"
                )
                for jid in ids
            }

        source_a = _DetailSource("a", a_behavior)
        source_b = _DetailSource("b", b_behavior)
        robin = DetailRobin(
            source_a, [PoolEntry("a", 1), PoolEntry("b", 1)], run_id="run-x"
        )
        config = SimpleNamespace(
            detail_batch_size=3,
            detail_interval=0,
            detail_reset_every=0,
            detail_batch_cooldown=0,
            detail_tab_pool_size=1,
        )

        with tempfile.TemporaryDirectory() as artifact_dir, \
                mock.patch.object(robin_mod, "make_detail_robin", return_value=robin), \
                mock.patch.object(robin_mod, "clone_source", return_value=source_b), \
                mock.patch.object(robin_mod, "_switch_browser_account", return_value=True), \
                mock.patch("webui.pipeline_exec.load_advanced_settings", return_value={}), \
                mock.patch("webui.pipeline_exec_details.time.sleep"):
            result = fetch_job_details(
                jobs, source_a, artifact_dir=artifact_dir,
                execution_config=config,
            )

        self.assertFalse(result["hard_stop"], result)
        self.assertEqual(result["fetched"], 3)
        self.assertEqual(source_a.calls, [["j1"], ["j2"], ["j3"]])
        self.assertEqual(source_b.calls, [["j2"]])
        self.assertEqual({job["job_id"]: job["jd"] for job in result["jobs"]}, {
            "j1": "JD-j1", "j2": "JD-j2", "j3": "JD-j3",
        })


if __name__ == "__main__":
    unittest.main()
