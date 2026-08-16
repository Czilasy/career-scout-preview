"""B054 地点范围：规范化、组合展开、脚本参数翻译与展示摘要。

只依赖 location_catalog 与 platforms；不反向依赖 app/pipeline。
"""
from __future__ import annotations

from typing import Any

_KNOWN_PLATFORMS = ("boss", "zhilian")


def _validate_platform(platform: str) -> str:
    if platform not in _KNOWN_PLATFORMS:
        raise ValueError(f"未知平台键: {platform}")
    return platform


def _split_keywords(keyword: str) -> list[str]:
    if not keyword:
        return []
    parts = str(keyword).replace("，", ",").split(",")
    return [part.strip() for part in parts if part.strip()]


def _split_cities(cities: Any) -> list[str]:
    if isinstance(cities, str):
        return [
            part.strip()
            for part in cities.replace("，", ",").split(",")
            if part.strip()
        ]
    return [str(city).strip() for city in (cities or []) if str(city).strip()]


def _norm(value: Any) -> str:
    return str(value or "").strip()


def location_label(location: dict) -> str:
    """生成“城市 · 区”或“城市 · 区 · 商圈/镇”展示文本。"""
    parts = [
        _norm(location.get("city_name") or location.get("city") or ""),
        _norm(location.get("district_name") or ""),
    ]
    business = _norm(location.get("business_name") or "")
    if business:
        parts.append(business)
    return " · ".join(part for part in parts if part)


def normalize_locations(platform: str, locations: Any) -> list[dict]:
    """规范化并校验地点条件；非法组合抛 ValueError，码表不可用抛 catalog 异常。"""
    platform = _validate_platform(platform)
    if locations is None or locations == []:
        return []
    if not isinstance(locations, list):
        raise ValueError("locations 必须是数组")
    from webui.location_catalog import find_district, validate_business
    from webui.platforms import resolve_platform_city

    normalized: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in locations:
        if not isinstance(raw, dict):
            raise ValueError("地点条件必须是对象")
        city_name = _norm(raw.get("city_name") or raw.get("city") or "")
        if not city_name:
            raise ValueError("地点条件缺少城市")
        entry = resolve_platform_city(platform, city_name)
        city_code = str(raw.get("city_code") or entry.platform_code)
        if city_code != entry.platform_code:
            raise ValueError("城市码与城市不匹配")
        district_name = _norm(raw.get("district_name") or "")
        district_code = str(raw.get("district_code") or "")
        if not district_name or not district_code:
            raise ValueError("地点条件缺少区/县")
        district = find_district(platform, city_name, district_code)
        if district is None:
            raise ValueError("区/县不属于所选城市")
        business_name = _norm(raw.get("business_name") or "")
        business_code = str(raw.get("business_code") or "")
        if platform == "zhilian" and business_code:
            raise ValueError("智联不支持商圈/镇")
        if business_code and not validate_business(
            platform, city_name, district_code, business_code
        ):
            raise ValueError("商圈/镇不属于所选区")
        key = (district_code, business_code)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "platform": platform,
            "city_name": city_name,
            "city_code": city_code,
            "district_name": str(district["name"]),
            "district_code": district_code,
            "business_name": business_name,
            "business_code": business_code,
            "label": location_label({
                "city_name": city_name,
                "district_name": district["name"],
                "business_name": business_name,
            }),
        })
    return normalized


def build_boss_filters(location: dict) -> dict[str, str]:
    """BOSS 区码或“区码:商圈码”，本轮最多一个商圈/镇。"""
    district_code = str(location.get("district_code") or "")
    business_code = str(location.get("business_code") or "")
    value = f"{district_code}:{business_code}" if business_code else district_code
    return {"multiBusinessDistrict": value}


def build_zhilian_city_snapshot(location: dict, city_entry: Any) -> dict:
    """智联 plan_item 城市快照：搜索用区县码，空态导航保留真实城市码。"""
    return {
        "name": city_entry.name,
        "label": city_entry.label,
        "platform_code": str(location.get("district_code") or ""),
        "mapping_version": city_entry.mapping_version,
        "district_name": str(location.get("district_name") or ""),
        "district_code": str(location.get("district_code") or ""),
        "route_city_code": str(location.get("city_code") or city_entry.platform_code),
    }


def expand_location_combinations(params: dict) -> list[dict]:
    """按 keyword × 地点条件展开组合；combo_key 含“城市·区”。

    未选地点时保持旧 ``keyword|城市`` 键，便于断点/进度兼容。
    """
    platform = _validate_platform(str(params.get("platform") or "boss"))
    keywords = _split_keywords(params.get("keyword", ""))
    cities = _split_cities(params.get("city") or [])
    locations = normalize_locations(platform, params.get("locations") or [])
    filters = params.get("filters") or {}
    combos: list[dict] = []
    if locations:
        for keyword in keywords:
            for location in locations:
                district = str(location.get("district_name") or "")
                combo_key = f"{keyword}|{location['city_name']}·{district}"
                combos.append({
                    "keyword": keyword,
                    "city": location["city_name"],
                    "filters": filters,
                    "location": dict(location),
                    "display_city": location["label"],
                    "combo_key": combo_key,
                    "source_filters": (
                        build_boss_filters(location) if platform == "boss" else {}
                    ),
                    "route_city_code": location["city_code"],
                })
        return combos
    for keyword in keywords:
        for city in cities:
            combos.append({
                "keyword": keyword,
                "city": city,
                "filters": filters,
                "display_city": city,
                "combo_key": f"{keyword}|{city}",
                "source_filters": {},
                "route_city_code": "",
            })
    return combos


def location_summary(locations: Any) -> str:
    """多个地点条件的展示摘要，多个用顿号连接。"""
    if not locations:
        return ""
    labels = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        label = _norm(location.get("label") or location_label(location))
        if label and label not in labels:
            labels.append(label)
    return "、".join(labels)


def scope_old_summary_compat(locations: Any) -> bool:
    """旧任务兼容：无地点条件时 canonical 摘要保持旧结构。"""
    return not locations
