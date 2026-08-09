# 数据模型：抓取恢复链路修复

**创建日期**：2026-08-09 | **Plan**：[plan.md](plan.md)

## 实体与关系

### 抓取任务（screening_runs 中 record_kind=process_log）

- **含义**：一次抓取/筛选/重抓过程的任务记录。
- **关键字段**：`id`、`platform`、`status`、`current_stage`、`error_code`、`error_reason`、`interruption_kind`、`processed_count`、`source_count`、`total_scraped`、`total_kept`、`total_dropped`、`execution_params_json`、`frozen_filters_json`。
- **关系**：抓取任务通过 `execution_params.scrape_task_id` 与结果快照关联；AI 筛选通过 `execution_params.scrape_task_id` 回链父抓取任务；重抓通过 `execution_params.source_run_id` 回链结果快照。

### 已抓岗位快照（scrape_run_jobs）

- **含义**：抓取过程中逐条持久化的岗位数据。
- **关键字段**：`run_id`、`platform_job_id`、`combo_key`、`job_payload_json`、`scraped_at`。
- **规则**：只要该表存在岗位记录，任务恢复就必须显示真实数量；这是“治 0”的权威数据源。
- **计数语义**：`scraped_count` 固定为 `scrape_run_jobs` 行数，表示已抓岗位数；02 页主数字使用该值，组合进度仅作辅助。

### 部分结果快照（screening_runs 中 record_kind=result_snapshot）

- **含义**：AI 完成或用户结束保存后生成的可展示结果存档。
- **关键字段**：`id`、`platform`、`status`（`done/partial`）、`search_params_json`/`script_params`、`execution_params_json`、`total_scraped`、`total_kept`、`total_dropped`、`profile_summary`。
- **本轮新增语义**：`script_params/execution_params` 必须记录 `platform` 与 `scrape_task_id`；岗位行必须带平台标识。
- **快照边界**：运行中结束保存时，快照以 finish 请求时已持久化数据为准；未落库批不保证进入。
- **旧快照**：修复前生成的快照缺 `scrape_task_id` 时不伪造；刷新后继续 AI 筛选不保证可用，需明确提示。
- **关系**：结果快照是刷新后进入 03 页继续 AI 筛选的父任务回链载体。

### 续跑接管标记（内存 `_resume_claims`）

- **含义**：防止同一任务被重复续跑的内存集合。
- **生命周期**：续跑启动成功时加入；续跑任务达到 done/failed/paused/cancelled 时释放；结束保存对陈旧标记兜底释放；进程重启后自然清空。
- **规则**：释放后才允许“结束并保存结果”，避免“已被续跑接管”卡死。

### 风控/冷却记录（cooldown.json）

- **含义**：账号在某平台命中高置信风控后的临时限制。
- **关键字段**：`account_id`、`platform`、`until`、`reason`、`from_run`。
- **规则**：本轮保证 `from_run` 写入并在环境检查中返回展示；通用失败不写冷却。

### 登录态缓存（login-state.json）

- **含义**：账号 × 平台登录状态缓存。
- **规则**：只有高置信风控（rate_limited/verification）写 `restricted`；登录失效写 `not_logged_in`；正常成功写 `logged_in`；通用失败不写 `restricted`。

## 状态机

### 现有 DB 状态

`queued / running / paused / succeeded / partial / failed / interrupted`

### 结束保存新增许可

`finish_screening_run` 原子许可：

- `queued → interrupted(user_finished)`
- `running → interrupted(user_finished)`
- `paused → interrupted(user_finished)`
- `failed → interrupted(user_finished)`
- `interrupted(process_restart/operator_stop) → interrupted(user_finished)`
- 禁止：`interrupted(user_cancelled)` 改写、`succeeded/partial` 改写。

写入内容：`status=interrupted`、`error_code=user_finished`、`error_reason=用户提前结束，已保存部分结果`、`interruption_kind=user_cancelled`。

### 恢复接口选取规则

- 返回“最近可恢复任务”时排除 `interruption_kind=user_cancelled` 或 `error_code=user_finished`。
- 可恢复抓取条件：`kind=scrape`、状态为 `paused/failed/interrupted(restart/operator_stop)`、`scrape_run_jobs` 非空。
- 恢复优先级：running > paused > restart-interrupted > failed。
- 已有更新结果快照时，旧 paused/failed/interrupted 均不恢复。
- 数据库真实无岗位时仍返回 0，这是真实结果，不编造。

## 验证规则

- partial 快照中 jobs/dropped 必须带 `platform`，值为任务冻结平台。
- partial 快照的 `scrape_task_id` 必须来自原任务 `execution_params.scrape_task_id`；缺失时不得伪造。
- `latest-pipeline-result` 返回的 `scrape_task_id` 只允许来自已保存快照；旧快照缺字段时前端不得自行猜 ID。
- 冷却 `from_run` 只记录触发任务的 run_id；无来源旧记录展示为空，不编造。
