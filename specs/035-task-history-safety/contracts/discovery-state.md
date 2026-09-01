# Contract: Discovery 前端状态语义（035 重拆版）

## 1. 「已结束」事实置位条件（上一轮修正，保留）

- **允许置位**：仅当 `!historyMode`（当前轮视图）且 `activeStep` 变为 `"results"`，或用户点「结束保存」。
- **禁止置位**：`historyMode` 为真（浏览历史轮）时，任何路径不得置位 `resultsPageSeen` / 写入 finished 持久化。

## 2. 未结束任务存在时的「开新一轮」行为（本轮扩展：入口全量 + 落点匹配）

- 判据：`hasLiveTaskState()`（`pausedRunId || interruptedRunId ||` 任一快照状态 ∈ {running, queued, paused, failed, interrupted}）。
- 落点：`liveTaskStep()`（本轮新增只读派生）——抓取活（scrapeBusy 或 scrapeSnapshot 进行态）→ `"search"`（02）；筛选/重抓活 → `"screen"`（03）。
- 未结束任务存在时，以下 **5 个入口**（完整清单）MUST 跳回 `liveTaskStep()`，MUST NOT resetWorkflow、MUST NOT 调 `/api/task/cancel`、MUST NOT 弹取消确认：
  1. 04 页「开始新一轮」按钮（`confirmNewRound`）——**含历史模式 04 页**（守卫在函数顶部，先于任何 resumable/reset 判定）
  2. 01 页「上传并分析」（`analyzeResume`，隐式新一轮）
  3. 02 页「开始筛选并 AI 优化」（一键链路）
  4. 02 页「单独抓取」（`startScrape`）
  5. 启动/刷新自动（`maybeAutoStartNewRound`）
- 任务真实已结束（`workflowIsFinished()` 为真）时，上述入口行为与现状一致（正常开始新一轮）。

## 3. 轮次展示状态收敛（本轮新增）

- `startScrape` 开新一轮时 MUST 清空 screen 侧展示状态：`screenSnapshot` / `screenTaskId` / `recrawlSnapshot` / `recrawlTaskId` / `currentRoundStatus`。
- `restoreRunningTask` 检测到活的抓取任务时 MUST 同样清空（活的抓取任务本轮进度在 02，screen 侧数据必属旧轮；含 sessionStorage 快照恢复带来的残留）。
- `screenSnapshot=null` 即 03 页「未开始」空态。

## 4. 历史浏览只读（本轮新增）

- `enterHistoryRound` 查看历史轮 MUST NOT 置位或修改当前轮标志（`scrapeCompleted` / `resultLoaded` / `analysisReady` 等）；不得通过先清 `historyRound` 再走带当前轮副作用的路径来绕过守卫。
- `returnToLatest` 后 `scrapeCompleted` MUST 按当前任务真实状态重算（抓取运行中 = false）；未结束任务存在时落点为 `liveTaskStep()`。
- 「直接查看结果」入口（DiscoveryView.vue 任务页按钮）要求：抓取真实完成 **且** 无活任务（防运行中半截结果被保存）。

## 5. 冒泡提示状态（上一轮已落地，保留）

- `TaskCompletedToast.vue` 暴露：`visible`、`onClick`（回最新）、`onClose`。
- 触发条件：轮询检测到当前轮状态由进行中迁移到已完成，且用户不在当前轮视图（`historyMode` 为真 或 当前不在 results 页）。
- 点击行为：回到最新（`returnToLatest`）。
