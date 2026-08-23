"""AI 筛选提交 / 取消 API 路由（021 B6 T019 外迁自 webui/app.py）。

筛选任务提交（含断点续跑识别）与取消。路由体纯搬运：HTTP 契约零改动；
任务声明 / runner 包装经 ctx 取用；可 patch 符号经 ctx 动态门面。
"""

from __future__ import annotations

import hashlib
import json

from flask import jsonify, request

from webui.app import (
    _MSG_TASK_ALREADY_RUNNING,
    _MSG_TASK_NOT_FOUND,
    _MSG_USER_STOPPED_SCREEN,
)
from webui.task_runners import _iso_epoch_ms

def register_ai_screen_routes(app, ctx):
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
            if stop_event is not None:
                stop_event.set()
            # 立刻标记 cancelled，让前端轮询马上看到状态变化
            task["status"] = "cancelled"
            task["error"] = _MSG_USER_STOPPED_SCREEN
            task["logs"].append("用户取消任务")
            cancel_platform = task.get("platform")
        # 关浏览器放到锁外（仅抓 JD 阶段有意义），best-effort，失败不阻塞取消。
        try:
            from webui.pipeline_exec import close_debug_chrome
            close_debug_chrome()
        except Exception:
            pass
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

    @app.route("/api/ai-screen", methods=["POST"])
    def ai_screen():
        """Stage 3b：对已抓取的原始岗位做两段式 AI 筛选。

        T406-T407: 接收 platform 做一致性校验，从父搜索 run 继承平台/scope/
        runtime，保存字段稳定值和当时标签的完整筛选快照。

        SPEC011 T015: 实验租约持有时拒绝启动（FR-035）。
        """
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
            or not source_result.get("ok")
        ):
            return jsonify({"ok": False, "error": "抓取任务尚未成功完成"}), 409

        # T406: 从父搜索 run 读取平台身份
        try:
            parent_identity = ctx.store.get_run_checkpoint_identity(scrape_task_id)
        except ctx.operational_errors:
            parent_identity = None
        if parent_identity is None:
            parent_platform = str(source_snapshot.get("platform") or "boss")
        else:
            parent_platform = parent_identity.get("platform") or "boss"
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
        task_id = ctx.uuid.uuid4().hex

        # 逻辑隔离：AI 筛选也不能与其它 pipeline 任务（抓取/重抓/暂停）并发。
        if ctx.has_active_pipeline_task():
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
                parent_run = ctx.store.get_screening_run(scrape_task_id)
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
                "detail": type(exc).__name__,
            }), 503
        if prev is not None:
            resume_from_run_id = prev["id"]
        if resume_from_run_id and prev is not None and prev["status"] == "paused":
            # paused run 就地转为 running，保持唯一任务身份和 canonical 状态。
            try:
                claimed = ctx.store.claim_paused_screening_run(resume_from_run_id)
            except ctx.operational_errors as exc:
                return jsonify({
                    "ok": False,
                    "error": "resume_claim_failed",
                    "detail": type(exc).__name__,
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
        claimed_task, previous_task = ctx.claim_pipeline_task_id(
            task_id, "ai_screen",
            started_at=(
                _iso_epoch_ms((prev or {}).get("started_at"))
                if resume_from_run_id and prev is not None else None
            ),
        )
        if claimed_task is None:
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                ctx.store.update_screening_run(resume_from_run_id, status="paused")
            if claimed_old_resume:
                ctx.release_resume_claim(resume_from_run_id)
            return jsonify({
                "ok": False, "error": "already_running",
            }), 409
        claimed_task["source_task_id"] = scrape_task_id
        account_source = prev if resume_from_run_id else None
        if resume_from_run_id:
            claimed_task["resumed_from"] = resume_from_run_id
        claimed_task["browser_account"] = ctx.account_for_run(account_source)
        claimed_task["platform"] = parent_platform
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
            if not str(resume_params.get("browser_account") or ""):
                resume_params["browser_account"] = ctx.account_for_run(prev)
                ctx.store.update_screening_execution_params(resume_from_run_id, resume_params)
        ctx.activate_run_browser(account_source)
        # T407: 创建 AI run 时保存平台身份和筛选快照
        if not resume_from_run_id:
            try:
                ctx.store.create_screening_run(
                    task_id,
                    frozen_filters=screening_fields,
                    source_count=0,
                    execution_params={
                        "platform": parent_platform,
                        "filter_schema_version": filter_schema_version,
                        "screening_fields": screening_fields,
                        "profile_summary": profile_summary,
                        "profile_facts": profile_facts,
                        "scrape_task_id": scrape_task_id,
                        "browser_account": claimed_task.get("browser_account"),
                        "task_input_digest": ai_digest,
                        "cross_platform_dedupe": cross_platform_dedupe,
                    },
                    backend_version=ctx.backend_version,
                )
                ctx.store.save_filter_snapshot(
                    task_id,
                    platform=parent_platform,
                    filter_schema_version=filter_schema_version,
                    filter_snapshot=screening_fields,
                    task_input_digest=ai_digest,
                )
            except ctx.operational_errors as exc:
                ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
                return jsonify({
                    "ok": False,
                    "error": "ai_screen_persist_failed",
                    "detail": type(exc).__name__,
                }), 503
        try:
            ctx.executor.submit(
                ctx.run_ai_screen_task, task_id, screening_fields,
                profile_summary, scrape_task_id, resume_from_run_id,
                profile_facts, cross_platform_dedupe=cross_platform_dedupe,
            )
        except RuntimeError:
            ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                ctx.store.update_screening_run(resume_from_run_id, status="paused")
            if claimed_old_resume:
                ctx.release_resume_claim(resume_from_run_id)
            raise
        if claimed_old_resume:
            try:
                ctx.store.update_screening_run(
                    resume_from_run_id,
                    error_code="resumed",
                    error_reason="已由新任务接管续跑",
                )
                ctx.store.append_task_event(
                    resume_from_run_id, "resume", {"task_id": task_id})
            except ctx.operational_errors:
                pass
        return jsonify({
            "ok": True, "task_id": task_id,
            "resuming": bool(resume_from_run_id),
            "platform": parent_platform,
            "filter_schema_version": filter_schema_version,
            "task_input_digest": ai_digest,
        })
