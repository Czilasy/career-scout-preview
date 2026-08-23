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
- 信号回写：登录成功（打开/等待登录完成）→ invalidate 软失效（保留上次结果、
  时间回拨到 TTL 外，任务侧读到的是"需重探"，UI 侧仍能看到上次确认的状态）；
  任务抓取持续拿到明文工资 → 写 logged_in；登录失效 → 写 not_logged_in；
  受限类信号不写缓存（016：受限只在当次任务内实时判定）；
  账号被删除 → forget 硬删该账号全部记录，不留幽灵数据。

模块级路径可注入（set_login_state_path），测试隔离用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

LOGIN_STATE_TTL = 15 * 60  # 秒；D3: TTL 15 分钟
# 016-error-module-rework：登录缓存只存事实态。受限（restricted）是瞬态，
# 持久化只会制造跨任务假拦截，已从值域移除；旧文件遗留的 restricted
# 记录在读取时按无缓存处理（触发重探），无需迁移。
LOGIN_STATE_STATES = ("logged_in", "not_logged_in", "unknown")

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
    """软失效：保留记录，只把时间回拨到 TTL 之外。

    登录成功信号（wait_for_login 完成 / 打开浏览器登录）触发。
    任务侧 read_cached_state 按 TTL 判定过期 → 下次 preflight 真实重探，
    不沿用登录前的旧判定（016 语义不变）；UI 侧 all_login_states 仍能读到
    上一次任务确认的状态，徽章显示「上次结果 · 待刷新」而不是「未使用过」。
    """
    account_id = str(account_id or "")
    if not account_id:
        return
    with _LOCK:
        data = _load()
        account = data.get(account_id)
        if not isinstance(account, dict):
            return
        changed = False
        records = (
            [account.get(str(platform))] if platform
            else list(account.values())
        )
        for record in records:
            if isinstance(record, dict) and record.get("state") in LOGIN_STATE_STATES:
                record["at"] = 0.0
                changed = True
        if changed:
            _save(data)


def forget_login_state(account_id: str) -> None:
    """硬删除整个账号的登录记录（账号被删除时调用）。"""
    account_id = str(account_id or "")
    if not account_id:
        return
    with _LOCK:
        data = _load()
        if data.pop(account_id, None) is None:
            return
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