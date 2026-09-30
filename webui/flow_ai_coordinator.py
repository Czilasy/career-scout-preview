"""B096 Flow AI coordinator for automatic post-scrape screening."""
from __future__ import annotations

import hashlib
import json
import uuid

from webui.flow_service import submit_platform_task
from webui.flow_future import attach_ai_future_failure
from webui.logging_setup import get_logger

_logger = get_logger(__name__)


class FlowAiCoordinator:
    """Durable AI run creation, Flow claim, whitebox and lane submission."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.store = ctx.store
        self.flow_service = getattr(ctx, "flow_service", None)

    def get_screening_run(self, run_id):
        """Read a screening run for Flow-aware route decisions."""
        return self.store.get_screening_run(run_id)

    def resolve_source_identity(
        self,
        scrape_task_id,
        *,
        fallback_account,
        accounts_path,
        operational_errors,
        source_platform,
    ):
        from webui.resume_identity import resolve_child_frozen_identity
        from webui.flow_task_coordinator import resolve_flow_binding

        binding = resolve_flow_binding(self.ctx, scrape_task_id)
        if binding is not None:
            source_platform = binding["platform"]

        return resolve_child_frozen_identity(
            self.store,
            scrape_task_id,
            fallback_account=fallback_account,
            accounts_path=accounts_path,
            operational_errors=operational_errors,
            source_platform=source_platform,
        )

    def persist_resume_identity(self, run_id, identity):
        from webui.resume_identity import persist_frozen_identity

        return persist_frozen_identity(self.store, run_id, identity)

    def claim_paused_resume(self, run_id):
        return self.store.claim_paused_screening_run(run_id)

    def restore_paused_resume(self, run_id):
        return self.store.update_screening_run(run_id, status="paused")

    def inherit_frozen_account(
        self,
        run_id,
        previous_run,
        *,
        platform,
        fallback_account,
        accounts_path,
        role="R2",
    ):
        from webui.resume_identity import ensure_frozen_browser_account

        return ensure_frozen_browser_account(
            self.store,
            run_id,
            previous_run,
            platform=platform,
            fallback_account=fallback_account,
            accounts_path=accounts_path,
            role=role,
        )

    def mark_resume_handoff(self, run_id, task_id):
        self.store.update_screening_run(
            run_id,
            error_code="resumed",
            error_reason="已由新任务接管续跑",
        )
        return self.store.append_task_event(run_id, "resume", {"task_id": task_id})

    def create_screening_run(
        self,
        *,
        task_id,
        screening_fields,
        source_count,
        profile_id,
        platform,
        filter_schema_version,
        profile_summary,
        profile_facts,
        scrape_task_id,
        flow_id,
        track_id,
        browser_account,
        cdp_port,
        profile_key,
        task_input_digest,
        cross_platform_dedupe,
    ):
        self.store.create_screening_run(
            task_id,
            frozen_filters=screening_fields,
            source_count=source_count,
            profile_id=profile_id,
            execution_params={
                "platform": platform,
                "filter_schema_version": filter_schema_version,
                "screening_fields": screening_fields,
                "profile_summary": profile_summary,
                "profile_facts": profile_facts,
                "scrape_task_id": scrape_task_id,
                "flow_id": flow_id,
                "track_id": track_id,
                "browser_account": browser_account,
                "cdp_port": cdp_port,
                "profile_key": profile_key,
                "task_input_digest": task_input_digest,
                "cross_platform_dedupe": cross_platform_dedupe,
            },
            backend_version=self.ctx.backend_version,
        )
        self.store.save_filter_snapshot(
            task_id,
            platform=platform,
            filter_schema_version=filter_schema_version,
            filter_snapshot=screening_fields,
            task_input_digest=task_input_digest,
        )

    def claim_track(self, *, flow_id, platform, profile_id, screen_run_id):
        return self.flow_service.begin_ai(
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            screen_run_id=screen_run_id,
        )

    def fail_track(
        self,
        *,
        flow_id,
        platform,
        profile_id,
        task_id,
        scrape_task_id,
        error_code,
        reason="",
        status="failed",
    ):
        if not flow_id:
            return None
        from webui.flow_task_state import FlowStateClosureError, close_flow_task_state
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            MissingPlatformIdentityError,
            resolve_flow_binding,
        )

        binding = resolve_flow_binding(self.ctx, scrape_task_id or task_id)
        if binding is not None:
            if (
                binding["flow_id"] != str(flow_id)
                or binding["profile_id"] != str(profile_id)
                or (
                    platform
                    and str(platform).strip().lower() != binding["platform"]
                )
            ):
                raise FlowTaskOperationError("Flow AI failure binding mismatch")
            platform = binding["platform"]
            profile_id = binding["profile_id"]
        elif not str(platform or "").strip() or not str(profile_id or "").strip():
            raise MissingPlatformIdentityError(
                "Flow AI failure has incomplete durable identity"
            )

        try:
            return close_flow_task_state(
                self.ctx,
                task_id=task_id,
                scrape_task_id=scrape_task_id,
                flow_id=flow_id,
                profile_id=profile_id,
                status=status,
                error_code=error_code,
                reason=reason,
                platform=platform,
                stage="ai",
            )
        except Exception as exc:  # noqa: BLE001 - preserve safe observable failure
            raise FlowStateClosureError() from exc

    def begin_whitebox(self, *, task_id, scrape_task_id):
        from webui.whitebox import WhiteboxService

        return WhiteboxService(self.store).begin(
            "screening",
            task_id,
            {
                "stages": ["ai_rough", "jd_detail", "ai_fine"],
                "units": [
                    {"unit_key": key, "unit_kind": "ai_stage", "stage": stage, "required": True}
                    for key, stage in (
                        ("ai_rough", "ai_rough"),
                        ("jd_detail", "jd_detail"),
                        ("ai_fine", "ai_fine"),
                    )
                ],
            },
            parent_owner_id=scrape_task_id,
        )

    def submit_flow(self, *, flow_id, platform, task_id, screening_fields,
                    profile_summary, scrape_task_id, resume_from_run_id,
                    profile_facts, cross_platform_dedupe):
        future = submit_platform_task(
            self.ctx,
            flow_id,
            platform,
            self.ctx.run_ai_screen_task,
            task_id,
            screening_fields,
            profile_summary,
            scrape_task_id,
            resume_from_run_id,
            profile_facts,
            cross_platform_dedupe=cross_platform_dedupe,
        )
        attach_ai_future_failure(
            future,
            self.ctx,
            task_id=task_id,
            scrape_task_id=scrape_task_id,
            platform=platform,
        )
        return future

    def mark_submission_failed(
        self,
        *,
        task_id,
        reason,
        error_code="submit_failed",
        flow_id=None,
        scrape_task_id=None,
        platform=None,
        profile_id=None,
        status="failed",
    ):
        from webui.store_helpers import _now
        from webui.whitebox import WhiteboxService

        current = self.store.get_screening_run(task_id) or {}
        params = current.get("execution_params") or {}
        flow_id = str(flow_id or params.get("flow_id") or "").strip() or None
        scrape_task_id = str(
            scrape_task_id or params.get("scrape_task_id") or ""
        ).strip() or None
        if flow_id:
            from webui.flow_task_coordinator import (
                FlowTaskOperationError,
                MissingPlatformIdentityError,
                resolve_flow_binding,
            )

            binding = resolve_flow_binding(self.ctx, scrape_task_id or task_id)
            if binding is None:
                raise MissingPlatformIdentityError(
                    "Flow submission failure has no durable platform identity"
                )
            if binding["flow_id"] != flow_id:
                raise FlowTaskOperationError("Flow submission failure binding mismatch")
            platform = binding["platform"]
            profile_id = binding["profile_id"]
        else:
            platform = str(
                platform or current.get("platform") or params.get("platform") or "boss"
            ).strip().lower()
        profile_id = profile_id or current.get("profile_id") or params.get("profile_id")

        if flow_id and scrape_task_id and profile_id:
            from webui.flow_task_state import FlowStateClosureError, close_flow_task_state

            try:
                close_flow_task_state(
                    self.ctx,
                    task_id=task_id,
                    scrape_task_id=scrape_task_id,
                    flow_id=flow_id,
                    profile_id=profile_id,
                    status=status,
                    error_code=error_code,
                    reason=reason,
                    platform=platform,
                    stage="ai",
                )
            except Exception as exc:  # noqa: BLE001 - preserve safe observable failure
                raise FlowStateClosureError() from exc
        else:
            self.store.update_screening_run(
                task_id,
                status="failed",
                error_code=error_code,
                error_reason=reason,
            )
            self.store.append_task_event(task_id, "submission_failed", {
                "error_code": error_code, "error_reason": reason,
            })
        whitebox = WhiteboxService(self.store)
        row = self.store.get_whitebox_run("screening", task_id)
        if row is None:
            whitebox.begin(
                "screening",
                task_id,
                {
                    "stages": ["ai_rough", "jd_detail", "ai_fine"],
                    "units": [
                        {"unit_key": key, "unit_kind": "ai_stage", "stage": stage, "required": True}
                        for key, stage in (
                            ("ai_rough", "ai_rough"),
                            ("jd_detail", "jd_detail"),
                            ("ai_fine", "ai_fine"),
                        )
                    ],
                },
                parent_owner_id=(
                    self.store.get_screening_run(task_id) or {}
                ).get("execution_params", {}).get("scrape_task_id"),
            )
            row = self.store.get_whitebox_run("screening", task_id)
        for unit in (self.store.list_whitebox_units(row["id"]) if row else []):
            key = str(unit.get("unit_key") or "")
            if not key:
                continue
            attempt = int(unit.get("attempt_no") or 1)
            if str(unit.get("status") or "planned") != "planned":
                attempt += 1
            whitebox.record(row["id"], {
                "idempotency_key": f"submission-failed:{task_id}:{key}:{attempt}",
                "event_type": "submission_failed",
                "occurred_at": _now(),
                "stage": "submit",
                "unit_kind": unit.get("unit_kind"),
                "unit_key": key,
                "attempt_no": attempt,
                "required_evidence": True,
                "severity": "error",
                "payload": {"error_code": error_code, "error_reason": reason},
            })
        if row:
            whitebox.finalize(row["id"], lifecycle_end="failed")

def enqueue_auto_screen_for_scrape(app, ctx, scrape_task_id: str):
    """Start the AI child of a completed Flow Track from the worker path.

    The HTTP route remains the manual/legacy entry point.  Flow auto-screening
    uses this same production task registry, durable screening run, Flow
    binding, whitebox, and per-platform executor so one Track can finish and
    become usable while its sibling is still scraping.
    """
    scrape_task_id = str(scrape_task_id or "").strip()
    if not scrape_task_id:
        return None
    source_run = ctx.store.get_screening_run(scrape_task_id) or {}
    params = source_run.get("execution_params") or {}
    flow_id = str(params.get("flow_id") or "").strip()
    flow_service = getattr(ctx, "flow_service", None)
    if not flow_id or flow_service is None:
        return None
    from webui.flow_task_coordinator import resolve_flow_binding

    binding = resolve_flow_binding(ctx, scrape_task_id)
    if binding is None:
        # The source carries a Flow id, so treating it as an unbound legacy
        # task would be an unsafe platform redirect.
        raise RuntimeError("Flow scrape source has no durable Track binding")
    flow_id = binding["flow_id"]
    platform = binding["platform"]
    profile_id = binding["profile_id"]
    coordinator = FlowAiCoordinator(ctx)
    source = ctx.ensure_scrape_source(scrape_task_id)
    if not isinstance(source, dict):
        coordinator.fail_track(
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            task_id=scrape_task_id,
            scrape_task_id=scrape_task_id,
            error_code="scrape_failed",
            reason="抓取任务结果不可用",
        )
        return None
    source_result = source.get("result") or {}
    if source.get("status") != "done" or not isinstance(source_result, dict):
        coordinator.fail_track(
            flow_id=flow_id, platform=platform,
            profile_id=profile_id,
            task_id=scrape_task_id,
            scrape_task_id=scrape_task_id,
            error_code="flow_ai_start_failed", reason="抓取结果尚未准备好",
        )
        return None
    screening_fields = params.get("auto_screen_fields") or {}
    if not isinstance(screening_fields, dict):
        screening_fields = {}
    profile_summary = str(params.get("profile_summary") or params.get("auto_screen_profile") or "")
    profile_facts = params.get("profile_facts") or params.get("auto_screen_facts")
    if not isinstance(profile_facts, dict):
        profile_facts = None
    cross_platform_dedupe = bool(params.get("cross_platform_dedupe", True))

    with ctx.lock:
        for existing_id, existing in ctx.tasks.items():
            if (
                existing.get("kind") == "ai_screen"
                and existing.get("source_task_id") == scrape_task_id
                and existing.get("status") in ("queued", "running")
            ):
                return existing_id
    task_id = uuid.uuid4().hex
    try:
        task = ctx.register_pipeline_task(task_id, "ai_screen", source_task_id=scrape_task_id)
    except Exception as exc:
        coordinator.fail_track(
            flow_id=flow_id, platform=platform, profile_id=profile_id,
            task_id=task_id, scrape_task_id=scrape_task_id,
            error_code="flow_ai_start_failed", reason=type(exc).__name__,
        )
        return None
    identity = {
        "platform": platform,
        "browser_account": params.get("browser_account"),
        "cdp_port": params.get("cdp_port"),
        "profile_key": params.get("profile_key"),
        "active_account_at_freeze": params.get("active_account_at_freeze"),
        "profile_id": profile_id,
    }
    task.update({key: value for key, value in identity.items() if value not in (None, "")})
    task["profile_id"] = profile_id
    task["active_account_at_freeze"] = task.get("active_account_at_freeze") or ctx.account_for_run()
    source_config = params.get("execution_config")
    source_scope = params.get("frozen_scope")
    digest = hashlib.sha256(json.dumps({
        "platform": platform,
        "scrape_task_id": scrape_task_id,
        "screening_fields": screening_fields,
        "browser_account": task.get("browser_account"),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    task["task_input_digest"] = digest
    try:
        ctx.store.create_screening_run(
            task_id,
            frozen_filters=screening_fields,
            source_count=len(source_result.get("jobs") or []),
            profile_id=profile_id,
            execution_params={
                "platform": platform,
                "filter_schema_version": params.get("filter_schema_version"),
                "screening_fields": screening_fields,
                "profile_summary": profile_summary,
                "profile_facts": profile_facts,
                "scrape_task_id": scrape_task_id,
                "flow_id": flow_id,
                "track_id": params.get("track_id"),
                "browser_account": task.get("browser_account"),
                "cdp_port": task.get("cdp_port"),
                "profile_key": task.get("profile_key"),
                "task_input_digest": digest,
                "cross_platform_dedupe": cross_platform_dedupe,
                "execution_config": source_config,
                "frozen_scope": source_scope,
            },
            backend_version=ctx.backend_version,
        )
        ctx.store.save_filter_snapshot(
            task_id, platform=platform,
            filter_schema_version=params.get("filter_schema_version"),
            filter_snapshot=screening_fields, task_input_digest=digest,
        )
        flow_service.begin_ai(
            flow_id=flow_id, platform=platform, profile_id=profile_id,
            screen_run_id=task_id,
        )
        from webui.whitebox import WhiteboxService
        WhiteboxService(ctx.store).begin(
            "screening", task_id,
            {
                "stages": ["ai_rough", "jd_detail", "ai_fine"],
                "units": [
                    {"unit_key": key, "unit_kind": "ai_stage", "stage": stage, "required": True}
                    for key, stage in (("ai_rough", "ai_rough"), ("jd_detail", "jd_detail"), ("ai_fine", "ai_fine"))
                ],
            },
            parent_owner_id=scrape_task_id,
        )
        future = submit_platform_task(
            ctx,
            flow_id,
            platform,
            ctx.run_ai_screen_task,
            task_id,
            screening_fields,
            profile_summary,
            scrape_task_id,
            "",
            profile_facts,
            cross_platform_dedupe=cross_platform_dedupe,
        )
        attach_ai_future_failure(
            future,
            ctx,
            task_id=task_id,
            scrape_task_id=scrape_task_id,
            platform=platform,
        )
    except Exception as exc:
        _logger.warning(
            "Flow auto AI submission failed (%s)", type(exc).__name__,
        )
        safe_reason = "AI 筛选启动失败"
        coordinator.mark_submission_failed(
            task_id=task_id,
            flow_id=flow_id,
            scrape_task_id=scrape_task_id,
            platform=platform,
            profile_id=profile_id,
            reason=safe_reason,
            error_code="flow_ai_start_failed",
        )
        with ctx.lock:
            task["status"] = "failed"
            task["error"] = safe_reason
        return None
    return task_id
