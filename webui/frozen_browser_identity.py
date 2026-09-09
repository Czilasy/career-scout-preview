"""Resolve and activate the frozen browser identity of a platform task.

The helper owns only account/profile mapping.  Chrome lifecycle and task
state remain in their existing callers; BOSS and Zhilian therefore share the
same binding contract while the platform registry supplies profile derivation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from webui.pipeline_exec_accounts import resolve_browser_account
from webui.platforms import derive_zhilian_profile_dir, resolve_login_space
from webui.logging_setup import get_logger
from webui.task_event_audit import append_task_event_best_effort

_logger = get_logger(__name__)


class FrozenBrowserBindingError(ValueError):
    """A frozen task identity cannot be resolved to a registered login space."""

    failed_code = "source_cdp_unavailable"


@dataclass(frozen=True)
class FrozenBrowserProfile:
    """Resolved logical identity and concrete data directory for one task."""

    platform: str
    browser_account: str
    profile_key: str
    cdp_port: int
    boss_profile_dir: str
    profile_dir: str


@dataclass(frozen=True)
class FrozenSourceProfileBinding:
    """Structured result for binding a task's frozen source profile."""

    ok: bool
    profile: FrozenBrowserProfile | None = None
    error_code: str | None = None
    error: str = ""


@dataclass(frozen=True)
class FrozenBrowserCleanupResult:
    """Observable outcome of closing a task-owned browser."""

    ok: bool
    error_code: str | None = None
    message: str = ""
    platform: str = ""
    browser_account: str = ""
    cdp_port: int | None = None
    attempted: bool = False
    closed: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "message": self.message,
            "platform": self.platform,
            "browser_account": self.browser_account,
            "cdp_port": self.cdp_port,
            "attempted": self.attempted,
            "closed": self.closed,
        }


def _identity(platform: object, browser_account: object, profile_key: object) -> str:
    return (
        f"platform={str(platform or '').strip() or '<missing>'}, "
        f"browser_account={str(browser_account or '').strip() or '<missing>'}, "
        f"profile_key={str(profile_key or '').strip() or '<missing>'}"
    )


def _binding_error(message: str, platform: object, browser_account: object,
                   profile_key: object) -> FrozenBrowserBindingError:
    return FrozenBrowserBindingError(
        f"{message} ({_identity(platform, browser_account, profile_key)})"
    )


def _run_identity_value(
        run: dict[str, object], params: dict[str, object], key: str) -> object:
    """Read one identity field with the durable run field taking precedence."""
    value = run.get(key)
    return params.get(key) if value in (None, "") else value


def resolve_frozen_browser_activation_identity(
        run: dict[str, object] | None, *,
        fallback_account: Callable[[dict[str, object] | None], object] | None = None,
        ) -> dict[str, object]:
    """Build the identity consumed by the strict browser binding helper.

    The only compatibility default in this boundary is the pre-freeze BOSS
    shape: an old run may carry just its account, or the account id itself as
    ``profile_key``.  A known Zhilian run never consults ``fallback_account``;
    its missing identity remains a hard binding error.
    """
    run_data = dict(run or {})
    raw_params = run_data.get("execution_params")
    params = dict(raw_params) if isinstance(raw_params, dict) else {}
    platform = str(_run_identity_value(run_data, params, "platform") or "").strip().lower()
    account = str(
        _run_identity_value(run_data, params, "browser_account") or ""
    ).strip()
    profile_key = str(
        _run_identity_value(run_data, params, "profile_key") or ""
    ).strip()
    cdp_port = _run_identity_value(run_data, params, "cdp_port")
    has_execution_config = bool(params.get("execution_config"))

    # ``run is None`` is the legacy no-run entry point.  An old BOSS row can
    # also omit platform entirely, but a frozen execution config must not be
    # assigned a guessed platform.
    if run is None or (not platform and not has_execution_config):
        platform = "boss"

    if platform == "boss" and not has_execution_config:
        if not account and fallback_account is not None:
            account = str(fallback_account(run) or "").strip()
        if account and (not profile_key or profile_key == account):
            profile_key = f"boss:{account}"

    return {
        "platform": platform,
        "browser_account": account,
        "profile_key": profile_key,
        "cdp_port": cdp_port,
    }


def activate_frozen_browser_run(
        run: dict[str, object] | None, *, accounts_path: object = None,
        fallback_account: Callable[[dict[str, object] | None], object] | None = None,
        activate: Callable[[str], None] | None = None,
        resolve_account: Callable[[str, object], str] | None = None,
        ) -> FrozenBrowserProfile:
    """Resolve and activate a run's identity through one public boundary."""
    identity = resolve_frozen_browser_activation_identity(
        run, fallback_account=fallback_account,
    )
    return activate_frozen_browser_profile(
        identity["platform"],
        identity["browser_account"],
        identity["profile_key"],
        cdp_port=identity["cdp_port"],
        accounts_path=accounts_path,
        activate=activate,
        resolve_account=resolve_account,
    )


def _platform_profile_dir(platform: str, boss_profile_dir: str) -> str:
    if platform == "boss":
        return boss_profile_dir
    if platform == "zhilian":
        return derive_zhilian_profile_dir(boss_profile_dir)
    raise ValueError(f"unsupported platform profile: {platform}")


def resolve_frozen_browser_profile(
    platform: object,
    browser_account: object,
    profile_key: object,
    *,
    cdp_port: object = None,
    accounts_path: object = None,
    resolve_account: Callable[[str, object], str] | None = None,
) -> FrozenBrowserProfile:
    """Resolve a frozen run identity without reading mutable settings.

    ``profile_key`` is checked against the platform registry's login-space
    contract. A missing account, unknown profile, or port/key mismatch is a
    hard binding error; there is intentionally no default-account fallback.
    """
    normalized_platform = str(platform or "").strip().lower()
    account = str(browser_account or "").strip()
    key = str(profile_key or "").strip()
    if not normalized_platform or not account or not key:
        raise _binding_error(
            "冻结登录空间身份不完整", normalized_platform, account, key,
        )

    if resolve_account is None:
        resolve_account = resolve_browser_account
    boss_profile_dir = str(resolve_account(account, accounts_path) or "")
    if not boss_profile_dir:
        raise _binding_error(
            "冻结账号未绑定浏览器资料目录",
            normalized_platform, account, key,
        )

    try:
        login_space = resolve_login_space(
            normalized_platform, account, boss_profile_dir=boss_profile_dir,
        )
    except Exception as exc:
        raise _binding_error(
            f"冻结登录空间解析失败: {type(exc).__name__}",
            normalized_platform, account, key,
        ) from exc

    if key != login_space.profile_key:
        raise _binding_error(
            "冻结 profile_key 与平台登录空间不一致",
            normalized_platform, account, key,
        )

    try:
        resolved_port = login_space.cdp_port if cdp_port is None else int(cdp_port)
    except (TypeError, ValueError) as exc:
        raise _binding_error(
            "冻结 cdp_port 无效", normalized_platform, account, key,
        ) from exc
    if resolved_port != login_space.cdp_port:
        raise _binding_error(
            "冻结 cdp_port 与平台登录空间不一致",
            normalized_platform, account, key,
        )

    try:
        profile_dir = _platform_profile_dir(normalized_platform, boss_profile_dir)
    except Exception as exc:
        raise _binding_error(
            f"平台 profile 目录派生失败: {type(exc).__name__}",
            normalized_platform, account, key,
        ) from exc
    if not str(profile_dir or "").strip():
        raise _binding_error(
            "平台 profile 目录为空", normalized_platform, account, key,
        )
    return FrozenBrowserProfile(
        platform=normalized_platform,
        browser_account=account,
        profile_key=key,
        cdp_port=resolved_port,
        boss_profile_dir=os.fspath(boss_profile_dir),
        profile_dir=os.fspath(profile_dir),
    )


def activate_frozen_browser_profile(
    platform: object,
    browser_account: object,
    profile_key: object,
    *,
    cdp_port: object = None,
    accounts_path: object = None,
    activate: Callable[[str], None] | None = None,
    resolve_account: Callable[[str, object], str] | None = None,
) -> FrozenBrowserProfile:
    """Resolve and activate one frozen profile, propagating bind failures."""
    resolved = resolve_frozen_browser_profile(
        platform,
        browser_account,
        profile_key,
        cdp_port=cdp_port,
        accounts_path=accounts_path,
        resolve_account=resolve_account,
    )
    if activate is None:
        from webui.pipeline_exec_accounts import set_active_cdp_data_dir
        activate = set_active_cdp_data_dir
    try:
        activate(resolved.profile_dir)
    except Exception as exc:
        raise _binding_error(
            f"冻结登录空间绑定失败: {type(exc).__name__}",
            resolved.platform, resolved.browser_account, resolved.profile_key,
        ) from exc
    return resolved


def bind_frozen_source_profile(
        source: object, execution_config: object, *,
        activate: Callable[[str], None]) -> FrozenSourceProfileBinding:
    """Resolve and activate a source profile for a frozen execution snapshot.

    The search orchestrator only needs the structured outcome.  Keeping the
    source identity lookup and activation here prevents a platform-specific
    fallback from being introduced at the pipeline entry point.
    """
    if execution_config is None:
        return FrozenSourceProfileBinding(ok=True)

    platform = getattr(source, "platform", "boss")
    browser_account = getattr(source, "browser_account", None)
    profile_key = getattr(source, "profile_key", None)
    cdp_port = getattr(source, "cdp_port", None)
    try:
        profile = activate_frozen_browser_profile(
            platform,
            browser_account,
            profile_key,
            cdp_port=cdp_port,
            activate=activate,
        )
    except Exception as exc:
        return FrozenSourceProfileBinding(
            ok=False,
            error_code=FrozenBrowserBindingError.failed_code,
            error=f"冻结登录空间不可用：{exc}",
        )
    return FrozenSourceProfileBinding(ok=True, profile=profile)


def close_frozen_run_browser(
        store: object, run: dict[str, object], *, accounts_path: object = None,
        activate: Callable[[str], None], close: Callable[..., object]
        ) -> FrozenBrowserCleanupResult:
    """Safely bind and close the browser belonging to one frozen run.

    The result is intentionally structured instead of a bool: a saved result
    or cancelled task must remain durable when browser cleanup fails, while
    the API still exposes that failure to the caller.  The close callback
    receives the resolved platform port, so an incomplete Zhilian run can
    never fall through to the BOSS default port.
    """
    run_data = dict(run or {})
    params = dict(run_data.get("execution_params") or {})
    for key in ("platform", "browser_account", "profile_key", "cdp_port"):
        if run_data.get(key) not in (None, "") and params.get(key) in (None, ""):
            params[key] = run_data[key]
    run_data["execution_params"] = params
    platform = str(run_data.get("platform") or params.get("platform") or "").strip().lower()

    def _failure(code: str, message: str, *, account: str = "",
                 port: int | None = None, attempted: bool = False,
                 closed: bool = False) -> FrozenBrowserCleanupResult:
        return FrozenBrowserCleanupResult(
            ok=False, error_code=code, message=message, platform=platform,
            browser_account=account, cdp_port=port,
            attempted=attempted, closed=closed,
        )

    # A pre-freeze BOSS task has no durable identity but historically closed
    # the default BOSS browser.  Keep only this explicit compatibility path;
    # a known Zhilian task must never call close() without its own port.
    has_identity = any(
        str(params.get(key) or "").strip()
        for key in ("browser_account", "profile_key", "cdp_port")
    )
    if not has_identity:
        if str(run_data.get("status") or "").strip().lower() == "paused":
            return FrozenBrowserCleanupResult(
                ok=True,
                message="任务从未绑定冻结浏览器身份，无需清理",
                platform=platform,
                attempted=False,
                closed=False,
            )
        if platform not in ("", "boss"):
            return _failure(
                "missing_frozen_identity",
                "智联任务缺少冻结浏览器身份，未关闭其他平台浏览器",
            )
        try:
            closed = bool(close())
        except Exception as exc:
            return _failure(
                "source_cdp_unavailable",
                "浏览器清理失败，任务结果已保留",
                attempted=True,
                closed=False,
            )
        if not closed:
            return _failure(
                "source_cdp_unavailable",
                "浏览器清理失败，任务结果已保留",
                attempted=True,
                closed=False,
            )
        return FrozenBrowserCleanupResult(
            ok=True, message="浏览器已关闭", platform=platform,
            attempted=True, closed=True,
        )

    from webui.resume_identity import resolve_frozen_identity

    identity = resolve_frozen_identity(store, run_data)
    platform = str(identity.get("platform") or platform).strip().lower()
    account = str(identity.get("browser_account") or "").strip()
    profile_key = str(identity.get("profile_key") or "").strip()
    cdp_port = identity.get("cdp_port")
    # Legacy BOSS runs stored only account or account-as-profile_key.  Resolve
    # those fields through the BOSS registry; this branch is intentionally
    # unavailable to Zhilian.
    if platform == "boss" and account:
        legacy_key = not profile_key or profile_key == account
        if legacy_key or cdp_port in (None, ""):
            try:
                boss_profile_dir = str(resolve_browser_account(account, accounts_path) or "")
                login_space = resolve_login_space(
                    "boss", account, boss_profile_dir=boss_profile_dir,
                )
                profile_key = login_space.profile_key
                cdp_port = login_space.cdp_port
            except Exception as exc:
                return _failure(
                    "source_cdp_unavailable",
                    "BOSS 冻结登录空间解析失败，未关闭浏览器",
                    account=account,
                )
    required = (platform, account, profile_key, cdp_port)
    if not platform or not account or not profile_key or cdp_port in (None, ""):
        return _failure(
            "missing_frozen_identity",
            "任务缺少冻结浏览器身份，未关闭浏览器",
            account=account,
        )
    try:
        resolved = activate_frozen_browser_profile(
            platform, account, profile_key, cdp_port=cdp_port,
            accounts_path=accounts_path, activate=activate,
        )
    except Exception as exc:
        return _failure(
            "source_cdp_unavailable",
            "冻结登录空间绑定失败，未关闭浏览器",
            account=account,
            attempted=False,
        )
    try:
        closed = bool(close(resolved.cdp_port))
    except Exception as exc:
        return _failure(
            "source_cdp_unavailable",
            "浏览器清理失败，任务结果已保留",
            account=account, port=resolved.cdp_port,
            attempted=True,
        )
    if not closed:
        return _failure(
            "source_cdp_unavailable",
            "浏览器清理失败，任务结果已保留",
            account=account, port=resolved.cdp_port,
            attempted=True,
        )
    return FrozenBrowserCleanupResult(
        ok=True, message="浏览器已关闭", platform=platform,
        browser_account=account, cdp_port=resolved.cdp_port,
        attempted=True, closed=True,
    )


def cleanup_frozen_task_browser(
        store: object, run_id: str, task: dict[str, object] | None, *,
        accounts_path: object = None, activate: Callable[[str], None],
        close: Callable[..., object]) -> FrozenBrowserCleanupResult:
    """Resolve the durable run and expose one cleanup outcome to cancel APIs."""
    try:
        durable_run = store.get_screening_run(run_id)
    except Exception as exc:
        _logger.warning(
            "冻结任务读取失败 task=%s error=%s；使用内存任务回退",
            run_id,
            type(exc).__name__,
        )
        durable_run = None
    result = close_frozen_run_browser(
        store, durable_run or task or {}, accounts_path=accounts_path,
        activate=activate, close=close,
    )
    if not result.ok and durable_run is not None:
        append_task_event_best_effort(
            store, run_id, "browser_cleanup_failed", result.as_dict(),
            logger=_logger,
            context="browser cleanup audit event write failed",
        )
    return result


__all__ = [
    "FrozenBrowserBindingError",
    "FrozenBrowserCleanupResult",
    "FrozenBrowserProfile",
    "FrozenSourceProfileBinding",
    "activate_frozen_browser_run",
    "activate_frozen_browser_profile",
    "bind_frozen_source_profile",
    "close_frozen_run_browser",
    "cleanup_frozen_task_browser",
    "resolve_frozen_browser_activation_identity",
    "resolve_frozen_browser_profile",
]
