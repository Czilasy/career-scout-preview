# Data Model: 偶发失败重试与失败展示优化

本特性**不新增数据库表、不新增字段、不改迁移**；以下为运行期概念模型与不变量。

## 实体

### 1. 抓取组合（Combo）

| 属性 | 说明 |
|---|---|
| unit_key | `关键词｜城市`（既有白箱单元键） |
| 结局 | `succeeded`（抓完，含空结果）/ `skipped`（重试后仍失败被跳过）/ 未开始 |
| 承载 | 白箱单元（既有 `whitebox_units`，不新增结构） |

### 2. 重试额度（RetryQuota）

| 属性 | 说明 |
|---|---|
| 作用域 | 单个组合、单次任务运行 |
| 取值 | 未使用 / 已使用（会话内布尔） |
| 规则 | 失联重启重试、登录复核重试、偶发重试三者共享；任一触发即置为已使用 |

### 3. 白箱事件（既有表新增事件类型，不新增结构）

| 事件类型 | 来源 | 语义 | 参与结论归约 |
|---|---|---|---|
| `retry_scheduled` | 新增（经既有 `record_fact` 写入） | 首次偶发失败，将重试一次 | 否（info、required=False） |
| `unit_failed` / `unit_skipped` | 既有 | 最终跳过 | 是（required） |
| `scope_completed` | 既有 | 抓完（含重试成功） | 是（required） |

### 4. 失败原因名称

| 项 | 说明 |
|---|---|
| 通用登记 | 既有错误注册表（`user_message` 即简短中文名） |
| 列表侧补映射 | 新增：`cdp_unavailable → source_cdp_unavailable`（名称“连不上调试浏览器”） |

## 状态迁移（单组合）

```text
planned → running → ┬─ succeeded（抓完 / 重试成功）
                    └─ failed(偶发) → retry_scheduled → running[attempt=2] ─┬─ succeeded
                                                                            └─ failed → unit_failed（跳过）
```

## 不变量

1. 每组合抓取尝试次数 ≤ 2（首次 + 一次重试）。
2. `retry_scheduled` 事件在每组合每轮最多一条（attempt=1 幂等键）。
3. 跳过与完成互斥：跳过的组合不计入已完成，计入失败数量。
4. 任务收尾后：已完成 + 跳过 + 未开始 = 组合总数（未开始应为 0）。
5. 重试成功时，用户界面不产生该组合的任何失败痕迹（失败数量不变）。
