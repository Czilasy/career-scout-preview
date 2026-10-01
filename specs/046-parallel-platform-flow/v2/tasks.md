# Tasks: 多平台主流程分轨合流与统一条件映射（B096 V2）

**Input**: `specs/046-parallel-platform-flow/v2/` 下的冻结 Spec、Plan、Research、Data Model、Quickstart 与 Contracts

**Tests**: V2 明确要求测试先行、受控顺序矩阵、最终全量、真实源码 E2E 与真实 EXE 最小验证。

**Organization**: 按四个 P1 用户故事组织。单个故事可以独立验证，但本功能只有四个故事全部通过才达到“主流程完成”的最高目标。

## File Boundaries

- **Allowed files**: 只允许修改 `plan.md` 中列出的产品、测试和文档路径。
- **Forbidden files**: `design/**`、`v1/**`、`useDiscoveryState.ts`、`app.py`、`store.py`、`exec_search_api.py`、Flow 后台协调/存储/runner、平台抓取树枝、B106、发布与版本文件。
- **New files**: `parallelFilterMapping.ts`、`useDiscoveryFlowPresentation.ts`、`store_migrations_v8.py` 及对应两个前端测试文件。
- **Reference direction**: view → composable/pure mapping → API；API → service → store；并行进度壳 → 原 `TaskProgress.vue`。
- **Line gate**: `DiscoveryView.vue` 最终低于 1200 行且净减少；新 Python 低于600行预警线，新 Vue 低于900行预警线。
- **Baseline gate**: `pre-v2-protected.sha256` 已冻结 76 个 V2 范围外的既有工作区文件；清单内文件哈希不得变化，清单外新增差异只能出现在允许路径。`design/**` 不读取、不哈希、不修改。

## Verification Gate

- 每个故事先让聚焦测试失败，再实施，再只跑本故事和直接相邻回归。
- 整条链收敛后只运行一次干净后端全量、前端全量、构建和卫生检查。
- 自动化不能替代真实源码/EXE 主流程；真实门禁缺失时不得宣布完成。
- 不提交、不推送、不发布；这些动作不在本 Tasks 范围。

## Implementation Record

- 2026-09-28：根据真实 E2E 发现并经用户明确授权，补修智联回调契约、CDP 关闭端点容错、详情无效输出诊断、流水线失败证据和结果通知竞态；本地回归与结构门禁纳入验证，真实源码复验仍待执行。
- 2026-09-28：根据后续真实 E2E 取证并经用户明确授权，新增 `webui/src/composables/discoveryDeps.ts` 与 `webui/src/composables/useDiscoveryExecution.ts` 精确 follow-up 路径，注入当前全部流程的抓取任务归属判断，阻止 Flow-owned 任务被 legacy 自动筛选重复启动；本地回归待随本轮验证，真实源码复验仍待执行。

- 2026-10-01（V2 重新执行轮）：规格新增 `## 状态词表`、`## 状态所有权`、`## 冷启动走查（硬验收）` 三节作为验收尺子（提交 `4fe49ae`）。基线证据：前端全量 69 文件 / 1394 例全绿（`%TEMP%\cs046_v2\fe-baseline.log`）；后端聚焦组合 189 例中唯一失败为 `test_baseline_files_keep_pre_v2_hashes`（36/76 基线哈希不符，属结构门禁：基线取自未提交工作树且与 `e4ca9e0` 自相矛盾，在 HEAD 不可能绿）。
- 2026-10-01 D-01（提交 `910589d`）：真实库冻结快照实测统一经验值为「1年以下」（来自简历建议，符合 FR-004），BOSS 四档同选符合冻结契约，其中「经验不限」是岗位属性档而非字段级哨兵。实测真因两处已修：智联把岗位档 `-1` 误登记为字段级不限制（`ai_platform_adapter.py`）、智联薪资「5K-10K」少映射一档 `4K-6K`（`parallelFilterMapping.ts`）；反向投影改为唯一命中。`tests.ai.test_ai_platform_filters` 17/17、`parallelFilterMapping.spec.ts` 17/17、`vue-tsc` 0 错。
- 2026-10-01 D-02（提交 `39091ed`）：`store_runs.py` 批次与阶段末两条写入路径收敛为共用 upsert 与唯一取值域，整包 JSON 不再进 `verdict` 列；全仓 `json.dumps(verdict` 归零，文件 789 行仍在红线内。`tests.healthy_pipeline.test_pipeline_pause_resume` 69/69 通过（其中 2 条旧断言按新入库形状由整包 JSON 改为枚举比对，属规格驱动的返修）。历史 10851 行整包 JSON 未迁移，仍由读方容错分支与 `historical_recovery` 兜住。

## Phase 1: Setup（结构护栏）

**Purpose**: 在改实现前先把本轮最容易复发的结构错误变成失败测试。

- [x] T001 在 `tests/test_b096_final_review.py` 增加 V2 边界与结构红测：读取 `specs/046-parallel-platform-flow/v2/pre-v2-protected.sha256` 校验 76 个既有范围外文件哈希未变，拒绝允许路径和冻结清单之外的新差异；同时禁止 `ParallelPlatformProgress.vue` 固定 35/65/100、禁止双列 grid、要求复用 `TaskProgress.vue`、禁止 `platformConfirmed`/确认复选框，并校验 `webui/src/views/DiscoveryView.vue` 不超过 1200 行

---

## Phase 2: Foundational（共享契约与持久化基础）

**Purpose**: 建立四个故事共用的类型、映射与配置快照存储；完成前不得接 UI 主流程。

- [x] T002 在 `webui/src/types.ts` 增加 `UnifiedFilterValues`、`PlatformFilterValues`、`ConditionSnapshotV2` 和常用配置 `conditions` 类型，保留 version 1 响应兼容
- [x] T003 [P] 在 `tests/webui_store/test_store_migrations.py` 先写迁移 040 红测：新库/旧库都有 `condition_snapshot_json`、默认 `{}`、重复迁移幂等
- [x] T004 在 `webui/store_migrations_v8.py` 实现迁移 040，并只在 `webui/store_migrations.py` 增加 import/MRO 组装使 T003 通过
- [x] T005 [P] 在 `webui/src/__tests__/parallelFilterMapping.spec.ts` 先写纯函数红测：冻结表全选项覆盖、标签精确解析、去重/不限互斥、字段级覆盖、平台微调不回流、专属字段保留、简历语义初值与 schema 缺项阻断
- [x] T006 在 `webui/src/parallelFilterMapping.ts` 实现映射版本、六类统一 schema、人工映射、简历语义投影、字段级平台覆盖、override 计算和 ConditionSnapshotV2 构造，使 T005 通过
- [x] T007 在 `.specify/memory/constitution.md` 模块地图登记 `parallelFilterMapping.ts`、`useDiscoveryFlowPresentation.ts` 与 `store_migrations_v8.py` 的职责和单向引用边界

**Checkpoint**: 统一条件能确定性生成两平台最终值，常用配置具备冻结它们的持久化位置。

---

## Phase 3: User Story 1 - 从“全部”直接确定两平台条件（Priority: P1）

**Goal**: “全部”默认显示六类统一条件，立即映射到两平台；无平台确认复选框；一次主按钮启动；中性主题；常用配置按原值恢复。

**Independent Test**: 修改统一字段 → 检查两平台映射 → 平台微调不回流 → 再改统一字段只覆盖同字段 → 保存/恢复配置 → 一次点击启动，全程没有额外确认。

### Tests for User Story 1

- [x] T008 [P] [US1] 在 `webui/src/components/__tests__/OneClickScreenDialog.spec.ts` 写红测：标签顺序为全部/BOSS/智联、全部仅六类、专属字段只在平台页、切页立即看到映射、无“我已确认”控件、主按钮始终按当前值提交
- [x] T009 [P] [US1] 在 `webui/src/composables/__tests__/useDiscoveryParallelFlow.spec.ts` 写红测：没有确认状态/缺失确认异常，请求不发送 `confirmed`，Flow 与 execute-search 分别保存完整快照和当前平台最终字段，恢复兼容 V1 纯字段快照
- [x] T010 [P] [US1] 在 `webui/src/composables/__tests__/useTheme.spec.ts` 写红测：`all` 在 light/dark 使用中性令牌、旧平台写入不能越过 all 作用域、清除作用域后恢复最近品牌
- [x] T011 [P] [US1] 在 `tests/test_b096_flow_api.py` 写红测：`POST /api/flows` 不要求 `confirmed` 且不再返回 `confirmations_required`，仍校验 profile/selection/platform availability
- [x] T012 [P] [US1] 在 `tests/test_search_packages.py` 写红测：version 2 完整校验/返回 conditions、version 1 无条件快照仍可读、损坏快照整包拒绝、不污染画像事实
- [x] T013 [P] [US1] 在 `webui/src/composables/__tests__/useSearchPackages.spec.ts` 与 `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts` 写红测：保存/恢复统一值、两平台最终值、overrides、专属字段和 mappingVersion，恢复不重算映射且失败不部分回填

### Implementation for User Story 1

- [x] T014 [US1] 在 `webui/src/components/OneClickScreenDialog.vue` 用统一/平台三页草稿替换双平台确认状态，接入 `parallelFilterMapping.ts` 的字段级即时映射并保持单平台模式原行为
- [x] T015 [US1] 在 `webui/src/composables/useDiscoveryParallelFlow.ts` 删除 `platformConfirmed` 及确认门禁，持有统一值和平台最终值，提交 ConditionSnapshotV2，并在 execute-search 仅发送当前平台最终字段
- [x] T016 [US1] 在 `webui/flow_api.py` 删除 `confirmed` 参数解析和逐平台确认错误，保持既有 FlowService 调用、幂等和错误映射不变
- [x] T017 [US1] 在 `webui/src/composables/useTheme.ts` 增加中性 `all` 作用域优先级，在 `webui/src/styles/theme.css` 增加 light/dark 中性令牌且不改 BOSS/智联令牌
- [x] T018 [US1] 在 `webui/store_search_packages.py` 读写 `condition_snapshot_json`，在 `webui/search_packages.py` 增加 version 2 白名单校验/投影并保持 version 1 可读
- [x] T019 [US1] 在 `webui/src/composables/useSearchPackages.ts` 构造、保存、完整校验并一次性恢复条件快照，失败时回滚关键词、城市、画像和条件全部 refs
- [x] T020 [US1] 在 `webui/src/views/DiscoveryView.vue` 以薄接线把统一草稿、平台草稿、简历 semantic、常用配置和 all 主题连到现有入口，删除确认参数传递且确保文件净减少
- [x] T021 [US1] 运行 `uv run python -m unittest tests.test_b096_flow_api tests.test_search_packages tests.webui_store.test_store_migrations`，并在 `webui/` 运行 `npm test -- parallelFilterMapping.spec.ts OneClickScreenDialog.spec.ts useDiscoveryParallelFlow.spec.ts useTheme.spec.ts useSearchPackages.spec.ts DiscoverySearchPackages.spec.ts`，只修复 US1 失败

**Checkpoint**: US1 可独立演示“全部”六类条件、平台微调、配置恢复和一次启动。

---

## Phase 4: User Story 2 - 领先平台推动页面、落后平台继续追赶（Priority: P1）

**Goal**: 两条线不互等；02/03 每平台一行原进度；领先平台推动 03/04；用户返回后不抢页；独立操作不串线。

**Independent Test**: 分别驱动智联先、BOSS 先、近同时和一轨失败，检查阶段列表、稳定顺序、自动前进、手动停留和单轨操作。

### Tests for User Story 2

- [x] T022 [P] [US2] 在 `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts` 写红测：智联先/BOSS 先/近同时、02 保留完成抓取、03 仅到达平台、稳定追加、首次水合不跳页、运行跃迁自动前进、手动返回后不抢页
- [x] T023 [P] [US2] 在 `webui/src/components/__tests__/ParallelPlatformProgress.spec.ts` 写红测：每项渲染原 `TaskProgress`、纵向单列、传入真实 snapshot、无英文原始阶段/假百分比、暂停继续停止只发对应平台
- [x] T024 [P] [US2] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 写视图红测：02 两条抓取进度、03 按进入顺序追加、第一条 AI/结果分别推动步骤、第二条到达不重复跳页

### Implementation for User Story 2

- [x] T025 [US2] 在 `webui/src/composables/useDiscoveryFlowPresentation.ts` 实现按 run id 拉真实 task-state、02/03 阶段投影、进入时间排序、单调解锁水位、初始水合抑制和手动停留状态机
- [x] T026 [US2] 在 `webui/src/components/ParallelPlatformProgress.vue` 删除自制进度算法和卡片网格，改为纵向渲染 `TaskProgress.vue` 并保留对应平台操作按钮
- [x] T027 [US2] 在 `webui/src/composables/useDiscoveryIslandBridge.ts` 接受 Flow 投影的可达步骤集合，避免灵动岛仍用旧单平台 `enabledSteps` 把已解锁 03/04 拦住
- [x] T028 [US2] 在 `webui/src/views/DiscoveryView.vue` 用 Flow presentation 的 `scrapeItems`/`screenItems`、投影步骤与选择处理替换 V1 直接 track 展示，保持单平台路径不变且文件低于 1200 行
- [x] T029 [US2] 运行 `webui` 下 `npm test -- useDiscoveryFlowPresentation.spec.ts ParallelPlatformProgress.spec.ts useDiscoveryIslandBridge.spec.ts DiscoveryView.spec.ts`，并运行 `uv run python -m unittest tests.test_b096_final_review`，只修复 US2 及直接相邻失败

**Checkpoint**: US2 可在受控状态下完整证明“分轨合流”，但尚不宣称结果阅读现场和刷新历史全部完成。

---

## Phase 5: User Story 3 - 结果原地合流且不打断阅读（Priority: P1）

**Goal**: 第一平台结果立即可见；第二平台结果在同一 04 原地加入，保留阅读现场并只提示一次。

**Independent Test**: 04 设置平台筛选、分类、列表筛选、排序、滚动和展开岗位，再加入第二平台结果；所有现场保持且只出现一次对应灵动岛通知。

### Tests for User Story 3

- [x] T030 [P] [US3] 在 `webui/src/composables/__tests__/useDiscoveryResults.spec.ts` 写红测：同 Flow 新结果使用 preservePresentation，不切分类/平台筛选、不换 scene identity、不混入其他 Flow，失败平台部分岗位仍保留未筛选标记
- [x] T031 [P] [US3] 在 `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts` 补红测：新 result signature 触发一次刷新和一次 `{flow}:{platform}:{run}` 通知，初始双结果水合不通知，同签名重复轮询不通知
- [x] T032 [P] [US3] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 写集成红测：第二平台加入后筛选、排序、滚动、展开和选中岗位保持，灵动岛载荷指向 results

### Implementation for User Story 3

- [x] T033 [US3] 在 `webui/src/composables/useDiscoveryResults.ts` 为 Flow 同轮合流增加 `preservePresentation` 选项，复用现有 Flow results 请求并保持当前分类、平台筛选和 scene identity
- [x] T034 [US3] 在 `webui/src/composables/useDiscoveryFlowPresentation.ts` 实现结果签名基线、首次水合抑制、去重通知及注入式结果刷新，不直接操作结果 DOM
- [x] T035 [US3] 在 `webui/src/views/DiscoveryView.vue` 将结果刷新与 `island-notice` 现有 emit 接到 presentation，并移除当前 deep watch 的无差别结果重载
- [x] T036 [US3] 在 `webui/` 运行 `npm test -- useDiscoveryResults.spec.ts useDiscoveryFlowPresentation.spec.ts DiscoveryView.spec.ts`，只修复 US3 和结果工作台直接回归

**Checkpoint**: US3 可独立证明第二平台结果不打断用户阅读。

---

## Phase 6: User Story 4 - 刷新、返回和历史始终恢复同一流程（Priority: P1）

**Goal**: 02/03/04 刷新恢复当前有效页面和已解锁入口；两条状态、条件快照、历史与流程归属不串轮。

**Independent Test**: 在四种现场刷新：双抓取、抓取+AI、结果+运行、双完成；再浏览历史和开始下一轮，检查页面、入口、进度、条件和结果归属。

### Tests for User Story 4

- [x] T037 [P] [US4] 在 `webui/src/views/__tests__/DiscoveryRecovery.spec.ts` 写红测：02/03/04 各自刷新原页、已解锁页不回锁、刷新不自动前进、刷新后用户停留旧页仍不被慢平台抢走
- [x] T038 [P] [US4] 在 `tests/test_b096_flow_history.py` 写红测：历史 Flow 返回两条 Track 的 V2 ConditionSnapshot、旧 V1 纯字段快照兼容、后续映射版本变化不重算、不同 Flow 不混入
- [x] T039 [P] [US4] 在 `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts` 补红测：version 1 配置恢复为空白条件、version 2 恢复原始平台微调和专属字段且不触发映射覆盖
- [x] T040 [P] [US4] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 写红测：失败/暂停/完成组合保持兄弟运行线和已解锁入口，结束后新单平台/新全部流程不继承旧结果或通知签名

### Implementation for User Story 4

- [x] T041 [US4] 在 `webui/src/composables/useDiscoveryFlowPresentation.ts` 完成按 flow id 重置运行时记忆、刷新时解锁推导/有效页钳制、旧页停留恢复和失败部分结果可见判断
- [x] T042 [US4] 在 `webui/src/composables/useDiscoveryParallelFlow.ts` 完成 V1/V2 快照恢复分支与新 Flow 清理，确保恢复只采用冻结 `platformValues` 且不执行当前映射
- [x] T043 [US4] 在 `webui/src/views/DiscoveryView.vue` 将 workflow 恢复顺序接到 Flow 初始水合之后的无跳转校正，并保持历史模式仍由既有入口控制
- [x] T044 [US4] 运行 `uv run python -m unittest tests.test_b096_flow_history`，并在 `webui/` 运行 `npm test -- DiscoveryRecovery.spec.ts DiscoverySearchPackages.spec.ts DiscoveryView.spec.ts useDiscoveryFlowPresentation.spec.ts useDiscoveryParallelFlow.spec.ts`，只修复 US4 及直接相邻失败

**Checkpoint**: 四个用户故事均具备受控自动化证据，进入统一收口。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 文档同步、尺寸/零引用检查、整链自动化与真实应用门禁。

- [x] T045 [P] 在 `README.md` 更新“全部”六类映射、一次启动、分轨合流和原地结果加入说明；在 `CHANGELOG.md` 按用户可感知口径新增 3–6 条未发布记录 —— README 半边已落（提交 `4fe49ae`，四条：六类映射 / 一次启动 / 分轨合流 / 原地加入）。CHANGELOG 半边按用户 2026-09-30 定案「同一未发布版本内的返修不算用户可感知新增，发布时统一攒」不追加，且本轮不授权发布；V2 未收口（见 D-13），此时写"分轨合流已可用"会名实不符。
- [x] T046 在 `tests/test_b096_final_review.py` 完成最终结构审计：新模块存在、旧并行假进度零引用、确认状态零引用、`pre-v2-protected.sha256` 全部哈希一致、允许路径和冻结清单之外零新增差异、`DiscoveryView.vue` 行数门禁通过 —— 五项绿，一项**结构/授权阻断**：基线哈希那条（`test_baseline_files_keep_pre_v2_hashes`）在 HEAD 不可能绿，基线 76 项取自当时未提交的工作树却与实现同提交入库，对 `e4ca9e0~1` 实测 76 项全不符；按交接口径一字未动。边界闸基准本轮改严为「`54164e6..HEAD` 提交区间 ∪ 工作树」，检出 0。
- [x] T047 运行后端聚焦组合 `uv run python -m unittest tests.test_b096_flow_api tests.test_b096_flow_history tests.test_b096_final_review tests.test_search_packages tests.webui_store.test_store_migrations`，保存失败清单并只处理直接相关失败 —— 收口后实测 `Ran 196 tests … FAILED (failures=1)`，唯一失败是结构门禁 `test_baseline_files_keep_pre_v2_hashes`（36 项基线哈希，见 T046 的授权阻断），无其它失败。
- [x] T048 在 `webui/` 运行前端聚焦组合 `npm test -- parallelFilterMapping.spec.ts OneClickScreenDialog.spec.ts ParallelPlatformProgress.spec.ts useDiscoveryParallelFlow.spec.ts useDiscoveryFlowPresentation.spec.ts useDiscoveryIslandBridge.spec.ts useDiscoveryResults.spec.ts useSearchPackages.spec.ts useTheme.spec.ts DiscoveryRecovery.spec.ts DiscoverySearchPackages.spec.ts DiscoveryView.spec.ts` —— 以全量前端超集执行：69 文件 / 1511 例全绿（`%TEMP%\cs046_v2\acc_npmtest.log` 与收口后的复跑）。
- [x] T049 运行唯一一次干净后端全量 `uv run python -m unittest discover -s tests`；若失败，把失败清单写入 `specs/046-parallel-platform-flow/v2/tasks.md` 的实施记录，禁止无相关修改重跑全量 —— 收敛后跑一次（进程 19:29 起、19:55 结束）：**`Ran 3496 tests in 1555.660s` → `FAILED (failures=1)`**，失败清单只有一条：`FAIL: test_baseline_files_keep_pre_v2_hashes (test_b096_final_review.B096V2StructuralGuardTests)` = 结构/授权阻断（基线哈希，见 T046），无产品失败。日志 `%TEMP%\cs046_v2\be-full-final.log`。
- [x] T050 在 `webui/` 依次运行 `npm test` 与 `npm run build`，记录文件数、测试数、构建模块数和任何失败边界到 `specs/046-parallel-platform-flow/v2/tasks.md` —— 收口后前端全量 **69 文件 / 1518 例全绿**（D-10 与 D-13 各自的红测已计入），`npx vue-tsc --noEmit` 退出码 **0**，`npm run build` 退出码 **0**（产物 `dist/assets/index-D05zNV-p.js` 651.09 kB / gzip 208.04 kB），构建是最后一条前端命令。日志：`%TEMP%\cs046_v2\be-full-final.log`（后端全量）、`acc_npmtest.log`（前端）。
- [x] T051 运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status --short`，确认无临时输出、凭据、意外文件和越界修改 —— `tests.test_repo_hygiene` **14 例全绿**；`git diff --check` 退出码 0（只有 CRLF 归一化提示，无空白错）；`git status --short` 只剩本文件一条待提交改动，**无未跟踪文件、无临时产物、无凭据**。
- [x] T052 按 `specs/046-parallel-platform-flow/v2/quickstart.md` 经正式入口、正式 live 数据库和真实 BOSS/智联登录态执行完整源码 E2E，记录 Flow/run id、实际完成顺序、02→03→04、旧页停留、结果合流和刷新证据，不记录敏感值 —— 已跑通一次真实轮：Flow `43e7c6adfc054004`，`selection=all`，15:20:59 一键启动 → 两条 `POST /api/execute-search`；**智联 15:25:42 先收尾**（抓 378 / 精筛 11：1 保留 10 淘汰，`result_run_id=c50be9bb-4474-442e-9f9f-c38c8ce90483`），**BOSS 15:40:44 后收尾**（抓 671 / 精筛 65：14 保留 51 淘汰，`result_run_id=4b4d086b-0992-4c10-8070-041182865559`）；收尾后 04 合并 匹配 15 / 不匹配 61 / 待确认 0 / 已筛除 973，全程控制台 0 error 0 warning；进行中三次全新浏览器上下文均自动接回现场。**未完成部分**：进行中那 15 分钟 04 看不到已交付平台的结果、D-08 说明未渲染 → 登记为 D-13 返修（本文件 1.3 与最终报告）。
- [x] T053 按项目现有本地打包流程构建 EXE（不改 `packaging/**`、不发布、不上传），并依 `quickstart.md` 执行“全部”主流程最小充分真实验证；未能执行时明确记录环境阻断 —— **环境阻断，未执行且未用源码运行冒充**：`pyinstaller` 不在 `pyproject.toml`/`uv.lock`/`requirements.txt`，当前 venv `import PyInstaller` 直接 `ModuleNotFoundError`，`packaging/build_exe.ps1:60-62` 会以"打包依赖缺失"终止。SC-009 因此标未验证。
- [x] T054 在 `specs/046-parallel-platform-flow/INDEX.md` 与 `specs/046-parallel-platform-flow/v2/tasks.md` 汇总已完成、测试证据、真实 E2E/EXE 状态、未验证边界和延期项；只有 T049–T053 的必需证据齐备才把 V2 标为完成 —— 已汇总（本条 + INDEX.md 2026-10-01 条目 + 最终报告 `%TEMP%\career-scout-spec046-v2-final-report.md`）。**V2 不标"完成"**：T053（EXE 真实一致性）为环境阻断未执行、D-11/D-12/D-10b 与失真夹具仍开着，按"已完成实现但有明确未验证边界与延期项"如实记录。

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 无依赖，先建立结构红线。
- Phase 2 依赖 Phase 1，阻塞所有用户故事。
- US1 依赖 Phase 2，提供条件快照与一次启动。
- US2 依赖 Phase 2，并在接入 `DiscoveryView.vue` 时依赖 US1 的统一入口接线已稳定。
- US3 依赖 US2 的 Flow presentation 和页面 04 解锁。
- US4 依赖 US1 的快照与 US2/US3 的页面投影、结果签名。
- Phase 7 依赖四个故事全部完成。

### User Story Dependencies

```plain
Foundation
   - US1 条件与启动
   - US2 分轨页面 - US3 结果合流
          ________________
US1 条件快照 ____________________  - US4 刷新与历史
```

### Parallel Opportunities

- T003 与 T005 可并行：分别修改 Python 迁移测试与前端纯函数测试。
- 同一故事内标 `[P]` 的红测文件互不重叠，可并行编写。
- T045 文档可在最终结构审计前并行完成。
- 逻辑上的 `[P]` 不自动授权多 AI；若用户后续明确要求多 AI，必须按项目规则使用独立分支、独立工作目录和唯一负责人。

---

## Parallel Examples

### US1

```plain
并行红测：OneClickScreenDialog.spec.ts / useDiscoveryParallelFlow.spec.ts / useTheme.spec.ts / test_b096_flow_api.py / test_search_packages.py
串行实现：parallelFilterMapping → Dialog/ParallelFlow → flow_api/theme → search package → DiscoveryView
```

### US2

```plain
并行红测：useDiscoveryFlowPresentation.spec.ts / ParallelPlatformProgress.spec.ts / DiscoveryView.spec.ts
串行实现：FlowPresentation → ParallelPlatformProgress → IslandBridge → DiscoveryView
```

### US3

```plain
并行红测：useDiscoveryResults.spec.ts / FlowPresentation 结果签名测试 / DiscoveryView 现场测试
串行实现：useDiscoveryResults → FlowPresentation 通知 → DiscoveryView 接线
```

### US4

```plain
并行红测：DiscoveryRecovery.spec.ts / test_b096_flow_history.py / DiscoverySearchPackages.spec.ts
串行实现：FlowPresentation 水合 → ParallelFlow 快照兼容 → DiscoveryView 恢复顺序
```

---

## Implementation Strategy

### 最小可演示切片

US1 是最小可独立演示切片：能证明统一条件、无确认、主题和快照正确。但它不是可发布 MVP；用户最高目标要求 US1–US4 全部完成。

### 增量顺序

1. 结构护栏 + 共享契约。
2. US1：先修条件与启动入口。
3. US2：再修 02→03→04 分轨推进和原进度。
4. US3：接通第二平台结果原地合流。
5. US4：完成刷新、返回、历史与跨轮隔离。
6. 统一自动化门禁 → 真实源码 E2E → EXE 最小验证。

### 完成标准

- 54 个任务均有客观证据或明确环境阻断。
- 所有实现任务只触碰允许文件。
- 聚焦测试、一次最终全量、前端构建和卫生检查有修改后证据。
- 正式源码主流程真实跑通；EXE 真实验证完成后才可声称双形态可用。
