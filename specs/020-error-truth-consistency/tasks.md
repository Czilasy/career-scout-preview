# Tasks: 错误如实呈现与数据口径一致（020）

**Input**: Design documents from `/specs/020-error-truth-consistency/`

**Prerequisites**: plan.md（已建）、spec.md（已建）、research.md、data-model.md、contracts/error-faithfulness.md

**Tests**: Spec 明确要求失败测试先行（修 bug 铁律），每个故事先写复现测试再修。

## File Boundaries

（摘自 plan.md，任务不得越界）

- **Allowed files**: `webui/source.py`、`webui/screen_flow.py`、`webui/app.py`（三段既有行为）、`webui/result_rounds.py`、`webui/store.py`（delete_profile + 新降级方法）、`webui/src/api.ts`、`webui/src/composables/useScreenRoundFlow.ts`、`webui/src/views/DiscoveryView.vue`（一行重置 + 可能的 disabled 修正）、`specs/018-screening-chain-bugfix/spec.md`（两处修订）、测试文件（`tests/test_source.py`、`tests/test_screen_flow.py`、`tests/test_webui_app.py`、`tests/test_webui_store.py`、`tests/test_result_rounds.py`、`webui/src/__tests__/errorCodes.spec.ts`、`webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`）、`webui/dist/**`（构建产物）
- **Forbidden files**: `webui/store_migrations.py`、`webui/pipeline_exec.py`、`webui/task_runners.py`、`webui/ai.py`、`webui/cross_platform_dedupe.py`、`webui/error_registry.py`、`scripts/**`、`hooks/**`、`packaging/**`、`specs/001-017`、`specs/019`、版本/CHANGELOG
- **New files**: 无生产代码；`specs/020-error-truth-consistency/**`（文档）
- **Reference direction**: `app.py → result_rounds → store`；`api.ts → errorCodes.ts`；无反向依赖
- **Line gate**: 见 plan.md（source ≤2790 / store ≤4930 / app ≤9400 / result_rounds ≤300）

## Verification Gate (task-type aware)

- 功能交付：聚焦测试 + 后端全量 `uv run python -m unittest discover -s tests` + 前端 `npm test` + `npm run build` + 卫生测试。
- 前端源码改动 → `webui/dist` 重建并提交。
- 提交身份 `czyooutzilas@gmail.com`；Conventional Commits。

## Phase 1: Setup

**Purpose**: 建立绿色基线（当前后端全量与前端 452 用例应全绿）。

- [X] T001 运行聚焦基线：`uv run python -m unittest tests.test_source tests.test_screen_flow tests.test_webui_app tests.test_webui_store tests.test_result_rounds tests.test_error_registry` 与 `cd webui && npm test`，确认改动前全绿（如有意外红，先停下核对环境）

## Phase 2: US1 熔断器如实报告 + 可复位

**Story Goal**: 登录开闸报登录、风控开闸报风控；冷却期满 + preflight 通过可复位。独立验证：`uv run python -m unittest tests.test_source`。

- [X] T002 [US1] 失败测试先行：`tests/test_source.py` 新增熔断器用例组——(a) 连续 2 次 `source_login_required` 开闸后 `fetch_list` 返回 `source_login_required` 而非 `source_blocked`；(b) 风控类 signal（verification/rate_limited/blocked）开闸后返回对应码；(c) 冷却未满：不调用 preflight、返回开闸失败；(d) 冷却期满 + preflight 成功：熔断器复位且本次抓取继续发起（mock `_run_command` 成功）；(e) 冷却期满 + preflight 失败：不复位、返回开闸失败；(f) 智联批量开闸（串行与并行路径）失败码透传；(g) `open_failure_code()` 无信号时回落 `source_blocked`；既有 breaker 用例（866/1048/1077 等）保持通过
- [X] T003 [US1] 实现：`webui/source.py`——`SourceCircuitBreaker.open_failure_code()`/`cooldown_elapsed()` 只读辅助；Boss/智联 source 私有恢复方法（开闸且冷却期满 → `self.preflight()` → `try_reset(outcome.ok)`，preflight 异常视为失败）；5 处开闸检查点失败码改 `open_failure_code()`，批量级检查点（~599/724/930/2521）先试恢复，逐岗位检查点（~2441）只透传
- [X] T004 [US1] 回归：`uv run python -m unittest tests.test_source tests.test_error_registry tests.test_healthy_pipeline tests.test_browser_recovery`，确认 `source_cdp_unavailable` 自动重启链与既有信号链路零回归

## Phase 3: US2 错误码中文映射接入

**Story Goal**: 后端只回机器码时前端显示中文。独立验证：`cd webui && npm test -- errorCodes.spec.ts`。

- [X] T005 [US2] 失败测试先行：`webui/src/__tests__/errorCodes.spec.ts` 新增——(a) payload 仅含 `error_code: "job_offline"`（映射表内）时 ApiError.message 为「岗位已下架」；(b) `user_message`/`message`/`error_reason` 仍优先；(c) 映射表没有的码沿既有链直出
- [X] T006 [US2] 实现：`webui/src/api.ts`——import `ERROR_MESSAGES`，兜底链在 `payload.error || payload.error_code` 之前插入 `ERROR_MESSAGES[String(payload.error_code || "")]` 查表（空值安全）

## Phase 4: US3 续跑重复岗位单侧化

**Story Goal**: 断点已保留 + 本轮新命中跨平台重复 → 岗位只进剔除侧。独立验证：新增用例 + `tests.test_webui_app` 回归。

- [X] T007 [US3] 失败测试先行：`tests/test_webui_app.py` 新增（复用 019 既有测试基建）——构造上次 run 断点内岗位 X 已保留、本轮对端新增同指纹可见轮，续跑粗筛收尾断言：X 仅在 dropped（含跨平台重复条目）、不在 kept/survivors、总计数不翻倍、X 不进 JD/精筛输入
- [X] T008 [US3] 实现：`webui/app.py` `_rough_kept_from_resume` 列表推导追加 `and str(j.get("job_id","")) not in _dup_ids`（~3195-3199），不动其余合并语义

## Phase 5: US4 画像可删除

**Story Goal**: 用过收藏/反馈的画像删除成功。独立验证：`uv run python -m unittest tests.test_webui_store`。

- [X] T009 [US4] 失败测试先行：`tests/test_webui_store.py` 新增——临时库 create_profile → 插 jobs 行 → link_profile_job → 写 profile_job_events 与 profile_job_command_receipts 各一行 → `delete_profile` 断言成功且两子表行同灭；无事件画像删除行为回归（返回结构不变）
- [X] T010 [US4] 实现：`webui/store.py` `delete_profile` 在删主表前显式 `DELETE FROM profile_job_command_receipts WHERE profile_id=?` → `DELETE FROM profile_job_events WHERE profile_id=?`（回执先于事件，回执表另有 event_id 外键）；修正 docstring 如实描述

## Phase 6: US5 运行态按钮如实

**Story Goal**: scraped_only 轮发起筛选后运行期显示暂停、不可重复发起。独立验证：`cd webui && npm test -- useScreenRoundFlow.spec.ts screenFlow.spec.ts`。

- [X] T011 [US5] 失败测试先行：`webui/src/composables/__tests__/useScreenRoundFlow.spec.ts` 新增——(a) `currentRoundStatus="scraped_only"` + `screenSnapshot={status:"running"}` → `screenStatus==="running"`、`screenAction.kind==="pause"`；(b) 同状态 + 仅 `screenBusy=true`（无快照）→ running；(c) 既有用例「scraped_only + completed 快照 → start」保持通过
- [X] T012 [US5] 实现：`webui/src/composables/useScreenRoundFlow.ts` `screenStatus` 中 raw==="running" 或 screenBusy 优先于 scraped_only 返回；`webui/src/views/DiscoveryView.vue` `startAiScreen` 校验通过后置 `currentRoundStatus.value = "screened"`；核查 ~3533 行主按钮 disabled 逻辑，若仍允许运行中点击则补运行态禁用

## Phase 7: US6 判定合并覆盖比较（修订 018）

**Story Goal**: 断点岗位缺判定即合并，数量够不算数；018 spec 同步修订。独立验证：`uv run python -m unittest tests.test_screen_flow tests.test_webui_app`。

- [X] T013 [US6] 失败测试先行：`tests/test_screen_flow.py` 新增——(a) 数量已够但键集不覆盖（断点 [a,b]、当前判定 {a:not_match, x:not_match}、run1 有 b:dropped）→ 合并发生、b 不复活；(b) 全覆盖（断点 ⊆ 判定键集）→ 跳过合并（回归）；`tests/test_webui_app.py` 新增——多 run 链（run1 粗筛 dropped → run2 接管写精筛判定 → run3 续跑）断言 dropped 不复活、resume_inconsistent 事件按覆盖口径记录缺失数
- [X] T014 [US6] 实现：`webui/screen_flow.py` ~130 行触发条件改 `set(checkpoint_ids) - set(verdicts)` 非空即合并；`webui/app.py` ~3203 护栏事件同口径（负载记缺失岗位数）
- [X] T015 [US6] 契约修订：`specs/018-screening-chain-bugfix/spec.md` US2 验收场景 1 与 FR-004 的「判定数少于断点数」表述改为覆盖比较表述，注明「修订自 020（判定覆盖口径）」；核对 018 其余提及处（SC/Edge Cases）一致性

## Phase 8: US7 终态后写轮失败救援（修订 018）

**Story Goal**: 写轮失败先重试，仍失败条件降级并可续跑重建。独立验证：`uv run python -m unittest tests.test_result_rounds tests.test_webui_store tests.test_webui_app`。

- [X] T016 [US7] 失败测试先行：(a) `tests/test_result_rounds.py`——save_finished_round 首两次抛 `sqlite3.OperationalError` 第三次成功 → 轮写入成功且共 3 次尝试；非 OperationalError 不重试直接抛；(b) `tests/test_webui_store.py`——`downgrade_succeeded_if_no_result_round`：succeeded + 无轮 → True 且状态 failed；同流程已有可见轮 → False 不动；非 succeeded → False；(c) `tests/test_webui_app.py`——完整链：mock save_finished_round 恒抛 OperationalError → 断言重试 3 次 → run 降级 failed、内存错误文案含「筛选已完成但结果保存失败，点继续可重试保存」、落 result_round_save_failed 诊断事件；随后以同条件续跑 → 断言不重筛（AI 调用零次）、直达收尾、结果轮写入成功、run 终态 succeeded
- [X] T017 [US7] 实现：(a) `webui/result_rounds.py` save_finished_round 对 `sqlite3.OperationalError` 做 2 次短退避重试（退避用可注入 sleep 便于测试）；(b) `webui/store.py` 新增 `downgrade_succeeded_if_no_result_round(run_id, *, error_code, error_reason)`（`_BEGIN_IMMEDIATE` 事务内仿 finish_screening_run 模式：先 `_assert_recovery_writes_allowed`，再校验 succeeded + 同流程无可见 result_snapshot 轮）；(c) `webui/app.py` 收尾段加 `finalized` 标记，通用异常分支内 finalized 时先试条件降级：成功 → 内存文案改「筛选已完成但结果保存失败，点继续可重试保存」+ append_task_event 诊断事件；失败（已有轮等）→ 保持 succeeded 落诊断事件
- [X] T018 [US7] 契约修订：`specs/018-screening-chain-bugfix/spec.md` FR-007「纯换序，不新增补偿/回滚逻辑」改为「换序 + scoped 重试与条件降级（修订自 020）」，US3 场景描述同步补救援行为

## Phase 9: Polish & 交叉收尾

- [X] T019 前端构建同步：`cd webui && npm run build`，`webui/dist` 随源码改动一并纳入提交（pre-push 检查）
- [X] T020 全量验证门禁：`uv run python -m unittest discover -s tests` + `cd webui && npm test` + `npm run build` + `uv run python -m unittest tests.test_repo_hygiene`，全绿才算交付
- [X] T021 文档核对：README 无需变更（纯 bug 修复、无新用户能力）；如发现行为描述不符则同步；`roadmap/BACKLOG.md` B049 顺手归档（仅本地文件，不入库）
- [ ] T022 提交收口（用户指令后执行）：`git status`/`git diff --cached` 核对无意外文件 → Conventional Commits（建议 `fix: 错误如实呈现与数据口径一致（020）——熔断器透传与复位、错误码映射、续跑去重单侧、画像删除、运行态按钮、判定覆盖合并、终态后写轮救援`）

## Dependencies

- T001 → 全部实现任务（基线先行）。
- 各故事相互独立，可并行（不同文件；US3/US6/US7 共享 `webui/app.py` 不同段落，串行更稳：US3 → US6 → US7）。
- T015/T018 契约修订分别在对应实现任务（T014/T017）完成后落笔，避免 spec 先行于实现。
- T019 依赖全部前端任务（T006/T012）；T020 依赖全部；T022 最后。

## Parallel Execution Examples

- T002+T005+T007+T009+T011+T013+T016 全部是测试文件任务、互不重叠，可并行写失败测试。
- T003/T006/T010/T012 修不同文件，可并行；T008→T014→T017 依次动 app.py 建议串行。

## Implementation Strategy

MVP = US1（用户误导性最强的硬停）。之后按 US7 → US6 → US3（数据口径）→ US4/US5/US2 的顺序推进也可，但同一文件（app.py）的三条建议按 US3 → US6 → US7 串行收尾。每条故事完成即跑其聚焦测试，最后统一过全量门禁。
