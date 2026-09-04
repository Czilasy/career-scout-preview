# Implementation Plan: 多账号轮询分摊抓取（B091 V2）

**Branch**: `038-multi-account-round-robin` | **Date**: 2026-09-04 | **Spec Version**: `v2` | **Supersedes**: `v1` | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/038-multi-account-round-robin/v2/spec.md`

## Summary

V2 继承 v1 的四块多账号能力，并增加第五块架构与验收补充：

1. **多账号轮询分摊抓取（核心）**：账号弹窗 R1/R2 从"角色→单账号互斥单选"改为"共用账号池多选"；开抓后串行按轮询（round-robin）分摊到选中账号，每轮每账号只抓固定配额（R1 按页 1–50 默认 25、R2 按 JD 条 1–200 默认 100）就换下一个，总量不够自动多轮覆盖；顺序按勾选顺序，第二轮从 1 号开始；末轮零头由下一个账号自然抓完。
2. **撞墙顺次换预选账号兜底**：撞硬阻断（验证码/限流等）自动在预选池顺次切下一个继续，剩余份额接力不丢；复用 B057 现有换号逻辑，只把取号范围从"系统全池自动挑"限定为"用户预选池顺次切"。
3. **全撞完即停 + 限流视觉标识**：所有预选账号撞墙走现有"暂停"；撞墙账号在账号列表项字体变红 + 后缀"限流"方框，不新增报错字段/弹窗/文案。
4. **默认零配置**：登录账号默认全进 R1/R2 池，新增账号自动加入，默认全选，每账号带默认配额；全取消阻止开抓。
5. **白箱接入**：复用现有任务事件持久化与按任务查看能力，在 038 多账号轮询调度 seam 记录账号池快照、分配段、正常配额轮换、撞墙接管和任务终态；不另建白箱产品或独立查看台。

技术路径：轮询分摊调度域 `webui/account_round_robin.py` 负责配额分摊/多轮覆盖/顺序/末轮零头/撞墙换号接力编排；V2 由独立的 `webui/account_round_robin_observability.py` 负责白箱安全摘要，R1/R2 只通过调度 seam 触发记录，复用既有 `task_logs`；R1 执行接线把正式任务的 `TaskStore` 传入调度域；账号池配置扩 `pipeline_exec_accounts.py`（345 行有余量），账号簿 schema 变（角色→单账号互斥 → 多账号池 + 配额 + 全选标记），旧配置不兼容全删；撞墙换号取号范围改 `resume_identity.py`（复用 B057）；前端 `BrowserAccountsDialog.vue`（863 逼近 Vue 900 预警）改多选 + 限流标识，净增控制，超则抽 `AccountPoolSelector.vue` 子组件；默认配置在账号簿读写层落地。

## Technical Context

**Language/Version**: Python 3.11+（后端）/ TypeScript + Vue 3（前端）

**Primary Dependencies**: 既有 webui pipeline 执行栈（`pipeline_exec_*`）/ Vue 3 组合式 API

**Storage**: v1 的账号池 schema 保持；白箱复用既有 `task_logs` 结构化任务事件，不新增数据库表；旧账号配置不兼容，全删，新 schema 直接上（不走数据迁移保留旧数据）

**Testing**: 后端 unittest（聚焦 `tests/test_account_round_robin.py` 等 + 全量）+ 前端 Vitest

**Target Platform**: BOSS + 智联两平台通用（B091 通用功能）

**Project Type**: 桌面应用本地 Web 工作台

**Performance Goals**: 轮询调度不引入额外延迟；分摊降风控为主，不追求提速

**Constraints**: `BrowserAccountsDialog.vue`（863 逼近 Vue 900 预警）、`settings_api.py`（569 逼近 600）、`pipeline_exec_search.py`（549 逼近 600）、`pipeline_exec_details.py`（538 逼近 600）均不得继续往里塞新逻辑，须分流新模块；门面文件（`pipeline_exec.py` / `app.py` / `store.py` / `source.py` / `boss_cdp_raw.py` / `zhilian_cdp_raw.py` / `task_runners.py` / `historical_recovery.py`）禁止加逻辑；`DiscoveryView.vue`（1249 超限）禁止改

**Scale/Scope**: 单用户桌面工具；V2 在 v1 基础上只增加 038 轮询白箱记录与按任务核对能力，不重写抓取栈、不动门面、不改 DiscoveryView、不新增独立白箱控制台

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **职责分层（I）**：通过。轮询调度落 `webui/account_round_robin.py` 独立域（纯逻辑），白箱摘要落 `account_round_robin_observability.py`，R1/R2 执行域只接线；账号池配置落 `pipeline_exec_accounts.py`（账号簿域）；前端多选/限流标识落 `BrowserAccountsDialog.vue`（或抽组件）；符合 api→service→store 与 view→component→composable 分层。
- **单文件尺寸（II）**：通过。`account_round_robin.py` 新建控 ~250 行；`pipeline_exec_search.py` / `pipeline_exec_details.py` / `settings_api.py` 均逼近 600 预警，新逻辑分流到 `account_round_robin.py`，执行域只接线净增 ≤30 行；`BrowserAccountsDialog.vue` 863 逼近 900 预警，多选+限流标识净增控制，超则抽 `AccountPoolSelector.vue` 子组件。
- **引用方向（III）**：通过。`pipeline_exec_search.py` / `pipeline_exec_details.py` → `account_round_robin.py`（单向）；`account_round_robin.py` → `resume_identity.py`（复用换号，单向）；前端 view→component→composable→api client。
- **拆分纪律（IV）**：通过。非重构 Spec，不拆既有大文件；新增调度域为新文件。
- **验证门禁（V）**：通过。Verification Gate 章节明确功能交付门禁（聚焦测试 + 后端全量 + 前端 + `npm run build` + 仓库卫生），用户端到端真跑交付后由用户执行；不涉及版本/打包/发布。
- **模块地图（VI）**：新增 `account_round_robin_observability.py` 与 R1 正式任务事件接线需在同一批次内登记进宪法模块地图。
- **错误处理与可观测性（VII）**：白箱沿用 `task_logs` 结构化事件与统一任务关联；轮询正常切换和撞墙接管都必须留痕；白箱写入失败不得伪造事实或静默吞掉，主流程保持既有错误处理口径。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`.*

- **Allowed files**:
  - `webui/account_round_robin.py`（既有轮询分摊调度域；V2 接入白箱事件）
  - `webui/account_round_robin_observability.py`（V2 白箱安全摘要适配器）
  - `webui/pipeline_exec_search.py`（仅接线：R1 调用调度域分摊页数）
  - `webui/runners/pipeline_task.py`（仅接线：把正式任务 TaskStore 传入 R1，并补齐抓取终态摘要）
  - `webui/pipeline_exec_details.py`（仅接线：R2 调用调度域分摊 JD）
  - `webui/pipeline_exec_accounts.py`（账号池配置 schema + 默认全进池/全选/默认配额）
  - `webui/resume_identity.py`（撞墙换号取号范围限定到预选池，复用 B057；V2 扩展安全切换摘要）
  - `webui/src/components/BrowserAccountsDialog.vue`（多选 UI + 限流视觉标识，净增控制）
  - `webui/src/composables/useDiscoveryExecution.ts`（如需：开抓前校验至少 1 账号选中）
  - `webui/src/api.ts`（账号池配置接口类型扩展，如需）
  - `webui/settings_api.py`（账号池配置读写端点，仅端点，净增控制；逻辑落 `pipeline_exec_accounts.py`）
  - `webui/store_runs.py`（如需：复用或扩展既有结构化任务事件写入，不新增存储表）
  - `webui/log_api.py`（如需：保证已结束 038 任务可按任务读取既有事件）
  - `tests/test_account_round_robin.py`（既有轮询聚焦测试，V2 增加白箱断言）
  - `tests/test_logging_whitebox.py`（038 白箱事件安全与失败留痕测试，如复用既有测试文件）
  - `tests/test_log_api.py`（已结束任务按任务读取白箱事件测试，如需）
  - `tests/test_pipeline_exec_accounts.py` 或既有账号簿测试（账号池配置/默认配置测试）
  - `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`（多选/限流标识测试）
  - `.specify/memory/constitution.md`（模块地图登记）
- **Forbidden files**: `webui/pipeline_exec.py`（门面）、`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`webui/historical_recovery.py`、`webui/src/views/DiscoveryView.vue`（1249 超限）、数据库迁移文件（旧配置不兼容全删，不走迁移）、`roadmap/`、`.codebuddy/`
- **New files**:
  - `webui/account_round_robin_observability.py`（V2 白箱安全摘要适配器；不新增白箱产品或数据表）
  - 无新增数据库表；如新增测试文件，仅限白箱事件的聚焦测试。
  - `webui/src/components/AccountPoolSelector.vue`（条件新，仅当 `BrowserAccountsDialog.vue` 净增超 900 预警时抽） — 多选 + 配额输入 + 限流标识子组件，~150 行
- **Reference direction**: 后端 `pipeline_exec_search.py` / `pipeline_exec_details.py` → `account_round_robin.py` → `resume_identity.py`（单向）；`settings_api.py` → `pipeline_exec_accounts.py`；前端 `view → component → composable → api client`；调度域不 import 前端
- **V2 whitebox direction**: `account_round_robin.py` → `account_round_robin_observability.py` → 既有任务事件写入 seam（`store_runs.py`）→ `log_api.py`/`LogViewerDialog.vue`；白箱事件只携带安全摘要，不反向依赖前端，不新增独立白箱产品层
- **Line gate**: `pipeline_exec_search.py` 净增后 ≤580（超 560 则 R1 接线逻辑进一步外移到调度域）；`pipeline_exec_details.py` 净增后 ≤570；`settings_api.py` 净增后 ≤590（端点逻辑外移到 `pipeline_exec_accounts.py`）；`BrowserAccountsDialog.vue` 净增后 ≤950（超 900 预警则抽 `AccountPoolSelector.vue`）
- **Rationale**: 轮询分摊是独立调度域，白箱接入也落在该调度 seam，避免 `pipeline_exec_search/details` 继续膨胀；任务事件和按任务读取已存在，优先复用而不是新增白箱平台；账号池配置属账号簿域扩 `pipeline_exec_accounts.py`（345 有余量）避免 `settings_api.py` 继续塞逻辑；前端多选+限流标识如净增大则抽组件避免 `BrowserAccountsDialog.vue` 超 900 预警；不追加到门面/超限文件

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付最终门禁：v1 聚焦测试 + 白箱事件聚焦测试（账号池快照、R1/R2 分配段、正常配额轮换、撞墙接管、终态、敏感信息排除）+ 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 用户端到端真跑：在小规模真实任务中验证 R1 每页切换、R2 每 10 条切换，并按任务查看白箱记录；同时保留 v1 的多账号、撞墙、默认配置和 BOSS/智联范围。
- 不涉及版本提升/打包/发布；BACKLOG B091 标题"BOSS 任务"误写需同步修正（改为通用），README 是否需同步实现收敛后检查。

## Project Structure

### Documentation (this feature)

```text
specs/038-multi-account-round-robin/v2/
├── spec.md              # 需求规格（继承 v1，增加白箱接入）
├── plan.md              # 本文件
├── tasks.md             # V2 执行任务
├── changes.md           # V2 相对 v1 的变化
└── checklists/
    └── requirements.md  # Spec 质量检查
```

### Source Code (repository root)

```text
webui/
├── account_round_robin.py          # [新] 轮询分摊调度域
├── account_round_robin_observability.py # [新] 轮询白箱安全摘要适配器
├── pipeline_exec_search.py         # [改] R1 接线调用调度域
├── pipeline_exec_details.py        # [改] R2 接线调用调度域
├── pipeline_exec_accounts.py       # [改] 账号池配置 schema + 默认配置
├── resume_identity.py              # [改] 撞墙换号取号范围限定预选池 + 安全切换摘要
├── runners/
│   └── pipeline_task.py            # [改] R1 TaskStore 接线 + 抓取终态摘要
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

**Structure Decision**: 保持 038 单一主体，按域落位（宪法模块地图）：轮询调度位于 `account_round_robin.py`，白箱安全摘要位于其配套适配器并从调度 seam 触发；结构化任务事件复用 `store_runs.py`；账号池配置位于 `pipeline_exec_accounts.py`；前端多选/限流仍位于 `BrowserAccountsDialog.vue`（超预警抽子组件）；不追加到门面/超限文件，不新建独立白箱产品模块。

## Complexity Tracking

无宪法违规，无需填写。
