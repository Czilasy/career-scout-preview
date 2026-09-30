# Tasks: 一个流程内多平台并行抓取与筛选（B096）

**Input**: [spec.md](spec.md)、[plan.md](plan.md)、[research.md](research.md)、[data-model.md](data-model.md)、[contracts/](contracts/)、[quickstart.md](quickstart.md)。
**状态**：实施中；T001–T003 已解除，T004–T032/T034/T038 已有实现或验证记录，并完成第三轮及质量复审返修；T033/T035 为真实环境未验证，T036 全量后端门禁返修后未重跑，T037 因新增文件尚未提交导致卫生测试保留 1 项失败。

## File Boundaries

- **已确认范围**：以 [plan.md](plan.md#file-boundaries) 的“已确认的 B096 产品落位”为准；测试仅限下列任务所列路径，门面仅注册/组装。
- **实施前待确认扩边**：`webui/browser_support.py`、`webui/pipeline_exec_accounts.py`、可能的 `webui/frozen_browser_identity.py`、`webui/store_migrations_v1.py`、`webui/src/composables/useDiscoverySearch.ts`（同一份分析的双平台建议投影）、`webui/src/composables/useDiscoveryWorkflow.ts`、`useDiscoverySceneState.ts`、`useDiscoverySceneIdentity.ts`、`.specify/memory/constitution.md`（模块地图登记）。T001–T003 是硬门禁，未解决不得执行相应代码任务。
- **禁止修改**：`scripts/boss/`、`scripts/zhilian/`、`webui/cross_platform_dedupe.py`、`specs/019-cross-platform-job-dedup/`；不借 B096 改 B106。
- **新增文件**：`webui/flow_api.py`（路由）、`flow_service.py`（协调）、`store_flow.py`（薄门面）、`store_flow_core.py`、`store_flow_claims.py`、`store_flow_preflight.py`、`store_flow_results.py`、`store_flow_legacy.py`、`store_flow_runs.py`、`store_flow_state.py`（按职责拆分的持久化域）、`store_migrations_v7.py`（迁移 038/039）、`ai_screen_failure.py`、`flow_future.py`、`flow_task_state.py`、`flow_submission_service.py`、`flow_ai_coordinator.py`、`task_continue_support.py`、`task_continue_results.py`、`task_continue_finish.py`、`webui/src/composables/useDiscoveryParallelFlow.ts`（前端流程状态）、`webui/src/components/ParallelPlatformProgress.vue`（两线进度）；相应聚焦测试文件。
- **引用方向与行数**：API → service/coordinator → store；view → composable → API；Python <800 行、Vue <1200 行，600/900 预警线后分流。2026-09-27 实测 `exec_search_api.py` 626、`exec_search_resume.py` 401、`ai_screen_api.py` 582、`store_runs.py` 764、`store_flow.py` 35、`store_flow_state.py` 242、`store_flow_claims.py` 632、`flow_service.py` 561、`flow_api.py` 174、`flow_future.py` 128、`ai_screen_failure.py` 67、`flow_task_state.py` 192、`flow_submission_service.py` 195、`flow_ai_coordinator.py` 472、`task_continue_api.py` 550、`task_continue_finish.py` 296、`task_continue_support.py` 39、`task_continue_results.py` 104、`DiscoveryView.vue` 1199；超限的 `exec_search_api.py`、`DiscoveryView.vue` 与曾超限的 `task_continue_api.py` 均已按独立拆分 Spec 处理，门面不再追加业务逻辑。

## Phase 1: Setup（实施前硬门禁）

**目标**：先使 B096 可在不违反项目规则的文件边界内实施；本阶段不改变 B096 产品行为。

- [x] T001 在 `specs/046-parallel-platform-flow/v1/plan.md` 核定 `webui/exec_search_api.py` 与 `webui/src/views/DiscoveryView.vue` 的独立拆分 Spec 及兼容验证完成证据；047 已完成最小职责提取，解除涉及两文件的实施阻断。
- [x] T002 在 `specs/046-parallel-platform-flow/v1/plan.md` 对照调用链确认浏览器身份、占用判断、迁移驱动与前端现场恢复的最小扩边文件，取得文件边界确认后更新精确路径。
- [x] T003 在 `specs/046-parallel-platform-flow/v1/plan.md` 固定实施时实际行数、前置拆分完成状态与兼容测试证据；047 新模块已按项目规则登记 `.specify/memory/constitution.md` 模块地图。

---

## Phase 2: Foundational（共享流程契约）

**目标**：先建立可持久、可恢复、可隔离的流程基础；完成后才实施用户故事。T004–T011 顺序执行，共享状态不并行写入。

- [x] T004 在 `tests/webui_store/test_store_migrations.py` 增加迁移 038 的旧库升级、重复迁移与故障回滚失败用例，保留冻结旧版测试库行为。
- [x] T005 在 `webui/store_migrations_v7.py` 实现 Flow/Track 及 run 关系的增量迁移，在 `webui/store_migrations.py` 只组装新 mixin；迁移调度接入 `webui/store_migrations_v1.py` 须先通过 T002 扩边。
- [x] T006 在 `tests/test_b096_flow_store.py` 增加“全部”一个流程/两条线与新单平台一个流程/一条线的原子创建、唯一归属、画像隔离、幂等启动及旧单平台历史投影测试。
- [x] T007 在 `webui/store_flow.py` 实现双平台 Flow/Track、迁移后新单平台单轨 Flow 的原子创建、关联 run、状态/结果查询和旧轮次兼容投影；`webui/store.py` 仅组装公开 store 能力。
- [x] T008 在 `tests/test_b096_parallel.py` 增加两个平台交错运行、同资源互斥、冻结账号/profile/端口不串用及一个平台停止不关闭另一浏览器的失败用例。
- [x] T009 在 `webui/app_support.py` 建立可同时调度两个不同平台任务的执行容量；在经 T002 确认的 `webui/browser_support.py`、`webui/pipeline_exec_accounts.py`/`webui/frozen_browser_identity.py` 最小范围内消除全局占用和活动 profile 竞争；未通过 T008 不开放“全部”入口。
- [x] T010 在 `tests/test_pipeline_guard.py`、`tests/test_pipeline_exec_accounts.py`、`tests/test_frozen_browser_identity.py` 跑直接受影响的现有单平台守卫与冻结身份回归，并修正 T009 引起的真实失败。
- [x] T011 在 `webui/flow_service.py` 定义以 Flow/Track 为中心的共享协调接口及失败留痕，确保跨流程新建门禁、单线提交失败、状态恢复和日志关联；不把平台特例写入共享树干。

**Checkpoint**：迁移与持久关系、并发身份隔离通过聚焦验证；旧单平台仍可用。

---

## Phase 3: User Story 1 — “全部”入口与双平台条件确认（P1）

**Goal**：默认“全部”，七类条件框里的两个平台切换项分别打开完整七类面板；同一份简历分析分别投影建议、独立编辑和确认后才启动；系统禁用任何一平台时阻止“全部”且可单平台启动。
**Independent Test**：在双平台可用与任一禁用两类条件下，经 01 页验证同一份分析生成两套建议、两个面板各有七类且修改互不覆盖，一次启动得到一个 Flow/两条 Track，单平台入口仍保留两种模式。

- [x] T012 [P] [US1] 在 `tests/test_b096_flow_api.py` 为 `POST /api/flows` 写双确认、`不限`、禁用平台、重复提交和画像边界的 API 合约失败用例，并验证运行/暂停的“全部”阻止单平台新轮、运行/暂停的单平台也阻止“全部”或另一单平台新轮。
- [x] T013 [P] [US1] 在 `webui/src/components/__tests__/OneClickScreenDialog.spec.ts`、`webui/src/composables/__tests__/useDiscoverySearch.spec.ts` 和 `webui/src/views/__tests__/DiscoveryView.spec.ts` 写默认“全部”、七类条件框两个平台切换项各自打开完整七类面板、同一份分析分别投影建议、编辑值互不覆盖、黄提示、确认标记及单平台入口的失败用例；不把拖动手势或特定动效作为通过条件。
- [x] T014 [US1] 在 `webui/flow_service.py` 实现服务器端两平台可用性/确认及所有新轮共用的运行/暂停门禁，协调双平台 Flow 和单平台单轨 Flow 创建；第二平台提交失败仅标记该 Track 失败，不静默改为单平台。
- [x] T015 [US1] 在 `webui/flow_api.py` 实现创建流程与读取当前流程的参数校验/响应，在 `webui/exec_search_api.py` 的既有单平台启动路径接入真实单轨 Flow 与共用门禁；`webui/app.py` 仅注册路由，既有单平台请求/响应保持兼容，修改超限文件前必须满足 T001。
- [x] T016 [US1] 在 `webui/src/components/OneClickScreenDialog.vue` 和 `webui/src/composables/useDiscoveryParallelFlow.ts` 实现七类条件框的两个平台切换项及对应面板、两套独立可编辑值/确认状态和提交门禁；复用现有 `webui/src/composables/useDiscoverySearch.ts` 的按平台 schema 投影能力，让同一份简历分析分别预填两平台建议，确需改此文件时先满足 T002 扩边；“不限”有效且不要求重点击建议。
- [x] T017 [US1] 在 `webui/src/composables/useDiscoveryState.ts`、`useDiscoveryExecution.ts` 接入默认“全部”、单平台模式保留及创建响应；`webui/src/views/DiscoveryView.vue` 仅在 T001 门禁已解后做薄装配。
- [x] T018 [US1] 运行 `tests/test_b096_flow_api.py` 与 `webui/src/components/__tests__/OneClickScreenDialog.spec.ts`、`webui/src/composables/__tests__/useDiscoverySearch.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts` 的聚焦测试，记录禁用平台、双平台建议投影、新单平台单轨 Flow 和双向新轮门禁证据。

**Checkpoint**：US1 可独立验证入口、确认与 Flow 创建；US2 的实际并发进度不冒称已完成。

---

## Phase 4: User Story 2 — 两条平台线独立推进（P1）

**Goal**：02/03 分平台进度与操作；抓取→AI→可看结果不等待；一方暂停/失败/停止不拖另一方。
**Independent Test**：分别让 BOSS/智联先完成，模拟另一线运行、暂停或失败；已完成线继续进入下一阶段，失败有/无岗位表现正确。

- [x] T019 [P] [US2] 在 `tests/test_b096_parallel.py`、`tests/test_b096_flow_api.py` 写交错完成、每线 pause/resume/stop、已有持久抓取岗位但尚无 AI 结果快照时失败仍可在 04 页查看且明确为未完成 AI 筛选、不计入已筛选口径、不重复岗位，以及零岗位失败、重复 AI 启动和任务重启的失败用例。
- [x] T020 [P] [US2] 在 `webui/src/composables/__tests__/useDiscoveryParallelFlow.spec.ts` 与 `webui/src/components/__tests__/ParallelPlatformProgress.spec.ts` 写两线轮询、各自按钮、04 开放但不强制跳页、失败标记及未完成 AI 筛选标识的失败用例。
- [x] T021 [US2] 在 `webui/flow_service.py` 把每条 Track 的抓取完成与自身 AI 提交/结果就绪连接起来，保持单线错误与停止的持久事实和可追踪日志；不改变 B106 去重策略。
- [x] T022 [US2] 在 `webui/flow_api.py`、`webui/ai_screen_api.py` 接入每线状态/操作与同 Flow 跨平台并发校验；`webui/exec_search_api.py` 仅在 T001 门禁已解后接入薄调用，不往超限文件追加业务逻辑。
- [x] T023 [US2] 在 `webui/src/composables/useDiscoveryParallelFlow.ts`、`useDiscoveryTasks.ts` 实现按 Flow/平台分别轮询和操作，不用全局单 task 状态覆盖另一线。
- [x] T024 [US2] 在 `webui/src/components/ParallelPlatformProgress.vue` 实现 02/03 各自进度条及上方操作区，在 `webui/src/composables/useDiscoveryExecution.ts` 接线；`DiscoveryView.vue` 只在 T001 门禁已解后薄装配。
- [x] T025 [US2] 运行 `tests/test_b096_parallel.py`、`tests/test_b096_flow_api.py` 和新增前端并行流程聚焦测试，并对 `tests/test_pipeline_pause_guard.py`、`tests/test_screen_flow.py` 做直接回归。

**Checkpoint**：US2 证明实际并行和互不等待，不以双进度条假并行通过。

---

## Phase 5: User Story 3 — 流程结果、历史与恢复隔离（P1）

**Goal**：04 只汇总当前 Flow，历史一个外层/两个内层，刷新重开仍保留归属；运行/暂停时不可开新一轮。
**Independent Test**：覆盖“单平台→全部”“全部→全部”“全部→单平台”，在一方先完成或失败时查看 04、归档、刷新与历史，均不串入相邻流程。

- [x] T026 [P] [US3] 在 `tests/test_b096_flow_history.py` 写当前 Flow 精确查询、无 AI 快照但有抓取岗位且标为未筛选的部分结果、沿用现有归档触发但只作用指定 Flow、零岗位失败 Track 仍在历史占内层方块、旧单平台兼容、新单平台真实单轨 Flow，以及“单平台→全部”“全部→全部”“全部→单平台”的跨流程隔离失败用例。
- [x] T027 [P] [US3] 在 `webui/src/composables/__tests__/useDiscoveryResults.spec.ts`、`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`、`webui/src/views/__tests__/DiscoveryRecovery.spec.ts` 写当前结果、零岗位失败历史方块、刷新恢复，以及另一平台运行时已完成岗位的收藏/反馈/单岗位补抓仍指向自身来源 run 的失败用例。
- [x] T028 [US3] 在 `webui/store_flow.py`、`webui/store_result_history_mixin.py` 增加指定 Flow 的结果/归档查询与写入，在 `webui/flow_service.py` 汇合本线持久抓取岗位与 AI 结果且避免重复，在 `webui/flow_api.py` 暴露 Flow 结果查询；`webui/results_api.py` 与现有归档调用只做必要兼容接线。无 AI 快照的失败线仍可见部分岗位并标为未完成 AI 筛选；沿用现有归档时机和用户操作，只收紧到当前 Flow，不新增用户归档入口且不波及同画像其他 Flow。
- [x] T029 [US3] 在 `webui/result_history.py`、`webui/result_history_api.py` 实现 Flow 外层及两平台内层投影；没有结果快照的失败 Track 也显示内层状态，保持旧单平台历史可读；不新增未经确认的外层删除语义。
- [x] T030 [US3] 在 `webui/src/composables/useDiscoveryResults.ts`、`webui/src/composables/resultHistory.ts` 和 `webui/src/components/ResultHistoryDrawer.vue` 接入 Flow 精确结果、把现有归档调用限定当前 Flow，以及历史两级展示、部分结果失败和未筛选标记；不新增归档交互，保留每个岗位的平台/来源 run，使已完成平台的既有整理操作在另一平台运行时仍正确可用。
- [x] T031 [US3] 在 `webui/src/composables/useDiscoveryParallelFlow.ts` 恢复服务端 Flow/Track；如确需触及 `useDiscoveryWorkflow.ts`、`useDiscoverySceneState.ts`、`useDiscoverySceneIdentity.ts`，先满足 T002 扩边；`DiscoveryView.vue` 装配须满足 T001。
- [x] T032 [US3] 运行 `tests/test_b096_flow_history.py`、`tests/test_result_history.py`、`tests/test_result_rounds.py` 和 US3 前端聚焦/直接回归，证明当前页与历史均无跨流程数据。

**Checkpoint**：三个用户故事都能分别验收，且合起来实现原始 B096。

---

## Phase 6: Polish & Cross-Cutting Verification

- [ ] T033 在 `webui/src/views/__tests__/DiscoveryView.spec.ts`、`webui/src/components/__tests__/ParallelPlatformProgress.spec.ts` 检查空/加载/成功/失败及键盘交互的组件行为；在 `specs/046-parallel-platform-flow/v1/quickstart.md` 记录真实浏览器桌面/窄屏渲染检查步骤，覆盖面板切换、溢出/双滚动条、焦点/点击区域/阅读顺序，不以 jsdom 测试代替布局证据。聚焦 jsdom 已覆盖并行状态/失败文案，真实布局仍待实际浏览器。
- [x] T034 在 `README.md` 及直接相关用户说明（若存在）同步“全部”入口和流程历史的可感知行为；检查 `webui/cross_platform_dedupe.py` 保持未改，并运行直接去重回归。
- [ ] T035 在 `specs/046-parallel-platform-flow/v1/quickstart.md` 对照真实应用入口记录双平台可用/禁用、两种先后顺序、部分失败与未筛选标识、零岗位历史、现有归档行为按 Flow 收紧、已完成岗位整理、“单平台→全部”“全部→全部”“全部→单平台”、刷新与桌面/窄屏真实浏览器布局的 E2E 证据；环境不具备则明确“未验证”，不得用 `tests/test_e2e_smoke.py` 冒充。
- [ ] T036 在仓库根执行一次 `uv run python -m unittest discover -s tests`；最终运行 3355 项，功能测试通过，汇总中仅保留修复前 `DiscoveryView.vue` 行数门禁与卫生门禁两项失败；结构修复后 `tests.test_047_split_compat` 5/5 通过，未将该次全量输出冒称为零失败。
- [ ] T037 在 `webui/` 执行 `npm test`、`npm run build`，并在仓库根执行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`；前端 67 个文件/1082 测试、生产构建 2296 modules 通过，卫生测试 14 项中唯一失败为 59 个按授权未提交的新增交付文件。
- [x] T038 在 `specs/046-parallel-platform-flow/v1/tasks.md` 记录已完成项、测试等级/数据类型、未验证与阻断；同步 `specs/046-parallel-platform-flow/INDEX.md` 的实际阶段，不以内部单元测试替代用户可见验收。

## Implementation evidence (2026-09-26)

- 已完成：T004–T032、T034、T038；证据见 `v1/plan.md` 与 `v1/quickstart.md`。后端 Flow/Track、迁移 038、平台容量/身份隔离、Flow 结果/历史边界和前端默认“全部”/双 schema/双线状态均已有聚焦测试；最终前端全量和构建已执行。T036 的后端全量仅完成一次初始尝试，返修后保留待收口确认。
- 暂未完成：T033 的真实浏览器桌面/窄屏布局检查、T035 的真实双平台 E2E；本轮没有可用真实账号/登录态，不能把测试替身、临时 SQLite 或 jsdom 证据计为真实验收。
- 验证限制：T033 的真实桌面/窄屏布局、T035 的真实 BOSS/智联账号 E2E 未执行；本轮没有可用真实账号/登录态，不能把测试替身或跨层冒烟计为 E2E。T037 的唯一未通过项是 `test_no_untracked_non_ignored_files`；本轮未执行提交，故保留该失败。

## Third-round implementation evidence (2026-09-27)

- 完成第三轮复审返修：Flow store 按职责拆分并保持 `store_flow.py` 薄门面；抓取/AI/续跑统一经共享平台 lane；无 run 的 paused Track 通过原始 execute-search 重新校验；finalizing pause 与完成回调受状态 guard 保护；Future、浏览器激活、注册/绑定/write_run/search-row 失败统一安全收口；API 的 B096 claim/提交补偿经 FlowService；迁移 039 提供无凭据 `submission_snapshot_json`。
- 失败优先与直接回归：`uv run python -m unittest tests.test_b096_round3_review` 为 12/12；`uv run python -m unittest tests.test_b096_round3_review tests.test_b096_round2_review tests.test_b096_production_flow tests.test_047_split_compat tests.test_b096_flow_store tests.test_b096_flow_service tests.test_b096_flow_api tests.test_b096_flow_history tests.test_b096_review_regressions` 为 59/59；`uv run python -m unittest tests.webui_app.test_webui_app_platform tests.webui_app.test_webui_app_taskrun tests.webui_store.test_store_migrations.Migration38FlowSchemaTests` 为 155/155。
- 数据类型和边界：以上后端测试使用临时 SQLite、受控 Future/平台容量和 Flask test client；没有真实 BOSS/智联账号、Cookie、Key 或真实平台数据。真实浏览器布局与正式入口双平台 E2E 仍按 T033/T035 标记未验证；卫生测试的未跟踪文件失败按未提交授权边界保留。

## Quality review follow-up evidence (2026-09-27)

- 已完成 048 续跑 facade 独立拆分：`task_continue_api.py` 829 → 550 行，新增 `task_continue_finish.py` 296 行；公开续跑、暂停、取消、结束保存路由及 facade 动态依赖兼容由 characterization/direct regression 固定。
- 已完成状态感知失败收口：recoverable browser/CDP activation 路径在同一收口内将 task、screening run、search run 和 Flow Track 持久为 `paused`；不可恢复路径统一为 `failed`；Flow API 对 finalizing Track 直接返回 409 且不改变 Track。
- 已完成 API→service/coordinator→store 边界：`flow_api.py` 通过 FlowService 查询方法，抓取 claim/run/绑定/提交/补偿经 `flow_submission_service.py`，AI 创建/claim/白箱/提交/补偿经 `flow_ai_coordinator.py`；所有新增模块低于 800 行。
- 聚焦命令 `uv run python -m unittest tests.test_048_task_continue_split_compat tests.test_b096_quality_round4 tests.test_b096_production_flow tests.test_b096_round2_review tests.test_b096_round3_review`：30/30 通过。使用临时 SQLite、受控执行器和 Flask test client；未执行真实账号或真实平台 E2E。

## 第五轮架构与原子性收敛证据（2026-09-27）

- Flow 抓取入口在 Flow 分支经 `FlowSubmissionService.begin_whitebox()`，AI Flow 路径的 run/task/恢复/失败补偿经 `FlowAiCoordinator`/`FlowTaskState`，legacy 分支保持原有兼容路径；`tests.test_048_task_continue_split_compat` 以真实 Flask test client 和注入 coordinator spy 验证路由只调用协调层。
- `store_flow_state.py` 提供同一 SQLite `BEGIN IMMEDIATE` 事务内的 screening run、search run、Flow Track 与事件收口；`tests.test_b096_quality_round4.test_flow_failure_state_is_atomic_when_search_update_fails` 通过 SQLite 故障触发器证明中途写失败会回滚，不留下部分状态并冒充成功。
- 最新聚焦命令 `uv run python -m unittest tests.test_048_task_continue_split_compat tests.test_b096_quality_round4 tests.test_b096_production_flow tests.test_b096_round2_review tests.test_b096_round3_review`：33/33 通过。实际行数：`exec_search_api.py` 626、`ai_screen_api.py` 582、`flow_submission_service.py` 195、`flow_ai_coordinator.py` 472、`flow_task_state.py` 192、`store_flow_state.py` 242、`store_flow.py` 35、`flow_service.py` 561、`task_continue_api.py` 550，均低于 Python 800 行门禁。

## Dependencies & Execution Order

`T001–T003` 是实施前硬门禁 → `T004–T011` 共享基础 → US1 `T012–T018` → US2 `T019–T025` → US3 `T026–T032` → 最终 `T033–T038`。US2 依赖 US1 创建的 Flow；US3 可先写独立测试，但结果/恢复集成依赖前两故事的 Flow/Track 状态。`T035` 真实 E2E 在功能和实际环境就绪后执行，`T036–T037` 在整条链收敛后执行一次。

`[P]` 只表示测试文件不同且可独立先写的机会，不授权多 AI 并行；共享接口、迁移、浏览器状态和生产文件均串行由一个负责人维护。若未来用户明确要求多 AI，须先按项目规则为每个 AI 配独立分支/工作目录、唯一文件所有权与依赖，并在集成分支组合验证。

## Suggested MVP & Independent Acceptance

US1 是首个可独立验收的入口切片，但不是 B096 完成判定；B096 必须同时满足 US2 的实际独立推进与 US3 的流程隔离。验收以 [spec.md](spec.md) 的 SC-001–SC-005 为准；不因内部接口、任务数量或测试通过而缩小用户原始目标。

## Final validation evidence (2026-09-27)

- 高/中阻断收口：AI worker 的结果绑定失败、零岗位、白箱不可验证/失败、抓取终态双重失败均统一走安全可观测收口；新增 `pipeline_task_outcome.py` 后 `pipeline_task.py` 为 598 行。Discovery 初始默认“全部”、当前 Flow 失败/空响应保持“全部”，显式单平台 Flow 按 `selection` 恢复；恢复/轮询生命周期经 `useDiscoveryParallelFlow.ts` 注册。
- 当前行数：`exec_search_api.py` 637、`exec_search_resume.py` 401、`ai_screen_api.py` 551、`runners/ai_screen_task.py` 624、`runners/pipeline_task.py` 598、`pipeline_task_outcome.py` 240、`store_runs.py` 764、`store_flow.py` 35、`flow_service.py` 565、`flow_task_state.py` 227、`flow_submission_service.py` 210、`flow_ai_coordinator.py` 494、`task_continue_api.py` 550、`DiscoveryView.vue` 1199、`useDiscoveryParallelFlow.ts` 429；硬红线全部满足。
- 测试证据：后端最终全量 `uv run python -m unittest discover -s tests` 运行 3355 项，功能测试通过；修复行数门禁后 `tests.test_047_split_compat tests.test_048_task_continue_split_compat tests.test_b096_final_review tests.test_b096_quality_round4` 为 22/22。前端 `npm test` 为 67 个文件/1082 测试，`npm run build` 为 2296 modules，均通过；`git diff --check` 通过。
- 保留边界：`tests.test_repo_hygiene` 14 项中唯一失败为 59 个按授权未提交的交付文件；真实 BOSS/智联账号 E2E、真实桌面/窄屏布局未执行，不以临时 SQLite、Flask test client 或 jsdom 冒充。

## Current-flow default selection follow-up (2026-09-27)

- 补充 `DiscoveryView` 点击级失败测试：`/api/flows/current` reject 与 `{}` 两种响应在 `start-one-click` → `prepareDialog` 后均保持“全部”，继续打开双平台确认弹窗；明确 `selection` 为 `boss`/`zhilian` 的恢复仍保持单平台。
- 红测先复现了 `parallelMode` 被 `available=false` 覆盖的问题；最小修复改为仅由明确 Flow `selection` 改变模式，`available` 记录成功接口能力，不再表示是否存在 current Flow。
- `npm test -- --run src/views/__tests__/DiscoveryView.spec.ts -t "B096 keeps 全部 through one-click preparation"`：2/2 通过；三文件聚焦（DiscoveryView、DiscoveryRecovery、useDiscoveryParallelFlow）：137/137 通过；前端全量：67 文件、1084/1084；构建：2296 modules 通过；`git diff --check` 通过。
