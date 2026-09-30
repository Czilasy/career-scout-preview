# Implementation Plan: 多平台主流程分轨合流与统一条件映射（B096 V2）

**Branch**: `codex/feature/b096-parallel-platform-flow` | **Date**: 2026-09-27 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/046-parallel-platform-flow/v2/spec.md` 与冻结映射 `contracts/filter-mapping.md`

## Summary

在 V1 已完成的 Flow/Track、双平台执行、独立操作、历史和结果归属之上做增量修复，不重建后台流程。前端新增两个深模块：一处维护“全部”六类条件及确定性平台映射，一处把持久化 Track 事实投影成页面解锁、自动前进、真实任务进度和结果加入事件。现有 `ParallelPlatformProgress.vue` 降为原 `TaskProgress.vue` 的纵向编排壳；现有结果加载增加“同轮合流时保留阅读现场”模式。常用配置增加版本化条件快照；运行历史复用 `flow_tracks.confirmed_filters_snapshot` 保存 V2 快照信封，不新增 Flow 表结构。

## Technical Context

**Language/Version**: Python 3.10+；TypeScript 5.9；Vue 3.5
**Primary Dependencies**: Flask 3.x、SQLite、Vue 3、Vite 8、Vitest 4、pywebview 6
**Storage**: 正式 SQLite；现有 `flows`/`flow_tracks`；`search_packages` 增加一个 JSON 快照列
**Testing**: Python `unittest`；Vitest + Vue Test Utils；Vite 构建；真实源码运行与打包 EXE 主流程
**Target Platform**: 本地 Web 工作台与 Windows EXE；macOS 构建不在本轮真实验收范围
**Project Type**: Python 本地服务 + Vue 单页工作台 + pywebview 桌面壳
**Performance Goals**: 轮询沿用 2 秒节奏；第二平台到达后在下一次轮询内进入对应页面/结果；映射为纯内存同步操作
**Constraints**: 不等待慢平台；不重载结果页；不重做进度条；不读取旧设计图；不修改平台抓取树枝和 B106；刷新不自动跳页
**Scale/Scope**: 当前仅 BOSS、智联两平台；六个共同字段、两个平台专属字段、四个步骤页

## Constitution Check

### Plan 前检查

- **执行与授权**：用户已授权生成到 Tasks 结束；本阶段不进入实现。
- **复用优先**：复用 `TaskProgress.vue`、Flow/Track API、任务状态 API、结果投影、灵动岛入口和现有历史结构。
- **树干/树枝**：统一映射和页面投影属于树干；平台 schema 仍是树枝权威，树干只消费公开 schema 标签和值。
- **文件尺寸**：`DiscoveryView.vue` 当前 1199 行，只允许薄接线且最终必须净减少；不修改 1574 行的 `useDiscoveryState.ts`。
- **门面纪律**：`webui/store_migrations.py` 仅增加 mixin 组装；不向 `webui/app.py`、`webui/store.py`、`webui/exec_search_api.py` 追加逻辑。
- **测试层级**：开发阶段聚焦；整条链收敛后一次全量；真实源码和 EXE 验收不可由替身代替。
- **结论**：无宪法豁免；可进入研究与设计。

### Phase 1 后复查

- 新业务逻辑均落在新深模块或既有领域模块，引用方向无反转。
- Flow 运行历史使用现有 JSON 列；只有常用配置新增迁移 040，避免扩大核心 Flow schema。
- `TaskProgress.vue` 不改视觉和进度算法；并行壳只负责列表、阶段过滤和操作转发。
- V1 未冲突能力、单平台入口和 B106 保持不变。
- 结论：设计满足宪法，可进入 Tasks。

## File Boundaries

### Allowed files

计划与契约：

- `specs/046-parallel-platform-flow/INDEX.md`
- `specs/046-parallel-platform-flow/v2/plan.md`
- `specs/046-parallel-platform-flow/v2/research.md`
- `specs/046-parallel-platform-flow/v2/data-model.md`
- `specs/046-parallel-platform-flow/v2/quickstart.md`
- `specs/046-parallel-platform-flow/v2/contracts/filter-mapping.md`（冻结，只读）
- `specs/046-parallel-platform-flow/v2/contracts/condition-snapshot.md`
- `specs/046-parallel-platform-flow/v2/contracts/flow-presentation.md`
- `specs/046-parallel-platform-flow/v2/tasks.md`

产品代码：

- `.specify/memory/constitution.md`（仅登记新模块）
- `webui/flow_api.py`
- `webui/search_packages.py`
- `webui/store_search_packages.py`
- `webui/store_migrations.py`（仅组装）
- `webui/store_migrations_v8.py`
- `webui/src/parallelFilterMapping.ts`
- `webui/src/types.ts`
- `webui/src/components/OneClickScreenDialog.vue`
- `webui/src/components/ParallelPlatformProgress.vue`
- `webui/src/composables/useDiscoveryParallelFlow.ts`
- `webui/src/composables/useDiscoveryFlowPresentation.ts`
- `webui/src/composables/useDiscoveryIslandBridge.ts`
- `webui/src/composables/useDiscoveryResults.ts`
- `webui/src/composables/useSearchPackages.ts`
- `webui/src/composables/useTheme.ts`
- `webui/src/styles/theme.css`
- `webui/src/views/DiscoveryView.vue`
- `README.md`
- `CHANGELOG.md`

聚焦测试：

- `tests/test_b096_flow_api.py`
- `tests/test_b096_final_review.py`
- `tests/test_b096_flow_history.py`
- `tests/test_search_packages.py`
- `tests/webui_store/test_store_migrations.py`
- `webui/src/__tests__/parallelFilterMapping.spec.ts`
- `webui/src/components/__tests__/OneClickScreenDialog.spec.ts`
- `webui/src/components/__tests__/ParallelPlatformProgress.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryParallelFlow.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryIslandBridge.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryResults.spec.ts`
- `webui/src/composables/__tests__/useSearchPackages.spec.ts`
- `webui/src/composables/__tests__/useTheme.spec.ts`
- `webui/src/views/__tests__/DiscoveryRecovery.spec.ts`
- `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts`
- `webui/src/views/__tests__/DiscoveryView.spec.ts`

### Forbidden files

- `design/**`
- `specs/046-parallel-platform-flow/v1/**`
- `webui/src/composables/useDiscoveryState.ts`
- `webui/app.py`、`webui/store.py`、`webui/exec_search_api.py`
- `webui/flow_service.py`、`webui/flow_ai_coordinator.py`、`webui/store_flow*.py`
- `webui/runners/**`
- `scripts/boss/**`、`scripts/zhilian/**`、`scripts/boss_cdp_raw.py`
- B106 去重实现与测试
- `packaging/**`、版本提升、提交、推送和发布文件

### Pre-V2 baseline gate

- `pre-v2-protected.sha256` 冻结 V2 开始前已经存在、但不属于本 Plan 允许写入范围的 76 个工作区文件；这些文件即使原本已修改或未跟踪，V2 也不得继续改变其内容。
- `tests/test_b096_final_review.py` 必须读取该清单并校验每个文件的 SHA-256；同时读取 `git status --porcelain=v1 --untracked-files=all`，任何不在允许清单、也不在冻结清单中的新增差异都必须失败。
- `design/**` 遵守用户禁令，不读取、不哈希、不修改；该目录不作为实现参考。
- 基线清单只用于区分 V1 既有工作与 V2 本轮差异，不表示清单内文件已提交，也不改变其所有权或完成状态。

### New files

- `specs/046-parallel-platform-flow/v2/pre-v2-protected.sha256`：V2 实施前的只读工作区边界基线。
- `webui/src/parallelFilterMapping.ts`（约 250 行）：统一字段定义、冻结映射、schema 标签解析、单字段覆盖、简历语义投影和快照构造纯函数。
- `webui/src/composables/useDiscoveryFlowPresentation.ts`（约 320 行）：真实任务快照、阶段列表、稳定顺序、页面解锁/跳转抑制、结果加入信号。
- `webui/store_migrations_v8.py`（约 60 行）：迁移 040，为常用配置增加条件快照 JSON 列。
- `webui/src/__tests__/parallelFilterMapping.spec.ts`（约 220 行）：映射表全覆盖、互斥、覆盖与快照测试。
- `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts`（约 300 行）：两种完成顺序、近同时、返回、刷新、失败与一次性通知测试。

### Reference direction

```text
DiscoveryView
  ├─> useDiscoveryParallelFlow ─> API
  ├─> useDiscoveryFlowPresentation ─> task-state API / injected result refresh
  ├─> OneClickScreenDialog ─> parallelFilterMapping
  └─> ParallelPlatformProgress ─> TaskProgress

flow_api ─> FlowService ─> existing Flow store
search_packages_api ─> SearchPackageService ─> StoreSearchPackagesMixin
store_migrations facade ─> StoreMigrationsV8Mixin ─> older migration mixins
```

后端不得依赖前端；纯映射模块不得依赖 Vue 页面；组件不得直接访问 SQLite；平台树枝不得反向依赖统一映射。

### Line gate

- `DiscoveryView.vue` 实施后必须低于 1200 行且相对当前 1199 行净减少。
- 新 Vue 文件不超过 900 行预警线；新 Python 文件不超过 600 行预警线。
- `flow_api.py`、`search_packages.py`、`store_search_packages.py` 保持低于 800 行。
- `store_migrations.py` 只增加 import/MRO，不增加迁移业务逻辑。

### Rationale

条件映射和页面投影都是新的共享树干职责，塞入 `DiscoveryView.vue`、`useDiscoveryState.ts` 或平台模块都会违反尺寸和引用方向。Flow 条件历史可由现有 JSON 快照承载；常用配置是独立持久实体，需要一个可迁移、可校验的新 JSON 列。并行进度组件保留名称以兼容现有接线，但内部改成原进度组件的列表壳，避免第二套视觉和算法。

## Verification Gate

- 每个故事先写聚焦红测，再实施，再只跑该故事及直接相邻回归。
- US1：映射、无确认、主题、配置快照与迁移。
- US2：真实任务快照、两种先后顺序、近同时、独立操作和原进度条复用。
- US3：结果原地合流、阅读现场和一次性灵动岛提示。
- US4：02/03/04 刷新、已解锁入口、旧页停留、历史快照和流程隔离。
- 整条 V2 收敛后只运行一次：后端全量、前端全量、前端构建、卫生测试、`git diff --check`、`git status`。
- 自动化通过后必须经正式入口和正式运行库完成真实 BOSS/智联源码 E2E；再构建 EXE 并做同一路径最小充分真实验证。任一真实门禁未完成，只能报告“未验证”，不得称 V2 完成。

## Project Structure

### Documentation

```text
specs/046-parallel-platform-flow/v2/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── filter-mapping.md
│   ├── condition-snapshot.md
│   └── flow-presentation.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code

```text
webui/
├── flow_api.py
├── search_packages.py
├── store_search_packages.py
├── store_migrations.py
├── store_migrations_v8.py
└── src/
    ├── parallelFilterMapping.ts
    ├── types.ts
    ├── components/
    │   ├── OneClickScreenDialog.vue
    │   ├── ParallelPlatformProgress.vue
    │   └── TaskProgress.vue              # 只复用，不修改
    ├── composables/
    │   ├── useDiscoveryParallelFlow.ts
    │   ├── useDiscoveryFlowPresentation.ts
    │   ├── useDiscoveryIslandBridge.ts
    │   ├── useDiscoveryResults.ts
    │   ├── useSearchPackages.ts
    │   └── useTheme.ts
    ├── styles/theme.css
    └── views/DiscoveryView.vue

tests/
├── test_b096_flow_api.py
├── test_b096_final_review.py
├── test_search_packages.py
└── webui_store/test_store_migrations.py
```

**Structure Decision**: 保持现有 Python + Vue 双层项目结构。新模块放入现有 Flow 展示域和前端纯映射域；不新建平台适配层，不触碰抓取实现。

## Complexity Tracking

无需要豁免的宪法违反项。
