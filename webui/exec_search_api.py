"""搜索执行 API 路由（021 B6 T019 外迁自 webui/app.py）。

搜索范围预览、组合抓取提交/续跑/取消。路由体纯搬运：HTTP 契约零改动；
任务声明 / 断言 / runner 包装经 ctx 取用。
"""

from __future__ import annotations
import threading
import uuid

import hashlib
import json
import sqlite3

from flask import jsonify, request

from webui.constants import (
    _MSG_TASK_ALREADY_RUNNING,
    _MSG_TASK_NOT_FOUND,
    _MSG_UNSUPPORTED_PLATFORM,
    _MSG_USER_STOPPED_SCRAPE,
)
from webui.task_status import _public_task_status
from webui.resume_identity import (
    append_account_switch_log_line,
    ensure_frozen_browser_account,
)
from webui.task_runners import _iso_epoch_ms

from webui.logging_setup import get_logger

_logger = get_logger(__name__)


def register_exec_search_routes(app, ctx):
    @app.route("/api/search-scope/preview", methods=["POST"])
    def search_scope_preview():
        """SPEC011 T004 / tasks005 T402: 后端权威范围预览与校验（平台感知）。

        不改变任务工作量字段；仅返回规范化后的 scope 和去重信息。
        对应 HTTP API POST /api/search-scope/preview。
        """
        from webui.execution_config import CityValidationError, preview_scope
        from webui.location_catalog import LocationCatalogUnavailable
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            validate_platform_key,
        )

        body = request.get_json(silent=True) or {}
        platform_raw = body.get("platform") or "boss"

        # 平台键校验
        try:
            validate_platform_key(platform_raw)
        except UnknownPlatformError:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
            }), 400
        reg = get_platform_or_none(platform_raw)
        if reg is None:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": "平台未注册",
            }), 400
        if not reg.enabled_for_new_tasks:
            return jsonify({
                "ok": False,
                "error_code": "platform_disabled",
                "user_message": reg.availability_reason or "平台暂不可用",
            }), 503

        keywords = body.get("keywords")
        scope_kind = body.get("scope_kind", "cities")
        cities = body.get("cities", [])
        pages_per_combination = body.get("pages_per_combination", 1)
        locations = body.get("locations") or []

        if not isinstance(keywords, list):
            return jsonify({"ok": False, "error": "keywords 必须是数组"}), 400
        if scope_kind not in ("cities", "nationwide"):
            return jsonify({"ok": False, "error": "scope_kind 必须是 cities 或 nationwide"}), 400
        if not isinstance(cities, list):
            return jsonify({"ok": False, "error": "cities 必须是数组"}), 400
        if not isinstance(locations, list):
            return jsonify({"ok": False, "error": "locations 必须是数组"}), 400

        if isinstance(pages_per_combination, bool) or not isinstance(
            pages_per_combination, int
        ):
            return jsonify({"ok": False, "error": "pages_per_combination 必须是整数"}), 400
        pages_int = pages_per_combination

        try:
            result = preview_scope(
                keywords=keywords,
                scope_kind=scope_kind,
                cities=cities,
                pages_per_combination=pages_int,
                locations=locations,
                platform=platform_raw,
            )
            ctx.scope_previews[result["scope"]["scope_digest"]] = dict(result["scope"])
            return jsonify({"ok": True, **result})
        except LocationCatalogUnavailable:
            return jsonify({"ok": False, "error_code": "location_catalog_unavailable", "error": "地点目录暂时不可用，按城市级搜索"}), 503
        except CityValidationError as e:
            return jsonify({
                "ok": False,
                "error_code": "city_validation_failed",
                "error": str(e),
                "details": e.details,
            }), 422
        except ValueError as e:
            return jsonify({
                "ok": False,
                "error_code": "scope_validation_failed",
                "error": str(e),
            }), 422

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
        from webui.pipeline_exec import resolve_browser_account
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            resolve_login_space,
            validate_platform_key,
        )

        body = request.get_json(silent=True) or {}
        script_params = body.get("script_params") or body
        if not isinstance(script_params, dict):
            return jsonify({"ok": False, "error": "无效的请求体"}), 400
        if not script_params.get("keyword") or not script_params.get("city"):
            return jsonify({"ok": False, "error": "缺少关键词或城市"}), 400
        locations = script_params.get("locations") or []
        if not isinstance(locations, list):
            return jsonify({"ok": False, "error": "locations 必须是数组"}), 400

        # B031: 一键链路标记；auto_screen_fields/profile 只作为刷新恢复快照，
        # 不进 script_params，不触碰搜索请求的 AI filters 校验。
        auto_screen = bool(body.get("auto_screen"))
        auto_screen_fields = body.get("auto_screen_fields") if auto_screen else {}
        if auto_screen and not isinstance(auto_screen_fields, dict):
            return jsonify({"ok": False, "error": "auto_screen_fields 必须是对象"}), 400
        auto_screen_profile = str(body.get("auto_screen_profile") or "") if auto_screen else ""
        auto_screen_facts = (
            body.get("auto_screen_facts")
            if auto_screen and isinstance(body.get("auto_screen_facts"), dict)
            else None
        )

        # B033/B038：普通抓取也冻结画像，刷新后单独查看结果可恢复三通道输入。
        profile_summary = str(body.get("profile_summary") or "")
        raw_profile_facts = body.get("profile_facts")
        profile_facts = (
            raw_profile_facts
            if isinstance(raw_profile_facts, dict) else None
        )

        # T402: 平台键校验（先于任何副作用）
        platform_raw = body.get("platform") or "boss"
        try:
            validate_platform_key(platform_raw)
        except UnknownPlatformError:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
            }), 400
        reg = get_platform_or_none(platform_raw)
        if reg is None:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": "平台未注册",
            }), 400

        # T402: 非空 AI filters 拒绝（零副作用，先于租约和 scope 检查）
        offending = [
            k for k in _AI_FILTER_KEYS
            if k in script_params and _is_non_empty_filter_value(script_params[k])
        ]
        if offending:
            return jsonify({
                "ok": False,
                "error_code": "search_filters_not_supported",
                "user_message": "搜索请求不允许携带非空 AI filters: " + ", ".join(sorted(offending)),
            }), 422

        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp

        # 逻辑隔离：同一时间只允许一个 pipeline 任务占用浏览器（B031 回归）。
        if ctx.browser_busy():
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前已有任务在运行或暂停，请先等待、继续或结束任务后再开始新任务",
            }), 409

        requested_digest = str(body.get("scope_digest") or "")
        scope_payload = ctx.scope_previews.get(requested_digest) if requested_digest else None
        if requested_digest and scope_payload is None:
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
                return jsonify({"ok": False, "error_code": "location_catalog_unavailable", "error": "地点目录暂时不可用，按城市级搜索"}), 503
            except (TypeError, ValueError) as exc:
                return jsonify({
                    "ok": False, "error_code": "scope_validation_failed",
                    "error": str(exc),
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
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({
                "ok": False, "error_code": "config_resolution_failed",
                "error": str(exc),
            }), 422

        # T402: 平台一致性校验
        if frozen_scope.platform != platform_raw:
            return jsonify({
                "ok": False,
                "error_code": "scope_platform_mismatch",
                "user_message": "请求平台与搜索范围平台不一致",
            }), 409

        # T402: 平台禁用检查（在 scope 平台不匹配之后）
        if not reg.enabled_for_new_tasks:
            return jsonify({
                "ok": False,
                "error_code": "platform_disabled",
                "user_message": reg.availability_reason or "平台暂不可用",
            }), 503

        # T402: script_params 与 scope 一致性校验
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
            return jsonify({"ok": False, "error_code": "location_catalog_unavailable", "error": "地点目录暂时不可用，按城市级搜索"}), 503
        except ValueError as exc:
            return jsonify({
                "ok": False,
                "error_code": "location_validation_failed",
                "error": str(exc),
            }), 422
        # pages 未显式提供时不校验（后端用 scope 冻结值覆盖）
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

        # T402: 冻结完整 runtime — 平台登录空间、task_input_digest
        # B073：BOSS 列表/广泛抓取阶段按 R1 角色解析账号（未指定/不可用降级当前账号）；
        # 智联平台不受角色影响，保持当前账号。
        if platform_raw == "boss":
            from webui.pipeline_exec import account_for_role
            browser_account = account_for_role(
                "R1", app.config["BROWSER_ACCOUNTS_PATH"],
                fallback=ctx.account_for_run(),
            )
        else:
            browser_account = ctx.account_for_run()
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
        task = ctx.register_pipeline_task(task_id, "scrape")
        # 把冻结配置摘要存入任务记录，供进度查询返回
        with ctx.lock:
            task["config_digest"] = execution_config.config_digest
            task["scope_digest"] = frozen_scope.scope_digest
            task["browser_account"] = browser_account
            task["platform"] = platform_raw
            task["cdp_port"] = login_space.cdp_port
            task["profile_key"] = login_space.profile_key
            task["task_input_digest"] = task_input_digest
            task["auto_screen"] = auto_screen
        # T402: 搜索 run 的 frozen_filters 为空，筛选快照为空
        ctx.store.create_screening_run(
            task_id,
            frozen_filters={},
            source_count=frozen_scope.combination_count,
            execution_params={
                "platform": platform_raw,
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
                "profile_summary": profile_summary,
                "profile_facts": profile_facts,
                # 030：创建时全局当前账号快照，续跑判定"用户是否主动换号"用
                "active_account_at_freeze": ctx.account_for_run(),
            },
            backend_version=ctx.backend_version,
        )
        # T402: 持久化平台、空筛选快照和 task_input_digest
        ctx.store.save_filter_snapshot(
            task_id,
            platform=platform_raw,
            filter_schema_version=None,
            filter_snapshot={},
            task_input_digest=task_input_digest,
        )
        ctx.activate_run_browser()
        try:
            ctx.executor.submit(
                ctx.run_pipeline_task, task_id, script_params,
                execution_config, frozen_scope,
            )
        except RuntimeError:
            ctx.store.update_screening_run(
                task_id, status="failed", error_code="submit_failed",
                error_reason="任务执行器未接受任务",
            )
            raise
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

    @app.route("/api/execute-search/continue/<old_task_id>", methods=["POST"])
    def continue_execute_search(old_task_id, _block_checked=False,
                                account_switch_note=None):
        """断点续抓：从上次失败的组合接着跑，跳过已完成的组合。

        切片4：支持 paused 状态继续（FR-020）。优先从 DB checkpoint 恢复
        completed_combos（服务重启后内存丢失也能恢复），回退到内存 task.result。
        同时检查阻断是否解除（如登录已恢复、验证码已过）。

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp
        # 1) 优先从内存读 old_task
        with ctx.lock:
            old_task = ctx.tasks.get(old_task_id)
            old_snapshot = dict(old_task) if old_task else None
        # 2) 内存没有则从 DB 读（服务重启恢复场景）
        db_run = None
        try:
            db_run = ctx.store.get_screening_run(old_task_id)
        except ctx.operational_errors:
            db_run = None
        if old_snapshot is None and db_run is None:
            return jsonify({"ok": False, "error": "原任务不存在或已过期"}), 404
        # DB 是服务重启后仍存在的状态权威；取消/失败均为终态，不得复活。
        mem_status = old_snapshot.get("status") if old_snapshot else None
        db_status = db_run.get("status") if db_run else None
        effective_status = db_status or mem_status
        if effective_status != "paused":
            return jsonify({
                "ok": False,
                "error": "not_paused",
                "status": _public_task_status(effective_status),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
        if db_run is not None and not _block_checked:
            passed, code, reason = ctx.check_resume_block(db_run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
        if db_run is not None:
            # 030：缺冻结账号时回退填充收口到续跑身份域（口径不变：沿用当前全局账号）
            ensure_frozen_browser_account(
                ctx.store, old_task_id, db_run,
                platform=str((db_run.get("execution_params") or {}).get("platform") or "boss"),
                fallback_account=ctx.account_for_run(db_run))
        # 3) 收集 script_params（内存优先，DB 兜底）
        script_params = (old_snapshot or {}).get("script_params")
        if not script_params and db_run:
            try:
                ep = db_run.get("execution_params") or {}
                script_params = ep.get("script_params") or ep
            except (AttributeError, TypeError):
                script_params = None
        if not script_params:
            return jsonify({"ok": False, "error": "原任务参数丢失，无法继续"}), 400
        # 4) 收集 completed_combos：DB checkpoint 优先（持久），内存 result 兜底
        completed: set[str] = set()
        try:
            completed = ctx.store.load_checkpoint(old_task_id, "scrape")
        except ctx.operational_errors:
            completed = set()
        if not completed and old_snapshot:
            old_result = old_snapshot.get("result") or {}
            completed = set(old_result.get("completed_combos") or [])
        try:
            old_jobs = ctx.store.load_scrape_run_jobs(old_task_id)
        except ctx.operational_errors:
            old_jobs = []
        if not old_jobs and old_snapshot:
            old_result = old_snapshot.get("result") or {}
            old_jobs = old_result.get("jobs") or []
        if not ctx.claim_resume(old_task_id):
            return jsonify({
                "ok": False, "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        task_id = old_task_id
        claimed_task, previous_task = ctx.claim_pipeline_task_id(
            task_id, "scrape",
            started_at=_iso_epoch_ms((db_run or {}).get("started_at")),
        )
        if claimed_task is None:
            ctx.release_resume_claim(old_task_id)
            return jsonify({
                "ok": False, "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        if account_switch_note:
            # 030 FR-005：自动换号在续跑启动日志留一行中文说明
            append_account_switch_log_line(
                claimed_task,
                from_account=account_switch_note[0],
                to_account=account_switch_note[1])
        # 把续抓信息存进 task，ctx.run_pipeline_task 会读取
        # T403: 从 DB 恢复冻结 runtime（platform/cdp_port/profile_key/
        # task_input_digest），不读当前 UI 或活动账号
        db_ep = (db_run or {}).get("execution_params") or {}
        # 高级设置续跑生效：从 DB 读取刷新后的 execution_config/frozen_scope，
        # 传给 ctx.run_pipeline_task（frozen_scope 保持冻结，只 pages 不变）。
        from webui.execution_config import (
            ExecutionConfigSnapshot,
            FrozenTaskScope,
        )
        resume_config = None
        resume_scope = None
        try:
            if db_ep.get("execution_config"):
                resume_config = ExecutionConfigSnapshot.from_dict(db_ep["execution_config"])
            if db_ep.get("frozen_scope"):
                resume_scope = FrozenTaskScope.from_dict(db_ep["frozen_scope"])
        except (KeyError, TypeError, ValueError):
            resume_config = None
            resume_scope = None
        with ctx.lock:
            task = ctx.tasks[task_id]
            task["skip_combos"] = completed
            task["old_jobs"] = old_jobs
            task["resuming_from"] = old_task_id
            task["browser_account"] = (
                db_ep.get("browser_account") or ctx.account_for_run(db_run)
            )
            task["platform"] = db_ep.get("platform") or "boss"
            task["cdp_port"] = db_ep.get("cdp_port")
            task["profile_key"] = db_ep.get("profile_key")
            task["task_input_digest"] = db_ep.get("task_input_digest")
            task["auto_screen"] = bool(db_ep.get("auto_screen"))
        start_gate = threading.Event()
        abort_start = threading.Event()

        def run_after_claim_commits():
            start_gate.wait()
            if not abort_start.is_set():
                ctx.run_pipeline_task(task_id, script_params, resume_config, resume_scope)

        try:
            future = ctx.executor.submit(run_after_claim_commits)
            # 事件与 DB claim 都在 worker 放行前完成；继续沿用同一 task_id，
            # 避免把内部 handoff 暴露成非 canonical 的 resumed 状态。
            if db_run is not None:
                ctx.store.append_task_event(old_task_id, "resume", {"task_id": task_id})
                if not ctx.store.claim_paused_screening_run(old_task_id):
                    raise RuntimeError("resume_already_claimed")
            with ctx.lock:
                if ctx.tasks.get(task_id) is claimed_task:
                    claimed_task["status"] = "running"
        except (sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            ctx.release_resume_claim(old_task_id)
            return jsonify({
                "ok": False, "error": "resume_submit_failed",
                "message": f"继续任务提交失败：{type(exc).__name__}",
            }), 500
        start_gate.set()
        return jsonify({"ok": True, "task_id": task_id,
                        "skipped": len(completed), "old_jobs": len(old_jobs),
                        "resumed_from": old_task_id})

    @app.route("/api/execute-search/<task_id>/cancel", methods=["POST"])
    def cancel_execute_search(task_id):
        """停止正在运行的抓取任务。

        做法：set stop_event → 立刻关调试 Chrome（不等当前组合抓完）→
        task 标 cancelled。run_search 会因浏览器被关而退出，ctx.run_pipeline_task
        看到 stop_event.is_set() 后标 cancelled 而非 failed/done。
        """
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            if task["status"] not in ("queued", "running"):
                return jsonify({"ok": False, "error": f"任务已结束，无法取消（当前状态：{task['status']}）"}), 400
            stop_event = task.get("stop_event")
            if stop_event is not None:
                stop_event.set()
            # 立刻标记 cancelled，让前端轮询马上看到状态变化
            task["status"] = "cancelled"
            task["error"] = _MSG_USER_STOPPED_SCRAPE
            task["logs"].append("用户取消任务")
            cancel_platform = task.get("platform")
        # 关浏览器放到锁外，避免持锁时间过长。best-effort，失败不阻塞取消。
        try:
            from webui.pipeline_exec import close_debug_chrome
            close_debug_chrome()
        except Exception:
            _logger.warning("调试 Chrome 关闭失败（不影响本次响应）", exc_info=True)

        ctx.clear_auto_screen(task_id)
        # T412 契约 http-api.md L223-229：DB run 存在时以 DB platform 为权威；
        # 仅 DB 创建前内存窗口用注册 task 的不可变平台快照。
        if not cancel_platform:
            try:
                _db_run = ctx.store.get_screening_run(task_id)
                cancel_platform = (_db_run or {}).get("platform")
            except ctx.operational_errors:
                pass
        return jsonify({
            "ok": True, "run_id": task_id, "task_id": task_id,
            "platform": cancel_platform, "status": "cancelled",
        })

    # 021 B6：路由函数回传 ctx，供 app.py 内续跑分发调用
    ctx.continue_execute_search = continue_execute_search
