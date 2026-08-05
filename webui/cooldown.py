"""风控冷却（账号级）。~/.career-scout/cooldown.json

结构::

    {
      "account_id": {
        "boss": {"until": 1785945600.0, "reason": "操作频繁，请稍后再试", "from_run": ""}
      }
    }

- 默认冷却 4 小时；风控文本含可解析的完整日期时间解封点时用精确时间
  （parse_unlock_time，见 scripts/boss_cdp_raw.py），否则 now + 4h；
- 写入时机：抓取链路命中风控（source_blocked / source_rate_limited /
  source_verification_required）时由 webui 侧同步标记，同时把
  "restricted" 回写登录态缓存；
- 生效机制：同账号提交任务 → 后端拒绝（返回剩余等待时间）；其他账号提交
  → warning 不拒绝（连坐提醒）；env-check 面板展示建议等待时间；
- 手动解除只清 cooldown，不碰登录态缓存。

模块级路径可注入（set_cooldown_path），测试隔离用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DEFAULT_COOLDOWN_SECONDS = 4 * 3600  # 默认 4 小时

_PATH_OVERRIDE: Path | None = None
_LOCK = threading.RLock()


def cooldown_path() -> Path:
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    return Path.home() / ".career-scout" / "cooldown.json"


def set_cooldown_path(path: str | os.PathLike[str]) -> None:
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = Path(path)


def reset_cooldown_path() -> None:
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = None


def _load() -> dict:
    path = cooldown_path()
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
    path = cooldown_path()
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


def mark_cooldown(
    account_id: str,
    platform: str,
    reason_text: str,
    *,
    from_run: str = "",
    seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> float | None:
    """Write a cooldown for account × platform.

    reason_text 里若含完整日期时间解封点（parse_unlock_time 可解析），
    用精确时间；否则用 now + seconds。返回 until 时间戳；参数无效返回 None。
    """
    account_id = str(account_id or "")
    platform = str(platform or "")
    if not account_id or not platform:
        return None
    try:
        from scripts.boss_cdp_raw import parse_unlock_time
        unlock_at = parse_unlock_time(reason_text or "")
    except Exception:
        unlock_at = None
    until = (
        unlock_at.timestamp()
        if unlock_at is not None
        else time.time() + max(1, int(seconds))
    )
    with _LOCK:
        data = _load()
        data.setdefault(account_id, {})[platform] = {
            "until": float(until),
            "reason": str(reason_text or "")[:300],
            "from_run": str(from_run or "")[:64],
        }
        _save(data)
    return float(until)


def get_cooldown(account_id: str, platform: str) -> dict | None:
    """Return the live cooldown record or None (absent / already expired)."""
    account_id = str(account_id or "")
    platform = str(platform or "")
    if not account_id or not platform:
        return None
    with _LOCK:
        record = _load().get(account_id, {}).get(platform)
    if not isinstance(record, dict):
        return None
    until = record.get("until")
    if not isinstance(until, (int, float)):
        return None
    if float(until) <= time.time():
        return None
    return {
        "until": float(until),
        "reason": str(record.get("reason") or ""),
        "from_run": str(record.get("from_run") or ""),
    }


def remaining_seconds(account_id: str, platform: str) -> int:
    """Remaining cooldown seconds for account × platform (0 = none)."""
    record = get_cooldown(account_id, platform)
    if record is None:
        return 0
    return max(0, int(record["until"] - time.time()))


def clear_cooldown(account_id: str, platform: str | None = None) -> None:
    """Manually clear a cooldown (never touches the login-state cache)."""
    account_id = str(account_id or "")
    if not account_id:
        return
    with _LOCK:
        data = _load()
        if platform:
            account = data.get(account_id)
            if account is None:
                return
            account.pop(str(platform), None)
            if not account:
                data.pop(account_id, None)
        else:
            data.pop(account_id, None)
        _save(data)


def all_cooldowns() -> dict:
    """Return a pruned snapshot {account_id: {platform: record}} for the UI."""
    with _LOCK:
        data = _load()
    snapshot = {}
    now = time.time()
    for account_id, platforms in data.items():
        clean = {}
        for platform, record in platforms.items():
            if isinstance(record, dict) and isinstance(record.get("until"), (int, float)):
                if float(record["until"]) > now:
                    clean[str(platform)] = {
                        "until": float(record["until"]),
                        "reason": str(record.get("reason") or ""),
                        "from_run": str(record.get("from_run") or ""),
                    }
        if clean:
            snapshot[str(account_id)] = clean
    return snapshot