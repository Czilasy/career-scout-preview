"""047 结构前置：普通 Track 动作编排（自 FlowService.operate_track 搬运）。

保持原校验顺序、回调注入、事件与返回；FlowService.operate_track 只做薄委托。
"""

from __future__ import annotations

from webui.flow_errors import public_flow_message
from webui.store_flow import FlowConflictError


def operate_flow_task_with_reason(ctx, action, platform, flow, track):
    """Keep a continuation's registered reason before the legacy success guard.

    The scoped context shares the real store/worker objects and invokes the
    existing continuation once. Other task callers retain their HTTP contract.
    """
    from copy import copy
    from webui.flow_task_coordinator import operate_bound_task

    if action == "stop" and track.get("status") in {"paused", "interrupted"}:
        from webui.flow_task_coordinator import _target_run_ids, resolve_flow_binding
        run_ids = _target_run_ids(track)
        lock = getattr(ctx, "lock", None)
        if run_ids and lock is not None:
            with lock:
                run_id = run_ids[0]
                if not getattr(ctx, "tasks", {}).get(run_id):
                    run = ctx.store.get_screening_run(run_id) or {}
                    binding = resolve_flow_binding(ctx, run_id)
                    if run.get("status") in {"paused", "interrupted"} and binding and (
                        binding["flow_id"] == str(flow.get("id"))
                        and binding["track_id"] == str(track.get("id"))
                        and binding["profile_id"] == str(flow.get("profile_id"))
                        and binding["platform"] == platform
                    ):
                        # 冷启动后的暂停无进程 worker，终止仍必须关闭当前绑定的持久化事实。
                        # 身份读取与取消保持在任务锁内，避免与同 run 接续注册交错。
                        import sqlite3
                        from webui.flow_task_coordinator import FlowTaskOperationError
                        try:
                            ctx.store.cancel_task_atomic(
                                run_id, flow_binding=binding, sync_track=True,
                                track_status="stopped", allow_interrupted_projection=True,
                            )
                        except (KeyError, ValueError, sqlite3.Error, RuntimeError, TypeError) as exc:
                            raise FlowTaskOperationError("cold task cancellation failed") from exc
                        return True
    if action != "resume":
        return operate_bound_task(ctx, action, platform, flow, track)

    from collections.abc import Mapping
    from flask import current_app
    from webui.error_registry import ALIAS_TO_CODE, ERROR_CODES, ERROR_USER_MESSAGES
    from webui.flow_errors import FlowResumeError

    def preserve_reason(callback):
        def invoke(*args, **kwargs):
            response = callback(*args, **kwargs)
            payload_response = response[0] if isinstance(response, tuple) else response
            status = response[1] if isinstance(response, tuple) and len(response) > 1 else getattr(response, "status_code", 200)
            getter = getattr(payload_response, "get_json", None)
            payload = getter(silent=True) if callable(getter) else payload_response
            if isinstance(payload, Mapping) and (payload.get("ok") is False or int(status) >= 400):
                raw_code = str(payload.get("error_code") or "")
                code = raw_code if raw_code in ERROR_CODES else ALIAS_TO_CODE.get(raw_code)
                if code:
                    raise FlowResumeError(code, ERROR_USER_MESSAGES[code])
            return response
        return invoke

    scoped = copy(ctx)
    for name in ("continue_execute_search", "continue_flow_task", "operate_flow_task"):
        callback = getattr(ctx, name, None)
        if callable(callback):
            setattr(scoped, name, preserve_reason(callback))
    if not callable(getattr(scoped, "continue_flow_task", None)):
        def continue_task(task_id, _platform, _flow, _track):
            view = current_app.view_functions.get("api_task_continue")
            return view(task_id) if callable(view) else None
        scoped.continue_flow_task = preserve_reason(continue_task)
    return operate_bound_task(scoped, action, platform, flow, track)


def operate_track(service, *, flow_id, platform, profile_id, action, expected_run_id=None, expected_updated_at=None, expected_track_id=None) -> dict:
    action = str(action or "").strip().lower()
    platform = str(platform or "").strip().lower()
    if action == "retry":
        # 047 C2：failed 单轨重试是独立动作，不复用 resume/stop 的终态门禁。
        retry = getattr(service, "retry_track", None)
        if not callable(retry):
            raise ValueError("retry action unavailable")
        return retry(
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            expected_run_id=expected_run_id,
            expected_updated_at=expected_updated_at,
            expected_track_id=expected_track_id,
        )
    target_status = {
        "pause": "paused",
        "resume": "running",
        "stop": "stopped",
    }.get(action)
    if target_status is None:
        raise ValueError("action must be pause, resume, stop, or retry")
    flow = service.store.get_flow(flow_id, profile_id=profile_id)
    track = next(
        (item for item in flow["tracks"] if item["platform"] == platform),
        None,
    )
    if track is None:
        raise KeyError(f"{flow_id}:{platform}")
    from webui.flow_task_actions import assert_action_target

    assert_action_target(track, expected_run_id)
    if action == "pause" and service.track_finalizing is not None:
        if service.track_finalizing(flow, track):
            raise FlowConflictError("finalizing")
    if (
        action == "resume"
        and track.get("status") == "paused"
        and not any(
            track.get(key)
            for key in ("scrape_run_id", "screen_run_id")
        )
    ):
        if track.get("result_run_id"):
            raise FlowConflictError("已有结果的平台运行线不能再次继续")
        if not track.get("submission_snapshot"):
            raise FlowConflictError("暂停的平台运行线缺少可重试的搜索快照")
        return service.resume_preflight_track(
            flow=flow, track=track, profile_id=profile_id,
        )
    if track.get("status") == target_status:
        return flow
    if track.get("status") in {"done", "succeeded", "failed", "stopped", "cancelled"}:
        raise FlowConflictError("已结束的平台运行线不能再次操作")
    from webui.flow_task_coordinator import (
        FlowTaskOperationError,
        _target_run_ids,
    )

    target_run_ids = _target_run_ids(track)
    if action == "stop" and not target_run_ids:
        stop_without_run = getattr(
            service.store, "stop_flow_track_without_run", None,
        )
        if not callable(stop_without_run):
            raise FlowTaskOperationError("missing atomic no-run stop boundary")
        stopped_flow = stop_without_run(
            flow_id,
            platform,
            profile_id=profile_id,
        )
        service._record_track_event(
            flow_id,
            platform,
            stopped_flow,
            "flow_track_stop",
            {"action": action, "status": target_status},
        )
        return service.store.get_flow(flow_id, profile_id=profile_id)
    if target_run_ids and service.operate_track_callback is None:
        # A durable Run is an instruction to operate a real worker, not a
        # reason to publish a Track status optimistically.  Production
        # injects the shared coordinator callback; direct service users
        # must fail closed when that dependency is absent.
        raise FlowTaskOperationError(
            "Flow task operation callback is unavailable"
        )
    if service.operate_track_callback is not None:
        operation_ctx = getattr(service, "operation_context", None)

        if target_run_ids:
            from webui.flow_task_coordinator import resolve_flow_binding

            if operation_ctx is None:
                from types import SimpleNamespace

                operation_ctx = SimpleNamespace(store=service.store)
            binding = resolve_flow_binding(operation_ctx, target_run_ids[0])
            if binding is None or (
                binding["flow_id"] != str(flow_id)
                or binding["track_id"] != str(track.get("id") or "")
                or binding["platform"] != platform
                or binding["profile_id"] != str(profile_id)
            ):
                raise FlowTaskOperationError(
                    "Flow action target is not exactly bound to its Track"
                )
        operated = service.operate_track_callback(action, platform, flow, track)
        # Legacy callbacks may return Flask-style ``(payload, status)``
        # responses.  Treat every explicit failure as an operation error;
        # Track publication is only allowed after a real target action.
        from webui.flow_task_coordinator import _ensure_operation_succeeded
        try:
            _ensure_operation_succeeded(operated)
        except FlowTaskOperationError:
            raise
    from webui.flow_task_coordinator import _normalize_track_stage

    updated = service.store.update_flow_track(
        flow_id,
        platform,
        profile_id=profile_id,
        status=target_status,
        stage=_normalize_track_stage(track.get("stage") or "scrape"),
    )
    service._record_track_event(
        flow_id,
        platform,
        updated,
        f"flow_track_{action}",
        {"action": action, "status": target_status},
    )
    return service.store.get_flow(flow_id, profile_id=profile_id)
