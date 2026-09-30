# B096 验证指南（实施阶段与最终门禁）

## 前置条件

先完成 [plan.md](plan.md) 的文件规模、扩边和浏览器并发安全门禁。测试库须由现有测试机制创建并标记 `env=test`；真实 E2E 使用项目正式启动方式、已就绪的 BOSS/智联登录态与真实输入，不替换成 mock/fixture。不得把真实数据或凭据写入仓库日志。

## 已执行聚焦验证（2026-09-26）

本轮使用临时 SQLite 测试库、内存任务替身和 Vitest/jsdom 数据，不读取真实账号、Cookie、Key 或本地用户数据。当前实际代码行数为 `webui/exec_search_api.py` 626 行、`webui/exec_search_resume.py` 401 行、`webui/src/views/DiscoveryView.vue` 1199 行，均低于门禁。

- `uv run python -m unittest tests.test_b096_flow_service tests.test_b096_flow_store tests.test_b096_flow_api tests.test_b096_flow_history tests.test_b096_parallel`：28/28 通过。
- `uv run python -m unittest tests.webui_store.test_store_migrations`：51/51 通过；迁移聚焦 3/3 通过。
- `uv run python -m unittest tests.test_b096_parallel tests.test_frozen_browser_identity tests.test_pipeline_exec_accounts tests.test_pipeline_pause_guard tests.test_screen_flow`：88/88 通过。
- `uv run python -m unittest tests.test_result_history tests.test_result_rounds`：25/25 通过。
- `uv run python -m unittest tests.test_cross_platform_dedupe`：33/33 通过；`webui/cross_platform_dedupe.py` 保持未修改。
- `uv run python -m unittest tests.webui_app.test_webui_app_platform`：69/69 通过。
- `npm test -- --run src/components/__tests__/OneClickScreenDialog.spec.ts src/composables/__tests__/useDiscoveryParallelFlow.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts src/views/__tests__/DiscoveryView.spec.ts`：4 个文件、130/130 通过（包含双 schema、七类面板、确认独立性、两线操作、Flow 提交请求和默认“全部”入口契约）。
- `npm test -- --run src/composables/__tests__/useDiscoveryResults.spec.ts src/composables/__tests__/resultHistory.spec.ts src/components/__tests__/ResultHistoryDrawer.spec.ts`：3 个文件、35/35 通过（当前 Flow 结果、Flow 归档参数、外层流程/内层平台历史和未完成 AI 筛选标记）。
- `npm test -- --run src/views/__tests__/DiscoveryView.spec.ts`：118/118 通过（包含默认“全部”与单平台切换回归）。
- `npm run build`：通过；仅保留既有 chunk size warning。
- `git diff --check`：通过。

聚焦测试已覆盖：Flow/Track 原子创建和画像隔离、双平台并发容量与同平台串行、冻结账号/profile/端口隔离、平台独立暂停/失败、无 AI 快照的部分岗位及“未完成 AI 筛选”标记、Flow 限定结果/归档/历史、BOSS/智联各自 schema 和简历建议草稿。B106 去重实现未修改。

## 最终门禁记录（2026-09-26）

- `uv run python -m unittest discover -s tests`：3321 项运行；功能测试未见失败，卫生门禁唯一剩余失败为 `test_repo_hygiene.RepoHygieneTests.test_no_untracked_non_ignored_files`，来自本轮新增源码、测试和 Spec 按授权未提交。该轮后原 pass-only 卫生回归 18/18 通过，B096 后端聚焦 42/42、直接平台/任务回归 150/150 通过。
- `cd webui && npm test`：67 个文件、1079/1079 通过；输出中的导航未实现提示为既有测试环境提示，不构成失败。
- `cd webui && npm run build`：通过，2296 个模块；保留既有 chunk 超过 500 kB 的构建警告。
- `uv run python -m unittest tests.test_repo_hygiene`：13/14 通过，唯一失败仍为新增未跟踪文件；`git diff --check` 通过，禁区路径差异为空，未发现项目根临时日志或凭据。
- `git status --short --branch`：分支为 `codex/feature/b096-parallel-platform-flow`；改动和新增文件均为 B096/047 范围内，未执行提交、推送或外部发布。

## 聚焦验证

运行 B096 新增的后端流程/迁移/API/并发测试和前端弹窗/运行线/结果历史测试，并跑直接受影响的既有单平台、暂停恢复、历史和去重回归。验证同一份简历分析分别投影两平台建议、双面板各有完整七类且修改互不覆盖；复现两种完成顺序、失败时有持久抓取岗位但无 AI 结果快照且岗位明确为未筛选、失败且零岗位、禁用任一平台、现有归档行为按 Flow 收紧、刷新与跨流程隔离。覆盖“单平台→全部”“全部→全部”“全部→单平台”，迁移后每个新单平台轮次必须有真实单轨 Flow。已完成平台的岗位整理动作需指向自身来源 run。同平台浏览器冲突须阻断；不同平台实际交错推进且账户/profile/端口不串用。

## 最终门禁

整条功能链收敛后，在仓库根运行一次 `uv run python -m unittest discover -s tests`；在 `webui/` 运行 `npm test`、`npm run build`；回到仓库根运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`。全量失败先保留失败清单，只对原失败和直接影响做聚焦返修；有相关改动并收敛后再做最终确认。

## 真实用户路径

1. 从应用入口打开新一轮，确认“全部”默认、两平台均可用；进入七类条件框的两个平台面板，分别确认各自完整七类条件和简历建议，一次启动后观察两个真实运行线。
2. 观察先完成抓取者立即进入自身 AI，先完成筛选者的结果在当前 04 页可见，页面不被强制跳转；另一线仍可运行、暂停或失败。
3. 操作单个平台暂停/继续/停止；检查另一平台仍推进，失败平台在 AI 快照尚未形成时也能显示本流程已存的部分岗位，并明确表示这些岗位尚未完成 AI 筛选；无岗位时仅显示失败状态，已完成平台的岗位仍能正确整理。
4. 刷新/重开，检查同一个流程恢复；先前单平台/全部流程结果不进入当前 04，现有归档操作只归档当前 Flow；历史只有一个外层方块和两个内层平台方块，零岗位失败平台也保留状态。再从已结束的“全部”开始单平台新一轮，确认它形成单轨 Flow 且不混入上一流程。
5. 在任一线运行/暂停时尝试新一轮，应被阻止；禁用平台门禁先由聚焦测试验证，真实 E2E 仅在环境已有相应禁用配置或另获配置写入授权时检查，不能直接改动外部系统。

用真实浏览器分别检查桌面和窄屏下的面板切换、进度布局、文本溢出、双滚动条、焦点和点击区域。逐项记录入口、输入、可见结果、后台可追踪 Flow/Track ID 和环境问题。若真实账号、平台或服务未就绪，明确列为未验证，不用跨层冒烟代替真实 E2E。

## 本轮未验证与范围边界

- 未执行真实 BOSS/智联 E2E：当前环境没有可授权使用的真实登录态/账号，不能把测试替身、临时 SQLite、内存执行器或 `tests/test_e2e_smoke.py` 计为真实 E2E。
- 单平台旧入口继续保留兼容路径；“全部”入口、当前 Flow 结果和历史 Flow 外层/平台内层已完成前端接线，旧历史轮次仍走兼容列表。
- 未进行真实桌面/窄屏布局检查；quickstart 中的浏览器步骤是待执行验收清单。T033/T035 保留为未验证；卫生测试的未跟踪文件失败保留为 T037 收口关注项。

## 第三轮复审返修验证（2026-09-27）

本轮先补失败测试，再做最小实现。Flow store 按职责拆成薄门面与 `store_flow_core.py`、`store_flow_claims.py`、`store_flow_preflight.py`、`store_flow_results.py`、`store_flow_legacy.py`、`store_flow_runs.py`；抓取/AI/续跑共用按平台 lane；无 run 的 paused Track 由原始 execute-search 重新校验并提交；finalizing pause 返回冲突且完成回调不覆盖持久 paused；Future、浏览器激活、任务注册、run 绑定和搜索 row 失败统一写安全错误码并收口 task/run/Track。迁移 039 增加无凭据 `submission_snapshot_json`。

实际行数门禁如下：`exec_search_api.py` 626、`exec_search_resume.py` 401、`ai_screen_api.py` 582、`runners/ai_screen_task.py` 570、`store_flow.py` 35、`store_flow_core.py` 178、`store_flow_claims.py` 632、`store_flow_preflight.py` 67、`store_flow_results.py` 183、`store_flow_legacy.py` 198、`store_flow_runs.py` 64、`store_flow_state.py` 242、`store_runs.py` 764、`flow_service.py` 561、`flow_api.py` 174、`flow_future.py` 128、`ai_screen_failure.py` 67、`flow_task_state.py` 192、`flow_submission_service.py` 195、`flow_ai_coordinator.py` 472、`task_continue_api.py` 550、`task_continue_finish.py` 296、`task_continue_support.py` 39、`task_continue_results.py` 104；`DiscoveryView.vue` 1199。硬红线 `<800`/`<1200` 全部满足；600/900 警戒文件不再追加业务逻辑。

返修聚焦命令与结果：

- `uv run python -m unittest tests.test_b096_round3_review`：12/12 通过，包含 Future 异常收口、无 run paused 恢复边界、finalizing pause、共享 lane、重复 preflight 与完整持久失败。
- `uv run python -m unittest tests.test_b096_round3_review tests.test_b096_round2_review tests.test_b096_production_flow tests.test_047_split_compat tests.test_b096_flow_store tests.test_b096_flow_service tests.test_b096_flow_api tests.test_b096_flow_history tests.test_b096_review_regressions`：59/59 通过。
- `uv run python -m unittest tests.webui_app.test_webui_app_platform tests.webui_app.test_webui_app_taskrun tests.webui_store.test_store_migrations.Migration38FlowSchemaTests`：155/155 通过，覆盖真实 Flask 路由、暂停/续跑兼容回归和迁移 039 schema。

测试数据为临时 SQLite、受控 Future/平台容量、内存任务替身和 Flask test client；这些是单元/集成/接口级证据，不是真实平台 E2E。当前环境无可授权的 BOSS/智联真实账号或登录态，T033 桌面/窄屏布局和 T035 正式入口双平台 E2E 继续标为未验证。

## 质量复审返修验证（2026-09-27）

- `task_continue_api.py` 已由 829 行提取至 550 行，结束保存路由位于 `task_continue_finish.py`（296 行）；路由注册、方法、响应字段及动态依赖替换由 `tests.test_048_task_continue_split_compat` 固定。
- recoverable browser/CDP activation 在统一收口路径中将 task、screening run、search run 与 Flow Track 一并持久为 `paused`；不可恢复异常一并持久为 `failed`；Flow API 直接面对 finalizing Track 时返回 409 且不改变 Track。
- B096 抓取提交与 AI 提交分别由 `flow_submission_service.py`、`flow_ai_coordinator.py` 协调，`flow_api.py` 的查询经 `FlowService`，新增模块均低于 800 行。
- `uv run python -m unittest tests.test_048_task_continue_split_compat tests.test_b096_quality_round4 tests.test_b096_production_flow tests.test_b096_round2_review tests.test_b096_round3_review`：30/30 通过。数据为临时 SQLite、受控执行器和 Flask test client；未执行真实账号或真实平台 E2E。

## 第五轮架构/原子性收敛验证（2026-09-27）

- Flow 抓取路由通过 `FlowSubmissionService.begin_whitebox()` 进入白箱提交；AI Flow 路径的恢复、run/task 更新与失败补偿通过 `FlowAiCoordinator`/`FlowTaskState`，legacy 路径保持独立兼容。
- `tests.test_048_task_continue_split_compat` 的真实 Flask test client 调用链使用 coordinator spy 验证 route→service/coordinator；`tests.test_b096_quality_round4` 的 SQLite 故障触发器验证 screening/search/Track 多实体状态写入在同一事务中回滚。
- `uv run python -m unittest tests.test_048_task_continue_split_compat tests.test_b096_quality_round4 tests.test_b096_production_flow tests.test_b096_round2_review tests.test_b096_round3_review`：33/33 通过。测试数据为临时 SQLite、受控服务和 Flask test client；真实账号、真实平台 E2E、真实桌面/窄屏布局仍未验证。

## 最终验证记录（2026-09-27）

- 当前有效行数：`exec_search_api.py` 637、`exec_search_resume.py` 401、`ai_screen_api.py` 551、`runners/ai_screen_task.py` 624、`runners/pipeline_task.py` 598、`pipeline_task_outcome.py` 240、`store_runs.py` 764、`store_flow.py` 35、`flow_service.py` 565、`flow_task_state.py` 227、`flow_submission_service.py` 210、`flow_ai_coordinator.py` 494、`task_continue_api.py` 550、`DiscoveryView.vue` 1199、`useDiscoveryParallelFlow.ts` 429、`ParallelPlatformProgress.vue` 142；硬红线满足，抓取 runner 低于 600 行预警线。
- `uv run python -m unittest discover -s tests`：3355 项运行；功能测试通过，汇总中保留修复前 Discovery 行数门禁和卫生门禁两项失败。结构修复后 `uv run python -m unittest tests.test_047_split_compat tests.test_048_task_continue_split_compat tests.test_b096_final_review tests.test_b096_quality_round4`：22/22 通过。
- `npm test`：67 个文件、1082/1082 通过；`npm run build`：2296 modules 构建通过，仅有既有 chunk warning；`git diff --check` 通过。
- `uv run python -m unittest tests.test_repo_hygiene`：14 项中唯一失败为 59 个按授权未提交的新增交付文件。未提交、不通过 `git add -f` 或其他方式规避卫生门禁。
- 真实 BOSS/智联账号 E2E、真实桌面/窄屏布局未执行；本轮只使用临时 SQLite、受控 Future、Flask test client 与 Vitest/jsdom，未将其作为真实 E2E 或布局证据。

## 当前 Flow 失败/空响应的默认入口回归（2026-09-27）

从 `start-one-click` 点击进入 `prepareDialog` 时，若 `/api/flows/current` reject 或返回 `{}`，页面保持默认“全部”并打开 BOSS/智联双平台确认弹窗；只有明确返回 `flow.selection` 为单平台时才恢复单平台。点击级回归 2/2、相关三文件 137/137、前端全量 67 文件/1084 测试通过，构建 2296 modules 通过。真实浏览器布局和真实平台 E2E 仍未验证。
