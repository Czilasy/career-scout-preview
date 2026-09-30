"""搜索执行 API 路由（021 B6 T019 外迁自 webui/app.py）。
搜索范围预览、组合抓取提交/续跑/取消。路由体纯搬运：HTTP 契约零改动；
任务声明 / 断言 / runner 包装经 ctx 取用。
"""
from __future__ import annotations
import uuid
import hashlib
import json
from flask import jsonify, request
from webui.constants import (
    _MSG_UNSUPPORTED_PLATFORM,
)
from webui.error_registry import (
    is_recoverable_systemic_block,
)
from webui.logging_setup import get_logger
from webui.exec_search_whitebox import begin_scrape_whitebox
from webui.store_flow import FlowConflictError
from webui.flow_service import public_flow_message
from webui.flow_submission_service import FlowSubmissionService
from webui.flow_future import attach_scrape_future_failure
from webui.flow_task_state import (
    FlowStateClosureError,
    register_flow_state_error_handler,
)
from webui.exec_search_cancel import register_cancel_execute_search
from webui.exec_search_resume import (
    _public_failure_details,
    register_search_continue,
)
from webui.exec_search_scope import register_search_scope_preview
_logger = get_logger(__name__)


def register_exec_search_routes(app, ctx):
    register_flow_state_error_handler(app)
    register_search_scope_preview(app, ctx)
    @app.route("/api/execute-search", methods=["POST"])
    def execute_search():
        """Stage 3 / tasks005 T402: 平台感知搜索 run 创建。
        Accepts JSON ``{"script_params": {...}}`` (or the params directly).
        Launches a background task and returns a ``task_id`` for polling.
        SPEC011 T006: 后端从权威 scope 和当前配置选择创建不可变快照；
        客户端不能提供或覆盖任务规模与执行配置。
        SPEC011 T015: 实验租约持有时拒绝启动（FR-035）。
        tasks005 T402: 冻结单一平台和完整 runtime，搜索 run 筛选快照为空。
        """
        from webui.core import _AI_FILTER_KEYS, _is_non_empty_filter_value
        from webui.location_catalog import LocationCatalogUnavailable
        from webui.location_scope import normalize_locations
        from webui.execution_config import (
            ExecutionConfigSnapshot,
            FrozenTaskScope,
            preview_scope,
        )
        from webui.pipeline_exec_accounts import has_selected_account
        from webui.pipeline_exec import resolve_browser_account
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            resolve_login_space,
            validate_platform_key,
        )
        body = request.get_json(silent=True) or {}
        career_profile_id = str(body.get("profile_id") or "").strip() or None
        requested_flow_id = str(body.get("flow_id") or "").strip() or None
        requested_platform = str(body.get("platform") or "").strip().lower()
        if requested_flow_id and not requested_platform:
            return jsonify({
                "ok": False,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "Flow 任务必须显式提供平台身份",
            }), 409
        platform_raw = requested_platform or "boss"
        flow_service = getattr(ctx, "flow_service", None)
        submission_service = getattr(ctx, "flow_submission_service", None)
        if submission_service is None:
            submission_service = FlowSubmissionService(ctx, flow_service)

        def _record_preflight(error_code, *, recoverable=False, reason=""):
            if not requested_flow_id or not career_profile_id or flow_service is None:
                return
            try:
                flow_service.record_preflight_failure(
                    flow_id=requested_flow_id,
                    platform=platform_raw,
                    profile_id=career_profile_id,
                    error_code=error_code,
                    recoverable=recoverable,
                    reason=reason,
                )
            except Exception as state_exc:
                _logger.warning(
                    "Flow preflight failure state write failed (%s)",
                    type(state_exc).__name__,
                )

        script_params = body.get("script_params") or body
        if not isinstance(script_params, dict):
            _record_preflight("track_submit_failed")
            return jsonify({"ok": False, "error": "无效的请求体"}), 400

        def _remember_submission_snapshot():
            """Keep only safe search inputs for a no-run preflight retry."""
            if not requested_flow_id or not career_profile_id or flow_service is None:
                return True, "", ""
            safe_script_params = {
                key: script_params.get(key)
                for key in ("keyword", "city", "locations", "pages")
                if key in script_params
            }
            snapshot = {
                "script_params": safe_script_params,
                "scope_digest": str(body.get("scope_digest") or ""),
                "auto_screen": bool(body.get("auto_screen")),
                "auto_screen_fields": (
                    dict(body.get("auto_screen_fields"))
                    if isinstance(body.get("auto_screen_fields"), dict) else {}
                ),
                "auto_screen_profile": str(body.get("auto_screen_profile") or ""),
                "auto_screen_facts": (
                    dict(body.get("auto_screen_facts"))
                    if isinstance(body.get("auto_screen_facts"), dict) else {}
                ),
                "profile_summary": str(body.get("profile_summary") or ""),
                "profile_facts": (
                    dict(body.get("profile_facts"))
                    if isinstance(body.get("profile_facts"), dict) else {}
                ),
                "cross_platform_dedupe": bool(body.get("cross_platform_dedupe", True)),
            }
            try:
                flow_service.save_track_submission_snapshot(
                    flow_id=requested_flow_id,
                    platform=platform_raw,
                    profile_id=career_profile_id,
                    snapshot=snapshot,
                )
                return True, "", ""
            except FlowConflictError as state_exc:
                return False, "flow_conflict", public_flow_message("flow_conflict", state_exc)
            except Exception as state_exc:
                _logger.warning(
                    "Flow preflight snapshot write failed (%s)",
                    type(state_exc).__name__,
                )
                return False, "track_submit_failed", public_flow_message(
                    "track_submit_failed", state_exc,
                )

        if not script_params.get("keyword") or not script_params.get("city"):
            _record_preflight("scope_validation_failed")
            return jsonify({"ok": False, "error": "缺少关键词或城市"}), 400
        locations = script_params.get("locations") or []
        if not isinstance(locations, list):
            _record_preflight("location_validation_failed")
            return jsonify({"ok": False, "error": "locations 必须是数组"}), 400
        auto_screen = bool(body.get("auto_screen"))
        cross_platform_dedupe = bool(body.get("cross_platform_dedupe", True))
        auto_screen_fields = body.get("auto_screen_fields") if auto_screen else {}
        if auto_screen and not isinstance(auto_screen_fields, dict):
            _record_preflight("track_submit_failed")
            return jsonify({"ok": False, "error": "auto_screen_fields 必须是对象"}), 400
        auto_screen_profile = str(body.get("auto_screen_profile") or "") if auto_screen else ""
        auto_screen_facts = body.get("auto_screen_facts") if auto_screen and isinstance(body.get("auto_screen_facts"), dict) else None
        profile_summary = str(body.get("profile_summary") or "")
        raw_profile_facts = body.get("profile_facts")
        profile_facts = (
            raw_profile_facts
            if isinstance(raw_profile_facts, dict) else None
        )
        try:
            validate_platform_key(platform_raw)
        except UnknownPlatformError:
            _record_preflight("platform_validation_failed")
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
            }), 400
        reg = get_platform_or_none(platform_raw)
        if reg is None:
            _record_preflight("platform_validation_failed")
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": "平台未注册",
            }), 400
        offending = [
            k for k in _AI_FILTER_KEYS
            if k in script_params and _is_non_empty_filter_value(script_params[k])
        ]
        if offending:
            _record_preflight("scope_validation_failed")
            return jsonify({
                "ok": False,
                "error_code": "search_filters_not_supported",
                "user_message": "搜索请求不允许携带非空 AI filters: " + ", ".join(sorted(offending)),
            }), 422
        requested_digest = str(body.get("scope_digest") or "")
        scope_payload = ctx.scope_previews.get(requested_digest) if requested_digest else None
        if requested_digest and scope_payload is None:
            _record_preflight("scope_validation_failed")
            return jsonify({
                "ok": False,
                "error_code": "scope_preview_required",
                "error": "搜索范围摘要未知，请重新校验搜索范围",
            }), 409
        if scope_payload is None:
            raw_keyword = script_params.get("keyword")
            keywords = (
                [item.strip() for item in str(raw_keyword).replace("，", ",").split(",")]
                if not isinstance(raw_keyword, list) else raw_keyword
            )
            raw_cities = script_params.get("city") or []
            if isinstance(raw_cities, str):
                raw_cities = [
                    item.strip() for item in raw_cities.replace("，", ",").split(",")
                    if item.strip()
                ]
            nationwide = raw_cities == ["全国"]
            try:
                preview = preview_scope(
                    keywords=keywords,
                    scope_kind="nationwide" if nationwide else "cities",
                    cities=[] if nationwide else raw_cities,
                    pages_per_combination=script_params.get("pages", 3),
                    locations=locations,
                    platform=platform_raw,
                )
            except LocationCatalogUnavailable:
                _record_preflight("location_validation_failed", recoverable=True)
                return jsonify({"ok": False, "error_code": "location_catalog_unavailable", "error": "地点目录暂时不可用，按城市级搜索"}), 503
            except (TypeError, ValueError):
                _record_preflight("scope_validation_failed")
                return jsonify({
                    "ok": False, "error_code": "scope_validation_failed",
                    "error": "搜索范围参数无效",
                }), 422
            scope_payload = preview["scope"]
            ctx.scope_previews[scope_payload["scope_digest"]] = dict(scope_payload)
        try:
            frozen_scope = FrozenTaskScope.from_dict(scope_payload)
            state = ctx.store.get_advanced_config_state()
            selected = ctx.store.select_mode(
                state["active_selection"], task_size=frozen_scope.task_size,
            )
            execution_config = ExecutionConfigSnapshot.from_dict(selected["config"])
        except (KeyError, TypeError, ValueError):
            _record_preflight("config_resolution_failed")
            return jsonify({
                "ok": False, "error_code": "config_resolution_failed",
                "error": "执行配置无效",
            }), 422
        if frozen_scope.platform != platform_raw:
            _record_preflight("scope_validation_failed")
            return jsonify({
                "ok": False,
                "error_code": "scope_platform_mismatch",
                "user_message": "请求平台与搜索范围平台不一致",
            }), 409
        if not reg.enabled_for_new_tasks:
            _record_preflight("platform_disabled")
            return jsonify({
                "ok": False,
                "error_code": "platform_disabled",
                "user_message": reg.availability_reason or "平台暂不可用",
            }), 503
        sp_keywords = script_params.get("keyword")
        if isinstance(sp_keywords, str):
            sp_keyword_list = [k.strip() for k in sp_keywords.replace("，", ",").split(",") if k.strip()]
        elif isinstance(sp_keywords, list):
            sp_keyword_list = [str(k).strip() for k in sp_keywords if k and str(k).strip()]
        else:
            sp_keyword_list = []
        sp_cities = script_params.get("city") or []
        if isinstance(sp_cities, str):
            sp_cities = [c.strip() for c in sp_cities.replace("，", ",").split(",") if c.strip()]
        scope_cities = (
            ["全国"] if frozen_scope.scope_kind == "nationwide"
            else list(frozen_scope.cities)
        )
        try:
            norm_request_locations = normalize_locations(platform_raw, locations)
        except LocationCatalogUnavailable:
            _record_preflight("location_validation_failed", recoverable=True)
            return jsonify({"ok": False, "error_code": "location_catalog_unavailable", "error": "地点目录暂时不可用，按城市级搜索"}), 503
        except ValueError:
            _record_preflight("location_validation_failed")
            return jsonify({
                "ok": False,
                "error_code": "location_validation_failed",
                "error": "搜索地点参数无效",
            }), 422
        pages_mismatch = False
        if "pages" in script_params:
            try:
                sp_pages = int(script_params["pages"])
                pages_mismatch = sp_pages != frozen_scope.pages_per_combination
            except (TypeError, ValueError):
                pages_mismatch = True
        if (sp_keyword_list != list(frozen_scope.keywords)
                or list(sp_cities) != scope_cities
                or list(norm_request_locations) != list(frozen_scope.locations)
                or pages_mismatch):
            _record_preflight("scope_validation_failed")
            return jsonify({
                "ok": False,
                "error_code": "scope_request_mismatch",
                "user_message": "搜索参数与搜索范围不一致",
            }), 409
        script_params = dict(script_params)
        script_params["keyword"] = ",".join(frozen_scope.keywords)
        script_params["city"] = scope_cities
        script_params["pages"] = frozen_scope.pages_per_combination
        if frozen_scope.locations:
            script_params["locations"] = list(frozen_scope.locations)
        else:
            script_params.pop("locations", None)

        # Only a fully validated request may freeze the retry payload.  Runtime
        # gates below can still reject this request, but the durable snapshot is
        # then safe to use for a later Flow action retry.
        snapshot_ok, snapshot_code, snapshot_reason = _remember_submission_snapshot()
        if not snapshot_ok:
            status_code = 409 if snapshot_code == "flow_conflict" else 503
            return jsonify({
                "ok": False,
                "error": snapshot_code,
                "error_code": snapshot_code,
                "error_reason": snapshot_reason,
            }), status_code
        if not has_selected_account(app.config["BROWSER_ACCOUNTS_PATH"]):
            _record_preflight("account_pool_empty")
            return jsonify({
                "ok": False,
                "error_code": "account_pool_empty",
                "error": "请至少勾选一个账号参与轮询后再开抓",
                "user_message": "请至少勾选一个账号参与轮询后再开抓",
            }), 422
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            _record_preflight("browser_busy", recoverable=True)
            return err_resp
        if ctx.browser_busy(platform_raw):
            _record_preflight("browser_busy", recoverable=True)
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前已有任务在运行或暂停，请先等待、继续或结束任务后再开始新任务",
            }), 409
        from webui.pipeline_exec import account_for_role
        browser_account = account_for_role(
            "R1", app.config["BROWSER_ACCOUNTS_PATH"],
            fallback=ctx.account_for_run(),
        )
        profile_dir = resolve_browser_account(
            browser_account, app.config["BROWSER_ACCOUNTS_PATH"])
        login_space = resolve_login_space(
            platform_raw, browser_account,
            boss_profile_dir=profile_dir or "unresolved",
        )
        from webui.platforms import resolve_platform_city
        resolved_cities = []
        for city_name in scope_cities:
            entry = resolve_platform_city(platform_raw, city_name)
            resolved_cities.append({
                "name": entry.name,
                "label": entry.label,
                "platform_code": entry.platform_code,
                "mapping_version": entry.mapping_version,
            })
        task_input_digest = hashlib.sha256(json.dumps({
            "platform": platform_raw,
            "scope_digest": frozen_scope.scope_digest,
            "filter_schema_version": None,
            "frozen_filters": {},
            "browser_account": browser_account,
            "cdp_port": login_space.cdp_port,
            "profile_key": login_space.profile_key,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        task_id = uuid.uuid4().hex
        flow_service = getattr(ctx, "flow_service", None)
        flow_id = requested_flow_id
        flow_track_id = None
        if flow_id:
            if not career_profile_id or flow_service is None: return jsonify({"ok": False, "error_code": "flow_not_found", "message": "流程不存在或不可访问"}), 404
            try:
                flow_service.validate_track_submission(flow_id=flow_id, platform=platform_raw, profile_id=career_profile_id)
            except FlowConflictError as exc: return jsonify({"ok": False, "error_code": "flow_conflict", "message": public_flow_message("flow_conflict", exc)}), 409
            except (KeyError, ValueError): return jsonify({"ok": False, "error_code": "flow_not_found", "message": "流程不存在或不可访问"}), 404
            except Exception as exc:
                _record_preflight("track_submit_failed", reason=type(exc).__name__)
                return jsonify({
                    "ok": False,
                    "error": "track_submit_failed",
                    "error_code": "track_submit_failed",
                    "error_reason": public_flow_message("track_submit_failed"),
                }), 503
        elif career_profile_id and flow_service is not None:
            try:
                single_flow = flow_service.start_single_flow(
                    profile_id=career_profile_id, platform=platform_raw, start_key=task_id)
                flow_id = single_flow["id"] if single_flow else None
            except FlowConflictError as exc:
                return jsonify({"ok": False, "error": "flow_conflict",
                                "error_code": "flow_conflict", "message": public_flow_message("flow_conflict", exc)}), 409
        # Claim the Track before creating any external task.  This is the
        # durable idempotency boundary for two concurrent execute-search
        # requests racing after the same Flow POST.
        if flow_id and career_profile_id:
            try:
                flow_track_id = submission_service.claim_track(
                    flow_id=flow_id,
                    platform=platform_raw,
                    profile_id=career_profile_id,
                )
            except FlowConflictError as exc:
                return jsonify({
                    "ok": False,
                    "error": "flow_conflict",
                    "error_code": "flow_conflict",
                    "message": public_flow_message("flow_conflict", exc),
                }), 409
            except (KeyError, ValueError):
                return jsonify({
                    "ok": False,
                    "error": "flow_not_found",
                    "error_code": "flow_not_found",
                    "message": "流程不存在或不可访问",
                }), 404
            except Exception as exc:
                _record_preflight("track_submit_failed", reason=type(exc).__name__)
                return jsonify({
                    "ok": False,
                    "error": "track_submit_failed",
                    "error_code": "track_submit_failed",
                    "error_reason": public_flow_message("track_submit_failed"),
                }), 503
        def _fail_flow_submission(error_code, detail="", *, status="failed"):
            """Persist a safe Track failure for every post-claim boundary."""
            return submission_service.fail(
                flow_id=flow_id,
                platform=str(platform_raw).strip().lower(),
                profile_id=career_profile_id,
                task_id=task_id,
                error_code=str(error_code or "track_submit_failed"),
                reason=public_flow_message(
                    str(error_code or "track_submit_failed"), detail,
                ),
                status=status,
            )

        try:
            task = ctx.register_pipeline_task(task_id, "scrape")
        except Exception as exc:
            _fail_flow_submission("track_submit_failed", exc)
            return jsonify({
                "ok": False,
                "error": "track_submit_failed",
                "error_code": "track_submit_failed",
                "error_reason": public_flow_message("track_submit_failed"),
            }), 503
        with ctx.lock:
            task["config_digest"] = execution_config.config_digest
            task["scope_digest"] = frozen_scope.scope_digest
            task["browser_account"] = browser_account
            task["platform"] = platform_raw
            task["cdp_port"] = login_space.cdp_port
            task["profile_key"] = login_space.profile_key
            task["task_input_digest"] = task_input_digest
            task["auto_screen"] = auto_screen
            task["cross_platform_dedupe"] = cross_platform_dedupe
            # Spec041：内存任务也携带画像身份，恢复/取消不得跨画像。
            task["profile_id"] = career_profile_id
            task["flow_id"] = flow_id

        try:
            submission_service.create_scrape_records(
                task_id=task_id,
                profile_id=career_profile_id,
                platform=platform_raw,
                flow_id=flow_id,
                track_id=flow_track_id,
                frozen_scope=frozen_scope,
                script_params=script_params,
                browser_account=browser_account,
                login_space=login_space,
                task_input_digest=task_input_digest,
                execution_config=execution_config,
                resolved_cities=resolved_cities,
                auto_screen=auto_screen,
                auto_screen_fields=auto_screen_fields,
                auto_screen_profile=auto_screen_profile,
                auto_screen_facts=auto_screen_facts,
                cross_platform_dedupe=cross_platform_dedupe,
                profile_summary=profile_summary,
                profile_facts=profile_facts,
            )
        except Exception as exc:
            _fail_flow_submission("track_submit_failed", exc)
            with ctx.lock:
                task["status"] = "failed"
                task["error"] = public_flow_message("track_submit_failed")
            return jsonify({
                "ok": False,
                "error": "track_submit_failed",
                "error_code": "track_submit_failed",
                "error_reason": public_flow_message("track_submit_failed"),
            }), 503

        try:
            if flow_id:
                submission_service.begin_whitebox(
                    task_id=task_id,
                    script_params=script_params,
                    pages_per_combination=frozen_scope.pages_per_combination,
                )
            else:
                begin_scrape_whitebox(
                    ctx.store,
                    task_id,
                    script_params,
                    frozen_scope.pages_per_combination,
                )
        except Exception as exc:
            reason = "任务证据白箱初始化失败"
            _logger.warning(
                "搜索白箱计划初始化失败 (%s)", type(exc).__name__,
            )
            _fail_flow_submission("whitebox_incomplete", exc)
            try:
                submission_service.mark_whitebox_failure(
                    task_id=task_id, reason=reason,
                )
            except Exception as state_exc:
                _logger.warning(
                    "搜索白箱初始化失败状态写入失败 (%s)",
                    type(state_exc).__name__,
                )
            with ctx.lock:
                task["status"] = "failed"
                task["error"] = reason
            return jsonify({"ok": False, "error": "whitebox_incomplete",
                            "error_reason": reason}), 503
        # Browser activation is part of the running scrape lifecycle.  Mark
        # the queued row running before the activation boundary so a
        # recoverable CDP failure can legally converge to paused and remain
        # resumable in durable storage.
        try:
            ctx.write_run(task_id, status="running", current_stage="scrape")
        except Exception as exc:
            _fail_flow_submission("track_submit_failed", exc)
            with ctx.lock:
                task["status"] = "failed"
                task["error"] = public_flow_message("track_submit_failed")
            return jsonify({
                "ok": False,
                "error": "track_submit_failed",
                "error_code": "track_submit_failed",
                "error_reason": public_flow_message("track_submit_failed"),
            }), 503
        try:
            ctx.activate_run_browser()
        except Exception as exc:
            raw_error_code = str(
                getattr(exc, "error_code", "")
                or getattr(exc, "failed_code", "")
                or "source_cdp_unavailable"
            )
            error_code, reason = _public_failure_details(
                raw_error_code, "", str(platform_raw),
            )
            recoverable = is_recoverable_systemic_block(error_code)
            resume_status = "paused" if recoverable else "failed"
            try:
                if flow_id:
                    _fail_flow_submission(error_code, reason, status=resume_status)
                else:
                    ctx.write_run(
                        task_id,
                        status=resume_status,
                        current_stage="scrape",
                        error_code=error_code,
                        error_reason=reason,
                    )
                if recoverable:
                    ctx.record_pause_failure(
                        task_id, "scrape", error_code, reason,
                        exception=exc, include_traceback=True,
                    )
            except Exception as state_exc:
                if flow_id and isinstance(state_exc, FlowStateClosureError):
                    raise
                _logger.warning(
                    "搜索任务激活失败状态写入失败 (%s)",
                    type(state_exc).__name__,
                )
            with ctx.lock:
                task["status"] = resume_status
                task["error"] = reason
            if not recoverable:
                ctx.clear_auto_screen(task_id)
            if not flow_id:
                _fail_flow_submission(error_code, exc, status=resume_status)
            ctx.schedule_pipeline_task_cleanup(task_id)
            ctx.release_worker_resume_claims(task)
            return jsonify({
                "ok": False,
                "error": error_code,
                "error_code": error_code,
                "status": resume_status,
                "error_reason": reason,
            }), 503
        try:
            future = submission_service.submit_scrape(
                flow_id=flow_id,
                platform=platform_raw,
                task_id=task_id,
                script_params=script_params,
                execution_config=execution_config,
                frozen_scope=frozen_scope,
            )
            if flow_id and career_profile_id:
                attach_scrape_future_failure(
                    future,
                    ctx,
                    task_id=task_id,
                    flow_id=flow_id,
                    platform=platform_raw,
                    profile_id=career_profile_id,
                )
        except Exception as exc:
            reason = "任务执行器未接受任务"
            _fail_flow_submission("submit_failed", exc)
            try:
                submission_service.mark_executor_failure(
                    task_id=task_id,
                    script_params=script_params,
                    reason=reason,
                    pages=frozen_scope.pages_per_combination,
                )
            except Exception as marker_exc:
                _logger.warning(
                    "搜索提交失败白箱记录失败 (%s)",
                    type(marker_exc).__name__,
                )
            with ctx.lock:
                task["status"] = "failed"
                task["error"] = reason
            return jsonify({"ok": False, "error": "submit_failed", "error_reason": reason}), 503
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "platform": platform_raw,
            "config_digest": execution_config.config_digest,
            "scope_digest": frozen_scope.scope_digest,
            "task_input_digest": task_input_digest,
            "task_size": frozen_scope.task_size,
            "browser_account": browser_account,
        })
    register_search_continue(app, ctx)
    register_cancel_execute_search(app, ctx)
