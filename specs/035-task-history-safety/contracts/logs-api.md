# Contract: GET /api/logs（日志读取，022 域扩展）

## 请求

`GET /api/logs`

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `tail` | int | 否 | 尾部行数，默认 200，上限 500 |
| `offset` | int | 否 | 更早分页：返回行号 < offset 的最多 tail 行 |
| `since` | int | 否 | 轮询增量：返回行号 > since 的新增行 |
| `identity` | string | 否 | 客户端记录的文件身份（size:mtime），用于检测轮转 |
| `task_id` | string | **否（本次新增）** | 按任务过滤：仅返回包含该 `task_id` 的日志行 |

## 响应

```json
{
  "ok": true,
  "lines": ["...log lines..."],
  "start": 1,
  "end": 120,
  "total": 120,
  "identity": "12345:1693564800",
  "rotated": false,
  "empty": false
}
```

## 行为规则

- 未传 `task_id`：行为与现状完全一致（全局日志）。
- 传 `task_id`：对读取到的日志行，保留**包含该 `task_id`** 的行，再按既有 tail/offset/since/identity 语义计算分页与增量；`start/end/total` 基于过滤后的行集计算。
- 文件不存在：返回空（`empty: true`），与现状一致。
- 轮转（`rotated`）：语义沿用，`identity` 变化时前端重置展示。
- 该端点仍受本地会话令牌保护（既有 before_request 敏感 GET 清单覆盖），无鉴权变化。
