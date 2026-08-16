# Tasks: AI 筛选停止/继续/恢复链路统一

**Input**: Design documents from `/specs/013-screen-continue-flow/`

**Prerequisites**: plan.md（必需）、spec.md（必需）、research.md、data-model.md、contracts/screen-resume-flow.md、quickstart.md

**Organization**: 按用户故事分层；US1/US2 是 P1 主链路，US3/US4 是 P2 出口与展示收敛。基础层先行，随后按用户故事增量实现与验证。

## File Boundaries

- **Allowed files**: `webui/screen_flow.py`、`webui/store_screen_resume_mixin.py`、`webui/store.py`（仅 mixin 挂载与守卫放宽）、`webui/scrape_only.py`、`webui/app.py`（最小接线）、`webui/src/types.ts`、`webui/src/screenFlow.ts`、`webui/src/composables/useScreenRoundFlow.ts`、`webui/src/components/ScreenRoundActions.vue`、`webui/src/views/DiscoveryView.vue`（净减少）、`webui/src/styles.css`、`tests/test_screen_flow.py`、`tests/test_store_screen_resume.py`、`tests/test_webui_app.py`、`webui/src/__tests__/screenFlow.spec.ts`、`webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`、`webui/src/components/__tests__/ScreenRoundActions.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`、`webui/src/views/__tests__/DiscoveryScrapeOnly.spec.ts`、`webui/src/views/__tests__/DiscoveryHistoryMode.spec.ts`。
- **Forbidden files**: `scripts/*`、`webui/source.py`、`webui/pipeline_exec.py`、`webui/error_registry.py`、`webui/store_migrations.py`、`webui/ai*.py`、`webui/tuning.py`、`webui/result_history*.py`、`webui/src/components/TaskProgress.vue`。
- **New files**: `webui/screen_flow.py`、`webui/store_screen_resume_mixin.py`、`webui/src/screenFlow.ts`、`webui/src/composables/useScreenRoundFlow.ts`、`webui/src/components/ScreenRoundActions.vue`、`tests/test_screen_flow.py`、`tests/test_store_screen_resume.py`、`webui/src/__tests__/screenFlow.spec.ts`、`webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`、`webui/src/components/__tests__/ScreenRoundActions.spec.ts`。
- **Reference direction**: 后端 `app.py → screen_flow.py → store mixin/store.py`；前端 `DiscoveryView.vue → useScreenRoundFlow.ts → screenFlow.ts / api.ts`；组件只依赖 pure module。
- **Line gate**: `app.py` 增量 ≤160；`store.py` 增量 ≤10；`DiscoveryView.vue` 净减少；`scrape_only.py` 增量 ≤30；`types.ts` 增量 ≤40；`styles.css` 增量 ≤30；新文件不超过宪法单文件上限。

## Verification Gate

- 功能交付最终门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 本任务清单只覆盖实现与验证；外部收口动作不在本清单范围。

## Phase 1: Setup（现状核对）

**Purpose**: 确认 Spec/Plan/契约已就位，任务可直接执行。

- [ ] T001 读取 `specs/013-screen-continue-flow/spec.md`、`plan.md`、`contracts/screen-resume-flow.md`、`data-model.md`，核对 `.specify/feature.json` 指向 `specs/013-screen-continue-flow`；无代码改动。

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 后端 store/service 与前端 pure module 先落地，US1-US4 才能接线。

- [ ] T002 [P] 新建 `webui/store_screen_resume_mixin.py`：实现 `load_screening_jd_map(run_id)`（从 `screening_results` 读取非 dropped 行的 `jd`，key 优先 `platform_job_id` 再回退 `job_id`）与 `latest_screen_runs_for_source(source_task_id, statuses)`（按 `updated_at DESC` 返回最近候选，复用 `store_helpers.latest_screening_run_for_source` 或等价 SQL）。
- [ ] T003 [P] 修改 `webui/store.py`：import `StoreScreenResumeMixin` 并加入 `TaskStore` 基类列表；放宽 `interrupted + error_code=user_finished` 的 `update_screening_run` 守卫：允许 `status=None` 或 `status="interrupted"` 的元数据更新（如 error_code/error_reason），仍禁止改为其它状态。
- [ ] T004 [P] 修改 `webui/scrape_only.py`：新增纯函数 `merge_round_script_params(parent_script_params, screening_fields, platform)`，返回 `{**parent_script_params, "screening": screening_fields, "platform": platform}`；保留现有 `build_screen_script_params` 兼容。
- [ ] T005 [P] 新建 `webui/screen_flow.py`：实现 `find_resumable_screen_run(store, scrape_task_id, screening_fields, profile_summary, profile_facts)`（按最新时间在 `paused`、`failed`、`interrupted`、`partial` 中找，字段/画像/事实一致才返回）、`build_round_script_params(store, run, screening_fields, platform)`、`build_round_context_payload(store, run)`（关键词/城市来自父 run `script_params`，条件来自 `frozen_filters`，画像/事实来自 `execution_params`，返回 `platform/keywords/cities/screening_fields/profile_summary/profile_facts/scrape_task_id/screen_run_id/status/resumable`）。
- [ ] T006 [P] 修改 `webui/src/types.ts`：新增 `RoundContext` 接口，字段与 `screen_flow.build_round_context_payload` 对齐。
- [ ] T007 [P] 新建 `webui/src/screenFlow.ts`：实现 `normalizeRoundContext(payload)`、`isResumableStatus(status)`、`deriveScreenPrimaryAction(state)`（运行中=暂停、暂停/失败/保存后未完成=继续、从未跑 AI=开始、真正完成且有不确定=全部重抓、无任务=none）、`continueTargets(contexts, filter)`（按平台筛选返回目标列表，all 且两边可续返回两项目标）、`primaryActionLabel(action)`。
- [ ] T008 [P] 新建 `tests/test_store_screen_resume.py`：覆盖 `load_screening_jd_map` 回退读取与 `latest_screen_runs_for_source` 排序。
- [ ] T009 [P] 新建 `tests/test_screen_flow.py`：覆盖 `find_resumable_screen_run` 四种状态、字段不一致不续跑、`build_round_script_params` 合并、`build_round_context_payload` 字段完整。
- [ ] T010 [P] 新建 `webui/src/__tests__/screenFlow.spec.ts`：覆盖状态派生、文案、多平台目标选择、`isResumableStatus`。

**Checkpoint**: 基础层测试通过后，开始 US1。

## Phase 3: 用户故事 1 - 暂停后可查看部分结果并从断点继续（P1）

**Goal**: 用户暂停 AI 筛选后，04 立即可查看部分结果，03 提供“继续 AI 筛选 + 查看结果 + 结束并保存结果”，04 继续直接续跑。

**Independent Test**: `tests/test_webui_app.py` 覆盖暂停路由与暂停快照；`ScreenRoundActions.spec.ts`、`useScreenRoundFlow.spec.ts`、`DiscoveryView.spec.ts` 覆盖按钮矩阵与续跑调用。

### Tests for User Story 1

- [ ] T011 [P] [US1] 在 `tests/test_webui_app.py` 新增后端测试：`POST /api/task/pause/<run_id>` 对 AI run 返回 `pausing`；worker 暂停后原 run 为 `paused`、`error_code=user_paused`、verdicts/checkpoints 保留，并生成 `record_kind=result_snapshot` 的 partial 快照。
- [ ] T012 [P] [US1] 新建 `webui/src/components/__tests__/ScreenRoundActions.spec.ts`：覆盖 AI 运行中只有“暂停筛选”、暂停后提供“继续 AI 筛选 + 查看结果 + 结束并保存结果”、点击暂停显示“正在暂停…”并禁用。
- [ ] T013 [P] [US1] 新建 `webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`：覆盖 `pauseScreen()` 调用 `/api/task/pause/<run_id>`、轮询到 paused 后切换动作；`continueScreen()` 恢复上下文并调用续跑接口。

### Implementation for User Story 1

- [ ] T014 [US1] 修改 `webui/app.py`：新增 `POST /api/task/pause/<run_id>` 最小路由，校验 run 存在、属于 AI 筛选且状态为 queued/running；设置内存任务 `stop_mode="pause"` 与 stop_event，返回 `{ok:true, run_id, status:"pausing"}`。
- [ ] T015 [US1] 修改 `webui/app.py` `_run_ai_screen_task`：在 `_stop_requested()` 分支判断 `task["stop_mode"]=="pause"`，调用新增暂停处理：先按现有部分结果构建逻辑保存 `partial` 快照（`script_params` 使用 `screen_flow.build_round_script_params`），再把原 run 写为 `paused`（`error_code="user_paused"`、`error_reason="用户已暂停，结果已保留"`），内存 task 同步 `paused`，追加 `pause` 事件；取消语义保持不变。
- [ ] T016 [US1] 新建 `webui/src/composables/useScreenRoundFlow.ts`：实现 `pauseScreen()`（带“正在暂停…”反馈，轮询 `/api/task-state/<run_id>` 直到 paused）、`continueScreen()`（调用现有续跑入口并自动跳 03）、`startScreen()`；所有任务按钮统一先禁用再请求。
- [ ] T017 [US1] 新建 `webui/src/components/ScreenRoundActions.vue`：渲染 03 AI 主动作按钮组（运行中/暂停/失败），props 传入 action、busy label、snapshot，事件 `pause`、`continue`、`view-results`、`finish-save`。
- [ ] T018 [US1] 修改 `webui/src/views/DiscoveryView.vue`：用 `useScreenRoundFlow` 与 `ScreenRoundActions` 替换 03 现有 `start-ai-screen`/`resume-ai-screen`/`finish-active-screen`/`cancel-paused-screen` 等散落按钮；把 04 `continue-ai-from-results` 从 `enterScreenStep()` 改为 `continueScreen()`；移除 AI 流程的 `取消任务` 入口；保持现有页面布局，按钮只在原位置替换。

**Checkpoint**: US1 完成，暂停/继续主链路可独立验证。

## Phase 4: 用户故事 2 - 返回 02/03 时本轮条件完整回显（P1）

**Goal**: 关键词、城市、六类条件、画像和“我已确认”随本轮恢复；高级执行设置不随轮恢复。

**Independent Test**: `tests/test_webui_app.py` 断言接口返回 `round_context`；`useScreenRoundFlow.spec.ts` 与 `DiscoveryView.spec.ts` 断言 02/03 回填。

### Tests for User Story 2

- [ ] T019 [P] [US2] 在 `tests/test_webui_app.py` 新增测试：`GET /api/latest-running-task`（paused/interrupted）与 `GET /api/latest-pipeline-result` 返回 `round_context`，关键词/城市/条件/画像与来源 run 一致。
- [ ] T020 [P] [US2] 在 `webui/src/composables/__tests__/useScreenRoundFlow.spec.ts` 新增用例：`restoreRoundContext()` 回填关键词、城市、条件、画像，并把“我已确认”置为 true；画像 watcher 不把程序恢复误判为人工修改。
- [ ] T021 [US2] 更新 `webui/src/views/__tests__/DiscoveryView.spec.ts`：从 04 返回 02/03 后关键词、城市、六类条件有值，画像确认态为已确认；高级执行设置不被覆盖；补充 2993 回归场景：已冻结条件恢复为空时不得无条件下发初筛。

### Implementation for User Story 2

- [ ] T022 [P] [US2] 修改 `webui/app.py`：`latest-running-task` 的 paused/interrupted 分支与 `latest-pipeline-result` 响应增加 `round_context`，统一调用 `screen_flow.build_round_context_payload`；缺失来源 run 时不伪造，返回空对象或省略字段。
- [ ] T023 [US2] 修改 `webui/src/composables/useScreenRoundFlow.ts`：实现 `restoreRoundContext(ctx)`，用一次性 suppress 标志避免 `profileSummary` watcher 重置 `profileConfirmed`；按 `ctx.platform` 写入对应 `filterValues` 槽，关键词/城市归一化后回填 02；已冻结条件恢复不到时阻断继续并提示，禁止以空条件发起初筛。
- [ ] T024 [US2] 修改 `webui/src/views/DiscoveryView.vue`：在 `restoreRunningTask` 与 `loadLatestResult` 中，当响应含 `round_context` 时调用 `restoreRoundContext`；删除旧的零散 profile/filter 回填（保留任务快照赋值）。

**Checkpoint**: US2 完成，02/03 恢复无空值。

## Phase 5: 用户故事 3 - 重抓进度集中到 03（P2）

**Goal**: 04 触发“全部重抓”并选择平台，03 显示进度和暂停/继续/结束控制，完成自动回 04。

**Independent Test**: `DiscoveryView.spec.ts` 覆盖自动跳转与自动返回；`ScreenRoundActions.spec.ts` 覆盖重抓三态按钮。

### Tests for User Story 3

- [ ] T025 [P] [US3] 更新 `webui/src/components/__tests__/ScreenRoundActions.spec.ts`：重抓运行中显示“暂停重抓 + 结束并保存结果”、暂停后显示“继续重抓 + 查看结果”、失败后显示“继续重抓 + 结束并保存结果”。
- [ ] T026 [US3] 更新 `webui/src/views/__tests__/DiscoveryView.spec.ts`：点击 04 “全部重抓”后自动进入 03；重抓完成自动回 04；暂停/失败留在 03。

### Implementation for User Story 3

- [ ] T027 [US3] 修改 `webui/src/composables/useScreenRoundFlow.ts`：实现 `startRecrawl()`（沿用现有 `recrawlUncertain` 请求，成功后切到 03）、`pauseRecrawl()`、`continueRecrawl()`、`finishRecrawl()`；完成时回调切回 04，暂停/失败留在 03。
- [ ] T028 [US3] 修改 `webui/src/components/ScreenRoundActions.vue`：增加重抓动作区，渲染 03 内的重抓进度与按钮组。
- [ ] T029 [US3] 修改 `webui/src/views/DiscoveryView.vue`：删除 04 `recrawl-banner` 内嵌 `TaskProgress` 与重抓控制按钮，保留 04 “全部重抓”触发和平台选择；03 接入 `ScreenRoundActions` 重抓区。

**Checkpoint**: US3 完成，重抓进度只出现在 03。

## Phase 6: 用户故事 4 - 失败、双平台和退出路径不出现死路（P2）

**Goal**: 失败态有“继续 + 结束保存”出口；双平台可续时先选平台；保存后未完成仍可续跑；“开始新一轮”有确认。

**Independent Test**: `tests/test_screen_flow.py`、`tests/test_webui_app.py` 覆盖续跑候选；`screenFlow.spec.ts` 与 `DiscoveryView.spec.ts` 覆盖双平台与确认。

### Tests for User Story 4

- [ ] T030 [P] [US4] 扩展 `tests/test_screen_flow.py`：`find_resumable_screen_run` 对 `failed`、`interrupted(user_finished)`、`partial` 均返回候选；字段/画像不一致时不返回。
- [ ] T031 [P] [US4] 扩展 `tests/test_webui_app.py`：`POST /api/ai-screen` 对 failed/partial/user_finished 旧 run 返回 `resuming=true`，新 run 继承已判定/JD；JD checkpoint 缺失时从 `screening_results` 回退。
- [ ] T032 [P] [US4] 更新 `webui/src/__tests__/screenFlow.spec.ts`：双平台续跑目标选择（all+两边可续返回两项、单边可续返回一项）；`开始新一轮` 确认规则。
- [ ] T033 [US4] 更新 `webui/src/views/__tests__/DiscoveryView.spec.ts`：AI 失败显示“继续 AI 筛选 + 结束并保存结果”；存在可续跑任务时点“开始新一轮”先弹确认。

### Implementation for User Story 4

- [ ] T034 [US4] 修改 `webui/screen_flow.py` 与 `webui/app.py`：`ai-screen` 使用 `find_resumable_screen_run` 扩展候选状态；`resume_from_run_id` 继承旧 run verdicts/JD/checkpoint；旧 run 为 `interrupted(user_finished)` 时允许 `error_code="resumed"` 元数据更新。
- [ ] T035 [US4] 修改 `webui/screen_flow.py`：续跑加载 `resume_jd` 时先读 JD checkpoint，缺失则调用 `store_screen_resume_mixin.load_screening_jd_map(resume_from_run_id)` 回退。
- [ ] T036 [US4] 修改 `webui/src/composables/useScreenRoundFlow.ts`：实现多平台续跑选择（04 all 且两边可续时弹平台选择，单边直接续）；实现“开始新一轮”确认回调。
- [ ] T037 [US4] 修改 `webui/src/views/DiscoveryView.vue`：接入多平台选择与“开始新一轮”确认；失败态按钮组由 `ScreenRoundActions` 统一渲染；“结束并保存结果”后 04 仍显示“继续 AI 筛选”。

**Checkpoint**: US4 完成，所有中断状态都有出口。

## Phase 7: 跨切面验证与收口前检查

**Purpose**: 聚焦测试、全量门禁与回归确认。

- [ ] T038 [P] 运行后端聚焦测试：`uv run python -m unittest tests.test_screen_flow tests.test_store_screen_resume tests.test_webui_app`。
- [ ] T039 [P] 运行前端聚焦测试：`cd webui && npm test -- screenFlow.spec.ts useScreenRoundFlow.spec.ts ScreenRoundActions.spec.ts DiscoveryView.spec.ts DiscoveryScrapeOnly.spec.ts DiscoveryHistoryMode.spec.ts`。
- [ ] T040 [P] 运行 `cd webui && npm run build`，确认 dist 与源码同步。
- [ ] T041 运行后端全量测试：`uv run python -m unittest discover tests`。
- [ ] T042 运行仓库卫生与差异检查：`uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`。

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 2（Foundational）**：T002-T010 完成前，所有用户故事不得开始。
- **US1**：依赖 T002-T010；完成 T014-T018 后可独立验证暂停/继续主链路。
- **US2**：依赖 T005/T006/T007 与 US1 的 composable/组件接线；可与 US3 并行。
- **US3**：依赖 US1 的组件/composable 基础；可与 US2 并行。
- **US4**：依赖 US1 与 US2；后端续跑候选依赖 T005。
- **Phase 7**：依赖 US1-US4 全部完成。

### 并行机会

- Foundational：T002-T010 中不同文件的任务可并行。
- US1 测试 T011-T013 可并行；实现 T014-T018 内部按“路由 → worker → composable → 组件 → 视图接线”串行。
- US2 后端接口任务与 US3 前端重抓任务可并行（不同文件）。
- US4 后端测试 T030/T031 可并行，前端 T032/T033 可并行。

## Implementation Strategy

### MVP First（US1）

1. 完成 Phase 2 基础层。
2. 完成 US1 暂停/继续主链路。
3. 独立验证：暂停后 04 有部分结果，03 提供“继续 AI 筛选 + 查看结果 + 结束并保存结果”，04 继续直接续跑。
4. 再增量交付 US2 上下文回显、US3 重抓进度、US4 失败/双平台出口。

### 增量交付顺序

1. Foundational → US1 → US2 → US3 → US4。
2. 每个用户故事完成后跑该故事独立测试。
3. 全部完成后执行 Phase 7 全量门禁。

## Notes

- 禁止修改 Forbidden files；超大文件只允许最小接线。
- 布局基本不动：按钮增删/合并只在现有位置，具体位置调整需用户确认。
- 2993 为回归验收：已冻结六类条件恢复为空时，禁止以空条件发起初筛；恢复不到必须阻断并提示。
- 所有任务按钮必须有点击后禁用与动作中文案反馈。
- 不新增“换条件重配”功能；筛选开始后条件锁定。
- 本清单只覆盖实现与验证，不含外部收口动作。
