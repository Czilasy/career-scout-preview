# Tasks: 批中暂停二选一 + 暂停断点保全 + 完成态自动新一轮

**Input**: `/specs/025-screen-pause-round-reset/`（spec.md / plan.md，冻结需求 3 条）

**Prerequisites**: plan.md（文件边界与方案要点）

**Tests**: 聚焦测试按用户故事先写后实现（先红后绿）。

**Organization**: 按用户故事分组；US3（B077 数据保全 Bug）最先（数据安全优先），US1/US2（B076 暂停体验）次之，US4（B078 完成态新一轮）最后。

## File Boundaries

- **Allowed files**: `webui/task_continue_api.py`（仅薄组装，+8 行）、`webui/pipeline_exec_details.py`、`webui/pipeline_guard.py`（仅新增停止清理助手，判定/监控逻辑零改动）、`webui/runners/ai_screen_jd.py`、`webui/src/composables/useScreenRoundFlow.ts`、`webui/src/composables/useDiscoveryTasks.ts`、`webui/src/views/DiscoveryView.vue`（仅最小组装，+5 行）、`.specify/memory/constitution.md`、`tests/`
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/boss/`、`webui/store_migrations*.py`、`webui/error_registry.py`（只读复用）、`webui/source_boss_cdp_detail.py`（零改动，B077 复用其既有抢救逻辑）；`pipeline_guard.py` 的 `_monitor_loop`/`scan_once`/`_mark_stalled`/`_divert`/`_maybe_fallback_pause` 及 300s/3 次/分流参数
- **New files**: `webui/task_pause_support.py`、`webui/src/components/PauseBatchChoiceDialog.vue`、`tests/test_pipeline_pause_guard.py`、`webui/src/components/__tests__/PauseBatchChoiceDialog.spec.ts`（扩展 `useScreenRoundFlow.spec.ts`、`DiscoveryView.spec.ts`）
- **Reference direction**: 后端 `runners/ai_screen_jd.py → pipeline_exec_details.py → pipeline_guard.py`；`task_continue_api.py → task_pause_support.py → ctx.pipeline_guard`；前端 `DiscoveryView.vue → useScreenRoundFlow → PauseBatchChoiceDialog.vue`、`DiscoveryView.vue → useDiscoveryTasks.resetWorkflow`
- **Line gate**: `task_continue_api.py` 增量 ≤8 行（仅组装，存量 791 行超预警线）、`DiscoveryView.vue` 增量 ≤5 行（仅组装，存量 1232 行超红线）；其余改动文件 ≤600 行；新增文件 ≤400 行

## Verification Gate

- 交付门禁：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 卫生检查。
- 用户端到端真跑验证（SC-004）在交付后进行。

---

## Phase 1: User Story 3 - 暂停断点保全（B077，Bug，数据安全） 🎯 MVP

**Goal**: 批返回后立即处理结果（抢救的已抓不再被重抓分支丢弃）、卡死重抓剔除已抓成功岗位只抓缺失、暂停返回绝不把空结果写进断点、断点保留已抓 JD；**卡死防护判定与 source 层零改动**

**Independent Test**: 注入"批返回后 stop_event 置位"（卡死重抓窗口暂停）：产物有已抓 detail（source 抢救逻辑 mock）→ 断点保留已抓；继续后只补抓未抓

### 测试（先写、先红）

- [ ] T001 [US3] `tests/test_pipeline_pause_guard.py`：批返回后结果处理提前——mock `fetch_details_batch` 返回含抢救已抓的 outcomes（子进程 returncode≠0 场景），断言已抓并入 jd_by_idx（不再被重抓分支丢弃）
- [ ] T002 [US3] `tests/test_pipeline_pause_guard.py`：卡死重抓剔除——`guard.should_retry` 命中时，已抓成功岗位从重抓列表剔除、只重抓缺失、重抓用新产物文件、结果合并不重复（"一批 8 个重复岗位"不复现）；剔除后剩余为空（卡死前已全部抓完）→ 不重抓直接完成
- [ ] T003 [US3] `tests/test_pipeline_pause_guard.py`：批返回窗口普通停止（stop_event 置位、非 immediate）→ 已处理并入的已抓保全 → 返回结果含已抓
- [ ] T004 [US3] `tests/test_pipeline_pause_guard.py`：run_jd_stage stopped 路径——jd_map 非空时落盘断点、为空时不写（绝不写空断点）；断点文件保留已抓 JD（即使该批被判过卡死）
- [ ] T005 [US3] `tests/test_pipeline_pause_guard.py`：卡死防护判定参数未变（300s 判定、3 次重试、分流逻辑的既有测试全绿，且本次改动不触碰这些代码路径；`source_boss_cdp_detail.py` 零改动）

### 实现

- [ ] T006 [US3] `webui/pipeline_exec_details.py`：批返回后的结果处理（outcomes → jd_by_idx / jd_fail）提前到 should_retry/stopped 检查之前执行（顺序：immediate 检查 → hard_stop 检查 → 处理结果 → should_retry → stopped → 正常收尾）
- [ ] T007 [US3] `webui/pipeline_exec_details.py`：卡死重抓（should_retry）路径——从重抓列表剔除已抓成功岗位（jd_by_idx 有 jd 的）、重抓用新产物文件、剔除后为空则不重抓直接 complete_batch
- [ ] T008 [US3] `webui/pipeline_exec_details.py`：批返回后先检查 immediate 信号（stop_event 附加标记）→ 该批结果作废不处理（FR-012 边界）
- [ ] T009 [US3] `webui/runners/ai_screen_jd.py`：stopped 分支 return 前——jd_map 非空才 `save_jd_checkpoint` 落盘、为空不写（FR-010）

**Checkpoint**: US3 独立可用——卡死批暂停后断点保留已抓、继续只补抓未抓、不重复；卡死判定与 source 层未动

---

## Phase 2: User Story 1 - 批中暂停弹二选一（B076 主体） (P1)

**Goal**: 批中暂停弹 PauseBatchChoiceDialog（立即停止默认聚焦回车触发 / 等这批抓完）；立即停止 1 秒内已暂停且浏览器关闭、当前批作废、幂等；等批完数据完整；粗筛/精筛/批间不弹窗

**Independent Test**: mock 批内信号（stage=fetch_jd + jd_batch 非空）→ pauseScreen 弹窗；选立即停止 → API mode=immediate；非批中 → 直接调 API

### 测试（先写、先红）

- [ ] T010 [US1] `tests/test_pipeline_pause_guard.py`：暂停 API mode=immediate——任务转 paused（非 cancelled）、guard 批次登记清理、活动批子进程被终止；mode 缺省 = graceful（现状行为）
- [ ] T011 [US1] `tests/test_pipeline_pause_guard.py`：immediate 幂等——已 paused 任务再调 immediate → `{"ok": true}` 不 409；已 immediate 再调不报错
- [ ] T012 [US1] `webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`：批内信号（progress.stage=fetch_jd + jd_batch 非空）→ pauseScreen 弹窗分支；无批内信号（粗筛/精筛/批间）→ 直接调暂停 API 不弹窗；弹窗打开期间批内信号消失（批次完成）或任务已 paused → 弹窗自动关闭（竞态边界）
- [ ] T013 [US1] `webui/src/components/__tests__/PauseBatchChoiceDialog.spec.ts`：二选一渲染、「立即停止」默认聚焦、回车触发立即停止、进度（第几批/共几批）与平实提示文案展示、文案为普通提示非警告风格

### 实现

- [ ] T014 [US1] `webui/pipeline_guard.py`：新增 `immediate_stop_task(task_id)`——终止该任务活动批次子进程（复用 `ScraperExecutor._terminate_tree`）+ 批次置 terminal + 清理登记（约 +20 行，判定/监控逻辑零改动）
- [ ] T015 [US1] 创建 `webui/task_pause_support.py`：暂停编排助手——`apply_pause_mode(ctx, task, run_id, mode)`（immediate = stop_mode="pause" + stop_event.set() + `stop_event.immediate=True` 信号标记 + guard.immediate_stop_task；幂等：已 paused/已 immediate → 返回 ok）、`cancel_task_cleanup(ctx, run_id)`（guard 清理批次登记）（约 80 行）
- [ ] T016 [US1] `webui/task_continue_api.py`：`api_task_pause` 薄组装——读 body mode（缺省 graceful）+ 调 `task_pause_support.apply_pause_mode` + 组装响应；`api_task_cancel` 追加调 `cancel_task_cleanup`（约 +8 行，不追加业务逻辑）
- [ ] T017 [US1] `webui/pipeline_exec_details.py`：批内信号回调 `batch_progress(current_batch, total_batches)`（批开始置位、批结束/停止清除）（约 +10 行）
- [ ] T018 [US1] `webui/runners/ai_screen_jd.py`：emit(stage="fetch_jd", ...) 携带批内信号（progress.jd_batch: {current, total} | null）（约 +8 行）
- [ ] T019 [US1] 创建 `webui/src/components/PauseBatchChoiceDialog.vue`：立即停止（默认聚焦、回车触发）/ 等这批抓完、平实提示文案、当前批进度（第几批/共几批）（约 180 行）
- [ ] T020 [US1] `webui/src/composables/useScreenRoundFlow.ts`：pauseScreen 批中弹窗分支——批内信号 → 打开弹窗（暂停动作挂起，等用户选择）；选立即停止 → 调暂停 API（mode=immediate）；选等批完 → 调暂停 API（graceful，现状批边界停）；弹窗状态监听（批内信号消失/任务已 paused → 自动关闭弹窗）（约 +50 行）
- [ ] T021 [US1] `webui/src/views/DiscoveryView.vue`：仅最小组装——挂载 PauseBatchChoiceDialog（import + tag + 事件，约 +5 行；存量 1232 行超红线，不追加业务逻辑）

**Checkpoint**: US1 独立可用——批中暂停弹窗、立即停止 1 秒内已暂停且浏览器关闭、继续后该批重头抓、等批完数据完整、幂等、进度+默认聚焦+平实文案

---

## Phase 3: User Story 2 - 配套行为：冷却分段响应 + 停止清理批次登记 (P1)

**Goal**: 批间冷却分段响应停止信号（批间暂停不用干等冷却结束）；停止时清理卡死防护批次登记（避免继续被误判卡死重抓三次）

**Independent Test**: 注入短冷却 + 提前置 stop_event → 冷却提前退出；停止后 guard 无残留批次登记

### 测试（先写、先红）

- [ ] T022 [US2] `tests/test_pipeline_pause_guard.py`：批间冷却分段响应——注入短冷却（如 5s）与提前置位 stop_event → 提前退出冷却（不等 sleep 满）、返回 stopped
- [ ] T023 [US2] `tests/test_pipeline_pause_guard.py`：`immediate_stop_task` 后 guard 活动批次登记清空、无残留 stalled 批次；同 task_id 新一轮 begin_batch 不触发旧登记误判
- [ ] T024 [US2] `tests/test_pipeline_pause_guard.py`：取消路径（api_task_cancel）也清理批次登记

### 实现

- [ ] T025 [US2] `webui/pipeline_exec_details.py`：批间冷却 `time.sleep(max(cooldown, 5))` 改为分段 sleep（每段 ≤1s 检查 stop_event，置位即提前退出）（约 +10 行）
- [ ] T026 [US2] `webui/task_pause_support.py`：`cancel_task_cleanup(ctx, run_id)`（guard 清理批次登记）；`webui/task_continue_api.py` api_task_cancel 调它（约 +2 行）

**Checkpoint**: US2 独立可用——批间暂停立即生效、停止后无残留批次登记

---

## Phase 4: User Story 4 - 完成态启动/刷新自动「开始新一轮」(B078，P2)

**Goal**: 完成态（无进行中任务 + 上一轮已落历史，含 latest-running-task 返回已完成终态任务）启动/刷新自动执行「开始新一轮」逻辑（复用 resetWorkflow）；未完成流程恢复现场（B068 不变）；纯前端、后端零改动

**Independent Test**: mock 完成态 → onMounted 后调用 resetWorkflow；mock 未完成态 → 不调用、恢复现场

### 测试（先写、先红）

- [ ] T027 [US4] `webui/src/views/__tests__/DiscoveryView.spec.ts`：完成态启动 A——mock restoreRunningTask 无活动任务（has_task=false）+ 本地无未完成快照 + 最新历史轮已完成（completed）→ 自动调用 resetWorkflow → 界面干净 01 页、无上一轮结果/草稿残留
- [ ] T028 [US4] `webui/src/views/__tests__/DiscoveryView.spec.ts`：完成态启动 B——mock latest-running-task 返回**已完成终态任务**（completed/completed_with_pending/partial，非 running/paused/interrupted）→ 同样自动调用 resetWorkflow（不恢复现场糊脸）
- [ ] T029 [US4] `webui/src/views/__tests__/DiscoveryView.spec.ts`：无历史轮（全新用户）→ 不糊脸（保持干净 01 页，不误调用/无副作用）
- [ ] T030 [US4] `webui/src/views/__tests__/DiscoveryView.spec.ts`：未完成态——运行中/暂停/中断任务 → 不调用 resetWorkflow、走既有恢复路径（B068 行为不变）

### 实现

- [ ] T031 [US4] `webui/src/composables/useDiscoveryTasks.ts`：暴露 `maybeAutoStartNewRound()` 完成态判定/触发辅助——复用 `fetchMergedLatestResult`（只查不设）与 `resetWorkflow`，不新写重置逻辑（约 +15 行）
- [ ] T032 [US4] `webui/src/views/DiscoveryView.vue`：onMounted finally 分支——调一行 `maybeAutoStartNewRound()`（判定逻辑在 composable）；未完成态走既有路径（约 +2 行，仅组装）

**Checkpoint**: US4 独立可用——完成一轮后刷新界面干净 01 页、历史可查（含 completed 终态任务场景）；暂停/中断一轮刷新可继续

---

## Phase 5: 收口 (P3)

**Goal**: 宪法模块地图登记、README 检查、全量验证门禁通过

- [ ] T033 [收口] `.specify/memory/constitution.md`：模块地图登记 2 个新文件——`PauseBatchChoiceDialog.vue`（暂停二选一弹窗）、`task_pause_support.py`（暂停编排助手）（各一行 + 一句话职责）
- [ ] T034 [收口] README 检查：暂停交互（批中弹窗二选一、立即停止语义）与完成态新一轮行为是否需要同步说明；如需则更新，如现状描述已覆盖则跳过并说明
- [ ] T035 [收口] 验证门禁：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 卫生检查（`tests.test_repo_hygiene` + hooks）全部通过
- [ ] T036 [收口] 用户端到端真跑验证清单（交付后）：①批中暂停 → 立即停 1 秒内已暂停且浏览器关闭、继续后该批重头抓；②等批完数据完整；③批间暂停立即生效；④卡死批暂停后断点保留已抓、继续只补抓未抓；⑤完成一轮后刷新界面干净 01 页、历史可查；⑥暂停/中断一轮刷新可继续（B068 不变）
