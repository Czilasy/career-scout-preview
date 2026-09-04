"""浏览器账号簿与 CDP 数据目录（021 B7 自 pipeline_exec.py 搬运）。"""

from __future__ import annotations

from webui.logging_setup import get_logger
from webui.pipeline_exec_settings import _ADVANCED_SETTINGS_DIR

import json
import os
import sys
import threading
import uuid
from pathlib import Path


_logger = get_logger(__name__)


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



# Spec 038 B091：默认每轮配额取范围中值（FR-004/005）。
DEFAULT_R1_QUOTA = 25
DEFAULT_R2_QUOTA = 150
R1_QUOTA_MIN, R1_QUOTA_MAX = 1, 50
R2_QUOTA_MIN, R2_QUOTA_MAX = 1, 300


def _clamp_quota(value: object, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _normalize_pool(raw: object, default_order: int = 0) -> dict:
    """把 pool 字段归一为 {selected, order, r1_quota, r2_quota}。

    Spec 038 FR-015/016/017/018：缺字段一律补默认——默认全进池、默认全选、
    默认配额取范围中值（R1 25 / R2 150）。``order`` 缺省时落
    ``default_order``（账号簿入盘顺序，保证勾选顺序稳定）。
    """
    if not isinstance(raw, dict):
        raw = {}
    selected = raw.get("selected")
    try:
        order = int(raw.get("order") if raw.get("order") is not None else default_order)
    except (TypeError, ValueError):
        order = default_order
    return {
        "selected": bool(selected) if selected is not None else True,
        "order": max(0, order),
        "r1_quota": _clamp_quota(raw.get("r1_quota"), R1_QUOTA_MIN, R1_QUOTA_MAX, DEFAULT_R1_QUOTA),
        "r2_quota": _clamp_quota(raw.get("r2_quota"), R2_QUOTA_MIN, R2_QUOTA_MAX, DEFAULT_R2_QUOTA),
    }


def _normalize_rate_limited(raw: object) -> bool:
    if raw is None:
        return False
    return bool(raw)


def parse_bool(value: object) -> bool | None:
    """Parse API boolean values without treating the string ``"false"`` as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return None


def _default_browser_accounts() -> dict[str, dict[str, object]]:
    """内置默认账号簿——每账号默认进池、默认全选、默认配额取中值（FR-015）。"""
    out: dict[str, dict[str, object]] = {}
    for order, (aid, item) in enumerate(BROWSER_ACCOUNTS.items()):
        out[str(aid)] = {
            "id": str(aid), "name": str(item["name"]), "profile_dir": str(item["profile_dir"]),
            "builtin": aid == "a",
            "pool": _normalize_pool(None, order),
            "rate_limited": False,
        }
    return out



def load_browser_accounts(path: str | os.PathLike[str] | None = None) -> dict[str, dict[str, object]]:
    """Load browser accounts; the accounts file, when present, is authoritative.

    Spec 038 (FR-021)：旧 ``roles`` 字段全删不兼容；新 schema 含 ``pool`` 与
    ``rate_limited``，缺字段补默认（FR-015：默认全进池、默认全选、默认配额）。
    """
    accounts_path = Path(path) if path is not None else browser_accounts_path()
    accounts: dict[str, dict[str, object]] = {}
    try:
        if accounts_path.is_file():
            with open(accounts_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for order, (aid, item) in enumerate(saved.items()):
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
                        # Spec 038 B091：账号池配置 + 限流标记（旧 roles 字段全删，FR-021）。
                        "pool": _normalize_pool(item.get("pool"), order),
                        "rate_limited": _normalize_rate_limited(item.get("rate_limited")),
                    }
            accounts.setdefault("a", _default_browser_accounts()["a"])
        else:
            accounts = _default_browser_accounts()
    except (OSError, json.JSONDecodeError, TypeError):
        accounts = _default_browser_accounts()
    return accounts



def save_browser_accounts(accounts: dict, path: str | os.PathLike[str] | None = None) -> None:
    """Atomically persist browser accounts (Spec 038: pool + rate_limited)."""
    accounts_path = Path(path) if path is not None else browser_accounts_path()
    accounts_path.parent.mkdir(parents=True, exist_ok=True)
    clean = {}
    for order, (aid, item) in enumerate(accounts.items()):
        pool = _normalize_pool(item.get("pool"), order)
        clean[str(aid)] = {
            "id": str(aid),
            "name": str(item.get("name") or "").strip(),
            "profile_dir": os.path.abspath(os.path.expanduser(str(item.get("profile_dir") or ""))),
            "builtin": bool(item.get("builtin", False)),
            "pool": pool,
            "rate_limited": _normalize_rate_limited(item.get("rate_limited")),
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
    """Add a user-defined browser account and return it.

    Spec 038 FR-018：新增账号自动加入 R1/R2 池、默认全选、默认配额取范围中值。
    """
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
        # 新增账号默认进池、默认全选、配额取中值，order 排到现有账号末尾。
        account = {
            "id": account_id, "name": name,
            "profile_dir": normalized_dir, "builtin": False,
            "pool": _normalize_pool(None, len(accounts)),
            "rate_limited": False,
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



_ROLE_VALUES = ("R1", "R2")  # 保留以兼容旧调用方校验（FR-021：角色 schema 已弃用）


def resolve_account_for_role(accounts: dict, role: str) -> str | None:
    """Spec 038 (FR-021)：返回账号池中第一个 ``pool.selected=True`` 的账号 ID。

    ``role`` 参数保留仅为兼容旧调用方（pipeline_jobs_api.py /
    exec_search_api.py / ai_screen_api.py / resume_identity.py 仍按 R1/R2
    传入），实际语义被忽略——R1/R2 共用同一账号池，不再做角色互斥。
    没有选中账号时返回 None。
    """
    _ = str(role or "").strip()  # 接受但不再使用
    selected: list[tuple[int, str]] = []
    for fallback_order, (aid, item) in enumerate(accounts.items()):
        pool = item.get("pool")
        if not isinstance(pool, dict):
            pool = _normalize_pool(pool, fallback_order)
        if pool.get("selected", True):
            try:
                order = int(pool.get("order", fallback_order))
            except (TypeError, ValueError):
                order = fallback_order
            selected.append((max(0, order), str(aid)))
    if not selected:
        return None
    selected.sort()
    return selected[0][1]


def has_selected_account(
        accounts_path: str | os.PathLike[str] | None = None) -> bool:
    """Return whether the account pool has at least one selected account."""
    return resolve_account_for_role(
        load_browser_accounts(accounts_path), ""
    ) is not None


def assign_account_role(
        accounts: dict, role: str, account_id_or_none: str | None) -> dict:
    """Spec 038 (FR-021)：角色→账号一对一 schema 已弃用，本函数为兼容 stub。

    旧调用方（settings_api.py 的角色端点）传入后返回 accounts 的浅拷贝不变，
    保留所有字段——pool 配置走专用端点（settings_api.py 的 pool 端点），
    此处仅保证旧调用链不爆错。``role`` 与 ``account_id_or_none`` 校验保留
    以维持 API 契约。
    """
    role = str(role or "").strip()
    if role not in _ROLE_VALUES:
        raise ValueError("角色必须是 R1 或 R2")
    # 不修改任何字段；返回浅拷贝避免外部 mutate 影响入参。
    return {str(aid): dict(item) for aid, item in accounts.items()}



def account_for_role(
        role: str,
        accounts_path: str | os.PathLike[str] | None = None,
        run: dict | None = None,
        fallback: str = "a") -> str:
    """Resolve the browser account for a BOSS task stage role (B073).

    Priority:
    1. run 冻结值优先——续跑一律沿用任务创建时冻结的 browser_account，
       不重新按角色解析（冻结需求：运行中改角色不影响当前任务）；
    2. 池解析——账号簿中第一个 ``pool.selected=True`` 的账号（FR-021：R1/R2 共用池，
       ``role`` 仅作兼容签名，不再有互斥语义）；
    3. 登录态检测——fresh 缓存为 not_logged_in/restricted 视为不可用，跳过；
    4. 池未选账号或账号不可用 → fallback（调用方传当前账号），不报错不阻断。
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


# ---------------------------------------------------------------------------
# Spec 038 B091：账号池配置与限流持久化（账号簿权威域）
# ---------------------------------------------------------------------------

def update_account_pool(
        accounts: dict, account_id: str, *,
        selected: bool | None = None,
        order: int | None = None,
        r1_quota: int | None = None,
        r2_quota: int | None = None) -> dict:
    """部分更新某账号的 pool 配置；返回新 accounts dict 不修改入参。

    - 目标账号不存在抛 ``KeyError``；
    - 任何字段未传（None）保持原值；
    - ``r1_quota``/``r2_quota`` 越界按 clamp 处理（落到 [min, max]，不抛错）；
    - 全字段都没传时返回 accounts 浅拷贝（pool 不变）。
    """
    target = accounts.get(str(account_id))
    if not isinstance(target, dict):
        raise KeyError(account_id)
    cur_pool = _normalize_pool(target.get("pool"), 0)
    if selected is not None:
        cur_pool["selected"] = bool(selected)
    if order is not None:
        try:
            cur_pool["order"] = max(0, int(order))
        except (TypeError, ValueError):
            pass
    if r1_quota is not None:
        cur_pool["r1_quota"] = _clamp_quota(r1_quota, R1_QUOTA_MIN, R1_QUOTA_MAX, DEFAULT_R1_QUOTA)
    if r2_quota is not None:
        cur_pool["r2_quota"] = _clamp_quota(r2_quota, R2_QUOTA_MIN, R2_QUOTA_MAX, DEFAULT_R2_QUOTA)
    updated = {str(aid): dict(item) for aid, item in accounts.items()}
    updated[str(account_id)] = {**target, "pool": dict(cur_pool)}
    return updated


def set_account_rate_limited(
        account_id: str, *, rate_limited: bool,
        path: str | os.PathLike[str] | None = None) -> None:
    """设置账号的撞墙限流标记（持久化，best-effort）。

    Spec 038 FR-014：撞墙时写 True；成功使用后清零（自愈）。
    账号不存在或写盘失败时仅记日志（不阻断运行流）。
    """
    aid = str(account_id or "").strip()
    if not aid:
        return
    try:
        # 与新增/删除/池设置共用同一把进程锁，避免撞墙标记的读改写
        # 覆盖用户刚保存的勾选顺序或配额。
        with _BROWSER_ACCOUNTS_LOCK:
            accounts = load_browser_accounts(path)
            item = accounts.get(aid)
            if not isinstance(item, dict):
                return
            if bool(item.get("rate_limited")) == bool(rate_limited):
                return  # 状态未变，避免无谓写盘
            item["rate_limited"] = bool(rate_limited)
            save_browser_accounts(accounts, path)
    except Exception:
        # best-effort：撞墙/自愈持久化失败不影响主流程
        _logger.debug(
            "set_account_rate_limited(%s, %s) 写盘失败（best-effort 忽略）",
            aid, rate_limited, exc_info=True,
        )
