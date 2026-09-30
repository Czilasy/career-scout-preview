"""Future completion guards for Flow-owned worker submissions."""
from __future__ import annotations

from webui.ai_screen_failure import persist_ai_worker_failure
from webui.flow_task_state import FlowStateClosureError, close_flow_task_state
from webui.logging_setup import get_logger


_LOGGER = get_logger(__name__)


def _attach_done_callback(future, callback):
    """Attach a completion guard when the executor exposes that contract.

    The production executors return ``concurrent.futures.Future`` objects, but
    a few compatibility executors only expose cancellation (or return
    ``None`` after handing ownership to another runner).  Those objects must
    remain valid resume results instead of failing the HTTP request merely
    because they cannot register a callback.
    """
    if future is None:
        return future
    registrar = getattr(future, "add_done_callback", None)
    if callable(registrar):
        registrar(callback)
    return future


def attach_ai_future_failure(
    future,
    ctx,
    *,
    task_id,
    scrape_task_id,
    platform,
):
    """Persist an AI Track failure if a submitted Future escapes its runner."""
    def _close_failed(done):
        try:
            done.result()
        except BaseException as exc:  # noqa: BLE001 - Future boundary must close state
            try:
                persist_ai_worker_failure(
                    ctx,
                    task_id,
                    scrape_task_id,
                    "flow_ai_start_failed",
                    "AI 筛选启动失败",
                    platform=platform,
                )
            except Exception as persist_exc:  # noqa: BLE001 - retain original failure
                _LOGGER.exception("Flow AI Future failure compensation failed")
                raise FlowStateClosureError() from persist_exc
            _LOGGER.warning(
                "Flow AI Future failed (%s)",
                type(exc).__name__,
            )
    return _attach_done_callback(future, _close_failed)


def attach_scrape_future_failure(
    future,
    ctx,
    *,
    task_id,
    flow_id=None,
    platform="",
    profile_id=None,
):
    """Close a Flow scrape when its submitted Future escapes the runner."""
    def _close_failed(done):
        try:
            done.result()
        except BaseException as exc:  # noqa: BLE001 - Future boundary must close state
            safe_reason = "抓取任务提交失败"
            if flow_id and profile_id:
                try:
                    close_flow_task_state(
                        ctx,
                        task_id=task_id,
                        scrape_task_id=task_id,
                        flow_id=flow_id,
                        profile_id=profile_id,
                        status="failed",
                        error_code="submit_failed",
                        reason=safe_reason,
                        platform=platform,
                        stage="scrape",
                    )
                except Exception as marker_exc:  # noqa: BLE001 - preserve safe failure
                    _LOGGER.exception("Flow scrape Future failure compensation failed")
                    raise FlowStateClosureError() from marker_exc
            else:
                ctx.write_run(
                    task_id,
                    status="failed",
                    current_stage="scrape",
                    error_code="submit_failed",
                    error_reason=safe_reason,
                )
                ctx.store.update_search_run(
                    task_id, status="failed", error_code="submit_failed",
                )
            try:
                with ctx.lock:
                    task = ctx.tasks.get(task_id)
                    if task is not None:
                        task["status"] = "failed"
                        task["error"] = safe_reason
            except Exception as task_exc:  # noqa: BLE001 - keep original failure visible in logs
                _LOGGER.exception("Flow scrape Future task close failed")
                raise FlowStateClosureError() from task_exc
            _LOGGER.warning(
                "Flow scrape Future failed (%s)", type(exc).__name__,
            )
    return _attach_done_callback(future, _close_failed)
