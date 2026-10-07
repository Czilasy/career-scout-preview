"""047 US5：当前尝试写权与迟到回调诊断。

worker 回调只有在「精确实例 + 精确当前绑定」时才可推进 Track/结果；
retry 换 run 或同 run 换 worker 实例之后，旧回调只落安全审计诊断，
不改新尝试、当前结果或已确认终态。
"""

from __future__ import annotations

from webui.flow_task_state import FlowStateClosureError, close_flow_task_state
from webui.logging_setup import get_logger

_logger = get_logger(__name__)

_TRACK_RUN_FIELDS = ("scrape_run_id", "screen_run_id", "result_run_id")
_FINISH_CLAIM_STATUS = "interrupted"
_FINISH_CLAIM_CODE = "user_finished"


def track_run_ids(track: dict | None) -> set[str]:
    """Return the non-empty run ids currently bound to one Track."""
    ids: set[str] = set()
    if not isinstance(track, dict):
        return ids
    for name in ("scrape_run_id", "screen_run_id"):
        value = str(track.get(name) or "").strip()
        if value:
            ids.add(value)
    return ids


def track_binding(store, *, flow_id, platform):
    """Read one Track row (raw) without inventing identity."""
    flow_id = str(flow_id or "").strip()
    platform = str(platform or "").strip().lower()
    if not flow_id or not platform:
        return None
    with store._connection() as conn:
        row = conn.execute(
            "SELECT * FROM flow_tracks WHERE flow_id = ? AND platform = ?",
            (flow_id, platform),
        ).fetchone()
    return dict(row) if row is not None else None


def is_superseded(store, *, run_id, flow_id, platform) -> bool:
    """True when the Track is bound to a different, newer attempt.

    无绑定（尚未 claim/绑定）或绑定包含本 run 时都不算过期：提交补偿、
    首轮 worker 仍走原路径。
    """
    run_id = str(run_id or "").strip()
    if not run_id:
        return False
    track = track_binding(store, flow_id=flow_id, platform=platform)
    if track is None:
        return False
    bound = track_run_ids(track)
    return bool(bound) and run_id not in bound


def finish_claim_pending(store, *, run_id, flow_id, platform) -> bool:
    """True while a user finish claim owns the run but its result is unbound.

    047 C3：claim 后结果尚未提交绑定期间，Track 仍是活动态；迟到的 worker
    失败/暂停命令不得改写 Track，否则用户点的“结束并保存”会丢结果。
    """
    run_id = str(run_id or "").strip()
    if not run_id:
        return False
    run = store.get_screening_run(run_id) or {}
    if str(run.get("status") or "") != _FINISH_CLAIM_STATUS:
        return False
    if str(run.get("error_code") or "") != _FINISH_CLAIM_CODE:
        return False
    if str(run.get("interruption_kind") or "") != _FINISH_CLAIM_CODE:
        return False
    track = track_binding(store, flow_id=flow_id, platform=platform)
    if track is None:
        return False
    return str(track.get("status") or "") not in {
        "done", "succeeded", "failed", "stopped", "cancelled",
    }


def worker_instance_superseded(ctx, run_id, original_task) -> bool:
    """True when resume replaced the worker instance for the same run id.

    run id 相同本身不足以接受旧回调；调用方必须持有本 worker 开始时
    捕获的 task 实例，与 ctx.tasks 的当前实例比较。
    """
    if original_task is None:
        return False
    try:
        with ctx.lock:
            current = ctx.tasks.get(str(run_id or ""))
    except Exception:  # noqa: BLE001 - lock failures must not fake ownership
        _logger.debug("worker instance comparison failed", exc_info=True)
        return False
    return current is not None and current is not original_task


def record_late_callback(store, *, run_id, stage, detail="") -> None:
    """Best-effort safe audit for a callback that no longer owns the run."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return
    try:
        store.append_task_event(run_id, "late_callback", {
            "stage": str(stage or ""),
            "detail": str(detail or "")[:200],
        })
    except Exception:  # noqa: BLE001 - diagnostics must never mask the outcome
        _logger.debug("late_callback audit write failed", exc_info=True)


def sync_blocked_reason(store, *, run_id, flow_id, platform) -> str:
    """Return the reason a late callback must not touch this Track, or ''."""
    if is_superseded(store, run_id=run_id, flow_id=flow_id, platform=platform):
        return "superseded_attempt"
    if finish_claim_pending(
        store, run_id=run_id, flow_id=flow_id, platform=platform,
    ):
        return "finish_pending"
    return ""


def sync_flow_track_after_scrape(ctx, task_id, status, *, error_code="", reason="", defer_auto_screen=False):
    flow_service = None
    flow_id = platform = profile_id = None
    try:
        flow_service = getattr(ctx, "flow_service", None)
        run = ctx.store.get_screening_run(task_id) or {}
        params = run.get("execution_params") or {}
        flow_id = params.get("flow_id")
        platform = run.get("platform") or params.get("platform")
        profile_id = run.get("profile_id")
        if not flow_id or flow_service is None or not platform or not profile_id:
            return
        if status == "finished":
            return
        if status == "done":
            flow_service.mark_scrape_complete(
                flow_id=flow_id, platform=platform, profile_id=profile_id,
            )
            auto_screen = getattr(ctx, "enqueue_auto_screen_for_scrape", None)
            if callable(auto_screen):
                if defer_auto_screen:
                    def handoff():
                        try:
                            auto_screen(task_id)
                            consume = getattr(ctx, "consume_auto_screen", None)
                            if callable(consume):
                                consume(task_id)
                        except Exception as exc:
                            # 调度可能在同平台执行器上内联运行；调用者须先释放全局任务锁。
                            # 真正的调度失败仍走同一可观察收口，不留 running 空壳。
                            sync_flow_track_after_scrape(
                                ctx, task_id, "failed", error_code="flow_ai_start_failed",
                                reason=type(exc).__name__,
                            )
                    return handoff
                auto_screen(task_id)
                consume_auto_screen = getattr(ctx, "consume_auto_screen", None)
                if callable(consume_auto_screen):
                    consume_auto_screen(task_id)
        elif status == "paused":
            # A recoverable source/CDP block is a durable paused Track.  Keep
            # it on the same failure-state path as other marker writes so a
            # later generic exception cannot silently turn it into a stale
            # running/queued Track.
            flow_service.fail_track(
                flow_id=flow_id, platform=platform, profile_id=profile_id,
                error_code=error_code or "source_cdp_unavailable",
                reason=reason or "source temporarily unavailable",
                stage="scrape", status="paused",
            )
        else:
            flow_service.fail_track(
                flow_id=flow_id, platform=platform, profile_id=profile_id,
                error_code=error_code or "scrape_failed", reason=reason or status,
            )
    except Exception as exc:
        # Marker/AI scheduling failures are part of the Track outcome.  The
        # worker has already persisted its run state; close the corresponding
        # Flow Track as well, while keeping a recoverable pause paused.
        try:
            fallback_status = "paused" if status == "paused" else "failed"
            flow_service.fail_track(
                flow_id=flow_id, platform=platform, profile_id=profile_id,
                error_code=(error_code or "source_cdp_unavailable")
                if fallback_status == "paused" else (error_code or "scrape_failed"),
                reason=reason or type(exc).__name__, stage="scrape",
                status=fallback_status,
            )
        except Exception as flow_error:  # noqa: BLE001 - preserve the marker error
            from webui.logging_setup import get_logger
            get_logger(__name__).debug(
                "B096 Flow scrape marker failure write failed (%s)",
                type(flow_error).__name__,
            )
            # The fallback failed too.  Try the single SQLite closure path so
            # a terminal scrape run cannot leave its Flow Track active; the
            # safe exception remains observable to the worker caller.
            try:
                if flow_id and platform and profile_id:
                    close_flow_task_state(
                        ctx,
                        task_id=task_id,
                        scrape_task_id=task_id,
                        flow_id=flow_id,
                        profile_id=profile_id,
                        status="paused" if status == "paused" else "failed",
                        error_code=(error_code or "source_cdp_unavailable")
                        if status == "paused" else (error_code or "scrape_failed"),
                        reason="抓取流程状态收口失败",
                        platform=platform,
                        stage="scrape",
                    )
            except Exception as closure_error:  # noqa: BLE001
                get_logger(__name__).debug(
                    "B096 Flow scrape atomic closure failed (%s)",
                    type(closure_error).__name__,
                )
            raise FlowStateClosureError() from flow_error

__all__ = [
    "track_run_ids", "track_binding", "is_superseded",
    "finish_claim_pending", "worker_instance_superseded",
    "record_late_callback", "sync_blocked_reason",
    "sync_flow_track_after_scrape",
]
