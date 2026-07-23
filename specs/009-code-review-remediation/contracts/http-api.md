# HTTP API Contract: 009 代码审查整改

**Plan**: [plan.md](plan.md) | **Date**: 2026-07-23

> Phase 1 输出。本 spec 第 1+2 波对 HTTP API 的最小变更。第 3 波的错误响应统一契约作为「目标态」记录，第 3 波激活时再细化迁移路径。

---

## 第 1 波：无 API 契约变更

第 1 波全部是后端内部清理（删死代码、重复 import、_pipeline_tasks 内存清理、constants.py 提取），不改变任何 HTTP 端点的请求/响应结构。

---

## 第 2 波：状态码修正（FR-2.5）

### `GET /api/ai-settings/models`

**变更前**（失败仍 200）：

```
GET /api/ai-settings/models
→ 上游 AI 服务拉取失败
← 200 OK
{
  "ok": false,
  "error_code": "ai_security_error",
  "models": []
}
```

**变更后**（失败返回 502）：

```
GET /api/ai-settings/models
→ 上游 AI 服务拉取失败
← 502 Bad Gateway
{
  "ok": false,
  "error_code": "ai_security_error",
  "models": []
}
```

**前端适配**：`api.ts` 的 `fetchModels` 已通过 `response.ok` 与 `payload.ok` 双重判断，状态码从 200 改 502 不影响前端逻辑（`!response.ok` 仍正确触发 `throw new ApiError(502, payload)`）。无须前端改动。

**一致性**：与同文件 `POST /api/ai-settings/test` 失败时返回 502 保持一致。

---

## 第 2 波：并发行为契约（不变更 URL，但承诺并发安全）

### `POST /api/tasks/<id>/logs`（隐式，append_log 被多处调用）

**承诺**：对同一 `task_id` 的并发追加不再触发 `sqlite3.IntegrityError`，seq 严格连续递增。

**验证手段**：`tests/test_concurrency.py` 模拟 2 线程 × 100 条并发追加，断言无异常且最终 seq 1-200 全部存在。

### `POST /api/jobs`（隐式，save_job 被多处调用）

**承诺**：对同一 `canonical_url` 的并发保存只产生 1 条 jobs 记录，UPDATE 不丢字段（source_url / title / company / salary / location / jd / last_seen_at 全部更新到最新值）。

**验证手段**：`tests/test_concurrency.py` 模拟 2 线程同时 save_job 同一 URL 不同字段值，断言最终只 1 条记录且字段非空。

---

## 第 3 波目标态：错误响应统一契约（占位，激活时细化）

**目标结构**（discovery envelope，第 3 波全仓统一）：

```json
{
  "error_code": "string",
  "user_message": "string",
  "stage": "string",
  "retryable": boolean
}
```

**HTTP 状态码语义**：
- 400：客户端请求语法错误（缺字段、格式错）
- 401：未认证（session token 失效）
- 403：已认证但无权限
- 404：资源不存在
- 422：请求语法对但语义不可处理（如重复标记收藏）
- 429：限流
- 500：服务端未预期错误
- 502：上游网关错误（AI 服务、Chrome CDP）
- 503：依赖未就绪（Chrome 未启动）

**迁移策略**（第 3 波激活时执行）：
- legacy 路由（`/api/tasks`、`/api/profile` 等用 `{ok, error}` 或 `{error}` 的）用 Flask `@app.errorhandler` 包装
- pipeline 路由（用 `{ok: false, error}` 的）迁移到 envelope
- discovery 路由（已用 envelope）保持不变

**前端适配**：`api.ts` 的 `ApiError` 已携带 `payload`，第 3 波只需在 `errorMessage` 工具函数里读 `payload.user_message` 优先于 `payload.error`。

---

## 不在 contract 范围

- 内部 Python 方法签名（`append_log`、`save_job`、`list_jobs_by_ids` 等）→ 见 [data-model.md](../data-model.md)
- 前端组件 props/emit 契约 → 第 3+4 波前端改动，不在 HTTP 契约内
