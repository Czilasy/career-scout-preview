# -*- coding: utf-8 -*-

"""BOSS cookie 会话导入（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

from scripts.boss.cdp_session import CDPSession
import sys as _sys

from webui.logging_setup import get_logger

_logger = get_logger(__name__)

from scripts.boss import browser
from scripts.boss import constants as boss_constants
from scripts.boss import runtime
from scripts.boss import login

# ============================================================
# --setup-chrome 自动启动
# ============================================================
def is_boss_cookie_domain(domain):
    """Return True only for zhipin.com and its real subdomains."""
    normalized = str(domain or "").strip().lower().lstrip(".")
    return normalized == "zhipin.com" or normalized.endswith(".zhipin.com")


def normalize_boss_cookie(cookie):
    """Project a CDP cookie onto the minimal safe import contract."""
    if not isinstance(cookie, dict) or not is_boss_cookie_domain(cookie.get("domain")):
        return None
    name = cookie.get("name")
    value = cookie.get("value")
    if not isinstance(name, str) or not name or not isinstance(value, str):
        return None

    normalized = {
        "name": name,
        "value": value,
        "domain": str(cookie["domain"]),
        "path": str(cookie.get("path") or "/"),
    }
    for field in ("secure", "httpOnly"):
        if isinstance(cookie.get(field), bool):
            normalized[field] = cookie[field]
    if cookie.get("sameSite") in ("Strict", "Lax", "None"):
        normalized["sameSite"] = cookie["sameSite"]
    expires = cookie.get("expires")
    if isinstance(expires, (int, float)) and expires > 0:
        normalized["expires"] = expires
    return normalized


def _cdp_cookies(session):
    response = session.send("Storage.getCookies")
    if response.get("error"):
        raise RuntimeError("cdp_cookie_read_failed")
    cookies = response.get("result", {}).get("cookies", [])
    if not isinstance(cookies, list):
        raise RuntimeError("cdp_cookie_read_failed")
    return cookies


def _rollback_boss_cookies(target, imported, original):
    """Restore only the target's BOSS-cookie subset after a failed import."""
    identities = {}
    for cookie in imported + original:
        identities[(cookie["name"], cookie["domain"], cookie["path"])] = cookie
    try:
        for name, domain, path in identities:
            response = target.send("Network.deleteCookies", {
                "name": name,
                "domain": domain,
                "path": path,
            })
            if response.get("error"):
                return False
        if original:
            response = target.send("Storage.setCookies", {"cookies": original})
            if response.get("error"):
                return False
        return True
    except Exception:
        return False


def import_boss_session(source_cdp_port, target_cdp_port, authorized=False,
                        session_factory=CDPSession, login_checker=None,
                        target_profile_checker=None):
    """Import only an explicitly authorized BOSS session between CDP browsers.

    Cookie values remain in memory and are never included in the returned result.
    """
    if not authorized:
        return {
            "status": "blocked",
            "code": "authorization_required",
            "imported_count": 0,
        }
    if source_cdp_port == target_cdp_port:
        return {
            "status": "blocked",
            "code": "source_target_port_conflict",
            "imported_count": 0,
        }
    profile_checker = target_profile_checker or (
        lambda port: browser.cdp_port_uses_profile(port, boss_constants.DEFAULT_CDP_DATA_DIR)
    )
    try:
        target_is_dedicated = bool(profile_checker(target_cdp_port))
    except Exception:
        target_is_dedicated = False
    if not target_is_dedicated:
        return {
            "status": "blocked",
            "code": "target_not_dedicated_profile",
            "imported_count": 0,
        }

    source = None
    target = None
    try:
        source = session_factory(source_cdp_port)
        target = session_factory(target_cdp_port)
        source_cookies = [
            normalized
            for cookie in _cdp_cookies(source)
            if (normalized := normalize_boss_cookie(cookie)) is not None
        ]
        target_cookies = [
            normalized
            for cookie in _cdp_cookies(target)
            if (normalized := normalize_boss_cookie(cookie)) is not None
        ]
        if not source_cookies:
            return {"status": "failed", "code": "no_boss_session", "imported_count": 0}
        try:
            response = target.send("Storage.setCookies", {"cookies": source_cookies})
        except Exception:
            if not _rollback_boss_cookies(target, source_cookies, target_cookies):
                return {"status": "failed", "code": "session_import_rollback_failed", "imported_count": 0}
            return {"status": "failed", "code": "session_write_failed", "imported_count": 0}
        if not isinstance(response, dict) or response.get("error"):
            if not _rollback_boss_cookies(target, source_cookies, target_cookies):
                return {"status": "failed", "code": "session_import_rollback_failed", "imported_count": 0}
            return {"status": "failed", "code": "session_write_failed", "imported_count": 0}
        checker = login_checker or login.check_login_state
        try:
            verified = bool(checker(target_cdp_port))
        except Exception:
            verified = False
        if not verified:
            if not _rollback_boss_cookies(target, source_cookies, target_cookies):
                return {"status": "failed", "code": "session_import_rollback_failed", "imported_count": 0}
            return {"status": "failed", "code": "session_import_unverified", "imported_count": 0}
        return {"status": "completed", "code": "ok", "imported_count": len(source_cookies)}
    except Exception:
        return {"status": "failed", "code": "session_import_failed", "imported_count": 0}
    finally:
        for session in (source, target):
            if session is not None:
                try:
                    session.close()
                except Exception:
                    _logger.debug("会话关闭失败（best-effort 忽略）", exc_info=True)



def run_import_boss_session(source_cdp_port, target_cdp_port, authorized=False):
    """CLI boundary for a safe, auditable BOSS-only session import."""
    print("=" * 50)
    print("  BOSS 专用会话导入")
    print("=" * 50)
    if not authorized:
        result = {"status": "blocked", "code": "authorization_required", "imported_count": 0}
    elif source_cdp_port is None:
        result = {"status": "blocked", "code": "source_cdp_port_required", "imported_count": 0}
    elif source_cdp_port == target_cdp_port:
        result = {"status": "blocked", "code": "source_target_port_conflict", "imported_count": 0}
    elif not browser.cdp_port_uses_profile(target_cdp_port, boss_constants.DEFAULT_CDP_DATA_DIR):
        result = {"status": "blocked", "code": "target_not_dedicated_profile", "imported_count": 0}
    else:
        result = import_boss_session(
            source_cdp_port=source_cdp_port,
            target_cdp_port=target_cdp_port,
            authorized=authorized,
        )
    print(f"status={result['status']}")
    print(f"code={result['code']}")
    print(f"imported_count={result['imported_count']}")
    return 0 if result["status"] == "completed" else 1
