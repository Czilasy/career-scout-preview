# Contract: 搜索地点支持区/镇

**Created**: 2026-08-16
**Feature**: [spec.md](../spec.md)

## GET `/api/location-catalog?platform=boss&city=上海`

返回指定城市的地点目录。

成功响应：

```json
{
  "ok": true,
  "platform": "boss",
  "city": "上海",
  "city_code": "101020100",
  "districts": [
    {
      "code": "310115",
      "name": "浦东新区",
      "children": [
        {"code": "39", "name": "张江"},
        {"code": "97", "name": "陆家嘴"}
      ]
    }
  ]
}
```

智联响应 `children` 恒为空数组。

失败/降级语义：

- 平台非法：`{ok:false, error_code:"platform_validation_failed"}` 400
- 城市未知：`{ok:false, error_code:"city_validation_failed"}` 422
- 城市存在但无区/镇数据：返回 200 且 `districts: []`，前端显示“暂无区/镇数据，按城市级搜索”
- 码表加载/网络不可用：`{ok:false, error_code:"location_catalog_unavailable"}` 503，前端同样按城市级搜索并提示

## POST `/api/location/validate`

请求体：

```json
{
  "platform": "boss",
  "scope_kind": "cities",
  "locations": [
    {
      "city_name": "上海",
      "city_code": "101020100",
      "district_name": "浦东新区",
      "district_code": "310115",
      "business_name": "张江",
      "business_code": "39"
    }
  ]
}
```

成功响应：

```json
{"ok": true, "locations": ["上海 · 浦东新区 · 张江"]}
```

失败响应：

- 区/县不属于城市：`{ok:false, error_code:"location_validation_failed", error:"区/县不属于所选城市"}` 422
- 商圈/镇不属于区：同上 422
- `scope_kind=nationwide` 且携带 locations：`{ok:false, error_code:"scope_validation_failed", error:"全国范围不能与具体地点同时选择"}` 422
- `scope_kind` 缺省时按 `cities` 处理

## POST `/api/search-scope/preview`（扩展）

请求体新增可选 `locations`：

```json
{
  "platform": "boss",
  "keywords": ["python"],
  "scope_kind": "cities",
  "cities": ["上海"],
  "locations": [
    {"city_name": "上海", "district_name": "浦东新区", "district_code": "310115", "business_code": "39"}
  ],
  "pages_per_combination": 3
}
```

响应 `scope` 新增 `locations`；`combination_count`/`planned_pages` 按地点组合数计算。

## POST `/api/execute-search`（扩展）

`script_params` 新增可选 `locations`，与 preview 一致；后端用冻结后的 `locations` 覆盖 `script_params["locations"]`，并校验与 `scope_digest` 一致。

## `script_params` 形状

```json
{
  "keyword": "python",
  "city": ["上海"],
  "locations": [
    {
      "city_name": "上海",
      "city_code": "101020100",
      "district_name": "浦东新区",
      "district_code": "310115",
      "business_name": "张江",
      "business_code": "39"
    }
  ],
  "pages": 3,
  "filters": {}
}
```

## 搜索参数映射

| 平台 | 字段 | 取值 |
|------|------|------|
| BOSS | `filters.multiBusinessDistrict` | 区码（无商圈）或 `区码:商圈码`；本轮最多一个商圈/镇 |
| BOSS | `city` | 真实城市码/名 |
| 智联 | `city.platform_code` | 区县码 |
| 智联 | `S_SOU_WORK_CITY` | 区县码 |
| 智联 | `route_city_code` | 真实城市码，仅供空态检测导航 |

## 组合键契约

- BOSS/智联统一使用 `combo_key = f"{keyword}|{city_name}·{district_name}"`；
- 未选区时保持现有 `f"{keyword}|{city_name}"`；
- `resume_pages`、`skip_combos`、页级事件、输出路径全部使用该键。
- BOSS `input_hash` 必须包含 `source_filters.multiBusinessDistrict`，否则 `source_input_drift`。

## 前端交互契约

- 城市小方块保持现有样式；外层容器可点击展开，x 按钮独立；
- 选中地点后小方块文字为 `城市 · 区`（BOSS 可选 ` · 商圈/镇`）；
- 面板在方块下方展开，支持多区选择；
- 无区数据时显示“暂无区/镇数据，按城市级搜索”；
- 地点草稿按平台独立保存；
- 任务锁定/历史模式只显示完整地点文本，不展开编辑。

## 结果页与历史详情展示

- 结果页：`DiscoveryView.vue` 向 `JobWorkspace.vue` 传入 `location_summary`，不新增后端接口字段。
- 历史详情：`ResultHistoryDrawer.vue` 从已有 `detail.script_params.locations` 派生 `location_summary`。
- 旧轮次无 `locations` 时保持城市级展示，不补区。
