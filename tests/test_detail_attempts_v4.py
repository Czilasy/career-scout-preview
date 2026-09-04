"""V4 详情尝试、产物唯一性和 JD pending 清理回归。"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import webui.account_round_robin as robin_mod
from webui.account_round_robin_observability import RoundRobinWhitebox
from webui.source import SourceOutcome
from webui.source_breaker import SourceCircuitBreaker


class _AttemptSource:
    platform = "boss"
    cdp_port = 9222
    run_id = "attempt-v4"
    cancel_event = None

    def __init__(self, account_id: str, responses):
        self.browser_account = account_id
        self.responses = list(responses)
        self.calls: list[dict] = []

    def fetch_details_batch(self, jobs, **kwargs):
        self.calls.append({
            "job_ids": [str(job["job_id"]) for job in jobs],
            "artifact": str(kwargs.get("detail_output_path") or ""),
        })
        response = self.responses.pop(0)
        return response([str(job["job_id"]) for job in jobs]) if callable(response) else response


def _config(batch_size: int = 2):
    return SimpleNamespace(
        detail_batch_size=batch_size,
        detail_interval=0,
        detail_reset_every=1,
        detail_batch_cooldown=0,
        detail_tab_pool_size=1,
    )


class _AlwaysRestartRecovery:
    def __init__(self, **_kwargs):
        pass

    @staticmethod
    def is_browser_lost(code):
        return str(code or "") in {"source_cdp_unavailable", "cdp_unavailable"}

    def try_restart(self):
        return True, ""

    def mark_progress(self):
        return None


class _AccountBook:
    def __init__(self):
        self.root = tempfile.TemporaryDirectory(prefix="cs_v4_attempt_accounts_")
        self.path = os.path.join(self.root.name, "browser_accounts.json")
        accounts = {}
        for order, account_id in enumerate(("a", "b")):
            accounts[account_id] = {
                "id": account_id,
                "name": account_id,
                "profile_dir": os.path.join(self.root.name, f"profile-{account_id}"),
                "builtin": account_id == "a",
                "pool": {
                    "selected": True,
                    "order": order,
                    "r1_quota": 25,
                    "r2_quota": 2,
                },
                "rate_limited": False,
            }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(accounts, handle)

    def close(self):
        self.root.cleanup()


class DetailAttemptsV4Tests(unittest.TestCase):
    def test_runtime_events_distinguish_reservation_request_terminal_and_summary(self):
        store = SimpleNamespace(events=[])

        def append_task_event(run_id, event_type, payload):
            store.events.append((run_id, event_type, dict(payload)))

        store.append_task_event = append_task_event
        tracker = RoundRobinWhitebox(
            store,
            "event-task",
            phase="R2",
            platform="boss",
            entries=[SimpleNamespace(account_id="a", quota=2)],
        )
        tracker.allocation(
            "a", round_no=1, count=2, remaining=0, pending_remaining=0,
        )
        tracker.request_start(
            account_id="a", segment=1, attempt_id="attempt-1",
            attempt_no=1, input_count=2, artifact_id="artifact-1",
        )
        tracker.request_terminal(
            account_id="a", segment=1, attempt_id="attempt-1",
            attempt_no=1, input_count=2, success_count=1,
            failure_count=1, short_circuit_count=0, unresolved_count=0,
            failure_code="source_rate_limited", handed_off=True,
        )
        tracker.account_summary(
            summaries=[{
                "account_id": "a", "reserved_count": 2,
                "request_started_count": 2, "unique_success_count": 1,
                "failure_count": 1, "short_circuit_count": 0,
                "handoff_in_count": 0, "handoff_out_count": 1,
            }],
            total_success=1,
        )

        by_type = {event_type: payload for _, event_type, payload in store.events}
        self.assertEqual(by_type["account_allocation"]["fact_kind"], "reservation")
        self.assertEqual(by_type["account_request_start"]["attempt_id"], "attempt-1")
        terminal = by_type["account_request_terminal"]
        self.assertEqual(
            terminal["input_count"],
            terminal["success_count"] + terminal["failure_count"]
            + terminal["short_circuit_count"] + terminal["unresolved_count"],
        )
        self.assertEqual(by_type["account_usage_summary"]["total_success"], 1)

    def test_retry_accounting_reconciles_unique_successes_by_account(self):
        from webui.detail_attempts import DetailAttemptTracker

        with tempfile.TemporaryDirectory(prefix="cs_v4_tracker_") as artifact_dir:
            tracker = DetailAttemptTracker("accounting-task", artifact_dir)
            first_segment = tracker.reserve("a", 20, round_no=1)
            first_attempt = tracker.new_attempt(first_segment, "a", 20)
            tracker.record_terminal(
                first_attempt, success_count=18, failure_count=0,
                short_circuit_count=0, unresolved_count=2,
                failure_code="source_rate_limited",
                success_job_ids=[f"j{i}" for i in range(18)], handed_off=True,
            )
            tracker.record_handoff("a", "b", 2)
            second_segment = tracker.reserve("b", 2, round_no=1)
            second_attempt = tracker.new_attempt(second_segment, "b", 2)
            tracker.record_terminal(
                second_attempt, success_count=2, failure_count=0,
                short_circuit_count=0, unresolved_count=0,
                success_job_ids=["j18", "j19"],
            )
            summary = tracker.summary(total_success=20)

        self.assertTrue(summary["reconciled"])
        self.assertEqual(summary["total_success"], 20)
        self.assertEqual(
            [(row["account_id"], row["unique_success_count"]) for row in summary["accounts"]],
            [("a", 18), ("b", 2)],
        )
        self.assertEqual(summary["accounts"][0]["handoff_out_count"], 2)
        self.assertEqual(summary["accounts"][1]["handoff_in_count"], 2)
        self.assertEqual(summary["accounts"][0]["unresolved_count"], 2)

    def test_replay_keeps_hashed_success_keys_unique_across_resume(self):
        from webui.detail_attempts import DetailAttemptTracker

        replayed_key = hashlib.sha256(b"j1").hexdigest()
        events = [
            {"type": "account_pool_snapshot", "payload": {
                "phase": "R2", "accounts": [{"account_id": "a", "quota": 2}],
            }},
            {"type": "account_allocation", "payload": {
                "phase": "R2", "account_id": "a", "count": 1,
            }},
            {"type": "account_request_start", "payload": {
                "phase": "R2", "account_id": "a", "attempt_id": "old",
                "attempt_no": 1, "input_count": 1, "segment": "old-segment",
            }},
            {"type": "account_request_terminal", "payload": {
                "phase": "R2", "account_id": "a", "attempt_id": "old",
                "input_count": 1, "success_count": 1, "failure_count": 0,
                "short_circuit_count": 0, "unresolved_count": 0,
                "success_keys": [replayed_key],
            }},
        ]
        with tempfile.TemporaryDirectory(prefix="cs_v4_tracker_replay_") as artifact_dir:
            tracker = DetailAttemptTracker.from_task_events(
                "replay-task", artifact_dir, events,
            )
            segment = tracker.reserve("a", 1)
            attempt = tracker.new_attempt(segment, "a", 1)
            tracker.record_terminal(
                attempt, success_count=1, failure_count=0,
                short_circuit_count=0, unresolved_count=0,
                success_job_ids=["j1"],
            )
            summary = tracker.summary(total_success=1)

        self.assertTrue(summary["reconciled"])
        self.assertFalse(summary["whitebox_incomplete"])
        self.assertEqual(summary["accounts"][0]["unique_success_count"], 1)

    def test_local_short_circuit_creates_no_request_start(self):
        from webui.detail_attempts import DetailAttemptTracker

        with tempfile.TemporaryDirectory(prefix="cs_v4_short_circuit_") as artifact_dir:
            tracker = DetailAttemptTracker("short-circuit-task", artifact_dir)
            segment = tracker.reserve("a", 2)
            attempt = tracker.new_attempt(segment, "a", 2, started=False)
            tracker.record_terminal(
                attempt, success_count=0, failure_count=0,
                short_circuit_count=2, unresolved_count=0,
            )
            summary = tracker.summary(total_success=0)

        self.assertEqual(tracker.started_count, 0)
        self.assertEqual(summary["accounts"][0]["short_circuit_count"], 2)

    def test_pipeline_events_prove_reservation_is_not_a_request(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.pipeline_exec_details import fetch_job_details

        book = _AccountBook()
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        source_a = _AttemptSource("a", [
            lambda ids: {
                job_id: SourceOutcome.failure(
                    failed_code="source_rate_limited", safe_log="blocked"
                ) for job_id in ids
            }
        ])
        source_b = _AttemptSource("b", [
            lambda ids: {
                job_id: SourceOutcome.success(detail={"jd": f"JD-{job_id}"})
                for job_id in ids
            }
        ])
        store = SimpleNamespace(events=[])
        store.append_task_event = lambda run_id, event_type, payload: store.events.append(
            (run_id, event_type, dict(payload))
        )
        with tempfile.TemporaryDirectory(prefix="cs_v4_event_artifacts_") as artifact_dir, mock.patch.object(
            robin_mod, "clone_source", return_value=source_b
        ), mock.patch.object(robin_mod, "_switch_browser_account", return_value=True), mock.patch.object(
            robin_mod, "mark_account_rate_limited", return_value=None
        ), mock.patch.object(robin_mod, "clear_account_rate_limited", return_value=None):
            result = fetch_job_details(
                [{"job_id": "j1"}, {"job_id": "j2"}], source_a,
                artifact_dir=artifact_dir, execution_config=_config(batch_size=2),
                task_id="event-task", store=store,
            )

        self.assertEqual(result["fetched"], 2)
        event_types = [event_type for _, event_type, _ in store.events]
        self.assertEqual(event_types.count("account_allocation"), 2)
        self.assertEqual(event_types.count("account_request_start"), 2)
        self.assertEqual(event_types.count("account_request_terminal"), 2)
        self.assertEqual(event_types.count("account_usage_summary"), 1)
        allocations = [payload for _, kind, payload in store.events if kind == "account_allocation"]
        self.assertTrue(all(payload["fact_kind"] == "reservation" for payload in allocations))
        summaries = [payload for _, kind, payload in store.events if kind == "account_usage_summary"]
        self.assertEqual(summaries[0]["total_success"], 2)
        handoffs = [payload for _, kind, payload in store.events if kind == "account_handoff"]
        self.assertTrue(handoffs[0].get("source_attempt_id"))

    def test_local_breaker_short_circuit_handoffs_without_marking_new_wall(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.pipeline_exec_details import fetch_job_details

        book = _AccountBook()
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        source_a = _AttemptSource("a", [])
        source_a.breaker = SourceCircuitBreaker()
        source_a.breaker.record_signal("source_rate_limited")
        source_a.breaker.record_signal("source_rate_limited")
        source_b = _AttemptSource("b", [
            lambda ids: {
                job_id: SourceOutcome.success(detail={"jd": f"JD-{job_id}"})
                for job_id in ids
            }
        ])
        mark = mock.Mock()
        store = SimpleNamespace(events=[])
        store.append_task_event = lambda run_id, event_type, payload: store.events.append(
            (run_id, event_type, dict(payload))
        )
        with tempfile.TemporaryDirectory(prefix="cs_v4_short_artifacts_") as artifact_dir, mock.patch.object(
            robin_mod, "clone_source", return_value=source_b
        ), mock.patch.object(robin_mod, "_switch_browser_account", return_value=True), mock.patch.object(
            robin_mod, "mark_account_rate_limited", mark
        ), mock.patch.object(robin_mod, "clear_account_rate_limited", return_value=None):
            result = fetch_job_details(
                [{"job_id": "j1"}, {"job_id": "j2"}], source_a,
                artifact_dir=artifact_dir, execution_config=_config(batch_size=2),
                task_id="short-circuit-task", store=store,
            )

        self.assertEqual(result["fetched"], 2)
        self.assertEqual(source_a.calls, [])
        self.assertEqual(len(source_b.calls), 1)
        mark.assert_not_called()
        handoffs = [payload for _, kind, payload in store.events if kind == "account_handoff"]
        self.assertEqual(handoffs[0]["blocked_reason"], "local_short_circuit")
        self.assertTrue(handoffs[0].get("source_attempt_id"))

    def test_browser_recovery_attempt_uses_a_new_artifact(self):
        from webui.pipeline_exec_details import fetch_job_details

        source = _AttemptSource("a", [
            lambda ids: {
                job_id: SourceOutcome.failure(
                    failed_code="source_cdp_unavailable",
                    safe_log="browser lost",
                ) for job_id in ids
            },
            lambda ids: {
                job_id: SourceOutcome.success(detail={"jd": f"JD-{job_id}"})
                for job_id in ids
            },
        ])
        with tempfile.TemporaryDirectory(prefix="cs_v4_attempt_artifacts_") as artifact_dir, mock.patch.object(
            robin_mod, "make_detail_robin", return_value=None
        ), mock.patch(
            "webui.pipeline_exec_details.BrowserRecovery", _AlwaysRestartRecovery
        ):
            result = fetch_job_details(
                [{"job_id": "j1"}],
                source,
                artifact_dir=artifact_dir,
                execution_config=_config(batch_size=1),
            )

        self.assertFalse(result["hard_stop"], result)
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(len(source.calls), 2)
        self.assertNotEqual(source.calls[0]["artifact"], source.calls[1]["artifact"])

    def test_local_recovery_short_circuit_only_handoffs_unfinished_items(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.pipeline_exec_details import fetch_job_details

        book = _AccountBook()
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        source_a = _AttemptSource("a", [])
        source_a.breaker = SourceCircuitBreaker()

        def first_response(ids):
            source_a.breaker.record_signal("source_rate_limited")
            source_a.breaker.record_signal("source_rate_limited")
            return {
                "j1": SourceOutcome.success(detail={"jd": "JD-j1"}),
                "j2": SourceOutcome.failure(
                    failed_code="source_cdp_unavailable", safe_log="browser lost"
                ),
            }

        source_a.responses = [first_response]
        source_b = _AttemptSource("b", [
            lambda ids: {
                job_id: SourceOutcome.success(detail={"jd": f"JD-{job_id}"})
                for job_id in ids
            }
        ])
        with tempfile.TemporaryDirectory(prefix="cs_v4_recovery_local_") as artifact_dir, mock.patch.object(
            robin_mod, "clone_source", return_value=source_b
        ), mock.patch.object(robin_mod, "_switch_browser_account", return_value=True), mock.patch(
            "webui.pipeline_exec_details.BrowserRecovery", _AlwaysRestartRecovery
        ), mock.patch.object(robin_mod, "mark_account_rate_limited", return_value=None), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ):
            result = fetch_job_details(
                [{"job_id": "j1"}, {"job_id": "j2"}], source_a,
                artifact_dir=artifact_dir, execution_config=_config(batch_size=2),
            )

        self.assertEqual(result["fetched"], 2)
        self.assertEqual(source_a.calls[0]["job_ids"], ["j1", "j2"])
        self.assertEqual(source_b.calls[0]["job_ids"], ["j2"])

    def test_cross_account_handoff_attempt_uses_a_new_artifact(self):
        from webui.pipeline_exec_accounts import set_browser_accounts_path
        from webui.pipeline_exec_details import fetch_job_details

        book = _AccountBook()
        self.addCleanup(book.close)
        set_browser_accounts_path(book.path)
        self.addCleanup(
            lambda: __import__(
                "webui.pipeline_exec_accounts",
                fromlist=["reset_browser_accounts_path"],
            ).reset_browser_accounts_path()
        )
        source_a = _AttemptSource("a", [
            lambda ids: {
                job_id: SourceOutcome.failure(
                    failed_code="source_rate_limited", safe_log="blocked"
                ) for job_id in ids
            }
        ])
        source_b = _AttemptSource("b", [
            lambda ids: {
                job_id: SourceOutcome.success(detail={"jd": f"JD-{job_id}"})
                for job_id in ids
            }
        ])
        with tempfile.TemporaryDirectory(prefix="cs_v4_handoff_artifacts_") as artifact_dir, mock.patch.object(
            robin_mod, "clone_source", return_value=source_b
        ), mock.patch.object(
            robin_mod, "_switch_browser_account", return_value=True
        ), mock.patch.object(
            robin_mod, "mark_account_rate_limited", return_value=None
        ), mock.patch.object(
            robin_mod, "clear_account_rate_limited", return_value=None
        ):
            result = fetch_job_details(
                [{"job_id": "j1"}, {"job_id": "j2"}],
                source_a,
                artifact_dir=artifact_dir,
                execution_config=_config(batch_size=2),
            )

        self.assertFalse(result["hard_stop"], result)
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(len(source_a.calls), 1)
        self.assertEqual(len(source_b.calls), 1)
        self.assertNotEqual(source_a.calls[0]["artifact"], source_b.calls[0]["artifact"])

    def test_successful_jd_removes_the_resolved_pending_record(self):
        from webui.pipeline_exec_details import fetch_job_details
        from webui.runners.ai_screen_jd import run_jd_stage

        class _Store:
            def __init__(self):
                self.deleted = []

            def get_advanced_config_state(self):
                return {}

            def delete_pending_result(self, run_id, job_id):
                self.deleted.append((str(run_id), str(job_id)))
                return True

            def get_pending_result(self, _run_id, _job_id):
                return {"failure_stage": "jd_detail"}

        class _Ctx:
            def __init__(self, result_dir, source, store):
                self.app = SimpleNamespace(config={"RESULT_DIR": result_dir})
                self.store = store
                self.pipeline_guard = None
                self.tasks = {"pending-run": {}}
                self.make_cdp_source = lambda **_kwargs: source

            def activate_task_browser(self, *_args, **_kwargs):
                return None

            def write_run(self, *_args, **_kwargs):
                return None

            def release_worker_resume_claims(self, *_args, **_kwargs):
                return None

        source = _AttemptSource("a", [
            lambda ids: {
                job_id: SourceOutcome.success(detail={"jd": "恢复后的有效 JD"})
                for job_id in ids
            }
        ])
        store = _Store()
        with tempfile.TemporaryDirectory(prefix="cs_v4_pending_artifacts_") as artifact_dir, mock.patch.object(
            robin_mod, "make_detail_robin", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            result = run_jd_stage(
                _Ctx(artifact_dir, source, store),
                "pending-run",
                enriched=[{"job_id": "j1"}],
                survivors=[{"job_id": "j1"}],
                resume_jd={},
                jd_path=os.path.join(artifact_dir, "jd.json"),
                frozen_platform="boss",
                frozen_cdp_port=9222,
                frozen_profile_key="boss:a",
                frozen_browser_account="a",
                execution_config=_config(batch_size=1),
                stop_event=None,
                emit=lambda **_kwargs: None,
                stop_requested=lambda: False,
                handle_user_stop=lambda: None,
                save_jd_checkpoint=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(result[0]["j1"], "恢复后的有效 JD")
        self.assertIn(("pending-run", "j1"), store.deleted)

    def test_resolved_jd_pending_is_deleted_but_unresolved_ai_pending_is_kept(self):
        from webui.runners.ai_screen_jd import run_jd_stage

        class _PendingStore:
            def __init__(self):
                self.rows = [
                    {"job_id": "j1", "failure_stage": "jd_detail"},
                    {"job_id": "j2", "failure_stage": "ai_fine"},
                ]
                self.deleted = []

            def get_advanced_config_state(self):
                return {}

            def list_pending_results(self, _run_id):
                return list(self.rows)

            def delete_pending_result(self, run_id, job_id):
                self.deleted.append((str(run_id), str(job_id)))
                return True

        class _Ctx:
            def __init__(self, result_dir, source, store):
                self.app = SimpleNamespace(config={"RESULT_DIR": result_dir})
                self.store = store
                self.pipeline_guard = None
                self.tasks = {"pending-cleanup": {}}
                self.make_cdp_source = lambda **_kwargs: source

            def activate_task_browser(self, *_args, **_kwargs):
                return None

            def write_run(self, *_args, **_kwargs):
                return None

            def release_worker_resume_claims(self, *_args, **_kwargs):
                return None

        source = _AttemptSource("a", [
            lambda ids: {
                "j1": SourceOutcome.success(detail={"jd": "resolved"}),
                "j2": SourceOutcome.failure(
                    failed_code="source_invalid_output", safe_log="unresolved"
                ),
            }
        ])
        store = _PendingStore()
        with tempfile.TemporaryDirectory(prefix="cs_v4_pending_split_") as artifact_dir, mock.patch.object(
            robin_mod, "make_detail_robin", return_value=None
        ), mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), mock.patch(
            "webui.pipeline_exec.close_debug_chrome", return_value=None
        ):
            result = run_jd_stage(
                _Ctx(artifact_dir, source, store), "pending-cleanup",
                [{"job_id": "j1"}, {"job_id": "j2"}],
                [{"job_id": "j1"}, {"job_id": "j2"}], {},
                os.path.join(artifact_dir, "jd.json"), "boss", 9222,
                "boss:a", "a", _config(batch_size=2), None,
                lambda **_kwargs: None, lambda: False, lambda: None,
                lambda *_args, **_kwargs: None,
            )

        self.assertEqual(result[0]["j1"], "resolved")
        self.assertEqual(store.deleted, [("pending-cleanup", "j1")])


if __name__ == "__main__":
    unittest.main()
