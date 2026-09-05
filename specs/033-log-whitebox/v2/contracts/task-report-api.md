# Contract: 任务完整性报告

**Version**: 033 V2

## 普通任务返回

现有任务状态和结果接口增加同一份 `integrity` 字段，不分别计算：

```json
{
  "integrity": {
    "conclusion": "partial",
    "label": "部分完成",
    "degraded": true,
    "evidence_complete": true,
    "primary_code": "combo_failed",
    "primary_reason": "1 个关键词和城市组合抓取失败",
    "recommendation": "可以查看已有结果；缺失组合建议重新执行",
    "revision": 1
  }
}
```

允许的 `conclusion`：

- `succeeded`：完整成功
- `empty`：空结果
- `partial`：部分完成
- `failed`：失败
- `unverifiable`：无法确认
- `interrupted`：中断
- 活动任务可为空，并通过现有 `queued`、`running`、`paused` 表示生命周期

普通接口不得返回逐页技术事件或敏感参数。

## 开发者报告

在既有任务状态路由域提供按任务查询的开发者报告。推荐路径：

```text
GET /api/task-state/<owner_kind>/<owner_id>/whitebox
```

成功响应：

```json
{
  "owner_kind": "scrape",
  "owner_id": "run-id",
  "lifecycle_status": "terminal",
  "integrity": {
    "conclusion": "unverifiable",
    "degraded": false,
    "evidence_complete": false,
    "primary_code": "page_evidence_missing",
    "primary_reason": "1 个组合缺少结束证据",
    "revision": 2
  },
  "plan": {
    "planned_units": 20
  },
  "summary": {
    "completed_units": 19,
    "failed_units": 0,
    "unknown_units": 1,
    "unit_output_sum": 4044,
    "run_unique_count": 3419,
    "quality_counts": {
      "salary_source.api_empty": 49
    }
  },
  "units": [],
  "events": []
}
```

## 查询参数

- `include_events=0|1`：默认 0。
- `event_limit`：采用项目已有安全上限；超过上限时返回截断标记和下一页位置。
- 事件必须按 `sequence` 稳定排序。

## 错误

- 业务任务不存在：404，`task_not_found`。
- 业务任务存在但没有 V2 证据：200，`conclusion=unverifiable`，`primary_code=legacy_evidence_missing`。
- 参数非法：400，明确指出非法字段。
- 查询失败：500，使用既有安全错误响应，同时写入主日志；不能返回空成功报告。

## 一致性

以下入口对同一任务必须返回同一个 `conclusion`、`primary_code` 和 `revision`：

- 当前任务状态；
- 历史结果；
- 开发者白箱报告；
- 普通前端任务提示。

任何入口不得根据岗位数、旧 `ok` 或子任务状态重新推断结论。

## 普通用户文案

| 结论 | 主提示 | 建议 |
|---|---|---|
| `succeeded` | 完整成功 | 可查看结果 |
| `empty` | 已完成，没有找到岗位 | 可调整条件后重试 |
| `partial` | 部分完成，部分结果可能缺失 | 查看已有结果或重试缺失部分 |
| `failed` | 执行失败 | 查看原因后重试 |
| `unverifiable` | 无法确认是否完成 | 建议重新执行 |
| `interrupted` | 任务已中断 | 可按现有恢复能力继续或重新执行 |

颜色和图标沿用现有主题语义；`partial` 与 `unverifiable` 不得使用完整成功的绿色状态。
