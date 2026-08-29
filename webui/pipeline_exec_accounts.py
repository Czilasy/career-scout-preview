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
            "builtin": aid == "a", "roles": [],
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
                        # B073：角色标记（R1/R2 可同时），旧文件无字段时兼容为空。
                        "roles": _normalize_roles(item.get("roles")),
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
            "roles": _normalize_roles(item.get("roles")),
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


# ---------------------------------------------------------------------------
# 按浏览器命名空间的数据目录派生（029 research D6）
# ---------------------------------------------------------------------------
def effective_data_dir(profile_dir, browser_key) -> str:
    """按所选浏览器派生生效数据目录（纯函数，不触碰文件系统）。

    - ``chrome`` / ``edge``（及空/未知键）→ 原样返回：存量账号登录态无损，
      chrome↔edge 之间切换免重登（与 029 前行为一致）；
    - 其他浏览器 → ``<profile_dir 父目录>/chrome-profile-<data_dir_key>/<目录名>``：
      切过去 = 各账号空目录（重新登录一次），旧目录原样保留、切回免重登；
    - 手动指定路径模式由调用方传 ``"manual"`` 作为命名空间键。
    """
    raw = str(profile_dir or "")
    key = str(browser_key or "").strip().lower()
    if not raw.strip() or key in ("", "chrome", "edge", "auto"):
        # 恒等分支原样返回：不做 abspath 规范化（调用方契约是字符串恒等，
        # POSIX 风格路径在 Windows 上规范化会改变字面值）
        return raw
    profile_dir = os.path.abspath(os.path.expanduser(raw))
    parent = os.path.dirname(profile_dir)
    name = os.path.basename(profile_dir) or "profile"
    return os.path.join(parent, f"chrome-profile-{key}", name)


def browser_data_dir_key() -> str | None:
    """当前浏览器选择对应的命名空间键（029 审查修复：实现下沉注册表域，
    此处保留兼容别名，见 ``scripts.boss.browser_registry.selection_data_dir_key``）。"""
    from scripts.boss.browser_registry import selection_data_dir_key

    return selection_data_dir_key()



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
    """Resolve the active browser profile directory.

    029：返回值经 :func:`effective_data_dir` 按当前浏览器选择翻译到对应
    命名空间——启动、关闭、profile 校验共用本漏斗，保证三者指向一致。
    """
    from webui import pipeline_exec as _facade
    if _ACTIVE_CDP_DATA_DIR:
        return effective_data_dir(_ACTIVE_CDP_DATA_DIR, browser_data_dir_key())
    accounts = load_browser_accounts()
    account = str(_facade.load_advanced_settings().get("browser_account") or "a")
    profile_dir = str(accounts.get(account, accounts["a"])["profile_dir"])
    return effective_data_dir(profile_dir, browser_data_dir_key())



_ROLE_VALUES = ("R1", "R2")


def _normalize_roles(roles) -> list[str]:
    """Normalize a raw roles value to a valid list of R1/R2 tags (dedup, order kept)."""
    if not isinstance(roles, list):
        return []
    seen: list[str] = []
    for raw in roles:
        value = str(raw or "").strip()
        if value in _ROLE_VALUES and value not in seen:
            seen.append(value)
    return seen



def resolve_account_for_role(accounts: dict, role: str) -> str | None:
    """Return the first account id whose roles contain ``role``; None when unassigned."""
    role = str(role or "").strip()
    for aid, item in accounts.items():
        if role in (item.get("roles") or []):
            return str(aid)
    return None



def assign_account_role(
        accounts: dict, role: str, account_id_or_none: str | None) -> dict:
    """Role→account one-to-one tag: clear ``role`` on every account, then tag the target.

    ``account_id_or_none`` of None clears the role entirely (back to 未指定).
    Returns a new accounts dict for the caller to persist; does not mutate the input.
    """
    role = str(role or "").strip()
    if role not in _ROLE_VALUES:
        raise ValueError("角色必须是 R1 或 R2")
    target = None if account_id_or_none is None else str(account_id_or_none)
    updated = {}
    for aid, item in accounts.items():
        roles = [r for r in (item.get("roles") or []) if r != role]
        if target is not None and str(aid) == target:
            roles.append(role)
        updated[str(aid)] = {**item, "roles": roles}
    return updated



def account_for_role(
        role: str,
        accounts_path: str | os.PathLike[str] | None = None,
        run: dict | None = None,
        fallback: str = "a") -> str:
    """Resolve the browser account for a BOSS task stage role (B073).

    Priority:
    1. run 冻结值优先——续跑一律沿用任务创建时冻结的 browser_account，
       不重新按角色解析（冻结需求：运行中改角色不影响当前任务）；
    2. 角色解析——账号簿中第一个带该角色标记的账号；
    3. 登录态检测——fresh 缓存为 not_logged_in/restricted 视为不可用，跳过；
    4. 角色未指定或账号不可用 → fallback（调用方传当前账号），不报错不阻断。
    """
    accounts = load_browser_accounts(accounts_path)
    if isinstance(run, dict):
        params = run.get("execution_params") or {}
        if isinstance(params, dict):
            frozen = str(params.get("browser_account") or "")
            if frozen in accounts:
                return frozen
    account_id = resolve_account_for_role(accounts, role)
    if account_id is not None:
        from scripts.login_state_cache import read_cached_state
        state = read_cached_state(str(account_id), "boss")
        if state in ("not_logged_in", "restricted"):
            account_id = None
    if account_id is None:
        return fallback if fallback in accounts else "a"
    return account_id
