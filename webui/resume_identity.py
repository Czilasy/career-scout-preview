"""Frozen identity resolution for resumed pipeline tasks.

Continue endpoints must restore the exact platform, browser account, CDP
port and profile captured when the task was frozen. Missing zhilian
identity is a hard block; no implicit BOSS fallback is allowed.

030：本模块扩展为续跑身份域——账号快照、自动换号双门槛判定、换号留痕、
缺冻结账号的角色感知兜底，均落位于此；路由层只做接线。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from webui.error_registry import resolve_code
from webui.logging_setup import get_logger
from webui.pipeline_exec_status import user_visible_failure_reason

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
                                accounts: dict[str, Any] | None = None,
                                phase: str | None = None,
                                reason: str | None = None,
                                result: str | None = None) -> bool:
    """换号留痕：写 ``account_switch`` 任务事件（030 FR-005）。

    038 调用方可补充阶段、切换原因和结果；旧调用方不传时保持原摘要。
    返回是否成功写入，失败不回滚已发生的身份改写。
    """
    payload = {
        "from_account": str(from_account or ""),
        "to_account": str(to_account or ""),
        "from_name": account_display_name(from_account, accounts),
        "to_name": account_display_name(to_account, accounts),
    }
    if phase:
        payload["phase"] = str(phase)
    if reason:
        payload["reason"] = str(reason)
    if result:
        payload["result"] = str(result)
    try:
        store.append_task_event(run_id, "account_switch", payload)
        return True
    except Exception as exc:
        _logger.warning(
            "账号切换事件落库失败 task=%s error=%s（不影响切换主流程）",
            run_id,
            type(exc).__name__,
        )
        if phase or reason:
            try:
                store.append_task_event(run_id, "whitebox_incomplete", {
                    "event_type": "account_switch", "reason": "write_failed",
                })
            except Exception as marker_exc:
                _logger.warning(
                    "白箱不完整标记写入失败 task=%s event=account_switch error=%s",
                    run_id,
                    type(marker_exc).__name__,
                )
        return False


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
    resolved = resolve_frozen_browser_account(
        run,
        platform=platform,
        fallback_account=fallback_account,
        accounts_path=accounts_path,
        role=role,
    )
    if resolved:
        params["browser_account"] = resolved
        store.update_screening_execution_params(run_id, params)
    return resolved


def resolve_frozen_browser_account(
        run: dict[str, Any] | None, *, platform: str,
        fallback_account: str, accounts_path: Any = None, role: str = "") -> str:
    """Resolve a missing account without writing it to the run."""
    params = dict((run or {}).get("execution_params") or {})
    frozen = str(params.get("browser_account") or "")
    if frozen:
        return frozen
    fallback = str(fallback_account or "") or "a"
    if role and str(platform or "") == "boss":
        from webui.pipeline_exec_accounts import account_for_role
        return account_for_role(role, accounts_path, run=run, fallback=fallback)
    return fallback


def inherit_parent_frozen_identity(store: Any, source_run_id: str,
                                   operational_errors: tuple) -> dict[str, Any]:
    """单岗位 JD 抓取从来源 run 继承冻结平台/浏览器身份（T407/T417，
    030 自 pipeline_jobs_api 原样搬入，行为不变）。查询失败返回空
    身份；调用方只有在确认 legacy BOSS 入口时才应补默认平台。"""
    identity: dict[str, Any] = {
        "platform": None,
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
        identity["platform"] = str(checkpoint.get("platform") or "") or None
        identity["filter_schema_version"] = checkpoint.get("filter_schema_version")
    parent_params = (parent_run or {}).get("execution_params") or {}
    identity["platform"] = (
        identity["platform"]
        or str((parent_run or {}).get("platform") or "")
        or str(parent_params.get("platform") or "")
        or None
    )
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


def resolve_child_frozen_identity(
        store: Any, source_run_id: str, *, fallback_account: str | Callable[[], str],
        accounts_path: Any = None, operational_errors: tuple = (),
        source_platform: str | None = None) -> dict[str, Any]:
    """Resolve a complete child-task identity from its frozen source run.

    A normal parent already carries all four identity fields.  Legacy BOSS
    parents may only have an account, so their registered login space fills in
    the missing port/key.  Zhilian never falls back to a mutable global account
    when the parent has no frozen account; the caller must surface the missing
    identity contract instead.
    """
    inherited = inherit_parent_frozen_identity(
        store, source_run_id, operational_errors,
    )
    identity = {
        key: inherited.get(key)
        for key in (
            "platform", "browser_account", "cdp_port", "profile_key",
            "filter_schema_version",
        )
    }
    platform = str(identity.get("platform") or "").strip().lower()
    if not platform:
        platform = str(source_platform or "").strip().lower()
        if platform:
            identity["platform"] = platform
    # No parent row or memory platform means this is an old BOSS source.
    # This is the sole compatibility default; a known platform is never
    # overwritten by it.
    if not platform:
        platform = "boss"
        identity["platform"] = platform
    account = str(identity.get("browser_account") or "").strip()
    if not account and platform == "boss":
        fallback = fallback_account() if callable(fallback_account) else fallback_account
        account = str(fallback or "").strip()
        if account:
            identity["browser_account"] = account
    if not platform or not account:
        return identity
    # Early BOSS runs stored the account id itself as ``profile_key``.  Keep
    # those legacy runs resumable, but normalize them to the registered login
    # space before the child task is activated.  Explicit non-legacy conflicts
    # remain frozen and are rejected by the strict browser binding helper.
    legacy_boss_key = platform == "boss" and str(identity.get("profile_key") or "").strip() == account
    if (
        identity.get("cdp_port") not in (None, "")
        and identity.get("profile_key")
        and not legacy_boss_key
    ):
        return identity
    try:
        from webui.pipeline_exec_accounts import resolve_browser_account
        from webui.platforms import resolve_login_space
        boss_profile_dir = resolve_browser_account(account, accounts_path) or "unresolved"
        login_space = resolve_login_space(
            platform, account, boss_profile_dir=boss_profile_dir,
        )
    except Exception as exc:
        _logger.warning(
            "子任务冻结身份登录空间解析失败 task=%s operation=child_login_space_resolve error=%s；保留原身份",
            source_run_id,
            type(exc).__name__,
        )
        return identity
    identity.update({
        "browser_account": account,
        "cdp_port": login_space.cdp_port,
        "profile_key": login_space.profile_key,
    })
    return identity


def apply_continue_account_switch(store: Any, run: dict[str, Any], *,
                                  run_id: str, target_account: str,
                                  auto_switch: tuple[bool, str, str] | None,
                                  accounts_path: Any) -> dict[str, Any]:
    """继续接口换号的应用与校验（030 自 task_continue_api 原样收口）。

    校验目标账号存在性与浏览器身份、对候选身份做阻断检查，返回尚未
    持久化的冻结身份候选；持久化和换号事件由继续路由在浏览器激活成功
    后统一提交。行为与原路由逐分支一致。

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
    return {
        "status": "ok",
        "run": candidate_run,
        "identity": candidate,
        "auto_switch": auto_switch,
    }


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


def build_frozen_identity_candidate(
        run: dict[str, Any] | None, identity: dict[str, Any]) -> dict[str, Any]:
    """Build an unpersisted task copy carrying one frozen browser identity."""
    candidate = dict(run or {})
    params = dict(candidate.get("execution_params") or {})
    for key, value in identity.items():
        if value not in (None, ""):
            candidate[key] = value
            params[key] = value
    candidate["execution_params"] = params
    return candidate


def activate_frozen_identity_candidate(
        activate: Callable[[dict[str, Any]], Any],
        run: dict[str, Any] | None,
        identity: dict[str, Any],
) -> dict[str, Any]:
    """Validate/activate an identity candidate before any durable mutation."""
    candidate = build_frozen_identity_candidate(run, identity)
    try:
        activate(candidate)
    except Exception as exc:
        raw_code = str(
            getattr(exc, "error_code", "")
            or getattr(exc, "failed_code", "")
            or "source_cdp_unavailable"
        )
        error_code = resolve_code(
            raw_code, default="source_cdp_unavailable",
        )
        message = user_visible_failure_reason(
            error_code, "", str(candidate.get("platform") or ""),
        )
        _logger.warning(
            "冻结浏览器激活失败 task=%s error=%s",
            str(candidate.get("id") or ""), type(exc).__name__,
            exc_info=True,
        )
        return {
            "ok": False,
            "error": error_code,
            "error_code": error_code,
            "status": "paused",
            "message": message,
            "detail": type(exc).__name__,
        }
    return {"ok": True, "run": candidate}


def prepare_continue_identity(
        store: Any, run: dict[str, Any], *, target_account: str,
        current_account: str | Callable[[], str],
        fallback_account: str | Callable[[], str], accounts_path: Any,
) -> dict[str, Any]:
    """Build a continue candidate without mutating the paused run."""
    identity = resolve_frozen_identity(store, run)
    params = dict(run.get("execution_params") or {})
    incomplete = any(
        identity.get(key) in (None, "")
        for key in ("platform", "browser_account", "cdp_port", "profile_key")
    )
    frozen_account = str(params.get("browser_account") or "").strip()
    platform = str(identity.get("platform") or "").strip().lower()
    # A known incomplete Zhilian run must fail before consulting mutable
    # advanced settings.  Apart from being the frozen-identity contract, this
    # prevents a missing Zhilian identity from accidentally selecting BOSS's
    # current account during resume.
    if platform == "zhilian" and incomplete:
        active = ""
    else:
        active = current_account() if callable(current_account) else current_account
        if isinstance(active, dict):
            active = active.get("browser_account")
    auto_switch = decide_auto_account_switch(
        run, current_active_account=str(active or ""))
    selected_account = str(target_account or "").strip()
    if not selected_account and auto_switch[0]:
        selected_account = auto_switch[2]

    if selected_account:
        applied = apply_continue_account_switch(
            store, run, run_id=str(run.get("id") or ""),
            target_account=selected_account, auto_switch=auto_switch,
            accounts_path=accounts_path,
        )
        if applied["status"] != "ok":
            return applied
        identity = applied["identity"]
        candidate_run = applied["run"]
    else:
        if not frozen_account and platform == "boss":
            fallback = fallback_account() if callable(fallback_account) else fallback_account
            effective = resolve_frozen_browser_account(
                run, platform=platform, fallback_account=str(fallback or ""),
                accounts_path=accounts_path, role="R2",
            )
            if effective:
                identity["browser_account"] = effective
        # Legacy BOSS tasks often froze only the account (or used the account
        # id as profile_key).  Complete that compatibility identity through
        # the registry before the strict activation helper runs.  Zhilian is
        # deliberately excluded: it must have its own frozen profile/port.
        if platform == "boss" and identity.get("browser_account"):
            account = str(identity["browser_account"])
            profile_key = str(identity.get("profile_key") or "")
            if not profile_key or profile_key == account or identity.get("cdp_port") in (None, ""):
                try:
                    from webui.pipeline_exec_accounts import resolve_browser_account
                    from webui.platforms import resolve_login_space
                    boss_profile_dir = resolve_browser_account(account, accounts_path)
                    login_space = resolve_login_space(
                        "boss", account, boss_profile_dir=boss_profile_dir,
                    )
                    identity["profile_key"] = login_space.profile_key
                    identity["cdp_port"] = login_space.cdp_port
                except Exception as exc:
                    # Keep the incomplete identity so the caller returns the
                    # existing public missing/activation error contract.
                    _logger.warning(
                        "续跑冻结身份兼容归一失败 task=%s operation=legacy_login_space_resolve error=%s；保留不完整身份",
                        str((run or {}).get("id") or ""),
                        type(exc).__name__,
                    )
        missing = [
            key for key in ("platform", "browser_account", "cdp_port", "profile_key")
            if identity.get(key) in (None, "")
        ]
        if not identity.get("platform") or (
                identity["platform"] == "zhilian" and missing):
            return {"status": "missing_frozen_identity", "http_status": 409,
                    "body": {
                        "ok": False, "error": "missing_frozen_identity",
                        "message": "继续任务缺少冻结的账号或浏览器身份，无法安全恢复",
                        "status": "paused", "missing_fields": missing,
                    }}
        candidate_run = build_frozen_identity_candidate(run, identity)
    return {
        "status": "ok", "run": candidate_run, "identity": identity,
        "auto_switch": auto_switch,
    }


def commit_continue_identity(store: Any, run_id: str,
                             identity: dict[str, Any],
                             auto_switch: tuple[bool, str, str] | None = None) -> None:
    """Persist a candidate only after its browser binding was activated.

    The continue route deliberately performs this after activation.  Keeping
    the commit in the identity module prevents callers from writing a paused
    run before a failed CDP bind has had a chance to leave it untouched.
    """
    persist_frozen_identity(store, run_id, identity)
    if auto_switch is not None and auto_switch[0]:
        record_account_switch_event(
            store, run_id,
            from_account=auto_switch[1], to_account=auto_switch[2],
        )


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
