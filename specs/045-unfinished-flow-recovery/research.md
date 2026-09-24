# Research: 未完成流程统一找回与关闭保存

## 决策

### D1. 落轮复用既有终态链路
- 证据：`webui/task_continue_api.py` 的 `/api/task/finish/<run_id>` 已构造部分结果、调用 `save_finished_round`、写 `user_finished`、清理浏览器；`webui/result_rounds.py` 已支持 done/partial/scraped_only 的一轮一改写。
- 结论：不新增保存链路；桌面保存与恢复接回继续走现有 API。
- 影响：B101 的历史可见性可由现有 result_snapshot 满足；不新增页面。

### D2. 启动恢复复用 latest-running-task
- 证据：该接口已有内存 running → DB paused/可恢复 failed → DB interrupted → succeeded 抓取兜底顺序；043 已挂 `run_notice`。
- 缺口：数据库中 queued/running/paused 的抓取/筛选残留若早于 interrupted 查询或无 worker，可能不被统一返回。
- 结论：在 DB 兜底路径中识别无 worker 的 queued/running/paused 残留，返回与 paused 等价的恢复形态；保留一次性提醒。

### D3. 关闭确认由桌面层执行
- 证据：`packaging/desktop.py` 已注入 `events.closing` 与 `_quit_and_cleanup`；pywebview `Event.set()` 集合中任一 False 会返回 should_cancel=True，WinForms 会取消 FormClosing。
- 结论：桌面层同步查询 `/api/latest-running-task`，有任务时调用落轮 API，等待结果；确认失败返回 False 保持窗口。
- 影响：`DesktopJsApi.window_close()` / `quit_app()` 也应复用同一决定器，避免原生 X 与自绘 X 行为分叉。

### D4. 提醒一次性沿用 043
- 证据：`run_notice.py`、`run_notice_state` 水位与 `/api/run-notice/mark` 已保证最新未收尾流程只提醒一次。
- 结论：不新增启动弹窗/提醒机制；扩展候选识别，正常落轮的不进入提醒。

### D5. pywebview 版本
- 证据：本仓 `.venv` pywebview 6.2.1；WinForms `on_closing` 支持 closing 事件取消。
- 结论：可继续用 events.closing，不引入 `confirm_close`（其是 OK/Cancel 原生框，不符合两按钮产品文案）。

## Alternatives considered

- **新增未完成流程专用表/状态机**: 违反已冻结边界；放弃。
- **桌面层直接读 SQLite**: 跨层且与 Flask 并发写冲突风险高；放弃。
- **启动抢救式保存**: 数据已由抓取边跑边落库，重启保存会伪造状态；放弃。
- **只用浏览器 beforeunload**: 无法覆盖桌面原生关闭，也不可靠；放弃。
