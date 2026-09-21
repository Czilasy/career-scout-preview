---
description: "Dependency-ordered implementation tasks for the two current P1 items"
---

# Tasks: P1 灵动岛分析态修复与常用搜索配置包

**Input**: `specs/044-p1-island-search-packages/v1/`

**Prerequisites**: `plan.md`、`spec.md`、`research.md`、`data-model.md`、`contracts/`、`quickstart.md`

**Tests**: 本 SPEC 明确要求保存、跨轮复用、全有或全无失败、跨平台共用和删除确认等可观察验收，因此各故事均先写聚焦测试再实现。

## File Boundaries

- **Allowed files**: `webui/store_migrations_v1.py`、`webui/store_migrations_v6.py`、`webui/store_migrations.py`、`webui/store_search_packages.py`、`webui/store.py`、`webui/search_packages.py`、`webui/search_packages_api.py`、`webui/app.py`、`webui/src/types.ts`、`webui/src/composables/useSearchPackages.ts`、`webui/src/components/DynamicIsland.vue`、`webui/src/components/SavedSearchPackagePicker.vue`、`webui/src/components/SavedSearchPackageSaveActions.vue`、`webui/src/views/DiscoveryView.vue`、上述组件/页面对应测试、`tests/test_search_packages.py`、`tests/webui_store/test_store_migrations.py`、`.specify/memory/constitution.md`、`CHANGELOG.md`、`specs/044-p1-island-search-packages/**`
- **Forbidden files**: `webui/src/composables/useDiscoveryState.ts`、`webui/src/composables/useDiscoveryWorkflow.ts`、`webui/src/composables/useDiscoverySearch.ts`、`webui/src/composables/useSearchDraftSlots.ts`、`webui/src/App.vue`、所有平台模块/抓取脚本/第三页筛选模块
- **New files**: `store_search_packages.py`、`search_packages.py`、`search_packages_api.py`、`useSearchPackages.ts`、两个配置包组件、对应聚焦测试及 `tests/test_search_packages.py`
- **Reference direction**: `app.py → API → service → store → SQLite`；`view/components → composable → apiRequest`；平台树枝不得反向进入配置包树干
- **Line gate**: `DiscoveryView.vue` 只做最小接线、不承载新业务逻辑；`DynamicIsland.vue <1200`、`app.py <800`；新 Python `<600`、新 Vue `<900`

## Phase 1: Setup and Boundary Gate

**Purpose**: 确认唯一 044 SPEC 的实施边界与当前基线。

- [ ] T001 核验当前分支不是 `main`，记录 `webui/src/components/DynamicIsland.vue` 与 `webui/app.py` 的实施前行数；本轮只使用 `specs/044-p1-island-search-packages/v1/`，不得创建、依赖或顺带处理其它 SPEC
- [ ] T002 [P] 在 `webui/src/types.ts` 增加 `SearchPackageSummary`、`SearchPackageKeywords`、`SearchPackageCity`、`SearchPackageProfile`、`SearchPackage` 类型，字段必须与 `data-model.md` 一致且不得出现 platform/filter 字段

---

## Phase 2: Foundational - Schema, Store and Domain Service

**Purpose**: 建立所有用户故事共享的版本化快照与持久化边界。

**⚠️ CRITICAL**: 本阶段完成前不得开始 UI 故事。

- [ ] T003 在 `tests/webui_store/test_store_migrations.py` 先写迁移 037 失败测试，验证新库/旧库创建 `search_packages` 表、字段、索引和 schema 版本，且表中没有 `platform`、`profile_id` 或筛选字段
- [ ] T004 在 `webui/store_migrations_v6.py` 实现迁移 037，并在 `webui/store_migrations_v1.py` 加入调度、在 `webui/store_migrations.py` 更新版本段说明；运行 T003 聚焦测试
- [ ] T005 在 `tests/test_search_packages.py` 先写存储与领域失败测试，覆盖完整 CRUD、事务边界、`payload_version=1`、JSON 损坏拒绝、默认名称、名称长度、字段归一化、禁止平台/筛选字段和列表排序
- [ ] T006 在 `webui/store_search_packages.py` 实现参数化 SQL 的列表、读取、创建、全量更新、仅改名和删除方法，并在 `webui/store.py` 只做 mixin 导入/组装；不得在存储层静默补齐损坏快照
- [ ] T007 在 `webui/search_packages.py` 实现 DTO 投影、快照规范化、完整性/版本校验、默认名称和领域错误；服务只调用 `store_search_packages.py` 暴露的方法，运行 T005 聚焦测试

**Checkpoint**: 配置包完整快照可被可靠持久化和校验，但尚无用户入口。

---

## Phase 3: User Story 1 - 在第二页主动保存当前搜索身份 (Priority: P1) 🎯 MVP

**Goal**: 只在用户点击时将当前第二页状态保存为新的配置包。

**Independent Test**: 第二页点击保存产生一个包；再次修改并保存产生独立新包；不点击不产生 API 写请求。

### Tests for User Story 1

- [ ] T008 [US1] 在 `tests/test_search_packages.py` 先写 `POST /api/search-packages` 失败测试，覆盖首次保存、再次保存新增独立 ID、空名默认值、非法快照 400 和不提供 PUT
- [ ] T009 [P] [US1] 在 `webui/src/composables/__tests__/useSearchPackages.spec.ts` 先写保存逻辑失败测试，覆盖每次 POST 新增、当前选择不改写、取消命名不发请求和普通编辑不自动写配置包 API
- [ ] T010 [P] [US1] 在 `webui/src/components/__tests__/SavedSearchPackageSaveActions.spec.ts` 先写组件失败测试，覆盖统一保存、默认名称可编辑、取消与忙碌禁用

### Implementation for User Story 1

- [ ] T011 [US1] 在 `webui/search_packages_api.py` 实现 POST 路由与安全错误映射，并在 `webui/app.py` 只增加一次 `register_search_package_routes(app, ctx)` 注册；保证 `app.py <800`，运行 T008
- [ ] T012 [US1] 在 `webui/src/composables/useSearchPackages.ts` 实现从现有第二页 refs 生成快照、默认名称和每次 POST 新增；不得添加 watch 自动保存，运行 T009
- [ ] T013 [US1] 在 `webui/src/components/SavedSearchPackageSaveActions.vue` 实现第二页统一保存命名交互，仅通过 emits 调用 composable 动作，运行 T010
- [ ] T014 [US1] 在 `webui/src/views/DiscoveryView.vue` 最小接入 `SavedSearchPackageSaveActions.vue` 与 `useSearchPackages.ts`，把现有关键词、城市、画像摘要、`profileFacts` 作为快照源；不传入 `filterValues` 或完整 `resumeAnalysis`
- [ ] T015 [US1] 在 `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts` 增加保存旅程测试并运行 US1 的后端/前端聚焦测试，证明未点击保存时零写请求

**Checkpoint**: 用户可手动将当前状态保存为新包；系统不会自动生成包，也不提供更新分支。

---

## Phase 4: User Story 2 - 从第一页选择配置包开始新一轮 (Priority: P1)

**Goal**: 第一页两次点击即可进入已完整回填的第二页，跳过上传和 AI，且不自动搜索。

**Independent Test**: 从第一页打开选择框并选择有效包，完整回填第二页；AI/搜索调用为 0，第三页筛选值不被读取或改写。

### Tests for User Story 2

- [ ] T016 [US2] 在 `tests/test_search_packages.py` 先写 GET 列表/单包失败测试，覆盖全量列表、最近更新排序、完整 DTO、同一 ID 无平台参数和读取损坏包返回 409
- [ ] T017 [P] [US2] 在 `webui/src/composables/__tests__/useSearchPackages.spec.ts` 先写 list/load/apply 失败测试，覆盖先校验后提交、第二页全部字段与 `profileFacts` 恢复、当前包记录、共享草稿写入、`filterValues` 不变和不调用 AI/搜索
- [ ] T018 [P] [US2] 在 `webui/src/components/__tests__/SavedSearchPackagePicker.spec.ts` 先写失败测试，覆盖小入口、较大列表框、全部包、空状态、加载态和选择事件

### Implementation for User Story 2

- [ ] T019 [US2] 在 `webui/search_packages_api.py` 增加 GET 列表与 GET 单包路由，复用 `search_packages.py` 完整校验与错误映射，运行 T016
- [ ] T020 [US2] 在 `webui/src/composables/useSearchPackages.ts` 实现列表加载、单包读取、本地结构复核、应用前快照与一次性提交；只在全部成功后记录当前包并请求进入第二页，运行 T017
- [ ] T021 [US2] 在 `webui/src/components/SavedSearchPackagePicker.vue` 实现第一页小入口、较大选择框、全部包列表、空/加载/失败可重试状态与选择事件，运行 T018
- [ ] T022 [US2] 在 `webui/src/views/DiscoveryView.vue` 的既有第一页区域最小接入 `SavedSearchPackagePicker.vue` 与 apply 回调；成功后最后调用现有第二页步骤切换，不调用 `analyzeResume` 或搜索动作，不重构现有上传区
- [ ] T023 [US2] 在 `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts` 增加跨轮、跨 BOSS/智联共用、完整回填、零 AI/零自动搜索、第三页不恢复的集成测试，并运行 US2 全部聚焦测试

**Checkpoint**: 保存与复用两条 P1 主链路都可独立验收。

---

## Phase 5: User Story 3 - 在选择框内管理配置包 (Priority: P2)

**Goal**: 用户可在选择框内重命名和经二次确认删除配置包。

**Independent Test**: 重命名只改名称；取消删除不发 DELETE；确认删除只移除目标包。

### Tests for User Story 3

- [ ] T024 [US3] 在 `tests/test_search_packages.py` 先写 PATCH name 与 DELETE 失败测试，覆盖内容不变、空/超长名称 400、目标不存在 404 和删除不级联其它数据
- [ ] T025 [P] [US3] 在 `webui/src/components/__tests__/SavedSearchPackagePicker.spec.ts` 先写重命名/删除失败测试，覆盖编辑取消、重命名成功、删除二次确认、取消零请求和确认后仅目标项移除

### Implementation for User Story 3

- [ ] T026 [US3] 在 `webui/search_packages_api.py` 增加 `PATCH /{id}/name` 与 DELETE 路由；在 `webui/src/composables/useSearchPackages.ts` 增加 rename/delete，并处理已载入包被删除后只清当前包身份、不清当前轮字段，运行 T024
- [ ] T027 [US3] 在 `webui/src/components/SavedSearchPackagePicker.vue` 实现内联重命名和删除确认交互，删除确认前绝不 emit 删除，运行 T025
- [ ] T028 [US3] 运行 US3 后端、composable、picker 聚焦测试，并在 `DiscoverySearchPackages.spec.ts` 补充删除配置包后的下一次“保存”仍创建新包的集成断言

**Checkpoint**: 多套并列配置可在第一页选择框内维护。

---

## Phase 6: User Story 4 - 配置包无法使用时明确报错 (Priority: P2)

**Goal**: 任何加载/校验失败都留在第一页、不部分覆盖，并通过现有灵动岛错误链报告。

**Independent Test**: 选择损坏包后，步骤和第二页全部字段保持原值，页面发出 error notice，现有灵动岛链收到红色错误消息。

### Tests for User Story 4

- [ ] T029 [US4] 在 `tests/test_search_packages.py` 补充损坏 JSON、缺字段、错误类型和未知 `payload_version` 的 409 `package_unusable` 测试，断言响应不泄露 SQL、路径或原始 payload
- [ ] T030 [P] [US4] 在 `webui/src/composables/__tests__/useSearchPackages.spec.ts` 先写服务端失败、客户端复核失败和提交期异常的原子回滚测试，断言 apply 回调与步骤切换均未发生或已完整回滚
- [ ] T031 [P] [US4] 在 `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts` 先写错误通知集成测试，断言页面停在第一页且 emit 的 notice tone 为 `error`、消息可读；复用现有 App/灵动岛链，不修改其文件

### Implementation for User Story 4

- [ ] T032 [US4] 在 `webui/search_packages.py` 与 `webui/search_packages_api.py` 收紧不可用包错误分类和安全消息，在 `webui/src/composables/useSearchPackages.ts` 实现提交期快照回滚并把错误交给页面通知回调，运行 T029–T030
- [ ] T033 [US4] 在 `webui/src/views/DiscoveryView.vue` 把配置包错误映射到现有 `notify` 事件，确保不切换 `activeStep`；运行 T031 和直接受影响的灵动岛既有聚焦测试，此错误链不得要求修改 `webui/src/App.vue`
- [ ] T034 [US4] 运行四个故事的聚焦回归，按 `quickstart.md` 核对全有或全无、零 AI、零自动搜索、零第三页恢复与平台无关断言

**Checkpoint**: 正常与低频失败路径均满足冻结 SPEC。

---

## Phase 7: User Story 5 - 修复灵动岛“分析中”显示 (Priority: P1)

**Goal**: 进入和离开“分析中”时立即重测胶囊宽度，状态点始终可见且可区分。

**Independent Test**: 从长内容状态切入分析态再切出，宽度测量两次正确触发；正常和减少动态模式下状态点都有明确颜色。

### Tests for User Story 5

- [ ] T035 [US5] 在 `webui/src/components/__tests__/DynamicIsland.spec.ts` 先写 B099 失败测试，覆盖“分析中”独立内容键、进入/离开时宽度重测、状态点 class/颜色以及减少动态模式下仍可见

### Implementation for User Story 5

- [ ] T036 [US5] 在 `webui/src/components/DynamicIsland.vue` 为简历“分析中”状态补独立内容键分支，复用现有测量 watcher，不增加轮询、固定宽度或第二套尺寸状态
- [ ] T037 [US5] 在 `webui/src/components/DynamicIsland.vue` 为“分析中”状态点增加明确静态颜色，与抓取、JD 抓取和精筛状态可区分，且不依赖呼吸动画才可见
- [ ] T038 [US5] 运行 `webui/src/components/__tests__/DynamicIsland.spec.ts` 聚焦测试并检查分析开始、分析结束和减少动态三条回归；不得改动其它状态文案、通知队列或轮播逻辑

**Checkpoint**: Bug 单 B099 与配置包主链路都已实现并通过聚焦验证。

---

## Phase 8: Documentation, Boundaries and Final Verification

**Purpose**: 更新模块地图与用户可感知记录，并对 044 中两个 P1 只做一次最终门禁。

- [ ] T039 [P] 在 `.specify/memory/constitution.md` 模块地图登记 `store_search_packages.py`、`search_packages.py`、`search_packages_api.py`、`useSearchPackages.ts` 和两个新组件的职责与单向引用；不修改宪法原则/版本
- [ ] T040 [P] 在 `CHANGELOG.md` 当前未发布版本分别记录“分析中状态显示正常”和“常用搜索配置可保存并直接复用”两项用户可感知变化，不写内部字段、测试或机制
- [ ] T041 检查全部允许文件实际行数和引用方向，确认 `DiscoveryView.vue` 只有最小接线、`DynamicIsland.vue <1200`、`app.py <800`、新 Python `<600`、新 Vue `<900`，并用 `git diff --name-only` 证明禁止文件未修改
- [ ] T042 运行一次干净后端全量 `uv run python -m unittest discover -s tests`；保存失败清单，若失败只转入失败用例与直接影响范围返修，相关改动和聚焦通过前不得重跑全量
- [ ] T043 在 `webui/` 运行一次前端全量 `npm test`，记录测试等级、数量和失败清单
- [ ] T044 通过项目真实对外 UI 在已就绪本地环境执行两条链路：简历分析时灵动岛正确显示；保存配置→开始新一轮→第一页选择→第二页回填；不直接查库或调用内部函数，前置不足时如实报告未执行
- [ ] T045 在 `webui/` 运行 `npm run build`，确认 TypeScript 与生产构建通过且构建产物不入库
- [ ] T046 运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status --short`，检查无临时文件、凭据、平台特例或无关改动；本任务不提交、不推送、不发布

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 无其它 SPEC 依赖。
- Phase 2 依赖 T001；T002 可与迁移测试并行，T006 依赖 T004–T005，T007 依赖 T006。
- US1–US4 均依赖 Phase 2，并按 P1 主链路顺序实施：US1 → US2 → US3 → US4；原因是它们增量修改同一 API、composable 和页面接线文件，不允许多 AI 并行写同文件。
- B099（US5）与配置包四个故事共享最终 Phase 8 门禁；US5 可在 Phase 2 后独立实施，但不得与其它代理同时修改同一个组件测试文件。
- Phase 8 依赖五个故事聚焦回归完成。

### Within Each Story

- 对应测试任务必须先运行并因缺少实现失败。
- 后端顺序：service/store 已就绪 → endpoint → API 聚焦测试。
- 前端顺序：composable → component → view integration → story 聚焦回归。
- 不得通过修改验收断言、跳过测试或给损坏数据补默认值让测试变绿。

### Parallel Opportunities

- T002 可与 T003–T005 的测试准备并行，因为文件不同。
- 同一故事中标 `[P]` 的 Python、composable、component 测试可由不同负责人在独立工作区准备；进入实现前由唯一集成人员串行合并。
- T039 与 T040 可并行。
- 任何会同时修改 `search_packages_api.py`、`useSearchPackages.ts`、`SavedSearchPackagePicker.vue` 或 `DiscoveryView.vue` 的任务不得并行。

## Implementation Strategy

1. 在唯一 044 SPEC 内建立迁移、存储和领域服务，锁死“通用快照、不含第三页”的数据边界。
2. 先交付第二页显式保存，再在既有第一页最小接入选择复用。
3. 增加管理与低频错误闭环。
4. 独立修复 B099 的分析态内容键与状态点颜色。
5. 两个 P1 聚焦收敛后，对整条交付链执行一次最终验证；停在验证结果，不自行提交、推送或发布。
