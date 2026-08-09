# Tasks: P1 完成度与界面可信度整修

**Input**: Design documents from `specs/006-p1-completion/`

**Prerequisites**: `spec.md`、`plan.md`

**Tests**: 本任务清单包含测试任务；测试任务在各用户故事内与实现一起按聚焦验证推进，进度语义测试必须覆盖新行为，不要求先失败后实现。

**Organization**: 按用户故事分组；US1 与 US2 都改 `TaskProgress.vue`，串行执行；US3/US4/US5 相互独立。

## Phase 1: Setup (Shared Baseline)

**Purpose**: 记录改动前测试基线，避免把既有失败误判为本任务引入。

- [X] T001 [P] 运行后端基线测试 `uv run python -m unittest tests.test_healthy_pipeline tests.test_webui_app tests.test_updater tests.test_workbench_api` 与前端基线测试 `cd webui && npm test`，记录失败/通过情况后再开始实现；同时确认 `webui/source.py` 的 `fetch_list` 当前是否已有逐页/逐条真实回调，没有则记录为本次已知限制。

---

## Phase 2: User Story 1 - 进度条只随真实完成事件推进 (Priority: P1)

**Goal**: 抓取/筛选/重抓进度由后端权威真实百分比驱动，前端不再时间假爬。

**Independent Test**: 构造 10 组抓取、筛选、重抓样本，断言准备阶段最多 1%、第 1/2 组完成节点正确、暂停定格、完成 100%、阶段内按真实条数推进。

### Implementation for User Story 1

- [X] T002 [P] [US1] 修改 `webui/pipeline_exec.py` 的 `_scrape_overall_percent` 与 `emit` 逻辑：准备阶段最多 1%，组合完成按 `已完成组合数 / 总组合数 * 100`，`searching / waiting / combo_failed / risk_warning / closing_chrome` 不推进百分比。
- [X] T003 [P] [US1] 核对/调整 `webui/app.py` 的 `_screen_overall_percent`（权重定为初筛 25%、抓 JD 50%、精筛 25%）与 `/api/task-state` 的 `overall_percent` 兜底公式：阶段内按真实 `current/total` 插值，兜底与 `emit` 使用同一真实语义，不引入时间爬升。
- [X] T004 [US1] 在 `tests/test_healthy_pipeline.py` 与 `tests/test_webui_app.py` 增加/更新进度百分比测试：10 组任务第 1 组完成=10、第 2 组完成=20、搜索/等待不涨、暂停定格、失败/取消显示当前真实值或 0、完成 100；筛选/重抓按真实条数；`/api/task-state` 兜底与 `emit` 一致。
- [X] T005 [US1] 重写 `webui/src/components/TaskProgress.vue` 的进度引擎：消费 `progress.overall_percent`，删除 `SCRAPE_WEIGHTS / SCREEN_WEIGHTS / SOFT_CAP_RATIO / ambientTargetAt / 随机停顿`，显示值只向真实锚点平滑追赶且不超前，暂停/终态直接取真实值。
- [X] T006 [US1] 重写 `webui/src/components/__tests__/TaskContinue.spec.ts` 中假进度用例并同步 `webui/src/components/__tests__/RecrawlContinue.spec.ts`：真实计数不变时不爬升、10 组不超前、暂停定格、完成 100。

**Checkpoint**: US1 可独立验证，进度条不再假爬。

---

## Phase 3: User Story 2 - 任务阶段不泄漏英文原始字段 (Priority: P1)

**Goal**: 任意任务路径下，界面都不出现原始英文 stage。

**Independent Test**: 模拟运行、刷新接回、暂停、中断、重抓、未知阶段快照，断言阶段文字为中文或隐藏。

### Implementation for User Story 2

- [X] T007 [US2] 在 `webui/src/components/TaskProgress.vue` 补齐已知原始阶段中文标签（如 `waiting / combo_done / combo_failed / risk_warning / closing_chrome / screen_a_done / resume / unknown`），未知阶段改为中文兜底或隐藏，不再回退显示原始 stage。
- [X] T008 [P] [US2] 在 `webui/app.py` 的 `/api/latest-running-task` 内存任务分支返回 `stage`（优先任务注册的规范阶段，缺失时用 `progress.stage` 并交由前端标签/兜底处理）。
- [X] T009 [US2] 在 `webui/src/components/__tests__/TaskContinue.spec.ts`、`webui/src/components/__tests__/RecrawlContinue.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts` 增加/更新阶段标签与刷新接回用例，断言不出现原始英文 stage。

**Checkpoint**: US2 可独立验证，任何路径不出现英文阶段。

---

## Phase 4: User Story 3 - 应用内更新失败只显示中文与下一步 (Priority: P1)

**Goal**: 更新下载、校验、重启失败只显示稳定中文原因和动作。

**Independent Test**: 模拟下载失败、SHA256 缺失/不匹配、重启脚本启动失败，断言弹窗无异常文本且日志含原始异常。

### Implementation for User Story 3

- [X] T010 [P] [US3] 修改 `webui/updater.py` 的 `UpdateDownloader`：失败状态只保存稳定错误码（如 `download_failed / sha256_unavailable / sha256_mismatch / invalid_download_url`），原始异常通过 logger 记录。
- [X] T011 [P] [US3] 修改 `webui/app.py` 的 `/api/update-restart`：启动失败返回稳定 `error_code` 与中文 `user_message`，原始异常通过 logger 记录。
- [X] T012 [US3] 修改 `webui/src/components/UpdateDialog.vue` 的 `failureMessage`：覆盖下载/校验/重启稳定码，输出中文原因与动作，默认中文兜底。
- [X] T013 [US3] 新增/更新测试：`tests/test_updater.py` 断言稳定错误码且不含 `download_failed: {exc}`；`tests/test_webui_app.py` 断言重启失败返回中文；新建 `webui/src/components/__tests__/UpdateDialog.spec.ts` 断言界面无异常类型/堆栈/英文 code。

**Checkpoint**: US3 可独立验证，更新失败弹窗全部可读。

---

## Phase 5: User Story 4 - AI 设置失败提示可读 (Priority: P1)

**Goal**: AI 连接测试与模型列表失败只显示中文原因。

**Independent Test**: 模拟认证、网络、超时、服务端、未知错误，断言弹窗无原始错误码。

### Implementation for User Story 4

- [X] T014 [P] [US4] 修改 `webui/app.py` 的 `/api/ai-settings/test` 与 `/api/ai-settings/models`：失败时返回纯中文 `user_message`；同步把 `webui/ai.py` 的 `user_facing_error` 未知码兜底改为不含 `error_code` 的纯中文（或由前端做纯中文兜底），内部错误码仅用于日志/状态。
- [X] T015 [US4] 修改 `webui/src/components/AiSettingsDialog.vue`：只展示后端中文 `user_message` 或中文兜底，删除界面中拼接 `error_code` 与 `warning_codes` 的文案。
- [X] T016 [US4] 更新 `tests/test_workbench_api.py` 与 `tests/test_webui_app.py`，断言 AI 失败返回中文 `user_message`；新建 `webui/src/components/__tests__/AiSettingsDialog.spec.ts`，断言界面不出现 `auth_failed / network_error` 等裸码。

**Checkpoint**: US4 可独立验证，AI 设置失败提示全部可读。

---

## Phase 6: User Story 5 - 桌面版首次运行有完整指引 (Priority: P1)

**Goal**: 主 README 能让普通用户完成桌面版第一次运行。

**Independent Test**: 按 README 桌面版章节逐项核对前置条件、首次启动、Gatekeeper、数据目录、常见排错与版本一致性。

### Implementation for User Story 5

- [X] T017 [P] [US5] 更新 `README.md` 桌面版章节：补 Chrome/Edge、WebView2、首次启动解压延迟、macOS Gatekeeper、数据目录 `~/.career-scout`、常见排错，并修正 `v2.8.2` 引用为 `v2.8.5`。
- [X] T018 [P] [US5] 更新 `packaging/README.md`：对用户首启指引只交叉引用主 README，避免重复维护。
- [X] T019 [US5] 扩展 `tests/test_chrome_setup.py` 的 README 校验：断言首启条目存在且版本引用一致。

**Checkpoint**: US5 可独立验证，普通用户按 README 可完成首次运行。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 收口文档状态、全量回归与真实渲染检查。

- [X] T020 更新 `roadmap/BACKLOG.md`：P1 总览数量由 6 改为 5，实施完成后将 B011/B012/B016/B017/B020 状态更新为已完成/归档。
- [X] T021 [P] 运行全量后端 `uv run python -m unittest discover -s tests`、全量前端 `cd webui && npm test`、`cd webui && npm run build`；功能用例全部通过且 `webui/dist/index.html` 引用新产物，卫生门禁另见 T022。
- [X] T022 运行 `uv run python -m unittest tests.test_repo_hygiene`，作为最终门禁（失败：仅因 spec006 目录、新增测试与 dist 产物未暂存；未授权暂存或提交）。
- [X] T023 对 `TaskProgress.vue`、`UpdateDialog.vue`、`AiSettingsDialog.vue` 在桌面 1440×900 与窄屏 390×844 做真实渲染检查，确认无重叠、无横向溢出、无英文 stage 泄漏。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，可立即执行。
- **US1 (Phase 2)**: 依赖基线通过；后端进度与前端进度引擎完成后验证。
- **US2 (Phase 3)**: 依赖 US1（两者都改 `TaskProgress.vue`），避免同文件并发冲突。
- **US3/US4/US5 (Phase 4-6)**: 依赖基线通过，彼此文件独立，可并行。
- **Polish (Phase 7)**: 依赖全部用户故事完成。

### User Story Dependencies

- **US1**: 无跨故事依赖。
- **US2**: 依赖 US1 完成后执行。
- **US3**: 无跨故事依赖。
- **US4**: 无跨故事依赖。
- **US5**: 无跨故事依赖。

### Parallel Opportunities

- T002 与 T003（后端抓取/筛选百分比）可并行。
- T007 与 T008（前端标签与后端接回 stage）可并行，T009 在其后验证。
- T010 与 T011（更新器与重启接口）可并行，T012/T013 在其后。
- T014 与 T015 按“后端先返回中文、前端后消费”的顺序执行，或契约明确后并行。
- US3、US4、US5 可并行。

## Implementation Strategy

### MVP First (US1 + US2)

1. 完成 Phase 1 基线记录。
2. 实现 US1：后端真实百分比 + 前端进度引擎重写 + 测试重写。
3. 实现 US2：阶段标签兜底 + 接回接口 stage。
4. 聚焦验证 US1+US2，再进入错误文案批次。

### Incremental Delivery

1. US1 + US2 完成并独立验证。
2. US3 + US4 完成后独立验证错误文案。
3. US5 完成后独立验证文档。
4. Polish 做 BACKLOG 状态、全量回归、构建与真实渲染检查。

## Notes

- 本任务清单不含仓库同步动作。
- 所有进度用例以真实事件为准，不保留“时间假爬”测试。
- 原始异常只进日志；用户界面只出现稳定中文与可执行动作。
