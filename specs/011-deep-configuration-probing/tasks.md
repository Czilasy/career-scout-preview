# Tasks: 高级设置深度自动调优

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

SPEC011 分为两个大阶段：

1. **第一阶段：代码实施与审查**。内部包含多个依赖排序的代码任务，必须全部完成并由高级监督者验收。
2. **第二阶段：正式深度实验**。只有第一阶段整体通过后才解锁，并更换新的实验执行者。

不得把任一大阶段压缩成单个任务。第一阶段已完成，当前执行第二阶段。

## Part I - 代码实施与审查（已完成）

### Phase 1 - 配置与输入基础

**Goal**: 建立速度字段配置（含 JD 并发 Tab 数）、城市/关键词规范化、任务规模和不可变快照的唯一语义。

**Independent Test**: Quickstart Scenario A/B；9/10/49/50/200 页边界正确，未知城市阻断，任务启动后修改正式设置不影响任何阶段。

- [X] T001 Create RED tests for all speed fields, canonical serialization, physical validation, keyword normalization, city aliases, nationwide exclusivity, planned-page boundaries, and immutable digests in `tests/test_execution_config.py`
- [X] T002 Implement the shared execution configuration schema, canonical JSON/digest, task-scope normalization, size classification, and snapshot value objects in `webui/execution_config.py`
- [X] T003 Upgrade `data/city_codes.json` to carry canonical names, codes, explicit aliases, enabled status, and nationwide metadata while preserving packaging compatibility; update loader regression tests in `tests/test_chrome_setup.py`
- [X] T004 Implement backend-authoritative scope preview and validation without changing task workload fields in `webui/app.py`, with endpoint tests in `tests/test_webui_app.py`
- [X] T005 Create RED regression tests proving list, detail, rough-screen, fine-screen, and recrawl stages use one frozen config digest in `tests/test_healthy_pipeline.py` and `tests/test_pipeline_tasks_cleanup.py`
- [X] T006 Refactor `webui/pipeline_exec.py`, `webui/ai.py`, `webui/source.py`, and the relevant `webui/app.py` call sites to accept explicit immutable snapshots and remove runtime late-binding reads for active tasks

### Phase 2 - 模式版本、最近自定义与持久化基础

**Goal**: 建立 SQLite 权威配置状态、完整模式版本、原子应用/回退和旧 JSON 一次性迁移。

**Independent Test**: Quickstart Scenario C/K；三模式按任务大小取值，自定义往返零丢失，九槽位整体切换，`pages` 永不被模式覆盖。

- [X] T007 Create RED migration and transaction tests for advanced config state, complete mode versions, recent custom config, one-time JSON import, and atomic whole-version activation/rollback in `tests/test_webui_store.py`
- [X] T008 Add the advanced configuration and mode-version migrations plus transactional store methods in `webui/store.py`
- [X] T009 Replace the legacy advanced-settings GET/POST behavior with complete custom-save, mode-select, version-apply, and rollback contracts in `webui/app.py`; add contract coverage in `tests/test_webui_app.py`
- [X] T010 Add tests proving experiment temporary configs never overwrite active modes or recent custom settings in `tests/test_tuning.py` and `tests/test_webui_store.py`

### Phase 3 - 实验状态机、轮次与独占租约

**Goal**: 持久表达实验、输入、质量参考、候选、轮次、任务单、报告、测量事件和唯一执行租约。

**Independent Test**: Quickstart Scenario D/H；同时启动只有一个成功，重启后 confirmed 轮次不重复，未确认轮次进入 uncertain 并只重跑一次。

- [X] T011 Create RED migration/state-transition tests for all tuning entities and invariants from `data-model.md` in `tests/test_webui_store.py`
- [X] T012 Add tuning experiment, input, workload, quality reference, candidate, round, manifest, report, measurement, lease, and mode-version persistence in `webui/store.py`
- [X] T013 Create RED tests for atomic lease claim/heartbeat/release, ordinary-task conflict, stale reconciliation, confirmed-round idempotency, and uncertain-round recovery in `tests/test_tuning.py`
- [X] T014 Implement the deep tuning module interface, legal state transitions, lease coordination, and restart reconciliation in `webui/tuning.py`
- [X] T015 Integrate experiment lease checks with the existing single-thread pipeline executor and ordinary task start paths in `webui/app.py`, with regression coverage in `tests/test_healthy_pipeline.py`

### Phase 4 - 客观测量、完整性与质量参考

**Goal**: 让程序记录完整耗时、等待、冷却、重试、错误、终态守恒和逐项质量差异。

**Independent Test**: Quickstart Scenario G/H；可恢复错误时间计入总耗时，硬错误停止，缺失/重复/错配为零，敏感数据不进入事件。

- [X] T016 Create RED measurement tests for stage/batch/request/wait/retry/item-terminal events, monotonic durations, terminal conservation, and sensitive-field rejection in `tests/test_tuning.py`, `tests/test_chrome_setup.py`, and `tests/test_ai.py`
- [X] T017 Add the allowlisted measurement sink and round evidence aggregation to `webui/tuning.py`
- [X] T018 Instrument list and detail execution, including controlled waits, cooldowns, subprocess batches, terminal safe codes, and previously omitted overhead in `webui/pipeline_exec.py`, `webui/source.py`, and `scripts/boss_cdp_raw.py`
- [X] T019 Instrument AI attempts, backoff, truncation splitting, batch counts, request duration, and systemic error evidence in `webui/ai.py`
- [X] T020 Implement baseline result versioning, item-level comparison, normal-variation calculation, review-required differences, and reference digest enforcement in `webui/tuning.py` and `webui/store.py`

### Phase 5 - 执行任务单与报告协议 (User Story 3)

**Goal**: 让无历史上下文的低自主性执行者机械执行，未知情况只能阻断。

**Independent Test**: Quickstart Scenario E/F；缺字段、占位符、越界路径、摘要不一致均在执行前或报告确认时被拒绝。

- [X] T021 [US3] Create RED manifest/report validation tests covering every required field, placeholder language, path containment, immutable digests, forbidden actions, evidence mismatch, and blocked reports in `tests/test_tuning.py`
- [X] T022 [US3] Implement strict executor manifest issuance, immutable digesting, Markdown rendering, report validation, and program-evidence reconciliation in `webui/tuning.py`
- [X] T023 [US3] Add controller-only manifest/decision routes and executor manifest/execute/report/evidence routes from `contracts/http-api.md` in `webui/app.py`, with API tests in `tests/test_webui_app.py`
- [X] T024 [US3] Implement a clean-context fake-executor acceptance harness that proves a complete task is executable and an unknown condition returns a blocked report in `tests/test_tuning.py`

### Phase 6 - 分阶段 Runner、漏斗晋级与边界选择 (User Stories 1, 2, 4)

**Goal**: 支持五种轮次、合法前置数据复用、动态步长、候选晋级/淘汰、危险边界和剩余时间预测。

**Independent Test**: Quickstart Scenario I/J；明显差候选不晋级，边界候选不可应用，最终重复不因时间预测被削减。

- [X] T025 [US1] Create RED deterministic tests for list/detail/rough/fine/end-to-end rounds, permitted stage-input reuse, forbidden end-to-end reuse, and cross-version digest rejection in `tests/test_tuning.py`
- [X] T026 [US1] Implement the five round adapters and frozen artifact-manifest reuse rules in `webui/tuning.py` and `webui/pipeline_exec.py`
- [X] T027 [US1] Create RED tests for coarse-to-fine steps, no-gain pruning, boundary bracketing, median/tail comparison, pressure tie-breaks, and shared mode-slot configs in `tests/test_tuning.py`
- [X] T028 [US1] Implement candidate proposal bookkeeping, dynamic-step validation, funnel promotion, boundary classification, convergence, and remaining-time projection in `webui/tuning.py`
- [X] T029 [US2] Implement immediate hard-stop and manifest-bounded recoverable retry behavior across round adapters, preserving in-flight evidence without automatic mode downgrade in `webui/tuning.py`, `webui/pipeline_exec.py`, and `webui/ai.py`
- [X] T030 [US4] Add experiment create/confirm/status/cancel/resume/result routes with persisted progress and restart recovery in `webui/app.py`, with API and restart tests in `tests/test_tuning.py` and `tests/test_webui_app.py`

### Phase 7 - 前端模式与深度实验工作区 (User Stories 5, 6)

**Goal**: 前端只展示稳定、平衡、极限、自定义，并提供权威任务规模、实验进度、阻断、证据和整体应用/回退。

**Independent Test**: Quickstart Scenario A/C/K 与 Frontend Render Validation；运行范围锁定，未完成结果没有应用操作，桌面和窄屏无溢出或遮挡。

- [X] T031 [US5] Add frontend types and pure tests for canonical scope preview, task size, four selections, mode-slot responses, and recent-custom recovery in `webui/src/types.ts` and `webui/src/__tests__/discovery.spec.ts`
- [X] T032 [US5] Implement frontend scope/mode helpers and typed tuning API calls in `webui/src/discovery.ts` and `webui/src/api.ts`
- [X] T033 [US5] Implement and test the stable/balanced/extreme/custom selector without changing `pages` in `webui/src/components/ExecutionModeSelector.vue` and `webui/src/components/__tests__/ExecutionModeSelector.spec.ts`
- [X] T034 [US6] Implement and test the experiment creation, serial progress, current candidate/round, remaining estimate, blocking, evidence, apply, and rollback workspace in `webui/src/components/TuningWorkspace.vue` and `webui/src/components/__tests__/TuningWorkspace.spec.ts`
- [X] T035 [US5] Integrate canonical scope preview, task-range locking, mode/custom state, and runtime safe-node changes into `webui/src/views/DiscoveryView.vue` and `webui/src/views/__tests__/DiscoveryView.spec.ts`
- [X] T036 [US6] Integrate persisted experiment recovery, incomplete-result gating, expandable evidence, complete apply/rollback, and accessible responsive styles into `webui/src/views/DiscoveryView.vue`, `webui/src/App.vue`, and `webui/src/styles.css`

### Phase 8 - 第一阶段集成验收与交付

**Goal**: 完成全部代码、测试、文档、服务替换和真实渲染，证明实验室代码就绪但不运行正式深度实验。

**Independent Test**: `quickstart.md` 自动与确定性 Scenario A-K 全部通过，完整 Python 回归、前端测试/构建、服务新 PID、桌面/窄屏真实页面均有证据。

- [X] T037 Update `README.md`, `README.en.md`, `CHANGELOG.md`, `SKILL.md`, and `pyproject.toml` for user-visible behavior and synchronized versioning where required
- [x] T038 Run focused syntax/unit/contract tests from `quickstart.md`, fix only first-stage implementation defects in authorized files, and record exact commands/results in the code execution report
- [x] T039 Run full `python -m unittest discover -s tests`, frontend `npm test`, and `npm run build`; resolve regressions without weakening tests
- [x] T040 Replace the old WebUI process on port 5000, verify a new PID and `/api/version`, and record service evidence
- [x] T041 Perform real desktop 1440x900 and narrow 390x844 rendered validation of modes, locked scope, experiment states, evidence, and apply gating; save screenshots and pixel/overflow evidence
- [x] T042 Execute deterministic Quickstart Scenario A-K, including clean-context fake executor acceptance, and assemble the complete first-stage delivery report without running Real-Chain Gates 1-4
- [X] T043 Have the independent高级监督者 review the entire first-stage diff, contracts, automatic evidence, service replacement, and visual artifacts; resolve every rejection before marking Part I complete

### Part I Completion Gate

- T001-T043 全部完成并由高级监督者审查通过。
- 所有自动测试和构建退出码为 0，聚焦测试不能替代全量回归。
- 后端/前端修改后的旧服务已经被新 PID 替换并验证可访问。
- 桌面与窄屏均有真实渲染证据。
- Quickstart Scenario A-K 确定性通过。
- 不运行正式外部深度实验，不生成或应用最终三档参数。

## Part II - 正式深度实验（当前阶段）

**Entry Gate**: T001-T043 全部通过，高级监督者明确宣布第一阶段完成，并更换新的实验执行者。

### Phase 9 - 新基线与质量参考

- [X] T044 Create and user-confirm the frozen small/medium/large multi-structure workload version, with exact keywords, cities/nationwide, pages, artifact paths, digests, and controller-issued manifests under `tuning/<experiment-id>/`
- [X] T045 Execute the newly issued low-pressure baseline manifests, repeat until speed and item-level quality variation converge, and confirm the quality reference version from program evidence

### Phase 10 - 分阶段探测与边界细测

- [X] T046 Execute controller-issued list-stage single-field probes, promising combinations, and boundary refinements serially; return after every manifest for controller review
- [X] T047 Execute controller-issued detail-stage single-field probes, promising combinations, and boundary refinements serially using only the permitted frozen list input
- [X] T048 Execute controller-issued AI rough-stage probes serially using the frozen list-field input and confirmed reference rules
- [X] T049 Execute controller-issued AI fine-stage probes serially using the frozen JD input and confirmed reference rules
- [X] T050 Execute controller-issued combined candidates and record the first unacceptable configurations as non-applicable dangerous boundaries

### Phase 10A - 真实实验诊断与终态纠偏

**Goal**: 不重做第一阶段；只修复 T051 真实运行暴露的诊断证据、唯一硬错误、终态重试和零输入门禁缺陷。

- [X] T050A Add RED regression tests for attempt-level safe diagnostics, first-failure-then-success terminal uniqueness, retry-exhausted single hard-error aggregation, and zero-input rejection
- [X] T050B Implement complete backend diagnostic persistence, deterministic single hard-error aggregation/front-end projection, final-only item terminals, and zero-input validation without changing experiment search parameters
- [X] T050C Run focused and full regression, replace and verify the WebUI process, perform one independent complete review plus any bounded focused re-review, then execute exactly one real `match_concurrency=2` confirmation before resuming T051

### Phase 11 - 三档最终验证、报告与应用

- [ ] T051 Validate stable, balanced, and extreme candidates for every applicable small/medium/large slot using at least two workload structures and at least three confirmed repetitions per structure
- [ ] T052 Run all required final end-to-end rounds without intermediate reuse and confirm terminal conservation, quality, total-duration accounting, and restart evidence
- [ ] T053 Generate the concise and detailed final experiment report, require explicit user approval, then atomically apply or retain the complete mode version without changing recent custom settings

## Dependencies

```text
Part I:
T001-T006 配置基础
  → T007-T010 模式/自定义
  → T011-T015 状态/租约
  → T016-T020 测量/质量
  → T021-T024 执行协议
  → T025-T030 Runner/恢复
  → T031-T036 前端
  → T037-T043 集成交付与独立审查

Part II（锁定）:
T001-T043 全部通过
  → T044-T045 新基线
  → T046-T050 分阶段边界
  → T050A-T050C 真实实验纠偏
  → T051-T053 最终验证与应用
```

## Parallel Opportunities

无对外并行实施。第一阶段按依赖顺序签发和审查；第二阶段严格串行运行，以免共享资源污染性能证据。

## Implementation Strategy

1. Part I 的 T001-T043 已完成；当前严格串行执行 Part II 的 T044-T053。
2. 高级监督者按依赖一次签发一个代码任务，执行者完成后返回真实差异和证据。
3. 每个阶段通过后才进入下一个阶段，最后执行 T043 独立整体验收。
4. Part I 完成后停止，用户更换实验执行者。
5. Part II 的每个 manifest 都由控制者依据上一份合格程序报告填写精确参数，禁止提前猜测。

**MVP**: Part I 的 T001-T043 是完整代码实验室，任何局部任务都不足以宣布代码阶段完成。
