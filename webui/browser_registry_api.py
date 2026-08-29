"""浏览器注册表路由域（029 B082③，仿 log_api 独立路由域先例）。

- GET  ``/api/browser-registry``            探测清单 + 当前选择 + 生效路径
- PUT  ``/api/browser-registry``            保存选择（manual 强制 ``--version`` 探活）
- POST ``/api/browser-registry/validate-path`` 手动路径即时校验（不落盘）

逻辑全部委托 ``scripts/boss/browser_registry``（api → 域单向）；选择持久化
由注册表域自持 ``browser_selection.json``，不触碰 settings 白名单通道。
"""

from __future__ import annotations

from flask import jsonify, request

from scripts.boss import browser_registry as br


def _selection_payload():
    """组装 GET/PUT 共用响应体：registry 清单 + selection + effective_path。"""
    registry = br.detect_browsers()
    selection = br.load_browser_selection()
    effective_path, _reason = br.resolve_executable(
        selection_loader=lambda: selection,
        detect_fn=lambda: registry,
    )
    return {
        "registry": registry,
        "selection": selection,
        "effective_path": effective_path,
    }


def register_browser_registry_routes(app, ctx):
    @app.route("/api/browser-registry", methods=["GET"])
    def browser_registry_list():
        return jsonify(_selection_payload())

    @app.route("/api/browser-registry", methods=["PUT"])
    def browser_registry_save():
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("mode") or "")
        if mode == "registry":
            key = payload.get("key")
            if key not in br.REGISTRY_KEYS:
                return jsonify({
                    "ok": False,
                    "error": "invalid_selection",
                    "message": f"未知浏览器：{key}",
                }), 400
            br.save_browser_selection("registry", key=key)
        elif mode == "manual":
            manual_path = str(payload.get("path") or "").strip()
            ok, info = br.validate_manual_path(manual_path)
            if not ok:
                return jsonify({"ok": False, **info}), 400
            br.save_browser_selection("manual", manual_path=manual_path)
        elif mode == "auto":
            br.save_browser_selection("auto")
        else:
            return jsonify({
                "ok": False,
                "error": "invalid_selection",
                "message": "mode 必须是 auto/registry/manual",
            }), 400
        return jsonify({"ok": True, **_selection_payload()})

    @app.route("/api/browser-registry/validate-path", methods=["POST"])
    def browser_registry_validate_path():
        payload = request.get_json(silent=True) or {}
        manual_path = str(payload.get("path") or "").strip()
        ok, info = br.validate_manual_path(manual_path)
        status = 200 if ok else 400
        return jsonify(info), status
