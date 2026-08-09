# Tasks: 抓取恢复链路修复

**输入**：`specs/005-scrape-recovery-chain/` 下冻结的 `spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/state-recovery.md`、`quickstart.md`

**执行前必读**：仓库根 `AGENTS.md`、本目录全部文档、`BACKLOG.md` 中 B027/B029/B030/B013。发现规格与代码冲突时停止并报告，不自行改规格。

**硬约束**：只做只读核实、修改、测试与报告；禁止执行任何仓库同步或产物分发动作，禁止删除文件。测试日志写入系统临时目录，不写入项目根目录。

**格式**：`- [x] TXXX [P] [USn] 描述（文件路径）`；`[P]` 表示可与同阶段其它 `[P]` 任务并行。

## Phase 1: Setup

**目的**：确认现有测试基线，作为修改前对照。

- [x] T001 运行现有后端与前端测试基线，记录失败项（如：`uv run python -m unittest tests.test_healthy_pipeline tests.test_webui_app tests.test_source tests.test_cooldown tests.test_repo_hygiene`，`cd webui && npm test`）

**检查点**：基线已知；后续改动不得引入未解释的新失败。

## Phase 2: Foundational（阻塞前置）

**目的**：完成后端状态机、接管释放、worker 终态保护、快照平台与父任务来源基础，供 US1-US4 复用。

- [x] T002 在 `webui/store.py` 新增原子 `finish_screening_run(run_id)`：允许 `queued/running/paused/failed` 及 `interrupted(process_restart/operator_stop)` 进入 `interrupted + error_code=user_finished + interruption_kind=user_cancelled`；`failed → interrupted` 加入状态机许可；`user_cancelled` 与 `succeeded/partial` 拒绝
- [x] T003 在 `webui/app.py` 增加统一的 `_release_resume_claim` 释放点：`_run_pipeline_task`、`_run_ai_screen_task`、`_run_recrawl_task` 的 done/failed/paused/cancelled 路径均释放对应 `resumed_from`/`resuming_from` 标记
- [x] T004 在 `webui/app.py` 增加“用户已结束”检测（如 `_is_user_finished(run_id)`），finish 先原子标记；`_run_pipeline_task`、`_run_ai_screen_task`、`_run_recrawl_task` 终态写入前检查，已 `user_finished` 时跳过 DB 状态覆盖，只更新内存快照
- [x] T005 在 `webui/app.py` 修改 `_build_partial_pipeline_result`：新增 `platform` 参数并写入 jobs 与 dropped；`api_task_finish` 调用时传任务平台
- [x] T006 在 `webui/app.py` 让 `save_pipeline_result` 的调用方（finish 与 AI 完成路径）在 `script_params/execution_params` 写入 `platform` 与 `scrape_task_id`；`latest_pipeline_result` 响应增加 `scrape_task_id`

**检查点**：基础函数与状态语义已具备；随后各用户故事只做集成与界面。

## Phase 3: User Story 1 - 大抓取中断后内容不归零（P1）

**目标**：刷新/重启后暂停、失败、中断的抓取任务显示真实数量，有数据不显示 0。

**独立测试**：构造 `scrape_run_jobs` 非空的任务并分别置为 `failed/paused/interrupted(restart)`，刷新后断言数量真实且不为 0；真实空数据仍显示 0。

### Tests for User Story 1

- [x] T007 [P] [US1] 后端测试：`/api/latest-running-task` 对 failed 抓取返回 `has_task/scraped_count/source_total/platform`，写入 `tests/test_webui_app.py`
- [x] T008 [P] [US1] 后端测试：paused/interrupted/failed 恢复返回真实计数，`user_finished` 不再恢复，已有更新结果快照时旧 failed 不恢复，恢复优先级正确，写入 `tests/test_webui_app.py`
- [x] T009 [P] [US1] 前端测试：刷新后 failed/interrupted scrape 显示真实数量与“结束并保存结果”，写入 `webui/src/views/__tests__/DiscoveryView.spec.ts`

### Implementation for User Story 1

- [x] T010 [US1] 在 `webui/app.py` 的 `latest_running_task` 增加“最近可恢复抓取”兜底：`kind=scrape`、状态为 `paused/failed/interrupted(restart/operator_stop)`、`scrape_run_jobs` 非空且未 `user_finished`；`scraped_count` 以 `scrape_run_jobs` 行数为准，套用“已有更新结果快照则跳过”保护，恢复优先级 running > paused > restart-interrupted > failed
- [x] T011 [US1] 在 `webui/app.py` 的 paused/interrupted 分支同样返回 `scraped_count/source_total`，确保所有恢复路径计数同源
- [x] T012 [US1] 在 `webui/src/views/DiscoveryView.vue` 的 `restoreRunningTask` 中对 failed/interrupted scrape 调用 `task-state` 并填充 `scrapeSnapshot` 计数；02 页主数字显示 `scraped_count` 岗位数，禁止在未加载真实数据时显示 0

**检查点**：有数据任务刷新后数量真实；无数据仍显示 0。

## Phase 4: User Story 2 - 续跑失败后仍能结束保存（P1）

**目标**：暂停、失败、重启中断、运行中的任务都可结束保存；结束/续跑/取消不互锁。

**独立测试**：暂停→续跑→再失败后调用 finish 成功；running 任务调用 finish 成功且 worker 不覆盖终态。

### Tests for User Story 2

- [x] T013 [P] [US2] 后端测试：`/api/task/finish` 接受 `failed` 与 `running` 并保存 partial 快照，写入 `tests/test_webui_app.py`
- [x] T014 [P] [US2] 后端测试：续跑再失败后 finish 不被“已被续跑接管”拒绝，写入 `tests/test_healthy_pipeline.py`
- [x] T015 [P] [US2] 后端测试：running 中 finish 后 worker 终态不覆盖 `user_finished`，快照以请求时已持久化数据为边界且未落库批不进入，写入 `tests/test_healthy_pipeline.py`
- [x] T016 [P] [US2] 前端测试：failed/running 状态显示“结束并保存结果”，保存后不自动跳结果页，写入 `webui/src/views/__tests__/DiscoveryView.spec.ts`

### Implementation for User Story 2

- [x] T017 [US2] 在 `webui/app.py` 扩展 `api_task_finish` 状态门：允许 `queued/running/paused/failed/restart-interrupted`；运行中先 `stop_event.set()` 并关闭调试浏览器，再从持久化数据生成快照；快照边界以请求时已持久化数据为准，未落库批不保证进入
- [x] T018 [US2] 在 `webui/app.py` 的 `api_task_finish` 中兜底释放陈旧 `_resume_claims`，并调用 `store.finish_screening_run` 原子收尾
- [x] T019 [US2] 在 `webui/src/views/DiscoveryView.vue` 增加 failed/running 的“结束并保存结果”入口；`finishPausedTask` 调用前先清 `pollTimer`，成功后不清空当前步骤，设置 `scrapeCompleted/scrapeTaskId/resultLoaded`

**检查点**：四类状态均可结束保存；worker 不覆盖；前端不强制跳结果页。

## Phase 5: User Story 3 - 结束保存后继续 AI 筛选（P1）

**目标**：保存部分结果后可基于已抓岗位继续 AI 筛选；刷新后 03 页能找回父抓取任务。

**独立测试**：保存 partial → 刷新 → 进入 03 启动 AI 筛选成功，不报“缺少任务”。

### Tests for User Story 3

- [x] T020 [P] [US3] 后端测试：`/api/latest-pipeline-result` 返回 `scrape_task_id` 且与父任务一致，写入 `tests/test_webui_app.py`
- [x] T021 [P] [US3] 前端测试：`loadLatestResult` 恢复 `scrapeTaskId`，03 页启动筛选携带正确任务 ID，写入 `webui/src/views/__tests__/DiscoveryView.spec.ts`

### Implementation for User Story 3

- [x] T022 [US3] 在 `webui/src/views/DiscoveryView.vue` 的 `loadLatestResult`/`setPipelineResult` 中恢复 `scrapeTaskId` 与 `resultRunIds`，并防御性回填 `platform`
- [x] T023 [US3] 在 `webui/src/views/DiscoveryView.vue` 增加结束保存后的“查看结果”与“继续 AI 筛选”入口；结果页也提供回到 03 的入口
- [x] T024 [US3] 在 `webui/src/views/DiscoveryView.vue` 的 `startAiScreen` 中：`scrapeTaskId` 为空时从已加载结果快照恢复，仍为空则明确提示，不请求后端

**检查点**：保存后继续筛选可用；刷新后 03 不报缺任务。

## Phase 6: User Story 4 - 未跑 AI 筛选的保存结果带平台角标（P2）

**目标**：未跑 AI 筛选直接结束保存，岗位带正确平台角标，单平台视图不丢数据。

**独立测试**：BOSS/智联 partial 结果即时响应与刷新后均带平台，单平台视图保留率 100%。

### Tests for User Story 4

- [x] T025 [P] [US4] 后端测试：finish 返回的 partial `jobs/dropped` 均带 `platform`，写入 `tests/test_webui_app.py`

### Implementation for User Story 4

- [x] T026 [US4] 在 `webui/src/views/DiscoveryView.vue` 的 `setPipelineResult`/`fetchMergedLatestResult` 中对缺 `platform` 的岗位按来源回填（后端权威优先，仅作兼容兜底）

**检查点**：未跑 AI 筛选的结果在即时与刷新路径都有平台角标。

## Phase 7: User Story 5 - 全部重抓入口三档可见（P2）

**目标**：全部/BOSS/智联三档都显示“全部重抓”；全部视图引导选择平台；滑块与按钮不重叠。

**独立测试**：三档按钮可见；全部视图点击不发起请求而引导选择；单平台正常重抓；桌面与窄屏无重叠溢出。

### Tests for User Story 5

- [x] T027 [P] [US5] 前端测试：待确认分类下全部/BOSS/智联三档均显示“全部重抓（N）”，全部视图点击出现平台选择引导且不调用 `/api/pipeline/recrawl`，写入 `webui/src/views/__tests__/DiscoveryView.spec.ts`
- [x] T028 [P] [US5] 前端测试：单平台视图点击后按当前平台 `source_run_id` 调用重抓，写入 `webui/src/views/__tests__/DiscoveryView.spec.ts`
- [x] T029 [P] [US5] 布局测试：`tests/sc015_viewport_check.py` 已增加 390×844 视口与滑块/按钮相交断言，`DiscoveryView.spec.ts` 校验脚本与 CSS 约束；真实运行 375/390/768/1440 四档全部 PASS，768 下 `.results-stage` 网格轨道被内容撑宽的问题已通过 `grid-template-columns: minmax(0, 1fr)` 修复

### Implementation for User Story 5

- [x] T030 [US5] 在 `webui/src/views/DiscoveryView.vue` 移除重抓入口的 `resultPlatformFilter !== "all"` 条件，三档均显示
- [x] T031 [US5] 在 `webui/src/views/DiscoveryView.vue` 的 `recrawlUncertain` 增加全部视图分支：弹出平台选择引导（BOSS/智联）并显示各平台待确认数量，数量为 0 的平台禁用或明确提示；确认后切换到对应视图并启动该平台重抓；不创建混合任务
- [x] T032 [US5] 在 `webui/src/components/JobWorkspace.vue` 与 `webui/src/styles.css` 调整头部布局：滑块不再绝对居中压住右侧按钮，窄屏换行策略稳定，按钮文字不截断

**检查点**：三档入口可见；全部视图只引导不混合重抓；布局无重叠。

## Phase 8: User Story 6 - 受限判断准确且文案平台隔离（P2）

**目标**：高置信风控才暂停/冷却；智联文案不出现 BOSS。

**独立测试**：普通词样本不判受限不写冷却；高置信样本暂停并写冷却；智联失败文案无 BOSS。

### Tests for User Story 6

- [x] T033 [P] [US6] 后端测试：普通词不判受限、不写冷却；HTTP 429/403/412/418、验证码、解封时间判受限并写冷却，写入 `tests/test_source.py`、`tests/test_webui_app.py` 与 `tests/test_inprocess_execution.py`
- [x] T034 [P] [US6] 后端测试：`api_task_state` 与重抓暂停等非 task-state 消费点的 `pause_info` 均按平台取文案，智联不出现 BOSS 文案，写入 `tests/test_webui_app.py` 与 `tests/test_healthy_pipeline.py`
- [x] T035 [P] [US6] 后端测试：`/api/env-check` 冷却记录返回 `from_run`，写入 `tests/test_webui_app.py`

### Implementation for User Story 6

- [x] T036 [US6] 在 `webui/app.py` 收紧 `_SCRAPE_BLOCK_PATTERNS`/`_RISK_CONTROL_REASON_PATTERNS`，并补解封时间/HTTP 403/412/418 识别：裸词不单独判受限，通用失败归 `source_unknown_error`
- [x] T037 [US6] 在 `webui/source.py` 收紧 `_RATE_LIMIT_KEYWORDS`/`_VERIFICATION_KEYWORDS`/`_classify_failed_code`，通用失败归 `source_unknown_error`；`_record_risk_signals` 仅高置信码写冷却与 restricted 缓存
- [x] T038 [US6] 在 `webui/pipeline_exec.py` 将 `_FAILED_CODE_LABELS`/`ERROR_TAXONOMY` 平台化（新增平台参数或按平台映射），列出全部消费点（pipeline emit、`_run_recrawl_task`、`_pause_recrawl_source_unavailable`、`api_task_state` 等）统一走平台化入口，`webui/app.py` 的 `api_task_state` 按 run 平台取文案
- [x] T039 [US6] 在 `webui/app.py` 的 `/api/env-check` 返回 `from_run`，`webui/src/components/EnvCheckDialog.vue` 展示冷却来源；BOSS/智联 source 构造透传 `run_id`，真实冷却记录带来源

**检查点**：风控判定准确、冷却写入有边界、智联文案无 BOSS、冷却来源可见。

## Phase 9: Polish & 全量验证

**目的**：最终回归与交付证据。

- [x] T040 运行后端全量：`uv run python -m unittest discover -s tests -p "test_*.py"`；实际 2006 例，2003 通过、3 跳过，全部通过
- [x] T041 运行前端全量：`cd webui && npm test`
- [x] T042 [P] 构建同步检查：`cd webui && npm run build`，确认 `webui/dist` 与源码一致
- [x] T043 按 `quickstart.md` 执行场景 A-F：A/B/C 由 `test_healthy_pipeline.py` + `test_webui_app.py` 覆盖，D 由 `test_webui_app.py` + `DiscoveryView.spec.ts` 覆盖，E 由 `DiscoveryView.spec.ts` + SC-015 真实验证覆盖，F 由 `test_source.py` + `test_inprocess_execution.py` + `test_webui_app.py` + `test_healthy_pipeline.py` 覆盖；对应测试全绿
- [x] T044 汇总交付：已改文件、测试证据、未验证边界；不做任何仓库同步或产物分发动作

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1（基线）→ Phase 2（状态机基础）→ 用户故事按 P1 优先级推进
- US1/US2 依赖 Phase 2；US3 依赖 US2 的前端收尾语义与 Phase 2 的父任务来源；US4 依赖 Phase 2 的 platform 基础；US5/US6 相对独立，可与 US1-US4 并行
- Phase 9 依赖全部用户故事

### User Story Dependencies

- US1：无跨故事依赖（除 Phase 2）
- US2：无跨故事依赖（除 Phase 2）
- US3：依赖 US2 的“保存后停留当前步骤”与 Phase 2 的快照父任务来源
- US4：依赖 Phase 2 的 platform 写入
- US5：无跨故事依赖
- US6：无跨故事依赖

### Parallel Opportunities

- T007/T008/T009 可并行；T013/T014/T015/T016 可并行；T020/T021 可并行；T025 独立；T027/T028/T029 可并行；T033/T034/T035 可并行
- US5 与 US6 可独立并行（不同文件为主；`DiscoveryView.vue` 若同时被 US1-US4 修改，按串行集成处理）

## Implementation Strategy

### MVP First

1. Phase 1 基线 → Phase 2 状态机基础
2. US1（不归零）独立验证
3. US2（结束保存）独立验证
4. US3（继续 AI 筛选）独立验证
5. US4（平台角标）独立验证
6. US5（全部重抓）独立验证
7. US6（风控/文案）独立验证
8. Phase 9 全量回归

### 实施纪律

- 每个用户故事先写/补测试，再实现，最后跑聚焦测试
- 同一文件串行修改，避免并行冲突；不同文件可并行
- 遇到测试失败先定位根因再修，不用聚焦测试冒充全量
- 最终全量必须基于最终代码执行；不做任何仓库同步或产物分发动作
