# Implementation Plan: 一个流程内多平台并行抓取与筛选（B096）

**Branch**: `codex/feature/b096-parallel-platform-flow` | **Date**: 2026-09-26 | **Spec**: [spec.md](spec.md)
**阶段**：已完成 T001–T003 前置拆分门禁、T004–T034 与 T038，并完成第三轮复审返修；T036 全量后端门禁已尝试但返修后未重跑，T037 卫生门禁保留新增未跟踪文件失败；真实浏览器布局和真实双平台 E2E 未验证。

## Summary

在现有 01–04 工作台中增加默认“全部”入口。七类条件框内有 BOSS、智联两个平台切换项及对应面板，每个平台各有完整七类条件；同一份简历分析按各平台条件分别投影建议。用户分别确认后，一次创建一个流程和两条独立运行线。服务端持久记录流程与运行线，将各平台抓取、AI 筛选、暂停/继续/停止、部分结果及失败分别归属；04 页和历史按流程 ID 取数，现有归档行为只作用当前流程。单平台入口继续可用，B096 上线后的新单平台轮次也创建单轨流程并遵守同一新轮门禁；任一平台被系统禁用新任务时，“全部”明确阻止启动。现有跨平台去重策略保持原样，B106 不在本计划内。

技术路线以现有 Flask + SQLite + Vue 3 能力为基础，不引入第三方服务。真正并行的前置条件是解开现有单 worker、全局浏览器占用判定和进程级活动 CDP profile；通过隔离测试证明不同平台并发与同平台资源互斥，才能开放“全部”。

## Technical Context

- **Language/Version**: Python >=3.10；TypeScript 5.9、Vue 3；Node >=20。
- **Primary Dependencies**: Flask、SQLite（标准库）、现有 CDP/抓取和 AI 筛选链路；前端 Vitest。无新增运行依赖。
- **Storage**: 现有 SQLite `screening_runs`/结果快照，加流程与平台运行线关系；既有库迁移须保持旧轮次可读。
- **Testing**: `unittest`、Vitest、应用对外入口的真实双平台 E2E；`tests/test_e2e_smoke.py` 只算跨层冒烟。
- **Target Platform**: 项目既有桌面/WebUI 运行方式，BOSS 与智联各自 CDP 登录空间。
- **Project Type**: Flask 服务 + Vue 单页工作台。
- **Performance Goals**: 一平台完成抓取或筛选后立即推进/可查看结果，不被另一平台阶段、暂停或错误阻塞；不规定未经用户确认的秒数指标。
- **Constraints**: 用户确认的禁用平台门禁、流程隔离、单平台兼容、B106 边界；不读取或复制凭据到流程响应/日志。
- **Scale/Scope**: 每个“全部”流程恰含 BOSS、智联各一条线；不扩展第三平台或批量流程。

## Constitution Check

| 门禁 | 设计结论 |
| --- | --- |
| 单向职责 | 新 API 只做路由/校验/响应，协调在 service，持久化在 store；前端 view 只装配 composable/component。 |
| 文件规模 | 047 拆分基线为 `exec_search_api.py` 901 行、`DiscoveryView.vue` 1202 行；当前实测分别为 626 行和 1199 行，B096 只保留薄装配/兼容接线，不再把业务逻辑堆回两文件。 |
| 门面 | `webui/app.py`、`webui/store.py`、`webui/store_migrations.py` 仅注册/组装，不承载业务。 |
| 并发安全 | 现有执行器 `max_workers=1`、`browser_support.py` 全局忙判定、`pipeline_exec_accounts.py` 进程级 `_ACTIVE_CDP_DATA_DIR` 都是实测阻碍。不能只改 worker 数；必须先证明每平台资源与冻结身份隔离。 |
| 验证 | 开发聚焦；整条 B096 链收敛后一次后端全量、前端测试、构建、卫生；真实 E2E 另经公开入口。 |
| 公开仓库 | 不写真实账号、Cookie、密钥或本机路径到代码、测试产物和文档。 |

**实施前阻断已解除**：原文件落位清单未包含 `webui/browser_support.py`、`webui/pipeline_exec_accounts.py`、`webui/store_migrations_v1.py` 和前端现场恢复模块；T002 已按调用链把必要扩边限定为这些最小接点，T001 已由 047 独立拆分 Spec 与兼容证据解除红线。该扩边不增补用户产品需求。

## File Boundaries

- **已确认的 B096 产品落位**：新建 `webui/flow_service.py`（流程协调）、`webui/flow_api.py`（路由）、`webui/store_flow.py`（薄门面）及 `store_flow_core.py`、`store_flow_claims.py`、`store_flow_preflight.py`、`store_flow_results.py`、`store_flow_legacy.py`、`store_flow_runs.py`、`store_flow_state.py`（按职责拆分的持久化域）、`webui/store_migrations_v7.py`（迁移 038/039）、`webui/ai_screen_failure.py`、`webui/flow_future.py`、`webui/flow_task_state.py`、`webui/flow_submission_service.py`、`webui/flow_ai_coordinator.py`、`webui/task_continue_support.py`、`webui/task_continue_results.py`、`webui/task_continue_finish.py`、`webui/src/composables/useDiscoveryParallelFlow.ts`（两条线的前端状态/API）、`webui/src/components/ParallelPlatformProgress.vue`（进度与操作）；集成点 `webui/app.py`、`webui/app_support.py`、`webui/store.py`、`webui/store_migrations.py`、`webui/exec_search_api.py`、`webui/ai_screen_api.py`、`webui/results_api.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/store_result_history_mixin.py`、`webui/src/views/DiscoveryView.vue`、`webui/src/composables/useDiscoveryState.ts`、`useDiscoveryExecution.ts`、`useDiscoveryTasks.ts`、`useDiscoveryResults.ts`、`webui/src/components/OneClickScreenDialog.vue`、`webui/src/composables/resultHistory.ts`、`webui/src/components/ResultHistoryDrawer.vue`；仅添加相应聚焦测试与用户文档。
- **待确认扩边**：`webui/browser_support.py`（按流程/平台判断资源占用）、`webui/pipeline_exec_accounts.py` 与可能的 `webui/frozen_browser_identity.py`（并发身份隔离）、`webui/store_migrations_v1.py`（调度 038）、`webui/src/composables/useDiscoverySearch.ts`（现有简历建议按平台 schema 投影）、`webui/src/composables/useDiscoveryWorkflow.ts`、`useDiscoverySceneState.ts`、`useDiscoverySceneIdentity.ts`（刷新与现场身份）、`.specify/memory/constitution.md`（依项目规则登记新增模块）。实施前实测调用链后确定最小清单，不得把“可能”路径当成已获修改授权。
- **禁止修改**：`scripts/boss/`、`scripts/zhilian/`、`webui/cross_platform_dedupe.py`、`specs/019-cross-platform-job-dedup/`；不变更 B106 去重默认值/开关/取舍，不扩展其他平台。
- **引用方向**：`flow_api → flow_service → store_flow`；现有运行器通过公开能力被协调；`DiscoveryView → useDiscoveryParallelFlow → API`；无 store 反向 import API。
- **Line gate**：新 Python 模块 <800、Vue 组件 <1200；既有 600/900 预警线分流。2026-09-27 实测：`exec_search_api.py` 626、`exec_search_resume.py` 401、`ai_screen_api.py` 582、`runners/ai_screen_task.py` 570、`store_flow.py` 35、`store_flow_core.py` 178、`store_flow_claims.py` 632、`store_flow_preflight.py` 67、`store_flow_results.py` 183、`store_flow_legacy.py` 198、`store_flow_runs.py` 64、`store_flow_state.py` 242、`store_runs.py` 764、`flow_service.py` 561、`flow_api.py` 174、`flow_future.py` 128、`ai_screen_failure.py` 67、`flow_task_state.py` 192、`flow_submission_service.py` 195、`flow_ai_coordinator.py` 472、`task_continue_api.py` 550、`task_continue_finish.py` 296、`task_continue_support.py` 39、`task_continue_results.py` 104`；`DiscoveryView.vue` 1199。所有硬红线文件低于 800/1200，600/900 警戒文件不再堆叠业务逻辑。`specs/048-task-continue-split/` 记录续跑 facade 的独立拆分和兼容证据；B096 不把新业务逻辑加回门面。
- **Rationale**：流程是跨平台共用概念，落在树干；平台脚本无需改动。新模块承接状态与协调，避免把逻辑堆入既有门面和超限文件。

## 实施前门禁核验（2026-09-26）

前置核验最初于本轮发现阻断，随后按监督决定在同一 B096 实施链内补齐 047 独立技术拆分 Spec 并完成最小职责提取；当前 T001–T003 已解除，可继续 T004。

- **T001：通过。** 新增 `specs/047-large-file-followup/`，复用 021 的职责拆分与兼容合同，仅补现有红线文件的后续超限证据；另新增 `specs/048-task-continue-split/` 记录续跑 facade 的独立技术拆分。047 提取基线为 `webui/exec_search_api.py` 901 → 764 行、`webui/src/views/DiscoveryView.vue` 1202 → 1136 行；B096 生产链路接线曾使搜索门面回升到 906 行，已继续将续跑职责提取到 `webui/exec_search_resume.py`，当前实测为 **401 行**与**1199 行**。048 将结束保存职责提取到 `webui/task_continue_finish.py`，当前 `task_continue_api.py` 为 **550 行**、提取模块为 **296 行**，公开路由、方法、响应字段及可替换依赖兼容保持。
- **T002：通过。** 后续 B096 的最小扩边已按调用链核对为：后端 `app_support.py`、`browser_support.py`、`pipeline_exec_accounts.py`、`frozen_browser_identity.py`；迁移 `store_migrations_v1.py`/`store_migrations.py`；前端 `useDiscoverySearch.ts`、`useDiscoveryWorkflow.ts`、`useDiscoverySceneState.ts`、`useDiscoverySceneIdentity.ts`。047 只落地拆分所需的三个新模块，不提前改这些 B096 扩边文件。
- **T003：通过。** 已记录 Python/Vue 实测行数及兼容证据；047 与 048 新模块均按项目规则登记到 `.specify/memory/constitution.md`，未改原则内容。前置证据：`uv run python -m unittest tests.test_047_split_compat tests.test_resume_continue tests.webui_app.test_webui_app_platform` 106/106、`npm test -- --run src/views/__tests__/DiscoveryView.spec.ts` 118/118、`npm run build` 通过、`git diff --check` 通过。后续 B096 收敛证据更新为质量复审聚焦 30/30、后端聚焦 59/59、直接 API/store/resume 回归 62/62、平台/任务回归 155/155；当前实际行数以本节 Line gate 为准。

## Implementation record (2026-09-26)

- **Foundational / US1 / US2 / backend US3**：已落地迁移 038、Flow/Track store、双平台执行容量与浏览器/profile 隔离、FlowService/API、单平台真实单轨 Flow、AI/抓取运行器状态绑定、按 Flow 结果/归档/历史投影。实现文件集中在 `webui/flow_api.py`、`flow_service.py`、`store_flow.py`、`store_migrations_v7.py`、`app_support.py`、`browser_support.py`、`pipeline_exec_accounts.py`、`result_history.py`、`result_history_api.py` 及必要薄接线。
- **前端入口、结果与双线状态**：`OneClickScreenDialog.vue`、`useDiscoveryParallelFlow.ts`、`ParallelPlatformProgress.vue` 已支持默认“全部”、BOSS/智联各自 schema、七类面板、独立草稿/确认、Flow 创建后两条 `/api/execute-search` 薄调用、轮询和单线操作；`useDiscoveryResults.ts`、`resultHistory.ts`、`ResultHistoryDrawer.vue` 已按 Flow 接入当前结果、归档和两级历史；`DiscoveryView.vue` 只负责薄装配，当前 1199 行。
- **验证证据**：见 [quickstart.md](quickstart.md) 的聚焦命令与数量；后端 B096 聚焦最新 59/59、直接 API/store/resume 回归 62/62、平台/任务/迁移回归 155/155 通过，质量复审聚焦 30/30，包含生产链路、迁移 039、恢复、归档和范围兼容回归。最终门禁尝试运行 3337 项，初次有 5 项失败；其中 3 项是迁移 038 测试仍期待旧版本号、1 项是新失败补偿文件触发 pass-only 规则，已由直接测试修正，未在返修后重跑整套长耗时全量；剩余 hygiene 失败是用户禁止提交导致的新文件未跟踪。前端全量最新 1080/1080，构建 2296 modules 和差异检查通过。测试数据为临时 SQLite、内存任务替身和 Vitest/jsdom。
- **未收敛项**：T033 的真实浏览器布局、T035 的真实双平台 E2E 尚未执行；T037 的卫生测试唯一失败为本轮新增文件尚未提交，故保留为收口关注项。当前环境无可授权真实账号/登录态，不伪造 E2E 证据。

## 第三轮复审收敛记录（2026-09-27）

- **职责与红线**：Flow store 已拆为薄门面与 claims/preflight/results/legacy/run-identity/core 深模块；`store_runs.py` 移出 B096 抓取 identity 写入。新增模块均低于 800 行，`store_flow_claims.py` 为 632 行且不再继续堆逻辑；`store_runs.py` 为 764 行。随后质量复审将续跑 facade 的 829 行红线单独记录到 048，并提取为 `task_continue_api.py` 550 行 + `task_continue_finish.py` 296 行；抓取/AI 协调分别落在 `flow_submission_service.py` 184 行与 `flow_ai_coordinator.py` 374 行，状态收口落在 `flow_task_state.py` 184 行。
- **运行链路**：Flow 抓取、AI 自动提交、续跑统一经按平台 lane；同平台抓取与 AI 共用资源容量，跨平台仍可并发，Flow lane 缺失时不回退全局 executor。抓取完成后按 Track 独立创建 AI run 并自动提交；Future、浏览器激活、任务注册、run 写入、Flow 绑定及搜索 row 失败均进入安全错误码和 task/run/Track 补偿。
- **持久化与恢复**：迁移 039 增加无凭据的 `submission_snapshot_json`，只允许在尚无 run/claim 的 queued/paused/interrupted Track 上原子记录 preflight 失败；运行/已 claim Track 的重试保持活动态。无 run 的 paused Track 通过原始 execute-search 重新校验并 claim；finalizing pause 返回 409，完成回调以持久 paused guard 防止覆盖。AI claim、结果 run 绑定和 Track 终态在 SQLite 事务内受状态与来源校验保护。
- **测试证据**：质量复审聚焦命令 `uv run python -m unittest tests.test_048_task_continue_split_compat tests.test_b096_quality_round4 tests.test_b096_production_flow tests.test_b096_round2_review tests.test_b096_round3_review` 为 30/30；`uv run python -m unittest tests.test_b096_round3_review tests.test_b096_round2_review tests.test_b096_production_flow tests.test_047_split_compat tests.test_b096_flow_store tests.test_b096_flow_service tests.test_b096_flow_api tests.test_b096_flow_history tests.test_b096_review_regressions` 为 59/59；直接 API/store/resume 回归 `uv run python -m unittest tests.test_b096_flow_api tests.test_b096_flow_service tests.test_b096_flow_store tests.test_b096_flow_history tests.test_b096_review_regressions tests.test_resume_continue` 为 62/62；`uv run python -m unittest tests.webui_app.test_webui_app_platform tests.webui_app.test_webui_app_taskrun tests.webui_store.test_store_migrations.Migration38FlowSchemaTests` 为 155/155。第三轮单独命令 `uv run python -m unittest tests.test_b096_round3_review` 为 12/12。状态一致性、finalizing 竞态、API→service→store 调用边界、共享 lane、preflight retry 与失败补偿均使用临时 SQLite/受控执行器或真实 Flask test client 覆盖；不把这些测试称为真实平台 E2E。

## 第五轮架构与原子性收敛记录（2026-09-27）

- **API→service/coordinator→store**：Flow 抓取入口在 Flow 分支经 `FlowSubmissionService.begin_whitebox()`，AI Flow 路径的 run/task/恢复/失败补偿经 `FlowAiCoordinator`/`FlowTaskState`；legacy 分支保持独立兼容。`tests.test_048_task_continue_split_compat` 使用真实 Flask test client 和注入 coordinator spy，验证路由只调用协调层。
- **多实体状态原子性**：`store_flow_state.py` 用同一 SQLite `BEGIN IMMEDIATE` 事务收口 screening run、search run、Flow Track 与事件；`tests.test_b096_quality_round4.test_flow_failure_state_is_atomic_when_search_update_fails` 通过 SQLite 故障触发器证明中途写失败会回滚，未留下部分状态并冒充成功。
- **最新行数与聚焦证据**：`exec_search_api.py` 626、`ai_screen_api.py` 582、`flow_submission_service.py` 195、`flow_ai_coordinator.py` 472、`flow_task_state.py` 192、`store_flow_state.py` 242、`store_flow.py` 35、`flow_service.py` 561、`task_continue_api.py` 550，均低于 Python 800 行门禁；聚焦命令 `uv run python -m unittest tests.test_048_task_continue_split_compat tests.test_b096_quality_round4 tests.test_b096_production_flow tests.test_b096_round2_review tests.test_b096_round3_review` 为 33/33。测试使用临时 SQLite、受控服务和 Flask test client；真实账号、真实平台 E2E 与真实桌面/窄屏布局仍未验证。

## Phase 0: Research decisions

详见 [research.md](research.md)。关键决策：持久流程身份作为所有查询边界；每条线独立运行及终态；保留既有单平台路径；在并发身份/资源隔离得到测试证明前不释放全局门禁。外部现成方案只能作思路参考，不能替代本项目 CDP/历史契约。

## Phase 1: Design & Contracts

1. [data-model.md](data-model.md) 定义流程、运行线、确认快照、部分抓取岗位与旧 run/结果的关系；迁移 038 仅增量、保留 legacy 轮次读取，失败需保持原库可回退。
2. [contracts/http-api.md](contracts/http-api.md) 定义启动、状态、操作、流程限定的结果/归档/历史与错误，不把内部临时任务表当作唯一真相；提交重试不得产生重复流程。
3. [contracts/ui-interaction.md](contracts/ui-interaction.md) 记录七类条件框的两个平台面板、01–04 页和空/加载/成功/失败/窄屏/焦点态验收。
4. [quickstart.md](quickstart.md) 定义聚焦检查、最终门禁及真实双平台验证场景。
5. `.trae/rules/project_rules.md` 当前不存在，故跳过 Agent context 更新；不新建该文件。

## Verification Gate

- 实施中只跑新增测试、原失败用例与直接影响回归。并发测试须覆盖 BOSS/智联交错完成、暂停/失败、同平台资源冲突、冻结账号/端口不串用；迁移测试须覆盖旧库、重复迁移、失败回滚。
- 收敛后执行一次 `uv run python -m unittest discover -s tests`；随后在 `webui/` 执行 `npm test` 和 `npm run build`；运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`。
- 经应用对外入口、项目正式启动方式及已就绪真实账号做双平台 E2E；无法满足条件时标记“未验证”，不得用 mock/临时库冒称真实 E2E。外部平台异常需区分产品缺陷与环境阻断。
- 最终检查同一份简历分析对两平台分别投影建议、七类条件双面板、两线独立推进、无筛选快照时部分岗位的“未完成 AI 筛选”标识、失败零岗位历史方块、已完成岗位的正确来源轮次与整理操作、现有归档行为按 Flow 收紧，以及单平台↔全部的双向流程隔离；桌面与窄屏的溢出/双滚动条须用真实浏览器渲染检查；B106 去重行为只回归“不变”。

## Project Structure

```text
specs/046-parallel-platform-flow/v1/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/{http-api,ui-interaction}.md
├── quickstart.md
└── tasks.md
webui/
├── flow_api.py / flow_service.py / store_flow*.py / store_migrations_v7.py
├── ai_screen_failure.py / flow_future.py / task_continue_*.py
├── existing API, runner, store and history modules (bounded integration)
└── src/
    ├── composables/useDiscoveryParallelFlow.ts
    ├── components/ParallelPlatformProgress.vue
    └── existing Discovery and history surfaces (bounded integration)
tests/ + webui/src/**/*.test.ts
```

**Structure Decision**：沿用单仓库 Flask/Vue 目录，不建新服务；先解决文件门禁，再以共享流程域承接新能力。Tasks 依赖图不得把有共享状态的后端改动标成可并行写入。

## Current-flow default selection follow-up (2026-09-27)

Discovery 点击 `start-one-click` 后，`prepareDialog` 只在返回明确 `flow.selection` 时切换模式；current Flow reject 或空响应保留默认/当前“全部”，双平台确认弹窗仍可打开。`useDiscoveryParallelFlow.available` 改为表示 current 接口成功可用，不再把“是否有 current Flow”当作并行能力。两条点击级红测先失败后通过；三文件聚焦 137/137、前端全量 67 文件/1084 测试、构建 2296 modules 通过。

## Final validation evidence (2026-09-27)

本节是当前工作区的有效行数与验证记录；上文各轮记录保留为历史快照。当前行数为：`exec_search_api.py` 637、`exec_search_resume.py` 401、`ai_screen_api.py` 551、`runners/ai_screen_task.py` 624、`runners/pipeline_task.py` 598、`pipeline_task_outcome.py` 240、`store_runs.py` 764、`store_flow.py` 35、`store_flow_core.py` 178、`store_flow_claims.py` 632、`store_flow_preflight.py` 67、`store_flow_results.py` 183、`store_flow_legacy.py` 198、`store_flow_runs.py` 64、`store_flow_state.py` 242、`flow_service.py` 565、`flow_api.py` 174、`flow_future.py` 116、`ai_screen_failure.py` 72、`flow_task_state.py` 227、`flow_submission_service.py` 210、`flow_ai_coordinator.py` 494、`task_continue_api.py` 550、`task_continue_finish.py` 296、`task_continue_support.py` 39、`task_continue_results.py` 104；`DiscoveryView.vue` 1199、`useDiscoveryParallelFlow.ts` 429、`ParallelPlatformProgress.vue` 142。所有 Python 文件低于 800 行、Vue 文件低于 1200 行，`pipeline_task.py` 低于 600 行预警线。

验证结果：`uv run python -m unittest discover -s tests` 运行 3355 项，功能测试通过；该次汇总保留两个门禁失败（修复前 `DiscoveryView.vue` 1224 行、卫生测试的 59 个未跟踪交付文件），修复后 `uv run python -m unittest tests.test_047_split_compat tests.test_048_task_continue_split_compat tests.test_b096_final_review tests.test_b096_quality_round4` 为 22/22。前端 `npm test` 为 67 个文件/1082 测试通过，`npm run build` 为 2296 modules 通过（仅既有 chunk warning）；`git diff --check` 通过。卫生测试 14 项中唯一失败为未跟踪交付文件，因本轮禁止提交而保留。真实 BOSS/智联账号 E2E、真实桌面/窄屏布局未执行。
