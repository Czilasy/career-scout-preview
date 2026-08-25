# Implementation Plan: 三档模式数值重设计 + 人形模拟行为 + 风险警示区

**Branch**: `024-mode-presets-humanlike` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/024-mode-presets-humanlike/spec.md`

## Summary

冻结的 14 条需求（grill-me 收口）。核心变更：

1. **三档数值重设计**：稳定/平衡/极限各一套冻结数值，3×3 matrix 结构保留、三规模同值；极限档 = 固化当前自定义值（与 custom 解耦）。因 `webui/execution_config.py` 已达 800 行硬上限，数值表整体外迁至新模块 `webui/mode_configs.py`，`get_mode_config` 在 execution_config 保持 re-export 兼容。
2. **详情抓取人形模拟行为**（加载随机等待 / 人形滚动 / 概率鼠标移动）：内部配置随档位下发，不进 UI。因 `scripts/boss/detail_scrape.py` 已 660 行（超 600 行预警线），模拟实现放新模块 `scripts/boss/detail_simulation.py`（参考 roadmap/boss-zhipin-scraper 的 `human_scroll`/`human_mouse_jitter`），detail_scrape 仅做接线（新参数 + 调用点）。参数经 `scrape_details → --simulation-mode → fetch_details_batch → _build_detail_batch_command → fetch_job_details` 贯通；custom 档与未传参路径零仿真。
3. **任务规模新口径**：总页数 <15 小 / 15~30 中 / >30 大，替换旧 9/49（后端 `classify_task_size` + 前端 `classifyTaskSize` 同步）。
4. **前端**：档位配色（稳定绿/平衡黄/极限红/自定义默认）、模式选择器正下方黄色警示区（极限警告 + 大任务警告，可同时、不可关闭）。因 `DiscoveryView.vue` 已 1169 行（超 900 行预警线），警示区拆新组件 `ModeWarningBanner.vue`，DiscoveryView 仅插入标签。pages 范围 1~9999 → 1~200。
5. **工程项**：README 档位说明、minor 版本提升、验证门禁（聚焦 + 后端全量 + 前端 + build + 卫生）。

## Technical Context

**Language/Version**: Python 3.11（`uv` 管理）、TypeScript + Vue 3（webui/src，Vite 构建）

**Primary Dependencies**: Flask（webui/app.py）、CDP raw protocol（scripts/boss/cdp_session.py）、SQLite（webui/store_* mixins）、Vue 3 + Vitest（前端测试）

**Storage**: SQLite（`mode_config_versions` / `advanced_config_state`，本次不新增表、不迁移）；`advanced_settings.json`（custom 配置载体，本次不改格式）

**Testing**: `python -m unittest`（后端，`uv run`）、Vitest（前端）、`npm run build`

**Target Platform**: Windows 桌面版（PyInstaller）+ 源码模式（`python webui/app.py`）

**Project Type**: 桌面 Web 工作台（后端 Flask + 前端 Vue SPA + CDP 抓取脚本域）

**Performance Goals**: 模拟行为引入的额外延迟 ≤ 档位等待区间上限（极限档 1-2s/岗位），不改变批次与并发吞吐结构。

**Constraints**:
- `webui/execution_config.py`（800 行）、`scripts/boss/detail_scrape.py`（660 行 >600 预警）、`webui/src/views/DiscoveryView.vue`（1169 行 >900 预警）、`webui/src/composables/useDiscoveryState.ts`（972 行）——新增逻辑禁止落入，只允许接线级修改或纯值修改。
- `webui/source_boss_cdp.py`（603 行）仅允许 `_build_detail_batch_command` 加条件参数（接线）。
- `ExecutionConfigSnapshot` 快照契约（10 字段 + digest）不变；`pages` 不进入配置快照（FR-009 维持）。
- safe event 契约不变（模拟行为不新增事件字段、不改 duration_ms 语义）。

**Scale/Scope**: 单仓库功能批次；改动约 10 个后端文件 + 6 个前端文件 + 4 个测试文件 + README/版本。

## Constitution Check

*GATE: Passed before Phase 0（勘察见任务 #1）；重检于 Phase 1 设计（本文件）。*

| 原则 | 结论 |
|---|---|
| I 职责分层 | 档位数据 → `webui/mode_configs.py`（数据域）；模拟行为 → `scripts/boss/detail_simulation.py`（行为域）；警示区 → `ModeWarningBanner.vue`（UI 域）。不向门面/超大文件追加业务逻辑。 |
| II 单文件尺寸边界 | `execution_config.py` 800 行上限 → 删除 `_MODE_CONFIGS`（约 120 行）外迁，行数下降；`detail_scrape.py`/`DiscoveryView.vue` 超预警线 → 新增逻辑全部放新文件，既有文件只做接线（≤10 行/文件）。 |
| III 引用方向 | `store_config → execution_config → mode_configs`；`detail_scrape → detail_simulation`；`pipeline_exec_details → source_boss_cdp_detail → source_boss_cdp → scripts/boss/cli → detail_scrape`；前端 `DiscoveryView → ModeWarningBanner / useDiscoveryState`。无反向依赖。 |
| IV 拆分与重构纪律 | 本次非拆分 Spec，但遵循「新增逻辑落新模块」精神；`execution_config._MODE_CONFIGS` 外迁属数据搬迁（保持 `get_mode_config` 接口与 import 兼容），不改变任何行为契约，不单独立拆分 Spec。 |
| V 验证门禁 | 功能交付 → 聚焦 + 后端全量 + 前端 + build + 卫生（需求 14）。 |
| VI 模块地图与落位 | 高级设置域既有 `webui/store_config.py`（329 行，可改）；但档位**数值**归属 execution_config（超限）→ 开新文件 `webui/mode_configs.py` 并随批次登记进宪法模块地图（见 plan 后置动作）。`scripts/boss/` 域既有 `detail_scrape.py`（超预警）→ 开 `detail_simulation.py`。前端既有组件域 → 开 `ModeWarningBanner.vue`。均符合「找不到合适落位或目标超限时才开新文件」规则。 |

**登记动作**：implement 收口时把 `webui/mode_configs.py`、`scripts/boss/detail_simulation.py`、`webui/src/components/ModeWarningBanner.vue` 三行登记进 `.specify/memory/constitution.md` 模块地图（每行一句话职责 + 批号 024）。

## File Boundaries

*GATE: 已完成（Plan 阶段产出）。*

### 后端

- **新文件 `webui/mode_configs.py`**（约 90 行）：
  - 职责：三档 × 三规模冻结数值表（一档一套、三规模同值）、任务规模阈值常量（`SMALL_TASK_MAX = 14`、`MEDIUM_TASK_MAX = 30`）、`get_mode_config(mode, task_size)`（对 `ExecutionConfigSnapshot.create` 封装，逻辑与现 execution_config 一致）。
  - 引用方向：`webui.execution_config` import 本模块（数据与函数）；`webui.store_config` 仍走 `webui.execution_config`（不直接依赖本模块）。
- **修改 `webui/execution_config.py`**（800 → 约 680 行，净减）：
  - 删除 `_MODE_CONFIGS` 数据块与 `get_mode_config` 本地实现；改为 `from webui.mode_configs import get_mode_config, SMALL_TASK_MAX as _SMALL_MAX, MEDIUM_TASK_MAX as _MEDIUM_MAX`（re-export 保持 `execution_config.get_mode_config` import 兼容）。
  - `classify_task_size` 用新阈值常量；`normalize_scope`/`classify_task_size` 的 docstring 更新为新口径（<15 小 / 15~30 中 / >30 大）。
  - 禁止：不得新增任何其他逻辑。
- **新文件 `scripts/boss/detail_simulation.py`**（约 90 行）：
  - 职责：`SIMULATION_PARAMS`（stable/balanced/extreme → wait_range/scroll_range/mouse_prob，按冻结表 12/13/14）、`resolve_params(mode)`、`simulate_after_load(ws, sid, *, params, sleeper, label_prefix="")`——随机等待（`sleeper(uniform(*wait_range), label=...)`）→ 人形滚动（`scroll_range` 内随机次数，每次 `window.scrollBy` 随机距离、间隔 0.8-1.8s、偶尔回滚，参考 roadmap `human_scroll`）→ 概率鼠标移动（`random.random() < mouse_prob` 时 `Input.dispatchMouseEvent` 随机坐标，参考 `human_mouse_jitter`）。
  - 引用方向：`scripts.boss.detail_scrape` import 本模块；本模块不得 import detail_scrape。
- **修改 `scripts/boss/detail_scrape.py`**（660 → 约 680 行，接线级）：
  - `scrape_details(..., simulation_mode: str | None = None)` 新参数；`_scrape_one_detail` / `_scrape_detail_on_tab` / `_tab_worker` 增加 `simulation_mode` 透传。
  - 调用点：两个抓取函数在 `_wait_for_detail_readiness` 之后、`EXTRACT_DETAIL_JS` 之前，`if simulation_mode:` 时调用 `simulate_after_load`。
  - 禁止：不得改动 readiness 探针、事件契约、限流/登录墙路径。
- **修改 `scripts/boss/cli.py`**（438 → 约 450 行）：`--simulation-mode {stable,balanced,extreme}`（可选，默认 None），detail 分支传给 `scrape_details`。
- **修改 `webui/source_boss_cdp.py`**（603 → 约 610 行，仅 `_build_detail_batch_command`）：加 `simulation_mode: str | None = None` 参数，非 None 时命令追加 `--simulation-mode <mode>`（保持 None 时命令字节级不变，保护 test_webui_app.py 既有命令断言）。
- **修改 `webui/source_boss_cdp_detail.py`**（768 → 约 790 行）：`fetch_details_batch(..., simulation_mode=None)` → 传 `_build_detail_batch_command`；`_translate_detail_batch_argv` 解析 `simulation-mode` → `scrape_details` params。
- **修改 `webui/pipeline_exec_details.py`**（413 → 约 420 行）：`fetch_job_details(..., simulation_mode=None)` → 传 `source.fetch_details_batch(simulation_mode=...)`。
- **修改 `webui/runners/ai_screen_jd.py`**：`run_jd_stage` 内取 `ctx.store.get_advanced_config_state()["active_selection"]`，非 custom 时传 `fetch_job_details(simulation_mode=selection)`。
- **修改 `webui/runners/recrawl_task.py`**：`fetch_job_details` 调用点同规则取当前档位传参（无 store 时留 None）。
- **修改 `webui/pipeline_jobs_api.py`**：`fetch_job_details` 复用点同规则（可选，None 兜底）。

### 前端

- **新文件 `webui/src/components/ModeWarningBanner.vue`**（约 80 行）：
  - 职责：黄色警示区（一行字横幅、固定显示、不可关闭、无叉）；`props: { extremeWarning: boolean; largeTaskWarning: boolean }`；两条都满足时分行显示；都不满足时 v-if 整体隐藏。黄色系样式（深色主题下黄底/黄字均可，取既有 CSS 变量 + 显式黄色）。
- **修改 `webui/src/components/ExecutionModeSelector.vue`**（109 → 约 130 行）：按 `modelValue` 给激活按钮加档位色 class（stable=绿、balanced=黄、extreme=红、custom=默认 brand 色）。
- **修改 `webui/src/composables/useDiscoveryState.ts`**（972 行，纯值修改 1 行）：`advancedRanges.pages: [1, 9999]` → `[1, 200]`。禁止新增逻辑。
- **修改 `webui/src/discovery.ts`**（335 → 约 340 行）：`classifyTaskSize` 阈值 9/49 → 14/30（docstring 同步）。
- **修改 `webui/src/views/DiscoveryView.vue`**（1169 → 约 1178 行，接线级）：
  - `ExecutionModeSelector` 正下方插入 `<ModeWarningBanner :extreme-warning="..." :large-task-warning="..." />`。
  - 计算属性：`extremeWarning = executionSelection === "extreme"`；`largeTaskWarning = scopePreview 的 planned_pages > 30`（或后端返回的 `task_size === "large"`，以后端权威为准；scopePreview 不可用/未确认时 false）。
  - 禁止：不得在视图内新增任何其他逻辑。

### 测试与文档

- **修改 `tests/test_execution_config.py`**：规模边界（14→small、15→medium、30→medium、31→large、201 拒绝）+ 三档新数值/三规模同值/extreme=固化值/pages 不在快照 + custom 解耦断言（走 store_config 或快照对比）。
- **修改 `webui/src/__tests__/discovery.spec.ts`**：`classifyTaskSize` 边界 14/15/30/31。
- **修改 `webui/src/components/__tests__/ExecutionModeSelector.spec.ts`**：档位配色 class 断言。
- **新增 `webui/src/components/__tests__/ModeWarningBanner.spec.ts`**：四种状态渲染 + 无关闭按钮。
- **修改 `tests/test_webui_app.py`**：`_build_detail_batch_command` 带/不带 simulation-mode 的命令断言。
- **修改 README.md**：档位说明章节（三档数值表、模拟行为、pages 范围 1~200、警示语义、custom 与极限解耦）。
- **版本**：`uv run python scripts/bump_version.py minor`（同步 pyproject.toml / webui/package.json / webui/package-lock.json / uv.lock / scripts/boss_cdp_raw.py / tests/test_desktop_shell.py / README 标题 + CHANGELOG）。

### 禁止修改（Forbidden）

- `webui/app.py`、`webui/store.py`、`scripts/boss_cdp_raw.py`（门面，宪法 VI 只允许 re-export/组装）。
- `ExecutionConfigSnapshot` 的字段/校验/digest 语义（10 字段 + schema_version 不变）。
- `mode_config_versions` 数据库结构与既有活动版本 matrix（Assumption A2）。
- `advanced_settings.json` 格式与 `save_custom_config` 语义。
- safe event 结构（`_EVENT_REQUIRED_FIELDS`/`_EVENT_FORBIDDEN_FIELDS`）。
- 智联链路（`scripts/zhilian_cdp_raw.py`、`webui/source_zhilian_*`）——本期不涉及。

### 引用方向

`store_config → execution_config → mode_configs`；`detail_scrape → detail_simulation`；`pipeline_exec_details → source_boss_cdp_detail → source_boss_cdp → scripts/boss/cli → detail_scrape`；前端 `DiscoveryView → ModeWarningBanner`、`DiscoveryView/useDiscoveryState → discovery.ts`。均单向向下，无反向 import。

### 行数门禁

- 修改文件目标：`execution_config.py` 净减至 ≤700；`detail_scrape.py` ≤690（接线）；`DiscoveryView.vue` ≤1190（接线）；`useDiscoveryState.ts` 不增长；`source_boss_cdp.py` ≤615（仅命令函数）；`source_boss_cdp_detail.py` ≤800；其余均在限内。
- 新文件规模：mode_configs ≤120、detail_simulation ≤120、ModeWarningBanner ≤120。

### Rationale

- 三档数值落地的最小合规路径是外迁数据而非就地改写：`execution_config.py` 已到 800 硬上限，宪法 VI 要求新逻辑/新数据落新模块；`get_mode_config` 作为既有公开接口在 execution_config re-export，`store_config` 与测试的 import 面零改动。
- 模拟行为实现放 `detail_simulation.py`：`detail_scrape.py` 超 600 预警线，新增滚动/鼠标逻辑（含随机停顿循环）约 60-80 行，必须分流；detail_scrape 只保留参数接线与调用点。
- 警示区拆 `ModeWarningBanner.vue`：`DiscoveryView.vue` 超 900 预警线，新增 UI 块 + 样式放独立组件，视图仅插标签与两个计算属性。
- 模拟参数用「独立参数贯通」而非改 `ExecutionConfigSnapshot`：快照 digest 契约与 10 字段 schema 不可变（改字段会破坏既有 digest/迁移），且 custom 档无对应模拟行为，语义上 mode 与速度字段解耦更干净。

## Verification Gate

*GATE: 已完成（Plan 阶段产出）。*

- 功能交付门禁（需求 14）：相关模块聚焦测试（test_execution_config、detail_simulation/scrape、source_boss_cdp_detail、test_webui_app 命令断言、前端 discovery/ExecutionModeSelector/ModeWarningBanner/DiscoveryView）+ 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene` + hooks）。
- minor 版本提升为收口操作：`scripts/bump_version.py minor` 后校验清单一致性（`--check --expect`），提交/推送走收口验证（卫生测试、hooks、`git diff --check`、`git status`）。
- 不打包、不 Release。

## Project Structure

### Documentation (this feature)

```text
specs/024-mode-presets-humanlike/
├── spec.md              # 冻结需求 → 用户故事/FR/成功标准/假设
├── plan.md              # 本文件
├── research.md          # Phase 0 调研结论（链路、参考实现、边界）[本文件已内联核心结论]
└── tasks.md             # Phase 2 任务分解（/speckit-tasks 产出）
```

### Source Code（本批次实际布局）

```text
# 后端新增
webui/mode_configs.py                    # 三档数值表 + 规模阈值 + get_mode_config
scripts/boss/detail_simulation.py        # 模拟行为参数 + simulate_after_load

# 后端修改（接线/外迁）
webui/execution_config.py                # 删 _MODE_CONFIGS，re-export get_mode_config，新阈值
scripts/boss/detail_scrape.py            # simulation_mode 参数 + 2 处调用点
scripts/boss/cli.py                      # --simulation-mode
webui/source_boss_cdp.py                 # _build_detail_batch_command + simulation_mode
webui/source_boss_cdp_detail.py          # fetch_details_batch + 翻译器
webui/pipeline_exec_details.py           # fetch_job_details + simulation_mode
webui/runners/ai_screen_jd.py            # run_jd_stage 取档位
webui/runners/recrawl_task.py            # 同规则取档位
webui/pipeline_jobs_api.py               # 同规则（None 兜底）

# 前端新增
webui/src/components/ModeWarningBanner.vue

# 前端修改（接线/值修改）
webui/src/components/ExecutionModeSelector.vue   # 档位配色
webui/src/composables/useDiscoveryState.ts       # pages 范围 1 行
webui/src/discovery.ts                           # classifyTaskSize 阈值
webui/src/views/DiscoveryView.vue                # 插入警示区

# 测试与文档
tests/test_execution_config.py  webui/src/__tests__/discovery.spec.ts
webui/src/components/__tests__/ExecutionModeSelector.spec.ts
webui/src/components/__tests__/ModeWarningBanner.spec.ts
tests/test_webui_app.py  README.md  CHANGELOG.md
```

**Structure Decision**: 沿用既有单仓库布局（webui/ 服务端 + webui/src/ 前端 + scripts/boss/ 抓取域 + tests/）。新增 3 个文件均为既有域的「超限分流」模块（见 Rationale），不引入新目录。

## Complexity Tracking

*无需填表：Constitution Check 无违规项需豁免（外迁/分流均为宪法 VI 明确要求的路径，非违规）。*
