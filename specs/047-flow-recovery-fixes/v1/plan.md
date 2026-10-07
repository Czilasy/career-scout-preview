# Implementation Plan: 047 六项流程修复

**日期**：2026-10-06｜**规格**：[spec.md](spec.md)｜**阶段**：Plan / Tasks 已完成；结构前置与六项已按本 Plan 实施（实施证据见 [tasks.md](tasks.md)；T055 真实入口未执行）。

规划工作区为 main；未创建分支、提交或修改产品。未来执行须先核对根 AGENTS 的实现与 Git 授权边界。本文不授予正式数据、停止服务、发布权限。

## Summary

通过通用原因传递、当前运行身份校验、单轨重试、统一阶段投影和事务内删除资格，修复六项用户体验。复用现有任务、继续、停止、保存、白箱、进度组件与结果合流。树干负责错误/动作/生命周期/历史规则，树枝负责平台登录证据。

依据：[research.md](research.md)；关系：[data-model.md](data-model.md)；契约：[contracts/flow-recovery.md](contracts/flow-recovery.md)；验证：[quickstart.md](quickstart.md)；执行：[tasks.md](tasks.md)。

## Technical Context

- Python >=3.10、Flask、SQLite；uv 运行/测试；Vue 3、TypeScript、Vite、Vitest、Node >=20。
- 精确依赖沿用锁文件，不升级依赖。正式启动方式为 README 的 python webui/app.py / tools/start.bat；本轮不启动。
- 复用 Flow/Track、screening/search run、live task、白箱、结果快照和 scene 现场。
- 不新增数据库表、迁移、持久化 status 枚举或外部服务。
- 严格风险级：涉及跨模块共享状态、恢复、持久化与删除。只处理当前流程，不全项目异步重构。

## Constitution Check

| 门禁 | 处理 |
|---|---|
| I/IV：超大文件专门结构规格 | 独立 [前置 Spec](prerequisites/runtime-boundaries/spec.md) 和 [Plan](prerequisites/runtime-boundaries/plan.md)，只搬运，再改行为 |
| II/VI：Python 800 红线/600 预警、Vue 1200/900 | search 817、state 891；service 694、submission 735；分流，不追加巨型文件 |
| III：单向依赖 | HTTP → service → store；view → composable → API；source → 通用事实 |
| V：整条链最后一次全量 | 拆分和各项只聚焦；全部收敛后唯一全量链 |
| VII：失败可查 | 具体原因与完整性并存；迟到事实有诊断，真实失败不隐藏 |
| 树干/树枝 | 通用新域无平台名称/特例，平台证据在平台适配器 |

独立结构前置已规划，不表示拆分已完成；不修订宪法原则。

## 已选择的实施方式

### US1：登录识别和原因传播

1. scripts/boss/login.py 根据响应事实保持四态。明确登录/未登录、受限和未知分开；空响应、损坏 JSON、异常结构、未识别业务码保持未知。保留 bool/四态公开入口、现有缓存隔离和风控规则。
2. 安全诊断只记 HTTP 类别、响应结构类别、已知业务码与失败阶段；禁止响应正文、Cookie、个人信息。可判定登录事实经 SourceOutcome/registry 正确传递。
3. classify_preflight_failure 始终保留规范 failed_code 与安全原因；hard_stop 只描述执行策略，不决定是否保留事实。
4. 抽出的 pipeline_search_preflight 记录所有预检失败，包括 unknown；未执行单元记真实预检阻断，不伪造完成分页/空结果。
5. outcome 优先保留具体执行原因，完整性另存另显示；成功仍遵守 033 v2。登录阻断走原 paused/continue，unknown 沿用既有错误分类并可 retry，不擅自改 taxonomy。
   现有store_runs.update_screening_run在partial时清空错误字段；outcome经新store_run_lifecycle公开原子入口落库，不能给旧方法传error参数就声称已保存。新入口沿原转移合法性，保留有具体错误的partial，不改旧全局更新方法。
6. 恢复探测绕过旧缓存；消除 preflight recovery 中把 source_status_unclear 改成 source_cdp_unavailable 的错误映射。

### US2：当前流程单轨重试

- 新 action=retry 与 resume 分开；普通 resume 仍只走现有暂停恢复。failed 无 run、有 run或有旧结果均可进入单轨 retry 资格核对。
- 复用现有 Flow action 路由，保持 Flow/Track id。flow_track_recovery 编排，store_flow_retry 事务 CAS-claim 并绑定新 run；每次实际 retry 使用新 run id，旧失败 run/白箱/日志不改成成功。
- 抓取失败用原冻结提交快照创建该轨新抓取；AI 失败用该轨已有持久化抓取输入创建新 AI run，经现有 FlowAiCoordinator。缺输入返回可理解错误，不偷偷改条件或重抓兄弟。
- 新尝试 execution_params 记录 retry_of_run_id；无 run 的失败由 Track 和审计关联。使用既有 JSON 列，无迁移。
- 兄弟的绑定、进度和结果保持；目标已有结果也不在 claim 时删除，新结果持久化提交后才换当前结果指针，旧快照仍可追踪。
- 同一失败版本并发两次请求最多一个提交。claim 失败不启动 worker；提交失败仅补偿新尝试，不能假运行/假成功。
- retry事务核对仍为该画像当前Flow及其他Flow活动门禁，和新轮创建竞争不能复活历史旧Flow。
- 所有状态写入在事务内核对当前 run，旧归属不等于当前写权。

### US5：正常收尾与时序一致

- 复用 claim_flow_finish、finish_flow_task_atomic、cancel_task_atomic，分别保持“终止本轨”和“结束并保存结果”语义；立即保存不强制等批。
- flow_run_lifecycle 管当前尝试、终结原因和账本同步；task_state_lifecycle 从现有 durable 原因/绑定推导可选 closure pending/committed，不新增操作表。
- user_finished 尚未绑定结果时显示收尾进行中；保存成功绑定后才正常结束。取消不暗示保存；真保存/白箱/清理失败仍可见。
- 前端操作 epoch 与 profile/flow/track/run 校验废弃旧请求，兄弟继续轮询；响应/权威刷新后更新，失败解忙态并留出口。
- 同一捕获 Flow 输入先收齐 scrape/screen 现场，再一次同步发布，最后解锁/导航；旧代次不允许部分落地。
- 已终结白箱的迟到 unit/page 回调只记 late_callback 审计，不改结论/摘要/结果。新 retry 用新 owner，真正 resume 仍用原重开证据规则。
- 同run resume还需核对捕获的原worker实例和既有白箱attempt；不能仅以run id相同接收旧worker回调。当前task实例核对及DB写在已有ctx.lock内协调，unit/page事实在写事务比较真实attempt。

### US3 / US4：收拢和紧凑操作

- useExecutionPanelCollapse 监听阶段进度首次出现，收拢现有 searchPanelsOpen、advancedPanelsOpen、screenPanelOpen；单平台、全部、自动 AI 交接共享。
- 以当前 scene/Flow 和阶段边沿为界；不每次 poll 写 false。使用现有现场持久化防旧展开立即覆盖，不新增偏好或 scene schema；保留既有手动展开/恢复能力，不新增跨轮规则。
- ScreenRoundActions/TaskProgress 继续共享，局部复用现有 screen-card-actions/recrawl 密度规则，不改全站 button。
- 主操作、保存、终止、重试层级清楚；平台壳和主题不派生资格。候选像素不冻结，不换主题。
- 依据用户截图和当前 design 基准检查桌面/窄屏、明暗与已注册主题，不复制本地数据到公开工件。

### US6：已结束历史删除

- store_history_lifecycle 在同 connection 分析资格/局部残留；查询与删除共享规则。新终结同步预防残留，兼容旧 queued search。
- 可证明旧残留的条件：Track 终结、同 id screening execution 终结、精确归属、无实际活动 task/子任务、无 retry/finish pending。没有证据、共享归属或真正 active 时拒绝。
- GET 只读投影 can_delete/delete_block_reason。DELETE BEGIN IMMEDIATE 内再核对、局部修正已证明的旧 search queue、再判定删除；任一失败全部回滚。
- 保留 result id / 空结果 track id 的现有单轨范围，闭包含同轨 retry 旧尝试。兄弟不删，最后一轨删除才移除外壳；不新增整 Flow 删除。

## File Boundaries

当前仅修改本主体规划工件。下列产品边界用于未来获准后的实现。

### 前置结构边界

允许：webui/pipeline_exec_search.py、store_flow_state.py、flow_service.py、flow_submission_service.py。
新增：webui/pipeline_search_preflight.py、store_flow_cancel.py、store_flow_failure.py、flow_track_operations.py、flow_preflight_recovery.py；tests/test_047_runtime_boundaries.py。
另新增webui/flow_errors.py只搬公共错误/安全文案，原service兼容导出，防恢复域反向引用service。预计70–120行。
精确搬运与行数目标见独立前置 Plan。前置禁止改行为、API、数据库结构。

### 六项阶段既有产品允许文件

| 精确路径（同一格路径逗号分隔） | 限定责任 |
|---|---|
| scripts/boss/login.py、webui/source_boss_cdp.py | 平台登录事实、安全摘要与适配 |
| webui/pipeline_exec_status.py、webui/pipeline_task_outcome.py | 原因、完整性与状态优先关系 |
| webui/runners/pipeline_task.py | 生命周期薄接线，不增长到 600 |
| webui/flow_service.py、webui/flow_submission_service.py | 拆分后复用提交/retry 薄接线 |
| webui/flow_ai_coordinator.py、webui/flow_api.py、webui/flow_task_state.py | AI 重试、HTTP 和当前尝试收口；coordinator 不超过 600 |
| webui/store_flow_state.py、webui/store_flow.py | 拆分后 finish 事务、mixin 组装；门面无判断 |
| webui/task_continue_finish.py、webui/task_state_api.py | 保存接线/只读 closure；原文件行数不增长，新增业务分流 |
| webui/whitebox.py、webui/whitebox_evidence.py | 终结后证据保护与预检事实 |
| webui/store_whitebox.py | 仅在既有写事务接入新写入策略 helper；不追加策略业务，行数增长限薄接线 |
| webui/store_result_history_mixin.py、webui/store_flow_results.py、webui/result_history.py | 同一删除资格、旧残留与历史投影 |
| webui/src/types.ts、webui/src/screenFlow.ts | 兼容类型与共享 retry 派生 |
| webui/src/composables/useDiscoveryParallelFlow.ts、webui/src/composables/useDiscoveryInstanceActions.ts | retry、epoch、单轨忙态 |
| webui/src/composables/useDiscoveryFlowPresentation.ts、webui/src/composables/useDiscoveryFlowCoordinator.ts | 一次投影发布、薄接收拢 helper |
| webui/src/components/ParallelPlatformProgress.vue、webui/src/components/ScreenRoundActions.vue、webui/src/components/TaskProgress.vue | 共享动作/closure 展示，平台壳只转发 |
| webui/src/components/ResultHistoryDrawer.vue、webui/src/styles.css | 删除资格与局部紧凑样式 |
| README.md、CHANGELOG.md | 行为改变后用户说明，不提升版本 |
| .specify/memory/constitution.md | 只登记新增模块地图，不改原则/版本 |

前置新增域在行为阶段按各自职责允许修改。

### 六项新增文件及预计规模

| 精确路径 | 职责 | 预估行 |
|---|---|---|
| webui/flow_track_recovery.py | failed 单轨重试服务，无 SQL | 250–450 |
| webui/store_flow_retry.py | CAS、绑定、补偿事务 | 200–400 |
| webui/store_run_lifecycle.py | 执行段终态/原因与关联账本同事务公开入口 | 180–350 |
| webui/flow_run_lifecycle.py | 当前尝试与正常收尾协调 | 180–350 |
| webui/task_state_lifecycle.py | 只读 closure 投影 | 80–180 |
| webui/store_history_lifecycle.py | 删除资格与局部旧 queue 处理 | 180–350 |
| webui/store_whitebox_lifecycle.py | 证据写事务内当前 owner/终结守卫，迟到事实改为诊断 | 100–220 |
| webui/src/composables/useExecutionPanelCollapse.ts | 进度边沿收拢 | 80–180 |
| webui/src/composables/useFlowOperationEpoch.ts | 操作/响应代次和身份守卫 | 80–180 |

新增文件是既有域分流，不是新框架；预计行数不豁免门禁。

### 测试允许文件

新建六个后端文件：tests/test_047_runtime_boundaries.py、tests/test_047_login_diagnostics.py、tests/test_047_preflight_outcome.py、tests/test_047_track_retry.py、tests/test_047_flow_lifecycle.py、tests/test_047_history_lifecycle.py。
前端新建：webui/src/composables/__tests__/useExecutionPanelCollapse.spec.ts、webui/src/composables/__tests__/useFlowOperationEpoch.spec.ts。

扩展既有：tests/chrome_setup/test_chrome_setup.py、tests/source/test_source_boss.py、tests/source/test_source_zhilian.py、tests/test_b096_instance_actions.py、tests/test_b096_flow_history.py、tests/test_b096_quality_round4.py、tests/test_whitebox_integration.py；
webui/src/__tests__/screenFlow.spec.ts；
webui/src/composables/__tests__/useDiscoveryParallelFlow.spec.ts、useDiscoveryInstanceActions.spec.ts、useDiscoveryFlowPresentation.spec.ts、useDiscoveryExecution.spec.ts；
webui/src/components/__tests__/TaskProgress.spec.ts、ScreenRoundActions.spec.ts、ParallelPlatformProgress.spec.ts、ResultHistoryDrawer.spec.ts、CollapsibleCard.spec.ts；
webui/src/views/__tests__/DiscoveryView.spec.ts（只补事件协作，不能冒充真浏览器）。

### 禁止与引用方向

- 未列路径不改，尤其 webui/app.py、webui/store.py、scripts/boss_cdp_raw.py、webui/task_continue_api.py、webui/source_zhilian_cdp.py、webui/src/views/DiscoveryView.vue、webui/src/composables/useDiscoveryExecution.ts。本计划从其下游解决，巨型门面不得追加业务。
- 046/033 与既有 047-large-file-followup 工件只读；不改旧保护哈希。
- 正式数据、凭据、浏览器空间、design/roadmap 只读；不改依赖锁、版本、packaging、.release 或发布 workflow。
- 引用：API → recovery/operation/lifecycle → store；store 不能 import service/app；scene helper 不能反向 import view；platform source 适配通用事实。
- 原 .trae/rules/project_rules.md 不存在，不新建工具专属目录；本索引和 .specify/feature.json 完整路径是当前上下文入口。

## Verification Gate

各 Task 明确依赖、文件、操作和证据，竞争用 barrier/Event/deferred promise，不靠 sleep。拆分与开发仅聚焦；全链收敛后唯一后端全量 → 前端全量 → 构建 → 卫生/diff/status。失败保存清单后聚焦返修，相关修复收敛前不重跑全量。

真实用户入口验收六项及 stop/finish 不同顺序全过程；临时库、桩、测试客户端不能叫真实 E2E。Plan 撰写时只做文档校验；实际实施与复选框更新以 tasks.md 的实施证据为准。

## 执行顺序和所有权

默认一个执行 AI 串行：授权/环境 → 独立结构前置 → US1 → US5 → US2 → US6 → US3 → US4 → 组合与最终门禁。US 编号保持 Spec 顺序。

公共状态/接口/投影不并行。只有未来明确多 AI 授权、独立工作目录且文件互不相交时可并行纯测试/只读工作。本轮未启动代理。先验证 US1/US5/US2 小闭环可行，但六项全部属于最终交付，不能以 MVP 代替全部。
