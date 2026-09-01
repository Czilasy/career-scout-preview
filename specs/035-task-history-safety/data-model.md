# Data Model: 任务历史浏览安全与界面一致性修复（035 重拆版）

本 Spec 不涉及数据库变更、不加表、不迁移。涉及前端状态语义的收敛规则与后端接口契约（后者上一轮已落地不变），记录如下。

## 1. 前端状态语义（本轮收敛规则）

### 已结束事实（`resultsPageSeen` / `finishedPartial`，localStorage 持久化）

- 语义不变（B078 冻结）：进当前轮 04 页或「结束保存」＝ 本轮流程已结束。
- 只允许由当前轮流程真实结束触发；浏览历史轮不得置位（上一轮已落地，保留）。

### 历史模式（`historyMode` / `historyRound`）

- `historyMode = Boolean(historyRound)`：为真表示用户在查看历史轮。
- **本轮新增规则（历史只读）**：`enterHistoryRound` 查看历史轮时，MUST NOT 置位或修改任何当前轮标志（`scrapeCompleted` / `resultLoaded` / `analysisReady` 等）；历史轮内容以历史数据渲染，不写入当前轮状态。修复要点：不得先清 `historyRound` 再走会触发当前轮副作用的路径来绕过守卫。

### 轮次展示状态（screen 侧，本轮收敛）

- 字段：`screenSnapshot` / `screenTaskId` / `recrawlSnapshot` / `recrawlTaskId` / `currentRoundStatus`。
- **清理规则**：
  1. 新一轮开始（`startScrape`）：上述字段全部清空——「新一轮开始 = 旧一轮展示清空」；
  2. 恢复（`restoreRunningTask` 检测到活的抓取任务）：同样清空——活的抓取任务本轮进度必在 02，screen 侧数据必属旧轮。
- `screenSnapshot=null` 即 03 页「未开始」空态（既有行为，无需新组件）。

### 未结束任务判定与跳回落点（`hasLiveTaskState()` / `liveTaskStep()`，只读派生）

- `hasLiveTaskState()`（既有）：`pausedRunId || interruptedRunId` 或任一快照（scrape/screen/recrawl）状态 ∈ {running, queued, paused, failed, interrupted}。
- `liveTaskStep()`（本轮新增）：未结束任务存在时，任务的真实进度页——抓取活（scrapeBusy 或 scrapeSnapshot 进行态）→ `"search"`（02）；否则（筛选/重抓活）→ `"screen"`（03）。无活任务返回空/当前页。
- 用途：所有「开新一轮」入口守卫的统一跳回落点；`returnToLatest` 在未结束任务存在时的落点。

### 「开始新一轮」入口清单（完整，5 项）

1. 04 页「开始新一轮」按钮（`confirmNewRound`；当前轮与历史模式 04 页都算）
2. 01 页「上传并分析」（`analyzeResume`，隐式开新一轮）
3. 02 页「开始筛选并 AI 优化」（一键链路 `openOneClick`/`confirmOneClick`/`startScrape`）
4. 02 页「单独抓取」（`startScrape`）
5. 启动/刷新自动（`maybeAutoStartNewRound`）

未结束任务存在时：5 个入口一律跳回 `liveTaskStep()`，不 reset、不调 `/api/task/cancel`、不弹确认。

### 任务页标准按钮集（抓取运行中）

- 标准 2 个：「停止抓取」「结束并保存结果」。
- 「进行确认AI筛选条件」「直接查看结果」只允许由「抓取真实完成」触发（scrapeCompleted 为任务真实状态派生，不被历史浏览污染）；「直接查看结果」额外要求无活任务（防半截保存）。

### 冒泡提示状态（上一轮已落地，保留）

- 显示/隐藏、关联轮次、触发来源（本轮后台跑完）；点击回最新或手动关闭后隐藏；不持久化。

## 2. 接口契约变化

### GET /api/logs（上一轮已落地，本轮不变）

- 可选查询参数 `task_id`：仅返回包含该 task_id 的日志行；其余语义（tail/offset/since/identity/轮转）不变。契约详见 [logs-api.md](contracts/logs-api.md)。

### 前端 api client（不变）

- `fetchLogs()` 的 `task_id` 透传已落地。

## 3. 运行日志（概念实体，不变）

- 定义：单轮任务的执行过程日志。数据来源：全局日志 `career-scout.log` 中 `task_id` 匹配的行子集。不新增存储。
