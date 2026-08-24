"""浏览器账号簿与 CDP 数据目录（021 B7 自 pipeline_exec.py 搬运）。"""

from __future__ import annotations

from webui.pipeline_exec_settings import _ADVANCED_SETTINGS_DIR

import json
import os
import sys
import threading
import uuid
from pathlib import Path



BROWSER_ACCOUNTS: dict[str, dict[str, str]] = {
    "a": {
        "name": "账号A",
        "profile_dir": os.path.expanduser("~/.career-scout/chrome-profile"),
    },
    "b": {
        "name": "Mom",
        "profile_dir": os.path.normpath(
            os.path.join(str(Path(__file__).resolve().parents[1]), ".chrome-profiles", "account_b")
        ),
    },
}



_BROWSER_ACCOUNTS_PATH: Path | None = None


_BROWSER_ACCOUNTS_LOCK = threading.RLock()



def set_browser_accounts_path(path: str | os.PathLike[str]) -> None:
    """Set the JSON file used for user-defined browser accounts."""
    global _BROWSER_ACCOUNTS_PATH
    _BROWSER_ACCOUNTS_PATH = Path(path)



def browser_accounts_path() -> Path:
    if _BROWSER_ACCOUNTS_PATH is not None:
        return _BROWSER_ACCOUNTS_PATH
    return _ADVANCED_SETTINGS_DIR / "browser_accounts.json"



def reset_browser_accounts_path() -> None:
    """Clear the app-injected account file path (mainly for test isolation)."""
    global _BROWSER_ACCOUNTS_PATH
    _BROWSER_ACCOUNTS_PATH = None



def _default_browser_accounts() -> dict[str, dict[str, str | bool]]:
    return {
        aid: {
            "id": aid, "name": str(item["name"]), "profile_dir": str(item["profile_dir"]),
            "builtin": aid == "a",
        } for aid, item in BROWSER_ACCOUNTS.items()
    }



def load_browser_accounts(path: str | os.PathLike[str] | None = None) -> dict[str, dict[str, str | bool]]:
    """Load browser accounts; the accounts file, when present, is authoritative."""
    accounts_path = Path(path) if path is not None else browser_accounts_path()
    accounts = {}
    try:
        if accounts_path.is_file():
            with open(accounts_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for aid, item in saved.items():
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    profile_dir = str(item.get("profile_dir") or "").strip()
                    if not name or not profile_dir:
                        continue
                    accounts[str(aid)] = {
                        "id": str(aid), "name": name,
                        "profile_dir": os.path.abspath(os.path.expanduser(profile_dir)),
                        # 只有默认账号不可删；历史文件里账号 b 的 builtin 标记不再沿用。
                        "builtin": str(aid) == "a",
                    }
            accounts.setdefault("a", _default_browser_accounts()["a"])
        else:
            accounts = _default_browser_accounts()
    except (OSError, json.JSONDecodeError, TypeError):
        accounts = _default_browser_accounts()
    return accounts



def save_browser_accounts(accounts: dict, path: str | os.PathLike[str] | None = None) -> None:
    """Atomically persist browser accounts."""
    accounts_path = Path(path) if path is not None else browser_accounts_path()
    accounts_path.parent.mkdir(parents=True, exist_ok=True)
    clean = {}
    for aid, item in accounts.items():
        clean[str(aid)] = {
            "id": str(aid),
            "name": str(item.get("name") or "").strip(),
            "profile_dir": os.path.abspath(os.path.expanduser(str(item.get("profile_dir") or ""))),
            "builtin": bool(item.get("builtin", False)),
        }
    tmp = accounts_path.with_name(f".{accounts_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, accounts_path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError("browser_accounts_write_failed") from exc



def _normalize_account_name(name: str, accounts: dict) -> str:
    name = str(name or "").strip()
    if not name:
        raise ValueError("账号名称不能为空")
    if len(name) > 30:
        raise ValueError("账号名称不能超过 30 个字符")
    if any(ord(ch) < 32 for ch in name):
        raise ValueError("账号名称不能包含控制字符")
    existing = {str(v.get("name", "")).lower() for v in accounts.values()}
    if name.lower() in existing:
        raise ValueError("账号名称已存在")
    return name



def resolve_browser_account(
        account_id: str, path: str | os.PathLike[str] | None = None) -> str:
    """Return the profile dir for a registered account id, or '' if unknown."""
    accounts = load_browser_accounts(path)
    account = accounts.get(str(account_id or ""))
    return str(account["profile_dir"]) if account else ""



def add_browser_account(
        name: str, profile_dir: str = "", path: str | os.PathLike[str] | None = None) -> dict:
    """Add a user-defined browser account and return it."""
    with _BROWSER_ACCOUNTS_LOCK:
        accounts = load_browser_accounts(path)
        name = _normalize_account_name(name, accounts)
        account_id = uuid.uuid4().hex[:10]
        if not str(profile_dir or "").strip():
            profile_dir = os.path.join(
                str(Path(__file__).resolve().parents[1]), ".chrome-profiles", f"account_{account_id}"
            )
        normalized_dir = os.path.abspath(os.path.expanduser(str(profile_dir)))
        existing_dirs = {
            os.path.normcase(str(v.get("profile_dir") or ""))
            for v in accounts.values()
        }
        if os.path.normcase(normalized_dir) in existing_dirs:
            raise ValueError("浏览器资料目录不能与其他账号重复")
        account = {
            "id": account_id, "name": name,
            "profile_dir": normalized_dir, "builtin": False,
        }
        accounts[account_id] = account
        save_browser_accounts(accounts, path)
        return dict(account)



def delete_browser_account(account_id: str, path: str | os.PathLike[str] | None = None) -> None:
    with _BROWSER_ACCOUNTS_LOCK:
        accounts = load_browser_accounts(path)
        account = accounts.get(str(account_id))
        if account is None:
            raise KeyError(account_id)
        if account.get("builtin"):
            raise ValueError("内置账号不能删除")
        del accounts[str(account_id)]
        save_browser_accounts(accounts, path)
        from scripts.login_state_cache import forget_login_state
        forget_login_state(str(account_id))



_ACTIVE_CDP_DATA_DIR: str | None = None



def set_active_cdp_data_dir(account_or_dir: str) -> None:
    """Set the browser profile directory used by Chrome helpers."""
    global _ACTIVE_CDP_DATA_DIR
    account = str(account_or_dir or "")
    accounts = load_browser_accounts()
    if account in accounts:
        _ACTIVE_CDP_DATA_DIR = str(accounts[account]["profile_dir"])
    elif account in BROWSER_ACCOUNTS:
        _ACTIVE_CDP_DATA_DIR = BROWSER_ACCOUNTS[account]["profile_dir"]
    elif account:
        _ACTIVE_CDP_DATA_DIR = os.path.abspath(os.path.expanduser(account))
    else:
        _ACTIVE_CDP_DATA_DIR = None
    # 门面曾直接持有该全局（外部以 webui.pipeline_exec._ACTIVE_CDP_DATA_DIR
    # 读写），拆分后权威值在本模块；同步回写门面命名空间保持可 patch 语义。
    _facade = sys.modules.get("webui.pipeline_exec")
    if _facade is not None:
        _facade._ACTIVE_CDP_DATA_DIR = _ACTIVE_CDP_DATA_DIR



def _cdp_data_dir() -> str:
    """Resolve the active browser profile directory."""
    from webui import pipeline_exec as _facade
    if _ACTIVE_CDP_DATA_DIR:
        return _ACTIVE_CDP_DATA_DIR
    accounts = load_browser_accounts()
    account = str(_facade.load_advanced_settings().get("browser_account") or "a")
    return str(accounts.get(account, accounts["a"])["profile_dir"])
