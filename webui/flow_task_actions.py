"""Run identity validation and delegation to the existing task action APIs."""
from webui.store_flow import FlowConflictError


def assert_action_target(track, expected_run_id):
    """An old stage click must never silently select the next stage's run."""
    if expected_run_id is None:
        return
    from webui.flow_task_coordinator import _target_run_ids

    targets = _target_run_ids(track)
    actual = targets[0] if targets else ""
    if str(expected_run_id) != actual:
        raise FlowConflictError("任务阶段已变化，请刷新后操作当前任务")


def delegate_task_pause(run_id):
    """HTTP hosts reuse the canonical pause mode, guard and durable sync path.

    Return None for non-HTTP hosts that inject the coordinator's signal API.
    No task state or lifecycle policy is implemented here.
    """
    from flask import current_app, has_app_context

    if not has_app_context():
        return None
    view = current_app.view_functions.get("api_task_pause")
    if view is None:
        return None
    from webui.flow_task_coordinator import _ensure_operation_succeeded

    _ensure_operation_succeeded(view(run_id))
    return True
