# Tasks: P1 Bug 三块（B072 / B074 / B073）

**Input**: `/specs/023-p1-bugfix-batch/`（spec.md / plan.md）

**Prerequisites**: plan.md（文件边界与方案要点）

**Tests**: 聚焦测试按用户故事先写后实现（先红后绿）。

**Organization**: 按用户故事分组；US1（B072）/ US2（B074）为纯前端小改，US3（B073）为前后端最简功能。US1/US2 可并行，US3 独立。

## File Boundaries

- **Allowed files**: `webui/src/components/LocationPicker.vue`、`webui/src/components/__tests__/LocationPicker.spec.ts`、`webui/src/components/PendingRecrawlCapsule.vue`、`webui/src/components/__tests__/PendingRecrawlCapsule.spec.ts`（新增）、`webui/src/views/DiscoveryView.vue`（仅胶囊传参/状态接线）、`webui/src/composables/useDiscoveryState.ts`（仅按需暴露复位信号）、`webui/src/composables/useDiscoveryResults.ts`（仅复核 resultEpoch 递增点）、`webui/src/components/BrowserAccountsDialog.vue`、`webui/src/types.ts`、`webui/pipeline_exec_accounts.py`、`webui/pipeline_exec.py`（门面仅加一行 re-export `account_for_role`）、`webui/settings_api.py`、`webui/browser_support.py`、`webui/exec_search_api.py`、`webui/ai_screen_api.py`、`webui/pipeline_jobs_api.py`、`.specify/memory/constitution.md`（如需）、`tests/`、`webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`
- **Forbidden files**: `webui/app.py`（不新增逻辑）、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/boss/`、`webui/store_migrations*.py`、`webui/error_registry.py`、`webui/app_support.py`（609 行已超 600 预警线，本批不碰）
- **New files**: `webui/src/components/__tests__/PendingRecrawlCapsule.spec.ts`、`tests/test_pipeline_exec_accounts.py`（若现有账号测试已覆盖则并入不新建）
- **Reference direction**: 后端 `settings_api/browser_support → pipeline_exec_accounts`；任务创建点 → `pipeline_exec`（门面 re-export `account_for_role`）→ `pipeline_exec_accounts`。前端 `BrowserAccountsDialog → api client`；`DiscoveryView → useDiscoveryState → PendingRecrawlCapsule`
- **Line gate**: `pipeline_exec_accounts.py` <600；`BrowserAccountsDialog.vue` ≤750；`DiscoveryView.vue` ≤1200（复位逻辑必要时放 composable）

## Verification Gate

- 交付门禁：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 卫生检查。
- 用户端到端真跑验证（SC-005）在交付后进行。

---

## Phase 1: User Story 1 - 区/县面板任何窗口宽度可用 (P1) 🎯 MVP

**Goal**: `LocationPicker.vue` 面板窄窗口定位正确、完整显示、列数自适应、最小宽度下限、区名不压缩

**Independent Test**: 组件测试窄视口断言面板在视口内 + 区名完整

### 测试（先写、先红）

- [x] T001 [US1] `webui/src/components/__tests__/LocationPicker.spec.ts`（现有文件，增补）：窄视口（jsdom `window.innerWidth` 设 500/360/300）打开面板，断言 panelStyle.left ≥ 0、left+width ≤ innerWidth（面板不溢出视口）
- [x] T002 [US1] `webui/src/components/__tests__/LocationPicker.spec.ts`：区/县格子文字完整（grid auto-fill minmax 保底 ≥96px，窄视口下面板内横向可滚动而非压字）

### 实现

- [x] T003 [US1] `webui/src/components/LocationPicker.vue`：`positionPanel` 宽度改为 `Math.min(380, window.innerWidth - 24)`（不超视口），保留 left/top clamp（left ≥ 12、left+width ≤ innerWidth-12）
- [x] T004 [US1] `webui/src/components/LocationPicker.vue`：区/县与商圈 grid 改 `repeat(auto-fill, minmax(96px, 1fr))` 等宽自适应；`.location-choice span` 不压缩完整显示（min-width:0 + white-space:normal + overflow-wrap:anywhere）；面板 `overflow-x:auto` 兜底极窄视口

**Checkpoint**: US1 独立可用——窄窗口面板正常

---

## Phase 2: User Story 2 - 重抓胶囊不再鬼畜重弹 (P1)

**Goal**: 「暂不处理」会话内隐藏、仅 resultEpoch 复位；移除绿色对勾；处理完直接消失

**Independent Test**: 组件测试 count 抖动不重弹 + resultEpoch 复位 + 无对勾

### 测试（先写、先红）

- [x] T005 [US2] 新增 `webui/src/components/__tests__/PendingRecrawlCapsule.spec.ts`：dismissed 后 count 从 >0 抖动归零再反弹，胶囊不重新出现
- [x] T006 [US2] `PendingRecrawlCapsule.spec.ts`：resultEpoch 变化 → dismissed 复位、胶囊重新出现（若仍有 count>0）
- [x] T007 [US2] `PendingRecrawlCapsule.spec.ts`：count 归零 → 胶囊直接消失，无「已全部处理」对勾元素

### 实现

- [x] T008 [US2] `webui/src/composables/useDiscoveryState.ts`：暴露共享状态 `recrawlCapsuleDismissed` ref + `dismissRecrawlCapsule()` 动作（会话内一直隐藏，组件卸载不丢）；**同文件内 `watch(resultEpoch)` 复位该状态**（不放 DiscoveryView，该文件逼近 1200 红线）
- [x] T009 [US2] `webui/src/components/PendingRecrawlCapsule.vue`：改为受控组件——新增 `dismissed: boolean` prop 与 `resultEpoch: number` prop，`dismiss` 事件上抛；删除 `done` ref、done 定时器、绿色对勾分支及 `.pending-capsule--done` / `done-collapse` 相关 CSS；删除 count 归零复位逻辑；`showPending = !dismissed && count > 0`
- [x] T010 [US2] `webui/src/views/DiscoveryView.vue`：仅模板接线——绑定共享 `dismissed`、`@dismiss="dismissRecrawlCapsule"`、传 `:result-epoch="resultEpoch"`（不新增脚本逻辑）
- [x] T011 [US2] `webui/src/composables/useDiscoveryResults.ts`：复核 resultEpoch 在结果替换/重抓完成后递增（现有 `resultEpoch.value += 1` 两处），如有缺口（如重抓出结果路径）补齐递增点

**Checkpoint**: US1+US2 独立可用——胶囊行为稳定

---

## Phase 3: User Story 3 - BOSS 账号角色 R1/R2 (P1)

**Goal**: 账号角色标记 + 弹窗顶部两个小长块下拉选号 + 任务按阶段冻结 R1/R2 账号（未指定/不可用降级当前账号），仅 BOSS

**Independent Test**: 后端角色解析与任务冻结测试 + 前端弹窗选号测试

### 测试（先写、先红）

- [x] T012 [US3] 新增 `tests/test_pipeline_exec_accounts.py`：`load_browser_accounts`/`save_browser_accounts` 透传 roles（含旧文件无 roles 字段兼容默认 `[]`）
- [x] T013 [US3] `tests/test_pipeline_exec_accounts.py`：`resolve_account_for_role`——按账号簿顺序返回第一个含该角色的账号；无匹配返回 None；`assign_account_role`——互斥（设置 R1=账号B 后其他账号的 R1 被清除，可传 None 清空该角色）
- [x] T014 [US3] 后端任务冻结测试（tests/ 相应模块）：BOSS 列表抓取任务创建 → browser_account 冻结为 R1 账号；BOSS AI 筛选任务（含 JD 详情）→ R2 账号；R1/R2 未指定 → 降级当前账号；**指定账号登录态不可用（read_cached_state not_logged_in/restricted）→ 降级当前账号**；zhilian 任务不受角色影响；**续跑路径（run 冻结值优先）沿用 DB 冻结账号、不按角色重新解析**
- [x] T015 [US3] `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`：两个小长块渲染（R1/R2 文案 + 未指定态）；点击弹下拉列出账号；选中后调 API 并显示用户名；同账号可同时选 R1+R2；再选另一账号时原账号角色被替换（互斥）

### 实现

- [x] T016 [US3] `webui/pipeline_exec_accounts.py`：`load_browser_accounts`/`save_browser_accounts` 透传 `roles`（默认 `[]`）；新增 `resolve_account_for_role(accounts, role) -> str | None`；新增 `assign_account_role(accounts, role, account_id_or_none)`（互斥打标/清标）；新增 `account_for_role(role, accounts_path=None, run=None, fallback="a")`（run 冻结值优先 → 角色解析 → read_cached_state 登录态检测 → fallback）
- [x] T017 [US3] `webui/browser_support.py`：`_project_browser_accounts` 输出 `roles` 字段（非敏感投影）
- [x] T018 [US3] `webui/settings_api.py`：新增 `PUT /api/browser-accounts/<account_id>/roles`（body `{"roles": ["R1"]}`，校验 ∈ {R1,R2} 与账号存在；**互斥语义：先清全账号该角色再给指定账号打标**；保存到账号簿；`GET` 经投影自动带 roles）
- [x] T019 [US3] `webui/src/types.ts`：`BrowserAccount` 加 `roles?: string[]`
- [x] T020 [US3] `webui/src/components/BrowserAccountsDialog.vue`：顶部（账号列表前）渲染 `[R1 · 列表/广泛抓取 · 未指定 ▾]` / `[R2 · 详情抓取 · 未指定 ▾]` 小长块 + 下拉（列出全部账号、当前角色已选高亮、可选回未指定）；选择变化调 PUT 保存并刷新；账号卡片不加控件
- [x] T021 [US3] 创建点账号解析（**仅 4 处创建点，仅 BOSS；续跑点保持 account_for_run 不动**；经 `webui/pipeline_exec.py` 门面 re-export `account_for_role`，不挂 ctx、不碰 app_support）：
  - `webui/exec_search_api.py:337`：BOSS 列表抓取任务创建 `browser_account = account_for_role("R1", app.config["BROWSER_ACCOUNTS_PATH"], fallback=ctx.account_for_run())`（zhilian 保持 account_for_run）
  - `webui/ai_screen_api.py:230`：BOSS AI 筛选任务创建 `claimed_task["browser_account"] = account_for_role("R2", app.config["BROWSER_ACCOUNTS_PATH"], run=account_source, fallback=ctx.account_for_run(account_source))`
  - `webui/pipeline_jobs_api.py:308/469`：BOSS 详情重抓任务创建 `parent_browser_account or account_for_role("R2", app.config["BROWSER_ACCOUNTS_PATH"], fallback=ctx.account_for_run())`
- [x] T022 [US3] 续跑点复核（**不改代码，仅确认冻结值优先**）：`exec_search_api.py:480/549`、`pipeline_jobs_api.py:583`、`task_continue_api.py:349/353/358`、`ai_screen_api.py:248` 均以 run/identity 冻结 browser_account 优先，续跑沿用冻结值即正确

**Checkpoint**: US1+US2+US3 全部独立可用

---

## Phase 4: 收尾与交叉

- [x] T023 [P] 复核 `webui/app.py` 未被追加逻辑（仅已有调用点）；`scripts/boss_cdp_raw.py`/`store.py`/`source.py` 零改动
- [x] T024 [P] `.specify/memory/constitution.md`：若新增文件（如 PendingRecrawlCapsule.spec.ts / test_pipeline_exec_accounts.py 属测试文件无需登记），实际无业务新文件则跳过登记
- [x] T025 [P] 仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`；`git status` 确认无无关文件

---

## Dependencies & Execution Order

- US1（B072）、US2（B074）纯前端独立，可并行；US3（B073）前后端，独立。
- 每个 Story 内：测试先写先红 → 实现 → Checkpoint 独立验证。
- 收尾（Phase 4）依赖全部 Story 完成。

## Notes

- 本批不涉及版本提升/打包/发布；收敛后 BACKLOG 三块移入归档（由收口阶段执行）。
- B073 任务冻结点改动保持最小：只替换账号解析调用，不碰 runner 主流程与门面。
