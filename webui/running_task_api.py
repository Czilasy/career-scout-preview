"""最新运行任务快照 API 路由（021 B6 T019 外迁自 webui/app.py）。

latest_running_task：查找顺序内存 running → DB paused → DB interrupted，
组装带冻结身份/断言/阶段口径的快照供前端恢复。路由体纯搬运；
store / 任务表经 ctx 取用。
"""

from __future__ import annotations

import json
import sqlite3

from flask import jsonify

from webui.constants import LOG_TAIL_LINES
from webui.task_status import _pipeline_kind_for_stage, _public_status_for_integrity
from webui.task_runners import _iso_epoch_ms
from webui.whitebox import WhiteboxService


def register_running_task_routes(app, ctx):
    def _integrity_for(owner_kind: str, owner_id: str, *, active: bool = False):
        """Read the canonical report without promoting legacy rows to success."""
        try:
            return WhiteboxService(ctx.store).report(owner_kind, str(owner_id))["integrity"]
        except ctx.operational_errors:
            return None if active else {
                "conclusion": "unverifiable", "label": "无法确认",
                "evidence_complete": False, "primary_code": "query_failed",
                "primary_reason": "任务证据查询失败", "recommendation": "建议重新执行",
                "revision": 0,
            }
        except Exception:
            return None if active else {
                "conclusion": "unverifiable", "label": "无法确认",
                "evidence_complete": False, "primary_code": "legacy_evidence_missing",
                "primary_reason": "历史证据不足，无法确认", "recommendation": "建议重新执行",
                "revision": 0,
            }

    def _scrape_completed_for_run(execution_params: dict) -> bool:
        """Infer whether the source scrape run finished after a service restart."""
        scrape_task_id = str(execution_params.get("scrape_task_id") or "")
        if not scrape_task_id:
            return False
        try:
            integrity = WhiteboxService(ctx.store).report("scrape", scrape_task_id)["integrity"]
        except ctx.operational_errors:
            return False
        except Exception:
            return False
        return bool(integrity.get("evidence_complete") and integrity.get("conclusion") in {"succeeded", "empty"})

    def _status_for_integrity(integrity: dict | None, fallback: str) -> str:
        return _public_status_for_integrity(integrity, fallback)
    @app.route("/api/latest-running-task")
    def latest_running_task():
        """返回最近一个仍在运行（running/queued）的 pipeline 任务。

        用于页面刷新后接回任务：前端 onMounted 调这个接口，有在跑的任务
        就恢复 task_id 和进度快照，重新开始轮询。dict 保序（Py3.7+），
        最后注册的任务排在最后，倒序找第一个非终态的返回。

        查找顺序（FR-028/FR-037）：
        1. 内存中 running/queued 任务（刷新接回）
        2. DB 中最近 paused 任务（服务重启后恢复暂停态）
        3. DB 中最近 interrupted 筛选（服务重启打断的工作线程）
        4. 无任务
        """
        from webui.pipeline_exec import failed_code_label
        with ctx.lock:
            for task_id, task in reversed(list(ctx.tasks.items())):
                try:
                    _mem_db_ep = ((ctx.store.get_screening_run(task_id) or {}).get("execution_params") or {})
                except ctx.operational_errors:
                    _mem_db_ep = {}
                try:
                    _mem_run = ctx.store.get_screening_run(task_id) or {}
                except ctx.operational_errors:
                    _mem_run = {}
                if task["status"] in ("running", "queued"):
                    return jsonify({
                        "ok": True,
                        "has_task": True,
                        "task_id": task_id,
                        "kind": task.get("kind", ""),
                        "status": task["status"],
                        "progress": task["progress"],
                        "stage": task.get("stage") or (task.get("progress") or {}).get("stage", ""),
                        "logs": list(task["logs"][-LOG_TAIL_LINES:]),
                        "error": task["error"],
                        "started_at": task.get("started_at"),
                        "finished_at": task.get("finished_at"),
                        # T409 契约 http-api.md L200-202：所有 has_task=true
                        # 响应增加 platform 和 task_input_digest。内存任务读取
                        # 注册时冻结值，不得因缺平台补成 BOSS。
                        "platform": task.get("platform"),
                        "task_input_digest": task.get("task_input_digest"),
                        "auto_screen": bool(task.get("auto_screen") or _mem_db_ep.get("auto_screen")),
                        "auto_screen_fields": _mem_db_ep.get("auto_screen_fields") or {},
                        "auto_screen_profile": str(_mem_db_ep.get("auto_screen_profile") or ""),
                        "scrape_task_id": (
                            _mem_db_ep.get("scrape_task_id")
                            or (task.get("source_task_id") if task.get("kind") == "ai_screen" else "")
                        ),
                        "scrape_completed": _scrape_completed_for_run(_mem_db_ep),
                        "source_run_id": _mem_db_ep.get("source_run_id"),
                        "frozen_filters": _mem_run.get("frozen_filters") or _mem_db_ep.get("auto_screen_fields") or {},
                        "profile_summary": str(
                            _mem_db_ep.get("profile_summary")
                            or _mem_db_ep.get("auto_screen_profile") or ""
                        ),
                        "profile_facts": (
                            _mem_db_ep.get("profile_facts")
                            or _mem_db_ep.get("auto_screen_facts")
                        ),
                        "round_context": ctx.round_context_for_run(_mem_run),
                        "integrity": _integrity_for(
                            "scrape" if task.get("kind") == "scrape" else (
                                "recrawl" if task.get("kind") == "recrawl" else "screening"
                            ),
                            task_id, active=True,
                        ),
                    })
        # 2. DB 中最近 paused（服务重启后恢复暂停态，FR-028）
        try:
            with ctx.store._connection() as conn:
                prow = conn.execute(
                    "SELECT id, status, current_stage, error_code, error_reason, "
                    "processed_count, source_count, pending_count, match_count, "
                    "mismatch_count, total_dropped, backend_version, updated_at "
                    "FROM screening_runs WHERE status = 'paused' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        except (sqlite3.Error, RuntimeError):
            prow = None
        if prow is not None and ctx.has_newer_saved_result_than(prow["updated_at"]):
            prow = None
        if prow is not None:
            paused_run = ctx.store.get_screening_run(prow["id"]) or {}
            execution_params = paused_run.get("execution_params") or {}
            paused_error_reason = (
                prow["error_reason"]
                or failed_code_label(
                    prow["error_code"], str(paused_run.get("platform") or "")
                )
                or prow["error_code"]
                or "任务已暂停"
            )
            paused_kind = _pipeline_kind_for_stage(prow["current_stage"] or "")
            paused_source_task_id = (
                str(execution_params.get("scrape_task_id") or "") or prow["id"]
            )
            paused_scraped_count = ctx.store.count_scrape_run_jobs(paused_source_task_id)
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": prow["id"],
                "kind": paused_kind,
                "status": "paused",
                "stage": prow["current_stage"],
                "progress": {
                    "processed": prow["processed_count"],
                    "total": prow["source_count"],
                    "pending": prow["pending_count"],
                    "match": prow["match_count"],
                    "mismatch": prow["mismatch_count"],
                    "dropped": prow["total_dropped"],
                    "message": paused_error_reason,
                },
                "logs": [],
                "error": "",
                "pause_info": {
                    "error_code": prow["error_code"],
                    "error_reason": paused_error_reason,
                },
                "backend_version": prow["backend_version"],
                "current_version": ctx.backend_version,
                "version_match": (prow["backend_version"] == ctx.backend_version),
                "started_at": _iso_epoch_ms(paused_run.get("started_at")),
                "finished_at": _iso_epoch_ms(paused_run.get("finished_at")),
                "resumable": True,
                "source": "database",
                "scrape_task_id": execution_params.get("scrape_task_id"),
                "scrape_completed": _scrape_completed_for_run(execution_params),
                "auto_screen": bool(execution_params.get("auto_screen")),
                "auto_screen_fields": execution_params.get("auto_screen_fields") or {},
                "auto_screen_profile": str(execution_params.get("auto_screen_profile") or ""),
                "profile_summary": str(execution_params.get("profile_summary") or ""),
                "profile_facts": execution_params.get("profile_facts"),
                "source_run_id": execution_params.get("source_run_id"),
                "checkpoint_stage": prow["current_stage"],
                # T409 契约 http-api.md L200-202：DB paused 从 screening_runs
                # 读取 platform/task_input_digest；source 字段只表示状态数据
                # 来源，不能承载招聘平台。
                "platform": paused_run.get("platform"),
                "task_input_digest": paused_run.get("task_input_digest"),
                "scraped_count": paused_scraped_count,
                "source_total": int(prow["source_count"] or 0),
                "round_context": ctx.round_context_for_run(paused_run),
                "integrity": _integrity_for(
                    paused_kind if paused_kind in {"scrape", "recrawl"} else "screening",
                    prow["id"],
                ),
            })
        # 3. DB 中被进程重启打断的筛选。重启后工作线程已死，
        # 不能假装还在跑——如实告诉前端有个可续跑的中断任务。
        try:
            run = ctx.store.latest_interrupted_screening_run()
        except ctx.operational_errors as exc:
            return jsonify({
                "ok": False,
                "error": "task_state_unavailable",
                "detail": type(exc).__name__,
            }), 503
        if run is not None and ctx.has_newer_saved_result_than(run.get("updated_at")):
            run = None
        if run is not None:
            interrupted_params = run.get("execution_params") or {}
            interrupted_source_task_id = (
                str(interrupted_params.get("scrape_task_id") or "") or run["id"]
            )
            interrupted_scraped_count = ctx.store.count_scrape_run_jobs(interrupted_source_task_id)
            interrupted_kind = _pipeline_kind_for_stage(run.get("current_stage") or "")
            if interrupted_kind == "scrape":
                interrupted_message = "上次抓取因服务重启被中断；已抓数据已保存"
            elif interrupted_kind == "recrawl":
                interrupted_message = "上次补抓因服务重启被中断；可结束保存已有结果"
            else:
                interrupted_message = "上次 AI 筛选因服务重启被中断"
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": run["id"],
                "kind": _pipeline_kind_for_stage(run.get("current_stage") or ""),
                "status": "interrupted",
                "progress": {"message": interrupted_message},
                "logs": [],
                "error": "",
                "started_at": _iso_epoch_ms(run.get("started_at")),
                "finished_at": _iso_epoch_ms(run.get("finished_at")),
                "resumable": True,
                "error_code": run.get("error_code"),
                "source_run_id": (run.get("execution_params") or {}).get("source_run_id"),
                "scrape_task_id": (run.get("execution_params") or {}).get("scrape_task_id"),
                "scrape_completed": _scrape_completed_for_run(run.get("execution_params") or {}),
                "auto_screen": bool((run.get("execution_params") or {}).get("auto_screen")),
                "frozen_filters": run.get("frozen_filters") or {},
                "profile_summary": str((run.get("execution_params") or {}).get("profile_summary") or ""),
                "profile_facts": (run.get("execution_params") or {}).get("profile_facts"),
                # T409 契约 http-api.md L200-202：DB interrupted 从
                # screening_runs 读取 platform/task_input_digest。
                "platform": run.get("platform"),
                "task_input_digest": run.get("task_input_digest"),
                "scraped_count": interrupted_scraped_count,
                "source_total": int(run.get("source_count") or 0),
                "round_context": ctx.round_context_for_run(run),
                "integrity": _integrity_for(
                    interrupted_kind if interrupted_kind in {"scrape", "recrawl"} else "screening",
                    run["id"],
                ),
            })
        # 3.4 孤儿收尾：DB 里 running+scrape、内存已无 worker、但 checkpoint
        # 已满且已抓岗位>0 —— 说明抓取确实完成而终态漏写。这里补写
        # succeeded，落到 3.5/3.6 继续走正常恢复展示，修复"数据已齐却
        # 卡在运行中"（续跑被旧清理定时器误删内存任务后仍能自愈）。
        try:
            with ctx.store._connection() as conn:
                _running_rows = conn.execute(
                    "SELECT * FROM screening_runs WHERE status = 'running' "
                    "AND current_stage = 'scrape' ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
        except (sqlite3.Error, RuntimeError):
            _running_rows = []
        for _running_row in _running_rows:
            _rid = str(_running_row["id"])
            with ctx.lock:
                _live_task = ctx.tasks.get(_rid)
            if _live_task is not None and _live_task.get("status") in ("running", "queued"):
                continue
            _running_run = ctx.store.get_screening_run(_rid) or {}
            if not _running_run:
                continue
            _rk = _running_run.get("record_kind")
            if _rk and _rk != "process_log":
                continue
            if ctx.has_newer_saved_result_than(_running_run.get("updated_at")):
                continue
            _source_total = int(_running_run.get("source_count") or 0)
            if _source_total <= 0:
                continue
            try:
                _checkpoint_done = len(ctx.store.load_checkpoint(_rid, "scrape"))
            except ctx.operational_errors:
                _checkpoint_done = 0
            if _checkpoint_done < _source_total:
                continue
            if ctx.store.count_scrape_run_jobs(_rid) <= 0:
                continue
            # 仅白箱完整结论可补写完成；岗位/检查点数量本身不是完成证据。
            try:
                _integrity = WhiteboxService(ctx.store).report("scrape", _rid)["integrity"]
            except Exception:
                _integrity = {"conclusion": "unverifiable", "label": "无法确认",
                              "evidence_complete": False, "primary_code": "legacy_evidence_missing",
                              "primary_reason": "历史证据不足，无法确认", "revision": 0}
            if _integrity.get("conclusion") not in {"succeeded", "empty"} or not _integrity.get("evidence_complete"):
                return jsonify({
                    "ok": True, "has_task": True, "task_id": _rid, "kind": "scrape",
                    "status": "running", "stage": "scrape", "progress": {"message": "正在核对任务完成证据"},
                    "logs": [], "error": _integrity.get("primary_reason") or "无法确认是否完成",
                    "resumable": True, "source": "database", "integrity": _integrity,
                    "platform": _running_run.get("platform"), "scrape_task_id": _rid,
                    "scrape_completed": False, "scraped_count": ctx.store.count_scrape_run_jobs(_rid),
                    "source_total": _source_total,
                })
            ctx.write_run(
                _rid, status="succeeded", current_stage="scrape",
                processed_count=max(int(_running_run.get("processed_count") or 0), _checkpoint_done),
                source_count=_source_total,
            )
            ctx.store.append_task_event(_rid, "stage_complete", {"stage": "scrape"})
        # 3.5 已完成抓取 + auto_screen 未消费：刷新后自动接 AI 筛选。
        try:
            with ctx.store._connection() as conn:
                auto_rows = conn.execute(
                    "SELECT id, platform, current_stage, source_count, "
                    "execution_params_json, updated_at "
                    "FROM screening_runs WHERE status = 'succeeded' "
                    "AND current_stage = 'scrape' "
                    "ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
        except (sqlite3.Error, RuntimeError):
            auto_rows = []
        for auto_row in auto_rows:
            if ctx.has_newer_saved_result_than(auto_row["updated_at"]):
                continue
            try:
                auto_params = json.loads(auto_row["execution_params_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                auto_params = {}
            if not bool(auto_params.get("auto_screen")):
                continue
            auto_scraped_count = ctx.store.count_scrape_run_jobs(auto_row["id"])
            if auto_scraped_count <= 0:
                continue
            auto_integrity = _integrity_for("scrape", auto_row["id"])
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": auto_row["id"],
                "kind": "scrape",
                "status": _status_for_integrity(auto_integrity, "completed"),
                "stage": "scrape",
                "progress": {"message": "抓取已完成，等待 AI 筛选"},
                "logs": [],
                "error": "",
                "resumable": False,
                "source": "database",
                "auto_screen": True,
                "scrape_task_id": auto_row["id"],
                "scrape_completed": _scrape_completed_for_run({"scrape_task_id": auto_row["id"]}),
                "frozen_filters": auto_params.get("auto_screen_fields") or {},
                "profile_summary": str(auto_params.get("auto_screen_profile") or ""),
                "profile_facts": auto_params.get("auto_screen_facts"),
                "platform": auto_row["platform"],
                "task_input_digest": auto_params.get("task_input_digest"),
                "scraped_count": auto_scraped_count,
                "source_total": int(auto_row["source_count"] or 0),
                "integrity": auto_integrity,
            })
        # 3.6 已完成普通抓取：快照未落库时刷新仍可恢复并触发保存。
        for completed_row in auto_rows:
            if ctx.has_newer_saved_result_than(completed_row["updated_at"]):
                continue
            try:
                completed_params = json.loads(completed_row["execution_params_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                completed_params = {}
            if (bool(completed_params.get("auto_screen"))
                    or completed_params.get("auto_screen_fields")
                    or completed_params.get("auto_screen_profile")):
                continue
            if ctx.store.latest_scraped_only_for_source(completed_row["id"]) is not None:
                continue
            completed_scraped_count = ctx.store.count_scrape_run_jobs(completed_row["id"])
            if completed_scraped_count <= 0:
                continue
            completed_run = ctx.store.get_screening_run(completed_row["id"]) or {}
            completed_integrity = _integrity_for("scrape", completed_row["id"])
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": completed_row["id"],
                "kind": "scrape",
                "status": _status_for_integrity(completed_integrity, "completed"),
                "stage": "scrape",
                "progress": {"message": "抓取已完成，正在恢复结果"},
                "logs": [],
                "error": "",
                "resumable": False,
                "source": "database",
                "auto_screen": False,
                "scrape_task_id": completed_row["id"],
                "scrape_completed": _scrape_completed_for_run({"scrape_task_id": completed_row["id"]}),
                "frozen_filters": (
                    completed_run.get("frozen_filters")
                    or completed_params.get("auto_screen_fields") or {}
                ),
                "profile_summary": str(
                    completed_params.get("profile_summary")
                    or completed_params.get("auto_screen_profile") or ""
                ),
                "profile_facts": (
                    completed_params.get("profile_facts")
                    or completed_params.get("auto_screen_facts")
                ),
                "platform": completed_row["platform"] or completed_run.get("platform"),
                "task_input_digest": completed_params.get("task_input_digest"),
                "scraped_count": completed_scraped_count,
                "source_total": int(completed_row["source_count"] or 0),
                "round_context": ctx.round_context_for_run(completed_run),
                "integrity": completed_integrity,
            })
        # 4. failed 抓取兜底：有已持久化岗位的任务刷新后可恢复显示真实数量。
        try:
            with ctx.store._connection() as conn:
                failed_rows = conn.execute(
                    "SELECT * FROM screening_runs WHERE status = 'failed' "
                    "AND current_stage = 'scrape' ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
        except (sqlite3.Error, RuntimeError):
            failed_rows = []
        for failed_row in failed_rows:
            failed_run = ctx.store.get_screening_run(failed_row["id"]) or {}
            if not failed_run or failed_run.get("error_code") == "user_finished":
                continue
            if ctx.has_newer_saved_result_than(failed_run.get("updated_at")):
                continue
            failed_scraped_count = ctx.store.count_scrape_run_jobs(failed_run["id"])
            if failed_scraped_count <= 0:
                continue
            failed_params = failed_run.get("execution_params") or {}
            failed_error_reason = (
                failed_run.get("error_reason")
                or failed_code_label(
                    failed_run.get("error_code"), str(failed_run.get("platform") or "")
                )
                or failed_run.get("error_code")
                or "抓取失败"
            )
            failed_integrity = _integrity_for("scrape", failed_run["id"])
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": failed_run["id"],
                "kind": "scrape",
                "status": _status_for_integrity(failed_integrity, "failed"),
                "stage": "scrape",
                "progress": {
                    "message": failed_error_reason,
                },
                "logs": [],
                "error": failed_error_reason,
                "pause_info": {
                    "error_code": failed_run.get("error_code"),
                    "error_reason": failed_error_reason,
                },
                "resumable": True,
                "source": "database",
                "scrape_task_id": failed_run["id"],
                "auto_screen": bool(failed_params.get("auto_screen")),
                "platform": failed_run.get("platform"),
                "task_input_digest": failed_run.get("task_input_digest"),
                "scraped_count": failed_scraped_count,
                "source_total": int(failed_run.get("source_count") or 0),
                "execution_params": failed_params,
                "integrity": failed_integrity,
            })
        return jsonify({"ok": True, "has_task": False})
