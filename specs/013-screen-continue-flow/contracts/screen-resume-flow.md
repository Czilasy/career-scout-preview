# Contract: AI 筛选暂停/继续/恢复链路

**Created**: 2026-08-15
**Feature**: [spec.md](spec.md)

## POST `/api/task/pause/<run_id>`

用户点击“暂停筛选”时调用。

请求体：无。

前置条件：

- run 存在；
- run 属于 AI 筛选（非抓取、非重抓）；
- run 状态为 `queued` 或 `running`。

成功响应：

```json
{
  "ok": true,
  "run_id": "<run_id>",
  "status": "pausing"
}
```

行为契约：

- 后端设置内存任务 `stop_mode="pause"` 并触发停止信号；
- worker 在下一个安全边界落库后把 DB run 写为 `paused`；
- 暂停时生成 `result_snapshot`（status=`partial`）供 04 展示；
- 已判定 verdicts、checkpoints、JD 断点全部保留；
- 暂停后前端继续轮询 `/api/task-state/<run_id>`，看到 `status=paused` 时按钮恢复为“继续 AI 筛选”。

失败响应沿用现有错误体：`{ok:false, error, message}`；例如 run 不存在返回 404，状态不允许返回 409。

## POST `/api/ai-screen`

继续/开始 AI 筛选的统一入口；本轮扩展现有续跑候选。

请求体不变：

```json
{
  "screening_fields": {},
  "filter_schema_version": 1,
  "profile_summary": "...",
  "profile_facts": {},
  "scrape_task_id": "..."
}
```

续跑候选顺序：

1. 同一来源最近 `paused`；
2. 同一来源最近 `failed`；
3. 同一来源最近 `interrupted`（`restart` 或 `user_finished`）；
4. 同一来源最近 `partial`。

只有 `frozen_filters`、`profile_summary`、`profile_facts` 全部一致时才续跑；否则创建新筛选 run。

成功响应新增/保持字段：

```json
{
  "ok": true,
  "task_id": "<task_id>",
  "resuming": true,
  "platform": "boss"
}
```

## GET `/api/latest-running-task`

`paused` 与 `interrupted` 分支新增 `round_context`：

```json
{
  "round_context": {
    "platform": "boss",
    "keywords": ["python"],
    "cities": ["北京"],
    "screening_fields": {"salary": ["20-50K"]},
    "profile_summary": "...",
    "profile_facts": {},
    "scrape_task_id": "<scrape_task_id>",
    "screen_run_id": "<run_id>",
    "status": "paused",
    "resumable": true
  }
}
```

前端据此恢复 02/03，不再分别拼字段。

## GET `/api/latest-pipeline-result`

响应新增 `round_context`，结构与上一致。`round_context` 由该结果快照来源的父抓取 run 与 AI run 合并生成；只有快照确实可追溯到来源 run 时才返回，不伪造。

## GET `/api/task-state/<run_id>`

本轮不改变公共字段；暂停态仍返回 `status=paused`、`pause_info`、计数。前端用轮询看到暂停完成后切换按钮。

## 前端主动作契约

`screenFlow.ts` 输出统一动作：

```ts
type ScreenPrimaryAction =
  | { kind: "pause"; label: "暂停筛选" }
  | { kind: "continue"; label: "继续 AI 筛选" }
  | { kind: "start"; label: "开始 AI 筛选" }
  | { kind: "recrawl"; label: "全部重抓" }
  | { kind: "pause-recrawl"; label: "暂停重抓" }
  | { kind: "continue-recrawl"; label: "继续重抓" }
  | { kind: "none" };
```

规则：

- 03/04 同一时刻只渲染一个主动作；
- 暂停后 03 次按钮为“查看结果”；
- 失败后 03 次按钮为“结束并保存结果”；
- 可续跑任务存在时 04 只显示“继续 AI 筛选”；
- 任务真正完成且有待确认时 04 只显示“全部重抓”；
- 本轮从未跑过 AI 时显示“开始 AI 筛选”。

## 数据兼容

- 不新增数据库表；
- 不改变 `screening_runs` 既有字段含义；
- `interrupted + user_finished` 仅允许更新错误码/原因等元数据，不允许直接改状态为 running；
- 旧快照缺 `round_context` 时前端按现状退化，不报错。
