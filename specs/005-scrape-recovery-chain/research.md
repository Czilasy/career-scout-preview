# 研究决策：抓取恢复链路修复

**创建日期**：2026-08-09 | **Plan**：[plan.md](plan.md) | **Spec**：[spec.md](spec.md)

## 决策汇总

### D1 — 不新增数据库迁移

**决策**：本轮复用现有 `screening_runs`、`screening_results`、`scrape_run_jobs`、`pipeline_checkpoints` 字段；父抓取任务来源写入结果快照的 `script_params/execution_params`，平台标识写入岗位对象与结果行。

**理由**：现有表已经具备保存部分结果、平台和逐岗位持久化能力；缺口是写入与读取，不是表结构。

**备选**：新增 `parent_scrape_task_id` 列。被拒绝：需要迁移与兼容层，且当前 `execution_params_json` 已能承载该关系，改动面更小。

### D2 — 恢复路径以持久数据为准，failed 兜底只是最后保险

**决策**：前端任何恢复入口都先加载 `task-state`/DB 计数；`latest-running-task` 增加 failed 抓取恢复兜底，但兜底不是“治 0”的手段。

**理由**：B027 的 0 是恢复路径没有加载真实计数，而不是缺少恢复入口；只加兜底会掩盖主路径缺陷。

**备选**：只在 failed 时返回计数。被拒绝：paused/interrupted 同样可能因前端快照为空显示 0，必须统一由真实数据填充。

### D3 — 结束保存状态机扩展，运行中结束需要 worker 终态保护

**决策**：`store.finish_screening_run` 允许 `queued/running/paused/failed` 与 `interrupted(process_restart/operator_stop)` 进入 `interrupted/user_finished`；运行中结束先停 `stop_event` 与浏览器，再从 DB 已持久化内容生成 partial 快照；worker 在写终态前检测 `user_finished` 并跳过覆盖。

**理由**：用户明确要求“任务在跑也能结束并保存”；不保护 worker 终态会形成 finish 后被 worker 写回 succeeded/failed 的状态竞争。

**备选**：要求先取消再保存。被拒绝：与用户确认的“运行中有权直接结束并保存”冲突。

### D4 — 续跑接管标记在任务再次暂停/终态/取消时释放

**决策**：`_resume_claims` 在续跑 worker 进入 done/failed/paused/cancelled 时释放；`api_task_finish` 对陈旧标记兜底释放。

**理由**：B027 卡死点是标记永不释放；释放时机覆盖所有后续状态，而不是只在启动失败路径释放。

**备选**：finish 时忽略标记。被拒绝：会让续跑并发保护失效，可能允许重复续跑。

### D5 — 全部视图不混合重抓，只引导选择平台

**决策**：三档视图都显示“全部重抓”，但 `all` 视图点击后引导选择 BOSS/智联，再按单平台执行；后端重抓接口保持单 `source_run_id` 语义不变。

**理由**：用户确认混合重抓不作为验收重点，核心是入口可见；保持后端单来源语义可避免跨平台任务身份复杂度。

**备选**：后端支持多来源聚合任务。被拒绝：改动 `_run_recrawl_task` 全链路且非本轮核心，收益不匹配风险。

### D6 — 风控判定只认高置信信号，通用失败不写冷却

**决策**：收紧应用层与子进程层的分类关键词；只有明确拦截文案、HTTP 429/403/412/418、验证码/滑块页或解封时间才暂停并写冷却；通用 `source_blocked` 不再写冷却/restricted 缓存。

**理由**：裸词误判会把正常账号写成受限并冷却 4 小时；B027 用户现象正是正常浏览器可访问却提示受限。

**备选**：保留宽泛词但仅展示不暂停。被拒绝：`_record_risk_signals` 现有链路会写缓存，宽泛词仍会造成持久副作用。

### D7 — 文案平台隔离

**决策**：`_FAILED_CODE_LABELS`/`ERROR_TAXONOMY` 增加平台维度映射或平台参数；`api_task_state` 用 run 平台取文案。

**理由**：B013 回归要求智联任务任何路径不出现 BOSS 文案；现有字典写死 BOSS。

**备选**：仅改前端按平台替换。被拒绝：`pause_info` 由后端生成，前端替换会遗漏其它消费方，且掩盖后端契约错误。

## 未决事实

- 用户命中“受限”的真实错误码/退出码/日志片段仍缺失；本轮以高置信规则与已知回归样本为准，真实复现后再核对日志。
- 用户运行版本是否包含最近的空页/详情误判修复仍待确认；本轮回归覆盖 `scripts/boss_cdp_raw.py` 现有保护，不假设用户旧版本行为。
