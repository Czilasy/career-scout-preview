# Tasks: 抓取阻断正确化

**Input**: Design documents from `/specs/034-scrape-block-handling/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

## File Boundaries

- **Allowed files**:
  1. `scripts/boss_cdp_raw.py` — `__main__` 补捕获三类异常 → 失败行 + 退出码（薄映射，净增 ≤10 行）
  2. `webui/source_boss_cdp_detail.py` — `fetch_details_batch` 非零退出分支读事件文件逐岗位归类（净增 ≤15 行；若超 800 则等价迁出到 `webui/source_boss_detail_events.py`）
  3. `tests/test_scrape_block_classification.py` — 新增假样本回归聚焦测试
- **Forbidden files**: 白箱 033 已改 12 文件（`webui/logging_setup.py`、`webui/runtime_audit.py`、`webui/source.py`、`webui/source_boss_cdp.py`、`webui/updater.py`、`webui/error_registry.py`、`webui/ai_client.py`、`scripts/boss/constants.py`、`scripts/boss/search.py`、`scripts/boss/login.py`、`scripts/boss/detail_scrape.py`、`webui/task_runners.py`）；`webui/error_registry.py`（错误码/文案已完备）；`webui/src/` 全部前端；`webui/app.py`、`webui/store.py`；数据库；roadmap/、`.codebuddy/`
- **New files**: `tests/test_scrape_block_classification.py`；超线兜底 `webui/source_boss_detail_events.py`（等价搬运）
- **Reference direction**: 脚本侧 `boss_cdp_raw.py` → `scripts/boss_cdp_signals.emit_failure_line`（既有）；webui 侧 `source_boss_cdp_detail.py` → `_read_events_file` / `_classify_failed_code`（既有）；tests → 被测代码
- **Line gate**: `scripts/boss_cdp_raw.py` ≤140；`source_boss_cdp_detail.py` ≤800；`tests/test_scrape_block_classification.py` ≤300

## Verification Gate

- 功能交付门禁：相关模块聚焦测试、后端全量测试、前端 `npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 测试日志/输出一律使用系统临时目录，禁止写项目根。
- 按用户要求：各类测试全部执行，不因"需要真实登录"跳过；模拟用户由项目自身就绪。

---

## Phase 1: 脚本主入口补捕获（US1 / FR-001, FR-008）

**Goal**: 子进程模式下 RiskControlError/LoginRequiredError/RequestLimitExceededError 不再裸 traceback exit 1，而是输出结构化失败行 + 正确退出码。

- [ ] T001 [US1] `scripts/boss_cdp_raw.py` import 行扩展：从 `scripts.boss.exceptions` 补入 `RiskControlError, LoginRequiredError, RequestLimitExceededError`
- [ ] T002 [US1] `scripts/boss_cdp_raw.py` `__main__` 在既有捕获后追加三个 except 块：
  - `RiskControlError` → `code = exc.code or "source_status_unclear"`；`emit_failure_line(code, str(exc))`；`sys.exit(10)`
  - `LoginRequiredError` → `emit_failure_line("source_login_required", str(exc))`；`sys.exit(1)`
  - `RequestLimitExceededError` → `emit_failure_line("source_request_limit_exceeded", str(exc))`；`sys.exit(11)`
- [ ] T003 [US1] 核对 `__main__` 块保持薄映射：不引入业务逻辑、不新增 import 到 webui 域；行数净增 ≤10

**Checkpoint**: 脚本侧三类账号级异常子进程退出时有失败行 + 精确退出码。

---

## Phase 2: 详情批非零退出读事件文件归类（US2 / FR-003, FR-004, FR-007）

**Goal**: `fetch_details_batch` 非零退出时不再只用退出码粗分类，按事件文件真实 safe_code 逐岗位归类；账号级码走熔断信号，软失败码带原因落待确认语义。

- [ ] T004 [US2] `webui/source_boss_cdp_detail.py` `fetch_details_batch` 非零退出分支（现 281-308 行）改造：
  - 先 `_read_events_file(events_output_path)` 读事件文件（复用既有方法）
  - 逐岗位：事件 `status=completed` 且 detail 存在 → `success`（保留既有抢救逻辑）；`status=unavailable|failed` → `failed_code = event.safe_code`；`status=cancelled` → safe_code 或 `source_unknown_error`；无事件记录 → 回退 `_classify_failed_code(returncode, captured)`
  - 账号级码（`SourceCircuitBreaker.SIGNAL_CODES`：login_required / verification_required / rate_limited / blocked）推进 `record_signal`
- [ ] T005 [US2] 行数门禁自查：`source_boss_cdp_detail.py` ≤800；若超线，把事件归类逻辑等价迁出到 `webui/source_boss_detail_events.py`（新模块，不改行为），`source_boss_cdp_detail.py` 保留调用入口，并在 plan.md/宪法模块地图登记新模块
- [ ] T006 [US2] 兜底：事件文件缺失/读不到 → 回退 `_classify_failed_code`，不崩溃、不伪造原因（FR-007）

**Checkpoint**: 详情批非零退出时逐岗位真实归类，账号级阻断不进待确认语义。

---

## Phase 3: 聚焦测试（US1+US2 / SC-001~SC-004）

**Goal**: 假样本回归证明两处接线正确。

- [ ] T007 [P] [US1] `tests/test_scrape_block_classification.py` — 脚本侧薄映射测试：patch 或直接调用 `__main__` 薄映射逻辑，断言 RiskControlError(code="source_verification_required") → 失败行 `code=source_verification_required` + exit(10)；LoginRequiredError → `source_login_required` + exit(1)；RequestLimitExceededError → `source_request_limit_exceeded` + exit(11)
- [ ] T008 [P] [US1] 同上文件 — webui 分类侧：`_classify_failed_code` 解析上述失败行 → 精确账号级码（非 `source_unknown_error`）
- [ ] T009 [P] [US2] 同上文件 — 非零退出归类：构造事件文件（含账号级码 + 单条软失败码 + 已落盘岗位）→ `fetch_details_batch` 非零退出时逐岗位归类正确；账号级码推进熔断信号；软失败码带原因
- [ ] T010 [P] [US2] 同上文件 — 兜底：事件文件缺失 → 回退 `_classify_failed_code`，不崩溃

**Checkpoint**: 聚焦测试全绿。

---

## Phase 4: 验证门禁（SC-001~SC-005）

**Goal**: 全部门禁通过，无降级。

- [ ] T011 运行聚焦测试：`uv run python -m unittest tests.test_scrape_block_classification`
- [ ] T012 后端全量测试：`uv run python -m unittest discover -s tests`（含 `tests.test_repo_hygiene`）
- [ ] T013 前端构建：`cd webui && npm run build`（确认 dist 与 src 同步）
- [ ] T014 仓库卫生检查：`uv run python -m unittest tests.test_repo_hygiene`
- [ ] T015 真实/模拟小抓取冒烟：按项目正式启动方式跑一次正常抓取（登录态由项目自身就绪；不主动触发验证码/限流），确认基本盘无回归

**Checkpoint**: 全部测试绿、构建通过、卫生检查通过。

---

## 实施后（用户额外要求，授权范围内）

- [ ] T016 修复过程中发现的任何错误：按推荐直接修复，不擅自扩大范围
- [ ] T017 git 提交（仅 commit，不 push；提交信息 Conventional Commits；身份邮箱 `czyooutzilas@gmail.com`；提交前确认 `git status` 无无关文件）
- [ ] T018 提交完毕后运行桌面相关脚本文件夹中的关机脚本，输入数字 1，1 分钟后关机

## Dependencies & Execution Order

- T001 → T002 → T003（同一文件，串行）
- T004 → T005 → T006（同一文件，串行）
- T001-T006 完成后 → T007-T010（聚焦测试，可并行）
- 聚焦测试全绿 → T011-T015（验证门禁，按序）
- 验证通过 → T016-T018（收尾，由用户"开始执行"指令授权）

## Notes

- tasks.md 完成即停滞点：停止报告，等待用户明确"开始执行"指令（用户已说明该指令 = 完全做完，含测试、提交、关机脚本）。
- [P] 任务 = 不同文件无依赖；本 feature 改动面小，以串行为主。
- 测试日志/输出禁止写项目根，一律系统临时目录。
