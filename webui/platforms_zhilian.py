"""智联平台注册：冻结 schema/城市目录（021 B7 自 platforms.py 搬运）。

模块加载时调用 _register_zhilian()（与原文件一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

from webui.platforms_registry import register_platform
from webui import recruiter_activity
from webui.platforms_schema import (
    CityEntry,
    FilterField,
    FilterOption,
    PlatformCityCatalog,
    PlatformFilterSchema,
    PlatformRegistry,
    ZHILIAN_CITY_MAPPING_VERSION,
    ZHILIAN_DEFAULT_CDP_PORT,
    ZHILIAN_FILTER_SCHEMA_VERSION,
)
from webui.platforms_urls import (
    normalize_zhilian_job_url,
    resolve_zhilian_login_space,
)




# ---------------------------------------------------------------------------
# 智联注册（tasks003 T204-T206）
# ---------------------------------------------------------------------------

# 智联 AI 筛选字段顺序（contracts/platform-schema.md 字段集合表）。
_ZHILIAN_FILTER_FIELDS: tuple[str, ...] = (
    "salary", "experience", "degree", "industry", "scale", "company_nature",
    "recruiter_activity",
)



_ZHILIAN_FIELD_LABELS: dict[str, str] = {
    "salary": "薪资范围",
    "experience": "经验要求",
    "degree": "学历",
    "industry": "行业",
    "scale": "公司规模",
    "company_nature": "公司性质",
    "recruiter_activity": "招聘者上次活跃",
}



#: 智联 AI 筛选选项（2026-08-04 fe-api.zhaopin.com/c/i/search/base/data 核验）。
_ZHILIAN_FIELD_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "salary": (
        ("0000,9999999", "不限"),
        ("0000,4000", "4K以下"),
        ("4001,6000", "4K-6K"),
        ("6001,8000", "6K-8K"),
        ("8001,10000", "8K-10K"),
        ("10001,15000", "10K-15K"),
        ("15001,25000", "15K-25K"),
        ("25001,35000", "25K-35K"),
        ("35001,50000", "35K-50K"),
        ("50001,9999999", "50K以上"),
    ),
    "experience": (
        ("-99", "全部"),
        ("-1", "经验不限"),
        ("0001", "1年以下"),
        ("0103", "1-3年"),
        ("0305", "3-5年"),
        ("0510", "5-10年"),
        ("1099", "10年以上"),
    ),
    "degree": (
        ("-1", "不限"),
        ("9", "初中及以下"),
        ("7", "高中"),
        ("12", "中专/中技"),
        ("5", "大专"),
        ("4", "本科"),
        ("3", "硕士"),
        ("10", "MBA/EMBA"),
        ("1", "博士"),
    ),
    "industry": (
        ("-1", "不限"),
        ("1600000000", "汽车/摩托车/电动车"),
        ("1300000000", "生物/制药/医疗/医美"),
        ("1700000000", "电子/通信/半导体"),
        ("1800000000", "互联网/AI/软件/IT服务"),
        ("1900000000", "新能源/环保/能源供应"),
        ("2000000000", "石油/矿产/化工/材料"),
        ("400000000", "房地产/建筑/工程"),
        ("600000000", "农/林/牧/渔"),
        ("1400000000", "政府/非盈利机构"),
        ("1200000000", "教育/培训/科研"),
        ("300000000", "金融业"),
        ("900000000", "广告/传媒/文化/体育"),
        ("1000000000", "交通/运输/仓储/物流"),
        ("800000000", "专业服务"),
        ("1500000000", "生活服务"),
        ("500000000", "制造业"),
        ("700000000", "批发/零售/贸易"),
    ),
    "scale": (
        ("-1", "不限"),
        ("1", "20人以下"),
        ("2", "20-99人"),
        ("3", "100-299人"),
        ("8", "300-499人"),
        ("4", "500-999人"),
        ("5", "1000-9999人"),
        ("6", "10000人以上"),
    ),
    "company_nature": (
        ("1", "国企"),
        ("2;3", "外企"),
        ("4", "合资"),
        ("5", "民营"),
        ("9", "上市公司"),
        ("8", "股份制企业"),
        ("6;10", "事业单位"),
        ("11;12;13;14;15;16;7", "其他"),
    ),
}



#: 智联全国城市码（冻结设计决策，platform-schema.md L176）。
ZHILIAN_NATIONWIDE_CODE = "jl0"


ZHILIAN_NATIONWIDE_NAME = "全国"



#: 智联禁用原因（真实页面核验后置空）。
ZHILIAN_AVAILABILITY_REASON = (
    "company_nature options / non-nationwide city codes / page markers "
    "未由当前真实页面核验"
)




def _build_zhilian_filter_schema() -> PlatformFilterSchema:
    """构建智联 AI 筛选 schema（2026-08-04 真实元数据核验后启用）。"""
    fields: list[FilterField] = []
    for key in _ZHILIAN_FILTER_FIELDS:
        # 028 第 7 类：平台无关、单选、档位 options 由判定域统一供给，
        # 不走平台码映射（智联搜索不支持按招聘者活跃过滤，纯本地判定）。
        if key == recruiter_activity.FIELD_KEY:
            fields.append(FilterField(
                key=key,
                label=_ZHILIAN_FIELD_LABELS[key],
                multiple=False,
                options=tuple(
                    FilterOption(value=value, label=label)
                    for value, label in recruiter_activity.filter_option_pairs()
                ),
            ))
            continue
        options = tuple(
            FilterOption(value=value, label=label)
            for value, label in _ZHILIAN_FIELD_OPTIONS.get(key, ())
        )
        fields.append(FilterField(
            key=key,
            label=_ZHILIAN_FIELD_LABELS.get(key, key),
            multiple=True,
            options=options,
        ))
    return PlatformFilterSchema(
        platform="zhilian",
        schema_version=ZHILIAN_FILTER_SCHEMA_VERSION,
        enabled_for_new_tasks=True,
        fields=tuple(fields),
    )




def _build_zhilian_city_catalog() -> PlatformCityCatalog:
    """构建智联城市目录（2026-08-04 真实元数据核验）。"""
    entries: list[CityEntry] = [
        CityEntry(
            name=ZHILIAN_NATIONWIDE_NAME,
            label=ZHILIAN_NATIONWIDE_NAME,
            platform_code=ZHILIAN_NATIONWIDE_CODE,
            mapping_version=ZHILIAN_CITY_MAPPING_VERSION,
        ),
    ]
    city_path = Path(__file__).resolve().parent.parent / "data" / "zhilian_city_codes.json"
    try:
        raw = json.loads(city_path.read_text(encoding="utf-8"))
        for item in raw.get("cities", []) or []:
            name = str(item.get("name") or "").strip()
            code = str(item.get("code") or "").strip()
            if not name or not code or name == ZHILIAN_NATIONWIDE_NAME:
                continue
            entries.append(CityEntry(
                name=name,
                label=str(item.get("label") or name),
                platform_code=code,
                mapping_version=ZHILIAN_CITY_MAPPING_VERSION,
            ))
    except (OSError, ValueError):
        pass
    return PlatformCityCatalog(
        platform="zhilian",
        mapping_version=ZHILIAN_CITY_MAPPING_VERSION,
        entries=tuple(entries),
    )




def _register_zhilian() -> PlatformRegistry:
    """注册智联平台（2026-08-04 外部事实核验后启用）。"""
    schema = _build_zhilian_filter_schema()
    catalog = _build_zhilian_city_catalog()
    enabled = bool(all(f.options for f in schema.fields) and catalog.entries)
    registry = PlatformRegistry(
        key="zhilian",
        display_name="智联招聘",
        filter_schema=schema,
        city_catalog=catalog,
        enabled_for_new_tasks=enabled,
        availability_reason="" if enabled else ZHILIAN_AVAILABILITY_REASON,
        default_cdp_port=ZHILIAN_DEFAULT_CDP_PORT,
        normalize_job_url_fn=normalize_zhilian_job_url,
        resolve_login_space_fn=resolve_zhilian_login_space,
        source_factory=None,  # 由 app.py 在启动时注入（tasks004+）
    )
    register_platform(registry)
    return registry




# 模块加载时注册智联（不依赖外部运行时数据，直接使用冻结值）。
_register_zhilian()
