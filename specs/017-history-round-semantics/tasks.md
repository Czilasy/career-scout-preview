---
description: "Task list for 017-history-round-semantics"
---

# Tasks: 历史轮次与流程终结语义修复

**Input**: Design documents from `/specs/017-history-round-semantics/`

**Prerequisites**: plan.md（文件边界已确认）、spec.md（US1-US5）、research.md（R1-R8）、data-model.md、contracts/http-api.md

**Tests**: 含测试任务（宪法 IV：行为变化由失败测试先行定义）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 归属用户故事（US1-US5）

## File Boundaries（自 plan.md，任务不得越界）

- **Allowed files**: 新增 `webui/result_rounds.py`、`tests/test_result_rounds.py`；修改 `webui/app.py`（只删不增）、`webui/store.py`（净减）、`webui/store_migrations.py`、`webui/scrape_only.py`、`webui/src/discovery.ts`、`webui/src/components/ResultHistoryDrawer.vue`、`webui/src/views/DiscoveryView.vue`、`tests/test_result_history.py`、`tests/test_webui_app.py`、`tests/test_webui_store.py`、`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`
- **Forbidden files**: `webui/ai.py`、`webui/error_registry.py`、`webui/tuning.py`（016 范围）；`scripts/boss_cdp_raw.py`；数据库表结构；前端其余视图组件
- **Reference direction**: `app.py → result_rounds.py → store/mixins`；`scrape_only.py → result_rounds.py`
- **Line gate**: `result_rounds.py` < 800；app.py 与 store.py 交付后行数低于 T001 基线

## Verification Gate (task-type aware)

- 功能交付：聚焦测试（`uv run python -m unittest tests.test_result_rounds tests.test_result_history`）→ 后端全量（`uv run python -m unittest`）→ 前端（`cd webui && npm test`）→ `npm run build` → 卫生（`uv run python -m unittest tests.test_repo_hygiene`）。
- 不为收口任务生成全量测试清单项（本 spec 全部为功能交付批次）。

---

## Phase 1: Setup（基线）

- [x] T001 基线验证与行数记录：跑通后端全量与前端测试；记录 `webui/app.py`、`webui/store.py` 当前行数作为行数门禁基线（写入本文件 Notes）

## Phase 2: Foundational（统一写入服务，阻塞 US2/US3）

**⚠️ US2/US3 依赖本阶段；US1/US4/US5 可先行**

- [x] T002 失败测试先行：新建 `tests/test_result_rounds.py`——断言三出口经统一服务各恰一轮、同流程（同 scrape_task_id+platform）已有轮时原地升级不新增、重抓回写刷新计数与定稿时间、0 岗位不成轮
- [x] T003 新建 `webui/result_rounds.py`：实现 `save_finished_round`（自然完成/结束保存共用，含防重升级）、`save_scraped_only_round`（幂等建未筛选轮）、`apply_recrawl_writeback`（判定/JD 回写+计数重算+定稿时间）；引用方向仅向 store/mixins；使 T002 全绿

## Phase 3: US1 - 没走到终点的流程不进历史 (P1, MVP)

**独立测试标准**：四种中断（暂停/错误强停/取消/重启中断）后 `GET /api/result-history` 轮数零增长（quickstart 场景 1）

- [x] T004 [US1] 失败测试先行：`tests/test_webui_app.py` 增加四中断不成轮用例；删除/改写现有"暂停/取消产生快照"断言
- [x] T005 [US1] `webui/app.py`：删 `_mark_paused` 中 `_try_save_failure_snapshot` 调用（~3115-3124），暂停只写 paused 终态与事件
- [x] T006 [US1] `webui/app.py`：删错误路径快照调用（~3525 粗筛阻断、~3715 精筛阻断、~4022/~4060 异常兜底）后删除 `_try_save_failure_snapshot` 函数体（~3151-3242）
- [x] T007 [US1] `webui/app.py`：删 `_save_cancelled_history_snapshot`（~463-555）及其在 `api_task_cancel` 的调用（~8869）、`_mark_cancelled` 内调用（~3245）
- [x] T008 [US1] 跑 US1 聚焦测试（`tests.test_webui_app` 相关用例）确认独立测试标准达成

## Phase 4: US2 - 一条流程一条轮 (P1)

**独立测试标准**：暂停→结束保存=1 轮；暂停→继续→跑完=1 轮；自然完成后 finish=409；补筛原地升级同轮（quickstart 场景 2）

- [x] T009 [US2] 失败测试先行：`tests/test_result_rounds.py` 补三条操作序列端到端用例（暂停→结束、暂停→继续→完成、完成后重复 finish 409）
- [x] T010 [US2] `webui/app.py`：finish 端点（~8935-9160）写入改调 `result_rounds.save_finished_round`；result 构建（~8985-9103）留原位不动
- [x] T011 [US2] `webui/app.py`：自然完成路径（~3953 `save_screen_result` 调用点）改调 `result_rounds.save_finished_round`
- [x] T012 [US2] `webui/app.py`：跳过筛选端点（~5263-5286）改调 `result_rounds.save_scraped_only_round`，删除端点内冗余的幂等预检（~5263-5273，幂等已由服务保证）
- [x] T013 [US2] `webui/scrape_only.py`：删 `save_screen_result` 与 `save_scrape_snapshot`（调用方均已迁移）；保留 `build_undecided_result`、`build_screen_script_params`、`merge_round_script_params` 纯构建函数
- [x] T014 [US2] `webui/app.py`：重抓回写段（~7753-7767、~7880-7895）改调 `result_rounds.apply_recrawl_writeback`，删除段内直写的 `save_screening_verdicts`/`delete_pending_result`/`recount_pipeline_result` 调用
- [x] T015 [US2] 跑 US2 聚焦测试确认独立测试标准达成

## Phase 5: US3 - 历史信息诚实 (P2)

**独立测试标准**：标签仅三种；重抓后计数与定稿时间更新、轮数与排序不变；升级清空存量（quickstart 场景 3/4）

- [x] T016 [US3] 失败测试先行：`tests/test_webui_store.py` 加 recount 刷新 finished_at 用例与清空迁移用例；`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts` 加定稿时间与三态标签用例
- [x] T017 [US3] `webui/store.py`：`recount_pipeline_result`（~1004-1054）UPDATE 增加 `finished_at` 刷新
- [x] T018 [US3] [P] `webui/src/components/ResultHistoryDrawer.vue`：主时间改显示 `finished_at`（缺失回退 `created_at`）
- [x] T019 [US3] [P] `webui/src/discovery.ts`：`historyStatusLabel` 收敛三种映射，删"失败但有 N 个岗位"兜底（未知状态不渲染标签）
- [x] T020 [US3] `webui/store_migrations.py`：新增版本化迁移——按 `delete_history_result_preserving_logs` 同表集删除全部 `record_kind='result_snapshot'` 轮（screening_results/screening_pending_results/pipeline_checkpoints/scrape_page_progress/screening_runs），任务行与日志不动
- [x] T021 [US3] 更新 `tests/test_result_history.py`：删除失败轮 raw status 用例（`test_failed_round_detail_keeps_raw_status`），补三态标签语义用例；跑 US3 聚焦测试

## Phase 6: US4 - 重抓显式目标 (P3)

**独立测试标准**：重抓/单岗位重查缺 `source_run_id` 返回 409；旧清空端点 404（quickstart 场景 3 第 3 步）

- [x] T022 [US4] 失败测试先行：`tests/test_webui_app.py` 加缺目标 409 与旧端点 404 用例
- [x] T023 [US4] `webui/app.py`：批量重抓（~7038-7042）与单岗位重查（~6909-6913）删 `get_latest_done_run_id` 回退；单 JD 回写（~6829-6833）改用请求自带 `source_run_id`
- [x] T024 [US4] `webui/app.py`：删 `/api/reset-latest-result` 端点（~6536-6560）；`webui/src/views/__tests__/DiscoveryView.spec.ts` 删陈旧 mock（~465）
- [x] T025 [US4] `webui/store.py`：删 `get_latest_done_run_id`（~1056-1065）；`webui/app.py` `_run_recrawl_task` 内回退（~7360）随参数必传化删除；`tests/test_webui_store.py` 删对应断言（~1659）；跑 US4 聚焦测试

## Phase 7: US5 - 一套话术一个口径 (P3)

**独立测试标准**：同一任务状态在列表/详情/轮询接口用词一致且无 `waiting`；latest 判定全局与按平台一致（quickstart 场景 5）

- [x] T026 [US5] 失败测试先行：`tests/test_webui_app.py` 加状态词一致性用例（paused/completed_with_pending/failed 三任务 × 三接口）
- [x] T027 [US5] `webui/app.py`：删 `_run_to_task_status`（~148-159），调用点（~5201/8405/8782/8795/8816/8854/8863）统一替换为 `_public_task_status`
- [x] T028 [US5] [P] `webui/src/views/DiscoveryView.vue`：核对状态词消费集（`waiting` 残留改 `queued`，当前检索为零则仅核对记录）
- [x] T029 [US5] `webui/store.py`：latest 查询公共过滤抽单一常量/辅助（状态集 {done,partial,scraped_only} + 未归档），`load_latest_pipeline_result` 与 `load_latest_pipeline_result_for_platform` 共用；补一致性测试；跑 US5 聚焦测试

## Phase 8: Polish（收尾门禁）

- [x] T030 全量验证门禁：聚焦 → `uv run python -m unittest` → `cd webui && npm test` → `npm run build` → `uv run python -m unittest tests.test_repo_hygiene`；复核行数门禁（app.py/store.py 低于 T001 基线、result_rounds.py < 800）
- [x] T031 [P] 文档同步检查：按 AGENTS 文档卫生核对 README 是否需更新"取消/暂停不再保留结果轮"等用户可感知变化；CHANGELOG 条目按发布流程另行处理，此处只检查不动版本

---

## Dependencies

```text
T001 → 全部
T002 → T003 → (T010-T015, T017*)
US1（T004-T008）独立于 Phase 2，可与其并行（不同文件区域）
US2（T009-T015）依赖 T003
US3（T016-T021）依赖 T003（定稿时间经由服务回写）；T018/T019 仅前端可随时并行
US4（T022-T025）独立，可与 US1-US3 并行
US5（T026-T029）独立，可与 US1-US4 并行（T027 与 T005-T007 同文件不同区域，建议串行 app.py 批次）
T030 依赖全部；T031 在 T030 前后皆可
```

**故事完成顺序建议**：US1（MVP，止血历史污染）→ Phase 2 → US2 → US3 → US4 → US5 → Polish。

## Parallel Execution Examples

- T002（新测试文件）‖ T004（test_webui_app.py 不同用例组）
- T018（抽屉）‖ T019（discovery.ts）‖ T020（迁移）
- US4 整段 ‖ US3 后端部分

## MVP Scope

仅 US1（T004-T008）：四种中断不再产生历史轮——立刻止住"历史被半成品污染"的核心痛点，其余语义在后续批次交付。

## Notes（T001 填写）

- 基线行数：app.py=9669、store.py=4882（2026-08-22，wc -l）
- 后端全量 `uv run python -m unittest discover -s tests` 通过（exit 0）；前端 `npm test` 34 文件 450 用例通过
