# Tasks: 任务历史浏览安全与界面一致性修复（035 重拆版）

**Input**: Design documents from `/specs/035-task-history-safety/`（重拆版：spec / plan / research / data-model / contracts / quickstart）

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 测试先写并 FAIL，再实现（红→绿）。前端测试必须含**界面级断言**（按钮数量、页面显示内容、入口逐一触发），不得只断言状态字段（spec SC-006 / research R8）。

**Organization**: 按重拆版 User Story 组织。US1/US2/US3 对应三个真机问题（P1）；US4（B087 冒泡）/US5（B085 日志滑块）上一轮已落地，仅回归验证。

## File Boundaries

Resolve the boundary section from plan.md. 所有任务必须限定在以下范围内：

- **Allowed files**：
  - `webui/src/composables/useDiscoveryExecution.ts`、`useScreenRoundFlow.ts`、`useDiscoveryResults.ts`、`useDiscoverySearch.ts`、`useDiscoveryTasks.ts`、`useDiscoveryState.ts`、`discoveryDeps.ts`
  - `webui/src/views/DiscoveryView.vue`（**仅 915 行「直接查看结果」既有 v-if 条件加无活任务守卫，净增 ≤1 行**，其余零改动）
  - 测试：`webui/src/**/__tests__/*.spec.ts`（相关既有 spec 文件；如 `useDiscoveryState` 无既有独立 spec，允许新建 `webui/src/composables/__tests__/useDiscoveryState.spec.ts`，属测试文件）
- **Forbidden files**：`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`scripts/maintenance/historical_recovery.py`、`webui/historical_recovery.py`；`webui/log_api.py` 与 `webui/src/components/LogViewerDialog.vue`（B085 已真机验证，不动）；`webui/src/components/TaskCompletedToast.vue`（不动）；数据库与迁移；`roadmap/`、`.codebuddy/`、`.specify/`（除 feature.json 已拨回）
- **New files**：无源码新文件；仅允许上述测试文件
- **Reference direction**: `view → composable → api client`；composable 间经 `discoveryDeps` 契约；`liveTaskStep` 为 `useDiscoveryState.ts` 只读派生
- **Line gate**: `DiscoveryView.vue` 净增 ≤1 行；其余 composable 每文件净增 ≤30 行

## Verification Gate (task-type aware)

- 功能交付门禁：聚焦测试（界面级）+ 后端全量测试（零改动纯回归）+ 前端测试 + `npm run build` + 仓库卫生检查（未提交文件导致卫生检查 1 例预期失败时如实报告）。
- 本功能为功能交付，不提交不推送。
- 用户端到端真跑（quickstart 场景 A-E）交付后由用户执行；A-D 需真实登录态。

---

## Phase 1: 基线

- [ ] T001 跑既有前端测试确认当前基线全绿（`cd webui && npm test`），记录用例数作为回归基线；后端零改动不跑

---

## Phase 2: Foundational — `liveTaskStep` 派生（US2/US3 共享前置）

**Goal**: 未结束任务存在时，任务真实进度页的统一只读派生：抓取活 → 02（"search"），筛选/重抓活 → 03（"screen"）。

- [ ] T002 [测试先行] 在 `webui/src/composables/__tests__/`（`useDiscoveryState` 既有 spec 或新建 `useDiscoveryState.spec.ts`）写 `liveTaskStep` 用例并确认 FAIL：①仅抓取活（scrapeBusy 或 scrapeSnapshot 进行态）→ `"search"`；②仅筛选/重抓活 → `"screen"`；③抓取+筛选同时活（一键链路 autoScreen 后筛选接续场景）→ 以真实进度为准（抓取完成后筛选活 → `"screen"`；抓取仍活 → `"search"`）；④无活任务 → 空/不改变
- [ ] T003 [US2/US3 前置] `webui/src/composables/useDiscoveryState.ts`：实现 `liveTaskStep()` 只读派生（复用 `hasLiveTaskState` 的判据面，按任务类型分派步骤），跑 T002 用例转绿
- [ ] T004 [US2/US3 前置] `webui/src/composables/discoveryDeps.ts`：契约同步暴露 `liveTaskStep`，`useScreenRoundFlow` / `useDiscoverySearch` / `useDiscoveryResults` 的消费接口接线

---

## Phase 3: US1 - 恢复/回到最新后四页与任务真实进度一致（真机问题①，FR-010）

**Goal**: 活的抓取任务下，03 页不残留旧一轮 AI 筛选内容；新一轮开始即清空旧轮展示。

**Independent Test**: mock 旧 screenSnapshot 残留 + 抓取运行中 → 刷新恢复 → 03 页无旧内容（screenSnapshot 已清、进度卡不渲染）。

### Tests for US1

- [ ] T005 [测试先行] `useDiscoveryExecution` 相关既有 spec：写用例并确认 FAIL——①`startScrape` 开新一轮时清空 `screenSnapshot`/`screenTaskId`/`recrawlSnapshot`/`recrawlTaskId`/`currentRoundStatus`；②`restoreRunningTask` 检测到活的抓取任务时同样清空（含 sessionStorage 恢复带入的 screenSnapshot 残留场景）
- [ ] T006 [P] [US1] `webui/src/views/__tests__/DiscoveryView.spec.ts` 界面级用例（先 FAIL）：mock 抓取运行中 + 旧一轮 screenSnapshot 残留 → 走刷新恢复 → 断言 03 页无旧筛选内容（进度卡不渲染、无旧轮保留/剔除计数）

### Implementation for US1

- [ ] T007 [US1] `webui/src/composables/useDiscoveryExecution.ts`：`startScrape` 增加清空 screen 侧展示状态（与既有 scrape 侧重置同点）；跑 T005-① 转绿
- [ ] T008 [US1] `webui/src/composables/useDiscoveryExecution.ts`：`restoreRunningTask` 抓取活任务分支增加同步清空 screen 侧残留；跑 T005-② 转绿
- [ ] T009 [US1] 跑 T006 界面级用例转绿；确认 03 页空态复用现状（screenSnapshot=null → 进度卡不渲染、「开始 AI 筛选」禁用），无新增空态组件

**Checkpoint**: US1 完成——抓取中刷新，03 页不再出现旧一轮内容（自动化界面断言通过）。

---

## Phase 4: US2 - 所有开新一轮入口一律跳回（真机问题②，FR-011）

**Goal**: 5 个入口在未结束任务存在时（含 scrape-only running、含历史模式 04 页）一律跳回任务真实进度页，不 reset、不取消、不弹窗。

**Independent Test**: 逐一触发 5 个入口 → 断言跳回落点正确（抓取→02）、无 resetWorkflow、无 `/api/task/cancel` 调用。

### Tests for US2

- [ ] T010 [测试先行] `webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`：写用例并确认 FAIL——`confirmNewRound` 在 ①scrape-only running（screen 侧全空）②历史模式 + scrape running 两个场景下：不调 `resetWorkflow`、不触发取消、`activeStep` 跳 `"search"`、给提示后返回；并更新既有「跳回 screen」用例为按任务类型分派（screen 活 → `"screen"` 不变）
- [ ] T011 [P] [测试先行] `webui/src/composables/__tests__/useDiscoveryTasks.spec.ts`：补 `maybeAutoStartNewRound` 的 scrape-only running 守卫用例（如既有用例已覆盖则确认并跳过新增）；`analyzeResume` 守卫落点用例（scrape 活 → `"search"`；screen 活 → `"screen"`）
- [ ] T012 [P] [US2] `webui/src/views/__tests__/DiscoveryView.spec.ts` 界面级用例（先 FAIL）：mock 后台抓取运行中 + 历史模式 04 页 → 点击「开始新一轮」按钮 → 断言跳到 02 抓取进度页、无任务取消请求发出

### Implementation for US2

- [ ] T013 [US2] `webui/src/composables/useScreenRoundFlow.ts`：`confirmNewRound` 顶部加 `hasLiveTaskState()` 守卫（先于 resumable 计算），命中 → 跳 `liveTaskStep()` + 提示 + return；跑 T010 转绿
- [ ] T014 [US2] `webui/src/composables/useDiscoverySearch.ts`：`analyzeResume` 守卫跳回落点由硬编码 `"screen"` 改为 `liveTaskStep()`；跑 T011 转绿
- [ ] T015 [US2] 验证 `startScrape`/一键链路/`maybeAutoStartNewRound` 既有守卫在 scrape/screen 活任务下行为正确（预计零改动；发现问题则最小修复并补用例）；跑 T012 界面级用例转绿

**Checkpoint**: US2 完成——含历史模式 04 页在内的 5 个入口，未结束任务存在时全部跳回真实进度页（自动化界面断言通过）。

---

## Phase 5: US3 - 任务页按钮在任何回到路径后与正常运行一致（真机问题③，FR-012/013）

**Goal**: 看历史不污染当前轮标志；回到最新/刷新后按钮集合与正常运行完全一致（抓取运行中恰好 2 个）；运行中无「直接查看结果」半截保存入口。

**Independent Test**: mock 抓取运行中 → 进历史 → 回到最新 → 断言任务页操作按钮恰好 2 个（渲染查询按钮集合）。

### Tests for US3

- [ ] T016 [测试先行] `webui/src/views/__tests__/DiscoveryHistoryMode.spec.ts`：写用例并确认 FAIL——①进历史轮后 `scrapeCompleted` 保持原值（false，不被历史轮数据置位）；②回最新后按任务真实状态重算（抓取运行中 = false）
- [ ] T017 [P] [测试先行] `webui/src/views/__tests__/DiscoveryHistoryMode.spec.ts` 或 `DiscoveryView.spec.ts` 界面级用例（先 FAIL）：mock 抓取运行中 → 进历史 → 回到最新 → 断言 02 任务页操作按钮**恰好 2 个**（停止抓取、结束并保存结果），「进行确认AI筛选条件」「直接查看结果」不渲染；对照组：抓取真实完成后这两个按钮正常出现
- [ ] T018 [P] [测试先行] 「直接查看结果」防半截保存用例（先 FAIL）：抓取运行中即使 `scrapeCompleted` 被异常置位，按钮仍因无活任务守卫不渲染/不可点（`viewScrapedOnly` 不被调用）

### Implementation for US3

- [ ] T019 [US3] `webui/src/composables/useDiscoveryResults.ts`：修正 `enterHistoryRound` 绕过 `setPipelineResult` historyMode 守卫的路径——历史轮浏览不置位 `scrapeCompleted`/`resultLoaded`/`analysisReady`（历史只读，实现取向见 research R7 F1，最小改动落定）；跑 T016-① 转绿
- [ ] T020 [US3] `webui/src/composables/useDiscoveryResults.ts`：`returnToLatest` 按当前任务真实状态重算 `scrapeCompleted`（防御层），未结束任务存在时落点用 `liveTaskStep()`；跑 T016-② 转绿
- [ ] T021 [US3] `webui/src/views/DiscoveryView.vue`：**仅 915 行**「直接查看结果」既有 v-if 条件增加无活任务守卫（净增 ≤1 行）；跑 T017/T018 转绿

**Checkpoint**: US3 完成——从历史回到任务页按钮恰好 2 个，与正常运行完全一致（自动化界面断言通过）。

---

## Phase 6: US4/US5 - 已落地部分回归验证（B087 冒泡 / B085 日志滑块）

**Goal**: 本轮修复不波及上一轮已实现且验证过的行为。

- [ ] T022 [P] 跑冒泡相关既有测试（`TaskCompletedToast.spec.ts`、`useDiscoveryTasks.spec.ts` 冒泡/历史模式用例）确认全绿；重点核对 `returnToLatest` 落点改动后冒泡点击路径仍正确回最新见本轮成果
- [ ] T023 [P] 跑日志相关既有测试（`LogViewerDialog.spec.ts`）确认全绿（本轮未触碰该域，纯回归）

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T024 全量门禁：后端全量（`uv run python -m unittest`，零改动纯回归）+ 前端全量（`npm test`）+ `npm run build` + 卫生检查（未提交文件 1 例预期失败如实报告）
- [ ] T025 更新 `roadmap/BACKLOG.md`：B085/B086/B087 状态改为「进行中（Spec 035 重拆修复完成，待用户真机验证）」并注明重拆版关联
- [ ] T026 按 `quickstart.md` 场景 A-E 用户端到端真跑（A-D 需真实登录态，环境归项目就绪；缺前置如实说明并等用户）——**待用户执行**
- [ ] T027 审查（spec SC-007）：以真实渲染/界面走查核对场景 A-E 对应路径，零起点不沿用旧结论；静态读码不能单独作为通过依据

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 2（liveTaskStep）**: US2/US3 的前置；US1 不依赖（可并行）
- **US1（Phase 3）**: 无前置，可与 Phase 2 并行
- **US2（Phase 4）**: 依赖 Phase 2（T003/T004）
- **US3（Phase 5）**: 依赖 Phase 2（T003/T004，returnToLatest 落点）
- **Phase 6（回归）**: 依赖 US1-US3 全部完成
- **Phase 7（收尾）**: 依赖全部

### Within Each User Story

- 测试先写并 FAIL，再实现转绿
- 实现顺序：派生/守卫（composable）→ 模板最小改动 → 界面级断言转绿

### Parallel Opportunities

- US1（useDiscoveryExecution.ts）与 Phase 2（useDiscoveryState.ts/discoveryDeps.ts）不同文件可并行
- US2 与 US3 在 Phase 2 完成后，主要文件不同（useScreenRoundFlow/useDiscoverySearch vs useDiscoveryResults/DiscoveryView），可并行；`useScreenRoundFlow.spec.ts` 与 `useDiscoveryTasks.spec.ts` 为共享测试文件注意串行
- T022/T023 相互独立可并行

## Implementation Strategy

### MVP First（US1 优先，但三问题同源同批收口）

1. Phase 1 基线 → Phase 2 派生
2. US1（T005-T009）→ US2（T010-T015）→ US3（T016-T021）
3. 回归（T022-T023）→ 收尾（T024-T027）

## Notes

- [P] 任务 = 不同文件、无依赖；`useScreenRoundFlow.spec.ts`/`useDiscoveryTasks.spec.ts` 为多 Story 共享测试文件，禁止并行写同一文件。
- 上一轮已实现的 B086 主链路修复（watcher 条件、maybeAutoStartNewRound 守卫、restoreRunningTask 优先恢复）保留不动，本轮只补漏与收口。
- 提交/推送不在本 Spec 范围（不提交不推送）。
- **tasks 完成为停滞点**：T001-T025 全部完成后停止并报告，等待用户明确命令后才进入实现之外的动作；T026/T027 由用户与审查环节执行。
