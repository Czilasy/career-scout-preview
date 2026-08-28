# Implementation Plan: AI 筛选停止/继续/恢复链路统一

**Branch**: `main`（沿用当前分支） | **Date**: 2026-08-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/013-screen-continue-flow/spec.md`

## Summary

本轮把 AI 筛选的“停止/继续/恢复”收敛成一条统一链路：用户暂停后保留断点和 04 可查看的部分结果；04 的“继续 AI 筛选”从断点续跑并完整恢复本轮上下文；失败、结束保存、重启打断和待确认状态都有明确出口；重抓进度迁到 03 展示。后端复用现有断点续筛机制，前端用统一状态派生替换散落的按钮 `v-if`。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript + Vite（前端）

**Primary Dependencies**: Flask、SQLite、Vue 3、Vitest（现有工具链，不新增运行时依赖）

**Storage**: SQLite `screening_runs` / `screening_results` / `screening_pending_results` / `pipeline_checkpoints`；本轮不新增数据库表，不新增 migration。

**Testing**: 后端 `unittest`；前端 `vitest`；构建 `npm run build`；仓库卫生 `uv run python -m unittest tests.test_repo_hygiene`。

**Target Platform**: 本地 Web 工作台 / 桌面 EXE（pywebview）

**Project Type**: 单仓库 Web + 桌面壳应用

**Performance Goals**: 暂停/续跑不新增轮询路径；暂停安全落库沿用现有批次 flush；04 加载部分结果仍走现有结果快照加载，无重复全量 AI 调用。

**Constraints**:

- 只改 AI 筛选链路和重抓进度展示；抓取停止语义、历史轮次入口不动。
- 布局基本不动；按钮增删/合并只在现有位置，具体位置调整需用户确认。
- 已冻结六类条件不得因恢复失败丢失；恢复不到时阻断继续，禁止以空条件发起初筛（2993 回归）。
- 续跑只处理未判定/待确认/未完成岗位，已判定结果保留，不重跑整批。
- 暂停必须安全：当前批次落库后再进入暂停态。
- 不新增“换条件重配”功能；条件锁定。
- 超大文件不追加新业务逻辑，只做最小接线；新逻辑落新模块/composable。

**Scale/Scope**: 后端涉及 AI 筛选暂停/续跑候选和上下文透传，前端涉及 03/04 主动作统一和恢复回填。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- 职责分层：路由/API 只做接线；暂停编排、续跑候选、上下文构建放 `webui/screen_flow.py`；数据访问放 `webui/store_screen_resume_mixin.py`；前端状态机放 composable/pure module。
- 单文件尺寸：`webui/app.py`、`webui/store.py`、`DiscoveryView.vue` 均为超大文件，本轮只允许最小接线或净删除，不新增业务函数。
- 引用方向：后端 `app.py → screen_flow.py → store mixin/store.py`；前端 `DiscoveryView.vue → useScreenRoundFlow.ts → screenFlow.ts / api.ts`；组件只依赖 pure module。
- 拆分纪律：新逻辑不追加进超大文件；如需复用 app.py 内部部分结果构建，只在现有闭包内调用，不搬动既有函数。
- 验证门禁：最终按功能交付全量门禁执行。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. 按用户“生成到 tasks 结束”的指令未单独停下确认；本清单以冻结需求、宪法和已读代码为准，如边界需调整可在 tasks 完成后修订。*

- **Allowed files**:
  - `webui/screen_flow.py`（新增）：暂停编排、续跑候选选择、完整上下文构建、快照 script_params 合并。
  - `webui/store_screen_resume_mixin.py`（新增）：JD 回退读取、可续跑 run 查询、上下文读取。
  - `webui/store.py`：仅挂载新 mixin，并放宽 `interrupted + user_finished` 的元数据更新守卫；增量 ≤10 行。
  - `webui/scrape_only.py`：新增 script_params 合并辅助；增量 ≤30 行。
  - `webui/app.py`：新增暂停路由、`ai-screen` 续跑候选扩展、`latest-running-task`/`latest-pipeline-result` 透传 `round_context`、`_run_ai_screen_task` 内暂停分支；增量 ≤160 行。
  - `webui/src/types.ts`：`RoundContext` 相关类型；增量 ≤40 行。
  - `webui/src/screenFlow.ts`（新增）：纯函数状态派生、上下文归一化、多平台续跑目标选择。
  - `webui/src/composables/useScreenRoundFlow.ts`（新增）：恢复上下文、暂停/继续/重抓动作、按钮反馈与导航回调。
  - `webui/src/components/ScreenRoundActions.vue`（新增）：03/04 主动作按钮组，替换散落 `v-if`。
  - `webui/src/views/DiscoveryView.vue`：只做接线替换，净减少行数，保持现有页面布局；不新增业务函数。
  - `webui/src/styles.css`：按钮反馈/平台选择所需少量样式；增量 ≤30 行。
  - 测试：`tests/test_screen_flow.py`、`tests/test_store_screen_resume.py`、`tests/test_webui_app.py`、`webui/src/__tests__/screenFlow.spec.ts`、`webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`、`webui/src/components/__tests__/ScreenRoundActions.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`、`DiscoveryScrapeOnly.spec.ts`、`DiscoveryHistoryMode.spec.ts`。
- **Forbidden files**:
  - `scripts/*`、`webui/source.py`、`webui/pipeline_exec.py`、`webui/error_registry.py`、`webui/store_migrations.py`、`webui/ai*.py`、`webui/tuning.py`、`webui/result_history*.py`、`webui/src/components/TaskProgress.vue`。
- **New files**:
  - `webui/screen_flow.py`：后端 AI 筛选暂停/续跑/上下文编排，预计 250-350 行。
  - `webui/store_screen_resume_mixin.py`：store 域数据访问，预计 120-200 行。
  - `webui/src/screenFlow.ts`：前端纯状态机与上下文归一化，预计 200-320 行。
  - `webui/src/composables/useScreenRoundFlow.ts`：前端可复用动作与恢复逻辑，预计 250-350 行。
  - `webui/src/components/ScreenRoundActions.vue`：03/04 主动作按钮组件，预计 150-250 行。
- **Reference direction**:
  - 后端：`app.py → screen_flow.py → store_screen_resume_mixin.py / store.py`；`scrape_only.py` 仅提供纯函数，不反向依赖 app。
  - 前端：`DiscoveryView.vue → useScreenRoundFlow.ts → screenFlow.ts / api.ts`；`ScreenRoundActions.vue → screenFlow.ts`；组件不直接持有后端请求。
- **Line gate**: `app.py` 增量 ≤160；`store.py` 增量 ≤10；`DiscoveryView.vue` 净减少；`scrape_only.py` 增量 ≤30；`types.ts` 增量 ≤40；`styles.css` 增量 ≤30；新文件不超过宪法单文件上限。
- **Rationale**: `app.py`、`store.py`、`DiscoveryView.vue` 均已超尺寸，新逻辑必须落到新模块；后端暂停/续跑需要复用 app.py 内部已有部分结果构建与 worker 停止机制，因此只允许最小接线，不搬动既有闭包。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 外部收口动作不适用本门禁；按根 `AGENTS.md` 收口规则另行执行。

## Project Structure

### Documentation (this feature)

```text
specs/013-screen-continue-flow/
├── spec.md                # 需求与验收
├── research.md            # Phase 0 决策
├── data-model.md          # 实体与状态
├── contracts/             # 接口契约
│   └── screen-resume-flow.md
├── quickstart.md          # 验证指南
├── plan.md                # This file
├── tasks.md               # Phase 2 output
└── checklists/requirements.md
```

### Source Code (repository root)

```text
webui/
├── screen_flow.py                    # 暂停编排/续跑候选/上下文构建
├── store_screen_resume_mixin.py      # 可续跑查询/JD 回退/上下文读取
├── store.py                          # 仅 mixin 挂载与守卫放宽
├── scrape_only.py                    # script_params 合并纯函数
├── app.py                            # pause 路由 + ai-screen 候选扩展 + round_context 透传
└── src/
    ├── types.ts                      # RoundContext 类型
    ├── screenFlow.ts                 # 前端状态派生纯函数
    ├── composables/useScreenRoundFlow.ts
    ├── components/ScreenRoundActions.vue
    └── views/DiscoveryView.vue       # 最小接线，净减少

tests/
├── test_screen_flow.py
├── test_store_screen_resume.py
└── test_webui_app.py

webui/src/
├── __tests__/screenFlow.spec.ts
├── composables/__tests__/useScreenRoundFlow.spec.ts
├── components/__tests__/ScreenRoundActions.spec.ts
└── views/__tests__/DiscoveryView.spec.ts
```

**Structure Decision**: 沿用现有单仓库结构；新增后端 service/store mixin、前端 pure module/composable/component，超大文件只做接线。

## Complexity Tracking

> 无宪法违规；不填复杂度表。
同时保持现有页面布局：按钮增删/合并只在现有位置；已冻结的六类条件在恢复后必须随初筛携带，禁止无条件下发导致 2993 全量保留回归。
