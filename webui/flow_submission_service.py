"""B096 scrape submission coordinator.

The execute-search route owns HTTP validation.  This module owns the durable
Flow Track claim, scrape/search identity creation, binding, lane submission,
and failure compensation that must happen after a Flow is selected.
"""
from __future__ import annotations

from webui.exec_search_whitebox import mark_scrape_submission_failed
from webui.flow_errors import (
    FlowConflictError,
    FlowResumeError,
    public_flow_message,
)
from webui.flow_service import submit_platform_task
from webui.flow_preflight_recovery import FlowPreflightRecoveryMixin
from webui.flow_track_recovery import FlowTrackRecoveryMixin
from webui.flow_task_state import FlowStateClosureError, close_flow_task_state
from webui.logging_setup import get_logger


_logger = get_logger(__name__)


class FlowSubmissionService(FlowPreflightRecoveryMixin, FlowTrackRecoveryMixin):
    def __init__(self, ctx, flow_service=None):
        self.ctx = ctx
        self.store = ctx.store
        self.flow_service = flow_service or getattr(ctx, "flow_service", None)
    def claim_track(self, *, flow_id, platform, profile_id) -> str | None:
        flow = self.flow_service.claim_track_submission(
            flow_id=flow_id,
            platform=str(platform).strip().lower(),
            profile_id=profile_id,
        )
        return next(
            (
                str(track.get("id"))
                for track in flow.get("tracks", [])
                if track.get("platform") == str(platform).strip().lower()
            ),
            None,
        )

    def create_scrape_records(
        self,
        *,
        task_id,
        profile_id,
        platform,
        flow_id,
        track_id,
        frozen_scope,
        script_params,
        browser_account,
        login_space,
        task_input_digest,
        execution_config,
        resolved_cities,
        auto_screen,
        auto_screen_fields,
        auto_screen_profile,
        auto_screen_facts,
        cross_platform_dedupe,
        profile_summary,
        profile_facts,
    ):
        """Create the durable screening/search identities and bind a Track.

        047 复核 P1：抓取 retry 的 claim 事务已经原子创建主记录与绑定
        （``retry_of_run_id`` 冻结在 ``execution_params``）。初始化必须复用
        该记录，补冻结参数和辅助快照，不能用 ``INSERT OR REPLACE`` 重建，
        否则会抹掉重试关联。未 claim 的首轮提交保持原创建路径。
        """
        init_params = {
            "platform": platform,
            "filter_schema_version": None,
            "script_params": script_params,
            "browser_account": browser_account,
            "cdp_port": login_space.cdp_port,
            "profile_key": login_space.profile_key,
            "task_input_digest": task_input_digest,
            "execution_config": execution_config.to_dict(),
            "resolved_cities": resolved_cities,
            "frozen_scope": frozen_scope.to_dict(),
            "auto_screen": auto_screen,
            "auto_screen_fields": auto_screen_fields,
            "auto_screen_profile": auto_screen_profile,
            "auto_screen_facts": auto_screen_facts,
            "cross_platform_dedupe": cross_platform_dedupe,
            "profile_summary": profile_summary,
            "profile_facts": profile_facts,
            "active_account_at_freeze": self.ctx.account_for_run(),
            "flow_id": flow_id,
            "track_id": track_id,
        }
        existing = self.store.get_screening_run(task_id)
        if existing is None:
            self.store.create_screening_run(
                task_id,
                frozen_filters={},
                source_count=frozen_scope.combination_count,
                profile_id=profile_id,
                execution_params=init_params,
                backend_version=self.ctx.backend_version,
            )
        else:
            # 已创建的主记录只做同身份校验与参数补全；retry_of_run_id 等
            # claim 事实保持原样。
            merged = dict(existing.get("execution_params") or {})
            merged.update(init_params)
            self.store.update_screening_execution_params(task_id, merged)
            self.store.update_screening_run(
                task_id, source_count=frozen_scope.combination_count,
                backend_version=getattr(self.ctx, "backend_version", None),
            )
        self.store.save_filter_snapshot(
            task_id,
            platform=platform,
            filter_schema_version=None,
            filter_snapshot={},
            task_input_digest=task_input_digest,
        )
        if flow_id and profile_id:
            self.flow_service.create_scrape_run(
                task_id=task_id,
                profile_id=profile_id,
                platform=str(platform).strip().lower(),
                flow_id=flow_id,
                track_id=track_id,
                profile_snapshot={
                    "platform": str(platform).strip().lower(),
                    "flow_id": flow_id,
                    "track_id": track_id,
                    "scope_digest": frozen_scope.scope_digest,
                },
            )
            self.flow_service.bind_track_runs(
                flow_id=flow_id,
                platform=str(platform).strip().lower(),
                profile_id=profile_id,
                scrape_run_id=task_id,
            )

    def submit_scrape(self, *, flow_id, platform, task_id, script_params, execution_config, frozen_scope):
        if flow_id:
            return submit_platform_task(
                self.ctx,
                flow_id,
                str(platform).strip().lower(),
                self.ctx.run_pipeline_task,
                task_id,
                script_params,
                execution_config,
                frozen_scope,
            )
        return self.ctx.executor.submit(
            self.ctx.run_pipeline_task,
            task_id,
            script_params,
            execution_config,
            frozen_scope,
        )

    def retry_failed_track(
        self,
        *,
        flow_id,
        platform,
        profile_id,
        expected_run_id="",
        expected_updated_at=None,
        expected_track_id=None,
        flow=None,
        track=None,
    ) -> dict:
        """CAS-claim one failed Track and resubmit it as a fresh attempt.

        047 C2：复用既有提交域（冻结提交快照/预检/记录创建/worker 提交/
        白箱），只把 claim 换成 retry 专用 CAS。旧失败 run、兄弟轨道与旧结果
        完全不动；成功后返回权威 Flow 投影，失败只补偿本次新尝试。
        """
        import hashlib
        import json

        platform = str(platform or "").strip().lower()
        if flow is None:
            flow = self.store.get_flow(flow_id, profile_id=profile_id)
        if track is None:
            track = next(
                (item for item in flow.get("tracks", [])
                 if item.get("platform") == platform),
                None,
            )
        if track is None:
            raise KeyError(f"{flow_id}:{platform}")
        if str(track.get("status") or "") != "failed":
            raise FlowResumeError("preflight_resume_unavailable", "只有失败的平台运行线可以单独重试")

        # 047 C2：AI 段失败复用该轨已有持久化抓取输入，不重抓 source；
        # 抓取段失败才走冻结提交快照重新提交抓取。
        if (str(track.get("stage") or "").strip().lower() == "ai"
                and str(track.get("screen_run_id") or "").strip()):
            return self.retry_failed_ai_track(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                expected_run_id=str(expected_run_id or ""),
                expected_updated_at=expected_updated_at,
                expected_track_id=expected_track_id,
                flow=flow,
                track=track,
            )

        snapshot, script_params, frozen_scope = self._resume_scope(
            platform=platform, track=track,
        )
        execution_config = self._resume_execution_config(frozen_scope)
        login_space = self._resume_login_space(platform=platform, snapshot=snapshot)

        claim = self.store.claim_flow_track_retry(
            flow_id, platform, profile_id=profile_id,
            expected_track_id=expected_track_id,
            expected_run_id=str(expected_run_id or ""),
            expected_updated_at=expected_updated_at,
        )
        if not claim.get("claimed"):
            raise FlowResumeError(
                "preflight_resume_unavailable",
                public_flow_message("preflight_resume_unavailable"),
            )
        task_id = str(claim["new_run_id"])
        track_id = str(claim["track_id"])
        claimed = True
        future = None
        try:
            register = getattr(self.ctx, "register_pipeline_task", None)
            if not callable(register):
                raise RuntimeError("pipeline task registration unavailable")
            task = register(task_id, "scrape")
            with self.ctx.lock:
                task.update({
                    "flow_id": str(flow_id), "profile_id": str(profile_id),
                    "platform": platform,
                    "browser_account": login_space.browser_account,
                    "cdp_port": login_space.cdp_port,
                    "profile_key": login_space.profile_key,
                })
            task_input_digest = hashlib.sha256(
                json.dumps({
                    "platform": platform,
                    "scope_digest": frozen_scope.scope_digest,
                    "filter_schema_version": None,
                    "frozen_filters": {},
                    "browser_account": login_space.browser_account,
                    "cdp_port": login_space.cdp_port,
                    "profile_key": login_space.profile_key,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            from webui.platforms import resolve_platform_city

            city_names = (
                ["全国"]
                if frozen_scope.scope_kind == "nationwide"
                else list(frozen_scope.cities)
            )
            resolved_cities = []
            for city in city_names:
                entry = resolve_platform_city(platform, city)
                resolved_cities.append({
                    "name": entry.name,
                    "label": entry.label,
                    "platform_code": entry.platform_code,
                    "mapping_version": entry.mapping_version,
                })
            self.create_scrape_records(
                task_id=task_id,
                profile_id=profile_id,
                platform=platform,
                flow_id=flow_id,
                track_id=track_id,
                frozen_scope=frozen_scope,
                script_params=script_params,
                browser_account=login_space.browser_account,
                login_space=login_space,
                task_input_digest=task_input_digest,
                execution_config=execution_config,
                resolved_cities=resolved_cities,
                auto_screen=bool(snapshot.get("auto_screen")),
                auto_screen_fields=snapshot.get("auto_screen_fields") or {},
                auto_screen_profile=str(snapshot.get("auto_screen_profile") or ""),
                auto_screen_facts=snapshot.get("auto_screen_facts"),
                cross_platform_dedupe=bool(snapshot.get("cross_platform_dedupe", True)),
                profile_summary=str(snapshot.get("profile_summary") or ""),
                profile_facts=snapshot.get("profile_facts"),
            )
            self.begin_whitebox(
                task_id=task_id,
                script_params=script_params,
                pages_per_combination=frozen_scope.pages_per_combination,
            )
            writer = getattr(self.ctx, "write_run", None)
            if callable(writer):
                writer(task_id, status="running", current_stage="scrape")
            future = self.submit_scrape(
                flow_id=flow_id,
                platform=platform,
                task_id=task_id,
                script_params=script_params,
                execution_config=execution_config,
                frozen_scope=frozen_scope,
            )
            if future is None:
                raise RuntimeError("platform executor rejected task")
            from webui.flow_future import attach_scrape_future_failure

            attach_scrape_future_failure(
                future,
                self.ctx,
                task_id=task_id,
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
            )
            return self.store.get_flow(flow_id, profile_id=profile_id)
        except Exception as exc:  # noqa: BLE001 - compensate only this new attempt
            if claimed:
                self._compensate_claim_failure(
                    flow_id=flow_id,
                    platform=platform,
                    profile_id=profile_id,
                    task_id=task_id,
                    future=future,
                    exc=exc,
                )
            raise

    def begin_whitebox(self, *, task_id, script_params, pages_per_combination):
        """Create the scrape evidence plan for a Flow-owned task."""
        from webui.exec_search_whitebox import begin_scrape_whitebox

        return begin_scrape_whitebox(
            self.store,
            task_id,
            script_params,
            pages_per_combination,
        )

    def fail(
        self,
        *,
        flow_id,
        platform,
        profile_id,
        task_id,
        error_code,
        reason="",
        status="failed",
    ):
        if not flow_id or not profile_id:
            return None
        code = str(error_code or "track_submit_failed")
        safe_reason = public_flow_message(code, reason)
        try:
            return close_flow_task_state(
                self.ctx,
                task_id=task_id,
                scrape_task_id=task_id,
                flow_id=flow_id,
                profile_id=profile_id,
                status=status,
                error_code=code,
                reason=safe_reason,
                platform=str(platform).strip().lower(),
                stage="scrape",
            )
        except Exception as exc:  # noqa: BLE001 - surface safe closure failure
            raise FlowStateClosureError() from exc

    def mark_executor_failure(self, *, task_id, script_params, reason, pages):
        """Persist submit evidence after the Flow state transaction succeeds."""
        mark_scrape_submission_failed(
            self.store, task_id, script_params, reason,
            stage="submit", pages=pages,
        )

    def mark_whitebox_failure(self, *, task_id, reason):
        """Record whitebox evidence without changing durable Flow statuses."""
        from webui.whitebox import WhiteboxService

        run = self.store.get_whitebox_run("scrape", task_id)
        if not run:
            return None
        units = self.store.list_whitebox_units(run["id"])
        plan = {
            "stages": ["scrape_list"],
            "units": [
                {
                    "unit_key": unit.get("unit_key"),
                    "unit_kind": unit.get("unit_kind") or "keyword_city",
                    "stage": "scrape_list",
                    "required": True,
                }
                for unit in units
                if unit.get("unit_key")
            ],
        }
        if not plan["units"]:
            return None
        return WhiteboxService(self.store).mark_submission_failed(
            "scrape", task_id, plan, reason, stage="submit",
        )
