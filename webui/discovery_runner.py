"""Discovery run orchestrator (feature 004).

Drives the discovery run state machine: created -> planning ->
fetching_lists -> fetching_details -> evaluating -> assembling ->
succeeded/partial/failed/interrupted/cancelled. Handles cancellation,
restart-interruption, resume and AI degrade paths.

The orchestrator is the only component that mutates discovery_runs
status/stage during active execution. It uses the store as the
checkpoint authority: every successful plan item / snapshot / assessment
is persisted before the orchestrator moves to the next unit of work, so
an interrupt at any point leaves a resumable state in the database.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from webui.discovery import (
    AISecurityError,
    DiscoveryError,
    ERROR_CODE_MAP,
    SCRAPER_FILTER_FIELDS,
    analyze_resume,
    assess_job_direction,
    build_portfolio,
    build_snapshot,
    calculate_run_completion,
    compile_search_plan,
    EVALUATION_POLICY_VERSION,
)
from webui.ai import AISecurityError as AIProviderSecurityError
from webui.constants import DETAIL_BUDGET
from webui.source import (
    SAFE_FAILURE_CODES,
    BossCdpSource,
    SourceOutcome,
    _input_hash as _source_input_hash,
)
from webui.store import _now
from webui.workbench import normalize_job_link


# ---------------------------------------------------------------------------
# Stage constants (mirror state-machine.md)
# ---------------------------------------------------------------------------

STAGE_CREATED = "created"
STAGE_PLANNING = "planning"
STAGE_FETCHING_LISTS = "fetching_lists"
STAGE_PRIORITIZING = "prioritizing"
STAGE_PROCESSING_JOBS = "processing_jobs"
STAGE_FETCHING_DETAILS = "fetching_details"
STAGE_EVALUATING = "evaluating"
STAGE_ASSEMBLING = "assembling"

STATUS_CREATED = "created"
STATUS_PLANNING = "planning"
STATUS_FETCHING_LISTS = "fetching_lists"
STATUS_PRIORITIZING = "prioritizing"
STATUS_PROCESSING_JOBS = "processing_jobs"
STATUS_FETCHING_DETAILS = "fetching_details"
STATUS_EVALUATING = "evaluating"
STATUS_ASSEMBLING = "assembling"
STATUS_SUCCEEDED = "succeeded"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = frozenset({
    STATUS_CREATED, STATUS_PLANNING, STATUS_FETCHING_LISTS,
    STATUS_FETCHING_DETAILS, STATUS_EVALUATING, STATUS_ASSEMBLING,
})
TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED})
RESUMABLE_STATUSES = frozenset({STATUS_INTERRUPTED, STATUS_PARTIAL})


# ---------------------------------------------------------------------------
# Policy v2 direction relevance selection (T058)
# ---------------------------------------------------------------------------

_DIRECTION_TYPE_PRIORITY = {"core": 0, "adjacent": 1, "growth": 2}


def select_job_directions_v2(enabled_directions, candidate_direction_ids, *, limit=2):
    """Select at most ``limit`` relevant enabled directions for one job.

    Relevance signal is the set of directions that surfaced this candidate
    (``candidate_direction_ids``). Among those, order by direction type
    priority (core < adjacent < growth), then confidence descending, then id.
    Falls back to all enabled directions when the candidate carries no
    direction ids. Never returns more than ``limit`` directions.
    """
    enabled = [d for d in (enabled_directions or []) if isinstance(d, dict) and d.get("id")]
    candidate_ids = set(candidate_direction_ids or [])
    relevant = [d for d in enabled if d["id"] in candidate_ids] if candidate_ids else []
    if not relevant:
        relevant = list(enabled)

    def sort_key(d):
        dtype = d.get("type") or d.get("direction_type") or ""
        try:
            conf = int(d.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0
        return (_DIRECTION_TYPE_PRIORITY.get(dtype, 99), -conf, str(d["id"]))

    relevant.sort(key=sort_key)
    return relevant[:limit]


# ---------------------------------------------------------------------------
# DiscoveryPerformanceMetrics (feature 005 deterministic performance gates)
# ---------------------------------------------------------------------------


class DiscoveryPerformanceMetrics:
    """Deterministic, injectable-clock performance metrics for policy v2 runs.

    SC-001: candidate pool assembled within 90 simulated seconds.
    SC-002: first five assessed results visible within 300 simulated seconds.
    """

    CONTRACT_VERSION = "perf_v1"

    def __init__(self, *, monotonic_clock=None):
        import time as _time
        self._clock = monotonic_clock or _time.monotonic
        self._start: float | None = None
        self._list_completed_at: float | None = None
        self._list_query_count = 0
        self._list_job_count = 0
        self._selection: dict = {}
        self._details: list[dict] = []
        self._ai_groups: list[dict] = []
        self._result_visible: list[tuple[str, float]] = []
        self._all_complete_at: float | None = None
        self._blockers: list[dict] = []
        self._resume_count = 0
        self._source_breaker_events: list[dict] = []

    def start(self) -> None:
        self._start = self._clock()

    def mark_list_completed(self, *, query_count: int, job_count: int) -> None:
        self._list_completed_at = self._clock()
        self._list_query_count = query_count
        self._list_job_count = job_count

    def record_selection(self, *, selected_count: int, deferred_count: int,
                         reasons: dict | None = None) -> None:
        self._selection = {
            "selected_count": selected_count,
            "deferred_count": deferred_count,
            "reasons": reasons or {},
        }

    def record_detail_completed(self, *, job_id: str, total_seconds: float,
                                wait_seconds: float, wait_reason: str,
                                batch: int, concurrency: int,
                                reused: bool = False) -> None:
        self._details.append({
            "job_id": job_id,
            "total_seconds": total_seconds,
            "wait_seconds": wait_seconds,
            "wait_reason": wait_reason,
            "batch": batch,
            "concurrency": concurrency,
            "reused": reused,
        })

    def record_ai_group_completed(self, *, job_id: str, direction_count: int,
                                  call_count: int, duration_seconds: float) -> None:
        self._ai_groups.append({
            "job_id": job_id,
            "direction_count": direction_count,
            "call_count": call_count,
            "duration_seconds": duration_seconds,
        })

    def record_result_visible(self, *, job_id: str) -> None:
        self._result_visible.append((job_id, self._clock()))

    def mark_all_complete(self) -> None:
        self._all_complete_at = self._clock()

    def record_blocker(self, *, code: str, stage: str, external: bool) -> None:
        self._blockers.append({"code": code, "stage": stage, "external": external})

    def record_resume(self) -> None:
        self._resume_count += 1

    def record_source_breaker(self, *, code: str, stage: str) -> None:
        self._source_breaker_events.append({"code": code, "stage": stage})

    def build_report(self) -> dict:
        start = self._start or 0.0
        list_duration = (self._list_completed_at - start) if self._list_completed_at is not None else None

        # Detail percentiles.
        detail_totals = sorted(d["total_seconds"] for d in self._details)
        detail_waits = sorted(d["wait_seconds"] for d in self._details)

        def _p(values: list[float], pct: float) -> float | None:
            if not values:
                return None
            idx = max(0, min(len(values) - 1, int(len(values) * pct / 100.0)))
            return values[idx]

        # Timing milestones.
        first_result_seconds = None
        first_five_seconds = None
        if self._result_visible:
            times = [t for _, t in self._result_visible]
            first_result_seconds = times[0] - start
            if len(times) >= 5:
                first_five_seconds = times[4] - start
        all_complete_seconds = (
            (self._all_complete_at - start) if self._all_complete_at is not None else None
        )

        # Gates.
        has_external_blocker = any(b.get("external") for b in self._blockers)
        list_pool_ok = list_duration is not None and list_duration <= 90.0
        first_five_ok = first_five_seconds is not None and first_five_seconds <= 300.0
        all_complete_ok = all_complete_seconds is not None and all_complete_seconds <= 600.0
        no_blocker = not has_external_blocker
        overall = list_pool_ok and first_five_ok and all_complete_ok and no_blocker

        status = "blocked" if has_external_blocker else ("complete" if self._all_complete_at is not None else "running")

        return {
            "contract_version": self.CONTRACT_VERSION,
            "status": status,
            "list": {
                "query_count": self._list_query_count,
                "job_count": self._list_job_count,
                "duration_seconds": list_duration,
            },
            "selection": self._selection or {"selected_count": 0, "deferred_count": 0, "reasons": {}},
            "details": {
                "processed_count": len(self._details),
                "reused_count": sum(1 for d in self._details if d.get("reused")),
                "failed_count": 0,
                "cancelled_count": 0,
                "items": self._details,
                "duration_seconds": {"p50": _p(detail_totals, 50), "p95": _p(detail_totals, 95)},
                "wait_duration_seconds": {"p50": _p(detail_waits, 50), "p95": _p(detail_waits, 95)},
                "batch_count": len({d["batch"] for d in self._details}),
                "peak_concurrency": max((d["concurrency"] for d in self._details), default=0),
            },
            "ai": {
                "group_count": len(self._ai_groups),
                "call_count": sum(g["call_count"] for g in self._ai_groups),
                "duration_seconds": sum(g["duration_seconds"] for g in self._ai_groups),
            },
            "timing": {
                "first_result_seconds": first_result_seconds,
                "first_five_seconds": first_five_seconds,
                "all_complete_seconds": all_complete_seconds,
            },
            "resume_count": self._resume_count,
            "source_breaker_events": self._source_breaker_events,
            "blockers": self._blockers,
            "gates": {
                "list_pool_within_90_seconds": list_pool_ok,
                "first_five_within_300_seconds": first_five_ok,
                "all_complete_within_600_seconds": all_complete_ok,
                "no_external_blocker": no_blocker,
                "overall": overall,
            },
        }


# ---------------------------------------------------------------------------
# DiscoveryRunner
# ---------------------------------------------------------------------------


class DiscoveryRunner:
    """Drive a discovery run through all stages to a terminal state.

    The runner is constructed with a store, a JobSource, and an optional
    AI provider. ``run(run_id)`` executes synchronously and returns the
    final run dict. For HTTP integration, ``run_async(run_id)`` submits
    the run to a background thread.

    Failure isolation:
      - A failed list fetch marks the plan item failed but does not abort
        the run.
      - A failed detail fetch produces a partial snapshot (completeness
        = "unavailable") but the job is still assessed (routed to
        needs_review).
      - A failed assessment (AI invalid) routes to needs_review and the
        run continues.

    Cancellation:
      - ``request_cancel(run_id)`` sets ``cancel_requested_at`` and the
        main loop checks before each unit of work.
      - Already-terminal runs ignore the request.

    Restart safety:
      - On construction the runner marks any run found in an active
        stage as ``interrupted`` (call ``mark_interrupted_on_restart``
        at app startup). Resume continues from the last saved stage.
    """

    def __init__(
        self,
        store,
        *,
        source: BossCdpSource | Any = None,
        ai_provider: Any = None,
        result_dir: Path | str | None = None,
        max_workers: int = 1,
        cancel_event: threading.Event | None = None,
        monotonic_clock: Any = None,
    ):
        self.store = store
        self.source = source
        self.ai_provider = ai_provider
        self.result_dir = Path(result_dir) if result_dir else Path.home() / ".career-scout" / "webui" / "discovery"
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max(1, int(max_workers))
        self._cancel_events: dict[str, threading.Event] = {}
        _list_jobs_cache: dict[str, list[dict]] = {}
        self._list_jobs_cache = _list_jobs_cache
        self._lock = threading.Lock()
        self._external_cancel_event = cancel_event
        self._monotonic_clock = monotonic_clock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, run_id: str) -> dict:
        """Execute the run synchronously to a terminal state."""
        try:
            run = self.store.get_discovery_run(run_id)
        except KeyError:
            raise DiscoveryError("not_found", user_message="运行不存在。")
        if run["status"] in TERMINAL_STATUSES:
            return run
        # Resume: if interrupted/partial, verify identity before re-activating.
        if run["status"] not in (STATUS_CREATED,) and run["status"] not in ACTIVE_STATUSES:
            # T081/T083: v2 runs must reject policy_version and input_hash drift
            # (http-api.md L319-320). v1 runs keep the legacy resume behavior
            # (no hash drift check) so existing 004 historical runs and
            # 005-code-created v1 runs continue to resume under policy v1.
            # Note: drift of policy_version from "discovery_v2" to a valid
            # v1 value is NOT detectable without a separate immutable field;
            # we only reject drift to invalid policy_version values.
            check_v2_resume_hash_drift(self.store, run)
            # interrupted or partial -> re-activate
            self.store.update_discovery_run(run_id, status=STATUS_CREATED)

        cancel_event = self._register_run(run_id)
        if self.source is not None and hasattr(self.source, "cancel_event"):
            self.source.cancel_event = cancel_event
        try:
            self._execute_stages(run_id, cancel_event)
        except DiscoveryError:
            self.store.update_discovery_run(
                run_id, status=STATUS_FAILED, failure_code="verification_error",
                failure_stage="executing", completed=True,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self.store.update_discovery_run(
                run_id, status=STATUS_FAILED, failure_code="verification_error",
                failure_stage="executing", completed=True,
            )
            raise DiscoveryError("verification_error", log_detail=str(exc))
        finally:
            self._unregister_run(run_id)
        return self.store.get_discovery_run(run_id)

    def run_async(self, run_id: str) -> None:
        """Submit the run to a background thread. Returns immediately."""
        executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="discovery-run")
        executor.submit(self._safe_run, run_id)

    def _safe_run(self, run_id: str) -> None:
        try:
            self.run(run_id)
        except Exception:  # noqa: BLE001
            # Error already persisted inside run(); swallow to keep thread alive.
            pass

    def run_progressive_detail_eval(self, run_id: str) -> dict:
        """V2 progressive orchestration: detail→assessment→result_revision per candidate.

        Processes only *selected* candidates in rank order.  For each
        candidate the detail is fetched, a snapshot persisted, an
        assessment created for every enabled direction, and
        ``result_revision`` incremented immediately — the frontend can
        poll and see results appear one-by-one without waiting for the
        entire run to finish.

        Every assessment is independently checkpointed to SQLite so an
        interrupt at any point leaves completed work recoverable.
        """
        run = self.store.get_discovery_run(run_id)
        if run.get("policy_version") != "discovery_v2":
            raise DiscoveryError("state_conflict", user_message="仅支持 policy v2 运行。")

        # Enter processing_jobs stage.
        self.store.update_discovery_run(
            run_id, status=STATUS_PROCESSING_JOBS, stage=STAGE_PROCESSING_JOBS,
        )
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_PROCESSING_JOBS})

        # Load confirmation context once.
        confirmation = self._load_confirmation_view(run)
        hard_constraints = confirmation.get("hard_constraints", {}) or {}
        # _load_confirmation_view already filters to enabled directions.
        enabled_directions = confirmation.get("enabled_directions", [])
        directions = [
            d for d in self.store.list_directions(run["analysis_id"])
            if d["id"] in {ed["id"] for ed in enabled_directions}
        ]
        evidence = self.store.list_evidence(run["analysis_id"])
        evidence_ids = {e["id"] for e in evidence}

        # Iterate selected candidates in stable rank order.
        selected = self.store.list_run_candidates(run_id, selection_decision="selected")
        selected.sort(key=lambda c: (c.get("selection_rank") or 9999, c["job_id"]))

        result_revision = 0
        detail_completed = 0
        assessment_completed = 0

        # T077: ensure detail_selected_count reflects the actual selected
        # candidates being processed. This is normally set by _stage_prioritizing,
        # but progressive eval can be entered directly on a resumed run where
        # the counter was never stamped.
        if selected:
            self.store.update_discovery_run(run_id, counters={
                "detail_selected_count": len(selected),
            })

        for candidate in selected:
            job_id = candidate["job_id"]
            source_url = candidate.get("source_url", "")

            # T079: cancel signal — when set, no new candidate work starts.
            # Already-persisted snapshots/assessments are preserved; the run
            # transitions to cancelled via the stage-loop cancel check.
            cancel_event = self._cancel_events.get(run_id)
            if cancel_event is not None and cancel_event.is_set():
                return self.store.get_discovery_run(run_id)
            if self.is_cancelled(run_id):
                return self.store.get_discovery_run(run_id)

            # T075: circuit breaker — when open, no new source work starts.
            # Already-assessed candidates keep their results; the run
            # transitions to partial (if usable results exist) or failed.
            if self._source_breaker_open(run_id, STAGE_PROCESSING_JOBS):
                self._finalize_breaker_open(run_id, STAGE_PROCESSING_JOBS)
                return self.store.get_discovery_run(run_id)

            # --- Fetch detail → snapshot -----------------------------------
            job_dict = {
                "job_id": job_id,
                "source_url": source_url,
                "title": (candidate.get("list_fields") or {}).get("title", ""),
                "company": (candidate.get("list_fields") or {}).get("company", ""),
                "salary": (candidate.get("list_fields") or {}).get("salary", ""),
                "location": (candidate.get("list_fields") or {}).get("location", ""),
            }
            detail_ok = self._fetch_one_detail(
                run_id, job_dict,
                run_candidate_id=candidate.get("id"),
                list_fields=candidate.get("list_fields") or {},
            )
            if detail_ok:
                detail_completed += 1

            # Retrieve the snapshot we just saved.
            snapshots = self._list_snapshots(run_id)
            snapshot = next((s for s in snapshots if s["job_id"] == job_id), None)
            if snapshot is None:
                continue

            # --- Assess immediately: one v2 group of ≤2 relevant directions ---
            selected_dirs = select_job_directions_v2(
                directions, candidate.get("direction_ids"), limit=2,
            )
            pending_dirs = []
            for direction in selected_dirs:
                existing = self._get_assessment(run_id, snapshot["id"], direction["id"])
                if existing and existing.get("status") == "completed":
                    assessment_completed += 1
                else:
                    pending_dirs.append(direction)
            if pending_dirs:
                assessment_completed += self._evaluate_job_v2_group(
                    run_id, snapshot, pending_dirs, hard_constraints, evidence_ids,
                )

            # --- Checkpoint: increment result_revision immediately ---------
            result_revision += 1
            self.store.update_discovery_run(run_id, counters={
                "result_revision": result_revision,
                "detail_completed_count": detail_completed,
                "assessment_completed_count": assessment_completed,
            })
            self.store.append_discovery_event(run_id, "progressive_result_visible", {
                "job_id": job_id,
                "result_revision": result_revision,
            })
            # T077: timing — first_result_at on first visible result,
            # first_batch_at on 5th. Monotonic (COALESCE in mark_run_timing
            # keeps the first non-NULL value across resumes/re-runs).
            timing_kwargs = {"first_result_at": _now()}
            if result_revision >= 5:
                timing_kwargs["first_batch_at"] = _now()
            self.store.mark_run_timing(run_id, **timing_kwargs)

            # Mark candidate as detail_ready → assessed.
            try:
                self.store.update_run_candidate_state(
                    candidate["id"], state="assessed",
                    expected_state="selected",
                )
            except Exception:  # noqa: BLE001 - CAS conflict on resume
                pass

        # T077: mark processing stage completion timestamp.
        self.store.mark_run_timing(run_id, processing_completed_at=_now())
        return self.store.get_discovery_run(run_id)

    def request_cancel(self, run_id: str) -> dict:
        """Request cancellation. The main loop checks before each unit of work."""
        try:
            run = self.store.get_discovery_run(run_id)
        except KeyError:
            raise DiscoveryError("not_found", user_message="运行不存在。")
        if run["status"] in TERMINAL_STATUSES:
            raise DiscoveryError(
                "state_conflict",
                user_message=f"运行已终态 ({run['status']})，无法取消。",
            )
        with self._lock:
            event = self._cancel_events.get(run_id)
        if event is None:
            event = threading.Event()
        event.set()
        self.store.update_discovery_run(run_id, cancel_requested=True)
        self.store.append_discovery_event(run_id, "cancel_requested", {"run_id": run_id})
        return self.store.get_discovery_run(run_id)

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(run_id)
        if event is not None and event.is_set():
            return True
        if self._external_cancel_event is not None and self._external_cancel_event.is_set():
            return True
        try:
            run = self.store.get_discovery_run(run_id)
        except KeyError:
            return True
        return run.get("cancel_requested_at") is not None

    # ------------------------------------------------------------------
    # Stage execution
    # ------------------------------------------------------------------

    def _execute_stages(self, run_id: str, cancel_event: threading.Event) -> None:
        """Drive all stages to terminal. Idempotent: resumes from last stage."""
        # Stage 1: planning (compile search plan if not yet present)
        self._stage_planning(run_id, cancel_event)
        if cancel_event.is_set() or self.is_cancelled(run_id):
            return self._handle_cancel(run_id)

        # Stage 2: fetching lists
        if self._stage_fetching_lists(run_id, cancel_event) is False:
            return
        if cancel_event.is_set() or self.is_cancelled(run_id):
            return self._handle_cancel(run_id)

        # Stage 2.5 (v2): prioritizing — persist candidates, precheck, select
        run = self.store.get_discovery_run(run_id)
        if run.get("policy_version") == "discovery_v2":
            self._stage_prioritizing(run_id, cancel_event)
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return self._handle_cancel(run_id)

        # Stage 3+4: v2 progressive (detail+assessment per candidate) or v1
        # sequential (fetch all details, then evaluate all).
        run = self.store.get_discovery_run(run_id)
        if run.get("policy_version") == "discovery_v2":
            # T081: v2 progressive path — run_progressive_detail_eval handles
            # detail fetch + assessment + result_revision per candidate. It
            # checks cancel_event internally via self._cancel_events.
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return self._handle_cancel(run_id)
            self.run_progressive_detail_eval(run_id)
        else:
            self._stage_fetching_details(run_id, cancel_event)
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return self._handle_cancel(run_id)
            self._stage_evaluating(run_id, cancel_event)
        if cancel_event.is_set() or self.is_cancelled(run_id):
            return self._handle_cancel(run_id)

        # Stage 5: assembling + completion
        self._stage_assembling(run_id, cancel_event)

    def _stage_planning(self, run_id: str, cancel_event: threading.Event) -> None:
        run = self.store.get_discovery_run(run_id)
        # T081: include PRIORITIZING and PROCESSING_JOBS so resume from a
        # later v2 stage skips plan recompilation.
        if run["stage"] in (STAGE_FETCHING_LISTS, STAGE_PRIORITIZING,
                            STAGE_PROCESSING_JOBS, STAGE_FETCHING_DETAILS,
                            STAGE_EVALUATING, STAGE_ASSEMBLING):
            return  # plan already compiled
        self.store.update_discovery_run(run_id, status=STATUS_PLANNING, stage=STAGE_PLANNING, started=True)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_PLANNING})

        # Check if plan already exists
        try:
            plan = self.store.get_search_plan(run_id)
        except KeyError:
            plan = None
        if plan is None:
            confirmation = self._load_confirmation_view(run)
            compiled = compile_search_plan(confirmation)
            items = self._materialize_plan_items(compiled, run_id)
            self.store.create_search_plan(
                run_id,
                detail_budget=compiled["detail_budget"],
                items=items,
            )
            self.store.append_discovery_event(
                run_id, "plan_compiled",
                {"item_count": len(items), "detail_budget": compiled["detail_budget"]},
            )
        self.store.update_discovery_run(run_id, status=STATUS_FETCHING_LISTS, stage=STAGE_FETCHING_LISTS)

    def _stage_fetching_lists(self, run_id: str, cancel_event: threading.Event) -> bool:
        run = self.store.get_discovery_run(run_id)
        # T081: include PRIORITIZING and PROCESSING_JOBS so resume from a
        # later v2 stage skips list fetching.
        if run["stage"] in (STAGE_PRIORITIZING, STAGE_PROCESSING_JOBS,
                            STAGE_FETCHING_DETAILS, STAGE_EVALUATING, STAGE_ASSEMBLING):
            return True
        self.store.update_discovery_run(run_id, status=STATUS_FETCHING_LISTS, stage=STAGE_FETCHING_LISTS)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_FETCHING_LISTS})

        plan = self.store.get_search_plan(run_id)
        # T075: if the breaker is open from a prior run/retry, try to reset
        # it via preflight + cooldown before abandoning new source work.
        self._try_reset_source_breaker()
        preflight = getattr(self.source, "preflight", None)
        if callable(preflight):
            outcome = preflight()
            if not outcome.ok:
                failure_code = outcome.failed_code
                if failure_code not in SAFE_FAILURE_CODES:
                    failure_code = "source_unknown_error"
                for item in plan["items"]:
                    if item["status"] not in ("completed", "failed", "cancelled", "skipped"):
                        self.store.update_plan_item(
                            item["id"], status="failed",
                            failure_code=failure_code, completed=True,
                        )
                self.store.append_discovery_event(run_id, "source_preflight_failed", {
                    "failure_code": failure_code,
                    "safe_log": outcome.safe_log,
                })
                self.store.update_discovery_run(
                    run_id,
                    status=STATUS_FAILED,
                    stage=STAGE_FETCHING_LISTS,
                    failure_code=failure_code,
                    failure_stage=STAGE_FETCHING_LISTS,
                    completed=True,
                )
                return False
        source_count = 0
        for item in plan["items"]:
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return
            if item["status"] in ("completed", "failed", "cancelled", "skipped"):
                continue
            # T075: circuit breaker — when open, stop starting new list fetches.
            # Remaining items stay non-terminal so the run can resume later.
            if self._source_breaker_open(run_id, STAGE_FETCHING_LISTS):
                self.store.update_discovery_run(run_id, counters={"source_count": source_count})
                return False
            # M6: verify input_hash integrity on resume (state-machine.md:51).
            current_hash = _source_input_hash({
                "keyword": item.get("keyword", ""),
                "city": item.get("city", ""),
                "source_filters": item.get("source_filters", {}) or {},
                "target_pages": item.get("target_pages", 1),
            })
            if item.get("input_hash") and current_hash != item["input_hash"]:
                self.store.update_plan_item(
                    item["id"], status="failed",
                    failure_code="input_hash_mismatch", completed=True,
                )
                self.store.append_discovery_event(run_id, "input_hash_mismatch", {
                    "item_id": item["id"], "stored": item["input_hash"], "current": current_hash,
                })
                continue
            self._fetch_one_list(run_id, item)
            updated_item = self._refresh_item(item)
            if updated_item["status"] == "completed":
                source_count += 1
        self.store.update_discovery_run(run_id, counters={"source_count": source_count})
        # T077: mark list stage completion timestamp.
        self.store.mark_run_timing(run_id, list_completed_at=_now())
        return True

    def _stage_prioritizing(self, run_id: str, cancel_event: threading.Event) -> None:
        """V2 candidate pool: persist all, precheck, select priority details."""
        from webui.discovery import precheck_list_candidate, select_priority_details

        run = self.store.get_discovery_run(run_id)
        # T081: include PROCESSING_JOBS so resume from the progressive stage
        # skips re-prioritizing (candidates are already selected).
        if run["stage"] in (STAGE_PROCESSING_JOBS, STAGE_FETCHING_DETAILS,
                            STAGE_EVALUATING, STAGE_ASSEMBLING):
            return  # already past prioritizing
        self.store.update_discovery_run(run_id, status=STATUS_PRIORITIZING, stage=STAGE_PRIORITIZING)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_PRIORITIZING})

        # Check if candidates already persisted (resume case).
        existing = self.store.list_run_candidates(run_id)
        if not existing:
            # Persist all list jobs as candidates.
            plan = self.store.get_search_plan(run_id)
            confirmation = self._load_confirmation_view(run)
            hard_constraints = confirmation.get("hard_constraints", {})
            input_hash = run.get("input_hash", "")
            enabled_dirs = [
                d.get("direction_id", d.get("id", ""))
                for d in confirmation.get("directions", [])
                if d.get("enabled")
            ]
            for item in plan["items"]:
                if item["status"] != "completed":
                    continue
                jobs = self._read_list_jobs(item)
                direction_ids = item.get("direction_ids", [])
                search_terms = [item.get("keyword", "")] if item.get("keyword") else []
                for idx, job in enumerate(jobs):
                    if cancel_event.is_set():
                        return
                    job_id = str(job.get("job_id") or job.get("id") or "")
                    source_url = normalize_job_link(job.get("source_url") or job.get("url") or "")
                    if not job_id or not source_url:
                        continue
                    list_fields = {
                        "title": job.get("title", ""),
                        "salary": job.get("salary", ""),
                        "location": job.get("location", job.get("city", "")),
                        "company": job.get("company", ""),
                    }
                    self.store.upsert_run_candidate(
                        run_id=run_id, job_id=job_id, source_url=source_url,
                        direction_ids=direction_ids, search_terms=search_terms,
                        source_positions=[{"item": item.get("keyword", ""), "page": item.get("page_cursor", 0), "rank": idx}],
                        list_fields=list_fields, input_hash=input_hash,
                    )

        # Run precheck on all discovered candidates.
        candidates = self.store.list_run_candidates(run_id, state="discovered")
        confirmation = self._load_confirmation_view(run)
        hard_constraints = confirmation.get("hard_constraints", {})
        for c in candidates:
            if cancel_event.is_set():
                return
            result = precheck_list_candidate(c.get("list_fields", {}), hard_constraints)
            if result["outcome"] == "violation":
                self.store.update_run_candidate_state(
                    c["id"], state="excluded", selection_decision="excluded",
                    precheck_outcome="violation", precheck=result["checks"],
                    selection_reason=result.get("reason", "hard_violation"),
                    expected_state="discovered",
                )
            elif result["outcome"] == "unknown":
                self.store.update_run_candidate_state(
                    c["id"], state="prechecked_unknown",
                    precheck_outcome="unknown", precheck=result["checks"],
                    expected_state="discovered",
                )
            else:
                self.store.update_run_candidate_state(
                    c["id"], state="prechecked_pass",
                    precheck_outcome="pass", precheck=result["checks"],
                    expected_state="discovered",
                )

        # Priority selection on eligible candidates.
        eligible = self.store.list_run_candidates(run_id)
        eligible = [c for c in eligible if c["selection_decision"] == "pending"]
        plan = self.store.get_search_plan(run_id)
        detail_budget = int(plan.get("detail_budget", 15))
        enabled_dirs = [
            d.get("direction_id", d.get("id", ""))
            for d in confirmation.get("directions", [])
            if d.get("enabled")
        ]
        selection = select_priority_details(
            eligible, detail_budget=detail_budget, directions=enabled_dirs,
        )
        for item in selection["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"],
                selection_reason="priority_score",
            )
        for item in selection["deferred"]:
            if item.get("selection_decision") != "excluded":
                self.store.update_run_candidate_state(
                    item["id"], selection_decision="deferred",
                    selection_reason="budget_deferred",
                )

        selected_count = len(selection["selected"])
        self.store.update_discovery_run(run_id, counters={
            "list_candidate_count": len(existing) if existing else len(self.store.list_run_candidates(run_id)),
            "detail_selected_count": selected_count,
        })
        self.store.append_discovery_event(run_id, "prioritizing_complete", {
            "total_candidates": len(self.store.list_run_candidates(run_id)),
            "selected": selected_count,
            "deferred": len(selection["deferred"]),
        })

    def _stage_fetching_details(self, run_id: str, cancel_event: threading.Event) -> None:
        run = self.store.get_discovery_run(run_id)
        if run["stage"] in (STAGE_EVALUATING, STAGE_ASSEMBLING):
            return
        self.store.update_discovery_run(run_id, status=STATUS_FETCHING_DETAILS, stage=STAGE_FETCHING_DETAILS)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_FETCHING_DETAILS})

        plan = self.store.get_search_plan(run_id)
        detail_budget = int(plan.get("detail_budget", DETAIL_BUDGET))
        # Collect all unique jobs across plan items, respecting budget.
        seen_job_ids: set[str] = set()
        jobs_to_fetch: list[dict] = []
        for item in plan["items"]:
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return
            if item["status"] != "completed":
                continue
            # Read list output path -> jobs
            jobs = self._read_list_jobs(item)
            for job in jobs:
                job_id = str(job.get("job_id") or job.get("id") or "")
                source_url = normalize_job_link(job.get("source_url") or job.get("url") or "")
                if not source_url:
                    continue
                if not job_id or job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job_id)
                if len(jobs_to_fetch) >= detail_budget:
                    break
                jobs_to_fetch.append({
                    **job, "source_url": source_url, "_plan_item_id": item["id"],
                })
            if len(jobs_to_fetch) >= detail_budget:
                break

        detail_count = 0
        for job in jobs_to_fetch:
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return
            # T075: circuit breaker — when open, stop starting new detail fetches.
            if self._source_breaker_open(run_id, STAGE_FETCHING_DETAILS):
                self.store.update_discovery_run(run_id, counters={"detail_count": detail_count})
                return
            if self._fetch_one_detail(run_id, job):
                detail_count += 1
        self.store.update_discovery_run(run_id, counters={"detail_count": detail_count})

    def _stage_evaluating(self, run_id: str, cancel_event: threading.Event) -> None:
        run = self.store.get_discovery_run(run_id)
        if run["stage"] in (STAGE_ASSEMBLING,):
            return
        self.store.update_discovery_run(run_id, status=STATUS_EVALUATING, stage=STAGE_EVALUATING)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_EVALUATING})

        run_dict = self.store.get_discovery_run(run_id)
        analysis_id = run_dict["analysis_id"]
        confirmation = self.store.get_confirmation(run_dict["confirmation_id"])
        enabled_direction_ids = {
            item["direction_id"]
            for item in confirmation.get("directions", [])
            if item.get("enabled")
        }
        directions = [
            direction
            for direction in self.store.list_directions(analysis_id)
            if direction["id"] in enabled_direction_ids
        ]
        evidence = self.store.list_evidence(analysis_id)
        evidence_ids = {e["id"] for e in evidence}

        snapshots = self._list_snapshots(run_id)
        # Determine hard_constraints from the confirmation.
        hard_constraints = confirmation.get("hard_constraints", {}) or {}

        evaluated_count = 0
        counts = {"high_count": 0, "adjacent_count": 0, "growth_count": 0, "review_count": 0, "unsuitable_count": 0}
        for snapshot in snapshots:
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return
            for direction in directions:
                # Skip already-completed assessments.
                existing = self._get_assessment(run_id, snapshot["id"], direction["id"])
                if existing and existing.get("status") == "completed":
                    category = existing.get("category", "needs_review")
                    self._bump_counts(counts, category)
                    evaluated_count += 1
                    self.store.update_discovery_run(
                        run_id,
                        counters={"evaluated_count": evaluated_count, **counts},
                    )
                    continue
                self._evaluate_one(run_id, snapshot, direction, evidence_ids, hard_constraints)
                evaluated_count += 1
                # Re-read to update counts
                updated = self._get_assessment(run_id, snapshot["id"], direction["id"])
                if updated:
                    self._bump_counts(counts, updated.get("category", "needs_review"))
                # Persist progress after every assessment.  If a later provider
                # call hangs or the process is interrupted, completed work and
                # its counter remain independently auditable and resumable.
                self.store.update_discovery_run(
                    run_id,
                    counters={"evaluated_count": evaluated_count, **counts},
                )
                self.store.append_discovery_event(
                    run_id,
                    "assessment_completed",
                    {"job_id": snapshot["job_id"], "direction_id": direction["id"]},
                )
        self.store.update_discovery_run(
            run_id,
            counters={"evaluated_count": evaluated_count, **counts},
        )

    def _stage_assembling(self, run_id: str, cancel_event: threading.Event) -> None:
        self.store.update_discovery_run(run_id, status=STATUS_ASSEMBLING, stage=STAGE_ASSEMBLING)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_ASSEMBLING})

        run = self.store.get_discovery_run(run_id)
        plan = self.store.get_search_plan(run_id)
        assessments = self.store.list_assessments(run_id)
        snapshots = self.store.list_snapshots(run_id)
        snapshots_by_id = {snapshot["id"]: snapshot for snapshot in snapshots}
        portfolio_assessments = []
        for assessment in assessments:
            merged = dict(assessment)
            snapshot = snapshots_by_id.get(assessment.get("snapshot_id"), {})
            merged.update({
                "job_id": snapshot.get("job_id", ""),
                "title": snapshot.get("title", ""),
                "company": snapshot.get("company", ""),
                "salary": snapshot.get("salary", ""),
                "location": snapshot.get("location", ""),
                "snapshot_completeness": snapshot.get("completeness", "unavailable"),
            })
            portfolio_assessments.append(merged)
        directions = self.store.list_directions(run["analysis_id"])

        portfolio = build_portfolio(
            run_id, portfolio_assessments, directions,
            resume_id=run.get("resume_id", ""),
            analysis_id=run.get("analysis_id", ""),
            confirmation_id=run.get("confirmation_id", ""),
        )
        completion = calculate_run_completion(run, plan["items"], assessments)
        final_status = completion["status"]
        self.store.append_discovery_event(
            run_id, "run_completed",
            {"status": final_status, "reason": completion.get("reason"), "counts": portfolio["counts"]},
        )
        self.store.update_discovery_run(
            run_id, status=final_status, stage=STAGE_ASSEMBLING, completed=True,
        )

    # ------------------------------------------------------------------
    # T075: Source circuit breaker integration
    # ------------------------------------------------------------------

    def _source_breaker_open(self, run_id: str, stage: str) -> bool:
        """Check whether the source circuit breaker is open.

        When open, records a ``source_breaker_open`` event and metrics entry
        so the run can transition to partial/failed. Returns True iff the
        breaker is open (no new source work should start).
        """
        breaker = getattr(self.source, "breaker", None)
        if breaker is None or not breaker.is_open():
            return False
        state = breaker.state()
        self.store.append_discovery_event(
            run_id, "source_breaker_open",
            {
                "stage": stage,
                "last_signal": state.get("last_signal"),
                "consecutive": state.get("consecutive"),
            },
        )
        self.metrics.record_source_breaker(
            code=state.get("last_signal") or "source_blocked",
            stage=stage,
        )
        return True

    def _try_reset_source_breaker(self) -> bool:
        """Attempt to reset an open breaker via preflight + cooldown.

        Returns True iff the breaker is now closed (or was already closed).
        The reset requires BOTH cooldown elapsed AND preflight success
        (state-machine.md L107).
        """
        breaker = getattr(self.source, "breaker", None)
        if breaker is None or not breaker.is_open():
            return True  # already closed
        preflight = getattr(self.source, "preflight", None)
        if not callable(preflight):
            return False
        try:
            outcome = preflight()
        except Exception:  # noqa: BLE001 - preflight must never crash the run
            return False
        return breaker.try_reset(outcome.ok)

    def _finalize_breaker_open(self, run_id: str, stage: str) -> None:
        """Transition a run to partial/failed when the breaker is open.

        Contract (state-machine.md L106): run becomes partial when usable
        results exist, otherwise failed/blocked with safe source code.
        """
        assessments = self.store.list_assessments(run_id)
        usable_categories = {"high_match", "adjacent_match", "growth_match"}
        has_usable = any(a.get("category") in usable_categories for a in assessments)
        failure_code = "source_blocked"
        if has_usable:
            self.store.update_discovery_run(
                run_id,
                status=STATUS_PARTIAL,
                stage=stage,
                failure_code=failure_code,
                failure_stage=stage,
            )
            self.store.append_discovery_event(
                run_id, "run_partial",
                {"reason": "source_breaker_open", "usable_count": len(assessments)},
            )
        else:
            self.store.update_discovery_run(
                run_id,
                status=STATUS_FAILED,
                stage=stage,
                failure_code=failure_code,
                failure_stage=stage,
                completed=True,
            )
            self.store.append_discovery_event(
                run_id, "run_failed",
                {"reason": "source_breaker_open", "failure_code": failure_code},
            )

    # ------------------------------------------------------------------
    # Per-unit work
    # ------------------------------------------------------------------

    def _fetch_one_list(self, run_id: str, item: dict) -> None:
        if self.source is None:
            self.store.update_plan_item(item["id"], status="failed", failure_code="source_unavailable", completed=True)
            self.store.append_discovery_event(run_id, "plan_item_failed", {"item_id": item["id"], "code": "source_unavailable"})
            return
        # Allocate output path.
        list_output_path = str(self.result_dir / f"list_{run_id}_{item['id']}.json")
        plan_item_view = {
            "keyword": item["keyword"],
            "city": item.get("city", ""),
            "source_filters": item.get("source_filters", {}),
            "input_hash": item["input_hash"],
            "target_pages": int(item.get("target_pages", 1)),
            "list_output_path": list_output_path,
        }
        self.store.append_discovery_event(
            run_id, "plan_item_started", {"item_id": item["id"]},
        )
        outcome = self.source.fetch_list(plan_item_view)
        if outcome.ok:
            # Cache jobs in-memory keyed by item id for the detail stage.
            self._list_jobs_cache[item["id"]] = list(outcome.jobs)
            # Persist canonical jobs (no plan_item_id column on jobs table;
            # traceability is via discovery_job_snapshots.run_id + plan items).
            self._persist_jobs(outcome.jobs, run_id, item)
            self.store.update_plan_item(item["id"], status="completed", completed=True)
            self.store.append_discovery_event(
                run_id, "plan_item_succeeded",
                {"item_id": item["id"], "job_count": len(outcome.jobs)},
            )
        else:
            self.store.update_plan_item(
                item["id"], status="failed", failure_code=outcome.failed_code or "source_unknown_error",
                attempt=True, completed=True,
            )
            self.store.append_discovery_event(
                run_id, "plan_item_failed",
                {"item_id": item["id"], "code": outcome.failed_code, "safe_log": outcome.safe_log},
            )

    def _fetch_one_detail(
        self, run_id: str, job: dict, *,
        run_candidate_id: str | None = None,
        list_fields: dict | None = None,
    ) -> bool:
        if self.source is None:
            return False
        job_id = str(job.get("job_id") or job.get("id") or "")
        source_url = normalize_job_link(job.get("source_url") or job.get("url") or "")
        if not job_id or not source_url:
            return False
        job = {**job, "source_url": source_url}

        # T081 SC-011: on resume, if this run already has a completed snapshot
        # for this job, skip source.fetch_detail entirely. find_reusable_snapshot
        # excludes the current run (exclude_run_id=run_id), so without this
        # check, resume would re-fetch details that are already persisted.
        try:
            existing = self.store.get_snapshot(run_id, job_id)
        except KeyError:
            existing = None
        except Exception:  # noqa: BLE001 - snapshot lookup must never crash
            existing = None
        if existing is not None and existing.get("fetch_status") == "completed":
            self.store.append_discovery_event(
                run_id, "detail_skipped_existing",
                {"job_id": job_id, "snapshot_id": existing.get("id")},
            )
            return True

        # T073: try to reuse a prior fresh snapshot before hitting the source.
        # This skips source.fetch_detail entirely when a valid prior capture
        # exists within the 12h freshness window with matching identity.
        if run_candidate_id and list_fields is not None:
            try:
                reusable = self.store.find_reusable_snapshot(
                    job_id=job_id,
                    source_url=source_url,
                    current_list_fields=list_fields,
                    exclude_run_id=run_id,
                )
            except Exception:  # noqa: BLE001 - reuse lookup must never crash the run
                reusable = None
            if reusable is not None:
                self.store.append_discovery_event(
                    run_id, "detail_reused",
                    {
                        "job_id": job_id,
                        "source_snapshot_id": reusable.get("id"),
                        "source_run_id": reusable.get("run_id"),
                    },
                )
                try:
                    self.store.create_reused_snapshot(
                        run_id=run_id,
                        run_candidate_id=run_candidate_id,
                        source_snapshot=reusable,
                        fetch_policy_version="discovery_v2",
                    )
                except Exception:  # noqa: BLE001 - reuse failure falls back to fetch
                    pass
                else:
                    self.store.append_discovery_event(
                        run_id, "snapshot_saved",
                        {"job_id": job_id, "completeness": reusable.get("completeness"),
                         "ok": True, "reused": True},
                    )
                    return True

        detail_output_path = str(self.result_dir / f"detail_{run_id}_{job_id}.json")
        self.store.append_discovery_event(
            run_id, "detail_fetch_started", {"job_id": job_id},
        )
        outcome = self.source.fetch_detail(job, detail_output_path=detail_output_path)
        snapshot_input = {
            "job_id": job_id,
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "salary": job.get("salary", ""),
            "location": job.get("location", "") or job.get("city", ""),
            "tags": job.get("tags", ""),
            "jd": job.get("jd", ""),
        }
        snapshot = build_snapshot(snapshot_input, outcome.detail if outcome.ok else {})
        snapshot["source_url"] = job.get("source_url") or job.get("url", "")
        snapshot["fetch_status"] = "completed" if outcome.ok else "failed"
        if not outcome.ok:
            # data-model contract: source_status is limited to
            # active/unknown/closed/unreachable.  A source-side detail failure
            # means this snapshot could not be reached, not a new status enum.
            snapshot["source_status"] = "unreachable"
            snapshot["completeness"] = "unavailable"
        # Persist canonical job if not already present.
        self._ensure_canonical_job(job, run_id)
        # Save snapshot.
        self.store.save_job_snapshot(
            run_id=run_id, job_id=job_id,
            source_url=snapshot.get("source_url", ""),
            title=snapshot.get("title", ""),
            company=snapshot.get("company", ""),
            salary=snapshot.get("salary", ""),
            location=snapshot.get("location", ""),
            tags=snapshot.get("tags", ""),
            jd=snapshot.get("jd", ""),
            company_json={},
            completeness=snapshot.get("completeness", "unavailable"),
            missing_fields=snapshot.get("missing_fields", []),
            source_status=snapshot.get("source_status", "unknown"),
            content_hash=snapshot.get("content_hash", ""),
            fetch_status=snapshot.get("fetch_status", "queued"),
        )
        self.store.append_discovery_event(
            run_id, "snapshot_saved",
            {"job_id": job_id, "completeness": snapshot.get("completeness"), "ok": outcome.ok},
        )
        return outcome.ok

    def _evaluate_one(
        self, run_id: str, snapshot: dict, direction: dict,
        analysis_evidence_ids: set, hard_constraints: dict,
    ) -> None:
        # T116: 构造完整脱敏评估输入（ai-contracts.md v1）
        run_dict = self.store.get_discovery_run(run_id)
        analysis = self.store.get_analysis(run_dict["analysis_id"])
        summary = analysis.get("summary", {}) or {}

        # Build candidate_summary (sanitized)
        candidate_summary = {
            "headline": summary.get("headline", ""),
            "experience_level": summary.get("experience_level", ""),
            "domains": summary.get("domains", []),
            "strengths": summary.get("strengths", []),
        }

        # Build direction view with evidence refs
        direction_evidence_rows = self.store.list_direction_evidence(direction["id"])
        direction_evidence_ids = {r["evidence_id"] for r in direction_evidence_rows}

        # Build evidence list (only evidence belonging to this direction)
        all_evidence = self.store.list_evidence(run_dict["analysis_id"])
        evidence_list = []
        for ev in all_evidence:
            if ev["id"] in direction_evidence_ids:
                evidence_list.append({
                    "id": ev["id"],
                    "type": ev.get("evidence_type", ""),
                    "normalized_value": ev.get("normalized_value", ""),
                    "safe_excerpt": ev.get("safe_excerpt", ""),
                    "assertion_type": ev.get("assertion_type", ""),
                })

        direction_view = {
            "id": direction["id"],
            "name": direction.get("name", ""),
            "type": direction.get("direction_type", ""),
            "rationale": direction.get("rationale", ""),
            "gaps": direction.get("gaps", []),
            "evidence": evidence_list,
            "evidence_refs": list(direction_evidence_ids),
            "analysis_evidence_ids": list(analysis_evidence_ids),
        }

        # Build snapshot view for assessment
        snapshot_view = {
            "job_id": snapshot["job_id"],
            "completeness": snapshot.get("completeness", "unavailable"),
            "fields": {
                "title": snapshot.get("title", ""),
                "company": snapshot.get("company", ""),
                "jd": snapshot.get("jd", ""),
                "salary": snapshot.get("salary", ""),
                "location": snapshot.get("location", ""),
                "tags": snapshot.get("tags", ""),
            },
        }

        # Get AI proposal if provider is available.
        ai_proposal = None
        failure_code = None
        if self.ai_provider is not None:
            try:
                ai_proposal = self.ai_provider.assess_job(
                    candidate_summary=candidate_summary,
                    direction=direction_view,
                    evidence=evidence_list,
                    job_snapshot=snapshot_view,
                )
            except TimeoutError:
                ai_proposal = None
                failure_code = "ai_timeout"
            except ConnectionError:
                ai_proposal = None
                failure_code = "ai_network_error"
            except AIProviderSecurityError as exc:
                ai_proposal = None
                provider_code = getattr(exc, "error_code", None)
                failure_code = (
                    provider_code if provider_code in ERROR_CODE_MAP
                    else "ai_invalid_output"
                )
            except Exception:  # noqa: BLE001 - provider adapter boundary
                ai_proposal = None
                failure_code = "ai_invalid_output"

        result = assess_job_direction(
            snapshot_view, direction_view, ai_proposal,
            hard_constraints=hard_constraints,
            candidate_profile=candidate_summary,
        )
        # T117: 持久化每岗位安全失败码，失败岗位 needs_review
        if not failure_code:
            assessment_failure = (result.get("ai_assessment") or {}).get("failure_stage")
            reason = result.get("reason")
            if assessment_failure in ERROR_CODE_MAP:
                failure_code = assessment_failure
            elif reason in ERROR_CODE_MAP:
                failure_code = reason
        if failure_code and not result.get("ai_assessment"):
            result["reason"] = failure_code
        # Persist assessment.
        assessment = result.get("ai_assessment") or {}
        dimensions = assessment.get("dimensions") or {}
        candidate_evidence_ids = sorted({
            ref
            for dimension in dimensions.values()
            for ref in (dimension.get("candidate_evidence_refs") or [])
        })
        job_evidence = {
            name: list(dimension.get("job_evidence_refs") or [])
            for name, dimension in dimensions.items()
            if dimension.get("job_evidence_refs")
        }
        self.store.create_assessment(
            run_id=run_id, snapshot_id=snapshot["id"], direction_id=direction["id"],
            hard_outcome=result.get("hard_rule_outcome", "unknown"),
            hard_checks=result.get("hard_rule_checks", {}),
            dimensions=dimensions,
            match_score=assessment.get("match_score"),
            confidence=assessment.get("confidence"),
            category=result.get("category", "needs_review"),
            candidate_evidence_ids=candidate_evidence_ids,
            job_evidence=job_evidence,
            gaps=result.get("gaps", []),
            policy_version=EVALUATION_POLICY_VERSION,
            failure_code=failure_code,
            status="completed",
        )

    def _evaluate_job_v2_group(
        self, run_id: str, snapshot: dict, selected_directions: list,
        hard_constraints: dict, analysis_evidence_ids: set,
    ) -> int:
        """Evaluate one job against ≤2 relevant directions in a single v2 group.

        One job-assessment v2 request covers the whole group; each direction is
        persisted as an independent assessment row sharing one
        ``evaluation_group_id`` but carrying its own ``input_hash``. A
        quarantined or missing direction (or a provider failure) degrades to
        ``needs_review`` without affecting a valid sibling. Returns the number
        of directions processed.
        """
        if not selected_directions:
            return 0

        run_dict = self.store.get_discovery_run(run_id)
        analysis = self.store.get_analysis(run_dict["analysis_id"])
        summary = analysis.get("summary", {}) or {}
        all_evidence = self.store.list_evidence(run_dict["analysis_id"])

        direction_payloads = []
        for direction in selected_directions:
            ev_rows = self.store.list_direction_evidence(direction["id"])
            ev_ids = [r["evidence_id"] for r in ev_rows]
            direction_payloads.append({
                "id": direction["id"],
                "name": direction.get("name", ""),
                "type": direction.get("direction_type", ""),
                "rationale": direction.get("rationale", ""),
                "gaps": direction.get("gaps", []),
                "fact_refs": [],
                "evidence_refs": ev_ids,
            })

        candidate_profile = {
            "profile_version_id": analysis.get("profile_version_id") or run_dict["analysis_id"],
            "summary": {
                "headline": summary.get("headline", ""),
                "experience_level": summary.get("experience_level", ""),
                "domains": summary.get("domains", []),
                "strengths": summary.get("strengths", []),
            },
            "facts": [],
            "evidence": [
                {
                    "client_ref": ev["id"],
                    "type": ev.get("evidence_type", ""),
                    "normalized_value": ev.get("normalized_value", ""),
                }
                for ev in all_evidence
            ],
        }
        job_snapshot = {
            "snapshot_id": snapshot["id"],
            "content_hash": snapshot.get("content_hash", "") or "",
            "fields": {
                "title": snapshot.get("title", ""),
                "company": snapshot.get("company", ""),
                "jd": snapshot.get("jd", ""),
                "salary": snapshot.get("salary", ""),
                "location": snapshot.get("location", ""),
                "tags": snapshot.get("tags", ""),
            },
        }

        evaluation_group_id = str(uuid.uuid4())
        envelope = None
        group_failure_code = None
        ai_call_count = None
        if self.ai_provider is not None:
            try:
                envelope = self.ai_provider.assess_job(
                    candidate_profile=candidate_profile,
                    directions=direction_payloads,
                    job_snapshot=job_snapshot,
                    contract_version="job_assessment_v2",
                )
                if isinstance(envelope, dict):
                    ai_call_count = (envelope.get("metrics") or {}).get("provider_call_count")
            except TimeoutError:
                group_failure_code = "ai_timeout"
            except ConnectionError:
                group_failure_code = "ai_network_error"
            except AIProviderSecurityError as exc:
                code = getattr(exc, "error_code", None)
                group_failure_code = code if code in ERROR_CODE_MAP else "ai_invalid_output"
            except Exception:  # noqa: BLE001 - provider adapter boundary
                group_failure_code = "ai_invalid_output"

        valid_by_dir: dict = {}
        quarantined_by_dir: dict = {}
        if isinstance(envelope, dict):
            for assessment in envelope.get("assessments") or []:
                if isinstance(assessment, dict) and assessment.get("direction_id"):
                    valid_by_dir[assessment["direction_id"]] = assessment
            for item in envelope.get("quarantined") or []:
                if isinstance(item, dict) and item.get("direction_id"):
                    quarantined_by_dir[item["direction_id"]] = item.get("reason") or "quarantined"

        processed = 0
        for direction in selected_directions:
            direction_id = direction["id"]
            input_hash = _source_input_hash({
                "snapshot_id": snapshot["id"],
                "content_hash": job_snapshot["content_hash"],
                "direction_id": direction_id,
            })
            common = dict(
                run_id=run_id, snapshot_id=snapshot["id"], direction_id=direction_id,
                policy_version="discovery_v2", contract_version="job_assessment_v2",
                evaluation_group_id=evaluation_group_id, input_hash=input_hash,
                ai_call_count=ai_call_count, status="completed",
            )
            if direction_id in valid_by_dir:
                assessment = valid_by_dir[direction_id]
                dimensions = assessment.get("dimensions") or {}
                candidate_evidence_ids = sorted({
                    ref
                    for dim in dimensions.values()
                    if isinstance(dim, dict)
                    for ref in (dim.get("candidate_evidence_refs") or [])
                })
                job_evidence = {
                    name: list(dim.get("job_evidence_refs") or [])
                    for name, dim in dimensions.items()
                    if isinstance(dim, dict) and dim.get("job_evidence_refs")
                }
                self.store.create_assessment(
                    hard_outcome="unknown", hard_checks={},
                    dimensions=dimensions,
                    match_score=assessment.get("match_score"),
                    confidence=assessment.get("confidence"),
                    category=self._v2_category_from_band(assessment.get("proposed_band")),
                    candidate_evidence_ids=candidate_evidence_ids,
                    job_evidence=job_evidence,
                    gaps=assessment.get("gaps") or [],
                    failure_code=None,
                    **common,
                )
            else:
                reason = quarantined_by_dir.get(direction_id)
                failure_code = (
                    f"quarantine:{reason}" if reason
                    else (group_failure_code or "ai_missing_direction")
                )
                self.store.create_assessment(
                    hard_outcome="unknown", hard_checks={},
                    dimensions={}, match_score=None, confidence=None,
                    category="needs_review",
                    candidate_evidence_ids=[], job_evidence={}, gaps=[],
                    failure_code=failure_code,
                    **common,
                )
            processed += 1
        return processed

    @staticmethod
    def _v2_category_from_band(proposed_band) -> str:
        """Map the advisory ``proposed_band`` to a program category (T058).

        Full hard-rule / soft-preference classification guards land in
        T059–T062; here ``uncertain`` or unknown bands degrade to needs_review.
        """
        if proposed_band in ("high", "adjacent", "growth", "unsuitable"):
            return proposed_band
        return "needs_review"

    # ------------------------------------------------------------------
    # Cancel handling
    # ------------------------------------------------------------------

    def _handle_cancel(self, run_id: str) -> None:
        # Mark pending plan items as cancelled. Plan may not exist yet if
        # cancel arrives before _stage_planning compiled it (e.g. cancel
        # during planning stage or resume of a run that only ran
        # run_progressive_detail_eval directly).
        try:
            plan = self.store.get_search_plan(run_id)
        except KeyError:
            plan = {"items": []}
        for item in plan["items"]:
            if item["status"] not in ("completed", "failed", "cancelled", "skipped"):
                self.store.update_plan_item(item["id"], status="cancelled", completed=True)
        self.store.update_discovery_run(run_id, status=STATUS_CANCELLED, completed=True)
        self.store.append_discovery_event(run_id, "run_cancelled", {})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _register_run(self, run_id: str) -> threading.Event:
        with self._lock:
            event = self._cancel_events.get(run_id)
            if event is None:
                event = threading.Event()
                self._cancel_events[run_id] = event
            return event

    def _unregister_run(self, run_id: str) -> None:
        with self._lock:
            self._cancel_events.pop(run_id, None)

    def _load_confirmation_view(self, run: dict) -> dict:
        return build_confirmation_view(self.store, run)

    def _materialize_plan_items(self, compiled: dict, run_id: str) -> list[dict]:
        """Convert compile_search_plan output to store.create_search_plan items."""
        items = []
        for raw_item in compiled["items"]:
            city = (compiled.get("hard_constraints") or {}).get("city", "")
            source_filters = {
                k: v for k, v in (compiled.get("hard_constraints") or {}).items()
                if k in SCRAPER_FILTER_FIELDS
            }
            target_pages = int((compiled.get("safe_limits") or {}).get("max_pages", 1))
            # input_hash must match what the source adapter computes (same fields).
            input_hash = _source_input_hash({
                "keyword": raw_item["term"],
                "city": city,
                "source_filters": source_filters,
                "target_pages": target_pages,
            })
            payload = {
                "keyword": raw_item["term"],
                "city": city,
                "source_filters": source_filters,
                "direction_ids": raw_item["direction_ids"],
                "input_hash": input_hash,
                "target_pages": target_pages,
                "detail_budget": int(compiled["detail_budget"] // max(1, len(compiled["items"]))),
            }
            items.append(payload)
        return items

    def _refresh_item(self, item: dict) -> dict:
        # Re-read from store to get updated status.
        try:
            return self.store.get_search_plan_item(item["id"])
        except KeyError:
            return item

    def _read_list_jobs(self, item: dict) -> list[dict]:
        """Return the jobs fetched for this plan item.

        Prefers the in-memory cache (populated by _fetch_one_list). Falls
        back to the list output file on disk for resume scenarios where the
        orchestrator was restarted.
        """
        cached = self._list_jobs_cache.get(item["id"])
        if cached is not None:
            return cached
        # On resume, the cache is empty; try the list output file on disk.
        list_output_path = str(self.result_dir / f"list_{item.get('run_id', '')}_{item['id']}.json")
        if list_output_path and Path(list_output_path).is_file():
            try:
                import json
                with Path(list_output_path).open(encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
                    self._list_jobs_cache[item["id"]] = payload["jobs"]
                    return payload["jobs"]
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _persist_jobs(self, jobs: list[dict], run_id: str, item: dict) -> None:
        """Persist canonical jobs. Does not link to plan_item_id (no such column)."""
        import time
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.store._connection() as conn:
            for job in jobs:
                job_id = str(job.get("job_id") or job.get("id") or uuid.uuid4().hex[:12])
                source_url = normalize_job_link(job.get("source_url") or job.get("url") or "")
                if not source_url:
                    continue
                canonical_url = source_url
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, canonical_url, source_url,
                     str(job.get("title", "")),
                     str(job.get("company", "")),
                     str(job.get("salary", "")),
                     str(job.get("location", "") or job.get("city", "")),
                     str(job.get("jd", "")),
                     ts, ts),
                )

    def _ensure_canonical_job(self, job: dict, run_id: str) -> None:
        """Ensure the canonical jobs row exists before saving snapshot."""
        import time
        job_id = str(job.get("job_id") or job.get("id") or "")
        if not job_id:
            return
        source_url = normalize_job_link(job.get("source_url") or job.get("url") or "")
        if not source_url:
            return
        canonical_url = source_url
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.store._connection() as conn:
            existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if existing:
                return
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, canonical_url, source_url,
                 str(job.get("title", "")),
                 str(job.get("company", "")),
                 str(job.get("salary", "")),
                 str(job.get("location", "") or job.get("city", "")),
                 str(job.get("jd", "")),
                 ts, ts),
            )

    def _list_snapshots(self, run_id: str) -> list[dict]:
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM discovery_job_snapshots WHERE run_id = ? ORDER BY fetched_at ASC",
                (str(run_id),),
            ).fetchall()
        return [dict(r) for r in rows]

    def _get_assessment(self, run_id: str, snapshot_id: str, direction_id: str) -> dict | None:
        try:
            return self.store.get_assessment(run_id, snapshot_id, direction_id)
        except KeyError:
            return None

    def _bump_counts(self, counts: dict, category: str) -> None:
        mapping = {
            "high_match": "high_count",
            "adjacent_match": "adjacent_count",
            "growth_match": "growth_count",
            "needs_review": "review_count",
            "not_suitable": "unsuitable_count",
        }
        key = mapping.get(category, "review_count")
        counts[key] = counts.get(key, 0) + 1


# ---------------------------------------------------------------------------
# Restart safety: mark interrupted runs
# ---------------------------------------------------------------------------


def mark_interrupted_on_restart(store) -> int:
    """Mark any discovery run in an active stage as interrupted.

    Called once at app startup. Returns the count of runs marked.
    Idempotent: runs already terminal or interrupted are not touched.
    """
    with store._connection() as conn:
        rows = conn.execute(
            "SELECT id FROM discovery_runs WHERE status IN (?, ?, ?, ?, ?, ?)",
            tuple(ACTIVE_STATUSES),
        ).fetchall()
    count = 0
    for row in rows:
        run_id = row["id"]
        try:
            store.update_discovery_run(run_id, status=STATUS_INTERRUPTED)
            store.append_discovery_event(run_id, "marked_interrupted_on_restart", {})
            count += 1
        except (KeyError, ValueError):
            continue
    return count


def reconcile_analysis_on_restart(store) -> int:
    """Safely close analyses interrupted in an in-flight v3 lifecycle stage."""
    stages = ("requesting", "normalizing", "validating", "repairing", "persisting")
    try:
        with store._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM candidate_analyses WHERE status NOT IN ('ready','failed','deleted') AND analysis_stage IN (?,?,?,?,?)",
                stages,
            ).fetchall()
    except (AttributeError, sqlite3.Error):
        return 0
    count = 0
    for row in rows:
        try:
            store.update_analysis_status(
                row["id"], "failed", analysis_stage="interrupted",
                failure_code="analysis_interrupted", quality_status="manual_required",
                quality_warnings=[], expected_stages=stages,
            )
            count += 1
        except (KeyError, ValueError, TypeError):
            continue
    return count


# ---------------------------------------------------------------------------
# T100: DiscoveryTaskRuntime — 应用持有的运行时基础设施
# ---------------------------------------------------------------------------


class DiscoveryTaskRuntime:
    """应用持有的唯一发现任务运行时。

    受控 executor、run future 管理、取消信号、dispatch failure、
    安全事件、shutdown。SQLite 状态为恢复事实来源。

    构造时调用 mark_interrupted_on_restart 收敛 active runs 为 interrupted。
    HTTP 创建 run 后必须通过 submit_run 真实提交，不得只写数据库返回 202。
    程序负责最终状态推进；AI 只能提供生成/提取/评估建议。
    """

    def __init__(self, store, *, source=None, ai_provider=None,
                 source_factory=None, ai_provider_factory=None,
                 result_dir=None, max_workers: int = 1):
        self.store = store
        self._source = source
        self._ai_provider = ai_provider
        self._source_factory = source_factory
        self._ai_provider_factory = ai_provider_factory
        self._result_dir = result_dir
        self._max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="discovery-runtime",
        )
        self._futures: dict[str, Any] = {}
        self._runners: dict[str, DiscoveryRunner] = {}
        self._lock = threading.Lock()
        # 构造时收敛 active runs 为 interrupted（进程重启恢复）
        try:
            mark_interrupted_on_restart(self.store)
            reconcile_analysis_on_restart(self.store)
        except Exception:
            pass

    def _resolve_source(self):
        """Resolve the current JobSource, preferring a factory if provided.

        Factories let the runtime pick up the latest CDP/source state at
        each submit_run, instead of being pinned to the source that existed
        when create_app constructed the runtime.
        """
        if self._source_factory is not None:
            try:
                return self._source_factory()
            except Exception:
                return None
        return self._source

    def _resolve_ai_provider(self):
        """Resolve the current AI provider, preferring a factory if provided.

        Factories let the runtime pick up the latest AI settings at each
        submit_run, so a user reconfiguring their API key mid-session does
        not require an app restart.
        """
        if self._ai_provider_factory is not None:
            try:
                return self._ai_provider_factory()
            except Exception:
                return None
        return self._ai_provider

    def _make_runner(self) -> DiscoveryRunner:
        """Create a DiscoveryRunner bound to this runtime's store/source/provider."""
        return DiscoveryRunner(
            self.store,
            source=self._resolve_source(),
            ai_provider=self._resolve_ai_provider(),
            result_dir=self._result_dir,
            max_workers=self._max_workers,
        )

    def submit_run(self, run_id: str) -> None:
        """Submit a run for background execution.

        Persists dispatch acceptance, then submits to the executor.
        If the runner fails, persists a safe dispatch_failed event.
        Does not return 202 without submitting executable work.
        """
        with self._lock:
            existing = self._futures.get(run_id)
            if existing is not None and not existing.done():
                return  # Already running
        runner = self._make_runner()
        future = self._executor.submit(self._safe_execute, runner, run_id)
        with self._lock:
            self._futures[run_id] = future
            self._runners[run_id] = runner

    def _safe_execute(self, runner: DiscoveryRunner, run_id: str) -> None:
        """Execute run in background, persisting dispatch failures safely."""
        try:
            runner.run(run_id)
        except DiscoveryError as exc:
            import traceback
            tb = traceback.format_exc()
            try:
                self.store.append_discovery_event(
                    run_id, "dispatch_failed",
                    {"error_code": exc.error_code,
                     "log_detail": (exc.log_detail or "")[:500],
                     "traceback": tb[:1000]},
                )
            except Exception:
                pass
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            try:
                self.store.append_discovery_event(
                    run_id, "dispatch_failed",
                    {"error_code": "verification_error",
                     "log_detail": str(exc)[:500],
                     "traceback": tb[:1000]},
                )
            except Exception:
                pass
        finally:
            with self._lock:
                self._runners.pop(run_id, None)

    def cancel_run(self, run_id: str) -> dict:
        """Request cancellation of a running run.

        Persists cancel_requested_at first; the worker checks before
        each unit of work. Saved completed work remains.
        """
        with self._lock:
            runner = self._runners.get(run_id)
        if runner is None:
            runner = self._make_runner()
        return runner.request_cancel(run_id)

    def resume_run(self, run_id: str) -> None:
        """Resume an interrupted or partial run by re-submitting it.

        Directly changing the persisted status without submitting work
        is forbidden — resume must re-submit unfinished work for execution.
        """
        self.store.append_discovery_event(
            run_id, "resume_accepted", {"run_id": run_id},
        )
        self.submit_run(run_id)

    def retry_job(self, run_id: str, job_id: str) -> dict:
        """Retry a single failed job within a run.

        Resets the job's snapshot fetch_status and re-submits the run
        so the runner re-fetches and re-evaluates this job.
        """
        try:
            run = self.store.get_discovery_run(run_id)
        except KeyError:
            raise DiscoveryError("not_found", user_message="运行不存在。")
        if run["status"] in TERMINAL_STATUSES:
            raise DiscoveryError(
                "state_conflict",
                user_message=f"运行已终态 ({run['status']})，无法重试。",
            )
        # Reset the snapshot's fetch_status so the runner re-fetches it
        try:
            self.store.reset_job_snapshot(run_id, job_id)
        except Exception:
            pass
        self.store.append_discovery_event(
            run_id, "job_retry_requested", {"job_id": job_id},
        )
        # Re-submit the run to pick up the reset snapshot
        self.submit_run(run_id)
        return self.store.get_discovery_run(run_id)

    # T109: Analysis submission (US1 async candidate analysis)
    def submit_analysis(self, analysis_id: str, *, ai_consent: bool) -> None:
        """Submit an analysis for background execution.

        - ``ai_consent`` must already be explicitly true at the route boundary.
        - ``ai_consent`` True: resolve the current AI provider via factory
          and submit ``_safe_execute_analysis`` to the executor. The
          worker calls :func:`analyze_resume` with the existing
          ``analysis_id`` so the analysis transitions
          ``queued -> analyzing -> ready/failed`` asynchronously.

        Provider failures (timeout / auth / network / invalid_output)
        are persisted by ``analyze_resume`` itself as safe failure codes
        on the analysis row; the worker swallows the raised exception so
        it never leaks to the executor's error log.
        """
        if not ai_consent:
            raise ValueError("ai_consent must be true")
        with self._lock:
            key = f"analysis:{analysis_id}"
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return  # Already running
        future = self._executor.submit(self._safe_execute_analysis, analysis_id)
        with self._lock:
            self._futures[key] = future

    def _safe_execute_analysis(self, analysis_id: str) -> None:
        """Execute analysis in background, mapping failures to safe codes.

        ``analyze_resume`` persists the failure status (``failed`` +
        ``failure_code``) before raising, so we only need to swallow the
        exception. Unexpected exceptions are coerced to ``ai_invalid_output``
        to avoid leaking raw tracebacks via the executor.
        """
        try:
            analysis = self.store.get_analysis(analysis_id)
        except KeyError:
            return  # Analysis gone; nothing to do
        provider = self._resolve_ai_provider()
        try:
            analyze_resume(
                self.store,
                analysis["resume_id"],
                ai_consent=True,
                ai_provider=provider,
                analysis_id=analysis_id,
            )
        except (DiscoveryError, AISecurityError):
            # Failure already persisted by analyze_resume (failure_code set).
            pass
        except Exception:  # noqa: BLE001 - runtime boundary
            try:
                self.store.update_analysis_status(
                    analysis_id, "failed", failure_code="ai_invalid_output",
                )
            except Exception:
                pass

    def shutdown(self) -> None:
        """Shut down the executor, waiting for active tasks to complete."""
        self._executor.shutdown(wait=True)


__all__ = [
    "DiscoveryRunner",
    "DiscoveryTaskRuntime",
    "mark_interrupted_on_restart",
    "reconcile_analysis_on_restart",
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "RESUMABLE_STATUSES",
    "STAGE_CREATED",
    "STAGE_PLANNING",
    "STAGE_FETCHING_LISTS",
    "STAGE_FETCHING_DETAILS",
    "STAGE_EVALUATING",
    "STAGE_ASSEMBLING",
    "STATUS_CREATED",
    "STATUS_PLANNING",
    "STATUS_FETCHING_LISTS",
    "STATUS_FETCHING_DETAILS",
    "STATUS_EVALUATING",
    "STATUS_ASSEMBLING",
    "STATUS_SUCCEEDED",
    "STATUS_PARTIAL",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_CANCELLED",
]


# ---------------------------------------------------------------------------
# T083: Module-level helpers shared by runner and HTTP resume endpoint
# ---------------------------------------------------------------------------


def build_confirmation_view(store, run: dict) -> dict:
    """T083: 模块级 helper，供 runner 和 HTTP resume 端点共享。

    构建 compile_search_plan 所需的 confirmation view：
    - 只包含 confirmation 中 enabled=True 的 directions（T133）
    - 每个 direction 附带 evidence_refs
    - hard_constraints / soft_preferences / safe_limits 来自 confirmation
    """
    confirmation = store.get_confirmation(run["confirmation_id"])
    directions = store.list_directions(run["analysis_id"])
    confirmed_ids = {
        cd["direction_id"] for cd in confirmation.get("directions", [])
        if cd.get("enabled")
    }
    enabled_directions = []
    for d in directions:
        if d["id"] not in confirmed_ids:
            continue
        direction_evidence = store.list_direction_evidence(d["id"])
        evidence_refs = [r["evidence_id"] for r in direction_evidence]
        enabled_directions.append({
            "id": d["id"],
            "direction_id": d["id"],
            "name": d.get("name", ""),
            "type": d.get("direction_type", ""),
            "search_terms": d.get("search_terms", []),
            "default_enabled": d.get("default_enabled", False),
            "evidence_refs": evidence_refs,
        })
    return {
        "id": confirmation["id"],
        "hard_constraints": confirmation.get("hard_constraints", {}),
        "soft_preferences": confirmation.get("soft_preferences", {}),
        "safe_limits": confirmation.get("safe_limits", {}),
        "enabled_directions": enabled_directions,
    }


def check_v2_resume_hash_drift(store, run: dict) -> None:
    """T083: 同步校验 v2 run resume 时的 hash drift。

    - policy_version 必须是合法值（v1 / discovery_v1 / discovery_v2）
    - 对 discovery_v2 run：重算 compile_search_plan input_hash 并与 stored 比对

    校验失败时抛 DiscoveryError("state_conflict")，HTTP 层映射为 409。
    v1 run 保留 legacy 行为（不做 hash drift check），保证 004 历史 run 可 resume。
    """
    policy_version = run.get("policy_version") or ""
    if policy_version not in ("v1", "discovery_v1", "discovery_v2"):
        raise DiscoveryError(
            "state_conflict",
            user_message="policy_version 漂移，无法恢复运行。",
        )
    if policy_version == "discovery_v2":
        stored_hash = run.get("input_hash") or ""
        try:
            confirmation_view = build_confirmation_view(store, run)
            plan = compile_search_plan(confirmation_view)
            recomputed_hash = plan.get("input_hash", "")
        except DiscoveryError:
            raise
        except Exception:
            recomputed_hash = None
        if not recomputed_hash or recomputed_hash != stored_hash:
            raise DiscoveryError(
                "state_conflict",
                user_message="input_hash 漂移，无法恢复运行。",
            )
