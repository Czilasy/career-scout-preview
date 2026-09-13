# Implementation Plan: 步骤页切换状态交接安全（B098 + B093 + B092）

**Branch**: `041-step-switch-state-safety` | **Date**: 2026-09-12 | **Spec**: `specs/041-step-switch-state-safety/spec.md`

**Input**: Feature specification from `/specs/041-step-switch-state-safety/spec.md`

## Summary

三条 P1 bug 合并立项，统一设计原则「各管各的，互不打扰」：

- **B098**（A/B/C/D 四块）：步骤页切换把该页整块拆掉重建、页面内现场丢失；切画像该清没清、开新轮旧轮降级口径不齐；灵动岛点击落点错位（暂停/出错不按真实进度派生、历史轮查看时点灵动岛被清成空白上传页、启动占位吞锁点击）；补抓/重抓自动切 03 再切回 04 重置现场。
- **B093**：简历分析中同步 await 锁住用户，分析完成强制切 02 致状态错乱、灵动岛误报。
- **B092**：只抓取流程结果页确认筛选条件时第七类「招聘者活跃时间」未跟前六类一起出现。

技术路线：现场外置存档（按「画像+轮次+平台」身份键，复用既有 sessionStorage 快照通道 + 模块级单例模式）；四步骤页 `v-if` 改 `v-show`/`KeepAlive` 防卸载；灵动岛落点由任务真实进度派生；补抓/重抓不切页后台跑；简历分析改后台异步按进度落点；第七类条件复用既有 `filterGroups`（从 schema 派生）核实并接入。优先复用 035 任务状态恢复、036/037 灵动岛状态机、028 第七类判定能力，不新增数据库表。

## Technical Context

**Language/Version**: 前端 TypeScript 5 + Vue 3（`<script setup>`）；后端 Python 3.11（uv 管理）。

**Primary Dependencies**: 前端 Vue 3 响应式 + `motion-v` 动画 + `@lucide/vue` 图标 + `vitest` 测试；后端 FastAPI + SQLite。

**Storage**: 前端 sessionStorage（既有 workflow 快照通道，`career-scout-workflow:<profileId>`）+ localStorage（既有 finished state 模式）+ 模块级单例内存态（既有 `resultHistory`/`useLocationDraft` 先例）；后端 SQLite（既有 `/api/latest-running-task`、`/api/latest-pipeline-result`、`/api/result-history*` 接口，不新增表）。

**Testing**: 前端 `vitest`（`webui/` 下 `npm test`）+ 构建 `npm run build`；后端 `uv run python -m unittest discover -s tests`；仓库卫生 `tests.test_repo_hygiene`。

**Target Platform**: Windows 桌面端（EXE）+ 浏览器开发态；Vue 单页应用。

**Project Type**: 桌面/Web 混合应用（前端 SPA + Python 后端服务）。

**Performance Goals**: 切页瞬时（现场从内存恢复，无网络请求）；补抓/重抓后台跑不阻塞 UI；灵动岛点击落点 <100ms 响应。

**Constraints**: Vue 单文件组件 ≤1200 行（红线）、Python 业务文件 ≤800 行（红线）；现场懒存（只存实际翻过的历史轮次，不无限膨胀）；选中岗位按身份跟随不按列表位置丢。

**Scale/Scope**: 涉及 1 个超红线视图（`DiscoveryView.vue` 1215 行）、1 个超红线 composable（`useDiscoveryState.ts` 1328 行）、6 个接近红线文件；前端为主，后端仅核实第七类 schema。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结果 |
|---|---|---|
| I. 职责分层 | 现场/落点/分析流程逻辑落独立 composable，门面文件只接线 | PASS（新模块各司其职） |
| II. 单文件尺寸边界 | `DiscoveryView.vue`(1215) 与 `useDiscoveryState.ts`(1328) 已超红线 | **VIOLATION**（见复杂性追踪表） |
| III. 引用方向 | view→composables→api/client 单向；新 composable 不反向依赖 view | PASS |
| IV. 拆分与重构纪律 | 本 Spec 是 bug 修复非纯重构；接线性改动最小化，不夹带拆分 | PASS（拆分另立方向） |
| V. 验证门禁 | 功能交付走聚焦测试+全量后端+前端测试+构建+卫生 | PASS（见 Verification Gate） |
| VI. 模块地图与落位规则 | 新 composable 登记进模块地图；落位按域 | PASS（见下） |
| VII. 错误处理与可观测性 | 不新增纯 pass 吞异常；分析失败按真实状态展示不误报 | PASS（B093 核心约束） |

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`.*

> 用户已明确授权「做完前置到 tasks 后停止」，文件边界以下清单为冻结方案，tasks 阶段据此拆分；实现阶段在 tasks 完成后的强制停滞点等待用户最终指令。

### 新建文件

| 路径 | 一句话职责 | 预估行数 |
|---|---|---|
| `webui/src/composables/useDiscoverySceneState.ts` | 步骤页现场外置存档：按「画像+轮次+平台」身份键存取页面内现场（滚动/选中/草稿/详情开合/批次/面板展开/画像高度/已加载目录），复用 sessionStorage 通道 + 模块级单例；切页/翻历史/刷新保留，切画像/开新轮/切平台清；历史轮懒存无草稿只留查看现场 | ~280 |
| `webui/src/composables/useIslandNavigation.ts` | 灵动岛点击落点派生：按任务真实进度派生目标页（空闲→01/分析中→01/抓取→02/筛选→03/跑完→04/暂停·出错→卡住那步页）；点灵动岛退出历史回当前轮且历史现场原地保留；启动占位点击防吞锁；硬红线「任何路径不清成空白上传页」 | ~160 |
| `webui/src/composables/useResumeAnalysisFlow.ts` | 简历分析后台异步流程：上传点分析后不锁用户、后台照跑、分析中可点灵动岛/翻历史；完成按真实进度落点（完→02、没完→01 显示分析中）；失败按真实失败展示不误报 | ~190 |
| `webui/src/composables/__tests__/useDiscoverySceneState.spec.ts` | 现场存档聚焦测试：切页/翻历史/刷新保留、三主体变清、历史轮懒存无草稿、选中按身份跟随 | ~220 |
| `webui/src/composables/__tests__/useIslandNavigation.spec.ts` | 落点派生聚焦测试：各态落点正确、历史退出保留、占位防吞锁、硬红线 | ~160 |
| `webui/src/composables/__tests__/useResumeAnalysisFlow.spec.ts` | 分析流程聚焦测试：不锁、后台跑、按进度落点、失败不误报 | ~150 |

### 修改文件

| 路径 | 改动要点 | 行数变化 |
|---|---|---|
| `webui/src/views/DiscoveryView.vue` (1215, **超红线**) | 四步骤页 `v-if`→`v-show`/`<KeepAlive>` 防卸载；接 `useDiscoverySceneState` 现场 ref 外置；接 `useIslandNavigation` 胶囊 navigate 处理；接 `useResumeAnalysisFlow`；补抓/重抓按钮不切页（`startRecrawl` 留 04）；切画像清理草稿/历史抽屉/页面四现场；03 页标题「确认 6 类」去硬编码数字 | 净增可控（接线为主，现场逻辑在新 composable） |
| `webui/src/composables/useDiscoveryState.ts` (1328, **超红线**) | `filterGroups` 接入第七类（核实 schema 已含 recruiter_activity，前端无障碍渲染）；`screenSummaryChips` 含第七类；场景现场相关 ref 上提到 `useDiscoverySceneState`（减负）；`capsuleNavigationTarget` 接落点派生 | 净减（ref 外置减负） |
| `webui/src/composables/useScreenRoundFlow.ts` (719) | `startRecrawl` 不再 `activeStep="screen"`（留 04，后台跑由灵动岛体现进度）；重抓完成原地增量（沿用 resultEpoch + jobKey 签名机制） | ~+10 |
| `webui/src/composables/useDiscoverySearch.ts` (618) | `analyzeResume` 改调 `useResumeAnalysisFlow`（后台异步、不锁、按进度落点）；`applyResumeAnalysisToCurrentSchema` 含第七类投影 | ~+20 |
| `webui/src/composables/useDiscoveryWorkflow.ts` (313) | `persistWorkflowState`/`restoreWorkflowState` 扩展纳入页面内现场快照（复用既有 sessionStorage 通道与版本号）；切画像守卫触发清理 | ~+30 |
| `webui/src/components/JobWorkspace.vue` (650) | 现场 ref（`sortKey`/`filterState`/`localSelectedId`/`detailOpen`/`userSelectedDetail`/`visibleCount`/`jdScrollEl`）外置到 `useDiscoverySceneState`，组件挂载从存档恢复、卸载不丢 | ~+40（外置接线） |
| `webui/src/components/LocationPicker.vue` (505) | 现场 ref（`open`/`districts`/`loading`/`cityCode`）外置到 `useDiscoverySceneState`，已加载目录缓存复用 | ~+30 |
| `webui/src/components/DynamicIsland.vue` (1005) | `stateTarget` 接 `useIslandNavigation` 派生；胶囊 `@navigate` emit 经新 composable 处理（退出历史回当前轮）；「分析中」态支持 | ~+15 |
| `webui/src/components/CollapsibleCard.vue` (57) | 收起不再强制 `scrollTop=0`（现场保留）；滚动位置存档 | ~+10 |
| `webui/src/composables/useDiscoveryTasks.ts` (1065) | `resetWorkflowInternal` 开新轮时旧轮查看现场降级保留（不清历史轮现场）、新轮页面四回默认；`pollTask` 重抓完成不强制切 04（原地增量） | ~+15 |
| `webui/src/composables/useDiscoveryResults.ts` (788) | `enterHistoryRound`/`returnToLatest` 历史轮查看现场懒存与还原；切画像清理挂钩 | ~+20 |
| `webui/src/composables/useDiscoveryExecution.ts` (1230) | `restoreRunningTask`（L116-488）恢复时检测分析中任务接 `useResumeAnalysisFlow.phase`（刷新后接续分析中态，落点按真实进度，spec FR-019 边缘场景） | ~+15 |
| `webui/src/components/IslandNoticePanel.vue` (305) | 通知面板行点击经 `useIslandNavigation.navigate`（规矩同胶囊，spec FR-014） | ~+5 |
| `webui/src/types.ts` | `IslandPhase`/`DynamicIslandState` 增 `analyzing` 态（如 037 状态机未含）；`CapsuleTarget` 派生口径 | ~+10 |
| `webui/src/composables/useIslandCarousel.ts` (216) | `deriveLiveState` 支持 `analyzing` 态派生（胶囊显示「分析中」，点击回 01） | ~+8 |

### 禁止修改文件

- 门面文件：`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`（仅 re-export，本 Spec 无拆分豁免）。
- 抓取脚本域：`scripts/boss/`、`scripts/zhilian/`（B098/D/B093 属前端现场与流程，不碰抓取实现）。
- 后端核心业务逻辑：`webui/recruiter_activity.py`（028 已实现第七类判定，本 Spec 只核实 schema 接入，不改判定）。
- 既有 035/036/037/028 落地模块的行为契约（只复用不回退）。

### 引用方向

```text
view (DiscoveryView) → composables (useDiscoverySceneState/useIslandNavigation/useResumeAnalysisFlow/useScreenRoundFlow/useDiscoveryWorkflow/...)
  → useDiscoveryState (共享 ref) → api client (api.ts)
components (JobWorkspace/LocationPicker/DynamicIsland/CollapsibleCard) → composables (现场/落点/分析流程)
```

新增 composable 单向被 view 与 component 调用，不反向依赖 view；`useDiscoverySceneState` 复用 `useDiscoveryWorkflow` 的 sessionStorage 通道（同层 composable 互调经 deps 契约，沿用 `discoveryDeps.ts` 模式）。

### 行数门禁

- `DiscoveryView.vue` 1215→接线后净增须严格控制（现场逻辑外置后，接线行应≤30 行净增）；若突破需在复杂性追踪表登记并开拆分方向。
- `useDiscoveryState.ts` 1328→ref 外置后应净减；`filterGroups` 改动是改既有 computed 不新增大逻辑。
- 新建 composable 单独红线内（≤800 行 TS 无硬红线，但自我约束 ≤300 行）。

### 复杂性追踪表

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `DiscoveryView.vue` 超红线仍做接线性修改 | B098 A 块根因即四步骤页 `v-if`（L633/689/900/967），不改该文件无法修「切页拆掉重建丢现场」；C 块灵动岛 navigate 处理、D 块补抓不切页、B 块切画像清理均需在此接线 | 纯新 composable 无法替代——v-if→v-show/KeepAlive 必须改模板；现场外置虽落 composable，但 view 需引用其 ref 与恢复钩子。接线性改动（引用+少量 watch）非「追加新业务逻辑」，符合宪法原则 II「拆分只搬代码不改行为」精神反推：bug 修复的必要模板改动豁免追加禁令，但同步登记拆分方向（见 Follow-up） |
| `useDiscoveryState.ts` 超红线仍改 `filterGroups` | B092 第七类接入必须改该 computed（从 schema 派生已无排除，需核实并确保渲染）；场景现场 ref 外置是减负不是加负 | `filterGroups` 是既有 computed，B092 是补全其应有行为非新增逻辑；现场 ref 外置到新 composable 后该文件净减 |

**Follow-up（不在本 Spec 内执行，登记为方向）**：`DiscoveryView.vue` 与 `useDiscoveryState.ts` 拆分应单独立项 Spec（符合宪法原则 IV「拆分必须单独建立 Spec」）；本 Spec 落地时顺手做零引用盘点确认无孤儿，并在收尾更新模块地图。

## 返工修订（2026-09-13）

> 触发：静态审查发现实现与 Spec 不一致（现场身份不稳定、运行日志跨画像取任务、task-state 无画像校验、
> 画像框高度空壳字段、文件规模违约、验收记录矛盾）。本次返工改实现与工件，不改冻结需求。

### 现场身份（FR-004/FR-005 的实现修订）

- 原实现用 `pipelineResultRunId || screenTaskId || scrapeTaskId || "draft"` 充当 `runEpoch`：同一轮会随
  阶段推进换键（draft → 抓取任务号 → 筛选任务号 → 结果 run id），组件把「任务推进」当成「换了新现场」，
  于是恢复成默认空现场——即使已用 `v-show`，卡片展开、滚动、城市面板、选中与详情的现场仍会丢。
- 现实现：轮次身份由 `useDiscoverySceneState` 生成一次并写入会话存档
  （`career-scout-round-epoch:<profileId>`），跨刷新稳定；只有切画像、开新一轮（`rotateRoundEpoch`）、
  切平台才换身份；开新一轮先 `archiveCurrentForNewRound(旧身份, 结果 run id)` 归档再换发新身份。
- 新增 `useDiscoverySceneIdentity.ts`（身份派生 + 结果 run id 登记）、`useProfileInputScene.ts`
  （画像框高度现场，字段真正接通）、`useDiscoveryIslandBridge.ts`（灵动岛落点桥接，自
  `useDiscoveryState.ts` 外迁）、`useDiscoveryLogViewer.ts`（历史轮日志对话框现场）。

### 画像隔离（FR-006/FR-007 的补口）

- `LogViewerDialog` 增 `profileId`；运行日志查询改为 `/api/latest-running-task?profile_id=`；
  `App.vue → AppSettingsMenu → LogViewerDialog` 全链传当前画像；切画像关窗并清任务号/日志/轮询。
- `/api/task-state/<run_id>` 支持 `profile_id` 校验（内存任务 + DB 结果轮）；跨画像按「不存在」处理
  （404 `run_not_found`，不泄漏存在性）；无归属老任务对所有画像可见（与历史结果同一兼容口径）；
  不带画像的查询保留为兼容口径，产品前端调用一律携带画像。

### 行数门禁实测（诚实记录，未达标）

| 文件 | plan 基线 | 返工前 | 返工后 | 结论 |
|---|---|---|---|---|
| `webui/src/views/DiscoveryView.vue` | 1215（超红线） | 1200 | 1200 | 回到红线内（=1200，未突破） |
| `webui/src/composables/useDiscoveryState.ts` | 1328（超红线） | 1467 | 1418 | **仍超线且未净减**（比基线多 90 行） |

- 返工把 Spec041 新增的灵动岛导航桥接整体外迁，`useDiscoveryState.ts` 比返工前减 49 行；但相对 plan
  基线仍是净增，「现场 ref 外置后净减」**未达成**。该文件拆分需单独立项 Spec（宪法原则 IV），
  本 Spec 记为未完成项，不宣称目标达成、不在本文件内继续追加逻辑。
- 新增/外迁模块行数：`useDiscoverySceneState.ts` 428（含轮次身份，超出原估 ~280）、
  `useDiscoveryIslandBridge.ts` 81、`useDiscoverySceneIdentity.ts` 50、`useProfileInputScene.ts` 40、
  `useDiscoveryLogViewer.ts` 35。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）。
- 只有 Spec 明确写入或用户明确要求时，收口任务才执行全量测试。
- 界面类验收 MUST 按模拟用户视角真实操作路径逐条走查，静态读码不能代替界面走查（035 教训）。

## Project Structure

### Documentation (this feature)

```text
specs/041-step-switch-state-safety/
├── plan.md              # 本文件
├── research.md          # Phase 0 研究产出（代码现状与修改点定位）
├── data-model.md        # Phase 1 现场存档数据模型与身份键
├── quickstart.md        # Phase 1 验证走查指南
├── contracts/           # Phase 1 接口契约
│   ├── scene-state.md   # 现场存档契约
│   ├── island-nav.md    # 灵动岛落点契约
│   └── resume-flow.md   # 简历分析流程契约
├── checklists/
│   └── requirements.md  # 需求检查清单
└── tasks.md             # Phase 2 任务拆分（speckit-tasks 产出）
```

### Source Code (repository root)

```text
webui/src/
├── views/
│   └── DiscoveryView.vue          # 步骤页壳（v-if→v-show/KeepAlive + 接线）
├── composables/
│   ├── useDiscoverySceneState.ts  # [新] 现场外置存档
│   ├── useIslandNavigation.ts     # [新] 灵动岛落点派生
│   ├── useResumeAnalysisFlow.ts   # [新] 简历分析后台异步
│   ├── useDiscoveryState.ts       # 状态家（filterGroups 接第七类 + ref 外置减负）
│   ├── useDiscoveryWorkflow.ts    # 快照通道扩展（纳入现场）
│   ├── useScreenRoundFlow.ts      # startRecrawl 不切页
│   ├── useDiscoverySearch.ts      # analyzeResume 改后台异步
│   ├── useDiscoveryTasks.ts       # 开新轮现场降级、重抓完成原地增量
│   ├── useDiscoveryResults.ts     # 历史轮现场懒存、切画像清理
│   └── useIslandCarousel.ts       # analyzing 态派生
├── components/
│   ├── JobWorkspace.vue           # 现场 ref 外置
│   ├── LocationPicker.vue         # 现场 ref 外置（目录缓存）
│   ├── DynamicIsland.vue         # navigate 落点接线 + 分析中态
│   └── CollapsibleCard.vue        # 收起不强制归零滚动
└── types.ts                       # analyzing 态类型

tests/                              # 后端第七类 schema 核实测试（如需）
```

**Structure Decision**: 沿用既有 Vue SPA + composable 分层（view→composables→api），不引入新框架（无 pinia、无 vue-router 步骤页路由）。三个新 composable 按职责域落位，符合宪法模块地图「找不到对应域才允许开新文件」——现场存档、落点派生、分析异步流程均为本 Spec 首次出现的全新领域，开新文件并在收尾登记进 `constitution.md` 模块地图。

## Complexity Tracking

> 见上文 File Boundaries · 复杂性追踪表（两处超红线豁免已登记）。
