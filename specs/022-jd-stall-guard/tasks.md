# Tasks: JD 抓取卡死防护与日志查看

**Input**: `/specs/022-jd-stall-guard/`（spec.md / plan.md，第二轮）

**Prerequisites**: plan.md（文件边界与方案要点）

**Tests**: 聚焦测试按用户故事先写后实现（先红后绿）。

**Organization**: 按用户故事分组；US1/US2 为 P1（事故根因），US3/US4 为 P2（配套）。

## File Boundaries

- **Allowed files**: `webui/process_executor.py`、`webui/pipeline_exec_details.py`、`webui/runners/ai_screen_jd.py`、`webui/app_support.py`、`webui/app.py`（仅注册行）、`webui/logging_setup.py`（按需）、`webui/src/components/AppSettingsMenu.vue`、`webui/src/api/client.ts`、`.specify/memory/constitution.md`、`tests/`
- **Forbidden files**: `webui/store.py`、`webui/source.py`、`webui/source_boss_cdp*.py`、`scripts/boss_cdp_raw.py`、`scripts/boss/`、`webui/store_migrations*.py`、`webui/error_registry.py`（只读复用）
- **New files**: `webui/pipeline_guard.py`、`webui/log_api.py`、`webui/src/components/LogViewerDialog.vue`、`tests/test_pipeline_guard.py`、`tests/test_log_api.py`、`webui/src/components/__tests__/LogViewerDialog.spec.ts`
- **Reference direction**: 后端 `runners/ai_screen_jd.py → pipeline_exec_details.py → pipeline_guard.py`；`log_api.py → logging_setup.py`；前端 `AppSettingsMenu.vue → LogViewerDialog.vue → api/client.ts`
- **Line gate**: 改动文件 ≤600 行；新增文件 ≤400 行

## Verification Gate

- 交付门禁：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 卫生检查。
- 用户端到端真跑验证（SC-003）在交付后进行。

---

## Phase 1: User Story 1 - 批次卡死自动恢复 (P1) 🎯 MVP

**Goal**: 一批 300s 无产出判定卡死 → 杀失联抓取工解出任务线程 → 3~5s 后自动重抓（共 3 次）→ 流程自己跑完

**Independent Test**: 注入假卡死（mock 抓取工不返回），验证自动重抓并完成

### 测试（先写、先红）

- [ ] T001 [US1] `tests/test_pipeline_guard.py`：批次登记/心跳/完成生命周期；300s（测试用短值）无心跳判定卡死；有心跳不误杀
- [ ] T002 [US1] `tests/test_pipeline_guard.py`：判定卡死后调用失联清理（mock 进程被 kill）、任务线程解出、自动重抓编排（尝试次数 1→2→3、间隔 3~5s）

### 实现

- [ ] T003 [US1] `webui/process_executor.py`：ScraperExecutor 加可选 spawn 钩子（登记子进程句柄供防护清理）与 on_output 心跳透传（约 +15 行）
- [ ] T004 [US1] 创建 `webui/pipeline_guard.py`：PipelineGuard 核心——begin_batch/touch/complete、独立 daemon 监控线程（每 5s 扫）、300s 判定、失联清理（kill 子进程）、重试编排（attempt 计数）、事件日志
- [ ] T005 [US1] `webui/pipeline_exec_details.py`：fetch_job_details 接入——批次登记/心跳/完成后检查卡死标记→等 3~5s 重抓该批（接受重复抓取）
- [ ] T006 [US1] `webui/runners/ai_screen_jd.py`：run_jd_stage 创建 guard（经 ctx）并传入 fetch_job_details

**Checkpoint**: US1 独立可用——卡死自动恢复，任务继续

---

## Phase 2: User Story 2 - 3 次失败按原因分流收场 (P1)

**Goal**: 第 3 次仍卡死 → 探测环境：环境级 → 暂停+报错模块接管、断点可续跑；偶发 → 跳过该批进待确认、继续下一批

**Independent Test**: 注入两类卡死，验证分别进入暂停报错与跳过待确认

### 测试（先写、先红）

- [ ] T007 [US2] `tests/test_pipeline_guard.py`：环境级分流——探测失败（mock CDP/登录态探测不可用）→ 任务 paused + 明确错误码 + 断点保留
- [ ] T008 [US2] `tests/test_pipeline_guard.py`：偶发分流——探测通过仍卡死 → 该批岗位进待确认 + 继续下一批
- [ ] T009 [US2] `tests/test_pipeline_guard.py`：最终兜底——杀进程后任务线程仍不解出（mock 监控侧观察到批次悬空）→ 任务 paused + "请重启应用"提示

### 实现

- [ ] T010 [US2] `webui/pipeline_guard.py`：3 次失败分流——环境探测（经 ctx 注入 preflight/check_login_state 语义，复用现有机制）→ 环境级/偶发分支；线程不解出兜底（经 ctx 标记 paused + 明示）
- [ ] T011 [US2] `webui/runners/ai_screen_jd.py`：环境级分流接入既有 hard_stop 暂停路径（write_run paused + 不关浏览器 + 可继续）；偶发分流该批岗位写 screening pending

**Checkpoint**: US1+US2 独立可用——卡死必有着落，无第三种状态

---

## Phase 3: User Story 3 - 卡死事件日志落盘 (P2)

**Goal**: 卡死/重试/放弃/分流/兜底事件写入 career-scout.log；所有运行模式日志可用；不补旧数据

**Independent Test**: 测试源码与模拟 EXE 模式日志可用 + 防护事件落盘

### 测试（先写、先红）

- [ ] T012 [US3] `tests/test_logging_mode.py`：源码模式与模拟 EXE（sys.frozen 注入）下 configure_logging 均产生可写日志文件
- [ ] T013 [US3] `tests/test_pipeline_guard.py`：卡死/重试/放弃事件行写入日志（含时间、批次、尝试次数、结果）

### 实现

- [ ] T014 [US3] 排查并确认 `configure_logging()` 在所有运行模式生效（app.py 调用链已有；如缺口则补 `webui/logging_setup.py`/`webui/app.py`）
- [ ] T015 [US3] `webui/pipeline_guard.py`：全部防护事件用 `get_logger("pipeline_guard")` 落盘（与 T004 同步实现）

**Checkpoint**: US3 独立可用——防护事件有据可查

---

## Phase 4: User Story 4 - 设置页"日志"浮窗实时查看 (P2)

**Goal**: 设置页并排"日志"按钮 → 黑框浮窗旧到新展示 career-scout.log + 实时更新

**Independent Test**: 日志 API 测试 + 前端组件测试；手动验证任务运行中浮窗自动刷新

### 测试（先写、先红）

- [ ] T016 [US4] `tests/test_log_api.py`：读尾部/分页取更早/轮询偏移；轮转后切换新文件；会话令牌保护；文件不存在空态
- [ ] T017 [US4] `webui/src/components/__tests__/LogViewerDialog.spec.ts`：浮窗渲染、旧到新、默认定位最新、实时刷新、上滑分页、回到底部

### 实现

- [ ] T018 [US4] 创建 `webui/log_api.py`：register_log_routes(app, ctx)——GET /api/logs（tail/offset/since），读 career-scout.log（每次重开文件 + 文件身份检测轮转），受会话令牌保护
- [ ] T019 [US4] `webui/app_support.py`：接线注册 log_api；`webui/app.py`：register_log_routes(app, store) 一行
- [ ] T020 [US4] `webui/src/api/client.ts`：fetchLogs（tail/offset/since）
- [ ] T021 [US4] 创建 `webui/src/components/LogViewerDialog.vue`：黑框浮窗（样式自定）、旧到新、定位最新、上滑分页 ≤500 行、2s 轮询、翻旧暂停跟随 + "回到底部"、空态
- [ ] T022 [US4] `webui/src/components/AppSettingsMenu.vue`：与 AI 设置/浏览器账号并排新增"日志"入口

**Checkpoint**: US4 独立可用——日志随时可看、实时更新

---

## Phase 5: 收尾与登记

- [ ] T023 [P] `.specify/memory/constitution.md`：模块地图登记 `webui/pipeline_guard.py`（流水线防护域）与 `webui/log_api.py`（日志读取路由域）
- [ ] T024 [P] 文档卫生：检查 README/说明是否需要同步"设置页日志入口"（用户可感知能力，AGENTS.md 文档卫生要求）
- [ ] T025 后端全量测试 + 前端测试 + `npm run build` 全绿
- [ ] T026 仓库卫生检查：`uv run python -m unittest tests.test_repo_hygiene` 通过；`git status` / `git diff --check` 无意外文件

**Checkpoint**: 交付就绪，等待用户端到端真跑验证（SC-003）

---

## Dependencies & Execution Order

- US1 → US2（分流依赖重抓编排）；US3 可与 US1/US2 并行（T012-T014 独立）；US4 独立可并行
- 每完成一个用户故事 checkpoint 即验证
- 测试先红后绿；提交遵循 Conventional Commits；提交前跑卫生检查
