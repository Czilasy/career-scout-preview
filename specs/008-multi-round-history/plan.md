# Implementation Plan: 多轮结果历史与稳定性整修

**Branch**: `008-multi-round-history` | **Date**: 2026-08-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/008-multi-round-history/spec.md`

## Summary

本轮以 B010 多轮结果历史为主，附带 B034（AI 重试）、B036（智联误报封禁）、B037（智联继续身份）、B015（无障碍播报）、B035（顶栏语义）与顶栏分层。核心实现策略：复用现有 `screening_runs/screening_results` 快照，新增 `archived_at` 归档标记；失败/中断/取消但有岗位的结束路径补保存快照；历史列表、详情、删除、保留清理放入独立服务与路由；前端新增历史抽屉、历史模式与设置菜单；AI 重试策略抽到独立模块；智联风险信号与继续身份分别做小范围修复。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript + Vite（前端）

**Primary Dependencies**: Flask、SQLite、Vue 3、Vitest、Playwright（现有工具链）

**Storage**: SQLite，数据库路径由 `app.config["DB_PATH"]` 提供；新增 schema migration 030 增加 `screening_runs.archived_at`。

**Testing**: 后端 `unittest`；前端 `vitest`；构建 `npm run build`；仓库卫生 `uv run python -m unittest tests.test_repo_hygiene`。

**Target Platform**: 本地 Web 工作台 / 桌面 EXE（pywebview）

**Project Type**: 单仓库 Web + 桌面壳应用

**Performance Goals**: 历史列表只返回元数据，不加载全量岗位；单轮详情按需加载；30 轮上限保证数据量有界。

**Constraints**: 不扩大超大文件；`webui/store.py` 只允许最小 mixin 继承与 `archived_at IS NULL` 过滤；`webui/store_migrations.py` 只允许 migration 030；`webui/source.py`、`webui/pipeline_exec.py`、`scripts/boss_cdp_raw.py` 不修改。

**Scale/Scope**: 单用户本地工具，历史轮次按平台最多 30 轮。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- 职责分层：历史数据访问进 `webui/store_result_history_mixin.py`，业务进 `webui/result_history.py`，路由进 `webui/result_history_api.py`；前端视图/组件/composable 分层。
- 单文件尺寸：新增文件预计均低于 800 行 Python / 1200 行 Vue；现有超大文件不追加业务实现。
- 引用方向：`app.py → result_history_api → result_history → store mixin`；前端 `App.vue → DiscoveryView → composable/resultHistory.ts → api.ts`。
- 拆分纪律：本 Spec 是功能交付，不是重构 Spec；`store.py` 仅做最小接线，不搬逻辑。
- 验证门禁：最终按功能交付全量门禁执行。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. User confirmed 2026-08-11 after review.*

- **Allowed files**:
  - `webui/store_migrations.py`：仅新增 migration 030（`screening_runs.archived_at`）。
  - `webui/store.py`：仅允许 TaskStore 继承 `ResultHistoryStoreMixin`，以及最新结果查询增加 `archived_at IS NULL`；不新增业务逻辑。
  - `webui/app.py`：注册历史路由、替换两处“清最新”调用为归档调用、B037 继续路径接入 helper、筛选任务失败/中断/取消结束但已生成含岗位结果时保存快照、保存新结果后触发保留清理；不新增其它业务实现。
  - `webui/ai.py`：默认重试策略改用 `webui/ai_retry.py`，删除默认路径的 timeout 预算提前放弃逻辑。
  - `scripts/zhilian_cdp_raw.py`：`_risk_signal` 增加 Chrome 错误页 URL 判定。
  - `webui/src/App.vue`、`webui/src/views/DiscoveryView.vue`、`webui/src/discovery.ts`、`webui/src/components/TaskProgress.vue`：按对应用户故事修改。
  - 新增文件与测试文件见下。
- **Forbidden files**:
  - `webui/source.py`、`webui/pipeline_exec.py`、`webui/pipeline_job_identity.py`、`scripts/boss_cdp_raw.py`：不修改。
  - `webui/store.py` 不允许追加长业务方法；`webui/store_migrations.py` 不允许做非 030 的其它结构改动。
- **New files**:
  - `webui/store_result_history_mixin.py`（约 240 行）：历史查询、归档、保留删除、删除最新后归档回退提升的 SQLite 数据访问 mixin。
  - `webui/result_history.py`（约 280 行）：历史服务，负责元数据组装、详情（原始状态保真）、归档、删除（含最新回退）、30 轮清理。
  - `webui/result_history_api.py`（约 220 行）：Flask blueprint 与稳定错误映射。
  - `webui/ai_retry.py`（约 140 行）：AI 默认重试计划与调优覆盖。
  - `webui/resume_identity.py`（约 150 行）：继续任务冻结身份解析、写回与缓存失效。
  - `webui/src/composables/resultHistory.ts`（约 220 行）：历史状态与 API。
  - `webui/src/components/ResultHistoryDrawer.vue`（约 380 行）：历史列表、打开、删除确认。
  - `webui/src/components/HistoryRoundProfile.vue`（约 100 行）：完整画像展开块。
  - `webui/src/components/AppSettingsMenu.vue`（约 220 行）：设置菜单。
  - 测试文件：`tests/test_result_history.py`、`tests/test_ai_retry.py`、`tests/test_zhilian_risk_signal.py`、`tests/test_resume_continue.py`、`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`、`webui/src/composables/__tests__/resultHistory.spec.ts`、`webui/src/components/__tests__/AppSettingsMenu.spec.ts`、`webui/src/components/__tests__/TaskProgress.spec.ts`。
- **Reference direction**:
  - 后端：`app.py → result_history_api → result_history → store_result_history_mixin`；`app.py → resume_identity`；`ai.py → ai_retry`。
  - 前端：`App.vue / DiscoveryView.vue → composable/resultHistory.ts → api.ts`；组件只接收 props/emit，不反向依赖视图实现。
- **Line gate**: 修改后的 `webui/store.py` 仍远低于 800 行？否，store.py 已超限；本次只允许最小 diff，不新增大段逻辑。`DiscoveryView.vue` 仍超 1200 行；本次只允许接线和受控状态，不新增长实现，长逻辑全部进 composable/组件。
- **Rationale**: 历史功能跨存储、服务、路由、前端多面，必须按宪法拆新模块；B034/B036/B037 是行为修复，分别落在新策略模块与最小脚本/路由修改，避免继续膨胀大文件。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）。
- 只有 Spec 明确写入或用户明确要求时，收口任务才执行全量测试。

## Project Structure

### Documentation (this feature)

```text
specs/008-multi-round-history/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md  # Spec quality checklist
├── HANDOFF.md           # 实施交接提示词
├── contracts/
│   ├── http-api.md
│   └── ui-interaction.md
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
webui/
├── app.py                              # 注册与最小接线
├── store.py                            # 仅 mixin 继承 + archived_at 过滤
├── store_migrations.py                 # migration 030
├── store_result_history_mixin.py       # 新增
├── result_history.py                   # 新增
├── result_history_api.py               # 新增
├── ai_retry.py                         # 新增
├── resume_identity.py                  # 新增
└── src/
    ├── composables/resultHistory.ts    # 新增
    └── components/
        ├── ResultHistoryDrawer.vue     # 新增
        ├── HistoryRoundProfile.vue     # 新增
        └── AppSettingsMenu.vue         # 新增
```

**Structure Decision**: 沿用现有 `webui/` 模块化约定；新增独立 service/store/api/composable/component，避免继续向超大文件追加。

## Complexity Tracking

> 无宪法违规；不填复杂度表。
