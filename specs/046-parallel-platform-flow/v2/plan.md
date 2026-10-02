# Implementation Plan: 多平台主流程分轨合流与统一条件映射（B096 V2）

**Branch**: `feature/parallel-platform-flow` | **Date**: 2026-09-27；2026-10-02 澄清 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/046-parallel-platform-flow/v2/spec.md` 与冻结映射 `contracts/filter-mapping.md`

## Summary

### 当前执行方式（2026-10-02）

- 当前交付状态：本轮源码修复与正式分轨合流主流程证据已收敛，交用户验收。最新前端1548条、构建与范围/尺寸检查通过；后端最终3506条的两项历史保护/卫生失败保留，未以修改基线或提交绕过。无后端相关改动不重复全量；实际运行、只读副本与未验证边界详见Tasks最终记录。
- 用户最新确认：继续完成整个分轨合流 V2，当前仅验收源码，EXE 不需要验证。本条覆盖下文历史源码/EXE 双门禁中的 EXE 要求；正式双轨非空、两种完成顺序、实例保存/暂停批次及合流阅读现场仍按既有验收执行，不因局部修复完成而宣称整个 V2 完成。
- 现有实现是核查起点，不从空白重新执行 V2。先按当前代码区分已符合、仍偏离与未验证项，再只修实际差距。
- 同一套业务逻辑接收独立实例上下文；单平台使用一份，“全部”使用两份。组合模块可以管理 Flow/Track 关系和页面合流，不得复制筛选、生命周期或动作判定。
- 进度复用 `TaskProgress.vue`，动作复用 `ScreenRoundActions.vue` 及其共享派生逻辑；组合壳只排列实例、绑定对应上下文、转发操作。仅复用外观不算完成。
- 页面导航与结果合流可有组合协调逻辑，但平台执行、单条线的状态含义、按钮可用性与终态处理须使用共同业务入口。
- 前一轮仅修改 V2 文档并只读核查；本次接手已获目标内实施授权，以本节下方精确修复边界为准。历史边界不得覆盖用户本次授权，提交、推送、合并和发布仍禁止。
- 原尺寸、测试及真实源码/EXE 门禁保留。下文“新增”“当前行数”“实施前”等描述属于 2026-09-27 初始计划，不能当作今天尚未完成的任务或实时测量。

### 2026-10-02 接手修复边界（当前生效）

最新真实双轨返修（先红测再实施）：R017 自动恢复常用配置重新覆盖用户在该配置基础上修改的本轮草稿（1×1 回退5×2），且共享 Workflow 在完成后返回02/03时清掉会话。精确允许 `webui/src/composables/useSearchPackages.ts` 及既有测试，仅自动恢复包身份时保留已经恢复的可用草稿，用户显式选择配置仍原样应用；允许现有 Workflow/测试保留完成轮的返回页与草稿。R018 首次观察轨道顺序仅存在内存，刷新改按续跑时间重排；允许现有 FlowPresentation/测试将已观察顺序作为按Flow隔离的页面现场保存，任务/结果事实仍取后台。上述共享文件不引入第二套平台业务、不改变后台、数据库或外部配置；范围门禁仅增精确路径，不改保护哈希。验收为正式原现场重新设置短草稿后刷新02/03保持、不同Flow顺序隔离及双轨结果阅读现场回归。

用户本轮已明确授权 V2-A01–A04 的代码与直接回归修复，覆盖前一轮仅文档边界；沿用现有分支，禁止提交、推送、合并、发布及正式数据修补。以下精确清单覆盖历史禁写条目中本轮直接相关的部分，其余历史边界保留。

- 允许产品文件：`webui/src/composables/useDiscoveryExecution.ts`、`useScreenRoundFlow.ts`、`useDiscoveryParallelFlow.ts`、`useDiscoveryFlowPresentation.ts`、`webui/src/screenFlow.ts`、`webui/src/components/ParallelPlatformProgress.vue`、`webui/src/views/DiscoveryView.vue`、`webui/flow_api.py`、`flow_service.py`、`flow_task_coordinator.py`。
- 允许新增：`webui/src/composables/useDiscoveryInstanceActions.ts`（实例身份、忙态、调用及合流协调）；`webui/flow_task_actions.py`（共用能力校验与既有 run 动作接线）；对应前端测试及 `tests/test_b096_instance_actions.py`。
- 直接共享边界补充：`webui/src/discovery.ts` 只登记唯一轨道终态词表；`webui/src/views/__tests__/DiscoveryRecovery.spec.ts` 属通知/结果合流直接回归（无需改产品恢复架构）。
- 真实运行后的直接合流返修：允许 `webui/src/composables/useDiscoveryResults.ts` 及其既有测试；共用 preservePresentation 合并按身份保留位置、采用最新判定、移除已淘汰的旧待确认对象；Flow 结果不得按先到平台改写共同草稿。只修 FR-014/015 的结果事实与阅读现场，不改筛选策略或数据库。
- 新轮直接回归返修：允许 `webui/src/composables/useDiscoveryTasks.ts`、`discoveryDeps.ts` 及既有测试；组合层先刷新权威 Flow，提供已归属 run 与当前未终结操作目标，共用新轮清理只取消这些目标，不向完成阶段发取消。保留 legacy 混合投影严格门禁；不改 `store_flow_state.py`，不修补正式 search_runs 历史状态。
- 允许测试：上述共享模块既有测试、`webui/src/__tests__/screenFlow.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`、`tests/test_b096_flow_api.py`、`tests/test_b096_flow_service.py`、`tests/test_b096_final_review.py`（只增本輪精确路径名单，不改历史哈希、数量或断言）。
- 允许文档：046 INDEX、V2 Plan/Tasks/Spec/数据模型/页面契约/Quickstart、宪法（只登记新模块）、README/CHANGELOG（仅本轮用户可感知修复）。
- 禁止：`design/**`、V1、V3、所有 `store_flow*.py`（本轮不需修改，不触发拆分）、平台树枝、runner、筛选算法、数据结构/迁移、发布配置、版本文件、main 工作目录与正式数据。
- 引用方向：view → 实例协调 → 共享执行/批次选择与既有 API；阶段投影 → 共享动作派生；Flow API → service → 动作能力/既有 run 入口 → store。组合层只刷新共同 Flow 和合流结果，不写单轨结果到全局现场。
- 验收用例：一轨保存另一轨仍运行；旧抓取卡无后续 AI 操作；恢复失败仅沿既有可恢复门禁；批次弹窗固定实例/run 且立即/等批参数一致；过期点击不操作新 run；合流保持分类/排序/阅读现场；两种顺序、返回/刷新/冷启动/新轮与单平台回归。
- 开发命令：`npm test -- <直接相关测试路径>`（webui）；`uv run python -m unittest tests.test_b096_instance_actions tests.test_b096_flow_api tests.test_b096_flow_service tests.test_task_pause_support`。最终全量只在本轮聚焦收敛后运行一次，真实源码、副本冷启动和 EXE 分别报告。

### 初始实施方案（历史，保留追溯）

2026-10-02 现状核查已记录在 `tasks.md`：核心 worker、继续入口及进度/动作组件已有复用，保留；剩余重点为实例级结束保存、阶段与操作目标对齐、失败动作门禁一致、批次选择复用。后续按该文件 V2-A01–A04 的顺序聚焦修复，不按下面初始方案重新建设全部能力。涉及历史禁写模块的路径目前仅为修复候选，不自动扩展写入授权。

在 V1 已完成的 Flow/Track、双平台执行、独立操作、历史和结果归属之上做增量修复，不重建后台流程。前端新增两个深模块：一处维护“全部”六类条件及确定性平台映射，一处把持久化 Track 事实投影成页面解锁、自动前进、真实任务进度和结果加入事件。现有 `ParallelPlatformProgress.vue` 降为原 `TaskProgress.vue` 的纵向编排壳；现有结果加载增加“同轮合流时保留阅读现场”模式。常用配置增加版本化条件快照；运行历史复用 `flow_tracks.confirmed_filters_snapshot` 保存 V2 快照信封，不新增 Flow 表结构。

## Technical Context

### 2026-10-02 继续真实验收

- R016 第二次定位：新副本完成轮可到04，正式旧浏览器刷新仍锁04；共享 `restoreSaved02State` 在 Flow/结果水合之后再次把旧快照的 `resultLoaded=false` 和结果载荷写回。精确补充 `webui/src/composables/useDiscoveryWorkflow.ts` 及既有测试、`useDiscoveryFlowCoordinator.ts` 和已允许 ParallelFlow：共享草稿恢复接受“保留权威结果”上下文，Flow 恢复后仅还原草稿/用户落点，不覆盖当前结果；无 Flow 仍保持 legacy 语义。引用为 Flow 恢复 → 共享 Workflow 恢复，结果事实继续由既有 Results 维护；不改 State、后台或现场存储结构。针对缓存覆盖当前结果写红测，再测正式原现场刷新和新单轨自动完成。

- R016 正式短任务 completed/succeeded 六分钟稳定、Flow done 且已有结果，页面却停在 03/04 disabled。实际从“单独抓取”启动，auto_screen=false，但权威 Flow 已运行 AI；前端交接仍以旧自动筛选标记判定是否接回。精确允许现有 `useDiscoveryTasks.ts`、其既有测试、`DiscoveryView.spec.ts` 修复单轨 Flow 的共享交接，必要时仅补 `useDiscoveryFlowCoordinator.ts` 的同 Flow 状态变化订阅及既有视图测试；不修改后端、筛选策略、State 或接口。先写针对该真实条件的失败用例，实例操作及全局 legacy 自动筛选路径保持原门禁。引用仍为 Tasks → 已有 Flow 水合/restoreRunningTask/结果加载；不提交第二个 AI 任务。验收为真实“单独抓取”已有 AI 接回、完成后04可见，刷新/冷启动读取同一结果。

- R015 运行证据更正：完成 AI 在 05:29:47 已 finalized succeeded，遗留 JD 批次在 05:33:19/39 继续写 stall/fallback/unit_failed；正式库与冷启动副本事件相同，不是启动恢复写坏。精确允许 `webui/pipeline_guard.py`（430 行）和 `tests/test_pipeline_guard.py`、最终范围测试路径名单；共享监控在杀进程、记录失败或暂停前核对对应任务的内存与持久状态，已暂停或已终结批次退出监控。引用方向仍为各 worker → 共享 guard → 既有状态查询/白箱；不修改超限 pipeline_exec_details、store、归约器、正式历史记录。失败用例覆盖完成后遗留批次、stall 后任务终结、持久终态与旧内存冲突；原活体失联暂停/重试保持。新真实任务及新副本冷启动重新取证，旧受污染 run 不修补。

- 用户再次明确“开始吧”；继续 T055–T057 的未验证项，已有通过自动化不机械重跑。
- 本轮先只增量维护 V2 Plan/Tasks 的实际证据；通过正式网页使用既有画像、账号与条件执行，浏览器恢复也仅调用应用自带入口。副本仅只读备份正式库并在副本设置 test。
- 若出现产品缺陷，先定位、写失败用例，再在上方既有精确允许范围内修复；禁止绕过状态门禁、操作外部平台或修补正式库。现有平台树枝、store、版本与发布文件仍禁止修改。
- 未通过的主流程、冷启动和 EXE 分开报告；环境缺失或无法确认平台状态不能算通过。
- 正式新轮追加失败：Flow 两轨 stopped，但共享动作入口以 flowActive 判断“属于 Flow”，于是终结后误用旧暂停现场阻断新轮。仅在既有 `useScreenRoundFlow.ts` 接收 Flow 归属投影、`DiscoveryView.vue` 薄接线及对应既有测试修复；保持单平台 legacy 暂停守卫与共享取消/归档门禁，不修改超限 State/store 文件。失败用例覆盖终结 Flow 的旧暂停投影和无 Flow 的真实暂停。
- 单平台直接回归追加失败：真实单轨 Flow 后端已起 AI，而前端归属谓词只认可 all，加上首次启动未水合 Flow，旧自动筛选重复提交并显示失败。仅在既有 ParallelFlow 归属谓词、Tasks 完成抓取交接、discoveryDeps/View 接线及其既有测试修复；交接先刷新 Flow、单轨复用 restoreRunningTask 接回已有 worker。组合展示保持原路径，legacy 无 Flow 仍走原启动；不修改 worker/API/store，不新建筛选实现。
- 暂停继续直接回归追加失败：正式任务立即暂停、继续后数据库 succeeded，而公开白箱仍 interrupted，页面停在 03。此次精确允许 `webui/runners/ai_screen_task.py`（实测 639 行）仅调整同 run 续跑的白箱初始化，复用既有 `WhiteboxService.resume`；允许 `tests/test_b096_final_review.py` 增失败用例及路径名单。此项覆盖上方 runner 禁写的这一处，其余 runner、白箱/store 实现、平台树枝及正式数据仍禁止修改。引用方向为既有继续 API → 共享 AI worker → 既有白箱 resume；不忽略完整性结论、不另造恢复逻辑。验收：旧暂停结论在续跑开始重置、审计事件保留、严格终态门禁保留；正式 UI 立即暂停→继续→结果页。

- R014 第二次真实复测：暂停/继续均 HTTP 200，续跑结束却 partial；旧暂停事件被复投影到同一尝试。精确补充允许 `webui/whitebox.py`（501 行）与 `tests/test_whitebox_integration.py`：共享 resume 为未完成单元建立新尝试，既有 record_for_owner 绑定当前尝试；不改阶段算法、store、规则归约器或终态门禁。引用为 worker → 共享白箱 → 既有 store API，抓取和 AI 一并直接回归。失败用例覆盖旧审计/已完成单元保留、新尝试正确完成。

**Language/Version**: Python 3.10+；TypeScript 5.9；Vue 3.5
**Primary Dependencies**: Flask 3.x、SQLite、Vue 3、Vite 8、Vitest 4、pywebview 6
**Storage**: 正式 SQLite；现有 `flows`/`flow_tracks`；`search_packages` 增加一个 JSON 快照列
**Testing**: Python `unittest`；Vitest + Vue Test Utils；Vite 构建；真实源码运行与打包 EXE 主流程
**Target Platform**: 本地 Web 工作台与 Windows EXE；macOS 构建不在本轮真实验收范围
**Project Type**: Python 本地服务 + Vue 单页工作台 + pywebview 桌面壳
**Performance Goals**: 轮询沿用 2 秒节奏；第二平台到达后在下一次轮询内进入对应页面/结果；映射为纯内存同步操作
**Constraints**: 不等待慢平台；不重载结果页；不重做进度条；不读取旧设计图；不修改平台抓取树枝和 B106；刷新不自动跳页
**Scale/Scope**: 当前仅 BOSS、智联两平台；六个共同字段、两个平台专属字段、四个步骤页

## Constitution Check

### Plan 前检查

- **执行与授权**：用户已授权生成到 Tasks 结束；本阶段不进入实现。
- **复用优先**：复用 `TaskProgress.vue`、Flow/Track API、任务状态 API、结果投影、灵动岛入口和现有历史结构。
- **树干/树枝**：统一映射和页面投影属于树干；平台 schema 仍是树枝权威，树干只消费公开 schema 标签和值。
- **文件尺寸**：`DiscoveryView.vue` 当前 1199 行，只允许薄接线且最终必须净减少；不修改 1574 行的 `useDiscoveryState.ts`。
- **门面纪律**：`webui/store_migrations.py` 仅增加 mixin 组装；不向 `webui/app.py`、`webui/store.py`、`webui/exec_search_api.py` 追加逻辑。
- **测试层级**：开发阶段聚焦；整条链收敛后一次全量；真实源码和 EXE 验收不可由替身代替。
- **结论**：无宪法豁免；可进入研究与设计。

### Phase 1 后复查

- 新业务逻辑均落在新深模块或既有领域模块，引用方向无反转。
- Flow 运行历史使用现有 JSON 列；只有常用配置新增迁移 040，避免扩大核心 Flow schema。
- `TaskProgress.vue` 不改视觉和进度算法；并行壳只负责列表、阶段过滤和操作转发。
- V1 未冲突能力、单平台入口和 B106 保持不变。
- 结论：设计满足宪法，可进入 Tasks。

## File Boundaries

### Allowed files

计划与契约：

- `specs/046-parallel-platform-flow/INDEX.md`
- `specs/046-parallel-platform-flow/v2/plan.md`
- `specs/046-parallel-platform-flow/v2/research.md`
- `specs/046-parallel-platform-flow/v2/data-model.md`
- `specs/046-parallel-platform-flow/v2/quickstart.md`
- `specs/046-parallel-platform-flow/v2/contracts/filter-mapping.md`（冻结，只读）
- `specs/046-parallel-platform-flow/v2/contracts/condition-snapshot.md`
- `specs/046-parallel-platform-flow/v2/contracts/flow-presentation.md`
- `specs/046-parallel-platform-flow/v2/tasks.md`

产品代码：

- `.specify/memory/constitution.md`（仅登记新模块）
- `webui/flow_api.py`
- `webui/search_packages.py`
- `webui/store_search_packages.py`
- `webui/store_migrations.py`（仅组装）
- `webui/store_migrations_v8.py`
- `webui/src/parallelFilterMapping.ts`
- `webui/src/types.ts`
- `webui/src/components/OneClickScreenDialog.vue`
- `webui/src/components/ParallelPlatformProgress.vue`
- `webui/src/composables/useDiscoveryParallelFlow.ts`
- `webui/src/composables/useDiscoveryFlowPresentation.ts`
- `webui/src/composables/useDiscoveryIslandBridge.ts`
- `webui/src/composables/useDiscoveryResults.ts`
- `webui/src/composables/useSearchPackages.ts`
- `webui/src/composables/useTheme.ts`
- `webui/src/styles/theme.css`
- `webui/src/views/DiscoveryView.vue`
- `README.md`
- `CHANGELOG.md`

聚焦测试：

- `tests/test_b096_flow_api.py`
- `tests/test_b096_final_review.py`
- `tests/test_b096_flow_history.py`
- `tests/test_search_packages.py`
- `tests/webui_store/test_store_migrations.py`
- `webui/src/__tests__/parallelFilterMapping.spec.ts`
- `webui/src/components/__tests__/OneClickScreenDialog.spec.ts`
- `webui/src/components/__tests__/ParallelPlatformProgress.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryParallelFlow.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryIslandBridge.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryResults.spec.ts`
- `webui/src/composables/__tests__/useSearchPackages.spec.ts`
- `webui/src/composables/__tests__/useTheme.spec.ts`
- `webui/src/views/__tests__/DiscoveryRecovery.spec.ts`
- `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts`
- `webui/src/views/__tests__/DiscoveryView.spec.ts`

### Forbidden files

- `design/**`
- `specs/046-parallel-platform-flow/v1/**`
- `webui/src/composables/useDiscoveryState.ts`
- `webui/app.py`、`webui/store.py`、`webui/exec_search_api.py`
- `webui/flow_service.py`、`webui/flow_ai_coordinator.py`、`webui/store_flow*.py`
- `webui/runners/**`
- `scripts/boss/**`、`scripts/zhilian/**`、`scripts/boss_cdp_raw.py`
- B106 去重实现与测试
- `packaging/**`、版本提升、提交、推送和发布文件

### Pre-V2 baseline gate

- `pre-v2-protected.sha256` 冻结 V2 开始前已经存在、但不属于本 Plan 允许写入范围的 76 个工作区文件；这些文件即使原本已修改或未跟踪，V2 也不得继续改变其内容。
- `tests/test_b096_final_review.py` 必须读取该清单并校验每个文件的 SHA-256；同时读取 `git status --porcelain=v1 --untracked-files=all`，任何不在允许清单、也不在冻结清单中的新增差异都必须失败。
- `design/**` 遵守用户禁令，不读取、不哈希、不修改；该目录不作为实现参考。
- 基线清单只用于区分 V1 既有工作与 V2 本轮差异，不表示清单内文件已提交，也不改变其所有权或完成状态。

### New files

- `specs/046-parallel-platform-flow/v2/pre-v2-protected.sha256`：V2 实施前的只读工作区边界基线。
- `webui/src/parallelFilterMapping.ts`（约 250 行）：统一字段定义、冻结映射、schema 标签解析、单字段覆盖、简历语义投影和快照构造纯函数。
- `webui/src/composables/useDiscoveryFlowPresentation.ts`（约 320 行）：真实任务快照、阶段列表、稳定顺序、页面解锁/跳转抑制、结果加入信号。
- `webui/store_migrations_v8.py`（约 60 行）：迁移 040，为常用配置增加条件快照 JSON 列。
- `webui/src/__tests__/parallelFilterMapping.spec.ts`（约 220 行）：映射表全覆盖、互斥、覆盖与快照测试。
- `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts`（约 300 行）：两种完成顺序、近同时、返回、刷新、失败与一次性通知测试。

### Reference direction

```text
DiscoveryView
  ├─> useDiscoveryParallelFlow ─> API
  ├─> useDiscoveryFlowPresentation ─> task-state API / injected result refresh
  ├─> OneClickScreenDialog ─> parallelFilterMapping
  └─> ParallelPlatformProgress ─> TaskProgress / ScreenRoundActions

flow_api ─> FlowService ─> existing Flow store
search_packages_api ─> SearchPackageService ─> StoreSearchPackagesMixin
store_migrations facade ─> StoreMigrationsV8Mixin ─> older migration mixins
```

后端不得依赖前端；纯映射模块不得依赖 Vue 页面；组件不得直接访问 SQLite；平台树枝不得反向依赖统一映射。

### Line gate

- `DiscoveryView.vue` 实施后必须低于 1200 行且相对当前 1199 行净减少。
- 新 Vue 文件不超过 900 行预警线；新 Python 文件不超过 600 行预警线。
- `flow_api.py`、`search_packages.py`、`store_search_packages.py` 保持低于 800 行。
- `store_migrations.py` 只增加 import/MRO，不增加迁移业务逻辑。

### Rationale

条件映射和页面投影都是新的共享树干职责，塞入 `DiscoveryView.vue`、`useDiscoveryState.ts` 或平台模块都会违反尺寸和引用方向。Flow 条件历史可由现有 JSON 快照承载；常用配置是独立持久实体，需要一个可迁移、可校验的新 JSON 列。并行进度组件保留名称以兼容现有接线，但内部改成原进度组件的列表壳，避免第二套视觉和算法。

## Verification Gate

- 每个故事先写聚焦红测，再实施，再只跑该故事及直接相邻回归。
- US1：映射、无确认、主题、配置快照与迁移。
- US2：真实任务快照、两种先后顺序、近同时、独立操作和原进度条复用。
- US3：结果原地合流、阅读现场和一次性灵动岛提示。
- US4：02/03/04 刷新、已解锁入口、旧页停留、历史快照和流程隔离。
- 整条 V2 收敛后只运行一次：后端全量、前端全量、前端构建、卫生测试、`git diff --check`、`git status`。
- 自动化通过后必须经正式入口和正式运行库完成真实 BOSS/智联源码 E2E；再构建 EXE 并做同一路径最小充分真实验证。任一真实门禁未完成，只能报告“未验证”，不得称 V2 完成。

## Project Structure

### Documentation

```text
specs/046-parallel-platform-flow/v2/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── filter-mapping.md
│   ├── condition-snapshot.md
│   └── flow-presentation.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code

```text
webui/
├── flow_api.py
├── search_packages.py
├── store_search_packages.py
├── store_migrations.py
├── store_migrations_v8.py
└── src/
    ├── parallelFilterMapping.ts
    ├── types.ts
    ├── components/
    │   ├── OneClickScreenDialog.vue
    │   ├── ParallelPlatformProgress.vue
    │   └── TaskProgress.vue              # 只复用，不修改
    ├── composables/
    │   ├── useDiscoveryParallelFlow.ts
    │   ├── useDiscoveryFlowPresentation.ts
    │   ├── useDiscoveryIslandBridge.ts
    │   ├── useDiscoveryResults.ts
    │   ├── useSearchPackages.ts
    │   └── useTheme.ts
    ├── styles/theme.css
    └── views/DiscoveryView.vue

tests/
├── test_b096_flow_api.py
├── test_b096_final_review.py
├── test_search_packages.py
└── webui_store/test_store_migrations.py
```

**Structure Decision**: 保持现有 Python + Vue 双层项目结构。新模块放入现有 Flow 展示域和前端纯映射域；不新建平台适配层，不触碰抓取实现。

## Complexity Tracking

无需要豁免的宪法违反项。
