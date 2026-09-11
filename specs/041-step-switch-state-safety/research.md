# Research: 步骤页切换状态交接安全（B098 + B093 + B092）

**Spec**: `specs/041-step-switch-state-safety/spec.md` | **Date**: 2026-09-12

> 本文记录 Phase 0 代码现状调研结论，定位每条 bug 的根因与修改点。研究覆盖前端 `webui/src` 全域与后端 schema 接入点，只读不改。

## 一、B098 A 块 · 切页/翻历史/刷新不丢现场

### 1.1 根因：四步骤页 `v-if` 链卸载组件

`webui/src/views/DiscoveryView.vue`（共 1215 行）四步骤页用纯 `v-if / v-else-if / v-else` 条件渲染，**无 keep-alive、无动态组件、非路由切换**：

| 步骤页 | 行区间 | 条件 |
|---|---|---|
| 01 上传简历 | L633-687 | `v-if="activeStep === 'upload'"` |
| 02 广泛抓取 | L689-898 | `v-else-if="activeStep === 'search'"` |
| 03 AI 筛选 | L900-965 | `v-else-if="activeStep === 'screen'"` |
| 04 查看结果 | L967-1060 | `v-else`（results-stage） |

`activeStep` 一变，旧 section 整块卸载、新 section 整块新建——所有只存在于被卸载组件内部的状态随之蒸发。

### 1.2 现场存放位置与丢失情况

**数据层状态（composable ref，切页不丢）**：`useDiscoveryState.ts` 的 `keywords`/`selectedKeywords`/`cityText`/`filterValues`（分平台双槽 L244-247）/`profileSummary`/`profileFacts`/`activeCategory`/`resultPlatformFilter`/`rejectedIds` 等；`useLocationDraft.ts` 模块级单例 `byPlatform`；`resultHistory.ts` 模块级单例。

**丢失的三类（仅存于组件内部 ref / DOM）**：

| 现场项 | 现存放位置 | 切页后 |
|---|---|---|
| 页面二画像框高度 | DOM `style.height`（`useAutoGrowTextarea.ts` L10-15 `resize()`），`profileInputEl`（state L539） | **丢**（回 4 行） |
| 城市面板已加载区县目录 | `LocationPicker.vue` 组件内 ref（`open` L29/`districts` L30/`loading` L31/`cityCode` L33），来自 `loadCatalog()` L100-115 | **丢**（重开重新请求） |
| 折叠卡片滚动 | `CollapsibleCard.vue` L22 `innerEl`，**收起时主动 `scrollTop=0`**（L23-25 watch） | **丢**（卡片随 section 卸载） |
| 页面四排序 | `JobWorkspace.vue` L74 `sortKey` | **丢** |
| 页面四列表展示筛选草稿 | `JobWorkspace.vue` L73 `filterState`（`listFilter.ts` 类型 L21-26：salary/experience/degree/welfare，与 03 页提交后端的 `filterValues` 是两套） | **丢** |
| 页面四已加载批次 | `JobWorkspace.vue` L59 `visibleCount`（默认 30，IntersectionObserver L110-150 无限滚动） | **丢**（回 30） |
| 页面四当前选中岗位 | `JobWorkspace.vue` L60 `localSelectedId` + L62 `userSelectedDetail` | **丢** |
| 页面四详情开合 | `JobWorkspace.vue` L61 `detailOpen`（重挂载由 L180-193 watch(immediate) 按 jobs 非空重置） | **丢** |
| 页面四 JD 滚动 | `JobWorkspace.vue` L119/559 `jdScrollEl` DOM（切岗位 L205-215 主动归顶） | **丢** |
| 计时显示 | `TaskProgress.vue` L119-208 组件内 `setInterval`，基准取 snapshot，重挂载 watch 重建 | 半丢（闪断后靠 snapshot 重建） |

### 1.3 切页函数与 watch

- `activeStep = ref<StepId>("upload")`（`useDiscoveryState.ts` L197）。
- 切页函数（`useDiscoveryWorkflow.ts`）：`selectStep`（L269-278，历史模式拦截 + enabledSteps 检查 + 分派）、`enterSearchStep`（L255-259）、`enterScreenStep`（L262-266，注释「面板不自动展开」）。
- 灵动岛胶囊导航 `capsuleNavigationTarget`（state L75-80 模块级信号 + watch L908-934）也直接写 `activeStep`。
- 切页 watch（DiscoveryView）：`watch(activeStep)`（L455-462）进 `results` 且非历史/非回最新/无活任务 → `markResultsPageSeen()`（清 sessionStorage 快照，B078 修复）；`scopeLocked` computed（state L569-573）依赖 activeStep。
- **结论**：切页动作本身不重置数据层状态，丢失全部来自 `v-if` 卸载。

### 1.4 历史轮查看

- 历史抽屉 `ResultHistoryDrawer` + `resultHistory.ts`（201 行模块级单例）。
- 切历史轮：`watch(historyDetail)`（DiscoveryView L502-508）→ `enterHistoryRound`（`useDiscoveryResults.ts` L390-425）：记录 `platformBeforeHistory`、设 `historyRound`、**直接覆盖 `pipelineResult`**、`resultEpoch+=1`、重算 `activeCategory`、`resultPlatformFilter`、`draftPlatform`、`activeStep="results"`、`currentRoundStatus=历史状态`。
- 返回：`returnToLatest`（useDiscoveryResults L428-480）清历史标记 + 重新 `fetchMergedLatestResult` 重建。
- 当前轮 04 现场被历史轮数据顶替（部分丢）；02/03 草稿不清，但 `draftPlatform` 被历史平台改写（returnToLatest L454-457 用 `platformBeforeHistory` 还原）。
- 历史轮**无独立查看现场**——每次翻历史都重新覆盖，翻回不接上次滚动位置。spec FR-002 要求历史轮查看现场各自保留（只存翻过的）。

### 1.5 可复用的现场外置通道

1. **sessionStorage workflow 快照**（`useDiscoveryWorkflow.ts` L47-56 读取 / L114-168 写入）：已有 `WORKFLOW_STATE_VERSION=1`、按 profileId 的 key、防完成态误恢复判定（`isCompletedWorkflowSnapshot` L29-41）——**数据层草稿持久化通道已存在，页面内现场未纳入快照**，是最直接扩展点。
2. **localStorage 先例**：finished state（L63-104）、主题、当前 profile、跨平台去重开关——小状态持久化成熟模式。
3. **模块级单例内存态**（跨组件卸载存活）：`resultHistory.ts` L58-67、`useLocationDraft.ts` L6-9、`useDiscoveryState.ts` L75（capsuleNavigationTarget）——不想动 sessionStorage 时可上提为模块级单例。
4. **`resultEpoch` 信号机制**（state L362 + JobWorkspace L81-89 watch）：「新结果重置列表现场、切分类/切平台保留」既有判据，现场保留须沿用此语义区分「该重置」与「该保留」。
5. 后端接口可恢复「轮次级」现场（`/api/latest-running-task` 含 round_context/frozen_filters/profile、`/api/latest-pipeline-result` 含 round_context），但无「页面内 UI 现场」接口。

**修改点决策**：现场外置采用「sessionStorage 快照通道扩展 + 模块级单例内存态」组合——切页/翻历史/刷新走内存态瞬时恢复（无网络），刷新后从 sessionStorage 恢复；新 composable `useDiscoverySceneState` 统一存取，按「画像+轮次+平台」身份键。

## 二、B098 B 块 · 只有主体身份变才清现场

### 2.1 切换画像当前不清理（残留 bug）

- 入口：`App.vue` `selectProfile`（L507-510）只改 `currentProfileId` + 写 localStorage。**DiscoveryView 无 `:key`**（App.vue L833-836），组件不重挂载、`useDiscoveryState` 的 ref 不重建。
- 唯一响应：`watch(() => props.profileId)`（DiscoveryView L481-485）仅在无暂停/忙碌任务时调 `loadLatestResult()`（只换 `pipelineResult`）。
- **未清理项**：内存草稿（keywords/cityText/filterValues/profileSummary）、城市草稿（`useLocationDraft` 模块级单例不清）、历史抽屉（`resultHistory` 仍旧画像列表）、页面四现场（activeCategory/rejectedIds）。
- spec FR-006 要求切画像清掉属于旧画像的一切，新画像从干净默认开始。

### 2.2 开新轮已实现降级 + 全量重置

- 入口：「开始新一轮」按钮（DiscoveryView L625-629）→ `roundFlow.confirmNewRound`（`useScreenRoundFlow.ts` L636-685）→ `deps.api.resetWorkflow()` → `resetWorkflowInternal`（`useDiscoveryTasks.ts` L939-1012）。
- 顺序：`workflowEpoch+=1` + 停 pollTimer → `cancelActiveTasksForNewRound`（L447-526）→ `clearLatestResult` → `archiveHistoryLatest`（resultHistory.ts L178-185，`POST /api/result-history/archive-latest` 旧轮降级为历史轮）→ `clearWorkflowState` + `clearFinishedState` → 全量重置约 50 项 ref（含 `activeStep="upload"`、`pipelineResult=null`、`locationDraft.reset()`、`filterValues={boss:{},zhilian:{}}`、`historyRound=null` 等）。
- **缺口**：旧轮「查看现场」未作为历史轮现场保留（spec FR-007 要求旧轮降级后其查看现场按 FR-002 保留）。

### 2.3 切换平台草稿已隔离

- `useDiscoverySearch.ts` `setDraftPlatform`（L89-110）清 scopePreview/scrapeTaskId 等，重载 schema/城市；草稿分平台双槽 `filterValues={boss:{},zhilian:{}}`（state L244-247）、`locationDraft.byPlatform`（useLocationDraft L6-9）。
- 例外：`profileSummary`/`profileConfirmed` 跨平台共享；`resumeAnalysis` 经 `applyResumeAnalysisToCurrentSchema`（L350-374）按新平台 schema 重投影。
- 切平台现场清理基本符合 spec FR-008，仅需确认页面内 UI 现场也随身份键隔离（新 composable 天然按平台键隔离）。

## 三、B098 C 块 · 灵动岛落点

### 3.1 落点派生现状（不按真实进度）

- `DynamicIsland.vue`（1005 行）胶囊点击 `onPillClick`（L358-368）：无未读时 `emit("navigate", stateTarget.value)`。
- `stateTarget` computed（L124-131）：`running→task`、`completed→results`、`attention→attention`、`default→home`。
- **问题**：暂停/出错都落 `attention`（=home?），不区分「卡在抓取→02」「卡在筛选→03」——spec FR-010 要求按任务真实进度派生落「卡住的那步页」。
- 落点目标 `CapsuleTarget`（L34）：`home|task|results|attention|reminders`，无「卡住那步页」语义。

### 3.2 历史退出与清场风险

- `navigate` emit 由父层（App/DiscoveryView）处理；`capsuleNavigationTarget`（state L75-80）模块级信号 + watch（L908-934）写 `activeStep`。
- 历史轮查看时点灵动岛：watch 把 `activeStep` 写回目标页，但**未先退出历史**（returnToLatest）——历史轮数据残留 + 落点页用历史轮 pipelineResult 渲染，可能错位。
- spec FR-011 要求点灵动岛切回当前轮对应页、历史轮现场原地保留；FR-012 硬红线「任何路径不清成空白上传页」。
- `resetWorkflowInternal` 会把 `pipelineResult=null` + `activeStep="upload"`——若 navigate 误触发 reset 即清成空白上传页（需排查 navigate 处理链是否含 reset 调用）。

### 3.3 启动占位点击

- 未见专门的占位期间点击吞锁代码，但 `onPillClick` 直接 emit navigate，若启动加载未完成时落点派生拿不到真实进度可能错位。
- spec FR-013 要求占位期间点击不被吞掉且锁死后续同目标点击；要么等加载完执行，要么明确不响应但放行下一次。

### 3.4 「分析中」态

- `useIslandCarousel.ts` `IslandPhase`（L26）：`scraping|jd|screening|completed|idle|attention`——**无 `analyzing` 态**。
- `deriveLiveState`（L75-107）从 `capsule.state` 派生：`idle/running/completed/attention`，无 analyzing 分支。
- spec FR-021 要求灵动岛支持「分析中」态，点击回页面一。需在 `IslandPhase` 增 `analyzing`、`deriveLiveState` 增分支、`DynamicIslandState` 增 analyzing capsule 态。

### 3.5 步骤页启用/禁用

- `enabledSteps`/`completedSteps` computed（state L576-593）；03 页在只抓取流程未启用。
- 落点需检查页面启用状态（spec 边缘场景：暂停时页面二未启用按真实进度落到对应启用页，不送未启用页）。

## 四、B098 D 块 · 补抓/重抓不切页

### 4.1 切页问题点

- `useScreenRoundFlow.ts` `startRecrawl`（L468-480）：显式平台时 `deps.refs.activeStep.value = "screen"`（切 03）；无平台时由 `recrawlUncertain` 决定（注释 L473-475「单平台直接重抓并在内部切 03」）。
- spec FR-015 要求点补抓/重抓不切页、留 04、后台跑、灵动岛体现进度。
- 触发点（DiscoveryView）：`PendingRecrawlCapsule` L981 `@recrawl` → `startRecrawl(...)`；「全部重抓」按钮 L1026 `@click` → `startRecrawl(...)`；「补抓 JD」单条按钮 L1044-1048 `@click` → `retryJd(job)`（单条补抓，不同路径）。

### 4.2 重抓完成切回与增量

- `useDiscoveryTasks.ts` `pollTask`（L75-324）：screen 完成时 L204 `activeStep.value = "results"`（切回 04）；L170-205 完成分支拉 `fetchMergedLatestResult` + `setPipelineResult` + `currentRoundStatus`。
- `retryMergeUpgrade`（L336-352）合并更新（轮询兜底）。
- `JobWorkspace.vue` 已有 `resultEpoch` + `prevJobKeySignature`（L179-193）机制：集合签名变化才重置选中/批次，原地内容更新（重抓只补 JD）保留滚动。spec FR-017 选中按身份跟随已部分具备——`jobKey`（L195-203）用 `platform:platform_job_id` 双 ID。
- **修改点**：`startRecrawl` 不切 03；pollTask 重抓完成不强制切 04（原地增量）；选中岗位按身份跟随沿用 jobKey 机制。

## 五、B093 · 简历分析中不锁

### 5.1 现状：同步 await 锁用户

- `useDiscoverySearch.ts` `analyzeResume`（L245-313）：
  - L255 `uploadBusy.value = true`（锁上传按钮）。
  - L259-264：035 守卫——有活任务跳回任务视图（抓取→02、筛选/重抓→03），不取消旧任务（已部分「不锁」）。
  - L265-305：无活任务时 `cancelActiveTasksForNewRound` + `clearLatestResult` + `await apiRequest("/api/analyze-resume")`（**同步阻塞**）+ `initializeFromAnalysis` + `enterSearchStep()`（强制切 02）。
- **问题**：`await` 期间用户若切页/点灵动岛，分析完成 `enterSearchStep` 把用户拉回 02 → 状态错乱。spec FR-018 要求分析中不锁、后台照跑、回来按真实进度落点。

### 5.2 误报来源

- `errorCodes.ts` 含 `source_cdp_unavailable`「连不上调试浏览器」、`source_unreachable`「抓取脚本不可用」、`source_login_required` 等。
- spec 说「分析完毕后状态错乱、灵动岛误报浏览器找不到/刷新失败」——疑为分析完成后状态切换（enterSearchStep）与真实进度不一致时，灵动岛 attention 态展示某 source_* 错误码。
- 具体误报文案与触发链需在实现时沿 analyzeResume 失败路径 + 灵动岛 attention 派生路径精确定位（research 开放点）。
- spec FR-022 要求分析中及失败展示与真实进度一致，不出现「浏览器找不到/刷新失败」式误报。

### 5.3 完成自动进 02 与回到最新

- 当前 `analyzeResume` 成功即 `enterSearchStep`（切 02）——这是「分析完成自动进 02」的既有实现，但属同步流程。
- 「回到最新」按钮逻辑在 `useDiscoveryResults.ts` `returnToLatest`（L428-480）。
- spec FR-019/FR-020 要求：用户回当前流程按真实进度落点（完→02、没完→01 显示分析中）；分析完成自动进 02。
- **修改点**：`analyzeResume` 改后台异步（不 await 阻塞 UI），分析中灵动岛显示 analyzing 态，完成时按用户当前位置决定是否切页（用户在 01 才自动进 02，离开则不强制切），失败按真实失败展示。

## 六、B092 · 第七类条件接入

### 6.1 filterGroups 从 schema 派生（无排除第七类）

- `useDiscoveryState.ts` `filterGroups` computed（L619-638）：从 `schemaRef.value.fields` 派生，`sentinelOpt` 找 value="0" 或 label∈["不限","全部"]，options 过滤掉 sentinel 项，最后 `.filter((group) => group.options.length || group.sentinel)`——**无任何排除 recruiter_activity 的逻辑**。
- 03 页确认 UI（DiscoveryView L901 标题「确认 6 类筛选条件」、L933-958 `v-for="group in filterGroups"`）用 filterGroups 渲染——理论上 schema 含第七类则 03 页应显示。
- 标题硬编码「6 类」（L901）——即使 schema 有七类，标题仍写「6 类」。

### 6.2 schema 加载

- `/api/filter-labels?platform=` 返回 `PlatformFilterSchema`（`discovery.ts` L213-302，`createSchemaLoader` 异步资源加载器）。
- `schemaRef`（state）= schemaLoader 当前已加载 schema；`loadFilterLabels` 在 onMounted + 切平台时调。
- spec 假设后端 `platforms_boss.py`/`platforms_zhilian.py` filter_schema.fields 已含 `recruiter_activity`（028 已实现）。

### 6.3 前端零接入确认

- `search_content` 全仓搜 `recruiter_activity` 在 `webui/src`：**仅 `__tests__/recruiterActivityFilter.spec.ts` 一处命中**——前端产品代码零引用第七类字段。
- `listFilter.ts`（列表展示筛选，四类 salary/experience/degree/welfare）与 03 页 `filterGroups`（提交后端筛选条件，从 schema 派生）是两套，第七类属后者。

### 6.4 根因待实现时核实

可能原因（需实现时核实）：
1. 后端 `/api/filter-labels` schema 实际未含 recruiter_activity field（spec 假设已含，需核实 `platforms_boss.py`/`platforms_zhilian.py`）。
2. schema 含但前端 `filterGroups` 在只抓取流程下 schema 未加载就渲染（scraped_only 流程跳过 03 页，结果页确认时 schema 可能未就绪）。
3. 「只抓取流程结果页确认条件」可能是独立于 03 页 filterGroups 的另一确认入口（spec 口径「结果页确认条件那一步」），需定位该 UI。

**修改方向**：核实后端 schema 含第七类 → 确保 `filterGroups` 在所有流程下渲染第七类（含只抓取流程结果页确认）→ 标题去硬编码「6 类」→ `screenSummaryChips`（state L652-666）含第七类摘要 → `applyResumeAnalysisToCurrentSchema`（useDiscoverySearch L350-374）第七类投影。

## 七、文件行数清单（修改前基线）

| 文件 | 行数 | 红线 | 备注 |
|---|---|---|---|
| `DiscoveryView.vue` | 1215 | **超**(1200) | 四步骤页 v-if 链根因所在 |
| `useDiscoveryState.ts` | 1328 | **超**(800 TS 软约束) | filterGroups + 共享 ref |
| `useDiscoveryExecution.ts` | 1230 | 接近 | restoreRunningTask/简历分析恢复 |
| `useDiscoveryTasks.ts` | 1065 | 接近 | pollTask/resetWorkflow |
| `DynamicIsland.vue` | 1005 | 接近 | stateTarget/navigate |
| `useDiscoveryResults.ts` | 788 | 安全 | 历史轮切换 |
| `useScreenRoundFlow.ts` | 719 | 安全 | startRecrawl/重抓 |
| `JobWorkspace.vue` | 650 | 安全 | 页面四现场 ref |
| `useDiscoverySearch.ts` | 618 | 安全 | analyzeResume |
| `LocationPicker.vue` | 505 | 安全 | 城市面板 ref |
| `useIslandCarousel.ts` | 216 | 安全 | 状态机 |
| `screenFlow.ts` | 133 | 安全 | 筛选动作派生 |
| `listFilter.ts` | 244 | 安全 | 列表展示筛选（与第七类无关） |
| `CollapsibleCard.vue` | 57 | 安全 | 折叠卡 |

## 八、研究开放点（实现时精确定位）

1. 灵动岛 navigate emit 的父层处理链（App.vue / DiscoveryView capsuleNavigationTarget watch）是否含 reset 调用——决定「清成空白上传页」路径是否存在。
2. 误报「浏览器找不到/刷新失败」的确切文案与触发链（analyzeResume 失败 → 灵动岛 attention 派生）。
3. 后端 `/api/filter-labels` schema 是否真含 recruiter_activity field（核实 `platforms_boss.py`/`platforms_zhilian.py`）。
4. 「只抓取流程结果页确认条件」是否为独立于 03 页 filterGroups 的 UI 入口（spec 口径需对齐代码）。
5. `markResultsPageSeen`（useDiscoveryWorkflow L99-104）与现场保留的交互——进 04 置「已结束」是 B078 修复，现场保留不得破坏此判据。
