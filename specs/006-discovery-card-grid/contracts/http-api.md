# HTTP API Contracts: 发现结果页卡片网格化与体验修复

**Feature**: 006-discovery-card-grid  
**Date**: 2026-07-22

## 概述

本功能不新增任何 HTTP API。以下端点均为 005 已有的能力，本次改动在前端复用它们。

## 复用的现有端点

### GET /api/discovery/runs/{run_id}/results

获取发现运行的结果列表。

**用途**：`restoreDiscoveryRun()` 和 `loadDiscoveryResults()` 调用，获取岗位卡片数据。

**返回关键字段**：
- `items[].job_id` — 岗位 ID
- `items[].title` — 岗位名称
- `items[].company` — 公司名
- `items[].salary` — 薪资
- `items[].location` — 地点
- `items[].experience` — 经验要求
- `items[].degree` — 学历要求
- `items[].jd` — **完整 JD 内容**（本功能不再截断）
- `items[].source_url` — BOSS 直聘原始链接
- `items[].category` — 匹配类别
- `items[].match_score` — 匹配分数
- `items[].interest_state` — 当前兴趣状态

### POST /api/discovery/feedback

提交岗位或方向反馈。

**用途**：卡片底部「感兴趣」/「不感兴趣」按钮调用。

**请求体**：
```json
{
  "profile_id": "string",
  "target_type": "job",
  "target_id": "job_id",
  "action": "interested | not_interested",
  "reason_code": "string | null",
  "scope": "exact_job"
}
```

**桥接行为**（后端自动）：
- `action=interested` → `profile_jobs.status='interested'`
- `action=not_interested` → `profile_jobs.status='deleted'` + `screening_trash_records` 记录

### POST /api/discovery/feedback/{feedback_id}/revoke

撤销反馈。

**用途**：卡片「恢复」按钮调用，撤销不感兴趣标记。

### GET /api/discovery/runs/{run_id}

获取单个发现运行的状态。

**用途**：`restoreDiscoveryRun()` 调用，检查运行是否已完成以决定是否恢复结果。

## 结论

无新增端点。所有前端交互复用 005 已定义的 API。
