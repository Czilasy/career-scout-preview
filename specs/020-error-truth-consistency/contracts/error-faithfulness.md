# Contract: 错误如实呈现契约（020）

**范围**: 本批不新增/不修改任何 HTTP 端点。本契约为**行为契约**：错误呈现口径与守卫条件。后端权威定义见 `webui/source.py`（熔断器）、`webui/store.py`（降级守卫）；前端见 `webui/src/api.ts`（消息链）。

## 1. 熔断器开闸失败码（source → pipeline → 前端）

**不变式**:

- 开闸期间列表/JD 请求的失败码 = 打开熔断器的最后一个信号（`last_signal`），只可能是 `source_login_required` / `source_verification_required` / `source_rate_limited` / `source_blocked` 四者。
- 登录开闸 → 前端文案为登录失效语义，走列表组合的登录二次复核（probe 通过重试 / 仍失效跳过组合）；风控类开闸 → 对应风控文案与系统性硬停。
- `last_signal` 缺失（防御路径）→ 回落 `source_blocked`，不产生新错误码；`error_registry` 条目零改动。
- `source_cdp_unavailable` 不进熔断器信号集，浏览器自动重启链行为不变。
- 复位：仅「开闸 + 冷却期满 + preflight 通过」三条件齐备时熔断器关闭；冷却未满不做探测；preflight 失败不复位。逐岗位检查点不做复位探测（避免批内 N 次探测）。

## 2. 前端错误消息回退链（ApiError）

```
user_message → message → error_reason → ERROR_MESSAGES[error_code] → error → error_code → 请求失败（status）
```

**不变式**: 映射查表只插在机器码直出之前；更具体的人读字段优先级不变；查不到的码沿既有链直出，不伪造文案。

## 3. 条件降级守卫（store）

`succeeded → failed` 仅允许经 `downgrade_succeeded_if_no_result_round`，且事务内同时满足：

1. run 当前仍为 `succeeded`；
2. 同流程（`execution_params.scrape_task_id` + `platform`）不存在任何可见 `result_snapshot` 轮（status ∈ done/partial/scraped_only）。

任一不满足 → 返回 False、终态不动、落诊断事件。通用状态机（`update_screening_run`）的转换表不放松。

## 4. 判定覆盖口径（续跑）

- 合并触发：`set(checkpoint_ids) − set(verdicts) ≠ ∅`（存在无判定记录的断点岗位）；全覆盖则跳过合并。
- `resume_inconsistent` 事件按覆盖口径记录缺失数，仅记录不阻断。
- 修订自 018（数量比较 → 覆盖比较），018 spec 文本同步修订。

## 5. 续跑重复岗位单侧性（019 补洞）

断点内已保留岗位在本轮命中跨平台去重时：仅存在于剔除列表（重复条目），不出现在保留列表/幸存者/精筛输入；019 spec（SC-003/FR-005）不变。
