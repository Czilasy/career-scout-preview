"""高级设置读写（021 B7 自 pipeline_exec.py 搬运）。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path




_PIPELINE_OPERATION_ERRORS = (
    OSError,
    sqlite3.Error,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    ConnectionError,
    TimeoutError,
)





# ---------------------------------------------------------------------------
# 高级设置（用户可通过前端调整，持久化到 JSON）
# ---------------------------------------------------------------------------
# 与 app.py 的 DEFAULT_STATE_DIR 保持一致：允许通过环境变量把状态目录改到
# 项目内，避免在沙箱环境中因无法写用户 home 目录而保存失败。
_ADVANCED_SETTINGS_DIR = Path(
    os.environ.get("CAREER_SCOUT_STATE_DIR")
    or os.environ.get("BOSS_WEBUI_STATE_DIR")
    or os.path.expanduser("~/.career-scout/webui")
)


ADVANCED_SETTINGS_PATH = _ADVANCED_SETTINGS_DIR / "advanced_settings.json"



_ADVANCED_DEFAULTS = {
    "pages": 3,
    "browser_account": "a",
    "inter_combo_delay": 10.0,
    "detail_batch_size": 15,
    "detail_interval": 2,
    "detail_reset_every": 4,
    "detail_batch_cooldown": 5,
    "detail_tab_pool_size": 5,
    "screen_batch_size": 50,
    "screen_concurrency": 5,
    "match_batch_size": 4,
    "match_concurrency": 10,
}




_MSG_CDP_UNAVAILABLE = "连不上调试浏览器"


_MSG_IP_RISK_CONTROL = "IP 级风控拦截"


_MSG_ZHILIAN_LOGIN_REQUIRED = "智联登录已失效，需重新登录"




def load_advanced_settings(path: str | os.PathLike[str] | None = None) -> dict:
    """读取高级设置，缺字段用默认值补全。"""
    settings_path = Path(path) if path is not None else ADVANCED_SETTINGS_PATH
    settings = dict(_ADVANCED_DEFAULTS)
    try:
        if settings_path.is_file():
            with open(settings_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                settings.update({k: v for k, v in saved.items() if k in _ADVANCED_DEFAULTS})
    except (json.JSONDecodeError, OSError):
        pass
    return settings




def save_advanced_settings(
    settings: dict,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """持久化高级设置到 JSON 文件。"""
    from webui import pipeline_exec as _facade
    settings_path = Path(path) if path is not None else ADVANCED_SETTINGS_PATH
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    # 部分写入（如只保存速度字段）不能覆盖旧文件里未提交的字段（如 browser_account）。
    merged = dict(_facade.load_advanced_settings(settings_path))
    merged.update({k: v for k, v in settings.items() if k in _ADVANCED_DEFAULTS})
    clean = {k: v for k, v in merged.items() if k in _ADVANCED_DEFAULTS}
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
