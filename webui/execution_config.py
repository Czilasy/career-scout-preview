"""SPEC011 T002 — 共享执行配置 schema、规范化、校验与不可变快照。

提供所有调用方唯一的配置语义，消除全局 JSON 晚绑定读取。

核心值对象:
- ExecutionConfigSnapshot: 10 个速度字段（含 JD 并发 Tab 数）+ schema_version + config_digest，不可变
- FrozenTaskScope: 关键词/城市/页数/规模 + scope_digest，不可变

规范化函数:
- normalize_keywords: FR-001/FR-002 关键词标准化与去重
- validate_cities: FR-003/FR-004 城市校验与别名统一
- normalize_scope: FR-005~FR-008 范围规范化与任务规模分类
- preview_scope: 后端权威预览接口

pages 不属于 ExecutionConfigSnapshot。
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import re
import unicodedata
from pathlib import Path
from typing import Any

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "ExecutionConfigSnapshot",
    "FrozenTaskScope",
    "CityValidationError",
    "normalize_keywords",
    "validate_cities",
    "normalize_scope",
    "preview_scope",
    "get_mode_config",
    "classify_task_size",
    "SPEED_FIELDS",
    "DEFAULT_DETAIL_TAB_POOL_SIZE",
]


CONFIG_SCHEMA_VERSION = 1
SCOPE_SCHEMA_VERSION = 1
SPEED_FIELDS: tuple[str, ...] = (
    "inter_combo_delay",
    "detail_batch_size",
    "detail_interval",
    "detail_reset_every",
    "detail_batch_cooldown",
    "detail_tab_pool_size",
    "screen_batch_size",
    "screen_concurrency",
    "match_batch_size",
    "match_concurrency",
)

DEFAULT_DETAIL_TAB_POOL_SIZE = 5

_MIN_PLANNED_PAGES = 1
_MAX_PLANNED_PAGES = 30
_SMALL_MAX = 9
_MEDIUM_MAX = 19
_LARGE_MAX = 30


# ---------------------------------------------------------------------------
# 城市码表加载 — 兼容旧扁平格式和新结构化格式
# ---------------------------------------------------------------------------
_CITY_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "city_codes.json"

_city_registry_cache: dict[str, Any] | None = None


def _load_city_registry() -> dict[str, Any]:
    """加载城市码表，兼容旧扁平 {name: code} 和新结构化格式。

    返回结构:
    {
        "name_to_code": {canonical_name: code},
        "code_to_name": {code: canonical_name},
        "aliases": {alias: canonical_name},
        "disabled": set(canonical_name),
        "nationwide_code": str | None,
    }
    """
    global _city_registry_cache
    if _city_registry_cache is not None:
        return _city_registry_cache

    registry: dict[str, Any] = {
        "name_to_code": {},
        "code_to_name": {},
        "aliases": {},
        "disabled": set(),
        "nationwide_code": None,
    }

    try:
        raw = json.loads(_CITY_DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _city_registry_cache = registry
        return registry

    if isinstance(raw, dict) and "cities" in raw:
        # 新结构化格式
        for entry in raw.get("cities", []):
            name = entry.get("name")
            code = entry.get("code")
            if not name or not code:
                continue
            if entry.get("enabled", True):
                registry["name_to_code"][name] = code
                registry["code_to_name"][code] = name
            else:
                registry["disabled"].add(name)
            for alias in entry.get("aliases", []):
                registry["aliases"][alias] = name
        nationwide = raw.get("nationwide")
        if isinstance(nationwide, dict):
            registry["nationwide_code"] = nationwide.get("code")
    elif isinstance(raw, dict):
        # 旧扁平格式 {name: code}
        for name, code in raw.items():
            if name == "全国":
                registry["nationwide_code"] = code
            else:
                registry["name_to_code"][name] = code
                registry["code_to_name"][code] = name

    _city_registry_cache = registry
    return registry


def _reset_city_registry_cache() -> None:
    """测试用：重置城市注册表缓存。"""
    global _city_registry_cache
    _city_registry_cache = None


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class CityValidationError(ValueError):
    """城市校验失败，包含结构化建议。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


# ---------------------------------------------------------------------------
# 类型校验辅助
# ---------------------------------------------------------------------------
def _validate_decimal(value: Any, *, min_value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError("值必须是有限数字")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("值必须是有限数字")
    if v < min_value:
        raise ValueError(f"值 {v} 小于最小允许值 {min_value}")
    return v


def _validate_int(value: Any, *, min_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError("值必须是整数")
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError("值必须是整数")
    v = int(numeric)
    if v < min_value:
        raise ValueError(f"值 {v} 小于最小允许值 {min_value}")
    return v


# ---------------------------------------------------------------------------
# ExecutionConfigSnapshot — 不可变执行配置快照
# ---------------------------------------------------------------------------
class ExecutionConfigSnapshot:
    """10 个速度字段的不可变快照，带规范 JSON 和 SHA-256 摘要。

    pages 不属于此对象。
    """

    __slots__ = (
        "inter_combo_delay",
        "detail_batch_size",
        "detail_interval",
        "detail_reset_every",
        "detail_batch_cooldown",
        "detail_tab_pool_size",
        "screen_batch_size",
        "screen_concurrency",
        "match_batch_size",
        "match_concurrency",
        "schema_version",
        "_config_digest",
    )

    def __init__(self, fields: dict[str, Any], *, config_digest: str | None = None):
        validated = self._validate_fields(fields)
        for name, value in validated.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", CONFIG_SCHEMA_VERSION)
        digest = config_digest if config_digest is not None else self._compute_digest()
        object.__setattr__(self, "_config_digest", digest)

    @staticmethod
    def _validate_fields(fields: dict[str, Any]) -> dict[str, Any]:
        values = dict(fields)
        # 兼容旧 9 字段配置：JD 并发 Tab 数缺省时使用默认值。
        values.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        validated: dict[str, Any] = {}
        for field_name in SPEED_FIELDS:
            if field_name not in values:
                raise ValueError(f"缺少必填字段: {field_name}")
        validators = {
            "inter_combo_delay": lambda v: _validate_decimal(v, min_value=0),
            "detail_batch_size": lambda v: _validate_int(v, min_value=1),
            "detail_interval": lambda v: _validate_decimal(v, min_value=0),
            "detail_reset_every": lambda v: _validate_int(v, min_value=1),
            "detail_batch_cooldown": lambda v: _validate_decimal(v, min_value=0),
            "detail_tab_pool_size": lambda v: _validate_int(v, min_value=1),
            "screen_batch_size": lambda v: _validate_int(v, min_value=1),
            "screen_concurrency": lambda v: _validate_int(v, min_value=1),
            "match_batch_size": lambda v: _validate_int(v, min_value=1),
            "match_concurrency": lambda v: _validate_int(v, min_value=1),
        }
        for field_name, validator in validators.items():
            validated[field_name] = validator(values[field_name])
        if validated["detail_tab_pool_size"] > 10:
            raise ValueError("detail_tab_pool_size 必须介于 1 和 10 之间")
        return validated

    def _compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def create(cls, values: dict[str, Any]) -> "ExecutionConfigSnapshot":
        """从原始字段值创建快照，执行物理校验。"""
        return cls(values)

    @property
    def config_digest(self) -> str:
        return self._config_digest

    def canonical_json(self) -> str:
        """规范 JSON，不含 config_digest 字段，键按固定顺序排列。"""
        payload: dict[str, Any] = {"schema_version": CONFIG_SCHEMA_VERSION}
        for field_name in SPEED_FIELDS:
            payload[field_name] = getattr(self, field_name)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"schema_version": self.schema_version}
        for field_name in SPEED_FIELDS:
            result[field_name] = getattr(self, field_name)
        result["config_digest"] = self._config_digest
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionConfigSnapshot":
        """从字典恢复快照，校验摘要一致性。"""
        fields: dict[str, Any] = {}
        for field_name in SPEED_FIELDS:
            if field_name not in data:
                if field_name == "detail_tab_pool_size":
                    fields[field_name] = DEFAULT_DETAIL_TAB_POOL_SIZE
                    continue
                raise ValueError(f"缺少必填字段: {field_name}")
            fields[field_name] = data[field_name]
        expected_digest = data.get("config_digest")
        snapshot = cls(fields, config_digest=None)
        actual_digest = snapshot._config_digest
        if expected_digest is not None and expected_digest != actual_digest:
            raise ValueError(
                f"config_digest 不匹配: 期望 {expected_digest}, 实际 {actual_digest}"
            )
        return snapshot

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"ExecutionConfigSnapshot 是不可变的，不能设置 {name}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"ExecutionConfigSnapshot 是不可变的，不能删除 {name}")

    def __repr__(self) -> str:
        return f"ExecutionConfigSnapshot(digest={self._config_digest[:12]}...)"


# ---------------------------------------------------------------------------
# 关键词标准化 (FR-001, FR-002)
# ---------------------------------------------------------------------------
def normalize_keywords(keywords: list[str] | None) -> list[str]:
    """FR-001: 标准化关键词 — 清除首尾空白、合并无意义连续空格、英文不区分大小写去重。

    FR-002: 含义相近但文字不同的关键词不自动合并。
    """
    if not keywords:
        return []

    normalized: list[str] = []
    seen_lower: set[str] = set()

    for raw in keywords:
        if raw is None:
            continue
        text = unicodedata.normalize("NFKC", str(raw)).strip()
        if not text:
            continue
        # 合并连续空格
        text = re.sub(r"\s+", " ", text)
        lower_key = text.lower()
        if lower_key in seen_lower:
            continue
        seen_lower.add(lower_key)
        normalized.append(text)

    return normalized


# ---------------------------------------------------------------------------
# 城市校验 (FR-003, FR-004)
# ---------------------------------------------------------------------------
def validate_cities(cities: list[str] | None) -> list[str]:
    """FR-003/FR-004: 校验城市，统一别名为正式名称，去重。

    未知城市抛出 CityValidationError 并提供建议；不自动替换。
    """
    if not cities:
        return []

    registry = _load_city_registry()
    result: list[str] = []
    seen: set[str] = set()

    for raw in cities:
        if not raw:
            continue
        city = str(raw).strip()
        if not city:
            continue

        # 别名统一
        canonical = registry["aliases"].get(city, city)

        if canonical in registry["name_to_code"]:
            if canonical not in seen:
                seen.add(canonical)
                result.append(canonical)
        elif canonical in registry["disabled"]:
            raise CityValidationError(
                f"城市 {city} 当前已禁用",
                details={"city": city, "suggestions": []},
            )
        else:
            # 未知城市 — 提供建议但不自动替换
            suggestions = _suggest_cities(city, registry)
            raise CityValidationError(
                f"未知城市: {city}",
                details={"city": city, "suggestions": suggestions},
            )

    return result


def _suggest_cities(input_city: str, registry: dict[str, Any]) -> list[str]:
    """为未知城市提供可执行建议（基于前缀/包含匹配）。"""
    known = list(registry["name_to_code"].keys())
    suggestions: list[str] = []
    # 前缀匹配
    for name in known:
        if name.startswith(input_city) or input_city.startswith(name):
            suggestions.append(name)
        if len(suggestions) >= 5:
            break
    # 包含匹配
    if len(suggestions) < 5:
        for name in known:
            if name not in suggestions and (input_city in name or name in input_city):
                suggestions.append(name)
            if len(suggestions) >= 5:
                break
    return suggestions[:5]


# ---------------------------------------------------------------------------
# 任务规模分类 (FR-006, FR-007)
# ---------------------------------------------------------------------------
def classify_task_size(planned_pages: int) -> str:
    """FR-007: 1-9=small, 10-19=medium, 20-30=large。"""
    if planned_pages < _MIN_PLANNED_PAGES:
        raise ValueError(f"计划页数 {planned_pages} 低于最小值 {_MIN_PLANNED_PAGES}")
    if planned_pages > _MAX_PLANNED_PAGES:
        raise ValueError(f"计划页数 {planned_pages} 超过最大值 {_MAX_PLANNED_PAGES}")
    if planned_pages <= _SMALL_MAX:
        return "small"
    if planned_pages <= _MEDIUM_MAX:
        return "medium"
    return "large"


# ---------------------------------------------------------------------------
# FrozenTaskScope — 不可变任务范围快照
# ---------------------------------------------------------------------------
class FrozenTaskScope:
    """冻结的任务范围，包含规范化关键词、城市、页数、规模和摘要。

    nationwide 与 cities 互斥；任务开始后不可修改。
    """

    __slots__ = (
        "keywords",
        "scope_kind",
        "cities",
        "pages_per_combination",
        "combination_count",
        "planned_pages",
        "task_size",
        "schema_version",
        "_scope_digest",
    )

    def __init__(
        self,
        *,
        keywords: list[str],
        scope_kind: str,
        cities: list[str],
        pages_per_combination: int,
        combination_count: int,
        planned_pages: int,
        task_size: str,
        scope_digest: str | None = None,
    ):
        object.__setattr__(self, "keywords", tuple(keywords))
        object.__setattr__(self, "scope_kind", scope_kind)
        object.__setattr__(self, "cities", tuple(cities))
        object.__setattr__(self, "pages_per_combination", pages_per_combination)
        object.__setattr__(self, "combination_count", combination_count)
        object.__setattr__(self, "planned_pages", planned_pages)
        object.__setattr__(self, "task_size", task_size)
        object.__setattr__(self, "schema_version", SCOPE_SCHEMA_VERSION)
        digest = scope_digest if scope_digest is not None else self._compute_digest()
        object.__setattr__(self, "_scope_digest", digest)

    def _compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def scope_digest(self) -> str:
        return self._scope_digest

    def canonical_json(self) -> str:
        """规范 JSON，不含 scope_digest 字段。"""
        payload: dict[str, Any] = {
            "schema_version": SCOPE_SCHEMA_VERSION,
            "keywords": list(self.keywords),
            "scope_kind": self.scope_kind,
            "cities": list(self.cities),
            "pages_per_combination": self.pages_per_combination,
            "combination_count": self.combination_count,
            "planned_pages": self.planned_pages,
            "task_size": self.task_size,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "keywords": list(self.keywords),
            "scope_kind": self.scope_kind,
            "cities": list(self.cities),
            "pages_per_combination": self.pages_per_combination,
            "combination_count": self.combination_count,
            "planned_pages": self.planned_pages,
            "task_size": self.task_size,
            "scope_digest": self._scope_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrozenTaskScope":
        expected_digest = data.get("scope_digest")
        scope = cls(
            keywords=list(data.get("keywords", [])),
            scope_kind=data["scope_kind"],
            cities=list(data.get("cities", [])),
            pages_per_combination=data["pages_per_combination"],
            combination_count=data["combination_count"],
            planned_pages=data["planned_pages"],
            task_size=data["task_size"],
            scope_digest=None,
        )
        if expected_digest is not None and expected_digest != scope._scope_digest:
            raise ValueError(
                f"scope_digest 不匹配: 期望 {expected_digest}, 实际 {scope._scope_digest}"
            )
        return scope

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"FrozenTaskScope 是不可变的，不能设置 {name}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"FrozenTaskScope 是不可变的，不能删除 {name}")

    def __repr__(self) -> str:
        return f"FrozenTaskScope(digest={self._scope_digest[:12]}...)"


# ---------------------------------------------------------------------------
# 范围规范化 (FR-005, FR-006, FR-007, FR-008)
# ---------------------------------------------------------------------------
def normalize_scope(
    *,
    keywords: list[str] | None,
    scope_kind: str,
    cities: list[str] | None,
    pages_per_combination: int,
) -> FrozenTaskScope:
    """规范化任务范围，生成不可变 FrozenTaskScope。

    FR-005: nationwide 与 cities 互斥。
    FR-006: planned_pages = keyword_count × scope_count × pages_per_combination。
    FR-007: 1-9=small, 10-19=medium, 20-30=large。
    FR-008: 任务开始前完成分类。
    """
    if scope_kind not in ("cities", "nationwide"):
        raise ValueError(f"无效 scope_kind: {scope_kind}")

    if pages_per_combination < 1:
        raise ValueError(f"pages_per_combination 必须 >= 1, 实际 {pages_per_combination}")

    norm_keywords = normalize_keywords(keywords)
    if not norm_keywords:
        raise ValueError("关键词不能为空")

    if scope_kind == "nationwide":
        if cities:
            raise ValueError("全国范围不能与具体城市同时选择")
        scope_count = 1
        norm_cities: list[str] = []
    else:
        norm_cities = validate_cities(cities)
        if not norm_cities:
            raise ValueError("城市范围不能为空")
        scope_count = len(norm_cities)

    combination_count = len(norm_keywords) * scope_count
    planned_pages = combination_count * pages_per_combination

    if planned_pages < _MIN_PLANNED_PAGES:
        raise ValueError(f"计划页数 {planned_pages} 低于最小值 {_MIN_PLANNED_PAGES}")
    if planned_pages > _MAX_PLANNED_PAGES:
        raise ValueError(f"计划页数 {planned_pages} 超过最大值 {_MAX_PLANNED_PAGES}")

    task_size = classify_task_size(planned_pages)

    return FrozenTaskScope(
        keywords=norm_keywords,
        scope_kind=scope_kind,
        cities=norm_cities,
        pages_per_combination=pages_per_combination,
        combination_count=combination_count,
        planned_pages=planned_pages,
        task_size=task_size,
    )


# ---------------------------------------------------------------------------
# 预览接口 (后端权威)
# ---------------------------------------------------------------------------
def preview_scope(
    *,
    keywords: list[str] | None,
    scope_kind: str,
    cities: list[str] | None,
    pages_per_combination: int,
) -> dict[str, Any]:
    """后端权威预览，返回规范化 scope 和去重信息。

    对应 HTTP API POST /api/search-scope/preview。
    """
    # 先记录原始输入用于 deduplicated 信息
    raw_keywords = [str(k).strip().lower() for k in (keywords or []) if k and str(k).strip()]
    raw_cities = [str(c).strip() for c in (cities or []) if c and str(c).strip()]

    scope = normalize_scope(
        keywords=keywords,
        scope_kind=scope_kind,
        cities=cities,
        pages_per_combination=pages_per_combination,
    )

    dedup_keywords = [k for k in raw_keywords if k not in [kw.lower() for kw in scope.keywords]]
    dedup_cities = [c for c in raw_cities if c not in scope.cities]

    return {
        "scope": {
            "keywords": list(scope.keywords),
            "scope_kind": scope.scope_kind,
            "cities": list(scope.cities),
            "pages_per_combination": scope.pages_per_combination,
            "combination_count": scope.combination_count,
            "planned_pages": scope.planned_pages,
            "task_size": scope.task_size,
            "scope_digest": scope.scope_digest,
        },
        "deduplicated": {
            "keywords": dedup_keywords,
            "cities": dedup_cities,
        },
    }


# ---------------------------------------------------------------------------
# 模式配置 (FR-051, FR-056, FR-009)
# ---------------------------------------------------------------------------
# 默认模式配置 — T007/T008 会将此迁移到 SQLite
# 这些是参考值，不是已验证结论；pages 不出现在模式配置中
_MODE_CONFIGS: dict[str, dict[str, dict[str, Any]]] = {
    "stable": {
        "small": {
            "inter_combo_delay": 30.0,
            "detail_batch_size": 5,
            "detail_interval": 5.0,
            "detail_reset_every": 2,
            "detail_batch_cooldown": 10.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 20,
            "screen_concurrency": 2,
            "match_batch_size": 2,
            "match_concurrency": 3,
        },
        "medium": {
            "inter_combo_delay": 25.0,
            "detail_batch_size": 8,
            "detail_interval": 4.0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 8.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 30,
            "screen_concurrency": 3,
            "match_batch_size": 3,
            "match_concurrency": 4,
        },
        "large": {
            "inter_combo_delay": 20.0,
            "detail_batch_size": 10,
            "detail_interval": 3.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 6.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 40,
            "screen_concurrency": 3,
            "match_batch_size": 4,
            "match_concurrency": 5,
        },
    },
    "balanced": {
        "small": {
            "inter_combo_delay": 15.0,
            "detail_batch_size": 10,
            "detail_interval": 3.0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 5.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 40,
            "screen_concurrency": 4,
            "match_batch_size": 3,
            "match_concurrency": 6,
        },
        "medium": {
            "inter_combo_delay": 12.0,
            "detail_batch_size": 12,
            "detail_interval": 2.5,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 4.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 8,
        },
        "large": {
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 5,
            "detail_batch_cooldown": 3.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 60,
            "screen_concurrency": 5,
            "match_batch_size": 5,
            "match_concurrency": 10,
        },
    },
    "extreme": {
        "small": {
            "inter_combo_delay": 8.0,
            "detail_batch_size": 15,
            "detail_interval": 1.5,
            "detail_reset_every": 5,
            "detail_batch_cooldown": 2.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 60,
            "screen_concurrency": 6,
            "match_batch_size": 5,
            "match_concurrency": 10,
        },
        "medium": {
            "inter_combo_delay": 6.0,
            "detail_batch_size": 20,
            "detail_interval": 1.0,
            "detail_reset_every": 6,
            "detail_batch_cooldown": 1.5,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 80,
            "screen_concurrency": 8,
            "match_batch_size": 6,
            "match_concurrency": 12,
        },
        "large": {
            "inter_combo_delay": 5.0,
            "detail_batch_size": 25,
            "detail_interval": 0.8,
            "detail_reset_every": 8,
            "detail_batch_cooldown": 1.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 100,
            "screen_concurrency": 10,
            "match_batch_size": 8,
            "match_concurrency": 15,
        },
    },
}


def get_mode_config(mode: str, *, task_size: str) -> ExecutionConfigSnapshot:
    """FR-051/FR-056: 获取指定模式和规模的配置快照。

    FR-009: pages 不出现在返回结果中。
    """
    if mode not in _MODE_CONFIGS:
        raise ValueError(f"未知模式: {mode}")
    if task_size not in ("small", "medium", "large"):
        raise ValueError(f"未知任务规模: {task_size}")
    config_values = _MODE_CONFIGS[mode][task_size]
    return ExecutionConfigSnapshot.create(config_values)
