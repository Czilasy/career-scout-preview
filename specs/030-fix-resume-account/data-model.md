# Data Model: 续跑账号身份修复

**Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

## 实体与字段变更

### 任务执行参数（screening_runs.execution_params_json，JSON 字段内新增键）

| 字段 | 类型 | 写入时机 | 语义 |
|---|---|---|---|
| `active_account_at_freeze` | string（账号 id） | 任务创建（抓取/AI 筛/重抓三类入口） | 创建时的全局当前账号，作为"用户暂停期间是否主动换号"的比对基准 |

- 无表结构变更；JSON 内新增键，存量行自然缺失（缺失语义 = 不自动换号）。
- 写入口径：高级设置 `browser_account`（与统一继续接口判定时的"当前全局账号"同源读取）。
- 该键只写不改：续跑、暂停、结束均不得改写快照。

### 任务事件（task_events，既有表）

| 事件 kind | 载荷字段 | 写入时机 |
|---|---|---|
| `account_switch` | `from_account`、`to_account`、`from_name`、`to_name`（名称缺失时省略） | 统一继续接口双门槛命中、身份改写发生时 |

- 事件经既有 `append_task_events` 通道持久化，诊断接口（最近 20 条）自动可见。

### 内存任务日志（ctx.tasks[run_id]["logs"]，既有结构）

- 续跑启动时追加一条中文提示行：`本次从账号「X」切换到账号「Y」继续`（名称缺失回退账号 id）。
- 仅在 `account_switch` 事件实际发生时写入；无换号的续跑零新增日志。

## 状态转换（无变化，仅约束确认）

- `paused → running`（claim_paused_screening_run / write_run）：本批次不改状态机。
- 账号改写点收口：任务执行参数中的 `browser_account`/`cdp_port`/`profile_key` 此后仅允许两条路径写——①显式 target_account 换号（既有校验链）②双门槛自动换号（新逻辑）。父借兜底仅允许在冻结账号字段缺失时填充（BOSS 按 R2 角色解析，智联维持现状）。

## 校验规则

- `active_account_at_freeze` 值必须存在于账号簿；账号簿中不存在时仍写入原值（审计优先，判定时自然比对不等）。
- `account_switch` 事件与执行参数改写在同一次继续请求内原子完成（同一请求线程内先判定后落库，沿用既有 persist_frozen_identity 时序）。
- 双门槛判定输入：run（含快照与暂停码）、当前全局账号、账号簿；暂停码取 `run.error_code`，AI 类集合见 research R2。
