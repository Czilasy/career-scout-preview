# HTTP API 合同：岗位反馈闭环与投递过期提醒

**版本**：`job-feedback-v1`

## 通用约定

- 所有写请求使用现有 session/build identity 防护。
- 时间字段为带 `Z` 或 offset 的 RFC 3339，响应统一为 UTC。
- 写请求必须含客户端生成的 UUID `request_id`；一次不确定网络请求重试复用同一 ID，新的用户确认生成新 ID。
- 稳定错误体：

```json
{
  "ok": false,
  "error_code": "applied_at_in_future",
  "user_message": "投递时间不能晚于当前时刻",
  "details": {}
}
```

- 服务端岗位对象中的 `job_id` 始终是内部 `jobs.id`；平台原始 ID 只在 `platform_job_id`。
- 所有列表/状态/事件/advice 读取均无副作用。

## 岗位身份对象

已入库岗位：

```json
{
  "job_id": "internal-uuid"
}
```

尚未返回内部 ID 的 pipeline 岗位：

```json
{
  "platform": "boss",
  "platform_job_id": "platform-stable-id",
  "canonical_url": "https://www.zhipin.com/job_detail/id.html",
  "title": "Python 后端工程师",
  "company": "示例公司",
  "salary": "20-30K",
  "location": "上海",
  "jd": "...",
  "experience": "3-5年",
  "degree": "本科",
  "extra": {}
}
```

三元组缺一返回 `422 job_identity_incomplete`；URL 与平台不符返回 `422 platform_url_mismatch`；双索引冲突返回 `409 job_identity_conflict`。身份失败时不得创建 `profile_jobs` 或事件。

## 生命周期快照

```json
{
  "profile_id": "profile-id",
  "job_id": "internal-uuid",
  "status": "applied",
  "applied_at": "2026-06-01T02:00:00+00:00",
  "last_follow_up_at": null,
  "revision": 4,
  "reminder": {
    "eligible": true,
    "baseline_at": "2026-06-01T02:00:00+00:00",
    "elapsed_seconds": 5616000,
    "elapsed_days": 65
  }
}
```

`reminder` 是响应时投影，不存入 `profile_jobs`。

## GET `/api/profile-jobs/state`

Query：

- `profile_id`：必填。
- `job_id`：内部岗位 ID；或者同时提供下方三项只用于解析已存在岗位。
- `platform`、`platform_job_id`、`canonical_url`：必须完整，读取请求不会因此创建岗位。

成功 `200`：

```json
{
  "ok": true,
  "exists": true,
  "state": { /* 生命周期快照 */ }
}
```

没有画像关联时返回 `200 {"ok":true,"exists":false,"job_id":"..."}`。未知岗位或画像返回 `404 not_found`。只查看不得标记已读。

## POST `/api/profile-jobs/actions`

请求：

```json
{
  "request_id": "client-uuid",
  "profile_id": "profile-id",
  "job": { "job_id": "internal-uuid" },
  "action": "mark_applied",
  "applied_at": "2026-06-01T10:00:00+08:00",
  "target_status": null
}
```

action allowlist：

- `mark_read`
- `mark_applied`
- `correct_applied_at`
- `follow_up`
- `mark_stale`
- `restore_applied`
- `correct_status`

字段规则：

- `correct_applied_at` 必须带 `applied_at`。
- `mark_applied` 可带 `applied_at`；没有历史投递时间时默认服务器当前时刻。
- `correct_status` 必须带 `target_status`；目标 applied 且没有历史投递时间时必须同时带 `applied_at`。
- 其它 action 不接受不相关字段，返回 `400 invalid_action_payload`。

成功 `200`：

```json
{
  "ok": true,
  "replayed": false,
  "changed": true,
  "event_id": "event-uuid",
  "event_sequence": 4,
  "state": { /* 服务端提交后的快照 */ }
}
```

同 request ID/同载荷重放返回同一 command receipt 所记录的 `event_id/changed`，并由关联事件投影同一个 `event_sequence`（无变化命令的事件字段均为 null），`replayed=true`，不产生第二次写入；`state` 与 `revision` 在响应时读取权威当前快照，可能包含该命令之后已经提交的更新。同 request ID/不同载荷返回 `409 idempotency_conflict`。前端只能使用返回 state 更新显示，并忽略低于当前已知 revision 的陈旧响应。

业务错误：

| HTTP | `error_code` | 语义 |
| --- | --- | --- |
| 400 | `invalid_action` | action 不在 allowlist |
| 400 | `invalid_action_payload` | 字段组合不符合 action |
| 404 | `profile_not_found` | 画像不存在 |
| 404 | `job_not_found` | 内部岗位不存在 |
| 409 | `idempotency_conflict` | request ID 被不同请求复用 |
| 409 | `job_identity_conflict` | 双索引或内部 ID/三元组冲突 |
| 409 | `state_precondition_failed` | follow-up/restore 等前置状态不满足 |
| 422 | `job_identity_incomplete` | 权威三元组不完整 |
| 422 | `platform_url_mismatch` | 规范链接不属于声明平台 |
| 422 | `applied_at_required` | 需要真实投递时间但缺失 |
| 422 | `applied_at_invalid` | 时间不可解析或无时区 |
| 422 | `applied_at_in_future` | 投递时间晚于服务器当前时刻 |
| 422 | `follow_up_before_application` | 投递时间纠正后晚于已有跟进 |

数据库异常返回 `500 persistence_failed`，不得返回原始 SQL/路径；快照和事件都不提交。

## GET `/api/profile-jobs/{profile_id}/{job_id}/events`

Query：`after_sequence` 可选，默认 0；`limit` 默认 100、最大 200。

成功 `200`：

```json
{
  "ok": true,
  "events": [
    {
      "sequence": 7,
      "id": "event-uuid",
      "action": "follow_up",
      "from_status": "applied",
      "to_status": "applied",
      "from_applied_at": "2026-06-01T02:00:00+00:00",
      "to_applied_at": "2026-06-01T02:00:00+00:00",
      "from_last_follow_up_at": null,
      "to_last_follow_up_at": "2026-07-01T02:00:00+00:00",
      "occurred_at": "2026-07-01T02:00:00+00:00"
    }
  ],
  "next_after_sequence": 7
}
```

事件表只包含真实变化；API 不返回 request ID、fingerprint 或 command receipt。

## GET `/api/job-reminders/count?profile_id={profile_id}`

只返回顶部徽标所需的轻量投影：

```json
{
  "ok": true,
  "profile_id": "profile-id",
  "threshold_hours": 720,
  "total": 137
}
```

profile 初始化、切换和生命周期写入成功后调用。它与列表端点使用同一资格函数，不得出现 count/list 规则漂移。

## GET `/api/job-reminders?profile_id={profile_id}`

仅当前 profile；不接受 `platform` 参数。可选 `limit` 默认/最大均为 100，超过返回 `400 invalid_limit`。

成功 `200`：

```json
{
  "ok": true,
  "profile_id": "profile-id",
  "threshold_hours": 720,
  "total": 137,
  "items": [
    {
      "job_id": "internal-uuid",
      "platform": "zhilian",
      "platform_job_id": "platform-id",
      "title": "Python 后端工程师",
      "company": "示例公司",
      "salary": "20-30K",
      "location": "上海",
      "canonical_url": "https://www.zhaopin.com/jobdetail/id.htm",
      "status": "applied",
      "applied_at": "2026-05-01T02:00:00+00:00",
      "last_follow_up_at": null,
      "baseline_at": "2026-05-01T02:00:00+00:00",
      "elapsed_seconds": 8294400,
      "elapsed_days": 96,
      "can_open": true
    }
  ]
}
```

`total` 统计全部合格项，不受列表 100 限制。items 按 `baseline_at ASC, job_id ASC`。BOSS 与智联混排，无平台过滤。URL 无效时 `canonical_url` 可保留安全空值且 `can_open=false`；生命周期操作仍可用。

`applied_at` 缺失/无效，或非空 `last_follow_up_at` 无效的记录不进入 total/items；系统不把损坏的跟进时间当作“从未跟进”并回退到 applied_at。

读取列表、打开 URL 或关闭抽屉不产生任何写入。

## POST `/api/profile-jobs/{profile_id}/{job_id}/advice`

请求体为空或 `{}`；一次只处理一个岗位。后端重新读取当前状态和岗位数据，不接受客户端 JD、时间、elapsed 或 platform。

成功 `200`：

```json
{
  "ok": true,
  "action": "follow_up",
  "reason": "已超过 30 天没有新的跟进记录，建议先主动确认进展。",
  "source": "ai"
}
```

`action` 只能是 `follow_up | review`；`source` 为 `ai | rule`。规则兜底同样返回 200：

- 缺少 JD：`review`，说明信息不足，需要人工复核。
- AI 未配置/密钥缺失/超时/网络/限流/服务错误/无效 JSON/非法 action：有 JD 时 `follow_up`，无 JD 时 `review`。

原始 AI 响应、服务错误、Key、endpoint 和提示词不返回。非逾期岗位返回 `409 reminder_not_eligible`，避免把该入口泛化成全岗位建议。请求前后 profile_jobs 和事件计数必须完全相同。

## Legacy PATCH `/api/profile-jobs/{profile_id}/{job_id}`

迁移期保留，但不得直接执行字段 UPDATE：

- `status=read/applied/stale/...` 映射为 `correct_status`，必须由请求头 `Idempotency-Key` 或 body `request_id` 提供 ID。
- `applied_at` 映射为 `correct_applied_at`；同时 status 时在领域服务内作为一个 `correct_status` 命令提交。
- `note` 保留现有独立备注更新语义，但不得与生命周期写入形成部分成功；混合请求要么在一个 transaction 中完成，要么拒绝 `400 legacy_patch_ambiguous`。
- 无 request ID 返回 `428 idempotency_key_required`。

新前端不使用 PATCH。

## Pipeline 收藏/拒绝兼容

`/api/pipeline/jobs/interest`、`reject` 和 `interest/cancel` 请求中的岗位必须升级为权威三元组或内部 ID。它们与生命周期入口共享岗位身份解析模块：

1. 三元组完整且链接合法。
2. 调用 `upsert_job(platform, platform_job_id, canonical_url, ...)`。
3. 使用返回的内部 ID 写 `profile_jobs/feedback_events`。
4. 响应中的 `job_id` 是内部 ID；平台 ID 保持 `platform_job_id`。

不再使用 BOSS-only `save_job`。身份冲突时兴趣反馈和生命周期均零副作用。

## HTTP 状态摘要

| 状态 | 用途 |
| --- | --- |
| 200 | 查询、命令成功、幂等重放、AI 规则兜底 |
| 400 | 结构/action/limit 错误 |
| 404 | profile/job 不存在 |
| 409 | 幂等、身份、状态前置或提醒资格冲突 |
| 422 | 权威身份、URL 或时间语义无效 |
| 428 | legacy 写请求缺少幂等键 |
| 500 | 原子持久化失败 |
