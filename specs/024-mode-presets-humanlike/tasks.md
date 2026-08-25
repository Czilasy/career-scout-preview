---
description: "024-mode-presets-humanlike 任务分解"
---

# Tasks: 三档模式数值重设计 + 人形模拟行为 + 风险警示区

**Input**: Design documents from `/specs/024-mode-presets-humanlike/`（spec.md + plan.md）

**Prerequisites**: plan.md（含 File Boundaries 与 Constitution Check）、spec.md

**Tests**: 按 Spec 需求 14，本批次为功能交付，测试为必须项（聚焦 + 后端全量 + 前端 + build + 卫生）。

**Organization**: 按 User Story 分组；US1-US4 可并行，US5/US6 依赖前者。

## File Boundaries

- **Allowed files**：`webui/mode_configs.py`（新）、`webui/execution_config.py`、`scripts/boss/detail_simulation.py`（新）、`scripts/boss/detail_scrape.py`、`scripts/boss/cli.py`、`webui/source_boss_cdp.py`（仅 `_build_detail_batch_command`）、`webui/source_boss_cdp_detail.py`、`webui/pipeline_exec_details.py`、`webui/runners/ai_screen_jd.py`、`webui/runners/recrawl_task.py`、`webui/pipeline_jobs_api.py`、`webui/src/components/ModeWarningBanner.vue`（新）、`webui/src/components/ExecutionModeSelector.vue`、`webui/src/composables/useDiscoveryState.ts`（仅 pages 范围 1 行）、`webui/src/discovery.ts`、`webui/src/views/DiscoveryView.vue`（仅插入警示区 + 2 个计算属性）、`tests/test_execution_config.py`、`tests/test_webui_app.py`（仅命令断言）、`webui/src/__tests__/discovery.spec.ts`、`webui/src/components/__tests__/ExecutionModeSelector.spec.ts`、`webui/src/components/__tests__/ModeWarningBanner.spec.ts`（新）、`webui/src/views/__tests__/DiscoveryView.spec.ts`（警示区相关）、README.md、CHANGELOG.md、版本清单文件（pyproject.toml / webui/package.json / webui/package-lock.json / uv.lock / scripts/boss_cdp_raw.py / tests/test_desktop_shell.py）
- **Forbidden files**：`webui/app.py`、`webui/store.py`、`scripts/boss_cdp_raw.py`（门面禁改）；`ExecutionConfigSnapshot` 字段/校验/digest 语义；`mode_config_versions` 结构与既有 matrix；`advanced_settings.json` 格式；safe event 结构；`scripts/zhilian_cdp_raw.py` 与 `webui/source_zhilian_*`；`.specify/memory/constitution.md`（仅收口登记 3 行模块地图，不属实现任务）
- **Reference direction**：`store_config → execution_config → mode_configs`；`detail_scrape → detail_simulation`；`pipeline_exec_details → source_boss_cdp_detail → source_boss_cdp → scripts/boss/cli → detail_scrape`；前端 `DiscoveryView → ModeWarningBanner / useDiscoveryState`；无反向 import。
- **Line gate**：`execution_config.py` 净减 ≤700；`detail_scrape.py` ≤690；`DiscoveryView.vue` ≤1190；`useDiscoveryState.ts` 不增长；`source_boss_cdp.py` ≤615；`source_boss_cdp_detail.py` ≤800；新文件各 ≤120。

## Verification Gate

- 功能交付：聚焦测试（本次涉及全部改动模块）+ 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene` + hooks）。
- 版本 minor 提升为收口操作（T016），完成后提交/推送走收口验证（卫生 + hooks + `git diff --check` + `git status`），不跑全量测试。

---

## Phase 1: Foundational — 配置数据外迁（US1 依赖，阻断后续）

**Purpose**: 三档数值脱离 800 行上限文件，落到 `mode_configs.py`，为 US1/US2 提供数据地基。

**⚠️ CRITICAL**: US1（档位数值）、US2（模拟行为参数）都依赖本阶段。

- [ ] T001 [US1] 新建 `webui/mode_configs.py`：`MODE_CONFIGS`（stable/balanced/extreme 三档 × 三规模，三规模同值，按冻结表 #2-#11）、`SMALL_TASK_MAX = 14` / `MEDIUM_TASK_MAX = 30`、`get_mode_config(mode, task_size)`（复用 `ExecutionConfigSnapshot.create` 语义）。只搬数据与函数，不 import webui.store。
- [ ] T002 [US1] 改造 `webui/execution_config.py`：删除本地 `_MODE_CONFIGS` 数据块与 `get_mode_config` 实现；`from webui.mode_configs import get_mode_config, SMALL_TASK_MAX as _SMALL_MAX, MEDIUM_TASK_MAX as _MEDIUM_MAX`；`__all__` 保持 `get_mode_config`；`classify_task_size` 使用新常量；`normalize_scope`/`classify_task_size` docstring 更新为「<15 小 / 15~30 中 / >30 大」。禁止新增任何其他逻辑。
- [ ] T003 [P] [US1] 更新 `tests/test_execution_config.py`：规模边界（14→small、15→medium、30→medium、31→large、200→large、201 拒绝）；三档新数值与三规模同值断言；extreme = 固化值（10/30/2/4/5/5/50/5/5/5 对照 inter_combo_delay/detail_batch_size/detail_interval/detail_reset_every/detail_batch_cooldown/detail_tab_pool_size/screen_batch_size/screen_concurrency/match_batch_size/match_concurrency）；pages 不在快照（既有断言保留）；`get_mode_config` 仍可经 `execution_config` 访问（import 兼容）。

**Checkpoint**: `uv run python -m unittest tests.test_execution_config` 全绿；`execution_config.py` 行数下降。

---

## Phase 2: User Story 1 — 三档数值落地与 custom 解耦 (P1) 🎯 MVP

**Goal**: 三档新数值生效（无活动模式版本路径），极限档固化，custom 改动不影响极限档。

**Independent Test**: `get_mode_config(mode, task_size)` 数值断言 + `store_config.select_mode` 的 custom/extreme 解耦。

### Tests for User Story 1

- [ ] T004 [P] [US1] `tests/test_execution_config.py` 补充 custom 解耦测试：`store_config.select_mode("extreme", task_size)` 后 `save_custom_config(改值)` 再 `select_mode("extreme")`，断言 extreme 数值不变（或走 `get_mode_config` + `get_advanced_config_state` 组合断言）。
- [ ] T005 [P] [US1] `tests/test_webui_store.py` 既有模式选择用例回归运行确认（无改动预期，仅执行验证）。

### Implementation for User Story 1

- [ ] T006 [US1] 验证 `webui/store_config.py` 无需改动：`select_mode` 的 matrix/默认路径逻辑不变，仅数据变化（T001 已落地）。若发现 matrix 槽位或默认路径引用被破坏，修复并记录。

**Checkpoint**: US1 独立可用——三档数值、三规模同值、极限固化、custom 解耦全部有测试证据。

---

## Phase 3: User Story 2 — 详情抓取人形模拟行为（内部，无 UI）(P1)

**Goal**: stable/balanced/extreme 档详情抓取按冻结表 12/13/14 执行模拟行为；custom/未传参零仿真。

**Independent Test**: fake CDP 会话 + 注入 sleeper/随机源断言等待区间、滚动次数、鼠标概率；CLI/in-process 翻译断言 `--simulation-mode` 贯通。

### Tests for User Story 2 ⚠️ 先写测试（预期 FAIL）

- [ ] T007 [P] [US2] 新建 `tests/test_detail_simulation.py`：`SIMULATION_PARAMS` 三档数值断言；`simulate_after_load` 在 fake 会话上断言 sleeper 调用时长落在区间、scrollBy 调用次数落在区间（注入固定随机种子）、mouse 事件按概率出现（概率 0.0/1.0 边界 + 0.3/0.5 种子化）。
- [ ] T008 [US2] `tests/test_webui_app.py` `_build_detail_batch_command` 断言扩展：传 `simulation_mode="stable"` 时命令含 `--simulation-mode stable`；不传时命令与现状字节一致（既有断言不破坏）。

### Implementation for User Story 2

- [ ] T009 [P] [US2] 新建 `scripts/boss/detail_simulation.py`：`SIMULATION_PARAMS`、`resolve_params(mode)`、`simulate_after_load(ws, sid, *, params, sleeper, label_prefix="")`——随机等待（`uniform(*wait_range)`，sleeper label=`sim_load_wait`）→ 人形滚动（`randint(*scroll_range)` 次，`window.scrollBy` 随机距离 ±、间隔 0.8-1.8s、偶尔回滚，参考 roadmap `human_scroll`）→ 概率鼠标移动（`Input.dispatchMouseEvent` 随机坐标，参考 `human_mouse_jitter`）。滚动/鼠标调用失败须吞异常（不影响抓取主流程）。
- [ ] T010 [US2] `scripts/boss/detail_scrape.py` 接线：`scrape_details` 加 `simulation_mode: str | None = None`；`_scrape_one_detail` / `_scrape_detail_on_tab` / `_tab_worker` 透传；两处调用点位于 `_wait_for_detail_readiness` 之后、`EXTRACT_DETAIL_JS` 之前（`if simulation_mode:` 守卫）。不改 readiness/事件/限流路径。
- [ ] T011 [US2] `scripts/boss/cli.py`：detail 相关参数区加 `--simulation-mode {stable,balanced,extreme}`（默认 None，choices 校验），detail 分支传入 `scrape_details`。
- [ ] T012 [US2] `webui/source_boss_cdp.py`：`_build_detail_batch_command` 加 `simulation_mode: str | None = None`，非 None 时命令追加 `--simulation-mode <mode>`。
- [ ] T013 [US2] `webui/source_boss_cdp_detail.py`：`fetch_details_batch` 加 `simulation_mode=None` → 传 `_build_detail_batch_command`；`_translate_detail_batch_argv` 解析 `simulation-mode` → `scrape_details` params。
- [ ] T014 [US2] `webui/pipeline_exec_details.py`：`fetch_job_details` 加 `simulation_mode=None` → 传 `source.fetch_details_batch(simulation_mode=...)`。
- [ ] T015 [US2] 调用方取档位：`webui/runners/ai_screen_jd.py` `run_jd_stage` 在创建 source 后取 `ctx.store.get_advanced_config_state()["active_selection"]`，非 custom 时传 `fetch_job_details(simulation_mode=selection)`；`webui/runners/recrawl_task.py` 与 `webui/pipeline_jobs_api.py` 复用点同规则（取不到/自定义时 None）。

**Checkpoint**: US2 独立可用——模拟行为测试全绿、`--simulation-mode` 链路贯通、None 路径回归通过。

---

## Phase 4: User Story 3 — 配色 + 黄色警示区 (P1)

**Goal**: 档位配色（稳定绿/平衡黄/极限红/自定义默认）；模式选择器正下方黄色警示区（极限警告 + 大任务警告，可同时、不可关闭、无叉）。

**Independent Test**: `ModeWarningBanner.spec.ts` 四种状态；`ExecutionModeSelector.spec.ts` 配色 class；`DiscoveryView.spec.ts` 警示区联动。

### Tests for User Story 3 ⚠️ 先写测试（预期 FAIL）

- [ ] T016 [P] [US3] 新建 `webui/src/components/__tests__/ModeWarningBanner.spec.ts`：仅极限警告 / 仅大任务 / 同时两条 / 都无（隐藏）四状态渲染 + 断言无关闭按钮。
- [ ] T017 [P] [US3] `webui/src/components/__tests__/ExecutionModeSelector.spec.ts`：各档位激活按钮配色 class 断言（stable/balanced/extreme/custom）。
- [ ] T018 [US3] `webui/src/views/__tests__/DiscoveryView.spec.ts`：选择极限档 → 极限警告出现；scopePreview planned_pages>30（或 task_size=large）→ 大任务警告出现；两者组合；custom+小任务 → 隐藏。

### Implementation for User Story 3

- [ ] T019 [P] [US3] 新建 `webui/src/components/ModeWarningBanner.vue`：`props: { extremeWarning: boolean; largeTaskWarning: boolean }`；黄色横幅样式（黄色系，深色主题可读）；两条分行；都无则 `v-if` 隐藏；无关闭按钮/叉。
- [ ] T020 [P] [US3] `webui/src/components/ExecutionModeSelector.vue`：激活按钮按 `modelValue` 加档位色 class（stable=绿、balanced=黄、extreme=红、custom=默认 brand 色）。
- [ ] T021 [US3] `webui/src/views/DiscoveryView.vue`：`ExecutionModeSelector` 正下方插入 `<ModeWarningBanner>`；新增两个计算属性（`extremeWarning = executionSelection === "extreme"`；`largeTaskWarning = scopePreview 的 planned_pages > 30`，scopePreview 不可用时为 false）。

**Checkpoint**: US3 独立可用——四种状态渲染测试全绿，配色生效。

---

## Phase 5: User Story 4 — 规模新口径 + pages 范围 (P1)

**Goal**: 前后端规模阈值 <15/15-30/>30（替换 9/49）；pages 输入范围 1~200。

**Independent Test**: 前后端边界测试 + pages 输入断言。

### Tests for User Story 4 ⚠️ 先写测试（预期 FAIL）

- [ ] T022 [P] [US4] `webui/src/__tests__/discovery.spec.ts`：`classifyTaskSize` 边界 14→small、15→medium、30→medium、31→large（替换旧 9/49 用例）。

### Implementation for User Story 4

- [ ] T023 [P] [US4] `webui/src/discovery.ts`：`classifyTaskSize` 阈值 9/49 → 14/30，docstring 同步。
- [ ] T024 [US4] `webui/src/composables/useDiscoveryState.ts`：`advancedRanges.pages: [1, 9999]` → `[1, 200]`（仅此 1 行值修改）。

**Checkpoint**: US4 独立可用——前后端规模口径一致、pages 范围生效。

---

## Phase 6: User Story 5 — 发布后保持档位选择 (P2)

**Goal**: 升级后 active_selection（当前 custom）不被重置。

- [ ] T025 [US5] 回归确认：无迁移逻辑改写 `advanced_config_state`（T002 仅删数据块 + 改阈值，不触碰 store 层）；`tests/test_webui_store.py` 模式选择与 `tests/test_tuning.py` 的 active_selection 用例运行通过即为证据。

**Checkpoint**: US5 由回归测试证明（无新增代码）。

---

## Phase 7: User Story 6 — README + 版本 minor + 验证门禁 (P3)

**Goal**: 文档同步、版本提升、全部门禁通过。

- [ ] T026 [P] [US6] README 新增/更新档位说明章节：三档数值表、模拟行为（内部说明）、pages 范围 1~200、黄色警示语义、极限与 custom 解耦说明。
- [ ] T027 [US6] `uv run python scripts/bump_version.py minor`：同步 pyproject.toml / webui/package.json / webui/package-lock.json / uv.lock / scripts/boss_cdp_raw.py / tests/test_desktop_shell.py / README 标题 + CHANGELOG 条目（简单列表：修复/增加/优化，无标题无加粗）。随后 `--check --expect <新版本>` 校验。
- [ ] T028 [US6] 验证门禁全跑：聚焦测试（本批次全部改动模块）→ 后端全量 `uv run python -m unittest discover -s tests` → 前端 `npm test`（webui/）→ `npm run build`（webui/）→ 卫生 `uv run python -m unittest tests.test_repo_hygiene`。**测试日志/输出重定向一律写入系统临时目录（$env:TEMP），禁止落项目根目录；自产临时文件当轮清理。**
- [ ] T029 [US6] 收口登记：`.specify/memory/constitution.md` 模块地图追加 3 行（`webui/mode_configs.py`、`scripts/boss/detail_simulation.py`、`webui/src/components/ModeWarningBanner.vue`，各一句话职责 + 批号 024）。

**Checkpoint**: 全部门禁绿、版本一致、宪法地图登记完成。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: 无前置，立即开始；**阻断 US1/US2**。
- **US1 (Phase 2)**: 依赖 Phase 1（T001/T002）。
- **US2 (Phase 3)**: 依赖 Phase 1（数值表）；T007-T009 可在 T001 后并行启动；T010-T015 顺序依赖（scrape → cli → source → pipeline → 调用方）。
- **US3 (Phase 4)**: 无后端依赖，可与 Phase 1/2 并行（不同文件）。
- **US4 (Phase 5)**: 无跨阶段依赖，可并行。
- **US5 (Phase 6)**: 依赖 Phase 1 完成（回归证明）。
- **US6 (Phase 7)**: 依赖 US1-US5 全部完成。

### Parallel Opportunities

- T003、T004、T007、T016、T017、T022、T023 等不同文件的测试/实现可并行。
- US3（前端警示区）与 US2（后端模拟行为）完全独立，可并行实施。
- US4（阈值/pages）独立，可并行。

### 建议执行顺序

1. Phase 1（T001→T002→T003）→ 验证 execution_config 测试
2. US2（T007-T009 先行，T010-T015 串行）→ 验证模拟行为链路
3. US3（T016-T021）与 US4（T022-T024）并行
4. US5（T025 回归）→ US6（T026-T029 收口）
5. 提交/推送（收口验证，用户确认后）
