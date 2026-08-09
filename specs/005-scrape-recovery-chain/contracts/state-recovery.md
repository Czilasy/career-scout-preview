# 接口契约：抓取恢复链路修复

**创建日期**：2026-08-09 | **Plan**：[plan.md](plan.md)

## POST /api/task/finish/<run_id>

**目的**：结束暂停、失败、重启中断或运行中的任务并保存已抓内容。

**允许状态**：`queued / running / paused / failed / interrupted(process_restart/operator_stop)`。

**行为**：

- 运行中先设置停止信号并关闭调试浏览器，再从持久化数据生成 partial 快照。
- 快照边界以 finish 请求时已持久化数据为准；未落库批不保证进入快照。
- finish 先原子标记 `user_finished`，worker 在标记后不得再写 DB 终态，只能更新内存展示。
- 保存成功后原 run 进入 `interrupted/user_finished`，刷新后不再作为可恢复任务返回。
- 已取消（`user_cancelled`）与已完成（`succeeded/partial`）拒绝改写。

**响应（200）**：

```json
{
  "ok": true,
  "run_id": "...",
  "snapshot_run_id": "...",
  "platform": "boss",
  "scrape_task_id": "...",
  "status": "completed_with_pending",
  "result": {
    "jobs": [{"platform": "boss", "platform_job_id": "...", "verdict": "uncertain"}],
    "dropped": [{"platform": "boss", "platform_job_id": "..."}],
    "total_scraped": 1280,
    "total_kept": 1280,
    "total_dropped": 0,
    "profile_summary": ""
  }
}
```

**错误**：`run_not_found`、`user_cancelled`、`not_paused`、`missing_scrape_snapshot`。

## GET /api/latest-running-task

**目的**：刷新/重启后接回仍在运行或可恢复的任务。

**新增语义**：

- paused/interrupted 恢复路径返回 `scraped_count`、`source_total`、`platform`、`scrape_task_id`。
- 增加“最近可恢复抓取”兜底：`kind=scrape`、`status=failed`、`scrape_run_jobs` 非空、未 `user_finished` 时返回，`has_task=true`。
- 已结束保存任务不得返回。
- `scraped_count` 以 `scrape_run_jobs` 行数为准，表示已抓岗位数；`source_total` 表示组合/来源总量。
- 恢复优先级为 running > paused > restart-interrupted > failed；failed 兜底同样受“已有更新结果快照则跳过”保护。

**响应新增字段**：

```json
{
  "has_task": true,
  "kind": "scrape",
  "status": "failed",
  "platform": "zhilian",
  "scrape_task_id": "...",
  "scraped_count": 1280,
  "source_total": 3000
}
```

## GET /api/task-state/<run_id>

**目的**：恢复后展示真实进度。

**语义**：`success_count/source_total/total` 必须来自 DB 计数与 `scrape_run_jobs`；`scraped_count` 表示已抓岗位数；`pause_info.error_reason` 及所有失败/暂停文案消费点均按 run 平台取文案，智联任务不出现 BOSS 文案。

## GET /api/latest-pipeline-result

**目的**：刷新后恢复最近结果快照。

**新增字段**：

```json
{
  "has_result": true,
  "source_run_id": "...",
  "platform": "boss",
  "scrape_task_id": "...",
  "result": {"jobs": [{"platform": "boss"}], "dropped": []}
}
```

**规则**：`scrape_task_id` 来自保存快照；旧快照无该字段时不伪造。

## POST /api/pipeline/recrawl

**目的**：单平台待确认岗位重抓。

**语义**：保持单 `source_run_id` + `job_ids`；前端在“全部”视图不得调用本接口发起跨平台混合任务，应先引导用户选择 BOSS/智联，并展示各平台待确认数量；数量为 0 的平台禁用或明确提示。

## GET /api/env-check

**目的**：环境检查与冷却展示。

**新增字段**：每个冷却记录增加 `from_run`。

```json
{
  "cooldowns": [
    {"account_id": "a", "platform": "boss", "until": 0, "until_text": "...", "reason": "...", "from_run": "..."}
  ]
}
```

## 前端交互契约

- 02 页在 `paused/failed/running/interrupted(restart)` 均显示“结束并保存结果”；运行中与“停止抓取”并存。
- 结束保存成功后不强制跳结果页；页面提供“查看结果”与“继续 AI 筛选”两个入口。
- 结果页“待确认”分类下“全部重抓（N）”在全部/BOSS/智联三档均可见；全部视图点击后先选择平台，不创建混合任务。
- 平台筛选滑块与重抓按钮在桌面和窄屏不重叠、不横向溢出。
