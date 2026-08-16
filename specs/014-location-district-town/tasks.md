# Tasks: 搜索地点支持区/镇（BOSS 区/商圈/镇 + 智联区/县）

**Input**: Design documents from `/specs/014-location-district-town/`

**Prerequisites**: plan.md（必需）、spec.md（必需）、research.md、data-model.md、contracts/location-flow.md、quickstart.md

**Organization**: 按用户故事分层；US1 是小方块交互，US2 是多城市/多区组合与结果页展示，US3 是旧任务、无数据、平台切换与历史详情兼容；基础层先行，最后做全量真实验证。

## File Boundaries

- **Allowed files**: `data/boss_business_districts.json`、`data/zhilian_districts.json`、`webui/location_catalog.py`、`webui/location_scope.py`、`webui/location_api.py`、`webui/execution_config.py`（净增量 ≤20 且总行数 ≤800）、`webui/app.py`（增量 ≤40）、`webui/pipeline_exec.py`（增量 ≤40，含 `_combo_hash`）、`webui/source.py`（增量 ≤10）、`scripts/boss_cdp_raw.py`（增量 ≤15）、`scripts/zhilian_cdp_raw.py`（增量 ≤20）、`webui/src/types.ts`（增量 ≤60）、`webui/src/discovery.ts`（增量 ≤30）、`webui/src/location.ts`、`webui/src/composables/useLocationDraft.ts`、`webui/src/components/LocationPicker.vue`、`webui/src/views/DiscoveryView.vue`（增量 ≤70）、`webui/src/components/JobWorkspace.vue`（增量 ≤30）、`webui/src/components/ResultHistoryDrawer.vue`（增量 ≤30）、`tests/test_location_catalog.py`、`tests/test_location_scope.py`、`tests/test_execution_config.py`、`tests/test_webui_app.py`、`webui/src/__tests__/location.spec.ts`、`webui/src/components/__tests__/LocationPicker.spec.ts`、`webui/src/components/__tests__/JobWorkspace.spec.ts`、`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`、`webui/src/composables/__tests__/useLocationDraft.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`。
- **Forbidden files**: `webui/store.py`、`webui/store_migrations.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/ai*.py`、`webui/tuning.py`、`webui/task_runners.py`、`webui/workbench.py`、`webui/error_registry.py`、`webui/platforms.py`、`webui/src/styles.css`、`webui/src/components/TaskProgress.vue`、`packaging/*`、`.github/*`、`README.md`、`CHANGELOG.md`。
- **New files**: `webui/location_catalog.py`、`webui/location_scope.py`、`webui/location_api.py`、`webui/src/location.ts`、`webui/src/composables/useLocationDraft.ts`、`webui/src/components/LocationPicker.vue`、`data/boss_business_districts.json`、`data/zhilian_districts.json`、对应测试文件。
- **Reference direction**: 后端 `app.py → location_api.py → location_scope.py / location_catalog.py`；`pipeline_exec.py → location_scope.py → location_catalog.py`；前端 `DiscoveryView.vue → useLocationDraft.ts → location.ts / api.ts`；`DiscoveryView.vue → JobWorkspace.vue / ResultHistoryDrawer.vue` 只传展示文本。
- **Line gate**: `execution_config.py` 净增量 ≤20 且总行数 ≤800，如超限先迁出非核心辅助；`app.py` ≤40；`pipeline_exec.py` ≤40；`source.py` ≤10；`boss_cdp_raw.py` ≤15；`zhilian_cdp_raw.py` ≤20；`types.ts` ≤60；`discovery.ts` ≤30；`DiscoveryView.vue` ≤70；`JobWorkspace.vue` ≤30；`ResultHistoryDrawer.vue` ≤30；新文件不超过宪法单文件上限。

## Verification Gate

- 功能交付最终门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- BOSS/智联真实浏览器冒烟各至少一次。
- 本任务清单只覆盖实现与验证；外部收口动作不在本清单范围。

## Phase 1: Setup（现状核对）

**Purpose**: 确认 Spec/Plan/契约已就位，任务可直接执行。

- [x] T001 读取 `specs/014-location-district-town/spec.md`、`plan.md`、`contracts/location-flow.md`、`data-model.md`，核对 `.specify/feature.json` 指向 `specs/014-location-district-town`；无代码改动。

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 地点目录、scope 兼容、脚本参数与前端纯函数先落地，US1-US3 才能接线。

- [x] T002 [P] 新建 `webui/location_catalog.py`：实现 BOSS/智联静态码表加载、运行时兜底拉取、城市/区/商圈从属校验、`refresh_static_catalogs()` 与 `python -m webui.location_catalog --refresh-data` 刷新入口。
- [x] T003 [P] 运行 `uv run python -m webui.location_catalog --refresh-data` 生成静态文件：智联全量一次生成；BOSS 热门城市快照 + 运行时逐城市兜底；检查文件存在、非空、单文件 ≤5MB（BOSS 抽查上海，智联抽查北京）。
- [x] T004 [P] 新建 `webui/location_scope.py`：实现 `normalize_locations(platform, locations)`、`expand_location_combinations(script_params)`（combo_key 含完整地点）、`build_boss_filters(location)`（区码或 `区码:商圈码`）、`build_zhilian_city_snapshot(location, city_entry)`、`location_summary(locations)`、scope 旧摘要兼容辅助。
- [x] T005 [P] 修改 `webui/execution_config.py`：`FrozenTaskScope` 增加可选 `locations`；`canonical_json()` 在 locations 为空时输出旧 payload 保持旧 digest；非空时输出含 locations 的新 payload；`from_dict` 对旧 dict 兼容；`normalize_scope`/`preview_scope` 透传 locations 并按地点组合计算。采用惰性导入避免与 `location_scope.py` 循环依赖；净增量 ≤20 行，超限时先迁出非核心辅助并保持总行数 ≤800。
- [x] T006 [P] 新建 `webui/location_api.py`：Flask blueprint 提供 `GET /api/location-catalog`、`POST /api/location/validate`；validate 支持 `scope_kind`；无区数据返回 200 空 districts，加载不可用返回 503。
- [x] T007 [P] 修改 `scripts/boss_cdp_raw.py`：增加隐藏 `--multiBusinessDistrict` argparse 参数（`help=argparse.SUPPRESS`），加入 filters 收集，供 `build_search_url`/API 参数透传。
- [x] T008 [P] 修改 `scripts/zhilian_cdp_raw.py`：`fetch_list` 支持 `plan_item.route_city_code`；有区县码时 `S_SOU_WORK_CITY` 用 `city.platform_code`，`_has_empty_marker` 仍用 `route_city_code` 拼 `jl<city>`。
- [x] T009 [P] 修改 `webui/source.py`：`SCRAPER_FILTER_FIELDS` 增加 `multiBusinessDistrict`，确保子进程命令与 in-process 翻译一致。
- [x] T010 [P] 修改 `webui/pipeline_exec.py`：`expand_combinations` 在 `params.locations` 存在时委托 `location_scope.expand_location_combinations`；`run_search` 使用返回的 `combo_key`，智联 plan_item 使用 `build_zhilian_city_snapshot` 与 `route_city_code`，BOSS plan_item 携带 `multiBusinessDistrict` source_filters；`_combo_hash` 增加可选 `source_filters` 参数并纳入 hash。
- [x] T011 [P] 修改 `webui/app.py`：注册 `location_api` 蓝图；`/api/search-scope/preview` 与 `/api/execute-search` 接收/冻结/校验 `locations`，写入 `script_params["locations"]`，保持旧请求兼容。
- [x] T012 [P] 修改 `webui/src/types.ts`：新增 `LocationCondition`、`LocationCatalogResponse`、`LocationCatalogEntry` 类型。
- [x] T013 [P] 新建 `webui/src/location.ts`：实现 `locationLabel`、`normalizeLocationDraft`、`buildLocationPayload`、`locationCombinationCount`、`locationSummary` 等纯函数。
- [x] T014 [P] 修改 `webui/src/discovery.ts`：`buildSearchScriptParams` 支持 `locations`，输出 `script_params.locations`。
- [x] T015 [P] 新建 `tests/test_location_catalog.py`：覆盖静态加载、运行时兜底、从属校验、无区数据城市返回空、不可用返回 503、刷新入口。
- [x] T016 [P] 新建 `tests/test_location_scope.py`：覆盖地点规范化、多区组合计数、combo_key 唯一、BOSS/智联参数翻译、scope 旧摘要兼容。
- [x] T017 [P] 更新 `tests/test_execution_config.py`：旧 scope dict（无 locations）from_dict 不抛 digest 失配；空 locations 与旧 digest 一致；新 locations 生成新 digest。
- [x] T018 [P] 新建 `webui/src/__tests__/location.spec.ts`：覆盖标签、payload、组合计数、`locationSummary`、按平台层级。

**Checkpoint**: 基础层测试通过后，开始 US1。

## Phase 3: 用户故事 1 - 在城市小方块内选择区/镇（P1）

**Goal**: 点击城市小方块展开地点面板，选中后显示“城市 · 区”，x 删除整个城市，样式不变。

**Independent Test**: `LocationPicker.spec.ts`、`useLocationDraft.spec.ts`、`DiscoveryView.spec.ts` 覆盖展开、选择、清空、无区数据、键盘可访问。

### Tests for User Story 1

- [x] T019 [P] [US1] 新建 `webui/src/components/__tests__/LocationPicker.spec.ts`：点击城市块展开面板、级联选择、多区选择、清空地点、无区数据提示、x 删除城市、键盘 Enter/Space 展开与 `aria-expanded`。
- [x] T020 [P] [US1] 新建 `webui/src/composables/__tests__/useLocationDraft.spec.ts`：按平台保存/恢复地点草稿、清空地点、平台切换不串码值、旧草稿无地点时保持城市级。

### Implementation for User Story 1

- [x] T021 [P] [US1] 新建 `webui/src/composables/useLocationDraft.ts`：按平台管理地点草稿、恢复、清空、与 cityText 联动；不反向依赖 view。
- [x] T022 [P] [US1] 新建 `webui/src/components/LocationPicker.vue`：城市块外层可点击容器 + 独立 x 按钮，下方展开面板；BOSS 两级、智联一级；面板样式 scoped，不改全局样式。
- [x] T023 [US1] 修改 `webui/src/views/DiscoveryView.vue`：城市小方块接入 LocationPicker；选中后小方块文字为“城市 · 区”；提交时 `buildSearchScriptParams` 携带 locations；只做接线，不新增业务函数。
- [x] T024 [US1] 更新 `webui/src/views/__tests__/DiscoveryView.spec.ts`：小方块显示完整地点、提交 locations、删除城市时同时清空地点。

**Checkpoint**: US1 完成，小方块交互可独立验证。

## Phase 4: 用户故事 2 - 多城市 + 多区独立组合与结果页展示（P1）

**Goal**: 多城市各自配区，同城多区拆独立组合；预览/进度/断点/输出路径使用含地点组合键；结果页显示完整地点。

**Independent Test**: `tests/test_location_scope.py`、`tests/test_webui_app.py`、`JobWorkspace.spec.ts`、`DiscoveryView.spec.ts` 覆盖组合计数、combo_key、hash、预览/执行接口与结果页展示。

### Tests for User Story 2

- [x] T025 [P] [US2] 扩展 `tests/test_location_scope.py`：1 关键词 × 上海 3 区 × 每区 3 页 = 9 页；combo_key 为 `keyword|上海·区名`；同城多区键不重复。
- [x] T026 [P] [US2] 更新 `tests/test_webui_app.py`：preview 返回 `locations` 与组合数；execute-search 接受 locations、scope 校验一致、script_params 含冻结 locations；`/api/location/validate` 支持 `scope_kind`；旧请求不带 locations 行为不变。
- [x] T027 [P] [US2] 在 `tests/test_location_scope.py` 增加 `_combo_hash` 用例：传入 `source_filters={"multiBusinessDistrict": "310115"}` 后 hash 与空 filters 不同；`run_search` 使用 combo_key 的断点/进度键不冲突。

### Implementation for User Story 2

- [x] T028 [US2] 在 `webui/location_scope.py` 完善组合展开：未选区保持旧 `keyword|城市` 键；选区使用 `keyword|城市·区`；输出 `combo_key`、`location`、`route_city_code`、BOSS `source_filters`。
- [x] T029 [US2] 在 `webui/pipeline_exec.py` 完成接线：`run_search` 的进度事件、`resume_pages`、`skip_combos`、`_combo_output_path` 使用地点组合键；`_combo_hash` 接收 source_filters；智联 plan_item 携带 route_city_code。
- [x] T030 [US2] 在 `webui/app.py` 完成接线：preview/execute 的 `locations` 进入 `FrozenTaskScope` 与 `script_params`；`scope_previews` 缓存键使用新 digest；响应 `scope.locations` 下发前端。
- [x] T031 [US2] 修改 `webui/src/views/DiscoveryView.vue`：把本轮 `location_summary` 传给 `JobWorkspace.vue`；无 locations 时传城市级文本或空。
- [x] T032 [US2] 修改 `webui/src/components/JobWorkspace.vue`：结果页上下文显示 `location_summary`；岗位卡仍显示岗位自身 `location`，不混用。
- [x] T033 [US2] 新建 `webui/src/components/__tests__/JobWorkspace.spec.ts` 并更新 `DiscoveryView.spec.ts`：结果页显示“上海 · 浦东新区”，旧轮无 locations 时保持城市级。

**Checkpoint**: US2 完成，多区组合主链路与结果页展示可独立验证。

## Phase 5: 用户故事 3 - 旧任务、无数据、平台切换与历史详情（P2）

**Goal**: 旧任务按城市级恢复；无区数据不阻断；平台切换地点按平台独立保存；历史详情显示完整地点。

**Independent Test**: `tests/test_location_scope.py`、`tests/test_webui_app.py`、`useLocationDraft.spec.ts`、`ResultHistoryDrawer.spec.ts`、`DiscoveryView.spec.ts` 覆盖兼容场景。

### Tests for User Story 3

- [x] T034 [P] [US3] 更新 `tests/test_execution_config.py` 与 `tests/test_webui_app.py`：旧 frozen_scope 无 locations 恢复不抛错；历史任务响应保持城市级。
- [x] T035 [P] [US3] 更新 `tests/test_location_catalog.py`：无区数据城市返回 200 空 districts；加载不可用返回 `location_catalog_unavailable` 503，两种语义分开断言。
- [x] T036 [P] [US3] 更新 `webui/src/composables/__tests__/useLocationDraft.spec.ts`：BOSS 选“上海 · 浦东新区”后切智联，城市保留、区选择清空为智联草稿。
- [x] T037 [P] [US3] 新建 `webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`：历史详情从 `detail.script_params.locations` 显示完整地点；无 locations 时显示城市级。

### Implementation for User Story 3

- [x] T038 [US3] 在 `webui/location_scope.py`/`webui/location_api.py`：旧 dict 无 locations 时按城市级处理；`/api/location-catalog` 无数据返回 200 空 districts，加载不可用返回 503；不自动补区。
- [x] T039 [US3] 在 `webui/src/composables/useLocationDraft.ts`：平台切换时城市保留、地点槽按平台独立；旧草稿无地点时 `locations=[]`。
- [x] T040 [US3] 在 `webui/src/components/LocationPicker.vue`：无区数据时显示“暂无区/镇数据，按城市级搜索”；任务锁定/历史模式只显示完整地点，不展开编辑。
- [x] T041 [US3] 修改 `webui/src/components/ResultHistoryDrawer.vue`：详情区从已有 `detail.script_params.locations` 派生 `location_summary` 并展示；不改 `webui/result_history*.py`。

**Checkpoint**: US3 完成，兼容、降级与历史详情展示可独立验证。

## Phase 6: 跨切面验证与真实浏览器冒烟

**Purpose**: 聚焦测试、全量门禁、构建、卫生与 BOSS/智联真实冒烟。

- [x] T042 运行后端聚焦测试：`uv run python -m unittest tests.test_location_catalog tests.test_location_scope tests.test_execution_config tests.test_webui_app`。
- [x] T043 运行前端聚焦测试：`cd webui && npm test -- location.spec.ts LocationPicker.spec.ts useLocationDraft.spec.ts JobWorkspace.spec.ts ResultHistoryDrawer.spec.ts DiscoveryView.spec.ts`。
- [x] T044 运行 `cd webui && npm run build`，确认 dist 同步。
- [x] T045 运行后端全量测试：`uv run python -m unittest discover tests`。
- [x] T046 运行前端全量测试：`cd webui && npm test`。
- [x] T047 运行仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`；检查 `git diff --check` 与 `git status`。
- [x] T048 真实 BOSS 冒烟：本地已登录 BOSS 专用 Chrome，选择“上海 · 浦东新区”，确认 `multiBusinessDistrict` 参数生效且结果被区级过滤。
- [x] T049 真实智联冒烟：本地已登录智联专用 Chrome，选择“北京 · 朝阳区”，确认 `S_SOU_WORK_CITY` 使用区县码且空态检测正常。

## Dependencies & Execution Order

- T001 → T002-T018 基础层（并行）→ US1（T019-T024）→ US2（T025-T033）→ US3（T034-T041）→ T042-T049。
- T002/T003 必须先于 T015 静态数据测试；T004-T011 必须先于 T016-T018 接口/scope 测试。
- T027 依赖 T010/T029 的 `_combo_hash` 实现；US2 依赖 T010/T011 完成；US3 依赖 T002/T004/T021 完成。
- T042-T049 依赖 US1-US3 全部完成后执行。

## Parallel Opportunities

- Phase 2 的 T002-T018 大部分可并行；T003 依赖 T002。
- US1 的组件/测试可并行；US2/US3 在基础层完成后可先后串行接线。
- T042-T049 按门禁顺序执行，不并行。
