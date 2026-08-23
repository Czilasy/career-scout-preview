"""Pipeline 岗位操作 / 批量重抓 API 路由（021 B6 T019 外迁自 webui/app.py）。

单岗位 JD 按需抓取、感兴趣/不感兴趣标记与撤销、批量重抓提交与续跑。
路由体纯搬运：HTTP 契约零改动；store / 任务声明 / 重抓 runner 经 ctx
取用；可 patch 符号（uuid / threading / boss 等）经 ctx 动态门面。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import jsonify, request

from webui.app import (
    _MSG_PROFILE_ID_REQUIRED,
    _MSG_PROFILE_NOT_FOUND,
    _ZHILIAN_HOST_TOKEN,
    _pipeline_identity_payload,
)
from webui.pipeline_job_identity import (
    JobIdentityError,
    parse_identity_payload,
    resolve_job_identity,
)
from webui.task_runners import _iso_epoch_ms
from webui.workbench import normalize_job_link_for_platform

def register_pipeline_jobs_routes(app, ctx):
    def _resolve_pipeline_job_identity(job):
        """Task 008：pipeline 入口统一走权威岗位身份解析（Task 003）。

        使用 Task 001 的 connection-aware 双索引 upsert，在调用方事务内
        完成可靠入库并返回内部 jobs.id；身份失败在关联写入前抛出，
        保证零副作用。BOSS 与智联共用同一协议，无平台分支。
        """
        identity_request = parse_identity_payload(
            _pipeline_identity_payload(job))
        with ctx.store._connection() as conn:
            return resolve_job_identity(conn, ctx.store, identity_request)

    def _pipeline_identity_error_response(exc: JobIdentityError):
        return jsonify({
            "ok": False,
            "error_code": exc.code,
            "user_message": str(exc),
            "details": exc.details,
        }), exc.http_status

    @app.route("/api/job-detail", methods=["POST"])
    def job_detail():
        """T417: 按需抓取单个岗位的 JD 正文。

        source_run_id + platform_job_id 为权威；从 source run 继承冻结平台。
        """
        from webui.pipeline_exec import ensure_chrome_ready

        raw = request.get_json(silent=True) or {}
        job_id = str(raw.get("job_id") or "").strip()
        platform_job_id = str(raw.get("platform_job_id") or job_id).strip()
        source_run_id = str(raw.get("source_run_id") or "").strip() or None
        raw_source_url = str(raw.get("source_url") or raw.get("job_link") or "")
        if not job_id or not raw_source_url.strip():
            return jsonify({"ok": False, "error": "缺少 job_id 或 source_url"}), 400

        # T417: 从 source run 继承冻结平台
        frozen_platform = "boss"
        frozen_browser_account = None
        frozen_cdp_port = None
        frozen_profile_key = None
        parent_run = None
        if source_run_id:
            try:
                identity = ctx.store.get_run_checkpoint_identity(source_run_id)
                parent_run = ctx.store.get_screening_run(source_run_id)
            except ctx.operational_errors:
                identity = parent_run = None
            if identity is not None:
                frozen_platform = str(identity.get("platform") or "boss")
            parent_params = (parent_run or {}).get("execution_params") or {}
            frozen_browser_account = str(parent_params.get("browser_account") or "") or None
            frozen_cdp_port = parent_params.get("cdp_port")
            frozen_profile_key = parent_params.get("profile_key")
            gp_task_id = str(parent_params.get("scrape_task_id") or "")
            if (not frozen_cdp_port or not frozen_profile_key) and gp_task_id:
                try:
                    gp = ctx.store.get_screening_run(gp_task_id)
                    gp_params = (gp or {}).get("execution_params") or {}
                    frozen_cdp_port = frozen_cdp_port or gp_params.get("cdp_port")
                    frozen_profile_key = frozen_profile_key or gp_params.get("profile_key")
                except ctx.operational_errors:
                    pass
        else:
            if _ZHILIAN_HOST_TOKEN in raw_source_url.lower():
                return jsonify({
                    "ok": False, "error": "run_identity_conflict",
                    "error_code": "run_identity_conflict",
                    "message": "智联单 JD 必须携带 source_run_id，不能按 URL 猜测来源",
                }), 409
        source_url = normalize_job_link_for_platform(
            raw_source_url, platform=frozen_platform
        )
        if not source_url:
            return jsonify({"ok": False, "error": "缺少合法 source_url"}), 400
        # 校验 URL 与平台一致性
        if frozen_platform == "zhilian" and _ZHILIAN_HOST_TOKEN not in source_url.lower():
            return jsonify({"ok": False, "error": "platform_url_mismatch",
                           "message": "智联岗位 URL 必须包含 zhaopin.com"}), 422
        if frozen_platform == "zhilian" and not (
                frozen_browser_account and frozen_cdp_port and frozen_profile_key):
            return jsonify({
                "ok": False, "error": "run_identity_conflict",
                "error_code": "run_identity_conflict",
                "message": "智联来源 run 缺少冻结浏览器身份",
            }), 409
        if source_run_id and parent_run is not None:
            ctx.activate_run_browser(parent_run)
        chrome_ok, chrome_err = ensure_chrome_ready(
            frozen_cdp_port if frozen_platform == "zhilian" else None,
            minimize_after_launch=True,
        )
        if not chrome_ok:
            return jsonify({"ok": False,
                            "error": f"调试浏览器未能就绪：{chrome_err}"}), 503

        source = ctx.make_cdp_source(
            platform=frozen_platform,
            browser_account=frozen_browser_account,
            cdp_port=frozen_cdp_port,
            profile_key=frozen_profile_key,
            run_id=source_run_id or "",
        )
        if source is None:
            return jsonify({"ok": False, "error": "抓取源不可用"}), 500

        job = {"job_id": platform_job_id, "source_url": source_url, "job_link": source_url}
        detail_path = str(
            Path(app.config["RESULT_DIR"]) / f"job_detail_{platform_job_id}.json"
        )
        with ctx.job_detail_lock:
            outcome = source.fetch_detail(job, detail_output_path=detail_path)
        if not outcome.ok:
            return jsonify({"ok": False,
                            "error": f"详情抓取失败（{outcome.failed_code}）"}), 502
        jd = str((outcome.detail or {}).get("jd", "")).strip()
        if not jd:
            return jsonify({"ok": False,
                            "error": "详情页未提取到 JD 正文，岗位可能已下架"}), 502
        return jsonify({
            "ok": True, "jd": jd,
            "platform": frozen_platform, "platform_job_id": platform_job_id,
        })

    @app.route("/api/pipeline/jobs/interest", methods=["POST"])
    def pipeline_mark_interest():
        """标记 pipeline 结果岗位为感兴趣：权威身份入库 + profile_jobs(interested)。

        复用筛选工作台的持久感兴趣区——标记后可在工作台"感兴趣"区看到
        （list_screening_interested 不按 run_id 过滤）。响应 job_id 是内部 ID。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": _MSG_PROFILE_NOT_FOUND}), 404
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        ctx.store.mark_screening_interest(profile_id, resolved.job_id, run_id=None)
        return jsonify({"interest_state": "interested", "job_id": resolved.job_id})

    @app.route("/api/pipeline/jobs/reject", methods=["POST"])
    def pipeline_mark_reject():
        """标记 pipeline 结果岗位为不感兴趣：权威身份入库 + profile_jobs(deleted)。

        标记后进入筛选工作台垃圾桶区。响应 job_id 是内部 ID。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": _MSG_PROFILE_NOT_FOUND}), 404
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        ctx.store.mark_screening_reject(profile_id, resolved.job_id, run_id=None)
        return jsonify({"reject_state": "rejected", "job_id": resolved.job_id})

    @app.route("/api/pipeline/jobs/reject/cancel", methods=["POST"])
    def pipeline_cancel_reject():
        """撤销 pipeline 结果岗位的不感兴趣标记：profile_jobs.status 回退。"""
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id or not isinstance(job, dict):
            return jsonify({"error": "missing profile_id or job"}), 400
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": _MSG_PROFILE_NOT_FOUND}), 404
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        try:
            ctx.store.cancel_screening_reject(profile_id, resolved.job_id)
        except sqlite3.Error as exc:
            return jsonify({"error": f"撤销不感兴趣失败: {exc}"}), 500
        return jsonify({"reject_state": "cancelled", "job_id": resolved.job_id})

    @app.route("/api/pipeline/jobs/interest/cancel", methods=["POST"])
    def pipeline_cancel_interest():
        """撤销 pipeline 结果岗位的感兴趣标记：profile_jobs.status 回退。

        payload 结构与 /api/pipeline/jobs/interest 一致（profile_id + job）；
        岗位必须能通过权威三元组或内部 ID 解析。幂等——即便当前不是
        interested 也不报错，使前端"感兴趣"按钮可再次点击取消。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id or not isinstance(job, dict):
            return jsonify({"error": "missing profile_id or job"}), 400
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": _MSG_PROFILE_NOT_FOUND}), 404
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        try:
            ctx.store.cancel_screening_interest(profile_id, resolved.job_id)
        except sqlite3.Error as exc:
            return jsonify({"error": f"撤销感兴趣失败: {exc}"}), 500
        return jsonify({"interest_state": "cancelled", "job_id": resolved.job_id})

    @app.route("/api/pipeline/jobs/<job_id>/jd", methods=["POST"])
    def pipeline_job_refetch_jd(job_id):
        """为单个岗位补抓 JD 并回写数据库中对应 job 项。

        用于 JD 抓取失败/缺失的岗位补抓；不重跑 AI、不跨 tab。与
        /api/job-detail 共用 ctx.job_detail_lock 串行化，避免并发争抢 CDP。
        """
        raw = request.get_json(silent=True) or {}
        source_run_id = str(raw.get("source_run_id") or "").strip()
        # 017-US4: 目标轮必填；禁止按 URL/最新结果猜测身份（FR-010）。
        if not source_run_id:
            return jsonify({
                "ok": False, "error": "missing_source_run_id",
                "message": "必须指定目标结果轮",
            }), 409
        if source_run_id:
            if ctx.store.get_pending_result(source_run_id, job_id) is None:
                return jsonify({
                    "ok": False, "error": "not_pending",
                    "message": "只能补抓当前待确认岗位",
                }), 409
            with ctx.lock:
                for existing_id, task in ctx.tasks.items():
                    if (task.get("kind") == "recrawl"
                            and task.get("source_run_id") == source_run_id
                            and task.get("status") in ("queued", "running")):
                        return jsonify({
                            "ok": False, "error": "already_running",
                            "existing_task_id": existing_id,
                        }), 409
            task_id = f"recrawl-{ctx.uuid.uuid4().hex[:12]}"
            # T406: 从父 run 读取冻结平台身份和浏览器身份
            parent_identity = None
            parent_run = None
            try:
                parent_identity = ctx.store.get_run_checkpoint_identity(source_run_id)
                parent_run = ctx.store.get_screening_run(source_run_id)
            except ctx.operational_errors:
                pass
            parent_platform = (parent_identity or {}).get("platform") or "boss"
            parent_task_input_digest = (parent_identity or {}).get("task_input_digest")
            parent_params = (parent_run or {}).get("execution_params") or {}
            parent_browser_account = str(parent_params.get("browser_account") or "") or None
            parent_cdp_port = parent_params.get("cdp_port")
            parent_profile_key = parent_params.get("profile_key")
            gp_task_id = str(parent_params.get("scrape_task_id") or "")
            if (not parent_cdp_port or not parent_profile_key) and gp_task_id:
                try:
                    grandparent = ctx.store.get_screening_run(gp_task_id)
                    gp_params = (grandparent or {}).get("execution_params") or {}
                    parent_cdp_port = parent_cdp_port or gp_params.get("cdp_port")
                    parent_profile_key = parent_profile_key or gp_params.get("profile_key")
                except ctx.operational_errors:
                    pass
            ctx.register_pipeline_task(task_id, "recrawl")
            with ctx.lock:
                ctx.tasks[task_id]["source_run_id"] = source_run_id
                ctx.tasks[task_id]["platform"] = parent_platform
                ctx.tasks[task_id]["cdp_port"] = parent_cdp_port
                ctx.tasks[task_id]["profile_key"] = parent_profile_key
                ctx.tasks[task_id]["browser_account"] = parent_browser_account or ctx.account_for_run()
                ctx.tasks[task_id]["task_input_digest"] = parent_task_input_digest
            profile_summary = str(raw.get("profile_summary") or "")
            profile_facts = raw.get("profile_facts") or None
            ctx.store.create_screening_run(
                task_id,
                source_count=1,
                execution_params={
                    "source_run_id": source_run_id,
                    "job_ids": [str(job_id)],
                    "profile_summary": profile_summary,
                    "profile_facts": profile_facts,
                    "single_retry": True,
                    "browser_account": ctx.tasks[task_id]["browser_account"],
                    "platform": parent_platform,
                    "cdp_port": parent_cdp_port,
                    "profile_key": parent_profile_key,
                    "task_input_digest": parent_task_input_digest,
                },
                backend_version=ctx.backend_version,
            )
            ctx.store.save_filter_snapshot(
                task_id,
                platform=parent_platform,
                task_input_digest=parent_task_input_digest,
            )
            ctx.store.update_screening_run(
                task_id, status="running", current_stage="recrawl_fetch_jd"
            )
            ctx.activate_run_browser(parent_run)
            try:
                ctx.executor.submit(
                    ctx.run_recrawl_task, task_id, [str(job_id)], profile_summary,
                    source_run_id, None, profile_facts,
                )
            except RuntimeError as exc:
                reason = f"后台任务提交失败：{type(exc).__name__}"
                ctx.store.update_screening_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=reason,
                )
                ctx.store.append_task_event(task_id, "job_fail", {
                    "stage": "recrawl_submit",
                    "job_id": str(job_id),
                    "failed_code": "internal_error",
                    "reason": reason,
                })
                with ctx.lock:
                    task = ctx.tasks.get(task_id)
                    if task is not None:
                        task["status"] = "failed"
                        task["error"] = reason
                return jsonify({
                    "ok": False, "error": "single_retry_submit_failed",
                }), 500
            return jsonify({
                "ok": True, "task_id": task_id, "source_run_id": source_run_id,
                "single_retry": True,
            }), 202

    @app.route("/api/pipeline/recrawl", methods=["POST"])
    def pipeline_recrawl():
        """对待确认（uncertain）岗位批量重抓：缺 JD 的补抓 JD，有 JD 的用画像重跑 AI 精筛。

        切片8（FR-022/FR-037）：防并发——同 source_run_id 已有 running 重抓任务时拒绝。
        切片8（FR-023）：job_ids 缺省时从 screening_pending_results 自动读取（全部重抓只处理待确认）。
        复用 fetch_job_details（CDP 通道，内部按 detail_batch_size 分批 + 冷却）与
        match_jds（按 match_batch_size 分批）。进度走与 AI 筛选相同的轮询机制
        （前端 pollTask + TaskProgress）。判定与 JD 原地回写 screening_results，
        返回 updates 映射供前端就地合并，保留当前结果 tab。
        """
        raw = request.get_json(silent=True) or {}
        job_ids = raw.get("job_ids")
        profile_summary = str(raw.get("profile_summary") or "")
        profile_facts = raw.get("profile_facts") or None
        # 017-US4: 目标轮必须显式携带，禁止"猜最新"回退（FR-010）。
        source_run_id = str(raw.get("source_run_id") or "").strip()
        if not source_run_id:
            return jsonify({"ok": False, "error": "missing_source_run_id"}), 409
        pending_rows = ctx.store.list_pending_results(source_run_id)
        pending_ids = {str(item.get("job_id") or "") for item in pending_rows}
        # B051：重抓目标 = 待确认表 ∪ 结果快照内无最终判定的岗位（含已有 JD
        # 但精筛未完成的提前结束保存岗位），按 JD 有无由任务内部分流。
        snapshot_ids = set()
        try:
            _snapshot = ctx.store.load_latest_pipeline_result(source_run_id)
            for _job in ((_snapshot or {}).get("result") or {}).get("jobs") or []:
                if not isinstance(_job, dict):
                    continue
                if str(_job.get("verdict") or "") in ("match", "not_match", "mismatch"):
                    continue
                _sid = ctx.recrawl_job_key(_job)
                if _sid:
                    snapshot_ids.add(_sid)
        except ctx.operational_errors:
            snapshot_ids = set()
        recrawlable_ids = pending_ids | snapshot_ids
        # FR-023：job_ids 缺省或 "auto" → 从可重抓集合读
        if not job_ids or job_ids == "auto":
            job_ids = sorted(recrawlable_ids)
            if not job_ids:
                return jsonify({
                    "ok": False, "error": "no_recrawlable_targets",
                    "message": "0 个可重抓岗位",
                }), 400
        if not isinstance(job_ids, list) or not job_ids:
            return jsonify({"ok": False, "error": "缺少 job_ids"}), 400
        requested_ids = {str(job_id) for job_id in job_ids}
        non_pending = sorted(requested_ids - recrawlable_ids)
        if non_pending:
            return jsonify({
                "ok": False,
                "error": "non_pending_job_ids",
                "message": "只能重抓当前结果中未完成判定的岗位",
                "job_ids": non_pending,
            }), 409
        # The request may pass pending-table IDs that no longer resolve to the
        # source snapshot (for example, a platform ID was empty in the old row).
        # Reject before queueing so this cannot become an asynchronous empty run.
        if snapshot_ids and requested_ids and not (requested_ids & snapshot_ids):
            return jsonify({
                "ok": False,
                "error": "no_recrawlable_targets",
                "message": "0 个可重抓岗位",
                "job_ids": sorted(requested_ids),
            }), 400
        job_ids = sorted(requested_ids)
        # T406: 从父 run 读取冻结平台身份和浏览器身份
        parent_identity = None
        parent_run = None
        try:
            parent_identity = ctx.store.get_run_checkpoint_identity(source_run_id)
            parent_run = ctx.store.get_screening_run(source_run_id)
        except ctx.operational_errors:
            pass
        parent_platform = (parent_identity or {}).get("platform") or "boss"
        parent_task_input_digest = (parent_identity or {}).get("task_input_digest")
        parent_params = (parent_run or {}).get("execution_params") or {}
        parent_browser_account = str(parent_params.get("browser_account") or "") or None
        parent_cdp_port = parent_params.get("cdp_port")
        parent_profile_key = parent_params.get("profile_key")
        # AI screen run 不含 cdp_port/profile_key → 从祖父 scrape run 读
        gp_task_id = str(parent_params.get("scrape_task_id") or "")
        if (not parent_cdp_port or not parent_profile_key) and gp_task_id:
            try:
                grandparent = ctx.store.get_screening_run(gp_task_id)
                gp_params = (grandparent or {}).get("execution_params") or {}
                parent_cdp_port = parent_cdp_port or gp_params.get("cdp_port")
                parent_profile_key = parent_profile_key or gp_params.get("profile_key")
            except ctx.operational_errors:
                pass
        task_id = f"recrawl-{ctx.uuid.uuid4().hex[:12]}"
        claimed_task, existing_task_id = ctx.claim_recrawl_start(
            task_id, source_run_id
        )
        if claimed_task is None:
            return jsonify({
                "ok": False,
                "error": "已有重抓任务在运行，请等待完成或取消后再试",
                "existing_task_id": existing_task_id,
            }), 409
        claimed_task["browser_account"] = parent_browser_account or ctx.account_for_run()
        claimed_task["platform"] = parent_platform
        claimed_task["cdp_port"] = parent_cdp_port
        claimed_task["profile_key"] = parent_profile_key
        claimed_task["task_input_digest"] = parent_task_input_digest
        ctx.activate_run_browser(parent_run)
        try:
            ctx.store.create_screening_run(
                task_id,
                source_count=len(job_ids),
                execution_params={
                    "source_run_id": source_run_id,
                    "job_ids": [str(x) for x in job_ids],
                    "profile_summary": profile_summary,
                    "profile_facts": profile_facts,
                    "browser_account": claimed_task["browser_account"],
                    "platform": parent_platform,
                    "cdp_port": parent_cdp_port,
                    "profile_key": parent_profile_key,
                    "task_input_digest": parent_task_input_digest,
                },
                backend_version=ctx.backend_version,
            )
            ctx.store.save_filter_snapshot(
                task_id,
                platform=parent_platform,
                task_input_digest=parent_task_input_digest,
            )
            ctx.store.update_screening_run(
                task_id, status="running", current_stage="recrawl_fetch_jd"
            )
        except ctx.operational_errors as exc:
            ctx.release_pipeline_claim(task_id, claimed_task)
            return jsonify({
                "ok": False,
                "error": f"重抓任务持久化失败：{type(exc).__name__}",
            }), 500
        try:
            ctx.executor.submit(
                ctx.run_recrawl_task, task_id, [str(x) for x in job_ids],
                profile_summary, source_run_id, None, profile_facts,
            )
        except RuntimeError as exc:
            try:
                ctx.store.update_screening_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=f"后台任务提交失败：{type(exc).__name__}",
                )
            finally:
                ctx.release_pipeline_claim(task_id, claimed_task)
            return jsonify({
                "ok": False, "error": "recrawl_submit_failed",
            }), 500
        return jsonify({"ok": True, "task_id": task_id, "source_run_id": source_run_id}), 202

    @app.route("/api/recrawl/continue/<task_id>", methods=["POST"])
    def continue_recrawl(task_id, _block_checked=False):
        """Resume a paused recrawl in place using its persisted checkpoint.

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp
        run = ctx.store.get_screening_run(task_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        stage = str(run.get("current_stage") or "")
        if run.get("status") != "paused" or not stage.startswith("recrawl_"):
            return jsonify({
                "ok": False, "error": "not_paused_recrawl",
                "status": run.get("status"), "stage": stage,
            }), 409
        ctx.activate_run_browser(run)
        if not _block_checked:
            passed, code, reason = ctx.check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
        with ctx.lock:
            existing = ctx.tasks.get(task_id)
            if existing is not None and existing.get("status") in ("queued", "running"):
                return jsonify({"ok": False, "error": "already_running"}), 409

        params = run.get("execution_params") or {}
        # 高级设置续跑生效：读取刷新后的 execution_config（api_task_continue
        # 在分发前已按当前 active 配置写回 DB），传给 ctx.run_recrawl_task。
        from webui.execution_config import ExecutionConfigSnapshot
        recrawl_config = None
        try:
            if params.get("execution_config"):
                recrawl_config = ExecutionConfigSnapshot.from_dict(params["execution_config"])
        except (KeyError, TypeError, ValueError):
            recrawl_config = None
        source_run_id = str(params.get("source_run_id") or "")
        job_ids = [str(job_id) for job_id in (params.get("job_ids") or [])]
        profile_summary = str(params.get("profile_summary") or "")
        profile_facts = params.get("profile_facts") or None
        checkpoint_stage = "recrawl_ai" if stage == "recrawl_ai" else "recrawl_jd"
        completed_job_ids = ctx.store.load_checkpoint(task_id, checkpoint_stage)
        if not job_ids:
            return jsonify({"ok": False, "error": "missing_job_ids"}), 409

        claimed_task, previous_task = ctx.claim_pipeline_task_id(
            task_id, "recrawl",
            started_at=_iso_epoch_ms(run.get("started_at")),
        )
        if claimed_task is None:
            return jsonify({"ok": False, "error": "already_running"}), 409
        claimed_task["source_run_id"] = source_run_id
        claimed_task["browser_account"] = ctx.account_for_run(run)
        resume_params = dict(run.get("execution_params") or {})
        if not str(resume_params.get("browser_account") or ""):
            resume_params["browser_account"] = ctx.account_for_run(run)
            ctx.store.update_screening_execution_params(task_id, resume_params)
        # T406: 继续时从 run 的 execution_params 恢复冻结平台身份
        claimed_task["platform"] = resume_params.get("platform") or "boss"
        claimed_task["cdp_port"] = resume_params.get("cdp_port")
        claimed_task["profile_key"] = resume_params.get("profile_key")
        claimed_task["task_input_digest"] = resume_params.get("task_input_digest")
        try:
            if not ctx.write_run(task_id, status="running"):
                ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
                return jsonify({
                    "ok": False, "error": "user_finished",
                    "message": "任务已结束保存，不能继续",
                    "status": "completed_with_pending",
                }), 409
            ctx.store.append_task_event(task_id, "resume", {
                "stage": stage, "completed": len(completed_job_ids),
            })
            ctx.executor.submit(
                ctx.run_recrawl_task, task_id, job_ids, profile_summary,
                source_run_id, completed_job_ids, profile_facts,
                recrawl_config,
            )
        except ctx.operational_errors as exc:
            try:
                current = ctx.store.get_screening_run(task_id)
                if current is not None and current.get("status") == "running":
                    ctx.write_run(
                        task_id, status="paused",
                        error_code=str(run.get("error_code") or "internal_error"),
                        error_reason=(
                            str(run.get("error_reason") or "")
                            or f"继续任务提交失败：{type(exc).__name__}"
                        ),
                    )
            finally:
                ctx.release_pipeline_claim(
                    task_id, claimed_task, previous_task
                )
            return jsonify({
                "ok": False, "error": "resume_submit_failed",
            }), 500
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "source_run_id": source_run_id,
            "completed_job_ids": sorted(completed_job_ids),
            "stage": stage,
        })

    # 021 B6：路由函数回传 ctx，供 app.py 内续跑分发调用
    ctx.continue_recrawl = continue_recrawl
