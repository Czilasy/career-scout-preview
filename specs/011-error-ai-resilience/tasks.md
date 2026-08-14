# Tasks: 错误码统一、AI 可观测性与重试策略整修

**Input**: Design documents from `/specs/011-error-ai-resilience/`

**Prerequisites**: plan.md, spec.md

**Organization**: 按用户故事分层；B042 独立前端，B043 为基础重构，B044/B045 在 AI 链路内实现。

## File Boundaries

- **Allowed files**: 见 `plan.md` File Boundaries；超大文件只做最小接线。
- **Forbidden files**: `webui/app.py`、`webui/store_migrations.py`、`webui/workbench.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/store_result_history_mixin.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py` 不修改。
- **New files**: `webui/error_registry.py`、`webui/ai_raw_log.py`、`webui/src/errorCodes.ts`、`tests/test_error_registry.py`、`tests/test_ai_raw_log.py`、`webui/src/__tests__/errorCodes.spec.ts`。
- **Reference direction**: 后端 `source/pipeline_exec/store/ai → error_registry`；`ai.py → ai_retry.py、ai_raw_log.py`；前端 `DiscoveryView.vue → discovery.ts`；`types.ts → errorCodes.ts`。
- **Line gate**: 新 Python 文件 ≤800 行；`DiscoveryView.vue` 增量 ≤120 行；`store.py`/`ai.py` 只做最小 diff。

## Verification Gate

- 功能交付最终门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务不要求全量测试，按根 `AGENTS.md` 收口规则执行。

## Phase 1: 用户故事 1 - 空城市确认后按全国（B042，P1）

**Goal**: 两个开始入口空城市时行为一致：弹应用内确认框，确认按全国提交，取消不执行。

**Independent Test**: 前端测试覆盖两入口弹框、取消、确认提交、双空不弹框。

- [x] T001 [P] [US1] 在 `webui/src/discovery.ts` 新增 `shouldConfirmNationalScope(keywords, cities)` 纯函数：关键词有、城市空返回 true；其余返回 false。
- [x] T002 [P] [US1] 在 `webui/src/__tests__/discovery.spec.ts` 添加纯函数测试。
- [x] T003 [US1] 修改 `webui/src/views/DiscoveryView.vue`：两个开始入口在 `shouldConfirmNationalScope` 为 true 时弹现有应用内确认框；取消不发请求；确认后按入口继续：单独抓取走现有 `startScrape()`，开始筛选并 AI 优化先打开 OneClick 筛选确认弹窗，再由弹窗确认后走 `startScrape({ autoScreen: true, ... })`。
- [x] T004 [US1] 扩展 `webui/src/views/__tests__/DiscoveryView.spec.ts`：覆盖 B042 四条验收场景。

## Phase 2: 用户故事 2 - 统一错误码注册表（B043，P2）

**Goal**: 后端、AI、前端、数据库历史错误码收敛到单一注册表；旧常量兼容；未知码测试失败、运行时可见。

**Independent Test**: `tests/test_error_registry.py` 与前端 `errorCodes.spec.ts` 验证唯一性、派生集合、未知码失败、前后端一致。

- [x] T005 [P] 新增 `webui/error_registry.py`：注册表条目（code/category/blocking/retryable/user_message/reason/resume_condition/aliases）、`to_json()`、`validate_code()`、`SYSTEMIC_BLOCK_CODES`、`INDEPENDENT_FAILURE_CODES`、`SAFE_FAILURE_CODES`、`ERROR_TAXONOMY` 兼容导出。
- [x] T006 [P] 新增 `tests/test_error_registry.py`：唯一码、未知码失败、旧别名、DB 历史码收录、to_json 与前端镜像一致。
- [x] T007 [US2] 修改 `webui/source.py`：`SAFE_FAILURE_CODES` 改为从 `webui.error_registry` 导入并保持导出名。
- [x] T008 [US2] 修改 `webui/pipeline_exec.py`：`ERROR_TAXONOMY`、`_FAILED_CODE_LABELS`、`_HARD_STOP_CODES` 改为注册表派生/兼容导出。
- [x] T009 [US2] 修改 `webui/store.py`：`SYSTEMIC_BLOCK_CODES`、`INDEPENDENT_FAILURE_CODES` 改为从注册表导入并保持导出名。
- [x] T010 [US2] 修改 `webui/ai.py`：AI 错误常量、`ERROR_USER_MESSAGES`、`SYSTEMIC_AI_ERROR_CODES` 改为注册表兼容导出。
- [x] T011 [P] [US2] 新增 `webui/src/errorCodes.ts`：前端错误码与中文文案镜像。
- [x] T012 [P] [US2] 修改 `webui/src/types.ts`：错误码类型引用/镜像 `errorCodes.ts`。
- [x] T013 [P] [US2] 新增 `webui/src/__tests__/errorCodes.spec.ts`：镜像结构完整、平台码与来源码保持契约。

## Phase 3: 用户故事 3 - AI 原始响应全部内容落盘（B044，P2）

**Goal**: AI 原始正文按 attempt 写入本地 `ai_raw.log`，500KB 截断，不记录密钥。

**Independent Test**: `tests/test_ai_raw_log.py` 覆盖成功、失败、超长、脱敏、attempt 字段。

- [x] T014 [P] 新增 `webui/ai_raw_log.py`：`record_raw_ai_response(correlation_id, attempt_index, text)`；`ai_raw.log` 轮转；500KB 截断；复用 `logging_setup.redact`。
- [x] T015 [P] 新增 `tests/test_ai_raw_log.py`：写文件、截断标记、脱敏、attempt 字段、日志目录环境变量。
- [x] T016 [US3] 修改 `webui/logging_setup.py`：提供 `ai_raw.log` 目录/轮转复用入口。
- [x] T017 [US3] 修改 `webui/ai.py`：`call_ai` 每次 attempt 拿到 `content` 后、解析前调用 `record_raw_ai_response`；成功与失败 attempt 均记录；测试通过 `CAREER_SCOUT_LOG_DIR` 指向临时目录。

## Phase 4: 用户故事 4 - AI 重试策略调整（B045，P2）

**Goal**: 按错误码退避 + 抖动 + 总上限；`invalid_response` 精筛单条 1 次重试；系统性暂停语义不变；manifest 与默认策略统一。

**Independent Test**: `tests/test_ai_retry.py`、`tests/test_ai.py`、`tests/test_tuning.py` 覆盖退避、上限、invalid_response、manifest 回退。

- [x] T018 [P] 修改 `webui/ai_retry.py`：默认策略改为按错误码退避序列 + 抖动 + 总上限 60s；`effective_retry_plan` 兼容调优 override。
- [x] T019 [US4] 修改 `webui/ai.py`：`call_ai` 按新策略退避；`invalid_response` 只允许精筛单条 1 次重试；系统性错误不自动重试。
- [x] T020 [US4] 修改 `webui/tuning.py`：manifest `retry_policy` 校验与默认策略同构，缺失/非法回退默认。
- [x] T021 [P] [US4] 扩展 `tests/test_ai_retry.py`：退避、抖动范围、总上限、invalid_response 单条、系统性不重试。
- [x] T022 [P] [US4] 扩展 `tests/test_tuning.py`：manifest retry_policy 同构校验与回退默认。

## Phase 5: 跨切面验证

**Purpose**: 全量门禁与回归确认。

- [x] T023 运行后端聚焦测试：`tests.test_error_registry`、`tests.test_ai_retry`、`tests.test_ai_raw_log`、`tests.test_ai`、`tests.test_tuning`。
- [x] T024 运行前端测试：`npm test`（含 DiscoveryView、discovery、errorCodes、types）。
- [x] T025 运行 `npm run build` 并确认 dist 同步。
- [x] T026 运行后端全量测试：`uv run python -m unittest discover tests`（2246 用例；功能用例通过，唯一失败为 T027 未跟踪新文件）。
- [ ] T027 运行仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`；检查 `git diff --check` 与 `git status`。（卫生测试因本轮新文件尚未提交而报未跟踪文件；`git diff --check` 通过；待提交授权后复跑）

## Dependencies & Execution Order

- T001-T004 独立可先做（B042）。
- T005-T013 内部串行（注册表 → 引用替换 → 前端镜像）。
- T014-T017 独立于 B043，可与 Phase 2 并行。
- T018-T022 依赖 `ai_retry.py`，可在 B044 完成后进行。
- T023-T027 全部完成后执行。
