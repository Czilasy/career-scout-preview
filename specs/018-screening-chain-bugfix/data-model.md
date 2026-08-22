# Data Model: 筛选链路三处 Bug 修复（018）

**Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

> 本次无 DDL、无迁移、无新表；以下为既有实体的语义修正与一个新事件载荷。

## 既有实体（语义修正）

### 判定 screening_results

- `verdict` 列两种历史形态：纯字符串（`kept`/`dropped`，粗筛旧数据）与 JSON 串（精筛 `{verdict, reason, caveats, flags}`）。
- `load_screening_verdicts` 统一规范化为 dict 返回（纯字符串 → `{"verdict": <str>}`）；合并逻辑不感知存储形态。
- `save_verdict_and_checkpoint_atomic` 写入的是 JSON 串形态（`json.dumps(verdict)`）——03fb82e1 的 10 条精筛判定即此形态。

### 断点 pipeline_checkpoints

- `(run_id, stage, completed_keys_json)`；`stage ∈ {scrape, ai_rough, ai_fine, jd_detail…}`。
- 语义修正（消费侧，不改存储）：ai_rough 断点集合在续跑时表示"链上已粗筛的岗位"，不再隐含"有精筛/kept 判定"。

### 同源链 screening_runs

- 以 `execution_params_json.$.scrape_task_id` 关联、`record_kind != 'result_snapshot'` 的全部 run。
- 新消费方式：按 `created_at ASC` 全量枚举（`latest_screen_runs_for_source(statuses=None)`），供判定合并。

## 新增事件载荷（task_logs）

### resume_inconsistent

```json
{
  "type": "resume_inconsistent",
  "payload": {
    "verdicts": <int 合并后判定数>,
    "checkpoint": <int ai_rough 断点数>
  }
}
```

- 触发：续跑场景且 `len(resume_verdicts) < len(_rough_done_ids)`。
- 语义：仅记录，不阻断、不改任务状态、不映射任何错误码。

## 数据清理（一次性，不入库）

- 目标：`DELETE screening_results WHERE run_id='828f8807-…'`；`DELETE screening_runs WHERE id='828f8807-…'`。
- 不可动：03fb82e1412f4ccdaab05c9dd581bfc3、0f0baa1b881845a2b824967523c17ba8、94e2c4400bc74543a806e572e41786e2 三条 run 及其判定与断点。
