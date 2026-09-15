# Data Model: 043 未收尾流程的一次提醒与轮次数据整条进出

## 新增字段

`screening_runs.notice_sent_at TEXT NULL`（迁移 v6，ADD COLUMN，无回填）

- 语义：该流程的"一次性提醒"已发出的时间；NULL = 从未提醒。
- 生命周期：随所在行一起删除；不参与除提醒闸门外的任何业务判定。
- 索引：不新增（按主键定位单行）。

## 清理视角的实体关系

- 结果轮 `result_snapshot` ──(`execution_params.scrape_task_id`)──> 根账本 `process_log`（抓取）
- 根账本 ──(`scrape_task_id` / `source_run_id` 递归)──> 派生过程记录（AI 筛选 / 重抓）
- 每条过程记录 ──> `screening_results` / `screening_pending_results` / `pipeline_checkpoints` / `scrape_page_progress` / `screening_source_attempts`（均外键 CASCADE，删主行即清）
- 过程记录 ──> `whitebox_runs`（按 owner_kind/owner_id，无外键，需显式删除）
- 过程记录 ──> `task_logs`(task_id) 与 `tasks` 占位行（`kind='screening_event_log'`，需显式删除）

## 判定规则

- **有主**：根账本被任一 `result_snapshot` 引用 → 永不进入淘汰。
- **保护**：被未结束任务（queued / running / paused）引用的流程 → 不删除、不淘汰。
- **无主兜底**：未被引用且无未结束任务的账本，按每平台最近 30 条流程保留，超出整条清除。
- **定稿瘦身**：流程定稿（完成/部分完成且无未结束任务）→ 清除更早中间档，只保留最新一次。

## 状态流

```text
未收尾流程形成 ──(下一次启动)──> 提醒一次（岛一行字 + 结果接回）──> 记号写入 ──> 永久沉默（历史可主动查看/处理）
流程定稿 ──> 更早中间档清除（保留最新一次）
流程删除 / 淘汰 ──> 整条清除（白箱 → 日志/占位行 → 主行级联）
```
