"""平台注册表：招聘平台能力的唯一权威来源。

本模块是平台键、显示名、AI 筛选 schema、城市目录、URL allowlist、
默认 CDP 端口、登录空间解析和 source factory 的唯一注册边界。
其它模块不得在 ``app.py``、``core.py``、Vue 或 pipeline 中维护第二套
平台字段、城市码、域名、端口或 profile 派生规则。

平台未知、能力缺失、schema 或城市目录不可用时，必须在创建执行前失败，
不能回退成 BOSS。

参考合同：``specs/001-add-zhilian-platform/contracts/platform-schema.md``。

021 B7 后本文件为兼容门面：实现拆至 platforms_* 域模块，re-export 全部
既有符号，旧 import 与 patch 面不变（宪法 VI）。注意：BOSS_PLATFORM 为
platforms_boss 的延迟赋值全局，门面绑定的是 import 时快照，外部读取请走
platforms_boss.BOSS_PLATFORM 或 get_platform("boss")。
"""

from __future__ import annotations

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from webui.url_safety import clean_https_url, is_safe_https_authority, parse_https_url


from webui.platforms_schema import (
    BOSS_CITY_MAPPING_VERSION,
    BOSS_DEFAULT_CDP_PORT,
    BOSS_FILTER_SCHEMA_VERSION,
    CityEntry,
    DEFAULT_PLATFORM,
    FilterField,
    FilterOption,
    KNOWN_PLATFORM_KEYS,
    LoginSpace,
    PlatformCityCatalog,
    PlatformDisabledError,
    PlatformError,
    PlatformFilterSchema,
    PlatformNotRegisteredError,
    PlatformRegistry,
    UnknownPlatformError,
    ZHILIAN_CITY_MAPPING_VERSION,
    ZHILIAN_DEFAULT_CDP_PORT,
    ZHILIAN_FILTER_SCHEMA_VERSION,
    _BOSS_ALLOWED_HOSTS,
    _BOSS_COMMON_FIELDS,
    _BOSS_EXCLUSIVE_FIELDS,
    _MSG_BOSS_PROFILE_DIR_REQUIRED,
    _MSG_BROWSER_ACCOUNT_REQUIRED,
    _ZHILIAN_ALLOWED_HOSTS,
    _ZHILIAN_EXCLUSIVE_FIELDS,
)
from webui.platforms_urls import (
    _ZHILIAN_DETAIL_PATTERN,
    _ZHILIAN_JOB_PATH_RE,
    _ZHILIAN_JOB_SOURCE_PATH_RE,
    _boss_profile_dir_differs_from_zhilian,
    _clean_url,
    normalize_boss_job_url,
    normalize_zhilian_job_url,
    resolve_boss_login_space,
    resolve_zhilian_login_space,
)
from webui.platforms_registry import (
    _REGISTRY,
    get_platform,
    get_platform_or_none,
    is_known_platform_key,
    list_platform_keys,
    list_platforms,
    register_platform,
    resolve_platform_or_default,
    validate_platform_key,
)
from webui.platforms_checks import (
    check_browser_account_activate,
    check_browser_account_delete,
    check_login_space_conflict,
    check_platform_fixture_integrity,
    derive_zhilian_profile_dir,
    normalize_job_url,
    resolve_login_space,
    resolve_platform_city,
)
from webui.platforms_filters import (
    _coerce_value_list,
    build_filter_snapshot,
    project_filter_schema,
    validate_filter_values,
)
from webui.platforms_boss import (
    BOSS_PLATFORM,
    _DEFAULT_BOSS_CITY_MAP,
    _DEFAULT_BOSS_FILTER_MAPS,
    _build_boss_city_catalog,
    _build_boss_filter_options,
    _build_boss_filter_schema,
    _ensure_boss_registered,
    _initialize_boss_from_runtime,
    _register_boss,
    boss_city_catalog,
    boss_filter_schema,
    register_boss_from_maps,
)
from webui.platforms_zhilian import (
    ZHILIAN_AVAILABILITY_REASON,
    ZHILIAN_NATIONWIDE_CODE,
    ZHILIAN_NATIONWIDE_NAME,
    _ZHILIAN_FIELD_LABELS,
    _ZHILIAN_FIELD_OPTIONS,
    _ZHILIAN_FILTER_FIELDS,
    _build_zhilian_city_catalog,
    _build_zhilian_filter_schema,
    _register_zhilian,
)


__all__ = [
    "BOSS_CITY_MAPPING_VERSION",
    "BOSS_DEFAULT_CDP_PORT",
    "BOSS_FILTER_SCHEMA_VERSION",
    "BOSS_PLATFORM",
    "DEFAULT_PLATFORM",
    "KNOWN_PLATFORM_KEYS",
    "ZHILIAN_AVAILABILITY_REASON",
    "ZHILIAN_CITY_MAPPING_VERSION",
    "ZHILIAN_DEFAULT_CDP_PORT",
    "ZHILIAN_FILTER_SCHEMA_VERSION",
    "ZHILIAN_NATIONWIDE_CODE",
    "ZHILIAN_NATIONWIDE_NAME",
    "CityEntry",
    "FilterField",
    "FilterOption",
    "LoginSpace",
    "PlatformCityCatalog",
    "PlatformDisabledError",
    "PlatformError",
    "PlatformFilterSchema",
    "PlatformNotRegisteredError",
    "PlatformRegistry",
    "UnknownPlatformError",
    "boss_city_catalog",
    "boss_filter_schema",
    "build_filter_snapshot",
    "check_browser_account_activate",
    "check_browser_account_delete",
    "check_login_space_conflict",
    "check_platform_fixture_integrity",
    "derive_zhilian_profile_dir",
    "get_platform",
    "get_platform_or_none",
    "is_known_platform_key",
    "list_platform_keys",
    "list_platforms",
    "normalize_job_url",
    "project_filter_schema",
    "register_platform",
    "resolve_login_space",
    "resolve_platform_city",
    "resolve_platform_or_default",
    "validate_filter_values",
    "validate_platform_key",
]
