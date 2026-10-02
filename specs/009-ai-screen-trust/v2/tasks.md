# Tasks: 垂直领域相关岗位覆盖（B094）

**Input**：[spec.md](spec.md)、[plan.md](plan.md)、[research.md](research.md)、[data-model.md](data-model.md)、[contracts/domain-screening.md](contracts/domain-screening.md)、[quickstart.md](quickstart.md)。

**状态**：2026-10-02 仅生成任务；37 项全部未执行。用户明确要求做到 Tasks 为止，不进入产品实施。本清单不构成实施、提交、推送、合并或发布授权。

## File Boundaries

允许的产品/测试文件以 plan.md 的精确白名单为准，任务逐条列明路径。新增：`webui/ai_domain_policy.py`、`webui/ai_domain_context.py`、`tests/ai/test_ai_domain_context.py`、`tests/ai/test_ai_domain_recall.py`。其余为既有 AI 域、runner、screen_flow 及指定测试；复审补入 `webui/runners/ai_screen_jd.py` 的缺项准备、`webui/store_screen_resume_mixin.py` 的 JD 只读模式、`webui/error_registry.py` / `webui/ai_screen_failure.py` / `webui/flow_task_state.py` 的错误注册/透传及 `tests/test_error_registry.py` / `tests/test_b096_production_flow.py` 对应回归；仅实施完成后同步 README、CHANGELOG 和宪法模块地图。

禁止修改：历史 V1 和其他 Spec；门面；`scripts/**` 抓取；`webui/src/**`；API、Flow 状态转换/身份编排（白名单仅可补错误分类透传）、store 写入/迁移及白名单外 store（只允许 JD 只读助手）、数据库结构；正式历史结果、账号凭据；B071、B106、无调用过滤函数；白名单外任何文件。

引用方向：runner → context → 既有公开绑定/store 服务；筛选/提示词/context/screen_flow → 领域纯 policy；平台字段留在适配器。不得反向 import API/app、私有跨模块符号或将平台名带入纯 policy。

行数门禁：Python 红线 800，预警 600；626 行 ai_screening 和 640 行 runner 只做必要接线且不增长，新职责分流。其余预算见 plan；如必须越过边界，先报告阻断，不能以清单自动增权。

## Verification Gate

变化由失败测试定义，开发/返修只跑聚焦和直接相邻回归。测试构造数据、假模型、临时数据库只证明自动化契约；真实 E2E 必须经产品入口用已就绪真实账号和岗位检查结果内容。

最终仅安排一项后端全量任务 T032，位于整条链收敛后；随后前端测试、真实 E2E、构建、卫生。失败留清单，先聚焦修复，相关改动并重新收敛后才允许必要最终确认。所有日志只写系统临时目录，当轮清理自产中转文件，不将凭据/正式正文提交公开仓库。

## Phase 1: Setup

- [ ] T001 复核 `specs/009-ai-screen-trust/INDEX.md`、`specs/009-ai-screen-trust/v2/spec.md`、`specs/009-ai-screen-trust/v2/plan.md` 与 `.specify/memory/constitution.md`；核实当前主题分支、文件白名单和行数，不创建平行规格或新增依赖。

## Phase 2: Foundational

先为来源与兼容行为定义失败测试，再实现共享能力。所有故事依赖该阶段。

- [ ] T002 在 `tests/ai/test_ai_domain_context.py` 定义失败用例：V2 原始组、同码不同组、覆盖/清空、实际选择变化、旧格式、损坏快照、错误 Track/platform/profile 不得借条件（FR-007）。
- [ ] T003 新增 `webui/ai_domain_policy.py` 的平台无关纯契约：实际选择优先级、稳定标签归一化、整组语义及内部版本/摘要；不建关键词词库，不读 store（FR-005、FR-007）。
- [ ] T004 新增 `webui/ai_domain_context.py`：复用公开 `resolve_flow_binding` 与 `TaskStore.get_flow` 读取准确 Track，派生领域上下文；以既有异常链反馈损坏/冲突，禁止默认为不限（FR-007）。
- [ ] T005 在 `tests/test_screen_flow.py`、`tests/ai/test_ai_domain_context.py`、`tests/test_error_registry.py`、`tests/test_b096_production_flow.py` 定义失败回归：自身判定提前返回、同源回退、旧版本、同码不同组、显式恢复不兼容、相同版本恢复、无领域原行为；兼容失败分类及安全说明必须通过公开失败入口在 run/task/Track 读取中一致可见、刷新后保留、补偿错误不吞（FR-001、FR-006、FR-007）。
- [ ] T006 修改 `webui/screen_flow.py` 与 `webui/ai_domain_policy.py` 的兼容助手：自动候选在返回前检查版本和预期完整语义、不兼容候选跳过后可正常新建筛选，恢复前再次检查，同源回退逐条守卫；保留旧调用签名兼容；只依赖 store 公开读取和 policy，不反向 import 将调用 screen_flow 的 context，不把不兼容缓存默认为可用（FR-001、FR-007）。
- [ ] T007 在 `tests/ai/test_ai_domain_context.py`、`tests/ai/test_ai_domain_recall.py` 定义独立 JD 资料通道失败用例：resume_from_run_id 为空且旧判定不兼容，旧检查点/结果表 JD 仍可达；文件缺失/部分检查点与结果表互补（临时真实 TaskStore 的 is_dropped=1 行有有效 JD，验证新 include_dropped 模式可取且默认旧模式不变）、多运行互补/优先级、同 ID 跨来源/平台/画像不复用、损坏资料失败、缓存齐全不启动详情 CDP、部分缓存只抓缺项（FR-001、FR-003、FR-006）。
- [ ] T008 在 `webui/ai_domain_context.py`、`webui/screen_flow.py`、`webui/store_screen_resume_mixin.py`、`webui/runners/ai_screen_task.py`、`webui/runners/ai_screen_jd.py` 接入与判定分开的只读资料加载：无 statuses 的 latest_screen_runs_for_source 全部候选、load_resume_jd/include_dropped=True 合并文件与结果表，底层公开 JD 只读助手同参数不按旧 is_dropped 过滤（默认 False 保持原行为）、身份与当前岗位集合校验、只补缺；新建领域 run 不依赖 resume_from_run_id，详情 CDP 准备前计算缺项，全部资料已有只回填，部分只抓缺项；不删除旧文件/结果或读旧判定 checkpoint，主文件不增长（FR-001、FR-003、FR-006）。
- [ ] T009 修改 `webui/error_registry.py`、`webui/ai_screen_failure.py`、`webui/flow_task_state.py`：唯一登记 screening_policy_incompatible 的安全说明/恢复语义，现有失败入口白名单保留分类并派生说明；既有 failed 收口、不自动重试旧判定、不归为账号/风控、不传原始异常正文，不改状态转换/身份逻辑；聚焦通过 T005 定义的注册和对外字段回归（FR-001、FR-006）。
- [ ] T010 修改 `webui/runners/ai_screen_task.py` 的必要接线与 `webui/ai_domain_context.py` 的元数据组装：先检查旧上下文再写入新 run.execution_params；显式旧规则续跑在当前规则元数据覆盖和粗精筛 checkpoint/阶段读取前单独捕获登记的类型化异常，调用 persist_ai_worker_failure，保留具体错误与对外安全说明；补偿失败沿 FlowStateClosureError，不能落入通用 internal_error；无领域恢复保持原路径，主文件不增长（FR-001、FR-006、FR-007）。

**Checkpoint**：来源隔离、判定恢复守卫、JD 资料复用和错误分类透传聚焦通过；完成后才能接入 AI 筛选。不得修改历史结果、清理旧判定表、修改 store 写入/迁移或白名单外 store。

## Phase 3: User Story 1 — 行业标签之外的相关岗位（P1）

**独立验收**：现有行业入口筛选后，分类不同的相关工作与主营明确相关公司的通用职能可进入结果，工具/宣传提词不能独自证明相关。其他条件和画像满足；自动化与真实验收分开。

- [ ] T011 [US1] 在 `tests/ai/test_ai_domain_recall.py` 定义从真实 runner/JD/精筛协作到模型边界的失败用例（列表/详情边界为明确假件、模型为桩）：分类不同的销售/运营/交付/支持及通用职能不被提前丢弃，缺少粗筛证据可到详情，列表公司/分类背景保留，JD 第 1500 字后真实主营原文完整到达模型，不直接给 match_jds 塞不存在的公司主营字段；工具/宣传提词反例口径（FR-001 至 FR-004、SC-001、SC-002）。
- [ ] T012 [US1] 修改 `webui/ai_platform_adapter.py`，按 `data-model.md` 来源表提供原始列表公司/分类背景、完整 JD 事实及实际平台选择标签；不依赖未传递的额外详情字段；两平台不编造公司简介，公司名/分类不作主营证明（FR-001、FR-003、FR-007）。
- [ ] T013 [US1] 修改 `webui/ai_filters.py`，解除 industry 编码不相交硬排除，接领域语义描述；保持其他平台筛选字段和已确认冲突的原规则（FR-002、FR-006）。
- [ ] T014 [US1] 修改 `webui/prompt_texts.py` 与 `webui/ai_domain_policy.py`，形成共享领域约束：工作/产品/主营、通用职能、偶然提词、整组/多选、未知不得编造以及其他条件不豁免（FR-001 至 FR-007）。
- [ ] T015 [US1] 修改 `webui/ai_prompts.py`，让粗筛和精筛复用领域约束，承接粗筛文本组装；保持输出解析、画像和无选择路径原契约（FR-001、FR-004、FR-006）。
- [ ] T016 [US1] 修改 `webui/ai_screening.py` 的必要接线：两阶段不因行业分类或通用标题硬剔除，领域生效时完整可用 JD/业务事实进入既有调用；无选择输入保持原行为，主文件不增长（FR-001、FR-002、FR-003）。
- [ ] T017 [US1] 修改 `webui/runners/ai_screen_task.py` 与 `webui/ai_domain_context.py`，把同一派生领域上下文交粗筛/精筛并在新建、暂停恢复路径保留内部元数据；不改变绑定/任务状态机与模型调用预算（FR-001、FR-007）。
- [ ] T018 [US1] 在 `tests/ai/test_ai_platform_filters.py`、`tests/test_ai_prompts.py` 补齐两平台输入事实、硬条件描述、粗精筛共享口径与无选择兼容；复用已有假件（FR-001 至 FR-004、FR-006）。
- [ ] T019 [US1] 按 `specs/009-ai-screen-trust/v2/quickstart.md` 聚焦运行 `tests/ai/test_ai_domain_recall.py`、`tests/ai/test_ai_platform_filters.py`、`tests/test_ai_prompts.py` 的 US1 用例；记录模型桩测试限制，不宣称真实语义验收（SC-001、SC-002）。

## Phase 4: User Story 2 — 多个领域扩大覆盖（P1）

**独立验收**：同选两个领域，分别只相关其中之一的岗位都能进入结果；均不相关的岗位不能自动相关。组选项按原始含义，覆盖不与原组选项错误合并。

- [ ] T020 [US2] 在 `tests/ai/test_ai_domain_context.py`、`tests/ai/test_ai_domain_recall.py` 定义并集、任意一个相关、均不相关、软件业务满足互联网整组，以及不同原始组选项同平台码的对照失败用例（FR-005、FR-007、SC-003、SC-005）。
- [ ] T021 [US2] 在 `webui/ai_domain_policy.py`、`webui/ai_prompts.py` 完善并集/整组组装，保证粗精筛无同时满足全部领域或只接受 AI 的歧义；必要修正限定这些共享模块（FR-005、FR-007）。
- [ ] T022 [US2] 在 `webui/ai_platform_adapter.py` 验证并必要修正旧格式/实际平台覆盖的标签适配，保留 V2 原始组含义，不反向猜测统一组或改前端映射（FR-007）。
- [ ] T023 [US2] 按 `specs/009-ai-screen-trust/v2/quickstart.md` 聚焦运行两个新测试模块的 US2 场景；软件相关、单领域相关两类及同码不同组均有输入和结果契约证据（SC-003、SC-005）。

## Phase 5: User Story 3 — 保持其他求职条件（P1）

**独立验收**：同一个领域相关岗位在其他条件满足/明确冲突时按原规则处理；未知、软性要求、画像约束和无领域选择保持既有语义。

- [ ] T024 [US3] 在 `tests/ai/test_ai_domain_recall.py`、`tests/ai/test_ai_platform_filters.py`、`tests/test_ai_prompts.py` 定义其他硬条件、未知口径、明确画像冲突、无领域选择的失败/回归对照（FR-006、SC-004）。
- [ ] T025 [US3] 在 `webui/ai_filters.py`、`webui/ai_prompts.py`、`webui/prompt_texts.py` 必要修正领域与其他条件的组合，解除分类误排但不豁免画像和其他筛选条件（FR-006）。
- [ ] T026 [US3] 在 `tests/test_screen_flow.py`、`tests/ai/test_ai_domain_context.py` 验证本轮版本守卫不削弱同来源、完整条件、画像/事实及身份一致要求；无领域沿用旧恢复（FR-006）。
- [ ] T027 [US3] 在 `tests/ai/test_ai_domain_recall.py` 使用已有 `tests/test_workbench_fixtures.py` / `tests/ai/harness.py` 假件测试 runner 元数据保留、真实 JD/精筛函数传递以及新建领域 run 的独立资料通道；验证不兼容续跑不进入粗精筛且不读旧 checkpoint，兼容恢复与新建同源筛选可达；新筛选不复用旧 verdict/checkpoint、却实际复用旧完整 JD，列表公司背景与 JD 主营原文到达模型、缓存齐全详情阶段零 CDP/抓取调用、缺项只抓缺项（FR-001、FR-006、FR-007）。
- [ ] T028 [US3] 按 `specs/009-ai-screen-trust/v2/quickstart.md` 运行聚焦集合及既有 `tests/test_b096_production_flow.py` 相邻自动化回归；失败先定位共同根因，不扩到其他功能（SC-004）。

## Phase 6: Convergence and Final Verification

- [ ] T029 对照 `specs/009-ai-screen-trust/v2/spec.md`、`specs/009-ai-screen-trust/v2/plan.md`、`specs/009-ai-screen-trust/v2/tasks.md` 与白名单实现逐项做 converge；缺口只记录本需求范围内任务，完成聚焦修复后再进入最终全量。
- [ ] T030 在实际行为落地后同步 `README.md`、`CHANGELOG.md`；在 `.specify/memory/constitution.md` 模块地图仅登记 `webui/ai_domain_policy.py`、`webui/ai_domain_context.py` 职责；不修改原则、不提前宣称可用。
- [ ] T031 按 `specs/009-ai-screen-trust/v2/plan.md` 对全部允许文件做静态差异/引用/行数核对，检查 `webui/ai_domain_policy.py` 平台无关、两个预警主文件不增长、无新依赖/数据库结构、凭据或无关差异；阻断先聚焦处理。
- [ ] T032 依据 `specs/009-ai-screen-trust/v2/quickstart.md`，整条交付链收敛后仅一次运行 `uv run python -m unittest discover -s tests`；输出留系统临时目录，失败保留清单，无相关改动不得立即重跑全量。
- [ ] T033 依据 `specs/009-ai-screen-trust/v2/quickstart.md` 在 `webui/` 执行 `npm test`，记录结果；不得借测试失败修改白名单外前端实现。
- [ ] T034 按 `specs/009-ai-screen-trust/v2/quickstart.md` 从现有行业选择到结果内容做真实 E2E，覆盖三条故事和 SC-001 至 SC-005；先确认真实输入和允许新 run 写入边界，缺少前置条件列未验证，不用临时数据库或模型桩替代。
- [ ] T035 依据 `specs/009-ai-screen-trust/v2/quickstart.md` 在 `webui/` 执行 `npm run build`；构建产物不入库，本项不授权打包或发布。
- [ ] T036 依据 `specs/009-ai-screen-trust/v2/quickstart.md` 执行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`；检查允许文件和意外产物，卫生失败不绕过，未授权不得暂存提交或强制添加。
- [ ] T037 将实际证据及未验证范围记录于 `specs/009-ai-screen-trust/v2/tasks.md`，同步 `specs/009-ai-screen-trust/INDEX.md` 的实施阶段状态；仅有对应证据才勾选任务，区分单元/集成/流程自动化与真实 E2E，停止于获授权范围。

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

任务统计：Setup 1，Foundational 9，US1 9，US2 4，US3 5，Convergence/Final 9，共 37。所有任务保持未勾选；本轮完成的是设计与清单生成。
