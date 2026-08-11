"""HTTP routes for multi-round result history."""

from __future__ import annotations

from flask import jsonify, request

from webui.result_history import ResultHistoryService

_PLATFORMS = ("boss", "zhilian")


def _error(code: str, message: str, status: int):
    return jsonify({"ok": False, "error": code, "message": message}), status


def register_result_history_routes(app, store) -> None:
    """Register history routes on the Flask app."""
    service = ResultHistoryService(store)

    @app.route("/api/result-history", methods=["GET"])
    def result_history_list():
        platform = str(request.args.get("platform", "")).strip() or None
        if platform is not None and platform not in _PLATFORMS:
            return _error("invalid_platform", "平台必须是 boss 或 zhilian", 400)
        try:
            items = service.list_history(platform)
        except Exception:
            return _error("persistence_failed", "历史列表读取失败", 500)
        return jsonify({"ok": True, "items": items})

    @app.route("/api/result-history/<run_id>", methods=["GET"])
    def result_history_detail(run_id: str):
        try:
            payload = service.get_round(str(run_id))
        except Exception:
            return _error("persistence_failed", "历史轮次读取失败", 500)
        if payload is None:
            return _error("round_not_found", "历史轮次不存在", 404)
        return jsonify(payload)

    @app.route("/api/result-history/archive-latest", methods=["POST"])
    def result_history_archive_latest():
        try:
            run_ids = service.archive_latest()
        except Exception:
            return _error("persistence_failed", "归档失败", 500)
        return jsonify({"ok": True, "archived_run_ids": run_ids})

    @app.route("/api/result-history/<run_id>", methods=["DELETE"])
    def result_history_delete(run_id: str):
        try:
            deleted = service.delete_round(str(run_id))
        except Exception:
            return _error("persistence_failed", "删除失败", 500)
        if not deleted:
            return _error("round_not_found", "历史轮次不存在", 404)
        return jsonify({"ok": True, "deleted": True, "run_id": str(run_id)})
