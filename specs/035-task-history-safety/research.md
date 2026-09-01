# Research: 任务历史浏览安全与界面一致性修复（035 重拆版）

> R1-R4 为上一轮已落地且有效的决策（保留备查）；R5-R7 为本轮三个真机问题的根因实证与修复决策（证据来自 2026-09-01 只读代码勘察，file:line 以当日工作区为准）。

## R1 · B086 主根因（已落地）

**Decision**: `DiscoveryView.vue` watcher 的 `markResultsPageSeen()` 判定加 `!historyMode` 等条件——浏览历史轮不产生「已结束」事实。

**Rationale**: 进历史轮 → `activeStep="results"` → watcher 置位并持久化 → 刷新后不恢复任务反而 `resetWorkflow → cancelActiveTasksForNewRound` 取消在跑任务。已实现并通过自动化测试。

**Alternatives considered**: enterHistoryRound 绕过 watcher（改动面大）；后端权威判定（推翻 B078 冻结设计，超范围）。

## R2 · B085 运行日志数据来源（已落地，真机验证通过）

**Decision**: `/api/logs` 增加 `task_id` 查询参数，从全局日志按 `task_id` 过滤；前端滑块切换全局/运行模式。

**Rationale**: `logging_setup.TaskContextFilter` 已在日志行写入 `task_id`，过滤即得运行日志；不新增存储与端点。

**Alternatives considered**: 复用任务快照内存 logs（50 行，不全、不可回看历史轮）；独立运行日志文件（成本高收益低）。

## R3 · 顶部冒泡触发机制（已落地）

**Decision**: 复用任务轮询链路（1.8s），检测状态由进行中迁移到已完成且用户不在当前轮视图时冒泡；点击回最新。

**Rationale**: 后端无推送机制，轮询为既有机制；「状态迁移 + 不在当前视图」双条件避免打扰。

**Alternatives considered**: SSE/WebSocket（超范围）；每次轮询都冒泡（打扰）。

## R4 · 未结束任务判定（已落地，本轮扩展使用）

**Decision**: 复用 `hasLiveTaskState()`（覆盖 scrape/screen/recrawl 三类快照 running/queued/paused/failed/interrupted + pausedRunId/interruptedRunId）作为「未结束任务存在」判据。

**Rationale**: 语义正确、覆盖全部未结束态；本轮修复 E 的入口守卫统一以它为第一道闸。

## R5 · 真机问题①根因：screen 侧展示状态无人清理（FR-010）

**Decision**: 确立「页面展示由任务真实进度推导」的两条清理规则：
1. `startScrape`（useDiscoveryExecution.ts:379-386）开新一轮时，同步清空上一轮 screen 侧展示状态：`screenSnapshot`/`screenTaskId`/`recrawlSnapshot`/`recrawlTaskId`/`currentRoundStatus`——新一轮开始即旧一轮展示清空；
2. `restoreRunningTask`（useDiscoveryExecution.ts:293-314）检测到活的抓取任务时，同样清空 screen 侧残留——活的抓取任务本轮进度必在 02，任何 screen 侧数据必属旧轮（含 sessionStorage 整包恢复带来的 screenSnapshot，useDiscoveryWorkflow.ts:182）。

**Rationale**（证据链）:
- 03 页内容 = 筛选条件卡 + ScreenRoundActions（依赖 `hasScreenRun`：screenTaskId/screenSnapshot，useScreenRoundFlow.ts:125-131）+ TaskProgress 绑定 `screenSnapshot`（DiscoveryView.vue:980）+ ScreenRecrawlProgress（v-if recrawlSnapshot || recrawlBusy，L981）。
- `startScrape` 只重置 scrape 侧（scrapeCompleted/resultLoaded/pipelineResult/interruptedRunId/finishedPartial，L379-386），不清 screen 侧；`restoreWorkflowState` 从 sessionStorage 整包恢复 screenSnapshot（L182）；`restoreSaved02State` 同样不碰（L192-213）；唯一清空点 `resetWorkflow`（useDiscoveryTasks.ts:746-750）只有「开始新一轮」路径经过 → 刷新/恢复路径永远不清。
- `screenSnapshot=null` 时 03 页即目标空态：TaskProgress 整卡 `v-if="snapshot"` 不渲染（TaskProgress.vue:439），「开始 AI 筛选」按钮因 `!scrapeCompleted` 禁用——即 resetWorkflow 后的初始 03 态，无需新做空态组件。

**Alternatives considered**:
- 恢复时整体重算各页（把 01-04 显示全部由后端快照重推导）：改动面大，且 01/02/04 现状已正确，只需收敛 03。
- 后端权威（恢复接口直接返回各页应显示内容）：超范围，前端已有全部事实。

## R6 · 真机问题②根因：confirmNewRound 守卫不含抓取任务 + 跳回落点错（FR-011）

**Decision**:
1. `confirmNewRound`（useScreenRoundFlow.ts:470-494）**顶部**加 `hasLiveTaskState()` 守卫（先于 resumable 计算）：命中即跳回 `liveTaskStep()` + 提示 + return，不 reset、不 cancel——覆盖历史模式 04 页（该页 enabledSteps 永远可进，useDiscoveryState.ts:561）；
2. 新增只读派生 `liveTaskStep()`（落 `useDiscoveryState.ts`，经 `discoveryDeps` 契约暴露）：抓取活（scrapeBusy 或 scrapeSnapshot 进行态）→ `"search"`（02）；筛选/重抓活 → `"screen"`（03）；替换现有全部「一律 `activeStep="screen"`」的守卫落点（useDiscoverySearch.ts:252-257 analyzeResume、useScreenRoundFlow 既有跳回、useDiscoveryResults.ts:337-339 returnToLatest）。

**Rationale**（证据链）:
- resumable 判定（L470-481）不含 `scrapeBusy`/`scrapeSnapshot.status==='running'`/`scrapeTaskId`：抓取运行中时 screenBusy=false、pausedRunId=""、screenSnapshot=null、roundContext=null → resumable=false → 走 `resetWorkflow()` → `cancelActiveTasksForNewRound()`（useDiscoveryTasks.ts:731-732）对 scrapeTaskId 调 `/api/task/cancel`——真机踩中路径的完整复现。
- 既有测试只覆盖 screen 侧任务（DiscoveryView.spec.ts:3190-3192 用例为 paused ai_screen），无 scrape-only running 用例——上一轮测试盲区。
- 抓取任务的真实进度页是 02（search），跳 03 对抓取任务是错误落点（用户要再手动点回 02）。

**Alternatives considered**:
- 把抓取状态并入 resumable 计算（改判定项）：仍走「跳回」分支但语义绕（resumable 是 screen 续跑语义），不如顶部独立守卫直白。
- 每个入口各自判断任务类型：重复代码，统一 `liveTaskStep()` 一处派生。

## R7 · 真机问题③根因：enterHistoryRound 绕过守卫污染当前轮标志（FR-012/013）

**Decision**（三层修复，F1 根因 + F2/F3 防御）:
- **F1（历史只读）**：修正 `enterHistoryRound`（useDiscoveryResults.ts:293-316）先置 `historyRound=null` 再调 `setPipelineResult`（L298）绕过其 historyMode 守卫（L63）的路径——历史轮浏览**不得**置位 `scrapeCompleted`/`resultLoaded`/`analysisReady` 等当前轮标志。实现取向：进入历史前先置浏览历史标志（或等价地让 `setPipelineResult` 的守卫判定不依赖被提前清空的 `historyRound`），具体以最小改动落定。
- **F2（回最新重算）**：`returnToLatest`（L319-349）复位 `scrapeCompleted` 为当前任务真实状态（抓取运行中 = false）——即使 F1 修好，回到最新也以任务真实状态重算，防御任何遗漏路径。
- **F3（按钮纵深防御）**：DiscoveryView.vue:915「直接查看结果」既有 v-if 条件增加「无活任务」守卫——防运行中半截结果经 `viewScrapedOnly → saveScrapedOnlySnapshot` 被保存（FR-013）。

**Rationale**（证据链）:
- 02 任务页按钮派生（DiscoveryView.vue:856-918）：正常运行（抓取中）= 864「停止抓取」+ 901「结束并保存结果」恰好 2 个（startScrape 已置 scrapeCompleted=false，L380）。
- 历史路径污染：`enterHistoryRound` 置 historyRound=null → `setPipelineResult` 守卫（L63 `if (!historyMode)` 类判定）失效 → L82 置 `scrapeCompleted=true`（连带 resultLoaded/analysisReady）。
- `returnToLatest` 不复位 scrapeCompleted：组合「scrapeCompleted=true（历史注入）+ resultLoaded=false（L327 复位）+ screenBusy=false」→ 多渲染 912「进行确认AI筛选条件」与 915「直接查看结果」＝4 个按钮。
- 915 风险：点击后以运行中任务的 task_id 调 `viewScrapedOnly → saveScrapedOnlySnapshot`，可能中途保存半截结果。

**Alternatives considered**:
- 只做 F2/F3（回到最新时重算全部标志）：治标，进历史期间的中间态仍被污染（历史里刷新 = 污染被持久化），必须 F1 根因修复。
- 按钮派生改为「历史模式专用投影组件」：改动面大，超出最小修复。

## R8 · 测试策略（界面级验收如何落地）

**Decision**: 前端测试必须包含界面级断言，不得只断言状态字段：
1. 按钮数量断言：mock 抓取运行中 + 走「进历史 → 回最新」路径 → 断言 02 任务页操作按钮恰好 2 个（渲染查询按钮集合）；
2. 页面内容断言：mock 旧 screenSnapshot 残留 + 抓取运行中刷新恢复 → 断言 03 页无旧筛选内容（screenSnapshot 已清、TaskProgress 不渲染）；
3. 入口逐一断言：5 个开新一轮入口在未结束任务（含 scrape-only running）存在时逐一触发 → 断言无 resetWorkflow、无 `/api/task/cancel` 调用、跳回落点正确（抓取→02）；
4. 既有断言「跳回 screen」的用例按 `liveTaskStep` 语义更新（抓取任务用例跳 search）。

**Rationale**: 上一轮三个问题漏网的直接原因就是测试只测状态字段不测界面；spec SC-006 已把「测试必须覆盖界面验收」写成门禁。

**Alternatives considered**: 只靠用户真机验收：返工成本高（真机每轮验证成本远高于渲染测试），且 SC-007 要求自动化+走查双层。
