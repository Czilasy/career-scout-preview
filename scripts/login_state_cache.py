"""登录态缓存（账号 × 平台），独立 JSON 文件放 ~/.career-scout/login-state.json。

结构::

    {
      "account_id": {
        "boss": {"state": "logged_in", "at": 1785940000.0},
        "zhilian": {"state": "not_logged_in", "at": 1785940000.0}
      }
    }

state 四态: "logged_in" / "not_logged_in" / "restricted" / "unknown"。
- TTL 15 分钟（可注入覆盖）；
- 信号回写：登录成功（打开/等待登录完成）→ invalidate 立即失效重探；
  任务抓取持续拿到明文工资 → 写 logged_in；RiskControlError 终止 → 写 restricted；
  智联 DOM marker 探测结果也进同一缓存。

模块级路径可注入（set_login_state_path），测试隔离用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

LOGIN_STATE_TTL = 15 * 60  # 秒；D3: TTL 15 分钟
LOGIN_STATE_STATES = ("logged_in", "not_logged_in", "restricted", "unknown")

_PATH_OVERRIDE: Path | None = None
_LOCK = threading.RLock()


def login_state_path() -> Path:
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    return Path.home() / ".career-scout" / "login-state.json"


def set_login_state_path(path: str | os.PathLike[str]) -> None:
    """Set the JSON file used for login-state cache (mainly test isolation)."""
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = Path(path)


def reset_login_state_path() -> None:
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = None


def _load() -> dict:
    path = login_state_path()
    try:
        if path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {}


def _save(data: dict) -> None:
    path = login_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_login_state(account_id: str, platform: str) -> dict | None:
    """Return the raw cached record ``{"state", "at"}`` or None when absent."""
    account_id = str(account_id or "")
    platform = str(platform or "")
    if not account_id or not platform:
        return None
    with _LOCK:
        record = _load().get(account_id, {}).get(platform)
    if not isinstance(record, dict):
        return None
    state = str(record.get("state") or "")
    at = record.get("at")
    if state not in LOGIN_STATE_STATES or not isinstance(at, (int, float)):
        return None
    return {"state": state, "at": float(at)}


def read_cached_state(
    account_id: str, platform: str, *, ttl: float = LOGIN_STATE_TTL,
) -> str | None:
    """Return the cached state only when fresh (within TTL); None otherwise.

    None 表示无记录或已过期，调用方应重新探测。
    """
    record = read_login_state(account_id, platform)
    if record is None:
        return None
    if time.time() - record["at"] > ttl:
        return None
    return record["state"]


def write_login_state(account_id: str, platform: str, state: str) -> None:
    """Persist a state for account × platform (atomic write)."""
    account_id = str(account_id or "")
    platform = str(platform or "")
    if not account_id or not platform or state not in LOGIN_STATE_STATES:
        return
    with _LOCK:
        data = _load()
        data.setdefault(account_id, {})[platform] = {
            "state": state,
            "at": time.time(),
        }
        _save(data)


def invalidate_login_state(
    account_id: str, platform: str | None = None,
) -> None:
    """Invalidate the login cache for one account (one or all platforms).

    登录成功信号（wait_for_login 完成 / 打开浏览器登录）触发，
    使下次读取重新探测，避免沿用登录前的旧状态。
    """
    account_id = str(account_id or "")
    if not account_id:
        return
    with _LOCK:
        data = _load()
        account = data.get(account_id)
        if account is None:
            return
        if platform:
            account.pop(str(platform), None)
            if not account:
                data.pop(account_id, None)
        else:
            data.pop(account_id, None)
        _save(data)


def all_login_states() -> dict:
    """Return a clean snapshot {account_id: {platform: {state, at}}} for UI."""
    with _LOCK:
        data = _load()
    snapshot = {}
    for account_id, platforms in data.items():
        clean = {}
        for platform, record in platforms.items():
            if isinstance(record, dict) and record.get("state") in LOGIN_STATE_STATES:
                clean[str(platform)] = {
                    "state": record["state"],
                    "at": record.get("at"),
                }
        if clean:
            snapshot[str(account_id)] = clean
    return snapshot