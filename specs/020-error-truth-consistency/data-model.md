# Data Model: 错误如实呈现与数据口径一致（020）

**Created**: 2026-08-23 | **Schema 变更**: 无（零迁移、零外键改动）

## 1. 涉及实体（既有）

### screening_runs（流程 run / 结果快照轮）
- `status`：状态机不变（RUN_TRANSITIONS）。本批新增一条**受守卫的逆转换**：`succeeded → failed` 仅经 `downgrade_succeeded_if_no_result_round` 在「同流程无可见结果轮」校验下发生；通用 `update_screening_run` 的转换表不动（仍拒绝 succeeded→failed）。
- `record_kind`：`process_log`（流程 run，列默认值）与 `result_snapshot`（结果轮）的区分不变；降级守卫用 result_snapshot + 可见状态（done/partial/scraped_only）+ 同流程（execution_params.scrape_task_id + platform）判定"该流程是否有轮"。
- `error_code`/`error_reason`：降级写入 `internal_error` + 「筛选已完成但结果保存失败，点继续可重试保存」语义的原因文本。

### SourceCircuitBreaker（纯内存状态机）
- 状态字段不变（consecutive / last_signal / opened_at / cooldown_until）。
- 新增只读视图：`open_failure_code()`（开闸期间对外失败码 = last_signal ∈ SIGNAL_CODES，否则 source_blocked）、`cooldown_elapsed()`。
- 复位语义不变：仅 `try_reset(preflight_ok)` 且冷却期满可关闭；本批只是接线（冷却期满 + preflight 通过时在批次发起前调用）。

### profile 域三表（删除依赖）
- `candidate_profiles` ←(CASCADE)─ `profile_jobs` ←(RESTRICT)─ { `profile_job_events`, `profile_job_command_receipts` }。
- `delete_profile` 删除顺序：receipts → events → candidate_profiles（profile_jobs 随 CASCADE）。回执表另有 event_id 外键，先删回执再删事件。

## 2. 值语义变化（无结构变化）

| 位置 | 旧 | 新 |
|---|---|---|
| 开闸期间列表/JD 请求失败码 | 恒 `source_blocked` | 透传开闸信号（login/verification/rate_limited/blocked） |
| 续跑判定合并触发 | `len(verdicts) < len(checkpoint_ids)` | `set(checkpoint_ids) − set(verdicts) ≠ ∅` |
| resume_inconsistent 事件负载 | `{"verdicts": n, "checkpoint": m}` | 覆盖口径（记缺失岗位数） |
| 粗筛续跑保留集 | 不排除跨平台重复 | 排除 `_dup_ids`（只进剔除侧） |
| 前端错误消息链 | `user_message‖message‖error_reason‖error‖error_code‖兜底` | `user_message‖message‖error_reason‖ERROR_MESSAGES[error_code]‖error‖error_code‖兜底` |

## 3. 不变量

- 一条流程（scrape_task_id + platform）至多一条可见结果轮（017）；降级仅在"零结果轮"时发生，不破坏该不变量。
- 跨平台去重剔除确定性重放（019）：任意次续跑与一次跑完一致——本批把续跑反向边（断点已保留 + 本轮新命中重复）补齐。
- 终态不被常规路径覆盖：`update_screening_run` 转换表未放松；succeeded 的唯一新出口是带守卫的降级方法。
