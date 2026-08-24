# Implementation Plan: JD 抓取卡死防护与日志查看

**Branch**: `022-jd-stall-guard` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)（第二轮）

**Input**: Feature specification from `/specs/022-jd-stall-guard/spec.md`

## Summary

按冻结需求修复 AI 筛选 JD 抓取阶段的"无声悬挂"：新增独立于任务线程的流水线防护组件（`webui/pipeline_guard.py`）——批次登记 + 心跳（产出刷新 300s 计时）→ 独立监控线程判定卡死 → 杀失联抓取工解出任务线程 → 3~5s 后自动重抓（共 3 次）→ 第 3 次失败探测环境分流（环境级暂停交报错模块 / 偶发跳过进待确认）→ 线程不解出时兜底暂停并明示；全部事件落盘 career-scout.log。同时修复所有运行模式日志可用性，前端设置页新增"日志"入口 + 黑框浮窗实时查看。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript（前端）

**Primary Dependencies**: Flask、sqlite3、Vue 3、Vite

**Storage**: `~/.career-scout/logs/career-scout.log`（RotatingFileHandler，5MB×10）；screening_runs / screening_pending_results（既有表，无迁移）

**Testing**: pytest（后端 unittest）、Vitest（前端）

**Target Platform**: Windows 桌面（源码模式 + PyInstaller EXE 模式）

**Project Type**: desktop-app（本地 Flask 后端 + WebView 前端）

**Performance Goals**: 日志浮窗轮询间隔 2s；分页每次 ≤500 行

**Constraints**: 防护监控独立于任务线程；批次重抓不按条去重（接受重复抓取）；不触碰门面文件与 source 域实现

## Constitution Check

- 原则 VI 模块地图：`pipeline_guard.py`（流水线防护域）、`log_api.py`（日志读取路由域）为全新域，符合"找不到对应域才开新文件"；同一批次登记进 constitution 模块地图。
- 门面禁改：`webui/app.py` 仅允许 register_log_routes 一行注册（组装职责）；`store.py`/`source.py`/`scripts/boss_cdp_raw.py` 禁止；`error_registry.py` 只读复用不改。
- 行数边界：新增文件 ≤400 行；改动文件保持 600 行预警线下。
- 引用方向：`runners/ai_screen_jd.py → pipeline_exec_details.py → pipeline_guard.py`；`log_api.py → logging_setup.py`；pipeline_guard 经 ctx 注入依赖，不反向 import app/store。

## File Boundaries

*GATE: Must be completed before tasks. 按冻结需求（范围 = JD 抓取段 + 通用组件）。*

- **Allowed files**:
  - `webui/process_executor.py` — 加可选进程钩子（spawn 回调供防护登记失联清理句柄）+ 心跳透传（on_output 到达批次），约 +15 行
  - `webui/pipeline_exec_details.py` — fetch_job_details 接入防护（批次登记/心跳/卡死重抓/分流标记），约 +40 行
  - `webui/runners/ai_screen_jd.py` — run_jd_stage 创建/持有 guard 并传递；环境级分流复用既有 hard_stop 暂停路径，约 +30 行
  - `webui/app_support.py` — ctx 接线：创建 PipelineGuard、注册 log_api，约 +15 行
  - `webui/app.py` — 注册 register_log_routes(app, store) 一行；确认 configure_logging 全模式调用（已有），约 ±5 行
  - `webui/logging_setup.py` — 如排查发现调用链缺失则补（预期无需大改），约 ±10 行
  - `webui/src/components/AppSettingsMenu.vue` — 设置菜单并排加"日志"入口，约 +10 行
  - `webui/src/api/client.ts` — fetchLogs 客户端函数，约 +30 行
  - `.specify/memory/constitution.md` — 模块地图登记 2 个新文件
  - `tests/` — 新增聚焦测试文件
- **Forbidden files**: `webui/store.py`、`webui/source.py`、`webui/source_boss_cdp*.py`、`scripts/boss_cdp_raw.py`、`scripts/boss/`、`webui/store_migrations*.py`、`webui/error_registry.py`（只读复用）
- **New files**:
  - `webui/pipeline_guard.py` — 流水线防护域：批次登记/心跳/独立监控/失联清理/重抓编排/分流探测/兜底暂停/事件日志（约 320 行）
  - `webui/log_api.py` — 日志读取路由域：读 career-scout.log 尾部/分页/轮询偏移 + 轮转切换，受会话令牌保护（约 170 行）
  - `webui/src/components/LogViewerDialog.vue` — 日志黑框浮窗：旧到新、定位最新、上滑分页、2s 轮询实时刷新、回到底部（约 220 行）
  - `tests/test_pipeline_guard.py`、`tests/test_log_api.py`、`webui/src/components/__tests__/LogViewerDialog.spec.ts`
- **Reference direction**: 后端单向 `runners → pipeline_exec_details → pipeline_guard`；guard 的写库/暂停经 ctx 注入（write_run/store/错误码语义），不反向 import；`log_api.py` 只读文件系统与 logging_setup。前端 `AppSettingsMenu.vue → LogViewerDialog.vue → api/client.ts`。
- **Line gate**: 改动文件 ≤600 行预警线；新增文件 ≤400 行；app.py 保持 ≤800 行。
- **Rationale**: 防护为全新领域按宪法原则 VI 开新文件并登记；日志 API 对齐现有 `*_api.py` 路由域模式；前端沿用 AppSettingsMenu 入口 + 独立 Dialog 的既有模式。

## 技术方案要点（按冻结需求）

**1. 批次生命周期与心跳（FR-001/FR-002）**
- fetch_job_details 每批开始：`guard.begin_batch(batch_key, attempt)`（记录开始时间、重置心跳）；
- 批次进行中任何产出刷新心跳：子进程 stdout 输出（经 process_executor 的 on_output 透传）、批次结果返回；
- 无心跳累计 300s → 卡死（配置可调）。

**2. 独立监控与解出（FR-003/FR-004）**
- guard 内置 daemon 监控线程（每 5s 扫一次），不依赖任务线程；
- 判定卡死 → 杀该批失联子进程（taskkill /T /F，复用 process_executor 的终止逻辑）→ 任务线程的等待被解开（poll 返回）→ 回到 fetch_job_details 正常流程；
- 任务线程在批次返回后检查 guard 的卡死标记：已卡死且次数 <3 → 等 3~5s → 重抓该批（重新 fetch_details_batch，接受重复抓取）。

**3. 3 次失败分流（FR-005/FR-006）**
- 每批尝试计数由 guard 维护（原始 1 + 重试 2 = 3）；
- 第 3 次仍卡死 → 探测环境：CDP 可达性（preflight 语义）与登录态（check_login_state 语义，复用现有机制，经 ctx 注入）：
  - 环境级（探测失败）→ 复用 run_jd_stage 既有 hard_stop 暂停路径：write_run paused + 明确错误码 + 不关浏览器 + 断点保留，用户处理后点"继续"续跑；
  - 偶发（探测通过）→ 该批岗位标记待确认（复用 screening pending 写入），任务继续下一批。

**4. 最终兜底（FR-007）**
- 若杀进程后任务线程仍不解出（极端死锁，监控侧可观察到批次既未完成也未重抓）：guard 直接把任务标记 paused + 明确提示"任务线程失去响应，请重启应用后继续"（经 ctx.write_run 与内存任务状态），保证不无声悬挂。

**5. 事件日志（FR-008）**
- guard 用 `get_logger("pipeline_guard")` 记录 stall/retry/giveup/divert/fallback 事件（时间、批次、尝试次数、结果）；确认 configure_logging 在源码与 EXE 模式均生效（app.py 已无条件非 TESTING 调用）。

**6. 日志查看（FR-009/FR-010）**
- `log_api.py`：GET /api/logs?tail=N（尾部 N 行）/ ?offset=（更早分页）/ ?since=（轮询增量），每次请求重开文件并携带文件身份（inode/大小）检测轮转，轮转后从新文件读取保证实时更新；受 before_request 会话令牌保护；
- `LogViewerDialog.vue`：黑框浮窗、旧到新、默认定位最新、上滑分页加载 ≤500 行、2s 轮询、翻旧时暂停跟随 + "回到底部"、空态文案。

## Verification Gate

- 功能交付门禁：聚焦测试（test_pipeline_guard / test_log_api / LogViewerDialog.spec.ts）、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 用户端到端真跑验证（SC-003）在交付后进行。
- 本功能不涉及版本提升/打包/发布（收口动作由用户另行指示）。

## Project Structure

```text
specs/022-jd-stall-guard/
├── spec.md / plan.md / tasks.md

webui/
├── pipeline_guard.py             # 新：流水线防护域
├── log_api.py                    # 新：日志读取路由域
├── process_executor.py           # 改：spawn 钩子 + on_output 心跳透传
├── pipeline_exec_details.py      # 改：接入防护
├── runners/ai_screen_jd.py       # 改：接入防护与分流
├── app_support.py                # 改：ctx 接线
├── app.py                        # 改：register_log_routes 一行
└── src/components/
    ├── AppSettingsMenu.vue       # 改：加"日志"入口
    └── LogViewerDialog.vue       # 新：日志黑框浮窗

tests/
├── test_pipeline_guard.py        # 新
└── test_log_api.py               # 新
```

## Complexity Tracking

无宪法违规；无过度设计（已按冻结需求收敛：不做批次内去重、不做虚拟滚动、不单立复现研究任务）。
