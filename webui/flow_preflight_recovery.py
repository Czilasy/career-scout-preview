"""047 结构前置：预检恢复编排（自 FlowSubmissionService 搬运）。

保持原 scope/config/login/probe 编排、补偿顺序与错误映射不变；
FlowSubmissionService 通过本 mixin 保持公开方法与原调用路径。
"""

from __future__ import annotations

import hashlib
import json
import uuid

from webui.flow_errors import FlowResumeError, public_flow_message
from webui.flow_task_state import FlowStateClosureError
from webui.logging_setup import get_logger
from webui.store_flow import FlowConflictError

_logger = get_logger(__name__)


class FlowPreflightRecoveryMixin:
    """Preflight resume scope/probe orchestration for one paused Track."""

    @staticmethod
    def _value_list(value):
        if isinstance(value, str):
            return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if item is not None and str(item).strip()]
        return []

    def _resume_scope(self, *, platform, track):
        """Rebuild the immutable source scope from the durable submission snapshot."""
        from webui.execution_config import FrozenTaskScope, preview_scope

        snapshot = track.get("submission_snapshot") or {}
        if not isinstance(snapshot, dict):
            raise FlowResumeError("scope_validation_failed", "暂停任务的搜索快照无效")
        script_params = snapshot.get("script_params")
        if not isinstance(script_params, dict):
            raise FlowResumeError("scope_validation_failed", "暂停任务缺少可恢复的搜索范围")
        script_params = dict(script_params)
        keywords = self._value_list(
            script_params.get("keyword", script_params.get("keywords"))
        )
        cities = self._value_list(
            script_params.get("city", script_params.get("cities"))
        )
        if not keywords or not cities:
            raise FlowResumeError("scope_validation_failed", "暂停任务缺少关键词或城市")
        try:
            # execute-search uses three pages when the request omits pages;
            # retrying must preserve that frozen source contract.
            pages = int(script_params.get("pages") or 3)
        except (TypeError, ValueError) as exc:
            raise FlowResumeError("scope_validation_failed", "暂停任务的页数无效") from exc
        locations = script_params.get("locations") or []
        if not isinstance(locations, list):
            raise FlowResumeError("location_validation_failed", "暂停任务的搜索地点无效")
        scope_payload = snapshot.get("scope")
        if isinstance(scope_payload, dict):
            try:
                scope = FrozenTaskScope.from_dict(scope_payload)
            except (KeyError, TypeError, ValueError) as exc:
                raise FlowResumeError(
                    "scope_validation_failed", "暂停任务的搜索范围已失效",
                ) from exc
            if scope.platform != platform:
                raise FlowResumeError("scope_platform_mismatch", "暂停任务的平台与搜索范围不一致")
        else:
            try:
                scope_payload = preview_scope(
                    keywords=keywords,
                    scope_kind="nationwide" if cities == ["全国"] else "cities",
                    cities=[] if cities == ["全国"] else cities,
                    pages_per_combination=pages,
                    locations=locations,
                    platform=platform,
                )["scope"]
                scope = FrozenTaskScope.from_dict(scope_payload)
            except (KeyError, TypeError, ValueError) as exc:
                raise FlowResumeError(
                    "scope_validation_failed", "暂停任务的搜索范围已失效",
                ) from exc
        expected_digest = str(snapshot.get("scope_digest") or "").strip()
        if expected_digest and expected_digest != scope.scope_digest:
            raise FlowResumeError("scope_validation_failed", "暂停任务的搜索范围已失效")
        script_params["keyword"] = ",".join(scope.keywords)
        script_params["city"] = ["全国"] if scope.scope_kind == "nationwide" else list(scope.cities)
        script_params["pages"] = scope.pages_per_combination
        if scope.locations:
            script_params["locations"] = [dict(item) for item in scope.locations]
        else:
            script_params.pop("locations", None)
        return snapshot, script_params, scope

    def _resume_execution_config(self, frozen_scope):
        from webui.execution_config import ExecutionConfigSnapshot

        try:
            state = self.store.get_advanced_config_state()
            selected = self.store.select_mode(
                state["active_selection"], task_size=frozen_scope.task_size,
            )
            return ExecutionConfigSnapshot.from_dict(selected["config"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FlowResumeError("config_resolution_failed", "执行配置无效") from exc

    def _resume_login_space(self, *, platform, snapshot):
        from webui.pipeline_exec import account_for_role, resolve_browser_account
        from webui.platforms import resolve_login_space

        app = getattr(self.ctx, "app", None)
        config = getattr(app, "config", {}) if app is not None else {}
        accounts_path = config.get("BROWSER_ACCOUNTS_PATH")
        account = str(snapshot.get("browser_account") or "").strip()
        if not account:
            resolver = getattr(self.ctx, "account_for_run", None)
            fallback = str(resolver() if callable(resolver) else "").strip()
            account = account_for_role("R1", accounts_path, fallback=fallback)
        if not account:
            raise FlowResumeError("source_cdp_unavailable", "平台登录空间暂不可用，任务已暂停")
        try:
            profile_dir = resolve_browser_account(account, accounts_path)
            return resolve_login_space(
                platform,
                account,
                boss_profile_dir=profile_dir or "unresolved",
            )
        except Exception as exc:  # noqa: BLE001 - login/CDP failures are retryable
            raise FlowResumeError(
                "source_cdp_unavailable", "平台登录空间暂不可用，任务已暂停",
            ) from exc

    def _check_resume_block(
        self, *, candidate, flow_id, profile_id, track_id, frozen_scope,
    ):
        """Run the production resume/CDP probe before claiming a Track.

        ``ctx.check_resume_block`` records diagnostics against a screening Run
        when it is using its built-in checker.  A preflight Track intentionally
        has no Run yet, so provide a short-lived, unbound process-log row for
        that checker and remove it in the same boundary.  Explicit injected
        checkers remain pure and receive the candidate directly.
        """
        checker = getattr(self.ctx, "check_resume_block", None)
        if not callable(checker):
            return True, "", ""

        def _stable_probe_code(raw_code, *, default="source_cdp_unavailable"):
            code = str(raw_code or "").strip().lower()
            if code in {
                "login_required", "not_logged_in", "login_expired",
                "source_login_expired", "boss_login_required",
                "zhilian_login_required",
            }:
                return "source_login_required"
            if code in {
                "cdp_unavailable", "cdp_unreachable", "browser_unavailable",
            }:
                return "source_cdp_unavailable"
            # 047 C1：unknown/unreachable 保留自己的事实，不冒充 CDP 不可用。
            if code in {"source_status_unclear", "source_unreachable"}:
                return code
            return str(raw_code or default).strip() or default
        app = getattr(self.ctx, "app", None)
        config = getattr(app, "config", {}) if app is not None else {}
        injected = callable(config.get("RESUME_BLOCK_CHECKER"))
        provisional = False
        if not injected:
            try:
                self.store.create_screening_run(
                    candidate["id"],
                    frozen_filters={},
                    source_count=frozen_scope.combination_count,
                    profile_id=profile_id,
                    execution_params={
                        **dict(candidate.get("execution_params") or {}),
                        "flow_id": str(flow_id),
                        "track_id": str(track_id or ""),
                    },
                    backend_version=getattr(self.ctx, "backend_version", None),
                )
                provisional = True
            except Exception as exc:  # noqa: BLE001 - no safe probe boundary
                return False, "preflight_resume_unavailable", str(exc)
        try:
            result = checker(candidate)
            if not isinstance(result, tuple) or len(result) != 3:
                return False, "source_cdp_unavailable", "平台登录空间暂不可用"
            passed, code, reason = result
            return bool(passed), _stable_probe_code(code), str(reason or "")
        except Exception as exc:  # noqa: BLE001 - map checker failure safely
            return False, _stable_probe_code(
                getattr(exc, "error_code", None)
                or getattr(exc, "failed_code", None)
            ), str(exc)
        finally:
            if provisional:
                try:
                    self.store.delete_unbound_flow_ai_run(
                        candidate["id"], flow_id=str(flow_id),
                    )
                    # ``check_resume_block`` records its diagnostic event via
                    # ``append_task_event``.  That helper creates a legacy
                    # task/log anchor for foreign-key compatibility; a
                    # preflight probe must not leave that anchor behind as a
                    # fake user task after the screening row is removed.
                    cleanup_task = getattr(self.store, "delete_task_with_logs", None)
                    if callable(cleanup_task):
                        cleanup_task(candidate["id"])
                except Exception as exc:  # noqa: BLE001 - cleanup must be visible
                    raise FlowResumeError(
                        "preflight_resume_unavailable",
                        "登录预检收口失败，请刷新后重试",
                    ) from exc

    def _pause_preflight_resume(self, *, flow_id, platform, profile_id, code, reason):
        safe_reason = reason or public_flow_message(code)
        try:
            self.flow_service.record_preflight_failure(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                error_code=code,
                recoverable=True,
                reason=safe_reason,
            )
        except Exception as exc:  # noqa: BLE001 - preserve retryable failure
            # Preserve the original retryable failure; the Track was already
            # paused and must not be reported as a successful resume.
            _logger.warning(
                "preflight resume failure state write failed; retaining paused Track (%s)",
                type(exc).__name__,
                exc_info=True,
            )
        raise FlowResumeError(code, safe_reason)

    def _compensate_claim_failure(
        self, *, flow_id, platform, profile_id, task_id, future, exc,
    ) -> None:
        """Close a claimed submission and stop any worker already handed off."""
        cleanup_errors = []
        if future is not None:
            cancel = getattr(future, "cancel", None)
            if callable(cancel):
                try:
                    if cancel() is False:
                        cleanup_errors.append("future cancellation was rejected")
                except Exception as cancel_exc:  # noqa: BLE001 - preserve compensation failure
                    cleanup_errors.append(cancel_exc)
        try:
            with self.ctx.lock:
                task = self.ctx.tasks.get(task_id)
                if task is not None:
                    stop_event = task.get("stop_event")
                    if stop_event is not None:
                        try:
                            from webui.task_pause_support import (
                                STOP_MODE_CANCEL,
                                request_stop,
                            )

                            request_stop(task, stop_event, STOP_MODE_CANCEL)
                        except Exception as stop_exc:  # noqa: BLE001
                            cleanup_errors.append(stop_exc)
                    task["status"] = "paused"
                    task["error"] = public_flow_message(
                        getattr(exc, "error_code", "")
                        or "preflight_resume_unavailable",
                        exc,
                    )
        except Exception as task_exc:  # noqa: BLE001 - compensation must be visible
            cleanup_errors.append(task_exc)

        error_code = str(
            getattr(exc, "error_code", "") or "preflight_resume_unavailable"
        )
        if error_code not in {
            "source_cdp_unavailable", "source_login_required", "browser_busy",
            "account_pool_empty", "scope_validation_failed",
            "location_validation_failed", "config_resolution_failed",
            "scope_platform_mismatch", "preflight_resume_unavailable",
        }:
            error_code = "preflight_resume_unavailable"
        reason = public_flow_message(error_code, exc)
        try:
            self.flow_service.mark_submission_failed(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                task_id=task_id,
                error_code=error_code,
                reason=reason,
                status="paused",
            )
        except Exception as state_exc:  # noqa: BLE001 - never report success
            raise FlowStateClosureError() from state_exc

        for name in ("schedule_pipeline_task_cleanup", "release_worker_resume_claims"):
            callback = getattr(self.ctx, name, None)
            if not callable(callback):
                continue
            try:
                if name == "release_worker_resume_claims":
                    callback(self.ctx.tasks.get(task_id))
                else:
                    callback(task_id)
            except Exception as cleanup_exc:  # noqa: BLE001 - never hide cleanup loss
                cleanup_errors.append(cleanup_exc)
        if cleanup_errors:
            raise FlowStateClosureError() from cleanup_errors[0]

    def resume_preflight_track(
        self, *, flow_id, platform, profile_id, flow=None, track=None,
    ) -> dict:
        """Safely re-submit a paused preflight Track which has no Run yet.

        This is deliberately the same low-level submission coordinator used by
        execute-search.  It creates one Flow-bound Run only after identity and
        source scope checks pass, and leaves the Track paused on recoverable
        activation/submission failures.
        """
        platform = str(platform or "").strip().lower()
        if flow is None:
            flow = self.store.get_flow(flow_id, profile_id=profile_id)
        if track is None:
            track = next(
                item for item in flow.get("tracks", [])
                if item.get("platform") == platform
            )
        if track.get("status") != "paused":
            raise FlowResumeError("preflight_resume_unavailable", "平台运行线当前不可继续")
        if any(track.get(key) for key in ("scrape_run_id", "screen_run_id", "result_run_id")):
            raise FlowResumeError("preflight_resume_unavailable", "平台运行线已提交，请刷新后重试")

        snapshot, script_params, frozen_scope = self._resume_scope(
            platform=platform, track=track,
        )
        execution_config = self._resume_execution_config(frozen_scope)
        try:
            login_space = self._resume_login_space(
                platform=platform, snapshot=snapshot,
            )
        except FlowResumeError as exc:
            self._pause_preflight_resume(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                code=exc.error_code,
                reason=exc.message,
            )
        task_id = uuid.uuid4().hex
        params = {
            "platform": platform,
            "flow_id": str(flow_id),
            "track_id": str(track.get("id") or ""),
            "script_params": script_params,
            "browser_account": login_space.browser_account,
            "cdp_port": login_space.cdp_port,
            "profile_key": login_space.profile_key,
        }
        candidate = {
            "id": task_id,
            "profile_id": str(profile_id),
            "platform": platform,
            "current_stage": "scrape",
            "execution_params": params,
            "frozen_filters": frozen_scope.to_dict(),
        }
        activate = getattr(self.ctx, "activate_run_browser", None)
        if callable(activate):
            try:
                activate(candidate)
            except Exception as exc:  # noqa: BLE001 - map login/CDP failures safely
                code = str(
                    getattr(exc, "error_code", "")
                    or getattr(exc, "failed_code", "")
                    or "source_cdp_unavailable"
                )
                if code not in {
                    "source_cdp_unavailable", "source_login_required",
                    "browser_busy", "account_pool_empty",
                }:
                    code = "source_cdp_unavailable"
                self._pause_preflight_resume(
                    flow_id=flow_id, platform=platform, profile_id=profile_id,
                    code=code, reason=public_flow_message(code),
                )

        passed, code, reason = self._check_resume_block(
            candidate=candidate,
            flow_id=flow_id,
            profile_id=profile_id,
            track_id=track.get("id"),
            frozen_scope=frozen_scope,
        )
        if not passed:
            code = str(code or "source_cdp_unavailable")
            if code not in {
                "source_cdp_unavailable", "source_login_required",
                "browser_busy", "account_pool_empty",
            }:
                code = "source_cdp_unavailable"
            self._pause_preflight_resume(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                code=code,
                reason=public_flow_message(code, reason),
            )

        claimed = False
        future = None
        try:
            track_id = self.claim_track(
                flow_id=flow_id, platform=platform, profile_id=profile_id,
            )
            claimed = True
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
        except (FlowResumeError, FlowConflictError) as exc:
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
        except Exception as exc:  # noqa: BLE001 - close one claimed Track safely
            if not claimed:
                raise
            self._compensate_claim_failure(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                task_id=task_id,
                future=future,
                exc=exc,
            )
            # The durable Track is paused and the caller receives a stable
            # retryable error; never turn a post-claim failure into success.
            raise FlowResumeError(
                "preflight_resume_unavailable",
                public_flow_message("preflight_resume_unavailable"),
            ) from exc
