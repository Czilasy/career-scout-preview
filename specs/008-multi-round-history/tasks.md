# Tasks: 多轮结果历史与稳定性整修

**Input**: Design documents from `/specs/008-multi-round-history/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Organization**: 按用户故事分阶段；基础层完成后各故事可并行。

## File Boundaries

- **Allowed files**: 见 `plan.md` File Boundaries；`webui/store.py` 只允许 mixin 继承与 `archived_at IS NULL` 过滤；`webui/store_migrations.py` 只允许 migration 030；`webui/app.py` 只允许注册/接线/小修/失败与中断有岗位时保存快照。
- **Forbidden files**: `webui/source.py`、`webui/pipeline_exec.py`、`webui/pipeline_job_identity.py`、`scripts/boss_cdp_raw.py` 不修改。
- **New files**: `webui/store_result_history_mixin.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/ai_retry.py`、`webui/resume_identity.py`、`webui/src/composables/resultHistory.ts`、`webui/src/components/ResultHistoryDrawer.vue`、`webui/src/components/HistoryRoundProfile.vue`、`webui/src/components/AppSettingsMenu.vue`。
- **Reference direction**: 后端 `app.py → result_history_api → result_history → store mixin`；前端 `view → composable → api.ts`；`ai.py → ai_retry.py`；`app.py → resume_identity.py`。
- **Line gate**: 新增 Python 文件 ≤800 行、Vue ≤1200 行；不向超大文件追加长逻辑。

## Verification Gate

- 功能交付最终门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务不要求全量测试，按根 `AGENTS.md` 收口规则执行。

## Phase 1: Setup & Foundational（共享基础）

**Purpose**: 迁移、历史数据访问、历史服务与 API 骨架；所有用户故事依赖此层。

- [ ] T001 在 `webui/store_migrations.py` 的 `_migrate()` 迁移序列注册 migration 030：给 `screening_runs` 加可空 `archived_at TEXT`（store.py 已通过 `_migrate` 调用，无需改初始化顺序）
- [ ] T002 [P] 新增 `webui/store_result_history_mixin.py`：`ResultHistoryStoreMixin` 提供 `list_history_rounds`、`archive_all_current_results`、`delete_history_result_preserving_logs`、`prune_result_history`、`history_round_exists`；删除最新未归档轮后若该平台无未归档快照，将最近归档轮回退为最新
- [ ] T003 修改 `webui/store.py` 最小接线：`TaskStore` 继承 `ResultHistoryStoreMixin`；`load_latest_pipeline_result`、`load_latest_pipeline_result_for_platform`、`get_latest_done_run_id`、`latest_pipeline_result_saved_at` 的查询增加 `archived_at IS NULL`
- [ ] T004 新增 `webui/result_history.py`：历史列表元数据组装（画像只返回截断摘要 `profile_summary_preview`）；单轮详情复用 `store.load_latest_pipeline_result(run_id)` 加载岗位明细，但 `status` 必须读取 `screening_runs` 原始机器值覆盖，禁止沿用归一化 `completed`/`completed_with_pending`；归档全部当前结果；删除保留任务日志并执行最新回退；30 轮保留清理
- [ ] T005 [P] 新增 `webui/result_history_api.py`：`GET /api/result-history`、`GET /api/result-history/<run_id>`、`POST /api/result-history/archive-latest`、`DELETE /api/result-history/<run_id>`，提供 `register_result_history_routes`
- [ ] T006 修改 `webui/app.py`：注册 `register_result_history_routes(app, store)`；`/api/reset-latest-result` 无 run_id 改为归档、有 run_id 改为保留日志删除；两处 `save_pipeline_result` 成功后调用 `prune_retention`
- [ ] T007 新增 `tests/test_result_history.py`：覆盖列表过滤、最新标记、归档幂等、删除保留 `tasks/task_logs`、删除最新后归档回退、失败轮详情原始状态、30 轮淘汰、API 错误码

**Checkpoint**: 后端历史基础可用，聚焦测试通过。

## Phase 1.5: 失败/中断轮次快照兜底（B010 口径）

**Goal**: 只要结束前已生成含岗位结果，失败/中断/取消也算一轮并进入历史。

- [ ] T008 修改 `webui/app.py` 的筛选任务失败/取消/中断结束路径：若已构建含岗位结果且未保存快照，在写终态前调用 `save_pipeline_result(status=<原始终态>)` 并关联 `source_run_id`；无岗位产出不保存
- [ ] T009 新增/扩展测试：失败/中断/取消但有岗位的轮次进入历史且详情状态为原始机器值、前端映射“失败但有 N 个岗位”；无岗位失败不进入历史

**Checkpoint**: 失败但有岗位的轮次可进入历史，聚焦测试通过。

## Phase 2: User Story 1 - 多轮结果历史前端（B010）

**Goal**: 用户可从任意步骤打开历史，查看/导出/删除轮次，并能在 04 页冻结时使用。

- [ ] T010 新增 `webui/src/composables/resultHistory.ts`：历史列表/详情/删除/归档状态与 API 调用
- [ ] T011 [P] 新增 `webui/src/components/HistoryRoundProfile.vue`：完整画像可展开块
- [ ] T012 [P] 新增 `webui/src/components/ResultHistoryDrawer.vue`：平台分组、状态中文、最新标记、删除确认、打开轮次
- [ ] T013 修改 `webui/src/views/DiscoveryView.vue`：暴露 `openHistoryDrawer`；历史模式加载单轮、锁定平台、禁用改写动作、提供“回到最新”；`analyzeResume` 与 `resetWorkflow` 的 `clearLatestResult()` 改为归档调用；历史模式也能进入 04 页
- [ ] T014 修改 `webui/src/App.vue`：新增顶栏“历史轮次”按钮并触发 `DiscoveryView` 历史抽屉；扩展 `round-status` 类型以支持 `all`
- [ ] T015 新增/扩展前端测试：`ResultHistoryDrawer.spec.ts`、`resultHistory.spec.ts`、`DiscoveryView.spec.ts`、`App.spec.ts`，覆盖冻结页可用、归档、删除回退、历史模式、状态中文映射（`done`/`partial`/`completed_with_pending`/其它有岗位状态）

**Checkpoint**: B010 前端主链可独立验证。

## Phase 3: User Story 2 - 顶栏分层收纳

**Goal**: 顶栏高频常驻、低频进设置菜单、更新角标挂设置。

- [ ] T016 [P] 新增 `webui/src/components/AppSettingsMenu.vue`：AI 设置/浏览器账号/环境检查/检查更新/GitHub，保留原 `data-testid`
- [ ] T017 修改 `webui/src/App.vue`：常驻状态胶囊/提醒/收藏/历史/主题/设置；设置菜单替换平铺按钮；新版本角标挂设置；窄屏图标化
- [ ] T018 新增 `webui/src/components/__tests__/AppSettingsMenu.spec.ts` 并扩展 `App.spec.ts`：验证菜单展开、角标、窄屏类名

**Checkpoint**: 顶栏分层可独立验证。

## Phase 4: User Story 3 - AI 限流/502 自动重试（B034）

**Goal**: 默认 3 次尝试、固定 30 秒；调优预算优先。

- [ ] T019 新增 `webui/ai_retry.py`：默认重试计划（3 次、30 秒、可重试错误集）与 `effective_retry_plan(retry_limits)`；401/403/解析错误/配额耗尽不重试
- [ ] T020 修改 `webui/ai.py`：默认路径使用 `ai_retry` 策略；移除默认路径 `waited + delay > budget` 提前放弃；调优 `retry_limits` 路径保持现有覆盖
- [ ] T021 新增 `tests/test_ai_retry.py` 并更新 `tests/test_ai.py`：429/5xx/超时三次成功、三次失败、401/403/解析错误/配额不重试、调优覆盖、等待时长

**Checkpoint**: B034 独立验证通过。

## Phase 5: User Story 4 - 智联误报封禁（B036）

**Goal**: Chrome 错误页归为瞬时不可达，不整批停工。

- [ ] T022 修改 `scripts/zhilian_cdp_raw.py` 的 `_risk_signal`：`chrome-error://chromewebdata/` 或 `data:text/html,chromewebdata` 先返回 `unreachable`，再进入 marker 判断
- [ ] T023 新增 `tests/test_zhilian_risk_signal.py`：Chrome 错误页返回 `unreachable`、真实封禁仍返回 `blocked`、普通页面不受影响

**Checkpoint**: B036 独立验证通过。

## Phase 6: User Story 5 - 智联继续身份（B037）

**Goal**: 继续任务使用冻结智联身份，缺失阻断，缓存不误放行。

- [ ] T024 新增 `webui/resume_identity.py`：`resolve_frozen_identity`、`persist_frozen_identity`、`invalidate_login_cache_for_resume`
- [ ] T025 修改 `webui/app.py` 的 `/api/task/continue/<run_id>`：先失效登录缓存；解析并写回 `claimed_task` 与 `run.execution_params`；智联身份缺失返回可读错误并保持 `paused`（失败时释放已占用的 resume/task claim）
- [ ] T026 新增 `tests/test_resume_continue.py`：身份完整、身份缺失、缓存已登录但真实 profile 失败、写回持久化

**Checkpoint**: B037 独立验证通过。

## Phase 7: User Story 6 - 无障碍播报（B015）

**Goal**: 只播报状态/阶段/关键计数变化。

- [ ] T027 修改 `webui/src/components/TaskProgress.vue`：主容器移除常驻 `aria-live`；新增视觉隐藏播报区，仅状态/阶段/关键计数变化时更新
- [ ] T028 新增 `webui/src/components/__tests__/TaskProgress.spec.ts`：秒级百分比不触发播报，状态/阶段/关键计数触发一次

**Checkpoint**: B015 独立验证通过。

## Phase 8: User Story 7 - 顶栏“已判定”语义对齐（B035）

**Goal**: 全部/BOSS/智联/历史轮次四种视图平台名与数字一致。

- [ ] T029 修改 `webui/src/discovery.ts`：新增结果视图范围与历史轮次状态类型、纯函数；修改 `webui/src/views/DiscoveryView.vue` 上抛 `round-status` 携带当前查看范围
- [ ] T030 修改 `webui/src/App.vue` 渲染：全部视图不显示单一平台名；BOSS/智联显示对应平台；历史轮次跟随该轮平台；扩展对应前端测试

**Checkpoint**: B035 独立验证通过。

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 文档同步、全量验证与收尾。

- [ ] T031 更新 `CHANGELOG.md` 与 `README.md`：按现有格式记录本轮用户可感知变更
- [ ] T032 运行聚焦测试并修复：`tests/test_result_history.py`、`tests/test_ai_retry.py`、`tests/test_zhilian_risk_signal.py`、`tests/test_resume_continue.py` 及新增前端 spec
- [ ] T033 运行后端全量测试、前端测试、`npm run build`、仓库卫生检查并修复失败
- [ ] T034 按 `quickstart.md` 做桌面/窄屏与真实渲染抽查，清理自产临时文件

**Checkpoint**: 全部门禁通过，可以进入 `speckit-converge`。

## Dependencies & Execution Order

- Phase 1 完成前，任何用户故事不能开始。
- Phase 1.5 在 Phase 1 后、Phase 2 前完成。
- US1（Phase 2）依赖 Phase 1 与 Phase 1.5；US2（Phase 3）依赖 Phase 1；US3-US7 依赖 Phase 1，可并行。
- B035（Phase 8）依赖 US1 历史模式与 US2 顶栏，因为需要“全部/单平台/历史轮次”三种状态。
- Phase 9 依赖全部用户故事。

## Parallel Opportunities

- T002 / T005 / T016 / T019 / T024 等不同文件任务可并行。
- Phase 4-7 四个修复故事可并行。
- 前端测试与后端测试可按模块并行。

## Implementation Strategy

1. 先完成 Phase 1 基础层与后端测试。
2. 完成 Phase 1.5 失败/中断快照兜底。
3. 实现 US1（B010）作为 MVP 主链。
4. 并行完成 US2-US7 修复。
5. 最后统一做 Phase 9 文档与全量验证。
