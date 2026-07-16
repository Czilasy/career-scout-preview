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
from webui.source import BossCdpSource, SourceOutcome, _input_hash as _source_input_hash
from webui.workbench import normalize_job_link


# ---------------------------------------------------------------------------
# Stage constants (mirror state-machine.md)
# ---------------------------------------------------------------------------

STAGE_CREATED = "created"
STAGE_PLANNING = "planning"
STAGE_FETCHING_LISTS = "fetching_lists"
STAGE_FETCHING_DETAILS = "fetching_details"
STAGE_EVALUATING = "evaluating"
STAGE_ASSEMBLING = "assembling"

STATUS_CREATED = "created"
STATUS_PLANNING = "planning"
STATUS_FETCHING_LISTS = "fetching_lists"
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
        # Resume: if interrupted/partial, just continue.
        if run["status"] not in (STATUS_CREATED,) and run["status"] not in ACTIVE_STATUSES:
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
        self._stage_fetching_lists(run_id, cancel_event)
        if cancel_event.is_set() or self.is_cancelled(run_id):
            return self._handle_cancel(run_id)

        # Stage 3: fetching details
        self._stage_fetching_details(run_id, cancel_event)
        if cancel_event.is_set() or self.is_cancelled(run_id):
            return self._handle_cancel(run_id)

        # Stage 4: evaluating
        self._stage_evaluating(run_id, cancel_event)
        if cancel_event.is_set() or self.is_cancelled(run_id):
            return self._handle_cancel(run_id)

        # Stage 5: assembling + completion
        self._stage_assembling(run_id, cancel_event)

    def _stage_planning(self, run_id: str, cancel_event: threading.Event) -> None:
        run = self.store.get_discovery_run(run_id)
        if run["stage"] in (STAGE_FETCHING_LISTS, STAGE_FETCHING_DETAILS, STAGE_EVALUATING, STAGE_ASSEMBLING):
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

    def _stage_fetching_lists(self, run_id: str, cancel_event: threading.Event) -> None:
        run = self.store.get_discovery_run(run_id)
        if run["stage"] in (STAGE_FETCHING_DETAILS, STAGE_EVALUATING, STAGE_ASSEMBLING):
            return
        self.store.update_discovery_run(run_id, status=STATUS_FETCHING_LISTS, stage=STAGE_FETCHING_LISTS)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_FETCHING_LISTS})

        plan = self.store.get_search_plan(run_id)
        source_count = 0
        for item in plan["items"]:
            if cancel_event.is_set() or self.is_cancelled(run_id):
                return
            if item["status"] in ("completed", "failed", "cancelled", "skipped"):
                continue
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

    def _stage_fetching_details(self, run_id: str, cancel_event: threading.Event) -> None:
        run = self.store.get_discovery_run(run_id)
        if run["stage"] in (STAGE_EVALUATING, STAGE_ASSEMBLING):
            return
        self.store.update_discovery_run(run_id, status=STATUS_FETCHING_DETAILS, stage=STAGE_FETCHING_DETAILS)
        self.store.append_discovery_event(run_id, "stage_entered", {"stage": STAGE_FETCHING_DETAILS})

        plan = self.store.get_search_plan(run_id)
        detail_budget = int(plan.get("detail_budget", 60))
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

    def _fetch_one_detail(self, run_id: str, job: dict) -> bool:
        if self.source is None:
            return False
        job_id = str(job.get("job_id") or job.get("id") or "")
        source_url = normalize_job_link(job.get("source_url") or job.get("url") or "")
        if not job_id or not source_url:
            return False
        job = {**job, "source_url": source_url}
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

    # ------------------------------------------------------------------
    # Cancel handling
    # ------------------------------------------------------------------

    def _handle_cancel(self, run_id: str) -> None:
        # Mark pending plan items as cancelled.
        plan = self.store.get_search_plan(run_id)
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
        confirmation = self.store.get_confirmation(run["confirmation_id"])
        directions = self.store.list_directions(run["analysis_id"])
        # T133: 只加载 confirmation 中 enabled=True 的 directions。
        # 旧实现加载所有 directions，当 analysis 生成的非 confirmed directions
        # 没有 search_terms 时，compile_search_plan 会抛 input_incomplete
        # （要求每个 direction 都有至少一个 search item）。
        confirmed_ids = {
            cd["direction_id"] for cd in confirmation.get("directions", [])
            if cd.get("enabled")
        }
        enabled_directions = []
        for d in directions:
            if d["id"] not in confirmed_ids:
                continue
            direction_evidence = self.store.list_direction_evidence(d["id"])
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
