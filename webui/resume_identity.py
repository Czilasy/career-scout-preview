"""Frozen identity resolution for resumed pipeline tasks.

Continue endpoints must restore the exact platform, browser account, CDP
port and profile captured when the task was frozen. Missing zhilian
identity is a hard block; no implicit BOSS fallback is allowed.

030：本模块扩展为续跑身份域——账号快照、自动换号双门槛判定、换号留痕、
缺冻结账号的角色感知兜底，均落位于此；路由层只做接线。
"""

from __future__ import annotations

from typing import Any

from webui.logging_setup import get_logger

_logger = get_logger(__name__)


# 030：任务创建时的全局当前账号快照键（execution_params 内字段，无表结构
# 变更）。续跑时比对"当前全局账号 ≠ 快照"判定用户是否主动换过号；快照
# 缺失（存量任务）一律不自动换号，沿用冻结身份。
ACTIVE_ACCOUNT_AT_FREEZE_KEY = "active_account_at_freeze"

# 030：AI 类暂停码——阻断在 AI 接口侧，换浏览器账号无意义，自动换号排除。
# 与 webui/app_support.py _check_resume_block 的集合同口径；来源
# webui/error_registry.py 的 "ai" 类目。新增 AI 类错误码时需同步此处。
AI_PAUSE_CODES = frozenset({
    "ai_rate_limited",
    "ai_quota_exhausted",
    "ai_key_invalid",
    "ai_network_error",
})


def freeze_active_account_snapshot(params: dict[str, Any],
                                   current_account: str) -> dict[str, Any]:
    """任务创建点把当时全局当前账号写入执行参数（030 FR-001）。原地更新并返回。"""
    params[ACTIVE_ACCOUNT_AT_FREEZE_KEY] = str(current_account or "")
    return params


def account_display_name(account_id: str,
                         accounts: dict[str, Any] | None = None) -> str:
    """账号展示名：账号簿 name 优先，缺失回退账号 id（030 FR-005）。"""
    account_id = str(account_id or "")
    if accounts is None:
        from webui.pipeline_exec_accounts import load_browser_accounts
        accounts = load_browser_accounts()
    name = str((accounts.get(account_id) or {}).get("name") or "").strip()
    return name or account_id


def decide_auto_account_switch(
        run: dict[str, Any] | None, *,
        current_active_account: str) -> tuple[bool, str, str]:
    """统一继续接口自动换号双门槛判定（030 FR-002/FR-003）。

    仅用于"未显式指定 target_account"的续跑路径；显式指定由调用方先行
    处理，语义不变（FR-004）。返回 ``(是否换号, 原冻结账号, 目标账号)``。

    全部条件满足才换：
    1. 当前全局账号非空且 ≠ 创建时快照——用户暂停期间主动换过全局账号；
    2. 暂停码非 AI 类——AI 阻断换浏览器账号无意义；
    3. 目标 ≠ 冻结账号——相同则无事可做。
    快照缺失（存量任务）一律不换，沿用冻结身份。
    """
    params = (run or {}).get("execution_params") or {}
    if not isinstance(params, dict):
        return False, "", ""
    frozen = str(params.get("browser_account") or "")
    snapshot = str(params.get(ACTIVE_ACCOUNT_AT_FREEZE_KEY) or "")
    current = str(current_active_account or "")
    if not current or not snapshot or current == snapshot:
        return False, "", ""
    if str((run or {}).get("error_code") or "") in AI_PAUSE_CODES:
        return False, "", ""
    if current == frozen:
        return False, "", ""
    return True, frozen, current


def record_account_switch_event(store: Any, run_id: str, *,
                                from_account: str, to_account: str,
                                accounts: dict[str, Any] | None = None) -> None:
    """换号留痕：写 ``account_switch`` 任务事件（030 FR-005）。尽力而为，
    不因事件失败回滚已发生的身份改写。"""
    payload = {
        "from_account": str(from_account or ""),
        "to_account": str(to_account or ""),
        "from_name": account_display_name(from_account, accounts),
        "to_name": account_display_name(to_account, accounts),
    }
    try:
        store.append_task_event(run_id, "account_switch", payload)
    except Exception:
        _logger.debug("账号切换事件落库失败（不影响切换主流程）", exc_info=True)


def append_account_switch_log_line(task: dict[str, Any] | None, *,
                                   from_account: str, to_account: str,
                                   accounts: dict[str, Any] | None = None) -> None:
    """换号留痕：内存任务进度日志追加一行中文说明（030 FR-005），
    前端进度界面经既有 logs 渲染直接可见。"""
    if task is None:
        return
    from_name = account_display_name(from_account, accounts)
    to_name = account_display_name(to_account, accounts)
    task.setdefault("logs", []).append(
        f"本次从账号「{from_name}」切换到账号「{to_name}」继续")


def ensure_frozen_browser_account(store: Any, run_id: str,
                                  run: dict[str, Any] | None, *, platform: str,
                                  fallback_account: str,
                                  accounts_path: Any = None,
                                  role: str = "") -> str:
    """缺冻结账号时按口径解析并写回执行参数（030 FR-007；抓取续跑的
    全局回退为 030 前既有行为）。已有冻结账号原样返回且不写库。

    缺账号时：``role`` 非空且平台为 BOSS → 按角色解析（R2 详情口径，
    沿用 ``account_for_role`` 的登录态过滤与回退链）；其余（含智联）→
    回退调用方给定的当前账号。
    """
    params = dict((run or {}).get("execution_params") or {})
    frozen = str(params.get("browser_account") or "")
    if frozen:
        return frozen
    fallback = str(fallback_account or "") or "a"
    resolved = ""
    if role and str(platform or "") == "boss":
        from webui.pipeline_exec_accounts import account_for_role
        resolved = account_for_role(role, accounts_path, run=run,
                                    fallback=fallback)
    else:
        resolved = fallback
    if resolved:
        params["browser_account"] = resolved
        store.update_screening_execution_params(run_id, params)
    return resolved


def inherit_parent_frozen_identity(store: Any, source_run_id: str,
                                   operational_errors: tuple) -> dict[str, Any]:
    """单岗位 JD 抓取从来源 run 继承冻结平台/浏览器身份（T407/T417，
    030 自 pipeline_jobs_api 原样搬入，行为不变）。查询失败返回默认空
    身份，语义与原路由的 except 分支一致。"""
    identity: dict[str, Any] = {
        "platform": "boss",
        "browser_account": None,
        "cdp_port": None,
        "profile_key": None,
        "parent_run": None,
    }
    if not source_run_id:
        return identity
    try:
        checkpoint = store.get_run_checkpoint_identity(source_run_id)
        parent_run = store.get_screening_run(source_run_id)
    except operational_errors:
        return identity
    if checkpoint is not None:
        identity["platform"] = str(checkpoint.get("platform") or "boss")
    parent_params = (parent_run or {}).get("execution_params") or {}
    identity["browser_account"] = str(parent_params.get("browser_account") or "") or None
    identity["cdp_port"] = parent_params.get("cdp_port")
    identity["profile_key"] = parent_params.get("profile_key")
    gp_task_id = str(parent_params.get("scrape_task_id") or "")
    if (not identity["cdp_port"] or not identity["profile_key"]) and gp_task_id:
        try:
            grandparent = store.get_screening_run(gp_task_id)
        except operational_errors:
            grandparent = None
        gp_params = (grandparent or {}).get("execution_params") or {}
        identity["cdp_port"] = identity["cdp_port"] or gp_params.get("cdp_port")
        identity["profile_key"] = identity["profile_key"] or gp_params.get("profile_key")
    identity["parent_run"] = parent_run
    return identity


def apply_continue_account_switch(store: Any, run: dict[str, Any], *,
                                  run_id: str, target_account: str,
                                  auto_switch: tuple[bool, str, str] | None,
                                  accounts_path: Any,
                                  check_resume_block: Any) -> dict[str, Any]:
    """继续接口换号的应用与校验（030 自 task_continue_api 原样收口）。

    校验目标账号存在性与浏览器身份、对候选身份做阻断检查、持久化冻结
    身份；自动换号时补写 account_switch 事件。行为与原路由逐分支一致。

    返回 ``{"status": "ok", "run": <刷新后的 run>}``，或
    ``{"status": <错误码>, "http_status": int, "body": <响应体>}``。
    """
    from webui.pipeline_exec import load_browser_accounts, resolve_browser_account
    from webui.platforms import resolve_login_space

    accounts = load_browser_accounts(accounts_path)
    if target_account not in accounts:
        return {"status": "target_account_not_found", "http_status": 404, "body": {
            "ok": False, "error": "target_account_not_found",
            "message": "目标账号不存在，请刷新账号列表后重试",
            "status": "paused",
        }}
    platform = str(run.get("platform")
                   or (run.get("execution_params") or {}).get("platform")
                   or "boss")
    target_dir = resolve_browser_account(
        target_account, accounts_path) or "unresolved"
    try:
        _login_space = resolve_login_space(
            platform, target_account, boss_profile_dir=target_dir)
    except ValueError:
        return {"status": "target_account_invalid", "http_status": 409, "body": {
            "ok": False, "error": "target_account_invalid",
            "message": "目标账号浏览器身份不可用，请确认该账号已配置",
            "status": "paused",
        }}
    candidate = {
        "platform": platform,
        "browser_account": target_account,
        "cdp_port": _login_space.cdp_port,
        "profile_key": _login_space.profile_key,
    }
    candidate_params = dict(run.get("execution_params") or {})
    candidate_params.update(
        {k: v for k, v in candidate.items() if v not in (None, "")})
    candidate_run = dict(run)
    candidate_run["execution_params"] = candidate_params
    candidate_run["platform"] = platform
    passed, code, reason = check_resume_block(candidate_run)
    if not passed:
        return {"status": "block_not_resolved", "http_status": 409, "body": {
            "ok": False, "error": "block_not_resolved",
            "error_code": code, "error_reason": reason,
            "target_account": target_account,
            "status": "paused",
            "message": (
                f"目标账号「{accounts[target_account].get('name') or target_account}」"
                f"暂不可用：{reason}"
            ),
        }}
    persist_frozen_identity(store, run_id, candidate)
    if auto_switch is not None and auto_switch[0]:
        record_account_switch_event(
            store, run_id,
            from_account=auto_switch[1], to_account=auto_switch[2])
    return {"status": "ok", "run": store.get_screening_run(run_id) or run}


def resolve_frozen_identity(store, run: dict[str, Any]) -> dict[str, Any]:
    """Resolve frozen identity from the run and its parent scrape run."""
    params = dict(run.get("execution_params") or {})
    platform = str(run.get("platform") or params.get("platform") or "")
    browser_account = str(params.get("browser_account") or "")
    cdp_port = params.get("cdp_port")
    profile_key = str(params.get("profile_key") or "")
    scrape_task_id = str(params.get("scrape_task_id") or "")

    if scrape_task_id and (
        not platform or not browser_account or cdp_port is None or not profile_key
    ):
        try:
            parent = store.get_screening_run(scrape_task_id)
        except Exception:
            parent = None
        if parent:
            parent_params = dict(parent.get("execution_params") or {})
            platform = platform or str(parent.get("platform") or parent_params.get("platform") or "")
            browser_account = browser_account or str(parent_params.get("browser_account") or "")
            if cdp_port is None:
                cdp_port = parent_params.get("cdp_port")
            profile_key = profile_key or str(parent_params.get("profile_key") or "")

    return {
        "platform": platform,
        "browser_account": browser_account,
        "cdp_port": cdp_port,
        "profile_key": profile_key,
    }


def persist_frozen_identity(store, run_id: str, identity: dict[str, Any]) -> None:
    """Write non-empty identity fields back into the run execution params."""
    run = store.get_screening_run(run_id) or {}
    params = dict(run.get("execution_params") or {})
    for key, value in identity.items():
        if value not in (None, ""):
            params[key] = value
    store.update_screening_execution_params(run_id, params)


def invalidate_login_cache_for_resume(account_id: str, platform: str) -> None:
    """Drop the login cache so the next preflight performs a real probe."""
    if not account_id or not platform:
        return
    try:
        from scripts.login_state_cache import invalidate_login_state
        invalidate_login_state(str(account_id), str(platform))
    except Exception:
        _logger.debug("登录态缓存失效操作失败（best-effort 忽略）", exc_info=True)
