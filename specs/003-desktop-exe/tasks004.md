# Task 004：后端 in-process 接线与运行模式

**所属 Wave**：2（并行） | **硬前置**：Task 001、002 完成 | **用户故事**：EXE2、EXE4、EXE5

## 必读文件

- 仓库根 `AGENTS.md`
- `specs/003-desktop-exe/contracts/runtime-mode.md`、`contracts/inprocess-runner.md`（冻结合同）
- Task 001 产出的 `scripts/boss_cdp_raw.py` 的 `run_search_programmatic`、Task 002 产出的 `webui/desktop_runtime.py`

## 写入范围（互斥）

`webui/app.py`、`webui/source.py`、`tests/test_webui_app.py`、`tests/test_source.py`（仅新增/聚焦用例）、`tests/` 新增测试文件（命名自定，如 `tests/test_inprocess_execution.py`）。**禁止**修改 `scripts/`、`packaging/`、前端。

## 原子清单

- [ ] T022 [P] 读取 TaskRunner / WorkbenchRunner / `_make_cdp_source` / `env_check`（app.py）与 BossCdpSource（source.py）现状，记录分派点与子进程语义（只读）
- [ ] T023 添加**先失败**测试：`create_app(RUNTIME_MODE="exe", ...)` 下 TaskRunner 任务状态机（queued→running→succeeded/failed）与子进程模式等价（fake source 注入，不依赖真实 Chrome）
- [ ] T024 添加取消语义测试：in-process 模式 `cancel()` 不触碰 process（无 process 可终止）、状态为 interrupted、已写产物保留
- [ ] T025 添加 WorkbenchRunner in-process 流式持久化测试：`on_poll` 增量入库语义保留（完成前 job 已入库）
- [ ] T026 添加 BossCdpSource `in_process=True` 翻译测试：list-only（`--no-detail`）、detail-only（`--input`）、detail-batch（`--events-output`）三类命令翻译为 programmatic 调用且产物/事件/熔断器行为不变；无法翻译命令返回失败 outcome 不崩溃
- [ ] T027 添加异常映射测试：CDPUnavailable / RiskControl / LoginRequired / SearchCancelled → 对应 failure_code / interrupted（映射表冻结为测试断言）
- [ ] T028 添加 `env_check` EXE 模式测试：响应含 `runtime_mode="exe"`、`deps` 项「内置运行时」恒 ok、`webview2` 项存在（注入检测替身）；源码模式响应与现状一致
- [ ] T029 添加 `_make_cdp_source` 测试：EXE 模式 BOSS 构造传 `in_process=True`；智联构造不变
- [ ] T030 实现 TaskRunner / WorkbenchRunner `execution_mode`（默认 `"subprocess"`）分派：in-process 分支调用 `run_search_programmatic`（`on_log` → `append_log` 按行转发、`on_poll` 透传、`cancel_event` 复用既有事件）；`cancel()` 分支适配；`validate_artifacts` 语义保留；**setup_chrome 分支同样分派**（in-process 调用 `boss.run_setup_chrome` 库式函数，契约 inprocess-runner §6）
- [ ] T031 实现 BossCdpSource `in_process: bool = False` 参数与 argv 翻译执行器（只翻译本类构建的三类命令；SourceOutcome/熔断器/输入 hash/事件校验逻辑零改动）
- [ ] T032 实现 app.py：`RUNTIME_MODE` config 键接线（默认 `"source"`）、`env_check` 适配（`runtime_mode` 字段 + EXE 模式检查项）、`_make_cdp_source` 传 `in_process`、`/api/check` 的 EXE 等价行为（复用 `collect_check_items` 库式路径，不再 spawn 外部 python，契约 inprocess-runner §6）
- [ ] T033 运行聚焦测试 + Python 全量回归（源码模式子进程路径零回归证据）
- [ ] T034 提交：仅本包文件，信息 `feat: wire in-process execution for exe mode`（改动大可拆多个小步提交，但不得越界）

## 完成定义

聚焦测试全绿；`RUNTIME_MODE` 默认 `"source"` 时源码模式行为与基线一致（全量回归证明）；`execution_mode` 默认 `"subprocess"`。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check` 与 `git status --short`。