# HTTP API Contract Changes: 历史轮次与流程终结语义修复

**Date**: 2026-08-22 | 消费方：本产品自带前端（无外部消费方）

## 行为变更（现有端点）

### POST /api/task/pause/<run_id>
- 响应不变；行为变更：暂停后**不再产生历史轮**（此前会写入 partial 快照）。

### POST /api/task/cancel/<run_id>
- 响应不变；行为变更：取消后**不再产生历史轮**（此前 FR-019 会写入 cancelled 快照）。底层抓取岗位数据保留。

### 错误强制暂停路径（内部行为）
- 硬阻断/内部错误终态**不再产生历史轮**。

### POST /api/task/finish/<run_id>
- 请求/响应结构不变；行为变更：写入走统一收口（同流程防重），任何操作序列下一条流程最多一条轮。
- 状态校验不变：succeeded/partial 状态拒绝（409 already_terminal）。

### POST /api/pipeline/recrawl、单岗位重抓、单 JD 补抓
- **Breaking**：`source_run_id` 变为必填；缺失返回 409 `missing_source_run_id`（此前静默回退到"最新结果"）。

### GET /api/result-history
- `items[].status` 取值域收敛：`done` / `partial` / `scraped_only`。
- `items[].finished_at` 语义明确为**定稿时间**（重抓/补筛后刷新）；排序仍按 `created_at`。

### 任务状态类响应（列表/详情/轮询/恢复）
- 状态词统一为公共词汇一套；`waiting` 废除（统一为 `queued`），`done` 统一为 `completed`，其余不变。

## 端点移除（Breaking）

- `POST /api/reset-latest-result`：路由删除。归档走 `POST /api/result-history/archive-latest`，删除走 `DELETE /api/result-history/<run_id>`。

## 不变契约

- `GET /api/latest-pipeline-result` 响应结构不变；判定口径收为唯一定义（全局与按平台一致：状态 ∈ {done, partial, scraped_only} 且未归档）。
- 历史删除/归档/保留上限（每平台 30）行为不变。
