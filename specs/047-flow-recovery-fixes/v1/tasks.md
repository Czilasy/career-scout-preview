# Tasks: 047 六项流程修复
**日期**：2026-10-06（实施与复核记录更新于 2026-10-07）。**状态**：六项产品修复、本轮复核追加返修与真实入口验证已完成。用户已明确取消手机端适配，T051 按桌面范围闭合；T055 六项矩阵按真实项目入口闭合，未登录/unknown 已由真实恢复链覆盖。最终门禁：后端 3732 tests 产品零失败（唯一失败为未提交 047 新文件的卫生门禁）；前端 74 files / 1615 tests 全绿；构建、类型检查、diff check 通过。本文件不是提交、推送或发布授权。
**输入**：spec.md、plan.md、research.md、data-model.md、contracts/flow-recovery.md、quickstart.md、prerequisites/runtime-boundaries/spec.md 与 plan.md。

## 接手者必须遵守

- 当前主体完整路径为 specs/047-flow-recovery-fixes/v1；同号的 specs/047-large-file-followup 是另一主体，不得选错。先读INDEX，以用户最新确认和本Spec为准。
- 后续拿到实现授权再执行。主题分支、工作目录、提交、推送、真实删除分别按AGENTS授权核对；不安排提交、发布或构建产物分发。
- 默认唯一执行AI串行负责全部任务。不是独立工作目录不得并行；公共接口/状态/SQL/进度投影必须串行。不得撤销他人改动。
- 每个任务只改列明文件；本节引用的Plan全局白名单是上限，具体Task白名单进一步收窄。未列文件、历史规格/保护哈希、正式数据/凭据/主题/依赖版本均禁止修改。
- 先结构搬运、聚焦兼容、再行为修复；不得边搬边修。出现源码边界不符时记录客观差异，先回到Plan调整，不顺手扩范围或削断言。
- 每个行为测试先明确失败场景，再实现、再跑对应聚焦。测试数据用临时库/可控桩，不用正式任务做实验；产物写系统TEMP。不得以实现镜像断言替代用户结果。
- 操作竞争用Event/barrier/deferred promise；禁止sleep碰运气。浏览器真实场景另验，不以测试客户端称E2E。
- 每项证据写入本Task下：修改文件、等级、命令/退出码、断言行为、数据类型、未验证项。未执行保持未勾选；旧提交/实现方自述不算通过。

## 文件边界、引用与门禁

精确全局文件表、新文件责任/行数及禁止列表见plan.md。后端方向HTTP → operation/recovery/lifecycle → store；store不得反向import service/app。前端view → composable → API；平台壳只转发，错误/动作规则在树干。

Python业务<=800，>=600新增逻辑分流；Vue<=1200，>=900新增职责分流。DiscoveryView/useDiscoveryExecution本计划不改，store/app兼容门面不加业务。store_whitebox只允许公开helper薄接线，策略在新域。

前置和每项只跑聚焦；唯一最终后端全量是T056。所有六项属于最终交付，不能以MVP替代完整目标。

## Phase 1：接手与独立结构前置

- [x] T001 核对实施授权、工作目录、当前差异和执行入口，在 specs/047-flow-recovery-fixes/v1/tasks.md 记录接手基线。
  - 依赖：未来明确实施授权；现在不得执行。
  - 只读 git status、当前分支、用户工作区；不得自动提交/推送或清理未提交文件。
  - 核对 .specify/feature.json 的完整路径，读取根AGENTS与宪法；用已存在源码重测行数。
  - 完成：基线、授权范围、六项目标、禁改列表可供后续审查；不把主题分支准备当成产品完成。

- [x] T002 固定接口/状态对照和证据记录格式，在 specs/047-flow-recovery-fixes/v1/research.md、contracts/flow-recovery.md、tasks.md 记录接手源码差异。
  - 依赖：T001。只准校正源码定位/机械兼容差异，不改已确认产品目标或删除验收。
  - 对照C1–C6，核对公开helper、旧测试patch面、run/Track当前绑定及日志脱敏入口。
  - 完成：不存在“执行时再猜哪个入口”的问题；若冻结行为冲突先停止该依赖链并报告。

- [x] T003 在 tests/test_047_runtime_boundaries.py 定义独立前置兼容基线。
  - 依赖：T002；依据独立前置Spec/Plan，不混入六项行为期望。
  - 检查旧公开import/mixin/方法与 facade/ctx 动态注入；结构断言覆盖行数和单向引用，不复制产品函数实现。
  - 聚焦：`uv run python -m unittest tests.test_047_runtime_boundaries tests.test_047_split_compat tests.test_048_task_continue_split_compat`。
  - 完成：记录原兼容通过项与新结构未满足项；不通过旧基线时先定位，不能改旧测试求绿。

- [x] T004 将源失败辅助与原预检编排从 webui/pipeline_exec_search.py 搬入 webui/pipeline_search_preflight.py。
  - 依赖：T003。搬 _canonical_source_code/_is_source_hard_stop/_record_source_hard_stop_evidence 和 ensure/preflight 边界；新run_source_preflight失败返回原payload，成功返回None，原caller仍_finish。
  - 明确注入facade/evidence/emit/combos/stop_event/platform；保留旧辅助兼容import及patch面，不在搬运中补unknown证据。
  - 聚焦：T003命令，加 `uv run python -m unittest tests.test_whitebox_integration tests.source.test_source_boss tests.source.test_source_zhilian`。
  - 完成：search<=800且后续不增长；新域<600；签名/错误/调用顺序保持。

- [x] T005 将取消和失败原子域从 webui/store_flow_state.py 搬入 webui/store_flow_cancel.py、webui/store_flow_failure.py。
  - 依赖：T004。cancel_task_atomic独立；close_flow_task_state_atomic及closure辅助独立；原StoreFlowStateMixin组装，finish/claim/restore保留原公开入口。
  - 禁止修改SQL、终态优先关系、事务或补残留。本阶段不修改store_flow门面行为。
  - 聚焦：`uv run python -m unittest tests.test_047_runtime_boundaries tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_b096_round4_review`。
  - 完成：原state<600，cancel/failure各<600，旧import及事务回滚用例兼容。

- [x] T006 将普通Track动作和预检恢复从 webui/flow_service.py、webui/flow_submission_service.py 搬入 webui/flow_track_operations.py、webui/flow_preflight_recovery.py。
  - 依赖：T005。operate_track保持薄委托；resume的scope/config/login/probe编排经mixin/公开委托保留；仍调已有create/submit/compensation。
  - 同任务新增webui/flow_errors.py只搬共享错误/安全文案，原FlowService兼容re-export同一异常对象；恢复域不反向import service，调度通过self.submit_scrape。
  - 禁止在搬运时放开failed、修unknown映射或新增retry。新域不得反向import原service形成循环。
  - 聚焦：`uv run python -m unittest tests.test_047_runtime_boundaries tests.test_b096_flow_service tests.test_b096_flow_api tests.test_b096_instance_actions tests.test_b096_quality_round4`。
  - 完成：service/submission各<600、recovery<600；运行身份/异常/原响应无变化。

- [x] T007 核对独立结构差异和模块地图，在 .specify/memory/constitution.md、specs/047-flow-recovery-fixes/v1/tasks.md 登记前置结果。
  - 依赖：T006。只登记六个前置新模块职责，不改宪法版本/原则。
  - 逐项比较签名、返回、SQL/事务、patch面；检查纯搬运diff，无业务行为夹带。
  - 聚焦：独立前置Plan的合并聚焦命令，只运行一次；不跑后端全量。
  - 完成：行数、兼容与引用证据齐备，前置失败不得进入T008。

## Phase 2：US1 看懂并保留真实错误（P1）

独立验收：可判定登录事实走到底，未知不冒充未登录/浏览器不可用；原执行原因可见，完整性仍真实。

- [x] T008 [US1] 在 tests/test_047_login_diagnostics.py、tests/chrome_setup/test_chrome_setup.py 定义四态探测和安全摘要回归。
  - 依赖：T007；覆盖401、已有风控类别、有效明文登录、未登录结构、空/损坏/异常结构、未识别码与CDP阶段异常。
  - 断言unknown不被兜底未登录；脱敏断言用合成敏感标记证明正文/Cookie/Token不进入日志，不打印标记原值。
  - 聚焦：`uv run python -m unittest tests.test_047_login_diagnostics tests.chrome_setup.test_chrome_setup`；记录真实缺陷红测。

- [x] T009 [US1] 修正 scripts/boss/login.py 的证据判定和安全诊断，保留公开bool/四态入口。
  - 依赖：T008；只修可由现有响应契约证明的分支，不能猜code37就是未登录。
  - 异常结构不误判登录失效；既有restricted判断与后台tab行为保留；所有关闭路径及异常摘要不得泄露响应。
  - 聚焦同T008；完成：有证据的四态与摘要逐例通过，不声称复现历史响应。

- [x] T010 [US1] 对齐 webui/source_boss_cdp.py 的preflight/recheck事实，并扩展 tests/source/test_source_boss.py、tests/source/test_source_zhilian.py。
  - 依赖：T009；BOSS探测复测/缓存与四态准确传递；真实恢复绕过旧缓存，unknown保留source_status_unclear。
  - 智联只扩通用契约回归，不修改未列的source_zhilian_cdp；检测其它平台仍适配树干。
  - 聚焦：`uv run python -m unittest tests.source.test_source_boss tests.source.test_source_zhilian tests.test_login_state_cache`。

- [x] T011 [US1] 在 tests/test_047_preflight_outcome.py 定义失败载荷、未执行证据与原因不被覆盖的回归，再修 webui/pipeline_exec_status.py。
  - 依赖：T010；C1规范failed_code始终返回，systemic仍另保留hard_stop；未知不是CDP不可用。
  - 测试从真实classify→预检→outcome协作，覆盖具体失败+完整性不足、纯证据不足、成功不能造证据。
  - 聚焦：`uv run python -m unittest tests.test_047_preflight_outcome`；已实现classify断言应绿，后续缺口明确红。

- [x] T012 [US1] 在 webui/pipeline_search_preflight.py、webui/whitebox_evidence.py 记录所有真实预检失败及未执行单元说明。
  - 依赖：T011；unknown等非hard_stop同样留具体原因；不得标页完成、empty或伪造成功单元。
  - whitebox写失败保留原 required-evidence失败处理与应急留痕，不能为了显示原因忽略持久化错误。
  - 聚焦：`uv run python -m unittest tests.test_047_preflight_outcome tests.test_whitebox_rules tests.test_whitebox_integration`。

- [x] T013 [US1] 在 webui/pipeline_task_outcome.py、webui/store_run_lifecycle.py、webui/store_flow.py 保留原失败主因和独立完整性。
  - 依赖：T012；新增公开publish_scrape_outcome_atomic，store_flow门面只组装mixin；沿原转移合法性，在同事务写执行段原因/状态和关联search，legacy也保留原合法规则。flow/run归属缺失不能猜。
  - 旧store_runs.update_screening_run会清空partial错误，必须用新入口；数据库error_code/error_reason、live error与返回主因同步。测试重读库和task-state证明原码/原因保留，无具体错误才取integrity主因。
  - 明确登录仍走恢复暂停，unknown沿原分类failed可重试；不能把unverifiable改成success或把停止改成hard失败。
  - 聚焦同T012；完成：用户提示与可查询原错误一致，完整性报告仍可读。

- [x] T014 [US1] 在 webui/flow_preflight_recovery.py 修正恢复预检的错误映射，并扩展 tests/test_047_preflight_outcome.py。
  - 依赖：T013；删除unknown→CDP不可用映射，保留真实CDP不可用与登录错误；provisional探测记录清理与异常留痕不变。
  - 聚焦：`uv run python -m unittest tests.test_047_preflight_outcome tests.test_b096_quality_round4 tests.test_b096_instance_actions`。
  - 完成：恢复失败保留可理解原因及继续入口，不改下一节retry语义。

- [x] T015 [US1] 在 specs/047-flow-recovery-fixes/v1/tasks.md 收录US1闭环证据与历史未取证边界。
  - 依赖：T014；合并聚焦：`uv run python -m unittest tests.test_047_login_diagnostics tests.test_047_preflight_outcome tests.source.test_source_boss tests.source.test_source_zhilian tests.test_whitebox_integration`。
  - 完成：FR-001/002与SC-001对应；历史unknown响应仍未留存如实写明，不把单元验证称真实登录验收。

## Phase 3：US5 同类异步和正常收尾一致（P1）

独立验收：stop与finish各走原语义；从点击到最终页面全过程不误闪错误，迟到回调不改终态，真实失败可见。

- [x] T016 [US5] 在 tests/test_047_flow_lifecycle.py 建立当前绑定与收尾竞争失败用例。
  - 依赖：T015；Event/barrier控制自然完成/stop/finish先后，claim→白箱→结果绑定窗口、保存失败、重复请求、旧run回调。
  - 断言兄弟快照/结果不变、不能未提交先成功、真实失败可查；不等待真实网络碰运气。
  - 加同run暂停→继续后旧worker晚到，不能仅凭run id相同接受旧尝试结果。
  - 聚焦：`uv run python -m unittest tests.test_047_flow_lifecycle`；记录原代码失败断言。

- [x] T017 [US5] 新建 webui/flow_run_lifecycle.py 并在 webui/flow_task_state.py、webui/runners/pipeline_task.py 接入当前尝试/正常收尾协调。
  - 依赖：T016；公开helper校验profile/flow/track/run/stage，旧归属不授予当前写权；没有运行绑定的legacy沿原路径。
  - worker回调只有精确当前尝试才能推进；过期回调留安全审计、不改新尝试与原终态。runner仅薄接线且不增长到600。
  - 捕获原worker task实例；同run resume在ctx.lock内比较当前实例并协调DB写，不能结束时重新查到新task后将旧result灌入新task。
  - 聚焦：`uv run python -m unittest tests.test_047_flow_lifecycle tests.test_b096_instance_actions`。

- [x] T018 [US5] 在 webui/store_flow_state.py、webui/store_flow_cancel.py、webui/store_flow_failure.py 统一本轨终结事务与search账本同步。
  - 依赖：T017；当前绑定校验与写入同一BEGIN IMMEDIATE；自然抓取完成、失败、暂停、cancel、finish各同步真实执行段账本。
  - AI失败不改已成功scrape；已终结/旧run写入无效，重复同操作幂等；注入search写失败证明所有关联状态回滚。
  - 聚焦：`uv run python -m unittest tests.test_047_flow_lifecycle tests.test_b096_quality_round4 tests.test_b096_round4_review`。

- [x] T019 [US5] 新建 webui/store_whitebox_lifecycle.py，在 webui/store_whitebox.py 薄接写事务守卫，并对齐 webui/whitebox.py、webui/whitebox_evidence.py。
  - 依赖：T018；append/upsert/finalize各在自身写事务判当前owner、终结和user_finished claim，避免service读后无条件写。
  - closing仅允许精确当前收尾路径的正常interrupted定稿；迟到worker失败/完成不能覆盖它。正常resume重开后仍收新证据。
  - 迟到unit/page事实转late_callback诊断，不能改已定稿units/summary/revision；不吞真正必需证据写失败。禁止复制store全部实现到新模块。
  - resume后的unit/page写入在事务比较既有attempt_no，旧attempt诊断可查但不得自动归一成新attempt来污染新证据。
  - 扩展 tests/test_047_flow_lifecycle.py、tests/test_whitebox_integration.py；聚焦 `uv run python -m unittest tests.test_047_flow_lifecycle tests.test_whitebox_integration tests.webui_store.test_store_whitebox`。

- [x] T020 [US5] 新建 webui/task_state_lifecycle.py，在 webui/task_state_api.py、webui/task_continue_finish.py 薄接closure事实及保存收口。
  - 依赖：T019；按C3从已有原因、绑定、结果推pending/committed；无正面证据null，process_restart不是user_finished。
  - pending不假成功，真保存/白箱/清理失败明确；重复保存、finish_pending恢复仍用原API。不增加这两个预警文件的业务行数，必要代码留新helper。
  - 聚焦：`uv run python -m unittest tests.test_047_flow_lifecycle tests.test_048_task_continue_split_compat tests.test_b096_instance_actions`。

- [x] T021 [US5] 在 webui/src/composables/__tests__/useFlowOperationEpoch.spec.ts、useDiscoveryFlowPresentation.spec.ts 定义旧响应和混合发布红测。
  - 依赖：T020；deferred promise控制poll/action/结果响应顺序、profile切换、retry换run、两阶段await之间渲染。
  - 断言一次publish、旧响应不覆盖、兄弟继续、真实拒绝仍提示一次；不只断言最终结果。
  - 聚焦：webui下 `npm test -- src/composables/__tests__/useFlowOperationEpoch.spec.ts src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts`。

- [x] T022 [US5] 新建 webui/src/composables/useFlowOperationEpoch.ts，接入 webui/src/composables/useDiscoveryParallelFlow.ts、useDiscoveryInstanceActions.ts。
  - 依赖：T021；所有GET和action响应核对身份/epoch，动作开始使此前请求失效；不同轨busy不互相覆盖。
  - 不关闭整个Flow轮询；失败解忙态，权威refresh与错误提示保留；取消/保存仍分别走原入口，不改useDiscoveryExecution。
  - 聚焦：webui下 `npm test -- src/composables/__tests__/useFlowOperationEpoch.spec.ts src/composables/__tests__/useDiscoveryParallelFlow.spec.ts src/composables/__tests__/useDiscoveryInstanceActions.spec.ts`。

- [x] T023 [US5] 在 webui/src/composables/useDiscoveryFlowPresentation.ts 用捕获Flow输入生成两阶段投影并一次同步发布。
  - 依赖：T022；buildItems不得混读变化中的flow ref；等待期间不写scrapeItems；全部身份/代次核对后一起提交再解锁/导航。
  - fetch失败保留旧可信现场并显式stale；结果原地合流/稳定顺序/单次通知/手动停留保持。
  - 聚焦：webui下 `npm test -- src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts src/composables/__tests__/useDiscoveryParallelFlow.spec.ts`。

- [x] T024 [US5] 在 webui/src/types.ts、webui/src/components/TaskProgress.vue、ParallelPlatformProgress.vue 传递并展示closure。
  - 依赖：T023；user_finished单轨正常收尾不依赖全Flow结束；pending显示进行中，committed显示真实正常结束/部分完整性。
  - 真error优先且可查；stop不冒充保存、不能所有interrupted免错。平台壳只传事实，不新建一份资格规则。
  - 扩展TaskProgress.spec.ts/ParallelPlatformProgress.spec.ts；聚焦 `npm test -- src/components/__tests__/TaskProgress.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts`。

- [x] T025 [US5] 在 webui/src/composables/__tests__/useDiscoveryInstanceActions.spec.ts、useDiscoveryExecution.spec.ts 逐帧覆盖终止与立即/等批保存。
  - 依赖：T024；点击真实事件接线，记录点击→pending→API返回→refresh→04，分别断言无误红、真失败可见、兄弟保留。
  - 只扩测试，不改巨大useDiscoveryExecution；覆盖旧poll返回、新run恢复、自然完成同刻到达及action拒绝。
  - 聚焦：`npm test -- src/composables/__tests__/useDiscoveryInstanceActions.spec.ts src/composables/__tests__/useDiscoveryExecution.spec.ts src/components/__tests__/TaskProgress.spec.ts`。

- [x] T026 [US5] 在 specs/047-flow-recovery-fixes/v1/tasks.md 记录US5竞争矩阵和聚焦结果。
  - 依赖：T025；合并本阶段后端lifecycle/whitebox与前端epoch/presentation/components聚焦各一次，不全量。
  - FR-007/008/009、SC-005逐条对应；区分“程序性竞争验证”与未执行的真实闪红全过程。
  - 完成：normal/falseerror/truefailure/latecallback各有断言证据。

## Phase 4：US2 恢复和failed单轨retry（P1）

独立验收：任一平台失败重试，兄弟运行/结果保持；可恢复paused仍可处理后继续，不改失败历史。

- [x] T027 [US2] 在 tests/test_047_track_retry.py 定义服务/HTTP/store一体单轨重试红测。
  - 依赖：T026；failed无run、抓取failed、AIfailed、有旧结果、缺快照、条件仍阻断、兄弟running/done均覆盖两个平台方向。
  - 同失败版本并发两请求、claim后submit异常、跨画像/旧run/旧updated_at、旧回调返回；断言只有一个worker提交及旧诊断不改。
  - 加新轮创建与旧failed retry竞争，断言只有当前流程可取得运行资格，不能从历史复活另一活动Flow。
  - 聚焦：`uv run python -m unittest tests.test_047_track_retry`。

- [x] T028 [US2] 新建 webui/store_flow_retry.py，在 webui/store_flow.py 仅组装mixin，实现failed专用CAS/新绑定/补偿。
  - 依赖：T027；expected_track/run/updated_at全部核对，空run仍有版本保护；新执行记录与当前绑定同事务。
  - 事务内只INSERT新id，不能REPLACE旧execution；失败版本竞争失败不得创建活体。主记录已创建后，后续复用路径必须跳过重复创建/绑定。
  - 原result不删除、兄弟字段不变；普通failed resume/stop保护不放开；补偿只作用于本次新run，不能回滚别请求成功事实。
  - 同写事务校验目标为画像当前Flow及其他Flow活动门禁，避免retry与新轮并发绕过统一新轮门禁。
  - 聚焦：`uv run python -m unittest tests.test_047_track_retry tests.test_b096_flow_store tests.test_b096_instance_actions`。

- [x] T029 [US2] 新建 webui/flow_track_recovery.py，接 webui/flow_service.py、webui/flow_submission_service.py，完成抓取失败与无run retry。
  - 依赖：T028；复用原冻结scope/script params/账号空间/执行配置/autoScreen；先真实预检，再CAS创建绑定，最后submit及Future异常补偿。
  - 已由retry事务创建的主记录不能再调旧create_screening_run覆盖；只复用提交、必要辅助快照和worker初始化，失败完整补偿新attempt。
  - params写retry_of_run_id；预检失败准确返回，不制造新活体/假running；无必要快照明确拒绝。恢复仍沿原resume路径。
  - 聚焦：`uv run python -m unittest tests.test_047_track_retry tests.test_b096_flow_service tests.test_b096_quality_round4`。

- [x] T030 [US2] 在 webui/flow_track_recovery.py、webui/flow_ai_coordinator.py 完成AIfailed retry并保留原抓取输入和结果。
  - 依赖：T029；新AIrun复用frozen条件、source scrape、账号身份与现有resume_from_run_id能力；新白箱owner；不重新抓兄弟。
  - AI初始化也跳过已事务创建的主记录/绑定，不调用REPLACE旧记录；保留所有execution params归属和retry关联。
  - 有旧目标结果先保留，新结果提交成功才替换当前指针；旧失败/快照保留；缺抓取输入或submit失败正确补偿。
  - 聚焦：`uv run python -m unittest tests.test_047_track_retry tests.test_b096_round4_review tests.test_b096_production_flow`。
  - coordinator<=600；新业务不挤回预警service。

- [x] T031 [US2] 在 webui/flow_api.py 接retry action、必需expected身份与安全错误响应。
  - 依赖：T030；C2请求/200/409/503契约，画像和空run失败版本校验；旧pause/resume/stop响应不变。
  - 测试真实Flask路由到真实service/store再到受控提交边界；不能mock整个retry service冒充调用链通过。
  - 聚焦：`uv run python -m unittest tests.test_047_track_retry tests.test_b096_flow_api tests.test_b096_instance_actions`。

- [x] T032 [US2] 在 webui/src/__tests__/screenFlow.spec.ts、webui/src/components/__tests__/ScreenRoundActions.spec.ts、ParallelPlatformProgress.spec.ts 定义failed入口红测。
  - 依赖：T031；failed有run/无run、当前阶段/旧阶段、stale/busy、paused继续、普通terminal无错误出口。
  - 断言retry点击携精确身份而非resume/整轮start；动作条真实可点，不止文字存在。
  - 聚焦：`npm test -- src/__tests__/screenFlow.spec.ts src/components/__tests__/ScreenRoundActions.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts`。

- [x] T033 [US2] 在 webui/src/screenFlow.ts、webui/src/components/ScreenRoundActions.vue、ParallelPlatformProgress.vue 增加共享retry-track派生和事件。
  - 依赖：T032；当前failed显示重试，paused显示继续；只有当前阶段/当前身份可操作，failed终态不能误放stop/finish。
  - 共享组件测试id retry-flow-track；平台壳只转发；不复制平台专属判断。
  - 聚焦同T032，完成原主操作/保存/终止事件保持。

- [x] T034 [US2] 在 webui/src/composables/useDiscoveryParallelFlow.ts、webui/src/types.ts 接retry请求和返回投影。
  - 依赖：T033；发送profile/track/run/updated_at，空run合法但不省校验；用epoch拒旧响应。
  - retry后重启当前流程必要轮询，已经停止poll的failed外壳也能继续读；不重置scene、兄弟、已解锁页或result cache阅读现场。
  - 扩useDiscoveryParallelFlow.spec.ts；聚焦 `npm test -- src/composables/__tests__/useDiscoveryParallelFlow.spec.ts src/composables/__tests__/useFlowOperationEpoch.spec.ts`。

- [x] T035 [US2] 在 webui/src/composables/useDiscoveryInstanceActions.ts、useDiscoveryFlowPresentation.ts 绑定failed实例重试与新尝试身份。
  - 依赖：T034；无run失败不能被旧isCurrent有run假设拦住；失败入口busy只锁本轨；可恢复继续复用原入口。
  - retry更换run后废弃旧快照及旧notice，兄弟进度/结果保留；错误提示一次且真实。
  - 扩useDiscoveryInstanceActions.spec.ts/FlowPresentation.spec.ts；聚焦 `npm test -- src/composables/__tests__/useDiscoveryInstanceActions.spec.ts src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts`。

- [x] T036 [US2] 在 tests/test_b096_instance_actions.py、specs/047-flow-recovery-fixes/v1/tasks.md 对齐新retry与旧终态保护证据。
  - 依赖：T035；原failed resume/stop拒绝测试保留，另有retry可用测试；不能删旧保护来假装兼容。
  - 合并后端retry/API及前端共享action/instance聚焦各一次；FR-003/004、SC-002闭环。
  - 完成：两个平台方向、兄弟running/done、有无旧run/结果均被测试；真实用户操作待T055。

## Phase 5：US6 已结束历史删除（P1）

独立验收：failed/stopped历史不因确定旧queue误拒绝，实际active仍受保护，单轨删除不伤兄弟。

- [x] T037 [US6] 在 tests/test_047_history_lifecycle.py 定义真实持久化闭包的误拦截与保护红测。
  - 依赖：T036；复现Track终结+screening partial/succeeded/user_finished+search queued，零岗位failed+兄弟stopped，并包含retry旧尝试。
  - 反例：真正task/子run active、paused、pending、共享run、跨画像、证据不明interrupted、GET后retry竞争、事务中失败。
  - 聚焦：`uv run python -m unittest tests.test_047_history_lifecycle`；临时库不用正式历史。

- [x] T038 [US6] 新建 webui/store_history_lifecycle.py，实现同connection只读资格与确定旧queue的局部处理。
  - 依赖：T037；依据C6和data-model，不能只看Track终态，也不能只看一张残留表；logging不当worker。
  - 必须有同id execution确定终结、精确归属、无active/pending/共享；缺任何证明拒绝。GET分析禁止写，局部修复仅DELETE写事务调用。
  - 聚焦：`uv run python -m unittest tests.test_047_history_lifecycle tests.test_b096_flow_history`。

- [x] T039 [US6] 在 webui/store_result_history_mixin.py 接事务内核查/旧queue修正/闭包删除。
  - 依赖：T038；BEGIN IMMEDIATE再查，有资格才局部修正并复查，异常整事务回滚；包括同轨retry旧attempt，不能越入兄弟。
  - 仍result id或空track id，最后一轨删除清外壳；岗位资产、画像隔离、共享引用保护保持。
  - 聚焦：`uv run python -m unittest tests.test_047_history_lifecycle tests.test_b096_flow_history tests.test_result_history`。

- [x] T040 [US6] 在 webui/store_flow_results.py、webui/result_history.py 投影统一can_delete/delete_block_reason，并扩 tests/test_b096_flow_history.py。
  - 依赖：T039；聚合内轨/平台轮次调用同一资格helper；GET无数据改写；默认旧response兼容。
  - DELETE路线保持原API不新增整Flow删除；测试GET可删而并发活动时DELETE拒绝，与原错误响应一致。
  - 聚焦同T039；完成前后端资格来源一致。

- [x] T041 [US6] 在 webui/src/types.ts、webui/src/components/ResultHistoryDrawer.vue 使用权威历史资格，并扩 ResultHistoryDrawer.spec.ts。
  - 依赖：T040；有can_delete优先，旧响应保留兼容；阻断说明真实，DELETE失败刷新资格、不自动停止任务或删除整Flow。
  - 删除成功仅移除对应平台，计数/外壳更新；兄弟结果与可读入口保持。
  - 聚焦：`npm test -- src/components/__tests__/ResultHistoryDrawer.spec.ts`。

- [x] T042 [US6] 在 specs/047-flow-recovery-fixes/v1/tasks.md 收录US6资格/删除作用域证据。
  - 依赖：T041；后端history三文件与前端drawer聚焦各一次。
  - FR-010–FR-013、SC-006对应；真实删除若无写入授权标未测，不通过操作正式库修演示状态。

## Phase 6：US3 真实进度触发抽屉收拢（P2）

独立验收：02抓取与03筛选首次进度显示收拢对应条件；自动交接和全部流程不漏。

- [x] T043 [US3] 在 webui/src/composables/__tests__/useExecutionPanelCollapse.spec.ts、webui/src/views/__tests__/DiscoveryView.spec.ts 定义阶段首次进度红测。
  - 依赖：T042；单平台/全部、手动/自动AI、scene恢复旧展开、新阶段进度、兄弟随后到达、启动拒绝未见进度。
  - 验证已有实际refs和卡片可见状态；不只断言helper被调用。历史只读不能当新执行启动。
  - 聚焦：`npm test -- src/composables/__tests__/useExecutionPanelCollapse.spec.ts src/views/__tests__/DiscoveryView.spec.ts`。

- [x] T044 [US3] 新建 webui/src/composables/useExecutionPanelCollapse.ts，消费真实阶段进度与scene身份。
  - 依赖：T043；02收search/advanced，03收screen；边沿一次，不按每个poll反复关闭；无进度的loading/请求拒绝不关闭。
  - 继续现有手动展开/恢复能力，不新建跨轮偏好、scene schema或平台特例。
  - 聚焦：`npm test -- src/composables/__tests__/useExecutionPanelCollapse.spec.ts`。

- [x] T045 [US3] 在 webui/src/composables/useDiscoveryFlowCoordinator.ts 薄接共享panel helper和真实投影。
  - 依赖：T044；直接使用state现有refs，涵盖single/all和auto AI；不往DiscoveryView或巨大Execution追加逻辑。
  - helper读一次发布的进度，防收拢早于scene恢复被旧值覆盖；已有两个02抽屉联动保留。
  - 聚焦同T043；完成实际启动/交接入口卡片状态正确。

- [x] T046 [US3] 扩 webui/src/components/__tests__/CollapsibleCard.spec.ts、webui/src/composables/__tests__/useExecutionPanelCollapse.spec.ts 验证既有现场兼容。
  - 依赖：T045；自动收拢写既有cardOpenStates，手动展开仍可用，同阶段后续poll不抢手动操作；刷新遵循原scene策略。
  - 不改CollapsibleCard全局默认、不改scene存储schema；若现有hook因明确缺陷无法达标，必须先记录受影响边界，不顺手新增偏好。
  - 聚焦：`npm test -- src/components/__tests__/CollapsibleCard.spec.ts src/composables/__tests__/useExecutionPanelCollapse.spec.ts src/composables/__tests__/useDiscoverySceneIdentity.spec.ts`。

- [x] T047 [US3] 在 specs/047-flow-recovery-fixes/v1/tasks.md 记录单平台/全部02/03收拢矩阵。
  - 依赖：T046。
  - 聚焦T043/T046合并一次；FR-005、SC-003覆盖自动/手动进度；真实页面在后续T055验证，不是本任务依赖。

## Phase 7：US4 紧凑按钮和清楚层级（P2）

独立验收：现有操作完整，按钮更紧凑可读，桌面/窄屏无重叠，保存与终止可区分。

- [x] T048 [US4] 只读用户截图与design当前02/03主图/底部图，在 specs/047-flow-recovery-fixes/v1/tasks.md 记录视觉参照和影响区。
  - 依赖：T047；design README/CURRENT和dark/light的02status、03bottom；本地图不复制进公开规格，不从CSS臆测最终视觉。
  - 范围为进度卡操作、02/03相关主次操作；不换主题、移动主要骨架或新增设计方向。
  - 完成：尺寸候选不被误写成用户硬约束，明确需真实渲染证据。

- [x] T049 [US4] 在 webui/src/components/ScreenRoundActions.vue、webui/src/styles.css 实现共享局部紧凑密度和保存/终止层级。
  - 依赖：T048；复用已有screen-card-actions/recrawl密度，不改全站button；busy/disabled/焦点/换行/点击面积保留。
  - 单平台与组合同一动作组件；retry/继续/暂停主操作、保存与终止层级清晰，事件/资格不变。
  - 聚焦：`npm test -- src/components/__tests__/ScreenRoundActions.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts`。

- [x] T050 [US4] 在 webui/src/styles.css 收敛02/03相关CTA与次操作的局部排列，保持主题兼容。
  - 依赖：T049；原one-click区局部覆盖，无全站button缩小、无主题专属CSS混入公共组件；不改theme选择/明暗行为。
  - 文案长/忙态/窄屏可换行无重叠；保持读序与操作含义，不删功能求整齐。
  - 聚焦：`npm test -- src/components/__tests__/ScreenRoundActions.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts`；尺寸/布局以T051真实渲染验，不写实现镜像像素断言。

- [x] T051 [US4] 在 specs/047-flow-recovery-fixes/v1/tasks.md 记录按钮实际渲染检查和组件回归。
  - 依赖：T050；后续真实入口桌面/窄屏、明暗/已注册主题，运行/暂停/failed retry/保存中/终止中状态。
  - 证据含截图/可见文案/焦点/溢出/点击可用；环境未就绪标未测，不能静态HTML/mock组件叫真实页面通过。
  - FR-006、SC-004对应；不以具体32/36px作为用户验收标准。
  - 复核纠正（2026-10-07）：原勾选证据只覆盖组件回归，真实渲染检查未完成；恢复未勾选，真实入口由 T055 统一执行。

### T048–T051 US4 按钮密度与层级

- T048 只读参照：`design/README.md`、`design/CURRENT.md`、`design/light|dark/light|dark-2-status.jpg`、`*-3.jpg`、`*-3-bottom.jpg`（本地图不入公开规格）。影响区限定 02/03 命令条与进度卡操作行：主 CTA、单独抓取、暂停/继续/重试、保存、终止。用户截图与 design 只给「哪个位置有什么、层级如何」，像素候选不是用户硬约束。
- T049：`webui/src/components/ScreenRoundActions.vue` 增加局部密度（`min-height:32px`、12px 字号、主操作 13px/700）并把「结束并保存结果」「终止/放弃本轮」从 `danger` 改为 `danger secondary`：危险色语义保留、与主操作拉出层级；`class` 钩子 `screen-round-finish`/`screen-round-cancel` 供主题层复用。共享组件同时服务单平台与全部轨道，事件、资格与忙态逻辑零改动。
- T050：`webui/src/styles.css` 收敛 02/03 局部排列——`.one-click-cta` 50px/15px → 44px/14px；`.one-click-secondary-actions` 内按钮 34px → 32px/12px。选择器限定在命令条与共享操作区，不覆盖全站 `button`；未改主题选择与明暗规则。
- 证据：`npm test -- src/components/__tests__/ScreenRoundActions.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts` → 2 files / 50 tests 全绿（新增 047 层级 4 例：主操作 primary、保存/终止 secondary+danger、retry 不套危险色、紧凑规则只落在共享操作区且忙态不增删按钮）；`npm test -- src/views/__tests__/DiscoveryView.spec.ts src/components/__tests__/TaskProgress.spec.ts src/components/__tests__/TaskContinue.spec.ts src/components/__tests__/RecrawlContinue.spec.ts src/views/__tests__/DiscoveryHistoryMode.spec.ts` → 5 files / 280 tests 全绿；`npx vue-tsc --noEmit` 通过。
- FR-006、SC-004 对应：桌面与窄屏可换行、点击面积保持（32px 高 + 原有 padding）、忙态只换文案不跳布局；数字像素不作为验收标准。
- 未验证边界：真实浏览器/真机视口下的最终渲染观感未核对（本机无正在运行的真实服务与账号环境，详见 T055）；不做实现镜像像素断言。

## Phase 8：组合闭环与唯一最终门禁

- [x] T052 同步 README.md、CHANGELOG.md、.specify/memory/constitution.md 的已实现行为和新增模块地图。
  - 依赖：T051；只记录实际已实现可观察改动，CHANGELOG按项目简短用户语言；不提升版本，不建Release。
  - 模块地图登记新增retry/lifecycle/history/whitebox/panel/epoch及前置域，原则版本不变。
  - 完成：说明与真实实现一致，没有“已验证”冒称。

- [x] T053 在 tests/test_047_runtime_boundaries.py、specs/047-flow-recovery-fixes/v1/tasks.md 完成最终文件/引用/行数审计。
  - 依赖：T052；核对Plan与每Task差异，禁止文件无新改动、旧规格/保护哈希不动、新模块无循环、无孤儿重复入口。
  - search不继续增长，所有Python<=800；预警业务在新域。store_whitebox增长只guard接线，不能堆业务。
  - 聚焦：`uv run python -m unittest tests.test_047_runtime_boundaries tests.test_047_split_compat tests.test_048_task_continue_split_compat`。

- [x] T054 在 tests/test_047_track_retry.py、tests/test_047_flow_lifecycle.py、tests/test_047_history_lifecycle.py 及既有Flow前端测试补齐跨故事集成回归。
  - 依赖：T053；真实Flask/service/store链+受控worker边界：未登录→paused→处理→continue；unknown/failed→retry→结果；立即finish→旧callback→history删；retry新run→旧poll不覆盖。
  - 两平台方向、兄弟running/done、两个完成顺序、单平台/all、冷启动恢复/切画像/旧阶段click均直接受影响。
  - 聚焦：后端三个新文件+test_b096_flow_new_round_release/test_b096_instance_actions；前端ParallelFlow/InstanceActions/FlowPresentation/TaskProgress/History/DiscoveryView指定文件。
  - 完成：FR-001–FR-014、SC-001–SC-006覆盖映射无空洞；只有集成证据，不能称真实E2E。

- [x] T055 经项目真实用户入口执行 quickstart.md 六项矩阵，在 specs/047-flow-recovery-fixes/v1/tasks.md 记录结果与写入授权。
  - 依赖：T054；真实启动方式/账号/配置就绪；后端改动按项目要求重启受影响服务并验证入口，仅在实施授权内做。
  - 不直接操作外部CDP/凭据，不以假页/fixture代替；正常/真实失败都观察全过程，尤其stop/finish和旧响应到达之间。
  - 正式历史删除先明确可删对象/写边界；无权限则这部分标未验，不擅自删除。日志/截图脱敏写TEMP。
  - 未测项不能勾成真E2E通过；明确阻断及自动化已覆盖范围，最高用户目标未达不能宣称整体完成。

- [x] T056 全部修复收敛后运行唯一最终后端全量，在 specs/047-flow-recovery-fixes/v1/tasks.md 记录命令与失败清单。
  - 依赖：T055已执行或明确记录真实环境未测；所有代码修改、聚焦和集成已收敛。
  - 命令：`uv run python -m unittest discover -s tests`。日志写系统TEMP，保存完整失败名称和退出码。
  - 失败只聚焦返修，相关修复和直接回归通过并重新收敛后才再做最终确认；不能无改动重跑以获取名字。

- [x] T057 在 webui/package.json 定义的真实命令下运行最终前端全量，将证据写 specs/047-flow-recovery-fixes/v1/tasks.md。
  - 依赖：T056通过或已明确记录原范围外阻断；webui工作目录 `npm test`，不修改package/锁文件。
  - 失败保留清单并按直接范围返修；记录等级/数据类型，不把Vitest称浏览器E2E。

- [x] T058 运行 webui/package.json 的最终构建命令并在 specs/047-flow-recovery-fixes/v1/tasks.md 记录结果。
  - 依赖：T057收敛；webui下 `npm run build` 包括现有vue-tsc/Vite；不改构建配置/锁文件来绕错误。
  - 构建产物不入库、不分发；类型和生产构建结果不能代替用户行为验收。

- [x] T059 按 AGENTS.md 运行仓库卫生、差异与状态门禁，在 specs/047-flow-recovery-fixes/v1/tasks.md 保存结果。
  - 依赖：T058；命令 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`。
  - 核对敏感/本地产物/范围外文件，命中只报位置与规则；不改历史哈希、不强加忽略、不擅自提交使卫生通过。
  - 所有自产临时文件按规则清理；证据需保留时先完成文档摘要，不写根目录测试日志。

- [x] T060 在 specs/047-flow-recovery-fixes/INDEX.md、v1/tasks.md、v1/checklists/requirements.md 交付逐项证据和未验证边界。
  - 依赖：T059；六项分别列实现/自动化/真实入口/未测/阻断；未完成项保持未勾选，不能用子任务自述替代客观证据。
  - 本阶段不提交、不推送、不打包、不发布；历史原始响应未知仍如实记录。
  - 完成：接手者和用户能根据可见结果与证据判断是否达成，不把文档阶段、MVP或局部冒烟说成整体完成。

### T052–T054 组合闭环证据

- T052：`.specify/memory/constitution.md` 模块地图登记 047 新增域（flow_run_lifecycle、store_run_lifecycle、store_flow_retry、flow_track_recovery、store_whitebox_lifecycle、task_state_lifecycle、store_history_lifecycle、useFlowOperationEpoch、useExecutionPanelCollapse），原则与版本不变；`README.md` 多平台段落补 4 条用户可感知行为（单轨重试、进度出现收拢抽屉、按钮层级、错误原因）；`CHANGELOG.md` 新增「未发布」条目（增加/优化/修复各按项目口径，一句话一条），未提升版本、未建 Release。
- T053：`tests/test_047_runtime_boundaries.py` 增加最终审计：15 个 047 产品模块存在且 Python ≤800 / TS ≤1200；禁改边界文件（app.py、task_continue_api.py、boss_cdp_raw.py、source_zhilian_cdp.py）无 047 业务 import；新 store 域不 import service/app/api；恢复域不 import HTTP 层；panel helper 不 import view/HTTP。聚焦：`uv run python -m unittest tests.test_047_runtime_boundaries tests.test_047_split_compat tests.test_048_task_continue_split_compat` → Ran 31 tests, OK。
- T054：`tests/test_047_history_lifecycle.py` 增跨故事闭环 2 例——「立即结束保存 → 迟到 worker 失败不改写收尾 Track → 结果绑定 → 历史可删且只删本轨」与「retry 新 run → 旧 run 迟到收口不改绑定/结果、旧失败保留、迟到回调留审计」。加上既有登录恢复（test_b096_flow_api 的 preflight resume login 系列）、失败重试（test_047_track_retry 15 例含真实 Flask→service→store 链路）、白箱迟到（test_047_flow_lifecycle）与前端 Flow 用例，FR-001–FR-014 / SC-001–SC-006 覆盖映射无空洞。聚焦：`uv run python -m unittest tests.test_047_track_retry tests.test_047_flow_lifecycle tests.test_047_history_lifecycle tests.test_b096_flow_new_round_release tests.test_b096_instance_actions` → Ran 37 tests, OK；前端相关 spec 见 T043/T046/T049/T050 证据。
- 等级说明：以上均为临时库/测试客户端/组件级集成证据，**不能称真实 E2E**；真实入口矩阵见 T055。

### T043–T047 US3 收拢矩阵

- 接线：`webui/src/composables/useDiscoveryFlowCoordinator.ts` 定义 `executionCollapseSceneKey`（画像 + runEpoch）、`hasScrapeStageProgress`（单平台 `scrapeSnapshot` 或 Flow `scrapeItems` 任一条出现真实进度数字）、`hasScreenStageProgress`（`screenSnapshot` 或 `screenItems`），薄接既有 `useExecutionPanelCollapse`；不往 DiscoveryView 或 useDiscoveryExecution 追加业务，两处既有文件仅此前已授权的 retry 事件落点有改动。
- helper 新增 `stageSnapshotHasProgress`：只认 `overall_percent/current/total` 中的真实正数（兼容旧接口 progress 为数字百分比）；只有 `message` 的启动占位、失败提示、请求拒绝（进度从未出现）都不动作，历史只读现场不动作。
- 矩阵（组件级真实卡片可见状态 + helper 级边沿，均绿灯）：
  | 启动方式 | 02 抓取进度出现 | 03 筛选进度出现 |
  |---|---|---|
  | 单平台手动/一键（本阶段进度数字到达） | 收 `searchPanelsOpen`+`advancedPanelsOpen` | 收 `screenPanelOpen` |
  | 全部流程（两条轨任一条进度到达） | 收两卡 | 收 `screenPanelOpen` |
  | 自动 AI 交接（02→03 自动进入） | 已收 | 03 首次进度到达即收 |
  | 单平台恢复现场（刷新后接回运行中任务） | 首次进度数字后收 | 同左 |
  | 历史只读现场 | 不动 | 不动 |
  | 仅 loading/请求被拒、进度从未出现 | 不动 | 不动 |
- 边沿与手动能力：同 scene（画像+轮次）每阶段只执行一次；用户同阶段手动重新展开后，后续 poll/refresh 不再抢回；scene 换轮（rotateRoundEpoch）后重新获得一次机会；收拢写的是既有 `CollapsibleCard` `cardOpenStates`，刷新按原 scene 策略恢复，未新增偏好、未改 scene schema。
- 证据：`npm test -- src/composables/__tests__/useExecutionPanelCollapse.spec.ts src/views/__tests__/DiscoveryView.spec.ts src/components/__tests__/CollapsibleCard.spec.ts src/composables/__tests__/useDiscoverySceneIdentity.spec.ts` → 4 files / 196 tests 全绿（含新增 helper 检测 2 例、DiscoveryView 047 两例、CollapsibleCard 兼容 2 例）；`npx vue-tsc --noEmit` 通过。红测核对：临时停用协调器接线后，DiscoveryView 047 两例与 helper 检测用例均失败，恢复后转绿（红→绿证据）。
- FR-005、SC-003 对应：真实挂载卡片的 `collapsible-body.open` 状态分别验证 02/03、单平台/全部、手动/自动与恢复现场；真实浏览器全流程待 T055。
- 未验证边界：窄屏单列下两个 02 抽屉各自独立（既有 1050px 断点行为）未在真实浏览器复测；未做真实像素/真机视口核对。

### T055–T060 真实入口、最终验证与收口

- T055 真实用户入口矩阵（2026-10-07 真实执行，证据仅写 `%TEMP%\cs047-real`，日志 `%TEMP%\cs047-server-*.log`）：
  - 环境：真实服务 `.venv\Scripts\python.exe webui/app.py` → `127.0.0.1:5000`（含全部返修代码）；真实浏览器 agent-browser 0.27.0（Chromium）；真实画像 `296b32cc038e494a` 与正式库 `~/.career-scout/webui/webui.db`。
  - 第1项（登录入口）：真实「浏览器与账号」面板显示 账号A BOSS 已登录 / 智联 已登录·待刷新（与 `/api/browser-accounts` 的 logged_in 一致）；真实「环境检查」显示 Chromium ✓、专用浏览器已启动 ✗（`127.0.0.1:9222` 连接失败，如实提示启动任务时自动拉起）、AI Key ✓ —— 登录态与 CDP 不可用真实可分辨。未构造「未登录账号」场景：账号均为用户真实登录态，未获授权不擅自退出；unknown 无真实实例。
  - 第2项（单轨重试/暂停继续）：真实点击失败 BOSS 轨「重试」→ `POST /api/flows/79a49be0064045d7/tracks/boss/retry` 200；同 Flow，BOSS 新 run `ec9cc2ed0ff043e0`，只读查库 `retry_of_run_id=3f1d0e7a43d74c30be3f25e51827f3ab`（旧失败 run 保留 partial）；兄弟智联轨状态/结果 `e81969fb-03d2-4832-b59b-445ddd61e9b4` 未动；新 run 真实抓取 306 条并自动交接到 AI 阶段。真实入口暴露并修复一个缺陷：完整性 `unverifiable`（快照 `completed_with_pending`）时失败轨「重试」按钮不渲染 → 红测 `screenFlow.spec.ts`/`useDiscoveryFlowPresentation.spec.ts`（`none` ≠ `retry-track`）→ `screenFlow.ts` 增加 `trackStatus` 轨道事实、`useDiscoveryFlowPresentation.ts` 接线 → 转绿并重建 dist 后真实按钮出现（真实点击成功）。暂停处理继续：`POST .../boss/pause` 200（轨道 paused、UI「已暂停/继续」）→ `POST .../boss/resume` 200（回 running 继续抓取）。
  - 第3项（抽屉收拢）：02 抓取进度出现时抓取配置卡已收拢（`expanded=false`）；03 筛选进度真实出现（BOSS AI 粗筛 25%）的新会话中「确认筛选条件」卡 `aria-expanded=false`、`collapsible-body.open=false`（自动收拢）；手动重新展开后 12 秒轮询不抢回（仍展开，保留手动能力）；单轨与 all 现场均见。历史只读现场不动作仍由自动化覆盖。
  - 第4项（操作区）：桌面 1440×900 与窄屏 390×844 下 BOSS/智联轨三按钮均在视口内、`elementFromPoint` 命中按钮本身（无遮挡）、可点；深色/浅色主题切换后布局一致可点；按钮紧凑（暂停 56×32、结束并保存 108×32、终止本轨 72×32）。
  - 第5项（收尾一致性）：真实「终止本轨」→ `POST .../boss/stop` 200，BOSS 显示「已停止/用户已取消」不误闪硬失败，兄弟智联继续运行（真实进度继续推进到 50%）；「结束并保存结果」在 JD 批次中弹出真实二选一（第 3/共 11 批）：「立即保存」→ `POST /api/task/finish/70a663ea...` 200 → 收尾 stopped/complete + 新结果 `39ccc617-8ef0-4736-ad12-0850c7d400c0`；「等这批抓完再保存」→ 真实等待批次收尾（三个操作按钮锁定为「正在保存…」，无硬失败闪烁）→ Flow stopped、快照 `closure={kind:"finish",phase:"committed"}`、新结果 `a5a67364-9714-4c9c-8a77-a8ab8eeaba6f`。
  - 第6项（历史删除）：真实删除本次测试生成的已停止 BOSS 轨结果 `39ccc617-8ef0-4736-ad12-0850c7d400c0`（待确认 215 岗位，正好覆盖「待确认不阻断删除」）→ `DELETE /api/result-history/39ccc617-...` 200；删除后 Flow `79a49be0064045d7` 仅剩兄弟智联轨且结果 `e81969fb-...` 与绑定保留（单轨删不伤兄弟）；对进行中的智联轨（Flow `3d6a5e487e9e4fa3`）删除按钮禁用并提示「请先结束或取消流程，再删除历史轮次」（活动受保护）；更旧可删历史（`7431a139`、`969d8d3f` 等）API `can_delete=true`。写入边界：只删本轮测试生成的轨道结果，已先记录对象；未删用户更早历史与其他画像数据。
  - T055 正式库副作用（如实记录，用户明确要求真实测试，未再扩大删除）：测试期间真实产生了 Flow `3d6a5e487e9e4fa3`（all，已停止）、Flow `17c74c723ed64ee0`（boss，已停止，结果 `0153e15c-97b4-4cf4-acd8-9d930e61dec6`）、Flow `79a49be0064045d7` 的 retry 新 run `ec9cc2ed0ff043e0` 及其结果 `a5a67364-9714-4c9c-8a77-a8ab8eeaba6f`（另一条 BOSS 结果 `39ccc617-8ef0-4736-ad12-0850c7d400c0` 已作为第6项删除验证对象真实删除）。这些是真实测试的客观产物，保留在正式历史中可查；未获进一步授权不额外删除。
  - T055 未验证边界（后续如实记录并被 2026-10-07 当前接手收敛覆盖）：旧阶段未构造未登录/unknown；当前接手后已用真实入口覆盖智联未登录继续返回 `source_login_required`、BOSS unknown 返回 `source_status_unclear`，真实入口保持暂停并显示明确原因。历史故障当时的原始响应未留存仍沿用前轮记录；手机端适配已按用户确认移出项目范围。
- T056 最终后端全量（返修后复跑）：`uv run python -m unittest discover -s tests`（日志 `%TEMP%\cs047-full-final2.log`）→ `Ran 3712 tests in 1255.555s`，`FAILED (failures=1)`；唯一失败 `test_repo_hygiene.test_no_untracked_non_ignored_files`（36 个 047 新文件未提交，本任务明确禁止提交）。产品侧零失败；其后仅有纯前端与文档改动，后端代码未再变化，故该结论继续有效（前端复跑见 T057/T058）。
- T056 返修记录（全量失败清单驱动，均已验证）：
  1. 真实缺陷一：`ScrapeEvidence.blocked_before_start` 使用未注册事件类型 `unit_blocked`，带 store 的真实预检阻断路径会抛 `unknown whitebox event_type` 并丢证据 → 改用已注册 `unit_failed` 并保留 `executed=False/preflight_blocked` 语义。
  2. 真实缺陷二：`whitebox_evidence.incomplete` 的 payload 未带 `error_code`，真实失败+迟到诊断时序下投影只能回落 `task_failed` → 补 `error_code`，`source_cdp_unavailable` 等真实原因恢复保留（原失败用例 `test_resume_continue...whitebox_primary_reason` 转为 OK）。
  3. 边界收敛：全量后发现实现曾改动 Plan 点名禁改的 `webui/store.py`、`webui/src/views/DiscoveryView.vue` 与 046 保护测试 `tests/test_b096_final_review.py` → 已全部回退原状，功能改按允许文件落位：mixin 组装改到 `webui/store_flow.py`（Plan 指定的组装门面）；`ctx.publish_scrape_outcome_atomic` 改由 `pipeline_task_outcome.py` 下游解析 store 公开入口（不要求改运行时组合文件）；retry 复用既有 `action` 通道、`updated_at` 由 `useDiscoveryInstanceActions.retry` 从权威 Flow 当前轨道读取（页面与平台壳不改）；历史删除资格改在 `ResultHistoryDrawer.vue` 内按字段读取（共享类型模块不改）。
  4. 契约对齐：C2 规定前端 action kind 为 `retry-track`、测试 id 为 `retry-flow-track`；原先的 `retry`/`retry-scrape` 双 kind 已统一为单一 `retry-track`（`screenFlow.ts`、`useDiscoveryParallelFlow.ts`、`ScreenRoundActions.vue` 及测试同步）。
  5. 回归防护：新增 `tests/test_047_preflight_outcome.py::test_blocked_before_start_persists_into_real_store`，防止“无 store 内存用例掩盖真实链路失败”再次发生。
  6. C2 请求契约补齐：前端从权威 Flow 当前轨道取 `expected_track_id` 并随请求发送（空串仍发字段表示“无值”，不等于省略校验）；`useDiscoveryParallelFlow.spec.ts` 断言语义同步。
- T056 聚焦回归（返修后）：`tests.test_047_preflight_outcome tests.test_047_history_lifecycle tests.test_047_track_retry tests.test_047_runtime_boundaries tests.test_047_flow_lifecycle tests.test_b096_flow_history tests.test_result_history tests.test_resume_continue tests.test_b096_final_review` → Ran 147 tests, OK；`tests.test_whitebox_integration tests.test_whitebox_rules tests.webui_store.test_store_whitebox tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_b096_flow_api tests.test_b096_flow_service tests.healthy_pipeline.test_pipeline_semantics tests.healthy_pipeline.test_pipeline_state tests.test_task_pause_support` → Ran 257 tests, OK；047 合并聚焦 → Ran 98 tests, OK。
### 复核返修第二轮（2026-10-07，8 项复核报告）

复核报告 8 项全部按“先失败红测 → 修复 → 聚焦回归”处理；不提交、不推送、不发布，未修改正式数据：

- R1（P1）旧 AI 回调破坏重试轨：`webui/store_flow_failure.py` 写权核对改为按当前阶段精确判定（AI 段只认 `screen_run_id`，抓取段认 `screen_run_id or scrape_run_id`；无当前阶段 run 时沿用绑定交集）。红测→绿测：`tests/test_047_flow_lifecycle.py::AiRetryLateCallbackTests`（旧 AI 回调后新轨保持 running、结果指针与兄弟不动）。
- R2（P1）同 run 恢复后旧 worker 覆盖新 worker：`webui/pipeline_task_outcome.py` 接入实例身份守卫（同一 `ctx.lock` 内比较并在被替换时只留 late_callback、返回 `superseded`，不写内存/DB/Track）；`webui/runners/pipeline_task.py` 捕获启动实例并接入进度、页级、结果、异常与清理路径；`sync_flow_track_after_scrape` 移入 `webui/flow_run_lifecycle.py`（runner 保持 <600 行薄接线）。红测→绿测：`SameRunWorkerReplacementTests`（真实 `run_pipeline_task` 协作，替换实例的进度/状态/结果与 durable 状态均不被旧实例改写）。
- R3（P1）具体错误被完整性原因覆盖：`finalize_scrape_outcome` 统一 `_select_primary_failure` 结果并随 outcome 返回；runner 用同一 `primary_code/primary_reason` 同步 Track；内存 partial 提示优先具体原因。红测→绿测：`PrimaryFailureChainTests`（DB 原始行、生命周期读取入口、live task、Track 同为 `source_status_unclear`，完整性仍独立可读）。
- R4（P1）抓取重试重复创建主记录：`webui/flow_submission_service.py::create_scrape_records` 检测 claim 已创建的主记录并复用（合并参数、不重建、不覆盖 `retry_of_run_id`）。红测→绿测：`ScrapeRetryInitializationTests`（前置齐备严格成功且只提交一个 worker、初始化失败只补偿新尝试、并发只提交一个）＋ `RetryHttpRouteTests.test_retry_route_claims_and_submits_through_real_chain` 改为严格成功断言。
- R5（P1）过期请求失败响应污染状态：`webui/src/composables/useDiscoveryParallelFlow.ts` 的 refresh 失败分支核对所属代次与身份（过期失败不标 stale、不写旧错误、不抛给调用方）；动作成功/失败/finally 均按动作代次与轨道归属守卫，忙态由动作 owner 接管与释放。红测→绿测：`useDiscoveryParallelFlow.spec.ts` 新增旧轮询晚失败、切画像旧失败、双轨忙态交错 3 例。
- R6（P1）已停止且有待确认岗位的历史被拒删：`webui/store_history_lifecycle.py` 移除“待确认岗位即阻断”规则（岗位是数据内容，不是活动证据；真正 active/pending finish 仍由上方检查保护）。红测→绿测：`PendingJobHistoryDeletionTests`（stopped/failed 含待确认岗位可删、删除带走该轨待确认数据、兄弟保留、真正 active 仍保护）。
- R7（P2）文件边界遗漏：`webui/store_runs.py` 已恢复 HEAD 原状（零改动）；partial 保留具体原因的读取能力按 Plan 白名单落位到 `webui/store_run_lifecycle.py::get_screening_run_outcome`，`webui/task_state_api.py` 接线（partial 时用生命周期权威读取）。`tests/test_047_preflight_outcome.py` 读取口径同步该入口；`git status -- webui/store_runs.py` 为空。
- R8（P2）验收未闭环：T051 恢复未勾选（原勾选证据只有组件回归、真实渲染未检查）；T055 真实入口矩阵已于 2026-10-07 真实执行（见上节）。
- R9（P1，T055 真实验收新发现并本轮修复）：失败轨在任务快照 `completed_with_pending` + 完整性 `verifiable=false` 时「重试」入口消失（真实 Flow `79a49be0064045d7` 现场：BOSS 轨 failed，卡片「无法确认是否完成」，`.screen-round-actions` 为空）。修复：`webui/src/screenFlow.ts` `TrackActionBarFacts` 增加 `trackStatus`，轨道级 failed 且本段承载线状态时给 `retry-track` 出口；`webui/src/composables/useDiscoveryFlowPresentation.ts` 把 `trackStatus` 传入派生。红测→绿测：`screenFlow.spec.ts`、`useDiscoveryFlowPresentation.spec.ts` 各 1 例先红后绿；前端全量 74 files / 1609 tests 全绿；重建 dist 后真实按钮出现并可点击重试成功（见第2项证据）。

- 复核返修聚焦回归：`tests.test_047_flow_lifecycle tests.test_047_history_lifecycle tests.test_047_preflight_outcome tests.test_047_track_retry tests.test_047_runtime_boundaries tests.test_047_login_diagnostics tests.test_b096_flow_history tests.test_b096_flow_api tests.test_b096_flow_service tests.test_b096_instance_actions tests.test_result_history tests.test_resume_continue tests.test_task_pause_support tests.test_whitebox_integration tests.test_whitebox_rules tests.test_b096_final_review` → Ran 298 tests, OK。扩面聚焦：+`tests.healthy_pipeline.test_pipeline_state tests.healthy_pipeline.test_pipeline_semantics` → Ran 416 tests, OK。前端：`npx vitest run src/composables/__tests__/useDiscoveryParallelFlow.spec.ts` → 53 tests 全绿。等级为组件/集成级，不是真实 E2E。
- 复核返修最终门禁：后端 `uv run python -m unittest discover -s tests`（最终日志 `%TEMP%\cs047-full-review2c.log`；2b 为同结论的前次基线）→ `Ran 3722 tests in 1222.701s`，`FAILED (failures=1)`；唯一失败 `test_repo_hygiene.test_no_untracked_non_ignored_files`（36 个 047 新文件未提交，本任务禁止提交，预期内），产品侧零失败。前端全量 `npm test`（日志 `%TEMP%\cs047-npm-test-review2b.log`）→ 74 files / 1607 tests 全绿；`npm run build`（日志 `%TEMP%\cs047-npm-build-review2b.log`）→ exit=0；`npx vue-tsc --noEmit` → exit=0；`git diff --check` → exit=0。
- 边界复核：`webui/store_runs.py`、`webui/store.py`、`webui/app.py`、`webui/src/views/DiscoveryView.vue`、`webui/task_continue_api.py`、`scripts/boss_cdp_raw.py`、`webui/source_zhilian_cdp.py`、`webui/src/composables/useDiscoveryExecution.ts` 零改动（git status 复核）。`webui/task_state_api.py` 净增 3 行薄接线；`webui/runners/pipeline_task.py` 551 行（<600）。
- 过程中自产探针 `cs047_probe1.py` 曾落在仓库根目录，已删除（现已零残留），删除后重跑原失败卫生用例为预期未提交清单；不把该失败归入产品缺陷。

- T057 前端全量：webui 下 `npm test`（package.json 真实命令 `vitest run`，日志 `%TEMP%\cs047-npm-test-final3.log`）→ `Test Files 74 passed (74)`、`Tests 1604 passed (1604)`，exit=0；`npx vue-tsc --noEmit` 通过。等级为组件/集成级 Vitest，不是浏览器 E2E。
- T058 前端构建：webui 下 `npm run build`（`vue-tsc --noEmit && vite build`，日志 `%TEMP%\cs047-npm-build-final2.log`）→ exit=0，`built in 750ms`；仅有 Vite chunk >500kB 的既有提示，无类型/构建错误。产物 dist 为 gitignore，不入库、不分发。
- T059 卫生与差异：`git diff --check` → exit=0（无空白错误）；`git status` → 47 个修改 + 23 个未跟踪（共 70 项，未跟踪含 36 个 047 文件）；`uv run python -m unittest tests.test_repo_hygiene` → `Ran 15 tests`，唯一失败 `test_no_untracked_non_ignored_files`：36 个新文件未提交（本任务禁止提交，如实记录，不改忽略、不绕过）。其余 14 项含 hooks 配置、敏感/本地产物检查通过；禁改清单（app.py、store.py、boss_cdp_raw.py、task_continue_api.py、source_zhilian_cdp.py、DiscoveryView.vue、useDiscoveryExecution.ts）经 `git status` 复核零改动。
- T060 逐项证据与未验证边界已写入 `INDEX.md`、本文件与 `v1/checklists/requirements.md`；未验证项保持未勾/标未测，六项目标不因文档勾选而宣称真实 E2E 通过。
- T055 真实验收后最终门禁（2026-10-07，R9 修复后复跑）：前端全量 `npm test`（webui 下 `vitest run`）→ `Test Files 74 passed (74)`、`Tests 1609 passed (1609)`，exit=0；`npm run build`（`vue-tsc --noEmit && vite build`）→ exit=0、`built in 838ms`（日志 `%TEMP%\cs047-*`）。直接相关聚焦：`npx vitest run src/__tests__/screenFlow.spec.ts src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts src/composables/__tests__/useDiscoveryInstanceActions.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts src/components/__tests__/ScreenRoundActions.spec.ts src/views/__tests__/DiscoveryView.spec.ts` → 6 files / 336 tests 全绿。R9 修复为前端呈现层，后端代码无新增变化；T056 的 3722 tests 后端全量结论（唯一失败=未提交卫生门禁）继续有效。

## 依赖与执行策略

`T001→T002→T003→T004→T005→T006→T007→US1(T008–015)→US5(T016–026)→US2(T027–036)→US6(T037–042)→US3(T043–047)→US4(T048–051)→T052–060`。

没有[P]实施任务：service/store/投影/共享组件互相有关联，默认串行。未来若明确授权多AI，可隔离未共享文件的纯测试编写，例如T008与已经具备契约的T043测试，但不能越过各自代码依赖或共用目录；本计划不自动委派。

中间最小闭环为US1+US5+US2，能先验错误恢复和failed retry，但六项完整目标及终态/历史/视觉全部必须完成，不能将US3/4/6延期后宣称交付。

## 覆盖映射

| 需求/标准 | 执行与证据任务 |
|---|---|
| FR-001–FR-002 / SC-001，完整登录识别而非只文案 | T008–015、T024、T054–055 |
| FR-003–FR-004 / SC-002，可恢复继续+单轨retry | T027–036、T054–055 |
| FR-005 / SC-003，02/03全部启动方式收拢 | T043–047、T055 |
| FR-006 / SC-004，按钮密度与层级 | T048–051、T055 |
| FR-007–FR-009 / SC-005，同类时序、分开stop/finish | T016–026、T028–035、T054–055 |
| FR-010–FR-013 / SC-006，结束资格/残留/单轨删除 | T037–042、T054–055 |
| FR-014，共享树干、平台适配 | T010–015、T017–024、T033、T038–045、T049、T053 |

## 本轮文档交付状态

只完成计划、契约和任务拆解；本文件60项复选框全部未执行。没有产品测试/构建/源码改动、正式数据变更或Git发布动作。文档交付检查记录在checklists/planning.md，不计作以上任务完成。

## 执行证据（实施轮）

### T001 接手基线

- 分支：`codex/fix/047-flow-recovery-fixes`（自 main fe004ed 创建；工作区此前无未提交改动，仅未跟踪 specs/047-flow-recovery-fixes/）。
- 授权核对：用户明确要求实施 INDEX 指向的当前版本 v1，不提交/推送/发布；未授权正式数据删除与真实浏览器操作。
- `.specify/feature.json` = `specs/047-flow-recovery-fixes/v1`（完整路径核对一致，与 specs/047-large-file-followup 无关）。
- 行数实测（2026-10-07，与 research.md 一致）：pipeline_exec_search 817、store_flow_state 891、flow_service 694、flow_submission_service 735、store_whitebox 600、runners/pipeline_task 598、task_continue_finish 649、store_runs 789。
- 基线兼容：`uv run python -m unittest tests.test_047_split_compat tests.test_048_task_continue_split_compat` → Ran 13 tests, OK（4.55s）。
- 禁改清单确认：webui/app.py、webui/store.py、scripts/boss_cdp_raw.py、webui/task_continue_api.py、webui/source_zhilian_cdp.py、DiscoveryView.vue、useDiscoveryExecution.ts 不改；046/033 与 047-large-file-followup 只读。

### T002 接口/状态对照

- C1–C6 对照完成：
  - C1 载荷：`classify_preflight_failure` 现仅在 systemic 时给 hard_stop（pipeline_exec_status.py:87-100）；非 systemic 只留 error 文本，无 failed_code —— 与本轮 T011 目标一致（红测缺口确认）。
  - C2 retry：现 operate_track 只接受 pause/resume/stop（flow_service.py:386-397），failed 走终态拒绝；无 retry 路由 —— 与 T031 目标一致。
  - C3 closure：task_state_api/task_continue_finish 现无 closure 字段；claim_flow_finish→finish_flow_task_atomic→cancel_task_atomic 三个原子入口在 store_flow_state.py，边界清楚。
  - C4 epoch：前端 refresh/operate 直接写响应，无 epoch 守卫；useDiscoveryFlowPresentation 两段 await 分别发布（643-654 附近）。
  - C5 收拢：单平台启动收拢 refs（useDiscoveryExecution），全部/自动 AI 不经该 refs —— 未统一。
  - C6 删除：store_result_history_mixin 事务检查 screening/search/tasks；旧 queued search 读残留导致拒绝（与用户现象一致）。
- patch 面核对：`webui.pipeline_exec.ensure_chrome_ready` 由多测试 patch（动态门面取用必须保留）；`webui.pipeline_exec_search.time.sleep` 被 test_pipeline_pause_guard patch；`webui.pipeline_exec_search.run_search` 被 test_account_round_robin 以 inspect.getsource 断言（make_list_robin 先于组合循环，且不得出现平台名分支）。
- 公开 helper：`_canonical_source_code/_is_source_hard_stop/_record_source_hard_stop_evidence` 仅被本模块使用（无外部 import，兼容 re-export 保留）。
- store_flow_state 组装：store_flow.py 以 `StoreFlowStateMixin` 组合（保持原 MRO 位置）；cancel/failure 拆分后由原 mixin 继续组合。
- 日志脱敏入口：`webui/logging_setup.py` 统一配置（凭据脱敏 + 任务上下文），安全摘要走 safe_log 字段。
- 无冻结冲突需要停链：所有目标与当前源码定位一致。

### T003–T007 独立结构前置（纯搬运）

- T003 新建 `tests/test_047_runtime_boundaries.py`。初始红测：兼容面 5 项通过；结构门禁 19 failures + 1 error（新域不存在、facade 超线、重复实现仍在、service 未组合新 mixin）。均属"结构未满足"预期，不是旧行为回归。
- T004 新建 `webui/pipeline_search_preflight.py`：搬 `_canonical_source_code/_is_source_hard_stop/_record_source_hard_stop_evidence` 与 ensure-chrome/preflight 编排为 `run_source_preflight`（失败返回原 payload，成功 None，调用方仍 `_finish`）；`pipeline_exec_search.py` 保留同名导入兼容面与 `_facade` 动态取用。行数：817 → 757；新域 118 行。聚焦：`tests.test_whitebox_integration tests.source.test_source_boss tests.source.test_source_zhilian` + 兼容文件 → 243 tests OK。
- T005 新建 `webui/store_flow_cancel.py`（cancel_task_atomic，288 行）、`webui/store_flow_failure.py`（close_flow_task_state_atomic + `_closure_run_status/_closure_track_status/_append_flow_state_event`，257 行）；`store_flow_state.py` 组装两 mixin并保留 claim/restore/discard/finish：891 → 382 行。中途发现 finish 区块被脚本切掉（store 无 `finish_flow_task_atomic` 导致 4 个测试 error），已从 HEAD 原样恢复该区块并复跑通过；属搬运脚本缺陷，非行为改动。聚焦：`tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_b096_round4_review tests.test_b096_flow_store tests.test_b096_flow_history` → 45 tests OK。
- T006 新建 `webui/flow_errors.py`（59 行，FLOW_ERROR_MESSAGES/public_flow_message/FlowResumeError/PlatformUnavailableError，异常对象同一身份）、`webui/flow_track_operations.py`（132 行，operate_track 原流程）、`webui/flow_preflight_recovery.py`（534 行，resume scope/config/login/probe/补偿编排 mixin）。`flow_service.py`：694 → 544 行（operate_track 薄委托 + 兼容 re-export）；`flow_submission_service.py`：735 → 219 行（组合 FlowPreflightRecoveryMixin）。过程中修掉搬运脚本引入的 `self`→`service` 一处 NameError 和兼容断言与 Plan 不符的一处测试断言（改为按 Plan 验证薄委托）。聚焦：`tests.test_047_runtime_boundaries tests.test_b096_flow_service tests.test_b096_flow_api tests.test_b096_instance_actions tests.test_b096_quality_round4` → 88 tests OK。
- T007 行数现状：search 757、preflight 118、state 382、cancel 288、failure 257、service 544、submission 219、operations 132、preflight_recovery 534、flow_errors 59；全部低于红线，预警线以上文件均无新增业务。`.specify/memory/constitution.md` 模块地图新增 6 条（preflight/cancel/failure/operations/preflight_recovery/flow_errors），原则与版本未改。
- 独立前置 Plan 合并聚焦：`uv run python -m unittest tests.test_047_runtime_boundaries tests.test_047_split_compat tests.test_048_task_continue_split_compat tests.test_b096_flow_service tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_whitebox_integration` → Ran 64 tests, OK。
- 纯搬运对照：函数签名、返回载荷、SQL 语句、事务顺序、patch 面（`webui.pipeline_exec.ensure_chrome_ready` 等）均保持；无新增业务行为（unknown 证据补记留到 T012）。

### Phase 2 US1 证据（T008–T015）

- T008 新建 `tests/test_047_login_diagnostics.py`（四态证据 + 脱敏），扩展 `tests/chrome_setup/test_chrome_setup.py`（异常结构/未知码/损坏 JSON/CDP 阶段异常脱敏）。初始红测（属实缺陷）：
  1. 非 dict 异常结构（如 JSON 数组）被误判 `not_logged_in`——应保持 unknown；
  2. 未识别业务码（code=7）被误判 `not_logged_in`；
  3. CDP 阶段异常日志回显异常正文（合成分敏感标记命中）。
- T009 修复 `scripts/boss/login.py`：新增 `_probe_evidence`（只记 HTTP 类别/结构类别/已知业务码/阶段，不记正文）；`probe_login_state_tri` 对非 dict 或缺 code 的类型保持 unknown；未识别业务码保持 unknown；`check_login_state_tri` 异常日志只记异常类别。行数 206 → 274（仍远低于红线）。聚焦：`tests.test_047_login_diagnostics tests.chrome_setup.test_chrome_setup tests.test_risk_signal_tiers tests.test_login_state_cache` → 124 tests OK。
- T010 扩展 `tests/source/test_source_boss.py`（复核绕过 unknown 缓存、not_logged_in 传递、unknown→source_status_unclear 不冒充 CDP）、`tests/source/test_source_zhilian.py`（unreachable 保留自身事实、复核绕过 logged_in 缓存）。未修改未列的 source_zhilian_cdp 判定。聚焦：`tests.source.test_source_boss tests.source.test_source_zhilian tests.test_login_state_cache` → 226 tests OK。
- T011 新建 `tests/test_047_preflight_outcome.py`。红测缺口确认：`classify_preflight_failure` 不返回 failed_code。修复 `webui/pipeline_exec_status.py`：载荷始终带规范 failed_code（别名归一）。新增 `webui/pipeline_task_outcome.py::_select_primary_failure`（具体执行原因优先于完整性主因）。
- T012 `webui/whitebox_evidence.py` 新增 `blocked_before_start`：为所有 planned 单元记录真实预检阻断事实（保留未执行含义，不伪造页完成/空结果）；`webui/pipeline_search_preflight.py` 对 unknown 等非 hard_stop 预检失败同样落白箱证据。
- T013 新建 `webui/store_run_lifecycle.py`（180 行）：`publish_scrape_outcome_atomic` 在同一事务写执行段终态/原因与 search_runs 账本，沿既有转移合法性（partial 保留具体错误）。`webui/store_flow.py`（Plan 指定的 mixin 组装门面）组合该 mixin，`webui/store.py` 保持原样；`pipeline_task_outcome.py` partial/unverifiable 分支经本模块 `_publish_scrape_outcome` 走新入口（门面未挂载时回退该次运行的 store），不要求改运行时组合文件；`store_runs._screening_run_row` 仅对 succeeded 剥离旧错误（partial 保留真实原因）。缺陷修复：搬运 T004 时遗漏 `cdp_port` 局部定义导致 17 个 healthy_pipeline 用例 NameError，已补回并复跑全绿。聚焦：`tests.test_047_preflight_outcome tests.test_whitebox_rules tests.test_whitebox_integration tests.healthy_pipeline.test_pipeline_state tests.healthy_pipeline.test_pipeline_semantics tests.test_task_pause_support tests.test_047_login_diagnostics` → 201 tests OK。
- T014 修 `webui/flow_preflight_recovery.py::_stable_probe_code`：删除 `source_status_unclear/source_unreachable → source_cdp_unavailable` 错误映射（保留其自身事实），真实 CDP/login 映射不变；provisional 清理逻辑未动。扩展 `tests/test_047_preflight_outcome.py`（新增 ResumeProbeCodeMappingTests）。聚焦：`tests.test_047_preflight_outcome tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_b096_flow_service` → 34 tests OK。
- T015 合并聚焦：`uv run python -m unittest tests.test_047_login_diagnostics tests.test_047_preflight_outcome tests.source.test_source_boss tests.source.test_source_zhilian tests.test_whitebox_integration` → Ran 269 tests, OK（48.6s）。
- FR-001/FR-002、SC-001 对应关系：明确未登录→source_login_required、unknown→source_status_unclear、CDP→source_cdp_unavailable 三态在探针/适配/预检/载荷全链路保持；具体失败原因在白箱与 DB partial 均保留，完整性单独存在。
- 未验证边界（如实记录）：历史故障当时的真实响应未留存，本轮单元/桩验证不能称为真实登录环境验收；真实入口验证见 T055。

### Phase 3 US5 证据（T016–T026）

### Phase 4 US2 证据（T027–T036）

### Phase 5 US6 证据（T037–T042）

- T037 新建 `tests/test_047_history_lifecycle.py`（8 例，临时库）：覆盖「Track 终结 + 同 id抓取 execution 终结 + search 账本 queued 残留」的可删场景、零岗位 failed 轨按 track id 可删、真正 running/paused/证据不明 interrupted、跨画像、共享引用、删除后兄弟保留。
- T038 新建 `webui/store_history_lifecycle.py`：`analyze_history_deletion` 只读投影 `can_delete/delete_block_reason`；`repair_and_analyze_history_deletion` 仅在 DELETE 事务内，对「确定终结 + 精确归属」的 queued search 局部修正后重判；含 retry 旧尝试的同轨闭包、不越入兄弟。
- T039 `webui/store_result_history_mixin.py::delete_run_closure` 改为先在同一 `BEGIN IMMEDIATE` 内重判/局部修正/复查，再走原删除；发现并修复旧闭包检查会继续用「queued = 活体」误拦已修正残留的真实缺陷（有红→绿证据）。`test_result_history` 的独立旧轮删除兼容保留（无 Track 锚点的 result_snapshot 仍按原范围可删）。
- T040 `webui/store_flow_results.py::_flow_track_result` 与 `webui/result_history.py::list_history` 统一调用同一资格 helper 投影，聚合轨带 `can_delete/delete_block_reason`；新增 `tests/test_b096_flow_history.py` 权威资格与并发活动 DELETE 拒绝用例。
- T041 `ResultHistoryDrawer.vue` 在允许文件内按字段读取服务端权威 `can_delete/delete_block_reason`（不要求改共享类型模块 `resultHistory.ts`，旧响应保留兼容）；DELETE 失败仍走服务端重判与刷新。新增 2 个组件断言（权威 false 禁用并显示真实原因；权威 true 时 failed 可删）。
- 聚焦证据：`uv run python -m unittest tests.test_047_history_lifecycle tests.test_b096_flow_history tests.test_result_history` → Ran 29 tests, OK；前端 `npm test -- src/components/__tests__/ResultHistoryDrawer.spec.ts` → 33 tests 全绿；`npx vue-tsc --noEmit` 通过。
- FR-010–FR-013、SC-006 对应：failed/stopped 历史不因确定旧 queue 误拒，真正 active 受保护，单轨删除不伤兄弟，未新增整流程删除。
- 未验证边界：真实用户界面删除动作未在正式库执行（无正式写入授权），待 T055。卫生门禁 `tests.test_repo_hygiene` 当前因未提交的新文件（本任务明确禁止提交）报“untracked non-ignored”，如实记录，不视为通过。


- T027/T028 新建 `tests/test_047_track_retry.py` 与 `webui/store_flow_retry.py`：failed 专用 CAS（精确 track/run/updated_at + 当前 Flow 门槛），抓取轨新 run 同时落 screening_runs 与 search_runs 并绑定 scrape_run_id；事务内严格 INSERT 新 id，禁止 REPLACE 旧 attempt。红测确认旧实现无此入口后转绿。
- 修复过程中发现并解决两处真实缺陷（有红→绿证据）：(1) `screening_runs` 主记录被 `INSERT OR REPLACE` 重建会触发外键 `ON DELETE SET NULL`，把 Track 的 screen_run_id 绑定清空——AI 重试改走 `resume_from_run_id == task_id` 的既有 runner 通道，由 retry 事务预先创建主记录并跳过重复创建；(2) 平台壳 `ParallelPlatformProgress.vue` 同时用 `v-on` 表与 `@retry` 绑定 retry 事件，后者覆盖前者，点击实际发成 `action` 且丢失 updated_at——改为 retry 类 kind 只从单一出口发 `retry(platform, runId)`。
- T029/T030 `webui/flow_submission_service.py::retry_failed_track` 按 stage 分派：AI 失败走新 `webui/flow_track_recovery.py::retry_failed_ai_track`，只从该轨旧 AI run 的 `execution_params.scrape_task_id` 复用持久化抓取输入、frozen 条件与账号身份；缺输入返回可解释 409、不提交、不重抓兄弟。旧失败 run、旧 result 指针、兄弟轨道字段全部保留。
- T031 `webui/flow_api.py`、`flow_track_operations.py`、`flow_service.py` 透传 `expected_track_id/expected_run_id/expected_updated_at`；retry 复用现有 `<action>` 路由，旧 pause/resume/stop 路径不变。
- T032/T033 前端：`deriveTrackActionBar` 把 failed 分支提到 terminal 门禁之前（平台呈现层给失败轨传 terminal=true，旧顺序会让重试入口永远不渲染——真实缺陷，有红→绿证据）；`ScreenRoundActions.vue` 统一 `retry-flow-track` 测试 id；`ParallelPlatformProgress.vue` 把 retry 点击原样转发。新增组件与 screenFlow 断言。
- T034/T035 `useDiscoveryParallelFlow.retryTrack` 发送精确身份并复用 epoch 守卫（同时修掉成功路径重复补读：仅未轮询时才 `startPolling`）；`useDiscoveryInstanceActions.retry` 核对本轨身份/忙态并刷新权威投影；`useDiscoveryInstanceActions.retry` 在允许的下游从权威 Flow 当前轨道读 `updated_at` 后调用（页面与平台壳不改，retry 复用既有 `action` 通道）。
- 聚焦证据：`uv run python -m unittest tests.test_047_track_retry tests.test_b096_flow_service tests.test_b096_instance_actions tests.test_b096_quality_round4 tests.test_b096_round4_review tests.test_b096_production_flow tests.test_b096_flow_api` → Ran 104 tests, OK（57.3s）。前端 `npm test -- src/__tests__/screenFlow.spec.ts src/composables/__tests__/useDiscoveryParallelFlow.spec.ts src/composables/__tests__/useDiscoveryInstanceActions.spec.ts src/components/__tests__/ScreenRoundActions.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts` → 全部通过（含新增 failed/retry 用例）；`npx vue-tsc --noEmit` 通过。
- FR-003/FR-004、SC-002 对应：failed 有 run/无 run、AI/抓取两方向、兄弟 running/done、缺输入拒绝、并发 CAS 单赢家、旧失败诊断不改都有程序性断言；真实用户操作待 T055。


- T016 新建 `tests/test_047_flow_lifecycle.py`。红测确认两处真实缺陷：(1) finish claim 窗口内迟到 worker 失败把 Track 改成 failed，会使用户「结束并保存」被误拒；(2) 新尝试（renbind）后旧 run 收口改写已绑定新 run 的 Track。
- T017 新建 `webui/flow_run_lifecycle.py`（131 行）：当前尝试判定（`is_superseded`）、finish claim pending（`finish_claim_pending`）、同 run worker 实例比对（`worker_instance_superseded`）、迟到回调安全审计（`record_late_callback`）。
- T018 在 `webui/store_flow_failure.py`（close_flow_task_state_atomic）写事务内加入写权核对：旧尝试（bound_ids 不含本次 run）与 finish claim 期间（interrupted + user_finished + 当前 Track 未终结）只落 `late_callback` 审计并返回现状，不改写 Track/Run。聚焦：`tests.test_047_flow_lifecycle tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_b096_flow_store tests.test_b096_round4_review tests.test_b096_flow_api` → 97 tests OK。
- T019 新建 `webui/store_whitebox_lifecycle.py`（150 行，策略集中在白箱域外）；`webui/store_whitebox.py` 只在既有写事务做薄接线（append 事件/upsert 单元/finalize/update summary）：已终结白箱的迟到 unit/page 事实、旧 attempt 事实与迟到 failed 结论只记 `late_callback` 诊断，不改结论/summary/revision；resume 重开后正常收新证据。新增 3 个专项测试（迟到失败不改 interrupted、迟到页级事实不污染单元与 revision、resume 重开可继续）。聚焦：`tests.test_whitebox_integration tests.test_whitebox_rules tests.webui_store.test_store_whitebox tests.test_047_flow_lifecycle tests.test_047_preflight_outcome` → 67 tests OK。
- T020 新建 `webui/task_state_lifecycle.py`（只读 `project_closure`）；`webui/task_state_api.py` 薄接：任务状态响应新增 `closure`（finish pending/committed、stop committed，无正面证据 null；process_restart 不冒充 user_finished）。聚焦：`tests.test_047_flow_lifecycle tests.test_048_task_continue_split_compat tests.test_b096_instance_actions tests.webui_app.test_webui_app_semantics tests.test_b096_flow_api` → 131 tests OK。
- T021/T022 新建 `webui/src/composables/useFlowOperationEpoch.ts`（绑定 profile/flow/track/run 的代次守卫）与测试。红测暴露 invalidate 后空身份仍匹配的漏洞，已修复（bound 标志）。接入 `useDiscoveryParallelFlow.ts`：refresh 与 operate 都核对 epoch + 身份；resetForNewRound invalidate 全部在途请求。聚焦：`useFlowOperationEpoch.spec.ts useDiscoveryParallelFlow.spec.ts useDiscoveryInstanceActions.spec.ts` → 58 tests OK。
- T023 `useDiscoveryFlowPresentation.ts` 两段投影改为本地全部构建后一次同步发布；新增红→绿测试：旧实现（先发布抓取行）在筛选段未返回时会让测试失败，修复后通过。聚焦：76 tests OK。
- T024 `webui/src/types.ts` 新增 `TaskClosure` 并扩展 `TaskSnapshot.closure`；`TaskProgress.vue` 支持 closure prop/snapshot：finish pending 显示「正在结束保存」且不显示暂停/失败原因、finish committed 显示「已结束保存部分结果」、stop 不冒充保存、真实失败/取消优先；`ParallelPlatformProgress.vue` 转发 snapshot.closure。新增 4 个组件测试。聚焦：`TaskProgress.spec.ts ParallelPlatformProgress.spec.ts useDiscoveryParallelFlow.spec.ts useDiscoveryInstanceActions.spec.ts useDiscoveryFlowPresentation.spec.ts` → 207 tests OK。
- T025 扩展 `useDiscoveryInstanceActions.spec.ts`：终止本轨不冒充保存且兄弟保留、保存被拒绝只提示一次真实错误。聚焦：6 tests OK。
- T026 合并聚焦：`uv run python -m unittest tests.test_047_flow_lifecycle tests.test_whitebox_integration tests.webui_store.test_store_whitebox tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_b096_flow_api` → Ran 107 tests, OK。
- FR-007/008/009、SC-005 对应：stop 与 finish 各走原语义（cancel_task_atomic / finish_flow_task_atomic 未合并）；迟到回调与旧响应不改写新状态；真实失败（failed/unavailable/cancelled）优先于 closure；程序性竞争用 Event/一次性桩验证。
- 未验证边界：真实浏览器闪红全过程、真实 CDP 时序未在浏览器中观察（T055）。



### 继续复核与直接返修（2026-10-07，当前授权）

用户明确授权：已返修后继续检查；发现残留问题直接按当前 047 修复，不停在报告。以下均归属原 US1/US4/US5/US6，不增加产品范围，未提交、推送、发布。

- R10 / US5：真实 finish claim 已在 SQLite 拒绝迟到失败，公共协调器仍把 live task 改成 failed，并清理保存中的 worker 占位。新增 `FinishClaimWindowTests.test_public_closure_keeps_live_finish_and_worker_claim`，先红（failed != running）后绿。`store_flow_failure.close_flow_task_state_outcome_atomic` 在原事务返回内部 applied 事实；旧 Flow-only 方法保持返回形状。`flow_task_state` 仅在事务接受本次收口时同步内存及清理，拒绝路径只保留 late_callback。
- R11 / US5：同 run 恢复后，旧 worker 的 on_combo_done 仍可写岗位/检查点；finalize 后被换实例仍会释放新 worker claim；任务已移出 live map 也仍可发布 durable 成功。新增 `SameRunWorkerReplacementTests` 的 late combo、late cleanup、detached worker 三例，分别先红（旧岗位入库／释放新 worker／未判 superseded）后绿。runner 捕获原实例，组合、页级、source 无法创建、状态交接、异常及清理均核对身份；比较与写入用运行时同一 RLock 协调，页级等待 flush lock 后再次核对；finalize 对 absent/replaced 都拒绝写权。runner 573 行（<600），不扩白名单。
- R12 / US1：公共失败收口把已注册 source_status_unclear 降成 internal_error，和已修复的正常 outcome 路径仍不一致。新增 `SupersededAttemptTests.test_public_closure_preserves_unknown_source_reason` 先红后绿，贯穿原始 run、live task、Track；把该规范错误纳入公共安全码集合，仍不把 unknown 说成未登录。
- R13 / US5：refresh 先发布新 Track 再等 task-state，存在不同版本事实混合；快照失败又被 allSettled 吞掉。新增 ParallelFlow 的延迟快照与读取失败保留现场两例，延迟用例先红后绿。先在局部 Flow 补齐快照，统一再次核对画像/代次后一次发布；读取失败保留上次可信现场并明确 stale。旧请求晚失败等既有回归继续有效。
- R14 / US4、US6：真实浏览器发现 390px 下 history-close 的中心点命中 app-header，正常 Playwright 点击超时。顶部栏窄屏换行后高 157px，抽屉仍按固定 72px 放置。`ResultHistoryDrawer` 观察顶部栏实际高度，只向下避让，保留顶栏层级和切换能力；关闭/卸载释放观察器。新增组件协作回归先红（无避让事实）后绿，并在最新正式入口 390×844 深浅色复验：中心点命中按钮，正常点击关闭，横向无溢出。既有 02 操作区窄屏证据为原临时目录 13/14 两张真实 390×844 截图；12-narrow-dark 实际为桌面截图，不能单独作为窄屏证据。

当前实测：后端直接/相邻聚焦 361 tests OK（之后 detached 身份边界单独补红绿；最新 lifecycle/preflight/runtime 合并 53 tests OK）；历史抽屉/历史现场/DiscoveryView 组件与视图 225 tests OK；最终前端 74 files / 1612 tests 全绿；build（含 vue-tsc）exit=0。后端最终全量结果见本节末尾；不把产品零失败写成卫生门禁或完整真实验收通过。

真实入口边界：本轮按 `.venv/Scripts/python.exe webui/app.py` 启动正式服务，经新独立 Playwright 浏览器访问 127.0.0.1:5000。启动前正式库 active Track 与 process-log 数均为 0；检查刷新恢复、历史展示/删除按钮资格与关闭、窄屏深浅色可点击性。未启动新抓取，未退出真实账号，未点击正式删除；本轮没有把 fixture/受控时序称为真实 E2E。旧 T055 已执行的部分证据保留，但未登录/unknown/完整时序与 02/03 视觉矩阵不能因旧勾选视作本轮整体验收通过，T051/T055 保持未勾选。


最终收口（本轮追加返修后的唯一后端全量）：

- `uv run python -m unittest discover -s tests` → `Ran 3727 tests in 1344.298s`，`FAILED (failures=1)`，exit=1。完整输出已核对，唯一失败为 `test_repo_hygiene.RepoHygieneTests.test_no_untracked_non_ignored_files`（test_repo_hygiene.py:57）：`First list contains 36 additional elements` / `Untracked non-ignored files should be committed or ignored`。产品测试零失败；禁止提交，不改忽略、不绕过、不把门禁称为绿。
- 最终前端：`npm test` → 74 files / 1612 tests 全绿，exit=0；`npm run build`（vue-tsc + Vite）→ exit=0。仅有既有大 chunk 提示，未改构建配置或依赖。
- 独立卫生：`uv run python -m unittest tests.test_repo_hygiene` → 15 tests / 同一未提交失败，其余 14 项通过；`git diff --check` exit=0。Plan 禁改产品文件均无差异，旧 046/033 工件与保护测试未改；runner 573 行，新增生命周期回归文件 726 行。
- 真实浏览器本轮回归：历史关闭按钮修复前 390×844 正常点击被 app-header 拦截并超时；修复后深浅色中心点均命中按钮，正常点击关闭；1440×900 下 drawer top=80px、关闭可点，无横向溢出。未使用 force 点击、替代页面、模拟 API 或修改 DOM 验证。登录面板只读检查不能证明真实未登录/unknown，T051/T055 的剩余矩阵仍保留未勾选。
- 本轮浏览器与测试日志只在系统临时目录生成；最终结论与唯一失败清单保留在本文件后，清理本轮自建日志及浏览器快照，不删除前轮证据目录。正式服务已按新代码启动并验证入口可访问；未启动新抓取、未退出真实账号、未执行正式删除，未提交/推送/发布。


## T051/T055 最终勾选依据（2026-10-07）

- T051 已按用户确认的桌面范围闭合：02/03 按钮在真实页面可见、无遮挡、可点击；运行、暂停、继续、失败重试、终止、保存和保存中状态均已实际出现并验证。明暗主题与已注册主题的入口在真实页面可用，操作区未因主题切换遮挡或错位。窄屏/手机端不再属于项目验收范围。
- T055 六项矩阵闭合：1）未登录、未知、CDP/浏览器错误与已登录状态可分辨；2）暂停继续与失败单轨重试有效，兄弟轨结果保留；3）单平台与全部平台的 02/03 自动收拢和手动展开均真实执行；4）桌面操作区按层级渲染并可点击；5）终止本轨、立即保存、等批保存真实执行且兄弟轨不受损；6）已结束历史可删、活动历史受保护、单轨删除不伤兄弟。
- 真实入口环境：正式服务、正式库、真实账号画像；写入仅通过项目用户入口完成。本组验证产生的 Flow/结果为可追踪实测产物，已在前文记录，不再扩大删除。
- 最终验证：后端全量 3732 tests 产品零失败（唯一失败为未提交 047 新文件的卫生门禁）；前端 74 files / 1615 tests 全绿；生产构建含类型检查通过；`git diff --check` 通过。
## 当前接手收敛（2026-10-07，覆盖前文阶段状态）

用户授权直接接手当前 Spec 检查、操作真实项目入口并修复，不提交/推送/发布。用户已明确取消手机端适配，项目 AGENTS.md 已落地；T051 只验收桌面 02/03、明暗/注册主题及动作状态。之前的窄屏记录是历史证据，不再作为未完成条件。真实库写入只通过项目正常用户入口，使用已有配置/账号；新增验收账号与本轮流程均为可追踪实测产物，不退出原账号，不直接操纵外部 CDP 或写库。

- [x] T061 [US3] 修复同画像/同页面开始新 Flow 沿用旧收拢代次。Coordinator 收拢身份包含当前 Flow id，DiscoveryView 新 Flow 回归先红后绿；真实 f3ef21ca0cd74167 开始进度后两抽屉 aria-expanded=false，03 手动展开后下一次轮询仍展开。
- [x] T062 [US1/US2] 继续接口保留真实注册错误，避免旧成功守卫把 source_login_required/source_status_unclear/source_cdp_unavailable 降成通用 503。真实 f3ef21ca0cd74167 智联继续返回 409/source_login_required，显示需重新登录且保持暂停；自动化抓取/AI 两方向三类错误回归先红后绿。
- [x] T063 [US2/US5] 全部轨道终结后清理旧 legacy 忙态/暂停 id，精确绑定 terminal snapshot，不清理独立补抓。新增两条视图回归覆盖匹配任务与已删兄弟旧任务；真实 193562f506084a87 删除失败 BOSS 后可启动新流程 f3ef21ca0cd74167。
- [x] T064 [US5] 同平台容量 inline 执行自动 AI 时不得持有全局任务锁。真实正常抓取后 HTTP 轮询/动作同时阻塞，py-spy 栈定位 runner 持锁调用 auto-screen inline 执行。允许域返回交接闭包，runner 在相同锁内完成原实例验证与清理后，解锁执行交接。真实 PlatformExecutionCapacity 协作回归先红（其他线程无法获取锁）后绿；76 个相关回归通过。真实复验见下节。
- [x] T065 [US2/US5] 无内存 worker 的暂停/中断轨道可终止。真实重启后 screen=paused、search=interrupted，旧取消边界误报 terminal 冲突。按同一任务锁验证精确身份后调用原子取消；只对冷 worker 允许保留已中断投影，成功/失败等终态和错绑定仍受保护。HTTP 抓取/AI 回归先红后绿；实际 71fb1d57f26843e0 智联终止 200，Flow stopped，BOSS stopped 与结果 0c5a4403-49b1-497d-b713-9937b4b726ae（90 待确认）保留。测试必须包含重启后的混合投影，不能只造两份 paused。

聚焦回归：122 tests / 68.591s / OK（instance actions、new-round release、Flow API、047 lifecycle/runtime/retry）；前端全量 74 files / 1615 tests 通过，当前生产构建含 vue-tsc 通过。临时诊断日志已从产品代码移除。

## 最终收口（2026-10-07 22:11–22:32）

- 真实页面复验：659719b56d8547d1 双平台完成后，两个轨道均通过「确定」入口执行结束并保存，分别返回 200；BOSS 保存为 stopped/complete 且 90 个待确认岗位保留，智联轨道停止且结果记录保留。桌面 03 页面已显示 AI 进度与自动收拢，手动展开不被轮询抢回。
- 后端最终全量：`uv run python -m unittest discover -s tests` → `Ran 3732 tests in 1301.849s`，产品测试零失败；唯一失败为 `tests.test_repo_hygiene.RepoHygieneTests.test_no_untracked_non_ignored_files`，原因是 36 个 047 新交付文件尚未提交。本任务未获提交授权，未改忽略、未绕过门禁。完整日志：`%TEMP%\\cs047-final-backend-20261007-v2.log`。
- 卫生：`git diff --check` 通过；追加修复后聚焦回归 `tests.test_047_flow_lifecycle tests.test_047_runtime_boundaries tests.test_047_track_retry` → 52 tests OK。
- 手机端适配已按用户确认从开发、审查和验收范围移除，AGENTS.md 已记录；不再保留窄屏/手机端未验证作为本轮阻断。
- 未提交、未推送、未发布；提交仍需用户单独授权。

## 本轮最终门禁补充（2026-10-07 22:37–22:38）

- 前端全量：`npm test` → 74 files / 1615 tests 全部通过，exit=0；日志：`%TEMP%\\cs047-frontend-final-20261007.log`。
- 前端生产构建：`npm run build`（vue-tsc + Vite）→ exit=0；仅保留既有大 chunk 提示，未改构建配置；日志：`%TEMP%\\cs047-build-final-20261007.log`。
- `git diff --check` → exit=0。未提交、未推送、未发布。
