# Data Model: 任务完成证据白箱

**Created**: 2026-09-05 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

## 模型目标

数据模型必须同时回答：

1. 任务原本计划完成什么；
2. 每个阶段、组合或批次实际发生了什么；
3. 哪些事实证明完成、失败、降级、恢复或未知；
4. 为什么最终得到当前结论；
5. 同一任务在所有查询入口中如何保持同一结论。

## 1. WhiteboxRun（任务证据总表）

每个可追踪后台任务只有一条当前汇总记录。

| 字段 | 类型 | 约束 | 含义 |
|---|---|---|---|
| `id` | 文本 | 主键 | 白箱内部编号 |
| `owner_kind` | 文本 | 必填 | `scrape`、`screening`、`recrawl`、`workbench`、`legacy_task`、`tuning` |
| `owner_id` | 文本 | 必填 | 对应业务任务编号 |
| `parent_owner_id` | 文本 | 可空 | 父流程编号 |
| `plan_json` | 文本 | 必填 | 冻结的阶段和计划单元快照 |
| `lifecycle_status` | 文本 | 必填 | `queued`、`running`、`paused`、`terminal` |
| `conclusion` | 文本 | 可空 | 终态结论；活动任务为空 |
| `evidence_complete` | 整数布尔 | 必填 | 必需证据是否齐全 |
| `degraded` | 整数布尔 | 必填 | 是否发生过降级 |
| `planned_unit_count` | 整数 | 非负 | 计划单元数 |
| `observed_unit_count` | 整数 | 非负 | 已有事实的单元数 |
| `completed_unit_count` | 整数 | 非负 | 有完整完成证据的单元数 |
| `failed_unit_count` | 整数 | 非负 | 明确失败单元数 |
| `unknown_unit_count` | 整数 | 非负 | 无法确认单元数 |
| `unit_output_sum` | 整数 | 非负 | 各单元唯一输出数量之和 |
| `run_unique_count` | 整数 | 非负 | 任务内最终唯一岗位数 |
| `quality_counts_json` | 文本 | 必填 | 关键字段缺失/降级来源分类计数 |
| `primary_code` | 文本 | 可空 | 主要失败或不完整代码 |
| `primary_reason` | 文本 | 可空 | 普通中文原因 |
| `revision` | 整数 | 单调递增 | 汇总修订号 |
| `started_at` | 时间 | 必填 | 白箱开始时间 |
| `finalized_at` | 时间 | 可空 | 首次收口时间 |
| `updated_at` | 时间 | 必填 | 最近事实或归并时间 |
| `schema_version` | 整数 | 必填 | 白箱结构版本 |

唯一约束：`(owner_kind, owner_id)`。任务报告必须通过这组业务身份读取，不能用岗位数量临时构造。

## 2. WhiteboxUnit（计划单元与尝试结果）

一条记录表示某个计划单元的一次尝试。恢复或重试创建新的 `attempt_no`，不覆盖旧尝试。

| 字段 | 类型 | 约束 | 含义 |
|---|---|---|---|
| `id` | 文本 | 主键 | 单元尝试编号 |
| `whitebox_run_id` | 文本 | 外键、必填 | 所属任务证据 |
| `stage` | 文本 | 必填 | 抓取列表、AI 粗筛、AI 细筛、详情复抓、调参等 |
| `unit_kind` | 文本 | 必填 | 组合、AI 批次、岗位批次、调参轮次等 |
| `unit_key` | 文本 | 必填 | 稳定且脱敏的计划单元身份 |
| `attempt_no` | 整数 | 从 1 开始 | 尝试次数 |
| `recovered_from_unit_id` | 文本 | 可空 | 本次恢复所承接的旧尝试 |
| `planned_pages` | 整数 | 可空、非负 | 计划页数 |
| `completed_pages` | 整数 | 非负 | 已有完成证据的页数 |
| `last_completed_page` | 整数 | 可空 | 最后一页 |
| `scope_complete` | 三值布尔 | 可空 | 是否完成本次计划范围；空表示未知 |
| `source_exhausted` | 三值布尔 | 可空 | 平台是否明确没有更多结果；空表示未知 |
| `returned_total_count` | 整数 | 非负 | 页面实际返回数量之和 |
| `unit_unique_count` | 整数 | 非负 | 组合或批次内唯一结果数 |
| `stop_reason` | 文本 | 可空 | 停止原因 |
| `status` | 文本 | 必填 | 单元状态 |
| `degraded` | 整数布尔 | 必填 | 本尝试是否降级 |
| `evidence_complete` | 整数布尔 | 必填 | 本尝试证据是否完整 |
| `quality_counts_json` | 文本 | 必填 | 关键字段质量分类计数 |
| `error_code` | 文本 | 可空 | 稳定错误码 |
| `error_reason` | 文本 | 可空 | 普通中文原因 |
| `started_at` | 时间 | 可空 | 尝试开始 |
| `finished_at` | 时间 | 可空 | 尝试结束 |
| `updated_at` | 时间 | 必填 | 最近更新时间 |

唯一约束：`(whitebox_run_id, stage, unit_kind, unit_key, attempt_no)`。

### 单元状态

- `planned`：已计划，尚未开始。
- `running`：正在执行。
- `succeeded`：范围完成、证据完整且有结果。
- `empty`：范围完成、证据完整且明确为空。
- `failed`：有明确失败原因。
- `incomplete`：明确只完成部分工作。
- `skipped`：计划单元被跳过。
- `unverifiable`：无法判断是否完成。
- `interrupted`：取消、停止或终止。

## 3. WhiteboxEvent（追加事实）

事件只追加，不原地修改。汇总表和单元表是事件的查询投影，不代替事件。

| 字段 | 类型 | 约束 | 含义 |
|---|---|---|---|
| `id` | 文本 | 主键 | 事件编号 |
| `whitebox_run_id` | 文本 | 外键、必填 | 所属任务 |
| `sequence` | 整数 | 单任务单调递增 | 稳定顺序 |
| `idempotency_key` | 文本 | 单任务唯一 | 防止重试造成重复累计 |
| `stage` | 文本 | 必填 | 发生阶段 |
| `unit_kind` | 文本 | 可空 | 单元类型 |
| `unit_key` | 文本 | 可空 | 单元身份 |
| `attempt_no` | 整数 | 可空 | 尝试次数 |
| `event_type` | 文本 | 必填 | 事件类型 |
| `required_evidence` | 整数布尔 | 必填 | 是否属于最终判定所需事实 |
| `severity` | 文本 | 必填 | 信息、提醒、错误 |
| `payload_json` | 文本 | 必填 | 已脱敏的事件参数 |
| `origin` | 文本 | 必填 | 主存储、应急记录导入等来源 |
| `occurred_at` | 时间 | 必填 | 实际发生时间 |
| `recorded_at` | 时间 | 必填 | 成功持久化时间 |

### 核心事件类型

- `task_started`、`stage_started`、`unit_started`
- `page_started`、`page_completed`
- `scope_completed`、`source_exhausted`、`explicit_empty`
- `unit_failed`、`unit_incomplete`、`unit_skipped`
- `account_switched`、`browser_restarted`、`stall_detected`、`retry_started`、`retry_abandoned`
- `ai_request_failed`、`ai_keep_all_fallback`
- `checkpoint_restored`、`recovery_completed`
- `submission_failed`
- `whitebox_incomplete`、`emergency_record_imported`
- `task_finalized`

## 4. 页面事实

`page_completed` 的参数至少包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `page` | 整数 | 已完成页码 |
| `planned_pages` | 整数 | 本单元计划页数 |
| `returned_count` | 整数 | 平台本页实际返回数 |
| `new_unique_count` | 整数 | 本页加入组合唯一集合的数量 |
| `has_more` | 真/假/未知 | 平台是否还有下一页 |
| `resume_page` | 整数 | 下次恢复起点 |

缺少 `returned_count`、页码或范围信息的页面事件不能单独证明页面完成。

## 5. 停止原因

允许的基础值：

- `target_reached`：完成用户要求的计划范围。
- `source_exhausted`：平台明确没有下一页。
- `explicit_empty`：平台明确返回空结果且范围完成。
- `cancelled`：用户取消。
- `paused`：任务暂停。
- `hard_block`：验证码、限流、登录阻断等硬阻断。
- `soft_failure`：可定位的普通失败。
- `browser_lost`：浏览器连接丢失。
- `persistence_failed`：必需证据无法持久化。
- `unknown`：无法获得真实停止原因；出现时证据不完整。

## 6. WhiteboxEmergencyRecord（备用追加记录）

主存储不可写时使用。它不是第二套结论系统，只是等待导入的最低限度事实。

必须包含：

- 业务任务类型和编号；
- 发生时间；
- 阶段和单元身份；
- 失败写入的事实类型；
- 已脱敏的错误类别；
- 幂等键；
- 内容摘要校验值。

恢复导入后写入 `emergency_record_imported`，原应急记录不用于直接判定成功。

## 7. 最终结论

| 结论 | 必要条件 |
|---|---|
| `succeeded` | 全部计划单元范围完成且证据完整，至少一个可用结果 |
| `empty` | 全部计划单元范围完成且证据完整，全部有明确空结果，总结果为零 |
| `partial` | 存在明确失败、跳过或未完成单元，同时有经过验证的可用结果 |
| `failed` | 没有可用结果，并有明确失败原因 |
| `unverifiable` | 必需证据缺失，无法判断至少一个计划单元是否完成 |
| `interrupted` | 任务因取消或明确停止结束 |

`degraded` 与结论正交。只有必需工作最终全部补齐，降级任务才可能得到 `succeeded` 或 `empty`。

## 8. 状态流转

```text
queued → running ↔ paused
queued/running/paused → terminal
terminal → succeeded | empty | partial | failed | unverifiable | interrupted
```

终态重复收口必须幂等。若晚到事实需要重新汇总，必须增加 `revision` 并记录修订事件，不能无痕覆盖。

## 9. 历史兼容

- V2 不回填旧任务的计划、单元或事件。
- 查询旧任务时，只有旧数据本身足以证明的字段才展示为事实。
- 没有 V2 总表记录或必需证据时，返回 `unverifiable`，原因是“历史证据不足”。
- 兼容结论只存在于读取结果，不写回旧业务表。

## 10. 数据保护

- `payload_json` 和原因字段必须经过既有脱敏规则。
- 禁止保存 Key、Token、Cookie、密码、私钥、完整简历正文和原始 AI 敏感请求。
- 关键词和城市可保存业务值；账号只保存内部账号编号或脱敏标签。
- 应急记录使用与主存储相同的脱敏规则。
