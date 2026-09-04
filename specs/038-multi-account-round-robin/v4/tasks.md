# Tasks: 多账号轮询分摊抓取可靠性修复（B091 V4）

**Spec**: [spec.md](./spec.md)

**Plan**: [plan.md](./plan.md)

**Status**: 待实施

## Phase 1: Setup and red baseline

- [ ] T001 Create reusable fake accounts, sources, event store, and deterministic job fixtures in `tests/test_r2_rotation_v4.py`
- [ ] T002 [P] Create attempt/artifact/pending test fixtures in `tests/test_detail_attempts_v4.py`
- [ ] T003 [P] Create BOSS and Zhilian clone-isolation fixtures in `tests/source/test_source_account_isolation_v4.py`
- [ ] T004 Run the new V4 focused tests before implementation and record the failing assertions under the red-baseline section of `specs/038-multi-account-round-robin/v4/quickstart.md`

## Phase 2: Foundational contracts

- [ ] T005 Add failing snapshot round-trip, identity mismatch, and damaged-checkpoint tests in `tests/test_r2_rotation_v4.py`
- [ ] T006 [P] Add failing reservation/request-start/request-terminal/account-summary contract tests in `tests/test_detail_attempts_v4.py`
- [ ] T007 [P] Replace the shared-breaker expectation with account-isolated mutable-state expectations in `tests/test_account_round_robin.py`

## Phase 3: User Story 1 — R2 全程按配额真实轮询

**Goal**: 一个任务的所有详情分块共享连续轮询状态。

**Independent test**: 六账号、配额 200、1048 个无失败岗位的实际请求分布为 `200/200/200/200/200/48`；11 个 20 条分块的第 11 块由第二账号承担。

- [ ] T008 [US1] Implement task-level R2 session, quota consumption, snapshot export, and restore validation in `webui/r2_rotation_session.py`
- [ ] T009 [US1] Create or restore one R2 session outside the outer detail chunk loop and save it at progress/pause boundaries in `webui/runners/ai_screen_jd.py`
- [ ] T010 [US1] Accept and consume the caller-owned R2 session without recreating it for each call in `webui/pipeline_exec_details.py`
- [ ] T011 [US1] Complete cross-chunk, heterogeneous-quota, multi-round, and six-account distribution tests in `tests/test_r2_rotation_v4.py`

## Phase 4: User Story 2 — 账号级熔断隔离与真实接力

**Goal**: 一个账号的本地阻断不传播到其他账号，接力账号真正启动请求。

**Independent test**: 第一个账号 18 成功 + 2 硬阻断，第二个账号实际请求并完成 2 条；只有第一个账号标记限流。

- [ ] T012 [US2] Stop sharing account-scoped breaker and mutable executor hooks while preserving safe configuration and cancellation semantics in `webui/account_round_robin.py`
- [ ] T013 [US2] Ensure binding failures and pre-request local short circuits do not mark a new account as platform-limited in `webui/account_round_robin.py`
- [ ] T014 [US2] Complete BOSS/Zhilian isolation and second-account real-invocation tests in `tests/source/test_source_account_isolation_v4.py`
- [ ] T015 [US2] Add the 18-success-plus-2-handoff and all-accounts-genuinely-blocked pause integration cases in `tests/test_r2_rotation_v4.py`

## Phase 5: User Story 3 — 按账号核对真实工作与完整证据

**Goal**: 预留、真实请求、终态、接力和成功汇总可以相互核对，重试产物不覆盖。

**Independent test**: 构造正常轮换、局部成功、接力和本地短路，账号唯一成功数之和等于详情成功总数，每次尝试有不同产物身份。

- [ ] T016 [US3] Implement attempt identity, unique artifact path, terminal-count validation, and per-account unique-success aggregation in `webui/detail_attempts.py`
- [ ] T017 [US3] Preserve reservation compatibility and add request-start, request-terminal, and account-summary events in `webui/account_round_robin_observability.py`
- [ ] T018 [US3] Emit start only immediately before a real source call, emit terminal afterward, and use a fresh artifact for every retry/handoff in `webui/pipeline_exec_details.py`
- [ ] T019 [US3] Complete allocation-versus-request, 1146-versus-1048-style retry accounting, artifact uniqueness, and summary reconciliation tests in `tests/test_detail_attempts_v4.py`

## Phase 6: User Story 4 — 暂停恢复后状态一致

**Goal**: 恢复沿用轮询断点，成功岗位不重复抓，旧 JD 失败被精确清理。

**Independent test**: 中途暂停后继续完成剩余岗位；已成功岗位重复请求为 0，已解决 JD pending 为 0，未解决 AI pending 保留。

- [ ] T020 [US4] Restore saved rotation state, honor explicit resume-account override, and pause on invalid snapshot in `webui/runners/ai_screen_jd.py`
- [ ] T021 [US4] Delete only the resolved JD failure records for successful jobs while preserving unresolved JD/AI records in `webui/runners/ai_screen_jd.py`
- [ ] T022 [US4] Complete pause/resume, explicit-account override, damaged-snapshot, duplicate-prevention, and pending-cleanup tests in `tests/test_r2_rotation_v4.py`
- [ ] T023 [P] [US4] Complete resolved-versus-unresolved pending record tests in `tests/test_detail_attempts_v4.py`

## Phase 7: Compatibility, documentation, and verification

- [ ] T024 Run existing R1/R2 round-robin and source tests, fixing only regressions caused by V4 within `webui/account_round_robin.py`
- [ ] T025 [P] Register `webui/r2_rotation_session.py` and `webui/detail_attempts.py` responsibilities and reference direction in `.specify/memory/constitution.md`
- [ ] T026 [P] Add one user-facing repair entry for real multi-account detail distribution and handoff in `CHANGELOG.md`
- [ ] T027 Run all V4 focused tests and preserve green command output in the system temporary directory referenced from `specs/038-multi-account-round-robin/v4/quickstart.md`
- [ ] T028 Run `uv run python -m unittest discover -s tests` and record the exact pass/fail result in `specs/038-multi-account-round-robin/v4/quickstart.md`
- [ ] T029 Run `npm test` and `npm run build` from `webui/`, recording exact results in `specs/038-multi-account-round-robin/v4/quickstart.md`
- [ ] T030 Run `uv run python -m unittest tests.test_repo_hygiene`, `git diff --check`, and `git status --short`; verify no root-level test artifacts and no changes under `specs/033-log-whitebox/`
- [ ] T031 Request an independent read-only review against `specs/038-multi-account-round-robin/v4/spec.md` and resolve only confirmed V4 blockers
- [ ] T032 After explicit user authorization, execute the minimal formal-account E2E in `specs/038-multi-account-round-robin/v4/quickstart.md`; otherwise record “真实账号端到端待验收” without claiming full completion
- [ ] T033 Update task checkboxes, verification evidence, and final implementation status in `specs/038-multi-account-round-robin/v4/tasks.md` without changing frozen requirements

## Dependencies

- Phase 1 must complete before any implementation; T004 preserves the red baseline.
- Phase 2 defines shared contracts and can run after fixtures exist.
- US1 is foundational for US2, US3, and US4 because all three consume the same task-level session.
- US2 and US3 may proceed in parallel after US1 if they modify separate files; their `pipeline_exec_details.py` integration must be serialized.
- US4 depends on the US1 snapshot and US3 attempt identity.
- Final verification starts only after US1–US4 pass their independent tests.

## Parallel Opportunities

- T002 and T003 can run in parallel after T001.
- T005, T006, and T007 can run in parallel because they target separate tests/contracts.
- T014 can run in parallel with T016–T017 after T012 is stable.
- T023 can run in parallel with T020–T022 once the pending cleanup contract is fixed.
- T025 and T026 can run in parallel with focused regression verification.

## Implementation Strategy

1. Build the red tests from the exact failure chain before editing production code.
2. Deliver US1 first; without persistent rotation, later handoff and accounting results are not trustworthy.
3. Deliver US2 next to restore real account isolation and handoff.
4. Deliver US3 so actual work can be proven independently of reservation logs.
5. Deliver US4 to close resume and stale-state gaps.
6. Run full gates and independent review; real account E2E remains separately authorized.

## Format Validation

- Total tasks: 33.
- US1 tasks: 4; US2 tasks: 4; US3 tasks: 4; US4 tasks: 4.
- Every task uses the required checkbox, sequential task ID, optional `[P]`, required story label inside story phases, and an exact file path.
