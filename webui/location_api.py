"""B054 地点 API：目录查询与地点条件校验。

031 B3 起按宪法统一注册方式收编：`register_location_routes(app, ctx)` 直接
挂路由，不再经 Flask Blueprint 中转。
"""
from __future__ import annotations

from flask import jsonify, request

from webui.location_catalog import LocationCatalogUnavailable, get_districts
from webui.location_scope import location_summary, normalize_locations


def register_location_routes(app, ctx):
    @app.route("/api/location-catalog", methods=["GET"])
    def location_catalog():
        from webui.platforms import UnknownPlatformError, resolve_platform_city, validate_platform_key

        platform = request.args.get("platform") or "boss"
        city_name = str(request.args.get("city") or "").strip()
        try:
            validate_platform_key(platform)
        except UnknownPlatformError:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "error": "未知平台",
            }), 400
        if not city_name:
            return jsonify({
                "ok": False,
                "error_code": "city_validation_failed",
                "error": "缺少城市",
            }), 422
        try:
            entry = resolve_platform_city(platform, city_name)
            districts = get_districts(platform, city_name)
        except ValueError:
            return jsonify({
                "ok": False,
                "error_code": "city_validation_failed",
                "error": "城市不在当前平台目录中",
            }), 422
        except LocationCatalogUnavailable:
            return jsonify({
                "ok": False,
                "error_code": "location_catalog_unavailable",
                "error": "地点目录暂时不可用，按城市级搜索",
            }), 503
        return jsonify({
            "ok": True,
            "platform": platform,
            "city": city_name,
            "city_code": entry.platform_code,
            "districts": districts,
        })

    @app.route("/api/location/validate", methods=["POST"])
    def location_validate():
        from webui.platforms import UnknownPlatformError, validate_platform_key

        body = request.get_json(silent=True) or {}
        platform = str(body.get("platform") or "boss")
        scope_kind = str(body.get("scope_kind") or "cities")
        locations = body.get("locations") or []
        try:
            validate_platform_key(platform)
        except UnknownPlatformError:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "error": "未知平台",
            }), 400
        if scope_kind not in ("cities", "nationwide"):
            return jsonify({
                "ok": False,
                "error_code": "scope_validation_failed",
                "error": "scope_kind 必须是 cities 或 nationwide",
            }), 422
        if scope_kind == "nationwide" and locations:
            return jsonify({
                "ok": False,
                "error_code": "scope_validation_failed",
                "error": "全国范围不能与具体地点同时选择",
            }), 422
        try:
            normalized = normalize_locations(platform, locations)
        except LocationCatalogUnavailable:
            return jsonify({
                "ok": False,
                "error_code": "location_catalog_unavailable",
                "error": "地点目录暂时不可用，按城市级搜索",
            }), 503
        except ValueError as exc:
            return jsonify({
                "ok": False,
                "error_code": "location_validation_failed",
                "error": str(exc),
            }), 422
        return jsonify({
            "ok": True,
            "locations": location_summary(normalized).split("、") if normalized else [],
        })
