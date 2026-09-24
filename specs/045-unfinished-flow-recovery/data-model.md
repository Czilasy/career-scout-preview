# Data Model: 未完成流程统一找回与关闭保存

## 沿用实体（无 schema 变更）

### screening_runs
| 字段 | 用途 |
| --- | --- |
| `status` | `queued / running / paused` 表示未结束；`interrupted + error_code=user_finished` 表示已结束保存；`scraped_only` 等快照状态表示已落轮 |
| `current_stage` | 判定抓取/筛选/重抓恢复形态 |
| `error_code` / `interruption_kind` | 识别用户已保存、进程重启与可恢复残留 |
| `source_count` / `processed_count` | 恢复快照进度展示 |
| `profile_id` | 画像隔离 |
| `notice_sent_at` | 043 一次性提醒记号 |

### screening_results / scrape_run_jobs
- 已抓岗位的权威持久化位置。
- 岗位数判定使用现有 `count_scrape_run_jobs(scrape_task_id)`；0 表示无内容。

### run_notice_state
- 按画像保存提醒水位；已提醒或更旧残留保持沉默。

## 恢复查询状态矩阵

| 数据库状态 | 有岗位 | 无 worker | 处理 |
| --- | ---: | --- | --- |
| queued / running / paused | >0 | yes | 启动恢复返回可接回快照；未提醒时触发一次性提醒 |
| queued / running / paused | >0 | no（内存活任务） | 保留现有 running/queued 现场行为 |
| queued / running / paused | 0 | any | 不提醒、不落轮；关闭静默 |
| interrupted(process_restart/operator_stop) | >0 | - | 沿用既有恢复形态 |
| scraped_only / partial / done / interrupted(user_finished) | >0 | - | 已落轮，不再作为未保存提醒 |

## 桌面关闭决策

| 查询结果 | 动作 |
| --- | --- |
| `has_task=false` | 保存窗口状态，关闭 |
| `has_task=true` 且 `job_count<=0` | 保存窗口状态，静默关闭 |
| `has_task=true` 且 `job_count>0` | 提示“结束并保存 / 取消”；确认后 finish 成功才关闭，失败或取消不关闭 |
