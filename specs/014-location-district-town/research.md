# Research: 搜索地点支持区/镇

**Created**: 2026-08-16
**Feature**: [spec.md](spec.md)

## Decision 1: 多区通过“每区独立搜索组合”实现

**Decision**: 同一城市下多区选择时，后端把每个“城市 + 区”展开为独立搜索组合，不依赖平台单次请求支持多区。

**Rationale**: 智联搜索接口的 `S_SOU_WORK_CITY` 只能表达一个区县码，官方 UI 也是单选；BOSS 的 `multiBusinessDistrict` 参考格式为 `区码:商圈码_商圈码`，未证实支持跨区多选。拆成独立组合后两个平台都能实现多区，且与用户已确认的“每区独立组合、各自翻页”一致。

**Alternatives considered**: 尝试单请求多区参数；证据不足，且跨平台不一致，弃用。

## Decision 2: 区/镇码表静态 JSON + 运行时兜底

**Decision**: 新增 `data/boss_business_districts.json`、`data/zhilian_districts.json`，`webui/location_catalog.py` 负责加载；加载失败或静态数据缺失时运行时拉取平台接口兜底。

**Rationale**: BOSS `https://www.zhipin.com/wapi/zpgeek/businessDistrict.json?cityCode=<city>` 已实测返回区 → 商圈/镇两级；智联 `https://fe-api.zhaopin.com/c/i/search/base/data?cityId=<city>` 已实测返回 `hotCity`/`allCity` 及区/县 `sublist`。静态快照保证离线可用，运行时兜底保证数据可更新。

**Alternatives considered**: 全部运行时拉取；网络失败时前端无法展示，弃用。

## Decision 3: 智联区县码替换 `S_SOU_WORK_CITY`，空态导航保留真实城市码

**Decision**: `plan_item.city.platform_code` 在选区时使用区县码，用于搜索 API 的 `S_SOU_WORK_CITY`；`plan_item` 同时携带 `route_city_code`（真实城市码）用于 `_has_empty_marker` 的 `jl<city>` 页面导航。

**Rationale**: 已从智联前端 JS 确认区县筛选就是“把 `S_SOU_WORK_CITY` 换成区县码”；但空态检测用区县码拼 `jl<district>` 可能得到非法路由，必须保留真实城市码。

**Alternatives considered**: 修改空态检测只依赖 API 空响应；会降低真实空结果的确认能力，弃用。

## Decision 4: BOSS 地点参数以隐藏 CLI 参数透传，商圈本轮单值建模

**Decision**: `scripts/boss_cdp_raw.py` 增加隐藏的 `--multiBusinessDistrict` 参数，进入 `filters` 后由现有 `build_search_url`/API 参数透传；`webui/source.py` 的 `SCRAPER_FILTER_FIELDS` 加入 `multiBusinessDistrict`，保证子进程与 in-process 两条路径一致。

**Rationale**: `scrape_list` 已经会把 `filters` 追加到搜索 URL/API，无需新增抓取逻辑；隐藏参数满足“CLI 不做公开新参数”的约束。本轮每个地点条件最多一个商圈/镇，因此 BOSS 参数取值为 `区码` 或 `区码:商圈码`；平台支持的多商圈 `_` 合并能力暂不暴露，后续需要再扩展。

**Alternatives considered**: 通过 `--city` 传拼接地点；会破坏城市解析与展示，弃用。

## Decision 5: scope 旧摘要兼容

**Decision**: `FrozenTaskScope.canonical_json()` 在 `locations` 为空时输出与旧版本完全一致的 payload（含旧 `schema_version` 分支），旧 `scope_digest` 继续有效；新地点任务使用带 `locations` 的新 payload。`from_dict` 对旧 dict 不做强制升级，恢复路径不抛 digest 失配。

**Rationale**: 现有 `from_dict` 会重算 digest 并拒绝不匹配；若直接改 canonical 结构，旧任务恢复会失败。保持旧摘要可验证是 FR-012 的硬约束。

**Alternatives considered**: 启动时迁移所有旧 scope；需要动 store/migration，超出本轮边界，弃用。

## Decision 6: 组合键必须包含完整地点

**Decision**: `expand_combinations` 的地点组合返回 `combo_key = f"{keyword}|{城市}·{区}"`，`run_search` 的断点、`resume_pages`、输出路径、进度事件全部使用该键。

**Rationale**: 现有 `combo_key = f"{kw}|{city}"` 在同城多区时冲突，续抓和进度会互相覆盖。

**Alternatives considered**: 在 combo 内加隐藏序号；会导致断点键不稳定，弃用。

## Decision 7: 前端小方块交互保持样式、补齐可访问性

**Decision**: 城市小方块保持现有外观和 x 删除按钮；外层变为可点击容器（非嵌套 button），`LocationPicker.vue` 面板在方块下方展开；选中后小方块文字变为“城市 · 区”，支持键盘展开/收起与 `aria-expanded`。

**Rationale**: 用户明确要求不改样式；可点击容器 + 独立 x 避免嵌套交互控件，满足键盘可访问性。

**Alternatives considered**: 整体 chip 变 button 并内嵌 x；形成非法嵌套且破坏现有 DOM，弃用。

## Decision 8: `_combo_hash` 必须包含 source_filters

**Decision**: `webui/pipeline_exec.py` 的 `_combo_hash(keyword, city, pages, source_filters=None)` 增加可选 `source_filters` 参数；BOSS 地点组合构造 `input_hash` 时传入 `{"multiBusinessDistrict": ...}`。

**Rationale**: `webui/source.py` 的 `_input_hash` 会用真实 `source_filters` 校验 plan_item；若 `_combo_hash` 仍固定 `source_filters:{}`，带地点搜索必然 `source_input_drift`。

**Alternatives considered**: 绕过 hash 校验；破坏输入完整性校验，弃用。

## Decision 9: 结果页与历史详情只做前端展示透传

**Decision**: FR-014 的结果页/历史详情展示不新增后端历史接口字段：`JobWorkspace.vue` 接收 `DiscoveryView.vue` 传入的 `location_summary`；`ResultHistoryDrawer.vue` 从已有 `detail.script_params.locations` 派生完整地点展示。

**Rationale**: 结果页/历史详情已有 `script_params` 或可在父视图直接拿到本轮地点；不改 `webui/result_history*.py`，避免扩大后端边界。

**Alternatives considered**: 后端新增 `location_summary` 字段并改历史接口；超出本轮文件边界，弃用。

## Decision 10: 静态数据生成范围

**Decision**: `refresh_static_catalogs()` 生成智联全量（一次 `base/data` 请求），BOSS 默认生成热门城市快照（`hot/city.json` 列表）并依赖运行时逐城市兜底；写文件前检查非空与体积上限（默认单文件 ≤5MB）。

**Rationale**: BOSS `businessDistrict.json` 是逐城市接口，全量刷新会发起数百次请求且易触发风控；静态热门快照 + 运行时兜底可满足离线可用和全量覆盖。

**Alternatives considered**: BOSS 全量顺序刷新；耗时长且风险高，弃用。
