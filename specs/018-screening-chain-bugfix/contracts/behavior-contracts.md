# Behavior Contracts: 筛选链路三处 Bug 修复（018）

**Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

> 本次无外部 HTTP 契约变更（前端零改动）；以下为两份内部行为契约。

## C1 判定同源链合并（webui/screen_flow.py）

`load_resume_verdicts_with_fallback(store, run_id, platform, scrape_task_id, screening_fields, profile_summary, profile_facts=None) -> {job_id: verdict_dict}`

- 第一段不变：`verdicts = store.load_screening_verdicts(run_id)`；`checkpoint_ids = store.load_checkpoint(run_id, "ai_rough")`；`无断点 或 len(verdicts) >= len(checkpoint_ids)` → 直接返回 run 自身判定。
- 回退段（新）：枚举 `store.latest_screen_runs_for_source(scrape_task_id, statuses=None)`（created_at 升序、排除 result_snapshot），逐 run 校验并跳过不一致者：
  - `frozen_filters == screening_fields`
  - `execution_params.profile_summary == profile_summary`（字符串相等）
  - `_same_facts(execution_params.profile_facts, profile_facts)`（排序 JSON 相等；`profile_facts=None` 时仅匹配未存 facts 的 run——与 find_resumable_screen_run 的比对口径一致）
  - `run.id != run_id`（排除自身）
- 合并：按枚举顺序 `merged.update(run 判定)`，返回 `{**merged, **verdicts}`（当前 run 自身判定最终覆盖）。
- 空链 / 全部不一致 / 合并结果为空 → 返回 run 自身判定。
- 旧契约（结果快照回退 load_latest_pipeline_result_for_platform）整体废止。

## C2 收尾顺序（webui/app.py `_run_ai_screen_task` 成功收尾段）

1. `store.append_task_events(task_id, job_events)`（job_success/job_fail）
2. 计数汇总 → `_write_run_unless_finished(task_id, …counts…, current_stage="done")`
3. `emit(stage="done", …)`（仍先于任何终态提交，017 契约保持）
4. 终态校验：`_is_user_finished → partial`；否则 `store.finalize_run_status(task_id)`，结果不在 `("succeeded","partial")` → `raise RuntimeError(f"invalid_ai_terminal_status:{status}")`（此刻库里无任何历史轮）
5. `save_finished_round(…, status="done", …)`（一条流程一条轮，017 契约保持）
6. `history_snapshot` 事件（payload 不变）
7. `_prune_history_best_effort()`
8. 内存置 done、`_schedule_pipeline_task_cleanup`、`_release_worker_resume_claims`、删 JD 断点文件

- 失败语义：第 4 步抛错走既有 internal_error 路径 → run failed(internal_error)、任务 failed、历史零新增。

## C3 续跑幸存者语义（webui/app.py）

- `_rough_kept_from_resume = [j for j in raw_jobs if jid in _rough_done_ids and merged_verdicts.get(jid, {}).verdict != "dropped"]`
- 判定来源：`resume_verdicts`（C1 合并结果），非 run 自身判定。
- dropped 恢复：`_resume_dropped_from_verdicts(raw_jobs, resume_verdicts)` 结果并入 dropped 时跳过已在 kept_ids 的岗位（重筛结果新于链上旧判定）。
- 护栏：`len(resume_verdicts) < len(_rough_done_ids)` → `append_task_event(task_id, "resume_inconsistent", {"verdicts": n, "checkpoint": m})`，仅记录。

## C4 AI 响应解析守卫（webui/ai.py）

- 精筛：`data.get("results")` 非列表 → 视为 `[]`（每项 missing → 重试预算 → uncertain）。
- 粗筛：`data.get("dropped")` 非列表 → 视为 `[]`（整批默认保留）。
- 不抛 TypeError；不新增错误码；熔断链路（连续全空）保持既有行为。
