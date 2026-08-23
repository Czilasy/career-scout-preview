"""平台常量、异常族与注册项/筛选 schema 数据类（021 B7 自 platforms.py 搬运）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any




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



#: 智联 AI 筛选 schema 版本。字段集合或稳定值/标签核验后变化时递增。
ZHILIAN_FILTER_SCHEMA_VERSION = 2



#: 智联城市目录映射版本。城市码集合变化时递增。
ZHILIAN_CITY_MAPPING_VERSION = 2



# BOSS AI 筛选公共字段顺序（contracts/platform-schema.md 字段集合表）。
_BOSS_COMMON_FIELDS: tuple[str, ...] = (
    "salary", "experience", "degree", "industry", "scale",
)


_BOSS_EXCLUSIVE_FIELDS: tuple[str, ...] = ("stage",)


_ZHILIAN_EXCLUSIVE_FIELDS: tuple[str, ...] = ("company_nature",)



# BOSS URL allowlist（保持与现有 webui/workbench.normalize_job_link 一致）。
_BOSS_ALLOWED_HOSTS = frozenset({"www.zhipin.com", "zhipin.com"})



# 智联 URL allowlist（2026-08-04 真实页面核验：jobs.zhaopin.com 为官方详情源站）。
_ZHILIAN_ALLOWED_HOSTS = frozenset({
    "zhaopin.com", "www.zhaopin.com", "jobs.zhaopin.com", "m.zhaopin.com",
})




# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

_MSG_BROWSER_ACCOUNT_REQUIRED = "browser_account 不能为空"


_MSG_BOSS_PROFILE_DIR_REQUIRED = "boss_profile_dir 不能为空"




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
