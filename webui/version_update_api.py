"""版本 / 应用内更新 / 主题 API 路由（021 B6 T019 外迁自 webui/app.py）。

路由体纯搬运：HTTP 契约与响应结构零改动。共享运行态（backend_version /
product_version / runtime_mode 等）经 ctx 取用；可 patch 符号（os /
_theme_path）经 ctx 动态门面保住 patch("webui.app.X") 面。
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import jsonify, request

from webui import updater as updater_mod

def register_version_update_routes(app, ctx):
    @app.route("/api/version", methods=["GET"])
    def api_version():
        """FR-039：返回后端版本标识。前端用于校验是否需要刷新。"""
        return jsonify({
            "backend_version": ctx.backend_version,
            "build_hash": ctx.build_hash,
            "build_time": ctx.build_time,
        })

    @app.route("/api/update-check", methods=["GET"])
    def update_check():
        info = updater_mod.check_for_update(
            ctx.product_version,
            state_dir=None if app.config.get("TESTING") else updater_mod.DEFAULT_STATE_DIR,
        )
        payload = ctx.update_env_payload(info.to_dict())
        # 跨重启后内存状态是 idle：先尝试把磁盘上已下载并通过校验的
        # 完整安装包恢复为 ready，弹窗打开时可直接重启更新。
        if payload.get("installable") and info.has_update:
            app.config["UPDATER"].recover_ready(info)
        return jsonify(payload)

    @app.route("/api/update-download", methods=["POST"])
    def update_download():
        """启动后台下载；仅接受带 sha256 资产的更新（无哈希拒绝）。"""
        info = updater_mod.check_for_update(
            ctx.product_version,
            state_dir=None if app.config.get("TESTING") else updater_mod.DEFAULT_STATE_DIR,
        )
        if not info.has_update:
            return jsonify({"ok": False, "error_code": "no_update",
                            "user_message": "当前已是最新版本"}), 409
        if not info.asset_url:
            return jsonify({"ok": False, "error_code": info.reason or "no_asset",
                            "user_message": "该版本未提供当前平台的安装包，请到 Release 页手动下载"}), 422
        if not info.sha256_url:
            return jsonify({"ok": False, "error_code": "no_sha256",
                            "user_message": "该版本未提供校验文件，为安全起见请到 Release 页手动下载"}), 422
        updater = app.config["UPDATER"]
        if updater.recover_ready(info):
            return jsonify({"ok": True, "already": True, **updater.status()})
        started = updater.start(info)
        if not started:
            status = app.config["UPDATER"].status()
            if status["status"] in ("downloading", "verifying", "ready"):
                return jsonify({"ok": True, "already": True, **status})
            return jsonify({"ok": False, "error_code": "download_start_failed",
                            "user_message": "下载启动失败，请稍后重试；若仍失败请到 Release 页手动下载"}), 500
        return jsonify({"ok": True, **app.config["UPDATER"].status()})

    @app.route("/api/update-status", methods=["GET"])
    def update_status():
        app.config["UPDATER"].recover_ready()
        status = app.config["UPDATER"].status()
        status["path"] = ""  # 本地下载路径不返回给前端
        return jsonify({"ok": True, **status})

    @app.route("/api/update-restart", methods=["POST"])
    def update_restart():
        """生成并 detached 启动替换脚本，随后由前端退出应用。

        脚本等主进程退出后替换文件并拉起新版本；未就绪/非 exe 模式/
        无安装目标一律拒绝（源码模式没有可替换产物）。
        """
        import subprocess
        import sys as _sys

        install_target = updater_mod.current_install_target()
        if ctx.runtime_mode != "exe" or install_target is None:
            return jsonify({"ok": False, "error_code": "not_installable",
                            "user_message": "源码模式不支持应用内安装，请手动更新"}), 409
        status = app.config["UPDATER"].status()
        if status["status"] != "ready" or not status["path"]:
            return jsonify({"ok": False, "error_code": "download_not_ready",
                            "user_message": "更新包尚未就绪"}), 409
        installer_path = Path(status["path"])
        if not installer_path.exists():
            return jsonify({"ok": False, "error_code": "installer_missing",
                            "user_message": "更新包丢失，请重新下载"}), 409
        runner, script = updater_mod.build_updater_script(
            installer_path=installer_path,
            install_target=install_target,
            pid=ctx.os.getpid(),
            script_dir=updater_mod.DEFAULT_STATE_DIR,
        )
        try:
            if _sys.platform == "win32":
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                     "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    cwd=str(updater_mod.DEFAULT_STATE_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    # CREATE_NO_WINDOW：绝不弹 cmd/powershell 黑窗
                    creationflags=subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                subprocess.Popen(
                    [runner, str(script)],
                    cwd=str(updater_mod.DEFAULT_STATE_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as exc:
            app.logger.exception("更新重启脚本启动失败：%s", exc)
            return jsonify({"ok": False, "error_code": "updater_launch_failed",
                            "user_message": "更新脚本启动失败，请关闭软件后手动下载更新"}), 500
        return jsonify({"ok": True, "user_message": "即将重启完成更新"})

    @app.route("/api/theme", methods=["GET"])
    def api_theme_get():
        mode = "light"
        try:
            data = json.loads(ctx._theme_path().read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("mode") in ("light", "dark", "kaleido"):
                mode = data["mode"]
        except (OSError, ValueError):
            pass
        return jsonify({"ok": True, "mode": mode})

    @app.route("/api/theme", methods=["PUT"])
    def api_theme_put():
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode") or "")
        if mode not in ("light", "dark", "kaleido"):
            return jsonify({"ok": False, "error": "mode 必须为 light、dark 或 kaleido"}), 400
        try:
            path = ctx._theme_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return jsonify({"ok": False, "error": "theme 写入失败"}), 500
        return jsonify({"ok": True, "mode": mode})
