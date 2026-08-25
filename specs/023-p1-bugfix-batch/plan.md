# Implementation Plan: P1 Bug 三块（B072 / B074 / B073）

**Branch**: `023-p1-bugfix-batch` | **Date**: 2026-08-25 | **Spec**: `specs/023-p1-bugfix-batch/spec.md`

**Input**: Feature specification from `/specs/023-p1-bugfix-batch/spec.md`（grill-me 冻结 14 条）

## Summary

三个已确认 P1 条目合并一个 Spec：
1. **B072（纯前端）**：`LocationPicker.vue` 区/县面板窄窗口定位错乱 + 两字区名压缩 → 自适视口定位 + 列数自适应 + 最小宽度下限。
2. **B074（纯前端）**：`PendingRecrawlCapsule.vue`「暂不处理」本地 ref 被 count 归零复位 → 隐藏态上提/共享、仅按 resultEpoch 复位、移除绿色对勾。
3. **B073（前后端，最简）**：账号加 R1/R2 角色标记；账号弹窗顶部两个小长块下拉选号；BOSS 列表抓取阶段冻结 R1 账号、JD 详情阶段冻结 R2 账号，未指定/不可用降级当前账号。

## Technical Context

**Language/Version**: Python 3.11（后端）、TypeScript + Vue 3（前端）

**Primary Dependencies**: Flask、Vue 3、Vitest

**Storage**: `browser_accounts.json`（`webui/pipeline_exec_accounts.py` 管理，`~/.career-scout/webui/browser_accounts.json`）

**Testing**: unittest（后端 `tests/`）、Vitest（前端 `webui/src/**/__tests__/*.spec.ts`）

**Target Platform**: Windows 桌面应用（源码 + EXE）

**Project Type**: 桌面 web 应用（Flask 后端 + Vue 前端）

**Constraints**: 门面文件（`webui/app.py`、`webui/store.py`、`scripts/boss_cdp_raw.py`）不追加逻辑；Python 业务文件 ≤800 行、Vue ≤1200 行；新功能按域落位。

## Constitution Check

- **原则 III 引用方向**：后端 `settings_api → pipeline_exec_accounts`（api → 业务），前端 `BrowserAccountsDialog → api client`；无反向依赖。
- **原则 VI 落位规则**：B073 角色数据/解析放既有账号域模块 `webui/pipeline_exec_accounts.py`（账号簿域），不落门面；任务冻结账号改动落在调用方（`exec_search_api` / `ai_screen_api` / `pipeline_jobs_api` 的账号解析点）。
- **原则 II 行数门禁**：`pipeline_exec_accounts.py` 当前约 226 行，增加 roles 读写 + 角色解析函数（约 +60 行）后 <600 行，合规。
- **原则 V 验证门禁**：功能交付，必须聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 卫生检查。

## File Boundaries

- **Allowed files**:
  - `webui/src/components/LocationPicker.vue`（B072）
  - `webui/src/components/__tests__/LocationPicker.spec.ts`（现有测试文件，B072 增补）
  - `webui/src/components/PendingRecrawlCapsule.vue`（B074）
  - `webui/src/components/__tests__/PendingRecrawlCapsule.spec.ts`（新增测试）
  - `webui/src/views/DiscoveryView.vue`（B074 仅模板接线：胶囊传参/事件绑定，不新增脚本逻辑；当前 1164 行逼近 1200 红线，禁止堆逻辑）
  - `webui/src/composables/useDiscoveryState.ts`（B074 共享 dismissed 状态 + watch(resultEpoch) 复位）
  - `webui/src/composables/useDiscoveryResults.ts`（B074 resultEpoch 递增点复核）
  - `webui/src/components/BrowserAccountsDialog.vue`（B073 顶部角色选择区；当前 631 行，+100 行内）
  - `webui/src/types.ts`（BrowserAccount.roles 类型）
  - `webui/pipeline_exec_accounts.py`（B073 角色字段读写 + `resolve_account_for_role`/`assign_account_role`/`account_for_role`）
  - `webui/pipeline_exec.py`（B073 门面仅加一行 re-export `account_for_role`，宪法 VI 允许门面 re-export）
  - `webui/settings_api.py`（B073 角色保存 API；当前 525 行，+30 行 <600）
  - `webui/browser_support.py`（B073 账号投影带 roles）
  - `webui/exec_search_api.py`、`webui/ai_screen_api.py`、`webui/pipeline_jobs_api.py`（B073 任务冻结账号创建点改为按角色解析）
  - `.specify/memory/constitution.md`（如需登记新文件）
  - `tests/`（新增 `tests/test_pipeline_exec_accounts.py` 或并入现有账号测试；B073 任务冻结测试）
  - `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`（B073 前端测试）
  - `webui/src/views/__tests__/DiscoveryView.spec.ts`（B074 集成断言）
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/boss/`（本批不涉及）、`webui/store_migrations*.py`、`webui/error_registry.py`、`webui/app_support.py`（609 行已超 600 预警线，本批不碰；`_account_for_role` 放 `pipeline_exec_accounts.py` 而非此处）
- **New files**: 无必须新文件（B074 复位逻辑放 `useDiscoveryState.ts` 内，不新建）。`tests/test_pipeline_exec_accounts.py`（后端角色测试，若现有测试文件已覆盖则并入）。
- **Reference direction**: 后端 `settings_api/browser_support → pipeline_exec_accounts`；任务创建点（`exec_search_api/ai_screen_api/pipeline_jobs_api`）→ `pipeline_exec`（门面 re-export `account_for_role`）→ `pipeline_exec_accounts`。前端 `BrowserAccountsDialog → api client`；`DiscoveryView → useDiscoveryState（胶囊隐藏态）→ PendingRecrawlCapsule`。
- **Line gate**: 改动后 `pipeline_exec_accounts.py` <600 行；`BrowserAccountsDialog.vue` ≤750 行；`DiscoveryView.vue` 保持 ≤1200 行（若逼近红线，复位逻辑放 composable）。
- **Rationale**: B072/B074 为单组件+状态层修复，不拆模块；B073 角色是账号属性，落账号域模块（既有域，禁止开新门面），任务冻结账号在调用点解析最小化改动，避免触碰 runner 主流程。

## Verification Gate

- 交付门禁：聚焦测试（LocationPicker / PendingRecrawlCapsule / useDiscoveryState / pipeline_exec_accounts / 任务冻结账号）+ 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查。
- 用户端到端真跑验证（SC-005）在交付后进行。

## Project Structure

```text
specs/023-p1-bugfix-batch/
├── spec.md              # 冻结需求 + 用户故事 + FR/SC
├── plan.md              # 本文件
└── tasks.md             # 按用户故事分组的实施清单
```

## Implementation Approach

### US1（B072）— `LocationPicker.vue`

- **定位修复**：`positionPanel()` 已有 fixed + 视口 clamp 逻辑，但窄窗口下因面板宽度随视口缩水（`min(380, innerWidth-24)`）且 grid 固定 3 列，导致面板过窄、区名被压成一字、位置错乱。改为：
  - 面板宽度：`width = Math.min(380, window.innerWidth - 24)`（**永远不超视口**，fixed 定位超出视口会不可见不可滚）；左侧/右侧 clamp 保持 `left ≥ 12`、`left + width ≤ innerWidth - 12`（现有逻辑保留）。
  - 区/县 grid：「不压字」用 **CSS 等宽自适应**实现——`repeat(auto-fill, minmax(96px, 1fr))`，格子永远 ≥96px，两字区名必然完整；面板内横向超出部分由面板 `overflow-x:auto` 承载（`innerWidth` 极小如 300px 时出现横向滚动，而不是面板溢出视口）。
  - 文本不压缩：`.location-choice span` 允许换行/收缩完整显示（`min-width:0` + `white-space:normal` + `overflow-wrap:anywhere`）。
  - 面板 `overflow-y:auto` 已在 body 上，内部纵向滚动自然生效。
- **测试**：`webui/src/components/__tests__/LocationPicker.spec.ts`（现有测试文件，B072 增补窄视口断言：jsdom 设 `window.innerWidth` 500/360/300，打开面板断言 panelStyle left ≥ 0、left+width ≤ innerWidth；grid 列数/格宽不压字）。

### US2（B074）— PendingRecrawlCapsule + Discovery 状态

- **根因**：`dismissed` 是组件内 ref，`watch(count)` 在 prev>0→0 时 `dismissed=false` 复位；平台切换使 filtered groups.uncertain 抖动归零 → 隐藏态被清、胶囊重弹；同时 `done` 对勾误触发。
- **修复**：
  - **`dismissed` 上提共享状态**（`useDiscoveryState.ts` 暴露 `recrawlCapsuleDismissed` ref 与 `dismissRecrawlCapsule()`），组件改为受控 prop（`dismissed` + `@dismiss` 事件），保证切页/切历史模式组件卸载重建后隐藏态不丢（满足「会话内一直隐藏」）。
  - 复位时机：仅按 `resultEpoch` 变化复位（`watch(resultEpoch)` 放在 `useDiscoveryState.ts` 内，与 `recrawlCapsuleDismissed` 同文件；**不放 DiscoveryView**——该文件 1164 行逼近 1200 红线，禁止堆逻辑），将 `recrawlCapsuleDismissed=false`，**删除 count 归零复位逻辑**。
  - 组件删除 `done` ref、1200ms 定时器、绿色对勾分支及 `.pending-capsule--done` / `done-collapse` 相关 CSS；`showPending = !dismissed && count > 0`。
  - `DiscoveryView.vue`：绑定 `dismissed` 共享状态、`@dismiss` 调用共享动作、传 `:result-epoch="resultEpoch"`（已有该值）。
- **测试**：`webui/src/components/__tests__/PendingRecrawlCapsule.spec.ts`（新增）：dismissed 后 count 抖动不重弹；resultEpoch 变化复位；对勾不存在；count 归零直接消失。`webui/src/views/__tests__/DiscoveryView.spec.ts` 增补传参/共享状态断言。

### US3（B073）— 账号角色 R1/R2

- **数据层** `webui/pipeline_exec_accounts.py`：
  - `load_browser_accounts` / `save_browser_accounts` 透传 `roles: list[str]`（默认 `[]`；兼容旧文件无字段）。
  - 新增 `resolve_account_for_role(accounts, role) -> str | None`：遍历账号簿，返回第一个 `role in roles` 的账号 id；无则 `None`。
  - 新增 `assign_account_role(accounts, role, account_id_or_none)`：角色→账号**一对一互斥**——先把所有账号的该 role 标记清除，再给指定账号（或 None）打标；返回更新后的账号簿（供保存）。
- **API** `webui/settings_api.py`：
  - `GET /api/browser-accounts` 经 `browser_support._project_browser_accounts` 返回 `roles`。
  - 新增 `PUT /api/browser-accounts/<id>/roles`，body `{"roles": ["R1"]}`（或 `[]`），校验取值 ∈ {R1,R2}、账号存在；**先清全账号该角色再给指定账号打标**（互斥语义），保存回 `browser_accounts.json`。
- **前端** `BrowserAccountsDialog.vue`：
  - 顶部（`browser-account-list` 之前）渲染两个小长块 `[R1 · 列表/广泛抓取 · 未指定 ▾]` / `[R2 · 详情抓取 · 未指定 ▾]`；点开下拉列出所有账号（当前角色已选者高亮，可取消选回「未指定」）。
  - 选择变化调 `PUT /api/browser-accounts/<id>/roles` 保存并刷新；同账号可同时是 R1+R2（各自独立）。
  - 账号删除后角色回退未指定（后端删除时若该账号被指定，前端刷新后自然回退；无需额外逻辑）。
  - `types.ts` BrowserAccount 加 `roles?: string[]`。
- **任务冻结账号**（角色在任务创建时冻结；仅 BOSS 平台；**续跑一律沿用 DB 冻结值，不重新解析角色**——冻结需求第 13 条）：
  - `webui/pipeline_exec_accounts.py`（225 行，+70 合规 <600）新增 `account_for_role(role, accounts_path=None, run=None, fallback="a") -> str`，逻辑顺序：
    1. **run 冻结值优先**：`run` 的 `execution_params.browser_account` 非空且在账号簿 → 直接返回（续跑沿用冻结值）；
    2. 角色解析：`resolve_account_for_role(load_browser_accounts(...), role)` 取角色账号；
    3. **登录态检测**：取到账号后用 `read_cached_state(account_id, "boss")` 检查（not_logged_in/restricted → 视为不可用，跳过）；
    4. 角色未指定或账号不可用 → 返回 `fallback`（调用方传当前账号），不报错不阻断。
    - 经 `webui/pipeline_exec.py` 门面 re-export（加一行 import，门面只 re-export 合规），调用点 `from webui.pipeline_exec import account_for_role`；**不挂 ctx、不碰 app_support**。
  - **创建点替换（共 4 处，仅 BOSS）**：
    - `exec_search_api.py:337`（BOSS 列表抓取任务创建）：`browser_account = account_for_role("R1", app.config["BROWSER_ACCOUNTS_PATH"], fallback=ctx.account_for_run())`（zhilian 保持 account_for_run）；
    - `ai_screen_api.py:230`（BOSS AI 筛选任务创建，含 JD 详情阶段）：`account_for_role("R2", app.config["BROWSER_ACCOUNTS_PATH"], run=account_source, fallback=ctx.account_for_run(account_source))`（新建时 account_source=None 走角色解析）；
    - `pipeline_jobs_api.py:308/469`（BOSS 详情重抓任务创建）：`parent_browser_account or account_for_role("R2", app.config["BROWSER_ACCOUNTS_PATH"], fallback=ctx.account_for_run())`（parent 冻结值优先，无则按 R2 角色）。
  - **续跑点不动（保持 `account_for_run`）**：`exec_search_api.py:480/549`、`pipeline_jobs_api.py:583`、`task_continue_api.py:349/353/358`、`ai_screen_api.py:248`——这些内部均以 run/identity 冻结 browser_account 优先，沿用冻结值即正确，不得改为按角色解析。
  - 智联平台全部保持 `account_for_run()` 不变。
- **测试**：
  - `tests/test_pipeline_exec_accounts.py`：roles 读写/兼容旧文件/`resolve_account_for_role` 首账号匹配。
  - 任务冻结测试：mock ctx 与账号簿，断言 boss 列表任务 browser_account=R1 账号、boss AI 任务=R2 账号、未指定降级当前账号、zhilian 不受影响；**续跑路径沿用 DB 冻结值（run 冻结优先于角色解析）**。
  - `BrowserAccountsDialog.spec.ts`：两个小长块渲染、下拉选号调 API、选中显示用户名、同账号双角色。

## Complexity Tracking

无宪法违规。B073 任务冻结点共 3 处调用点替换，不新增任务类型、不碰 runner 主流程。
