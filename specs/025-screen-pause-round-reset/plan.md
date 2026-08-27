# Implementation Plan: 批中暂停二选一 + 暂停断点保全 + 完成态自动新一轮

**Branch**: `025-screen-pause-round-reset` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)（冻结需求 3 条：B076 优化 / B077 Bug / B078 优化）

**Input**: Feature specification from `/specs/025-screen-pause-round-reset/spec.md`

## Summary

三个条目同批交付：**B076** 批中暂停弹二选一（立即停止默认聚焦回车触发 / 等这批抓完），配套批间冷却分段响应停止信号、停止时清理卡死防护批次登记、暂停 API 支持 immediate 模式（编排进新模块 `task_pause_support.py`，`task_continue_api.py` 仅薄组装）且幂等；**B077** 修复暂停导致已抓 JD 断点丢失——批返回后立即处理结果（source 抢救出的已抓不再被重抓分支丢弃）、卡死重抓剔除已抓成功岗位只抓缺失、暂停返回绝不把空结果写进断点（**卡死防护判定与 source 层零改动**，与 B076 边界：立即停止一律作废当前批）；**B078** 完成态启动/刷新自动执行「开始新一轮」逻辑（复用前端 `resetWorkflow`，纯前端、后端零改动，B068 未完成流程恢复现场保留）。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript（前端）

**Primary Dependencies**: Flask、sqlite3、Vue 3、Vite

**Storage**: 子进程产物文件（`RESULT_DIR/pipeline_batch_*.json`，source 边抓边原子写盘）；JD 断点文件（`RESULT_DIR/ai_screen_jd_<run_id>.json`，`_save_jd_checkpoint` 原子写）；sessionStorage workflow 快照（前端）

**Testing**: pytest（后端 unittest）、Vitest（前端）

**Target Platform**: Windows 桌面（源码模式 + PyInstaller EXE 模式）

**Project Type**: desktop-app（本地 Flask 后端 + WebView 前端）

**Performance Goals**: 立即停止 1 秒内任务转「已暂停」且浏览器关闭；批间冷却分段响应（每段 ≤1s 检查停止信号）

**Constraints**: 卡死防护判定逻辑一行不改；门面文件禁改；B078 后端零改动；前端新增组件走现有 Dialog 模式

## Constitution Check

- 原则 VI 模块地图：前端新增 `PauseBatchChoiceDialog.vue`（暂停二选一弹窗）、后端新增 `webui/task_pause_support.py`（暂停编排助手）为新文件，同批次登记进 constitution 模块地图。
- 门面禁改：`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py` 全部禁止；`source_boss_cdp_detail.py` **零改动**（B077 捡回复用其既有抢救逻辑，见技术方案要点 4）。
- **行数红线（存量披露）**：`webui/task_continue_api.py` 存量 791 行（已超 600 预警线、逼近 800 红线）——本次只做最薄组装（api 层解析 mode + 调 `task_pause_support`，约 +8 行，不追加业务逻辑）；`webui/src/views/DiscoveryView.vue` 存量 1232 行（已超 1200 红线）——本次仅最小组装（挂载弹窗组件 + onMounted 一行判定调用，约 +5 行，判定逻辑全部放 composables）。两文件拆分另行立项（不在本期范围，本期承诺不扩大其职责内聚度）。
- 行数边界：`pipeline_exec_details.py` 424→约 490 行、`pipeline_guard.py` 375→约 395 行、`ai_screen_jd.py` 248→约 270 行，均在 600 预警线下；新增文件 ≤400 行；前端 composables 无宪法行数约束（021 B8 拆分产物，本次增量小）。
- 引用方向：后端 `runners/ai_screen_jd.py → pipeline_exec_details.py → pipeline_guard.py`；`task_continue_api.py → task_pause_support.py → ctx.pipeline_guard`（经 ctx 注入，不反向 import）；前端 `DiscoveryView.vue → useScreenRoundFlow.ts → PauseBatchChoiceDialog.vue`。

## File Boundaries

*GATE: Must be completed before tasks. 按冻结需求（范围 = AI 筛选暂停体验 + 断点保全 + 完成态新一轮）。*

- **Allowed files**:
  - `webui/task_continue_api.py` — 仅薄组装：`api_task_pause` 解析 body mode 并转发 `task_pause_support`、`api_task_cancel` 调取消清理助手，约 +8 行（存量 791 行超预警线，只做组装不追加业务逻辑）
  - `webui/pipeline_exec_details.py` — 批返回后立即处理结果（抢救已抓并入）、重抓剔除已抓成功岗位（只抓缺失）、immediate 作废、批返回窗口停止保全、冷却分段响应、批内信号回调，约 +60 行
  - `webui/pipeline_guard.py` — 新增 `immediate_stop_task(task_id)`（杀子进程 + 清理登记），约 +20 行；判定/监控逻辑零改动
  - `webui/runners/ai_screen_jd.py` — stopped 路径 return 前保全断点（有已抓才落盘、绝不写空）、批内信号 emit 传递，约 +15 行
  - `webui/src/composables/useScreenRoundFlow.ts` — pauseScreen 加批中弹窗分支（判定批内信号 → 弹二选一 → 按选择调 API）+ 弹窗状态监听（批完成/已停自动关闭），约 +50 行
  - `webui/src/composables/useDiscoveryTasks.ts` — 暴露完成态判定/触发辅助（复用 `fetchMergedLatestResult` 与 `resetWorkflow`，不新写重置逻辑），约 +15 行
  - `webui/src/views/DiscoveryView.vue` — 仅最小组装：onMounted finally 分支调一行完成态判定、挂载 PauseBatchChoiceDialog（import + tag + 事件），约 +5 行（存量 1232 行超红线，判定逻辑全部在 composables）
  - `.specify/memory/constitution.md` — 模块地图登记 2 个新文件
  - `tests/` — 新增聚焦测试文件
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/boss/`、`webui/store_migrations*.py`、`webui/error_registry.py`（只读复用）、`webui/source_boss_cdp_detail.py`（零改动）、`webui/pipeline_guard.py` 的判定/监控逻辑（`_monitor_loop`/`scan_once`/`_mark_stalled`/`_divert`/`_maybe_fallback_pause` 一行不改）
- **New files**:
  - `webui/task_pause_support.py` — 暂停编排助手：mode 分支编排、immediate 幂等判定、立即停止信号标记、与 guard 联动（immediate_stop_task / 取消清理）（约 80 行）
  - `webui/src/components/PauseBatchChoiceDialog.vue` — 批中暂停二选一弹窗（约 180 行）
  - `tests/test_pipeline_pause_guard.py` — 暂停 API mode/immediate/幂等、guard 停止清理、冷却分段响应、批返回后处理提前/重抓剔除/immediate 作废/停止保全、空结果不写断点、判定参数不变
  - `webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`（扩展现有）— 批中弹窗分支、非批中直接停、弹窗过时自动关闭
  - `webui/src/components/__tests__/PauseBatchChoiceDialog.spec.ts` — 二选一渲染、默认聚焦、回车触发、进度与文案
  - `webui/src/views/__tests__/DiscoveryView.spec.ts`（扩展现有）— 完成态启动自动 resetWorkflow（含 completed 终态任务场景）、未完成态恢复现场不变
- **Reference direction**: 后端单向 `runners/ai_screen_jd.py → pipeline_exec_details.py → pipeline_guard.py`；`task_continue_api.py → task_pause_support.py → ctx.pipeline_guard`；前端 `DiscoveryView.vue → useScreenRoundFlow → PauseBatchChoiceDialog.vue`、`DiscoveryView.vue → useDiscoveryTasks.resetWorkflow`（保持现有组合方向）。
- **Line gate**: `task_continue_api.py` 本次增量 ≤8 行（仅组装）、`DiscoveryView.vue` 本次增量 ≤5 行（仅组装）；其余改动文件 ≤600 行预警线；新增文件 ≤400 行；`source_boss_cdp_detail.py` 零改动。
- **Rationale**: B076/B077 改动全部落在既有暂停/抓取链路模块（task_continue_api 薄组装 + task_pause_support 新模块、pipeline_exec_details、runners/ai_screen_jd、pipeline_guard），source 层零改动（抢救逻辑既有）；B078 复用前端既有 resetWorkflow，不新写恢复逻辑；新增弹窗组件参照 LogViewerDialog 的 Dialog 模式；行数超线文件只做组装不追加逻辑，拆分另行立项。

## 技术方案要点（按冻结需求）

**1. 批内信号（FR-001/FR-002）**
- `fetch_job_details` 新增可选回调 `batch_progress(current_batch, total_batches)`：每批开始时调用（置"批内"信号），批结束/停止时调用（清信号）；
- `run_jd_stage` 的进度 emit 携带批内信号（如 `progress.jd_batch: {current, total} | null`），经任务快照透传前端；
- 前端 `pauseScreen()`：snapshot.progress.stage === "fetch_jd" 且 `jd_batch` 非空 → 弹 PauseBatchChoiceDialog；粗筛（screen_a/ai_rough）/精筛（ai_fine）/批间（无批内信号）→ 不弹窗直接调暂停 API（graceful）。

**2. 暂停 API mode 参数（FR-003/FR-004/FR-008）**
- 新增 `webui/task_pause_support.py`（暂停编排助手，≤400 行）：
  - `apply_pause_mode(ctx, task, run_id, mode)`：解析 mode（缺省 graceful，旧前端兼容）；
  - graceful：现状行为（stop_mode="pause" + stop_event.set()，worker 在安全边界停）；
  - immediate：stop_mode="pause" + stop_event.set() + `stop_event.immediate = True`（信号标记，fetch_job_details 据此作废当前批）+ 调 `ctx.pipeline_guard.immediate_stop_task(run_id)`（终止活动批子进程 + 清理登记）；
  - 幂等：任务已 paused/已 immediate → 直接返回 `{"ok": true}`（不 409）；graceful 保持现状错误码。
- `task_continue_api.py` 的 `api_task_pause` 仅做：读 body mode + 调 helper + 组装响应（薄组装，+8 行）；`api_task_cancel` 追加调取消清理助手（guard 清理批次登记）。

**3. 立即停止作废当前批（FR-003/FR-012）**
- `fetch_job_details` 批返回后立即检查（在结果处理**之前**）：`stop_event.is_set() and getattr(stop_event, "immediate", False)` → 当前批结果作废（不并入 jd_by_idx）→ stopped=True；
- 批返回窗口普通停止（非 immediate）：结果处理已先行完成（要点 4）→ 已抓保全，直接 stopped=True；
- `run_jd_stage` stopped 分支：`close_debug_chrome` + `handle_user_stop`（既有）；**return 前若 jd_map 非空则 `save_jd_checkpoint` 落盘、为空则不写（绝不写空断点）**（FR-010）。

**4. 批返回后立即处理结果 + 重抓剔除（FR-009/FR-011，B077 核心）**
- **根因（已核实）**：`fetch_details_batch` 在子进程 returncode≠0（卡死 kill）时已有抢救逻辑，把产物文件已抓的放进返回值（`source_boss_cdp_detail.py` 286-307 行）；但 `fetch_job_details` 的卡死重抓分支（`guard.should_retry` 命中后 `continue`）**跳过**批返回后的结果处理（现 340-381 行），抢救出的已抓从未进入 jd_by_idx 即被丢弃 → 暂停落在重抓窗口时断点无该批已抓 → 从头重抓。
- **修复**：把批返回后的结果处理（outcomes → jd_by_idx / jd_fail）**提前到批返回后立即执行**（在 should_retry/stopped 检查之前），顺序：
  1. 批返回 → immediate 检查（作废则不处理，要点 3）；
  2. hard_stop 检查（现 270-330 行，含浏览器重启重试，位置不变）；
  3. **处理 outcomes 并入 jd_by_idx / jd_fail**（提前执行，抢救的已抓不再被丢弃）；
  4. `guard.should_retry` 命中 → 从重抓列表剔除已抓成功岗位（jd_by_idx 有 jd 的）；剩余非空 → 等 3~5s 重抓剩余（重抓用新产物文件）；剩余为空（卡死前已全部抓完）→ 直接 complete_batch 不重抓；
  5. `stop_event.is_set()`（批返回窗口普通停止）→ 已并入保全 → stopped=True → break；
  6. 正常收尾（complete_batch）→ 下一批。
- 卡死防护判定（300s 无心跳、3 次重试、分流）零改动；`source_boss_cdp_detail.py` 零改动。

**5. 批间冷却分段响应（FR-006）**
- `fetch_job_details` 批间冷却 `time.sleep(max(cooldown, 5))` 改为分段 sleep（每段 ≤1s 检查 stop_event），置位即提前退出冷却，随后走既有 stopped 路径。

**6. 停止清理批次登记（FR-007）**
- `pipeline_guard` 新增 `immediate_stop_task(task_id)`：遍历活动批次（begin_batch 已登记 task_id），终止子进程（复用 `ScraperExecutor._terminate_tree`）、将批次置 terminal 并清理登记；
- 供暂停 API immediate（经 task_pause_support）与取消路径调用；监控线程逻辑不动。

**7. 完成态自动新一轮（FR-013/FR-014/FR-015）**
- `DiscoveryView.vue` onMounted 流程（`restoreWorkflowState()` + `restoreRunningTask().finally(...)`），判定逻辑放 composables（useDiscoveryTasks 暴露 `maybeAutoStartNewRound()`），DiscoveryView 仅一行调用：
  - **完成态**（自动新一轮）：无未完成流程（本地无未完成 workflow 快照）且无进行中任务（latest-running-task 无 running/queued/paused/interrupted 任务；**latest-running-task 返回已完成终态任务 completed/completed_with_pending/partial 也属完成态**）且最新历史轮为已完成（复用 `fetchMergedLatestResult` 只查不设判定）→ 调 `resetWorkflow()`（复用「开始新一轮」按钮逻辑）替代 `loadLatestResult`；
  - **未完成态**（恢复现场）：有未完成流程 → 走既有恢复路径（B068 行为不变）；
  - 纯前端，后端零改动；不新增恢复/重置代码，只改变触发时机。
