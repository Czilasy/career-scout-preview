---
description: "033 V2 任务完成证据白箱实施清单"
---

# Tasks: 任务完成证据白箱（033 V2）

**Input**: `/specs/033-log-whitebox/v2/` 下的 Spec、Plan、Research、Data Model、Contracts 与 Quickstart

**Implementation owner**: 后续执行 AI

**Review owner**: 本轮 Spec 编写代理只进行审查，不直接实施

**Tests**: 本功能明确要求测试先行。每个阶段必须先让对应错误场景变红，再写实现。

## Format

- `[P]`：不同文件且不存在未完成依赖时可并行
- `[USn]`：对应 `spec.md` 的用户故事
- 所有任务必须遵守 `plan.md` 的文件边界

## File Boundaries

- **新文件**：`webui/whitebox.py`、`webui/whitebox_rules.py`、`webui/store_whitebox.py`、`webui/store_migrations_v5.py`、`tests/test_whitebox_rules.py`、`tests/webui_store/test_store_whitebox.py`、`tests/test_whitebox_integration.py`
- **本轮返修薄提取**：`webui/whitebox_evidence.py`、`webui/task_finish_whitebox.py`、`webui/exec_search_whitebox.py`；只搬运已有白箱证据、手动结束和提交失败逻辑，不增加用户功能或新的业务入口
- **允许修改**：仅限 `plan.md` “Allowed existing backend/frontend/tests and user docs”列出的精确路径
- **禁止修改**：正式数据库、活动任务、`webui/app.py`、`webui/account_round_robin.py`、兼容门面逻辑、范围外同步接口、033 V1 与根目录历史文档、其他未列明文件
- **引用方向**：`api/runner/source → webui.whitebox → webui.store_whitebox`；`webui.whitebox → webui.whitebox_rules`；前端 `component → composable → api`
- **行数门禁**：新 Python 文件低于 600 行；现有超过 600 行的文件只能净减少或薄委托，不能增长新逻辑
- **授权边界**：不得提交、推送、发布、删除文件或清理正式数据

## Phase 1: Safe Setup

**Purpose**: 锁定安全边界和 V2 工作上下文。

- [X] T001 运行只读 `uv run python scripts/db_info.py` 并在实施报告中记录正式库与最新任务；禁止查询或操作活动任务 `45a2dcd730334002b21f30e18d5008e6`，依据 `specs/033-log-whitebox/v2/quickstart.md`
- [X] T002 检查 `git status --short`，确认只处理 `specs/033-log-whitebox/v2/plan.md` 允许的文件；不得清理、覆盖或回退他人改动
- [X] T003 检查 `.specify/feature.json` 指向 `specs/033-log-whitebox/v2`，完整读取 `specs/033-log-whitebox/INDEX.md` 与 V2 全部文档后再实施

**Checkpoint**: 实施者能明确活动任务、正式数据和允许写入范围。

---

## Phase 2: Foundational Whitebox Module

**Purpose**: 建立所有用户故事共用的规则、存储和唯一公共接口。

**Critical**: 本阶段完成前不得接入任何业务流程。

- [X] T004 [P] 在 `tests/test_whitebox_rules.py` 先写完整成功、空结果、部分完成、失败、无法确认、中断、降级、低数量不参与判定、重复收口幂等的失败测试
- [X] T005 [P] 在 `tests/webui_store/test_store_whitebox.py` 先写 schema 33、三表约束、事件只追加、幂等键、三值 `has_more`、历史不回填和应急导入的失败测试
- [X] T006 [P] 在 `tests/test_whitebox_integration.py` 先写“调用方不能指定成功”“所有查询消费同一 revision”“必需写入失败不能成功”的基础失败测试
- [X] T007 实现 `webui/whitebox_rules.py` 的纯归并器、状态优先级、主要原因选择和数量/证据校验，使 T004 转绿
- [X] T008 [P] 在 `webui/store_migrations_v5.py` 实现迁移 033：`whitebox_runs`、`whitebox_units`、`whitebox_events`、唯一约束和查询索引
- [X] T009 在 `webui/store_migrations.py` 只组装 `StoreMigrationsV5Mixin`，并在 `webui/store_migrations_v1.py` 的迁移调度中增加版本 33 调用
- [X] T010 在 `webui/store_whitebox.py` 实现计划、单元尝试、追加事件、报告分页、汇总写入和应急导入的数据访问方法，使 T005 转绿
- [X] T011 在 `webui/store.py` 只增加 `StoreWhiteboxMixin` import 与 MRO 组装，不加入白箱业务逻辑
- [X] T012 在 `webui/whitebox.py` 实现 `begin`、`record`、`finalize`、`report`、事实校验、脱敏复用、主存储失败标记和应急追加处理，使 T006 转绿
- [X] T013 在 `webui/store_constants.py` 定义白箱结论、单元状态、停止原因和对外标签的共享值域，禁止复制多份字符串表
- [X] T014 运行 `tests.test_whitebox_rules`、`tests.webui_store.test_store_whitebox`、`tests.test_whitebox_integration`，确认基础模块全绿且没有正式库写入

**Checkpoint**: 统一模块能独立保存事实并计算结论，尚未依赖业务入口。

---

## Phase 3: User Story 1 — 只有证据齐全才成功 (Priority: P1) MVP

**Goal**: 先封住空结果、部分失败和证据缺失被判为完整成功的路径。

**Independent Test**: 20 组中 1 组失败得到 `partial`；0 条且缺证据得到 `unverifiable`；全部有完成证据才允许成功或空结果。

### Tests

- [X] T015 [P] [US1] 修改 `tests/webui_app/test_webui_app_runtime.py`，把 `test_partial_fail_still_returns_ok_true` 改为期望统一部分完成，并先确认旧实现失败
- [X] T016 [P] [US1] 修改 `tests/webui_app/test_webui_app_taskrun.py`，增加有岗位但缺单元证据不能成功、任务收口读取白箱结论的失败测试
- [X] T017 [P] [US1] 修改 `tests/source/test_source_boss.py` 和 `tests/source/test_source_zhilian.py`，增加空列表无空证据必须拒绝的契约测试

### Implementation

- [X] T018 [US1] 在 `webui/source_breaker.py` 强制 `SourceOutcome` 空列表成功必须携带明确空结果与范围完成证据
- [X] T019 [US1] 在 `webui/pipeline_exec_search.py` 冻结全部计划组合、报告失败/跳过/未知事实，并移除“只要其他组合有结果就整体 ok”的最终判定
- [X] T020 [US1] 在 `webui/runners/pipeline_task.py` 让成功和失败组合都写单元事实，任务结束只调用 `whitebox.finalize`，不直接把 `ok` 映射为成功
- [X] T021 [US1] 在 `webui/task_status.py` 增加白箱结论到普通用户状态的唯一映射，确保 `partial` 与 `unverifiable` 不映射为完整完成
- [X] T022 [US1] 扩充 `tests/test_whitebox_integration.py` 的 0 条无证据、明确空结果、20 组 1 组失败、全部失败和中断场景，使 T015-T017 及新增集成测试转绿

**Checkpoint**: 主抓取已经不能把缺证据、空证据不足或部分失败显示为完整成功。

---

## Phase 4: User Story 2 — 组合与分页证据长期可复核 (Priority: P1)

**Goal**: 保存逐页、停止、数量和字段质量证据，解释任务实际做了什么。

**Independent Test**: 组合完成后仍能查询每页事实，并区分计划范围完成、平台结果已尽、页面返回数、组合唯一数和任务唯一数。

### Tests

- [X] T023 [P] [US2] 修改 `tests/webui_store/test_store_domains.py`，把“组合完成清除页面检查点”旧预期改为恢复点可清理但历史页面证据永久保留，并先确认旧实现失败
- [X] T024 [P] [US2] 在 `tests/source/test_source_boss.py` 增加 `returned_count`、`new_unique_count`、三值 `has_more`、`target_reached` 与 `source_exhausted` 测试
- [X] T025 [P] [US2] 在 `tests/source/test_source_zhilian.py` 增加与 BOSS 同语义的页面、明确空结果和停止原因测试
- [X] T026 [P] [US2] 在 `tests/test_whitebox_integration.py` 增加 4044/3419 类数量分层和 `salary_source=api_empty` 质量汇总测试

### Implementation

- [X] T027 [US2] 在 `scripts/boss/search.py` 发出页面实际返回数、本页新增唯一数、三值 `has_more`、范围完成和明确停止事件
- [X] T028 [US2] 在 `webui/source_boss_cdp.py` 验证并转交 BOSS 页面/结束事实；删除原先“有任意事件即可让空列表成功”的推断，保证文件净不增长
- [X] T029 [P] [US2] 在 `scripts/zhilian/search.py` 发出与 BOSS 契约一致的页面、空结果、范围完成和停止事实
- [X] T030 [US2] 在 `webui/source_zhilian_cdp.py` 验证并转交智联事实；禁止 `signal=ok + []` 绕过空证据，保证文件净不增长
- [X] T031 [US2] 在 `webui/store_scrape_runs.py` 将恢复点与白箱历史事件解耦，移除组合完成后删除唯一页面证据的行为
- [X] T032 [US2] 在 `webui/runners/pipeline_task.py` 汇总各组合输出数量、任务内唯一数量和 `salary_source` 等关键字段质量计数
- [X] T033 [US2] 运行 T023-T026 对应测试，验证页面事件在组合结束后仍可按任务和单元查询

**Checkpoint**: 开发者能解释每组翻页停止和数量差异；零结果有无证据可客观区分。

---

## Phase 5: User Story 3 — 失败、降级、恢复与提交异常可追踪 (Priority: P2)

**Goal**: 把 AI 兜底、恢复链、浏览器/账号事件、提交失败和白箱自身失败绑定到任务并纳入结论。

**Independent Test**: 模拟各类恢复和失败后，报告能定位任务/阶段/单元/尝试，且未补齐工作时不能成功。

### Tests

- [X] T034 [P] [US3] 在 `tests/test_screen_flow.py` 增加 AI 粗筛失败后全部保留必须记录降级且整体不能完整成功的失败测试
- [X] T035 [P] [US3] 在 `tests/test_pipeline_guard.py` 增加卡住、重试、放弃和浏览器恢复事件必须带任务/阶段/单元/尝试的失败测试
- [X] T036 [P] [US3] 在 `tests/test_pipeline_exec_accounts.py` 增加账号快照、分配、切换、撞墙与恢复链的白箱关联测试
- [X] T037 [P] [US3] 在 `tests/webui_app/test_webui_app_taskrun.py` 增加失败/部分/中断恢复不得凭已有岗位升级成功的失败测试
- [X] T038 [P] [US3] 在 `tests/test_whitebox_integration.py` 增加执行器拒绝提交和主/备用白箱落点写入失败的矩阵测试

### AI and runtime implementation

- [X] T039 [US3] 在 `webui/ai_screening.py` 让全部保留路径返回明确的失败原因、兜底动作和 `normal_screening_completed=false`，保证文件净不增长
- [X] T040 [US3] 在 `webui/runners/ai_screen_rough.py` 记录粗筛批次正常完成、请求失败和全部保留事实
- [X] T041 [P] [US3] 在 `webui/runners/ai_screen_fine.py` 统一记录细筛失败、不确定和完成事实
- [X] T042 [US3] 在 `webui/runners/ai_screen_task.py` 用统一收口结果决定完整性，不再只依赖细筛 pending 数，保证文件净不增长
- [X] T043 [P] [US3] 在 `webui/browser_recovery.py` 为重启、成功恢复和放弃事件增加显式任务上下文
- [X] T044 [US3] 在 `webui/pipeline_guard.py` 把卡住、重试、放弃、暂停和兜底写入统一白箱，同时保留既有全局日志
- [X] T045 [US3] 在 `webui/account_round_robin_observability.py` 把账号池、配额、分配、切换、撞墙和接管事实写入统一白箱；不得修改 `webui/account_round_robin.py`
- [X] T046 [US3] 在 `webui/diagnostics.py` 将结构化诊断写入失败升级为 `whitebox_incomplete`，禁止静默返回空结果

### Recovery and submission implementation

- [X] T047 [US3] 在 `webui/running_task_api.py` 用白箱报告处理孤儿任务，不再用检查点数量和岗位数量升级成功
- [X] T048 [US3] 在 `webui/app_support.py` 将 `_ensure_scrape_source` 改为对白箱恢复结论的薄委托，删除“有岗位即 done+ok”推断并保证文件净减少
- [X] T049 [US3] 在 `webui/exec_search_api.py` 统一首次、继续和恢复抓取的提交失败事实与终态，保证文件净不增长
- [X] T050 [US3] 在 `webui/ai_screen_api.py` 让 AI 后台提交失败立即写失败终态和原因，并释放既有 claim
- [X] T051 [US3] 在 `webui/task_continue_api.py` 让继续/恢复提交失败形成可查询终态，保证文件净不增长
- [X] T052 [US3] 运行 T034-T038 对应测试，确认恢复、降级、提交和持久化失败均不会产生完整成功

**Checkpoint**: 主流程所有高风险旁路均受统一结论约束。

---

## Phase 6: User Story 4 — 旧流程、复抓与调参统一 (Priority: P2)

**Goal**: 关闭旧工作台、旧任务、任务化职位复抓和调参的成功旁路。

**Independent Test**: 四类入口发生部分失败、提交失败或测量缺失时，全部生成统一结论。

### Tests

- [ ] T053 [P] [US4] 在 `tests/test_workbench.py` 与 `tests/test_workbench_api.py` 增加旧工作台部分子查询失败、AI 排序降级和提交失败测试（本轮仅复跑既有 73 项回归，未形成新增测试证据）
- [X] T054 [P] [US4] 在 `tests/tuning/test_tuning_manifest.py` 增加测量缺失但存在结果对象时不得补推成功的失败测试
- [X] T055 [P] [US4] 在 `tests/webui_app/test_webui_app_tuning.py` 增加调参提交失败必须结束轮次并保存原因的失败测试
- [X] T056 [P] [US4] 在 `tests/test_whitebox_integration.py` 增加旧任务与任务化职位复抓部分失败/提交失败测试

### Implementation

- [X] T057 [US4] 在 `webui/workbench_runner.py` 为父运行和子查询建立计划单元，记录失败/降级并用统一规则收口
- [X] T058 [US4] 在 `webui/task_runners.py` 让旧任务提交和执行均进入统一白箱，提交失败不得停留 queued/running
- [X] T059 [US4] 在 `webui/pipeline_jobs_api.py` 为任务化职位复抓建立岗位批次单元、记录提交失败并统一收口，保证文件净不增长；即时 `/api/job-detail` 保持范围外
- [X] T060 [US4] 在 `webui/tuning_api.py` 让调参提交失败结束轮次并记录原因
- [X] T061 [US4] 在 `webui/runners/tuning_manifest.py` 删除根据 `jobs`/`verdicts` 数量补推测量成功的逻辑；缺测量时记录无法确认
- [X] T062 [US4] 运行 T053-T056 对应测试，确认所有旧入口和调参均无独立成功推断

**Checkpoint**: 项目不存在可绕过统一白箱的后台任务成功入口。

---

## Phase 7: User Story 5 — 普通提示与开发者报告一致 (Priority: P3)

**Goal**: 普通用户看到简短真实结论，开发者查看完整证据，所有入口一致。

**Independent Test**: 对同一任务请求任务状态、结果历史和开发者报告并渲染前端，结论、原因和 revision 全部一致。

### Backend tests and implementation

- [X] T063 [P] [US5] 在 `tests/test_whitebox_integration.py` 增加任务状态、结果历史、开发者报告一致性、事件分页、任务不存在和历史证据不足测试
- [X] T064 [US5] 在 `webui/task_state_api.py` 增加按 `owner_kind/owner_id` 查询白箱报告的开发者路由，并复用 `whitebox.report`
- [X] T065 [US5] 在 `webui/results_api.py` 返回统一 `integrity`，删除根据 source attempts 重新推断最终状态的逻辑
- [X] T066 [US5] 在 `webui/result_history.py` 返回同一 `integrity`，旧历史任务只读映射为 `legacy_evidence_missing`
- [X] T067 [US5] 在 `webui/running_task_api.py` 分离活动生命周期与完成完整性，确保 paused/running 不被终态文案覆盖

### Frontend tests and implementation

- [X] T068 [P] [US5] 在 `webui/src/__tests__/types.spec.ts` 与 `webui/src/__tests__/discovery.spec.ts` 先写六类完整性结论解析测试
- [X] T069 [P] [US5] 在 `webui/src/components/__tests__/TaskProgress.spec.ts` 与 `webui/src/components/__tests__/DynamicIsland.spec.ts` 先写 partial/unverifiable 不使用完整成功样式和文案的测试
- [X] T070 [P] [US5] 在 `webui/src/composables/__tests__/useDiscoveryTasks.spec.ts` 与 `webui/src/composables/__tests__/useDiscoveryState.spec.ts` 先写同一 integrity 驱动任务与结果状态的测试
- [X] T071 [US5] 在 `webui/src/types.ts`、`webui/src/discovery.ts` 与 `webui/src/api.ts` 增加 `integrity` 契约和开发者报告类型，不复制后端判定规则
- [X] T072 [US5] 在 `webui/src/composables/useDiscoveryTasks.ts` 与 `webui/src/composables/useDiscoveryState.ts` 让任务状态和结果状态只消费 `integrity`
- [X] T073 [US5] 在 `webui/src/components/TaskProgress.vue` 显示完整成功、空结果、部分完成、失败、无法确认和中断的简短中文提示
- [X] T074 [US5] 在 `webui/src/components/DynamicIsland.vue` 接入相同结论和色调；只改状态语义，不进行布局、主题或动画改版
- [X] T075 [US5] 运行 T063、T068-T070 对应后端和前端聚焦测试，确认四个读取入口一致

**Checkpoint**: 用户不再看到虚假绿色成功，开发者可按任务复核完整证据。

---

## Phase 8: Cross-Cutting Documentation and Governance

**Purpose**: 同步用户文档、模块地图和安全规则，不扩大实现范围。

- [X] T076 [P] 在 `README.md` 说明任务可能显示完整成功、空结果、部分完成、失败和无法确认，以及普通用户的建议动作
- [X] T077 [P] 在 `CHANGELOG.md` 用 3-6 条用户可感知中文记录本次增加/优化/修复，不写表名、代码结构或测试细节
- [X] T078 在 `.specify/memory/constitution.md` 的模块地图登记 `webui/whitebox.py`、`webui/whitebox_rules.py`、`webui/store_whitebox.py`、`webui/store_migrations_v5.py`；不改变原则或宪法版本
- [X] T079 运行 `uv run python -m unittest tests.test_repo_hygiene`，用现有卫生规则验证新增模块地图、文件行数、引用方向和异常留痕；失败时只修本计划允许的目标文件
- [ ] T080 对照 `specs/033-log-whitebox/v2/contracts/` 检查实现字段、状态值、错误码、分页和脱敏完全一致，发现偏差时修实现而不是私自改验收（尚未形成独立逐项核对记录）

---

## Phase 9: Converge and Verification

**Purpose**: 对照冻结 Spec 收敛遗漏并形成可审查证据。

- [X] T081 按 `specs/033-log-whitebox/v2/spec.md` 的 FR-001 至 FR-028 和 SC-001 至 SC-012 逐条核对代码与测试，在 `specs/033-log-whitebox/v2/tasks.md` 追加任何真实遗漏后再实现
- [X] T082 运行 `uv run python -m unittest tests.test_whitebox_rules tests.webui_store.test_store_whitebox tests.test_whitebox_integration`，把完整输出保存在系统临时目录
- [X] T083 运行 source、pipeline、screen、guard、workbench、tuning、task-state 的全部直接受影响聚焦测试，命令与结果记录到实施报告
- [X] T084 运行后端全量 `uv run python -m unittest discover -s tests`，失败时先归纳共同根因，再只返修受影响范围
- [X] T085 在 `webui/` 运行 `npm test` 和 `npm run build`，记录测试数和构建结果
- [X] T086 运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check` 和 `git status --short`，确认无临时产物、凭据和意外文件
- [X] T087 按 `specs/033-log-whitebox/v2/quickstart.md` 复核 0 条无证据、明确 10 页空结果、20 组 1 组失败、中途停止、事件丢失、失败恢复、AI 兜底、提交失败、白箱写失败和调参缺测量场景
- [X] T088 仅在用户另行授权真实输入与写入边界后，经项目公开入口执行新的最小真实端到端任务；不得使用活动任务或直接改数据库；未获授权则明确记录“真实端到端未执行”
- [X] T089 生成实施交接报告，逐项列出改动文件、测试等级、命令、数据类型、通过/失败数量、未测范围和真实端到端状态；不得提交、推送或发布

## Dependencies & Execution Order

### Phase dependencies

- Phase 1 无前置。
- Phase 2 依赖 Phase 1，阻塞全部用户故事。
- Phase 3（US1）依赖 Phase 2，是最小可交付阶段。
- Phase 4（US2）依赖 Phase 3 的主抓取接入。
- Phase 5（US3）依赖 Phase 2；涉及主抓取恢复的任务依赖 Phase 3-4。
- Phase 6（US4）依赖 Phase 2 与统一提交/收口能力。
- Phase 7（US5）依赖 US1-US4 已产生稳定报告。
- Phase 8 依赖新模块和公开字段已确定。
- Phase 9 依赖所有计划实施阶段。

### User story dependencies

- **US1**：独立阻止虚假成功，是 MVP。
- **US2**：在 US1 上补齐证明成功所需的分页与数量事实。
- **US3**：复用基础模块，可与 US2 的纯 AI/浏览器部分并行，但恢复主抓取必须等 US2。
- **US4**：可在基础模块完成后独立接入旧流程，但最终一致性依赖 US1 规则。
- **US5**：只消费前四个故事的统一报告，最后实施。

### Parallel opportunities

- T004-T006 可并行写基础红测。
- T015-T017 可并行写 US1 红测。
- T023-T026 可并行写 US2 红测。
- T027-T030 中 BOSS 与智联脚本可分给不同执行者；适配器任务分别跟随各自脚本。
- T034-T038 可并行写 US3 红测。
- T040、T041、T043 可在不同文件并行，T042 等待 AI 分段事实稳定。
- T053-T056 可并行写 US4 红测。
- T068-T070 可并行写前端红测。
- T076 与 T077 可并行；T078 等新模块最终落位后执行。

## Parallel Example

完成 Phase 2 后，可分为三条不会写同一文件的支线：

```text
执行者 A：T023 → T024 → T027 → T028（BOSS 页面证据）
执行者 B：T025 → T029 → T030（智联页面证据）
执行者 C：T034 → T039 → T040（AI 粗筛降级）
```

每条支线都不得修改其他支线的目标文件；合并后由一个执行者完成 `pipeline_task.py` 和最终归并接线。

## Implementation Strategy

### MVP first

1. 完成 Phase 1-2。
2. 完成 US1。
3. 停止并验证所有“不能成功”场景。
4. US1 未通过前不得用更多日志或前端页面掩盖结论错误。

### Incremental delivery

1. US1：封住虚假成功。
2. US2：补齐抓取证据。
3. US3：补齐恢复和降级。
4. US4：关闭旧旁路。
5. US5：统一展示。
6. Converge 后执行一次干净全量验证。

## Notes

- 任务列表不授权提交、推送、发布、删除或正式数据写入。
- 禁止回退或覆盖其他人的未提交改动。
- 任何阶段发现必须修改 Forbidden 文件时立即停止，回到 Plan 更新边界并取得用户授权。
- 所有测试日志、输出和临时产物必须写系统临时目录并在当轮清理。
- 现有低级测试通过不能冒充真实端到端。
- 审查时以 V2 Spec、实际差异和当次测试结果为准，不以实施者自述为完成证据。

## Phase 10: 本轮审查返修与验证记录

- [X] T090 [US1/US3] 修复已有 `whitebox_incomplete` 仍可成功的问题，覆盖主库失败、备用落点成功以及主库和备用落点同时失败；白箱聚焦套件 32 项通过
- [X] T091 [US5] 修复 `failed`、`cancelled`、`interrupted` 被完整性结论覆盖的问题；焦点套件 87 项、直接回归和全量回归通过
- [X] T092 [US2] 修复逐页 `new_unique_count` 只保留最大值的问题，并覆盖尚未产生 `scope_completed` 即中断；白箱集成测试通过
- [X] T093 [US3] 修复 Mock/代理 Store 的 `db_path` 类型误用并恢复账号轮询回归；焦点套件 87 项通过
- [X] T094 [US3] 修复 AI 持久化、检查点、提交失败的计划单元绑定和失败终态；直接回归 52 项及后端全量通过
- [X] T095 [US3/US4] 修复暂停/重启恢复时白箱尝试号和复抓检查点证据不连续的问题；暂停恢复聚焦测试 45 项及后端全量通过
- [X] T096 [US3/US4] 修复异常留痕卫生门禁中的空 `pass`，改为带类型信息的告警并复跑卫生测试
- [X] T097 [US5] 修复公开 Spec 中的本机绝对路径泄露，并用卫生测试和后端全量回归复核
- [X] T098 [cross] 完成本轮文档更新后的最终卫生测试、`git diff --check` 和工作区状态核对，并将结果写入实施报告

## Phase 11: 审查阻断返修（2026-09-05）

- [X] T099 [US1/US3] 在 `finalize()` 前导入并检查与当前白箱运行匹配的应急 JSONL 事实；新增“兜底文件存在但不得成功”的复现测试
- [X] T100 [US3] 收紧浏览器恢复事件语义，只有明确的白箱持久化修复事实才能解除 `whitebox_incomplete`；新增浏览器恢复误清除复现测试
- [X] T101 [US1] 将白箱汇总和 `task_finalized` 事件放进同一事务；新增最终事件写入失败时不得留下成功结论的复现测试
- [X] T102 [US3] 阻止显式 `lifecycle_end="succeeded"` 无恢复证据升级既有中断任务；新增中断后二次收口复现测试
- [X] T103 [US4] 手动保存部分结果时同步写入 `task_interrupted` 并形成终端白箱完整性结论；扩充任务结束 API 测试
- [X] T104 [US5] 将历史证据降级报告改为公开接口，并完成白箱证据适配、提交失败和手动结束逻辑的薄提取；受影响大文件均未净增长
- [X] T105 [US5/cross] 补齐 T068 的六类完整性结论前端解析测试；将缺少新增证据的 T053、T080 保持未勾选，不用改期待值掩盖缺口
- [X] T106 [cross] 完成增量验证：白箱/任务结束 101 项通过，生命周期/账号/暂停恢复 132 项通过，旧工作台 73 项通过，前端 T068 51 项通过
- [X] T107 [cross] 完成卫生测试、差异检查和工作区状态核对；记录未执行后端全量、前端全量/构建及真实 E2E 的边界

## Phase 12: Convergence

- [ ] T108 [cross] 按冻结 Plan 的文件边界复核 `webui/whitebox_evidence.py`、`webui/task_finish_whitebox.py` 和 `webui/exec_search_whitebox.py` 的薄提取归属；在不修改本轮 Plan 的前提下完成批准范围确认或另立拆分 Spec（Constitution IV/VI，unrequested）
- [ ] T109 [US4] 补齐 `tests/test_workbench.py` 与 `tests/test_workbench_api.py` 中旧工作台部分子查询失败、AI 排序降级和提交失败的新增证据（T053，missing）
- [ ] T110 [US5] 对 `specs/033-log-whitebox/v2/contracts/` 完成字段、状态值、错误码、分页和脱敏逐项核对，并留下可追溯记录（T080，missing）
