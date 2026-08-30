# -*- coding: utf-8 -*-

"""城市码表与城市解析（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

from urllib.request import Request
import json
import os
from urllib.request import urlopen
from scripts.boss.constants import CITY_GROUP_URL, HOT_CITY_URL
from scripts.boss.constants import log
import sys as _sys
from scripts.boss import runtime

# ============================================================
# 筛选参数映射
# Source snapshots:
# - 城市: https://www.zhipin.com/wapi/zpgeek/search/job/hot/city.json + cityGroup.json
# - 筛选项: https://www.zhipin.com/wapi/zpgeek/search/job/condition.json
# ============================================================
# 城市码表已外置到 data/city_codes.json（全量城市，覆盖一二三四五线），
# resolve_city 查询链：本地静态 → 运行时拉 BOSS 接口 → 原样兜底。
# 仓库内路径（开发态）与打包后路径（pip install）都在 _city_data_path() 里处理。
CITY_DATA_FILENAME = "city_codes.json"


_local_city_map_cache = None


def _city_data_path():
    """返回 data/city_codes.json 的路径，兼容仓库开发态与 pip 打包态。"""
    # 1. 仓库开发态：脚本在 scripts/，数据在 ../data/
    # 021 B8 T026：拆分后本模块位于 scripts/boss/，仓库根需从 __file__ 上溯两级
    repo_data = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", CITY_DATA_FILENAME)
    if os.path.isfile(repo_data):
        return os.path.normpath(repo_data)
    # 2. 打包态：wheel force-include 到包根 data/，用 importlib.resources 兜底
    try:
        from importlib.resources import files  # py3.9+
        pkg_data = files(__package__ or "__main__").joinpath("..", "data", CITY_DATA_FILENAME) \
            if __package__ else None
    except Exception:
        pkg_data = None
    if pkg_data is not None and os.path.isfile(str(pkg_data)):
        return str(pkg_data)
    # 3. 找不到则返回开发态路径（让调用方决定降级）
    return os.path.normpath(repo_data)


def load_local_city_map():
    """读取本地 data/city_codes.json 静态全量城市码表。

    兼容两种格式：
    - 旧扁平格式: {name: code}
    - 新结构化格式: {schema_version, nationwide, cities: [{name, code, aliases, enabled}]}

    返回 (name_to_code, code_to_name) 两个字典；读取失败返回 ({}, {})。
    结果缓存，重复调用零开销。
    """
    global _local_city_map_cache
    if _local_city_map_cache is not None:
        return _local_city_map_cache
    name_to_code = {}
    try:
        path = _city_data_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and "cities" in raw:
            # 新结构化格式 (schema_version >= 2)
            nationwide = raw.get("nationwide")
            if isinstance(nationwide, dict) and nationwide.get("enabled", True):
                nw_name = nationwide.get("name")
                nw_code = nationwide.get("code")
                if nw_name and nw_code:
                    name_to_code[str(nw_name)] = str(nw_code)
            for entry in raw.get("cities", []):
                if not entry.get("enabled", True):
                    continue
                cname = entry.get("name")
                ccode = entry.get("code")
                if cname and ccode:
                    name_to_code[str(cname)] = str(ccode)
        elif isinstance(raw, dict):
            # 旧扁平格式 {name: code}
            for name, code in raw.items():
                if name and code is not None:
                    name_to_code[str(name)] = str(code)
    except (OSError, ValueError) as e:
        log.debug(f"读取本地城市码表失败: {e}")
    code_to_name = {code: name for name, code in name_to_code.items()}
    _local_city_map_cache = name_to_code, code_to_name
    return _local_city_map_cache


# Backward-compatible aliases for the existing Web UI integration. The
# source of truth remains the external city_codes.json introduced upstream.
CITY_MAP, CITY_R = load_local_city_map()


# Backward-compatible aliases for the existing Web UI integration. The
# source of truth remains the external city_codes.json introduced upstream.
CITY_MAP, CITY_R = load_local_city_map()


# ============================================================
# 解析城市参数（支持中文和代码）
# ============================================================
def fetch_boss_json(url, timeout=10):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_live_city_maps(timeout=10):
    global _live_city_maps_cache
    if _live_city_maps_cache is not None:
        return _live_city_maps_cache

    name_to_code = {}

    try:
        hot_city_data = fetch_boss_json(HOT_CITY_URL, timeout=timeout)
        for item in hot_city_data.get("zpData", {}).get("hotCityList", []):
            name = item.get("name")
            code = item.get("code")
            if name and code is not None:
                name_to_code[name] = str(code)

        city_group_data = fetch_boss_json(CITY_GROUP_URL, timeout=timeout)
        for group in city_group_data.get("zpData", {}).get("cityGroup", []):
            for item in group.get("cityList", []):
                name = item.get("name")
                code = item.get("code")
                if name and code is not None:
                    name_to_code.setdefault(name, str(code))
    except (OSError, ValueError) as e:
        log.debug(f"加载 BOSS 城市映射失败，使用内置城市映射: {e}")

    code_to_name = {code: name for name, code in name_to_code.items()}
    _live_city_maps_cache = name_to_code, code_to_name
    return _live_city_maps_cache


def resolve_city(city_input):
    """把「中文城市名 / 城市码」解析为 (name, code)。

    查询链（逐级降级）:
      1. 本地静态码表 data/city_codes.json（全量、离线可用）
      2. 运行时拉 BOSS 接口 hot/city.json + cityGroup.json（自愈）
      3. 都查不到则原样返回（兼容用户直接传裸 city code）
    """
    if not city_input:
        return city_input, city_input

    # 1. 本地静态码表
    local_map, local_reverse = load_local_city_map()
    if city_input in local_map:
        return city_input, local_map[city_input]
    if city_input in local_reverse:
        return local_reverse[city_input], city_input

    # 2. 运行时拉 BOSS 接口
    live_map, live_reverse = load_live_city_maps()
    if city_input in live_map:
        return city_input, live_map[city_input]
    if city_input in live_reverse:
        return live_reverse[city_input], city_input

    # 3. 兜底：原样返回
    return city_input, city_input


def list_cities(keyword=None, use_live=True):
    """打印支持的城市列表。keyword 非空时只打印城市名含该关键词的城市。

    优先用运行时拉取的最新码表（use_live=True），拉取失败回退本地静态码表。
    """
    name_to_code = {}
    if use_live:
        live_map, _ = load_live_city_maps()
        name_to_code.update(live_map)
    if not name_to_code:
        local_map, _ = load_local_city_map()
        name_to_code.update(local_map)
    if not name_to_code:
        print("⚠️ 无法加载城市码表（本地静态文件缺失且网络拉取失败）")
        return

    items = sorted(name_to_code.items(), key=lambda kv: kv[0])
    if keyword:
        keyword = keyword.strip()
        items = [(n, c) for n, c in items if keyword in n]
        if not items:
            print(f"没有匹配「{keyword}」的城市")
            return
    print(f"共 {len(items)} 个城市（支持中文城市名或城市码）：")
    for name, code in items:
        print(f"  {name}\t{code}")


_live_city_maps_cache = None
