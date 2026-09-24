# Implementation Plan: 未完成流程统一找回与关闭保存

**Branch**: `codex/fix/unfinished-flow-recovery` | **Date**: 2026-09-24 | **Spec**: [spec.md](./spec.md)

**Input**: Frozen spec from `specs/045-unfinished-flow-recovery/spec.md`

## Summary

用最小增量把 B101–B104 收成一个交付链：

1. **统一落轮/找回**：复用 `save_finished_round`、`/api/task/finish/<run_id>` 与历史轮读路径，不新增独立存储、状态机、页面或提醒体系。
2. **启动恢复扩展**：`/api/latest-running-task` 对启动后无真实 worker 的 queued/running/paused 残留返回可恢复快照，复用现有前端 `restoreRunningTask()` 与 043 一次性提醒水位；已落轮保存的流程不再提醒。
3. **关闭保存**：桌面层在关闭事件上先查询“是否有未结束且有岗位流程”；有则先落轮再继续原有关闭流程；取消时返回不关闭；无任务或零岗位直接关闭。
4. **真实回归**：B104 在实现完成后用真实应用界面执行三条分段用例，并单条记录结论。

## Technical Context

**Language/Version**: Python 3.12+（uv 管理）；TypeScript + Vue 3（Vite）

**Primary Dependencies**: Flask、SQLite（标准库）、pywebview 6.2.1、Vitest

**Storage**: SQLite；沿用 `screening_runs`、`screening_results`、`run_notice_state`，无新表/新列

**Testing**: `uv run python -m unittest`；`npm test`；`npm run build`；真实界面走查

**Target Platform**: Windows 桌面 EXE；本地浏览器访问作为自动测试入口

**Project Type**: desktop-app（本地服务 + Web UI）

**Performance Goals**: 关闭前状态查询在 3 秒内返回；保存完成等待上限 15 秒；启动恢复复用现有单请求

**Constraints**: Python 文件 ≤800 行；Vue ≤1200 行；`task_continue_api.py` 与 `DiscoveryView.vue` 已超限，只做接线级修改；平台树枝不得反向进入通用生命周期。

## Constitution Check

*GATE: PASS*

- **职责分层**: 新增桌面关闭生命周期域 `packaging/desktop_close.py`；后端判定/落轮复用既有 API 与服务；不把业务逻辑塞进 `app.py`。
- **文件尺寸**: 新文件均在预警线内；超限旧文件只做小接线，不新增业务块。
- **引用方向**: `packaging/desktop.py → desktop_close.py → HTTP API`；`running_task_api.py → store/result_rounds`；前端复用既有 composable，不反向依赖视图。
- **拆分纪律**: 非重构 Spec；只做生命周期补口。
- **验证门禁**: 聚焦测试优先；整链收敛后一次最终全量；B104 真实界面单独执行。
- **模块地图**: 修改后登记 `packaging/desktop_close.py` 职责。

## File Boundaries

- **Allowed files**:
  - `packaging/desktop_close.py`（新增）
  - `packaging/desktop.py`（接线级）
  - `webui/running_task_api.py`
  - `webui/result_rounds.py`
  - `webui/run_notice.py`
  - `webui/store_result_history_mixin.py`
  - `webui/src/composables/useDiscoveryExecution.ts`
  - `webui/src/composables/useDiscoveryTasks.ts`
  - `tests/test_run_lifecycle.py`
  - `tests/test_result_rounds.py`
  - `tests/test_desktop_shell.py`
  - `.specify/memory/constitution.md`
  - `CHANGELOG.md`
  - `specs/045-unfinished-flow-recovery/**`
- **Forbidden files**:
  - `webui/app.py`
  - `webui/store.py`
  - `webui/task_continue_api.py`
  - `webui/src/views/DiscoveryView.vue`
  - `webui/src/App.vue`
  - 所有平台模块、抓取脚本、迁移 v1–v6 与正式数据目录
- **New files**:
  - `packaging/desktop_close.py`: 关闭前残留查询、结束保存等待与提示文本；预计 180–260 行。
- **Reference direction**:
  - `desktop.py → desktop_close.py → /api/latest-running-task → running_task_api → result_rounds/store`
  - `useDiscoveryExecution.ts → /api/task/finish`；`useDiscoveryTasks.ts → saveScrapedOnlySnapshot`
- **Line gate**: 新 Python <600；`desktop.py` 仍是宿主编排，不新增大业务块；现有超限文件不扩行。

## Verification Gate

- 每个 Phase 只运行对应聚焦测试与直接相邻回归。
- B101–B103 全部实现并聚焦收敛后，最终一次：
  1. `uv run python -m unittest discover -s tests`
  2. 在 `webui/` 执行 `npm test`
  3. 在 `webui/` 执行 `npm run build`
  4. `uv run python -m unittest tests.test_repo_hygiene`
  5. `git diff --check`、`git status --short`
- B104 在实现完成后单独真实界面执行；不列入自动测试门禁，也不以自动化冒充。
- 若真实环境不可用，只报告“未执行/阻断”，不宣称通过。

## Project Structure

```text
packaging/
├── desktop.py                  # 接线关闭生命周期
├── desktop_close.py            # 新增：关闭判定与保存编排
webui/
├── running_task_api.py         # 启动残留可恢复 + 一次性提醒
├── result_rounds.py            # 必要的落轮收口辅助
├── run_notice.py               # 提醒口径扩展
├── store_result_history_mixin.py
└── src/composables/            # 关闭/恢复前端接线
specs/045-unfinished-flow-recovery/
tests/
```

## Risks

- pywebview 关闭事件取消语义必须通过替身测试锁住：确认期返回 False 取消关闭，确认后返回 True。
- 桌面层 HTTP 调用不能误写正式数据；测试只注入替身或使用隔离库。
- “运行中但无 worker”的残留必须避免误杀当前真实 worker：只在 `latest-running-task` 的数据库兜底路径中处理，且内存活任务优先返回原状态。
- 已落轮但残留 process_log 不应再次提醒；提醒目标只能是未保存轮。

## Complexity Tracking

无违反项。
