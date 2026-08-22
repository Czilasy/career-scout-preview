# Data Model: 历史轮次与流程终结语义修复

**Date**: 2026-08-22 | 无表结构变更（无 DDL），本文定义语义与状态机。

## 实体

### 历史轮（screening_runs 行，record_kind='result_snapshot'）

| 字段 | 语义 | 本 spec 变化 |
|---|---|---|
| status | 轮状态 | 取值收敛为 `done` / `partial` / `scraped_only`；`cancelled` / `failed` 不再出现 |
| created_at | 建轮时间 | 历史列表排序键，升级/重抓不变 |
| finished_at | 定稿时间 | 语义升级：内容最后一次落定时刻；重抓回写与补筛升级必须刷新 |
| match_count / mismatch_count / pending_count / total_* | 计数 | 重抓原地重算（现状保持） |
| execution_params.scrape_task_id | 流程关联键 | 防重查询依据（同流程同轮） |

**轮状态机**（唯一合法迁移）：

```text
(创建) ──→ scraped_only ──补筛──→ done | partial
(创建) ──→ done | partial            （自然完成 / 结束保存）
done | partial ──重抓回写──→ done | partial   （原地：计数与定稿时间更新，轮身份不变）
```

- 不存在从任何轮到 `cancelled` / `failed` 的边；这两个值在存量清空后于历史轮中绝迹。

### 流程（一次抓取 + 筛选的完整过程）

- 标识：`scrape_task_id`（+ platform）。
- 约束：一条流程最多对应一条历史轮（写入服务内防重，非调用方自觉）。
- 流程终态与轮的关系：自然完成→轮 done/partial；结束保存→轮 partial（或 done）；取消/暂停/中断→无轮；强杀重启→无轮（中断态，可结束保存或重来）。

### 任务状态公共词汇（唯一一套）

- 数据库任务状态保持不变：`queued/running/paused/succeeded/partial/failed/interrupted/cancelled`。
- 对前端报告唯一映射（公共词汇）：`queued/running/paused/completed/completed_with_pending/failed/interrupted/cancelled`；`waiting` 一词废除。

## 验证规则（来自需求）

- 历史列表查询的可见状态集合 = {done, partial, scraped_only}。
- "最新结果"判定唯一口径：状态 ∈ {done, partial, scraped_only} 且未归档，按平台取最新；全局与按平台共用同一过滤定义。
- 每平台历史轮保留上限 30（现状不变）。
