# Implementation Plan: 任务历史浏览安全与界面一致性修复（035 全量重拆版）

**Branch**: `035-task-history-safety` | **Date**: 2026-09-01（同日重拆） | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/035-task-history-safety/spec.md`（重拆版：界面为唯一验收标准）

## Summary

上一轮已修复 B086 主链路（历史浏览污染「已结束」事实）并实现 B087 冒泡、B085 日志滑块；但用户真机发现三个界面问题，本轮按重拆 Spec 全局收口。根因与方案（均已代码实证，证据见 research.md R5-R7）：

1. **真机问题①（03 页残留，FR-010）**：`startScrape`（useDiscoveryExecution.ts:379-386）开新一轮时只重置抓取侧状态，**从不清空上一轮 AI 筛选展示状态**（`screenSnapshot`/`screenTaskId`/`recrawlSnapshot`/`currentRoundStatus`）；恢复链路（`restoreWorkflowState` 从 sessionStorage 整包恢复 screenSnapshot，useDiscoveryWorkflow.ts:182；`restoreRunningTask` 抓取分支 useDiscoveryExecution.ts:293-314 同样不碰）原样带回旧轮数据。唯一清空点 `resetWorkflow`（useDiscoveryTasks.ts:746-750）只有「开始新一轮」路径经过。
   - **修复 D（轮次状态收敛）**：确立「新一轮开始 / 恢复到活任务时，页面展示由任务真实进度推导」——`startScrape` 清空 screen 侧展示状态；`restoreRunningTask` 检测到活的抓取任务时同步清空 screen 侧残留（活的抓取任务本轮进度必在 02，任何 screen 数据必属旧轮）。`screenSnapshot=null` 时 03 页有现成「未开始」空态（TaskProgress 整卡 `v-if="snapshot"`）。
2. **真机问题②（历史里开新一轮，FR-011）**：`confirmNewRound`（useScreenRoundFlow.ts:470-494）的 resumable 判定只看 AI 筛选侧状态（pausedRunId/interruptedRunId/screenBusy/screenSnapshot 状态/anyResumableTarget/roundContext），**不含「抓取运行中」**——抓取跑着时 resumable=false → 直接 `resetWorkflow()` → `cancelActiveTasksForNewRound()`（useDiscoveryTasks.ts:731-732, 327-359）取消抓取任务并开新一轮。历史模式 `enabledSteps=["results"]`（useDiscoveryState.ts:561）使 04 页永远可进，该入口在历史模式完全可达（真机踩中路径）。另：既有各入口守卫的跳回落点一律 `activeStep="screen"`（useDiscoverySearch.ts:252-257 等），抓取任务应回 02。
   - **修复 E（入口守卫补全 + 跳回落点匹配）**：`confirmNewRound` 顶部加 `hasLiveTaskState()` 守卫（先于 resumable 计算）；新增 `liveTaskStep()` 派生助手（抓取活 → "search"/02，筛选或重抓活 → "screen"/03），全部入口守卫统一使用，替换「一律跳 screen」。
3. **真机问题③（按钮 2→4，FR-012/013）**：`enterHistoryRound`（useDiscoveryResults.ts:293-316）先置 `historyRound=null` 再调 `setPipelineResult`（L298），**绕过其 historyMode 守卫（L63）**，置位 `scrapeCompleted=true`（L82，连带 resultLoaded/analysisReady）；`returnToLatest`（L319-349）复位 historyRound/pipelineResult/resultLoaded=false/currentRoundStatus，**不复位 scrapeCompleted**。组合出：864「停止抓取」+ 901「结束并保存结果」照常，**多渲染 912「进行确认AI筛选条件」（v-if scrapeCompleted）与 915「直接查看结果」（scrapeCompleted && !resultLoaded && !screenBusy）**＝4 个。915 还有以运行中任务调 `viewScrapedOnly → saveScrapedOnlySnapshot` 半截保存风险。
   - **修复 F（历史浏览只读 + 回最新重算）**：F1 修正 `enterHistoryRound` 绕过守卫路径，历史浏览不得置位 `scrapeCompleted` 等当前轮标志（历史只读原则）；F2 `returnToLatest` 后 `scrapeCompleted` 按当前任务真实状态重算（抓取运行中 = false）作防御层；F3 915「直接查看结果」条件加「无活任务」守卫（防半截保存，纵深防御，DiscoveryView.vue 仅改既有行条件）。

## Technical Context

**Language/Version**: Python 3（uv 管理）；TypeScript / Vue 3（webui/src）

**Primary Dependencies**: Vue 3 composables（既有 `useDiscovery*` 与 `discoveryDeps.ts` 契约）；本轮**不改后端**（B085 日志过滤上一轮已落地且真机验证通过）

**Storage**: 无数据库变更；本轮修复纯前端状态语义收敛，不碰 sessionStorage/localStorage 的 key 结构（恢复时对 screen 侧快照做按任务真实进度的收敛处理）

**Testing**: 后端 `uv run python -m unittest`（本轮后端无改动，全量作回归）；前端 `npm test`（**必须含界面级渲染断言**：按钮数量、页面显示内容、入口逐一触发）+ `npm run build`

**Target Platform**: Windows（源码 + EXE 桌面）；macOS（DMG）

**Project Type**: 桌面应用（内嵌 web UI）+ CLI 抓取脚本

**Performance Goals**: 无新增轮询/请求；修复为状态清理与判定收敛，零性能影响

**Constraints**: `DiscoveryView.vue` 超宪法红线（1200，031 还债中）——本轮仅允许改 915 行按钮既有条件一处（F3，净增 0-1 行），其余修复全部落 composable 层；`useScreenRoundFlow.ts`/`useDiscoveryResults.ts`/`useDiscoveryExecution.ts` 为既有 Discovery 域 composable，跨文件调用经 `discoveryDeps.ts` 契约，新增派生（`liveTaskStep`）必须同步契约类型

**Scale/Scope**: 单用户桌面工具；本轮只做「三个真机问题的全局收口」，不碰抓取/筛选业务逻辑、不碰后端、不碰数据库、不碰门面文件；B085/B087 已实现部分行为不变（回归保护）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **原则 I 职责分层**：改动全部落在既有「Discovery 历史/恢复/轮次流」composable 域；`DiscoveryView.vue` 仅改既有按钮条件一处，不新增逻辑。不违反。
- **原则 II 单文件尺寸**：`DiscoveryView.vue` 超限（031 还债在途）——本轮净增 ≤1 行（F3 条件内联），不加逻辑块；其余目标文件均在限内；**本轮无新增源码文件**。不违反。
- **原则 III 引用方向**：前端 `view → composable → api client`；composable 间经 `discoveryDeps.ts` 契约调用（`liveTaskStep` 派生落 `useDiscoveryState.ts` 并经契约暴露），无反向依赖。不违反。
- **原则 IV 拆分与重构纪律**：本轮为缺陷修复，不混入重构；历史浏览只读化只改判定与清理，不改接口。不违反。
- **原则 V 验证门禁**：聚焦测试（含界面级渲染断言）+ 后端全量 + 前端测试 + 构建 + 卫生检查；用户端到端真跑交付后进行。不违反。
- **原则 VI 模块地图与落位**：改动全部落入既有 Discovery 域模块，无新文件需登记；宪法已登记的 `TaskCompletedToast.vue`（035）不动。不违反。

## File Boundaries

*GATE: 沿用上一轮用户已确认的文件边界清单（根因落点相同、无新增源码文件），依据为本 plan 与交接硬约束。*

- **Allowed files**（修改，本轮全部为 composable 层 + 一处模板既有行）：
  1. `webui/src/composables/useDiscoveryExecution.ts` — 修复 D：`startScrape` 清空 screen 侧展示状态；`restoreRunningTask` 抓取活任务分支同步清空
  2. `webui/src/composables/useScreenRoundFlow.ts` — 修复 E：`confirmNewRound` 顶部加 `hasLiveTaskState` 守卫 + `liveTaskStep` 跳回落点
  3. `webui/src/composables/useDiscoveryResults.ts` — 修复 F：`enterHistoryRound` 不再绕过守卫置位当前轮标志；`returnToLatest` 按任务真实状态重算 `scrapeCompleted`、落点用 `liveTaskStep`
  4. `webui/src/composables/useDiscoverySearch.ts` — 修复 E：`analyzeResume` 守卫跳回落点改 `liveTaskStep`
  5. `webui/src/composables/useDiscoveryTasks.ts` — 验证/微调 `maybeAutoStartNewRound` 既有守卫（预计不改；若 resetWorkflow 清理面需对齐修复 D 则同步）
  6. `webui/src/composables/useDiscoveryState.ts` — 修复 E：新增 `liveTaskStep()` 只读派生（抓取活→search，筛选/重抓活→screen），并确认 `hasLiveTaskState` 覆盖面
  7. `webui/src/composables/discoveryDeps.ts` — 契约同步（`liveTaskStep` 暴露）
  8. `webui/src/views/DiscoveryView.vue` — 修复 F3：**仅 915 行「直接查看结果」既有 v-if 条件加无活任务守卫，净增 ≤1 行**；其余零改动
- **测试文件**：`webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`、`webui/src/composables/__tests__/useDiscoveryTasks.spec.ts`、`webui/src/composables/__tests__/useDiscoveryResults.spec.ts`（如存在）/`useDiscoveryExecution` 相关既有 spec、`webui/src/views/__tests__/DiscoveryHistoryMode.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`；后端测试无改动（全量回归即可）
- **New files**：无源码新文件；本 spec 目录文档重拆更新（research.md、data-model.md、contracts/discovery-state.md、quickstart.md、tasks.md）
- **Forbidden files**：`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`scripts/maintenance/historical_recovery.py`、`webui/historical_recovery.py`（门面/超限/还债文件）；`webui/log_api.py` 与 `LogViewerDialog.vue`（B085 已真机验证，本轮不动）；数据库与迁移；`roadmap/`、`.codebuddy/`、`.specify/`（除 feature.json 已拨回 035）
- **Reference direction**: 前端 `view → composable → api client`；composable 间经 `discoveryDeps` 契约；`liveTaskStep` 为 `useDiscoveryState.ts` 只读派生，被 useScreenRoundFlow / useDiscoverySearch / useDiscoveryResults 消费
- **Line gate**: `DiscoveryView.vue` 仅改 915 行既有条件（净增 ≤1 行）；其余 TS composable 无宪法红线，改动为守卫条件与状态清理，净增控制在每个文件 ≤30 行
- **Rationale**: 三个真机问题根因全部落在「轮次状态收敛 / 入口判定 / 历史浏览副作用」三点，修复点即根因点；界面级验收（按钮数量、页面内容、入口逐一）由前端测试的渲染断言承载，`DiscoveryView.spec.ts`/`DiscoveryHistoryMode.spec.ts` 已有渲染测试基建

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付门禁：聚焦测试（**界面级**：按钮数量断言、03 页显示内容断言、5 入口逐一触发断言）+ 后端全量测试（后端零改动，纯回归）+ 前端测试 + `npm run build` + 仓库卫生检查。
- 本功能为功能交付，不适用收口规则（不提交不推送）。
- 用户端到端真跑验证在交付后进行（模拟真实用户，见 quickstart.md 场景 A-E，A-D 需真实登录态，环境归项目就绪；缺前置如实报告并等用户）：
  1. 抓取中刷新 → 只有 02 页进度、03 页不出现旧内容、任务页按钮恰好 2 个（真机问题①③合并走查）；
  2. 历史里（历史模式 04 页）点「开始新一轮」→ 跳回正在跑的任务、不开新一轮（真机问题②）；
  3. 暂停/中断任务同路径不丢状态；
  4. 任务跑完看历史 → 冒泡 + 回最新见本轮成果；
  5. 日志滑块切换正常（回归）。
- 审查要求（spec SC-007）：审查必须以真实渲染/界面走查核对上述路径，静态读码不能单独作为通过依据。

## Project Structure

### Documentation (this feature)

```text
specs/035-task-history-safety/
├── plan.md              # This file（重拆版）
├── spec.md              # 冻结规格（重拆版）
├── research.md          # 技术决策（重拆版：R1-R4 已落地决策保留 + R5-R7 本轮根因与决策）
├── data-model.md        # 状态/契约（重拆版）
├── quickstart.md        # 验证指南（重拆版：场景 A-E 界面走查）
├── contracts/           # discovery-state.md（更新）/ logs-api.md（不变）
├── checklists/          # requirements.md（重拆版）
└── tasks.md             # Phase 2 输出（重拆版）
```

### Source Code (repository root)

```text
webui/src/composables/useDiscoveryExecution.ts   # [改] startScrape/restoreRunningTask 清空 screen 侧
webui/src/composables/useScreenRoundFlow.ts      # [改] confirmNewRound 守卫 + liveTaskStep 落点
webui/src/composables/useDiscoveryResults.ts     # [改] enterHistoryRound 只读 / returnToLatest 重算
webui/src/composables/useDiscoverySearch.ts      # [改] analyzeResume 落点
webui/src/composables/useDiscoveryTasks.ts       # [改/验证] maybeAutoStartNewRound 守卫
webui/src/composables/useDiscoveryState.ts       # [改] liveTaskStep 派生
webui/src/composables/discoveryDeps.ts           # [改] 契约同步
webui/src/views/DiscoveryView.vue                # [改] 仅 915 行按钮条件一处
```

**Structure Decision**: 零新增源码文件；修复收敛在既有 Discovery 域 composable；界面级验收由既有渲染测试基建承载。

## Complexity Tracking

无宪法违规需要辩解；`DiscoveryView.vue` 超限属 031 还债在途，本轮仅一行既有条件修改，符合「普通功能不得追加新逻辑」约束。上一轮已实现且真机验证通过的部分（B085 日志滑块、B087 冒泡）不在本轮改动面内，仅回归保护。
