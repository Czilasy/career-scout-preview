# Tasks: 筛选链路三处 Bug 修复（018）

**Input**: Design documents from `/specs/018-screening-chain-bugfix/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/behavior-contracts.md、quickstart.md

**Tests**: 冻结需求明确要求回归测试，测试任务包含在内。

**Organization**: 按用户故事分组；US1/US3 相互独立，US2 依赖 store 扩展先行。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事

## File Boundaries

- **Allowed files**: `webui/ai.py`、`webui/app.py`、`webui/screen_flow.py`、`webui/store_screen_resume_mixin.py`、`tests/test_ai.py`、`tests/test_webui_app.py`、`tests/test_screen_flow.py`、`tests/test_result_rounds.py`（如断言旧顺序）、`CHANGELOG.md`、`specs/018-screening-chain-bugfix/**`
- **Forbidden files**: `webui/store.py`、`webui/src/**`、`webui/dist/**`、`scripts/boss_cdp_raw.py`、任何表结构/迁移、任何新代码文件/新 store 方法
- **New files**: 无代码新文件；live 库清理用临时执行方式（不建脚本）
- **Reference direction**: `app.py → screen_flow.py → store_screen_resume_mixin.py`；不反向
- **Line gate**: app.py 净增 ≤ 约 20 行；其余文件远低于 800 行上限

## Verification Gate (task-type aware)

- 功能交付：`uv run python -m unittest tests.test_ai tests.test_webui_app tests.test_result_rounds tests.test_repo_hygiene tests.test_screen_flow`（聚焦，最后者因本次改写其用例）
- 提交收口：卫生测试 + `git diff --check` + `git status`；不跑全量、不 build 前端（冻结核限定）
- 测试输出一律进系统临时目录

---

## Phase 1: Setup

- [X] T001 确认基线：`uv run python -m unittest tests.test_ai tests.test_webui_app tests.test_result_rounds tests.test_screen_flow tests.test_repo_hygiene` 在改动前全绿（记录基线，输出进临时目录）

## Phase 2: US1 — AI 响应坏格式不再炸整轮（P1）

独立测试：mock AI 返回 `{"results": 40}` / `{"dropped": 40}`，断言不抛异常、降级链路生效。

- [X] T002 [P] [US1] 精筛守卫：`webui/ai.py` `_match_one_batch`（约 1854 行）`results` 字段非列表时按空列表处理（`raw_results`/`results` 两行写法），坏格式走既有 missing → 单条重试预算 → uncertain → 熔断链路
- [X] T003 [P] [US1] 粗筛守卫：`webui/ai.py` `_process_batch`（约 1553 行）`dropped` 字段加同款类型守卫，非列表按空名单（整批默认保留）
- [X] T004 [P] [US1] 回归测试：`tests/test_ai.py` 新增用例——mock call_ai 返回 `{"results": 40}`（int），断言 match_jds 不抛异常且产出 uncertain 判定；粗筛 `{"dropped": 40}` 同理断言全保留

## Phase 3: US2 — 续跑不丢岗位（P1）

独立测试：事故链回归（run1 277 判定 + 277 断点；run2 40 精筛判定 + ai_fine 断点；run3 续跑 → 幸存者 165、40 条精筛继承、零静默丢失）。

- [X] T005 [P] [US2] store 扩展：`webui/store_screen_resume_mixin.py` `latest_screen_runs_for_source` 增加 `statuses=None` 分支（全部状态、排除 result_snapshot、`created_at ASC, rowid ASC`），既有调用行为不变，不新增方法
- [X] T006 [US2] 同源链合并：`webui/screen_flow.py` `load_resume_verdicts_with_fallback` 回退段（128-146 行快照回退，含 scrape_task_id 闸门）整体替换为同源链合并（contracts/behavior-contracts.md C1）：新增可选参数 `profile_facts=None`，逐 run 校验 frozen_filters/profile_summary/profile_facts、排除自身、created_at 从旧到新 `update` 合并、`{**merged, **verdicts}` 返回
- [X] T007 [US2] 主修：`webui/app.py` 约 3162-3167 粗筛幸存者条件反转为 `!= "dropped"` 默认保留，且 `_resume_verdicts` 改从合并后的 `resume_verdicts`（3102 行，已在作用域）提取 verdict 字符串（research.md R2）；`_resume_dropped_from_verdicts` 并入 dropped 时跳过已在 kept_ids 的岗位（research.md R3）
- [X] T008 [US2] 护栏：`webui/app.py` `_rough_done_ids` 与 `resume_verdicts` 就绪后（约 3168 行附近），`len(resume_verdicts) < len(_rough_done_ids)` 时 `append_task_event(task_id, "resume_inconsistent", {"verdicts": n, "checkpoint": m})`，仅记录不阻断；若 `_FINE_VERDICTS` 在 app.py 不再被引用则从 import 清单移除
- [X] T009 [P] [US2] 用例改写：`tests/test_screen_flow.py` `LoadResumeVerdictsTests` 两个快照回退用例改写为同源链合并用例（合并覆盖、条件不一致跳过、排除自身、新覆盖旧）
- [X] T010 [US2] 事故链回归：`tests/test_webui_app.py` 参照既有 pipeline 手法（捕获 executor.submit 闭包同步执行 + mock screen_jobs/match_jds/浏览器）：run1 写 277 条粗筛判定（165 kept + 112 dropped）+ ai_rough 277 keys；run2 名下 40 条 not_match 精筛判定 + ai_fine 断点 + ai_rough 277 keys（对齐 live 实证）；模拟 run3 续跑 → 断言幸存者 165、40 条精筛判定被继承、无岗位静默消失

## Phase 4: US3 — 收尾校验失败不留幽灵轮（P1）

独立测试：finalize 判 paused 场景零 done 轮 + 任务 failed(internal_error)；正常完成路径照常写轮。

- [X] T011 [US3] 换序：`webui/app.py` `_run_ai_screen_task` 成功收尾段（约 3622-3696）按 contracts/behavior-contracts.md C2 换序：job_events → 计数(_write_run, current_stage="done") → emit(done) → finalize 校验（不合法抛 RuntimeError，此刻无历史轮）→ save_finished_round(status="done") → history_snapshot → _prune_history_best_effort → 内存置 done 与清理；纯换序，不加补偿/回滚
- [X] T012 [US3] 回归测试：`tests/test_webui_app.py` 新增——finalize 判 paused 场景（如 3 岗位中 1 个既未 kept 也未 dropped）断言不产生任何 done 轮、任务 failed(internal_error)；正常完成路径仍写轮；若 `tests/test_result_rounds.py` 或其他既有用例断言旧顺序，同步修正

## Phase 5: US4 — 事故数据清理（P2，代码合入后执行）

独立测试：清理后 `scripts/db_info.py` 复核最新 run 不是 828f8807；三条失败 run 判定行数不变。

- [X] T013 [US4] 清理 live 库：备份 `~/.career-scout/webui/webui.db` → `uv run python - <<EOF` 临时执行两条 DELETE（run 828f8807 的 screening_results 与 screening_runs）→ `uv run python scripts/db_info.py` 复核；严禁动 03fb82e1/0f0baa1b/94e2c440

## Phase 6: Polish & 收口

- [X] T014 CHANGELOG.md 按 AGENTS.md 文案格式（修复：/优化：简单列表、无标题无英文术语）补 018 条目
- [X] T015 全量聚焦验证：`uv run python -m unittest tests.test_ai tests.test_webui_app tests.test_result_rounds tests.test_repo_hygiene tests.test_screen_flow` 全绿；`uv run python -m unittest tests.test_repo_hygiene` + `git status` + `git diff --check` 通过后按 Conventional Commits 提交（`fix: …018…`，作者邮箱 czyooutzilas@gmail.com）

---

## Dependencies

```text
T001（基线）
├─ T002/T003/T004 [US1] —— 相互独立，可并行
├─ T005 → T006 → T007 → T008 [US2 链式]
│            ├─ T009（与 T007/T008 并行，不同文件）
│            └─ T010（依赖 T005-T008 完成）
├─ T011 → T012 [US3]（独立于 US2，可与 US2 并行）
└─ T013 [US4]（依赖全部代码任务合入）
     └─ T014 → T015
```

## Parallel Execution Examples

- T002、T003（同文件不同函数，顺序执行更稳）与 T005（不同文件）可并行
- T009 与 T007/T008 不同文件，可并行

## Implementation Strategy

- MVP = US1（最小独立价值：坏格式不再炸整轮）；随后 US2（数据安全主修）、US3（历史可信）、US4（清库）、收口。
- 行号漂移以冻结核代码片段内容定位为准（基于 HEAD 900927f）。

## Phase 7: 全量审查（用户明确要求全量验证后追加）

- [X] T016 后端全量 2434 例 + 前端 452 例 + npm run build + 卫生测试；失败分诊：2 个 ContractCompliancePatchTests 与 ~37 个 test_historical_recovery_realdb / test_process_executor 失败经 018 前基线（worktree 900927f + 8-20 备份库复验）确认为存量/环境性，与 018 无关
- [X] T017 test_healthy_pipeline 的 test_terminal_failure_after_snapshot_does_not_duplicate_history 断言旧收尾顺序（终态写失败仍留 1 条 done 轮），按 018 契约修正为零历史轮并改名 test_terminal_write_failure_leaves_no_history_round（tests/test_healthy_pipeline.py）
- [X] T018 前端构建产物随 018 后端指纹同步（webui/dist/**，ensure_frontend_sync 校验通过）
