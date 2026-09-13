"""暂停编排助手（025-screen-pause-round-reset，B076/B077）。

`webui/task_continue_api.py` 存量超行数预警线，暂停 API 的编排逻辑外迁至此，
`task_continue_api.py` 只保留路由/参数校验与响应组装（宪法：api 层只做组装）。

- ``pause_with_mode``：暂停 API 全量编排——校验 + graceful/immediate 分支 +
  幂等 + guard 联动（终止活动批子进程并清理批次登记）。
- ``cancel_task_cleanup``：取消路径清理 guard 批次登记（避免「继续」后被
  旧登记误判卡死触发额外重抓）。

引用方向：`task_continue_api.py → task_pause_support.py → ctx.pipeline_guard`
（经 ctx 注入，本文件不反向 import app / store / task_continue_api）。
"""

from __future__ import annotations

import threading

from flask import jsonify

from webui.constants import _MSG_TASK_NOT_FOUND
from webui.error_registry import ERROR_USER_MESSAGES, resolve_code
from webui.logging_setup import get_logger
from webui.task_event_audit import append_task_event_best_effort

_logger = get_logger("task_pause_support")


STOP_MODE_PAUSE = "pause"
STOP_MODE_CANCEL = "cancel"
STOP_MODE_FINISH = "finish"
STOP_MODE_TERMINATE = "terminate"

_STOP_MODE_PRIORITY = {
    STOP_MODE_PAUSE: 1,
    STOP_MODE_CANCEL: 2,
    STOP_MODE_FINISH: 2,
    STOP_MODE_TERMINATE: 2,
}
_STOP_MODE_TIE_BREAK = {
    STOP_MODE_PAUSE: 0,
    STOP_MODE_CANCEL: 1,
    STOP_MODE_TERMINATE: 2,
    STOP_MODE_FINISH: 3,
}
_STOP_MODE_LOCK = threading.RLock()


class ScrapeCheckpointReadError(RuntimeError):
    """Raised when a scrape checkpoint cannot be read safely."""

    error_code = "checkpoint_read_failed"
    public_reason = "暂停断点读取失败，任务已结束，请重试"

    def __init__(self, *, pause_persisted: bool = False):
        super().__init__(self.public_reason)
        self.pause_persisted = bool(pause_persisted)


class ScrapeCheckpointWriteError(RuntimeError):
    """Raised when a scrape checkpoint cannot be durably saved."""

    error_code = "checkpoint_write_failed"
    public_reason = "暂停断点保存失败，任务已结束，请重试"

    def __init__(self, *, pause_persisted: bool = False):
        super().__init__(self.public_reason)
        self.pause_persisted = bool(pause_persisted)


def is_user_paused_run(run: dict | None) -> bool:
    """Return whether a paused run represents an explicit user pause.

    System failures used to be stored as ``paused`` by older workers, so an
    explicit non-pause code (or failure reason) must not retain the
    resumable/occupying semantics. A legacy paused row without any error
    metadata remains conservatively resumable for backward compatibility.
    """
    if not isinstance(run, dict) or run.get("status") != "paused":
        return False
    error_code = str(run.get("error_code") or "").strip().lower()
    if error_code:
        return error_code == "user_paused"
    reason = str(run.get("error_reason") or "").strip()
    if reason:
        return "用户已暂停" in reason or "用户主动暂停" in reason
    return True


def _normalize_stop_mode(mode: object) -> str:
    value = str(mode or "").strip().lower()
    if value in _STOP_MODE_PRIORITY:
        return value
    return STOP_MODE_CANCEL


def _strongest_stop_mode(task: dict | None, stop_event) -> str | None:
    modes = []
    task_mode = str((task or {}).get("stop_mode") or "").strip().lower()
    if task_mode in _STOP_MODE_PRIORITY:
        modes.append(task_mode)
    event_mode = str(getattr(stop_event, "stop_mode", "") or "").strip().lower()
    if event_mode in _STOP_MODE_PRIORITY:
        modes.append(event_mode)
    if not modes:
        return None
    return max(
        modes,
        key=lambda value: (
            _STOP_MODE_PRIORITY[value],
            _STOP_MODE_TIE_BREAK[value],
        ),
    )


def set_stop_mode(task: dict, stop_event, mode: str) -> str:
    """Record the shared stop reason on both task and stop signal.

    ``pipeline_exec_search`` only receives the event, while runners also have
    the task snapshot.  Keeping the same small marker in both places makes a
    stop reason survive either call path without teaching the search loop
    about a platform or a route.
    """
    requested = _normalize_stop_mode(mode)
    with _STOP_MODE_LOCK:
        current = _strongest_stop_mode(task, stop_event)
        normalized = max(
            (current, requested),
            key=lambda value: (
                _STOP_MODE_PRIORITY[value],
                _STOP_MODE_TIE_BREAK[value],
            ),
        ) if current is not None else requested
        task["stop_mode"] = normalized
        if stop_event is not None:
            try:
                stop_event.stop_mode = normalized
            except (AttributeError, TypeError):
                # Event-like test doubles may use slots; the task marker remains
                # the authoritative value for runners.
                return normalized
        return normalized


def request_stop(task: dict, stop_event, mode: str) -> str:
    """Atomically publish a prioritized stop mode and fire its signal.

    Pause is deliberately lower priority than terminal cleanup, finish, and
    cancel.  Keeping the mode write and ``Event.set`` under one process-local
    lock prevents an interleaved pause request from changing the reason a
    worker observes.
    """
    with _STOP_MODE_LOCK:
        normalized = set_stop_mode(task, stop_event, mode)
        if stop_event is not None:
            stop_event.set()
        return normalized


def stop_mode_for_event(stop_event, task: dict | None = None) -> str | None:
    """Return ``pause``/``cancel`` only after an event has actually fired."""
    if stop_event is None or not stop_event.is_set():
        return None
    with _STOP_MODE_LOCK:
        return _strongest_stop_mode(task, stop_event) or STOP_MODE_CANCEL


def continue_task_kind(ctx, run_id: str, run: dict | None = None) -> str:
    """Resolve the continuation kind, preferring the live task declaration.

    An AI hard-stop can persist ``current_stage='scrape'`` while it is
    materialising its source snapshot.  The in-memory task kind is the only
    reliable discriminator in that window; after a restart, the persisted
    stage remains the compatibility fallback for legacy scrape runs.
    """
    with ctx.lock:
        task = ctx.tasks.get(run_id)
        live_kind = str((task or {}).get("kind") or "").strip().lower()
    if live_kind in {"scrape", "ai_screen", "recrawl"}:
        return live_kind
    stage = str((run or {}).get("current_stage") or "").strip().lower()
    if stage.startswith("recrawl_"):
        return "recrawl"
    if stage == "scrape":
        params = (run or {}).get("execution_params") or {}
        if params.get("scrape_task_id") and not params.get("script_params"):
            return "ai_screen"
        return "scrape"
    return "ai_screen"


def _record_checkpoint_read_failure(
        ctx, run_id: str, *, completed_combos=None, source_count=0,
        total_scraped=0, error_code=None, reason=None) -> bool:
    """Persist a failed state without replacing checkpoint data.

    A strict read or write failure is itself a terminal task error.  A
    corrupt checkpoint row must remain byte-for-byte intact, but the run must
    not be left as ``running`` or ``paused`` after its worker exits (or after
    a continue request rejects). All writes here are status/event writes
    only; no checkpoint save is ever attempted.
    """
    error_code = error_code or ScrapeCheckpointReadError.error_code
    reason = reason or ScrapeCheckpointReadError.public_reason
    completed = [
        str(key) for key in (completed_combos or []) if str(key).strip()
    ]
    # A continue request may fail before it has an in-memory result.  Keep the
    # durable counters as a floor so publishing the paused failure cannot make
    # a refresh look like a brand-new zero-progress run.
    existing_run = None
    getter = getattr(ctx.store, "get_screening_run", None)
    if callable(getter):
        try:
            existing_run = getter(run_id)
        except ctx.operational_errors:
            existing_run = None
    existing_run = existing_run or {}
    processed_count = max(
        len(completed), int(existing_run.get("processed_count") or 0),
    )
    durable_source_count = max(
        int(source_count or 0), int(existing_run.get("source_count") or 0),
    )
    durable_total_scraped = max(
        int(total_scraped or 0), int(existing_run.get("total_scraped") or 0),
    )
    status_persisted = False
    writer = getattr(ctx, "write_run", None)
    if callable(writer):
        try:
            writer(
                run_id,
                status="failed",
                current_stage="scrape",
                error_code=error_code,
                error_reason=reason,
                processed_count=processed_count,
                source_count=durable_source_count,
                total_scraped=durable_total_scraped,
            )
            status_persisted = True
        except Exception as exc:
            _logger.warning(
                "scrape checkpoint failed state write failed stage=scrape "
                "error_type=%s",
                type(exc).__name__,
            )
    payload = {
        "stage": "scrape",
        "code": error_code,
        "error_code": error_code,
        "completed_combos": len(completed),
        "checkpoint_read": "failed" if error_code == ScrapeCheckpointReadError.error_code else "ok",
        "checkpoint_write": "failed" if error_code == ScrapeCheckpointWriteError.error_code else "ok",
    }
    try:
        ctx.store.append_task_event(run_id, "failure", payload)
    except Exception as exc:
        _logger.warning(
            "scrape checkpoint failure event failed stage=scrape error_type=%s",
            type(exc).__name__,
        )
    _logger.error(
        "scrape checkpoint persistence failed stage=scrape error_code=%s",
        error_code,
    )
    recorder = getattr(ctx, "record_pause_failure", None)
    if callable(recorder):
        try:
            recorder(
                run_id,
                "scrape",
                error_code,
                reason,
                processed=len(completed),
                total=max(durable_source_count, len(completed)),
                extra={
                    "checkpoint_stage": "scrape",
                    "checkpoint_read": error_code == ScrapeCheckpointReadError.error_code,
                    "checkpoint_write": error_code == ScrapeCheckpointWriteError.error_code,
                },
            )
            with ctx.lock:
                task = ctx.tasks.get(run_id)
                if task is not None:
                    task["status"] = "failed"
                    task["error"] = reason
            return status_persisted
        except Exception as exc:
            _logger.warning(
                "scrape checkpoint failure audit failed stage=scrape "
                "error_type=%s",
                type(exc).__name__,
            )
    try:
        from webui.diagnostics import record_failure

        record_failure(
            ctx.store,
            run_id,
            stage="scrape",
            error_code=error_code,
            reason=reason,
            correlation_id=run_id,
            diagnostics={
                "checkpoint_stage": "scrape",
                "checkpoint_read": error_code == ScrapeCheckpointReadError.error_code,
                "checkpoint_write": error_code == ScrapeCheckpointWriteError.error_code,
            },
        )
    except Exception as exc:
        _logger.warning(
            "scrape checkpoint failure event failed stage=scrape error_type=%s",
            type(exc).__name__,
        )
    with ctx.lock:
        task = ctx.tasks.get(run_id)
        if task is not None:
            task["status"] = "failed"
            task["error"] = reason
    return status_persisted


def scrape_checkpoint(ctx, run_id: str, *, completed_combos=None,
                      skip_combos=None) -> list[str]:
    """Merge in-memory and durable scrape combo checkpoints.

    The DB checkpoint is authoritative after a worker boundary.  Unioning it
    with the current result and the resume skip set keeps a pause from
    regressing a completed combination when the stop arrives between two
    callbacks or immediately after a continuation starts.
    """
    keys = {
        str(key) for key in (completed_combos or [])
        if str(key).strip()
    }
    keys.update(
        str(key) for key in (skip_combos or [])
        if str(key).strip()
    )
    try:
        loader = getattr(ctx.store, "load_checkpoint_strict", None)
        if not callable(loader):
            # A lax reader silently turns corrupt JSON into an empty set and
            # would violate the pause/resume safety boundary.  Treat an
            # adapter without the strict API as a read failure instead.
            raise RuntimeError("strict checkpoint reader unavailable")
        keys.update(str(key) for key in loader(run_id, "scrape"))
    except ctx.operational_errors:
        persisted = _record_checkpoint_read_failure(
            ctx,
            run_id,
            completed_combos=keys,
        )
        raise ScrapeCheckpointReadError(pause_persisted=persisted) from None
    return sorted(keys)


def mark_scrape_paused(ctx, run_id: str, *, completed_combos=None,
                       skip_combos=None, source_count=0, total_scraped=0,
                       reason="用户已暂停，结果已保留",
                       error_code="user_paused") -> list[str]:
    """Persist a scrape pause/block and its public event."""
    completed = scrape_checkpoint(
        ctx, run_id, completed_combos=completed_combos,
        skip_combos=skip_combos,
    )
    existing_run = None
    getter = getattr(ctx.store, "get_screening_run", None)
    if callable(getter):
        try:
            existing_run = getter(run_id)
        except ctx.operational_errors:
            existing_run = None
    existing_run = existing_run or {}
    ctx.write_run(
        run_id, status="paused", current_stage="scrape",
        error_code=error_code, error_reason=reason,
        processed_count=max(
            len(completed), int(existing_run.get("processed_count") or 0),
        ),
        source_count=max(
            int(source_count or 0), int(existing_run.get("source_count") or 0),
        ),
        total_scraped=max(
            int(total_scraped or 0), int(existing_run.get("total_scraped") or 0),
        ),
    )
    try:
        ctx.store.save_checkpoint(run_id, "scrape", completed)
    except ctx.operational_errors:
        persisted = _record_checkpoint_read_failure(
            ctx,
            run_id,
            completed_combos=completed,
            source_count=source_count,
            total_scraped=total_scraped,
            error_code=ScrapeCheckpointWriteError.error_code,
            reason=ScrapeCheckpointWriteError.public_reason,
        )
        raise ScrapeCheckpointWriteError(pause_persisted=persisted) from None
    append_task_event_best_effort(
        ctx.store, run_id, "pause", {
            "stage": "scrape", "code": error_code,
            "completed_combos": len(completed),
            "reason": reason,
        },
        logger=_logger,
        context="scrape pause audit event write failed",
    )
    with ctx.lock:
        task = ctx.tasks.get(run_id)
        if task is not None:
            task["status"] = "paused"
            task["error"] = reason
    return completed


def normalize_recoverable_failed_run(ctx, run: dict | None) -> dict | None:
    """Migrate a legacy recoverable ``failed`` row into ``paused`` once.

    This is only a compatibility bridge for rows written before the shared
    hard-stop fix.  It changes status and canonical error metadata only;
    checkpoints, counters, platform and execution identity remain untouched.
    """
    if not isinstance(run, dict) or run.get("status") != "failed":
        return run
    from webui.error_registry import is_recoverable_systemic_block

    raw_code = str(run.get("error_code") or "").strip()
    if not is_recoverable_systemic_block(raw_code):
        return run
    code = resolve_code(raw_code, default=raw_code)
    params = run.get("execution_params") or {}
    platform = str(run.get("platform") or params.get("platform") or "")
    reason = str(run.get("error_reason") or "").strip()
    if not reason:
        reason = ERROR_USER_MESSAGES.get(code, code)
    ctx.store.update_screening_run(
        run["id"], status="paused",
        current_stage=str(run.get("current_stage") or "scrape"),
        error_code=code, error_reason=reason,
    )
    append_task_event_best_effort(
        ctx.store, run["id"], "pause", {
            "stage": str(run.get("current_stage") or "scrape"),
            "code": code,
            "reason": reason,
            "legacy_recovery": True,
            "platform": platform,
        },
        logger=_logger,
        context="legacy failed recovery audit event write failed",
    )
    with ctx.lock:
        task = ctx.tasks.get(run["id"])
        if task is not None:
            task["status"] = "paused"
            task["error"] = reason
    refreshed = ctx.store.get_screening_run(run["id"])
    return refreshed or {**run, "status": "paused", "error_code": code,
                         "error_reason": reason}




class ImmediateOnlyCancelEvent:
    """把任务 stop_event 适配成抓取源的 cancel_event：仅立即停止时视为置位。

    in-process（EXE）模式没有子进程，guard 杀不到，scrape_details 的逐条
    检查点是批内唯一中断手段——信号必须接到 source.cancel_event。graceful
    （等这批抓完）不置位，批次照常跑完批边界停止，语义不变。

    ``set()`` 刻意不回写 stop_event：run_with_deadline 超时路径会调 set()
    请求协作停止，超时≠用户暂停，保持 no-op 与历史行为（cancel_event=None）一致。
    """

    def __init__(self, stop_event):
        self._stop_event = stop_event

    def is_set(self) -> bool:
        return bool(
            self._stop_event is not None
            and self._stop_event.is_set()
            and getattr(self._stop_event, "immediate", False)
        )

    def set(self) -> None:
        return None


def pause_with_mode(ctx, run_id: str, mode: str):
    """暂停可恢复任务（抓取、AI 筛选或重抓）。

    平台抓取脚本只由 runner/source 选择，暂停编排保持平台无关。
    （025：支持 mode=immediate 批中立即停止）。

    ``mode`` 取值：
    - ``graceful``（缺省）：现状行为——stop_mode="pause" + stop_event.set()，
      worker 在安全边界（批间/批完）停止，数据完整。
    - ``immediate``：立即停止——任务落「已暂停」语义（非取消）、stop_event
      携带 immediate 信号（fetch_job_details 据此作废当前批）、终止活动批
      子进程并清理 guard 批次登记；已暂停/已 immediate 幂等返回 ok（不 409）。
    """
    with ctx.lock:
        task = ctx.tasks.get(run_id)
        if task is None:
            return jsonify({
                "ok": False, "error": "run_not_found",
                "message": _MSG_TASK_NOT_FOUND,
            }), 404
        if task.get("kind") not in ("ai_screen", "recrawl", "scrape"):
            return jsonify({
                "ok": False, "error": "not_pausable_task",
                "message": "只有可暂停任务可以暂停",
            }), 409
        if task.get("finalizing"):
            # 收尾区（事实已定）：任务正在关浏览器/写终态，暂停请求只回执、
            # 不执行——收尾结论统一结算（完成优先），随后任务即到终态。
            return jsonify({
                "ok": True, "run_id": run_id, "status": "finalizing",
                "message": "任务正在收尾，暂停未执行",
            }), 200
        if task["status"] not in ("queued", "running"):
            if mode == "immediate":
                # 025：已暂停/已终态再点立即停止 → 幂等不报错
                return jsonify({
                    "ok": True, "run_id": run_id, "status": "paused",
                }), 200
            return jsonify({
                "ok": False, "error": "task_not_active",
                "message": f"任务当前状态（{task['status']}）不能暂停",
            }), 409
        run = ctx.store.get_screening_run(run_id)
        if run is not None and run.get("status") not in ("queued", "running"):
            if mode == "immediate":
                return jsonify({
                    "ok": True, "run_id": run_id, "status": "paused",
                }), 200
            return jsonify({
                "ok": False, "error": "task_not_active",
                "message": f"任务当前状态（{run.get('status')}）不能暂停",
            }), 409
        stop_event = task.get("stop_event")
        if stop_event is None:
            return jsonify({
                "ok": False, "error": "stop_signal_unavailable",
                "message": "任务缺少停止信号，无法暂停",
            }), 409
        if task.get("immediate_stop"):
            # 025：已 immediate 再调 → 幂等（不重复清理）
            return jsonify({
                "ok": True, "run_id": run_id, "status": "pausing",
            }), 200
        if mode == "immediate":
            task["immediate_stop"] = True
            stop_event.immediate = True  # fetch_job_details 据此作废当前批
        request_stop(task, stop_event, STOP_MODE_PAUSE)
    if mode == "immediate":
        # 025：终止活动批子进程 + 清理批次登记（锁外，可能耗时）
        guard = getattr(ctx, "pipeline_guard", None)
        if guard is not None:
            try:
                guard.immediate_stop_task(run_id)
            except Exception:
                # 清理失败不阻断暂停（幂等兜底）
                _logger.exception("immediate_stop_task 异常（已忽略）")
    return jsonify({"ok": True, "run_id": run_id, "status": "pausing"})


def cancel_task_cleanup(ctx, run_id: str) -> None:
    """取消路径清理 guard 批次登记（025 B076，best-effort）。"""
    guard = getattr(ctx, "pipeline_guard", None)
    if guard is not None:
        try:
            guard.immediate_stop_task(run_id)
        except Exception:
            _logger.exception("cancel_task_cleanup 异常（已忽略）")


def scrape_completion_evidence(ctx, run_id: str) -> bool:
    """是否为「事实已完成」的抓取 run（收尾族：纠正误写暂停的判定依据）。

    与 /api/latest-running-task 的孤儿补写同口径：断点覆盖全部组合、已抓
    岗位非空、白箱结论 succeeded/empty 且证据完整。任何读取异常一律返回
    False——绝不拿不确定的证据把暂停任务改成完成。
    """
    try:
        run = ctx.store.get_screening_run(run_id)
    except ctx.operational_errors:
        return False
    if run is None:
        return False
    source_total = int(run.get("source_count") or 0)
    if source_total <= 0:
        return False
    try:
        checkpoint_done = len(ctx.store.load_checkpoint(run_id, "scrape"))
        scraped_count = int(ctx.store.count_scrape_run_jobs(run_id))
    except ctx.operational_errors:
        return False
    if checkpoint_done < source_total or scraped_count <= 0:
        return False
    try:
        from webui.whitebox import WhiteboxService
        integrity = WhiteboxService(ctx.store).report("scrape", run_id)["integrity"]
    except Exception:
        return False
    if not isinstance(integrity, dict):
        return False
    return (
        str(integrity.get("conclusion") or "") in {"succeeded", "empty"}
        and bool(integrity.get("evidence_complete"))
    )
