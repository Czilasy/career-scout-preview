# Implementation Plan: 纯抓取完成后跳过 AI 直接查看结果

**Branch**: `main`（用户决定本分支实施） | **Date**: 2026-08-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/010-scrape-only-view/spec.md`

## Summary

B038 本质是"跳过 AI 筛选"：抓取自然完成后，步骤 2 出现"进行确认AI筛选条件 / 直接查看结果"两个入口；"直接查看结果"把本轮抓取岗位固化为历史轮（机器状态 `scraped_only`，即"已抓取，未筛选"），04 页以"待筛选"单列表展示。历史中可对该轮发起 AI 筛选，完成后升级同一轮次（不新建历史记录）。实现策略：新增存储 mixin 与 service 模块承载保存/升级/查询（不向超大 `store.py`/`app.py` 追加业务逻辑），`store.py` 仅做最新结果白名单最小接线，前端在现有步骤 2 按钮区加入口、按轮次级状态切换 04 页展示模式。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript + Vite（前端）

**Primary Dependencies**: Flask、SQLite、Vue 3、Vitest（现有工具链）

**Storage**: SQLite；不新增列、不新增 migration——`scraped_only` 只是 `screening_runs.status` 的新值。

**Testing**: 后端 `unittest`；前端 `vitest`；构建 `npm run build`；仓库卫生 `uv run python -m unittest tests.test_repo_hygiene`。

**Target Platform**: 本地 Web 工作台 / 桌面 EXE（pywebview）

**Project Type**: 单仓库 Web + 桌面壳应用

**Performance Goals**: 保存/升级只读写单轮数据，不引入额外查询面；升级查询沿用 `latest_screening_run_for_source` 的"最近 50 条 + Python 侧过滤"模式。

**Constraints**: 不扩大超大文件；`webui/store.py` 只允许最新结果白名单两处最小改动；`webui/app.py` 只允许注册 1 个薄路由 + AI 完成点 1 行替换；`webui/store_migrations.py`、`webui/source.py`、`webui/pipeline_exec.py`、`scripts/boss_cdp_raw.py` 不修改。

**Scale/Scope**: 单用户本地工具；单轮岗位量级与现有抓取一致。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- 职责分层：存储访问进新 `webui/store_scrape_only_mixin.py`，业务编排进新 `webui/scrape_only.py`，路由在 `webui/app.py` 仅注册；前端展示模式由视图状态驱动，不加新组件层级。
- 单文件尺寸：新增文件预计低于 800 行 Python；现有超大文件只做最小接线（`store.py` ≤5 行、`app.py` ≤40 行）。
- 引用方向：`app.py → scrape_only → store mixin`；前端 `App.vue ← DiscoveryView.vue`（上抛 round-status）。
- 拆分纪律：本 Spec 是功能交付，不是重构 Spec；`store.py` 仅做白名单接线，不搬逻辑。
- 验证门禁：最终按功能交付全量门禁执行。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. User confirmed 2026-08-12 after review.*

- **Allowed files**:
  - `webui/store.py`：仅 `load_latest_pipeline_result` 与 `load_latest_pipeline_result_for_platform` 两处查询的状态白名单加入 `'scraped_only'`，且返回 `status` 对该值原样透传（不归一化为 `completed`）；不新增业务方法。
  - `webui/app.py`：注册 1 个薄路由 `POST /api/scrape-result-save`（参数校验 + 复用现有 `_ensure_scrape_source` 校验来源 + 调 service）；AI 筛选完成点的 `store.save_pipeline_result(...)` 替换为 `scrape_only.save_screen_result(...)` 一行；不新增其它业务实现。
  - `webui/src/views/DiscoveryView.vue`：仅最小接线——按钮改名与新增"直接查看结果"、保存调用、轮次级展示状态、历史补筛入口、`roundStatusPayload` scraped 分支；长逻辑进新模块或复用现有函数，不新增长实现。
  - `webui/src/discovery.ts`：`historyStatusLabel` 增加 `scraped_only` 映射；`RoundStatusPhase` 增加 `"scraped"`。
  - `webui/src/composables/resultHistory.ts`：仅 `HistoryRoundDetail` 增加 `scrape_task_id?` 类型字段（补筛入口读取父任务）。
  - `webui/src/App.vue`：`roundStatusText` 增加 `phase==="scraped"` 分支（"已抓取 N 个岗位"）。
- **Forbidden files**:
  - `webui/store_migrations.py`、`webui/source.py`、`webui/pipeline_exec.py`、`webui/pipeline_job_identity.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/store_result_history_mixin.py`、`scripts/boss_cdp_raw.py`：不修改。
  - `webui/store.py` 不允许追加长业务方法；`webui/app.py` 不允许做白名单接线以外的逻辑堆积。
- **New files**:
  - `webui/store_scrape_only_mixin.py`（约 200 行）：`save_scraped_only_snapshot`（写 runs + results，不写 pending）、`latest_scraped_only_for_source`（最近 50 条 + Python 侧按 `execution_params.scrape_task_id` 过滤）、`upgrade_scraped_run`（UPDATE runs 且 `created_at` 不动；DELETE 并重插 results/pending）。
  - `webui/scrape_only.py`（约 180 行）：`save_scrape_snapshot`（构建无判定 result + 调 mixin 保存，返回 run_id/result）、`save_screen_result`（AI 完成后选升级或新建，复用 `store.save_pipeline_result` 新建路径）。
  - 测试文件：`tests/test_scrape_only.py`、`webui/src/views/__tests__/DiscoveryView.spec.ts`（扩展）、`webui/src/__tests__/discovery.spec.ts`（扩展）、`webui/src/__tests__/App.spec.ts`（扩展）。
- **Reference direction**:
  - 后端：`app.py → scrape_only → store_scrape_only_mixin`；`scrape_only.save_screen_result → store.save_pipeline_result`（新建回退路径）。
  - 前端：`DiscoveryView.vue → api.ts`；`App.vue ← DiscoveryView.vue`（round-status 上抛）；`discovery.ts` 纯函数供视图与测试共用。
- **Line gate**: `store.py`/`app.py`/`DiscoveryView.vue` 已超限；本次各自只允许最小 diff（≤5 / ≤40 / ≤120 行），新逻辑全部落新模块。
- **Rationale**: 新增机器状态跨存储、服务、路由、前端四层，但改动面窄；按宪法拆新模块避免继续膨胀大文件，同时把 `save_pipeline_result` 的既有新建语义与升级语义隔离。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）。
- 只有 Spec 明确写入或用户明确要求时，收口任务才执行全量测试。

## Project Structure

### Documentation (this feature)

```text
specs/010-scrape-only-view/
├── spec.md     # 需求与验收（本目录当前内容）
├── plan.md     # This file
└── tasks.md    # Phase 2 output
```

### Source Code (repository root)

```text
webui/
├── app.py                               # 注册薄路由 + AI 完成点 1 行替换
├── store.py                             # 最新结果白名单 2 处最小接线
├── store_scrape_only_mixin.py           # 新增：保存/升级/来源查询
├── scrape_only.py                       # 新增：编排 service
└── src/
    ├── discovery.ts                     # 状态映射 + phase 类型
    ├── views/DiscoveryView.vue          # 按钮/模式/补筛入口（最小接线）
    └── App.vue                          # 顶栏"已抓取 N 个岗位"
```

**Structure Decision**: 沿用 008 的 mixin + service 分层模式（`store_result_history_mixin.py` / `result_history.py` 先例），避免向超大文件追加。

## Complexity Tracking

> 无宪法违规；不填复杂度表。