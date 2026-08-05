# Tasks：Windows 桌面版（EXE 发行形态）

**输入**：`specs/003-desktop-exe/` 下已冻结的 `spec.md`、`plan.md`、`research.md`、`contracts/runtime-mode.md`、`contracts/inprocess-runner.md`、`contracts/desktop-shell.md` 和 `checklists/requirements.md`

**执行方式**：用户指派其它 AI 会话实施。实现共拆为 7 个执行包；每个会话只领取一个 `tasksNNN.md`，不得顺带执行后续包。本仓库已存在多会话并行先例（spec 002），本功能按 Wave 1 → Wave 2 → Wave 3 组织。

**状态**：Tasks 已生成，等待用户指派实施。

## 清单格式

- `[P]` 只标记每个可并行执行包的启动项。领取该包的会话按包内顺序继续执行，其它会话可同时推进不同写入范围。
- `[EXE1]` 下载即用。 `[EXE2]` 环境就绪检查与引导。 `[EXE3]` 桌面窗口体验。 `[EXE4]` 任务照常执行。 `[EXE5]` 源码模式零回归。 `[EXE6]` 构建可复现。
- 所有编号在本文件全局唯一；执行包中的编号必须逐项回填完成证据，不得自行新增或改写业务合同。

## 启动总门禁

1. 新会话先读取仓库根 `AGENTS.md`、所领执行包及其"必读文件"。
2. 检查 `git status --short`；工作区已有用户/其它会话改动，禁止还原、覆盖、批量格式化或暂存不属于本包的文件。
3. `spec.md`、`contracts/runtime-mode.md`、`contracts/inprocess-runner.md`、`contracts/desktop-shell.md` 是冻结合同。发现矛盾时停止并回报主会话，不得在实现会话自行改合同。
4. 并行会话使用互斥写入范围。`webui/app.py`、`webui/source.py` 只归 Task 004 所有；`scripts/boss_cdp_raw.py` 只归 Task 001 所有；`packaging/` 下 desktop.py 只归 Task 005 所有，spec/构建脚本只归 Task 006 所有。
5. 每个包先写或补齐失败测试，再实现，再运行聚焦回归；测试失败、越界改动或仓库共享暂存区不干净时禁止提交。
6. 提交前运行本包命令、`uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status --short` 和 `git diff --cached`；仅暂存本包文件，commit email 固定为 `czyooutzilas@gmail.com`，提交信息使用 Conventional Commits。
7. 并行 Wave 中，执行会话不得使用 broad `git add .`、不得提交其它会话文件；为避免共享 Git index 竞态，主会话在该 Wave 全部返回后统一运行仓库卫生、差异审计并按任务范围创建小步提交。执行会话先提交聚焦测试、差异和变更路径证据。

## Wave 1：基础实现（三路并行）

### Task 001：BOSS programmatic 执行入口

**执行包**：[`tasks001.md`](tasks001.md)

- [ ] T001 [P] [EXE4] [EXE5] 读取 `scripts/boss_cdp_raw.py` 的 `main()` / `scrape_list` / `scrape_details` / `check_login_state` 与 `contracts/inprocess-runner.md`，记录 CLI 参数全集、退出码语义与可复用模块函数（不修改 main）
- [ ] T002 [EXE4] 在 `tests/test_boss_programmatic.py` 添加参数等价先失败测试：programmatic 与 CLI 相同输入产生相同产物（fixture/替身，不依赖真实 Chrome）
- [ ] T003 [EXE4] 添加日志转发测试：`on_log` 收到与子进程 stdout 一致的日志行序列
- [ ] T004 [EXE4] 添加取消测试：`cancel_event` 置位后快速停止、已写产物保留
- [ ] T005 [EXE4] 添加异常测试：CDPUnavailable / RiskControl / LoginRequired / SearchCancelled 原样抛出且携带必要信息
- [ ] T006 [EXE4] 实现 `run_search_programmatic`：参数 dict、`redirect_stdout` → `on_log`、返回 `{"list_data", "details"}`、`main()` 零改动
- [ ] T007 [EXE4] 在 `scrape_list` 逐页循环与 `scrape_details` 逐岗位循环增加可选 `cancel_event` 检查点（默认 None 时行为与现状完全一致）
- [ ] T008 [EXE4] 新增 `SearchCancelled` 与登录失效异常（若既有异常体系无等价物），与 `RiskControlError` / `CDPUnavailableError` 同级
- [ ] T009 [EXE5] 运行聚焦测试 + 既有脚本相关测试全绿（证明 `cancel_event=None` 零影响）
- [ ] T010 [EXE5] 检查并仅提交 `scripts/boss_cdp_raw.py`、`tests/test_boss_programmatic.py`，提交信息 `feat: add in-process search entry`

### Task 002：桌面运行时模块

**执行包**：[`tasks002.md`](tasks002.md)

- [ ] T011 [P] [EXE2] 读取 `webui/app.py` 的 `create_app` config 注入方式与 `env_check()` 契约、`contracts/runtime-mode.md`（只读，不修改 app.py）
- [ ] T012 [EXE2] 在 `tests/test_desktop_runtime.py` 添加 `runtime_mode` 判定先失败测试（config 优先、`sys.frozen` 兜底）
- [ ] T013 [EXE2] 添加 WebView2 注册表检测测试（注入注册表替身：已装/未装/版本无效/非 Windows 安全退化）
- [ ] T014 [EXE2] 添加随机端口测试（可绑定、每次唯一）
- [ ] T015 [EXE2] 实现 `webui/desktop_runtime.py`：`runtime_mode()`、`check_webview2()`、`pick_free_port()`
- [ ] T016 [EXE5] 运行聚焦测试，仅提交 `webui/desktop_runtime.py`、`tests/test_desktop_runtime.py`，提交信息 `feat: add desktop runtime detection`

### Task 003：环境检查前端适配

**执行包**：[`tasks003.md`](tasks003.md)

- [ ] T017 [P] [EXE2] 读取 `webui/src/components/EnvCheckDialog.vue` 与其既有测试，记录响应结构与渲染路径（只读）
- [ ] T018 [EXE2] 在既有/新增组件测试中添加 `runtime_mode="exe"` 响应渲染测试：deps 项差异文案、`webview2` 项按通用 CheckItem 渲染
- [ ] T019 [EXE5] 添加 `runtime_mode="source"` 响应渲染与现状一致、既有测试零回归的测试
- [ ] T020 [EXE2] 实现 EnvCheckDialog.vue：读取 `runtime_mode`、渲染差异文案与 `webview2` 项（不引入新修复动作）
- [ ] T021 [EXE5] 运行前端聚焦测试与 `npm run build`，仅提交 EnvCheckDialog.vue 及其测试，提交信息 `feat: adapt env check for exe mode`

**Wave 1 检查点**：Task 001-003 各自聚焦测试通过、写入范围互斥、冻结合同未被修改后，才能解锁 Wave 2。Task 004 依赖 Task 001 的 programmatic 入口与 Task 002 的运行时模块；Task 005 只依赖 Task 002 的运行时模块与 create_app config 契约。

## Wave 2：消费方接线（两路并行）

### Task 004：后端 in-process 接线与运行模式

**硬前置**：Task 001、002 完成。

**执行包**：[`tasks004.md`](tasks004.md)

- [ ] T022 [P] [EXE4] 读取 `webui/app.py` 的 TaskRunner / WorkbenchRunner / `_make_cdp_source` / `env_check` 与 `webui/source.py` 的 BossCdpSource 现状（只读）
- [ ] T023 [EXE4] 在测试中添加 TaskRunner in-process 分派先失败测试：`create_app(RUNTIME_MODE="exe")` 下任务状态机与子进程等价（fake source 注入）
- [ ] T024 [EXE4] 添加取消语义测试：in-process 模式 cancel 不触碰 process、interrupted 语义不变
- [ ] T025 [EXE4] 添加 WorkbenchRunner in-process 流式持久化测试（`on_poll` 增量入库语义保留）
- [ ] T026 [EXE4] 添加 BossCdpSource `in_process` 翻译测试：list-only / detail-only / detail-batch 三类命令翻译正确；无法翻译命令返回失败 outcome 不崩溃
- [ ] T027 [EXE4] 添加异常映射测试：CDPUnavailable / RiskControl / LoginRequired / SearchCancelled → 对应 failure_code / interrupted
- [ ] T028 [EXE2] 添加 `env_check` EXE 模式测试：响应含 `runtime_mode`、deps 项「内置运行时」恒 ok、`webview2` 项存在；源码模式响应不变
- [ ] T029 [EXE2] 添加 `_make_cdp_source` 测试：EXE 模式 BOSS 传 `in_process=True`、智联保持现状
- [ ] T030 [EXE4] 实现 TaskRunner / WorkbenchRunner `execution_mode` 分派与 cancel 适配
- [ ] T031 [EXE4] 实现 BossCdpSource `in_process` 参数与 argv 翻译执行器（SourceOutcome/熔断器/hash/事件逻辑零改动）
- [ ] T032 [EXE2] 实现 app.py：`RUNTIME_MODE` 接线、`env_check` 适配、`_make_cdp_source` 更新、`--check` 的 EXE 等价行为
- [ ] T033 [EXE5] 运行聚焦测试 + Python 全量回归（源码模式子进程路径零回归证据）
- [ ] T034 [EXE5] 仅提交 `webui/app.py`、`webui/source.py`、相关测试，提交信息 `feat: wire in-process execution for exe mode`

### Task 005：桌面壳

**硬前置**：Task 002 完成（依赖运行时模块与 create_app config 契约；窗口交互留真实验收）。

**执行包**：[`tasks005.md`](tasks005.md)

- [ ] T035 [P] [EXE1] 读取 `contracts/desktop-shell.md` 与 pywebview API（`create_window` / `min_size` / `events.closing`），记录接线点（只读）
- [ ] T036 [EXE1] 添加单实例先失败测试：mutex 已存在时提示并退出（mutex 可注入替身）
- [ ] T037 [EXE3] 添加窗口状态文件测试：读写、非法值回退默认、越界位置回退
- [ ] T038 [EXE1] 添加错误路径测试：端口失败 / 就绪超时 / WebView2 缺失 → 明确提示与非零退出（替身触发）
- [ ] T039 [EXE3] 实现 `packaging/desktop.py`：mutex 单实例 → 随机端口 → Flask 线程（`RUNTIME_MODE="exe"`、`use_reloader=False`）→ `/api/session` 就绪轮询 → pywebview 窗口（1280×800、min_size 1024×700、标题含版本、窗口状态记忆）→ closing 保存 + 取消抓取 → `os._exit(0)` 兜底；失败 MessageBox + `desktop.log`
- [ ] T040 [EXE5] 运行聚焦测试，仅提交 `packaging/desktop.py` 及其测试，提交信息 `feat: add desktop shell`

**Wave 2 检查点**：Task 004-005 完成且聚焦测试通过后，进入 Wave 3。

## Wave 3：打包与集成（串行）

### Task 006：PyInstaller 打包配置与构建脚本

**硬前置**：Task 004、005 完成。

**执行包**：[`tasks006.md`](tasks006.md)

- [ ] T041 [P] [EXE6] 读取 `pyproject.toml` 版本、`.gitignore` 现状、`webui/package.json` 构建方式（只读）
- [ ] T042 [EXE6] 编写 `packaging/career_scout.spec`：entry `packaging/desktop.py`、onefile + windowed、datas（`webui/dist` → `webui/dist`、`data/city_codes.json` → `data/`）、hiddenimports 按构建验证收敛
- [ ] T043 [EXE6] 编写 `packaging/build_exe.ps1`：npm 构建前置校验 → pyinstaller → `.release/CareerScout-v{version}.exe`；任一前置失败非零退出
- [ ] T044 [EXE6] 编写 `packaging/README.md`：构建前置、步骤、产物位置、常见排错、Release 发布流程（本地构建 + 上传 EXE + SHA256）
- [ ] T045 [EXE6] 核补 `.gitignore`：打包中间产物不入库；确认 `build/`、`dist/`、`.release/` 已忽略
- [ ] T046 [EXE6] 运行卫生测试 + `git status` 核实产物零入库，仅提交 `packaging/*`、`.gitignore`，提交信息 `build: add exe packaging scripts`

### Task 007：集成验证与全量回归

**硬前置**：Task 006 完成。

**执行包**：[`tasks007.md`](tasks007.md)

- [ ] T047 [P] [EXE6] 本机执行 `build_exe.ps1` 构建 EXE，验证产物存在、体积合理、版本号正确
- [ ] T048 [EXE1] 启动 EXE：窗口出现、界面加载、无控制台窗口；首启 ≤10s
- [ ] T049 [EXE2] EXE 内环境检查：deps「内置运行时」、webview2 项、浏览器/登录/AI 项与源码版一致
- [ ] T050 [EXE4] EXE 内发起一次 BOSS 抓取任务成功（in-process 路径），结果落库；源码模式可读同一数据
- [ ] T051 [EXE4] 任务中关闭窗口：无残留进程；重启后数据与任务历史完好、恢复机制正常
- [ ] T052 [EXE3] 单实例提示、窗口缩放（1024×700 与 1440×900 断点）、窗口状态记忆验证
- [ ] T053 [EXE5] 全量回归：Python 全量、前端全量、类型检查/构建、卫生测试
- [ ] T054 [EXE5] 修复阻断项后聚焦复查；`git status` 干净、产物零入库；如有修复按 Conventional Commits 小步提交

## 完成定义

1. Wave 3 全部通过后，向主会话回报：构建产物路径、真实 EXE 验收证据（视口/任务/单实例/窗口）、全量回归结果、卫生测试结果、git 差异审计。
2. 主会话核对后向用户交付：EXE 可用于 Release 发布（发布动作由用户执行，流程见 `packaging/README.md`）。