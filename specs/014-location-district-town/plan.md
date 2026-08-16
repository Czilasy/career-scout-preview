# Implementation Plan: 搜索地点支持区/镇（BOSS 区/商圈/镇 + 智联区/县）

**Branch**: `main`（沿用当前分支） | **Date**: 2026-08-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/014-location-district-town/spec.md`

## Summary

B054 把搜索地点从城市粒度扩展到区/镇：BOSS 支持“区 → 商圈/镇”，智联支持“区/县”。前端保持现有城市小方块样式，点击小方块后在下方展开地点选择面板；选中后小方块显示“城市 · 区”。多区通过“每区独立搜索组合”实现；任务范围、进度、历史与脚本参数全部使用结构化地点字段。静态码表为主、运行时拉取兜底；旧任务保持城市级兼容。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript + Vite（前端）

**Primary Dependencies**: Flask、SQLite、Vue 3、Vitest（现有工具链，不新增运行时依赖）

**Storage**: 不新增数据库表；地点随 `script_params` 与 `FrozenTaskScope` 结构化扩展；新增 `data/boss_business_districts.json`、`data/zhilian_districts.json` 静态码表。

**Testing**: 后端 `unittest`；前端 `vitest`；构建 `npm run build`；仓库卫生 `uv run python -m unittest tests.test_repo_hygiene`；真实浏览器冒烟 BOSS/智联各一次。

**Target Platform**: 本地 Web 工作台 / 桌面 EXE（pywebview）

**Project Type**: 单仓库 Web + 桌面壳应用

**Performance Goals**: 地点码表启动后内存缓存，接口命中缓存不重复拉平台；多区拆组合不新增轮询路径；任务进度沿用现有组合进度机制。

**Constraints**:

- 城市小方块外观、x 删除语义和整体布局不变；只新增点击展开和完整地点文字。
- 多区通过“每区独立搜索组合”实现，不依赖平台单次请求支持多区。
- 本轮每个地点条件最多一个商圈/镇；BOSS 平台的多商圈合并能力暂不暴露。
- 旧任务/旧草稿无地点字段时保持原样；scope 旧摘要必须可恢复。
- `webui/app.py`、`webui/store.py`、`DiscoveryView.vue`、`scripts/boss_cdp_raw.py` 等超大文件只允许最小接线，不追加业务逻辑。
- CLI 不新增公开参数；BOSS 参数以隐藏 CLI 参数透传。
- 组合键必须包含完整地点，避免同城多区冲突。
- 智联空结果检测继续使用真实城市码导航，区县码只用于 API 请求体。
- 超过 200 页上限明确提示，不自动截断。

**Scale/Scope**: 单用户本地工具；后端涉及地点目录、任务范围、脚本参数与组合展开，前端涉及城市小方块交互、结果页和历史详情展示。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- 职责分层：地点目录、地点范围、地点 API 分别落在 `location_catalog.py`、`location_scope.py`、`location_api.py`；前端逻辑落在 `location.ts`、`useLocationDraft.ts`、`LocationPicker.vue`。
- 单文件尺寸：`execution_config.py` 当前 780 行，本轮净增量 ≤20 行并保持 ≤800；其余超大文件按行数门禁最小改动。
- 引用方向：`app.py → location_api.py → location_scope.py / location_catalog.py`；`pipeline_exec.py → location_scope.py → location_catalog.py`；前端 `DiscoveryView.vue → useLocationDraft.ts → location.ts / api.ts`。
- 拆分纪律：不搬动既有函数；超大文件只做接线或参数透传；新逻辑全部落新模块。
- 验证门禁：最终按功能交付全量门禁执行，并补真实浏览器冒烟。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. 用户已确认文件边界；两轮审查发现的修正均已并入本清单。*

- **Allowed files**:
  - `data/boss_business_districts.json`（新增静态码表）
  - `data/zhilian_districts.json`（新增静态码表）
  - `webui/location_catalog.py`（新增）
  - `webui/location_scope.py`（新增）
  - `webui/location_api.py`（新增）
  - `webui/execution_config.py`（最小扩展，净增量 ≤20 行）
  - `webui/app.py`（注册蓝图 + 预览/执行透传，增量 ≤40 行）
  - `webui/pipeline_exec.py`（组合展开/run_search/`_combo_hash` 委托，增量 ≤40 行）
  - `webui/source.py`（`SCRAPER_FILTER_FIELDS` 透传，增量 ≤10 行）
  - `scripts/boss_cdp_raw.py`（隐藏 CLI 参数，增量 ≤15 行）
  - `scripts/zhilian_cdp_raw.py`（区县码 + 空态路由城市码，增量 ≤20 行）
  - `webui/src/types.ts`（地点类型，增量 ≤60 行）
  - `webui/src/discovery.ts`（`buildSearchScriptParams` 支持 locations，增量 ≤30 行）
  - `webui/src/location.ts`（新增）
  - `webui/src/composables/useLocationDraft.ts`（新增）
  - `webui/src/components/LocationPicker.vue`（新增）
  - `webui/src/views/DiscoveryView.vue`（小方块接线 + 向结果页传完整地点，增量 ≤70 行）
  - `webui/src/components/JobWorkspace.vue`（结果页完整地点展示，增量 ≤30 行）
  - `webui/src/components/ResultHistoryDrawer.vue`（历史详情完整地点展示，增量 ≤30 行）
  - 测试：`tests/test_location_catalog.py`、`tests/test_location_scope.py`、`tests/test_execution_config.py`、`tests/test_webui_app.py`、`webui/src/__tests__/location.spec.ts`、`webui/src/components/__tests__/LocationPicker.spec.ts`、`webui/src/components/__tests__/JobWorkspace.spec.ts`、`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`、`webui/src/composables/__tests__/useLocationDraft.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`
- **Forbidden files**:
  - `webui/store.py`、`webui/store_migrations.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/ai*.py`、`webui/tuning.py`、`webui/task_runners.py`、`webui/workbench.py`、`webui/error_registry.py`
  - `webui/platforms.py`、`webui/src/styles.css`、`webui/src/components/TaskProgress.vue`
  - `packaging/*`、`.github/*`、`README.md`、`CHANGELOG.md`（本轮不发布、不对外改文案）
- **New files**:
  - `data/boss_business_districts.json`：BOSS 区/商圈/镇静态码表快照。
  - `data/zhilian_districts.json`：智联区/县静态码表快照。
  - `webui/location_catalog.py`：码表加载/解析/校验/运行时兜底，250-350 行。
  - `webui/location_scope.py`：地点规范化、组合展开、scope 兼容、脚本参数翻译，250-350 行。
  - `webui/location_api.py`：`/api/location-catalog`、`/api/location/validate` 蓝图，100-150 行。
  - `webui/src/location.ts`：前端地点类型/标签/参数纯函数，150-250 行。
  - `webui/src/composables/useLocationDraft.ts`：按平台地点草稿、恢复、清空，150-250 行。
  - `webui/src/components/LocationPicker.vue`：城市小方块展开面板组件，样式 scoped，250-400 行。
  - 测试文件：`tests/test_location_catalog.py`、`tests/test_location_scope.py`、`webui/src/__tests__/location.spec.ts`、`webui/src/components/__tests__/LocationPicker.spec.ts`、`webui/src/components/__tests__/JobWorkspace.spec.ts`、`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`、`webui/src/composables/__tests__/useLocationDraft.spec.ts`
- **Reference direction**:
  - 后端：`app.py → location_api.py → location_scope.py / location_catalog.py`；`pipeline_exec.py → location_scope.py → location_catalog.py`；`source.py → boss_cdp_raw.py / zhilian_cdp_raw.py` 只透传参数。
  - 前端：`DiscoveryView.vue → useLocationDraft.ts → location.ts / api.ts`；`LocationPicker.vue → location.ts`；`DiscoveryView.vue → JobWorkspace.vue / ResultHistoryDrawer.vue` 只传展示文本。
- **Line gate**:
  - `execution_config.py` 净增量 ≤20 且总行数 ≤800；如实现确需更多行，必须先迁出同文件内非核心辅助到 `location_scope.py`，再保持总行数 ≤800。
  - `app.py` 增量 ≤40；`pipeline_exec.py` 增量 ≤40；`source.py` 增量 ≤10；`boss_cdp_raw.py` 增量 ≤15；`zhilian_cdp_raw.py` 增量 ≤20；`types.ts` 增量 ≤60；`discovery.ts` 增量 ≤30；`DiscoveryView.vue` 增量 ≤70；`JobWorkspace.vue` 增量 ≤30；`ResultHistoryDrawer.vue` 增量 ≤30。
  - 新文件不超过宪法单文件上限。
- **Rationale**: 超大文件不得继续积累业务逻辑；地点是新域，必须独立成模块。两轮审查修正：scope 旧摘要兼容、组合键含地点、智联空态保留真实城市码、BOSS 参数双路径一致、`_combo_hash` 必须包含 `source_filters`、FR-014 结果页/历史详情补齐、商圈本轮按单值建模、`/api/location/validate` 增加 `scope_kind`、无数据与 503 语义分开、静态刷新范围明确。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- BOSS/智联真实浏览器冒烟：至少各一次，选择区/镇后确认请求参数或返回岗位确实被区级条件过滤。
- 收口发布任务不适用本门禁；按根 `AGENTS.md` 收口规则另行执行。

## Project Structure

### Documentation (this feature)

```text
specs/014-location-district-town/
├── spec.md                # 需求与验收
├── research.md            # Phase 0 决策
├── data-model.md          # 实体与校验
├── contracts/location-flow.md
├── quickstart.md          # 验证指南
├── plan.md                # This file
├── tasks.md               # Phase 2 output
└── checklists/requirements.md
```

### Source Code (repository root)

```text
data/
├── boss_business_districts.json
└── zhilian_districts.json

webui/
├── location_catalog.py          # 码表加载/解析/校验/兜底
├── location_scope.py            # 地点组合/scope 兼容/参数翻译
├── location_api.py              # /api/location-catalog、/api/location/validate
├── app.py                       # 蓝图注册 + 预览/执行透传（最小接线）
├── execution_config.py          # FrozenTaskScope locations 最小扩展
├── pipeline_exec.py             # expand_combinations/run_search/_combo_hash 委托
├── source.py                    # SCRAPER_FILTER_FIELDS 透传
└── src/
    ├── types.ts                 # LocationCondition 等类型
    ├── discovery.ts             # buildSearchScriptParams 支持 locations
    ├── location.ts              # 前端地点纯函数
    ├── composables/useLocationDraft.ts
    ├── components/LocationPicker.vue
    ├── components/JobWorkspace.vue        # 结果页完整地点展示
    └── components/ResultHistoryDrawer.vue # 历史详情完整地点展示

scripts/
├── boss_cdp_raw.py              # 隐藏 --multiBusinessDistrict
└── zhilian_cdp_raw.py           # 区县码 + 空态路由城市码

tests/
├── test_location_catalog.py
├── test_location_scope.py
├── test_execution_config.py
└── test_webui_app.py

webui/src/
├── __tests__/location.spec.ts
├── components/__tests__/LocationPicker.spec.ts
├── components/__tests__/JobWorkspace.spec.ts
├── components/__tests__/ResultHistoryDrawer.spec.ts
├── composables/__tests__/useLocationDraft.spec.ts
└── views/__tests__/DiscoveryView.spec.ts
```

**Structure Decision**: 沿用现有模块边界，新增独立地点域模块；超大文件只做最小接线，不搬既有逻辑；结果页/历史详情只做展示层透传，不新增后端历史接口字段。

## Complexity Tracking

> 无宪法违规；不填复杂度表。
