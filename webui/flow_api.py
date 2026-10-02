"""HTTP routes for B096 Flow/Track state."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext

from flask import jsonify, request

from webui.flow_service import (
    FlowResumeError,
    FlowService,
    PlatformUnavailableError,
    public_flow_message,
)
from webui.store_flow import FlowConflictError
from webui.flow_task_coordinator import (
    FlowTaskOperationError,
    FlowTrackTaskUnavailableError,
    MissingPlatformIdentityError,
    operate_bound_task,
)


def register_flow_routes(app, ctx):
    service = getattr(ctx, "flow_service", None)
    if service is None:
        service = FlowService(
            ctx.store,
            platform_enabled=_platform_enabled,
            operate_track=lambda action, platform, flow, track: operate_bound_task(
                ctx, action, platform, flow, track,
            ),
            track_finalizing=lambda flow, track: _track_finalizing(ctx, flow, track),
        )
        ctx.flow_service = service
    elif getattr(service, "track_finalizing", None) is None:
        service.track_finalizing = lambda flow, track: _track_finalizing(ctx, flow, track)
    # Keep the service's state transition and the concrete worker operation in
    # one identity context.  This is intentionally process-local; durable
    # binding validation still happens in the shared coordinator.
    service.operation_context = ctx
    if getattr(service, "operate_track_callback", None) is None:
        service.operate_track_callback = lambda action, platform, flow, track: operate_bound_task(
            ctx, action, platform, flow, track,
        )

    @app.route("/api/flows", methods=["POST"])
    def create_flow():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, Mapping):
            return _error("invalid_request", "请求体必须是 JSON 对象", 422)
        profile_id = str(body.get("profile_id") or "").strip()
        if not profile_id:
            return _error("profile_id_required", "profile_id 不能为空", 422)
        selection = str(body.get("selection") or "all").strip().lower()
        confirmed_filters = body.get("confirmed_filters")
        if not isinstance(confirmed_filters, Mapping):
            confirmed_filters = {}
        try:
            platforms = FlowService._platforms_for_selection(selection)
        except ValueError:
            return _error("invalid_request", "流程选择参数无效", 422)

        start_key = body.get("start_key") or body.get("idempotency_key")
        existing_id = None
        if start_key:
            try:
                existing_id = next(
                    (
                        item["id"]
                        for item in service.list_flows(profile_id, include_legacy=False)
                        if item.get("start_key") == str(start_key).strip()
                    ),
                    None,
                )
            except (KeyError, ValueError):
                existing_id = None
        try:
            flow = service.start_flow(
                profile_id=profile_id,
                selection=selection,
                confirmed_filters=confirmed_filters,
                start_key=start_key,
            )
        except PlatformUnavailableError as exc:
            return _error(
                "platform_disabled",
                _platform_unavailable_message(exc.platform),
                503,
                platform=exc.platform,
            )
        except FlowConflictError as exc:
            return _error("flow_conflict", public_flow_message("flow_conflict", exc), 409)
        except KeyError:
            return _error("profile_not_found", "画像不存在或不可访问", 404)
        except ValueError:
            return _error("invalid_request", "流程参数无效", 422)
        return jsonify({"ok": True, "flow_id": flow["id"], "flow": flow}), (
            200 if existing_id == flow["id"] else 201
        )

    @app.route("/api/flows/current", methods=["GET"])
    def current_flow():
        profile_id = str(request.args.get("profile_id") or "").strip()
        if not profile_id:
            return _error("profile_id_required", "profile_id 不能为空", 422)
        try:
            flow = service.get_current_flow(profile_id)
        except (KeyError, ValueError):
            return _error("profile_not_found", "画像不存在或不可访问", 404)
        return jsonify({"ok": True, "profile_id": profile_id, "flow": flow})

    @app.route("/api/flows/<flow_id>", methods=["GET"])
    def flow_detail(flow_id):
        profile_id = str(request.args.get("profile_id") or "").strip()
        if not profile_id:
            return _error("profile_id_required", "profile_id 不能为空", 422)
        try:
            flow = service.get_flow(flow_id, profile_id=profile_id)
        except (KeyError, ValueError):
            return _error("flow_not_found", "流程不存在或不可访问", 404)
        return jsonify({"ok": True, "flow": flow})

    @app.route("/api/flows/<flow_id>/results", methods=["GET"])
    def flow_results(flow_id):
        profile_id = str(request.args.get("profile_id") or "").strip()
        if not profile_id:
            return _error("profile_id_required", "profile_id 不能为空", 422)
        try:
            results = service.get_flow_results(flow_id, profile_id=profile_id)
        except KeyError:
            return _error("flow_not_found", "流程或结果不存在或不可访问", 404)
        except Exception:  # noqa: BLE001 - durable result reads must be observable
            return _error(
                "flow_results_unavailable",
                "流程结果暂时不可读取，请稍后重试",
                503,
            )
        return jsonify({"ok": True, "results": results})

    @app.route(
        "/api/flows/<flow_id>/tracks/<platform>/<action>",
        methods=["POST"],
    )
    def operate_track(flow_id, platform, action):
        body = request.get_json(silent=True) or {}
        profile_id = str(
            body.get("profile_id") or request.args.get("profile_id") or ""
        ).strip()
        if not profile_id:
            return _error("profile_id_required", "profile_id 不能为空", 422)
        try:
            flow = service.operate_track(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                action=action,
                **({"expected_run_id": body["expected_run_id"]} if "expected_run_id" in body else {}),
            )
        except FlowResumeError as exc:
            return _error(
                exc.error_code,
                exc.message,
                409,
                retryable=True,
            )
        except FlowConflictError as exc:
            return _error("flow_conflict", public_flow_message("flow_conflict", exc), 409)
        except MissingPlatformIdentityError:
            return _error(
                "platform_identity_missing",
                "目标任务缺少平台身份，无法操作运行线",
                409,
            )
        except FlowTrackTaskUnavailableError:
            return _error(
                "flow_track_task_unavailable",
                "该运行线还没有可操作的任务，请重新开始这一平台的筛选",
                503,
            )
        except FlowTaskOperationError:
            return _error(
                "flow_task_operation_failed",
                "目标任务操作失败，请刷新任务状态后重试",
                503,
            )
        except (KeyError, ValueError):
            return _error("flow_not_found", "流程或平台运行线不存在", 404)
        return jsonify({"ok": True, "flow": flow})


def _platform_enabled(platform: str) -> bool:
    from webui.platforms import get_platform_or_none

    registry = get_platform_or_none(platform)
    return bool(registry and registry.enabled_for_new_tasks)


def _platform_unavailable_message(platform: str) -> str:
    """平台不可用的用户可读文案（与抓取入口同一口径：只转注册表给出的原因）。

    内部平台码不回吐给用户；结构化 ``platform`` 字段照常带上，
    显示名由前端的平台显示名投影负责。
    """
    from webui.platforms import get_platform_or_none

    registry = get_platform_or_none(platform)
    reason = str(getattr(registry, "availability_reason", "") or "").strip()
    return reason or "该平台暂不可用，请改用可用平台后重新开始"


def _track_finalizing(ctx, _flow, track) -> bool:
    """Read live task markers before a direct Flow pause mutation."""
    task_ids = [
        str(track.get("scrape_run_id") or "").strip(),
        str(track.get("screen_run_id") or "").strip(),
    ]
    lock = getattr(ctx, "lock", None)
    tasks = getattr(ctx, "tasks", {})
    with (lock if lock is not None else nullcontext()):
        return any(
            bool(tasks.get(task_id, {}).get("finalizing"))
            for task_id in task_ids
            if task_id
        )


def _error(error_code: str, message: str, status: int, **extra):
    return jsonify({"ok": False, "error_code": error_code, "message": message, **extra}), status
