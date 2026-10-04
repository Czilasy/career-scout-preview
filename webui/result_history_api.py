"""HTTP routes for multi-round result history.

Spec041：历史列表/详情/删除/归档支持并校验 profile_id 归属；
真实 UI 调用一律带画像，避免跨画像读取、删除或归档。
"""

from __future__ import annotations

from flask import jsonify, request

from webui.result_history import ResultHistoryService
from webui.run_cleanup import HistoryDeletionBlocked

_PLATFORMS = ("boss", "zhilian")


def _error(code: str, message: str, status: int):
    return jsonify({"ok": False, "error": code, "error_code": code, "message": message}), status


def register_result_history_routes(app, store) -> None:
    """Register history routes on the Flask app."""
    service = ResultHistoryService(store)

    def _profile_id_arg() -> str | None:
        value = str(request.args.get("profile_id", "")).strip()
        return value or None

    @app.route("/api/result-history", methods=["GET"])
    def result_history_list():
        platform = str(request.args.get("platform", "")).strip() or None
        if platform is not None and platform not in _PLATFORMS:
            return _error("invalid_platform", "平台必须是 boss 或 zhilian", 400)
        try:
            items = service.list_history(platform, _profile_id_arg())
        except Exception:
            return _error("persistence_failed", "历史列表读取失败", 500)
        return jsonify({"ok": True, "items": items})

    @app.route("/api/result-history/flows", methods=["GET"])
    def result_flow_history_list():
        profile_id = _profile_id_arg()
        if not profile_id:
            return _error("profile_id_required", "profile_id 不能为空", 422)
        try:
            items = service.list_flow_history(profile_id)
        except Exception:
            return _error("persistence_failed", "流程历史读取失败", 500)
        return jsonify({"ok": True, "items": items})

    @app.route("/api/result-history/flows/<flow_id>", methods=["GET"])
    def result_flow_history_detail(flow_id: str):
        profile_id = _profile_id_arg()
        if not profile_id:
            return _error("profile_id_required", "profile_id 不能为空", 422)
        try:
            item = next(
                (entry for entry in service.list_flow_history(profile_id)
                 if entry["flow_id"] == str(flow_id)),
                None,
            )
        except Exception:
            return _error("persistence_failed", "流程历史读取失败", 500)
        if item is None:
            return _error("flow_not_found", "流程历史不存在", 404)
        return jsonify({"ok": True, "item": item})

    @app.route("/api/result-history/<run_id>", methods=["GET"])
    def result_history_detail(run_id: str):
        try:
            payload = service.get_round(str(run_id), _profile_id_arg())
        except Exception:
            return _error("persistence_failed", "历史轮次读取失败", 500)
        if payload is None:
            return _error("round_not_found", "历史轮次不存在", 404)
        return jsonify(payload)

    @app.route("/api/result-history/archive-latest", methods=["POST"])
    def result_history_archive_all_current():
        body = request.get_json(silent=True)
        profile_id = ""
        flow_id = ""
        if isinstance(body, dict):
            profile_id = str(body.get("profile_id") or "").strip()
            flow_id = str(body.get("flow_id") or "").strip()
        if bool(profile_id) != bool(flow_id):
            return _error(
                "flow_scope_required",
                "flow_id 与 profile_id 必须同时提供",
                422,
            )
        if not profile_id or not flow_id:
            return _error(
                "flow_scope_required",
                "归档必须指定流程与画像",
                422,
            )
        try:
            run_ids = service.archive_flow(flow_id, profile_id)
        except (KeyError, ValueError):
            return _error("flow_not_found", "流程不存在或不属于该画像", 404)
        except Exception:
            return _error("persistence_failed", "归档失败", 500)
        return jsonify({"ok": True, "archived_run_ids": run_ids})

    @app.route("/api/result-history/<run_id>", methods=["DELETE"])
    def result_history_delete(run_id: str):
        try:
            deleted = service.delete_round(str(run_id), _profile_id_arg())
        except HistoryDeletionBlocked as exc:
            return _error("history_delete_blocked", str(exc), 409)
        except Exception:
            return _error("persistence_failed", "删除失败", 500)
        if not deleted:
            return _error("round_not_found", "历史轮次不存在", 404)
        return jsonify({"ok": True, "deleted": True, "run_id": str(run_id)})
