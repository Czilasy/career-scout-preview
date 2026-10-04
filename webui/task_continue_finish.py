"""Finish endpoint for paused/partial task runs.

The public route remains registered by ``task_continue_api``.  ``compat`` is
the facade module itself so existing monkeypatches and injected helpers keep
working while the finish orchestration lives in its own bounded module.
"""
from __future__ import annotations

import sqlite3
import time
from flask import jsonify, request

from webui.flow_task_coordinator import (
    FlowTaskOperationError,
    MissingPlatformIdentityError,
    resolve_flow_binding,
)


def register_finish_route(app, ctx, compat):
    def _bound_track(binding):
        try:
            flow_now = ctx.store.get_flow(
                binding["flow_id"], profile_id=binding["profile_id"]
            )
            return next(
                item for item in flow_now.get("tracks", [])
                if item.get("id") == binding["track_id"]
            )
        except (KeyError, StopIteration, ValueError):
            return None

    def _flow_idempotent_finish(run_id, binding):
        bound_track = _bound_track(binding)
        result_id = str((bound_track or {}).get("result_run_id") or "").strip()
        if not result_id or str((bound_track or {}).get("status") or "") != "stopped":
            return None
        # A repeated finish must expose the durable result or an observable
        # read failure; an empty placeholder would falsely claim success.
        flow_result = ctx.store.get_flow_results(
            binding["flow_id"], profile_id=binding["profile_id"]
        )
        return jsonify({
            "ok": True,
            "idempotent": True,
            "run_id": run_id,
            "snapshot_run_id": result_id,
            "platform": binding["platform"],
            "status": "completed_with_pending",
            "result": flow_result,
            "message": "任务已结束，已完成结果已保存",
        })

    def _flow_results_unavailable():
        return jsonify({
            "ok": False,
            "error": "flow_results_unavailable",
            "error_code": "flow_results_unavailable",
            "message": "流程结果暂时不可读取，请稍后重试",
        }), 503

    def _flow_finish_pending(run_now, binding):
        """Recognize a claimed-but-not-yet-published Flow finish.

        ``claim_flow_finish`` intentionally leaves the Track active while a
        result snapshot is being prepared.  A retry must be allowed to finish
        that claim, but a terminal Track or a bound result remains the normal
        idempotent/terminal path.
        """
        if binding is None or not isinstance(run_now, dict):
            return False
        if not (
            run_now.get("status") == "interrupted"
            and run_now.get("error_code") == "user_finished"
            and run_now.get("interruption_kind") == "user_finished"
        ):
            return False
        track_now = _bound_track(binding)
        return bool(
            track_now is not None
            and str(track_now.get("status") or "") not in {
                "failed", "cancelled", "done", "succeeded", "stopped",
            }
            and not str(track_now.get("result_run_id") or "").strip()
        )

    @app.route("/api/task/finish/<run_id>", methods=["POST"])
    def api_task_finish(run_id: str):
        """T416: 结束可恢复任务并生成可展示的部分结果快照。
        允许 queued/running/paused/failed 以及 interrupted(process_restart/
        operator_stop)；user_cancelled 是终态，不能通过 finish 改写。
        """
        run = ctx.store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        try:
            flow_binding = resolve_flow_binding(ctx, run_id)
        except MissingPlatformIdentityError:
            return jsonify({
                "ok": False,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法结束保存",
            }), 409
        except FlowTaskOperationError:
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "任务关联运行线无效，无法结束保存",
            }), 409
        if (
            flow_binding is not None
            and run.get("status") == "interrupted"
            and run.get("error_code") == "user_finished"
        ):
            try:
                repeated = _flow_idempotent_finish(run_id, flow_binding)
            except Exception:  # noqa: BLE001 - result read failure is observable
                return _flow_results_unavailable()
            if repeated is not None:
                return repeated
        # 收尾族：worker 正在写终态时先等它定稿，再按真实状态判断——已完成的
        # 任务不生成假的部分快照（旧行为会把成功轮结果顶成旧快照）。
        _finalize_deadline = time.monotonic() + compat._FINALIZE_WAIT_TIMEOUT_S
        while True:
            with ctx.lock:
                _live_task = ctx.tasks.get(run_id)
            if _live_task is None or not _live_task.get("finalizing"):
                break
            if time.monotonic() >= _finalize_deadline:
                return jsonify({
                    "ok": False, "error": "finalizing",
                    "message": "任务正在收尾，请稍候重试",
                }), 409
            time.sleep(0.05)
        run = ctx.store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        interruption_kind = run.get("interruption_kind") or ""
        if (
            run["status"] == "interrupted"
            and run.get("error_code") == "user_finished"
            and not _flow_finish_pending(run, flow_binding)
        ):
            return jsonify({
                "ok": False, "error": "already_finished",
                "message": "任务已结束保存，请勿重复操作",
            }), 409
        if run["status"] == "interrupted" and interruption_kind == "user_cancelled":
            return jsonify({
                "ok": False, "error": "user_cancelled",
                "message": "用户已取消的任务不能通过 finish 改写",
            }), 409
        if (
            run["status"] == "interrupted"
            and interruption_kind not in ("process_restart", "operator_stop")
            and not _flow_finish_pending(run, flow_binding)
        ):
            return jsonify({
                "ok": False, "error": "interrupted_not_restartable",
                "message": "该中断状态不能结束保存",
            }), 409
        if run["status"] in ("succeeded", "partial"):
            return jsonify({
                "ok": False, "error": "already_terminal",
                "status": compat._public_task_status(run["status"], run.get("interruption_kind")),
                "message": "任务已完成，无需结束保存",
            }), 409
        if flow_binding is not None and run["status"] == "failed":
            return jsonify({
                "ok": False,
                "error": "already_terminal",
                "status": compat._public_task_status(
                    run["status"], run.get("interruption_kind")
                ),
                "message": "任务已失败，不能通过结束保存改写",
            }), 409
        allowed_finish_statuses = {
            "queued", "running", "paused", "failed", "interrupted",
        }
        if run["status"] not in allowed_finish_statuses:
            return jsonify({
                "ok": False, "error": "not_paused",
                "status": compat._public_task_status(run["status"], run.get("interruption_kind")),
                "message": "当前任务状态不能结束并保存",
            }), 409
        params = run.get("execution_params") or {}
        scrape_task_id = str(params.get("scrape_task_id") or "")
        source_run_id = str(params.get("source_run_id") or "")
        platform = (
            (flow_binding or {}).get("platform")
            or params.get("platform")
            or run.get("platform")
        )
        if not platform and flow_binding is None and not params.get("flow_id"):
            # Explicitly legacy rows retain their historical BOSS default.  A
            # row carrying any Flow identity must have been resolved above.
            platform = "boss"
        if not platform:
            return jsonify({
                "ok": False,
                "error": "platform_identity_missing",
                "error_code": "platform_identity_missing",
                "message": "任务缺少平台身份，无法结束保存",
            }), 409
        if flow_binding is not None:
            # Check the Track before writing operator-stop metadata, signalling
            # a worker, or creating a result snapshot.  A terminal sibling
            # must win over a late finish request without any side effect.
            bound_track = _bound_track(flow_binding)
            if bound_track is None:
                return jsonify({
                    "ok": False,
                    "error": "flow_task_operation_failed",
                    "error_code": "flow_task_operation_failed",
                    "message": "任务关联运行线无效，无法结束保存",
                }), 409
            current_track_status = str(bound_track.get("status") or "")
            if current_track_status in {
                "failed", "cancelled", "done", "succeeded", "stopped",
            }:
                return jsonify({
                    "ok": False,
                    "error": "already_terminal",
                    "message": "任务状态已变化，无法结束保存",
                }), 409

        # Reserve the exact Flow task before emitting any irreversible
        # operator-stop signal.  The claim is also the durable retry marker
        # when an earlier attempt signalled the worker but failed to publish a
        # result snapshot.
        flow_finish_claim = None
        finish_signal_sent = False
        if flow_binding is not None:
            with ctx.lock:
                latest = ctx.store.get_screening_run(run_id) or run
                if (
                    latest.get("status") == "interrupted"
                    and latest.get("error_code") == "user_finished"
                ) and not _flow_finish_pending(latest, flow_binding):
                    try:
                        repeated = _flow_idempotent_finish(run_id, flow_binding)
                    except Exception:  # noqa: BLE001 - result read failure is observable
                        return _flow_results_unavailable()
                    if repeated is not None:
                        return repeated
                    return jsonify({
                        "ok": False,
                        "error": "already_finished",
                        "message": "任务已结束保存，请勿重复操作",
                    }), 409
                try:
                    flow_finish_claim = ctx.store.claim_flow_finish(
                        flow_binding["flow_id"],
                        flow_binding["platform"],
                        profile_id=flow_binding["profile_id"],
                        task_run_id=run_id,
                    )
                except compat.DiscoveryStoreConflictError as exc:
                    conflict_messages = {
                        "already_finished": "任务已结束保存，请勿重复操作",
                        "already_terminal": "任务已完成，无需结束保存",
                        "user_cancelled": "用户已取消的任务不能结束保存",
                    }
                    conflict_code = str(exc)
                    public_conflict_code = (
                        conflict_code
                        if conflict_code in conflict_messages else "state_conflict"
                    )
                    return jsonify({
                        "ok": False,
                        "error": public_conflict_code,
                        "message": conflict_messages.get(
                            public_conflict_code, "任务状态已变化，无法结束保存"
                        ),
                    }), 409
                except FlowTaskOperationError:
                    return jsonify({
                        "ok": False,
                        "error": "flow_task_operation_failed",
                        "error_code": "flow_task_operation_failed",
                        "message": "流程任务操作失败，请刷新任务状态后重试",
                    }), 409
                except (sqlite3.Error, KeyError, ValueError, RuntimeError):
                    return jsonify({
                        "ok": False,
                        "error": "flow_task_operation_failed",
                        "error_code": "flow_task_operation_failed",
                        "message": "流程任务操作失败，请刷新任务状态后重试",
                    }), 409
                if not flow_finish_claim.get("claimed", True):
                    try:
                        repeated = _flow_idempotent_finish(run_id, flow_binding)
                    except Exception:  # noqa: BLE001 - result read failure is observable
                        return _flow_results_unavailable()
                    if repeated is not None:
                        return repeated
                    return jsonify({
                        "ok": False,
                        "error": "state_conflict",
                        "message": "任务状态已变化，无法结束保存",
                    }), 409

        def _signal_finish(task, stop_event):
            nonlocal finish_signal_sent
            if stop_event is None:
                return
            compat.request_stop(task, stop_event, compat.STOP_MODE_FINISH)
            finish_signal_sent = True

        source_jobs = []
        verdicts = {}
        pending_rows = []
        jd_map = {}
        source_payload = None
        source_dropped = []
        source_total_scraped = None
        flush_run_id = scrape_task_id or (
            run_id if str(run.get("current_stage") or "") == "scrape" else ""
        )
        if flush_run_id:
            with ctx.lock:
                task = ctx.tasks.get(flush_run_id)
                stop_event = task.get("stop_event") if task is not None else None
                flush_lock = task.get("page_flush_lock") if task is not None else None
            if stop_event is not None:
                _signal_finish(task, stop_event)
            if flush_lock is not None:
                stable_since = time.monotonic()
                last_seq = None
                flush_deadline = time.monotonic() + 3.0
                while time.monotonic() < flush_deadline:
                    with ctx.lock:
                        task = ctx.tasks.get(flush_run_id)
                        seq = int((task or {}).get("page_persist_seq") or 0) if task is not None else 0
                    if seq != last_seq:
                        last_seq = seq
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= 0.2:
                        break
                    time.sleep(0.05)
                if flush_lock.acquire(timeout=3.0):
                    flush_lock.release()
        # 结束保存：先把停止信号交给本任务，再按需等当前 JD 批次收尾——批次
        # 结果会在返回后落进断点，等它落盘再取快照，数据更全（wait_for_batch）。
        with ctx.lock:
            own_task = ctx.tasks.get(run_id)
            own_stop_event = own_task.get("stop_event") if own_task is not None else None
        if own_stop_event is not None:
            _signal_finish(own_task, own_stop_event)
        wait_for_batch = bool(
            (request.get_json(silent=True) or {}).get("wait_for_batch")
        )
        waited_for_batch = compat._wait_for_jd_batch_settle(ctx, run_id) if wait_for_batch else False
        if scrape_task_id:
            try:
                source_jobs = ctx.store.load_scrape_run_jobs(scrape_task_id)
            except ctx.operational_errors:
                source_jobs = []
            verdicts = ctx.store.load_screening_verdicts(run_id)
            pending_rows = ctx.store.load_screening_pending(run_id)
            try:
                jd_map = ctx.load_jd_checkpoint(
                    ctx.jd_checkpoint_path(app.config["RESULT_DIR"], run_id))
            except RuntimeError as exc:
                compat._logger.warning(
                    "JD checkpoint read failed during finish (%s)",
                    type(exc).__name__,
                )
                return jsonify({
                    "ok": False, "error": "checkpoint_read_failed",
                    "message": "JD 断点文件损坏，无法生成部分结果",
                }), 503
        elif source_run_id:
            payload = ctx.store.load_latest_pipeline_result(source_run_id)
            source_payload = payload
            source_jobs = ((payload or {}).get("result") or {}).get("jobs") or []
            source_dropped = ((payload or {}).get("result") or {}).get("dropped") or []
            source_total_scraped = ((payload or {}).get("result") or {}).get("total_scraped") or None
            verdicts = ctx.store.load_screening_verdicts(source_run_id)
            pending_rows = ctx.store.load_screening_pending(run_id)
            if not pending_rows:
                pending_rows = ctx.store.load_screening_pending(source_run_id)
        elif not scrape_task_id and not source_run_id and str(run.get("current_stage") or "") == "scrape":
            try:
                source_jobs = ctx.store.load_scrape_run_jobs(run_id)
            except ctx.operational_errors:
                source_jobs = []
            verdicts = ctx.store.load_screening_verdicts(run_id)
            pending_rows = ctx.store.load_screening_pending(run_id)
        profile_summary = str(params.get("profile_summary") or "")
        if not profile_summary and source_run_id:
            if source_payload is None:
                source_payload = ctx.store.load_latest_pipeline_result(source_run_id)
            profile_summary = str(((source_payload or {}).get("result") or {}).get("profile_summary") or "")
        parent_scrape_task_id = scrape_task_id
        if not parent_scrape_task_id:
            parent_scrape_task_id = (
                run_id if str(run.get("current_stage") or "") == "scrape" else ""
            )
        profile_facts = params.get("profile_facts")
        if not isinstance(profile_facts, dict) or not profile_facts:
            profile_facts = None
            if scrape_task_id:
                try:
                    parent_run = ctx.store.get_screening_run(scrape_task_id)
                except ctx.operational_errors:
                    parent_run = None
                parent_facts = ((parent_run or {}).get("execution_params") or {}).get("profile_facts")
                if isinstance(parent_facts, dict) and parent_facts:
                    profile_facts = parent_facts
            if profile_facts is None and source_run_id:
                if source_payload is None:
                    source_payload = ctx.store.load_latest_pipeline_result(source_run_id)
                source_facts = ((source_payload or {}).get("result") or {}).get("profile_facts")
                if isinstance(source_facts, dict) and source_facts:
                    profile_facts = source_facts
        with ctx.lock:
            task = ctx.tasks.get(run_id)
            if task is not None and task.get("stop_event") is not None:
                _signal_finish(task, task["stop_event"])
            ctx.resume_claims.discard(run_id)
        from webui import pipeline_exec as _facade
        from webui.frozen_browser_identity import close_frozen_run_browser
        result = compat.build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map,
            profile_summary,
            source_dropped=source_dropped,
            total_scraped=source_total_scraped,
            platform=platform,
            profile_facts=profile_facts,
            unfiltered=str(run.get("current_stage") or "") == "scrape",
        )
        from webui.screen_flow import build_round_script_params
        from webui.result_rounds import save_finished_round
        preexisting_snapshot_ids = set()

        def _existing_snapshot_ids():
            if not parent_scrape_task_id:
                return set()
            connection = getattr(ctx.store, "_connection", None)
            if not callable(connection):
                return set()
            profile_id = str(
                (flow_binding or {}).get("profile_id")
                or run.get("profile_id")
                or ""
            ).strip()
            if not profile_id:
                return set()
            try:
                with connection() as conn:
                    rows = conn.execute(
                        "SELECT id FROM screening_runs "
                        "WHERE record_kind = 'result_snapshot' "
                        "AND profile_id = ? AND platform = ? "
                        "AND json_extract(execution_params_json, '$.scrape_task_id') = ?",
                        (profile_id, str(platform), str(parent_scrape_task_id)),
                    ).fetchall()
                return {str(row["id"]) for row in rows}
            except Exception:  # noqa: BLE001 - snapshot cleanup remains best-effort
                return set()
        def _save_finish_snapshot():
            return save_finished_round(
                ctx.store,
                result,
                build_round_script_params(
                    ctx.store, run, run.get("frozen_filters") or {}, platform,
                ),
                scrape_task_id=parent_scrape_task_id,
                status="partial",
                execution_config=params.get("execution_config") or {},
                platform=platform,
                profile_summary=profile_summary,
                profile_facts=profile_facts,
                started_at=run.get("started_at"),
                finished_at=int(time.time() * 1000),
            )

        def _rollback_flow_finish(snapshot_run_id=None):
            """Compensate a claimed Flow finish without touching a newer state."""
            if flow_binding is None or not flow_finish_claim:
                return
            if snapshot_run_id and str(snapshot_run_id) not in preexisting_snapshot_ids:
                try:
                    ctx.store.discard_unbound_result_snapshot(
                        snapshot_run_id,
                        flow_id=flow_binding["flow_id"],
                        track_id=flow_binding["track_id"],
                        profile_id=flow_binding["profile_id"],
                    )
                except Exception as cleanup_exc:  # noqa: BLE001
                    compat._logger.warning(
                        "Flow finish snapshot compensation failed: %s",
                        type(cleanup_exc).__name__,
                    )
            # Once the worker has received the irreversible finish signal,
            # preserve the durable claim as ``finish_pending``.  Restoring an
            # active Run here lets a late worker race back into ``running``
            # and makes a retry indistinguishable from a fresh finish.
            if finish_signal_sent or flow_finish_claim.get("finish_pending"):
                return
            try:
                ctx.store.restore_flow_finish_claim(flow_finish_claim)
            except Exception as restore_exc:  # noqa: BLE001
                compat._logger.warning(
                    "Flow finish claim compensation failed: %s",
                    type(restore_exc).__name__,
                )

        # Serialize only the immutable snapshot publication.  A second finish
        # request that races before the first Flow transaction completes reuses
        # the same source/platform round instead of creating an orphan.
        with ctx.lock:
            preexisting_snapshot_ids = _existing_snapshot_ids()
            snapshot_run_id = None
            if (
                flow_finish_claim
                and flow_finish_claim.get("finish_pending")
                and preexisting_snapshot_ids
            ):
                snapshot_run_id = sorted(preexisting_snapshot_ids)[-1]
            latest = ctx.store.get_screening_run(run_id) or run
            if (
                latest.get("status") == "interrupted"
                and latest.get("error_code") == "user_finished"
                and flow_binding is not None
            ):
                try:
                    repeated = _flow_idempotent_finish(run_id, flow_binding)
                except Exception:  # noqa: BLE001 - result read failure is observable
                    return _flow_results_unavailable()
                if repeated is not None:
                    return repeated
            if snapshot_run_id is None:
                try:
                    snapshot_run_id = _save_finish_snapshot()
                except Exception:
                    _rollback_flow_finish()
                    return jsonify({
                        "ok": False,
                        "error": "flow_result_save_failed",
                        "error_code": "flow_result_save_failed",
                        "message": "筛选结果保存失败，请重试",
                    }), 503
        from webui.task_finish_whitebox import finalize_manual_partial_whitebox_or_none
        whitebox_integrity = finalize_manual_partial_whitebox_or_none(
            ctx.store, run, parent_owner_id=parent_scrape_task_id)
        if whitebox_integrity is None:
            _rollback_flow_finish(snapshot_run_id)
            return jsonify({
                "ok": False, "error": "whitebox_incomplete",
                "message": "部分结果已生成，但任务证据未能可靠终结，请重试结束保存",
            }), 503
        try:
            if flow_binding is not None:
                ctx.store.finish_flow_task_atomic(
                    flow_binding["flow_id"],
                    flow_binding["platform"],
                    profile_id=flow_binding["profile_id"],
                    task_run_id=run_id,
                    result_run_id=snapshot_run_id,
                )
            else:
                ctx.store.finish_screening_run(run_id)
        except compat.DiscoveryStoreConflictError as exc:
            _rollback_flow_finish(snapshot_run_id)
            conflict_messages = {
                "already_finished": "任务已结束保存，请勿重复操作",
                "already_terminal": "任务已完成，无需结束保存",
                "user_cancelled": "用户已取消的任务不能结束保存",
            }
            conflict_code = str(exc)
            public_conflict_code = (
                conflict_code if conflict_code in conflict_messages else "state_conflict"
            )
            return jsonify({
                "ok": False, "error": public_conflict_code,
                "message": conflict_messages.get(
                    public_conflict_code, "任务状态已变化，无法结束保存"
                ),
            }), 409
        except FlowTaskOperationError:
            _rollback_flow_finish(snapshot_run_id)
            return jsonify({
                "ok": False,
                "error": "flow_task_operation_failed",
                "error_code": "flow_task_operation_failed",
                "message": "流程任务操作失败，请刷新任务状态后重试",
            }), 409
        except KeyError:
            _rollback_flow_finish(snapshot_run_id)
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        except (sqlite3.Error, ValueError, RuntimeError):
            _rollback_flow_finish(snapshot_run_id)
            # A Flow publication failure must never be reported as a successful
            # finish or leave an unbound result snapshot behind.
            return jsonify({
                "ok": False,
                "error": "flow_result_save_failed",
                "error_code": "flow_result_save_failed",
                "message": "筛选结果保存失败，请重试",
            }), 503
        ctx.clear_auto_screen(run_id)
        if scrape_task_id and scrape_task_id != run_id:
            ctx.clear_auto_screen(scrape_task_id)
        compat.append_task_event_best_effort(
            ctx.store, run_id, "finish", {
                "snapshot_run_id": snapshot_run_id,
                "stage": run.get("current_stage") or "", "jobs": len(result["jobs"]),
                "dropped": len(result["dropped"]),
            }, logger=compat._logger,
        )
        with ctx.lock:
            current = ctx.tasks.get(run_id)
            if current is not None:
                current["status"] = "completed_with_pending"
                current["error"] = "用户提前结束，已保存部分结果"
                current["result"] = result
                current["finished_at"] = int(time.time() * 1000)
        cleanup = close_frozen_run_browser(
            ctx.store, run,
            accounts_path=app.config["BROWSER_ACCOUNTS_PATH"],
            activate=_facade.set_active_cdp_data_dir,
            close=_facade.close_debug_chrome,
        )
        if not cleanup.ok:
            compat.append_task_event_best_effort(
                ctx.store, run_id, "browser_cleanup_failed",
                cleanup.as_dict(), logger=compat._logger,
                context="browser cleanup audit event write failed",
            )
            with ctx.lock:
                current = ctx.tasks.get(run_id)
                if current is not None:
                    current["error"] = "结果已保存，但浏览器清理失败"
        return jsonify({
            "ok": True, "run_id": run_id, "snapshot_run_id": snapshot_run_id,
            "platform": platform,
            "status": "completed_with_pending", "result": result,
            "integrity": whitebox_integrity,
            "scrape_task_id": parent_scrape_task_id,
            "waited_for_batch": waited_for_batch,
            "message": "任务已结束，已完成结果已保存",
            "cleanup": cleanup.as_dict(),
            **({"cleanup_error": "browser_cleanup_failed"}
               if not cleanup.ok else {}),
        })
