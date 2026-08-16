# Data Model: AI 筛选停止/继续/恢复链路统一

**Created**: 2026-08-15
**Feature**: [spec.md](spec.md)

## 实体

### 本轮上下文（RoundContext）

由后端从父抓取 run 与 AI 筛选 run 合并生成，用于前端恢复 02/03。

| 字段 | 来源 | 说明 |
|------|------|------|
| `platform` | AI run / 父抓取 run | BOSS 或智联 |
| `keywords` | 父抓取 `script_params.keyword` | 关键词列表 |
| `cities` | 父抓取 `script_params.city` | 城市列表；全国为 `["全国"]` |
| `screening_fields` | AI run `frozen_filters` | 六类筛选条件 |
| `profile_summary` | AI run `execution_params.profile_summary` | 求职画像 |
| `profile_facts` | AI run `execution_params.profile_facts` | 画像事实 |
| `scrape_task_id` | AI run `execution_params.scrape_task_id` | 来源抓取任务 |
| `screen_run_id` | AI run id | 最近 AI 筛选任务 |
| `status` | AI run 状态 | 用于前端派生主动作 |
| `resumable` | 派生 | 是否为可续跑状态 |

校验规则：

- 关键词/城市缺失时前端不得用空值覆盖已有草稿；只能显式恢复或保持原值。
- `screening_fields` 只写入该 `platform` 对应的草稿槽，不跨平台回填。
- 画像恢复后“我已确认”由前端按“本轮已抓取/筛选过”置为已确认。
- 已冻结条件不得因恢复失败丢失；恢复不到时阻断继续，禁止以空条件发起初筛（2993 回归）。

### 可续跑筛选 run（ResumableScreenRun）

复用 `screening_runs` 表，不新增表。

| 状态 | 是否续跑 | 续跑方式 |
|------|----------|----------|
| `paused` | 是 | 就地继续（`/api/task/continue` 或 `/api/ai-screen` 原地 claim） |
| `failed` | 是 | 新 run 接管旧断点 |
| `interrupted` + `error_code=user_finished` | 是 | 新 run 接管旧断点 |
| `interrupted` + `error_code=restart` | 是 | 新 run 接管旧断点（现状保留） |
| `partial` | 是 | 新 run 接管旧断点 |
| `succeeded` | 否 | 视为真正结束；04 显示“全部重抓”处理待确认 |

续跑条件（沿用现有契约）：

- `frozen_filters` 与当前提交的 `screening_fields` 一致；
- `profile_summary` 与 `profile_facts` 与当前提交一致；
- 同一来源 `scrape_task_id`。

### 暂停部分结果快照（PauseSnapshot）

复用 `screening_runs` 表 `record_kind='result_snapshot'`。

| 字段 | 值 |
|------|-----|
| `status` | `partial` |
| `record_kind` | `result_snapshot` |
| `search_params_json` | 父抓取 `script_params` + `screening` + `platform` 的合并结果 |
| `execution_params_json` | 含 `platform`、`scrape_task_id` |
| 结果行 | `screening_results`（kept/dropped） |
| 待确认行 | `screening_pending_results` |

约束：

- 原 screening run 保持 `paused`，不被快照覆盖；
- 快照只用于 04 展示与历史，不消费断点；
- 续跑仍以原 run 的 verdicts/checkpoints/JD 为准。

## 状态与动作派生

前端主动作由以下输入唯一派生：

- `screen_run_status`：running/queued、paused、failed、interrupted、partial、succeeded、scraped_only、无 run；
- `recrawl_status`：running、paused、failed、无；
- `resultLoaded` 与 `uncertainCount`；
- 当前 04 平台筛选（boss/zhilian/all）。

| 场景 | 03 主动作 | 03 次按钮 | 04 主动作 |
|------|-----------|-----------|-----------|
| AI 运行中 | 暂停筛选 | 无 | 不显示继续 |
| AI 暂停 | 继续 AI 筛选 | 查看结果、结束并保存结果 | 继续 AI 筛选 |
| AI 失败 | 继续 AI 筛选 | 结束并保存结果 | 继续 AI 筛选 |
| AI 保存后未完成 | 继续 AI 筛选 | 查看结果 | 继续 AI 筛选 |
| AI 真正完成 + 待确认 | 不适用 | 不适用 | 全部重抓 |
| 本轮未跑 AI（scraped_only） | 开始 AI 筛选 | 无 | 开始 AI 筛选 |
| 重抓运行中 | 暂停重抓 | 结束并保存结果 | 自动跳 03 |
| 重抓暂停 | 继续重抓 | 查看结果 | 自动跳 03 |
| 重抓失败 | 继续重抓 | 结束并保存结果 | 自动跳 03 |
| 无任务 | 开始 AI 筛选 | 无 | 开始 AI 筛选 |

## 关系

```text
父抓取 run（script_params）
    │
    ▼
AI 筛选 run（frozen_filters / profile / verdicts / checkpoints）
    │
    ├── 暂停时生成 ──► 部分结果快照（04 展示）
    │
    └── 续跑时继承 ──► 新 AI run（已判定保留）
```
