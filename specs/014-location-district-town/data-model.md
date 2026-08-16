# Data Model: 搜索地点支持区/镇

**Created**: 2026-08-16
**Feature**: [spec.md](spec.md)

## 实体

### 地点条件（LocationCondition）

用户在一个城市下选择的完整地点，结构化保存。

| 字段 | 类型 | 说明 |
|------|------|------|
| `platform` | `boss \| zhilian` | 平台，决定层级语义 |
| `city_name` | string | 城市规范名，如“上海” |
| `city_code` | string | 平台城市码 |
| `district_name` | string | 区/县名，如“浦东新区” |
| `district_code` | string | 区/县平台码 |
| `business_name` | string | BOSS 商圈/镇名，可空；本轮最多一个 |
| `business_code` | string | BOSS 商圈/镇码，可空；本轮最多一个 |
| `label` | string | 展示文本“上海 · 浦东新区” |

校验规则：

- `city_code` 必须属于当前平台城市目录；
- `district_code` 必须属于 `city_code` 的区/县子级；
- BOSS 出现 `business_code` 时必须属于 `district_code` 的商圈/镇子级；
- 智联不得携带 `business_code`；
- 本轮每个地点条件最多一个商圈/镇，不暴露平台多商圈合并能力；
- 全国范围不能携带地点条件；
- 同一城市下的多个地点条件按 `district_code + business_code` 去重。

### 区/镇码表条目（LocationCatalogEntry）

静态 JSON 或运行时拉取后的统一结构。

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 平台码 |
| `name` | string | 展示名 |
| `level` | `city \| district \| business` | 层级 |
| `parent_code` | string | 父级码 |
| `children` | array | 下一级条目 |

BOSS 结构：`city → district → business`。
智联结构：`city → district`（无 business 级）。

### 任务范围快照（FrozenTaskScope + locations）

在现有 `FrozenTaskScope` 上增加可选 `locations` 列表。

| 字段 | 变化 | 说明 |
|------|------|------|
| `locations` | 新增可选 | 规范化后的地点条件列表 |
| `combination_count` | 重新计算 | 关键词数 × 地点组合数 |
| `planned_pages` | 重新计算 | 组合数 × 每组合页数 |
| `scope_digest` | 新地点任务变化 | 空地点任务保持旧摘要兼容 |

兼容规则：

- `locations` 为空时 canonical payload 与旧版本完全一致，旧 digest 继续有效；
- `locations` 非空时 canonical payload 包含地点字段并生成新 digest；
- `from_dict` 对旧 dict（无 locations）按旧摘要加载，不抛失配；
- 旧任务恢复后 `locations=[]`，前端按城市级展示。

### 搜索组合（SearchCombo）

组合展开后的单个搜索单位。

| 字段 | 类型 | 说明 |
|------|------|------|
| `keyword` | string | 单关键词 |
| `city` | string / dict | BOSS 为城市码/名；智联为含区县码的 city snapshot |
| `location` | object | 完整地点条件 |
| `combo_key` | string | `keyword|城市·区`，唯一身份 |
| `source_filters` | dict | BOSS 含 `multiBusinessDistrict` |
| `route_city_code` | string | 智联空态导航用真实城市码 |

### 展示摘要（LocationSummary）

用于结果页与历史详情的纯展示文本。

| 字段 | 类型 | 说明 |
|------|------|------|
| `location_summary` | string | “城市 · 区”（BOSS 可选“ · 商圈/镇”） |
| `source` | 派生 | 当前轮 `script_params.locations` 或 `FrozenTaskScope.locations` |

## 关系

```text
任务范围快照（FrozenTaskScope.locations）
        │
        ▼
expand_combinations ──► SearchCombo 列表（combo_key 含地点）
        │
        ▼
plan_item ──► BOSS：filters.multiBusinessDistrict（区码 或 区码:商圈码）
           └──► 智联：city.platform_code=区县码 + route_city_code=城市码
        │
        ▼
页级事件 / 断点 / 输出路径（全部使用 combo_key）
        │
        ▼
结果页 / 历史详情（前端派生 location_summary）
```

## 状态与兼容

- 旧任务/旧草稿：`locations` 缺失或为空，行为与现状完全一致。
- 新任务：`locations` 非空时按地点组合计数与执行。
- 平台切换：地点草稿按平台独立保存，不迁移码值。
- 无区数据城市：`district_entries=[]`，前端隐藏选择并提示，任务按城市级。
- 组合超限：`planned_pages > 200` 时 preview 返回明确错误，不自动截断。
- 结果页/历史详情只做前端展示派生，不新增后端历史接口字段。
