"""平台注册表：招聘平台能力的唯一权威来源。

本模块是平台键、显示名、AI 筛选 schema、城市目录、URL allowlist、
默认 CDP 端口、登录空间解析和 source factory 的唯一注册边界。
其它模块不得在 ``app.py``、``core.py``、Vue 或 pipeline 中维护第二套
平台字段、城市码、域名、端口或 profile 派生规则。

平台未知、能力缺失、schema 或城市目录不可用时，必须在创建执行前失败，
不能回退成 BOSS。

参考合同：``specs/001-add-zhilian-platform/contracts/platform-schema.md``。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse, urlunparse

__all__ = [
    "KNOWN_PLATFORM_KEYS",
    "DEFAULT_PLATFORM",
    "BOSS_DEFAULT_CDP_PORT",
    "ZHILIAN_DEFAULT_CDP_PORT",
    "BOSS_FILTER_SCHEMA_VERSION",
    "BOSS_CITY_MAPPING_VERSION",
    "FilterOption",
    "FilterField",
    "PlatformFilterSchema",
    "CityEntry",
    "PlatformCityCatalog",
    "LoginSpace",
    "PlatformRegistry",
    "PlatformError",
    "UnknownPlatformError",
    "PlatformNotRegisteredError",
    "PlatformDisabledError",
    "is_known_platform_key",
    "validate_platform_key",
    "get_platform",
    "get_platform_or_none",
    "list_platforms",
    "list_platform_keys",
    "resolve_platform_or_default",
    "normalize_job_url",
    "resolve_login_space",
    "project_filter_schema",
    "validate_filter_values",
    "build_filter_snapshot",
    "boss_filter_schema",
    "boss_city_catalog",
    "BOSS_PLATFORM",
    "register_platform",
]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 当前已知（规格允许）的平台键。注册表可能只包含其中已启用项。
KNOWN_PLATFORM_KEYS: tuple[str, ...] = ("boss", "zhilian")

#: 默认平台键（旧 BOSS 兼容入口省略 platform 时使用）。
DEFAULT_PLATFORM = "boss"

BOSS_DEFAULT_CDP_PORT = 9222
ZHILIAN_DEFAULT_CDP_PORT = 9223

#: BOSS AI 筛选 schema 版本。字段集合或稳定值/标签变化时递增。
BOSS_FILTER_SCHEMA_VERSION = 1

#: BOSS 城市目录映射版本。城市码集合变化时递增。
BOSS_CITY_MAPPING_VERSION = 2

# BOSS AI 筛选公共字段顺序（contracts/platform-schema.md 字段集合表）。
_BOSS_COMMON_FIELDS: tuple[str, ...] = (
    "salary", "experience", "degree", "industry", "scale",
)
_BOSS_EXCLUSIVE_FIELDS: tuple[str, ...] = ("stage",)
_ZHILIAN_EXCLUSIVE_FIELDS: tuple[str, ...] = ("company_nature",)

# BOSS URL allowlist（保持与现有 webui/workbench.normalize_job_link 一致）。
_BOSS_ALLOWED_HOSTS = frozenset({"www.zhipin.com", "zhipin.com"})

# 智联 URL allowlist（contracts/platform-schema.md URL 规范化）。
_ZHILIAN_ALLOWED_HOSTS = frozenset({"zhaopin.com", "www.zhaopin.com"})


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class PlatformError(ValueError):
    """平台注册或校验相关错误的基类。"""


class UnknownPlatformError(PlatformError):
    """平台键不在 KNOWN_PLATFORM_KEYS 中。"""


class PlatformNotRegisteredError(PlatformError):
    """平台键已知但尚未注册（如智联真实 fixture 未核验前）。"""


class PlatformDisabledError(PlatformError):
    """平台已注册但 enabled_for_new_tasks=false，禁止新任务。"""


# ---------------------------------------------------------------------------
# 不可变值对象
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FilterOption:
    """AI 筛选选项：稳定 value + 当时用户可见 label。"""
    value: str
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("FilterOption.value 必须为非空字符串")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("FilterOption.label 必须为非空字符串")


@dataclass(frozen=True)
class FilterField:
    """AI 筛选字段：key + label + multiple + options。"""
    key: str
    label: str
    multiple: bool
    options: tuple[FilterOption, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("FilterField.key 必须为非空字符串")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("FilterField.label 必须为非空字符串")
        if not isinstance(self.options, tuple):
            object.__setattr__(self, "options", tuple(self.options))
        # 同一字段内 value 唯一。
        seen: set[str] = set()
        for opt in self.options:
            if not isinstance(opt, FilterOption):
                raise TypeError("FilterField.options 必须为 FilterOption 元组")
            if opt.value in seen:
                raise ValueError(f"FilterField {self.key} 存在重复 value: {opt.value}")
            seen.add(opt.value)

    def option_values(self) -> tuple[str, ...]:
        return tuple(opt.value for opt in self.options)

    def label_for(self, value: str) -> str | None:
        for opt in self.options:
            if opt.value == value:
                return opt.label
        return None


@dataclass(frozen=True)
class PlatformFilterSchema:
    """平台 AI 筛选 schema：platform + schema_version + enabled + fields。"""
    platform: str
    schema_version: int
    enabled_for_new_tasks: bool
    fields: tuple[FilterField, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("schema_version 必须为正整数")
        if not isinstance(self.fields, tuple):
            object.__setattr__(self, "fields", tuple(self.fields))
        seen_keys: set[str] = set()
        for f in self.fields:
            if not isinstance(f, FilterField):
                raise TypeError("fields 必须为 FilterField 元组")
            if f.key in seen_keys:
                raise ValueError(f"schema 存在重复字段键: {f.key}")
            seen_keys.add(f.key)

    def field_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def get_field(self, key: str) -> FilterField | None:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "schema_version": self.schema_version,
            "enabled_for_new_tasks": self.enabled_for_new_tasks,
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "multiple": f.multiple,
                    "options": [{"value": o.value, "label": o.label} for o in f.options],
                }
                for f in self.fields
            ],
        }


@dataclass(frozen=True)
class CityEntry:
    """城市目录条目：规范名 + 标签 + 平台码 + 映射版本。"""
    name: str
    label: str
    platform_code: str
    mapping_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("CityEntry.name 必须为非空字符串")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("CityEntry.label 必须为非空字符串")
        if not isinstance(self.platform_code, str) or not self.platform_code:
            raise ValueError("CityEntry.platform_code 必须为非空字符串")
        if not isinstance(self.mapping_version, int) or self.mapping_version < 1:
            raise ValueError("mapping_version 必须为正整数")


@dataclass(frozen=True)
class PlatformCityCatalog:
    """平台城市目录：platform + mapping_version + entries。"""
    platform: str
    mapping_version: int
    entries: tuple[CityEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            object.__setattr__(self, "entries", tuple(self.entries))
        seen_names: set[str] = set()
        seen_codes: set[str] = set()
        for e in self.entries:
            if not isinstance(e, CityEntry):
                raise TypeError("entries 必须为 CityEntry 元组")
            if e.name in seen_names:
                raise ValueError(f"城市目录存在重复规范名: {e.name}")
            if e.platform_code in seen_codes:
                raise ValueError(f"城市目录存在重复平台码: {e.platform_code}")
            seen_names.add(e.name)
            seen_codes.add(e.platform_code)

    def find(self, name: str) -> CityEntry | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None

    def names(self) -> tuple[str, ...]:
        return tuple(e.name for e in self.entries)


@dataclass(frozen=True)
class LoginSpace:
    """平台登录空间：非敏感逻辑标识，不含 profile 绝对路径。"""
    platform: str
    browser_account: str
    profile_key: str
    cdp_port: int

    def __post_init__(self) -> None:
        if not isinstance(self.cdp_port, int) or self.cdp_port <= 0:
            raise ValueError("cdp_port 必须为正整数")
        if self.profile_key != f"{self.platform}:{self.browser_account}":
            raise ValueError(
                f"profile_key 必须为 '<platform>:<browser_account>'，"
                f"实际: {self.profile_key}"
            )


# ---------------------------------------------------------------------------
# URL 规范化（平台权威）
# ---------------------------------------------------------------------------

def _clean_url(raw: str) -> str:
    """剥离 query/fragment，返回 scheme+host+path 或空串。"""
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned)


def normalize_boss_job_url(raw: str) -> str:
    """BOSS 岗位链接规范化：仅 HTTPS + *.zhipin.com，剥离 query/fragment。"""
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme != "https":
        return ""
    host = (parsed.hostname or "").lower()
    if host not in _BOSS_ALLOWED_HOSTS and not host.endswith(".zhipin.com"):
        return ""
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned)


def normalize_zhilian_job_url(raw: str) -> str:
    """智联岗位链接规范化。

    允许 host 恰为 zhaopin.com 或 www.zhaopin.com；path 符合
    jobdetail/<id>.htm；http 升级为 https；移除 query/fragment。
    """
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if scheme == "http":
        parsed = parsed._replace(scheme="https")
    elif scheme != "https":
        return ""
    host = (parsed.hostname or "").lower()
    if host not in _ZHILIAN_ALLOWED_HOSTS:
        return ""
    path = parsed.path or ""
    # path 必须符合 jobdetail/<id>.htm（contracts/platform-schema.md）。
    if not _ZHILIAN_JOB_PATH_RE.match(path):
        return ""
    cleaned = parsed._replace(query="", fragment="", params="")
    return urlunparse(cleaned)


import re as _re
_ZHILIAN_JOB_PATH_RE = _re.compile(r"^/jobdetail/[A-Za-z0-9_-]+\.htm$")


# ---------------------------------------------------------------------------
# 登录空间解析
# ---------------------------------------------------------------------------

def resolve_boss_login_space(
    browser_account: str, *, boss_profile_dir: str,
) -> LoginSpace:
    """BOSS 登录空间：复用现有账号 profile_dir，端口 9222。"""
    if not browser_account:
        raise ValueError("browser_account 不能为空")
    if not boss_profile_dir:
        raise ValueError("boss_profile_dir 不能为空")
    return LoginSpace(
        platform="boss",
        browser_account=browser_account,
        profile_key=f"boss:{browser_account}",
        cdp_port=BOSS_DEFAULT_CDP_PORT,
    )


def resolve_zhilian_login_space(
    browser_account: str, *, boss_profile_dir: str,
) -> LoginSpace:
    """智联登录空间：profile_dir = boss_profile_dir + '.zhilian'，端口 9223。"""
    if not browser_account:
        raise ValueError("browser_account 不能为空")
    if not boss_profile_dir:
        raise ValueError("boss_profile_dir 不能为空")
    return LoginSpace(
        platform="zhilian",
        browser_account=browser_account,
        profile_key=f"zhilian:{browser_account}",
        cdp_port=ZHILIAN_DEFAULT_CDP_PORT,
    )


def _boss_profile_dir_differs_from_zhilian(boss_profile_dir: str) -> bool:
    """校验 BOSS profile_dir 与其 .zhilian 派生路径不同。"""
    if not boss_profile_dir:
        return False
    return str(boss_profile_dir).rstrip("/") + ".zhilian" != str(boss_profile_dir)


# ---------------------------------------------------------------------------
# 平台注册项
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlatformRegistry:
    """单个平台的完整注册项。

    source_factory 为可选 Callable，由 app.py 在启动时注入，避免
    platforms.py 反向依赖 source.py（adapter 实现模块）。注册表本身
    只持有平台能力描述，不持有 adapter 实例。
    """
    key: str
    display_name: str
    filter_schema: PlatformFilterSchema
    city_catalog: PlatformCityCatalog
    enabled_for_new_tasks: bool
    availability_reason: str
    default_cdp_port: int
    normalize_job_url_fn: Callable[[str], str]
    resolve_login_space_fn: Callable[..., LoginSpace]
    source_factory: Callable[..., Any] | None = None

    def normalize_job_url(self, raw: str) -> str:
        return self.normalize_job_url_fn(raw)

    def resolve_login_space(
        self, browser_account: str, *, boss_profile_dir: str,
    ) -> LoginSpace:
        return self.resolve_login_space_fn(
            browser_account, boss_profile_dir=boss_profile_dir,
        )


# ---------------------------------------------------------------------------
# 注册表存储
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, PlatformRegistry] = {}


def register_platform(registry: PlatformRegistry) -> None:
    """注册或替换一个平台注册项。"""
    if not isinstance(registry, PlatformRegistry):
        raise TypeError("registry 必须为 PlatformRegistry")
    if registry.key not in KNOWN_PLATFORM_KEYS:
        raise UnknownPlatformError(f"未知平台键: {registry.key}")
    _REGISTRY[registry.key] = registry


def is_known_platform_key(key: str | None) -> bool:
    """平台键是否在规格允许的已知集合中（不论是否已注册）。"""
    return key in KNOWN_PLATFORM_KEYS


def validate_platform_key(key: str | None) -> str:
    """校验平台键已知；返回规范化键，未知抛 UnknownPlatformError。"""
    if key is None or key == "":
        raise UnknownPlatformError("平台键不能为空")
    if not isinstance(key, str):
        raise UnknownPlatformError(f"平台键必须为字符串，实际类型: {type(key).__name__}")
    if key not in KNOWN_PLATFORM_KEYS:
        raise UnknownPlatformError(f"未知平台键: {key}")
    return key


def get_platform(key: str) -> PlatformRegistry:
    """获取已注册平台；未知抛 UnknownPlatformError，已知未注册抛 PlatformNotRegisteredError。"""
    validate_platform_key(key)
    reg = _REGISTRY.get(key)
    if reg is None:
        raise PlatformNotRegisteredError(
            f"平台 {key} 已知但尚未注册（真实 fixture/页面合同未核验）"
        )
    return reg


def get_platform_or_none(key: str) -> PlatformRegistry | None:
    """获取已注册平台；未知或未注册返回 None。"""
    if not is_known_platform_key(key):
        return None
    return _REGISTRY.get(key)


def list_platforms() -> tuple[PlatformRegistry, ...]:
    """列出全部已注册平台（按 KNOWN_PLATFORM_KEYS 顺序）。"""
    return tuple(
        _REGISTRY[k] for k in KNOWN_PLATFORM_KEYS if k in _REGISTRY
    )


def list_platform_keys() -> tuple[str, ...]:
    """列出全部已注册平台键（按 KNOWN_PLATFORM_KEYS 顺序）。"""
    return tuple(k for k in KNOWN_PLATFORM_KEYS if k in _REGISTRY)


def resolve_platform_or_default(key: str | None) -> str:
    """解析平台键；None/空 → DEFAULT_PLATFORM；未知抛 UnknownPlatformError。"""
    if key is None or key == "":
        return DEFAULT_PLATFORM
    return validate_platform_key(key)


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
# AI 筛选 schema 投影与快照
# ---------------------------------------------------------------------------

def project_filter_schema(platform: str) -> dict[str, Any]:
    """投影平台 AI 筛选 schema 为 API 响应（contracts/http-api.md /api/filter-labels）。"""
    reg = get_platform(platform)
    schema = reg.filter_schema
    return {
        "ok": True,
        "platform": schema.platform,
        "schema_version": schema.schema_version,
        "enabled_for_new_tasks": schema.enabled_for_new_tasks,
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "multiple": f.multiple,
                "options": [{"value": o.value, "label": o.label} for o in f.options],
            }
            for f in schema.fields
        ],
    }


def validate_filter_values(
    platform: str, *, schema_version: int, screening_fields: dict[str, Any],
) -> dict[str, list[str]]:
    """按平台 schema 校验 AI 筛选值。

    返回规范化后的 ``{field_key: [stable_value, ...]}``。跨平台字段或
    未知值抛 ValueError，调用方映射为 ``422 filter_validation_failed``。
    """
    reg = get_platform(platform)
    schema = reg.filter_schema
    if schema_version != schema.schema_version:
        raise ValueError(
            f"filter_schema_version_mismatch: 期望 {schema.schema_version}, "
            f"实际 {schema_version}"
        )
    if not isinstance(screening_fields, dict):
        raise ValueError("screening_fields 必须为对象")

    allowed_exclusive: tuple[str, ...] = ()
    if platform == "boss":
        allowed_exclusive = _BOSS_EXCLUSIVE_FIELDS
    elif platform == "zhilian":
        allowed_exclusive = _ZHILIAN_EXCLUSIVE_FIELDS

    normalized: dict[str, list[str]] = {}
    for key, raw_value in screening_fields.items():
        field = schema.get_field(key)
        if field is None:
            raise ValueError(f"filter_validation_failed: 平台 {platform} 不支持字段 {key}")
        # 跨平台专属字段检查（防御性：schema 已隔离，此处显式拒绝）。
        if key in _ZHILIAN_EXCLUSIVE_FIELDS and platform != "zhilian":
            raise ValueError(f"filter_validation_failed: 平台 {platform} 不支持字段 {key}")
        if key in _BOSS_EXCLUSIVE_FIELDS and platform != "boss":
            raise ValueError(f"filter_validation_failed: 平台 {platform} 不支持字段 {key}")
        values = _coerce_value_list(raw_value)
        if not field.multiple and len(values) > 1:
            raise ValueError(f"字段 {key} 不允许多选")
        valid_values = field.option_values()
        for v in values:
            if v not in valid_values:
                raise ValueError(
                    f"filter_validation_failed: 字段 {key} 值 {v!r} 不在平台 {platform} schema 中"
                )
        normalized[key] = values
    return normalized


def _coerce_value_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def build_filter_snapshot(
    platform: str, *, schema_version: int, screening_fields: dict[str, Any],
) -> dict[str, Any]:
    """构建完整冻结筛选快照：保存字段键、稳定值和当时标签。

    快照格式见 data-model.md ScreeningRun.filter_snapshot_json。
    """
    reg = get_platform(platform)
    schema = reg.filter_schema
    normalized = validate_filter_values(
        platform, schema_version=schema_version, screening_fields=screening_fields,
    )
    fields_snapshot: dict[str, Any] = {}
    for key, values in normalized.items():
        field = schema.get_field(key)
        labels = [field.label_for(v) or "" for v in values]
        fields_snapshot[key] = {"values": list(values), "labels": labels}
    return {
        "schema_version": schema.schema_version,
        "platform": platform,
        "fields": fields_snapshot,
    }


# ---------------------------------------------------------------------------
# BOSS 注册（tasks001 基线）
# ---------------------------------------------------------------------------

def _build_boss_filter_options(
    mapping: dict[str, str],
) -> tuple[FilterOption, ...]:
    """从 BOSS label→code 映射构建选项列表（label=中文标签，value=BOSS 稳定码）。

    BOSS 现有码值（如 "301"）即长期语义值，已被存量任务快照使用；
    本期不改写为另一套抽象值，避免破坏存量恢复（research.md 决策 4）。
    "不限" 的 BOSS 码为 "0"，是非空稳定值，保留为 option.value；
    是否把 "不限" 视作"不下推筛选"由调用方/前端在下推层处理，不在 schema 层
    改写为空串（platform-schema.md:85 要求 option.value 非空）。
    """
    options: list[FilterOption] = []
    seen_values: set[str] = set()
    # 保留 dict 插入顺序（BOSS 现有映射均为字面量 dict）。
    for label, value in mapping.items():
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append(FilterOption(value=str(value), label=str(label)))
    return tuple(options)


def _build_boss_filter_schema(
    filter_maps: dict[str, dict[str, str]],
) -> PlatformFilterSchema:
    """从 BOSS 现有 FILTER_MAPS 构建 PlatformFilterSchema。"""
    fields: list[FilterField] = []
    field_labels = {
        "salary": "薪资范围",
        "experience": "经验要求",
        "degree": "学历",
        "industry": "行业",
        "scale": "公司规模",
        "stage": "融资阶段",
    }
    for key in _BOSS_COMMON_FIELDS + _BOSS_EXCLUSIVE_FIELDS:
        mapping = filter_maps.get(key, {})
        options = _build_boss_filter_options(mapping)
        fields.append(FilterField(
            key=key,
            label=field_labels.get(key, key),
            multiple=True,
            options=options,
        ))
    return PlatformFilterSchema(
        platform="boss",
        schema_version=BOSS_FILTER_SCHEMA_VERSION,
        enabled_for_new_tasks=True,
        fields=tuple(fields),
    )


def _build_boss_city_catalog(
    city_map: dict[str, str], *, nationwide_name: str, nationwide_code: str,
) -> PlatformCityCatalog:
    """从 BOSS CITY_MAP 构建城市目录。"""
    entries: list[CityEntry] = [
        CityEntry(
            name=nationwide_name,
            label=nationwide_name,
            platform_code=nationwide_code,
            mapping_version=BOSS_CITY_MAPPING_VERSION,
        )
    ]
    seen_names = {nationwide_name}
    for name, code in city_map.items():
        if name in seen_names:
            continue
        seen_names.add(name)
        entries.append(CityEntry(
            name=str(name),
            label=str(name),
            platform_code=str(code),
            mapping_version=BOSS_CITY_MAPPING_VERSION,
        ))
    return PlatformCityCatalog(
        platform="boss",
        mapping_version=BOSS_CITY_MAPPING_VERSION,
        entries=tuple(entries),
    )


def _register_boss(
    filter_maps: dict[str, dict[str, str]],
    city_map: dict[str, str],
    *,
    nationwide_name: str,
    nationwide_code: str,
) -> PlatformRegistry:
    schema = _build_boss_filter_schema(filter_maps)
    catalog = _build_boss_city_catalog(
        city_map, nationwide_name=nationwide_name, nationwide_code=nationwide_code,
    )
    registry = PlatformRegistry(
        key="boss",
        display_name="BOSS直聘",
        filter_schema=schema,
        city_catalog=catalog,
        enabled_for_new_tasks=True,
        availability_reason="",
        default_cdp_port=BOSS_DEFAULT_CDP_PORT,
        normalize_job_url_fn=normalize_boss_job_url,
        resolve_login_space_fn=resolve_boss_login_space,
        source_factory=None,  # 由 app.py 在启动时注入（T006）
    )
    register_platform(registry)
    return registry


def boss_filter_schema(
    filter_maps: dict[str, dict[str, str]],
) -> PlatformFilterSchema:
    """测试与启动期辅助：从 BOSS FILTER_MAPS 构建 schema（不写入注册表）。"""
    return _build_boss_filter_schema(filter_maps)


def boss_city_catalog(
    city_map: dict[str, str], *, nationwide_name: str, nationwide_code: str,
) -> PlatformCityCatalog:
    """测试与启动期辅助：从 BOSS CITY_MAP 构建目录（不写入注册表）。"""
    return _build_boss_city_catalog(
        city_map, nationwide_name=nationwide_name, nationwide_code=nationwide_code,
    )


def _initialize_boss_from_runtime() -> None:
    """从 scripts.boss_cdp_raw 加载 BOSS 现有 FILTER_MAPS/CITY_MAP 并注册。

    延迟导入避免 platforms.py 在被 source.py/workbench.py 间接导入时
    强制加载 scraper 运行依赖。注册只在 boss_cdp_raw 可导入时发生；
    不可导入时（如纯单元测试）调用方需自行调用 register_boss_from_maps。
    """
    try:
        from scripts import boss_cdp_raw as boss  # noqa: WPS433 (延迟导入)
    except Exception:
        return
    filter_maps = {
        "scale": boss.SCALE_MAP,
        "stage": boss.STAGE_MAP,
        "salary": boss.SALARY_MAP,
        "experience": boss.EXPERIENCE_MAP,
        "degree": boss.DEGREE_MAP,
        "industry": boss.INDUSTRY_MAP,
    }
    nationwide_name = "全国"
    nationwide_code = ""
    # 从 city_codes.json 读取 nationwide code（与 boss.load_local_city_map 一致）。
    try:
        city_data_path = Path(__file__).resolve().parent.parent / "data" / "city_codes.json"
        raw = json.loads(city_data_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            nationwide = raw.get("nationwide")
            if isinstance(nationwide, dict):
                name = nationwide.get("name")
                code = nationwide.get("code")
                if isinstance(name, str) and isinstance(code, str):
                    nationwide_name = name
                    nationwide_code = code
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    _register_boss(
        filter_maps,
        dict(boss.CITY_MAP),
        nationwide_name=nationwide_name,
        nationwide_code=nationwide_code or "100010000",
    )


def register_boss_from_maps(
    filter_maps: dict[str, dict[str, str]],
    city_map: dict[str, str],
    *,
    nationwide_name: str = "全国",
    nationwide_code: str = "100010000",
) -> PlatformRegistry:
    """显式注册 BOSS（测试或自定义运行时数据用）。"""
    return _register_boss(
        filter_maps, city_map,
        nationwide_name=nationwide_name, nationwide_code=nationwide_code,
    )


#: BOSS 平台注册项（延迟注册后可用；未注册时为 None）。
BOSS_PLATFORM: PlatformRegistry | None = None


def _ensure_boss_registered() -> PlatformRegistry:
    """确保 BOSS 已注册；返回注册项。"""
    global BOSS_PLATFORM
    if "boss" in _REGISTRY:
        BOSS_PLATFORM = _REGISTRY["boss"]
        return BOSS_PLATFORM
    reg = _register_boss(
        _DEFAULT_BOSS_FILTER_MAPS,
        _DEFAULT_BOSS_CITY_MAP,
        nationwide_name="全国",
        nationwide_code="100010000",
    )
    BOSS_PLATFORM = reg
    return reg


# 启动时尝试从真实 boss_cdp_raw 注册；失败时保留空注册表，由调用方兜底。
_initialize_boss_from_runtime()


# 纯函数注册兜底用的最小 BOSS 映射（仅在 boss_cdp_raw 不可导入时使用，
# 保证 platforms.py 自身可被纯单元测试导入）。
_DEFAULT_BOSS_FILTER_MAPS: dict[str, dict[str, str]] = {
    "scale": {"0-20人": "301", "20-99人": "302"},
    "stage": {"未融资": "801", "已上市": "807"},
    "salary": {"不限": "0", "3K以下": "402"},
    "experience": {"不限": "0", "应届生": "102"},
    "degree": {"不限": "0", "本科": "203"},
    "industry": {"互联网": "1001"},
}
_DEFAULT_BOSS_CITY_MAP: dict[str, str] = {"上海": "101020100", "北京": "101010100"}
