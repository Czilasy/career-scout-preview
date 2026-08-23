"""平台能力检查：URL/登录空间派发、fixture 完整性、城市解析、账号变更冲突（021 B7 自 platforms.py 搬运）。"""

from __future__ import annotations



from webui.platforms_registry import (
    get_platform,
    validate_platform_key,
)
from webui.platforms_schema import (
    _MSG_BOSS_PROFILE_DIR_REQUIRED,
    _MSG_BROWSER_ACCOUNT_REQUIRED,
    CityEntry,
    LoginSpace,
)




def normalize_job_url(platform: str, raw: str) -> str:
    """按平台规则规范化岗位链接；未注册平台抛错。"""
    return get_platform(platform).normalize_job_url(raw)




def resolve_login_space(
    platform: str, browser_account: str, *, boss_profile_dir: str,
) -> LoginSpace:
    """按平台规则解析登录空间；未注册平台抛错。"""
    return get_platform(platform).resolve_login_space(
        browser_account, boss_profile_dir=boss_profile_dir,
    )




# ---------------------------------------------------------------------------
# T206: schema/城市 fixture 完整性检查
# ---------------------------------------------------------------------------

def check_platform_fixture_integrity(platform: str) -> tuple[bool, str]:
    """检查平台 schema/城市 fixture 完整性（T206）。

    返回 ``(ok, reason)``：
    - 需选项字段非空（每个 FilterField.options 必须有元素）；
    - 稳定值和标签非空（FilterOption.__post_init__ 已强制）；
    - 映射版本存在（PlatformCityCatalog 已强制 mapping_version 正整数）；
    - 城市目录非空。

    ok=False 时 reason 描述缺失项；调用方据此保持 ``enabled_for_new_tasks=False``
    或返回 ``platform_schema_unavailable`` / ``city_mapping_unavailable``。
    """
    reg = get_platform(platform)
    schema = reg.filter_schema
    catalog = reg.city_catalog

    empty_option_fields = [f.key for f in schema.fields if not f.options]
    if empty_option_fields:
        return False, (
            f"fields with empty options: {', '.join(empty_option_fields)}"
        )

    if not catalog.entries:
        return False, "city catalog is empty"

    return True, ""




def resolve_platform_city(platform: str, city_name: str) -> CityEntry:
    """按平台城市目录解析规范城市名为平台码（T206 缺城阻断）。

    缺城时抛 ``ValueError("city_mapping_missing")``；
    平台未注册时抛 ``PlatformNotRegisteredError``。
    """
    reg = get_platform(platform)
    entry = reg.city_catalog.find(city_name)
    if entry is None:
        raise ValueError(
            f"city_mapping_missing: 平台 {platform} 缺少城市映射: {city_name}"
        )
    return entry




# ---------------------------------------------------------------------------
# T208-T210: 浏览器登录空间派生与双平台检查
# ---------------------------------------------------------------------------

def derive_zhilian_profile_dir(boss_profile_dir: str) -> str:
    """派生智联 profile_dir = boss_profile_dir + '.zhilian'（T208）。

    确定性派生：同一 ``boss_profile_dir`` 总是产生同一智联 profile_dir。
    与 BOSS profile_dir 不同（后缀 ``.zhilian``），保证两个平台的 profile
    目录隔离。绝对路径只在后端运行时存在，不写数据库、日志或用户 API。
    """
    if not boss_profile_dir:
        raise ValueError(_MSG_BOSS_PROFILE_DIR_REQUIRED)
    return str(boss_profile_dir).rstrip("/\\") + ".zhilian"




def check_login_space_conflict(
    platform: str,
    browser_account: str,
    *,
    boss_profile_dir: str,
    port_profile_paths: list[str],
    known_profile_paths: list[str],
) -> tuple[bool, str]:
    """检查登录空间冲突（T209）。

    受控切换规则（platform-schema.md 浏览器登录空间）：

    - 端口空闲 → ``(True, "")``；
    - 端口被期望 profile 占用 → ``(True, "")``（复用）；
    - 端口被同平台已知 profile 占用 → ``(True, "")``（允许受控切换）；
    - 端口被未知 profile 占用 → ``(False, "login_space_conflict")``。

    参数：
        platform: ``boss`` 或 ``zhilian``
        browser_account: 账号 ID
        boss_profile_dir: 该账号的 BOSS profile 目录
        port_profile_paths: 端口上当前占用的 profile 路径列表（已规范化）
        known_profile_paths: 该平台所有已知账号的 profile 路径列表（已规范化）

    未知平台抛 ``UnknownPlatformError``。
    """
    validate_platform_key(platform)
    if not browser_account:
        raise ValueError(_MSG_BROWSER_ACCOUNT_REQUIRED)

    if platform == "boss":
        expected = str(boss_profile_dir)
    else:  # zhilian
        expected = derive_zhilian_profile_dir(boss_profile_dir)

    if not port_profile_paths:
        return True, ""

    if expected in port_profile_paths:
        return True, ""

    known_set = set(known_profile_paths)
    for port_path in port_profile_paths:
        if port_path in known_set:
            return True, ""

    return False, "login_space_conflict"




def check_browser_account_delete(
    browser_account: str,
    *,
    boss_profile_dir: str,
    running_locks: list[dict],
    port_profiles_boss: list[str],
    port_profiles_zhilian: list[str],
) -> tuple[bool, str]:
    """检查账号删除是否允许（T210）。

    原子检查两个平台 profile、两个端口和运行锁：

    - ``running_locks``: ``[{platform, account, kind}, ...]``，任一命中该账号
      → ``(False, "browser_busy")``；
    - ``port_profiles_boss``: 9222 端口上的 profile 路径，BOSS profile 命中
      → ``(False, "browser_in_use")``；
    - ``port_profiles_zhilian``: 9223 端口上的 profile 路径，智联派生 profile
      命中 → ``(False, "browser_in_use")``。

    任一命中即阻断删除，不先删除其中一个目录。
    """
    if not browser_account:
        raise ValueError(_MSG_BROWSER_ACCOUNT_REQUIRED)

    for lock in running_locks:
        if lock.get("account") == browser_account:
            kind = lock.get("kind", "unknown")
            return False, f"browser_busy: {kind} lock"

    if boss_profile_dir and boss_profile_dir in port_profiles_boss:
        return False, "browser_in_use: boss profile"

    zhilian_dir = derive_zhilian_profile_dir(boss_profile_dir)
    if zhilian_dir in port_profiles_zhilian:
        return False, "browser_in_use: zhilian profile"

    return True, ""




def check_browser_account_activate(
    browser_account: str,
    *,
    account_exists: bool,
) -> tuple[bool, str]:
    """检查账号激活是否允许（T210）。

    ``activate`` 只改草稿（设置当前活跃账号），不启动 Chrome、不切换
    profile、不触碰端口。只验证账号存在，不检查运行锁或端口占用。
    """
    del browser_account  # 调用方已校验账号；这里只做存在性门禁
    if not account_exists:
        return False, "account_not_found"
    return True, ""
