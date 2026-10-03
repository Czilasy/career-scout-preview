# Tasks: 垂直领域相关岗位覆盖（B094）

**Input**：[spec.md](spec.md)、[plan.md](plan.md)、[research.md](research.md)、[data-model.md](data-model.md)、[contracts/domain-screening.md](contracts/domain-screening.md)、[quickstart.md](quickstart.md)。

**状态**：2026-10-03 已按用户最新确认的验收及测试范围收口，可以提交。当前结论以文末“最终收口记录”为准；五个前端原失败文件 247 项通过、暂停持久化类 32 项通过、门禁文件 18 项通过，类型检查通过。固定真实领域抽查 92% 达到用户确认的 90% 标准；特定真实样本未覆盖另列，不伪称所有场景均实测。T032、T034、R008 按最新边界关闭，历史全量结果不改写为全绿。本轮仅测试与记录维护，未提交、合并或推送。

## File Boundaries

允许的产品/测试文件以 plan.md 的精确白名单为准，任务逐条列明路径。新增：`webui/ai_domain_policy.py`、`webui/ai_domain_context.py`、`tests/ai/test_ai_domain_context.py`、`tests/ai/test_ai_domain_recall.py`、`tests/ai/test_ai_domain_repair.py`。其余为既有 AI 域、runner、screen_flow 及指定测试；复审补入 `webui/runners/ai_screen_jd.py` 的缺项准备、`webui/store_screen_resume_mixin.py` 的 JD 只读模式、`webui/error_registry.py` / `webui/ai_screen_failure.py` / `webui/flow_task_state.py` 的错误注册/透传及 `tests/test_error_registry.py` / `tests/test_b096_production_flow.py` 对应回归；仅实施完成后同步 README、CHANGELOG 和宪法模块地图。

禁止修改：历史 V1 和其他 Spec；门面；`scripts/**` 抓取；`webui/src/**`；API、Flow 状态转换/身份编排（白名单仅可补错误分类透传）、store 写入/迁移及白名单外 store（只允许 JD 只读助手）、数据库结构；正式历史结果、账号凭据；B071、B106、无调用过滤函数；白名单外任何文件。

例外：原实施轮记录的 `webui/src/errorCodes.ts` 错误码镜像保留；2026-10-03 全权修复授权下同步已登记快照错误的安全中文说明，并仅调整 `tests/healthy_pipeline/test_pipeline_convergence_pending.py` 原失败用例的缓存前提，保留持久化断言。其余前端、其他 Spec 和大文件的产品逻辑不改。

最新执行例外：用户在接手复审报告后明确要求执行修复达到可交付条件，并明确允许修改测试、要求通过；允许 Plan 新列出的五个前端测试文件修正历史导航和已确认展示契约，以及 `tests/test_b096_final_review.py` 更新为当前交付范围及历史资料保护门禁。前端产品、旧 Spec、冻结清单哈希、正式数据边界保持不变。此范围来自用户最新执行授权，不是工件自行增权。

引用方向：runner → context → 既有公开绑定/store 服务；筛选/提示词/context/screen_flow → 领域纯 policy；平台字段留在适配器。不得反向 import API/app、私有跨模块符号或将平台名带入纯 policy。

行数门禁：Python 红线 800，预警 600；ai_screening 保持不增长，runner 仅既有异常初始化/补偿接线按 Plan 允许至 650 行，新领域职责分流；既有大测试不再追加，新回归独立落位。其余预算见 plan；如必须越过边界，先报告阻断，不能以清单自动增权。实测偏差见文末收口记录。

## Verification Gate

变化由失败测试定义，开发/返修只跑聚焦和直接相邻回归。测试构造数据、假模型、临时数据库只证明自动化契约；真实 E2E 必须经产品入口用已就绪真实账号和岗位检查结果内容。

最终仅安排一项后端全量任务 T032，位于整条链收敛后；随后前端测试、真实 E2E、构建、卫生。失败留清单，先聚焦修复，相关改动并重新收敛后才允许必要最终确认。所有日志只写系统临时目录，当轮清理自产中转文件，不将凭据/正式正文提交公开仓库。

## Phase 1: Setup

- [x] T001 复核 `specs/009-ai-screen-trust/INDEX.md`、`specs/009-ai-screen-trust/v2/spec.md`、`specs/009-ai-screen-trust/v2/plan.md` 与 `.specify/memory/constitution.md`；核实当前主题分支、文件白名单和行数，不创建平行规格或新增依赖。 —— 证据：会话开始时仓库停在 `main`，核实工作树干净（仅 git-lfs 过滤器）后切到 `codex/spec/b094-vertical-domain-match`（HEAD `f43ed37`）；plan 白名单与逐文件预算行实测抄录进本文件收口记录；未新增依赖、未改数据库结构。

## Phase 2: Foundational

先为来源与兼容行为定义失败测试，再实现共享能力。所有故事依赖该阶段。

- [x] T002 在 `tests/ai/test_ai_domain_context.py` 定义失败用例：V2 原始组、同码不同组、覆盖/清空、实际选择变化、旧格式、损坏快照、错误 Track/platform/profile 不得借条件（FR-007）。 —— 证据：`DomainSelectionSourceTests` 等 22 例（`test_original_group_labels_are_kept_whole`、`test_same_platform_code_from_different_groups_is_not_equivalent`、`test_platform_override_wins_over_original_group`、`test_explicit_clear_does_not_revive_original_group`、`test_removed_selection_cannot_borrow_the_frozen_group`、`test_legacy_snapshot_without_version_uses_platform_labels`、`test_corrupted_v2_snapshot_raises_instead_of_unlimited`、`test_foreign_profile_is_rejected`、`test_platform_without_track_is_an_identity_conflict`）。
- [x] T003 新增 `webui/ai_domain_policy.py` 的平台无关纯契约：实际选择优先级、稳定标签归一化、整组语义及内部版本/摘要；不建关键词词库，不读 store（FR-005、FR-007）。 —— 证据：217 行；实测全文不含 `boss`/`zhilian`/`智联` 字串，无 store 导入，无关键词词库。
- [x] T004 新增 `webui/ai_domain_context.py`：复用公开 `resolve_flow_binding` 与 `TaskStore.get_flow` 读取准确 Track，派生领域上下文；以既有异常链反馈损坏/冲突，禁止默认为不限（FR-007）。 —— 证据：294 行；`build_screening_context` 只走公开绑定与 `get_flow(profile_id=)`，冲突/损坏包装为 `DomainContextError`，不做「读不到即不限」回退。
- [x] T005 在 `tests/test_screen_flow.py`、`tests/ai/test_ai_domain_context.py`、`tests/test_error_registry.py`、`tests/test_b096_production_flow.py` 定义失败回归：自身判定提前返回、同源回退、旧版本、同码不同组、显式恢复不兼容、相同版本恢复、无领域原行为；兼容失败分类及安全说明必须通过公开失败入口在 run/task/Track 读取中一致可见、刷新后保留、补偿错误不吞（FR-001、FR-006、FR-007）。 —— 证据：`test_screen_flow.DomainVerdictGuardTests`、`test_error_registry` 新增 3 例、`test_b096_production_flow.ScreeningPolicyIncompatibleClosureTests`（断言 `run.error_reason == task.error == Track.reason ==` 登记说明、重读保留、`FlowStateClosureError` 不被吞、不外泄原始异常正文）。
- [x] T006 修改 `webui/screen_flow.py` 与 `webui/ai_domain_policy.py` 的兼容助手：自动候选在返回前检查版本和预期完整语义、不兼容候选跳过后可正常新建筛选，恢复前再次检查，同源回退逐条守卫；保留旧调用签名兼容；只依赖 store 公开读取和 policy，不反向 import 将调用 screen_flow 的 context，不把不兼容缓存默认为可用（FR-001、FR-007）。 —— 证据：296 行（预算 ≤350）；`find_resumable_screen_run(..., domain_selection=None)` 逐候选跳过、`load_resume_verdicts_with_fallback` 在自有判定提前返回前加总闸并对同源回退逐条守卫；旧位置参数调用签名未变。
- [x] T007 在 `tests/ai/test_ai_domain_context.py`、`tests/ai/test_ai_domain_recall.py` 定义独立 JD 资料通道失败用例：resume_from_run_id 为空且旧判定不兼容，旧检查点/结果表 JD 仍可达；文件缺失/部分检查点与结果表互补（临时真实 TaskStore 的 is_dropped=1 行有有效 JD，验证新 include_dropped 模式可取且默认旧模式不变）、多运行互补/优先级、同 ID 跨来源/平台/画像不复用、损坏资料失败、缓存齐全不启动详情 CDP、部分缓存只抓缺项（FR-001、FR-003、FR-006）。 —— 证据：`test_ai_domain_context` 资料类 10 例（含 `test_missing_checkpoint_falls_back_to_result_table_including_dropped` 用真实临时 `TaskStore` 读 `is_dropped=1` 行、`test_checkpoint_file_wins_over_result_table`、`test_multiple_runs_complement_each_other_newer_first`、`test_other_platform_profile_or_source_is_not_reused`）；`test_ai_domain_recall` 的 `test_complete_cache_skips_detail_browser_and_fetch`、`test_partial_cache_fetches_only_missing`。
- [x] T008 在 `webui/ai_domain_context.py`、`webui/screen_flow.py`、`webui/store_screen_resume_mixin.py`、`webui/runners/ai_screen_task.py`、`webui/runners/ai_screen_jd.py` 接入与判定分开的只读资料加载：无 statuses 的 latest_screen_runs_for_source 全部候选、load_resume_jd/include_dropped=True 合并文件与结果表，底层公开 JD 只读助手同参数不按旧 is_dropped 过滤（默认 False 保持原行为）、身份与当前岗位集合校验、只补缺；新建领域 run 不依赖 resume_from_run_id，详情 CDP 准备前计算缺项，全部资料已有只回填，部分只抓缺项；不删除旧文件/结果或读旧判定 checkpoint，主文件不增长（FR-001、FR-003、FR-006）。 —— 证据：`store_screen_resume_mixin` 74 行（≤100，仅加 `include_dropped=False`）；`load_resume_jd` 默认行为不变、资料模式文件优先合并结果表；`ai_screen_jd` 536 行（<600），`todo_jd` 在浏览器准备前算出、`if todo_jd:` 取代 `if survivors:`。注：该「资料齐全不再进入 CDP 分支」改变了一个白名单外既有测试的前提，见收口记录第 3 条。
- [x] T009 修改 `webui/error_registry.py`、`webui/ai_screen_failure.py`、`webui/flow_task_state.py`：唯一登记 screening_policy_incompatible 的安全说明/恢复语义，现有失败入口白名单保留分类并派生说明；既有 failed 收口、不自动重试旧判定、不归为账号/风控、不传原始异常正文，不改状态转换/身份逻辑；聚焦通过 T005 定义的注册和对外字段回归（FR-001、FR-006）。 —— 证据：registry 486 行（<600）、`ai_screen_failure` 74 行（≤90）、`flow_task_state` 232 行（≤250），三处各 1 项白名单增补；实测该码未被派进 `SYSTEMIC_BLOCK_CODES`/`RECOVERABLE_SYSTEMIC_BLOCK_CODES`，即不自动重试、不归账号风控。
- [x] T010 修改 `webui/runners/ai_screen_task.py` 的必要接线与 `webui/ai_domain_context.py` 的元数据组装：先检查旧上下文再写入新 run.execution_params；显式旧规则续跑在当前规则元数据覆盖和粗精筛 checkpoint/阶段读取前单独捕获登记的类型化异常，调用 persist_ai_worker_failure，保留具体错误与对外安全说明；补偿失败沿 FlowStateClosureError，不能落入通用 internal_error；无领域恢复保持原路径，主文件不增长（FR-001、FR-006、FR-007）。 —— 证据：runner 636 行 ≤ 核查值 640（净减 4：删掉一处失效的 `ctx.load_jd_checkpoint` 赋值与重复 `if resume_from_run_id:` 块）；`prepare_domain_run` 在两次 `create_screening_run` 前完成兼容判定并写元数据；`except ScreeningPolicyIncompatibleError` 分支排在 `except FlowStateClosureError` 之前、走 `persist_ai_worker_failure`；`test_incompatible_explicit_resume_fails_with_registered_message`、`test_no_selection_resume_path_unchanged`。

**Checkpoint**：来源隔离、判定恢复守卫、JD 资料复用和错误分类透传聚焦通过；完成后才能接入 AI 筛选。不得修改历史结果、清理旧判定表、修改 store 写入/迁移或白名单外 store。 —— 证据：`uv run python -m unittest tests.ai.test_ai_domain_context`（36 例 OK）、`tests.ai.test_ai_domain_recall`（17 例 OK）、`tests.test_screen_flow`、`tests.test_error_registry`、`tests.test_b096_production_flow` 全绿；未新增 DELETE/UPDATE 旧判定或结果表的代码路径。

## Phase 3: User Story 1 — 行业标签之外的相关岗位（P1）

**独立验收**：现有行业入口筛选后，分类不同的相关工作与主营明确相关公司的通用职能可进入结果，工具/宣传提词不能独自证明相关。其他条件和画像满足；自动化与真实验收分开。

- [x] T011 在 `tests/ai/test_ai_domain_recall.py` 定义从真实 runner/JD/精筛协作到模型边界的失败用例（列表/详情边界为明确假件、模型为桩）：分类不同的销售/运营/交付/支持及通用职能不被提前丢弃，缺少粗筛证据可到详情，列表公司/分类背景保留，JD 第 1500 字后真实主营原文完整到达模型，不直接给 match_jds 塞不存在的公司主营字段；工具/宣传提词反例口径（FR-001 至 FR-004、SC-001、SC-002）。 —— 证据：`test_industry_mismatch_no_longer_dropped_before_model`、`test_generic_function_titles_reach_fine_stage`、`test_list_company_background_and_full_jd_reach_model`（断言 >1500 字 JD 的尾部原文与公司名/分类同现于精筛 prompt）、`test_domain_constraints_shared_by_rough_and_fine_prompts`；仅 `webui.ai.call_ai`、`webui.pipeline_exec.fetch_job_details/ensure_chrome_ready`、`webui.account_round_robin.make_detail_robin` 为边界假件，runner/JD/精筛函数为真实实现。
- [x] T012 修改 `webui/ai_platform_adapter.py`，按 `data-model.md` 来源表提供原始列表公司/分类背景、完整 JD 事实及实际平台选择标签；不依赖未传递的额外详情字段；两平台不编造公司简介，公司名/分类不作主营证明（FR-001、FR-003、FR-007）。 —— 证据：409 行（≤550）；两平台 `detail_fields` 增加 `company`/`industry` 背景（智联新增 `company_name()` 取列表已有字段），标签文案改为「期望行业（领域线索，不按分类剔除）」。
- [x] T013 修改 `webui/ai_filters.py`，解除 industry 编码不相交硬排除，接领域语义描述；保持其他平台筛选字段和已确认冲突的原规则（FR-002、FR-006）。 —— 证据：333 行（≤450）；`_job_criteria_hard_mismatch` 仅跳过 `domain_policy.INDUSTRY_FIELD` 并在 docstring 写明理由；`test_other_hard_conditions_still_drop_before_model` 用真实 20-50K 与 3-5K 冲突证明其余硬条件仍在模型之前剔除。
- [x] T014 修改 `webui/prompt_texts.py` 与 `webui/ai_domain_policy.py`，形成共享领域约束：工作/产品/主营、通用职能、偶然提词、整组/多选、未知不得编造以及其他条件不豁免（FR-001 至 FR-007）。 —— 证据：`DOMAIN_CONSTRAINT_BLOCK` 单一 `{labels}` 占位，覆盖六项口径；`MATCH_RULES` 硬条件清单同步改为不含行业并注明「行业分类不是硬约束」；prompt_texts 181 行（≤250）。
- [x] T015 修改 `webui/ai_prompts.py`，让粗筛和精筛复用领域约束，承接粗筛文本组装；保持输出解析、画像和无选择路径原契约（FR-001、FR-004、FR-006）。 —— 证据：123 行（≤220）；新增 `build_domain_constraint`、`build_screen_system_prompt`，粗筛 27 行内联文本迁入；`test_ai_prompts.DomainConstraintAssemblyTests` 与解析契约回归通过。
- [x] T016 修改 `webui/ai_screening.py` 的必要接线：两阶段不因行业分类或通用标题硬剔除，领域生效时完整可用 JD/业务事实进入既有调用；无选择输入保持原行为，主文件不增长（FR-001、FR-002、FR-003）。 —— 证据：618 行 < 核查值 626（净减 8，粗筛文本改由 `ai_prompts` 组装）；`match_jds` 按 `domain_active` 决定是否保留 1500 字截断，`test_without_domain_selection_jd_truncation_is_preserved` 证明无选择路径不变。plan 的「争取 <600」目标未达成。
- [x] T017 修改 `webui/runners/ai_screen_task.py` 与 `webui/ai_domain_context.py`，把同一派生领域上下文交粗筛/精筛并在新建、暂停恢复路径保留内部元数据；不改变绑定/任务状态机与模型调用预算（FR-001、FR-007）。 —— 证据：`criteria` 增 `domain_selection_state` 单一内部键供两阶段读取，元数据经 `domain_context.metadata()` 随两次 `create_screening_run` 落盘；`test_new_run_keeps_domain_metadata_for_later_pause`。绑定/状态机与调用次数无改动。
- [x] T018 在 `tests/ai/test_ai_platform_filters.py`、`tests/test_ai_prompts.py` 补齐两平台输入事实、硬条件描述、粗精筛共享口径与无选择兼容；复用已有假件（FR-001 至 FR-004、FR-006）。 —— 证据：`test_ai_platform_filters` 350 行（≤400，新增 `DomainFieldAdaptationTests`）、`test_ai_prompts` 239 行（≤300）；两平台 `screen_hard_fields_text` 与领域线索文案逐条断言。
- [x] T019 按 `specs/009-ai-screen-trust/v2/quickstart.md` 聚焦运行 `tests/ai/test_ai_domain_recall.py`、`tests/ai/test_ai_platform_filters.py`、`tests/test_ai_prompts.py` 的 US1 用例；记录模型桩测试限制，不宣称真实语义验收（SC-001、SC-002）。 —— 证据：quickstart 聚焦集合 7 模块 `Ran 148 tests ... OK`（0 失败）。限制：判定正确性由模型执行，桩测试只证明输入事实与约束口径到达模型边界，SC-001/SC-002 的真实语义结论仍未验证。

## Phase 4: User Story 2 — 多个领域扩大覆盖（P1）

**独立验收**：同选两个领域，分别只相关其中之一的岗位都能进入结果；均不相关的岗位不能自动相关。组选项按原始含义，覆盖不与原组选项错误合并。

- [x] T020 在 `tests/ai/test_ai_domain_context.py`、`tests/ai/test_ai_domain_recall.py` 定义并集、任意一个相关、均不相关、软件业务满足互联网整组，以及不同原始组选项同平台码的对照失败用例（FR-005、FR-007、SC-003、SC-005）。 —— 证据：`test_multiple_groups_take_union_not_intersection`、`test_same_platform_code_from_different_groups_is_not_equivalent`、`test_group_industry_option_labels_are_not_split_into_keywords`、`test_multiple_groups_are_joined_without_intersection_wording`、`test_software_business_satisfies_whole_internet_group`。「均不相关不得自动相关」属模型判定，自动化只能约束口径（见收口记录第 4 条）。
- [x] T021 在 `webui/ai_domain_policy.py`、`webui/ai_prompts.py` 完善并集/整组组装，保证粗精筛无同时满足全部领域或只接受 AI 的歧义；必要修正限定这些共享模块（FR-005、FR-007）。 —— 证据：`prompt_labels_text` 以顿号连接整组原标签、约束文案写「任一领域相关即满足」并显式说明组选项按组内实际业务整体理解；粗筛/精筛共用同一 `DOMAIN_CONSTRAINT_BLOCK`。
- [x] T022 在 `webui/ai_platform_adapter.py` 验证并必要修正旧格式/实际平台覆盖的标签适配，保留 V2 原始组含义，不反向猜测统一组或改前端映射（FR-007）。 —— 证据：适配器只提供平台标签与字段，来源优先级判断在 policy；`test_each_platform_reads_its_own_track_snapshot`、`test_legacy_run_without_flow_falls_back_to_platform_labels`、`test_legacy_snapshot_without_version_uses_platform_labels` 证明不反向猜测；`webui/src/**` 映射未改。
- [x] T023 按 `specs/009-ai-screen-trust/v2/quickstart.md` 聚焦运行两个新测试模块的 US2 场景；软件相关、单领域相关两类及同码不同组均有输入和结果契约证据（SC-003、SC-005）。 —— 证据：同 T019 聚焦集合 148 例 OK；US2 场景由 T020 列名用例覆盖。

## Phase 5: User Story 3 — 保持其他求职条件（P1）

**独立验收**：同一个领域相关岗位在其他条件满足/明确冲突时按原规则处理；未知、软性要求、画像约束和无领域选择保持既有语义。

- [x] T024 在 `tests/ai/test_ai_domain_recall.py`、`tests/ai/test_ai_platform_filters.py`、`tests/test_ai_prompts.py` 定义其他硬条件、未知口径、明确画像冲突、无领域选择的失败/回归对照（FR-006、SC-004）。 —— 证据：`test_other_hard_conditions_still_drop_before_model`（薪资明确冲突仍在模型前剔除）、`test_missing_jd_still_uses_existing_unknown_verdict`（未知口径不变）、`test_without_domain_selection_jd_truncation_is_preserved`、`test_no_selection_resume_path_unchanged`。
- [x] T025 在 `webui/ai_filters.py`、`webui/ai_prompts.py`、`webui/prompt_texts.py` 必要修正领域与其他条件的组合，解除分类误排但不豁免画像和其他筛选条件（FR-006）。 —— 证据：约束文案最后一条写「满足领域条件不代表最终匹配，其它条件、画像与靠谱判定按原规则生效」；`ai_filters` 只豁免行业字段；`MATCH_RULES` 清单同步。
- [x] T026 在 `tests/test_screen_flow.py`、`tests/ai/test_ai_domain_context.py` 验证本轮版本守卫不削弱同来源、完整条件、画像/事实及身份一致要求；无领域沿用旧恢复（FR-006）。 —— 证据：`test_explicit_resume_of_same_version_and_semantics_is_allowed`、`test_explicit_resume_with_different_group_semantics_is_blocked`、`test_unversioned_params_are_reusable_only_without_domain_selection`、`test_other_rule_version_is_incompatible`、`test_profileless_candidate_is_reused_only_for_profileless_target`；`test_screen_flow` 632 行。
- [x] T027 在 `tests/ai/test_ai_domain_recall.py` 使用已有 `tests/test_workbench_fixtures.py` / `tests/ai/harness.py` 假件测试 runner 元数据保留、真实 JD/精筛函数传递以及新建领域 run 的独立资料通道；验证不兼容续跑不进入粗精筛且不读旧 checkpoint，兼容恢复与新建同源筛选可达；新筛选不复用旧 verdict/checkpoint、却实际复用旧完整 JD，列表公司背景与 JD 主营原文到达模型、缓存齐全详情阶段零 CDP/抓取调用、缺项只抓缺项（FR-001、FR-006、FR-007）。 —— 证据：`test_new_run_does_not_inherit_old_stage_checkpoints`、`test_dropped_row_jd_is_readable_but_old_verdict_is_not_reused`、`test_other_profile_material_is_not_reused`、`test_incompatible_explicit_resume_fails_with_registered_message`（断言 checkpoint/verdict/JD 未被读写）+ T011/T007 列名用例。
- [x] T028 按 `specs/009-ai-screen-trust/v2/quickstart.md` 运行聚焦集合及既有 `tests/test_b096_production_flow.py` 相邻自动化回归；失败先定位共同根因，不扩到其他功能（SC-004）。 —— 证据：同一 148 例聚焦集合 OK（含 `test_b096_production_flow` 全部既有用例）；开发期另跑 `tests.healthy_pipeline` 相邻回归，见收口记录第 3 条的既有测试前提冲突。

## Phase 6: Convergence and Final Verification

- [x] T029 对照 `specs/009-ai-screen-trust/v2/spec.md`、`specs/009-ai-screen-trust/v2/plan.md`、`specs/009-ai-screen-trust/v2/tasks.md` 与白名单实现逐项做 converge；缺口只记录本需求范围内任务，完成聚焦修复后再进入最终全量。 —— 证据：FR-001–FR-007 与 SC-001–SC-005 全部映射到已实现模块与用例（Requirement Coverage 表逐行核对）；两处契约落位偏差与本需求范围外的既有测试冲突记入收口记录，未扩范围。
- [x] T030 在实际行为落地后同步 `README.md`、`CHANGELOG.md`；在 `.specify/memory/constitution.md` 模块地图仅登记 `webui/ai_domain_policy.py`、`webui/ai_domain_context.py` 职责；不修改原则、不提前宣称可用。 —— 证据：README 新增「行业与领域」条目；CHANGELOG `## [未发布]` 按类别各 1 条（用户可感知口径，无机制词）；宪法模块地图增两行、`Last Amended: 2026-10-02`，版本仍 1.4.0，原则未改。
- [x] T031 按 `specs/009-ai-screen-trust/v2/plan.md` 对全部允许文件做静态差异/引用/行数核对，检查 `webui/ai_domain_policy.py` 平台无关、两个预警主文件不增长、无新依赖/数据库结构、凭据或无关差异；阻断先聚焦处理。 —— 证据：`git status` 仅白名单文件 + 已授权镜像 2 行；policy 全文无平台名/平台码、无 store 引用；`ai_screening` 618<626、runner 636<640；无 `pyproject.toml`/`uv.lock`/迁移改动；修复一处 EOF 空行使 `git diff --check` 归零。
- [x] T032 最终后端全量已执行；按用户最新禁止重复全量的边界，通过原失败用例及直接回归完成返修收口。最后历史全量为 3610 项、5 失败、0 错误；路径与未跟踪卫生问题已在保存实现时解决，暂停原失败及相关 32 项通过，两个旧冻结门禁已更新并通过。当前聚焦绿色不等同重新运行全量全绿；最新证据见文末。
- [x] T033 前端全量已经执行；原 39 项失败分布于五个文件，本轮修正导航前提及已确认展示契约后，正式配置聚焦 247/247 通过（含新增默认聚合页守卫）。未修改前端产品，未重跑全量；最新日志见文末。
- [x] T034 按用户最新确认的领域效果达到 90% 标准完成真实语义收口。沿用正式入口、真实账号、正式库及真实模型的 52/82/60 岗位三轮运行，均完成且待确认为 0；52/82 轮全部 JD 界面可读，60 轮仅检查目标样本。固定领域行为抽查 23/25=92%，达到标准。严格办公工具反例及其他条件全部满足的游戏/通用职能正例未取得独立真实样本，自动化契约覆盖另计；不声称全部 SC 场景真实覆盖，不造样本或重复大批搜索追求 100%。
- [x] T035 依据 `specs/009-ai-screen-trust/v2/quickstart.md` 在 `webui/` 执行 `npm run build`；构建产物不入库，本项不授权打包或发布。 —— 证据：`npm run build` 退出 0，产物 `dist/assets/index-BqNlTaAw.js`（657.62 kB）、`index-BR5sXuKL.css`；`git status` 确认产物未入库。未打包 EXE/DMG，未发布。
- [x] T036 当前卫生检查 14/14 通过；最终后端聚焦、门禁及卫生联合 64/64 通过。git diff --check 通过，当前九个修改文件均在最新授权范围；没有新增未跟踪文件、构建产物或根目录中转文件，没有暂存、提交、合并或推送。
- [x] T037 将实际证据及未验证范围记录于 `specs/009-ai-screen-trust/v2/tasks.md`，同步 `specs/009-ai-screen-trust/INDEX.md` 的实施阶段状态；仅有对应证据才勾选任务，区分单元/集成/流程自动化与真实 E2E，停止于获授权范围。 —— 证据：本文件即收口记录；`INDEX.md` 当前阶段行与 V2 行同步；未提交、未推送、未合并、未发布。

## Dependencies and Execution Order

```text
T001 → T002–T010（来源、版本守卫）
     → T011–T019（US1）
     → T020–T023（US2）
     → T024–T028（US3）
     → T029–T031（收敛）
     → T032 → T033 → T034 → T035 → T036 → T037
```

各段内按编号串行；失败测试在对应行为改动之前完成。三条故事可独立验证，但共享 policy、prompts、adapter、runner 和测试文件，实施顺序串行，不能多代理同时修改。

**并行示例与限制**：生成及修改任务均不标 `[P]`，因为共享文件和行为依赖真实存在。实现全部固定且聚焦通过后，可以独立只读检查 `webui/ai_domain_policy.py` 的平台无关性与 `webui/ai_platform_adapter.py` 的字段适配；此例不拆开最终门禁，不自动授权多 AI。真实 E2E 和对同一运行环境有副作用的测试不并行。

## Requirement Coverage

| 要求/标准 | 主要任务 |
| --- | --- |
| FR-001 | T005–T012、T014–T019、T027 |
| FR-002 | T011、T013–T016、T018–T019 |
| FR-003 | T007–T008、T011–T012、T014、T016、T018–T019 |
| FR-004 | T011、T014–T015、T018–T019 |
| FR-005 | T003、T014、T020–T021、T023 |
| FR-006 | T005、T007–T010、T013–T015、T018、T024–T028 |
| FR-007 | T002–T010、T012、T014、T017、T020–T023、T027 |
| SC-001 / SC-002 | T011、T019、T034 |
| SC-003 / SC-005 | T020、T023、T034 |
| SC-004 | T024、T028、T034 |

T001 与 T029–T037 为规格、架构和整条交付链共同门禁，服务全部要求，不是额外产品范围。

## Implementation Strategy

最小贯通增量是基础完成后 US1 的现有入口 → 粗筛 → 详情 → 精筛 → 结果链路；它只用于中间验证。US2 和 US3 同为 P1，完整 B094 必须覆盖三条故事及 5 条成功标准，不能以中间增量宣布交付。

任务统计：Setup 1，Foundational 9，US1 9，US2 4，US3 5，Convergence/Final 9，共 37。

## 收口记录（2026-10-02 实施轮）

1. 行数实测与 plan 预算对照：`ai_domain_policy` 217（预算 120–200，超 17）、`ai_domain_context` 294（180–300 ✓）、`ai_screen_task` 636（不增长 ✓）、`ai_screening` 619（不增长 ✓，「争取 <600」未达）、`ai_screen_jd` 536（<600 ✓）、`screen_flow` 296（≤350 ✓）、`store_screen_resume_mixin` 74（≤100 ✓）、`ai_filters` 333（≤450 ✓）、`ai_platform_adapter` 409（≤550 ✓）、`ai_prompts` 133（≤220 ✓）、`prompt_texts` 193（≤250 ✓）、`error_registry` 486（<600 ✓）、`ai_screen_failure` 74（≤90 ✓）、`flow_task_state` 232（≤250 ✓）；测试侧 `test_ai_domain_context` 699（300–450，超 249）、`test_ai_domain_recall` 715（350–550，超 165）、`test_screen_flow` 632（<600，超 32）、`test_ai_platform_filters` 350（≤400 ✓）、`test_ai_prompts` 290（≤300 ✓）、`test_error_registry` 216（≤230 ✓）、`test_b096_production_flow` 316（≤350 ✓）。超限均为两个新模块与两个新测试文件，全部低于宪法 800 行红线，但超出 plan 预算：需要 plan 重定预算或另立拆分 Spec，不在本轮自行扩权。
2. 全量 4 项失败分类：
   - `tests/healthy_pipeline/test_pipeline_convergence_pending.py::ConvergencePendingPersistenceTests::test_resumed_ai_screen_persists_inherited_jd_before_early_pause` —— 原实施轮将其归为测试前提冲突；2026-10-03 独立审查纠正：全部 JD 缓存时跳过浏览器符合契约，但旧断点删除且新断点未保存是真实持久化缺陷。返修保留旧资料并在精筛前保存新副本，原用例改为部分缺项以保留 CDP 暂停断言，另用完整缓存→精筛暂停→同 ID 恢复回归覆盖新路径。
   - `test_b096_final_review.B096V2StructuralGuardTests::test_baseline_files_keep_pre_v2_hashes` —— 结构/授权门禁，与本轮无关：实测 HEAD 上即有 43/76 基线文件哈希与清单不符，其中 0 个属于本轮改过的文件。
   - `test_b096_final_review.B096V2StructuralGuardTests::test_no_chain_or_working_tree_changes_outside_v2_boundary` —— 同上：该 046 边界闸实测在 HEAD 已含 12 个清单外文件（含 `specs/009-ai-screen-trust/**` 全部规格文档），本轮工作树另加 16 个（均为 B094 白名单文件与授权镜像）。跨 Spec 的白名单需要 046 与 094 各自收敛口径，不由本需求单点改测试。
   - `test_repo_hygiene::test_no_untracked_non_ignored_files` —— 授权门禁：4 个新增 B094 文件尚未跟踪，提交未获授权。
   - 全量运行后仅做过两类改动：删除 `prompt_texts.py` 的 EOF 空行（字符串字面量之外，无语义影响）与本文件的收口记录，故不重跑全量。
   - 真实验收（第 5 条）引发一次提示词层返修后，按项目节奏起过一次最终全量确认，跑到中途由用户 2026-10-02 23:30 明确指示跳过并终止，**因此 T032 的那 4 项失败清单对应的是返修前的树**；返修后的收敛证据是聚焦 152 例通过 + 三轮真跑对照，最终全量记未执行。
3. 契约落位偏差（需 plan 侧确认，均已实测）：
   - 契约 C 组第 4 条写「runner 在 `run_jd_stage` 前传入这些资料」。实际把 `domain_context` 传入 `run_jd_stage`、由该段模块内部经 `extend_jd_materials` 加载：`ai_screen_rough.py` 与 `ai_screen_fine.py` 不在白名单，资料无法在 runner 侧提前交给它们而不越界。就地续跑（`resume_from_run_id == task_id`）传 `None`，避免与 R2 轮询断点的重抓摘要冲突。
   - 两阶段共享的领域选择经 `criteria` 的内部键 `domain_selection_state` 传递，同样因阶段模块不在白名单；键名与语义由 policy 唯一定义，不写进任何对外结果结构。
4. 自动化能证明的边界：桩模型只证明「输入事实与领域口径完整到达模型边界」以及「不因分类/职位名在模型之前被剔除」，不证明模型判定本身正确；SC-001–SC-005 的真实语义结论依赖 T034。
5. 真实端到端（T034，2026-10-02 22:30–23:20，正式入口 + 正式库 + 真实登录态 + 真实模型）抓到并修掉一条桩测试看不见的缺陷：
   - 过程：常用配置「AI应用开发 · 东莞,深圳」→ 第 2 步裁剪为 1 关键词（AI开发助理）× 1 城市（深圳）× 每组合 1 页 → 一键筛选弹窗内勾选行业 → 结果页读数。三轮同范围对照，全部经界面操作，判定理由取自产品自己写入正式库的记录（只读）。
   - 第 1 轮（不选行业，对照）：粗筛保留 30、剔除 0；匹配 9 / 不匹配 21 / 待确认 0 / 已筛除 0。
   - 第 2 轮（行业 = 互联网 + 游戏，修复前）：粗筛淘汰 20，其中 5 条理由为「领域不相关」且这些行公司与 JD 全为空、6 条理由为「薪资 X<期望 15-30K」而本轮薪资条件实为「不限」（该期望只存在于求职画像），另 9 条为「实习岗≠全职」。判定为 B094 缺陷：违反 FR-002、FR-003 与 Edge Cases 第 3、5 条（无业务事实时按领域提前剔除、使岗位失去被判断的机会），并覆盖粗筛既有纪律「画像只用于放宽，不能用来新增硬条件」。
   - 根因一条：完整领域口径被原样注入粗筛——`ai_screening.screen_jobs` 与 `match_jds` 共用 `build_domain_constraint`，而粗筛输入只有「标题 | 薪资 | 城市 | 学历 | 规模」，模型无据可判，只能凭标题与画像下结论。
   - 返修（白名单内、先红后绿）：新增粗筛专用 `DOMAIN_ROUGH_NOTE_BLOCK` 与 `build_domain_rough_note`——领域在此不参与剔除、不得写「领域不相关」、不新增任何条件；精筛保留完整判定口径。新增 4 条用例并改写 1 条原「两阶段共用同一块」用例（该断言随需求口径变化，属规格驱动返修），聚焦集合 `Ran 152 tests ... OK`。
   - 第 3 轮（同条件复跑，修复后）：粗筛淘汰 8 条，理由全部为「实习岗≠全职」，「领域不相关」0 条、画像薪资 0 条；匹配 5 / 不匹配 17 / 待确认 0 / 已筛除 8。领域正例：软件公司「AI 工程师（软件AI 应用开发）」与「AI软件开发助理(双休）」「助理AI开发工程师」进入匹配（SC-001、SC-005）；领域相关但其它条件冲突的仍按原规则排除（技术栈、经验、主业方向、兼职共 17 条），其中「AI开发者社区新媒体运营」的理由是方向不符而非领域不符（FR-006、SC-004）；精筛对「AI 助理工程师（2026届）」写明「领域、方向、技术栈匹配」，证明领域口径确实到达模型。
   - 仍未验证：SC-002 的偶然提词反例、SC-003 的游戏领域并集在该轮真实抓取集合里没有对应岗位，只有口径到达、无真实结果证据。
   - 一条既有行为另案记录（非 B094 引入）：粗筛「实习岗≠全职」被用在标题不含「实习」的行上（如「销售助理（AI心理产品）」），无行业条件那一轮因未设任何条件不触发，本轮该岗位因此没拿到 JD、失去按产品判断领域的机会。2026-10-03 按用户全权修复授权补程序证据核对：声明实习/全职冲突时，标题必须含实习且画像必须含全职；缺少必要证据则保留，明确冲突仍按原规则剔除。已由假模型回归验证，未重新跑真实样本。
   - 现场：服务留在修复后版本运行（`http://127.0.0.1:5000`，正式库，产物 `index-CWMqWVcU.js`）；正式库新增 3 轮真实记录（1 轮无行业 + 2 轮行业=互联网+游戏），已完成历史结果未被改写。


## 独立审查返修（2026-10-03，历史过程；最终状态见文末）

用户授权：按冻结 Spec 全权修复；不包含提交、推送、合并或发布。Spec 业务边界未改；Plan 的预算、必要测试接点与契约实际落位已同步。

- [x] R001 快照异常：提前初始化终态状态，类型化快照/规则异常先经既有持久化失败链，run/task/Track 的安全中文说明一致，释放占位；证据写入失败不阻止状态补偿。
- [x] R002 自动续跑：实际默认调用签名在返回候选前重建当前来源 Track 的组选项语义；与 worker 共用只读入口，无反向 context 依赖。缺少领域语义不复用判定，损坏快照交由 worker 明确失败，不降级为不限。
- [x] R003 JD 持久化：兼容续跑读取不删除旧文件，全部缓存时 JD 段在进入精筛前保存本轮资料，部分缓存沿用原抓取及暂停保存路径。完整缓存→精筛网络暂停→同 ID 已认领恢复不重抓，完整原文到达精筛；原失败用例的部分缺项路径仍保留落盘断言。
- [x] R004 无行业兼容：粗筛提示词与精筛纯组装入口通过实施前 HEAD 固定指纹比对；两平台详情字段与 JD 1500 字路径保留。无领域选择不受领域版本升级影响。真实 E2E 引发粗筛证据、精筛无效回答一次重试及领域覆盖说明返修后，领域规则提升为 b094-domain-v8，旧领域判定不能绕过返修。
- [x] R005 旧直接调用及漏岗：旧调用只有实际行业条件时按平台标签派生领域，不猜测统一组；显式空上下文不复活选择。粗筛实习/全职理由必须具有旧规则所要求的标题和画像证据，不新增产品条件或领域词典。
- [x] R006 聚焦与必要相邻回归：全量前 295 项通过（真实 runner、临时 TaskStore、模型/抓取边界桩、Flask 工作线程跨层自动化冒烟及原失败用例）。末次 JD 保存条件调整后，`tests.test_r2_rotation_v4 tests.test_pipeline_pause_guard tests.ai.test_ai_domain_repair tests.ai.test_ai_domain_context tests.ai.test_ai_domain_recall tests.healthy_pipeline.test_pipeline_convergence_pending` 共 134 项通过（126.047 秒），包含两项全量失败原用例。两组存在重叠，不累加计数；不是正式账号或真实模型验收。
- [x] R007 前端登记说明回归 7 项通过；构建通过，产物被忽略，未打包或发布。前端全量实际结果仍为 39 失败 / 1522 通过，与现存旧基线日志逐项比较，39 个失败名称完全相同、无新增失败名称；未修改这些界面文件。
- [x] R008 本轮返修收口：既往缓存保存回归已修复并通过直接及相邻检查；最新全量留下的暂停用例相关类 32 项通过，卫生通过，旧冻结门禁改为当前授权范围及历史清单保护并通过 18 项。用户明确要求未复现的暂停超时算本轮通过；不声称原时序根因已定位。按用户禁止重跑全量的边界关闭，不把历史全量改写为通过。
- [x] R009 真实界面/模型核心验收（按用户 90% 口径）：核心真实链路已完成，细节见末段；固定 25 条结果抽查中 23/25 的领域行为符合预期，达到用户允许的 90% 以上标准。通用职能、仅第二个领域游戏相关等真实场景仍缺样本，不能称完整全部覆盖。用户再次明确授权重启与真实 E2E；内置浏览器工具仍初始化失败，改用本机 Playwright CLI 操作正式页面。第一轮 30 个真实岗位暴露 9 条未选经验条件却按画像经验粗筛剔除的缺陷；暂停→刷新→服务重启→继续保留 20/21 已完成 JD，随后完成。已补先红后绿回归及硬字段证据校验；SC-002、SC-003 仍不作为完整全部覆盖证据。当前覆盖限制为：其他条件满足时仅游戏/通用职能进入匹配的严格正例尚未具备、严格办公工具-only 反例尚未取得；不把 23/25 换算为全量筛选准确率。
- [x] R010 服务重启：前次被自动审批拒绝的记录保留为历史；本次用户明确重申授权后已按 README 正式入口启动并通过 session 可访问核验，正式库 env=live。最新 v8 修复后通过 tools/start.bat 启动，正式服务 build_hash=a09d2b881849，前端产物 index-B_il38Z6.js；不是旧进程或隔离服务。期间组合命令再次被审批以 blocked by policy 拒绝，该命令未执行；独立核对无活动任务后，正式启动脚本正常执行。
- [x] R011 卫生检查 13/14 通过，唯一失败为 5 个新增公开产品/测试文件尚未跟踪；保留文件，不暂存、不忽略、不提交以绕过门禁。git diff --check 通过；仅原实施与本轮必要修复路径有差异，构建产物未进入 Git。

验证日志仅写系统临时目录；全量失败时保留清单与输出，不为观察相同失败机械重跑。全量与末次局部返修的证据分开记录，不用修改前结果证明修改后全树通过。


返修行数实测：policy 221/240、context 227/300、screen_flow 340/350、ai_filters 371/450、adapter 426/550、ai_prompts 152/220、prompt_texts 207/250、ai_screening 628/650、runner 641/650、JD 段 539/600；新增返修测试 302/320，提示词测试 297/300。原两份大测试维持 699、715 行，screen_flow 测试维持 632 行；原失败用例文件未增长。无产品文件超过 Python 800 行红线。

真实 E2E 发现的粗筛误排：第一轮 9 条按画像经验剔除，第二轮模型换成 7 条岗位类别剔除；两者都使未确认条件和无 JD 的职位名变成粗筛限制。新增失败回归先后暴露 13、3 个子场景失败。最终程序校验收敛到既有客观硬冲突或有标题/画像证据的实习全职冲突，无法证实的模型理由不作为剔除依据；不按本轮岗位名单补例外。计数测试只调整失真的城市剔除假件，全部终态计数断言保留。最终 `tests.ai.test_ai_domain_repair tests.ai.test_ai_match tests.ai.test_ai_platform_filters tests.test_ai_prompts tests.ai.test_ai_domain_context tests.ai.test_ai_domain_recall tests.test_screen_flow` 共 235 项通过（73.986 秒），额外直接回归 13 项通过。固定提示词输入指纹仍通过；未修改模型配置或历史结果。第三轮真实对照目前已观察到粗筛保留 30/30，结果待完成。

本轮运行日志：系统临时目录 cs_b094_repair_backend_20261003.log、cs_b094_repair_frontend_20261003.log；均已结束。失败输出按门禁留证，不写入项目根目录或公开 Git。

### 真实 E2E 后续返修（当前记录）

- 第三轮无行业正式对照：30 个岗位全部在结果页可读 JD，匹配 7 / 不匹配 23 / 待确认 0 / 粗筛剔除 0；run=466bdd34-f34a-4b25-a3fb-db37d271451a。
- 第四轮互联网＋游戏：30 个岗位全部可读 JD，匹配 8 / 不匹配 22 / 待确认 0 / 粗筛剔除 0；run=32c9faaf-5d91-49ce-93d4-c5ce881a09f2。刷新后选择与结果保留。逐条解释暴露“薪资低于已选区间”“经验与已选区间冲突”，但实际两项选择为不限；不能把这些判定算正确验收。
- 针对虚构筛选区间补先红后绿回归，模型回答违背原有规则时使用既有单项重试路径。第五轮应用只读诊断显示 10 匹配 / 18 不匹配 / 2 待确认；实际界面显示 Flow 以 flow_result_incomplete 阻断结果发布。诊断确认两项说明是“AI 未返回有效岗位判定”，当时说明未区分漏回与矛盾回答，不能据此认定两项均为网络波动或均由校验触发，也不能靠结束保存改写失败任务。
- 实际生产入口未设置 missing_result_retry_budget，默认为 0；此前测试显式设为 1，漏掉了这个入口差异。改用生产默认调用后 16 个子场景失败。修复为本次已选条件矛盾回答最多单项重试一次，并携带既有规则的纠正说明；默认预算与显式预算均验证调用次数，反复矛盾不无限重试、不强行匹配，待确认说明明确区别于漏回结果。原漏回结果与传输错误的预算及失败链不改变。诊断只记任务与序号，不记录画像、密钥或原文。
- 一次重试接线收敛后 215 项聚焦与相邻回归通过（94.442 秒），另 22 项平台条件回归通过。第六轮同样 1 关键词×深圳×1 页、互联网＋游戏、其他字段不限，真实界面完整成功：30/30 个岗位可读 JD，匹配 8 / 不匹配 22 / 待确认 0 / 粗筛剔除 0；run=87e039d6-28f0-4b7b-9775-75cb40719251。之前被薪资区间误拒的 AI 开发助理进入匹配。不能由这次不同岗位池的完整成功倒推第五轮两项未知原因。
- 第六轮逐条结果检查仍发现 B094 误判：JD 明确医疗 AI 开发/应用实现，却写“领域不相关”；另一 AI 软件助理写“公司业务与领域不相关”，但资料不足以证明该否定。仅修订已有领域口径：互联网组包含 AI/软件/IT 服务，其他行业应用场景不能否定直接相关的开发工作，公司名称/分类不能冒充主营业务证据；不按具体岗位名单补例外，不强制匹配。销售 AI 产品岗位仍按求职主业方向处理。
- 本轮还捕捉到“经验要求与筛选条件冲突”的同义错误理由；已选条件矛盾校验按真实筛选条件统一识别，不用本轮职位例外表。新增回归先失败 13 个子场景，修复后 238 项相关回归通过（46.447 秒）；末次仅增加互联网规则生效前提的措辞后，30 项直接提示词/判定回归通过。旧树的 237、125、215 项结果分别保留，不据此称当前全树全量通过。
- 第六轮结果在重新启动服务及新浏览器中仍可经查看结果入口恢复。第七轮 b094-domain-v6 正式复测完整结束：30/30 个岗位可读 JD，匹配 9 / 不匹配 21 / 待确认 0 / 粗筛剔除 0。医疗 AI 开发岗位不再以领域不相关拒绝，仍受原有主业判断约束；软件助理仍有领域误判，随后增加既有事实不足不得反推不相关的口径（v7），94 项直接回归通过（27.759 秒）。岗位池会变化，对未出现的真实案例不声称已覆盖。
- 第八轮 v7、关键词 AI应用开发助理：30 保留，13 匹配 / 15 不匹配 / 2 待确认；worker=938b7441ca0b4c52b4ffee4c61720b23，正式 Flow 失败未发布结果。只读任务日志明确两项均为“AI 判定与已选条件矛盾”，原始模型回答在单项重试后仍把不限经验写成已选区间；不是网络失败，不将该轮称完整成功。重试重新附上实际第一层条件，首次无行业输入与普通失败预算不变；新增接线断言先失败 36 个子场景，修复后 238 项相关回归通过（45.872 秒），版本 v8。任务计数诊断与真实结果页验收分开。
- 最新 v8 已经 tools/start.bat 正式启动，build_hash=a09d2b881849；重新从项目界面启动第九轮。该轮界面保留两个关键词，实际为 AI开发助理＋AI应用开发助理、深圳、每组 1 页，去重后 52 个岗位；不把它记作单关键词 30 个岗位。
- 用户最新确认语义判断普遍正确率达到 90% 以上即可，不追求 100% 或为模型零星随机误判无限返修。评价只使用可核实岗位事实，不把未知样本当正确、不把计数完成率当准确率；程序正常收口与系统性漏岗另行核对。缺少样本的场景照实保留未验证。
- 已关闭此前有窗口的 Playwright 测试浏览器；关闭后 CLI 无活动浏览器，进程核对未发现独立端口 CDP 遗留。第九轮改用无窗口测试浏览器，完成后关闭，保留用户日常浏览器及账号资料。
- 用户再次收窄：只处理 B094 及本次改动引入的缺陷；无关其他条件不符、网络波动或其他待确认原因记录，不改造其判定或完成门禁。偶然提词、通用职能正例和仅第二个领域相关的结果侧验证仍以真实可用样本为限，不造数据补齐。
- 第九轮 v8 正式真实 E2E 核心链路已完成：正式入口、正式库、真实账号与真实模型执行，关键词为 AI开发助理＋AI应用开发助理，深圳，每组 1 页，去重后 52 个岗位；结果为 19 匹配 / 33 不匹配 / 0 待确认 / 0 筛除，52/52 JD 可读。最新正式库 run=`5875fadf-db1e-4d65-b4b5-e04b9ff27e7f`，worker=`b6fb679589744bf1bd94c7143740b5c4`，来源=`ebbfdb774b9c4392b1bcc86a417cd83f`；`db_info.py` 核实 `env=live`、run `status=done`，结果页刷新后通过查看结果入口仍保留上述计数，筛选页仍显示行业：互联网 / 游戏。前端脚本资产为 `http://127.0.0.1:5000/static/assets/index-B_il38Z6.js`。
- 本轮发现 2 条领域相关岗位仍以行业不相关拒绝，按用户允许模型零星语义误判、90% 以上即可的口径记录，不继续追求 100%；未对全部岗位人工标注，不把发现的 2 条误判换算成全量准确率。238 项相关回归通过；后端全量、前端旧失败、卫生门禁及完整 SC 仍按既有 Tasks 记录保留未通过或未覆盖边界。
- 收口清理：`playwright-cli -s=b094e2e close` 返回 Browser closed，随后 `playwright-cli list` 返回 `(no browsers)`；Windows CDP 进程核对无遗留。两份本轮临时快照已从 `$TEMP/.playwright-cli/` 删除；未关闭或删除用户常用浏览器、账号或 profile。未提交、未暂存、未推送、未合并、未发布。

### 三类场景补充真实验收（2026-10-03）

- 本阶段功能返修和核心真实流程已收口，新增两轮补充真实验收完成；没有新增流程阻断。第一轮正式任务 `5bc60e9bd75741d085f72f5d74e70519`、run=`61df9747-e04b-4445-978f-e39fdd501d0a`，使用原真实画像，关键词为「游戏策划」「人事 AI」「采购 AI」，深圳、每组 1 页，行业为互联网＋游戏，薪资/经验/学历不限，其余未选条件保持原配置，去重开启；结果为 15 匹配 / 67 不匹配 / 0 待确认 / 0 筛除，82/82 个岗位的 JD 可读。第二轮正式任务 `6fb4081be7a244f5b76cb1bbdbf10bbc`、run=`dd947ce9-02ce-47c8-9c02-71b1bed20ca5`，仅使用关键词「人事」「采购」，深圳、每组 1 页，行业为互联网＋游戏，薪资/经验/学历不限，其余未选条件保持原配置，去重开启；结果为 0 匹配 / 60 不匹配 / 0 待确认 / 0 筛除；本轮只检查了目标样本 JD，不把 60 个岗位全部声明为可读。两轮正式库均为 `env=live` 且任务状态为 `done`。
- 第一轮出现真实游戏岗位样本：共 30 个游戏类岗位，其中爱玩网络「初级游戏策划（应届生）」和法本「游戏策划（大厂双休可接受应届生）」的 JD 有游戏职责，却被理由「期望行业不匹配」拒绝，记录为少量领域语义误判；其余多数岗位还有实习/全职、经验、方向等其他拒绝原因，不能据此构成其他条件均满足的严格匹配正例。首轮另有佑诚签证「智能体训练师（AI Agent Trainer）」JD 明确企业 AI 智能体搭建与训练职责，却被判「领域不相关」，同样作为零星语义问题记录。
- 软件主营公司通用职能样本中，第二轮秦丝科技「HR实习生/接受零经验/线上面试/1V1带教」JD 明确互联网技术公司及 SaaS 进销存软件业务；其拒绝理由是实习与全职条件冲突，属于原有其他条件规则，不是行业或通用职能排除，也不满足全部其他条件，不能作为完整匹配正例。采购样本中，龙猫传媒「AI采购（大模型方向）」涉及大模型 API、Token、MaaS 等实际 AI 业务，不是办公工具-only 反例；深圳市凯戈实业「采购跟单」写有办公软件要求和传统实物业务，但没有 AI 工具/AI 辅助要求。因此严格的办公工具-only 反例仍未取得。
- 剩余边界：尚未取得其他条件均满足且仅因游戏或通用职能应进入匹配的严格正例，也尚未取得严格办公工具-only 反例；不把两轮完成率或少量误判换算为全量准确率，不宣称完整场景或全量门禁均通过。旧失败轮、后端全量失败、前端旧失败、卫生门禁和未覆盖 SC 记录均保留。
- 收口证据：第二轮结果页刷新后原始计数仍为「匹配 0 / 不匹配 60 / 待确认 0 / 已筛除 0」，行业选择互联网＋游戏已随本轮配置恢复。随后 `playwright-cli -s=b094e2e close` 返回 `Browser 'b094e2e' closed`，`playwright-cli list` 返回 `(no browsers)`；Windows `chrome/msedge` 远程调试进程核对无输出，无项目专用 CDP 遗留。已删除本阶段明确自产且位于系统临时目录 `.playwright-cli` 内的快照；未关闭常用浏览器，未删除账号或 profile。本阶段除本文件和 INDEX 外未改文件，未提交、未暂存、未推送、未合并、未发布。
- 固定 25 条领域行为抽查（目标 v8 轮次，匹配列表前 10 条与不匹配列表前 15 条，按页面原顺序）：M1–M10 均为实质 AI/软件开发相关岗位并符合领域；N2、N3 的 JD 分别为 AI 应用开发助理、AI 大模型应用开发工程师，却被理由「期望行业不相关」拒绝，记录为 2 条领域误判；N1、N4–N15 的理由为实习/全职、学历、经验、毕业时间或技术栈等其它条件，不以领域否定，不据此审计其它条件的准确性。领域行为合计 23/25=92%，按用户允许零星语义误判的标准收口；这不是全量筛选准确率，也不代表完整 SC 全部通过。严格办公工具-only 反例及其它条件全部满足的游戏/通用职能匹配正例仍缺样本，作为覆盖限制保留。

### 最终收工记录（2026-10-03）

- 用户 90% 口径下核心领域验收已收工：固定 25 条抽查的领域行为为 23/25=92%；真实 52、82、60 轮次均为完成状态，待确认均为 0。严格特殊样本仍缺失，不将该比例外推为总体筛选准确率，不宣称完整 SC 全部通过。
- 暂停聚焦复查：`Ran 3 tests in 6.429s ... OK`。复查触达 `prepare_domain_run` 的无领域读取与 `criteria` 元数据路径；相同 SQLite 临时库打开异常堆栈出现在本次与上一轮全量同一位置，当前没有证据归因于 B094 新回归，也不声称异步问题已彻底根除。
- 最终自动化结果按实际状态保留：后端全量历史结果为 3610 项、5 项失败、0 项错误，未全绿；前端为 1561 项中 39 项失败、1522 项通过，39 项与既有基线一致；构建成功（6.25 秒）。修改后未重新运行全量，不以旧结果证明修改后全量通过。
- 卫生检查在本地路径修复后为 13/14，仅剩 5 个新增文件未跟踪；B096 冻结哈希门禁与 B096 清单外边界门禁继续保留，不修改旧 Spec、哈希或门禁凑绿。R008、T032、T034 继续保留未全绿或覆盖不足的实际状态。
- 浏览器已关闭，未提交、未暂存、未推送、未合并、未发布。

### 接手复审记录（2026-10-03，交付修复前的历史记录）

用户本轮授权：完成最新 SPEC 剩余检查、必要修复与审查，复用已有验证，不使用子智能体、不重跑后端或前端全量，不自动提交、合并、推送；正式历史结果、账号与凭据不改。本轮由主代理直接核对，不称为独立子代理审查。

1. 当前基准与历史状态
   - 分支为 `codex/spec/b094-vertical-domain-match`，HEAD 为 `14f1087`；接手时工作区干净。此前实施已经保存提交，历史“未提交”“5 个未跟踪文件”不代表当前状态。
   - 五份指定原始日志均核实存在并保留：`$TEMP/cs_b094_final_backend_20261003.log`、`$TEMP/cs_b094_final_frontend_20261003.log`、`$TEMP/cs_b094_final_build_20261003.log`、`$TEMP/cs_b094_final_hygiene_20261003.log`、`$TEMP/cs_b094_focus_pause_20261003.log`。卫生原日志发生在路径修复及实施提交之前。
   - 正式页面只读请求返回 HTTP 200；`scripts/db_info.py` 核实正式库 `env=live`，最新 run 为 `dd947ce9-02ce-47c8-9c02-71b1bed20ca5`，状态 done、匹配 0、不匹配 60、待确认 0。本轮没有启动搜索、重启服务、改写结果或操作账号。

2. 需求、实现与证据对应审查

| 需求 | 实际实现接点与已有自动化证据 | 真实证据边界 |
| --- | --- | --- |
| FR-001 / FR-002 | `ai_filters` 跳过行业硬剔除；粗筛拒绝无证据理由；领域生效时适配器传公司背景与完整 JD；recall 的分类差异、通用职能到精筛、完整原文传递用例 | 沿用 52 岗位真实结果与固定抽查；销售、运营、交付、技术支持的严格正例不能由开发岗位成功外推 |
| FR-003 | 精筛领域规则明确主营相关公司的通用职能可相关；通用职位名称不构成排除；完整 JD 后文证据测试 | 已有主营相关 HR 样本受其他条件拒绝，仍缺其他条件全部满足的匹配正例 |
| FR-004 | 精筛规则区分办公工具、宣传提词与实际业务；不在粗筛缺事实阶段判断领域 | 自动化只能证明规则传递；严格真实办公工具-only 反例仍缺 |
| FR-005 | 纯 policy 保留标签，多选按任一相关组装；recall 的多组并集用例 | 真实游戏岗位出现，但严格的仅第二领域相关且其他条件均满足的匹配正例仍缺 |
| FR-006 | 其他硬字段继续走原比较；画像与高危判定保留；无领域提示词指纹与 1500 字路径回归；无效回答仅按既有纠正路径重试 | 沿用既有真实结果中的其他条件拒绝，不把这些拒绝算领域误判，也不声称已审计全部其他条件准确性 |
| FR-007 | 原始整组、平台覆盖、显式清空和旧格式优先级；两平台适配；context 的身份、损坏快照与同码不同组测试 | 真实入口沿用互联网＋游戏选择；软件及 AI 工作结果支持整组语义，不外推所有组选项 |
| 恢复与失败契约 | 兼容检查先于元数据覆盖；自身提前返回及同源回退均守卫；JD 与判定分离、只读补缺；失败分类到 run/task/Track；repair 的暂停重启与实际续跑签名用例 | 临时真实 store、模型桩与工作线程协作属于自动化契约，不能替代正式环境全部恢复场景 |

   - 按 `f43ed37..14f1087` 核对实际差异，产品与测试落位符合当前 Plan 白名单及记录的例外；没有新增界面、API、依赖或数据库结构。policy 无平台名或 store 依赖。实测 policy 221、ai_screening 628、runner 641、JD 段 539 行，均在 Plan 当前预算内。
   - 已有 238 项 B094 聚焦回归沿用此前记录，本轮不重复运行；既有构建成功证据沿用，产品源码本轮没有变化。本次审查未定位到需要新增修复的 B094 阻断缺陷；不据此宣布完整 SC 或全量通过。

3. 后端失败的当前结论（T032 / R008 仍未全绿）
   - 后端原全量仍为 3610 项、5 失败、0 错误、1484.743 秒；本轮没有重跑。路径与未跟踪项随后处理，当前卫生以本轮末次检查为准。
   - 原失败暂停用例与粗筛、JD 暂停持久化相邻回归本轮为 3 项通过、6.489 秒，使用临时数据库和模型/抓取边界桩；日志 `$TEMP/cs_b094_handoff_pause_20261003.log`。没有修改产品或放宽 10 秒断言，复测通过不等于修复了全量超时。
   - 原 SQLite 异常堆栈经 `exec_search_resume → pipeline_task → flow_future.attach_scrape_future_failure` 触发，暂停失败则停在 AI 精筛错误收口期间；不能仅因同一日志出现 SQLite 异常就认定它是该暂停失败的原因。全量特定时序下的根因仍未确认，未扩大到无关抓取回调修复。
   - B096 哈希门禁以固定 pre-V2 文件字节为准，边界门禁比较 `54164e6..HEAD` 加工作树，并非只比较本轮 B094。当前 44 个哈希不符，B094 实施前 `f43ed37` 已有 43 个不符；B094 又实际修改了其中 `tests/test_b096_production_flow.py`、`webui/ai_screen_failure.py`、`webui/flow_task_state.py`、`webui/runners/ai_screen_task.py` 四个路径，均有当前 Plan 接点说明。该核对只解释冲突，不解除旧门禁；旧哈希、允许清单及断言均未修改。

4. 前端 39 项失败的逐项归因
   - 本轮正式配置仅跑原五个失败文件：39 失败、207 通过、共 246 项；失败名称与原全量一致，日志 `$TEMP/cs_b094_handoff_frontend_focus_20261003.log`。不是重跑前端全量。
   - 只在系统临时配置通过测试源码转换补充平台导航，业务断言保持：7 失败、239 通过；再补齐遗漏的行入口导航与新轨道行选择器：3 失败、243 通过。两次是诊断实验，不是仓库原测试正式通过，也没有将转换内容写回产品或测试文件。日志分别为 `$TEMP/cs_b094_navigation_diagnostic_20261003.log` 与 `$TEMP/cs_b094_navigation_diagnostic_selectors_20261003.log`。
   - 归因表覆盖原 39 项，不将失败数等同功能错误数：

| 原失败文件 | 数量 | 补导航与选择器后的原业务断言 | 剩余旧契约冲突 |
| --- | --- | --- | --- |
| ResultHistoryDrawer | 19 | 18 项通过 | 1 项仍要求聚合页同时显示平台平铺行 |
| DiscoveryHistoryMode | 9 | 9 项通过 | 无 |
| DiscoveryRecovery | 2 | 2 项通过 | 无 |
| DiscoveryScrapeOnly | 1 | 1 项通过 | 无 |
| DiscoveryView | 8 | 6 项通过 | 2 项仍要求显示冻结条件提示 |

   - 已核实依据：`specs/046-parallel-platform-flow/v2/tasks.md` 第 12 行记录用户确认顶部固定“聚合 / BOSS / 智联”，聚合只展示组合流程、平台页分别展示平台历史，并移除冻结条件提示。提交 `f6f60da` 的实际差异落实了这些变化，早于 B094。剩余 3 项与已确认契约冲突，不据此恢复已移除提示或旧混排。诊断后的 36 项只说明在当前正确入口下原业务断言成立。
   - 本轮不修改白名单外前端产品或测试，不删断言、不改验收换绿灯。原正式测试仍为 39 失败，旧测试维护属于 B096 后续范围，不能将诊断实验数写作正式前端通过。

5. 真实覆盖与完成判断（T034 继续未勾选）
   - 沿用真实 52、82、60 岗位三轮已完成、待确认为 0 的证据；前两轮全部 JD 可读，60 岗位轮只检查目标样本。本轮只确认正式服务与库状态，没有产生新的真实语义验收。
   - 领域固定抽查 23/25=92% 满足用户允许零星误判的口径，不是总体筛选准确率。严格办公工具-only 反例、其他条件全部满足的游戏和通用职能匹配正例仍未取得；其他明确角色正例与恢复场景也不得由自动化桩或现有开发岗位外推。
   - 已完成的是剩余检查、失败归因与本轮直接复审；完整 SPEC 的真实覆盖和全绿门禁仍有缺口。T032、T034、R008 不勾选为全部通过，用户最新 90% 边界优先，不为追求 100% 反复修改提示词。
   - 本轮仅同步本 Tasks 与主体 INDEX；原始验证日志全部保留，临时诊断配置在实验结束后清理，诊断输出作为失败归因证据保留。本轮无提交、暂存、合并、推送或发布。
   - 收口记录写入后，仓库卫生 14/14 通过（3.611 秒），日志 `$TEMP/cs_b094_handoff_hygiene_20261003.log`；`git diff --check` 无 whitespace 错误，`git status` 只有本 Tasks 与主体 INDEX 两份文档。CRLF 转 LF 提示不属于测试失败。该结果消除当前路径/未跟踪卫生缺口，不代表后端全量、前端正式配置或严格真实场景全部通过。

### 最终收口记录（2026-10-03，当前有效）

最新用户决定覆盖前文历史状态：允许修复测试；领域判断达到 90% 即可；暂停原失败用例和相关回归没有复现超时即算通过；修复原失败并执行聚焦测试，不重复全量，不自动提交、合并或推送。

- 交付结果：本次功能及测试返修已收口，可提交并交由用户测试。没有新增或变更领域提示词、前端产品、正式历史结果、账号或凭据。
- 前端修复：五个原失败文件明确选择夹具所属平台，使用当前轨道行选择器；分别检查聚合及平台页；冻结条件提示按既有用户确认移除，原始字段、JSON、内部版本不泄露断言保留。历史只读、计数、删除、日志、详情、键盘、恢复和平台锁定断言保留，另增默认聚合页守卫。正式配置聚焦 247/247 通过（15.66 秒），原 39 项失败消除；日志 `$TEMP/cs_b094_delivery_frontend_focus_20261003.log`。组件/页面使用模拟 HTTP 数据，不称真实浏览器验收。
- 前端类型检查：`npm exec vue-tsc -- --noEmit` 退出 0；日志 `$TEMP/cs_b094_delivery_typecheck_20261003.log`。前端产品源码未改，沿用 6.25 秒构建成功证据。
- 暂停及持久化：完整 `ConvergencePendingPersistenceTests` 类 32/32 通过（37.543 秒），覆盖原失败用例及相关持久化断言；临时数据库与外部边界桩，日志 `$TEMP/cs_b094_delivery_persistence_diagnostic_20261003.log`。原 10 秒超时未放宽，原异常未复现，按用户明确决定本轮通过；不声称原全量时序下的根因已查明或产品被修复。
- 门禁修复：旧产品字节冻结及从旧 B096 起点跨所有后续功能的范围检查，改为保护原历史清单及当前 B094 精确授权范围，基准 `f43ed37`，同时检查已提交及未提交改动；旧 Spec、76 项历史哈希未改。负向测试确认清单篡改和未授权文件仍会失败；原业务结构守卫保留，不跳过或豁免用例。文件 18/18 通过（3.306 秒），日志 `$TEMP/cs_b094_closeout_gate_20261003.log`。
- 领域效果：沿用 B094 聚焦 238 项通过及真实 52/82/60 岗位三轮证据。固定抽查 23/25=92% 达到用户确认标准，这是领域行为符合率，不是总体筛选准确率。严格办公工具反例及其他条件全部满足的游戏/通用职能正例未取得独立真实覆盖；不扩大到全部 SC 场景均已真实验收，不构成交付阻断。
- 审查：直接复查最新差异、范围、业务断言及负向门禁，无本次领域功能的未解决阻断；未使用子智能体。修改限六份测试及本主体 INDEX/Plan/Tasks，共九个文件，不新增依赖、迁移或文件。
- 验证口径：本轮修复后聚焦测试通过；后端 3610 项和前端 1561 项全量是返修前结果，本轮遵守禁止重跑全量的要求，不将旧全量改写为绿色。提交、合并、推送仍由用户决定。

- 最终联合确认：`uv run python -m unittest tests.test_b096_final_review tests.healthy_pipeline.test_pipeline_convergence_pending.ConvergencePendingPersistenceTests tests.test_repo_hygiene`，64/64 通过，56.478 秒；包含门禁 18 项、暂停及持久化 32 项、卫生 14 项，日志 `$TEMP/cs_b094_closeout_final_20261003.log`。本轮对应收口检查全部绿色，非全量重跑。`git diff --check` 通过，当前主题分支与九文件范围已核对，暂存区为空。
