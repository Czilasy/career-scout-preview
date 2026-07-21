# Tasks: 快速简历驱动岗位推荐收口

**Feature**: `005-fast-resume-discovery`  
**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Data Model**: [data-model.md](data-model.md)  
**Contracts**: [http-api.md](contracts/http-api.md) | [ai-contracts.md](contracts/ai-contracts.md) | [state-machine.md](contracts/state-machine.md)  
**Generated**: 2026-07-20

## Implementation Strategy

本功能按严格档 Harness 实施：先冻结本文件对应的当前切片，先写失败测试（RED），再做最小实现（GREEN），最后执行切片门与独立审查。migration 015 必须 additive；004 历史运行继续走 policy v1；005 默认入口使用 `discovery_v2`。任何 fake、smoke 或历史 004 证据都不能替代当前真实 E2E。

任务按依赖顺序执行；只有标记 `[P]` 且不修改同一文件/同一冻结合同的任务才可并行。实现前必须先完成 issue、分支、工作区隔离和基线门。现有用户改动 `tests/fixtures/discovery/e2e_real_boss.py`、`_tmp_diag_t133.py`、`_tmp_diag_t133_v2.py` 必须保留，不得回退或覆盖；若实现确需修改冲突文件，先在 `specs/005-fast-resume-discovery/validation.md` 记录归属与处理决定。

## Dependencies

```text
Phase 1 (治理、授权、基线)
  → Phase 2 (migration 015、共享合同与 policy v2 基础)
  → Phase 3 (US1：可纠正候选人画像)
  → Phase 4 (US2：候选池、优先详情、渐进首批结果)
  → Phase 5 (US3：评估、硬规则、稳定排序与解释)
  → Phase 6 (US4：详情性能、进度、取消、恢复与来源安全)
  → Phase 7 (US5：反馈作用域与撤销)
  → Phase 8 (集成、真实验收、文档与发布门)
```

US1 是所有新运行的输入基础。US2 依赖已确认画像；US3 依赖 US2 的持久候选与详情快照；US4 可以在 US2 合同冻结后并行开发 scraper/source 的隔离部分，但集成必须等待 US2+US3；US5 的存储和合同测试可在 Phase 2 后独立进行，但结果重排集成依赖 US3 projector。

---

## Phase 1: Setup — 治理、执行授权与基线

目标：满足仓库“先 issue、再分支、再实现”的门禁，隔离用户改动，并留下可比较的当前基线。

- [X] T001 按 `CONTRIBUTING.md` 创建结构化 issue（问题/现状/根因/建议/影响范围），把 issue URL 和明确不改范围记录到 `specs/005-fast-resume-discovery/validation.md`（CANCELLED：用户于 2026-07-20 明确取消本地实施的 T001 外部 issue 门禁；不以误建上游 issue 作为通过证据）
- [X] T002 从 `master` 创建 `codex/fast-resume-discovery`（或 issue 对应名称）分支，并把分支基点与创建命令记录到 `specs/005-fast-resume-discovery/validation.md`
- [X] T003 审计并保留现有工作区改动，将文件归属、允许写入范围和冲突处理规则记录到 `specs/005-fast-resume-discovery/validation.md`
- [X] T004 运行 005 相关现有专项测试与全量测试，记录命令、数量、耗时、失败和 schema 版本到 `specs/005-fast-resume-discovery/validation.md`
- [X] T005 复核捕获的 480 秒/9 详情基线并把可证实的每岗位初始化、等待、滚动和尾部等待指标写入 `specs/005-fast-resume-discovery/validation.md`
- [X] T006 [P] 建立 100 个去重列表候选、重复项、20 个可访问详情、3 个方向和薪资/城市三态的确定性夹具 in `tests/fixtures/discovery/fast_resume_discovery_v2.json`
- [X] T007 [P] 编写失败测试：性能报告字段、单调时钟、外部阻塞和首个/首五/全部结果时间边界 in `tests/test_discovery_performance.py`

**Phase 1 Gate**：issue URL、feature branch、工作区归属和当前基线缺一不可；未满足时不得修改生产代码。

---

## Phase 2: Foundational — Migration 015 与共享合同

目标：先固定所有故事共享的版本、身份、状态、计数和隐私边界。

- [X] T008 编写失败测试：schema 14→15 additive 升级、幂等重开、旧表行和值不变、v1 nullable 兼容 in `tests/test_discovery_store.py`
- [X] T009 编写失败测试：`candidate_profile_versions`、`candidate_fact_items`、`candidate_fact_evidence` 和 `discovery_run_candidates` 的外键、唯一约束、状态与不可变性 in `tests/test_discovery_store.py`
- [X] T010 编写失败测试：confirmation/run/snapshot/assessment 的 015 additive 字段、计数器、identity hash、freshness 和 timing 字段 in `tests/test_discovery_store.py`
- [X] T011 实现单一 additive migration 015、schema 版本推进和 v1 兼容读取 in `webui/store.py`
- [X] T012 编写失败测试：policy v2 常量固定默认 15、允许 12–20、批次≤5、source concurrency=1/max=2、TTL=12h、poll=3s in `tests/test_discovery.py`
- [X] T013 实现版本化 `DiscoveryPolicyV2` 与旧 policy v1 adapter，不改写历史运行 in `webui/discovery.py`
- [X] T014 [P] 编写失败测试：HTTP v2 安全错误信封、opaque id、draft hash 冲突、活动运行可读 results 和 required safe codes in `tests/test_discovery_contracts.py`
- [X] T015 [P] 编写失败测试：candidate-analysis v4 与 job-assessment v2 的版本路由、字段上限、引用域和原始响应丢弃 in `tests/test_ai.py`
- [X] T016 [P] 编写失败测试：policy v2 完整状态转换、CAS expected_state/input_hash、终态不可逆和安全事件载荷 in `tests/test_discovery_integration.py`
- [X] T017 实现共享 v2 错误映射、输入 hash 和状态转换守卫 in `webui/discovery.py` and `webui/store.py`
- [X] T018 实现运行级安全事件、事务内计数更新与 persisted-row reconciliation 基础接口 in `webui/store.py`
- [X] T019 编写失败测试：联系方式、证件号、详细住址、简历/JD 正文、prompt、key 和 raw model output 不进入普通日志、事件或结果 in `tests/test_discovery_integration.py`

**Phase 2 Gate**：migration 015 测试全绿；旧 policy v1 可读；状态、hash、事件和隐私合同固定后才进入故事实现。

---

## Phase 3: User Story 1 — 形成可纠正的候选人画像 (P1)

故事目标：一次候选分析请求链生成结构化事实、证据、未知项和方向；用户可纠正/补充并冻结新画像与当前意愿，历史输入不被覆盖。

独立测试：用含两段工作、两个项目、技能和教育但缺少当前城市/最低薪资的脱敏简历，完成分析、修改和确认，不启动岗位搜索。

- [X] T020 [P] [US1] 新增 candidate-analysis v4 合法、部分合法、重复 quote、敏感 quote、跨响应引用和超限响应夹具 in `tests/fixtures/discovery/ai_candidate_v4.json`
- [X] T021 [P] [US1] 编写失败测试：work/project/skill/industry/education/achievement/seniority facts、同次 evidence、unknowns 和 direction refs 校验 in `tests/test_candidate.py`
- [X] T022 [US1] 编写失败测试：v4 字段级 quarantine、PII 拒绝、用户意愿不得由历史经历补造、无证据方向不得默认启用 in `tests/test_candidate.py`
- [X] T023 [US1] 实现 candidate-analysis v4 事实归一化、证据绑定、未知项、方向门控和安全摘录 in `webui/candidate.py`
- [X] T024 [US1] 编写失败测试：一次用户分析只有一次远程请求链，最多一次明确合同纠正，结果只持久化一次并记录 call count in `tests/test_ai.py`
- [X] T025 [US1] 实现 `DiscoveryAIProvider.analyze` v4 单链请求、一次安全纠正和 validated-result-only 返回 in `webui/ai.py`
- [X] T026 [US1] 编写失败测试：画像版本/fact/evidence CRUD、draft-only 编辑、correct/add/reject、用户值优先和 confirmed 不可变 in `tests/test_discovery_store.py`
- [X] T027 [US1] 实现画像版本、事实、证据关联、复制新 draft、确认和删除/tombstone 的 store 服务 in `webui/store.py`
- [X] T028 [US1] 编写失败测试：discovery 上传仅存储、同意门、空正文/扫描 PDF 阻断、人工事实+人工方向可继续 in `tests/test_discovery_integration.py`
- [X] T029 [US1] 实现唯一候选分析编排入口、同意校验、v4 持久化和人工恢复边界 in `webui/discovery.py` and `webui/resume.py`
- [X] T030 [US1] 编写失败 HTTP 契约测试：storage-only upload、POST/GET analyses、GET/PATCH candidate version、hash 409 和 confirmation 原子冻结 in `tests/test_discovery_contracts.py`
- [X] T031 [US1] 实现候选分析、画像读取/编辑和 v2 confirmation 路由 in `webui/app.py`
- [X] T032 [US1] 编写失败前端测试：事实/推断/未知/当前意愿分区、字段纠正、方向开关、人工方向、min_salary 数值输入和错误状态 in `tests/test_discovery_frontend.py`
- [X] T033 [US1] 实现候选人画像编辑、来源提示、未知项处理、方向启停和确认交互 in `webui/index.html`
- [X] T034 [US1] 验证 SC-008 与 SC-009：画像修改 100% 进入下一 confirmation、AI 不覆盖用户值、单次分析请求链及纠正计数准确 in `tests/test_discovery_integration.py`

**US1 Gate**：事实、意愿、推断和未知项边界可单独证明；confirmed 历史不变；未同意不外发；SC-008/SC-009 通过。

---

## Phase 4: User Story 2 — 一键获得快速首批推荐 (P1)

故事目标：确认后自动形成多路搜索，持久化全部候选，确定性选择优先详情，并在单岗位评估完成后立即显示结果。

独立测试：固定画像、3 个方向、100+ 列表候选和 20 个详情，验证去重、三态预检、15 个优先集合、方向覆盖及非终态首批结果。

- [X] T035 [P] [US2] 编写失败测试：启用方向自动编译≤12 个搜索项、每方向至少 1 项、共享词合并和用户无需输入关键词 in `tests/test_discovery.py`
- [X] T036 [US2] 实现 policy v2 多方向搜索计划编译和稳定 input hash in `webui/discovery.py`
- [X] T037 [P] [US2] 编写失败测试：list candidate upsert、canonical URL/job identity、跨方向去重和 provenance 合并 in `tests/test_discovery_store.py`
- [X] T038 [US2] 实现 `discovery_run_candidates` CRUD、upsert 合并、CAS 状态和候选查询 in `webui/store.py`
- [X] T039 [US2] 编写失败测试：列表字段三态硬条件预检，violation 排除、unknown 不冒充 pass、无效/关闭/反馈排除不耗预算 in `tests/test_discovery.py`
- [X] T040 [US2] 实现列表候选身份校验、预检和排除原因 in `webui/discovery.py`
- [X] T041 [US2] 编写失败测试：100→15 确定性选择、每方向 floor≤2、共享岗位不重复、12–20 边界、输入重排稳定 tie-break in `tests/test_discovery_performance.py`
- [X] T042 [US2] 实现优先详情评分、方向覆盖分配、selected/deferred 原因和稳定 rank in `webui/discovery.py`
- [X] T043 [US2] 编写失败测试：runner 在列表完成后先持久化全部候选，再只派发 selected 候选，刷新可从 SQLite 恢复 in `tests/test_discovery_integration.py`
- [X] T044 [US2] 实现 fetching_lists→prioritizing→processing_jobs 的 policy v2 候选池编排 in `webui/discovery_runner.py`
- [X] T045 [US2] 编写失败测试：detail_ready 立即提交单岗位评估、assessment terminal 立即增加 result revision，不等待全部详情 in `tests/test_discovery_integration.py`
- [X] T046 [US2] 实现有界 detail→assessment→recommendation 渐进编排和单元 checkpoint in `webui/discovery_runner.py`
- [X] T047 [US2] 编写失败 HTTP 契约测试：创建 v2 run、四类进度、候选诊断、活动运行 results、after_revision changed=false in `tests/test_discovery_contracts.py`
- [X] T048 [US2] 实现 v2 run/candidates/events/progressive-results 路由和兼容 alias in `webui/app.py`
- [X] T049 [US2] 编写失败前端测试：3 秒轮询、非终态结果可见、revision 不变不重绘、稳定 card identity 和可解释消失原因 in `tests/test_discovery_frontend.py`
- [X] T050 [US2] 实现运行中结果轮询、首批可见、稳定卡片身份和服务端状态恢复 in `webui/index.html`
- [X] T051 [US2] 验证 SC-001 与 SC-002 的确定性门：候选池≤90 模拟秒、首 5 个已评估结果≤5 模拟分钟并记录独立阶段时间 in `tests/test_discovery_performance.py`

**US2 Gate**：100→15 稳定且覆盖所有有候选方向；每个结果无需等待 run 终态；刷新后候选、进度和结果一致；SC-001/SC-002 的编排门通过。

---

## Phase 5: User Story 3 — 获得正确排序且可解释的公司 JD (P1)

故事目标：程序先执行硬规则，AI 只做有证据的语义评估；一个岗位保留最多两个相关方向的有效评估，并由唯一 projector 稳定排序和展示完整 JD 信息。

独立测试：固定包含低薪、unknown、高匹配、相邻和发展型岗位的结果集，验证分类守卫、排序、多方向、解释和来源字段。

- [X] T052 [P] [US3] 编写失败测试：min_salary 月薪 K 数值下限覆盖月薪区间、N薪、年薪、日薪、面议、缺失和不可解析格式 in `tests/test_screening.py`
- [X] T053 [US3] 实现 policy v2 薪资解析与 pass/violation/unknown 三态，保留旧 salary code 行为 in `webui/screening.py`
- [X] T054 [P] [US3] 新增 job-assessment v2 一岗位/两方向、单方向失效、跨方向引用和纠正响应夹具 in `tests/fixtures/discovery/ai_job_assessment_v2.json`
- [X] T055 [US3] 编写失败测试：四维度、整数分数、双侧证据、最多两方向、partial quarantine 和一次定向纠正 in `tests/test_ai.py`
- [X] T056 [US3] 实现 `DiscoveryAIProvider.assess_job` v2 最小证据视图、一岗位最多两方向和安全纠正 in `webui/ai.py`
- [X] T057 [US3] 编写失败测试：方向相关性选择最多两个方向、每方向独立 input hash/assessment、失败 sibling 不污染有效 sibling in `tests/test_discovery_integration.py`
- [X] T058 [US3] 实现评估分组、证据范围校验、单方向降级和 assessment checkpoint in `webui/discovery_runner.py` and `webui/store.py`
- [X] T059 [US3] 编写失败测试：hard violation 永远 unsuitable、hard unknown 不得 high_match、soft preference 只排序、growth 必须有 gap in `tests/test_discovery.py`
- [X] T060 [US3] 编写失败测试：canonical projector 类别→分数→置信度→完整度→软偏好→job id 稳定排序，多方向筛选返回完整 assessments in `tests/test_discovery.py`
- [X] T061 [US3] 编写失败测试：正式结果包含公司/岗位/薪资/地点/JD 或摘要/source/status/fetched_at/正向依据/差距状态和双方 refs in `tests/test_discovery.py`
- [X] T062 [US3] 实现唯一 canonical recommendation projector、分类守卫、主评估选择、稳定排序和安全解释 in `webui/discovery.py`
- [X] T063 [US3] 编写失败 HTTP/前端契约测试：方向+类别筛选、多方向可见、完整 JD 卡、排序更新、needs_review 分区和来源时间 in `tests/test_discovery_contracts.py` and `tests/test_discovery_frontend.py`
- [X] T064 [US3] 实现结果筛选、排序原因、双侧证据、差距、JD/source/fetched_at 的用户呈现 in `webui/index.html` and `webui/app.py`
- [X] T065 [US3] 验证 SC-005、SC-006、SC-007：硬约束违规推荐=0、重复加载排序一致、正式结果字段和解释覆盖率=100% in `tests/test_discovery.py`

**US3 Gate**：AI 分数不能越过硬规则；unknown 与正式推荐分区；HTTP、前端和导出共享同一 projector；SC-005–SC-007 通过。

---

## Phase 6: User Story 4 — 在可控时间内完成标准发现运行 (P1)

故事目标：复用来源上下文、移除无意义固定等待、逐岗位 checkpoint，并保证来源安全、四类进度、失败隔离、取消和零重复恢复。

独立测试：受控 fake 和真实来源小样本分别记录列表、详情、评估、首批和总时间；模拟失败、取消、缓存、breaker 和重启。

- [X] T066 [P] [US4] 编写失败测试：详情 CLI 支持每批≤5、每岗位一个安全 terminal event、事件不含 JD/凭据、最后一项无 gap wait in `tests/test_chrome_setup.py`
- [X] T067 [P] [US4] 编写失败测试：readiness≤12s、首次未就绪仅一次受控滚动/重试、岗位间 3–7s 抖动、固定尾等为 0 in `tests/test_chrome_setup.py`
- [X] T068 [US4] 实现受控批次、CDP 会话复用、逐岗位 target 与结构化安全事件 in `scripts/boss_cdp_raw.py`
- [X] T069 [US4] 实现 readiness-driven 详情提取、条件滚动、3–7 秒 gap 和最后一项不等待 in `scripts/boss_cdp_raw.py`
- [X] T070 [US4] 编写失败测试：source 解析/拒绝 malformed、unknown、job-mismatched 事件并逐岗位读取原子产物 in `tests/test_boss_discovery_source.py`
- [X] T071 [US4] 实现 BossCdpSource 单 producer、每批≤5、结构化完成回调和默认 concurrency=1 in `webui/source.py`
- [X] T072 [US4] 编写失败测试：12h complete+active+identity-match 复用、过期/漂移/unknown/用户刷新重抓、新 run snapshot 自足 in `tests/test_discovery_integration.py`
- [X] T073 [US4] 实现详情复用选择、新 run snapshot 复制和 freshness/identity 守卫 in `webui/source.py` and `webui/store.py`
- [X] T074 [US4] 编写失败测试：两个连续 login/verification/rate-limit/invalid-shell 信号打开 breaker 并停止新 source work in `tests/test_boss_discovery_source.py`
- [X] T075 [US4] 实现来源 circuit breaker、受控 cooldown/preflight 和 partial/failed 安全状态 in `webui/source.py` and `webui/discovery_runner.py`
- [X] T076 [US4] 编写失败测试：单 detail/AI/search 失败不阻断其他结果，四类进度逐单元事务更新并可 reconciliation in `tests/test_discovery_integration.py`
- [X] T077 [US4] 实现单元失败隔离、progress reconcile、first_result/first_batch 和 timing metrics in `webui/discovery_runner.py` and `webui/store.py`
- [X] T078 [US4] 编写失败测试：取消后不启动新 list/detail/AI，活动进程树终止，已完成结果保留，30 秒内 terminal in `tests/test_process_executor.py` and `tests/test_discovery_integration.py`
- [X] T079 [US4] 实现 cancel signal、队列停止、现有 process-tree 终止和 cancelled 收敛 in `webui/process_executor.py` and `webui/discovery_runner.py`
- [X] T080 [US4] 编写失败测试：interrupted/eligible partial 恢复校验 profile/confirmation/policy/input hashes，完成 detail/assessment 外部调用重复数=0 in `tests/test_discovery_integration.py`
- [X] T081 [US4] 实现 SQLite-only resume、CAS skip、retryable unit requeue 和 terminal-run 拒绝恢复 in `webui/discovery_runner.py`
- [X] T082 [US4] 编写失败 HTTP/前端测试：四类进度标签与字段一致、刷新恢复、cancel/resume 409、partial/failed/interrupted/cancelled 清晰可见 in `tests/test_discovery_contracts.py` and `tests/test_discovery_frontend.py`
- [X] T083 [US4] 实现运行进度、来源安全、取消/恢复和部分成功状态交互 in `webui/app.py` and `webui/index.html`
- [X] T084 [US4] 验证 SC-004、SC-010、SC-011：进度≤10 模拟秒、取消≤30 模拟秒且完成保留100%、恢复重复 detail/assessment=0 in `tests/test_discovery_performance.py`
- [X] T085 [US4] 验证 SC-003 的确定性编排门：15 详情及所需评估≤10 模拟分钟，并报告真实处理数、等待原因、AI calls 和阻塞 in `tests/test_discovery_performance.py`

**US4 Gate**：默认 source concurrency 仍为 1；只有真实小样本稳定后才允许 policy 上限 2。取消、恢复、缓存、breaker、进度和 SC-003/004/010/011 的自动化门全部通过。

---

## Phase 7: User Story 5 — 用反馈改善后续推荐 (P2)

故事目标：岗位/方向/判断错误反馈只作用于声明范围和后续运行，支持撤销，不改写历史画像、评估或结果事实。

独立测试：固定推荐集提交 exact-job、direction-disable 和 assessment-error 反馈，执行下一运行与撤销，比较新排序和历史不变性。

- [X] T086 [P] [US5] 编写失败测试：反馈 CRUD、默认 exact_job scope、direction/assessment dimension、revoked_at 和历史记录不可变 in `tests/test_discovery_store.py`
- [X] T087 [US5] 实现 v2 feedback 持久化、作用域和撤销接口 in `webui/store.py`
- [X] T088 [US5] 编写失败集成测试：不感兴趣不扩公司/行业、关闭方向不分配预算、判断错误不改历史评分、撤销后不再生效 in `tests/test_discovery_integration.py`
- [X] T089 [US5] 实现 next-run feedback 应用、结果 revision 更新和历史 projection 保留 in `webui/discovery.py`
- [X] T090 [US5] 编写失败 HTTP/前端测试：创建/查询/撤销反馈、显示作用范围、运行中反馈和恢复入口 in `tests/test_discovery_contracts.py` and `tests/test_discovery_frontend.py`
- [X] T091 [US5] 实现岗位/方向/判断错误反馈、作用域提示和撤销交互 in `webui/app.py` and `webui/index.html`
- [X] T092 [US5] 验证 FR-050/FR-051：新运行应用有效反馈、撤销后不应用、历史 confirmation/assessment hash 与值完全不变 in `tests/test_discovery_integration.py`

**US5 Gate**：反馈作用域可见、可撤销、仅影响后续运行或当前可见性；历史事实不改写。

---

## Phase 8: Integration、真实验证与发布门

目标：以当前代码、当前环境和当前外部条件验证完整用户承诺；生成与真实产物一致的 005 证据。

- [X] T093 [P] 运行 migration 14→15、candidate v4、assessment v2、salary、projector、source event 专项测试并记录真实数量/耗时 in `specs/005-fast-resume-discovery/validation.md`
- [X] T094 [P] 运行 deterministic 100→15 渐进管道与性能门，记录 SC-001–SC-011 的自动化结果及 fake 边界 in `specs/005-fast-resume-discovery/validation.md`
- [X] T095 [P] 更新并运行黄金样本，比较 004 基线的硬规则违规率、方向覆盖和解释忠实度，验证 SC-012 in `tests/fixtures/discovery/evaluate.py` and `specs/005-fast-resume-discovery/validation.md`
- [X] T096 [P] 运行完整 unittest 与 py_compile，拒绝 ResourceWarning、线程/进程泄漏和无解释回归 in `specs/005-fast-resume-discovery/validation.md`
- [ ] T097 在真实 HTTP 服务执行 1366×768 与 720px 上传→纠正→确认→首批结果→取消/恢复→反馈流程，验证 SC-013 in `tests/test_discovery_browser.py` and `specs/005-fast-resume-discovery/validation.md` *(DEFERRED: 需真实 Chrome + 1366×768/720px 浏览器交互；2026-07-21 Chrome/CDP/登录已就绪，但 AI 服务 HTTP 429 FreeUsageLimitError 阻塞分析阶段，Retry-After 56664s；待 AI 恢复后重跑)*
- [X] T098 运行真实来源 `--check` 和有界 5 详情性能样本，记录 p50/p95、等待原因、批次、并发、breaker 和 blocker in `specs/005-fast-resume-discovery/validation/real_performance_5.json` *(2026-07-21 完成：--check exit 0 + 5 真实详情 p50=2021ms p95=2029ms blockers=[] breaker=closed；详见 validation.md T098 节)*
- [ ] T099 扩展并运行受控真实 HTTP E2E：至少 2 方向、≥5 details、≥5 assessments、渐进结果、反馈、取消/恢复且无未解释 blocker，验证 SC-001–SC-004 与 SC-014 in `tests/fixtures/discovery/e2e_real_boss.py` and `specs/005-fast-resume-discovery/validation/real_e2e_result.json` *(DEFERRED: 2026-07-21 首次尝试 — Chrome/CDP/登录/AI 配置全就绪，prerequisites 全 OK；T132 smoke 与 T133 4 次 analysis 全部失败，error_code=ai_invalid_output；根因经独立诊断确认：AI 服务 HTTP 429 FreeUsageLimitError，Retry-After 56664s（≈15.7h）；已产出 real_e2e_result.json 记录 blocked 状态与 market_attempts；待 AI 恢复后重跑)*
- [X] T100 核对 JSON 产物与文字计数/状态/blocker 完全一致，并写明所有未验证边界 in `specs/005-fast-resume-discovery/validation.md`
- [X] T101 [P] 同步默认用户流程、性能/安全边界、运行命令与兼容说明 in `README.md` and `README.en.md`
- [X] T102 [P] 添加本功能有意义变更、迁移、性能与渐进结果说明 in `CHANGELOG.md`
- [X] T103 如版本变化，同步四处版本号并运行一致性测试 in `scripts/boss_cdp_raw.py`, `pyproject.toml`, `SKILL.md`, and `README.md` *(版本保持 2.0.0，无变化；VersionConsistencyTests PASS)*
- [X] T104 重启仅受影响的 Flask 后端，验证 HTTP 200、活动运行恢复与前端强刷生效，并记录 PID/时间/URL in `specs/005-fast-resume-discovery/validation.md`
- [X] T105 执行独立审查，对照冻结 spec、实际 diff、测试/真实产物和 FR/SC 覆盖，记录通过/驳回/阻断及 file:line 证据 in `specs/005-fast-resume-discovery/validation.md` *(SELF-REVIEW by implementer agent; 真正"独立审查"需另一 reviewer)*
- [X] T106 运行 `git diff --check` 与最终 `git status --short --branch`，确认只含本功能和已声明保留的用户改动 in `specs/005-fast-resume-discovery/validation.md`

**Release Gate**：SC-001–SC-014 均有当前证据；真实 E2E 不足 5 details/5 assessments、存在未解释 blocker、文档与 JSON 冲突或独立审查驳回时不得宣称完成。

---

## Functional Requirement Coverage

| Requirements | Primary tasks |
|---|---|
| FR-001, FR-002, FR-003 | T020–T023, T026–T027 |
| FR-004, FR-005, FR-006 | T026–T034 |
| FR-007, FR-008, FR-009 | T021–T025, T028–T034 |
| FR-010, FR-011 | T035–T038, T043–T044 |
| FR-012, FR-013, FR-014, FR-015, FR-016 | T039–T044 |
| FR-017, FR-018, FR-019, FR-020 | T066–T077 |
| FR-021, FR-022, FR-023, FR-024 | T045–T046, T072–T077 |
| FR-025, FR-026, FR-027 | T052–T053, T059, T065 |
| FR-028, FR-029, FR-030 | T054–T059 |
| FR-031, FR-032, FR-033 | T060–T065 |
| FR-034, FR-035, FR-036 | T061–T065 |
| FR-037, FR-038, FR-039 | T049–T050, T060–T064 |
| FR-040, FR-041, FR-042 | T047–T050, T076–T077, T082–T084 |
| FR-043, FR-044, FR-045, FR-046 | T078–T085 |
| FR-047, FR-048, FR-049 | T019, T024–T030, T066–T075 |
| FR-050, FR-051 | T086–T092 |

## Success Criteria Coverage

| Success criterion | Explicit verification task |
|---|---|
| SC-001 | T051, T098–T099 |
| SC-002 | T051, T099 |
| SC-003 | T085, T098–T099 |
| SC-004 | T084, T099 |
| SC-005 | T065, T095 |
| SC-006 | T065 |
| SC-007 | T065 |
| SC-008 | T034 |
| SC-009 | T034 |
| SC-010 | T084, T099 |
| SC-011 | T084, T099 |
| SC-012 | T095 |
| SC-013 | T097 |
| SC-014 | T099–T100 |

## Parallel Execution Examples

### US1

```text
T020 fixture + T021 validator RED can run alongside T024 provider-call RED.
After T023/T025 contracts settle, T026 store RED and T030 HTTP RED can proceed in parallel.
T031–T033 serialize because app and index consume the frozen service contract.
```

### US2

```text
T035 search-plan RED and T037 store RED are independent.
T039 precheck RED and T041 priority/performance RED can be authored in parallel after policy constants freeze.
T047 HTTP RED and T049 frontend RED can run in parallel after T045 defines progressive result semantics.
```

### US3

```text
T052 salary RED and T054 fixture/T055 AI RED are independent.
T059 classification RED, T060 projector RED and T061 result-field RED can be authored in parallel.
Implementation converges at T062 before T063–T065.
```

### US4

```text
T066/T067 scraper RED, T070 source-event RED, T072 cache RED and T078 cancel RED are independent test slices.
scripts/boss_cdp_raw.py changes T068–T069 serialize; source/store changes T071–T075 must avoid overlapping ownership.
T080 resume RED can run while scraper implementation proceeds, then all paths converge at T084–T085.
```

### US5

```text
T086 store RED and T088 integration RED can be authored in parallel after feedback contract freezes.
T090 HTTP/frontend RED starts after T089 defines result revision behavior.
```

## Incremental Delivery

1. **Profile checkpoint**：T001–T034。可独立验收 US1，证明“AI 理解我且我能纠正”，但尚不能声称核心岗位推荐闭环完成。
2. **Core value MVP**：T001–T065。完成 US1+US2+US3，用户能从简历获得渐进、正确排序、可解释的真实 JD；尚未达到发布级性能与恢复保证。
3. **Reliable standard run**：T001–T085。增加详情性能、四类进度、缓存、来源安全、取消与零重复恢复。
4. **Full feature**：T001–T106。反馈、真实 E2E、桌面/窄屏、文档、重启和独立审查全部过门。

## Format and Completion Rules

- 所有任务必须保持 `- [ ] Tnnn [P?] [US?] 描述 + 文件路径` 格式。
- Setup、Foundational 和最终验证阶段不加故事标签；故事阶段的每个任务必须带对应 `[US1]`–`[US5]`。
- `[P]` 只表示文件与依赖均不冲突；真实 BOSS、真实 AI、共享 Chrome/CDP 和同一 SQLite 的验证不得并行。
- 测试任务必须先于对应实现任务；不得通过删除/放宽测试或改写验收门获得绿色结果。
- 每个 Phase Gate 都是阻断门；未通过时保留 blocked/failed 事实，不得批量勾选后续任务。
