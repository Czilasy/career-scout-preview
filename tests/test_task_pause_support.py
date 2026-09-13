"""Safety contracts for the shared scrape pause helpers."""

from __future__ import annotations

import threading
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from webui.constants import _OPERATIONAL_ERRORS
from webui.browser_support import build_browser_support
from webui.diagnostics import record_failure
from webui.runners.pipeline_task import run_pipeline_task
from webui.store import TaskStore
from webui.task_pause_support import (
    STOP_MODE_CANCEL,
    STOP_MODE_FINISH,
    STOP_MODE_PAUSE,
    STOP_MODE_TERMINATE,
    ScrapeCheckpointReadError,
    ScrapeCheckpointWriteError,
    mark_scrape_paused,
    request_stop,
    scrape_completion_evidence,
    stop_mode_for_event,
)


class ScrapeCheckpointPauseSafetyTests(unittest.TestCase):
    def test_strict_reader_accepts_missing_and_empty_but_rejects_corrupt_payload(self):
        """Pause-only checkpoint reads distinguish legal empty from corruption."""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "strict-checkpoint-read"
            store.create_screening_run(run_id, source_count=1)

            self.assertEqual(
                store.load_checkpoint_strict(run_id, "scrape"), set())
            store.save_checkpoint(run_id, "scrape", [])
            self.assertEqual(
                store.load_checkpoint_strict(run_id, "scrape"), set())

            with store._connection() as conn:
                conn.execute(
                    "UPDATE pipeline_checkpoints SET completed_keys_json = ? "
                    "WHERE run_id = ? AND stage = 'scrape'",
                    ("{not-json", run_id),
                )
            with self.assertRaises(ValueError):
                store.load_checkpoint_strict(run_id, "scrape")

    def test_checkpoint_read_failure_keeps_durable_checkpoint_and_is_visible(self):
        """A failed read must not overwrite the last durable checkpoint."""

        class FailingCheckpointStore:
            def __init__(self):
                self.durable = {"already-saved|city"}
                self.save_calls = []
                self.events = []

            def load_checkpoint(self, _run_id, _stage):
                raise RuntimeError("credential-secret must not be logged")

            def load_checkpoint_strict(self, _run_id, _stage):
                raise RuntimeError("credential-secret must not be logged")

            def save_checkpoint(self, _run_id, _stage, keys):
                self.save_calls.append(set(keys))
                self.durable = set(keys)

            def append_task_event(self, _run_id, event_type, payload):
                self.events.append((event_type, payload))

        store = FailingCheckpointStore()
        failure_calls = []
        ctx = SimpleNamespace(
            store=store,
            operational_errors=(RuntimeError,),
            write_run=mock.Mock(),
            lock=threading.RLock(),
            tasks={},
            record_pause_failure=lambda *args, **kwargs: failure_calls.append(
                (args, kwargs)
            ),
        )

        with self.assertLogs("career_scout.task_pause_support", level="ERROR") as logs:
            with self.assertRaises(ScrapeCheckpointReadError) as raised:
                mark_scrape_paused(
                    ctx,
                    "run-checkpoint-read-failure",
                    completed_combos=["new|city"],
                    skip_combos=["skipped|city"],
                )

        self.assertEqual(raised.exception.error_code, "checkpoint_read_failed")
        self.assertEqual(store.durable, {"already-saved|city"})
        self.assertEqual(store.save_calls, [])
        ctx.write_run.assert_called_once()
        self.assertEqual(ctx.write_run.call_args.kwargs["status"], "failed")
        self.assertEqual(
            ctx.write_run.call_args.kwargs["error_code"], "checkpoint_read_failed",
        )
        self.assertTrue(failure_calls)
        self.assertEqual(failure_calls[0][0][2], "checkpoint_read_failed")
        self.assertTrue(any("checkpoint_read_failed" in line for line in logs.output))
        self.assertNotIn("credential-secret", "\n".join(logs.output))

    def test_real_db_corrupt_checkpoint_records_failure_without_overwrite(self):
        """A corrupt JSON checkpoint preserves its exact durable bytes."""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "real-corrupt-checkpoint"
            store.create_screening_run(run_id, source_count=2)
            store.update_screening_run(run_id, status="running")
            store.save_checkpoint(run_id, "scrape", ["already|saved"])
            corrupt = '{"credential":"credential-secret"'
            with store._connection() as conn:
                conn.execute(
                    "UPDATE pipeline_checkpoints SET completed_keys_json = ? "
                    "WHERE run_id = ? AND stage = 'scrape'",
                    (corrupt, run_id),
                )

            def _record_failure(run, stage, code, reason, **kwargs):
                return record_failure(
                    store, run, stage=stage, error_code=code, reason=reason,
                    correlation_id=run, diagnostics=kwargs,
                )

            ctx = SimpleNamespace(
                store=store,
                operational_errors=_OPERATIONAL_ERRORS,
                write_run=mock.Mock(),
                lock=threading.RLock(),
                tasks={},
                record_pause_failure=_record_failure,
            )

            with self.assertLogs(
                    "career_scout.task_pause_support", level="ERROR") as logs:
                with self.assertRaises(ScrapeCheckpointReadError):
                    mark_scrape_paused(
                        ctx, run_id,
                        completed_combos=["new|city"],
                        reason="用户暂停",
                    )

            with store._connection() as conn:
                row = conn.execute(
                    "SELECT completed_keys_json FROM pipeline_checkpoints "
                    "WHERE run_id = ? AND stage = 'scrape'", (run_id,),
                ).fetchone()
            self.assertEqual(row["completed_keys_json"], corrupt)
            ctx.write_run.assert_called_once()
            self.assertEqual(ctx.write_run.call_args.kwargs["status"], "failed")
            self.assertEqual(
                ctx.write_run.call_args.kwargs["error_code"], "checkpoint_read_failed",
            )
            failures = [
                event for event in store.list_task_events(run_id)
                if event["type"] == "failure"
            ]
            self.assertTrue(failures)
            self.assertEqual(
                failures[-1]["payload"]["error_code"], "checkpoint_read_failed")
            self.assertNotIn(
                "credential-secret",
                json.dumps(failures[-1]["payload"], ensure_ascii=False),
            )
            self.assertNotIn("credential-secret", "\n".join(logs.output))

    def test_corrupt_checkpoint_marks_run_failed_before_worker_exits(self):
        """严格读取失败也必须把真实 run 固定为 failed，而不是 running。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "real-corrupt-checkpoint-paused"
            store.create_screening_run(run_id, source_count=2)
            store.update_screening_run(run_id, status="running", current_stage="scrape")
            store.save_checkpoint(run_id, "scrape", ["already|saved"])
            corrupt = '{"credential":"credential-secret"'
            with store._connection() as conn:
                conn.execute(
                    "UPDATE pipeline_checkpoints SET completed_keys_json = ? "
                    "WHERE run_id = ? AND stage = 'scrape'",
                    (corrupt, run_id),
                )

            ctx = SimpleNamespace(
                store=store,
                operational_errors=_OPERATIONAL_ERRORS,
                write_run=lambda current_id, **kwargs: store.update_screening_run(
                    current_id, **kwargs),
                lock=threading.RLock(),
                tasks={},
                record_pause_failure=lambda current_id, stage, code, reason, **kwargs: record_failure(
                    store, current_id, stage=stage, error_code=code,
                    reason=reason, correlation_id=current_id,
                    diagnostics=kwargs,
                ),
            )

            with self.assertRaises(ScrapeCheckpointReadError):
                mark_scrape_paused(
                    ctx, run_id, completed_combos=["new|city"], reason="用户暂停",
                )

            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_code"], "checkpoint_read_failed")
            self.assertEqual(run["source_count"], 2)
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT completed_keys_json FROM pipeline_checkpoints "
                    "WHERE run_id = ? AND stage = 'scrape'", (run_id,),
                ).fetchone()
            self.assertEqual(row["completed_keys_json"], corrupt)
            failures = [
                event for event in store.list_task_events(run_id)
                if event["type"] == "failure"
            ]
            self.assertTrue(failures)
            self.assertEqual(
                failures[-1]["payload"]["error_code"], "checkpoint_read_failed",
            )

    def test_checkpoint_save_failure_marks_run_failed_and_records_failure(self):
        """断点保存失败时 runner 结束也不能把 durable run 留在 running。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "checkpoint-save-failure-paused"
            store.create_screening_run(run_id, source_count=2)
            store.update_screening_run(run_id, status="running", current_stage="scrape")
            ctx = SimpleNamespace(
                store=store,
                operational_errors=_OPERATIONAL_ERRORS,
                write_run=lambda current_id, **kwargs: store.update_screening_run(
                    current_id, **kwargs),
                lock=threading.RLock(),
                tasks={},
                record_pause_failure=lambda current_id, stage, code, reason, **kwargs: record_failure(
                    store, current_id, stage=stage, error_code=code,
                    reason=reason, correlation_id=current_id,
                    diagnostics=kwargs,
                ),
            )

            with mock.patch.object(
                    store, "save_checkpoint",
                    side_effect=RuntimeError("checkpoint write unavailable")):
                with self.assertRaises(ScrapeCheckpointWriteError):
                    mark_scrape_paused(
                        ctx, run_id, completed_combos=["new|city"], reason="用户暂停",
                    )

            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_code"], "checkpoint_write_failed")
            self.assertEqual(run["source_count"], 2)
            failures = [
                event for event in store.list_task_events(run_id)
                if event["type"] == "failure"
            ]
            self.assertTrue(failures)
            self.assertEqual(
                failures[-1]["payload"]["error_code"], "checkpoint_write_failed",
            )

    def test_pause_audit_write_failure_does_not_undo_durable_pause(self):
        """暂停主状态和断点成功后，审计事件失败不能把任务改回 failed。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "pause-audit-write-failure"
            store.create_screening_run(run_id, source_count=1)
            store.update_screening_run(
                run_id, status="running", current_stage="scrape",
            )
            original_append = store.append_task_event

            def append_event(current_id, event_type, payload):
                if event_type == "pause":
                    raise RuntimeError("audit event unavailable")
                return original_append(current_id, event_type, payload)

            ctx = SimpleNamespace(
                store=store,
                operational_errors=_OPERATIONAL_ERRORS,
                write_run=lambda current_id, **kwargs: store.update_screening_run(
                    current_id, **kwargs,
                ),
                lock=threading.RLock(),
                tasks={},
            )
            with mock.patch.object(
                    store, "append_task_event", side_effect=append_event):
                completed = mark_scrape_paused(
                    ctx, run_id, completed_combos=["kw|city"],
                    error_code="source_cdp_unavailable", reason="浏览器未连接",
                )

            self.assertEqual(completed, ["kw|city"])
            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "paused")
            self.assertEqual(run["error_code"], "source_cdp_unavailable")
            self.assertEqual(
                store.load_checkpoint(run_id, "scrape"), {"kw|city"},
            )


class StopModePriorityTests(unittest.TestCase):
    def test_pause_cannot_overwrite_terminal_mode_in_either_order(self):
        """Terminal cleanup/finish/cancel always wins over a pause request."""
        for terminal_mode in (
            STOP_MODE_FINISH,
            STOP_MODE_CANCEL,
            STOP_MODE_TERMINATE,
        ):
            for first_mode, second_mode in (
                (terminal_mode, STOP_MODE_PAUSE),
                (STOP_MODE_PAUSE, terminal_mode),
            ):
                task = {}
                stop_event = threading.Event()
                request_stop(task, stop_event, first_mode)
                request_stop(task, stop_event, second_mode)

                self.assertEqual(task["stop_mode"], terminal_mode)
                self.assertEqual(
                    getattr(stop_event, "stop_mode", None), terminal_mode
                )
                self.assertEqual(
                    stop_mode_for_event(stop_event, task), terminal_mode
                )

    def test_concurrent_pause_and_terminal_stop_never_leaves_pause(self):
        """A terminal request wins even when pause and finish/cancel race.

        批四 T076：竞态窗口来自 Barrier(2) 的同步起跑，不来自重复轮数；
        3 轮 × 3 种终态已覆盖同一交叠场景，原 25 轮只是重复消耗。
        """
        for terminal_mode in (
                STOP_MODE_CANCEL, STOP_MODE_FINISH, STOP_MODE_TERMINATE):
            for _ in range(3):
                task = {}
                stop_event = threading.Event()
                start = threading.Barrier(2)

                def _request(mode):
                    start.wait()
                    request_stop(task, stop_event, mode)

                pause_thread = threading.Thread(
                    target=_request, args=(STOP_MODE_PAUSE,))
                terminal_thread = threading.Thread(
                    target=_request, args=(terminal_mode,))
                pause_thread.start()
                terminal_thread.start()
                pause_thread.join(timeout=2)
                terminal_thread.join(timeout=2)
                self.assertFalse(pause_thread.is_alive())
                self.assertFalse(terminal_thread.is_alive())
                self.assertEqual(task["stop_mode"], terminal_mode)
                self.assertEqual(stop_mode_for_event(stop_event, task), terminal_mode)


class ScrapeFailureLifecycleTests(unittest.TestCase):
    def _context_for_run(self, store, run_id, *, activate=None):
        task = {
            "kind": "scrape", "status": "queued", "progress": {},
            "logs": [], "result": None, "error": "",
            "started_at": 1, "finished_at": None,
            "stop_event": threading.Event(),
        }
        return SimpleNamespace(
            store=store,
            operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(),
            tasks={run_id: task},
            app=SimpleNamespace(config={"RESULT_DIR": tempfile.gettempdir()}),
            is_user_finished=lambda _run_id: False,
            activate_task_browser=activate or mock.Mock(),
            release_worker_resume_claims=mock.Mock(),
            schedule_pipeline_task_cleanup=mock.Mock(),
            clear_auto_screen=mock.Mock(),
            write_run=lambda current_id, **kwargs: store.update_screening_run(
                current_id, **kwargs),
            record_pause_failure=mock.Mock(),
        )

    def test_checkpoint_error_finishes_failed_and_releases_browser_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "checkpoint-error-failed"
            store.create_screening_run(run_id, source_count=1)
            store.update_screening_run(run_id, status="running", current_stage="scrape")
            ctx = SimpleNamespace(
                store=store,
                operational_errors=_OPERATIONAL_ERRORS,
                write_run=lambda current_id, **kwargs: store.update_screening_run(
                    current_id, **kwargs),
                lock=threading.RLock(),
                tasks={},
                record_pause_failure=lambda current_id, stage, code, reason, **kwargs: record_failure(
                    store, current_id, stage=stage, error_code=code,
                    reason=reason, correlation_id=current_id,
                    diagnostics=kwargs,
                ),
            )
            store.save_checkpoint(run_id, "scrape", ["saved|city"])
            with store._connection() as conn:
                conn.execute(
                    "UPDATE pipeline_checkpoints SET completed_keys_json = ? "
                    "WHERE run_id = ? AND stage = 'scrape'",
                    ("{broken", run_id),
                )

            with self.assertRaises(ScrapeCheckpointReadError):
                mark_scrape_paused(ctx, run_id, completed_combos=["new|city"])

            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_code"], "checkpoint_read_failed")
            support = build_browser_support(
                store, {}, threading.RLock(), lambda _run: "a", mock.Mock(),
            )
            self.assertFalse(support[1]())

    def test_system_error_paused_row_does_not_hold_browser_but_user_pause_does(self):
        for error_code, expected_busy in (
                ("source_cdp_unavailable", False),
                ("user_paused", True)):
            with self.subTest(error_code=error_code), tempfile.TemporaryDirectory() as tmp:
                store = TaskStore(f"{tmp}/test/webui.db")
                run_id = f"paused-{error_code}"
                store.create_screening_run(
                    run_id, source_count=1,
                    execution_params={"browser_account": "a", "platform": "boss"},
                )
                store.update_screening_run(run_id, status="running")
                store.update_screening_run(
                    run_id, status="paused", error_code=error_code,
                    error_reason="test reason",
                )
                support = build_browser_support(
                    store, {}, threading.RLock(), lambda _run: "a", mock.Mock(),
                )
                self.assertEqual(support[1](), expected_busy)

    def test_activate_task_browser_failure_converges_to_failed_without_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "activation-error-failed"
            store.create_screening_run(run_id, source_count=1)
            activation = mock.Mock(side_effect=RuntimeError("bind failed"))
            ctx = self._context_for_run(store, run_id, activate=activation)

            try:
                run_pipeline_task(
                    ctx, run_id,
                    {"keyword": "kw", "city": ["city"], "pages": 1},
                )
            except RuntimeError as exc:
                self.fail(f"activation failure escaped the lifecycle boundary: {exc}")

            self.assertEqual(store.get_screening_run(run_id)["status"], "failed")
            self.assertEqual(ctx.tasks[run_id]["status"], "failed")
            ctx.schedule_pipeline_task_cleanup.assert_called_once_with(run_id)
            ctx.release_worker_resume_claims.assert_called_once()

    def test_recoverable_hard_stop_pauses_and_does_not_hold_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "hard-stop-paused"
            store.create_screening_run(
                run_id, source_count=1,
                execution_params={"platform": "boss"},
            )
            ctx = self._context_for_run(store, run_id)
            ctx.make_cdp_source = mock.Mock(return_value=object())

            result = {
                "ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 1,
                "completed_combos": [], "hard_stop": True,
                "hard_stop_code": "source_cdp_unavailable",
                "error": "系统性阻断：浏览器不可用",
            }
            with mock.patch("webui.pipeline_exec.run_search", return_value=result):
                run_pipeline_task(
                    ctx, run_id,
                    {"keyword": "kw", "city": ["city"], "pages": 1},
                )

            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "paused")
            self.assertEqual(run["error_code"], "source_cdp_unavailable")
            self.assertEqual(run["error_reason"], result["error"])
            self.assertEqual(store.load_checkpoint(run_id, "scrape"), set())
            self.assertEqual(ctx.tasks[run_id]["status"], "paused")
            support = build_browser_support(
                store, ctx.tasks, ctx.lock, lambda _run: "a", mock.Mock(),
            )
            self.assertFalse(support[1]())


class ScrapeFinalizeWindowTests(unittest.TestCase):
    """收尾族：事实优先 + 收尾区立牌 + 完成证据核实。"""

    def _context_for_run(self, store, run_id):
        task = {
            "kind": "scrape", "status": "queued", "progress": {},
            "logs": [], "result": None, "error": "",
            "started_at": 1, "finished_at": None,
            "stop_event": threading.Event(),
        }
        return SimpleNamespace(
            store=store,
            operational_errors=_OPERATIONAL_ERRORS,
            lock=threading.RLock(),
            tasks={run_id: task},
            app=SimpleNamespace(config={"RESULT_DIR": tempfile.gettempdir()}),
            is_user_finished=lambda _run_id: False,
            activate_task_browser=mock.Mock(),
            release_worker_resume_claims=mock.Mock(),
            schedule_pipeline_task_cleanup=mock.Mock(),
            clear_auto_screen=mock.Mock(),
            write_run=lambda current_id, **kwargs: store.update_screening_run(
                current_id, **kwargs),
            record_pause_failure=mock.Mock(),
        )

    def test_finished_scrape_fact_wins_over_pause_flag(self):
        """收尾窗口里点暂停：抓取事实已完成 → 终态仍按 succeeded 定稿。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "finalize-fact-first"
            store.create_screening_run(
                run_id, source_count=1,
                execution_params={"platform": "boss"},
            )
            ctx = self._context_for_run(store, run_id)
            ctx.make_cdp_source = mock.Mock(return_value=object())
            result = {
                "ok": True, "jobs": [{"job_id": "j1"}], "total_scraped": 1,
                "total_matched": 1, "combinations": 1,
                "completed_combos": ["kw|city"],
                "integrity": {"conclusion": "succeeded", "evidence_complete": True},
                "error": "",
            }
            # 用户在收尾窗口点了暂停：旗子已置位，worker 即将写终态。
            request_stop(
                ctx.tasks[run_id], ctx.tasks[run_id]["stop_event"], STOP_MODE_PAUSE,
            )
            with mock.patch("webui.pipeline_exec.run_search", return_value=result):
                run_pipeline_task(
                    ctx, run_id,
                    {"keyword": "kw", "city": ["city"], "pages": 1},
                )

            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "succeeded")
            self.assertNotEqual(run["error_code"], "user_paused")
            self.assertEqual(ctx.tasks[run_id]["status"], "done")
            self.assertNotIn("finalizing", ctx.tasks[run_id])

    def test_finalizing_flag_set_on_finalize_progress_and_cleared_after(self):
        """收尾进度信号立牌、终态写好后清牌（命令口据此只回执不执行）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "finalize-flag"
            store.create_screening_run(
                run_id, source_count=1,
                execution_params={"platform": "boss"},
            )
            ctx = self._context_for_run(store, run_id)
            ctx.make_cdp_source = mock.Mock(return_value=object())
            seen = {}

            def fake_run_search(params, source, **kwargs):
                progress = kwargs.get("progress")
                progress({"stage": "closing_chrome", "message": "正在关闭调试浏览器…"})
                seen["finalizing_during"] = bool(
                    ctx.tasks[run_id].get("finalizing"))
                return {
                    "ok": True, "jobs": [{"job_id": "j1"}], "total_scraped": 1,
                    "total_matched": 1, "combinations": 1,
                    "completed_combos": ["kw|city"],
                    "integrity": {"conclusion": "succeeded", "evidence_complete": True},
                    "error": "",
                }

            with mock.patch(
                    "webui.pipeline_exec.run_search", side_effect=fake_run_search):
                run_pipeline_task(
                    ctx, run_id,
                    {"keyword": "kw", "city": ["city"], "pages": 1},
                )

            self.assertTrue(seen["finalizing_during"])
            self.assertNotIn("finalizing", ctx.tasks[run_id])

    def test_scrape_completion_evidence_requires_checkpoint_jobs_and_whitebox(self):
        """完成证据核实：断点满 + 有岗位 + 白箱完整结论，三者缺一不可。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "evidence-run"
            store.create_screening_run(run_id, source_count=2)
            store.update_screening_run(run_id, status="running")
            ctx = SimpleNamespace(
                store=store, operational_errors=_OPERATIONAL_ERRORS,
            )

            store.save_checkpoint(run_id, "scrape", ["kw|city"])
            self.assertFalse(scrape_completion_evidence(ctx, run_id))

            store.save_checkpoint(run_id, "scrape", ["kw|city", "kw2|city"])
            self.assertFalse(scrape_completion_evidence(ctx, run_id))

            # 断点会随组合结果同事务覆盖，这里保持两项已完成组合。
            store.save_scrape_combo_result(
                run_id, "kw|city", [{"job_id": "j1"}], ["kw|city", "kw2|city"])
            from webui.whitebox import WhiteboxService
            with mock.patch.object(
                WhiteboxService, "report",
                return_value={"integrity": {
                    "conclusion": "succeeded", "evidence_complete": True}},
            ):
                self.assertTrue(scrape_completion_evidence(ctx, run_id))
            with mock.patch.object(
                WhiteboxService, "report",
                return_value={"integrity": {
                    "conclusion": "succeeded", "evidence_complete": False}},
            ):
                self.assertFalse(scrape_completion_evidence(ctx, run_id))

    def test_settle_paused_run_as_completed_only_from_paused(self):
        """受控纠正只走 paused → succeeded；已终态不再改写，并清暂停残留文案。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "settle-paused"
            store.create_screening_run(run_id, source_count=2)
            store.update_screening_run(run_id, status="running")
            store.update_screening_run(
                run_id, status="paused", error_code="source_cdp_unavailable",
                error_reason="调试浏览器未就绪", current_stage="scrape",
            )

            self.assertTrue(store.settle_paused_run_as_completed(
                run_id, processed_count=2, source_count=2))
            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "succeeded")
            self.assertIsNone(run["error_code"])
            self.assertIsNone(run["error_reason"])
            self.assertFalse(store.settle_paused_run_as_completed(run_id))

    def test_checkpoint_failure_does_not_leave_recoverable_pause_in_memory(self):
        """断点持久化失败后，内存状态必须与 durable failed 状态一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "recoverable-pause-checkpoint-failure"
            store.create_screening_run(
                run_id, source_count=1,
                execution_params={"platform": "boss"},
            )
            ctx = self._context_for_run(store, run_id)
            ctx.make_cdp_source = mock.Mock(return_value=object())

            class RecoverableSourceError(RuntimeError):
                error_code = "source_cdp_unavailable"

            with mock.patch(
                    "webui.pipeline_exec.run_search",
                    side_effect=RecoverableSourceError("source disconnected")), \
                    mock.patch.object(
                        store, "save_checkpoint",
                        side_effect=RuntimeError("checkpoint unavailable")), \
                    self.assertLogs(
                        "career_scout.task_pause_support", level="ERROR"):
                run_pipeline_task(
                    ctx, run_id,
                    {"keyword": "kw", "city": ["city"], "pages": 1},
                )

            self.assertEqual(store.get_screening_run(run_id)["status"], "failed")
            self.assertEqual(ctx.tasks[run_id]["status"], "failed")

    def test_unrecoverable_hard_stop_still_finishes_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(f"{tmp}/test/webui.db")
            run_id = "hard-stop-internal-error"
            store.create_screening_run(run_id, source_count=1)
            ctx = self._context_for_run(store, run_id)

            result = {
                "ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 1,
                "completed_combos": [], "hard_stop": True,
                "hard_stop_code": "internal_error",
                "error": "checkpoint persistence failed",
            }
            with mock.patch("webui.pipeline_exec.run_search", return_value=result):
                run_pipeline_task(
                    ctx, run_id,
                    {"keyword": "kw", "city": ["city"], "pages": 1},
                )

            self.assertEqual(store.get_screening_run(run_id)["status"], "failed")
            self.assertEqual(ctx.tasks[run_id]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
