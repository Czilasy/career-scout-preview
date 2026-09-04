"""V4 R2 组合回归：跨外层详情分块必须延续同一轮询会话。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import webui.account_round_robin as robin_mod
from webui.account_round_robin import PoolEntry
from webui.source import SourceOutcome


class _AccountBook:
    """隔离的账号池夹具，不读取用户正式账号配置。"""

    def __init__(self, account_ids: tuple[str, ...], *, r2_quota: int):
        self.root = tempfile.TemporaryDirectory(prefix="cs_v4_accounts_")
        self.path = os.path.join(self.root.name, "browser_accounts.json")
        accounts = {}
        for order, account_id in enumerate(account_ids):
            accounts[account_id] = {
                "id": account_id,
                "name": f"账号-{account_id}",
                "profile_dir": os.path.join(self.root.name, f"profile-{account_id}"),
                "builtin": account_id == account_ids[0],
                "pool": {
                    "selected": True,
                    "order": order,
                    "r1_quota": 25,
                    "r2_quota": r2_quota,
                },
                "rate_limited": False,
            }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(accounts, handle, ensure_ascii=False)

    def close(self) -> None:
        self.root.cleanup()


class _DetailSource:
    platform = "boss"
    cdp_port = 9222
    run_id = "v4-r2"
    cancel_event = None

    def __init__(self, account_id: str):
        self.browser_account = account_id
        self.requests: list[list[str]] = []

    def fetch_details_batch(self, jobs, **_kwargs):
        ids = [str(job["job_id"]) for job in jobs]
        self.requests.append(ids)
        return {
            job_id: SourceOutcome.success(detail={"jd": f"JD-{job_id}"})
            for job_id in ids
        }


class _PartialDetailSource(_DetailSource):
    def fetch_details_batch(self, jobs, **kwargs):
        ids = [str(job["job_id"]) for job in jobs]
        self.requests.append(ids)
        return {
            job_id: (
                SourceOutcome.success(detail={"jd": f"JD-{job_id}"})
                if index < 18 else SourceOutcome.failure(
                    failed_code="source_rate_limited", safe_log="blocked"
                )
            )
            for index, job_id in enumerate(ids)
        }


class _BlockingDetailSource(_DetailSource):
    def fetch_details_batch(self, jobs, **kwargs):
        ids = [str(job["job_id"]) for job in jobs]
        self.requests.append(ids)
        return {
            job_id: SourceOutcome.failure(
                failed_code="source_rate_limited", safe_log="blocked"
            ) for job_id in ids
        }


class _StageStore:
    def get_advanced_config_state(self):
        return {}

    def append_task_event(self, *_args, **_kwargs):
        return None

    def update_job_extra(self, *_args, **_kwargs):
        return None


class _PersistentStageStore(_StageStore):
    def __init__(self):
        self.events = []

    def append_task_event(self, run_id, event_type, payload):
        self.events.append({
            "type": str(event_type), "payload": dict(payload), "run_id": str(run_id),
        })

    def list_task_events(self, _run_id):
        return list(self.events)

    def save_checkpoint(self, *_args, **_kwargs):
        return None


class _StageContext:
    def __init__(self, result_dir: str, source, *, store=None, task_id="r2-v4"):
        self.app = SimpleNamespace(config={"RESULT_DIR": result_dir})
        self.store = store or _StageStore()
        self.pipeline_guard = None
        self.tasks = {task_id: {}}
        self.lock = threading.RLock()
        self.make_cdp_source = mock.Mock(return_value=source)

    def activate_task_browser(self, *_args, **_kwargs):
        return None

    def write_run(self, *_args, **_kwargs):
        return None

    def release_worker_resume_claims(self, *_args, **_kwargs):
        return None

    def record_pause_failure(self, *_args, **_kwargs):
        return None

    def persist_jd_job_failures(self, *_args, **_kwargs):
        return None


class _StoppingDetailSource(_DetailSource):
    def __init__(self, account_id: str, stop_event):
        super().__init__(account_id)
        self.stop_event = stop_event

    def fetch_details_batch(self, jobs, **kwargs):
        result = super().fetch_details_batch(jobs, **kwargs)
        self.stop_event.set()
        return result


def _execution_config(batch_size: int = 20):
    return SimpleNamespace(
        detail_batch_size=batch_size,
        detail_interval=0,
        detail_reset_every=1,
        detail_batch_cooldown=0,
        detail_tab_pool_size=1,
    )


class R2RotationV4Tests(unittest.TestCase):
    def test_rotation_snapshot_round_trip_preserves_position(self):
        from webui.r2_rotation_session import R2RotationSession

        source = _DetailSource("a")
        session = R2RotationSession(
            source,
            [PoolEntry("a", 2), PoolEntry("b", 3)],
            task_id="snapshot-task",
            platform="boss",
        )
        entry, taken = session.reserve(2)
        self.assertEqual((entry.account_id, taken), ("a", 2))
        snapshot = session.export_snapshot(completed_count=2)

        restored = R2RotationSession.from_snapshot(
            source,
            snapshot,
            task_id="snapshot-task",
            platform="boss",
        )
        self.assertEqual(restored.active_account, "b")
        self.assertEqual(restored.remaining_quota, 3)
        self.assertEqual(restored.completed_count, 2)
        next_entry, next_taken = restored.reserve(1)
        self.assertEqual((next_entry.account_id, next_taken), ("b", 1))

    def test_rotation_snapshot_rejects_identity_mismatch(self):
        from webui.r2_rotation_session import R2RotationSession, R2RotationSnapshotError

        source = _DetailSource("a")
        session = R2RotationSession(
            source,
            [PoolEntry("a", 2), PoolEntry("b", 3)],
            task_id="snapshot-task",
            platform="boss",
        )
        snapshot = session.export_snapshot()

        with self.assertRaises(R2RotationSnapshotError):
            R2RotationSession.from_snapshot(
                source,
                snapshot,
                task_id="different-task",
                platform="boss",
            )

    def test_rotation_snapshot_rejects_damaged_checkpoint(self):
        from webui.r2_rotation_session import R2RotationSession, R2RotationSnapshotError

        source = _DetailSource("a")
        session = R2RotationSession(
            source,
            [PoolEntry("a", 2), PoolEntry("b", 3)],
            task_id="snapshot-task",
            platform="boss",
        )
        snapshot = session.export_snapshot()
        snapshot.pop("active_account")

        with self.assertRaises(R2RotationSnapshotError):
            R2RotationSession.from_snapshot(
                source,
                snapshot,
                task_id="snapshot-task",
                platform="boss",
            )

    def test_explicit_resume_account_overrides_position_without_clearing_facts(self):
        from webui.r2_rotation_session import R2RotationSession

        source = _DetailSource("a")
        session = R2RotationSession(
            source,
            [PoolEntry("a", 2), PoolEntry("b", 2), PoolEntry("c", 2)],
            task_id="override-task",
            platform="boss",
        )
        session.reserve(1)
        snapshot = session.export_snapshot(completed_count=1)
        restored = R2RotationSession.from_snapshot(
            source, snapshot, task_id="override-task", platform="boss"
        )
        restored.override_active_account("c")

        self.assertEqual(restored.active_account, "c")
        self.assertEqual(restored.completed_count, 1)
        self.assertEqual(restored.blocked_accounts, [])

    def test_session_preserves_heterogeneous_quotas_across_multiple_rounds(self):
        from webui.r2_rotation_session import R2RotationSession

        session = R2RotationSession(
            _DetailSource("a"),
            [PoolEntry("a", 2), PoolEntry("b", 3)],
            task_id="heterogeneous-task",
            platform="boss",
        )
        allocations = [session.reserve(10) for _ in range(4)]

        self.assertEqual(
            [(entry.account_id, count) for entry, count in allocations],
            [("a", 2), ("b", 3), ("a", 2), ("b", 3)],
        )
        self.assertEqual(session.last_round, 2)

    def test_damaged_saved_checkpoint_pauses_instead_of_restarting_from_first_account(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.runners.ai_screen_jd import run_jd_stage

        book = _AccountBook(("a", "b"), r2_quota=2)
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        store = _PersistentStageStore()
        store.events.append({
            "type": "r2_rotation_checkpoint",
            "payload": {
                "version": 1, "task_id": "r2-damaged", "platform": "boss",
                "account_order": ["a", "b"], "quotas": {"a": 2, "b": 2},
                "round_no": 1, "remaining_quota": 1,
                "blocked_accounts": [], "completed_count": 1,
                "completed_digest": "",
                "saved_at": "2026-09-05T00:00:00+00:00",
            },
        })
        source = _DetailSource("a")
        ctx = _StageContext(
            tempfile.mkdtemp(prefix="cs_v4_damaged_artifacts_"), source,
            store=store, task_id="r2-damaged",
        )
        self.addCleanup(lambda: __import__("shutil").rmtree(ctx.app.config["RESULT_DIR"], ignore_errors=True))
        survivors = [{"job_id": "j1"}, {"job_id": "j2"}]
        save_checkpoint = mock.Mock(side_effect=RuntimeError("jd_checkpoint_write_failed"))

        with mock.patch.object(robin_mod, "clone_source", return_value=_DetailSource("b")), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            result = run_jd_stage(
                ctx, "r2-damaged", [dict(job) for job in survivors], survivors,
                {"j0": "already"},
                os.path.join(ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:a", "a", _execution_config(batch_size=2),
                threading.Event(), lambda **_kwargs: None,
                lambda: False, lambda: None, save_checkpoint,
            )

        self.assertIsNone(result)
        self.assertEqual(source.requests, [])
        self.assertTrue(save_checkpoint.called)
        self.assertEqual(ctx.tasks["r2-damaged"]["status"], "paused")
        self.assertIn("JD 断点写入失败", ctx.tasks["r2-damaged"]["error"])
        self.assertIn(
            "r2_rotation_checkpoint_invalid",
            [event["type"] for event in store.events],
        )

    def test_cross_chunk_rotation_keeps_one_quota_budget(self):
        """11 个 20 条外层分块应得到 200/20，而不是每块重置为账号一。"""
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.runners.ai_screen_jd import run_jd_stage

        book = _AccountBook(("a", "b"), r2_quota=200)
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )

        source_a = _DetailSource("a")
        source_b = _DetailSource("b")
        survivors = [{"job_id": f"j{i}"} for i in range(220)]
        ctx = _StageContext(tempfile.mkdtemp(prefix="cs_v4_r2_artifacts_"), source_a)
        self.addCleanup(lambda: __import__("shutil").rmtree(ctx.app.config["RESULT_DIR"], ignore_errors=True))

        with mock.patch.object(
            robin_mod,
            "clone_source",
            return_value=source_b,
        ), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch.object(
            robin_mod, "mark_account_rate_limited", return_value=None
        ), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            result = run_jd_stage(
                ctx,
                "r2-v4",
                enriched=[dict(job) for job in survivors],
                survivors=survivors,
                resume_jd={},
                jd_path=os.path.join(ctx.app.config["RESULT_DIR"], "jd.json"),
                frozen_platform="boss",
                frozen_cdp_port=9222,
                frozen_profile_key="boss:a",
                frozen_browser_account="a",
                execution_config=_execution_config(),
                stop_event=threading.Event(),
                emit=lambda **_kwargs: None,
                stop_requested=lambda: False,
                handle_user_stop=lambda: None,
                save_jd_checkpoint=lambda *_args, **_kwargs: None,
            )

        self.assertIsNotNone(result)
        self.assertEqual(sum(map(len, source_a.requests)), 200)
        self.assertEqual(sum(map(len, source_b.requests)), 20)

    def test_six_accounts_distribute_1048_real_requests_by_quota(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.runners.ai_screen_jd import run_jd_stage

        book = _AccountBook(tuple(f"a{i}" for i in range(6)), r2_quota=200)
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        sources = {account_id: _DetailSource(account_id) for account_id in book_ids(book)}
        source_a = sources["a0"]
        ctx = _StageContext(tempfile.mkdtemp(prefix="cs_v4_six_artifacts_"), source_a)
        self.addCleanup(lambda: __import__("shutil").rmtree(ctx.app.config["RESULT_DIR"], ignore_errors=True))
        survivors = [{"job_id": f"j{i}"} for i in range(1048)]

        def clone(_template, account_id, **_kwargs):
            return sources[str(account_id)]

        with mock.patch.object(robin_mod, "clone_source", side_effect=clone), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch.object(robin_mod, "mark_account_rate_limited", return_value=None), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            result = run_jd_stage(
                ctx, "r2-six", [dict(job) for job in survivors], survivors, {},
                os.path.join(ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:a0", "a0", _execution_config(),
                threading.Event(), lambda **_kwargs: None,
                lambda: False, lambda: None, lambda *_args, **_kwargs: None,
            )

        self.assertIsNotNone(result)
        self.assertEqual(
            [sum(map(len, sources[account_id].requests)) for account_id in book_ids(book)],
            [200, 200, 200, 200, 200, 48],
        )

    def test_partial_wall_handoff_makes_a_real_second_account_request(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.runners.ai_screen_jd import run_jd_stage

        book = _AccountBook(("a", "b"), r2_quota=200)
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        source_a = _PartialDetailSource("a")
        source_b = _DetailSource("b")
        ctx = _StageContext(tempfile.mkdtemp(prefix="cs_v4_handoff_artifacts_"), source_a)
        self.addCleanup(lambda: __import__("shutil").rmtree(ctx.app.config["RESULT_DIR"], ignore_errors=True))
        survivors = [{"job_id": f"j{i}"} for i in range(20)]
        with mock.patch.object(robin_mod, "clone_source", return_value=source_b), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch.object(robin_mod, "mark_account_rate_limited", return_value=None), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            result = run_jd_stage(
                ctx, "r2-handoff", [dict(job) for job in survivors], survivors, {},
                os.path.join(ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:a", "a", _execution_config(),
                threading.Event(), lambda **_kwargs: None,
                lambda: False, lambda: None, lambda *_args, **_kwargs: None,
            )

        self.assertIsNotNone(result)
        self.assertEqual(sum(map(len, source_a.requests)), 20)
        self.assertEqual(sum(map(len, source_b.requests)), 2)
        self.assertEqual(len(result[0]), 20)

    def test_all_accounts_genuinely_blocked_pause_with_successes_preserved(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.runners.ai_screen_jd import run_jd_stage

        book = _AccountBook(("a", "b", "c"), r2_quota=200)
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        sources = {account_id: _BlockingDetailSource(account_id) for account_id in ("a", "b", "c")}
        ctx = _StageContext(tempfile.mkdtemp(prefix="cs_v4_blocked_artifacts_"), sources["a"])
        self.addCleanup(lambda: __import__("shutil").rmtree(ctx.app.config["RESULT_DIR"], ignore_errors=True))
        survivors = [{"job_id": f"j{i}"} for i in range(3)]

        with mock.patch.object(robin_mod, "clone_source", side_effect=lambda _source, account_id, **_kwargs: sources[account_id]), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch.object(robin_mod, "mark_account_rate_limited", return_value=None), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            result = run_jd_stage(
                ctx, "r2-blocked", [dict(job) for job in survivors], survivors, {},
                os.path.join(ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:a", "a", _execution_config(batch_size=3),
                threading.Event(), lambda **_kwargs: None,
                lambda: False, lambda: None, lambda *_args, **_kwargs: None,
            )

        self.assertIsNone(result)
        self.assertEqual([sum(map(len, sources[account_id].requests)) for account_id in ("a", "b", "c")], [3, 3, 3])

    def test_pause_resume_restores_checkpoint_without_repeating_successes(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.runners.ai_screen_jd import run_jd_stage

        book = _AccountBook(("a", "b"), r2_quota=3)
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        store = _PersistentStageStore()
        first_stop = threading.Event()
        first_source = _StoppingDetailSource("a", first_stop)
        survivors = [{"job_id": f"j{i}"} for i in range(5)]
        first_ctx = _StageContext(
            tempfile.mkdtemp(prefix="cs_v4_resume_first_"), first_source,
            store=store, task_id="r2-resume",
        )
        self.addCleanup(lambda: __import__("shutil").rmtree(first_ctx.app.config["RESULT_DIR"], ignore_errors=True))

        def clone_first(_template, account_id, **_kwargs):
            return _DetailSource(str(account_id))

        with mock.patch.object(robin_mod, "clone_source", side_effect=clone_first), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch.object(robin_mod, "mark_account_rate_limited", return_value=None), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            first_result = run_jd_stage(
                first_ctx, "r2-resume", [dict(job) for job in survivors], survivors, {},
                os.path.join(first_ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:a", "a", _execution_config(batch_size=2),
                first_stop, lambda **_kwargs: None,
                lambda: first_stop.is_set(), lambda: None,
                lambda *_args, **_kwargs: None,
            )
        self.assertIsNone(first_result)
        self.assertEqual(first_source.requests, [["j0", "j1"]])

        second_a = _DetailSource("a")
        second_b = _DetailSource("b")
        second_ctx = _StageContext(
            tempfile.mkdtemp(prefix="cs_v4_resume_second_"), second_a,
            store=store, task_id="r2-resume",
        )
        self.addCleanup(lambda: __import__("shutil").rmtree(second_ctx.app.config["RESULT_DIR"], ignore_errors=True))
        second_stop = threading.Event()

        with mock.patch.object(robin_mod, "clone_source", return_value=second_b), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch.object(robin_mod, "mark_account_rate_limited", return_value=None), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            resumed_result = run_jd_stage(
                second_ctx, "r2-resume", [dict(job) for job in survivors], survivors,
                {"j0": "JD-j0", "j1": "JD-j1"},
                os.path.join(second_ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:a", "a", _execution_config(batch_size=2),
                second_stop, lambda **_kwargs: None,
                lambda: False, lambda: None, lambda *_args, **_kwargs: None,
            )

        self.assertIsNotNone(resumed_result)
        self.assertEqual(second_a.requests, [["j2"]])
        self.assertEqual([job_id for request in second_b.requests for job_id in request], ["j3", "j4"])
        self.assertEqual(set(resumed_result[0]), {"j0", "j1", "j2", "j3", "j4"})
        summaries = [
            event["payload"] for event in store.events
            if event["type"] == "account_usage_summary"
        ]
        latest = summaries[-1]
        usage = {item["account_id"]: item for item in latest["accounts"]}
        self.assertTrue(latest["reconciled"])
        self.assertFalse(latest["whitebox_incomplete"])
        self.assertEqual(usage["a"]["unique_success_count"], 3)
        self.assertEqual(usage["b"]["unique_success_count"], 2)

    def test_pause_resume_honors_explicit_account_override(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.runners.ai_screen_jd import run_jd_stage

        book = _AccountBook(("a", "b"), r2_quota=3)
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        store = _PersistentStageStore()
        first_stop = threading.Event()
        first_source = _StoppingDetailSource("a", first_stop)
        survivors = [{"job_id": f"j{i}"} for i in range(5)]
        first_ctx = _StageContext(
            tempfile.mkdtemp(prefix="cs_v4_resume_override_first_"), first_source,
            store=store, task_id="r2-resume-override",
        )
        self.addCleanup(lambda: __import__("shutil").rmtree(
            first_ctx.app.config["RESULT_DIR"], ignore_errors=True
        ))

        with mock.patch.object(robin_mod, "_switch_browser_account", return_value=True), mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=None):
            first_result = run_jd_stage(
                first_ctx, "r2-resume-override", [dict(job) for job in survivors],
                survivors, {}, os.path.join(first_ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:a", "a", _execution_config(batch_size=2),
                first_stop, lambda **_kwargs: None,
                lambda: first_stop.is_set(), lambda: None,
                lambda *_args, **_kwargs: None,
            )
        self.assertIsNone(first_result)
        checkpoint = [
            event["payload"] for event in store.events
            if event["type"] == "r2_rotation_checkpoint"
        ][-1]
        self.assertEqual(checkpoint["active_account"], "a")
        self.assertEqual(checkpoint["remaining_quota"], 1)

        second_source = _DetailSource("b")
        second_ctx = _StageContext(
            tempfile.mkdtemp(prefix="cs_v4_resume_override_second_"), second_source,
            store=store, task_id="r2-resume-override",
        )
        self.addCleanup(lambda: __import__("shutil").rmtree(
            second_ctx.app.config["RESULT_DIR"], ignore_errors=True
        ))
        with mock.patch.object(robin_mod, "_switch_browser_account", return_value=True), mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=None):
            resumed_result = run_jd_stage(
                second_ctx, "r2-resume-override", [dict(job) for job in survivors],
                survivors, {"j0": "JD-j0", "j1": "JD-j1"},
                os.path.join(second_ctx.app.config["RESULT_DIR"], "jd.json"),
                "boss", 9222, "boss:b", "b", _execution_config(batch_size=3),
                threading.Event(), lambda **_kwargs: None,
                lambda: False, lambda: None,
                lambda *_args, **_kwargs: None,
            )

        self.assertIsNotNone(resumed_result)
        self.assertEqual(second_source.requests, [["j2", "j3", "j4"]])


def book_ids(book: _AccountBook) -> tuple[str, ...]:
    with open(book.path, "r", encoding="utf-8") as handle:
        return tuple(json.load(handle))


if __name__ == "__main__":
    unittest.main()
