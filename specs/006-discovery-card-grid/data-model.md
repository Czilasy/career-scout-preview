# Data Model: 发现结果页卡片网格化与体验修复

**Feature**: 006-discovery-card-grid  
**Date**: 2026-07-22

## 概述

本功能不新增任何数据模型、表或字段。所有数据存储复用 005 已有的 schema。

## 复用的现有数据模型

### discovery_job_snapshots（004 迁移创建）

存储发现运行中抓取的岗位快照，包含完整 JD。

关键字段：
- `id` — 快照 ID
- `run_id` — 所属发现运行
- `job_id` — 关联 jobs 表的岗位 ID
- `title` — 岗位名称
- `company` — 公司名
- `salary` — 薪资
- `location` — 地点
- `jd` — **完整 JD 内容**（本功能直接使用，不再截断）
- `tags` — 标签数组

### discovery_feedback（004 迁移创建）

存储用户对岗位/方向的反馈。

关键字段：
- `id` — 反馈 ID
- `profile_id` — 画像 ID
- `job_id` — 岗位 ID（job-level 反馈时）
- `target_type` — `job` 或 `direction`
- `action` — `interested` 或 `not_interested`
- `revoked_at` — 撤销时间戳（NULL 表示有效）

### profile_jobs（001 迁移创建）

画像-岗位关联表，桥接的目标表。

关键字段：
- `profile_id` — 画像 ID
- `job_id` — 岗位 ID
- `status` — `new` / `interested` / `applied` / `deleted`
- `shown_at` — 最近展示时间

### 桥接关系

```
discovery_feedback (action=interested/not_interested, target_type=job)
    ↓ _bridge_discovery_feedback_to_legacy (自动)
profile_jobs (status=interested/deleted)
    ↓ list_screening_interested / list_screening_rejected
筛选工作台「感兴趣」区 / 垃圾桶区
```

## localStorage 键

| 键 | 用途 | 本次改动 |
|---|---|---|
| `boss-discovery-run` | 存储 `{run_id, profile_id}`，用于刷新恢复 | 不改动，仅确保 `restoreDiscoveryRun()` 被调用 |

## 结论

无新增数据模型。所有数据流复用 005 已有的表结构和桥接机制。
