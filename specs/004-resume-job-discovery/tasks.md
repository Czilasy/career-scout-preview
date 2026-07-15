# Tasks: 简历驱动的岗位发现

**Feature**: 004-resume-job-discovery
**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data Model**: [data-model.md](data-model.md)
**Generated**: 2026-07-14
**Revised**: 2026-07-15（真实 AI 与发现运行闭环）

## Implementation Strategy

TDD 优先：每个任务先写失败测试（RED），再最小实现（GREEN），最后提交。所有任务按依赖顺序推进；标记 `[P]` 的任务可与同阶段其他 `[P]` 任务并行。迁移验收基线为 schema 10，目标工作树为累加迁移 011–013 后的 schema 13，任何复核都不得重写旧表。旧 workbench/screening API 与历史数据保持只读兼容。

T001–T095 是原始全功能实施清单，其复选框从未与真实工作树收敛。不得根据历史测试总数批量勾选，也不得把未勾选自动解释为代码不存在。当前实施入口是 T096：先逐项核验证据，再执行本次新增的运行时闭环任务。最终完成门已移动到 T134。

候选人合同 T010/T011 描述的是原始 v1 基线；本轮 T102–T107 以已批准的 v2 exact-quote 合同取代模型直接提供 locator 的假设。收敛 T096 时应把 v1 已完成部分与 v2 待实施部分分别记录，不得把旧测试通过当作 v2 完成。

## Dependencies

```text
Phase 1 (Setup) → Phase 2 (Foundational: migrations + contracts + tri-state rules)
Phase 2 → Phase 3 (US1: resume→evidence→directions→confirmation)
Phase 3 → Phase 4 (US2: search plan→fetch→evaluate→portfolio)
Phase 2 + Phase 3 → Phase 5 (US5: privacy/deletion, cross-cutting)
Phase 4 + Phase 5 → Phase 6 (US3: feedback/long-term state)
Phase 4 → Phase 7 (US4: run state/resume/partial/AI degrade)
Phase 3-7 backend done → Phase 8 (unified 4-step frontend)
Phase 8 → Phase 9 (verification gates: migration/integration/golden/browser/real E2E)
Phase 9 evidence audit → Phase 10 (runtime-closure foundation)
Phase 10 → Phase 11 (US1 real candidate analysis)
Phase 10 + Phase 11 → Phase 12 (US2 real assessment and run dispatch)
Phase 12 → Phase 13 (US4 cancellation/resume/failure traceability)
Phase 11-13 → Phase 14 (US3/US5 HTTP and privacy closure)
Phase 14 → Phase 15 (live provider + real E2E + final verification)
```

独立测试标准：每个用户故事可在不依赖其他故事运行时态的情况下独立验证其契约与单元行为。

---

## Phase 1: Setup

- [X] T001 创建 discovery 测试夹具目录与脱敏样本 in tests/fixtures/discovery/（README.md + 至少 7 类脱敏简历文本样本：single-path、cross-family、intent-unclear、long-tenure-low-project、junior、multi-industry-gap、no-salary-city；配套 directions/jobs 人工标注 JSON）
- [X] T002 [P] 新建空模块 webui/candidate.py、webui/discovery.py、webui/discovery_runner.py、webui/source.py（仅模块文档字符串与占位，不引入业务逻辑）
- [X] T003 [P] 新建测试骨架 tests/test_candidate.py、tests/test_discovery.py、tests/test_discovery_contracts.py、tests/test_discovery_store.py、tests/test_discovery_integration.py、tests/test_discovery_frontend.py、tests/test_discovery_browser.py、tests/test_boss_discovery_source.py（各含 unittest TestCase 空壳，可被 discover 收集）

## Phase 2: Foundational

- [X] T004 编写失败测试：迁移 011 创建 candidate_analyses/resume_evidence/career_directions/direction_evidence 表且幂等 in tests/test_discovery_store.py（从 schema-version-10 fixture 升级，断言表结构、外键级联、唯一约束、旧表行数不变）
- [X] T005 实现 migration 011 in webui/store.py（_migration_011：建四表，direction_evidence 复合主键且限定同 analysis，INSERT schema_migrations version=11；_migrate 追加 if current<11）
- [X] T006 编写失败测试：迁移 012 创建 direction_confirmations/confirmation_directions/discovery_runs/discovery_run_events/search_plans/search_plan_items 表且幂等 in tests/test_discovery_store.py（断言 input_hash 唯一、终态不可逆、确认版本不可更新）
- [X] T007 实现 migration 012 in webui/store.py（_migration_012：建六表，discovery_runs.input_hash 不可变，search_plan_items.input_hash UNIQUE，_migrate 追加 if current<12）
- [X] T008 编写失败测试：迁移 013 创建 discovery_job_snapshots/job_direction_assessments/discovery_feedback 表且幂等 in tests/test_discovery_store.py（断言 (run_id,job_id) 唯一、(run_id,snapshot_id,direction_id) 唯一、分类优先级）
- [X] T009 实现 migration 013 in webui/store.py（_migration_013：建三表，_migrate 追加 if current<13；schema_version 升至 13）
- [X] T010 [P] 编写失败测试：候选人分析 AI 输出合同 v1 校验 in tests/test_candidate.py（valid/缺字段/越界 confidence/证据引用不存在/敏感证据/同义方向合并/超过 5 方向/超过 3 搜索词）
- [X] T011 [P] 实现候选人分析合同校验 in webui/candidate.py（validate_candidate_analysis(data, resume_text)：校验 summary/evidence/unknowns/directions；证据 locator 必须落在 resume_text；safe_excerpt 过敏感字段；方向证据引用解析；归并同义方向；限 5 方向 3 词）
- [X] T012 [P] 编写失败测试：岗位方向评估 AI 输出合同 v1 校验 in tests/test_discovery.py（dimensions 数值 0-100、布尔拒绝、候选证据引用属本方向、岗位证据引用解析、proposed_band 仅为建议、缺维度→needs_review）
- [X] T013 [P] 实现岗位评估合同校验 in webui/semantic.py（validate_job_assessment(data, analysis_evidence_ids, direction_evidence_ids, snapshot_fields)：复用 _score 模式，校验四维度与证据引用，输出经净化的评估提案）
- [X] T014 编写失败测试：三态硬规则 pass/violation/unknown in tests/test_screening.py（字段缺失→unknown 且不 violation；明确不匹配→violation；明确匹配→pass；unknown 不得进 high_match）
- [X] T015 重构 screening.py 暴露三态硬规则 in webui/screening.py（新增 verify_hard_rules_tri_state(job, hard_constraints) -> {outcome, checks}，outcome∈pass/violation/unknown；保留旧 verify_hard_rules_detailed 兼容旧 screening 流程）
- [X] T016 [P] 编写失败测试：安全错误码与失败信封契约 in tests/test_discovery_contracts.py（ai_timeout/ai_auth_failed/ai_network_error/ai_invalid_output/ai_uncertain/evidence_reference_invalid/input_incomplete/verification_error 映射 retryable/stage）
- [X] T017 [P] 实现统一错误信封助手 in webui/discovery.py（DiscoveryError(error_code, stage, retryable, user_message)；to_envelope() 返回 openapi Error schema；AISecurityError 映射）

## Phase 3: User Story 1 — 从简历获得方向并一次确认

故事目标：上传简历→安全提取证据→生成候选人模型与多方向→一次轻量确认冻结意愿。
独立测试：使用脱敏简历完成上传、证据提取、方向生成、一次确认，不执行岗位搜索即可验证。

- [X] T018 [US1] 编写失败测试：TaskStore 新增分析/证据/方向/确认/方向证据 CRUD in tests/test_discovery_store.py（创建分析、写证据、写方向、链方向证据、创建确认版本、不可变性、级联删除）
- [X] T019 [US1] 实现 store 层分析/证据/方向/确认持久化 in webui/store.py（create_analysis/get_analysis/list_analyses、add_evidence/list_evidence、add_direction/list_directions/link_direction_evidence、create_confirmation/get_confirmation/list_confirmations；均返回 dict，敏感字段不外泄）
- [X] T020 [US1] 编写失败测试：resume 证据规范化与去重 in tests/test_candidate.py（同能力多来源合并保留多 locator、敏感标识排除、assertion_type 区分 explicit/inferred、unknowns 不伪造为 evidence）
- [X] T021 [US1] 实现证据规范化与去重 in webui/candidate.py（normalize_evidence(raw_evidence, resume_text)：去重合并、敏感字段 redact、locator 校验、assertion_type 校验）
- [X] T022 [US1] 编写失败测试：方向归并、证据链接、默认启用门控 in tests/test_candidate.py（同义岗位名归一、证据不足不默认启用、1-5 方向上限、每个默认启用方向至少 1 证据链接）
- [X] T023 [US1] 实现方向领域逻辑 in webui/candidate.py（merge_directions：同义名归并保留搜索词并集；enforce_direction_policy：置信度门控默认启用、上限 5、证据链接校验）
- [X] T024 [US1] 编写失败测试：analyze_resume 编排（consent 校验、空正文阻断、AI 不可用降级、失败码、版本递增）in tests/test_discovery_integration.py（fake AI 返回合法/非法合同）
- [X] T025 [US1] 实现 analyze_resume 应用服务 in webui/discovery.py（analyze_resume(resume_id, consent)：校验 consent→读 resume_text→空正文阻断→调 AIProvider→校验合同→normalize_evidence→merge_directions→持久化分析+证据+方向；AISecurityError→failed+failure_code；不持久化原始响应）
- [X] T026 [US1] 编写失败测试：confirm_directions 冻结不可变版本 in tests/test_discovery.py（仅 ready 分析可确认、至少 1 方向、城市/薪资缺失不补造硬约束、编辑创建新版本不覆盖旧版、user_added 方向标记来源）
- [X] T027 [US1] 实现 confirm_directions 应用服务 in webui/discovery.py（confirm_directions(analysis_id, enabled_direction_ids, hard_constraints, soft_preferences, safe_limits, user_directions)：校验分析 ready、方向属本分析、硬约束仅用户明确项、创建不可变 confirmation 版本）
- [X] T028 [US1] 编写失败测试：分析/确认 HTTP 契约 in tests/test_discovery_contracts.py（POST /api/discovery/analyses 202、GET /api/discovery/analyses/{id} 200、POST /api/discovery/confirmations 201；错误信封；隐私字段不返回）— 由 T108 的分析 HTTP 契约测试及本轮补充的 confirmation 201/越界方向测试覆盖
- [X] T029 [US1] 实现 discovery 分析与确认路由 in webui/app.py（POST /api/discovery/analyses 上传+ai_consent→202；GET /api/discovery/analyses/{id}；POST /api/discovery/analyses/{id}/retry；POST /api/discovery/confirmations；沿用 before_request token 保护）— 由 T109 运行时接线及 T028 HTTP 契约验证覆盖
- [X] T030 [US1] 编写失败测试：隐私同意门控（未同意不发送远程 AI）in tests/test_discovery_integration.py（consent=False→仅本地校验不调 AI；consent=True→调 fake AI；日志不含简历正文）

## Phase 4: User Story 2 — 自动发现并解释多方向岗位

故事目标：确认后自动编译多路搜索计划→抓真实列表+详情→硬约束+语义评估→交付按方向组织可解释岗位组合。
独立测试：用已确认模型与方向快照独立执行搜索计划、详情获取、匹配、结果整理，无需手填关键词。

- [X] T031 [US2] 编写失败测试：搜索计划编译 in tests/test_discovery.py（每方向≤3 词、全局去重≤12 项、合并共享词保留多方向归属、每方向至少 1 项、input_hash 唯一、硬约束进入边界）
- [X] T032 [US2] 实现搜索计划编译器 in webui/discovery.py（compile_search_plan(confirmation)：归一化搜索词、去重合并、分配 detail_budget、生成 search_plan_items 含 input_hash；失败指向受影响方向）
- [X] T033 [US2] 编写失败测试：store 层 run/plan/snapshot/assessment CRUD in tests/test_discovery_store.py（创建 run、plan、items、snapshot、assessment；input_hash 不可变；终态不可逆；(run,snapshot,direction) 唯一）
- [X] T034 [US2] 实现 store 层 run/plan/snapshot/assessment 持久化 in webui/store.py（create_discovery_run/get_run/list_runs、append_run_event、create_search_plan/get_plan/update_plan_item、save_job_snapshot/get_snapshot、create_assessment/get_assessment/list_assessments；计数器单调更新）
- [X] T035 [US2] 编写失败测试：JobSource 适配器（fake source）list/detail 隔离与 input_hash 校验 in tests/test_boss_discovery_source.py（单查询失败不阻断、detail 失败隔离、artifact input_hash 不符拒绝导入、返回 typed outcome）
- [X] T036 [US2] 实现 JobSource 适配器 in webui/source.py（BossCdpSource：fetch_list(plan_item) 调 boss_cdp_raw 子进程并校验 input_hash；fetch_detail(job) 隔离失败；返回 SourceOutcome(list_jobs/detail/failed_code)；类型化结果不泄漏异常文本）
- [X] T037 [US2] 编写失败测试：run 编排阶段流转 in tests/test_discovery_integration.py（created→planning→fetching_lists→fetching_details→evaluating→assembling→succeeded；阶段事件追加；计数器更新；fake source + fake AI + temp SQLite）
- [X] T038 [US2] 实现发现运行编排器 in webui/discovery_runner.py（DiscoveryRunner：阶段驱动循环；每页落库 checkpoint；detail 轮询按方向覆盖+去重；单岗位失败隔离；部分成功计算；不覆盖已完成评估）
- [X] T039 [US2] 编写失败测试：岗位详情快照与完整性 in tests/test_discovery.py（complete/partial/unavailable/expired；missing_fields 记录；source_status；content_hash；只标题不进 high_match）
- [X] T040 [US2] 实现详情快照构建 in webui/source.py（build_snapshot(job, detail)：完整性判定、missing_fields 收集、source_status 判定、content_hash；仅标题→completeness=unavailable）
- [X] T041 [US2] 编写失败测试：硬约束三态+语义评估→分类 in tests/test_discovery.py（violation→not_suitable；unknown/缺详情/低置信→needs_review；valid→high/adjacent/growth；AI 不可用硬规则过→needs_review 非 high；多方向独立评估）
- [X] T042 [US2] 实现评估与分类策略 in webui/discovery.py（assess_job_direction(snapshot, direction, ai_proposal)：三态硬规则→AI 合同校验→EvaluationPolicy 分类；policy_version 持久化；candidate_evidence_ids/job_evidence 校验）
- [X] T043 [US2] 编写失败测试：结果组合与多样性 in tests/test_discovery.py（high 需硬约束过+详情充分+强证据；相邻说明可迁移；发展型说明缺口；待确认/不适合分离；至少两方向覆盖；同公司去重；无结果方向分原因）— 6 项定向 RED→GREEN；另由 RunResultsHttpContractTests 验证 HTTP 结果路径同样执行 high 降级守卫
- [X] T044 [US2] 实现结果组合构建 in webui/discovery.py（build_portfolio(run_id)：按方向+分类组织；primary_assessment 选择；alternate 方向标签；安全解释关联候选人证据+岗位证据；无结果方向原因编码）
- [X] T045 [US2] 编写失败测试：安全解释生成（不泄漏简历正文/模型原始响应）in tests/test_discovery.py（explanation 仅含 safe_excerpt+证据 id；无整段简历；无模型 raw 文本）
- [X] T046 [US2] 实现安全解释生成 in webui/discovery.py（build_safe_explanation(assessment)：仅引用 evidence id + safe_excerpt；redact 敏感；无 raw model 文本）
- [X] T047 [US2] 编写失败测试：run/results/retry HTTP 契约 in tests/test_discovery_contracts.py（POST /api/discovery/runs 202；GET /api/discovery/runs/{id}；GET /api/discovery/runs/{id}/results 分页+counts；POST retry 202；JobResult schema）— 由 T118 的真实持久状态测试及本轮 RunResultsHttpContractTests 复核覆盖
- [X] T048 [US2] 实现 discovery run/results 路由 in webui/app.py（POST /api/discovery/runs；GET runs/{id}；GET runs/{id}/results 支持 category/direction_id/after/limit；POST runs/{id}/jobs/{job_id}/retry）— 由 T119 运行时接线及 T047 HTTP 契约验证覆盖

## Phase 5: User Story 5 — 隐私边界（跨切，与 US2 并行）

故事目标：简历发送前告知接收方用途；敏感内容不进日志/结果/可分享页；删除简历清理派生数据。
独立测试：含虚构敏感标记的脱敏简历执行同意/拒绝/分析/查看/删除，验证泄漏数为 0。

- [X] T049 [US5] 编写失败测试：敏感字段 redact 与日志无简历正文 in tests/test_discovery_integration.py（联系方式/证件号/住址不进 evidence/结果/事件；普通日志无简历正文）
- [X] T050 [US5] 实现敏感字段 redact 与安全日志 in webui/candidate.py + webui/discovery.py（SENSITIVE_PATTERNS 正则；redact_pii(text)；run_event payload 仅 counts/ids/codes；不记录 prompt/response）
- [X] T051 [US5] 编写失败测试：删除简历级联清理派生证据/方向/解释但保留岗位与显式反馈 in tests/test_discovery_store.py（删 resume→analyses/evidence/directions 删除；jobs/profile_jobs/feedback_events 保留；历史 run 解释标记 unavailable）
- [X] T052 [US5] 实现删除级联 in webui/store.py + webui/resume.py（delete_resume_with_cascade：删文件+文本+证据+方向+方向证据+解释载荷；标记历史 run 的 evidence_backed_explanation 为 unavailable；保留 canonical jobs 与显式 feedback）
- [X] T053 [US5] 编写失败测试：结果可追溯（简历/确认/运行标识）in tests/test_discovery_contracts.py（JobResult 含 run_id/confirmation_id；Analysis 含 resume_id；可识别来源链）
- [X] T054 [US5] 实现结果追溯标识 in webui/discovery.py（portfolio 与 explanation 携带 resume_id/analysis_id/confirmation_id/run_id；policy_version 可查）

## Phase 6: User Story 3 — 反馈改善下一次发现

故事目标：结构化反馈+撤销+长期状态+再次发现使用最新确认。
独立测试：固定结果集执行兴趣/不兴趣/方向反馈/撤销/再次发现，验证影响范围正确且历史不改写。

- [X] T055 [US3] 编写失败测试：结构化反馈 CRUD 与 scope in tests/test_discovery_store.py（target_type job/direction/assessment/constraint；action 枚举；scope 默认 exact_job；撤销置 revoked_at；不改写历史 run snapshot）
- [X] T056 [US3] 实现结构化反馈持久化 in webui/store.py（create_discovery_feedback/get_feedback/revoke_discovery_feedback/list_discovery_feedback；与旧 feedback_events 共存不冲突）
- [X] T057 [US3] 编写失败测试：反馈影响范围（不兴趣默认仅该岗位；方向反馈影响后续运行不改历史）in tests/test_discovery_integration.py（job not_interested 不扩公司；direction_disable 影响新 run；撤销恢复）
- [X] T058 [US3] 实现反馈应用与再次发现 in webui/discovery.py（apply_feedback_to_next_run：方向反馈调整新 run 启用方向；岗位反馈调整排序/排除；历史 run 快照不可变；记录偏好变化可见可撤销）
- [X] T059 [US3] 编写失败测试：反馈 HTTP 契约 in tests/test_discovery_contracts.py（POST /api/discovery/feedback 201 返回 feedback_id+effective_scope；POST /api/discovery/feedback/{id}/revoke 200）— 由 T127 的真实 feedback/revoke HTTP 契约测试覆盖
- [X] T060 [US3] 实现反馈路由 in webui/app.py（POST /api/discovery/feedback；POST /api/discovery/feedback/{feedback_id}/revoke；沿用 token 保护）
- [X] T061 [US3] 编写失败测试：兴趣/垃圾桶与旧持久区兼容 in tests/test_discovery_store.py（discovery 感兴趣复用 profile_jobs.status=interested；垃圾桶复用 screening_trash_records；跨 run 可见）

## Phase 7: User Story 4 — 可靠完成或恢复长时间运行

故事目标：运行进度可见、切换页面不丢、取消真实阻止、中断可恢复、AI 降级明确。
独立测试：固定 run 模拟页面切换/单岗位失败/AI 不可用/取消/恢复，验证已保存不丢不重。

- [X] T062 [US4] 编写失败测试：cancel 真实阻止后续步骤且保留已保存 in tests/test_discovery_integration.py（cancel_requested_at 置位；未开始 plan_item→cancelled；已完成 snapshot/assessment 保留）
- [X] T063 [US4] 实现 cancel 语义 in webui/discovery_runner.py（cancel_run：置 cancel_requested_at；运行循环每阶段前检查；未开始工作单元→cancelled；已终端不变）
- [X] T064 [US4] 编写失败测试：中断→interrupted 且 checkpoint 保留 in tests/test_discovery_integration.py（模拟进程中断；重启后 run→interrupted；plan_item page_cursor 保留；snapshot/assessment 不丢）
- [X] T065 [US4] 实现中断与恢复 in webui/discovery_runner.py + webui/store.py（mark_interrupted_on_restart：迁移已扩展至 discovery_runs；resume_run：从最近 saved stage 恢复；input_hash 校验；不重复导入已完成项）
- [X] T066 [US4] 编写失败测试：部分成功计算与无结果仍可成功 in tests/test_discovery.py（有可用结果+部分分支失败→partial；全部分支正常完成但无合格岗位→succeeded+无结果原因；存在阻断项不可 succeeded）
- [X] T067 [US4] 实现完成判定 in webui/discovery.py（calculate_run_completion：所有必要工作单元终端+无阻断→succeeded；有可用结果+剩余不可完成→partial；无可用结果且全阻断→failed）
- [X] T068 [US4] 编写失败测试：AI 不可用降级（不宣称新方向分析；硬规则过但未评估→needs_review 非 high）in tests/test_discovery_integration.py（ai_available=False：analyze_resume→failed ai_unavailable；评估→needs_review）
- [X] T069 [US4] 实现 AI 降级路径 in webui/discovery.py + webui/discovery_runner.py（AI 不可用时 analyze_resume 不伪装 ready；评估缺失→needs_review；可只重试未完成项不重跑成功步骤）
- [X] T070 [US4] 编写失败测试：cancel/resume HTTP 契约 in tests/test_discovery_contracts.py（POST /api/discovery/runs/{id}/cancel 202；POST /api/discovery/runs/{id}/resume 202；终态 409）— 由 T125 及本轮 active cancel/interrupted resume HTTP 断言覆盖
- [X] T071 [US4] 实现 cancel/resume 路由 in webui/app.py（POST runs/{id}/cancel；POST runs/{id}/resume；状态冲突→409）— 由 T124 的 runtime 委托实现及 T070 HTTP 契约验证覆盖

## Phase 8: 统一四步前端

- [X] T072 编写失败测试：默认首页仅四步主线 in tests/test_discovery_frontend.py（GET /：上传→确认→进度→结果；关键词/页数/来源编码在高级设置折叠；旧 workbench/screening 为兼容入口）
- [X] T073 实现统一四步前端 in webui/index.html（discovery home：上传简历→查看系统理解（事实/推断/未知/用户确认区分+方向列表+缺口+硬约束待确认）→启动发现查看阶段进度→按方向查看结果；高级设置折叠区；复用设计 token）— 由 T126 状态恢复增强及本轮桌面/窄屏 84 项前端浏览器验证覆盖
- [X] T074 [P] 编写失败测试：方向确认页交互 in tests/test_discovery_frontend.py（启用/关闭方向；修改城市/最低薪资；硬约束与软偏好区分；开始寻找岗位冻结）
- [X] T075 [P] 实现方向确认页交互 in webui/index.html（renderAnalysis(analysis)：渲染证据摘要+方向卡片+缺口+未知项；confirmDirections()：收集启用方向+硬约束→POST confirmations）
- [X] T076 [P] 编写失败测试：运行进度与结果视图 in tests/test_discovery_frontend.py（阶段/计数/最后更新时间；方向+分类切换；推荐依据+缺口+详情链接+反馈；空/加载/成功/部分/失败/待确认/无结果状态）
- [X] T077 [P] 实现进度与结果视图 in webui/index.html（renderRunProgress(run)；renderResults(items,counts)：方向选择器+分类控件；createDiscoveryCard(job)：依据/缺口/完整性/详情入口/反馈按钮）
- [X] T078 [P] 编写失败测试：反馈与撤销交互 in tests/test_discovery_frontend.py（感兴趣/不感兴趣/垃圾桶/方向反馈/撤销；scope 默认 exact_job；偏好变化可见）— 静态前端契约 + 8 项 FeedbackHttpContractTests + 真实 HTTP Playwright 交互覆盖
- [X] T079 [P] 实现反馈交互 in webui/index.html（markDiscoveryInterest/reject/restore；recordDirectionFeedback；revokeFeedback；renderPreferenceChanges）— discovery 结果页已实现兴趣/垃圾桶/恢复、结构化拒绝原因、方向反馈、撤销及偏好变化展示；后端 GET/POST/revoke 契约同步实现
- [X] T080 编写失败测试：旧流程兼容入口可见且不阻断默认 in tests/test_discovery_frontend.py（旧 workbench/screening 链接存在；历史搜索/筛选标签；兴趣/垃圾桶跨 run 可见）
- [X] T081 实现旧流程兼容入口 in webui/index.html（兼容入口链接；历史数据标签“历史搜索/历史筛选”；不维护两套默认简历状态）

## Phase 9: Polish & 验证门

- [X] T082 [P] 编写迁移验收测试 in tests/test_discovery_store.py（schema-10 fixture→13：旧行数不变、幂等重启、FK 拒绝跨 analysis 证据链接、run 重启→interrupted、简历删除级联）
- [X] T083 [P] 运行迁移验收：python -m unittest tests.test_discovery_store -v，并记录结果 in specs/004-resume-job-discovery/validation.md
- [X] T084 [P] 编写集成测试 in tests/test_discovery_integration.py（fake AI+fake JobSource+temp SQLite 全管道：取消/部分成功/重启恢复/AI 不可用/隐私不泄漏）
- [X] T085 [P] 运行集成测试：python -m unittest tests.test_discovery_integration -v，并记录结果 in specs/004-resume-job-discovery/validation.md
- [X] T086 [P] 编写黄金样本评估脚本 in tests/fixtures/discovery/evaluate.py（计算方向接受率/Precision@20/召回率/硬约束违规率/多方向覆盖率/解释忠实度；校准 adjacent/growth 阈值生成 policy_version）
- [X] T087 [P] 运行黄金样本评估：python tests/fixtures/discovery/evaluate.py，记录指标与是否达 SC-003–SC-009
- [X] T088 [P] 编写浏览器渲染测试 in tests/test_discovery_browser.py（1366×768 与 720px：空/加载/成功/部分/失败/待确认/无结果状态；无横向溢出；主操作可触达；焦点态）
- [X] T089 [P] 运行浏览器渲染验证（启动后端，playwright/手动核验桌面与窄屏），记录证据 in specs/004-resume-job-discovery/validation.md
- [X] T090 [P] 运行真实来源 smoke：python scripts/boss_cdp_raw.py --check 与 --smoke-test，仅证明连通性
- [X] T091 编写受控真实 BOSS E2E 脚本 in tests/fixtures/discovery/e2e_real_boss.py（脱敏简历→分析→≥2 方向确认→多路搜索→多页列表去重→详情→评估→反馈→中断/恢复；记录输入边界/写入范围/计数/中断节点）
- [X] T092 运行受控真实 BOSS E2E（如真实 AI 凭据/BOSS 登录态可用），记录结果；不可用则明确记录阻塞节点与已完成证据 in specs/004-resume-job-discovery/validation.md — 由 T133 最终真实 HTTP E2E 覆盖，source=6/detail=1/evaluated=2，feedback/cancel/resume 门通过
- [X] T093 [P] 运行全量自动化回归：python -m unittest discover -s tests -v，并记录总数与耗时 in specs/004-resume-job-discovery/validation.md
- [X] T094 重启受影响后端服务并验证 http://127.0.0.1:5000 可访问，记录进程与访问结果 in specs/004-resume-job-discovery/validation.md
- [X] T095 创建 specs/004-resume-job-discovery/validation.md 记录所有命令、测试数量、结果、环境、证据边界与未验证项
- [X] T096 核对 T001–T095 的代码、测试和 validation 证据并只勾选可由当前命令或明确产物证明完成的任务 in specs/004-resume-job-discovery/tasks.md + specs/004-resume-job-discovery/validation.md（占位测试、fake-only 接缝、被阻塞真实 E2E 一律不得标完成）

## Phase 10: 真实 AI 与任务运行时基础

目标：建立所有用户故事共享的 provider、错误分类和持久化任务运行边界。本阶段阻断 US1/US2/US4 的真实路径。

- [X] T097 [P] 编写失败测试：真实 provider 构建、无 store 依赖、认证/超时/网络/无效输出映射和单次纠正重试 in tests/test_ai.py
- [X] T098 [P] 编写失败测试：运行时提交、dispatch_failed、安全事件和进程重启收敛为 interrupted in tests/test_discovery_integration.py
- [X] T099 实现 DiscoveryAIProvider 公共边界和 feature-safe 错误映射 in webui/ai.py（仅持 endpoint/model/api_key；提供 analyze/assess_job；不得读写 TaskStore；不得泄漏 prompt/response/key）
- [X] T100 实现应用持有的 DiscoveryTaskRuntime 基础设施 in webui/discovery_runner.py（受控 executor、analysis/run future、取消信号、dispatch failure、安全事件、shutdown；SQLite 状态为恢复事实）
- [X] T101 在 create_app 中构造并暴露唯一 DiscoveryTaskRuntime，注入 provider/source factory in webui/app.py（不得在 app.py 内新增平行业务编排器）

## Phase 11: User Story 1 — 真实候选人分析闭环

故事目标：用户对已上传简历明确同意后，真实 provider 生成可验证证据与方向；请求后台执行并可查询 ready 或明确安全失败。
独立测试：使用临时库、真实 DiscoveryAIProvider、mock call_ai 和脱敏 Unicode 简历，从 HTTP 分析入口提交并轮询到 ready；证据位置逐项与规范化正文一致，不执行岗位搜索。

- [X] T102 [P] [US1] 新增 candidate-analysis v2 合法/非法响应夹具（exact quote、重复 quote、Unicode、敏感 quote、未知 evidence_ref）in tests/fixtures/discovery/ai_candidate_v2.json + tests/fixtures/discovery/resume_locator_cases.txt
- [X] T103 [P] [US1] 编写失败测试：规范化文本和 exact-quote 定位拒绝越界、范围内错误切片、重复无法消歧及敏感摘录 in tests/test_candidate.py
- [X] T104 [US1] 实现版本化 canonicalize_resume_text_v2 与 resolve_evidence_quote in webui/candidate.py（Unicode code-point offset；唯一精确匹配；slice 再验证；不得 fuzzy 猜测）
- [X] T105 [US1] 编写失败测试：DiscoveryAIProvider.analyze 构造 v2 prompt、忽略模型 offset、生成程序 locator、一次纠正重试且不返回部分 ready in tests/test_ai.py
- [X] T106 [US1] 实现 DiscoveryAIProvider.analyze v2 和本地 locator enrichment in webui/ai.py（调用 call_ai；返回最终候选人合同；不持久化原始响应）
- [X] T107 [US1] 加强最终候选人合同校验，强制 safe_excerpt 与 canonical slice 对应并保留引用完整性 in webui/candidate.py + tests/test_candidate.py
- [X] T108 [US1] 编写失败 HTTP 契约测试：POST /api/discovery/analyses 绑定 resume_id+ai_consent，已配置同意路径不得 NameError/500，状态可轮询，retry 新建版本 in tests/test_discovery_contracts.py
- [X] T109 [US1] 将分析入口改为创建 queued attempt 后提交 DiscoveryTaskRuntime，并映射 provider 安全失败 in webui/app.py + webui/discovery.py + webui/discovery_runner.py
- [X] T110 [US1] 更新默认前端分析请求显式提交 resume_id、轮询 queued/analyzing/ready/failed 并展示安全失败 in webui/index.html
- [X] T111 [US1] 编写 US1 组合集成测试：app→runtime→real provider(mock transport)→candidate validator→store→confirmation，覆盖 consent=false 不外发和日志无正文 in tests/test_discovery_integration.py

## Phase 12: User Story 2 — 真实岗位评估与发现运行调度

故事目标：确认方向后，运行从用户入口真实进入 planning 并完成搜索、详情、按方向评估和结果整理；岗位评估获得可解释候选人证据。
独立测试：使用 ready analysis、确认版本、real provider(mock transport)、fake JobSource 和临时库，从 HTTP 创建 run，轮询到 succeeded/partial，并验证每个 assessment 只引用本方向和本 snapshot 的证据。

- [X] T112 [P] [US2] 新增 job-assessment v1 输入/输出夹具，覆盖完整证据视图、未知候选证据、未知岗位字段和 gaps 对象 in tests/fixtures/discovery/ai_job_assessment_v1.json
- [X] T113 [P] [US2] 编写失败测试：DiscoveryAIProvider.assess_job 的四维 prompt、证据最小化、字段引用和安全错误映射 in tests/test_ai.py
- [X] T114 [US2] 实现 DiscoveryAIProvider.assess_job v1 in webui/ai.py（候选 summary+direction+linked evidence+snapshot fields；不得发送全简历或无关证据）
- [X] T115 [US2] 编写失败测试：runner assessment view 含方向 type/rationale/gaps、linked evidence value/excerpt/assertion_type，且拒绝跨方向/跨分析引用 in tests/test_discovery_integration.py
- [X] T116 [US2] 在 runner 评估边界加载并构造完整脱敏 assessment view in webui/discovery_runner.py + webui/store.py
- [X] T117 [US2] 持久化每岗位 ai_auth_failed/ai_timeout/ai_network_error/ai_invalid_output/ai_uncertain/evidence_reference_invalid，失败岗位 needs_review 且其他岗位继续 in webui/discovery_runner.py + webui/store.py
- [X] T118 [US2] 编写失败 HTTP 测试：POST /api/discovery/runs 后 5 秒内进入 planning 或明确 dispatch_failed，results/retry 使用真实持久状态 in tests/test_discovery_contracts.py
- [X] T119 [US2] 将 run 创建和单岗位 retry 接入 DiscoveryTaskRuntime，删除仅写状态/返回 accepted 的占位行为 in webui/app.py + webui/discovery_runner.py
- [X] T120 [US2] 编写 US2 全管道组合测试：HTTP→runtime→fake source→real provider(mock transport)→store→results，验证阶段事件和成功门 in tests/test_discovery_integration.py

## Phase 13: User Story 4 — 真实取消、恢复与失败追踪

故事目标：取消真实阻止后续工作，恢复真实重新提交未完成项，调度失败和 AI 失败可查询且不会制造成功。
独立测试：运行一个可阻塞 fake source，在列表/详情阶段取消并断言后续调用数不增长；模拟重启后从 HTTP 恢复，已完成项不重复且 unfinished work 被重新提交。

- [X] T121 [P] [US4] 编写失败运行时测试：cancel_requested 先持久化、取消后不启动新 query/detail/assessment、已完成结果保留 in tests/test_discovery_integration.py
- [X] T122 [P] [US4] 编写失败运行时测试：interrupted/partial resume 校验 input_hash 并真实 resubmit，直接状态改写不算恢复 in tests/test_discovery_integration.py
- [X] T123 [US4] 实现 DiscoveryTaskRuntime.cancel_run/resume_run 和 worker cancellation checkpoints in webui/discovery_runner.py
- [X] T124 [US4] 将 cancel/resume HTTP 路由委托给 runtime，终态/哈希冲突返回安全 409，提交失败记录 dispatch_failed in webui/app.py
- [X] T125 [US4] 替换 cancel/resume HTTP 占位测试并断言状态、事件、工作调用和幂等恢复 in tests/test_discovery_contracts.py
- [X] T126 [US4] 更新前端取消/恢复反馈和 created 超时阻断展示，页面切换后从服务端恢复运行 in webui/index.html + tests/test_discovery_frontend.py

## Phase 14: User Story 3 / User Story 5 — HTTP 与隐私门收口

故事目标：反馈接口具有真实请求证据；provider/runtime 新增链路不泄漏简历、凭据和模型原始响应。
独立测试：用虚构敏感标记执行分析、评估、失败、反馈和删除，检查响应、事件、数据库安全字段和普通日志命中数为 0；反馈影响范围可撤销且不改历史运行。

- [X] T127 [P] [US3] 替换 feedback HTTP 占位测试，覆盖创建、撤销、默认 exact_job scope、认证保护和历史运行不可变 in tests/test_discovery_contracts.py
- [X] T128 [US3] 修复 feedback HTTP 契约测试暴露的路由/错误信封/状态问题 in webui/app.py + webui/store.py
- [X] T129 [P] [US5] 编写 provider/runtime 隐私失败测试：API key、credential_ref、完整 prompt、简历正文、原始响应不进日志/事件/错误信封/数据库 in tests/test_ai.py + tests/test_discovery_integration.py
- [X] T130 [US5] 修复 provider/runtime 新链路的最小披露和安全日志边界 in webui/ai.py + webui/discovery_runner.py + webui/discovery.py

## Phase 15: Polish & 真实验证门

- [X] T131 [P] 运行专项自动化：python -m unittest tests.test_ai tests.test_candidate tests.test_discovery_contracts tests.test_discovery_integration -v，并把真实数量/结果写入 specs/004-resume-job-discovery/validation.md
- [X] T132 [P] 更新并运行脱敏 live-provider contract smoke，分别验证 candidate-analysis v2 与 job-assessment v1；记录 endpoint/model/时间/合同结果但不记录 key/prompt/raw response in tests/fixtures/discovery/e2e_real_boss.py + specs/004-resume-job-discovery/validation.md
- [X] T133 让受控真实 BOSS E2E 从实际用户 HTTP 路径构建 provider 和提交 runtime，完成≥2方向、真实列表/详情/评估、反馈、取消/恢复；禁止脚本内返回未实现 adapter 的固定 blocked in tests/fixtures/discovery/e2e_real_boss.py
- [X] T134 运行迁移、完整自动化、黄金样本、桌面/窄屏浏览器、真实来源 smoke 和真实 E2E，重启受影响后端并验证可访问，最后执行 verification-before-completion 并按事实更新 specs/004-resume-job-discovery/validation.md + specs/004-resume-job-discovery/tasks.md — 1169 项全量回归通过；2026-07-15 19:08 新鲜真实 HTTP E2E source=6/detail=1/evaluated=2，feedback/cancel/resume 全部门通过，blockers=[]；PID 3424 HTTP 200

## Runtime Closure Dependencies

```text
T096 audit
  → T097-T101 shared provider/runtime foundation
  → T102-T111 US1 real candidate analysis
  → T112-T120 US2 real assessment + run dispatch
  → T121-T126 US4 cancel/resume
  → T127-T130 US3/US5 HTTP + privacy closure
  → T131-T134 live/full verification
```

并行边界：

- T097 与 T098 可并行；T099 依赖 T097，T100 依赖 T098，T101 依赖 T099+T100。
- T102 与 T103 可并行；T105 可在 T104 的接口固定后进行，T108 可与 T105/T106 并行写 RED 测试。
- T112/T113 与 T115 可并行写 RED；T114、T116 完成后再进入 T117–T120。
- T121 与 T122 可并行；T127 与 T129 可并行。
- T131 与 live-provider 前置准备可并行，但 T132–T134 的真实调用按顺序执行，避免共享凭据、登录态和运行状态互相污染。

## Runtime Closure MVP

最小可交付范围是 T096–T111：真实候选人分析从用户入口可用、证据位置可验证、错误可追踪。它可以独立验收 US1，但不能宣称 feature 004 完成；岗位发现价值闭环至少还需要 T112–T126，发布完成必须通过 T134。
