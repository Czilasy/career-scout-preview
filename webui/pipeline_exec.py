"""Three-stage pipeline execution layer (stage 3).

Expands the confirmed multi-select search params into keyword × city
combinations, runs the BOSS CDP scraper for each combination (reusing the
scraper's built-in anti-rate-limit protections: random page delays,
human-like scrolling, request caps, circuit breaker), merges and dedups
the results, then applies the multi-select filters as a local post-filter.

The scraper subprocess enforces per-search rate limiting on its own.  This
layer adds a random delay BETWEEN combinations so consecutive searches are
never back-to-back, absorbing the same "slow is safe" philosophy.
"""

from __future__ import annotations

import json
import hashlib
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from types import MappingProxyType

from scripts import boss_cdp_raw as boss


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
    settings_path = Path(path) if path is not None else ADVANCED_SETTINGS_PATH
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in settings.items() if k in _ADVANCED_DEFAULTS}
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)

BROWSER_ACCOUNTS: dict[str, dict[str, str]] = {
    "a": {
        "name": "账号A",
        "profile_dir": os.path.expanduser("~/.career-scout/chrome-profile"),
    },
    "b": {
        "name": "账号B",
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
            "builtin": True,
        } for aid, item in BROWSER_ACCOUNTS.items()
    }

def load_browser_accounts(path: str | os.PathLike[str] | None = None) -> dict[str, dict[str, str | bool]]:
    """Load browser accounts, always merging built-in A/B defaults."""
    accounts_path = Path(path) if path is not None else browser_accounts_path()
    accounts = _default_browser_accounts()
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
                        "builtin": bool(item.get("builtin", str(aid) in ("a", "b"))),
                    }
    except (OSError, json.JSONDecodeError, TypeError):
        pass
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

def _cdp_data_dir() -> str:
    """Resolve the active browser profile directory."""
    if _ACTIVE_CDP_DATA_DIR:
        return _ACTIVE_CDP_DATA_DIR
    accounts = load_browser_accounts()
    account = str(load_advanced_settings().get("browser_account") or "a")
    return str(accounts.get(account, accounts["a"])["profile_dir"])


# ---------------------------------------------------------------------------
# 进度百分比与阶段文案
# ---------------------------------------------------------------------------
_SCRAPE_STAGE_WEIGHTS: dict[str, tuple[int, int]] = {
    "ensure_chrome": (0, 5),
    "preflight": (5, 10),
    "searching": (10, 90),
    "combo_done": (10, 90),
    "combo_failed": (10, 90),
    "waiting": (10, 90),
    "risk_warning": (90, 95),
    "closing_chrome": (95, 100),
    "done": (100, 100),
}

_SCRAPE_STAGE_MESSAGES: dict[str, str] = {
    "ensure_chrome": "检查并启动调试浏览器…",
    "preflight": "检查 BOSS 登录状态…",
    "searching": "列表页抓取中…",
    "combo_done": "列表页抓取中…",
    "combo_failed": "部分组合抓取失败，继续中…",
    "waiting": "防限流等待中…",
    "risk_warning": "所有组合均失败，建议手动过验证码…",
    "closing_chrome": "正在关闭调试浏览器…",
    "done": "抓取完成",
    "cancelled": "运行已取消",
}

# failed_code → 用户可读中文（进度条状态文案用）
_FAILED_CODE_LABELS: dict[str, str] = {
    "source_cdp_unavailable": "连不上调试浏览器",
    "source_login_required": "BOSS 登录已失效",
    "source_verification_required": "触发验证码/滑块",
    "source_rate_limited": "账号/操作频繁被限流",
    "source_blocked": "IP 级风控拦截",
    "source_timeout": "抓取超时",
    "source_unreachable": "抓取脚本不可用",
    # 010 healthy-pipeline 新增（与 ERROR_TAXONOMY 对齐）
    "captcha_required": "触发验证码/滑块，需手动完成",
    "login_expired": "BOSS 登录已失效，需重新登录",
    "ai_rate_limited": "AI 服务限流，请求过于频繁",
    "ai_quota_exhausted": "AI 额度已耗尽",
    "ai_key_invalid": "AI 密钥失效或鉴权失败",
    "ai_network_error": "AI 网络或服务故障",
    "ip_risk_control": "IP 级风控拦截",
    "cdp_unavailable": "连不上调试浏览器",
    "job_offline": "岗位已下架",
    "detail_timeout": "单岗位详情抓取超时",
    "detail_invalid": "详情结构无效（登录墙/导航壳/空壳）",
    "ai_missing_job": "AI 漏回单个岗位判定",
    "internal_error": "内部状态或持久化错误",
}


# 统一错误分类码表（FR-040/SC-006）—— 13 类错误
# 每条含：impact（影响范围）/ blocking（是否阻断整任务）/ retryable（是否可重试）/
# reason（用户可读原因）/ resume_condition（继续条件）
ERROR_TAXONOMY: dict[str, dict] = {
    "captcha_required": {
        "impact": "systemic",
        "blocking": True,
        "retryable": True,
        "reason": "触发验证码/滑块，需手动完成",
        "resume_condition": "用户完成验证码后点继续",
    },
    "login_expired": {
        "impact": "systemic",
        "blocking": True,
        "retryable": True,
        "reason": "BOSS 登录已失效，需重新登录",
        "resume_condition": "用户重新登录后点继续",
    },
    "ai_rate_limited": {
        "impact": "systemic",
        "blocking": True,
        "retryable": True,
        "reason": "AI 服务限流，请求过于频繁",
        "resume_condition": "等待限流解除后点继续",
    },
    "ai_quota_exhausted": {
        "impact": "systemic",
        "blocking": True,
        "retryable": False,
        "reason": "AI 额度已耗尽",
        "resume_condition": "充值或更换密钥后点继续",
    },
    "ai_key_invalid": {
        "impact": "systemic",
        "blocking": True,
        "retryable": False,
        "reason": "AI 密钥失效或鉴权失败",
        "resume_condition": "更换有效密钥后点继续",
    },
    "ai_network_error": {
        "impact": "systemic",
        "blocking": True,
        "retryable": True,
        "reason": "AI 网络或服务故障",
        "resume_condition": "网络恢复后点继续",
    },
    "ip_risk_control": {
        "impact": "systemic",
        "blocking": True,
        "retryable": True,
        "reason": "IP 级风控拦截",
        "resume_condition": "更换网络或等待后点继续",
    },
    "cdp_unavailable": {
        "impact": "systemic",
        "blocking": True,
        "retryable": True,
        "reason": "连不上调试浏览器",
        "resume_condition": "启动 Chrome 调试端口后点继续",
    },
    "job_offline": {
        "impact": "independent",
        "blocking": False,
        "retryable": False,
        "reason": "岗位已下架",
        "resume_condition": "无需继续，该岗位进入待确认",
    },
    "detail_timeout": {
        "impact": "independent",
        "blocking": False,
        "retryable": True,
        "reason": "单岗位详情抓取超时",
        "resume_condition": "可单条补抓重试",
    },
    "detail_invalid": {
        "impact": "independent",
        "blocking": False,
        "retryable": False,
        "reason": "详情结构无效（登录墙/导航壳/空壳）",
        "resume_condition": "可单条补抓",
    },
    "ai_missing_job": {
        "impact": "independent",
        "blocking": False,
        "retryable": True,
        "reason": "AI 漏回单个岗位判定",
        "resume_condition": "可单条补抓重试",
    },
    "internal_error": {
        "impact": "systemic",
        "blocking": True,
        "retryable": False,
        "reason": "内部状态或持久化错误",
        "resume_condition": "需人工排查日志",
    },
}


# 硬停止码（命中即暂停整个任务）—— 与 store.SYSTEMIC_BLOCK_CODES 对齐
_HARD_STOP_CODES: set[str] = {
    "captcha_required", "login_expired",
    "ai_rate_limited", "ai_quota_exhausted", "ai_key_invalid", "ai_network_error",
    "ip_risk_control", "cdp_unavailable", "internal_error",
    "source_verification_required", "source_login_required",
    "source_rate_limited", "source_blocked", "source_cdp_unavailable",
}


def _classify_detail_batch_exception(exc: Exception) -> str:
    """Map a batch-level detail failure to a systemic, user-visible code."""
    text = f"{type(exc).__name__}: {exc}".lower()
    cdp_markers = (
        "cdp_", "devtools", "websocket", "chrome", "browser", "session",
        "connection", "disconnected", "target closed",
    )
    if isinstance(exc, (ConnectionError, TimeoutError)) or any(
            marker in text for marker in cdp_markers):
        return "cdp_unavailable"
    return "internal_error"


def _scrape_overall_percent(stage: str, current: int, total: int) -> int:
    """把抓取 pipeline 的当前阶段映射到整体百分比（0-100）。"""
    start, end = _SCRAPE_STAGE_WEIGHTS.get(stage, (0, 100))
    if total <= 0:
        return start
    ratio = min(1.0, max(0.0, current / total))
    return min(100, round(start + (end - start) * ratio))


# ---------------------------------------------------------------------------
# Auto-launch the debug Chrome (self-contained execution)
# ---------------------------------------------------------------------------

def ensure_chrome_ready(cdp_port: int | None = None) -> tuple[bool, str]:
    """Ensure the dedicated debug Chrome is running; launch it if not.

    Returns ``(True, "")`` when CDP is reachable (already running or just
    launched).  Returns ``(False, msg)`` when the browser fails to come up,
    where ``msg`` carries the cause (early exit / stderr tail / timeout) so
    the caller can surface it to the user instead of a generic "not ready".

    This makes execution self-contained: confirming the params auto-opens the
    browser in front of the user instead of surfacing a raw "CDP unavailable"
    infrastructure error.  Login is checked separately afterwards.
    """
    port = cdp_port or boss.DEFAULT_CDP_PORT
    if boss.is_cdp_ready(port):
        cdp_data_dir = _cdp_data_dir()
        if boss.cdp_port_uses_profile(port, cdp_data_dir):
            return True, ""
        known_profiles = {
            boss.normalize_profile_path(str(info["profile_dir"]))
            for info in load_browser_accounts().values()
        }
        try:
            from webui.platforms import derive_zhilian_profile_dir, get_platform
            zhilian_port = int(get_platform("zhilian").default_cdp_port)
        except Exception:
            zhilian_port = 9223
        if port == zhilian_port:
            known_profiles.update(
                boss.normalize_profile_path(derive_zhilian_profile_dir(
                    str(info["profile_dir"])))
                for info in load_browser_accounts().values()
                if str(info.get("profile_dir") or "").strip()
            )
        port_profiles = [
            boss.normalize_profile_path(path)
            for path in boss.chrome_user_data_dirs_for_cdp_port(port)
            if path
        ]
        if not any(profile in known_profiles for profile in port_profiles):
            return False, "CDP 端口被非 scraper 账号的 Chrome 占用，为避免误关未自动切换"
        try:
            boss.close_cdp_chrome(port, cdp_data_dir, profile_checker=lambda *_: True)
        except Exception as exc:
            return False, f"切换账号时关闭旧 Chrome 失败：{type(exc).__name__}"
    # Not running: prepare the isolated profile, stop stale processes, launch.
    profile = boss.prepare_cdp_profile(data_dir=_cdp_data_dir())
    cdp_data_dir = profile["path"]
    try:
        boss.stop_cdp_chrome(cdp_data_dir)
    except Exception:
        pass
    cmd = [
        boss.DEFAULT_CHROME_PATH,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={cdp_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ]
    proc = boss.launch_chrome(cmd)
    # 轮询 CDP，同时检查 Chrome 进程是否还活着
    # 死等 90 秒会让用户莫名其妙，Chrome 早退时立即返回失败原因
    deadline = time.time() + 90
    attempt = 0
    # Windows handoff 机制：当已有相同 user-data-dir 的 Chrome 实例在跑时，
    # 新启动的 chrome.exe 主进程会把命令行转发给已运行实例并立即退出
    # （exit code 通常是 0 或 21），但子进程仍在运行并会监听调试端口。
    # 此时 Popen.poll() 立即返回非 None，但 is_cdp_ready 不久后会变 True。
    # 所以主进程退出后不能立即认为失败，要继续等 CDP 就绪一段时间。
    parent_exited_at = None
    PARENT_EXIT_GRACE = 10  # 主进程退出后给 CDP 10s 宽限期
    while time.time() < deadline:
        if boss.is_cdp_ready(port):
            return True, ""
        try:
            rc = proc.poll()
        except Exception:
            rc = None
        if rc is not None:
            # Chrome 主进程已退出
            if parent_exited_at is None:
                parent_exited_at = time.time()
            # 主进程退出超过宽限期，CDP 还没就绪，才认为真的失败
            if time.time() - parent_exited_at > PARENT_EXIT_GRACE:
                attempt += 1
                if attempt <= 3:
                    # 重试前清理可能残留的 Chrome 子进程
                    # （否则新 Chrome 又会 handoff 给旧子进程，无限循环）
                    try:
                        boss.stop_cdp_chrome(cdp_data_dir)
                    except Exception:
                        pass
                    time.sleep(2)
                    proc = boss.launch_chrome(cmd)
                    parent_exited_at = None
                    continue
                # 重试 3 次都失败，返回错误
                tail = _read_chrome_stderr_tail(cdp_data_dir)
                if tail:
                    return False, f"调试浏览器启动后立即退出（exit code={rc}，已重试 {attempt-1} 次）。stderr 末尾：\n{tail}"
                return False, f"调试浏览器启动后立即退出（exit code={rc}，已重试 {attempt-1} 次），无 stderr 输出。"
        time.sleep(1)
    return False, "等待 CDP 就绪超时（90s）。Chrome 进程仍在运行但未开放调试端口。"


def _read_chrome_stderr_tail(cdp_data_dir: str, max_chars: int = 800) -> str:
    """读取 chrome_stderr.log 的末尾内容，用于诊断启动失败。"""
    log_path = os.path.join(cdp_data_dir, "chrome_stderr.log")
    try:
        with open(log_path, "rb") as f:
            data = f.read()
        if not data:
            return ""
        text = data.decode("utf-8", errors="replace")
        if len(text) > max_chars:
            text = "..." + text[-max_chars:]
        return text.strip()
    except Exception:
        return ""


def close_debug_chrome(cdp_port: int | None = None) -> bool:
    """Close the dedicated debug Chrome (best-effort).

    Uses ``boss.close_cdp_chrome``, which first verifies the port really is
    serving the scraper's isolated profile before closing — so the user's
    regular browser is never touched.  Called after a successful run so the
    automation browser doesn't linger in the taskbar.  A close failure is
    swallowed: it must never break an otherwise successful run.
    """
    port = cdp_port or boss.DEFAULT_CDP_PORT
    try:
        profile = boss.prepare_cdp_profile(data_dir=_cdp_data_dir())
        return bool(boss.close_cdp_chrome(port, profile["path"]))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Search combination expansion
# ---------------------------------------------------------------------------

def split_keywords(keyword: str) -> list[str]:
    """Split a keyword string on Chinese/English commas into distinct terms."""
    if not keyword:
        return []
    parts = str(keyword).replace("，", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def expand_combinations(params: dict) -> list[dict]:
    """Expand confirmed params into a list of single keyword×city searches.

    ``params`` has ``keyword`` (comma string), ``city`` (list) and
    ``filters`` (dict of lists).  Returns one entry per (keyword, city)
    pair, each carrying the full multi-select ``filters`` for post-filtering.
    """
    keywords = split_keywords(params.get("keyword", ""))
    cities = params.get("city") or []
    if isinstance(cities, str):
        cities = [c.strip() for c in cities.replace("，", ",").split(",") if c.strip()]
    filters = params.get("filters") or {}
    combos = []
    for kw in keywords:
        for city in cities:
            combos.append({"keyword": kw, "city": city, "filters": filters})
    return combos


# ---------------------------------------------------------------------------
# Local post-filter: match a job against multi-select filter codes
# ---------------------------------------------------------------------------

def _job_scale_code(job: dict) -> str:
    return boss.SCALE_MAP.get((job.get("company_scale") or "").strip(), "")


def _job_stage_code(job: dict) -> str:
    return boss.STAGE_MAP.get((job.get("company_stage") or "").strip(), "")


def _job_industry_code(job: dict) -> str:
    industry = (job.get("company_industry") or "").strip()
    if industry in boss.INDUSTRY_MAP:
        return boss.INDUSTRY_MAP[industry]
    # Industry strings may be longer ("互联网 · 电商"); try prefix match.
    for name, code in boss.INDUSTRY_MAP.items():
        if name and name in industry:
            return code
    return ""


def _job_exp_degree_codes(job: dict) -> tuple[str, str]:
    """Extract experience and degree codes from the ``tags`` field.

    The scraper joins ``jobExperience`` and ``jobDegree`` into ``tags`` as
    e.g. ``"1-3年 | 本科"``.
    """
    tags = job.get("tags") or ""
    parts = [p.strip() for p in tags.split("|")]
    exp = ""
    deg = ""
    for p in parts:
        if p in boss.EXPERIENCE_MAP:
            exp = boss.EXPERIENCE_MAP[p]
        if p in boss.DEGREE_MAP:
            deg = boss.DEGREE_MAP[p]
    return exp, deg


def _job_salary_code(job: dict) -> str:
    """Best-effort mapping of a plaintext salary string to a SALARY_MAP code.

    Returns "" when the salary is unparseable (e.g. "面议"); callers treat
    an empty code as "unknown" and keep the job rather than dropping it.
    """
    salary = job.get("salary") or ""
    # 1. Direct substring match against band labels ("10-20K·13薪" -> "10-20K").
    for label, code in boss.SALARY_MAP.items():
        if label != "不限" and label in salary:
            return code
    # 2. Numeric fallback: use the lower bound of the first number found.
    nums = re.findall(r"\d+(?:\.\d+)?", salary)
    if not nums:
        return ""
    try:
        low = float(nums[0])
    except ValueError:
        return ""
    if low < 3:
        return "402"
    if low < 5:
        return "403"
    if low < 10:
        return "404"
    if low < 20:
        return "405"
    if low < 50:
        return "406"
    return "407"


def job_matches(job: dict, filters: dict) -> bool:
    """Return True iff *job* satisfies every selected multi-select filter.

    A filter dimension that the user left empty imposes no constraint.  A job
    whose value for a dimension is unknown/empty is kept (we avoid dropping
    jobs on missing data).
    """
    if not filters:
        return True

    scale_sel = filters.get("scale") or []
    if scale_sel:
        code = _job_scale_code(job)
        if code and code not in scale_sel:
            return False

    stage_sel = filters.get("stage") or []
    if stage_sel:
        code = _job_stage_code(job)
        if code and code not in stage_sel:
            return False

    industry_sel = filters.get("industry") or []
    if industry_sel:
        code = _job_industry_code(job)
        if code and code not in industry_sel:
            return False

    exp_sel = filters.get("experience") or []
    deg_sel = filters.get("degree") or []
    if exp_sel or deg_sel:
        exp_code, deg_code = _job_exp_degree_codes(job)
        if exp_sel and exp_code and exp_code not in exp_sel:
            return False
        if deg_sel and deg_code and deg_code not in deg_sel:
            return False

    salary_sel = filters.get("salary") or []
    if salary_sel:
        code = _job_salary_code(job)
        if code and code not in salary_sel:
            return False

    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# INTER_COMBO_DELAY 现从 advanced_settings 动态读取（默认 20~40s）。


def run_search(params: dict, source, *, pages: int = 3,
               progress=None, stop_event=None,
               artifact_dir=None, sleeper=None,
               skip_combos: set[str] | None = None,
               on_combo_done=None,
               execution_config=None,
               measurement_callback=None,
               close_chrome_on_success: bool = True) -> dict:
    """Execute the multi-search pipeline and return merged, filtered jobs.

    ``source`` is a ``BossCdpSource`` (or compatible) providing ``preflight``
    and ``fetch_list``.  ``progress`` is an optional callable receiving a
    dict snapshot after each step.  ``stop_event`` (threading.Event-like)
    aborts the run when set.

    ``skip_combos``: 可选，已完成的组合键集合（格式 "keyword|city"），
    断点续抓时跳过这些组合不重复抓。
    ``on_combo_done``: 可选持久化回调，收到 ``(combo_key, jobs,
    completed_combos)``。回调失败表示进度无法安全保存，流程立即硬停止。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 ``inter_combo_delay``，不读取 advanced_settings.json。
    未提供时回退到运行时读取（向后兼容）。pages 不属于 execution_config。

    Returns ``{"ok": bool, "jobs": [...], "total_scraped": int,
    "total_matched": int, "combinations": int, "error": str,
    "completed_combos": [...]}``.
    """
    if sleeper is None:
        sleeper = time.sleep

    if execution_config is not None:
        # SPEC011 T006: 使用任务创建时冻结的配置快照，不读 JSON
        _base_delay = float(execution_config.inter_combo_delay)
    else:
        _adv = load_advanced_settings()
        if pages == 3:  # 调用方未显式指定时用用户配置
            pages = int(_adv.get("pages") or 3)
        _base_delay = float(_adv.get("inter_combo_delay") or 30.0)
    _delay_range = (max(5, _base_delay - 5), _base_delay + 5)

    combos = expand_combinations(params)

    def emit(**kw):
        stage = str(kw.get("stage", ""))
        current = int(kw.get("current") or 0)
        total = int(kw.get("total") or 0)
        kw["overall_percent"] = _scrape_overall_percent(stage, current, total)
        # 调用方传了具体 message（如"完成：抓取 X 条"）就优先用；没传才回退默认文案
        if not kw.get("message"):
            kw["message"] = _SCRAPE_STAGE_MESSAGES.get(stage, "")
        if progress is not None:
            try:
                progress(kw)
            except Exception:
                pass

    if not combos:
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 0,
                "error": "没有可执行的搜索组合（关键词或城市为空）"}

    # Auto-launch the debug Chrome if it isn't running, so the user is shown
    # the browser instead of a raw infrastructure error.
    emit(stage="ensure_chrome", message="检查并启动调试浏览器…")
    platform = str(getattr(source, "platform", "boss") or "boss")
    cdp_port = getattr(source, "cdp_port", None)
    chrome_ok, chrome_err = ensure_chrome_ready(cdp_port)
    if not chrome_ok:
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "error": f"调试浏览器未就绪：{chrome_err}。"
                         "若始终无法启动，请手动运行 scripts/boss_cdp_raw.py --setup-chrome 后重试。"}

    # Preflight: CDP connection + BOSS login.
    emit(stage="preflight", message=f"检查 {('BOSS直聘' if platform == 'boss' else '智联招聘')} 登录状态…")
    pre = source.preflight()
    if not pre.ok:
        if pre.failed_code == "source_login_required":
            msg = ("浏览器已打开，但还未登录 BOSS。请在浏览器中登录 zhipin.com，登录后重新继续。" if platform == "boss"
                   else "浏览器已打开，但还未登录智联招聘。请在浏览器中登录 zhaopin.com，登录后重新继续。")
        else:
            msg = f"预检失败：{pre.failed_code}"
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "error": msg}

    merged: dict[str, dict] = {}
    total_scraped = 0
    failed_combos = 0
    completed_combos: list[str] = []
    _skip = skip_combos or set()

    for idx, combo in enumerate(combos):
        if stop_event is not None and stop_event.is_set():
            emit(stage="cancelled", message="运行已取消")
            break

        kw = combo["keyword"]
        city = combo["city"]
        combo_key = f"{kw}|{city}"

        # 断点续抓：跳过已完成的组合
        if combo_key in _skip:
            completed_combos.append(combo_key)
            continue

        emit(stage="searching", current=idx + 1, total=len(combos),
             keyword=kw, city=city,
             message=f"正在搜索 [{idx + 1}/{len(combos)}] {kw} · {city}")

        if platform == "zhilian":
            from webui.platforms import resolve_platform_city
            from webui.source import _zhilian_input_hash
            city_entry = resolve_platform_city("zhilian", city)
            city_snapshot = {
                "name": city_entry.name,
                "label": city_entry.label,
                "platform_code": city_entry.platform_code,
                "mapping_version": city_entry.mapping_version,
            }
            plan_item = {
                "platform": "zhilian",
                "keyword": kw,
                "city": city_snapshot,
                "target_pages": pages,
                "input_hash": _zhilian_input_hash({
                    "platform": "zhilian", "keyword": kw,
                    "city": city_snapshot, "target_pages": pages,
                }),
                "list_output_path": _combo_output_path(artifact_dir, kw, city),
            }
        else:
            plan_item = {
                "keyword": kw,
                "city": city,
                "source_filters": {},  # broad search; multi-select applied as post-filter
                "target_pages": pages,
                "input_hash": _combo_hash(kw, city, pages),
                "list_output_path": _combo_output_path(artifact_dir, kw, city),
            }
        outcome = source.fetch_list(plan_item)
        if not outcome.ok:
            # 系统性阻断（验证码/登录失效/IP风控/CDP不可用）：立即停止，不继续跑其他组合
            if outcome.failed_code in _HARD_STOP_CODES:
                label = _FAILED_CODE_LABELS.get(outcome.failed_code, outcome.failed_code)
                emit(stage="hard_stop", current=idx + 1, total=len(combos),
                     keyword=kw, city=city, failed_code=outcome.failed_code,
                     message=f"系统性阻断：{label}，任务暂停")
                return {"ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": outcome.failed_code,
                        "error": f"系统性阻断：{label}"}
            failed_combos += 1
            # 从 safe_log 提取 reason= 后的可读原因
            _reason = ""
            if outcome.safe_log and "reason=" in outcome.safe_log:
                _reason = outcome.safe_log.split("reason=", 1)[1]
            detail = f"（{_reason}）" if _reason else ""
            label = _FAILED_CODE_LABELS.get(outcome.failed_code, outcome.failed_code)
            emit(stage="combo_failed", current=idx + 1, total=len(combos),
                 keyword=kw, city=city, failed_code=outcome.failed_code,
                 message=f"组合失败：{label}{detail}")
        else:
            total_scraped += len(outcome.jobs)
            completed_combos.append(combo_key)
            for job in outcome.jobs:
                jid = (job.get("platform_job_id") or job.get("job_id") or job.get("source_url") or "")
                if jid and jid not in merged:
                    merged[jid] = job
            if on_combo_done is not None:
                try:
                    on_combo_done(combo_key, list(outcome.jobs), list(completed_combos), outcome=outcome)
                except _PIPELINE_OPERATION_ERRORS as exc:
                    emit(
                        stage="hard_stop", current=idx + 1, total=len(combos),
                        keyword=kw, city=city, failed_code="internal_error",
                        message="组合结果持久化失败，任务暂停",
                    )
                    return {
                        "ok": False,
                        "jobs": list(merged.values()),
                        "total_scraped": total_scraped,
                        "total_matched": len(merged),
                        "combinations": len(combos),
                        "completed_combos": completed_combos,
                        "hard_stop": True,
                        "hard_stop_code": "internal_error",
                        "error": f"组合结果持久化失败（{type(exc).__name__}），任务已暂停",
                    }
            emit(stage="combo_done", current=idx + 1, total=len(combos),
                 keyword=kw, city=city, scraped=len(outcome.jobs),
                 merged=len(merged),
                 message=f"完成 {kw} · {city}：本页 {len(outcome.jobs)} 条，累计去重 {len(merged)} 条")
            # T018: 记录 batch 事件（combo 输入输出数量）
            if measurement_callback is not None:
                try:
                    measurement_callback("batch", "list", 0,
                                         counts={"input_count": pages,
                                                 "output_count": len(outcome.jobs),
                                                 "batch_index": idx + 1})
                except Exception:
                    pass

        # Delay between combinations (not after the last one).
        if idx < len(combos) - 1:
            if stop_event is not None and stop_event.is_set():
                break
            delay = random.uniform(*_delay_range)
            emit(stage="waiting", current=idx + 1, total=len(combos),
                 wait_seconds=int(delay),
                 message=f"防限流等待 {delay:.0f}s 后搜索下一个组合…")
            _t0_wait = time.time()
            sleeper(delay)
            # T018: 记录 wait 事件（防限流冷却时间计入总耗时）
            if measurement_callback is not None:
                try:
                    measurement_callback("wait", "list",
                                         int((time.time() - _t0_wait) * 1000),
                                         counts={"combo_index": idx + 1})
                except Exception:
                    pass

    # 广搜策略：不做本地硬筛选，全量返回，筛选交给后续 AI 步骤。
    all_jobs = list(merged.values())

    # 哨兵第三层：所有非跳过组合全失败 → 大概率 IP 级风控
    ran_combos = len(combos) - len(_skip)
    if failed_combos > 0 and total_scraped == 0 and ran_combos > 0:
        emit(stage="risk_warning",
             message="所有组合均失败，大概率是 IP 级风控限制。建议：打开 Chrome 手动过一次验证码，或等 30 分钟后再试。")
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "completed_combos": completed_combos,
                "error": "所有搜索组合均失败，大概率 IP 级风控。建议手动过一次验证码或等 30 分钟后重试"}

    # 有数据才关浏览器（任务完成）；全失败则保留窗口供用户排查/重试。
    if total_scraped > 0 and close_chrome_on_success:
        emit(stage="closing_chrome", message="正在关闭调试浏览器…")
        close_debug_chrome(cdp_port)
    emit(stage="done", total_scraped=total_scraped, total_matched=len(all_jobs),
         message=f"完成：抓取 {total_scraped} 条，去重 {len(all_jobs)} 条")

    return {"ok": True, "jobs": all_jobs, "total_scraped": total_scraped,
            "total_matched": len(all_jobs), "combinations": len(combos),
            "completed_combos": completed_combos,
            "error": ""}


def fetch_job_details(jobs, source, *, artifact_dir=None, progress=None,
                      stop_event=None, completed_job_ids=None,
                      execution_config=None,
                      measurement_callback=None,
                      emit_terminal_events=True):
    """对一批岗位批量抓 JD（调用方需先确保 Chrome 就绪）。

    Spec 007 ⑧：改用 fetch_details_batch（≤5 一批）走 --enable-parallel 常驻 tab 池，
    替代旧的逐条 fetch_detail。单条失败不中断（该岗位 jd 留空，前端可保留按需加载兜底）。
    ``progress(done, total)`` 按累计完成数回报。

    ``stop_event``: 可选取消信号，每批前检查，命中即停（剩余岗位 jd 留空）。
    ``completed_job_ids``: 可选，已抓过 JD 的 job_id 集合（断点续抓），跳过不重复抓，
    其 jd 保留原值。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 detail_* 字段，不读取 advanced_settings.json。
    未提供时回退到运行时读取（向后兼容）。

    返回 {"jobs": 带 jd 的岗位列表, "hard_stop": bool, "hard_stop_code": str|None,
           "stopped": bool, "fetched": int}：
    - hard_stop=True：批内出现源级硬信号（登录失效/验证码/限流/IP 风控），已停止
      后续批次（继续抓只会抓空气还装完成），调用方应停并向用户上报。
      hard_stop_code 为具体触发的 failed_code（对应 _FAILED_CODE_LABELS）。
    - stopped=True：用户取消导致提前停止。
    - fetched：本次实际抓到 JD 的条数。
    """
    import os
    if artifact_dir is None:
        artifact_dir = os.path.join(os.path.expanduser("~"), ".career-scout", "job-result")
    os.makedirs(artifact_dir, exist_ok=True)
    total = len(jobs)
    if total == 0:
        return {"jobs": [], "hard_stop": False, "hard_stop_code": None,
                "stopped": False, "fetched": 0}
    if execution_config is not None:
        # SPEC011 T006: 使用冻结配置快照，不读 JSON
        BATCH_SIZE = int(execution_config.detail_batch_size)
        _detail_interval = float(execution_config.detail_interval)
        _detail_reset_every = int(execution_config.detail_reset_every)
        _detail_batch_cooldown = float(execution_config.detail_batch_cooldown)
        _detail_tab_pool_size = int(execution_config.detail_tab_pool_size)
    else:
        BATCH_SIZE = int(load_advanced_settings().get("detail_batch_size") or 5)
        _adv = load_advanced_settings()
        _detail_interval = float(_adv.get("detail_interval") or 8)
        _detail_reset_every = int(_adv.get("detail_reset_every") or 3)
        _detail_batch_cooldown = float(_adv.get("detail_batch_cooldown") or 30)
        _detail_tab_pool_size = int(_adv.get("detail_tab_pool_size") or 5)
    done_ids = {str(x) for x in completed_job_ids} if completed_job_ids else set()
    # 预先为每个 job 计算稳定 job_id（与 fetch_details_batch 内部 key 一致），
    # 缺 job_id 的 job 填充 idx{idx} 兜底，确保 batch 返回的 outcome 能映射回原 job。
    indexed_jobs = []
    for idx, job in enumerate(jobs):
        if not isinstance(job, dict):
            indexed_jobs.append((idx, f"idx{idx}", {}))
            continue
        jid = str(job.get("platform_job_id") or job.get("job_id") or job.get("id") or "").strip()
        if not jid:
            jid = f"idx{idx}"
        indexed_jobs.append((idx, jid, dict(job)))
    jd_by_idx = {}
    jd_fail_by_idx: dict[int, str] = {}
    jd_fail_reason_by_idx: dict[int, str] = {}
    done = 0
    fetched = 0
    hard_stop = False
    hard_stop_code: str | None = None
    stopped = False
    # 源级硬信号集合：命中任何一个都意味着继续抓只会抓空气，必须截停并上报用户。
    # JD 抓取阶段只关心 source_* 码（不调 AI，不会产生 ai_* 码）。
    _jd_hard_stop_codes = frozenset({
        "captcha_required",
        "source_login_required",
        "source_verification_required",
        "source_rate_limited",
        "source_blocked",
        "source_cdp_unavailable",
    })
    for batch_start in range(0, len(indexed_jobs), BATCH_SIZE):
        if stop_event is not None and stop_event.is_set():
            stopped = True
            break
        # 批次间冷却：防 BOSS session 级反爬（code:37），首批不等
        if batch_start > 0:
            cooldown = _detail_batch_cooldown + random.uniform(-5, 5)
            print(f"[fetch_jd] 批次间冷却 {cooldown:.0f}s（防 code:37）...")
            _t0_cooldown = time.time()
            time.sleep(max(cooldown, 5))
            # T018: 记录 wait 事件（冷却时间计入总耗时）
            if measurement_callback is not None and emit_terminal_events:
                try:
                    measurement_callback("wait", "detail",
                                         int((time.time() - _t0_cooldown) * 1000),
                                         counts={"batch_index": batch_start // BATCH_SIZE})
                except Exception:
                    pass
        batch = indexed_jobs[batch_start:batch_start + BATCH_SIZE]
        batch_jobs = [job for _, _, job in batch]
        batch_path = os.path.join(
            artifact_dir, f"pipeline_batch_{batch_start}_{time.time_ns()}.json"
        )
        batch_exception_code: str | None = None
        _t0_batch = time.time()

        # 批内条级进度：智联串行逐条抓取时由 source 逐条回调（on_item_done），
        # 否则一批 15 条要十几分钟，前端进度条一直停在 0。BOSS 子进程模式
        # 在批返回时一次性回调（幂等），不改变原有批量语义。
        batch_done_before = done

        def _item_progress(n: int, _base: int = batch_done_before, _total: int = total) -> None:
            if progress is None:
                return
            try:
                progress(min(_base + n, _total), _total)
            except Exception:
                pass

        try:
            outcomes = source.fetch_details_batch(
                batch_jobs,
                detail_output_path=batch_path,
                max_batch_size=BATCH_SIZE,
                gap_min=_detail_interval,
                gap_max=_detail_interval + 7,
                reset_every=_detail_reset_every,
                tab_pool_size=_detail_tab_pool_size,
                on_item_done=_item_progress,
            )
        except _PIPELINE_OPERATION_ERRORS as exc:
            # 批调用本身抛错时没有逐岗位 outcome 可供后续分类；这属于源/编排
            # 级故障，不能伪装成一批空结果继续推进。
            batch_exception_code = _classify_detail_batch_exception(exc)
            hard_stop = True
            hard_stop_code = batch_exception_code
            outcomes = {}
        # T018: 记录 request 事件（批次请求时长）
        if measurement_callback is not None:
            try:
                measurement_callback("request", "detail",
                                     int((time.time() - _t0_batch) * 1000),
                                     counts={"batch_size": len(batch)},
                                     error_code=batch_exception_code)
            except Exception:
                pass
        for idx, jid, _ in batch:
            outcome = outcomes.get(jid)
            jd = ""
            if outcome is not None and outcome.ok and isinstance(outcome.detail, dict):
                jd = str(outcome.detail.get("jd", "")).strip()
            elif outcome is not None and outcome.failed_code in _jd_hard_stop_codes:
                # 源级硬信号：停后续批次并上报（别继续抓空气还装完成）
                hard_stop = True
                hard_stop_code = outcome.failed_code
            if not jd and batch_exception_code:
                jd_fail_by_idx[idx] = batch_exception_code
                jd_fail_reason_by_idx[idx] = ERROR_TAXONOMY.get(
                    batch_exception_code, {}
                ).get(
                    "reason",
                    _FAILED_CODE_LABELS.get(batch_exception_code, "抓取失败"),
                )
            elif not jd and outcome is not None and outcome.failed_code:
                jd_fail_by_idx[idx] = outcome.failed_code
                jd_fail_reason_by_idx[idx] = (
                    outcome.failed_reason
                    or _FAILED_CODE_LABELS.get(outcome.failed_code, "岗位详情抓取失败")
                )
            jd_by_idx[idx] = jd
            if jd:
                fetched += 1
            # T018: 记录 item_terminal 事件（SC-007 终态守恒）
            if measurement_callback is not None:
                try:
                    _status = "success" if jd else "failed"
                    measurement_callback("item_terminal", "detail", 0,
                                         counts={"item_index": idx, "status": _status,
                                                 "input_count": total})
                except Exception:
                    pass
            done += 1
            if progress is not None:
                try:
                    progress(done, total)
                except Exception:
                    pass
        # T018: 记录 batch 事件
        if measurement_callback is not None:
            try:
                _batch_fetched = sum(1 for _, jid, _ in batch if outcomes.get(jid) and outcomes[jid].ok)
                measurement_callback("batch", "detail", 0,
                                     counts={"input_count": len(batch),
                                             "output_count": _batch_fetched,
                                             "batch_index": batch_start // BATCH_SIZE})
            except Exception:
                pass
        if hard_stop:
            break
    enriched = []
    for idx, job in enumerate(jobs):
        e = dict(job) if isinstance(job, dict) else {}
        jid = str(e.get("platform_job_id") or e.get("job_id") or e.get("id") or "")
        if jid and jid in done_ids and str(e.get("jd", "")).strip():
            # 断点续抓：已抓过的岗位保留原 JD，不重复抓也不覆盖
            enriched.append(e)
            continue
        e["jd"] = jd_by_idx.get(idx, "")
        if not e["jd"] and idx in jd_fail_by_idx:
            e["jd_failed_code"] = jd_fail_by_idx[idx]
            e["jd_failed_reason"] = jd_fail_reason_by_idx[idx]
        enriched.append(e)
    return {"jobs": enriched, "hard_stop": hard_stop,
            "hard_stop_code": hard_stop_code,
            "stopped": stopped, "fetched": fetched}


def _combo_hash(keyword: str, city: str, pages: int) -> str:
    import hashlib
    import json
    blob = json.dumps({"keyword": keyword, "city": city, "target_pages": pages,
                       "source_filters": {}}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _combo_output_path(artifact_dir, keyword: str, city: str) -> str:
    import os
    import re as _re
    if artifact_dir is None:
        base = os.path.join(os.path.expanduser("~"), ".career-scout", "job-result")
    else:
        base = str(artifact_dir)
    os.makedirs(base, exist_ok=True)
    safe_kw = _re.sub(r"[^\w\u4e00-\u9fff]", "", keyword)[:20] or "kw"
    safe_city = _re.sub(r"[^\w\u4e00-\u9fff]", "", city)[:10] or "city"
    return os.path.join(base, f"pipeline_{safe_kw}_{safe_city}_{time.time_ns()}.json")


def get_frozen_artifact_manifest(
    artifact_manifest: dict | None, stage: str,
) -> dict | None:
    """从工作负载的产物清单中返回指定阶段的冻结产物引用（T026）。

    支持分阶段复用规则（research.md Decision 7）：
    - stage="list" → 返回 list 阶段产物（供 detail/rough 复用）
    - stage="detail" → 返回 detail 阶段产物（供 fine 复用 JD）
    - stage="end_to_end" → 始终返回 None（端到端不复用中间结果）

    artifact_manifest 格式示例::

        {"stages": {"list": {"path": "...", "digest": "..."},
                     "detail": {"path": "...", "digest": "..."}}}

    返回 ``None`` 表示该阶段无可用冻结产物。
    """
    if not artifact_manifest or not isinstance(artifact_manifest, dict):
        return None
    if stage == "end_to_end":
        # FR-025: end_to_end 不复用中间结果
        return None
    stages = artifact_manifest.get("stages", {})
    if not isinstance(stages, dict):
        return None
    return stages.get(stage)


class TuningStageError(RuntimeError):
    """A stage failure with a safe, controller-visible error code."""

    def __init__(self, error_code: str, message: str):
        self.error_code = str(error_code)
        super().__init__(message)


class TuningRoundRunner:
    """用冻结 manifest 机械分派五种真实阶段，不作候选或参数决策。"""

    ROUND_KINDS = frozenset({"list", "detail", "rough", "fine", "end_to_end"})

    def __init__(self, *, workspace_root, source_factory, ai_settings_provider):
        self.workspace_root = Path(workspace_root).resolve()
        self.source_factory = source_factory
        self.ai_settings_provider = ai_settings_provider

    def _read_artifact(
        self, manifest: dict, *, path_field: str, digest_field: str,
        required: bool,
    ) -> dict:
        frozen = manifest.get("frozen_input", {})
        path = frozen.get(path_field)
        if not path:
            if required:
                raise ValueError(f"轮次缺少冻结输入 {path_field}")
            return {}
        absolute = (self.workspace_root / str(path)).resolve()
        experiment_root = (
            self.workspace_root / "tuning" / manifest["experiment_id"]
        ).resolve()
        if experiment_root not in absolute.parents:
            raise ValueError("冻结输入产物越过实验根目录")
        if not absolute.is_file():
            if required:
                raise ValueError("冻结输入产物不存在")
            return {}
        try:
            artifact_bytes = absolute.read_bytes()
            payload = json.loads(artifact_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("冻结输入产物不可读") from exc
        expected_digest = frozen.get(digest_field)
        if not isinstance(expected_digest, str) or not expected_digest:
            raise ValueError(f"轮次缺少冻结输入 {digest_field}")
        actual_digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError("冻结输入产物摘要不匹配")
        if not isinstance(payload, dict):
            raise ValueError("冻结输入产物必须是 JSON 对象")
        return payload

    def _ai_settings(self) -> tuple[str, str, str]:
        settings = self.ai_settings_provider()
        endpoint = str(settings.get("endpoint_url") or "")
        api_key = str(settings.get("api_key") or "")
        model = str(settings.get("model") or "")
        if not endpoint or not api_key:
            raise ValueError("AI 阶段缺少已配置的端点或凭据")
        return endpoint, api_key, model

    @staticmethod
    def _retry_limits_from_manifest(manifest: dict):
        """Build the immutable AI transport retry budget authorized by a manifest."""
        policy = manifest.get("retry_policy") or {}
        recoverable_codes = policy.get("recoverable_codes") or []
        try:
            max_retries = max(0, int(policy.get("max_retries", 0)))
        except (TypeError, ValueError):
            max_retries = 0
        return MappingProxyType({
            str(code): max_retries
            for code in recoverable_codes
            if isinstance(code, str) and code
        })

    def execute(self, manifest: dict, *, measurement_callback=None) -> dict:
        from webui.ai import match_jds, screen_jobs
        from webui.execution_config import ExecutionConfigSnapshot

        kind = manifest.get("round_kind")
        if kind not in self.ROUND_KINDS:
            raise ValueError(f"未知轮次类型: {kind}")
        config = ExecutionConfigSnapshot.from_dict(manifest["execution_config"])
        fixed = manifest["fixed_fields"]
        params = {
            "keyword": ",".join(fixed["keywords"]),
            "city": (["全国"] if fixed["scope_kind"] == "nationwide"
                     else list(fixed["cities"])),
            "pages": fixed["pages_per_combination"], "filters": {},
        }
        artifact_dir = (
            self.workspace_root / "tuning" / manifest["experiment_id"]
            / "artifacts" / manifest["round_id"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        base_context = self._read_artifact(
            manifest, path_field="artifact_manifest_path",
            digest_field="artifact_digest", required=True,
        )
        source_context = self._read_artifact(
            manifest, path_field="source_artifact_path",
            digest_field="source_artifact_digest",
            required=kind in {"detail", "rough", "fine"},
        )
        quality_context = base_context.get("quality_context")
        if not isinstance(quality_context, dict):
            raise ValueError("冻结输入产物缺少 quality_context")
        source = (
            self.source_factory(
                artifact_root=artifact_dir,
                platform=manifest.get("fixed_fields", {}).get("platform"),
            )
            if kind in {"list", "detail", "end_to_end"}
            else None
        )
        if kind in {"list", "end_to_end"}:
            listed = run_search(
                params, source, pages=fixed["pages_per_combination"],
                artifact_dir=str(artifact_dir), execution_config=config,
                measurement_callback=measurement_callback,
                close_chrome_on_success=(kind == "list"),
            )
            if not listed.get("ok"):
                raise TuningStageError(
                    listed.get("hard_stop_code") or "list_stage_failed",
                    listed.get("error") or "list 阶段失败",
                )
            jobs = listed["jobs"]
            if kind == "list":
                return {"round_kind": kind, "jobs": jobs, "list_result": listed}
        else:
            jobs = source_context.get("jobs")
            if not isinstance(jobs, list):
                raise ValueError("阶段输入产物缺少 jobs 列表")
        for index, job in enumerate(jobs):
            if isinstance(job, dict):
                job.setdefault("_tuning_measurement_index", index)
        base_input_count = len(jobs)
        if kind in {"detail", "end_to_end"}:
            detailed = fetch_job_details(
                jobs, source, artifact_dir=str(artifact_dir),
                execution_config=config, measurement_callback=measurement_callback,
                emit_terminal_events=(kind == "detail"),
            )
            if detailed.get("hard_stop"):
                raise TuningStageError(
                    detailed.get("hard_stop_code") or "detail_stage_failed",
                    detailed.get("hard_stop_code") or "detail 阶段硬阻断",
                )
            jobs = detailed["jobs"]
            if kind == "detail":
                return {"round_kind": kind, **detailed}
            close_debug_chrome()
        if kind in {"rough", "end_to_end"}:
            criteria = quality_context.get("screening_fields")
            if not isinstance(criteria, dict):
                raise ValueError("AI 粗筛缺少冻结 criteria")
            endpoint, api_key, model = self._ai_settings()
            retry_limits = self._retry_limits_from_manifest(manifest)
            rough = screen_jobs(
                jobs, criteria, endpoint, api_key, model=model,
                raise_on_systemic=True, execution_config=config,
                measurement_callback=measurement_callback,
                emit_kept_terminal=(kind == "rough"),
                measurement_input_count=base_input_count,
                retry_limits=retry_limits,
            )
            kept = set(rough["kept"])
            jobs = [job for job in jobs if str(job.get("job_id", "")) in kept]
            if kind == "rough":
                return {"round_kind": kind, **rough}
        if kind in {"fine", "end_to_end"}:
            profile_summary = quality_context.get("profile_summary")
            if not isinstance(profile_summary, str) or not profile_summary.strip():
                raise ValueError("AI 精筛缺少冻结 profile_summary")
            endpoint, api_key, model = self._ai_settings()
            retry_policy = manifest.get("retry_policy") or {}
            recoverable_codes = set(retry_policy.get("recoverable_codes") or [])
            missing_retry_budget = (
                int(retry_policy.get("max_retries", 0))
                if "ai_missing_job" in recoverable_codes else 0
            )
            fine = match_jds(
                jobs, profile_summary, endpoint, api_key, model=model,
                raise_on_systemic=True, execution_config=config,
                measurement_callback=measurement_callback,
                measurement_input_count=base_input_count,
                missing_result_retry_budget=missing_retry_budget,
                retry_limits=self._retry_limits_from_manifest(manifest),
            )
            return {"round_kind": kind, "jobs": jobs, **fine}
        raise ValueError(f"轮次 {kind} 未产生结果")
