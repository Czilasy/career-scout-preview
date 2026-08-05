# 契约：in-process 抓取执行（inprocess-runner）

**所属**：specs/003-desktop-exe

**状态**：冻结（2026-08-06）。实现必须遵循本契约；发现契约冲突回到主会话统一修订。

## 1. 背景与目标

EXE 模式没有外部 Python 解释器，`TaskRunner` / `WorkbenchRunner` / `BossCdpSource` 的 `[python.exe, scripts/boss_cdp_raw.py, ...]` 子进程链路必须替换为**应用进程内直接调用**。执行语义（任务状态机、日志、取消、产物、退出码分类）必须与子进程模式等价，源码模式保持子进程不变。

## 2. 新入口：`run_search_programmatic`

在 `scripts/boss_cdp_raw.py` 新增（**`main()` 保持不变**，不重构 CLI 路径；programmatic 与 CLI 共享 `scrape_list` / `scrape_details` / `check_login_state` 等既有模块函数，避免逻辑漂移）：

```python
def run_search_programmatic(
    *,
    keyword: str,
    city: str,
    pages: int,
    cdp_port: int = DEFAULT_CDP_PORT,
    filters: dict | None = None,
    output_path: str | None = None,
    detail_output_path: str | None = None,
    detail: bool = True,
    max_details: int | None = None,
    fmt: str = "json",
    start_page: int = 1,
    allow_dom_fallback: bool = False,
    merge: str | None = None,
    analysis: bool = False,
    events_output: str | None = None,
    enable_parallel: bool = False,
    tab_pool_size: int = 5,
    stagger_range: tuple[float, float] = (5.0, 10.0),
    inter_job_gap_range: tuple[float, float] = (8.0, 15.0),
    reset_every: int = 3,
    close_chrome: bool = False,
    *,
    on_log: Callable[[str], None] | None = None,
    on_poll: Callable[[], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """EXE 模式搜索全流程（列表 + 可选详情 + 可选分析/合并）。

    与 CLI main() 的搜索路径语义等价；返回 {list_data, details}；
    失败通过异常表达（见 §3）。
    """
```

### 2.1 参数映射

参数名与 CLI 一一对应（`--keyword` → `keyword`、`--output` → `output_path`、`--format` → `fmt` 等）；调用方不得再经过 argv 文本往返（**直接传 dict 参数，不解析命令字符串**）。

### 2.2 日志契约

- 内部所有 `print` 输出（既有代码）通过 `contextlib.redirect_stdout` 捕获并转发 `on_log(line)`；不修改既有 print 语句。
- 行内容与子进程模式的 stdout 完全一致（同一批 print），因此 `store.append_log` 落库格式无差异。
- `on_log=None` 时输出到标准 stdout（等价 CLI 行为）。

### 2.3 返回与产物

- 返回 `{"list_data": dict, "details": list | None}`；与 main() 相同路径写产物文件（`flush_jobs` / `write_json_atomic`），产物路径、内容、原子性语义与 CLI 一致。
- `events_output` 非空时按 CLI 语义写终端 safe event JSONL（复用 `scrape_details` 的 `event_callback` 机制）。

### 2.4 轮询回调（on_poll）

- `on_poll` 为可选回调，供 WorkbenchRunner 保留「完成前增量持久化」语义（子进程模式的 `ScraperExecutor.on_poll` 等价物）。
- 实现要求：`on_poll` 在列表逐页循环与详情逐岗位循环的**每个检查点**（与 `cancel_event` 检查点同一位置）调用一次；不传时零开销、零影响。
- 调用方约定：`on_poll` 内只做读产物/持久化等轻量操作，不得抛异常（抛异常视为抓取失败，按 §3 映射）。

### 2.5 取消契约

- `cancel_event` 为可选 `threading.Event`；None 表示不可取消。
- `scrape_list` 的逐页循环与 `scrape_details` 的逐岗位循环内增加取消检查点：`cancel_event.is_set()` 时抛出 `SearchCancelled`（本模块新增异常，或复用现有异常体系——由实现会话定，但必须与 `RiskControlError` / `CDPUnavailableError` 同级、可被 TaskRunner 识别）。
- 取消后已写产物保留（与子进程 terminate 后产物保留语义一致）。
- 取消点新增必须零影响 CLI 路径：CLI 不传 `cancel_event` 时行为与现在完全一致。

## 3. 异常语义（调用方映射）

| 异常 | CLI 退出码（现状） | programmatic 语义 |
|---|---|---|
| `CDPUnavailableError` | 2 | 原样抛出；调用方映射任务失败码（沿用现有 `process_failed` 之外的分类逻辑——TaskRunner 现按 failure_code 落库，in-process 分支按异常类型映射 `failure_code`，具体映射表由实现会话冻结并在单测覆盖） |
| `RiskControlError` | 10 | 原样抛出，携带 `reason` / `page` / `scraped_count` / `output_path`；`on_log` 输出与 `print_risk_control_report` 等价的报告文本 |
| 未登录（main 中 `check_login_state` False → exit 1） | 1 | programmatic 抛 `LoginRequiredError`（新增，语义=登录失效），调用方映射 |
| `SearchCancelled` | —（CLI 无） | 调用方映射为「用户取消」中断语义，不落失败 |

## 4. 消费方接线（契约）

### 4.1 TaskRunner（webui/app.py 内）

- 构造新增 `execution_mode: "subprocess" | "in_process" = "subprocess"`（由 `create_app` 按 `RUNTIME_MODE` 注入；`"exe"` → `"in_process"`）。
- `_execute` 按模式分派：
  - `subprocess`：现有逻辑（零改动）。
  - `in_process`：线程内调用 `run_search_programmatic`，`on_log` → `store.append_log(task_id, line)`（按行转发，保持现有日志格式）；`cancel_event` 复用现有 `_cancel_events[task_id]`；成功 → `validate_artifacts` + `succeeded`；异常按 §3 映射失败。
- `cancel()`：`in_process` 模式跳过 `process.terminate()`（无 process），仅 set cancel_event；`interrupted` 状态语义不变。
- `build_command` / `ScraperExecutor` 在 `in_process` 模式不参与。

### 4.2 WorkbenchRunner（webui/app.py 内）

- 同样分派；`_query_command` 对应的 programmatic 调用参数：`--max-details` → `max_details`、filters 直传。
- **流式持久化语义必须保留**：in-process 分支把既有 `on_poll` 闭包直接传给 `run_search_programmatic(on_poll=...)`，增量入库行为与子进程模式一致（不另起轮询线程）。
- 熔断器 / 登录态回写 / 冷却信号（`_record_risk_signals` 等）由异常映射路径触发，语义与子进程的退出码分类等价。

### 4.3 BossCdpSource（webui/source.py）

- 构造新增 `in_process: bool = False`；`in_process=True` 时内部执行器把本类构建的 argv **翻译为 `run_search_programmatic` 调用**（仅限本类构建的三类命令：list-only / detail-only / detail-batch），其余行为（SourceOutcome、事件校验、熔断器、输入 hash、产物读取）**零改动**。
- 翻译器必须覆盖 `_build_list_command` / `_build_detail_command` / `_build_detail_batch_command` 产出的全部参数组合；无法翻译的命令（如 `--setup-chrome`）在 `in_process` 模式下返回失败 outcome 而非崩溃。
- `ZhilianCdpSource` 已是库式调用（无子进程），**无需改动**；EXE 模式下 `_make_cdp_source` 对 BOSS 传 `in_process=True`、智联保持现状。

## 5. 测试要求

- 新增测试文件（命名与落点由实现会话定，如 `tests/test_boss_programmatic.py`）：
  - 参数等价：CLI 与 programmatic 在相同输入下产生相同产物（用 FakeJobSource 级别替身或 fixture，**不依赖真实 Chrome**）。
  - 日志转发：`on_log` 收到与子进程一致的日志行序列。
  - 取消：`cancel_event` 置位后任务快速停止、产物保留、状态映射为中断。
  - 异常映射：CDPUnavailable / RiskControl / LoginRequired / SearchCancelled → 对应 failure_code / 中断。
  - 取消检查点零影响：`cancel_event=None` 时行为与现状一致（现有测试全绿即证明）。
- 现有 `tests/test_webui_app.py`、`tests/test_source.py` 等在源码模式下必须零回归（子进程路径未动）。

## 6. 工具命令的 EXE 语义（冻结）

以下工具命令在 EXE 模式下不得 spawn 外部 python，语义如下：

| 命令 | EXE 模式语义 |
|---|---|
| `TaskRunner.create_setup_chrome`（`--setup-chrome`） | in-process 调用 `boss_cdp_raw.run_setup_chrome(...)`（既有库式函数，main() 已使用）；TaskRunner 的 setup_chrome 分支同样按 `execution_mode` 分派。日志、取消语义与抓取分支一致。 |
| `/api/check`（`--check`） | 不再 spawn 子进程；复用 `boss_cdp_raw.collect_check_items(cdp_port=...)` 库式路径，返回结构与源码模式一致（源码模式行为不变）。EXE 下不再依赖 `PYTHON_EXECUTABLE`。 |
| `--stop-chrome` / `--import-boss-session` / `--smoke-test` 等 | 不通过 webui API 暴露（现状即如此）；无需适配。 |

## 7. 非目标

- 不改 `main()` CLI 路径、不改 `ScraperExecutor`、不改 `process_executor.py`。
- 本契约只冻结「搜索全流程 + setup_chrome + --check」的 EXE 语义；其余 CLI 工具命令不通过 EXE 暴露。