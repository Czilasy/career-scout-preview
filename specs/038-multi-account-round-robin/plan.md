# Implementation Plan: 多账号轮询分摊抓取（B091）

**Branch**: `038-multi-account-round-robin` | **Date**: 2026-09-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/038-multi-account-round-robin/spec.md`

## Summary

四块用户可感知改动：

1. **多账号轮询分摊抓取（核心）**：账号弹窗 R1/R2 从"角色→单账号互斥单选"改为"共用账号池多选"；开抓后串行按轮询（round-robin）分摊到选中账号，每轮每账号只抓固定配额（R1 按页 1–50 默认 25、R2 按 JD 条 1–200 默认 100）就换下一个，总量不够自动多轮覆盖；顺序按勾选顺序，第二轮从 1 号开始；末轮零头由下一个账号自然抓完。
2. **撞墙顺次换预选账号兜底**：撞硬阻断（验证码/限流等）自动在预选池顺次切下一个继续，剩余份额接力不丢；复用 B057 现有换号逻辑，只把取号范围从"系统全池自动挑"限定为"用户预选池顺次切"。
3. **全撞完即停 + 限流视觉标识**：所有预选账号撞墙走现有"暂停"；撞墙账号在账号列表项字体变红 + 后缀"限流"方框，不新增报错字段/弹窗/文案。
4. **默认零配置**：登录账号默认全进 R1/R2 池，新增账号自动加入，默认全选，每账号带默认配额；全取消阻止开抓。

技术路径：新建轮询分摊调度域 `webui/account_round_robin.py`（配额分摊/多轮覆盖/顺序/末轮零头/撞墙换号接力编排，纯逻辑可单测）；R1/R2 执行（`pipeline_exec_search.py` / `pipeline_exec_details.py`）只接线调用调度域，不往里塞逻辑（两文件均逼近 Python 600 预警线）；账号池配置扩 `pipeline_exec_accounts.py`（345 行有余量），账号簿 schema 变（角色→单账号互斥 → 多账号池 + 配额 + 全选标记），旧配置不兼容全删；撞墙换号取号范围改 `resume_identity.py`（复用 B057）；前端 `BrowserAccountsDialog.vue`（863 逼近 Vue 900 预警）改多选 + 限流标识，净增控制，超则抽 `AccountPoolSelector.vue` 子组件；默认配置在账号簿读写层落地。

## Technical Context

**Language/Version**: Python 3.11+（后端）/ TypeScript + Vue 3（前端）

**Primary Dependencies**: 既有 webui pipeline 执行栈（`pipeline_exec_*`）/ Vue 3 组合式 API

**Storage**: 账号配置 schema 变（角色→单账号互斥 → 多账号池 + 每账号配额 + 全选标记）；旧配置不兼容，全删，新 schema 直接上（不走数据迁移保留旧数据）

**Testing**: 后端 pytest（聚焦 `tests/test_account_round_robin.py` 等 + 全量）+ 前端 Vitest

**Target Platform**: BOSS + 智联两平台通用（B091 通用功能）

**Project Type**: 桌面应用本地 Web 工作台

**Performance Goals**: 轮询调度不引入额外延迟；分摊降风控为主，不追求提速

**Constraints**: `BrowserAccountsDialog.vue`（863 逼近 Vue 900 预警）、`settings_api.py`（569 逼近 600）、`pipeline_exec_search.py`（549 逼近 600）、`pipeline_exec_details.py`（538 逼近 600）均不得继续往里塞新逻辑，须分流新模块；门面文件（`pipeline_exec.py` / `app.py` / `store.py` / `source.py` / `boss_cdp_raw.py` / `zhilian_cdp_raw.py` / `task_runners.py` / `historical_recovery.py`）禁止加逻辑；`DiscoveryView.vue`（1249 超限）禁止改

**Scale/Scope**: 单用户桌面工具；本轮只做账号池多选 + 轮询分摊 + 撞墙换号取号范围 + 限流标识 + 默认配置，不重写抓取栈、不动门面、不改 DiscoveryView

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **职责分层（I）**：通过。轮询调度落 `webui/account_round_robin.py` 独立域（纯逻辑），R1/R2 执行域只接线；账号池配置落 `pipeline_exec_accounts.py`（账号簿域）；前端多选/限流标识落 `BrowserAccountsDialog.vue`（或抽组件）；符合 api→service→store 与 view→component→composable 分层。
- **单文件尺寸（II）**：通过。`account_round_robin.py` 新建控 ~250 行；`pipeline_exec_search.py` / `pipeline_exec_details.py` / `settings_api.py` 均逼近 600 预警，新逻辑分流到 `account_round_robin.py`，执行域只接线净增 ≤30 行；`BrowserAccountsDialog.vue` 863 逼近 900 预警，多选+限流标识净增控制，超则抽 `AccountPoolSelector.vue` 子组件。
- **引用方向（III）**：通过。`pipeline_exec_search.py` / `pipeline_exec_details.py` → `account_round_robin.py`（单向）；`account_round_robin.py` → `resume_identity.py`（复用换号，单向）；前端 view→component→composable→api client。
- **拆分纪律（IV）**：通过。非重构 Spec，不拆既有大文件；新增调度域为新文件。
- **验证门禁（V）**：通过。Verification Gate 章节明确功能交付门禁（聚焦测试 + 后端全量 + 前端 + `npm run build` + 仓库卫生），用户端到端真跑交付后由用户执行；不涉及版本/打包/发布。
- **模块地图（VI）**：新文件（`account_round_robin.py`，必要时 `AccountPoolSelector.vue`）需在同一批次内登记进宪法模块地图。
- **错误处理与可观测性（VII）**：轮询撞墙走现有暂停 + 限流标识，不新增 pass 吞异常；换号留痕复用 B057 既有。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`.*

- **Allowed files**:
  - `webui/account_round_robin.py`（新，轮询分摊调度域）
  - `webui/pipeline_exec_search.py`（仅接线：R1 调用调度域分摊页数）
  - `webui/pipeline_exec_details.py`（仅接线：R2 调用调度域分摊 JD）
  - `webui/pipeline_exec_accounts.py`（账号池配置 schema + 默认全进池/全选/默认配额）
  - `webui/resume_identity.py`（撞墙换号取号范围限定到预选池，复用 B057）
  - `webui/src/components/BrowserAccountsDialog.vue`（多选 UI + 限流视觉标识，净增控制）
  - `webui/src/composables/useDiscoveryExecution.ts`（如需：开抓前校验至少 1 账号选中）
  - `webui/src/api.ts`（账号池配置接口类型扩展，如需）
  - `webui/settings_api.py`（账号池配置读写端点，仅端点，净增控制；逻辑落 `pipeline_exec_accounts.py`）
  - `tests/test_account_round_robin.py`（新，聚焦测试）
  - `tests/test_pipeline_exec_accounts.py` 或既有账号簿测试（账号池配置/默认配置测试）
  - `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`（多选/限流标识测试）
  - `.specify/memory/constitution.md`（模块地图登记）
- **Forbidden files**: `webui/pipeline_exec.py`（门面）、`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`webui/historical_recovery.py`、`webui/src/views/DiscoveryView.vue`（1249 超限）、数据库迁移文件（旧配置不兼容全删，不走迁移）、`roadmap/`、`.codebuddy/`
- **New files**:
  - `webui/account_round_robin.py` — 轮询分摊调度域：配额分摊、多轮覆盖、勾选顺序、末轮零头、撞墙换号接力编排（复用 B057 取号范围限定），纯逻辑可单测，~250 行
  - `tests/test_account_round_robin.py` — 轮询调度聚焦测试（分摊/多轮/顺序/末轮/撞墙接力），~150 行
  - `webui/src/components/AccountPoolSelector.vue`（条件新，仅当 `BrowserAccountsDialog.vue` 净增超 900 预警时抽） — 多选 + 配额输入 + 限流标识子组件，~150 行
- **Reference direction**: 后端 `pipeline_exec_search.py` / `pipeline_exec_details.py` → `account_round_robin.py` → `resume_identity.py`（单向）；`settings_api.py` → `pipeline_exec_accounts.py`；前端 `view → component → composable → api client`；调度域不 import 前端
- **Line gate**: `pipeline_exec_search.py` 净增后 ≤580（超 560 则 R1 接线逻辑进一步外移到调度域）；`pipeline_exec_details.py` 净增后 ≤570；`settings_api.py` 净增后 ≤590（端点逻辑外移到 `pipeline_exec_accounts.py`）；`BrowserAccountsDialog.vue` 净增后 ≤950（超 900 预警则抽 `AccountPoolSelector.vue`）
- **Rationale**: 轮询分摊是独立调度域，开新模块避免 `pipeline_exec_search/details` 逼近 600 预警继续膨胀；账号池配置属账号簿域扩 `pipeline_exec_accounts.py`（345 有余量）避免 `settings_api.py` 逼近 600 继续塞逻辑；前端多选+限流标识如净增大则抽组件避免 `BrowserAccountsDialog.vue` 超 900 预警；不追加到门面/超限文件

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付最终门禁：聚焦测试（轮询分摊、撞墙换号顺次接力、全撞完暂停 + 限流标识、默认配置生效、全取消阻止开抓、两平台通吃）+ 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 用户端到端真跑（多账号多选开抓轮询分摊、撞墙顺次换号、全撞完暂停 + 标红、默认配置、两平台）在交付后由用户验证。
- 不涉及版本提升/打包/发布；BACKLOG B091 标题"BOSS 任务"误写需同步修正（改为通用），README 是否需同步实现收敛后检查。

## Project Structure

### Documentation (this feature)

```text
specs/038-multi-account-round-robin/
├── spec.md              # 需求规格（已完成）
├── plan.md              # 本文件
└── tasks.md             # /speckit-tasks 输出
```

### Source Code (repository root)

```text
webui/
├── account_round_robin.py          # [新] 轮询分摊调度域
├── pipeline_exec_search.py         # [改] R1 接线调用调度域
├── pipeline_exec_details.py        # [改] R2 接线调用调度域
├── pipeline_exec_accounts.py       # [改] 账号池配置 schema + 默认配置
├── resume_identity.py              # [改] 撞墙换号取号范围限定预选池
├── settings_api.py                 # [改] 账号池配置端点（仅端点）
└── src/
    ├── components/
    │   ├── BrowserAccountsDialog.vue   # [改] 多选 UI + 限流标识
    │   └── AccountPoolSelector.vue     # [新，条件] 多选+配额+限流子组件
    ├── composables/
    │   └── useDiscoveryExecution.ts     # [改] 开抓前至少1账号选中校验
    └── api.ts                           # [改] 账号池配置接口类型

tests/
├── test_account_round_robin.py          # [新] 轮询调度聚焦测试
└── test_pipeline_exec_accounts*.py       # [改] 账号池配置/默认配置测试
```

**Structure Decision**: 采用既有单一项目结构，按域落位（宪法模块地图）：轮询调度独立 Python 域 `account_round_robin.py`；账号池配置扩账号簿域 `pipeline_exec_accounts.py`；前端多选/限流落 `BrowserAccountsDialog.vue`（超预警抽子组件）；不追加到门面/超限文件。

## Complexity Tracking

无宪法违规，无需填写。
