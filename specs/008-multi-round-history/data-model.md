# Data Model: 多轮结果历史

**Date**: 2026-08-11

## Entity: Result Snapshot（结果快照）

对应现有 `screening_runs` 中 `record_kind='result_snapshot'` 的行。本轮不新增表，只新增一个可空字段。

### Fields

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | run_id，历史轮次稳定标识 |
| `platform` | TEXT | `boss` / `zhilian` |
| `status` | TEXT | 快照机器状态：`done` / `partial` / `failed` / `interrupted` / `cancelled` 等有产出状态 |
| `created_at` | TEXT | 保存时间，历史排序依据 |
| `started_at` / `finished_at` | TEXT | 本轮起止时间 |
| `record_kind` | TEXT | 固定 `result_snapshot` |
| `archived_at` | TEXT NULL | 新增；非空表示已从默认最新视图归档 |
| `search_params_json` | TEXT | 搜索参数（关键词、城市等） |
| `execution_params_json` | TEXT | 执行参数，含平台身份、画像摘要 |
| `profile_summary` | TEXT | 完整画像摘要 |
| `source_count` / `match_count` / `mismatch_count` / `pending_count` / `total_kept` / `total_dropped` | INTEGER | 本轮计数 |

### New Migration 030

```text
ALTER TABLE screening_runs ADD COLUMN archived_at TEXT
```

迁移只加列，不回填；旧行 `archived_at IS NULL` 保持现有最新语义。

## Entity: History Round（历史轮次）

历史列表查询规则：

- `record_kind = 'result_snapshot'`
- 存在至少一条 `screening_results` 行（`EXISTS` 子查询）
- 按 `created_at DESC, rowid DESC` 排序
- 平台筛选可选

最新判定：

- `archived_at IS NULL`
- 每平台 `created_at` 最新一条
- 历史列表中该条标记“最新”
- 删除最新未归档轮次后：若该平台仍有未归档快照，由其中最新一条接任；若已无未归档快照，把该平台最近一个归档轮置为未归档（`archived_at IS NULL`）恢复为最新；没有任何更早轮次时显示“暂无结果”。

## Entity: Archived Round（归档轮次）

- 通过 `archive_all_current_results()` 将全部 `archived_at IS NULL` 的结果快照置为已归档。
- 归档后不再被 `/api/latest-pipeline-result` 查询返回。
- 仍出现在历史列表，仍占用该平台 30 轮名额。
- 可被手动删除。

## Deletion Semantics

`delete_history_result_preserving_logs(run_id)`：

- 删除 `screening_results`、`screening_pending_results`、`pipeline_checkpoints`、`scrape_run_jobs`、`scrape_page_progress`（按 run_id）
- 删除 `screening_runs` 行
- 保留 `tasks`、`task_logs`、`screening_source_attempts`、`jobs`、`profile_jobs`、`feedback_events`
- 删除最新未归档轮次时按“最新判定”的回退规则执行；删除历史归档轮不触发回退。

## Retention Rule

- 每平台统计所有有岗位产出的结果快照（含归档）。
- 超过 30 条时，从最旧开始调用删除方法，直到该平台只剩最近 30 条。
- 触发点：每次 `save_pipeline_result` 成功后由 `result_history_service.prune_retention()` 执行；也允许历史服务幂等重放。

## Status Mapping（前端）

| 机器状态 | 前端文案 |
|---|---|
| `done` / `succeeded` / `completed` | 完成 |
| `partial` / `completed_with_pending` | 部分结果（`completed_with_pending` 表示该轮有待确认岗位） |
| 其它状态但已有岗位（`failed` / `interrupted` / `cancelled` 等） | 失败但有 N 个岗位 |

后端接口只返回机器状态与计数，前端负责中文映射。

历史详情不得使用 `load_latest_pipeline_result` 的 `completed` / `completed_with_pending` 归一化结果作为展示状态，必须保留 `screening_runs.status` 原始机器值。
