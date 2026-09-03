# Tasks: 多账号轮询分摊抓取（B091）

**Input**: Design documents from `/specs/038-multi-account-round-robin/`

**Prerequisites**: plan.md（必需）、spec.md（必需）

**Tests**: 本 Spec 验证门禁含聚焦测试与全量门禁（spec Verification Scope），故各 Story 含测试任务。

**Organization**: 按 User Story 分组，可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: US1 = 轮询分摊核心；US2 = 撞墙换号兜底；US3 = 全撞完即停 + 限流标识；US4 = 默认零配置
- 含确切文件路径

## File Boundaries

解析自 plan.md，每个任务只碰允许文件：

- **Allowed files**: `webui/account_round_robin.py`（新）、`webui/pipeline_exec_search.py`、`webui/pipeline_exec_details.py`、`webui/pipeline_exec_accounts.py`、`webui/resume_identity.py`、`webui/settings_api.py`、`webui/src/components/BrowserAccountsDialog.vue`、`webui/src/components/AccountPoolSelector.vue`（条件新）、`webui/src/composables/useDiscoveryExecution.ts`、`webui/src/api.ts`、`tests/test_account_round_robin.py`（新）、`tests/test_pipeline_exec_accounts*.py`、`webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`、`.specify/memory/constitution.md`
- **Forbidden files**: `webui/pipeline_exec.py`（门面）、`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`webui/historical_recovery.py`、`webui/src/views/DiscoveryView.vue`（1249 超限）、数据库迁移文件（旧配置不兼容全删，不走迁移）、`roadmap/`、`.codebuddy/`
- **New files**: `webui/account_round_robin.py`（轮询分摊调度域，~250 行）、`tests/test_account_round_robin.py`（聚焦测试，~150 行）、`webui/src/components/AccountPoolSelector.vue`（条件新，仅当 `BrowserAccountsDialog.vue` 净增超 900 预警时抽，~150 行）
- **Reference direction**: 后端 `pipeline_exec_search.py`/`pipeline_exec_details.py` → `account_round_robin.py` → `resume_identity.py`（单向）；`settings_api.py` → `pipeline_exec_accounts.py`；前端 `view → component → composable → api client`；调度域不 import 前端
- **Line gate**: `pipeline_exec_search.py` 净增后 ≤580；`pipeline_exec_details.py` 净增后 ≤570；`settings_api.py` 净增后 ≤590；`BrowserAccountsDialog.vue` 净增后 ≤950（超 900 预警则抽 `AccountPoolSelector.vue`）

## Verification Gate (task-type aware)

- 功能交付最终门禁：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 用户端到端真跑（多账号多选开抓轮询分摊、撞墙顺次换号、全撞完暂停 + 标红、默认配置、BOSS+智联两平台）在交付后由用户执行。
- 不涉及版本提升/打包/发布。

---

## Phase 1: Setup & Foundational (阻塞前置)

**Purpose**: 轮询分摊调度域（US1 前置）+ 账号池配置 schema 变更（全部 US 前置）

- [x] T001 [P] 新建 `webui/account_round_robin.py`：轮询分摊调度域——配额分摊（R1 按页/R2 按 JD 条）、多轮覆盖（总量不够自动回到 1 号再来一轮）、勾选顺序轮转、末轮零头由下一个账号自然抓完、撞墙换号接力编排入口，纯逻辑可单测，~250 行
- [x] T002 [P] 新建 `tests/test_account_round_robin.py`：轮询调度聚焦测试（配额分摊/多轮覆盖/勾选顺序/末轮零头/撞墙换号接力），~150 行
- [x] T003 [P] `webui/pipeline_exec_accounts.py`：账号池配置 schema 变更——角色→单账号互斥 → 多账号池 + 每账号配额 + 全选标记；旧配置不兼容，清空旧数据，新 schema 直接上（不走迁移）

**Checkpoint**: 轮询调度域 + 账号簿新 schema 就绪；US1–US4 可开工

---

## Phase 2: User Story 1 - 多账号轮询分摊抓取 (P1) 🎯 MVP

**Goal**: 账号弹窗 R1/R2 共用账号池多选，开抓后串行按轮询分摊（每轮每账号抓固定配额就换下一个，总量不够多轮覆盖），顺序按勾选，末轮零头自然结束

**Independent Test**: `uv run python -m unittest tests.test_account_round_robin` + R1/R2 接线测试；前端 `BrowserAccountsDialog.spec.ts` 多选渲染

### Implementation for User Story 1

- [x] T004 [P] [US1] `webui/pipeline_exec_search.py`：R1 列表抓取接线调用 `account_round_robin` 分摊页数（每轮每账号抓固定页配额就换下一个，总量不够自动多轮），净增 ≤30 行
- [x] T005 [P] [US1] `webui/pipeline_exec_details.py`：R2 详情抓取接线调用 `account_round_robin` 分摊 JD（每轮每账号抓固定 JD 配额就换下一个，总量不够自动多轮），净增 ≤30 行
- [x] T006 [P] [US1] `webui/src/components/BrowserAccountsDialog.vue`：R1/R2 角色选择从"单选互斥"改"多选 + 共用账号池"；每账号配额输入框 placeholder 灰字提示范围（R1 1–50 / R2 1–200，不暴露每页条数）；净增控制，超 900 预警则抽 `AccountPoolSelector.vue` 子组件
- [x] T007 [US1] 回归验证：`tests/test_account_round_robin.py` 全绿（分摊/多轮/顺序/末轮零头）+ R1/R2 接线不破坏既有抓取测试

**Checkpoint**: US1 完成——多选开抓轮询分摊可用

---

## Phase 3: User Story 2 - 撞墙顺次换预选账号兜底 (P1)

**Goal**: 撞硬阻断（验证码/限流等）自动在预选池顺次切下一个继续，剩余份额接力不丢，复用 B057 换号逻辑只改取号范围

**Independent Test**: `uv run python -m unittest tests.test_account_round_robin`（撞墙换号顺次接力用例）

### Implementation for User Story 2

- [x] T008 [P] [US2] `webui/resume_identity.py`：撞墙换号取号范围从"系统全池自动挑"限定为"用户预选池顺次切"（复用 B057 现有换号逻辑，只改取号范围；剩余份额接力转下一个不丢）
- [x] T009 [US2] `webui/account_round_robin.py`：撞墙信号（验证码/限流/硬阻断）触发时调用 `resume_identity` 顺次换号 + 剩余份额接力编排（依赖 T001 调度域、T008 取号范围）
- [x] T010 [US2] `tests/test_account_round_robin.py`：撞墙换号顺次接力 + 剩余份额不丢 + 只取预选池账号（不取未选账号）测试

**Checkpoint**: US2 完成——撞墙顺次换号兜底可用

---

## Phase 4: User Story 3 - 全撞完即停 + 账号限流视觉标识 (P1)

**Goal**: 所有预选账号撞墙走现有"暂停"；撞墙账号列表项字体变红 + 后缀"限流"方框，不新增报错字段/弹窗/文案

**Independent Test**: 后端全撞完走暂停测试 + 前端 `BrowserAccountsDialog.spec.ts` 限流标识渲染

### Implementation for User Story 3

- [x] T011 [P] [US3] `webui/account_round_robin.py`：所有预选账号撞墙时任务停下走现有"暂停"状态（不新增报错字段/弹窗/文案体系）
- [x] T012 [P] [US3] `webui/src/components/BrowserAccountsDialog.vue`：撞墙账号列表项字体颜色变红 + 后缀小方框标"限流"字样，作为唯一视觉标识（不新增专门报错 UI）
- [x] T013 [US3] `tests/test_account_round_robin.py` + `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`：全撞完走暂停 + 限流标识渲染测试

**Checkpoint**: US3 完成——全撞完即停 + 限流标识可用

---

## Phase 5: User Story 4 - 默认零配置可用 (P2)

**Goal**: 登录账号默认全进 R1/R2 池，新增自动加入，默认全选，每账号带默认配额（R1 25/R2 100）；全取消阻止开抓

**Independent Test**: 后端账号簿默认配置测试 + 前端开抓前校验测试

### Implementation for User Story 4

- [x] T014 [P] [US4] `webui/pipeline_exec_accounts.py`：登录账号默认全进 R1/R2 池 + 新增账号自动加入两池 + 默认全选 + 每账号默认配额（R1 25 页/R2 100 条，取范围中值）
- [x] T015 [P] [US4] `webui/src/composables/useDiscoveryExecution.ts`：开抓前校验至少 1 账号选中，全取消所有账号勾选时阻止开抓
- [x] T016 [US4] `tests/test_pipeline_exec_accounts*.py` + 前端测试：默认全进池/新增自动加入/默认全选/默认配额/全取消阻止开抓测试

**Checkpoint**: US4 完成——零配置可用

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 收尾、登记、门禁

- [x] T017 `.specify/memory/constitution.md`：模块地图登记 `webui/account_round_robin.py`（+ `AccountPoolSelector.vue` 如抽）
- [x] T018 检查 `webui/src/components/BrowserAccountsDialog.vue` 净增是否超 900 预警，超则抽 `AccountPoolSelector.vue` 子组件（多选 + 配额输入 + 限流标识），原组件只挂载
- [x] T019 `roadmap/BACKLOG.md`：B091 标题"BOSS 任务"误写修正为通用（BOSS + 智联都做）
- [x] T020 检查 README/文档是否需同步（账号弹窗多选行为变化，若需则更新 `README.md`）
- [x] T021 全量门禁：后端全量测试（`uv run python -m unittest discover -s tests -t tests`）+ 前端全量（`cd webui && npx vitest --run`）+ `npm run build`（`cd webui && npm run build`）+ 仓库卫生（`uv run python -m unittest tests.test_repo_hygiene`）；FR-020 BOSS + 智联两平台轮询分摊/撞墙换号/默认配置均覆盖（调度域平台无关，R1/R2 执行栈两平台既有，聚焦测试 + 用户端到端真跑两平台都验）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（Setup/Foundational）**: 无依赖，先行；T001 调度域 + T003 账号簿 schema 阻塞所有 US
- **Phase 2（US1 轮询分摊）**: 依赖 Phase 1（调度域 + schema）；MVP
- **Phase 3（US2 撞墙换号）**: 依赖 Phase 1（调度域）+ US1（轮询分摊先有，撞墙才接得上）
- **Phase 4（US3 全撞完 + 限流标识）**: 依赖 Phase 1 + US2（撞墙换号先有，全撞完才走暂停）
- **Phase 5（US4 默认零配置）**: 依赖 Phase 1（账号簿 schema）；可与 US2/US3 并行
- **Phase 6（Polish）**: 依赖所有 US 完成

### User Story Dependencies

- **US1（P1）**: Phase 1 后即可开始，独立可测
- **US2（P1）**: 依赖 US1 轮询分摊（撞墙换号接在分摊架构上）
- **US3（P1）**: 依赖 US2 撞墙换号（全撞完是撞墙换号的终点）
- **US4（P2）**: Phase 1 后即可开始，与 US2/US3 可并行

### Within Each User Story

- 调度域/账号簿 schema 先行，接线后行；测试与实现同批交付；聚焦测试通过后再进入下一任务

### Parallel Opportunities

- T001/T002/T003（Phase 1）可并行（不同文件）
- T004/T005/T006（US1 内）可并行（不同文件）
- T008/T009（US2 内）T009 依赖 T001 调度域；T008 可与 T009 部分并行
- T011/T012（US3 内）可并行（不同文件）
- T014/T015（US4 内）可并行（不同文件）

---

## Parallel Example: Phase 1

```bash
# 并行：轮询调度域 + 聚焦测试 + 账号簿 schema
Task: "T001 account_round_robin.py 轮询分摊调度域"
Task: "T002 test_account_round_robin.py 聚焦测试"
Task: "T003 pipeline_exec_accounts.py 账号池配置 schema"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1：调度域 + 账号簿 schema（T001→T002/T003）
2. Phase 2：US1（T004/T005/T006→T007）—— 多选开抓轮询分摊落地
3. **STOP and VALIDATE**：聚焦测试 + 真机多选开抓轮询分摊走查
4. 继续 Phase 3–5：US2 撞墙换号 → US3 全撞完 + 限流标识 → US4 默认零配置

### Incremental Delivery

1. Phase 1 → 调度域 + schema 就绪
2. US1 多选轮询分摊 → 测试 → 真机走查（MVP）
3. US2 撞墙换号兜底 → 测试 → 真机走查
4. US3 全撞完 + 限流标识 → 测试 → 真机走查
5. US4 默认零配置 → 测试 → 真机走查
6. Phase 6 门禁全绿 → 交付

---

## Notes

- [P] 任务 = 不同文件、无依赖，可并行
- `webui/src/views/DiscoveryView.vue` 一律禁止修改（超限红线）
- 轮询调度逻辑一律落 `account_round_robin.py`，R1/R2 执行域只接线，不往里塞逻辑（`pipeline_exec_search.py`/`pipeline_exec_details.py` 均逼近 600 预警）
- 撞墙换号复用 B057 现有逻辑（`resume_identity.py`），只改取号范围，不重写换号机制
- 旧账号配置不兼容，全删，新 schema 直接上，不走数据迁移
- 真机端到端验证项最终由用户执行，自动化门禁覆盖单元/组件层
