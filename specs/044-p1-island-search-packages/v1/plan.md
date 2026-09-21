# Implementation Plan: P1 灵动岛分析态修复与常用搜索配置包

**Branch**: `codex/spec/saved-search-packages` | **Date**: 2026-09-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/044-p1-island-search-packages/v1/spec.md`

## Summary

同一份 044 SPEC 完成两个 P1。第一项在 `DynamicIsland.vue` 为“分析中”补齐独立内容键与状态点配色，使状态切换立即重测宽度。第二项新增本地、平台无关的搜索配置包领域：用户只在第二页主动保存，第一页选择后完整回填第二页；第三页筛选、平台标识和自动保存均不进入配置包。

## Technical Context

**Language/Version**: Python >=3.10；Vue 3.5；TypeScript 5.9

**Primary Dependencies**: Flask 3.x、SQLite、Vue Composition API、现有 `apiRequest` 与 Discovery composables

**Storage**: SQLite 新表 `search_packages`，迁移 037；JSON 字段保存平台无关快照

**Testing**: Python `unittest`；Vitest + Vue Test Utils；最终以项目既有全量与构建门禁收口

**Target Platform**: Career Scout Windows/macOS 桌面应用内 WebUI

**Project Type**: 本地桌面 Web 应用，Python API/服务/存储 + Vue 前端

**Performance Goals**: 本地列表和单包加载在正常桌面数据库规模下即时完成；选择成功不触发 AI 或抓取网络调用

**Constraints**: 多包并列、无历史版本；不按平台拆分；全有或全无加载；不修改第三页筛选；不自动保存或自动搜索

**Scale/Scope**: 两个 P1；1 个灵动岛状态修复；单用户本地配置库；1 张表、5 个 REST 端点、2 个前端组件、1 个前端 composable

## Constitution Check

- **接口分层**: PASS。`search_packages_api.py → search_packages.py → store_search_packages.py`，路由不直接写 SQL。
- **前端分层**: PASS。`DiscoveryView.vue / components → useSearchPackages.ts → apiRequest`，组件不直接操作数据库语义。
- **树干/树枝**: PASS。数据模型与 UI 均不含 BOSS/智联字段或分支；两个平台读取同一快照。
- **页面红线**: 按用户最新指示，本 SPEC 不再为 `DiscoveryView.vue` 的既有行数问题建立或夹带拆分工作；只做配置包入口的最小接线。
- **超限模块保护**: PASS。禁止修改 `useDiscoveryState.ts`（1574 行）和 `App.vue`（945 行）；`DynamicIsland.vue` 只做 B099 的最小内容键与样式修复，不做结构扩张。
- **门面纪律**: PASS。`app.py` 只增加路由注册且必须保持 `<800`；`store.py` 只增加 mixin 组装。
- **验证节奏**: PASS。各阶段仅聚焦测试；两个 P1 全部收敛后只安排一次最终全量链路。
- **宪法修订**: 原则不变；实施时只更新模块地图。

## File Boundaries

- **Allowed files**:
  - `webui/store_migrations_v6.py`
  - `webui/store_migrations.py`
  - `webui/store_migrations_v1.py`
  - `webui/store_search_packages.py`
  - `webui/store.py`
  - `webui/search_packages.py`
  - `webui/search_packages_api.py`
  - `webui/app.py`
  - `webui/src/types.ts`
  - `webui/src/composables/useSearchPackages.ts`
  - `webui/src/components/SavedSearchPackagePicker.vue`
  - `webui/src/components/SavedSearchPackageSaveActions.vue`
  - `webui/src/components/DynamicIsland.vue`
  - `webui/src/components/__tests__/DynamicIsland.spec.ts`
  - `webui/src/views/DiscoveryView.vue`
  - 与上述新领域直接对应的新测试文件，以及 `tests/webui_store/test_store_migrations.py`
  - `.specify/memory/constitution.md`
  - `CHANGELOG.md`
  - `specs/044-p1-island-search-packages/**`
- **Forbidden files**:
  - `webui/src/composables/useDiscoveryState.ts`
  - `webui/src/composables/useDiscoveryWorkflow.ts`
  - `webui/src/composables/useDiscoverySearch.ts`
  - `webui/src/composables/useSearchDraftSlots.ts`
  - `webui/src/App.vue`
  - 所有 BOSS/智联平台模块、抓取脚本和第三页筛选模块
- **New files**:
  - `webui/store_search_packages.py`：配置包 CRUD 数据访问，预计 160–240 行。
  - `webui/search_packages.py`：快照归一化、完整性校验、默认命名与服务编排，预计 220–320 行。
  - `webui/search_packages_api.py`：配置包 REST 路由与错误映射，预计 140–220 行。
  - `webui/src/composables/useSearchPackages.ts`：列表、加载、始终新增保存、重命名、删除与原子应用，预计 180–280 行。
  - `webui/src/components/SavedSearchPackagePicker.vue`：第一页小入口与选择/管理弹框，预计 240–360 行。
  - `webui/src/components/SavedSearchPackageSaveActions.vue`：第二页统一保存与可编辑名称交互，预计 80–160 行。
  - `tests/test_search_packages.py`：服务、API 与存储聚焦测试，预计 350–550 行。
  - `webui/src/composables/__tests__/useSearchPackages.spec.ts`：前端领域逻辑测试，预计 260–420 行。
  - `webui/src/components/__tests__/SavedSearchPackagePicker.spec.ts`：选择框管理交互测试，预计 220–360 行。
  - `webui/src/components/__tests__/SavedSearchPackageSaveActions.spec.ts`：保存交互测试，预计 160–260 行。
  - `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts`：第一页到第二页的集成测试，预计 240–380 行。
- **Reference direction**: `app.py → search_packages_api.py → search_packages.py → store_search_packages.py → SQLite`；`DiscoveryView.vue → SavedSearchPackage* components → useSearchPackages.ts → apiRequest`。存储层和通用前端领域不得导入平台模块。
- **Line gate**: `DiscoveryView.vue` 只允许最小模板/接线变化且不承载新业务逻辑；`DynamicIsland.vue <1200`、`app.py <800`；全部新 Python 文件 `<600` 预警线、新 Vue 文件 `<900` 预警线。
- **Rationale**: 配置包业务逻辑全部进入新领域和组件，`DiscoveryView.vue` 只组装第一页入口与第二页保存动作；本轮不处理其既有行数问题，也不创建旁支 SPEC。

## Verification Gate

- 实施中按任务运行迁移、服务/API、composable、组件和页面旅程的聚焦测试；失败后只重跑失败用例和直接受影响回归。
- 本 SPEC 两个 P1 全部收敛后只运行一次最终门禁：
  1. `uv run python -m unittest discover -s tests`
  2. 在 `webui/` 执行 `npm test`
  3. 在 `webui/` 执行 `npm run build`
  4. `uv run python -m unittest tests.test_repo_hygiene`
  5. `git diff --check` 与 `git status --short`
- `tests/test_e2e_smoke.py` 仅是跨层自动化冒烟，不宣称真实浏览器 E2E；真实账号/真实数据 E2E 不属于本轮文档阶段，也不以未执行冒充通过。

## Project Structure

```text
webui/
├── app.py                                      # 修改：注册配置包路由
├── store.py                                    # 修改：组装配置包存储 mixin
├── store_migrations.py                         # 修改：迁移门面范围说明（若需要）
├── store_migrations_v1.py                      # 修改：迁移调度加入 037
├── store_migrations_v6.py                      # 修改：迁移 037
├── store_search_packages.py                    # 新增：数据访问
├── search_packages.py                          # 新增：领域服务
├── search_packages_api.py                      # 新增：HTTP API
└── src/
    ├── types.ts                                # 修改：共享类型
    ├── composables/
    │   ├── useSearchPackages.ts                # 新增：前端领域编排
    │   └── __tests__/useSearchPackages.spec.ts
    ├── components/
    │   ├── DynamicIsland.vue                   # 修改：分析中内容键与状态点颜色
    │   ├── SavedSearchPackagePicker.vue
    │   ├── SavedSearchPackageSaveActions.vue
    │   └── __tests__/
    │       ├── DynamicIsland.spec.ts           # 修改：B099 回归
    │       ├── SavedSearchPackagePicker.spec.ts
    │       └── SavedSearchPackageSaveActions.spec.ts
    └── views/
        ├── DiscoveryView.vue                   # 修改：最小接线
        └── __tests__/DiscoverySearchPackages.spec.ts

tests/
├── test_search_packages.py
└── webui_store/test_store_migrations.py
```

**Structure Decision**: 使用项目既有门面 + 领域模块 + Vue composable/组件结构；配置包作为树干领域，不进入 profiles 或任一平台树枝。

## Complexity Tracking

无额外 SPEC 依赖。`DiscoveryView.vue` 的既有行数问题不属于本轮两个 P1，不在当前 044 中拆分或另立 SPEC。
