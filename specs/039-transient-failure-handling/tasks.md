# Tasks: 偶发失败重试与失败展示优化

**Spec**: [spec.md](./spec.md)

**Plan**: [plan.md](./plan.md)

**Status**: 已完成（2026-09-11 用户指令后全量实施并验证；结果与偏差见 [quickstart.md](./quickstart.md)「红基线」「验证结果」「实施偏差与说明」）

## Phase 1: Setup and red baseline

- [x] T001 Create retry test fixtures (fake source, evidence spy, resume maps) and failing S1/S2/S5 cases (retry-resume, zero-trace-on-success, skip-after-retry, no-retry-for-login) in `tests/test_transient_retry.py`
- [x] T002 [P] Add failing display cases in `webui/src/components/__tests__/TaskProgress.spec.ts`: no combo row list, hover tooltip on fail count showing `组合名：简单原因`, and settled counts (completed 14/16 + fail 2 + unstarted 0)
- [x] T003 Run the new focused tests before implementation and record the failing baseline in `specs/039-transient-failure-handling/quickstart.md`

## Phase 2: Foundational contracts

- [x] T004 Implement the transient retry whitelist, `is_transient_retryable(failed_code, *, retry_used)`, and `retry_event_payload(...)` in `webui/pipeline_exec_retry.py`（新文件，约 60–90 行，纯逻辑无反向依赖）
- [x] T005 [P] Add `build_zhilian_list_signal_map` with the `cdp_unavailable → source_cdp_unavailable` addition (mirroring the detail-side pattern) in `webui/source_zhilian_runtime_adapter.py`

## Phase 3: User Story 1 — 偶发失败自动重试一次

**Goal**: 偶发失败后自动重试一次（从断点续抓），重试成功零痕迹，重试仍失败才跳过。
**Independent test**: 第 9 页超时后从第 9 页续抓且不重抓前 8 页；重试成功后该组计入完成、失败数量为 0；同一组合合计尝试不超过 2 次。

- [x] T006 [US1] Add the per-combo `retry_used` flag shared by login-recheck retry, browser-lost restart retry, and the new transient retry in `webui/pipeline_exec_search.py`（净增长受控，禁越 800 行红线）
- [x] T007 [US1] Insert the transient retry branch between the browser-lost branch and failure classification in `webui/pipeline_exec_search.py`: record `retry_scheduled` via `evidence.record_fact`（info、required=False）、apply resume fields（恢复起始页 + 已抓岗位快照）、retry once
- [x] T008 [US1] Complete single-attempt-budget tests（失联重启重试后不再触发偶发重试；登录复核重试后同样不再触发）in `tests/test_transient_retry.py`

## Phase 4: User Story 2 — 失败低调展示

**Goal**: 失败仅显示数量；悬停弹小浮窗逐条显示简单原因；不再成排展示明细。
**Independent test**: 面板仅显示"失败 2"；悬停出现浮窗逐条"组合名：简单原因"；面板无成排明细列表。

- [x] T009 [US2] Remove the combo-issues row list rendering and its styles while keeping the `combo_issues` data source for the tooltip in `webui/src/components/TaskProgress.vue`
- [x] T010 [US2] Implement the hover tooltip on the fail count（最多 5 条、超出显示"另有 N 条"、`code_text` 优先缺失用 `reason`、不使用"已跳过"与"超时抓取"式定性）in `webui/src/components/TaskProgress.vue`
- [x] T011 [US2] Complete tooltip visibility, wording, overflow line, and no-row-list assertions in `webui/src/components/__tests__/TaskProgress.spec.ts`

## Phase 5: User Story 3 — 原因有名有姓，失联可自愈

**Goal**: 列表抓取的"连不上浏览器"类失败显示真实名称并恢复自动恢复资格。
**Independent test**: `cdp_unavailable` 显示"连不上调试浏览器"（非"未知"）；进入重启重试通道；重试后仍失联则任务按既有阻断语义暂停并提示。

- [x] T012 [US3] Route the Zhilian list signal map through `build_zhilian_list_signal_map` in `webui/source_zhilian_cdp.py`（净增长 ≤3 行）
- [x] T013 [US3] Complete name-resolution tests（列表 signal 补映射、不出现"未知抓取错误"、失联进入重启重试、仍失联硬停暂停）in `tests/test_transient_retry.py`

## Phase 6: User Story 4 — 收尾计数与事实一致

**Goal**: 任务收尾后完成/跳过/未开始计数与真实结论一致。
**Independent test**: 16 组中 14 完成 + 2 跳过时，面板显示已完成 14/16、失败 2、未开始 0，与进度和已抓岗位数不矛盾。

- [x] T014 [US4] Replace the fallback branch of `scrapeCountState`（写死"0 完成、全部未开始"）with backend truth（success_count + fail_count + total）in `webui/src/components/TaskProgress.vue`，运行中分支保持既有派生
- [x] T015 [US4] Complete settled-count consistency and running-state-unchanged cases in `webui/src/components/__tests__/TaskProgress.spec.ts`

## Phase 7: Verification and registration

- [x] T016 Run focused tests（`uv run python -m unittest tests.test_transient_retry -v` 与前端用例）and record exact results in `specs/039-transient-failure-handling/quickstart.md`
- [x] T017 Run `uv run python -m unittest discover -s tests` and record the exact result in `specs/039-transient-failure-handling/quickstart.md`
- [x] T018 Run `npm test` and `npm run build` in `webui/`, recording exact results in `specs/039-transient-failure-handling/quickstart.md`
- [x] T019 Run `uv run python -m unittest tests.test_repo_hygiene`, `git diff --check`, and `git status --short`; verify no root-level test artifacts and no changes to forbidden files listed in plan.md
- [x] T020 [P] Register `webui/pipeline_exec_retry.py` responsibility and reference direction（pipeline_exec_search → pipeline_exec_retry 单向）in `.specify/memory/constitution.md` 模块地图（不改原则、不改版本号）
- [x] T021 [P] Add user-facing entries in `CHANGELOG.md` following 更新说明写作规范（每条一句话 ≤25 字；如：修复：偶发抓取失败会自动重试一次 / 优化：抓取失败只显示数量，悬停可看原因 / 修复：连不上浏览器不再显示为未知错误 / 修复：抓取结束后完成数量不再显示错误）
- [x] T022 Update task checkboxes, verification evidence, and final status in `specs/039-transient-failure-handling/tasks.md` without changing frozen requirements
- [x] T023 [US4-增补 FR-016] 恢复态快照对齐真实计数：启动/刷新加载上一轮时按该轮任务快照补齐完成/跳过/未开始与失败留痕；「结束并保存结果」后的合成快照保留真实计数（`useDiscoveryResults.ts` / `useDiscoveryExecution.ts` / `useDiscoveryState.ts` / `types.ts`）
- [x] T024 [US4-增补 FR-016] 用例：恢复上一轮时面板显示真实完成/跳过计数并支持悬停查看失败留痕（`DiscoveryView.spec.ts`）

## Dependencies

- Phase 2（T004/T005）先于 Phase 3/5 的实现任务：T007 依赖 T004，T012 依赖 T005。
- US1（T006–T008）与 US2（T009–T011）、US4（T014–T015）互相独立；US2 可先用快照桩独立交付，失败"只计最终跳过"的语义在 US1 落地后自然生效。
- US3（T012–T013）依赖 T005 与 T006/T007 的重试通道（重启重试路径为既有行为，US3 仅恢复其触发资格）。
- Phase 7（T016–T022）依赖全部实现任务完成。
- 停滞点：本文件为 tasks 阶段最终产物；按项目规则，实施需用户明确指令后才启动。（2026-09-11 用户明确指令「开始全量执行 spec039」后已实施。）

## Parallel execution

- T001 ∥ T002（不同文件）
- T004 ∥ T005（不同文件）
- T009/T010 完成后 T011 ∥ T013 ∥ T015（不同测试文件/组件内独立区块）
- T020 ∥ T021（不同文件）

## Implementation strategy

- **MVP**：T004 → T006 → T007 → T008 → T016–T019（重试自愈即用户最核心诉求，独立可交付）。
- **增量**：US2 展示 → US3 正名 → US4 计数，各自独立验收。
- 每个故事完成后均可独立验证（见各 Independent test），最后统一跑 Phase 7 门禁。

## 实施结果摘要（2026-09-11）

- 后端聚焦 `tests/test_transient_retry.py`：**16 例通过**（含审查后补的「额度按组合各一份」「重试期间暂停」两例）；关闭重试通道复跑有 3 例失败，证明用例确实覆盖新增行为。
- 后端全量：见 quickstart「验证结果」最新记录（唯一失败为未入库文件的卫生检查）。
- 前端：`npm test` **872 例全通过**；`npm run build` 成功。
- 真机验收（模拟用户视角，真实库 + 真实智联）：抓取跑通并验证收尾计数（已完成 2/2、未开始 0）；关闭调试浏览器验证列表侧失联自动重启续抓与阻断提示；FR-016 落地后复验恢复态计数（0/40 → 2/2）。过程、证据与发现处置见 quickstart「真机验收」。
- 计划允许清单外的必要改动 2 处（`webui/whitebox.py` 登记事件类型、2 个既有测试文件同步旧契约断言）与计划行数漂移已记入 quickstart。
