"""AI 筛选提交 / 取消 API 路由（021 B6 T019 外迁自 webui/app.py）。

筛选任务提交（含断点续跑识别）与取消。路由体纯搬运：HTTP 契约零改动；
任务声明 / runner 包装经 ctx 取用。
"""
from __future__ import annotations
import uuid
import hashlib
import json
from flask import jsonify, request
from webui.constants import (
    _MSG_TASK_ALREADY_RUNNING,
    _MSG_TASK_NOT_FOUND,
    _MSG_USER_STOPPED_SCREEN,
)
from webui.resume_identity import (
    activate_frozen_identity_candidate,
)
from webui.task_runners import _iso_epoch_ms
from webui.task_pause_support import STOP_MODE_CANCEL, request_stop
from webui.logging_setup import get_logger
from webui.flow_ai_coordinator import FlowAiCoordinator
from webui.flow_task_state import register_flow_state_error_handler

_logger = get_logger(__name__)

from webui.flow_ai_coordinator import enqueue_auto_screen_for_scrape as _enqueue_auto_screen_for_scrape

def enqueue_auto_screen_for_scrape(app, ctx, scrape_task_id: str):
    """Compatibility facade for the production Flow auto-screen callback."""
    return _enqueue_auto_screen_for_scrape(app, ctx, scrape_task_id)


def register_ai_screen_routes(app, ctx):
    register_flow_state_error_handler(app)
    default_flow_ai = FlowAiCoordinator(ctx)

    def _get_flow_ai():
        return getattr(ctx, "flow_ai_coordinator", None) or default_flow_ai

    def _fail_flow_track(
        flow_id,
        platform,
        profile_id,
        error_code,
        *,
        task_id=None,
        scrape_task_id=None,
        reason="",
        status="failed",
    ):
        if not flow_id or not profile_id:
            return
        flow_ai = _get_flow_ai()
        return flow_ai.fail_track(
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            task_id=task_id,
            scrape_task_id=scrape_task_id or task_id,
            error_code=error_code,
            reason=reason,
            status=status,
        )

    def _close_flow_ai_start_failure(
        flow_ai,
        *,
        flow_id,
        platform,
        profile_id,
        task_id,
        scrape_task_id,
        error_code,
        reason,
    ):
        if not flow_id:
            return None
        from webui.error_registry import is_recoverable_systemic_block

        closure_status = (
            "paused"
            if is_recoverable_systemic_block(str(error_code or ""))
            or str(error_code or "") in {"source_cdp_unavailable", "browser_busy"}
            else "failed"
        )
        return flow_ai.mark_submission_failed(
            task_id=task_id,
            scrape_task_id=scrape_task_id,
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            error_code=error_code,
            reason=reason,
            status=closure_status,
        )

    @app.route("/api/ai-screen/<task_id>/cancel", methods=["POST"])
    def cancel_ai_screen(task_id):
        """停止正在运行的 AI 筛选任务。

        与抓取取消同套路但按 kind 区分：纯 AI 调用阶段（粗筛/精筛）没有
        浏览器可关，close_debug_chrome 是 no-op；抓 JD 阶段关浏览器可让
        子进程抓取立即中断。工作线程在阶段边界看到 stop_event 后标
        cancelled，不会把结果覆盖成 done。
        """
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            if task.get("kind") != "ai_screen":
                return jsonify({"ok": False, "error": "不是 AI 筛选任务"}), 409
            if task["status"] not in ("queued", "running"):
                return jsonify({"ok": False, "error": f"任务已结束，无法取消（当前状态：{task['status']}）"}), 400
            stop_event = task.get("stop_event")
            cancel_platform = task.get("platform")
            task_snapshot = dict(task)

        # Durable Run/Track state is the acknowledgement boundary.  Do not
        # mark the process-local task cancelled until both projections accept
        # the same operation.
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            MissingPlatformIdentityError,
            persist_task_cancelled,
        )
        try:
            persisted_run = persist_task_cancelled(ctx, task_id)
        except MissingPlatformIdentityError:
            return jsonify({
                "ok": False,
                "run_id": task_id,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法取消关联运行线",
            }), 409
        except FlowTaskOperationError:
            return jsonify({
                "ok": False,
                "run_id": task_id,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "任务取消失败，请刷新任务状态后重试",
            }), 503

        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            if stop_event is not None:
                request_stop(task, stop_event, STOP_MODE_CANCEL)
            task["status"] = "cancelled"
            task["error"] = _MSG_USER_STOPPED_SCREEN
            task.setdefault("logs", []).append("用户取消任务")
            cancel_platform = task.get("platform") or cancel_platform
        # 关浏览器放到锁外（仅抓 JD 阶段有意义），但必须按任务冻结
        # 身份绑定；智联缺身份时不能落到 BOSS 默认端口。
        from webui import pipeline_exec as _facade
        from webui.frozen_browser_identity import cleanup_frozen_task_browser
        cleanup = cleanup_frozen_task_browser(
            ctx.store, task_id, task,
            accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
            activate=_facade.set_active_cdp_data_dir,
            close=_facade.close_debug_chrome,
        )
        if not cleanup.ok:
            with ctx.lock:
                current = ctx.tasks.get(task_id)
                if current is not None:
                    current["error"] = "用户已取消，但浏览器清理失败"

        # T412 契约 http-api.md L223-229：DB run 存在时以 DB platform 为权威；
        # 仅 DB 创建前内存窗口用注册 task 的不可变平台快照。
        if not cancel_platform:
            try:
                _db_run = ctx.store.get_screening_run(task_id)
                cancel_platform = (_db_run or {}).get("platform")
            except ctx.operational_errors:
                pass
        return jsonify({
            "ok": cleanup.ok,
            **({"error": "browser_cleanup_failed"}
               if not cleanup.ok else {}),
            "run_id": task_id, "task_id": task_id,
            "platform": cancel_platform, "status": "cancelled",
            "cleanup": cleanup.as_dict(),
        })

    @app.route("/api/ai-screen", methods=["POST"])
    def ai_screen():
        """Stage 3b：对已抓取的原始岗位做两段式 AI 筛选。

        T406-T407: 接收 platform 做一致性校验，从父搜索 run 继承平台/scope/
        runtime，保存字段稳定值和当时标签的完整筛选快照。

        SPEC011 T015: 实验租约持有时拒绝启动（FR-035）。
        """
        flow_ai = _get_flow_ai()
        body = request.get_json(silent=True) or {}
        screening_fields = body.get("screening_fields") or {}
        profile_summary = str(body.get("profile_summary") or "")
        # 019：跨平台去重开关（缺省开）；续跑沿用冻结值。
        cross_platform_dedupe = bool(body.get("cross_platform_dedupe", True))
        profile_facts = body.get("profile_facts")
        if not isinstance(profile_facts, dict) or not profile_facts:
            profile_facts = None
        scrape_task_id = str(body.get("scrape_task_id") or "").strip()
        request_platform = str(body.get("platform") or "").strip() or None
        filter_schema_version = body.get("filter_schema_version")
        # B031: 一键自动接续在进入现有校验前消费标记，失败也不会刷新重试。
        if bool(body.get("consume_auto_screen")) and scrape_task_id:
            ctx.consume_auto_screen(scrape_task_id)
        if not isinstance(screening_fields, dict):
            return jsonify({"ok": False, "error": "无效的筛选字段"}), 400
        if not scrape_task_id:
            return jsonify({"ok": False, "error": "缺少 scrape_task_id"}), 400
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp
        source_snapshot = ctx.ensure_scrape_source(scrape_task_id)
        if source_snapshot is None:
            return jsonify({"ok": False, "error": "抓取任务不存在"}), 404
        if source_snapshot.get("kind") != "scrape":
            return jsonify({"ok": False, "error": "来源任务不是抓取任务"}), 409
        source_result = source_snapshot.get("result")
        if (
            source_snapshot.get("status") != "done"
            or not isinstance(source_result, dict)
            or (not source_result.get("ok") and not source_result.get("jobs"))
        ):
            return jsonify({"ok": False, "error": "抓取任务尚未成功完成"}), 409
        try:
            parent_flow_params = (
                (flow_ai.get_screening_run(scrape_task_id) or {})
                .get("execution_params") or {}
            )
        except ctx.operational_errors:
            parent_flow_params = {}
        parent_flow_id = str(parent_flow_params.get("flow_id") or "").strip() or None
        parent_track_id = str(parent_flow_params.get("track_id") or "").strip() or None
        flow_binding = None
        from webui.flow_task_coordinator import (
            FlowTaskOperationError,
            MissingPlatformIdentityError,
            resolve_flow_binding,
        )

        try:
            flow_binding = resolve_flow_binding(ctx, scrape_task_id)
        except MissingPlatformIdentityError:
            return jsonify({
                "ok": False,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "来源抓取任务缺少平台身份，无法开始 AI 筛选",
                "status": "paused",
            }), 409
        except FlowTaskOperationError:
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "来源抓取任务的流程绑定无效",
            }), 409
        if flow_binding is not None:
            if parent_flow_id and parent_flow_id != flow_binding["flow_id"]:
                return jsonify({
                    "ok": False,
                    "error": "flow_task_operation_failed",
                    "error_code": "flow_task_operation_failed",
                    "message": "来源抓取任务的流程绑定无效",
                }), 409
            parent_flow_id = flow_binding["flow_id"]
            parent_track_id = flow_binding["track_id"]
        elif parent_flow_id:
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "来源抓取任务未绑定流程运行线",
            }), 409

        # T406/T417: 从父抓取 run 的冻结身份组装完整子任务登录空间。
        parent_identity = flow_ai.resolve_source_identity(
            scrape_task_id,
            fallback_account=ctx.account_for_run,
            accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
            operational_errors=ctx.operational_errors,
            source_platform=(source_snapshot.get("platform") or request_platform),
        )
        parent_platform = str(
            (flow_binding or {}).get("platform")
            or parent_identity.get("platform")
            or source_snapshot.get("platform")
            or "boss"
        )
        parent_identity["platform"] = parent_platform
        missing_identity = [
            key for key in ("platform", "browser_account", "cdp_port", "profile_key")
            if parent_identity.get(key) in (None, "")
        ]
        if missing_identity:
            return jsonify({
                "ok": False, "error": "missing_frozen_identity",
                "message": "来源抓取任务缺少冻结的账号或浏览器身份，无法安全开始 AI 筛选",
                "status": "paused", "missing_fields": missing_identity,
            }), 409
        # 客户端显式 platform 与父平台不一致
        if request_platform and request_platform != parent_platform:
            return jsonify({
                "ok": False, "error": "parent_platform_mismatch",
                "message": "客户端平台与父搜索 run 平台不一致",
                "parent_platform": parent_platform,
            }), 409
        # T407: 校验 filter_schema_version
        parent_schema = parent_identity.get("filter_schema_version") if parent_identity else None
        if (filter_schema_version is not None and parent_schema is not None
                and int(filter_schema_version) != int(parent_schema)):
            return jsonify({
                    "ok": False, "error": "filter_schema_version_mismatch",
                    "message": "筛选 schema 版本与父 run 不一致",
            }), 409
        # 平台禁用检查
        from webui.platforms import get_platform_or_none
        platform_info = get_platform_or_none(parent_platform)
        if platform_info is not None and not platform_info.enabled_for_new_tasks:
            return jsonify({"ok": False, "error": "platform_disabled"}), 503
        # 同一抓取任务只允许一个 AI 筛选工作线程；防止多标签页重复提交。
        with ctx.lock:
            for existing_id, existing in ctx.tasks.items():
                if (existing.get("kind") == "ai_screen"
                        and existing.get("source_task_id") == scrape_task_id
                        and existing.get("status") in ("queued", "running")):
                    return jsonify({
                        "ok": False, "error": "already_running",
                        "existing_task_id": existing_id,
                        "message": "同一抓取任务已有 AI 筛选在运行",
                    }), 409
        task_id = uuid.uuid4().hex

        # Legacy single-run AI keeps the process-wide browser gate.  Flow
        # Tracks use their platform lane and are intentionally independent.
        if not parent_flow_id and ctx.has_active_pipeline_task():
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前已有任务在运行或暂停，请先等待、继续或结束任务后再开始新任务",
            }), 409

        # paused 就地继续；服务重启打断的 interrupted（error_code=restart）
        # 也可以被“重新开始 AI 筛选”继承断点，但保留旧 run 的终态记录。
        resume_from_run_id = ""
        prev = None
        if profile_facts is None:
            try:
                parent_run = flow_ai.get_screening_run(scrape_task_id)
            except ctx.operational_errors:
                parent_run = None
            parent_facts = ((parent_run or {}).get("execution_params") or {}).get("profile_facts")
            if isinstance(parent_facts, dict) and parent_facts:
                profile_facts = parent_facts
        try:
            from webui.screen_flow import find_resumable_screen_run
            prev = find_resumable_screen_run(
                ctx.store, scrape_task_id, screening_fields,
                profile_summary, profile_facts,
            )
        except ctx.operational_errors as exc:
            return jsonify({
                "ok": False,
                "error": "resume_state_unavailable",
            }), 503
        if prev is not None:
            resume_from_run_id = prev["id"]
        # Spec041：AI 筛选任务继承画像身份（续跑取被续跑 run，新建取父抓取 run）。
        task_profile_id = ""
        if prev is not None:
            task_profile_id = str(prev.get("profile_id") or "")
        if not task_profile_id:
            try:
                _parent_run_row = flow_ai.get_screening_run(scrape_task_id)
            except ctx.operational_errors:
                _parent_run_row = None
            task_profile_id = str((_parent_run_row or {}).get("profile_id") or "")
        task_profile_id = task_profile_id or None
        candidate_base = dict(prev or {})
        candidate_base.update({
            "kind": "ai_screen",
            "source_task_id": scrape_task_id,
        })
        activation = activate_frozen_identity_candidate(
            ctx.activate_run_browser,
            candidate_base,
            parent_identity,
        )
        if not activation["ok"]:
            error_code = activation.get("error_code") or activation.get("error")
            _close_flow_ai_start_failure(
                flow_ai,
                flow_id=parent_flow_id,
                platform=parent_platform,
                profile_id=task_profile_id,
                task_id=task_id,
                scrape_task_id=scrape_task_id,
                error_code=error_code,
                reason=activation.get("message") or "AI 筛选登录空间激活失败",
            )
            return jsonify({
                "ok": False,
                "error": activation["error"],
                "error_code": activation["error_code"],
                "status": activation["status"],
                "message": activation["message"],
            }), 409
        # A resumed paused run may predate frozen browser fields.  The
        # candidate has already been activated successfully; persist the
        # inherited identity before claiming the run so retries cannot lose
        # the platform-specific login space.
        if resume_from_run_id and prev is not None:
            try:
                flow_ai.persist_resume_identity(resume_from_run_id, parent_identity)
            except Exception as exc:
                _close_flow_ai_start_failure(
                    flow_ai,
                    flow_id=parent_flow_id,
                    platform=parent_platform,
                    profile_id=task_profile_id,
                    task_id=resume_from_run_id,
                    scrape_task_id=scrape_task_id,
                    error_code="source_cdp_unavailable",
                    reason="筛选任务登录空间未能保存",
                )
                return jsonify({
                    "ok": False,
                    "error": "ai_screen_identity_persist_failed",
                    "error_code": "source_cdp_unavailable",
                    "status": "paused",
                    "message": "筛选任务登录空间未能保存，任务保持暂停，请重试",
                }), 503
        if resume_from_run_id and prev is not None and prev["status"] == "paused":
            # paused run 就地转为 running，保持唯一任务身份和 canonical 状态。
            try:
                claimed = flow_ai.claim_paused_resume(resume_from_run_id)
            except ctx.operational_errors as exc:
                return jsonify({
                    "ok": False,
                    "error": "resume_claim_failed",
                }), 503
            if not claimed:
                return jsonify({
                    "ok": False,
                    "error": "resume_already_claimed",
                }), 409
            task_id = resume_from_run_id
        claimed_old_resume = False
        if (resume_from_run_id and prev is not None
                and prev["status"] != "paused"):
            if not ctx.claim_resume(resume_from_run_id):
                return jsonify({
                    "ok": False, "error": "already_running",
                    "message": _MSG_TASK_ALREADY_RUNNING,
                }), 409
            claimed_old_resume = True
        try:
            claimed_task, previous_task = ctx.claim_pipeline_task_id(
                task_id, "ai_screen",
                started_at=(
                    _iso_epoch_ms((prev or {}).get("started_at"))
                    if resume_from_run_id and prev is not None else None
                ),
            )
        except Exception as exc:
            _fail_flow_track(
                parent_flow_id, parent_platform, task_profile_id,
                "flow_ai_start_failed", task_id=task_id,
                scrape_task_id=scrape_task_id, reason=type(exc).__name__,
            )
            return jsonify({
                "ok": False,
                "error": "flow_ai_start_failed",
                "error_code": "flow_ai_start_failed",
                "message": "AI 筛选启动失败",
            }), 503
        if claimed_task is None:
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                flow_ai.restore_paused_resume(resume_from_run_id)
            if claimed_old_resume:
                ctx.release_resume_claim(resume_from_run_id)
            return jsonify({
                "ok": False, "error": "already_running",
            }), 409
        claimed_task["source_task_id"] = scrape_task_id
        account_source = prev if resume_from_run_id else None
        if resume_from_run_id:
            claimed_task["resumed_from"] = resume_from_run_id
        claimed_task.update(parent_identity)
        claimed_task["profile_id"] = task_profile_id
        # 030：新建路径把创建时全局当前账号随任务透传给 runner 落库为快照
        # （runner 的 INSERT OR REPLACE 会覆盖 API 预建行，快照必须随之写入）
        claimed_task["active_account_at_freeze"] = ctx.account_for_run()
        # T407: 生成 AI 阶段 task_input_digest
        ai_digest = hashlib.sha256(json.dumps({
            "platform": parent_platform,
            "scrape_task_id": scrape_task_id,
            "filter_schema_version": filter_schema_version,
            "screening_fields": {k: sorted(v) if isinstance(v, list) else v
                                 for k, v in screening_fields.items()},
            "browser_account": claimed_task.get("browser_account"),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        claimed_task["task_input_digest"] = ai_digest
        if resume_from_run_id and prev is not None:
            resume_params = dict(prev.get("execution_params") or {})
            # 019：去重开关随链上冻结值沿用（重启/断链续跑）。
            cross_platform_dedupe = bool(
                resume_params.get("cross_platform_dedupe", cross_platform_dedupe))
            # 030：缺冻结账号时按角色感知兜底（BOSS=R2，智联=当前账号），
            # 与统一继续接口同口径，消除两条续跑路径的账号分歧。
            flow_ai.inherit_frozen_account(
                resume_from_run_id, prev,
                platform=parent_platform,
                fallback_account=ctx.account_for_run(prev),
                accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
                role="R2")
        # T407: 创建 AI run 时保存平台身份和筛选快照
        if not resume_from_run_id:
            try:
                flow_ai.create_screening_run(
                    task_id=task_id,
                    screening_fields=screening_fields,
                    source_count=0,
                    profile_id=task_profile_id,
                    platform=parent_platform,
                    filter_schema_version=filter_schema_version,
                    profile_summary=profile_summary,
                    profile_facts=profile_facts,
                    scrape_task_id=scrape_task_id,
                    flow_id=parent_flow_id,
                    track_id=parent_track_id,
                    browser_account=claimed_task.get("browser_account"),
                    cdp_port=claimed_task.get("cdp_port"),
                    profile_key=claimed_task.get("profile_key"),
                    task_input_digest=ai_digest,
                    cross_platform_dedupe=cross_platform_dedupe,
                )
            except Exception as exc:
                ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
                flow_ai.mark_submission_failed(
                    task_id=task_id,
                    reason="AI 筛选初始化失败",
                    error_code="ai_screen_persist_failed",
                    flow_id=parent_flow_id,
                    scrape_task_id=scrape_task_id,
                    platform=parent_platform,
                    profile_id=task_profile_id,
                )
                with ctx.lock:
                    claimed_task["status"] = "failed"
                    claimed_task["error"] = "AI 筛选初始化失败"
                return jsonify({
                    "ok": False,
                    "error": "ai_screen_persist_failed",
                }), 503
        # B096: when this source belongs to a Flow, promote only its Track to
        # the AI stage.  Legacy callers without a Flow keep the old route.
        flow_id = parent_flow_id
        # 首次提交和就地续跑都要把这条 AI run 绑回轨道：轨道级暂停/恢复只有拿到
        # screen_run_id 才有可操作对象。接管式续跑的新 run 行还不存在，由 worker
        # 落库时补绑（见 webui/runners/ai_screen_task.py）。
        binds_at_submission = (not resume_from_run_id) or task_id == resume_from_run_id
        if binds_at_submission and flow_id and task_profile_id and flow_ai.flow_service is not None:
            try:
                flow_ai.claim_track(
                    flow_id=flow_id,
                    platform=parent_platform,
                    profile_id=task_profile_id,
                    screen_run_id=task_id,
                )
            except Exception as exc:  # noqa: BLE001 - preserve durable failure
                from webui.flow_service import FlowConflictError
                ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
                flow_ai.mark_submission_failed(
                    task_id=task_id,
                    reason="AI 筛选启动失败",
                    error_code="flow_ai_start_failed",
                    flow_id=flow_id,
                    scrape_task_id=scrape_task_id,
                    platform=parent_platform,
                    profile_id=task_profile_id,
                )
                conflicted = isinstance(exc, FlowConflictError)
                return jsonify({
                    "ok": False,
                    "error": "flow_ai_track_busy" if conflicted else "flow_ai_track_bind_failed",
                    "error_code": "flow_ai_track_busy" if conflicted else "flow_ai_track_bind_failed",
                    "message": (
                        "这条运行线已经有筛选任务在跑，请刷新后查看"
                        if conflicted else
                        "筛选任务没能挂到这条运行线上，请刷新后重新发起筛选"
                    ),
                }), 409 if conflicted else 503
        _ai_units = [("ai_rough", "ai_rough"), ("jd_detail", "jd_detail"), ("ai_fine", "ai_fine")]
        try:
            flow_ai.begin_whitebox(
                task_id=task_id,
                scrape_task_id=scrape_task_id,
            )
        except Exception as exc:
            reason = "任务证据白箱初始化失败"
            _logger.warning(
                "AI whitebox initialization failed (%s)", type(exc).__name__,
            )
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            if claimed_old_resume:
                ctx.release_resume_claim(resume_from_run_id)
            flow_ai.mark_submission_failed(
                task_id=task_id,
                reason=reason,
                error_code="whitebox_incomplete",
                flow_id=flow_id,
                scrape_task_id=scrape_task_id,
                platform=parent_platform,
                profile_id=task_profile_id,
            )
            with ctx.lock:
                claimed_task["status"] = "failed"
                claimed_task["error"] = reason
            return jsonify({"ok": False, "error": "whitebox_incomplete",
                            "error_reason": reason}), 503
        try:
            if flow_id:
                flow_ai.submit_flow(
                    flow_id=flow_id,
                    platform=parent_platform,
                    task_id=task_id,
                    screening_fields=screening_fields,
                    profile_summary=profile_summary,
                    scrape_task_id=scrape_task_id,
                    resume_from_run_id=resume_from_run_id,
                    profile_facts=profile_facts,
                    cross_platform_dedupe=cross_platform_dedupe,
                )
            else:
                ctx.executor.submit(
                    ctx.run_ai_screen_task,
                    task_id,
                    screening_fields,
                    profile_summary,
                    scrape_task_id,
                    resume_from_run_id,
                    profile_facts,
                    cross_platform_dedupe=cross_platform_dedupe,
                )
        except Exception as exc:
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                flow_ai.restore_paused_resume(resume_from_run_id)
            if claimed_old_resume:
                ctx.release_resume_claim(resume_from_run_id)
            reason = "AI 筛选后台任务提交失败"
            flow_ai.mark_submission_failed(
                task_id=task_id,
                reason=reason,
                error_code="submit_failed",
                flow_id=flow_id,
                scrape_task_id=scrape_task_id,
                platform=parent_platform,
                profile_id=task_profile_id,
            )
            with ctx.lock:
                claimed_task["status"] = "failed"
                claimed_task["error"] = reason
            return jsonify({"ok": False, "error": "submit_failed", "error_reason": reason}), 503
        if claimed_old_resume:
            try:
                flow_ai.mark_resume_handoff(resume_from_run_id, task_id)
            except ctx.operational_errors:
                pass
        return jsonify({
            "ok": True, "task_id": task_id,
            "resuming": bool(resume_from_run_id),
            "platform": parent_platform,
            "filter_schema_version": filter_schema_version,
            "task_input_digest": ai_digest,
        })
