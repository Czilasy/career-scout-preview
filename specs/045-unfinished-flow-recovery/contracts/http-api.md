# HTTP Contract Additions: 未完成流程恢复

## GET /api/latest-running-task

既有响应保持兼容，新增可选字段仅用于未保存残留：

```json
{
  "ok": true,
  "has_task": true,
  "source": "database",
  "status": "paused",
  "job_count": 3,
  "notice": {"kind": "run_notice", "run_id": "...", "message": "..."}
}
```

- 内存活任务优先，不为了关闭确认改写真实运行状态。
- 无 worker 的 queued/running/paused 残留返回 `status="paused"`、`resumable=true`。
- `job_count` 是当前流程已持久化岗位数。
- 已落轮/已保存流程不返回未保存提醒；0 岗位残留不返回恢复项。
- 查询失败返回既有 503；桌面层视为“不能确认”，不得静默删除数据。

## POST /api/task/finish/<run_id>

沿用现有契约；桌面层确认后调用：

- 成功：`ok=true`，快照可见后可关闭。
- 失败：保持窗口打开并允许重试；不得把失败冒充保存成功。
- 已终态：返回 409 `already_finished` / `already_terminal`；若历史已有该轮，可按保存完成处理。

## 提醒

- `/api/run-notice/mark` 语义不变：成功标记后前端展示载荷。
- 正常保存过的轮不再进入未保存提醒候选。
