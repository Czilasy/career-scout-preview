# Tasks: 步骤页切换状态交接安全（B098 + B093 + B092）

**Input**: Design documents from `/specs/041-step-switch-state-safety/`

**Prerequisites**: plan.md ✅、spec.md ✅、research.md ✅、data-model.md ✅、contracts/ ✅、quickstart.md ✅

**Tests**: 本任务清单包含聚焦测试任务（宪法原则 V 要求聚焦测试；三个新 composable 各配一套测试，界面走查见 quickstart.md）。

**Organization**: 按 spec.md 的 6 个 User Story 组织（均 P1），每个 US 可独立实现与测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属 User Story（US1-US6）
- 描述含精确文件路径

## File Boundaries

> 摘自 plan.md，任务执行前必读；完整清单与红线豁免见 plan.md「File Boundaries」与「复杂性追踪表」。

- **Allowed files**: `webui/src/views/DiscoveryView.vue`、`webui/src/composables/useDiscoveryState.ts`、`useDiscoveryWorkflow.ts`、`useScreenRoundFlow.ts`、`useDiscoverySearch.ts`、`useDiscoveryTasks.ts`、`useDiscoveryResults.ts`、`useIslandCarousel.ts`、`webui/src/components/JobWorkspace.vue`、`LocationPicker.vue`、`DynamicIsland.vue`、`CollapsibleCard.vue`、`IslandNoticePanel.vue`、`webui/src/types.ts`、`App.vue`（切画像接线）
- **New files**: `webui/src/composables/useDiscoverySceneState.ts`、`useIslandNavigation.ts`、`useResumeAnalysisFlow.ts` 及各自 `__tests__/*.spec.ts`
- **Forbidden files**: 门面（`app.py`/`store.py`/`source.py`/`boss_cdp_raw.py`/`zhilian_cdp_raw.py`）、抓取脚本域（`scripts/boss/`、`scripts/zhilian/`）、`webui/recruiter_activity.py`（028 已实现只核实不改）
- **Reference direction**: view → composables → api client；components → composables；新 composable 单向被调用，不反向依赖 view
- **Line gate**: `DiscoveryView.vue`（plan 基线 1215，现状 1200）与 `useDiscoveryState.ts`（plan 基线 1328，返工后 1418）已超红线，本 Spec 仅做接线性修改（现场逻辑外置到新 composable），拆分另立方向（见 plan 复杂性追踪表与「返工修订（2026-09-13）」实测表）

## Verification Gate (task-type aware)

- 功能交付：相关模块聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查。
- 收口发布任务：默认只跑卫生测试、hooks、`git diff --check`、`git status`、`scripts/release_check.ps1`（若存在）；不跑全量测试。
- 界面类验收 MUST 按真实用户操作路径走查（quickstart.md 六场景），静态读码不代替。

---

## Phase 1: Setup（核实前置）

**Purpose**: 核实后端第七类 schema 已含 recruiter_activity，为 US6 排除根因疑点。

- [x] T001 核实后端 `/api/filter-labels` schema 含 `recruiter_activity` field：检查 `webui/platforms_boss.py` 与 `webui/platforms_zhilian.py` 的 filter_schema.fields 是否含 recruiter_activity（028 已实现），若缺则记录为后端待补（不在本 Spec 改 recruiter_activity.py）

---

## Phase 2: Foundational（阻塞前置 · 三大新 composable + 类型 + 通道）

**Purpose**: 所有 User Story 共享的基础设施，MUST 完成后才能开始各 US。

**⚠️ CRITICAL**: 本阶段未完成前不得开始任何 User Story。

- [x] T002 [P] 新建 `webui/src/composables/useDiscoverySceneState.ts`：现场外置存档 composable，按「画像+轮次+平台」身份键存取 PageScene（getCurrent/saveCurrent/getHistory/saveHistory/clearForProfileSwitch/archiveCurrentForNewRound/restoreFromSession/persistToSession），契约见 `contracts/scene-state.md`
- [x] T003 [P] 新建 `webui/src/composables/useIslandNavigation.ts`：灵动岛落点派生 composable（deriveTarget/navigate/handleBootstrapClick），按任务真实进度派生目标页 + 历史退出保留 + 占位防吞锁 + 硬红线，契约见 `contracts/island-nav.md`
- [x] T004 [P] 新建 `webui/src/composables/useResumeAnalysisFlow.ts`：简历分析后台异步流程 composable（startAnalysis/phase/landOnReturn/onAnalysisComplete），不锁用户、后台照跑、按真实进度落点、失败不误报，契约见 `contracts/resume-flow.md`
- [x] T005 扩展 `webui/src/types.ts`：`IslandPhase` 增 `analyzing` 态；`DynamicIslandState` 增 analyzing capsule 态（progress 携带分析进度）；`CapsuleTarget` 派生口径对齐 `IslandNavTarget`
- [x] T006 扩展 `webui/src/composables/useDiscoveryWorkflow.ts`：`persistWorkflowState`/`restoreWorkflowState` 纳入 PageScene 现场（复用既有 sessionStorage 通道，`WORKFLOW_STATE_VERSION` 升至 2，旧版本缺现场字段回默认）；防完成态误恢复沿用 `isCompletedWorkflowSnapshot`

**Checkpoint**: 三大新 composable + 类型 + 通道就绪，User Story 实现可开始。

---

## Phase 3: User Story 1 - 切页/翻历史/刷新不丢现场（B098 · A 块）(Priority: P1) 🎯 MVP

**Goal**: 步骤页内部现场（滚动/选中/草稿/详情开合/批次/面板展开/画像高度/目录）切页/翻历史/刷新后原样保留。

**Independent Test**: 构造页面四滚到第 3 批、选中第 5 条、详情展开、筛选草稿未确认 → 切页面二再回 → 现场全保留；翻历史第 N 轮再回 → 当前轮原样接上；历史第 N 轮再翻 → 接上次查看现场。

### Tests for User Story 1

- [x] T007 [P] [US1] 新建 `webui/src/composables/__tests__/useDiscoverySceneState.spec.ts`：切页/翻历史/刷新保留、历史轮懒存无草稿、身份键变更触发清理、选中按身份跟随（先写测试使其 FAIL）

### Implementation for User Story 1

- [x] T008 [US1] 改 `webui/src/views/DiscoveryView.vue` 四步骤页（L633/689/900/967 区域）`v-if`/`v-else-if`/`v-else` → `v-show` 或包 `<KeepAlive>`，切页不卸载组件
- [x] T009 [US1] 改 `webui/src/components/JobWorkspace.vue`：现场 ref（`sortKey`/`filterState`/`localSelectedId`/`detailOpen`/`userSelectedDetail`/`visibleCount`/`jdScrollEl`）读写走 `useDiscoverySceneState` 的 getCurrent/saveCurrent，挂载恢复、卸载不丢；保留 `resultEpoch`+`prevJobKeySignature` 语义不破坏
- [x] T010 [US1] 改 `webui/src/components/LocationPicker.vue`：现场 ref（`open`/`districts`/`loading`/`cityCode`）外置到现场存档，已加载区县目录缓存复用（不重复请求）
- [x] T011 [US1] 改 `webui/src/components/CollapsibleCard.vue`：收起不再强制 `scrollTop=0`（L23-25 watch 调整），滚动位置存档到现场
- [x] T012 [US1] 改 `webui/src/composables/useDiscoveryResults.ts` `enterHistoryRound`/`returnToLatest`（L390-480）：历史轮查看现场懒存与还原（getHistory/saveHistory），当前轮现场不被历史轮顶替丢失

**Checkpoint**: US1 独立可用——切页/翻历史/刷新现场保留。

---

## Phase 4: User Story 2 - 只有主体身份变才清（B098 · B 块）(Priority: P1)

**Goal**: 现场绑身份，只有切画像/开新轮/切平台三场景清，其他不清；清后达默认态。

**Independent Test**: 画像 A 有历史 3 轮+现场+城市草稿 → 切画像 B → 历史空/城市默认/页面四空；切回 A → 原样恢复。开新轮 → 旧轮降级保留查看现场、新轮回默认。

### Implementation for User Story 2

- [x] T013 [US2] 改 `webui/src/App.vue` `selectProfile`（L507-510）与 `webui/src/views/DiscoveryView.vue` `watch(() => props.profileId)`（L481-485）：切画像触发 `useDiscoverySceneState.clearForProfileSwitch()`，清理历史抽屉（`resultHistory`）、城市草稿（`useLocationDraft`）、页面四现场；新画像从干净默认开始
- [x] T014 [US2] 改 `webui/src/composables/useDiscoveryTasks.ts` `resetWorkflowInternal`（L939-1012）：开新轮调 `archiveCurrentForNewRound(旧runEpoch)`——旧轮 current 降级为 history[旧runId]（查看现场保留），新轮 current 回默认（默认排序/选中第一条/滚到顶/无草稿）；旧轮已确认筛选条件跟旧轮走不串新轮
- [x] T015 [US2] 验证切平台现场按 `identity.platform` 身份键隔离（`useDiscoverySceneState` 天然按平台区分），BOSS 草稿不串智联；补 `useLocationDraft`/`filterValues` 既有双槽隔离回归
- [x] T016 [US2] 验证三主体清后达默认态（该轮真实结果+默认排序+选中第一条+滚到顶+无未确认草稿），切页/翻历史/刷新/后台冒泡不清

**Checkpoint**: US2 独立可用——只有三主体变才清，清后达默认。

---

## Phase 5: User Story 3 - 灵动岛各态落点正确（B098 · C 块）(Priority: P1)

**Goal**: 灵动岛胶囊/通知行点击按任务真实进度派生落点，历史退出保留现场，占位防吞锁，硬红线不清成空白上传页。

**Independent Test**: 当前轮跑完+翻历史第 3 轮 → 点灵动岛 → 切回第 8 轮页面四、历史现场原地保留；任务暂停在抓取 → 点灵动岛 → 落页面二不落未启用页面三。

### Tests for User Story 3

- [x] T017 [P] [US3] 新建 `webui/src/composables/__tests__/useIslandNavigation.spec.ts`：各态落点正确（idle/analyzing/running·scraping/running·screening/completed/paused·stuck/error·stuck）、历史退出保留、占位防吞锁、硬红线 navigate 不触发 reset（先写测试 FAIL）

### Implementation for User Story 3

- [x] T018 [US3] 改 `webui/src/components/DynamicIsland.vue` `stateTarget` computed（L124-131）：改调 `useIslandNavigation.deriveTarget`，按真实进度派生（暂停/出错落卡住那步页，不无脑送固定页）
- [x] T019 [US3] 改 `webui/src/components/DynamicIsland.vue` `onPillClick`（L358-368）与 `onRowClick`（L370-373）：emit navigate 经 `useIslandNavigation.navigate` 处理，历史模式先 `onExitHistory`（returnToLatest，历史现场原地保留）再落目标页；硬红线 navigate 永不调 resetWorkflow
- [x] T020 [US3] 改 `webui/src/composables/useIslandCarousel.ts` `deriveLiveState`（L75-107）：支持 `analyzing` 态派生（capsule.state=analyzing → phase=analyzing）；`IslandPhase`（L26）已由 T005 增 analyzing
- [x] T021 [US3] 改 `webui/src/components/IslandNoticePanel.vue` 行点击：emit row-click 经 `useIslandNavigation.navigate`（规矩同胶囊，spec FR-014）
- [x] T022 [US3] 改 `webui/src/views/DiscoveryView.vue` `capsuleNavigationTarget` watch（L908-934）接 `navigate` 的 `onSetActiveStep`；启动占位期间点击接 `handleBootstrapClick`（等加载完执行 / 不响应但放行下一次，spec FR-013）

**Checkpoint**: US3 独立可用——灵动岛各态落点正确、硬红线无违反。

---

## Phase 6: User Story 4 - 补抓/重抓不切页（B098 · D 块）(Priority: P1)

**Goal**: 点「补抓 JD」/「全部重抓」不切页、留页面四、后台跑、灵动岛体现进度、完成原地增量更新。

**Independent Test**: 页面四滚到第 3 批/选中第 5 条/详情展开 → 点补抓 → 不切页、灵动岛显示进度、现场不动 → 完成后第 5 条内容更新、现场保留。

### Implementation for User Story 4

- [x] T023 [US4] 改 `webui/src/composables/useScreenRoundFlow.ts` `startRecrawl`（L468-480）：移除显式平台时 `activeStep="screen"`（留页面四），后台跑由灵动岛 running 态体现进度；`recrawlUncertain` 内部切 03 逻辑同步移除（核实 `useDiscoveryTasks.ts` recrawlUncertain 实现）
- [x] T024 [US4] 改 `webui/src/composables/useDiscoveryTasks.ts` `pollTask`（L75-324）重抓完成分支（L170-205）：不强制 `activeStep="results"`（用户在页面四则原地增量），拉 `fetchMergedLatestResult`+`setPipelineResult` 更新结果
- [x] T025 [US4] 验证 `webui/src/components/JobWorkspace.vue` 重抓完成选中按身份跟随：沿用 `jobKey`（L195-203 `platform:platform_job_id`）+ `resultEpoch`+`prevJobKeySignature`（L179-193）机制，岗位消失回第一条并提示（spec 边缘场景）

**Checkpoint**: US4 独立可用——补抓/重抓不切页、现场保留、原地增量。

---

## Phase 7: User Story 5 - 简历分析中不锁（B093）(Priority: P1)

**Goal**: 上传简历点分析不锁用户、后台照跑、分析中可点灵动岛/翻历史；回当前流程按真实进度落点；完成自动进 02；失败不误报。

**Independent Test**: 上传简历点分析、分析中 → 去历史翻第 5 轮 → 分析后台继续 → 点回到最新：完→落页面二、没完→落页面一显示分析中；分析完自动进页面二。

### Tests for User Story 5

- [x] T026 [P] [US5] 新建 `webui/src/composables/__tests__/useResumeAnalysisFlow.spec.ts`：不锁（startAnalysis 不阻塞）、后台跑、按进度落点（succeeded→02/analyzing→01/failed→01真实失败）、失败不误报（先写测试 FAIL）

### Implementation for User Story 5

- [x] T027 [US5] 改 `webui/src/composables/useDiscoverySearch.ts` `analyzeResume`（L245-313）：改调 `useResumeAnalysisFlow.startAnalysis`（不 await 阻塞 UI），移除同步 `await apiRequest`+强制 `enterSearchStep`；保留 035 守卫（有活任务跳回任务视图不取消）
- [x] T028 [US5] 实现 `useResumeAnalysisFlow.landOnReturn`/`onAnalysisComplete`：用户回当前流程按 `phase` 落点（succeeded→enterSearchStep 切 02、analyzing→留 01 显示分析中、failed→留 01 显示真实失败）；分析完成自动进 02 仅当用户在 01 或主动回当前流程
- [x] T029 [US5] 灵动岛 analyzing 态显示（`DynamicIsland.vue` + `useIslandCarousel.ts` 由 T020 支持）：点击回页面一显示分析中
- [x] T030 [US5] 失败按真实失败展示不误报：沿 `analyzeResume` 失败路径核实「浏览器找不到/刷新失败」误报来源（research 开放点②），改为展示 API 真实 error（不编造 source_* 误报），符合 spec FR-022
- [x] T030b [US5] 改 `webui/src/composables/useDiscoveryExecution.ts` `restoreRunningTask`（L116-488）：恢复时检测分析中任务接 `useResumeAnalysisFlow.phase`（刷新后接续分析中态，落点按真实进度），与 spec FR-019 边缘场景「简历分析失败→按真实失败展示」对齐

**Checkpoint**: US5 独立可用——分析中不锁、按进度落点、不误报、刷新后接续分析中态。

---

## Phase 8: User Story 6 - 只抓取流程结果页第七类条件（B092）(Priority: P1)

**Goal**: 只抓取流程结果页确认筛选条件时第七类「招聘者活跃时间」跟前六类一起出现、可选可确认，与页面二（实为 03 页确认）一致。

**Independent Test**: 走只抓取流程抓完一轮 → 进结果页确认条件 → 七类全部出现、可选项与 03 页一致、可确认生效。

### Implementation for User Story 6

- [x] T031 [US6] 改 `webui/src/composables/useDiscoveryState.ts` `filterGroups` computed（L619-638）：核实并确保从 schema 派生时渲染第七类 recruiter_activity（无排除逻辑，核实 schema 已含 per T001）；`screenSummaryChips`（L652-666）含第七类摘要
- [x] T032 [US6] 改 `webui/src/views/DiscoveryView.vue` 03 页标题（L901）「确认 6 类筛选条件」去硬编码数字，按实际 schema 类数展示（如「确认筛选条件」）
- [x] T033 [US6] 改 `webui/src/composables/useDiscoverySearch.ts` `applyResumeAnalysisToCurrentSchema`（L350-374）：第七类投影（resumeAnalysis 语义 → schema recruiter_activity 档位 code），与 028 后端判定口径一致
- [x] T034 [US6] 定位并接入「只抓取流程结果页确认条件」入口（research 开放点④）：核实是 03 页 filterGroups 在 scraped_only 流程下的渲染，还是独立 UI；确保该入口渲染第七类、可选可确认、与 03 页选项集一致（spec FR-023/FR-024）
- [x] T035 [US6] 验证第七类确认后生效参与判定（沿用 028 `recruiter_activity.evaluate`），只抓取流程结果页确认的第七类选项集与 03 页一致不缺不漏

**Checkpoint**: US6 独立可用——第七类出现、可选可确认、与 03 页一致。

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 跨 US 收尾、模块地图登记、零引用盘点、界面走查、文档同步。

- [x] T036 [P] 更新 `.specify/memory/constitution.md` 模块地图：登记 `useDiscoverySceneState.ts`/`useIslandNavigation.ts`/`useResumeAnalysisFlow.ts`（路径+一句话职责+本 Spec 编号）
- [x] T037 [P] 零引用盘点：确认三个新 composable 无孤儿代码、`DiscoveryView.vue`/`useDiscoveryState.ts` 接线后无未使用 ref
- [ ] T038 按 `quickstart.md` 界面走查清单走查（原标记"六场景全过"无客观证据，2026-09-13 返工改回未完成；现状见 `quickstart.md`「走查记录（2026-09-13）」）
- [x] T039 [P] 文档同步：`CHANGELOG.md` 按用户可感知口径记条目（修复：切页不丢现场/灵动岛落点正确/补抓不切页/分析中不锁/第七类条件出现）；`README.md` 若涉及用户可感知能力变化则同步

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始（T001 核实后端 schema）
- **Foundational (Phase 2)**: 与 Setup 可并行；**阻塞所有 User Story**（T002-T006 三大 composable + 类型 + 通道必须先就绪）
- **User Stories (Phase 3-8)**: 均依赖 Foundational 完成
  - US1（现场保留）与 US3（灵动岛落点）可并行（不同 composable）
  - US2（主体清）依赖 US1 的 `useDiscoverySceneState`（清理/降级用同一 composable）→ US2 在 US1 后
  - US4（补抓不切页）较独立，可与 US1/US3 并行
  - US5（分析不锁）依赖 `useResumeAnalysisFlow`（Foundational）+ analyzing 态（US3 的 T020）→ US5 在 US3 后或与 US3 协调 analyzing 态
  - US6（第七类）较独立，可与 US1/US4 并行
- **Polish (Phase 9)**: 依赖所有 US 完成

### User Story Dependencies

- **US1 (P1)**: Foundational 后开始，无其他 US 依赖（MVP 首选）
- **US2 (P1)**: 依赖 US1（现场存档清理/降级 API）
- **US3 (P1)**: Foundational 后开始，无其他 US 依赖
- **US4 (P1)**: Foundational 后开始，无其他 US 依赖
- **US5 (P1)**: 依赖 US3 的 analyzing 态派生（T020）
- **US6 (P1)**: Foundational 后开始，依赖 T001 核实结果

### Within Each User Story

- 测试（含）MUST 先写并 FAIL
- composable/类型 → 组件接线 → 集成
- US 完成前过 Independent Test

### Parallel Opportunities

- Foundational 的 T002/T003/T004 三新 composable 不同文件可并行
- US1（现场）/US3（落点）/US4（补抓）/US6（第七类）Foundational 后可并行
- 各 US 的测试任务 [P] 可并行

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup（T001 核实第七类 schema）
2. Phase 2 Foundational（T002-T006 三 composable + 类型 + 通道）
3. Phase 3 User Story 1（T007-T012 现场保留）
4. **STOP and VALIDATE**: 独立测 US1（切页/翻历史/刷新现场保留）
5. 其余 US 按依赖增量推进

### Incremental Delivery

1. Setup + Foundational → 基础就绪
2. US1 → 独立测 → 现场保留可用
3. US2 → 独立测 → 三主体清口径齐
4. US3 → 独立测 → 灵动岛落点正确
5. US4 → 独立测 → 补抓不切页
6. US5 → 独立测 → 分析中不锁
7. US6 → 独立测 → 第七类接入
8. Polish → 走查 + 文档

---

## Notes

- 红线豁免：`DiscoveryView.vue`（基线 1215→现状 1200）/`useDiscoveryState.ts`（基线 1328→返工后 1418，仍未净减）已超红线，本 Spec 仅接线性修改，拆分另立方向（plan 复杂性追踪表）
- [P] 任务 = 不同文件、无未完成依赖
- [Story] 标签映射 spec.md User Story 便于追溯
- 每个 US 独立可完成可测试，停在 Checkpoint 可单独验证
- 提交纪律：每个任务或逻辑组提交后等用户明确要求才 push（全局规则）


---

## 实现偏离与补记（2026-09-13 收尾）

**实际完成情况**：T001–T037、T039 落地；**T038 未完成**（界面走查未执行，2026-09-13 返工改回未勾选）。
以下为原清单未列、实现与验收过程中补齐的必要项：

- **后端画像隔离合同测试**（新增文件）：`tests/webui_app/test_profile_isolation_contracts.py`，
  把历史列表/详情/删除、归档、最新结果、任务创建点按 `profile_id` 隔离写成契约（B098 B 块"不串台"的另一面）。
- **未筛选轮（scraped_only）刷新行为修复**（验收发现）：`useDiscoveryWorkflow.isCompletedWorkflowSnapshot`
  排除 `currentRoundStatus === "scraped_only"`、`persistWorkflowState` 快照补传 `currentRoundStatus`、
  `DiscoveryView.vue` 的 `activeStep` watch 对未筛选轮不置"已结束"；已完成筛选的轮仍保持 026 的"刷新开新一轮"行为。
- **恢复流程不再覆盖折叠卡现场**（验收发现）：`useDiscoveryWorkflow.restoreWorkflowState` 包进抑制窗口，
  恢复快照时的 `analysisReady=true` 不再触发"分析完成自动展开"去覆盖现场存档里的收起状态；补回归测试
  （`useDiscoveryWorkflow.spec.ts` "恢复快照的 analysisReady 不触发面板自动展开"）。
- **跨画像返回后列表滚动位置补应用**（验收发现）：`JobWorkspace.vue` 在原恢复拍与列表元素出现时按需补应用
  滚动现场（仅在 DOM 未滚动且存档非 0 时写入，不覆盖用户当前位置）；补回归测试
  （`JobWorkspace.spec.ts` "re-applies the list scroll position when the list mounts after the restore tick"）。
- **03 页摘要过渡态防御**（验收发现）：`v-show` 常驻渲染后 `screenSummaryChips` 读到缺平台键草稿时不再
  渲染中断（`useDiscoveryState.ts` 加 `|| {}` 防御）。


### 收尾补充（2026-09-13，用户拍板）

**无归属老数据保留口径**：画像功能之前的历史轮次没有归属标记（归属为空）。
用户拍板"老数据要留下、历史里要能看见"，口径如下：

- 历史列表、详情、归档、删除：无归属老数据对所有画像可见、可管；
  有归属的轮次仍严格按画像隔离，跨画像读写一律按不存在处理。
- 任务恢复（`/api/latest-running-task`）：保持严格按画像，不放宽——
  正式库存在大量无归属历史过程记录，放宽会把陈旧任务接回当前页面。
- 覆盖测试：`tests/webui_app/test_profile_isolation_contracts.py`
  `test_legacy_unowned_rounds_stay_visible_and_manageable`。
- 正式库 5 条无归属历史轮次已只读验证：历史列表 5 条可见、最新结果可加载；
  隔离环境下画像 A/B 均可见老数据、且互相看不到对方的新轮次。

---

## 返工记录（2026-09-13，静态审查后的收尾返工）

> 触发：静态审查发现实现 / 工件 / 验收互相不一致。本次返工改实现与工件，不改冻结需求；
> 除下列 T040–T045 外，未改动其它已完成任务的行为。

- [x] T040 现场身份稳定化：`runEpoch` 改为会话存档里的稳定轮次身份（`ensureRoundEpoch`/`rotateRoundEpoch`），
  不再用 task_id / 结果 run id；新增 `useDiscoverySceneIdentity.ts`；`useDiscoveryResults.currentSceneIdentity`
  与 `useDiscoveryTasks.resetWorkflowInternal` 统一改用同一身份；开新轮先归档（键=结果 run id）再换身份。
  证据：`useDiscoverySceneIdentity.spec.ts`（4）、`useDiscoverySceneState.spec.ts`（归档/身份，7）、
  `useDiscoveryTasks.spec.ts`（开新轮归档，37）、`useDiscoveryIslandBridge.spec.ts`（2）。
- [x] T041 运行日志画像隔离：`LogViewerDialog` 增 `profileId` 并携带到 `/api/latest-running-task?profile_id=`；
  `App.vue → AppSettingsMenu → LogViewerDialog` 全链传递；切画像关窗清任务号/日志/轮询；
  历史轮 `initialTaskId` 打开功能保留。证据：`LogViewerDialog.spec.ts`（11）。
- [x] T042 `task-state` 画像归属闭环：后端支持 `profile_id` 校验（内存任务 + DB 结果轮，跨画像 404
  `run_not_found`，无归属老任务保持对所有画像可见）；前端 `useDiscoveryTasks` / `useDiscoveryResults` /
  `useScreenRoundFlow` / `useDiscoverySearch` 的任务状态查询一律带画像。
  证据：`tests/webui_app/test_profile_isolation_contracts.py`（11，含 3 个新用例）。
- [x] T043 画像框高度现场真正接通：`PageScene` 增 `profileInputWidth`/`profileInputContent`，
  自动高度按「同轮同宽同内容接回、宽度/内容/身份变则重算并回写」工作；新增 `useProfileInputScene.ts`。
  证据：`useProfileInputScene.spec.ts`（3）。
- [x] T044 `useDiscoveryState.ts` 减负：Spec041 新增的灵动岛导航桥接整体外迁到
  `useDiscoveryIslandBridge.ts`，该文件 1467→1418 行；但相对 plan 基线（1328）仍净增 90 行，
  **"净减"未达成**，拆分需单独立项 Spec（未完成项，见 plan 返工修订实测表）。
- [ ] T045 界面走查（quickstart 七个勾选项）：**未执行**——本环境没有真实平台登录与真实任务数据，
  无法按真实用户路径走查；全部标为"未验证"，不以此前任务文字或其他 AI 自述勾选（见 `quickstart.md`）。

### 返工遗留（阻断 Spec041 宣称"全部完成"）

1. T045 界面走查未执行（quickstart 七个勾选项全为未验证）。
2. `useDiscoveryState.ts` 未净减、仍超红线；拆分 Spec 未建立（plan 已登记为另立方向，本次未开工）。

---

## 真实页面返工复测（2026-09-13，四项失败根因修复）

> 触发：真实验收发现当前实现不通过，定位四个真实失败根因并修复生产代码。
> 本轮只处理这四项；T038/T045 仍未执行，不因这四项通过而宣称 Spec041 全部完成。
> 正式进程已按项目正式方式重启（源码模式启动自动同步前端产物），正式库 env=live 未被修改。

四项根因、修改与真实复测结果见下；聚焦测试 537 项全绿，`vue-tsc --noEmit` 通过。

| 失败项 | 根因 | 生产代码修改 | 聚焦测试 | 真实复测结果 |
|---|---|---|---|---|
| 一、智联结果页刷新回 BOSS 空上传页 | `maybeAutoStartNewRound` 把"已进 04 页"一律 `resetWorkflow`；完成态现场被 `clearWorkflowState` 清掉，刷新无恢复来源 | `useDiscoveryWorkflow`：完成态结果页现场改为持久化（`writeWorkflowSnapshot(true)` 含平台/结果/分类/现场），`restoreWorkflowState` 同步接回（首帧前调用，不闪 BOSS）；`useDiscoveryTasks.maybeAutoStartNewRound`：有结果原地接回、无结果才问后端、确实没有才新一轮；`useDiscoveryExecution.restoreRunningTask`：真正在跑的任务不被已结束事实挡住；`JobWorkspace`：结果数据晚到时认领存档选中、JD/列表滚动现场补应用；`DiscoveryView`：完成事实标记带平台、结果页骨架占位 `resultsBootstrapPending` | `useDiscoveryWorkflow.spec`（完成态现场原地接回 3）、`useDiscoveryTasks.spec`（maybeAutoStartNewRound 4）、`DiscoveryRecovery.spec`（刷新恢复 3）、`JobWorkspace.spec`（晚到数据恢复选中+JD 滚动 1） | 智联结果页刷新后仍在智联结果页、平台=智联、20 岗、选中第 3 条「Python 爬虫开发实习生」接回、列表滚动 300 接回、无 BOSS/空上传页闪现（截图 `spec041-sc1-after-reload.png`） |
| 二、历史轮点灵动岛跳错平台/空结果页 | `enterHistoryRound` 用历史轮平台覆盖当前轮草稿/结果平台；`returnToLatest` 早返路径不还原平台；灵动岛 `execute` 不等真实落点就按历史轮目标设步骤 | `useDiscoveryResults`：`enterHistoryRound` 不再改写草稿平台（只展示），`currentRoundPlatform` 与 `platformBeforeHistory` 分清当前/历史平台；`returnToLatest` 返回真实落点步骤（无结果落 01 不造空结果页）；`useIslandNavigation.execute`：历史模式下落点取退出历史的真实状态、退出未完成不改步骤；`useDiscoveryIslandBridge`：`onExitHistory` await 完整恢复；`DynamicIsland`：胶囊发出真实目标（task-scrape/task-screen）不压成 "task" | `useIslandNavigation.spec`（4）、`useDiscoveryIslandBridge.spec`（2）、`DynamicIsland.spec`（55）、`DiscoveryHistoryMode.spec`（4：智联历史→岛→回智联、BOSS历史→岛→回智联、智联历史→BOSS任务→回BOSS、无结果不造空结果页） | 智联历史轮内点灵动岛→回当前智联轮（20 岗、非 BOSS、非空结果页）；历史现场（选中第 1 条「python后端开发工程师」+ JD 滚动 212）往返保留（截图 `spec041-sc2-history-jd-restored.png`） |
| 三、BOSS/智联关键词·城市·区县草稿互相覆盖 | `keywords/selectedKeywords/customKeyword/cityText/customCity` 是跨平台单一 ref；切平台不保存旧平台、不恢复新平台；范围预览旧响应覆盖新平台 | 新增 `useSearchDraftSlots.ts`（树干通用：按画像+平台槽位持久化）；`useDiscoveryState`：工作副本经平台 watcher 保存/装载、范围预览按平台各存一份 + 请求平台复核作废旧响应；`useDiscoverySearch.refreshScopePreview`：所有出口推进请求序号、按发起平台落槽；`useDiscoveryTasks.resetWorkflowInternal`：开新一轮只清当前平台槽位；简历分析建议镜像到两平台（用户编辑仍按平台隔离） | `useSearchDraftSlots.spec`（3：完整往返 + 预览乱序 + 槽位单元） | BOSS 设 BOSSKW/深圳→切智联不见→智联设 ZKW/广州→切回 BOSS 恢复 BOSSKW/深圳→再切智联恢复 ZKW/广州→刷新后仍各自独立（截图 `spec041-sc3-boss-draft-restored.png`） |
| 四、运行日志重复渲染 | 后端 `/api/logs` 在 `since==total`（无新增行）时退回重发整个尾部；前端 `poll` 每拍把整段再追加 | `webui/log_api.py`：统一成 (行号, 文本) 列表，`since` 无新增行时返回空增量（游标不动）；`LogViewerDialog`：游标增量合并（只接行号>游标的行）、单轮询器、代次作废旧响应、`initialTaskId` 变化清旧任务、`loadOlder` 重叠裁剪 | `test_log_api.py`（2：since==end 空增量 ×文件/任务）、`LogViewerDialog.spec`（16：同区间连轮询不重复、相同文案不同行不去重、loadOlder 重叠、单轮询器、initialTaskId 切换清空） | 03:53 历史轮运行日志 24 条唯一事件，4 个轮询周期稳定 24 行（修复前约 360 行/15 倍），无倍增（截图 `spec041-sc4-log-stable-24.png`） |

**仍未验证（不因本次四项通过而变更）**：多画像隔离、窄屏、BOSS 成功抓取、BOSS 第七类、简历分析中刷新、补抓/重抓、单平台结果双向验证；T038/T045 仍保持未完成。
