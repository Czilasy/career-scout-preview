"""BOSS 平台注册：schema/城市目录构建与运行时初始化（021 B7 自 platforms.py 搬运）。

模块加载时调用 _initialize_boss_from_runtime()（与原文件一致）；
BOSS_PLATFORM 为本模块延迟赋值的全局，经门面 re-export。
"""

from __future__ import annotations

import json
from pathlib import Path

from webui.platforms_registry import register_platform
from webui import recruiter_activity
from webui.platforms_schema import (
    _BOSS_COMMON_FIELDS,
    _BOSS_EXCLUSIVE_FIELDS,
    BOSS_CITY_MAPPING_VERSION,
    BOSS_DEFAULT_CDP_PORT,
    BOSS_FILTER_SCHEMA_VERSION,
    CityEntry,
    FilterField,
    FilterOption,
    PlatformCityCatalog,
    PlatformFilterSchema,
    PlatformRegistry,
)
from webui.platforms_urls import (
    normalize_boss_job_url,
    resolve_boss_login_space,
)




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
        "recruiter_activity": "招聘者上次活跃",
    }
    for key in _BOSS_COMMON_FIELDS + _BOSS_EXCLUSIVE_FIELDS:
        # 028 第 7 类：平台无关、单选、档位 options 由判定域统一供给，
        # 不走平台码映射（BOSS 搜索不支持按招聘者活跃过滤，纯本地判定）。
        if key == recruiter_activity.FIELD_KEY:
            fields.append(FilterField(
                key=key,
                label=field_labels[key],
                multiple=False,
                options=tuple(
                    FilterOption(value=value, label=label)
                    for value, label in recruiter_activity.filter_option_pairs()
                ),
            ))
            continue
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
    except (OSError, ValueError):
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
    from webui import platforms as _facade
    global BOSS_PLATFORM
    if "boss" in _facade._REGISTRY:
        BOSS_PLATFORM = _facade._REGISTRY["boss"]
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
