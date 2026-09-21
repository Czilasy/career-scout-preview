"""常用搜索配置包的 HTTP 路由（Spec 044 B100）。

路由层只做请求解析、领域调用与稳定错误映射：业务规则（快照归一化、
完整性/版本校验、默认命名）全部在 webui/search_packages.py，数据访问全部
在 webui/store_search_packages.py。端点不带平台参数，也不接受第三页筛选
字段；同一套包在 BOSS 与智联共用。

错误响应统一为 ``{"error": {"code": ..., "message": ...}}``，``message``
可直接展示给用户，且不含 SQL、路径、堆栈或原始损坏数据。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from webui.search_packages import SearchPackageError, SearchPackageService

__all__ = [
    "create_search_packages_blueprint",
    "register_search_package_routes",
]

# 领域错误码 → HTTP 状态（contracts/http-api.md）
_ERROR_STATUS = {
    "invalid_package": 400,
    "invalid_name": 400,
    "package_not_found": 404,
    "package_unusable": 409,
    "persistence_failed": 500,
}

_PERSISTENCE_MESSAGE = "配置包保存失败，请重试"


def _error(code: str, message: str):
    """稳定错误体；不泄露 SQL、路径、凭据或原始损坏数据。"""
    return jsonify({"error": {"code": code, "message": message}}), _ERROR_STATUS.get(code, 500)


def _domain_error_response(exc: SearchPackageError):
    return _error(exc.code, exc.message)


def _persistence_error_response():
    return _error("persistence_failed", _PERSISTENCE_MESSAGE)


def create_search_packages_blueprint(store) -> Blueprint:
    """构造配置包路由；不产生应用级副作用，便于隔离夹具直接使用。"""
    service = SearchPackageService(store)
    blueprint = Blueprint("search_packages_api", __name__)

    @blueprint.route("/api/search-packages", methods=["GET"])
    def list_packages():
        """全部配置包的轻量列表，默认最近更新在前。"""
        try:
            return jsonify({"items": service.list_packages()})
        except SearchPackageError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    @blueprint.route("/api/search-packages/<package_id>", methods=["GET"])
    def get_package(package_id):
        """读取经完整校验的包；损坏或版本不支持按不可用整包拒绝。"""
        try:
            return jsonify(service.get_package(package_id))
        except SearchPackageError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    @blueprint.route("/api/search-packages", methods=["POST"])
    def create_package():
        """保存当前页面状态：总是创建一套新的并列配置包。"""
        try:
            raw = request.get_json(silent=True)
            return jsonify(service.create_package(raw)), 201
        except SearchPackageError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    @blueprint.route("/api/search-packages/<package_id>/name", methods=["PATCH"])
    def rename_package(package_id):
        """只改名称；改名不触碰关键词、城市或画像内容。"""
        try:
            raw = request.get_json(silent=True)
            name = raw.get("name") if isinstance(raw, dict) else None
            return jsonify(service.rename_package(package_id, name))
        except SearchPackageError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    @blueprint.route("/api/search-packages/<package_id>", methods=["DELETE"])
    def delete_package(package_id):
        """二次确认后由前端调用；后端只删除目标配置包行。"""
        try:
            service.delete_package(package_id)
            return "", 204
        except SearchPackageError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    return blueprint


def register_search_package_routes(app, ctx, **options) -> Blueprint:
    """注册到真实应用或隔离夹具；``ctx`` 需带 ``store`` 属性。"""
    blueprint = create_search_packages_blueprint(ctx.store, **options)
    app.register_blueprint(blueprint)
    return blueprint
