"""Shared run-to-Flow coordination for legacy and Flow task actions.

The HTTP routes remain backward compatible, but every action that can mutate a
real task also publishes the matching Flow Track fact through this module.  A
missing platform is an observable identity error; it is never guessed from a
legacy default.
"""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3

from webui.store_flow import FlowConflictError
from webui.task_pause_support import (
    STOP_MODE_CANCEL,
    STOP_MODE_PAUSE,
    request_stop,
)


class MissingPlatformIdentityError(ValueError):
    """A Flow-owned run cannot be safely associated with a platform Track."""

    error_code = "platform_identity_missing"


class FlowTaskOperationError(RuntimeError):
    """The target task operation failed before its Track could be published."""

    error_code = "flow_task_operation_failed"


class FlowTrackTaskUnavailableError(FlowTaskOperationError):
    """The Track has no durable run to operate at all.

    Distinct from a general operation failure: the caller must tell the user
    this line has no task to act on instead of reporting a retry-the-same
    action hint, because refreshing alone cannot create the missing run.
    """

    error_code = "flow_track_task_unavailable"


# The durable Flow model has one AI lane even while the worker reports the
# finer-grained units used for progress and white-box evidence.  Keep those
# worker stages out of the scrape lane when selecting the run to operate.
_AI_TRACK_SUBSTAGES = frozenset({"ai_rough", "jd_detail", "ai_fine"})
_AI_TRACK_STAGE_PREFIXES = ("ai_", "jd_", "screen_", "recrawl_ai")


def _is_ai_track_stage(stage) -> bool:
    value = str(stage or "").strip().lower()
    return (
        value in {"ai", "screen", "complete", *_AI_TRACK_SUBSTAGES}
        or value.startswith(_AI_TRACK_STAGE_PREFIXES)
    )


def _normalize_track_stage(stage, fallback="scrape") -> str:
    value = str(stage or fallback or "").strip().lower()
    if value in _AI_TRACK_SUBSTAGES or value.startswith(_AI_TRACK_STAGE_PREFIXES):
        return "ai"
    return value or "scrape"


def _track_stage_for_run(track: Mapping, run_id: str, stage=None) -> str:
    """Map a worker checkpoint to the durable lane that owns the run.

    A worker can report ``current_stage='scrape'`` while an AI run is
    materialising or consuming its source snapshot.  The run-to-Track binding
    is the stronger fact: a ``screen_run_id`` is always the AI lane, while a
    ``scrape_run_id`` is the source lane.  Only unbound legacy calls use the
    checkpoint string as a fallback.
    """
    run_id = str(run_id or "").strip()
    screen_run_id = str(track.get("screen_run_id") or "").strip()
    scrape_run_id = str(track.get("scrape_run_id") or "").strip()
    if run_id and run_id == screen_run_id:
        return "ai"
    if run_id and run_id == scrape_run_id:
        return "scrape"
    return _normalize_track_stage(
        stage if stage is not None else track.get("stage"),
        fallback=track.get("stage") or "scrape",
    )


def _ensure_operation_succeeded(result):
    """Reject an HTTP-style failure returned by a legacy operation hook."""
    if result is None:
        raise FlowTaskOperationError("target task operation returned no acknowledgement")
    if result is False or (
        isinstance(result, Mapping) and result.get("ok") is False
    ):
        raise FlowTaskOperationError("target task operation was rejected")
    get_json = getattr(result, "get_json", None)
    if callable(get_json):
        try:
            payload = get_json(silent=True)
        except TypeError:
            payload = get_json()
        if isinstance(payload, Mapping) and payload.get("ok") is False:
            raise FlowTaskOperationError("target task operation was rejected")
    status_code = getattr(result, "status_code", None)
    if status_code is None and isinstance(result, tuple) and len(result) > 1:
        status_code = result[1]
    try:
        if status_code is not None and int(status_code) >= 400:
            raise FlowTaskOperationError(
                f"target task operation returned HTTP {int(status_code)}"
            )
    except (TypeError, ValueError):
        pass
    return result


def _run_params(run: Mapping | None) -> dict:
    params = (run or {}).get("execution_params") or {}
    return dict(params) if isinstance(params, Mapping) else {}


def _get_run(ctx, run_id: str):
    """Read either durable run projection without inventing a platform."""
    store = getattr(ctx, "store", None)
    screening_getter = getattr(store, "get_screening_run", None)
    run = screening_getter(run_id) if callable(screening_getter) else None
    if run is not None:
        return run
    search_getter = getattr(store, "get_search_run", None)
    if not callable(search_getter):
        return None
    try:
        search_run = search_getter(run_id)
    except (KeyError, ValueError):
        return None
    snapshot = search_run.get("profile_snapshot") or {}
    params = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    return {
        **dict(search_run),
        "platform": params.get("platform"),
        "execution_params": params,
    }


def _track_row_for_run(store, run_id: str):
    """Find the durable Track owning a screening/search run, if any."""
    connection = getattr(store, "_connection", None)
    if not callable(connection):
        return None
    with connection() as conn:
        return conn.execute(
            "SELECT ft.*, f.profile_id AS flow_profile_id "
            "FROM flow_tracks ft JOIN flows f ON f.id = ft.flow_id "
            "WHERE ft.scrape_run_id = ? OR ft.screen_run_id = ? "
            "ORDER BY CASE WHEN ft.screen_run_id = ? THEN 0 ELSE 1 END "
            "LIMIT 1",
            (str(run_id), str(run_id), str(run_id)),
        ).fetchone()


def resolve_flow_binding(ctx, run_id: str) -> dict | None:
    """Resolve a run's exact Flow/Track/platform identity.

    Legacy runs with no Flow remain valid and return ``None``.  Once a Flow is
    present, all three identity pieces must be available either in the frozen
    run parameters or the durable Track row.
    """
    run_id = str(run_id or "").strip()
    if not run_id:
        return None
    run = _get_run(ctx, run_id)
    if run is None:
        # A Track FK pointing at a missing run is not a legacy task; refusing
        # it prevents a process-local task with a recycled id from operating
        # a Flow line without a durable Run owner.
        if _track_row_for_run(ctx.store, run_id) is not None:
            raise FlowTaskOperationError(
                f"Flow Track points to a missing task run: {run_id}"
            )
        return None
    params = _run_params(run)
    track_row = _track_row_for_run(ctx.store, run_id)
    flow_id = str(params.get("flow_id") or "").strip()
    track_id = str(params.get("track_id") or "").strip()
    declared_platform = str(params.get("platform") or "").strip().lower()
    # ``create_screening_run`` historically stores ``boss`` when the frozen
    # payload omitted a platform.  Once a Flow/Track hint exists that value is
    # not an identity fact; the durable Track is authoritative instead.
    platform = declared_platform
    if not platform and not (flow_id or track_row is not None):
        platform = str(run.get("platform") or "").strip().lower()
    if flow_id and track_row is None:
        # A frozen Flow id is not enough to select a sibling Track.  Refuse
        # the action instead of redirecting it to the requested platform.
        if not platform:
            raise MissingPlatformIdentityError(
                f"Flow run {run_id} has no durable platform identity"
            )
        raise FlowTaskOperationError(
            f"Flow run {run_id} has no durable Track binding"
        )
    if track_row is not None:
        durable_flow_id = str(track_row["flow_id"] or "").strip()
        durable_track_id = str(track_row["id"] or "").strip()
        durable_platform = str(track_row["platform"] or "").strip().lower()
        if flow_id and durable_flow_id and flow_id != durable_flow_id:
            raise FlowTaskOperationError(
                f"Flow run {run_id} belongs to a different Flow"
            )
        if track_id and durable_track_id and track_id != durable_track_id:
            raise FlowTaskOperationError(
                f"Flow run {run_id} belongs to a different Track"
            )
        if platform and durable_platform and platform != durable_platform:
            raise FlowTaskOperationError(
                f"Flow run {run_id} belongs to a different platform"
            )
        run_profile_id = str(
            run.get("profile_id") or params.get("profile_id") or ""
        ).strip()
        durable_profile_id = str(track_row["flow_profile_id"] or "").strip()
        if run_profile_id and durable_profile_id and run_profile_id != durable_profile_id:
            raise FlowTaskOperationError(
                f"Flow run {run_id} belongs to a different profile"
            )
        flow_id = flow_id or str(track_row["flow_id"] or "").strip()
        track_id = track_id or str(track_row["id"] or "").strip()
        platform = platform or str(track_row["platform"] or "").strip().lower()
    if not flow_id:
        return None
    if not platform:
        raise MissingPlatformIdentityError(
            f"Flow run {run_id} has no frozen platform identity"
        )
    if not track_id:
        raise FlowTaskOperationError(f"Flow run {run_id} has no Track identity")
    profile_id = str(run.get("profile_id") or params.get("profile_id") or "").strip()
    if not profile_id and track_row is not None:
        profile_id = str(track_row["flow_profile_id"] or "").strip()
    if not profile_id:
        raise FlowTaskOperationError(f"Flow run {run_id} has no profile identity")
    return {
        "run_id": run_id,
        "run": run,
        "flow_id": flow_id,
        "track_id": track_id,
        "platform": platform,
        "profile_id": profile_id,
        "track_row": track_row,
    }


def ensure_ai_run_bound_to_track(ctx, run_id: str) -> dict | None:
    """Guarantee a Flow-owned AI run owns its Track's ``screen_run_id``.

    Every path that starts an AI screening (first submission, in-place resume,
    a worker materialising its durable row after a takeover) must end with the
    same fact: the Track names the run a Track action can operate.  Without it
    ``POST /api/flows/<id>/tracks/<platform>/pause`` only sees the finished
    scrape run and cannot reach the live AI worker.

    Identity comes from the run's own frozen parameters plus the durable Track
    row that already owns the source scrape run; a missing or conflicting
    identity is left alone rather than guessed.
    """
    run_id = str(run_id or "").strip()
    store = getattr(ctx, "store", None)
    if not run_id or store is None:
        return None
    getter = getattr(store, "get_screening_run", None)
    if not callable(getter):
        return None
    try:
        run = getter(run_id)
    except Exception:  # noqa: BLE001 - a missing projection is not a binding
        return None
    if run is None:
        return None
    params = _run_params(run)
    flow_id = str(params.get("flow_id") or "").strip()
    if not flow_id:
        # A legacy single-run AI has no Track to bind; nothing to guarantee.
        return None
    declared_track_id = str(params.get("track_id") or "").strip()
    platform = str(
        run.get("platform") or params.get("platform") or ""
    ).strip().lower()
    profile_id = str(run.get("profile_id") or params.get("profile_id") or "").strip()
    scrape_run_id = str(params.get("scrape_task_id") or "").strip()
    track_row = None
    for owner_id in (run_id, scrape_run_id):
        if owner_id:
            track_row = _track_row_for_run(store, owner_id)
            if track_row is not None:
                break
    if track_row is not None:
        existing = str(track_row["screen_run_id"] or "").strip()
        if existing == run_id:
            return None
        if existing:
            # Another run already owns the AI lane; never steal it.
            return None
        if declared_track_id and declared_track_id != str(track_row["id"] or ""):
            return None
        if str(track_row["flow_id"] or "") != flow_id:
            return None
        flow_id = str(track_row["flow_id"] or flow_id)
        platform = platform or str(track_row["platform"] or "").strip().lower()
        profile_id = profile_id or str(track_row["flow_profile_id"] or "").strip()
    if not platform or not profile_id:
        # Without a durable platform/profile identity the binding would be a
        # guess; the caller keeps its own observable failure path.
        return None
    flow_service = getattr(ctx, "flow_service", None)
    begin_ai = getattr(flow_service, "begin_ai", None)
    if not callable(begin_ai):
        return None
    try:
        return begin_ai(
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            screen_run_id=run_id,
        )
    except FlowConflictError as exc:
        raise FlowTaskOperationError(
            f"AI run {run_id} cannot take its Track: {exc}"
        ) from exc


def sync_flow_track_for_run(
    ctx,
    run_id: str,
    *,
    status: str,
    stage: str | None = None,
    error_code: str | None = None,
    reason: str | None = None,
) -> dict | None:
    """Idempotently publish a legacy task action to its owning Track."""
    binding = resolve_flow_binding(ctx, run_id)
    if binding is None:
        return None
    flow_id = binding["flow_id"]
    platform = binding["platform"]
    profile_id = binding["profile_id"]
    try:
        flow = ctx.store.get_flow(flow_id, profile_id=profile_id)
        track = next(
            (item for item in flow.get("tracks", [])
             if str(item.get("id") or "") == str(binding["track_id"])),
            None,
        )
    except (AttributeError, KeyError, StopIteration, ValueError) as exc:
        raise FlowTaskOperationError(
            f"Flow Track not found: {flow_id}:{binding['track_id']}"
        ) from exc
    if track is None:
        raise FlowTaskOperationError(f"Flow Track not found: {flow_id}:{platform}")

    desired = str(status or "").strip().lower()
    if desired not in {"paused", "running", "stopped", "cancelled"}:
        raise ValueError(f"unsupported task-to-Track status: {desired}")
    current = str(track.get("status") or "")
    desired_stage = _track_stage_for_run(track, run_id, stage)
    if current == desired:
        # Repeating a terminal state is idempotent and must not rewrite its
        # historical lane.  Active states may still need a lane correction
        # when a worker checkpoint used the stale ``scrape`` label.
        if current in {"done", "succeeded", "failed", "stopped", "cancelled"}:
            return flow
        if str(track.get("stage") or "") == desired_stage:
            return flow
    if current in {"done", "succeeded", "failed", "stopped", "cancelled"}:
        # Terminal Track facts win over a late legacy response, but the HTTP
        # caller must not receive a success receipt for an action that was not
        # applied.  Repeating the same desired state remains idempotent above.
        raise FlowTaskOperationError(
            f"Flow Track is already terminal: {flow_id}:{platform}:{current}"
        )

    updates = {
        "status": desired,
        "stage": desired_stage,
    }
    if error_code is not None:
        updates["error_code"] = error_code
    if reason is not None:
        updates["reason"] = reason
    try:
        return ctx.store.update_flow_track(
            flow_id,
            platform,
            profile_id=profile_id,
            **updates,
        )
    except Exception as exc:  # noqa: BLE001 - preserve observable operation failure
        raise FlowTaskOperationError(
            f"Flow Track update failed for {flow_id}:{platform}"
        ) from exc


def _target_run_ids(track: Mapping) -> list[str]:
    # Durable IDs outrank the mutable worker stage.  In particular, an AI
    # hard-stop may leave ``stage='scrape'`` while ``screen_run_id`` is the
    # concrete task that must receive the operation.
    first = (
        ("screen_run_id", "scrape_run_id")
        if str(track.get("screen_run_id") or "").strip()
        else ("scrape_run_id", "screen_run_id")
    )
    ids = []
    for key in first:
        value = str(track.get(key) or "").strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def persist_task_cancelled(
    ctx,
    task_id: str,
    *,
    sync_track: bool = True,
    track_status: str = "cancelled",
):
    """Persist one task's cancel fact across its run projections.

    Search tasks have a screening projection and a ``search_runs`` projection;
    Flow actions must not leave one saying ``running`` after the other has been
    cancelled.  A fully settled durable task is acknowledged as an explicit
    no-op so a stale historical id cannot block reset.  Mixed projections
    that still contain a live run remain strict and surface an operation
    failure.
    """
    # Resolve the durable identity before changing either run projection so a
    # malformed Flow task cannot be cancelled under an implicit legacy
    # platform.  The store then validates the same binding again inside its
    # BEGIN IMMEDIATE transaction and updates every projection atomically.
    binding = resolve_flow_binding(ctx, task_id)
    store = getattr(ctx, "store", None)
    cancel_atomic = getattr(store, "cancel_task_atomic", None)
    if not callable(cancel_atomic):
        raise FlowTaskOperationError("atomic task cancellation is unavailable")
    try:
        return cancel_atomic(
            task_id,
            flow_binding=binding,
            sync_track=bool(sync_track),
            track_status=track_status,
        )
    except FlowTaskOperationError:
        raise
    except (KeyError, ValueError, sqlite3.Error, RuntimeError, TypeError) as exc:
        raise FlowTaskOperationError(
            f"bound task cancellation failed for {task_id}"
        ) from exc


def _persist_bound_task_cancelled(ctx, task_id: str) -> None:
    """Close a bound screening run when a Flow stop is requested.

    The Flow endpoint is allowed to acknowledge a process-local stop signal
    before the worker reaches its own safe boundary, but a terminal stop must
    still survive a refresh.  Only the exact run selected by the Track is
    touched; a missing or conflicting durable run is an operation failure.
    """
    if not task_id:
        raise FlowTaskOperationError("bound task id is missing")
    # FlowService publishes ``stopped`` after the real worker stop succeeds;
    # do not publish the legacy ``cancelled`` Track state before that service
    # transition.  The Run projections are still closed atomically here.
    persist_task_cancelled(
        ctx,
        task_id,
        sync_track=True,
        track_status="stopped",
    )


def _call_flow_continuation(ctx, task_id: str, platform: str, flow, track):
    """Invoke the unified continuation path for AI/recrawl Tracks.

    Production context keeps the legacy continuation route as the canonical
    implementation.  A direct callback remains injectable for focused tests
    and non-HTTP hosts; the Flask view fallback keeps the Flow action wired to
    the same identity, login, checkpoint and capacity gates as the task API.
    """
    continuation = getattr(ctx, "continue_flow_task", None)
    if callable(continuation):
        return _ensure_operation_succeeded(
            continuation(task_id, platform, flow, track)
        )
    try:
        from flask import current_app

        view = current_app.view_functions.get("api_task_continue")
    except (ImportError, RuntimeError, AttributeError):
        view = None
    if callable(view):
        return _ensure_operation_succeeded(view(task_id))
    raise FlowTaskOperationError("Flow task continuation is unavailable")


def operate_bound_task(ctx, action: str, platform: str, flow: Mapping, track: Mapping):
    """Operate the concrete task behind a Flow action.

    Pause/cancel are process-local signal operations and therefore work even
    when a worker has not yet persisted its terminal row.  Resume delegates to
    the existing continuation hook when one is available; a Flow action never
    fabricates a second task.
    """
    action = str(action or "").strip().lower()
    if action not in {"pause", "resume", "stop"}:
        raise ValueError("action must be pause, resume, or stop")
    target_track_status = {
        "pause": "paused",
        "resume": "running",
        "stop": "stopped",
    }[action]
    current_track_status = str(track.get("status") or "").strip().lower()
    if current_track_status == target_track_status:
        # Repeating the exact durable state is safe and does not touch a
        # process-local worker that may already have been torn down.
        return True
    if current_track_status in {
        "done", "succeeded", "failed", "stopped", "cancelled",
    }:
        raise FlowTaskOperationError(
            f"target Flow Track is terminal: {current_track_status}"
        )
    run_ids = _target_run_ids(track)
    task_id = run_ids[0] if run_ids else ""
    task = None
    lock = getattr(ctx, "lock", None)
    tasks = getattr(ctx, "tasks", {})
    if task_id:
        if lock is None:
            task = tasks.get(task_id)
        else:
            with lock:
                task = tasks.get(task_id)

    store = getattr(ctx, "store", None)
    if task_id and (
        callable(getattr(store, "get_screening_run", None))
        or callable(getattr(store, "get_search_run", None))
    ):
        binding = resolve_flow_binding(ctx, task_id)
        if binding is None:
            raise FlowTaskOperationError(
                f"target task is not bound to a Flow Track: {task_id}"
            )
        expected_flow_id = str(flow.get("id") or "").strip()
        expected_track_id = str(track.get("id") or "").strip()
        expected_profile_id = str(flow.get("profile_id") or "").strip()
        if (
            binding["flow_id"] != expected_flow_id
            or (expected_track_id and binding["track_id"] != expected_track_id)
            or binding["platform"] != str(platform or "").strip().lower()
            or (expected_profile_id and binding["profile_id"] != expected_profile_id)
        ):
            raise FlowTaskOperationError("target task/Flow Track binding mismatch")

    # A freshly queued Track has no external task yet; pausing that durable
    # submission gate is a valid scheduling operation.  Once a run identity
    # exists, however, a missing worker is never treated as a successful pause
    # or stop.  A Track without any run identity has nothing to operate and
    # says so with its own code.
    if (
        action in {"pause", "stop"}
        and (not task_id or task is None)
        and not (action == "pause" and str(track.get("status") or "") == "queued" and not task_id)
    ):
        if not task_id:
            raise FlowTrackTaskUnavailableError(
                f"Flow Track has no bound task to operate: {track.get('id')}"
            )
        raise FlowTaskOperationError("target task is not active in this worker")

    hook = getattr(ctx, "operate_flow_task", None)
    if callable(hook):
        return _ensure_operation_succeeded(hook(action, platform, flow, track))

    if action == "pause":
        if task is None:
            return True
        from webui.flow_task_actions import delegate_task_pause

        if delegate_task_pause(task_id):
            return True
        current = str(task.get("status") or "")
        if current not in {"queued", "running"}:
            if current == "paused" and current_track_status == "running":
                # The signal may already have reached the worker while the
                # durable Track write failed.  Retrying the same Flow action
                # must publish the Track fact, not signal a second worker.
                return True
            raise FlowTaskOperationError(
                f"target task is not active: {task_id}:{current}"
            )
        if current in {"queued", "running"}:
            stop_event = task.get("stop_event")
            if stop_event is None:
                raise FlowTaskOperationError("target task has no pause signal")
            request_stop(task, stop_event, STOP_MODE_PAUSE)
        return True

    if action == "stop":
        current = str(task.get("status") or "")
        if current not in {"queued", "running", "paused"}:
            raise FlowTaskOperationError(
                f"target task is not active: {task_id}:{current}"
            )
        stop_event = task.get("stop_event")
        if stop_event is None:
            raise FlowTaskOperationError("target task has no stop signal")
        request_stop(task, stop_event, STOP_MODE_CANCEL)
        _persist_bound_task_cancelled(ctx, task_id)
        task["status"] = "cancelled"
        return True

    # ``resume`` is intentionally delegated to the existing continuation
    # implementation.  If the legacy route already resumed the task, this is
    # a no-op and cannot submit a duplicate worker.
    if task is not None and str(task.get("status") or "") in {"queued", "running"}:
        return True
    screen_run_id = str(track.get("screen_run_id") or "").strip()
    stage = str(track.get("stage") or "").strip().lower()
    task_kind = str((task or {}).get("kind") or "").strip().lower()
    # A durable screen binding wins over both the mutable Track stage and a
    # stale in-memory task kind after an AI hard-stop.
    if (
        task_id == screen_run_id
        or _is_ai_track_stage(stage)
        or task_kind in {"ai_screen", "recrawl"}
    ):
        return _call_flow_continuation(ctx, task_id, platform, flow, track)
    continuation = getattr(ctx, "continue_execute_search", None)
    if callable(continuation) and task_id:
        response = continuation(task_id)
        if response is None:
            raise FlowTaskOperationError("task continuation returned no response")
        return _ensure_operation_succeeded(response)
    return _call_flow_continuation(ctx, task_id, platform, flow, track)


__all__ = [
    "FlowTaskOperationError",
    "FlowTrackTaskUnavailableError",
    "MissingPlatformIdentityError",
    "ensure_ai_run_bound_to_track",
    "operate_bound_task",
    "persist_task_cancelled",
    "resolve_flow_binding",
    "sync_flow_track_for_run",
]
