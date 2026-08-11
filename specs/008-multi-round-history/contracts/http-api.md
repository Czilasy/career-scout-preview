# HTTP API Contract: 多轮结果历史

**Date**: 2026-08-11

## GET /api/result-history

返回历史轮次元数据列表，不返回岗位明细。

Query:

- `platform`（可选）：`boss` / `zhilian`；缺省返回全部平台。

Response 200:

```json
{
  "ok": true,
  "items": [
    {
      "run_id": "abc123",
      "platform": "boss",
      "status": "done",
      "created_at": "2026-08-11T00:00:00+08:00",
      "started_at": "2026-08-11T00:00:00+08:00",
      "finished_at": "2026-08-11T00:10:00+08:00",
      "total_scraped": 40,
      "total_kept": 12,
      "total_matched": 8,
      "total_dropped": 28,
      "pending_count": 2,
      "keyword_summary": "Python 后端 / 上海",
      "profile_summary_preview": "3年Python后端...",
      "archived_at": null,
      "is_latest": true
    }
  ]
}
```

规则：

- `is_latest`：该平台 `archived_at IS NULL` 且 `created_at` 最新的一条为 `true`。
- `status` 为机器值；前端负责中文映射。
- 不返回 `jobs`/`dropped`。
- 列表只返回 `profile_summary_preview`（截断摘要）；完整画像文本由详情接口返回。

## GET /api/result-history/<run_id>

返回单个历史轮次完整结果，结构与 `/api/latest-pipeline-result` 对齐。

Response 200:

```json
{
  "ok": true,
  "has_result": true,
  "source_run_id": "abc123",
  "platform": "boss",
  "status": "partial",
  "saved_at": "...",
  "started_at": 123,
  "finished_at": 456,
  "script_params": {},
  "execution_config": {},
  "source_summary": {},
  "source_outcomes": [],
  "result": {
    "total_scraped": 40,
    "total_matched": 8,
    "total_kept": 12,
    "total_dropped": 28,
    "combinations": 1,
    "jobs": [],
    "dropped": [],
    "profile_summary": "完整画像文本"
  }
}
```

`status` 为该轮 `screening_runs` 原始机器值（如 `done` / `partial` / `failed` / `interrupted` / `cancelled`），前端按 data-model Status Mapping 映射；不使用 latest-pipeline-result 的 `completed` / `completed_with_pending` 归一化作为历史展示状态。

Errors:

- 404 `{ "ok": false, "error": "round_not_found" }`

## POST /api/result-history/archive-latest

把全部 `archived_at IS NULL` 的结果快照归档；用于“开始新一轮”和“重新上传简历”。

Response 200:

```json
{
  "ok": true,
  "archived_run_ids": ["abc123", "def456"]
}
```

幂等：没有可归档行时返回空数组，不报错。

## DELETE /api/result-history/<run_id>

删除指定历史轮次，保留 `tasks`/`task_logs` 与全局收藏、反馈、岗位库。

Response 200:

```json
{
  "ok": true,
  "deleted": true,
  "run_id": "abc123"
}
```

删除最新未归档轮次后，按 data-model 最新回退规则处理，接口响应不变。

Errors:

- 404 `{ "ok": false, "error": "round_not_found" }`

## POST /api/reset-latest-result（兼容语义调整）

- 无 `run_id`：改为调用归档服务，返回 `archived_run_ids`，不再删除。
- 有 `run_id`：改为调用保留任务日志的删除服务。
- 保留旧响应结构 `{ ok, cleared, run_id, platform }`，新增 `archived` 字段可选。

## 错误体

所有错误使用：

```json
{
  "ok": false,
  "error": "stable_code",
  "message": "用户可读中文"
}
```

错误码：`round_not_found`、`invalid_platform`、`persistence_failed`。
