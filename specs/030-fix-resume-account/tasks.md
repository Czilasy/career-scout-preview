---
description: "Task list template for feature implementation"
---

# Tasks: 续跑账号身份修复

**Input**: Design documents from `/specs/030-fix-resume-account/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/http-api-delta.md, quickstart.md

**Tests**: Spec 的验证范围与 quickstart.md 明确列出测试文件与场景，测试用例为交付必需（先写先败）。

**Organization**: 按用户故事分组，各故事可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事（US1–US4）
- 描述必须含确切文件路径

## File Boundaries

（承自 plan.md，2026-08-30 用户确认）

- **Allowed files**: `webui/resume_identity.py`、`webui/task_continue_api.py`、`webui/pipeline_jobs_api.py`、`webui/exec_search_api.py`、`webui/ai_screen_api.py`、`webui/runners/ai_screen_jd.py`、`tests/webui_app/test_webui_app_taskrun.py`、`tests/webui_app/test_resume_account_gate.py`（新）、`.specify/memory/constitution.md`（仅模块地图小节）
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`（门面禁改）；`webui/src/**`；`webui/dist/**`；`scripts/boss/**`；`webui/store_*.py`
- **New files**: `tests/webui_app/test_resume_account_gate.py`（双门槛/快照/兜底/可见化聚焦测试）
- **Reference direction**: `*_api.py → resume_identity → pipeline_exec_accounts`；`runners/ai_screen_jd → ctx.activate_task_browser`；禁止反向 import
- **Line gate**: task_continue_api ≤765（预期净减）；pipeline_jobs_api ≤659；exec_search_api ≤644；resume_identity ≤400

## Verification Gate (task-type aware)

- 本批次为功能交付：最终门禁为聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查（quickstart.md T018 汇总）。
- 版本提升/提交/推送等收口任务不在本清单内；收口时按根 `AGENTS.md` 只跑卫生测试、hooks、`git diff --check`、`git status`、`scripts/release_check.ps1`（若存在）。

---

## Phase 1: Setup

**Purpose**: 本批次无项目初始化需求（复用既有分层与测试基建），无 Setup 任务。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 续跑身份域共享基建，全部用户故事依赖。

- [x] T002 [P] 在 `webui/resume_identity.py` 新增共享基建：快照键常量 `ACTIVE_ACCOUNT_AT_FREEZE_KEY = "active_account_at_freeze"`、AI 类暂停码冻结集合 `AI_PAUSE_CODES`（ai_rate_limited / ai_quota_exhausted / ai_key_invalid / ai_network_error，注释标注 error_registry "ai" 类目来源）、创建点快照助手（把当前全局账号写入 execution_params 的单函数）、账号展示名解析助手（`load_browser_accounts` 取 name，缺失回退账号 id）。保持纯域逻辑：不 import api/app，仅可依赖 `webui.pipeline_exec_accounts` 与注入参数。

**Checkpoint**: 域基建就绪，用户故事可开始。

---

## Phase 3: User Story 1 - 续跑沿用任务自己的账号 (Priority: P1) 🎯 MVP

**Goal**: 统一继续接口的自动换号收紧为双门槛（快照变化 + 非 AI 类暂停码），R2 冻结账号不再被覆盖，B057 面板切号语义保留。

**Independent Test**: 配置全局 d、R2=b 创建 AI 筛 run 并暂停，不带 target_account 继续——任务仍用 b 且执行参数账号字段零改写；B057 场景（暂停期间激活新账号）继续仍换号。

### Tests for User Story 1 ⚠️（先写先败）

- [x] T003 [P] [US1] 新建 `tests/webui_app/test_resume_account_gate.py`，写 US1 判定用例：①快照命中未换号（快照=当前，冻结≠当前）→ 不换；②快照≠当前且冻结≠当前（B057）→ 换到当前；③快照缺失 → 不换；④快照≠当前但暂停码为 AI 类 → 不换；⑤显式 target_account 优先于一切门槛；⑥智联 run 不受双门槛影响（沿用冻结）。

### Implementation for User Story 1

- [x] T004 [US1] 在 `webui/resume_identity.py` 实现双门槛判定函数（输入 run、当前全局账号、账号簿；输出 换号决策/原账号/新账号；内部先判显式 target（调用方传入）→ 快照门槛 → AI 码门槛 → 账号可用性沿用既有 check_resume_block 链，不在本函数重复校验登录态），纯函数无 IO（账号簿经参数注入）。
- [x] T005 [P] [US1] 三个创建点写入快照：`webui/exec_search_api.py`（抓取创建 execution_params）、`webui/ai_screen_api.py`（AI 筛创建 execution_params）、`webui/pipeline_jobs_api.py`（批量与单岗位重抓创建 execution_params），均经 T002 助手取"当时全局账号"；`exec_search_api.py` 同时把续跑兜底（原 486-490 行区域）替换为域助手调用以保证净不增长。
- [x] T006 [US1] `webui/task_continue_api.py` 将原自动换号块（151-214 行区域：active/frozen 比较与 target_account 推导）替换为 T004 判定函数调用 + 既有校验链（登录空间解析、check_resume_block、persist_frozen_identity 全部保持原顺序与语义）；显式 target_account 分支行为不变；删除随之冗余的局部代码，确保文件净减。
- [x] T007 [US1] `tests/webui_app/test_webui_app_taskrun.py` 回归与扩展：既有 B057 用例（`test_b057_continue_uses_active_account_without_target` 等）不改断言语义保持通过；新增集成用例：创建含快照的 BOSS AI run（冻结 b、快照 d）→ 置 paused（非 AI 码）→ 不带 target 继续 → 断言执行参数 `browser_account` 仍为 b 且无 account_switch 事件。

**Checkpoint**: US1 独立可验证——R2 场景零改写、B057 场景保留、存量无快照任务安全。

---

## Phase 4: User Story 2 - 自动换号对用户可见 (Priority: P2)

**Goal**: 双门槛命中并完成换号时，写 `account_switch` 任务事件 + 续跑启动日志中文行；未换号零痕迹。

**Independent Test**: 构造 B057 式换号继续——任务事件含 account_switch（原/新账号标识），任务进度日志出现"本次从账号「X」切换到账号「Y」继续"；未换号续跑两者皆无。

### Tests for User Story 2 ⚠️（先写先败）

- [x] T008 [P] [US2] `tests/webui_app/test_resume_account_gate.py` 补 US2 用例：换号 → 事件 kind `account_switch` 载荷含 from/to 账号 id 与展示名、内存任务 logs 含中文换号行；未换号 → 无事件无日志行。

### Implementation for User Story 2

- [x] T009 [US2] `webui/resume_identity.py` 新增换号应用函数（封装：`store.append_task_event(run_id, "account_switch", {from_account, to_account, from_name, to_name})` + 内存任务 logs 追加中文说明行，账号展示名经 T002 助手）；`webui/task_continue_api.py` 在身份改写发生处调用（T006 决策命中后），续跑启动路径可见日志。事件与执行参数改写保持在同一继续请求内完成。

**Checkpoint**: US2 独立可验证——换号必留痕、不换零痕迹。

---

## Phase 5: User Story 3 - 存量任务账号兜底口径统一 (Priority: P3)

**Goal**: BOSS 存量任务缺冻结账号时，统一继续接口与筛选提交续跑同口径按 R2 角色解析并写回；智联不变。

**Independent Test**: 构造无冻结账号的 BOSS paused run + R2=b，从统一继续接口续跑 → 使用 b 且冻结身份写回 b；智联同场景维持 account_for_run 口径。

### Tests for User Story 3 ⚠️（先写先败）

- [x] T010 [P] [US3] `tests/webui_app/test_resume_account_gate.py` 补 US3 用例：①BOSS 无冻结账号 → 角色解析出 R2 账号并写回 execution_params；②R2 未标记或账号簿不可用 → 回退当前全局账号（不报错）；③智联 → 维持现状口径。

### Implementation for User Story 3

- [x] T011 [US3] `webui/resume_identity.py` 新增角色感知兜底函数（BOSS → `account_for_role("R2", run=run, fallback=当前全局账号)`；智联 → `account_for_run(run)`；仅当 run 冻结账号字段缺失时生效）。
- [x] T012 [US3] `webui/task_continue_api.py` AI 分支冻结账号缺省填充改调 T011 助手；`webui/pipeline_jobs_api.py` `continue_recrawl` 兜底（605-609 行区域）同步替换，并把 job-detail 的父身份继承块（66-92 行区域）搬至 `webui/resume_identity.py` 作为 `inherit_parent_browser_identity` 助手，路由处改调用——两文件净行数不增长。

**Checkpoint**: US3 独立可验证——两条续跑路径同口径。

---

## Phase 6: User Story 4 - 任务运行中浏览器不被其它操作抢用 (Priority: P3)

**Goal**: 单岗位 JD 抓取在任务运行/排队时 409 拒绝；JD 阶段启动浏览器前重绑任务冻结身份。

**Independent Test**: 任务运行中 POST /api/job-detail → 409 中文提示且全局目录无副作用；全局目录被改后，AI 筛 JD 阶段启动前 `activate_task_browser(task_id)` 以冻结账号被调用。

### Tests for User Story 4 ⚠️（先写先败）

- [x] T013 [P] [US4] `tests/webui_app/test_webui_app_taskrun.py` 补用例：①内存任务 running 时 POST /api/job-detail → 409 + 中文提示，且 `set_active_cdp_data_dir` 未被调用；②run_jd_stage 启动时 `ctx.activate_task_browser` 以任务冻结账号被调用（mock 断言参数）。

### Implementation for User Story 4

- [x] T014 [US4] `webui/pipeline_jobs_api.py` job-detail 路由在身份继承与浏览器激活之前加 `ctx.has_active_pipeline_task()` 门禁，命中返回 409 + 中文提示（口径与 AI 筛提交入口一致）。
- [x] T015 [US4] `webui/runners/ai_screen_jd.py` `run_jd_stage` 在首次 `ensure_chrome_ready` 调用前执行 `ctx.activate_task_browser(task_id)`（对齐 `runners/recrawl_task.py` 既有模式）。

**Checkpoint**: US4 独立可验证——并发拒绝生效、JD 阶段身份自持。

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T016 [P] `.specify/memory/constitution.md` 模块地图小节登记一行：`webui/resume_identity.py` — 续跑身份域：冻结身份解析/持久化、账号快照、双门槛自动换号判定、角色感知兜底、父身份继承（030）。
- [x] T017 行数门禁核查（宪法 VI）：`wc -l` 核对 `webui/task_continue_api.py` ≤765、`webui/pipeline_jobs_api.py` ≤659、`webui/exec_search_api.py` ≤644、`webui/resume_identity.py` ≤400、`webui/runners/ai_screen_jd.py` ≤600、`webui/ai_screen_api.py` ≤600；超限则回到对应任务继续搬运。
- [x] T018 按 `specs/030-fix-resume-account/quickstart.md` 执行完整验证：聚焦测试 → 后端全量 `uv run python -m unittest discover -s tests` → 前端 `npm --prefix webui test` → `npm --prefix webui run build` → `uv run python -m unittest tests.test_repo_hygiene`；全绿后本批次交付完成（版本提升与提交为收口任务，另行执行）。

> **T018 执行结果（2026-08-30）**：聚焦测试全绿（gate 21 项 + taskrun 65 项，含 test_pipeline_pause_guard 修复 fake ctx 后 20 项）；后端全量 2748 项中 2745 绿，余 3 项均与本批次无关——①README 桌面段版本仍为 v1.8.1（版本文件已 1.8.3，既有文档滞后）②Vue 契约测试解析的 dist 与会话前未提交的前端改动不同步 ③卫生测试的未跟踪文件即本批次交付物（提交时 git add 解决）。前端 vitest 553 项全绿；`npm run build` 失败于会话前已存在的 styles.css/App.vue 未完成改动（lightningcss 空选择器），属本批次禁改边界，待前端工作完成后重建。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational（T002）**：阻塞全部用户故事。
- **US1（T003-T007）**：依赖 T002；US1 是 MVP。
- **US2（T008-T009）**：依赖 T002 + T004/T006（复用决策与应用位置）。
- **US3（T010-T012）**：依赖 T002；与 US1/US2 无逻辑耦合，可在其后并行。
- **US4（T013-T015）**：无前置依赖（T002 之外），可与 US2/US3 并行。
- **Polish（T016-T018）**：依赖全部故事完成。

### Within Each User Story

- 测试先写且先败（T003/T008/T010/T013 先于对应实现任务）。
- 域函数先于路由接线；接线先于集成回归。

### Parallel Opportunities

- T002 完成后：US1 测试（T003）与 US4 测试（T013）可并行。
- T005（三个创建点）内部三个文件可并行；与 T006 同属 US1 但不同文件，T004 完成后可并行。
- US3、US4 的实现任务在 US1 合入后可并行推进（不同文件）。

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. T002 域基建 → 2. T003 测试先败 → 3. T004-T006 实现 → 4. T007 回归 → **STOP 验证 US1**。

### Incremental Delivery

US1（MVP，核心缺陷修复）→ US2（可见化，小增量）→ US3（口径统一，小增量）→ US4（并发防护，独立增量）→ Polish（门禁核查 + 全量验证）。

---

## Notes

- [P] 任务 = 不同文件、无未完成依赖。
- 每故事 Checkpoint 处可独立验证后再前进。
- 本清单不含版本提升/提交/推送收口任务；交付后按根 `AGENTS.md` 收口规则另行执行。

## Phase 8: Convergence

> 2026-08-30 收口审查：FR-001～FR-010、US1-US4 全部验收场景、宪法 I/II/III/VI 与行数门禁均核验通过（显式 target_account 路径经 404/阻断用例回归，覆盖率成立）。唯一发现为下述文档追溯缺口。

- [x] T019 按 Complexity Tracking 的实际实施记录，在 plan.md File Boundaries 的 Allowed files 中补登 webui/runners/ai_screen_task.py（快照随 INSERT OR REPLACE 落库所必需）与 tests/test_pipeline_pause_guard.py（T015 桩适配），消除边界清单与实施记录的偏差 per plan: File Boundaries (partial)
