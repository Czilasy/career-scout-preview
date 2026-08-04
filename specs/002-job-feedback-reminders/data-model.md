# 数据模型：岗位反馈闭环与投递过期提醒

**目标 schema version**：28

## 设计不变式

1. 一个 `(profile_id, job_id)` 只有一个当前生命周期状态。
2. 生命周期状态集合为 `new | interested | read | applied | stale | deleted`。
3. `last_follow_up_at` 是时间事实，不是状态；记录跟进后状态仍为 `applied`。
4. 只有 `status='applied'` 且 `applied_at` 合法时可进入提醒。
5. 提醒 baseline 为 `last_follow_up_at`（如有），否则 `applied_at`；经过时间 `>= 720h` 即逾期。
6. 状态/时间快照、命令回执与生命周期事件在一个 SQLite transaction 中提交或回滚。
7. 生命周期事件只追加，不修改或删除；与 `feedback_events` 完全分离。
8. 岗位隔离以内部 `jobs.id` 为准；平台只参与入库身份和规范 URL 校验。
9. 所有新写入时间使用 UTC RFC 3339；缺失/无效旧时间不猜测。
10. AI 建议和提醒不作为持久实体。
11. `profile_jobs.status` 是唯一当前状态；`feedback_events` 的历史聚合不得覆盖该快照。

## `profile_jobs` 扩展

现有主键和外键不变：

```sql
ALTER TABLE profile_jobs ADD COLUMN last_follow_up_at TEXT;
```

逻辑字段：

| 字段 | 类型 | 约束/语义 |
| --- | --- | --- |
| `profile_id` | TEXT | 画像隔离键，FK `candidate_profiles.id` |
| `job_id` | TEXT | 内部岗位 ID，FK `jobs.id` |
| `status` | TEXT | `new/interested/read/applied/stale/deleted` 之一 |
| `applied_at` | TEXT NULL | 实际投递时刻；新写入为 UTC RFC 3339，不晚于现在 |
| `last_follow_up_at` | TEXT NULL | 最近一次明确跟进时刻；新写入为 UTC RFC 3339，不早于合法投递时刻 |
| `note` | TEXT NULL | 现有备注语义不变，不参与提醒 |
| 其它字段 | 现有 | run、rank、shown 信息保持兼容 |

允许非 `applied` 状态保留历史 `applied_at/last_follow_up_at`，因为它们是已经发生的客观事实。是否提醒只看当前 `status`。恢复已投递因此无需猜造旧投递时间。

## `profile_job_events`

```sql
CREATE TABLE profile_job_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    profile_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    from_applied_at TEXT,
    to_applied_at TEXT,
    from_last_follow_up_at TEXT,
    to_last_follow_up_at TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (profile_id, job_id)
        REFERENCES profile_jobs(profile_id, job_id) ON DELETE RESTRICT
);
```

该表每一行都代表真实状态或时间变化，不保存 no-op、失败请求或 AI 建议。`revision` 是该画像岗位最新事件 sequence；没有客观事件时为 0，不需要在 profile_jobs 增加重复列。

## `profile_job_command_receipts`

```sql
CREATE TABLE profile_job_command_receipts (
    request_id TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    action TEXT NOT NULL,
    changed INTEGER NOT NULL CHECK (changed IN (0, 1)),
    event_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_id, job_id)
        REFERENCES profile_jobs(profile_id, job_id) ON DELETE RESTRICT,
    FOREIGN KEY (event_id) REFERENCES profile_job_events(id)
);
```

实际变化命令先追加 event，再追加关联它的 receipt；无变化命令只追加 `changed=0`、`event_id` 为空的 receipt。重放时通过 `event_id` 连接事件取得 sequence，避免在回执表重复保存一组可能失配的事件身份。响应复用 receipt 的 `changed/event_id`，但 `state` 在响应时重新读取当前 profile_jobs 并投影当前 revision。因此旧请求在更晚命令已经成功后到达，不会把客户端回滚到旧 after 值。

`request_fingerprint` 是以下规范 JSON 的 SHA-256，不包含服务器生成的当前时刻：

```json
{
  "profile_id": "profile-id",
  "job_identity": {"job_id": "internal-id"},
  "action": "mark_applied",
  "target_status": null,
  "applied_at": null
}
```

如果请求使用权威三元组首次入库，fingerprint 使用规范化后的 `platform`、`platform_job_id` 和 `canonical_url`；同一 request 在完成身份解析前后不会改变指纹。事件响应不返回 fingerprint。

## 索引

```sql
CREATE INDEX idx_profile_jobs_reminder_candidates
ON profile_jobs(profile_id, status, applied_at, last_follow_up_at);

CREATE INDEX idx_profile_job_events_history
ON profile_job_events(profile_id, job_id, sequence);

CREATE INDEX idx_profile_job_command_receipts_job
ON profile_job_command_receipts(profile_id, job_id, created_at);
```

现有 `jobs(platform, platform_job_id)` 部分唯一索引和 `jobs.canonical_url` 全局唯一约束继续作为岗位身份边界。提醒查询不连接或过滤 `jobs.platform`。

## 生命周期命令

### `mark_read`

- `status -> read`。
- 保留已有投递/跟进时间。
- 当前已是 `read` 时 `changed=0`。
- 打开详情不会隐式触发。

### `mark_applied`

- `status -> applied`。
- 请求带 `applied_at` 时校验并使用。
- 未带且已有合法 `applied_at` 时保留原值。
- 未带且无合法 `applied_at` 时使用服务器当前 UTC 时刻。
- 保留已有合法 `last_follow_up_at`；若它早于新的投递时间，请求失败，不产生部分写入。

### `correct_applied_at`

- 只修改 `applied_at`，状态保持原值；允许 `applied` 或 `stale` 状态。
- 必须显式提供合法、含时区且不晚于现在的时间。
- 如存在 `last_follow_up_at`，新投递时间不得晚于最后跟进时间。
- 相同规范时刻为 `changed=0`。

### `follow_up`

- 要求当前 `status='applied'` 且存在合法 `applied_at`。
- 状态保持 `applied`，`last_follow_up_at -> 服务器当前 UTC 时刻`。
- 每个新的 `request_id` 表示一次新现实动作并产生 `changed=1`；同 request 重放复用首次时刻。

### `mark_stale`

- `status -> stale`，只允许用户显式调用。
- 保留投递与跟进时间。
- 当前已是 `stale` 时 `changed=0`。

### `restore_applied`

- 要求当前 `status='stale'` 且有合法 `applied_at`。
- `status -> applied`，保留 `applied_at`，`last_follow_up_at -> 服务器当前 UTC 时刻`。
- 新 request 是新恢复动作；同 request 重放不刷新第二次。

### `correct_status`

- 请求必须包含 `target_status`。
- 允许纠正为任一状态，不强制转换图。
- 纠正为 `applied` 时必须已有合法 `applied_at` 或同时提供合法 `applied_at`；不会默认猜当前时间。
- 纠正为 `stale` 仍是用户显式命令，因此合法。
- 默认保留历史投递/跟进时间；提醒资格由纠正后的状态立即重算。
- 目标状态与时间均无变化时 `changed=0`。

## 时间验证

规范化函数必须满足：

1. 输入为 RFC 3339/ISO 8601 文本且具有 `Z` 或显式 UTC offset。
2. 拒绝日期-only、无时区 datetime、无效日期、NaN/epoch 猜测。
3. 转成 timezone-aware UTC datetime 比较。
4. 允许等于服务器当前时刻；未来时刻返回 `applied_at_in_future`。
5. 保存为 UTC ISO text，例如 `2026-08-04T03:30:00+00:00`。
6. API 返回标准化时间和 `elapsed_seconds/elapsed_days`，前端不自行决定逾期。

测试可向领域/查询函数注入 `now`；生产路由不接受客户端 `now`。

## 提醒投影

候选 SQL 只做窄化，不在 SQL 中用日期截断决定资格：

```sql
SELECT pj.*, j.*
FROM profile_jobs pj
JOIN jobs j ON j.id = pj.job_id
WHERE pj.profile_id = ?
  AND pj.status = 'applied'
  AND pj.applied_at IS NOT NULL;
```

后端逐条解析：

```text
baseline = parse(last_follow_up_at) if last_follow_up_at is not null else parse(applied_at)
eligible = baseline exists and now_utc - baseline >= 720 hours
```

`last_follow_up_at` 非空但不可解析时不回退 `applied_at`，该行按数据无效排除，避免产生错误提醒；合法写入路径本身必须杜绝这种值。结果按 `(baseline ASC, job_id ASC)` 排序，即最长未活动优先且同一时刻稳定排序。count 与 list 调用同一个投影服务：count 只返回全部 `total`，list 再返回 `items[:100]`。每项包含 `baseline_at`、`elapsed_seconds`、向下取整的 `elapsed_days` 和安全岗位投影；不在列表复制整段 JD，建议路由按 job_id 从数据库读取。

## 岗位身份解析

生命周期写入只接受两种身份：

1. `job_id`：读取 `jobs` 行；若请求另带三元组，必须逐项一致。
2. 完整三元组：`platform + platform_job_id + canonical_url`，另带展示字段；规范 URL 校验成功后调用双索引 upsert，取得内部 `jobs.id`。

用于生命周期/偏好动作时，双索引算法必须通过接收现有 SQLite connection 的内部 helper 执行，使 jobs upsert、profile_jobs、command receipt、feedback/profile event 属于同一事务；禁止在 action transaction 中调用另一个自行开连接的公开 upsert。

禁止：

- 只有裸 `platform_job_id`；
- 只有 URL 并从 host 猜 platform；
- 使用当前 UI 草稿 platform；
- 用标题、公司或 JD 相似度合并；
- 将 `platform_job_id` 塃入内部 `job_id`。

## migration 28

1. `_MIGRATION_BACKUP_TARGET_VERSION = 28`，使现有 v27 数据库先进入备份门禁。
2. 在单事务中添加 `last_follow_up_at`、创建事件/命令回执表和索引。
3. 不回填时间，不生成历史事件或命令回执，不修改现有 `profile_jobs.status`。
4. 扩展进程允许的 status 常量；表当前没有数据库 CHECK，因此无需重建 `profile_jobs`。
5. 执行 `PRAGMA foreign_key_check`，确认事件/回执无孤儿、profile_jobs 行数未减少、现有 feedback_events 行数/内容未改变。
6. 记录 schema migration 28；重复构造 store 不重复列、表、索引或事件。

## 清理语义

自动清理继续只匹配 `profile_jobs.status='new'`。新增 `read` 和 `stale` 不进入清理，现有 `interested/applied/deleted` 也保持。由于 `profile_job_events` 外键随画像岗位关联 jobs，任何未来物理删除逻辑必须先证明目标仍为 `new` 且没有生命周期事件；v1 不新增物理事件删除入口。

## 与偏好反馈的兼容

- `feedback_events` 保持现有 schema、计数、撤销和 AI 偏好输入语义。
- 用户点击“感兴趣/不感兴趣”仍可显式将当前 snapshot 设置为 `interested/deleted`。
- 一旦用户后续标记已读、已投递或已荒废，当前状态以 `profile_jobs.status` 为准；旧 interested 事件仍是历史偏好事实，但不得在列表投影时覆盖当前状态。
- 生命周期 action 不创建、撤销或修改 `feedback_events`；偏好 action 也不伪造 `profile_job_events` 中列出的生命周期动作。
