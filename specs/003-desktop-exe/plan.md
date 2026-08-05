# 技术计划：Windows 桌面版（EXE 发行形态）

**分支**：`main`（本轮不创建功能分支） | **日期**：2026-08-06 | **规格**：[spec.md](spec.md)

**输入**：为 Career Scout 增加 Windows EXE 发行形态：pywebview 桌面壳 + PyInstaller onefile 打包；界面复用现有 Vue 前端（响应式断点已具备）；抓取任务在 EXE 内 in-process 执行（EXE 无外部 Python）；数据目录 `~/.career-scout` 与源码版互通；打包配置公开入库；构建产物 gitignore 隔离；Release 手动发布（GitHub Actions 记入 roadmap 后续）。

## 摘要

核心矛盾：现有抓取链路全部经 `[python.exe, scripts/boss_cdp_raw.py, ...]` 子进程（`TaskRunner`/`WorkbenchRunner` 的 `ScraperExecutor` 与 `BossCdpSource` 的 `_default_run`），EXE 模式下无外部解释器，必须提供等价 in-process 路径；`ZhilianCdpSource` 已是库式调用（直接调用 `zhilian_cdp_raw` 函数），无需改造。

in-process 改造以「**新增不侵入**」为原则：`boss_cdp_raw.py` 新增 `run_search_programmatic(...)`（与 CLI `main()` 共享 `scrape_list`/`scrape_details`/`check_login_state` 模块函数；`main()` 不动）；日志经 `contextlib.redirect_stdout` 转发 `on_log`，行格式与子进程一致；取消在逐页/逐岗位循环加 `cancel_event` 检查点（不传时零影响）；异常（CDPUnavailable/RiskControl/LoginRequired/SearchCancelled）由调用方映射失败语义。消费方（`TaskRunner`、`WorkbenchRunner`、`BossCdpSource`）按 `execution_mode`/`in_process` 分派，源码模式子进程路径零改动。

桌面壳 `packaging/desktop.py` 只做编排：单实例（named mutex）→ 随机端口 → 线程启动 Flask（`create_app(config={"RUNTIME_MODE": "exe", ...})`）→ 就绪轮询 → pywebview 窗口（1280×800、min_size 1024×700、窗口状态记忆）→ closing 时保存状态并终止全部执行 → `os._exit` 兜底。`webui/app.py` 仅新增 `RUNTIME_MODE` 配置与 `env_check` 适配（`deps` 项「内置运行时」语义 + 新增 `webview2` 检查项，注册表检测，函数可注入测试）。打包由 `packaging/career_scout.spec` + `build_exe.ps1` 复现，产物进 `.release/`（已 gitignore）。

## 技术背景

**语言/版本**：Python 3.11.15（本地实测；项目约束 >=3.10，PyInstaller 支持 3.8-3.15，注意 3.10.0 有 bug）；TypeScript 5.9；Node.js 20+

**新增依赖**（仅构建/壳侧）：`pywebview>=6.2.1`（运行依赖，BSD）、`pyinstaller`（构建依赖，仅打包环境）；WebView2 Runtime 为系统组件不打包（Win11 预装，Win10 绝大多数已装）

**存储**：不变（`~/.career-scout`，SQLite + JSON）；新增 `~/.career-scout/desktop_window.json`（窗口状态，schema 版本字段）

**测试**：Python `unittest`；Vitest + Vue Test Utils；`vue-tsc` + Vite build；Playwright 真实渲染（1440×900 桌面、1024×700 中窗、390×844 窄屏）

**目标平台**：Windows 10/11 + WebView2；真实验收环境为 Windows + Chrome

**项目类型**：Flask 后端、Vue 前端与本地 SQLite 组成的桌面式 Web 应用，新增 EXE 发行形态

**性能目标**：onefile 首启自解压 + 后端就绪 ≤10s（spec SC-001）；窗口交互无感知延迟；in-process 抓取吞吐与子进程模式等价

**硬约束**：源码模式（`python webui/app.py`、CLI、既有测试）零回归；`main()` CLI 路径不动；数据目录与 DB schema 不变；构建产物一律 gitignore；打包配置公开入库；EXE 不捆绑 Chromium/WebView2；不做自动更新

**范围**：Windows EXE 打包 + in-process 执行 + 桌面壳 + 环境检查适配 + 构建脚本入库；不含 GitHub Actions 流水线、自动更新、mac/Linux、PyPI/安装器

## 规则门禁

*项目没有 constitution 文件。以下门禁来自全局/项目 `AGENTS.md`、冻结 Spec 和本地 roadmap；Phase 0 已检查，Phase 3 设计后复核。*

| 门禁 | 状态 | 证据/处理 |
| --- | --- | --- |
| 设计前读取本地 roadmap | 通过 | 已读 `roadmap/DIRECTIONS.md` 与 `REFERENCE_GET_JOBS.md`；方向三「EXE 桌面壳：pywebview 壳 + PyInstaller 打包（脚本入库可复现），Release 挂 EXE」为本功能依据；EXE 不捆绑 Chromium 遵循 roadmap 决策 |
| 外部事实查证 | 通过 | pywebview 6.2.1（官网）、PyInstaller Python 3.8-3.15（官方 GitHub）、WebView2 预装与注册表检测（微软官方 Learn），全部记录于 research.md |
| 需求仍处于 Spec 流程，未经授权不实现 | 通过 | Plan 与 Tasks 工件生成后交付，Implement 由用户指派的其他会话执行 |
| 源码模式零回归 | 设计通过，待实现验证 | in-process 全部为新增路径/分支；`main()`、`ScraperExecutor`、子进程路径不动；全量回归门禁 |
| 日志/产物/取消语义等价 | 设计通过，待实现验证 | `redirect_stdout` 转发保证行级一致；取消检查点不传时零影响；产物路径与原子性共用既有函数 |
| 数据与构建产物隔离 | 设计通过，待实现验证 | 数据目录不变；`build/`、`.release/`、`packaging/build-*` 均 gitignore；卫生测试覆盖 |
| 卫生规则（公开仓库） | 通过 | 打包配置公开入库；不提交产物/凭据/本地路径；提交前跑 `test_repo_hygiene` |
| 前端响应式可用性验收 | 待实施验证 | 固定 1440×900 与 1024×700 与 390×844，覆盖任务、提醒、抽屉、环境检查 |

## 项目结构

### 本功能工件

```text
specs/003-desktop-exe/
├── spec.md
├── research.md
├── plan.md
├── contracts/
│   ├── runtime-mode.md      # RUNTIME_MODE + env_check 适配 + WebView2 注册表检测
│   ├── inprocess-runner.md  # run_search_programmatic + 消费方接线
│   └── desktop-shell.md     # 壳：单实例/端口/窗口/退出/错误提示
└── checklists/
    └── requirements.md
```

### 预计源码落点

```text
scripts/
└── boss_cdp_raw.py          # 新增 run_search_programmatic + 取消检查点 + 新异常（main 不动）

webui/
├── app.py                   # RUNTIME_MODE 接线、TaskRunner/WorkbenchRunner in-process 分派、
│                            #   env_check 适配、_make_cdp_source 传 in_process、--check EXE 等价
├── source.py                # BossCdpSource 新增 in_process 参数 + argv 翻译执行器
├── desktop_runtime.py       # 新增：RUNTIME_MODE 判定、WebView2 注册表检测、随机端口（可单测）
└── src/
    └── components/EnvCheckDialog.vue   # runtime_mode 差异文案 + webview2 项渲染（若有）

packaging/                   # 公开入库
├── desktop.py               # 壳入口（PyInstaller entry point）
├── career_scout.spec        # PyInstaller 清单：datas=[webui/dist, data/]、onefile、windowed
├── build_exe.ps1            # 一键构建：npm build → pyinstaller → .release/CareerScout.exe
└── README.md                # 构建文档（前置、步骤、产物位置、排错）

tests/
├── test_boss_programmatic.py   # programmatic 参数/日志/取消/异常映射（不依赖真实 Chrome）
├── test_desktop_runtime.py     # RUNTIME_MODE、WebView2 检测（注入注册表替身）、随机端口
└── 既有测试全量回归
```

**结构决定**：业务规则不继续堆入已经很大的 `app.py`。新增小型 `webui/desktop_runtime.py` 承载运行时判定与平台检测（可注入、可单测）；壳、执行器、运行时检测按互斥文件并行实现；`app.py` 只在集成波次完成装配；`boss_cdp_raw.py` 的 programmatic 入口与其既有模块函数同文件，避免复制抓取逻辑。

## 关键设计

### 1. in-process 执行（contracts/inprocess-runner.md）

1. `boss_cdp_raw.py` 新增 `run_search_programmatic(**params, on_log=None, cancel_event=None)`：与 CLI 搜索路径语义等价；`redirect_stdout` → `on_log`；返回 `{"list_data", "details"}`；失败抛既有/新增异常。`main()` 不动。
2. 取消检查点：`scrape_list` 逐页循环、`scrape_details` 逐岗位循环加 `cancel_event.is_set()` 检查；不传 `cancel_event` 时行为与现状完全一致（既有测试即回归证据）。
3. 消费方分派：`TaskRunner`/`WorkbenchRunner` 构造加 `execution_mode`；`_execute`/`_execute_search_run` 按模式分派；`cancel()` 在 in-process 分支跳过 process 终止；WorkbenchRunner 保留 `on_poll` 流式持久化语义。`BossCdpSource` 加 `in_process` 参数，把本类构建的 argv 翻译为 programmatic 调用，其余逻辑（SourceOutcome/熔断器/hash/事件）零改动；无法翻译的命令返回失败 outcome。
4. 异常映射：CDPUnavailable→(cdp 类失败码)、RiskControl→(blocked/rate_limited 按 hint 分类)、LoginRequired→(login_required)、SearchCancelled→interrupted，映射表由实现会话冻结并单测。

### 2. 运行时模式与环境检查（contracts/runtime-mode.md）

1. `webui/desktop_runtime.py`：`runtime_mode()` 判定（config 优先，`sys.frozen` 兜底）、`check_webview2()`（winreg 只读检测，注入测试替身，非 Windows 返回不可用）、`pick_free_port()`。
2. `app.py`：`create_app` 支持 `RUNTIME_MODE` 键；`env_check()` 响应加 `runtime_mode`；EXE 模式 `deps` 项变「内置运行时」恒 ok、新增 `webview2` 项（注册表检测 + 安装引导文案）。
3. 前端：`EnvCheckDialog.vue` 按 `runtime_mode` 渲染差异文案；`webview2` 项走通用 CheckItem 渲染。

### 3. 桌面壳（contracts/desktop-shell.md）

`packaging/desktop.py`：named mutex 单实例（已存在→提示退出）→ 随机端口 → 线程 Flask（`RUNTIME_MODE="exe"`、`use_reloader=False`）→ 轮询 `/api/session` 就绪 → pywebview 窗口（1280×800、min_size 1024×700、标题含版本、`~/.career-scout/desktop_window.json` 记忆）→ closing 保存状态 + 取消抓取 → `os._exit(0)` 兜底；失败场景 MessageBox + `desktop.log`。

### 4. 打包与构建（packaging/）

1. `career_scout.spec`：entry `packaging/desktop.py`；`onefile` + `windowed`；`datas` 收集 `webui/dist` → `webui/dist`、`data/city_codes.json` → `data/`；`hiddenimports` 覆盖 flask/keyring 后端等（构建验证时收敛）；EXE 名 `CareerScout`。
2. `build_exe.ps1`：`npm ci && npm run build`（前置校验 dist 存在）→ `uv run pyinstaller packaging/career_scout.spec` → 产物 `career_scout` 重命名/移动到 `.release/CareerScout-v{version}.exe`；任一前置失败非零退出。
3. `.gitignore`：确认 `build/`、`dist/`、`.release/` 已忽略；打包产生的 `packaging/build-*/`、`*.spec 变体` 等若不入库则补忽略条目（实现会话在提交前核实 `git status`）。
4. 版本号：spec 从 `pyproject.toml` 读版本注入 EXE 元数据与窗口标题。

## 并行交付拓扑

### 共享合同冻结门禁

进入 Tasks 前以本目录 `spec.md`、`contracts/runtime-mode.md`、`contracts/inprocess-runner.md`、`contracts/desktop-shell.md` 为共享合同。后续任何会话不得自行改 `RUNTIME_MODE` 语义、programmatic 参数/异常、检查项 id、窗口尺寸/单实例行为；发现合同冲突回到主会话统一修订再派发。

### Wave 1：基础实现，可三路并行

| 会话 | 允许写入 | 输出与门禁 |
| --- | --- | --- |
| programmatic 入口 | `scripts/boss_cdp_raw.py`、`tests/test_boss_programmatic.py` | `run_search_programmatic` 全参数等价、日志转发、取消检查点、异常抛出；`main()` 未动；既有脚本测试零回归 |
| 运行时模块 | `webui/desktop_runtime.py`、`tests/test_desktop_runtime.py` | `runtime_mode` 判定、WebView2 注册表检测（注入替身）、随机端口；非 Windows 安全退化 |
| 前端适配 | `webui/src/components/EnvCheckDialog.vue`、对应单测 | 按 `runtime_mode` 渲染差异文案 + `webview2` 项；既有组件测试零回归 |

### Wave 2：消费方接线，可两路并行

| 会话 | 前置依赖 | 允许写入 | 输出与门禁 |
| --- | --- | --- | --- |
| 后端接线 | programmatic 入口、运行时模块 | `webui/app.py`、`webui/source.py`、`tests/test_webui_app.py`、`tests/test_source.py`（聚焦新增用例） | `RUNTIME_MODE` 接线、TaskRunner/WorkbenchRunner in-process 分派与取消、BossCdpSource `in_process` 翻译、env_check 适配、`--check` EXE 等价；源码模式子进程路径零改动；既有测试零回归 |
| 壳 | 运行时模块（仅契约） | `packaging/desktop.py`、`packaging/desktop_tests/`（或并入 tests） | 单实例、随机端口、就绪轮询、窗口状态记忆、closing 终止；纯逻辑可单测，窗口交互留真实验收 |

### Wave 3：打包与集成，串行

1. `packaging/career_scout.spec` + `build_exe.ps1` + `packaging/README.md` + `.gitignore` 核补（单会话）。
2. 本机构建 EXE → 启动验证（窗口出现、界面加载、env-check 正确、发起一次 BOSS 抓取成功）→ 关闭无残留进程 → 单实例行为 → 窗口缩放/记忆。
3. 全量回归：Python 全量、前端全量、类型检查/构建、卫生测试；提交前 `git status` 核实产物零入库。

## 验证策略

### 单元与契约测试

- programmatic：参数等价（同输入同产物，fixture 不依赖真实 Chrome）、日志行序列、取消快速停止且产物保留、四类异常映射、`cancel_event=None` 与现状一致。
- runtime 模块：WebView2 检测（注入注册表替身：存在/缺失/版本无效/非 Windows）、随机端口唯一且可绑定、RUNTIME_MODE 判定优先级。
- 后端接线：`create_app(RUNTIME_MODE="exe")` 下 TaskRunner/WorkbenchRunner 走 in-process 且任务状态机与子进程等价（fake source 注入）、cancel 语义、env_check 响应含 `runtime_mode` 且 EXE 模式 `deps`/`webview2` 正确、`_make_cdp_source` 构造 in_process 参数。
- 壳：单实例互斥（进程级测试或模拟）、窗口状态文件读写/越界回退、错误路径返回码（用替身触发）。

### 前端与真实验收

- EnvCheckDialog 在 `runtime_mode="exe"` 响应下渲染差异文案与 webview2 项；源码模式响应渲染不变。
- 桌面（1440×900）、中窗（1024×700）、窄屏（390×844）完成：环境检查、任务发起/取消、提醒抽屉、结果查看；检查横向溢出、重叠、不可达操作。
- 真实 EXE：未装 Python/Node 的机器（或清空环境变量模拟）双击启动 ≤10s；发起 BOSS 抓取成功；关闭后任务历史与数据完好；重启窗口恢复；双开提示。

### 卫生与发布

- `uv run python -m unittest tests.test_repo_hygiene` 通过；`git status` 无产物/凭据/本地路径；提交身份 `czyooutzilas@gmail.com`；Conventional Commits。
- Release 发布流程（本地构建 → 上传 EXE + SHA256）写入 `packaging/README.md`，实际发布由用户在 EXE 验证通过后执行。

## 回滚与禁用策略

1. 本功能全部为新增路径与新增文件：`RUNTIME_MODE` 默认 `"source"`，不注入即完全保持现状——回滚 = 移除 config 注入与 packaging 目录（或按 git 历史还原）。
2. in-process 路径任何异常不影响源码模式：`execution_mode` 默认 `subprocess`。
3. 数据库 schema 与数据目录零变更，不存在数据迁移回滚。
4. 打包配置入库即公开；构建产物不入库，删除本地 `.release/` 无任何仓库影响。

## 复杂度说明

| 增加项 | 必要原因 | 更简单方案未采用的原因 |
| --- | --- | --- |
| `run_search_programmatic` 新入口（main 不动） | EXE 无外部 Python，必须进程内执行；与 CLI 共享模块函数避免逻辑漂移 | 重构 main 让 CLI 复用入口：改动 CLI 路径，回归面大，违反零回归硬约束 |
| `redirect_stdout` 日志转发 | 不侵入既有 print，行级日志与子进程完全一致 | 改造全部 print 为 logger：改动面大、风险高、收益为零 |
| 取消检查点参数（可选） | EXE 内无法 terminate 进程，需协作式取消 | 不取消：关闭窗口后抓取线程失控，违反 FR-007 |
| `BossCdpSource` argv 翻译执行器 | adapter 契约（SourceOutcome/熔断器/事件）必须完整复用 | 另写一套 in-process adapter：重复契约逻辑，双份漂移 |
| 独立 `desktop_runtime.py` | 运行时判定与平台检测需被 app/壳/测试共享且可注入 | 散落 `sys.frozen` 判断：无法单测、多处漂移 |
| 独立壳 `packaging/desktop.py` | 窗口编排与业务隔离，PyInstaller 入口单一 | 并入 app.py：启动逻辑与 API 耦合，打包入口不清晰 |

## 设计后复核状态

Phase 1-3 已完成：spec 6 个用户故事、17 条 FR、10 条 SC；research 外部事实全部官方查证；三份契约冻结（runtime-mode / inprocess-runner / desktop-shell）。设计门禁全部通过；无待澄清标记。Tasks 阶段将按 Wave 1 → Wave 2 → Wave 3 生成执行包，交付后由用户指派实施。