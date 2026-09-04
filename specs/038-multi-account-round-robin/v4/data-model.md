# Data Model: 多账号轮询分摊抓取可靠性修复（B091 V4）

V4 不新增业务数据库表。以下为运行时与断点中的领域模型，用于约束实现和测试。

## R2RotationSnapshot

描述一个任务可恢复的 R2 轮询状态。

| 字段 | 含义 | 约束 |
|------|------|------|
| version | 快照结构版本 | 必须可校验；未知版本不得静默回退 |
| task_id | 所属任务 | 必填，与恢复任务一致 |
| platform | 招聘平台 | BOSS 或智联，与冻结任务一致 |
| account_order | 冻结账号顺序 | 不含账号凭据，只保存安全账号标识 |
| quotas | 各账号每轮 R2 配额 | 与任务冻结配置一致 |
| round_no | 当前轮次 | 正整数 |
| active_account | 当前或下一可用账号 | 必须属于冻结账号池，除非队列已空 |
| remaining_quota | 当前账号本轮剩余配额 | 0 至该账号配额 |
| blocked_accounts | 已由自身真实请求触发硬阻断的账号 | 不包含绑定失败或继承短路账号 |
| completed_count | 快照时已落定的唯一详情数量 | 与 JD 断点可核对 |
| saved_at | 保存时间 | 用于诊断，不参与轮询计算 |

### 状态转换

`ready → reserved → requesting → terminal → ready`

- 配额耗尽：当前账号移动到队尾，下一账号成为 active。
- 账号真实撞墙：加入 blocked_accounts，未完成项交给下一可用账号。
- 全部账号撞墙：进入 paused，保留快照。
- 用户显式选择可用账号恢复：active_account 改为用户选择，其他完成与阻断事实保持。
- 快照损坏或与任务身份不一致：进入可恢复暂停，不自动从头开始。

## DetailReservation

表示调度计划，不代表抓取事实。

| 字段 | 含义 |
|------|------|
| segment_id | 任务内唯一分配段标识 |
| account_id | 计划承担账号 |
| round_no | 所属轮次 |
| requested_count | 本段预留数量 |
| remaining_quota | 预留后的账号剩余配额 |
| tail_count | 尚未领取的尾部数量 |

## DetailAttempt

表示某账号实际启动的一次详情请求。

| 字段 | 含义 | 约束 |
|------|------|------|
| attempt_id | 任务内唯一尝试标识 | 每次跨账号接力或浏览器恢复必须变化 |
| segment_id | 来源预留段 | 必填 |
| account_id | 实际请求账号 | 必填 |
| attempt_no | 同一逻辑段内尝试序号 | 从 1 递增 |
| input_count | 实际输入岗位数 | 非负整数 |
| artifact_id | 独立证据标识 | 不含凭据和岗位正文 |
| started | 是否真正进入抓取执行 | 本地短路时为 false |

## DetailAttemptTerminal

| 字段 | 含义 | 核对规则 |
|------|------|----------|
| success_count | 本次产生有效 JD 的数量 | 只计唯一有效结果 |
| failure_count | 已有明确失败终态的数量 | 不包含成功项 |
| short_circuit_count | 未启动平台请求即被本地保护拦截的数量 | 不得用于标记该账号平台限流 |
| unresolved_count | 仍需接力或暂停的数量 | 与下一次接力输入可核对 |
| failure_code | 安全失败分类 | 不记录敏感正文 |
| handoff_account | 接力目标账号 | 无接力时为空 |

恒等式：`input_count = success_count + failure_count + short_circuit_count + unresolved_count`。同一岗位在不同尝试中可有多个失败，但只能在最终账号工作汇总中记一次成功。

## AccountUsageSummary

| 字段 | 含义 |
|------|------|
| account_id | 安全账号标识 |
| reserved_count | 累计预留数量，允许包含重试重复 |
| request_started_count | 实际启动请求的岗位数量 |
| unique_success_count | 最终由该账号产出有效 JD 的唯一岗位数 |
| failure_count | 实际请求失败数量 |
| short_circuit_count | 本地短路数量 |
| handoff_in_count | 接入数量 |
| handoff_out_count | 移交数量 |

所有账号 `unique_success_count` 之和必须等于任务详情抓取成功总数。

## JdPendingOwnership

记录某条 JD 失败曾写入哪些 run，用于成功后的精确清理。

| 字段 | 含义 |
|------|------|
| job_id | 岗位稳定标识 |
| run_ids | 本次 JD 失败写入的任务/来源轮标识集合 |
| failure_stage | JD 失败阶段 |
| failed_code | 安全失败码 |
| resolved | 是否已由有效 JD 解决 |

清理只作用于同一岗位、同一 JD 失败链路写入的记录，不扩大到未解决的 AI 精筛失败。
