"""浏览器账号限流标记的 HTTP 路由。"""

from __future__ import annotations

from flask import jsonify

from webui.constants import _MSG_ACCOUNT_NOT_FOUND


def register_browser_account_rate_limit_routes(app) -> None:
    """注册用户手动清除账号限流视觉标记的最小接口。"""

    @app.route("/api/browser-accounts/<account_id>/rate-limited", methods=["DELETE"])
    def clear_browser_account_rate_limited(account_id):
        from webui.pipeline_exec_accounts import (
            _BROWSER_ACCOUNTS_LOCK,
            load_browser_accounts,
            save_browser_accounts,
        )

        account_id = str(account_id)
        with _BROWSER_ACCOUNTS_LOCK:
            accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
            account = accounts.get(account_id)
            if not isinstance(account, dict):
                return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
            account["rate_limited"] = False
            save_browser_accounts(accounts, app.config["BROWSER_ACCOUNTS_PATH"])
        return jsonify({"ok": True, "account_id": account_id, "rate_limited": False})
