# Tasks: 纯抓取完成后跳过 AI 直接查看结果

**Input**: Design documents from `/specs/010-scrape-only-view/`

**Prerequisites**: plan.md, spec.md

**Organization**: 按依赖分层：存储 → 服务/API → 前端 → 测试验证。

## File Boundaries

- **Allowed files**: 见 `plan.md` File Boundaries；`webui/store.py` 只允许最新结果白名单两处最小改动；`webui/app.py` 只允许注册薄路由 + AI 完成点 1 行替换；`webui/src/views/DiscoveryView.vue` 只允许最小接线。
- **Forbidden files**: `webui/store_migrations.py`、`webui/source.py`、`webui/pipeline_exec.py`、`webui/pipeline_job_identity.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/store_result_history_mixin.py`、`scripts/boss_cdp_raw.py` 不修改。
- **New files**: `webui/store_scrape_only_mixin.py`、`webui/scrape_only.py`、`tests/test_scrape_only.py`。
- **Reference direction**: 后端 `app.py → scrape_only → store_scrape_only_mixin`；`scrape_only.save_screen_result → store.save_pipeline_result`；前端 `DiscoveryView.vue → api.ts`；`App.vue ← DiscoveryView.vue`（round-status）。
- **Line gate**: 新增 Python 文件 ≤800 行；`store.py`/`app.py`/`DiscoveryView.vue` 各自最小 diff（≤5 / ≤40 / ≤120 行），长逻辑一律进新模块。

## Verification Gate

- 功能交付最终门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务不要求全量测试，按根 `AGENTS.md` 收口规则执行。

## Phase 1: 后端存储层

**Purpose**: `scraped_only` 快照的保存、来源查询与升级写回；所有上层依赖此层。

- [ ] T001 [P] 新增 `webui/store_scrape_only_mixin.py`：`ScrapeOnlyStoreMixin` 提供
  - `save_scraped_only_snapshot(result, script_params, *, started_at, finished_at, execution_config, platform, profile_summary, profile_facts)`：INSERT `screening_runs`（`status='scraped_only'`、`record_kind='result_snapshot'`、计数按无判定口径）+ INSERT `screening_results`（`verdict=''`、`is_dropped=0`），**不写** `screening_pending_results`；返回 run_id。
  - `latest_scraped_only_for_source(source_task_id)`：沿用 `latest_screening_run_for_source` 的"最近 50 条 + Python 侧按 `execution_params.scrape_task_id` 过滤"模式，过滤 `status='scraped_only'`；返回 run dict 或 None。
  - `upgrade_scraped_run(run_id, result, script_params, *, status, execution_config, profile_summary, profile_facts, finished_at)`：UPDATE `screening_runs`（status/各计数/search_params_json/execution_params_json/profile_summary/profile_facts_json/finished_at/updated_at；**created_at/started_at 不动**）；DELETE 该 run 的 `screening_results` 与 `screening_pending_results`；按 AI 结果重插 results/dropped/pending（pending 语义与 `save_pipeline_result` 一致）。
- [ ] T002 修改 `webui/store.py` 最小接线：`TaskStore` 继承 `ScrapeOnlyStoreMixin`；`load_latest_pipeline_result`（无 run_id 分支）与 `load_latest_pipeline_result_for_platform` 的 `status IN ('done','partial')` 加入 `'scraped_only'`，且返回 `status` 字段对 `scraped_only` 原样透传（不归一化为 `completed`）；`get_latest_done_run_id`、`latest_pipeline_result_saved_at`、`load_latest_pipeline_result(run_id)` 分支语义保持不变。
- [ ] T003 新增 `tests/test_scrape_only_store.py`（或并入 T010）：保存后 runs/results 形态（status、verdict 空、pending 表零行）、来源查询命中/未命中、升级后 created_at 不变且 results/pending 重写正确、升级后最新轮白名单可被 `load_latest_pipeline_result_for_platform` 命中。

**Checkpoint**: 存储层聚焦测试通过。

## Phase 2: Service 与 API

**Purpose**: 保存编排与 AI 完成点升级分流。

- [ ] T004 [P] 新增 `webui/scrape_only.py`：service 层，纯函数式编排（不依赖 app.py 内部状态）
  - `build_undecided_result(source_jobs, *, platform, profile_summary, profile_facts)`：把 `load_scrape_run_jobs` 原始岗位规整为无判定 result（platform 回填、字段与 `_build_partial_pipeline_result` 输出同构、`verdict` 留空、`total_scraped/total_kept` 计数、dropped 空）。
  - `save_scrape_snapshot(store, source_jobs, *, platform, profile_summary, profile_facts, script_params, execution_config, started_at, finished_at)`：调 `store.save_scraped_only_snapshot`，返回 `{run_id, result}`；0 岗位返回 `{saved: False}`。
  - `save_screen_result(store, result, script_params, *, scrape_task_id, execution_config, started_at, finished_at, status)`：查 `store.latest_scraped_only_for_source(scrape_task_id)`，命中 → `store.upgrade_scraped_run`（返回同一 run_id）；未命中 → 回退 `store.save_pipeline_result`（现有新建语义，行为不变）。
- [ ] T005 修改 `webui/app.py`（最小两处）：
  - 注册 `POST /api/scrape-result-save`：body `{task_id, profile_summary}`；复用现有 `_ensure_scrape_source` 校验（kind=scrape 且 done）；调 `scrape_only.save_scrape_snapshot`；0 岗位返回 `{ok:True, saved:False}`；任务不存在/非 scrape/未完成返回 404/409（复用现有错误码风格）。
  - AI 筛选完成点：`store.save_pipeline_result(...)` 替换为 `scrape_only.save_screen_result(store, result, {...}, scrape_task_id=..., ...)`；`result["source_run_id"]` 与后续事件记录逻辑保持不变。
- [ ] T006 新增 `tests/test_scrape_only.py`（service 层）：无判定 result 构建（platform 回填/verdict 空/计数）、0 岗位 saved:False、save_screen_result 命中升级（同 run_id、created_at 不变）/未命中新建（新 run_id）；API 层：正常保存、0 岗位、非法 task_id、非 scrape 来源拒绝。

**Checkpoint**: service/API 聚焦测试通过。

## Phase 3: 前端

**Purpose**: 步骤 2 双入口、04 页未筛选展示模式、顶栏口径、历史补筛。

- [ ] T007 修改 `webui/src/discovery.ts`：`historyStatusLabel` 增加 `scraped_only → 已抓取，未筛选`；`RoundStatusPhase` 增加 `"scraped"`。
- [ ] T008 [P] 修改 `webui/src/views/DiscoveryView.vue`（最小接线）：
  - 按钮："继续确认筛选条件"改名"进行确认AI筛选条件"；新增"直接查看结果"（显示条件：`scrapeCompleted && !resultLoaded && !screenBusy`，保证一键链路运行中与已有结果时不出现），点击后：`scraped_count>0` 时调 `POST /api/scrape-result-save`（带 `profile_summary`），成功后 `setPipelineResult` + 记录轮状态 `scraped_only` 并进 04 页；0 岗位直接构造空 result 进 04 页（显示 0，不调保存）。
  - 轮次级展示状态：新增 `currentRoundStatus`（来自保存响应 / `loadLatestResult` 的 raw status / 历史详情 status），`isScrapedOnly` computed；为真时：tabs 收敛为单"待筛选"（展示当前平台筛选范围内的全部岗位，不经过 verdict 分组）、隐藏"补抓 JD"与"全部重抓"与"判定依据"文案、"本轮画像"（含历史模式，未筛选轮不显示画像）。
  - 顶栏：`roundStatusPayload` 在 `isScrapedOnly` 时 `phase:"scraped"`、数量取当前查看范围（与 B035 平台/数字同源语义一致）内的岗位总数。
  - 历史补筛：历史模式且该轮 `status==="scraped_only"` 时显示"开始 AI 筛选"按钮 → 退出历史模式，挂载父任务（`scrape_task_id`/`profile_summary` 取自历史详情）→ `enterScreenStep()`，复用现有 `startAiScreen` 全流程；父任务缺失时提示可读错误。
- [ ] T009 修改 `webui/src/App.vue`：`roundStatusText` 增加 `phase==="scraped"` 分支 → `已抓取 N 个岗位`（scope 语义沿用现有平台/全部/history 规则）。
- [ ] T010 前端测试：扩展 `DiscoveryView.spec.ts`（双按钮出现与改名、直接查看保存成功/0 岗位路径、未筛选模式 tabs/操作区隐藏、顶栏 scraped 上抛、历史补筛入口与父任务挂载）、`discovery.spec.ts`（状态映射）、`App.spec.ts`（顶栏文案）。

**Checkpoint**: 前端主链可独立验证，聚焦测试通过。

## Phase 4: 集成验证与收口

**Purpose**: 全量门禁与回归确认。

- [ ] T011 后端全量测试：`uv run python -m unittest discover tests`。
- [ ] T012 前端测试与构建：`npm test`（或项目现有前端测试命令）与 `npm run build`（dist 需同步提交）。
- [ ] T013 仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`；确认 `git status` 无无关产物。
- [ ] T014 收口（用户确认后）：按根 `AGENTS.md` 收口规则提交（Conventional Commits），README/CHANGELOG 若涉及用户可感知能力变化需同步。

**Checkpoint**: 全部门禁通过，交付可审查。