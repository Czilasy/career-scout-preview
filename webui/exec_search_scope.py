"""范围预览路由的内部实现。

047：从 ``exec_search_api`` 提取纯范围校验职责；路径、响应和上下文
写入保持原样，模块不持有任务运行状态。
"""

from __future__ import annotations

from flask import jsonify, request

from webui.constants import _MSG_UNSUPPORTED_PLATFORM


def register_search_scope_preview(app, ctx):
    @app.route("/api/search-scope/preview", methods=["POST"])
    def search_scope_preview():
        """SPEC011 T004 / tasks005 T402: 后端权威范围预览与校验（平台感知）。"""
        from webui.execution_config import CityValidationError, preview_scope
        from webui.location_catalog import LocationCatalogUnavailable
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            validate_platform_key,
        )

        body = request.get_json(silent=True) or {}
        platform_raw = body.get("platform") or "boss"
        try:
            validate_platform_key(platform_raw)
        except UnknownPlatformError:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
            }), 400
        reg = get_platform_or_none(platform_raw)
        if reg is None:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": "平台未注册",
            }), 400
        if not reg.enabled_for_new_tasks:
            return jsonify({
                "ok": False,
                "error_code": "platform_disabled",
                "user_message": reg.availability_reason or "平台暂不可用",
            }), 503
        keywords = body.get("keywords")
        scope_kind = body.get("scope_kind", "cities")
        cities = body.get("cities", [])
        pages_per_combination = body.get("pages_per_combination", 1)
        locations = body.get("locations") or []
        if not isinstance(keywords, list):
            return jsonify({"ok": False, "error": "keywords 必须是数组"}), 400
        if scope_kind not in ("cities", "nationwide"):
            return jsonify({"ok": False, "error": "scope_kind 必须是 cities 或 nationwide"}), 400
        if not isinstance(cities, list):
            return jsonify({"ok": False, "error": "cities 必须是数组"}), 400
        if not isinstance(locations, list):
            return jsonify({"ok": False, "error": "locations 必须是数组"}), 400
        if isinstance(pages_per_combination, bool) or not isinstance(
            pages_per_combination, int
        ):
            return jsonify({"ok": False, "error": "pages_per_combination 必须是整数"}), 400
        pages_int = pages_per_combination
        try:
            result = preview_scope(
                keywords=keywords,
                scope_kind=scope_kind,
                cities=cities,
                pages_per_combination=pages_int,
                locations=locations,
                platform=platform_raw,
            )
            ctx.scope_previews[result["scope"]["scope_digest"]] = dict(result["scope"])
            return jsonify({"ok": True, **result})
        except LocationCatalogUnavailable:
            return jsonify({
                "ok": False,
                "error_code": "location_catalog_unavailable",
                "error": "地点目录暂时不可用，按城市级搜索",
            }), 503
        except CityValidationError as e:
            return jsonify({
                "ok": False,
                "error_code": "city_validation_failed",
                "error": "城市参数无效",
                "details": e.details,
            }), 422
        except ValueError:
            return jsonify({
                "ok": False,
                "error_code": "scope_validation_failed",
                "error": "搜索范围参数无效",
            }), 422

    return search_scope_preview
