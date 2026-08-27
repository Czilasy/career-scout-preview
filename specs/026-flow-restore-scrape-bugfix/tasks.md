# Tasks: 流程恢复与抓取链路三处缺陷修复

**Input**: `/specs/026-flow-restore-scrape-bugfix/`（spec.md / plan.md，冻结需求 3 条）

**Prerequisites**: plan.md（文件边界与方案要点）

**Tests**: 按项目惯例"测试先写后实现"（先红后绿）。本批为三个独立缺陷修复，三个 US 文件互不重叠，可并行。

**Organization**: 按用户故事分组；US1（B078 前端恢复，P1）最先，US2（B079 写盘重试，P2）、US3（B080 重抓 JD，P2）随后；三者互相独立。

## File Boundaries

- **Allowed files**:
  - B078（US1）：`webui/src/composables/useDiscoveryWorkflow.ts`、`webui/src/composables/useDiscoveryExecution.ts`、`webui/src/composables/useDiscoveryTasks.ts`、`webui/src/views/DiscoveryView.vue`（仅最小组装）、`webui/src/composables/__tests__/*.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`
  - B079（US2）：`scripts/boss/output.py`、`tests/test_boss_output.py`（或并入现有测试文件）
  - B080（US3）：`webui/runners/recrawl_task.py`、`tests/test_recrawl_task.py`（或现有 recrawl 测试）
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`（门面，本批不追加逻辑）；`webui/ai_screening.py`、`webui/screening_jd_gate.py`（精筛判定不动）；`webui/store_*.py`（数据层不动）
- **New files**: 无（均为既有文件定点修复）
- **Reference direction**: 后端 `runners/recrawl_task.py → ai_screening.match_jds`；`scripts/boss/output.py` 自足重试；前端 `DiscoveryView.vue → useDiscoveryWorkflow/useDiscoveryExecution/useDiscoveryTasks`
- **Line gate**: 目标文件均小增量，不触及 600/900 预警线

## Verification Gate

- 交付门禁：相关模块聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 卫生检查。
- 用户端到端真跑验证在交付后进行。

---

## Phase 1: User Story 1 - 完成流程后刷新只显示 01 页（B078，P1） 🎯 MVP

**Goal**: 以"用户上次是否进过 04 页"为唯一判据——进过 04 页＝已结束，启动/刷新只显示 01 页（不恢复 02/03 页、不弹"服务重启被中断"提示）；没进过＝未结束，恢复半截流程续跑。纯前端，后端零改动。

**Independent Test**: mock 上一次流程已进 04 页 + 后端 `/api/latest-running-task` 返回历史 interrupted run → 启动只显示 01 页、无"被中断"提示；mock 未进 04 页 → 恢复 02/03 页续跑。

### 测试（先写、先红）

- [x] T001 [US1] `webui/src/composables/__tests__/useDiscoveryExecution.spec.ts`：mock `/api/latest-running-task` 返回 `status=interrupted`（screen 任务）＋ 已恢复的 workflow 快照 `resultsPageSeen=true`（上次已进 04 页）→ `restoreRunningTask` 不设 `interruptedRunId`、不 `enterScreenStep`、不弹"服务重启被中断"提示，保持 01 页（FR-002/FR-003）
- [x] T002 [US1] `webui/src/composables/__tests__/useDiscoveryExecution.spec.ts`：mock 同 interrupted 返回 ＋ 恢复快照 `resultsPageSeen=false`（本次未进 04 页）→ 走既有 interrupted 恢复（设 `interruptedRunId`、进 02/03 页、弹提示）（FR-004）
- [x] T003 [US1] `webui/src/composables/__tests__/useDiscoveryWorkflow.spec.ts`：`persistWorkflowState` 持久化 `resultsPageSeen` 准确反映"进没进 04 页"；已结束（resultsPageSeen=true）时写入快照 `unfinished` 语义正确（不残留未完成态）（FR-001）
- [x] T004 [US1] `webui/src/composables/__tests__/useDiscoveryTasks.spec.ts`：`maybeAutoStartNewRound` 判定"已完成"用新判据（是否进 04 页/已结束），mock 最新历史轮已完成 + 无进行中任务 → 自动新一轮只显示 01 页；mock 未结束 → 不触发（FR-001/FR-005）
- [x] T005 [US1] `webui/src/views/__tests__/DiscoveryView.spec.ts`：完成态启动（已进 04 页 + 后端残留 interrupted run）→ 界面只显示 01 页、无上一轮残留、无"被中断"提示（FR-002）

### 实现

- [x] T006 [US1] `webui/src/composables/useDiscoveryExecution.ts`：`restoreRunningTask` 的 `interrupted` 分支（约 line 159）——进入前先判断"本次是否未进 04 页"（读恢复的 `resultsPageSeen`）；若已进 04 页（已结束）则**跳过 interrupted 恢复**（不设 `interruptedRunId`/`screenTaskId`、不 `enterScreenStep`、不弹提示），保持 01 页（FR-002/FR-003/FR-005）
- [x] T007 [US1] `webui/src/composables/useDiscoveryWorkflow.ts`：`restoreWorkflowState`/`persistWorkflowState`——确保 `resultsPageSeen` 作为"是否进 04 页"的事实被可靠持久化与恢复，作为恢复判定的唯一闸门；已结束时快照不残留未完成语义（FR-001）
- [x] T008 [US1] `webui/src/composables/useDiscoveryTasks.ts`：`maybeAutoStartNewRound` 判定对齐"是否进 04 页 / 是否已结束"判据，不再依赖 `has_newer_saved_result_than` 之类时间戳推断（FR-005）
- [x] T009 [US1] `webui/src/views/DiscoveryView.vue`：初始化时序——恢复 workflow 状态后，若已结束则不触发 interrupted 恢复，只显示 01 页（最小组装，不追加业务逻辑）（FR-002）

**Checkpoint**: US1 独立可用——完成一轮后刷新/重启界面干净 01 页（含后端残留 interrupted run 场景）、历史可查；未进 04 页的半截流程刷新可续跑（B068 行为不变）。

---

## Phase 2: User Story 2 - 列表抓取文件写失败不再误报「登录态失效」（B079，P2）

**Goal**: `write_json_atomic` 的 `os.replace` 加短暂重试（偶发占用重试即过）；重试耗尽抛专门异常 `ResultFileWriteError`，子进程顶层映射为独立退出码 + 结构化失败行 `source_result_write_failed`，上游分类为"结果文件写入失败"——**任何情况下都不再误报登录态失效**（满足 spec FR-006/007/008）。跨模块链路：`output.py → exceptions.py → boss_cdp_raw.py → error_registry.py → source_boss_helpers.py → 前端 errorCodes.ts`。

**Independent Test**: 注入 `os.replace` 首次抛 OSError、重试成功 → `write_json_atomic` 成功落盘、不抛异常；连续失败超上限 → 抛 `ResultFileWriteError`；子进程捕获 → 输出失败行 `source_result_write_failed` → 分类为"结果文件写入失败"而非"登录态失效"。

### 测试（先写、先红）

- [x] T010 [US2] `tests/test_boss_output.py`（或并入 output 相关测试）：mock `os.replace` 首次抛 OSError → `write_json_atomic` 重试后成功，最终文件完整写入、tmp 已清理（FR-006）
- [x] T011 [US2] `tests/test_boss_output.py`：`os.replace` 连续失败超过重试上限 → `write_json_atomic` 抛 `ResultFileWriteError`（非裸 OSError），tmp 文件在 finally 中清理、不残留（FR-006/FR-008 边界）
- [x] T012 [US2] `tests/test_boss_output.py`：`flush_jobs` 合并逻辑不受重试影响——重试成功后数据完整（jobs 去重、total 正确）（FR-006）
- [x] T013 [US2] `tests/test_error_registry.py`（或错误码校验测试）：`source_result_write_failed` 已注册、文案为"结果文件写入失败"、归类 source、retryable 语义正确、`resolve_code("source_result_write_failed")` 正常（FR-007）
- [x] T014 [US2] 前端 `webui/src/__tests__/errorCodes.spec.ts`：`source_result_write_failed` 镜像同步（由 mirror test 校验，与后端 `to_json()` 一致）（FR-007）

### 实现

- [x] T015 [US2] `scripts/boss/exceptions.py`：新增 `class ResultFileWriteError(RuntimeError)`（结果文件落盘失败，域包异常）
- [x] T016 [US2] `scripts/boss/output.py`：`write_json_atomic` 的 `os.replace(temp_path, path)` 改为带重试的 `_replace_with_retry`（重试 3 次、间隔递增 0.05s 起）；重试耗尽抛 `ResultFileWriteError`（FR-006/FR-008；不改 flush_jobs 合并逻辑）
- [x] T017 [US2] `scripts/boss_cdp_raw.py`（门面，薄映射）：顶层 `except ResultFileWriteError` → `emit_failure_line("source_result_write_failed", str(exc))` + `sys.exit(4)`（新退出码，不与 1/2/3/10/11 冲突；用户已豁免门面此薄映射）
- [x] T018 [US2] `webui/error_registry.py`：`_SOURCE_CODES` 加 `source_result_write_failed`（user_message="结果文件写入失败"，retryable=True，非 systemic/blocking）
- [x] T019 [US2] `webui/src/errorCodes.ts`：镜像加 `source_result_write_failed` + 文案"结果文件写入失败"（与后端 `to_json()` 同步）
- [x] T020 [US2] `webui/source_boss_helpers.py`：`_EXIT_REASONS` 补 `4: "结果文件写入失败"`（无失败行时的兜底文案）

**Checkpoint**: US2 独立可用——列表抓取结果文件写失败（偶发或持续）都明确报"结果文件写入失败"，不再误报"登录态失效"；重试成功 combo 正常完成。

---

## Phase 3: User Story 3 - 重抓补抓到的 JD 真实进入 AI 精筛（B080，P2）

**Goal**: `recrawl_task.py` AI 精筛装配段，把补抓到的 JD 写入传给 `match_jds` 的岗位对象，补抓成功的岗位能正常精筛判定，不再一律"未抓到 JD 无法精筛"。

**Independent Test**: 对缺 JD 岗位构造重抓，补抓返回含 JD 结果 → 重判输入岗位对象携带该 JD → 精筛能正常判定（非"未抓到 JD"）。

### 测试（先写、先红）

- [x] T021 [US3] `tests/test_recrawl_task.py`（或现有 recrawl 测试）：mock `fetched_jd` 含补抓 JD + target 原 jd 为空 → AI 精筛收到的岗位对象 `jd` 字段携带补抓 JD，`has_usable_jd` 判 true、能正常出判定（非"未抓到 JD 无法精筛"）（FR-009/FR-010）
- [x] T022 [US3] `tests/test_recrawl_task.py`：补抓确实失败（JD 仍空）→ 精筛才落"未抓到 JD 无法精筛"（不误判为成功）（FR-010）
- [x] T023 [US3] `tests/test_recrawl_task.py`：多岗位重抓——补抓成功的岗位带 JD 正常判定、失败岗位独立处理，成功的不受失败影响（FR-011）

### 实现

- [x] T024 [US3] `webui/runners/recrawl_task.py`：AI 精筛装配段（约 line 516-519）`jj = dict(j)` 后补 `jj["jd"] = jd`（把 `fetched_jd` 或 target 的 JD 写入精筛输入），补抓成功的岗位 JD 进入 `match_jds`（FR-009；`ai_screening.match_jds`/`screening_jd_gate.has_usable_jd` 判定本身不动）

**Checkpoint**: US3 独立可用——缺 JD 岗位重抓补抓成功后，重判能正常判定；补抓失败才落"未抓到 JD"。

---

## Phase 4: 收口

**Goal**: 全量验证门禁通过。

- [ ] T025 [收口] 验证门禁：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 卫生检查（`tests.test_repo_hygiene` + hooks）全部通过
- [ ] T026 [收口] 用户端到端真跑验证清单（交付后）：①完成一轮后刷新界面干净 01 页、历史可查、连续多次刷新稳定；②未进 04 页的半截流程刷新可续跑；③列表抓取偶发写盘失败不再误报登录失效（含持续写盘失败时报"结果文件写入失败"）；④缺 JD 岗位重抓后能正常判定

---

## Dependencies & Execution Order

### Phase Dependencies

- **US1（B078）**：无前置依赖，可最先开始（P1）
- **US2（B079）**：无前置依赖，独立
- **US3（B080）**：无前置依赖，独立
- **收口**：依赖三个 US 全部完成

### User Story Dependencies

- 三个 US 文件互不重叠（前端 composables/view / 后端 output.py / 后端 recrawl_task.py），**可并行**。

### Parallel Opportunities

- T001-T005（US1 测试）、T010-T014（US2 测试）、T021-T023（US3 测试）可并行
- T006-T009（US1 实现）、T015-T020（US2 实现）、T024（US3 实现）可并行

---

## Implementation Strategy

### MVP First（User Story 1 先交付）

1. 完成 US1（B078）：前端恢复判据改为"是否进 04 页"→ 独立验证（完成一轮后刷新干净 01 页）
2. 完成 US2（B079）：os.replace 重试 → 独立验证（偶发写盘不再误报）
3. 完成 US3（B080）：重抓 JD 写入精筛输入 → 独立验证（重抓后能正常判定）
4. 收口：全量验证门禁

### Incremental Delivery

- 每个 US 都是独立可测、可交付的切片。
- 三个 US 无交叉依赖，可按并行或 P1→P2→P2 顺序推进。

## Notes

- [P] 任务 = 不同文件、无依赖，可并行。
- [Story] 标签将任务映射到对应用户故事。
- 每个 US 先写测试（先红）再实现（先绿）。
- 停在任何 checkpoint 都可独立验证该 US。
