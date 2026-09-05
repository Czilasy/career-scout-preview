# Contract: 统一白箱服务

**Version**: 033 V2

业务模块通过本契约报告事实和读取结论。任何调用方都不得自行把“有岗位”“返回 ok”或“子任务结束”解释为成功。

## 公共操作

### 1. begin

```text
begin(owner_kind, owner_id, plan, parent_owner_id=None) -> WhiteboxRunRef
```

要求：

- `plan` 必须列出全部必需阶段和稳定计划单元。
- 同一 `(owner_kind, owner_id)` 重复调用必须幂等。
- 已有计划与新计划不一致时必须返回冲突，不能无痕覆盖。
- 成功返回前，任务计划必须已经持久化。

### 2. record

```text
record(run_ref, fact) -> RecordReceipt
```

`fact` 必须包含：

- 唯一幂等键；
- 事件类型和发生时间；
- 阶段；
- 可选的单元类型、单元键和尝试次数；
- 是否属于必需证据；
- 已脱敏参数。

要求：

- 重复幂等键返回原收据，不重复累计。
- 参数缺失或值域非法时拒绝写入。
- 必需证据写入失败时不能只返回警告。
- 记录失败处理遵守 `whitebox_incomplete` 与应急追加契约。

### 3. finalize

```text
finalize(run_ref, lifecycle_end=None) -> WhiteboxConclusion
```

要求：

- 读取冻结计划、所有单元尝试和必需事件。
- 只由统一归并规则计算结论。
- 调用方不能传入期望的成功状态。
- 重复收口在事实未变化时返回相同结论和修订号。
- 事实发生合法变化时增加修订号并追加修订事件。
- 返回结论成功后，对应任务状态写入必须与该结论一致；任一写入失败时不得向调用方返回完整成功。

### 4. report

```text
report(owner_kind, owner_id, include_events=False) -> WhiteboxReport
```

要求：

- 默认返回任务汇总和单元结果。
- `include_events=True` 时按顺序返回追加事件。
- 对 V2 前历史任务执行只读兼容，不写回旧表。
- 找不到任务与历史证据不足必须是两个不同结果。

## Fact 基础结构

```json
{
  "idempotency_key": "stable-key",
  "event_type": "page_completed",
  "occurred_at": "ISO-8601",
  "stage": "scrape_list",
  "unit_kind": "keyword_city",
  "unit_key": "stable-unit-key",
  "attempt_no": 1,
  "required_evidence": true,
  "payload": {}
}
```

## 页面完成事实

```json
{
  "event_type": "page_completed",
  "payload": {
    "page": 3,
    "planned_pages": 10,
    "returned_count": 25,
    "new_unique_count": 20,
    "has_more": true,
    "resume_page": 4
  }
}
```

`has_more` 允许 `true`、`false` 或 `null`。缺失时不得默认转换。

## 单元结束事实

单元结束必须至少记录：

```json
{
  "event_type": "scope_completed",
  "payload": {
    "scope_complete": true,
    "source_exhausted": false,
    "stop_reason": "target_reached",
    "returned_total_count": 250,
    "unit_unique_count": 218,
    "quality_counts": {
      "salary_source.api_empty": 4
    }
  }
}
```

零结果单元还必须存在 `explicit_empty` 或等价平台空结果事实。仅有 `unit_unique_count=0` 不足以结束为空。

## 失败与降级事实

失败事实必须包含稳定错误码和普通中文原因。降级事实必须包含：

- 原步骤；
- 降级动作；
- 是否仍补齐必需工作；
- 影响的单元或批次。

AI 全部保留使用 `ai_keep_all_fallback`，并明确 `normal_screening_completed=false`。

## 写入失败

处理顺序：

1. 尝试在主存储写 `whitebox_incomplete`；
2. 主存储不可用时写应急追加记录；
3. 两者都失败时抛出明确错误，调用方不得继续返回完整成功；
4. 不得把写入异常吞掉或只写 debug 日志。

## 依赖方向

```text
api/runner/source/guard → webui.whitebox → webui.store_whitebox
                                  ↓
                         webui.whitebox_rules
```

- `store_whitebox` 不得反向引用 runner、API 或 source。
- `whitebox_rules` 是无数据库副作用的纯归并模块。
- 外部模块不得直接调用 `whitebox_rules` 绕过持久化。
