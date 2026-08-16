"""B054 地点目录：静态 JSON 为主，运行时平台接口兜底。

职责：加载/解析/校验 BOSS 区商圈与智联区县目录；提供刷新静态快照入口。
不接触任务范围、搜索组合或前端状态。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BOSS_FILE = DATA_DIR / "boss_business_districts.json"
ZHILIAN_FILE = DATA_DIR / "zhilian_districts.json"
BOSS_CITY_FILE = DATA_DIR / "city_codes.json"
ZHILIAN_CITY_FILE = DATA_DIR / "zhilian_city_codes.json"

BOSS_DISTRICT_URL = "https://www.zhipin.com/wapi/zpgeek/businessDistrict.json?cityCode={code}"
BOSS_HOT_CITY_URL = "https://www.zhipin.com/wapi/zpgeek/search/job/hot/city.json"
ZHILIAN_BASE_URL = "https://fe-api.zhaopin.com/c/i/search/base/data?cityId={code}"
ZHILIAN_NATIONWIDE_CODE = "jl0"

MAX_STATIC_BYTES = 5 * 1024 * 1024
_KNOWN_PLATFORMS = ("boss", "zhilian")
_cache: dict[str, dict[str, Any] | None] = {"boss": None, "zhilian": None}


class LocationCatalogUnavailable(RuntimeError):
    """地点码表加载或平台接口不可用。"""


def _validate_platform(platform: str) -> str:
    if platform not in _KNOWN_PLATFORMS:
        raise ValueError(f"未知平台键: {platform}")
    return platform


def _http_json(url: str, *, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)


def _normalize_boss_node(node: dict) -> dict:
    children = []
    for child in node.get("subLevelModelList") or []:
        if not isinstance(child, dict):
            continue
        code = child.get("code")
        name = child.get("name")
        if code is None or not name:
            continue
        children.append({"code": str(code), "name": str(name)})
    return {
        "code": str(node.get("code") or ""),
        "name": str(node.get("name") or ""),
        "children": children,
    }


def _normalize_zhilian_district(node: dict) -> dict:
    return {
        "code": str(node.get("code") or ""),
        "name": str(node.get("name") or ""),
        "children": [],
    }


def fetch_boss_districts(city_code: str, *, timeout: float = 15.0) -> list[dict]:
    """拉取 BOSS 单个城市的区 -> 商圈/镇目录。"""
    data = _http_json(
        BOSS_DISTRICT_URL.format(code=urllib.parse.quote(str(city_code))),
        timeout=timeout,
    )
    if not isinstance(data, dict) or data.get("code") not in (0, "0"):
        raise LocationCatalogUnavailable("BOSS 地点目录接口返回异常")
    zp = data.get("zpData") or {}
    district = zp.get("businessDistrict") or {}
    rows = []
    for node in district.get("subLevelModelList") or []:
        if isinstance(node, dict) and node.get("code") is not None and node.get("name"):
            rows.append(_normalize_boss_node(node))
    return rows


def fetch_zhilian_districts(city_code: str, *, timeout: float = 15.0) -> list[dict]:
    """从智联 base/data 返回中查找指定城市的区县子级。"""
    data = _http_json(
        ZHILIAN_BASE_URL.format(code=urllib.parse.quote(str(city_code))),
        timeout=timeout,
    )
    if not isinstance(data, dict) or data.get("code") != 200:
        raise LocationCatalogUnavailable("智联地点目录接口返回异常")
    payload = data.get("data") or {}
    needle = str(city_code)
    for key in ("allCity", "hotCity"):
        for row in payload.get(key) or []:
            if isinstance(row, dict) and str(row.get("code") or "") == needle:
                return [
                    _normalize_zhilian_district(child)
                    for child in row.get("sublist") or []
                    if isinstance(child, dict) and child.get("code") is not None
                    and child.get("name")
                ]
    return []


def fetch_boss_hot_cities(*, timeout: float = 15.0) -> list[dict]:
    """返回 BOSS 热门城市列表，排除全国。"""
    data = _http_json(BOSS_HOT_CITY_URL, timeout=timeout)
    if not isinstance(data, dict) or data.get("code") not in (0, "0"):
        raise LocationCatalogUnavailable("BOSS 热门城市接口返回异常")
    zp = data.get("zpData") or {}
    result = []
    for row in zp.get("hotCityList") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        if code and name and code != "100010000":
            result.append({"code": code, "name": name})
    return result


def fetch_zhilian_catalog(*, timeout: float = 15.0) -> dict[str, list[dict]]:
    """一次 base/data 请求生成智联城市 -> 区县映射。"""
    data = _http_json(
        ZHILIAN_BASE_URL.format(code="530"),
        timeout=timeout,
    )
    if not isinstance(data, dict) or data.get("code") != 200:
        raise LocationCatalogUnavailable("智联地点目录接口返回异常")
    payload = data.get("data") or {}
    catalog: dict[str, list[dict]] = {}
    for key in ("allCity", "hotCity"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "")
            if not code or code == ZHILIAN_NATIONWIDE_CODE:
                continue
            catalog.setdefault(code, [])
            for child in row.get("sublist") or []:
                if not isinstance(child, dict):
                    continue
                child_code = str(child.get("code") or "")
                child_name = str(child.get("name") or "")
                if child_code and child_name:
                    catalog[code].append(_normalize_zhilian_district(child))
    return catalog


def _static_payload(platform: str) -> dict[str, Any]:
    path = BOSS_FILE if platform == "boss" else ZHILIAN_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _static_map(platform: str) -> dict[str, list[dict]]:
    payload = _static_payload(platform)
    result: dict[str, list[dict]] = {}
    for city in payload.get("cities") or []:
        if not isinstance(city, dict):
            continue
        code = str(city.get("code") or "")
        if code:
            result[code] = [dict(row) for row in (city.get("districts") or [])]
    return result


def _runtime_fetch(platform: str, city_code: str) -> list[dict]:
    if platform == "boss":
        return fetch_boss_districts(city_code)
    return fetch_zhilian_districts(city_code)


def get_districts(platform: str, city_name: str, *, fetcher: Callable[..., list[dict]] | None = None) -> list[dict]:
    """返回城市的地点目录；无数据返回 []，网络不可用抛 LocationCatalogUnavailable。

    fetcher 只用于测试注入；正式路径使用平台实时接口。
    """
    platform = _validate_platform(platform)
    from webui.platforms import resolve_platform_city

    entry = resolve_platform_city(platform, city_name)
    city_code = entry.platform_code
    cached = _cache.get(platform)
    if cached is None:
        cached = _static_map(platform)
        _cache[platform] = cached
    if city_code in cached:
        return [dict(row) for row in cached[city_code]]
    try:
        rows = (fetcher or _runtime_fetch)(platform, city_code)
    except LocationCatalogUnavailable:
        raise
    except Exception as exc:
        raise LocationCatalogUnavailable(str(exc)) from exc
    cached[city_code] = [dict(row) for row in rows]
    return [dict(row) for row in rows]


def find_district(platform: str, city_name: str, district_code: str) -> dict | None:
    for row in get_districts(platform, city_name):
        if str(row.get("code") or "") == str(district_code):
            return row
    return None


def validate_business(platform: str, city_name: str, district_code: str, business_code: str) -> bool:
    if not business_code:
        return True
    if platform != "boss":
        return False
    district = find_district(platform, city_name, district_code)
    if district is None:
        return False
    return any(
        str(child.get("code") or "") == str(business_code)
        for child in district.get("children") or []
    )


def _write_static(path: Path, payload: dict[str, Any]) -> None:
    if not payload.get("cities"):
        raise LocationCatalogUnavailable("地点目录刷新结果为空，拒绝写入")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, separators=(",", ": "))
    if len(encoded.encode("utf-8")) > MAX_STATIC_BYTES:
        raise LocationCatalogUnavailable("地点目录超过 5MB 上限，拒绝写入")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded + "\n", encoding="utf-8")


def refresh_static_catalogs() -> dict[str, Path]:
    """生成静态码表：BOSS 热门城市快照，智联一次全量。"""
    boss_cities = fetch_boss_hot_cities()
    boss_cities_payload: list[dict[str, Any]] = []
    for city in boss_cities:
        districts = fetch_boss_districts(city["code"])
        boss_cities_payload.append({
            "name": city["name"],
            "code": city["code"],
            "districts": districts,
        })
        time.sleep(0.4)
    _write_static(BOSS_FILE, {
        "platform": "boss",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cities": boss_cities_payload,
    })

    zhilian_catalog = fetch_zhilian_catalog()
    zhilian_cities_payload = [
        {"name": str(code), "code": code, "districts": districts}
        for code, districts in sorted(zhilian_catalog.items())
    ]
    _write_static(ZHILIAN_FILE, {
        "platform": "zhilian",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cities": zhilian_cities_payload,
    })
    _cache["boss"] = None
    _cache["zhilian"] = None
    return {"boss": BOSS_FILE, "zhilian": ZHILIAN_FILE}


def reset_catalog_cache() -> None:
    _cache["boss"] = None
    _cache["zhilian"] = None


if __name__ == "__main__":
    if "--refresh-data" in sys.argv:
        paths = refresh_static_catalogs()
        for platform, path in paths.items():
            print(f"{platform}: {path} ({path.stat().st_size} bytes)")
        sys.exit(0)
    print("用法: python -m webui.location_catalog --refresh-data")
    sys.exit(2)
