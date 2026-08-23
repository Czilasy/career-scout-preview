"""结果查看 / 进度 / 导出 API 路由（021 B6 T019 外迁自 webui/app.py）。

抓取结果保存、搜索进度、最新运行任务快照、最新 pipeline 结果与 CSV
导出。路由体纯搬运：HTTP 契约零改动；store / 任务表 / 断言助手经 ctx
取用；可 patch 符号（boss 等）经 ctx 动态门面。
"""

from __future__ import annotations

import csv
import io
import time

from flask import jsonify, request

from webui.app import LOG_TAIL_LINES, _MSG_TASK_NOT_FOUND
from webui.result_rounds import save_scraped_only_round
from webui.app import _public_task_status
from webui.task_runners import _iso_epoch_ms
from webui.workbench import normalize_job_link

def register_results_routes(app, ctx):
    def _build_source_summary_and_outcomes(run_id):
        """T405: 从持久化 screening_source_attempts 汇总 source outcomes。

        按 combo 最新 attempt 汇总，不从岗位数为零反推 empty。
        返回 (source_summary, source_outcomes)。
        """
        source_run_id = str(run_id or "")
        try:
            run = ctx.store.get_screening_run(source_run_id)
            if run is not None and run.get("record_kind") == "result_snapshot":
                params = run.get("execution_params") or {}
                source_run_id = str(
                    params.get("scrape_task_id") or params.get("source_run_id") or source_run_id
                )
        except ctx.operational_errors:
            pass
        try:
            attempts = ctx.store.list_latest_source_attempts(source_run_id)
        except ctx.operational_errors:
            attempts = []
        outcomes = []
        counts = {"non_empty": 0, "empty": 0, "failed": 0, "paused": 0}
        for a in attempts:
            outcomes.append({
                "combo_key": a["combo_key"],
                "attempt_no": a["attempt_no"],
                "outcome_kind": a["outcome_kind"],
                "job_count": a["job_count"],
                "input_hash": a["input_hash"],
                "error_code": a["error_code"],
            })
            if a["outcome_kind"] in counts:
                counts[a["outcome_kind"]] += 1
        summary = {"total_combos": len(outcomes), **counts}
        return summary, outcomes

    def _check_run_identity_conflict(run_id, task_dict):
        """T405: 校验内存 task 与 DB run 的 platform/task_input_digest 一致。

        返回 (db_run, error_response)。一致时 error_response=None；
        不一致时 error_response 为 409 响应。
        """
        mem_platform = (task_dict or {}).get("platform")
        mem_digest = (task_dict or {}).get("task_input_digest")
        try:
            db_run = ctx.store.get_screening_run(run_id)
        except ctx.operational_errors:
            db_run = None
        if db_run is not None and mem_platform:
            db_platform = db_run.get("platform")
            db_digest = db_run.get("task_input_digest")
            if db_platform and db_platform != mem_platform:
                return None, (jsonify({
                    "ok": False,
                    "error": "run_identity_conflict",
                    "message": "内存任务平台与数据库记录不一致",
                }), 409)
            if db_digest and mem_digest and db_digest != mem_digest:
                return None, (jsonify({
                    "ok": False,
                    "error": "run_identity_conflict",
                    "message": "内存任务输入摘要与数据库记录不一致",
                }), 409)
        return db_run, None

    def _has_newer_saved_result_than(timestamp: str | None) -> bool:
        """True when a result snapshot was saved after the given DB timestamp."""
        if not timestamp:
            return False
        try:
            saved_at = ctx.store.latest_pipeline_result_saved_at()
        except ctx.operational_errors:
            return False
        # 顺序写入且时间戳相同时也视为“已有更新快照”，避免同微秒下旧任务被误恢复。
        return bool(saved_at and str(saved_at) >= str(timestamp))

    def _round_context_for_run(run):
        """构建本轮上下文；无法追溯时返回空对象，不伪造。"""
        if run is None:
            return {}
        try:
            from webui.screen_flow import build_round_context_payload
            return build_round_context_payload(ctx.store, run) or {}
        except ctx.operational_errors:
            return {}


    @app.route("/api/scrape-result-save", methods=["POST"])
    def scrape_result_save():
        """B038: 把已完成的抓取任务固化为"已抓取，未筛选"历史轮。

        任务本身已自然完成，这里只固化快照（status=scraped_only），
        不终结任务、不跑 AI；0 岗位时不落库。
        """
        body = request.get_json(silent=True) or {}
        task_id = str(body.get("task_id") or "").strip()
        if not task_id:
            return jsonify({
                "ok": False, "error": "missing_task_id",
                "message": "缺少 task_id",
            }), 400
        source_snapshot = ctx.ensure_scrape_source(task_id)
        if source_snapshot is None:
            # 区分三类：任务不存在 / 任务未完成 / 任务完成但 0 岗位。
            try:
                existing = ctx.store.get_screening_run(task_id)
            except ctx.operational_errors:
                existing = None
            if existing is None:
                return jsonify({
                    "ok": False, "error": "scrape_task_not_found",
                    "message": "抓取任务不存在",
                }), 404
            run_status = str(existing.get("status") or "")
            if run_status not in ("succeeded", "partial", "failed", "interrupted"):
                return jsonify({
                    "ok": False, "error": "scrape_not_completed",
                    "message": "抓取任务尚未成功完成",
                }), 409
            # 已完成的 0 岗位任务：不进历史，前端仍展示 0。
            return jsonify({"ok": True, "saved": False, "run_id": task_id})
        if source_snapshot.get("kind") != "scrape" or source_snapshot.get("status") != "done":
            return jsonify({
                "ok": False, "error": "scrape_not_completed",
                "message": "抓取任务尚未成功完成",
            }), 409
        source_result = source_snapshot.get("result") or {}
        source_jobs = source_result.get("jobs") or []
        # 平台身份优先取冻结的 run checkpoint（与 ai_screen 同一口径）。
        try:
            parent_identity = ctx.store.get_run_checkpoint_identity(task_id)
        except ctx.operational_errors:
            parent_identity = None
        platform = str(
            (parent_identity or {}).get("platform")
            or source_snapshot.get("platform")
            or source_result.get("platform")
            or "boss"
        )
        profile_summary = str(body.get("profile_summary") or "")
        raw_facts = body.get("profile_facts")
        profile_facts = raw_facts if isinstance(raw_facts, dict) else None
        execution_config = source_snapshot.get("execution_config") or {}
        run_row = {}
        try:
            run_row = ctx.store.get_screening_run(task_id) or {}
            params = run_row.get("execution_params") or {}
            if not execution_config:
                execution_config = params.get("execution_config") or {}
        except ctx.operational_errors:
            params = {}
        if not profile_summary:
            profile_summary = str(
                params.get("profile_summary")
                or (source_result.get("profile_summary") if isinstance(source_result, dict) else "")
                or ""
            )
        if profile_facts is None:
            candidate_facts = params.get("profile_facts")
            if not isinstance(candidate_facts, dict) and isinstance(source_result, dict):
                candidate_facts = source_result.get("profile_facts")
            profile_facts = candidate_facts if isinstance(candidate_facts, dict) else None
        # 搜索参数（关键词/城市）来自 run 冻结的 script_params；
        # 缺失时退化为仅平台，历史列表关键词摘要能正常展示。
        script_params = params.get("script_params") or {}
        if isinstance(script_params, dict) and "platform" not in script_params:
            script_params = {**script_params, "platform": platform}
        # 017-US2: 幂等由 result_rounds.save_scraped_only_round 保证（不再端点预检）。
        outcome = save_scraped_only_round(
            ctx.store,
            source_jobs,
            platform=platform,
            scrape_task_id=task_id,
            profile_summary=profile_summary,
            profile_facts=profile_facts,
            execution_config=execution_config,
            script_params=script_params,
            started_at=run_row.get("started_at"),
            finished_at=run_row.get("finished_at"),
        )
        return jsonify({"ok": True, **outcome})

    @app.route("/api/search-progress/<task_id>")
    def search_progress(task_id):
        """Poll the progress of a pipeline run.

        Returns ``{status, progress, logs, result, error}``. ``status`` is one
        of ``running`` / ``done`` / ``failed``. ``result`` (present when done)
        carries the matched ``jobs`` and counts.
        """
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            # T405: 内存 task 与 DB run 身份一致性校验
            db_run, conflict = _check_run_identity_conflict(task_id, task)
            if conflict is not None:
                return conflict
            # 终态补结束时间戳（首次进入终态时记一次），供前端计时器显示真实用时
            if task["status"] in ("done", "failed", "cancelled") and task.get("finished_at") is None:
                task["finished_at"] = int(time.time() * 1000)
            # T405: 按 combo 最新 attempt 汇总 source outcomes
            source_summary, source_outcomes = _build_source_summary_and_outcomes(task_id)
            snapshot = {
                "ok": True,
                "kind": task.get("kind", ""),
                "status": _public_task_status(
                    task["status"], (db_run or {}).get("interruption_kind")),
                "progress": task["progress"],
                "logs": list(task["logs"][-LOG_TAIL_LINES:]),
                "error": task["error"],
                "started_at": task.get("started_at"),
                "finished_at": task.get("finished_at"),
                "config_digest": task.get("config_digest"),
                "scope_digest": task.get("scope_digest"),
                # T405: 平台身份与 source outcomes 汇总
                "platform": task.get("platform") or (db_run or {}).get("platform"),
                "task_input_digest": task.get("task_input_digest") or (db_run or {}).get("task_input_digest"),
                "source_summary": source_summary,
                "source_outcomes": source_outcomes,
            }
            if task["status"] in ("done", "failed") and task["result"] is not None:
                # 原样返回整个 result：抓取任务含 jobs/计数；
                # AI 筛选任务还含 dropped/verdict/profile_summary 等
                snapshot["result"] = task["result"]
        return jsonify(snapshot)


    @app.route("/api/latest-pipeline-result")
    def latest_pipeline_result():
        """Return the persisted latest pipeline run (survives page refresh).

        T409: 支持 platform/run_id 过滤查询；返回平台身份和 source_outcomes。

        Only a successful run is persisted, so this always reflects the most
        recent good data.  ``has_result`` is false until the first successful
        run (or if the file is missing/unreadable).

        传入 ``profile_id`` 时，给当前 profile 已标记 interested 的岗位补
        ``_marked: "interested"``，使刷新后「已感兴趣」按钮状态能正确回显
        （跨刷新持久化，见 spec）。匹配按 canonical_url——pipeline 结果的
        ``job_id`` 是 BOSS 岗位 id，profile_jobs.job_id 是内部 UUID，二者
        不能直接相等，统一用规范化链接对齐（同 _build_zone_canonical_urls）。
        """
        query_platform = request.args.get("platform", "").strip() or None
        query_run_id = request.args.get("run_id", "").strip() or None
        # T409: 精确 run_id 查询
        if query_run_id:
            try:
                run = ctx.store.get_screening_run(query_run_id)
            except ctx.operational_errors:
                run = None
            if run is None:
                return jsonify({"ok": True, "has_result": False})
            # 只返回已完成或部分完成的结果
            if run["status"] not in ("succeeded", "partial", "scraped_only"):
                return jsonify({"ok": True, "has_result": False})
            # T409: run_id + platform 必须一致
            if query_platform and query_platform != run.get("platform"):
                return jsonify({
                    "ok": False, "error": "run_platform_conflict",
                    "message": "run_id 与 platform 不一致",
                }), 409
            # 从该 run 的 result snapshot 构造响应
            payload = ctx.store.load_latest_pipeline_result(query_run_id)
            if payload is None:
                return jsonify({"ok": True, "has_result": False})
        # T409: 按平台过滤
        elif query_platform:
            payload = ctx.store.load_latest_pipeline_result_for_platform(query_platform)
        else:
            payload = ctx.store.load_latest_pipeline_result()
        if payload is None:
            return jsonify({"ok": True, "has_result": False})
        result = payload["result"]
        jobs = result.get("jobs", [])
        run_id = payload.get("run_id", "")
        round_context = _round_context_for_run(
            ctx.store.get_screening_run(run_id) if run_id else None)
        # T409: 汇总 source outcomes
        source_summary, source_outcomes = _build_source_summary_and_outcomes(run_id)

        profile_id = request.args.get("profile_id")
        if profile_id and isinstance(jobs, list) and jobs:
            try:
                ctx.store.get_profile(profile_id)
            except KeyError:
                profile_id = None
            if profile_id:
                interested_pjs = ctx.store.list_screening_interested(profile_id)
                # 批量预取，避免逐条 ctx.store.get_job 的 N+1
                interested_jobs = ctx.store.list_jobs_by_ids([pj["job_id"] for pj in interested_pjs])
                interested_urls = set()
                interested_slugs = set()

                def _collect_url_keys(pj_rows, job_map):
                    """把 profile_jobs 行换成 (canonical_url, slug) 匹配键集合。"""
                    urls = set()
                    slugs = set()
                    for pj in pj_rows:
                        stored = job_map.get(str(pj["job_id"]))
                        if not stored:
                            continue
                        url = normalize_job_link(ctx.stored.get("canonical_url", ""))
                        if not url:
                            continue
                        urls.add(url)
                        # 从 URL 路径提取平台岗位 slug 作为备用匹配（boss .html / 智联 .htm）
                        slug = url.rstrip("/").rsplit("/", 1)[-1]
                        for suffix in (".html", ".htm"):
                            slug = slug.removesuffix(suffix)
                        if slug:
                            slugs.add(slug)
                    return urls, slugs

                interested_urls, interested_slugs = _collect_url_keys(
                    interested_pjs, interested_jobs)
                # 投递状态：applied_at 非空即“投递过”（含已投递后跟进/荒废的状态变迁）
                applied_pjs = [
                    pj for pj in ctx.store.list_profile_jobs(profile_id)
                    if pj.get("applied_at")
                ]
                applied_jobs = ctx.store.list_jobs_by_ids([pj["job_id"] for pj in applied_pjs])
                applied_urls, applied_slugs = _collect_url_keys(applied_pjs, applied_jobs)
                rejected_pjs = ctx.store.list_profile_jobs(profile_id, status="deleted")
                rejected_jobs = ctx.store.list_jobs_by_ids([pj["job_id"] for pj in rejected_pjs])
                rejected_urls, rejected_slugs = _collect_url_keys(
                    rejected_pjs, rejected_jobs)
                if (
                    interested_urls or interested_slugs or applied_urls or applied_slugs
                    or rejected_urls or rejected_slugs
                ):
                    for item in jobs:
                        if not isinstance(item, dict):
                            continue
                        url = normalize_job_link(
                            item.get("source_url") or item.get("job_link") or ""
                        )
                        if (url and url in interested_urls) or (interested_slugs and str(item.get("job_id", "")) in interested_slugs):
                            item["_marked"] = "interested"
                        elif (url and url in rejected_urls) or (rejected_slugs and str(item.get("job_id", "")) in rejected_slugs):
                            item["_marked"] = "rejected"
                        if (url and url in applied_urls) or (applied_slugs and str(item.get("job_id", "")) in applied_slugs):
                            item["_applied"] = True

        return jsonify({
            "ok": True,
            "has_result": True,
            "source_run_id": payload.get("run_id"),
            "platform": payload.get("platform"),
            "status": payload.get("status", "completed"),
            "scrape_task_id": str(payload.get("scrape_task_id") or ""),
            "saved_at": payload.get("saved_at"),
            "started_at": _iso_epoch_ms(payload.get("started_at")),
            "finished_at": _iso_epoch_ms(payload.get("finished_at")),
            "script_params": payload.get("script_params", {}),
            "round_context": round_context,
            "execution_config": payload.get("execution_config", {}),
            "source_summary": source_summary,
            "source_outcomes": source_outcomes,
            "source_evidence_available": True,
            "result": {
                "total_scraped": result.get("total_scraped", 0),
                "total_matched": result.get("total_matched", 0),
                "total_kept": result.get("total_kept", 0),
                "total_dropped": result.get("total_dropped", 0),
                "combinations": result.get("combinations", 0),
                "jobs": jobs,
                "dropped": result.get("dropped", []),
                "profile_summary": result.get("profile_summary", ""),
                "profile_facts": result.get("profile_facts"),
            },
        })

    @app.route("/api/pipeline-result/export.csv")
    def export_pipeline_result_csv():
        """导出最终结果页数据：匹配的在前、不匹配的在后，各自带分组标志行。

        数据源与 ``/api/latest-pipeline-result`` 完全同源；支持 platform /
        run_id 参数，语义与该接口一致。每个岗位行带岗位直达链接。
        """
        query_platform = request.args.get("platform", "").strip() or None
        query_run_id = request.args.get("run_id", "").strip() or None
        if query_run_id:
            try:
                run = ctx.store.get_screening_run(query_run_id)
            except ctx.operational_errors:
                run = None
            if run is None:
                return jsonify({
                    "error_code": "not_found", "user_message": _MSG_TASK_NOT_FOUND,
                }), 404
            if run["status"] not in ("done", "succeeded", "partial"):
                return jsonify({
                    "error_code": "result_not_ready",
                    "user_message": "结果尚未完成，暂无法导出",
                }), 409
            if query_platform and query_platform != run.get("platform"):
                return jsonify({
                    "error_code": "run_platform_conflict",
                    "user_message": "run_id 与 platform 不一致",
                }), 409
            payload = ctx.store.load_latest_pipeline_result(query_run_id)
        elif query_platform:
            payload = ctx.store.load_latest_pipeline_result_for_platform(query_platform)
        else:
            payload = ctx.store.load_latest_pipeline_result()
        if payload is None:
            return jsonify({
                "error_code": "not_found", "user_message": "暂无可导出的结果",
            }), 404

        result = payload["result"]
        columns = [
            "title", "company", "salary", "location", "experience", "degree",
            "reason", "job_link",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()

        def _write_section(label, rows):
            section_row = {column: "" for column in columns}
            section_row["title"] = label
            writer.writerow(section_row)
            for row in rows:
                writer.writerow({
                    key: ctx.boss.csv_safe_cell(row.get(key, "")) for key in columns
                })

        matched_rows = [
            {
                **job,
                "reason": "",
                "job_link": job.get("canonical_url") or job.get("source_url") or "",
            }
            for job in (result.get("jobs") or []) if isinstance(job, dict)
        ]
        dropped_rows = [
            {
                **job,
                "job_link": job.get("canonical_url") or job.get("source_url") or "",
            }
            for job in (result.get("dropped") or []) if isinstance(job, dict)
        ]
        _write_section("匹配：", matched_rows)
        _write_section("不匹配：", dropped_rows)
        platform_label = payload.get("platform") or "all"
        return app.response_class(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=career_scout_jobs_{platform_label}.csv"
                ),
            },
        )

    # 021 B6：共享断言助手回传 ctx，供 app.py 内 taskstate 域调用
    ctx.check_run_identity_conflict = _check_run_identity_conflict
    ctx.build_source_summary_and_outcomes = _build_source_summary_and_outcomes
    ctx.round_context_for_run = _round_context_for_run
    ctx.has_newer_saved_result_than = _has_newer_saved_result_than
