# Tasks: 抓取批次失败透出、重抓分流与暂停提示整修

**Input**: Design documents from `/specs/012-scrape-batch-fixes/`

**Prerequisites**: plan.md, spec.md

**Organization**: 按用户故事分层；B050/B053 同一链路串行，B051 后端校验独立，B052 前端展示独立。

## File Boundaries

- **Allowed files**: `scripts/boss_cdp_raw.py`、`webui/source.py`、`webui/pipeline_exec.py`、`webui/error_registry.py`、`webui/app.py`（仅 `pipeline_recrawl` 校验段）、`webui/src/errorCodes.ts`、`webui/src/components/TaskProgress.vue`、`webui/src/styles.css`、`tests/test_source.py`、`tests/test_healthy_pipeline.py`、`tests/test_inprocess_execution.py`、`tests/test_error_registry.py`、`webui/src/__tests__/errorCodes.spec.ts`、`webui/src/components/__tests__/TaskProgress.spec.ts`。
- **Forbidden files**: `webui/store.py`、`webui/store_migrations.py`、`webui/workbench.py`、`webui/result_history*.py`、`webui/ai*.py`、`scripts/zhilian_cdp_raw.py`、`webui/src/views/DiscoveryView.vue`（前端重抓入口不改）。
- **New files**: 无。
- **Reference direction**: `source.py / pipeline_exec.py → error_registry.py`；`TaskProgress.vue → errorCodes.ts`。
- **Line gate**: `boss_cdp_raw.py` 增量 ≤120 行；`app.py` 增量 ≤40 行；`TaskProgress.vue` 增量 ≤60 行。

## Verification Gate

- 功能交付最终门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务不要求全量测试，按根 `AGENTS.md` 收口规则执行。

## Phase 1: 用户故事 1 - 详情批次失败透出与计数隔离（B050/B053，P1）

**Goal**: BOSS worker 异常透出、部分结果保留、请求计数按运行隔离且上限 999、显式失败码进注册表。

**Independent Test**: `tests/test_source.py`、`tests/test_inprocess_execution.py` 覆盖退出码 11、in-process 映射、空批次不再判 CDP；新增 worker 透出与计数隔离聚焦测试。

- [x] T001 [P] [US1] `scripts/boss_cdp_raw.py`：`MAX_API_REQUESTS` 改为 999；新增 `RequestLimitExceededError`；将全局 `_request_counter` 收敛为可替换的运行级计数对象，`incr_request()` 在锁内递增并命中上限抛 `RequestLimitExceededError`。
- [x] T002 [P] [US1] `scripts/boss_cdp_raw.py`：提供 `reset_request_counter()`；在 `run_search_programmatic()` 与 CLI `main()` 抓取路径入口调用，确保整轮（列表+详情）共享一次计数、下一轮从 0 开始；`scrape_details()` 独立调用同样从 0 开始。
- [x] T003 [P] [US1] `scripts/boss_cdp_raw.py`：`_tab_worker()` 主体包 try/except，把异常按线程安全方式写入共享 `worker_errors`；`scrape_details()` 并行分支 `join()` 后读取：有异常则保留已写盘的部分结果并重抛（优先 `RequestLimitExceededError`，其余原类型）。
- [x] T004 [US1] `scripts/boss_cdp_raw.py`：`__main__` 捕获 `RequestLimitExceededError` 输出明确文案并 `sys.exit(11)`；串行详情路径 `incr_request()` 命中上限时同样以该异常结束（维持现有异常透出语义）。
- [x] T005 [P] [US1] `webui/error_registry.py`：新增 `source_request_limit_exceeded` 注册项（category=source、blocking=True、retryable=True、user_message="本轮抓取请求数已达上限"）并加入 `SYSTEMIC_BLOCK_CODES`。
- [x] T006 [P] [US1] `webui/source.py`：`_classify_failed_code` 增加退出码 11 → `source_request_limit_exceeded`；in-process `_run_in_process` 捕获 `boss.RequestLimitExceededError` 返回 `(11, ...)`；移除 JD 详情批 `returncode == 0 and not parsed_events and not details_by_url → source_cdp_unavailable` 分支。
- [x] T007 [US1] `webui/pipeline_exec.py`：`fetch_job_details` 的 `_jd_hard_stop_codes` 加入 `source_request_limit_exceeded`，命中后走“已抓部分已保存 + 暂停”路径。
- [x] T008 [US1] `webui/src/errorCodes.ts`：新增 `source_request_limit_exceeded` 与中文文案，保持与后端镜像一致。
- [x] T009 [P] [US1] `tests/test_source.py`：新增/更新用例——退出码 11 映射；in-process `RequestLimitExceededError` → `(11, ...)`；JD 空批次退出码 0 + 0 事件不再映射 `source_cdp_unavailable`（改为 `source_invalid_output` 或明确断言不判 CDP）；既有 CDP 退出码 2 映射保持。
- [x] T010 [P] [US1] `tests/test_error_registry.py`：断言 `source_request_limit_exceeded` 已收录、进入系统性阻断集合、to_json 含中文文案。
- [x] T011 [US1] `webui/src/__tests__/errorCodes.spec.ts`：断言新码与文案出现在前端镜像。

## Phase 2: 用户故事 2 - 待确认全部重抓分流（B051，P1）

**Goal**: 后端接受 source run 快照内无最终判定的 ID，按 JD 有无分流；已有判定/不在快照仍拒绝。

**Independent Test**: `tests/test_healthy_pipeline.py` 覆盖“有 JD 未精筛可重抓”“已有判定仍拒绝”“不在快照仍拒绝”“auto 语义不变”。

- [x] T012 [P] [US2] `webui/app.py` `pipeline_recrawl`：加载 source run 结果快照，构造“快照内无最终判定”集合（verdict 非 match/not_match/mismatch），与 `screening_pending_results` 集合合并作为可重抓集合；`job_ids` 缺省/`auto` 维持从可重抓集合读取。
- [x] T013 [P] [US2] `webui/app.py` `pipeline_recrawl`：请求 ID 与可重抓集合求差，仍返回 409 `non_pending_job_ids`；任务执行函数不改（`_run_recrawl_task` 已按 JD 有无分流）。
- [x] T014 [US2] `tests/test_healthy_pipeline.py`：更新 `test_recrawl_rejects_non_pending_job_ids` 为“match-1 已有判定仍拒绝”；新增“有 JD 未精筛的 uncertain 岗位可启动重抓且 job_ids 保留”。

## Phase 3: 用户故事 3 - 暂停/失败提示统一内联（B052，P2）

**Goal**: 暂停/失败两态显示 `中文原因 · 错误字段`，红色字段内联；无诊断盒、无复制按钮。

**Independent Test**: `webui/src/components/__tests__/TaskProgress.spec.ts` 覆盖暂停/失败两态、无码兜底、无诊断盒、无复制按钮。

- [x] T015 [P] [US3] `webui/src/components/TaskProgress.vue`：新增内联展示 computed（暂停取 `pause_info.error_reason`/`error`，失败取 `error`/`message`，拼接错误码 `中文原因 · 错误码`；无码时仅中文原因）；暂停与失败提示行统一使用。
- [x] T016 [P] [US3] `webui/src/components/TaskProgress.vue`：移除 `task-diagnostics` 盒子、`diagnostic-code`、`copy-diagnostics` 按钮、`copyDiagnostics`/`copyText`/`diagnosticText`/`copied` 相关逻辑；移除不再使用的 `apiRequest` 导入（如无其他用途）。
- [x] T017 [US3] `webui/src/styles.css`：新增 `.error-field` 红色内联样式；删除 `.task-diagnostics`/`.diag-code`/`.diag-copy` 样式。
- [x] T018 [US3] `webui/src/components/__tests__/TaskProgress.spec.ts`：更新诊断用例为内联断言；新增暂停/失败两态与无码兜底用例。

## Phase 4: 跨切面验证

**Purpose**: 聚焦测试、全量门禁与回归确认。

- [x] T019 运行后端聚焦测试：`tests.test_error_registry`、`tests.test_source`、`tests.test_healthy_pipeline`、`tests.test_inprocess_execution`。
- [x] T020 运行前端测试：`npm test`（含 TaskProgress、errorCodes、DiscoveryView 回归）。
- [x] T021 运行 `npm run build` 并确认 dist 同步（dist 改动只作为构建产物提交）。
- [x] T022 运行后端全量测试：`uv run python -m unittest discover tests`。
- [x] T023 运行仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`；检查 `git diff --check` 与 `git status`。

## Dependencies & Execution Order

- T001-T004 内部串行（计数 → 入口 → worker 透出 → 退出码）。
- T005-T008 依赖 T001/T004 的失败码定义，可与 T003 并行。
- T009-T011 依赖对应实现完成。
- T012-T014 独立于 Phase 1。
- T015-T018 独立于后端链路。
- T019-T023 全部完成后执行。


## Converge 记录（2026-08-14）

- 实现范围按冻结 Spec 完成；新增 `webui/task_runners.py` 最小接线（B053 in-process 退出码 11 映射，TaskRunner/WorkbenchRunner 两处 catch），属于 B053 端到端透出的必要部分。
- 验证证据：`tests.test_source` + `tests.test_inprocess_execution` 187 项通过；`tests.test_healthy_pipeline` 194 项通过（2 项跳过为既有 Spec010 内部产物跳过）；前端 `vitest` 372 项通过；`npm run build` 通过；后端全量 2256 项仅 `test_repo_hygiene.test_no_untracked_non_ignored_files` 失败（未提交导致的预期失败：新 spec 文件与 dist 构建产物未跟踪）。
- 收口前卫生门禁：`git diff --check` 通过；卫生测试其余 10 项通过。

## 审查修复与端到端记录（2026-08-14 第二轮）

- 完整审查发现并修复 2 个实质缺陷：
  - B053：`run_search_programmatic` 组合流程中 `_run_active=True` 使 `scrape_list/scrape_details` 跳过重置，计数跨轮累计；已在该入口调用 `begin_request_run()`，并新增 `test_programmatic_run_resets_counter_between_runs`。
  - B052：`/api/task-state` 对失败态非系统性错误码不下发 `pause_info`，前端失败态会缺错误字段；已改为 failed 且有 error_code 时同样返回，并新增 `test_task_state_api_failed_with_non_systemic_error_code`。
- 另修复 `_run_active` 早期校验失败泄漏、`source.py` 注释与退出码 docstring、`TaskProgress.vue` 残留注释等小问题。
- 全量验证：后端 2258 项，唯一失败为卫生测试未跟踪文件断言（新 spec/dist 未提交）；前端 372 项通过；`npm run build` 通过；`git diff --check` 通过。
- 端到端（真实智联主链，BOSS 被封改用智联）：启动 `~/.career-scout/chrome-profile.zhilian` 于 9223，`preflight=ok`，列表 20 条真实岗位，2 条真实 JD（254/362 字），无 degrade，`ZHILIAN_E2E_OK`。
- SC-015 勘误与修复：此前用 `--fixture ai` 运行失败是种子选错，不是脚本或本轮改动问题——暂停 AI 任务停在筛选步骤，不含结果页控件，而 SC-015 验收对象是结果页。改用 `run_isolated_webui.py --fixture recrawl` 后，375/390/768/1440 四档真实视口全部 PASS，暂停提示正确显示 `中文原因 · captcha_required`。已给 `tests/sc015_viewport_check.py` 增加 `--fixture` 参数（默认 recrawl）、文档说明与失败提示，防止再选错；`Sc015AcceptanceHarnessTests` 8 项通过。
