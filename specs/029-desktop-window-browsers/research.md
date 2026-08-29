# Phase 0 Research: 029 桌面壳窗口记忆修复批

**Date**: 2026-08-29 | **Status**: 全部已决（grill-me 冻结 + 代码事实核查，无遗留 NEEDS CLARIFICATION）

## D1 窗口事件机制与保存策略

**Decision**: 运行时监听 `resized / moved / maximized / restored` 事件，仅更新**内存中**的"最后一次普通矩形"与"当前是否最大化"两个状态；**文件写入只在关窗时刻（closing）发生**，按内存状态决定写什么（最大化 → 最后普通矩形 + `maximized: true`；普通态 → 当前矩形）。

**Rationale**: 已核实 pywebview 6.2.1 `Window.events` 含 `resized/moved/maximized/restored/minimized/closing/closed` 全部所需事件。拖动/缩放事件高频触发，若每次都写文件会造成无谓磁盘 IO；关窗时刻一次性落盘即可满足需求。WinForms 后端个别事件（尤其 `moved` 连续拖动时序）存在不确定性，但 closing 兜底保证最终一致性——事件丢失的最坏结果只是普通矩形略旧，不会重现"全屏矩形覆盖普通记忆"的 Bug（因为写文件前永远经过"最大化 → 取普通矩形"的分支判断）。

**Alternatives considered**:
- 每事件落盘：IO 放大，且 WinForms 高频触发下无收益 → 否。
- 仅 closing 时读 `window.width/height` 判断是否最大化：现状 Bug 的根源正是 closing 时刻窗口已呈全屏矩形且无法区分最大化态 → 否，必须靠运行时状态。

**兜底规则**：`maximized` 内存态以事件为准；若事件 API 不可用（pywebview < 6，README 已约束 ≥6.0），按现有模式记日志并退化为旧行为（普通矩形以 closing 时窗口值为准，不做最大化分支），不阻塞交付。

## D2 记忆文件 schema 3 设计

**Decision**: `desktop_window.json` schema 3 字段：`schema=3`、`width/height/x/y`（= 普通矩形语义，不再是"上次关窗矩形"）、`maximized`（bool）、`default_width/default_height`（用户自定义普通默认，机制保留）。升级规则：

- schema 2 正常记忆（宽高 ≤ 工作区）→ 继承为 schema 3 普通矩形，`maximized=false`；
- schema 2 污染记忆（宽高超出任一工作区，即被全屏矩形覆盖过的）→ 视同无记忆（按首开处理）；
- schema 1 / 非法 JSON / 字段缺失 → 视同无记忆。

**Rationale**: schema 2 的 `width/height` 语义在 Bug 存在期间已被污染（可能是全屏矩形），无法与真实拖拽值区分；用"是否超出工作区"作判据是因为 Windows 最大化矩形（1936×1056 @ -8,-8）必然大于工作区（1920×1040），而合法拖拽值不可能超工作区——判据可靠且实现为纯函数、可单测。不写迁移脚本：读时按规则解释，首次保存自然落为 schema 3。

**Alternatives considered**: 保留 schema 2 并新增字段——旧污染值会被继续信任，Bug 复现 → 否。

## D3 钳制口径

**Decision**: 读取记忆时按当前工作区钳制：宽高大于任一工作区 → 钳到主工作区（`min(w, work_w)`），位置越出全部工作区 → 主工作区居中；首开普通默认 1545×900 同样过钳制。钳制结果**只作用于返回值，不回写文件**——下次保存自然落盘钳后值。多显示器：位置合法性 = 落在任一工作区即可（现有逻辑保留）。

**Rationale**: 读时钳制纯函数化（workarea_provider 注入），单测友好；不回写避免"读文件产生写副作用"的隐蔽行为。

## D4 浏览器注册表数据结构与位置

**Decision**: 新模块 `scripts/boss/browser_registry.py`，常量 `BROWSER_REGISTRY`：8 条记录 `{key, name, exe_names: [..], path_candidates: [..], data_dir_key}`。探测 = 候选路径存在性检查（Windows：`LOCALAPPDATA/PROGRAMFILES/PROGRAMFILES(X86)` 相对段拼接；macOS/Linux 常见路径尽力支持）。`constants.py` 的 `detect_chromium_browsers()` 改为查注册表（chrome/edge 条目），模块内 re-export 保持旧符号兼容；`browser.py` 的进程枚举 PowerShell 过滤器改为按注册表全部 exe 名生成。

**Rationale**: 注册表是数据 + 探测 + 校验的内聚域；constants.py 已 425 行且职责是"常量/映射"，塞入 200 行注册表域违反落位规则；browser.py 452 行同理。消费方向 webui → scripts/boss 与现状一致。

**注册表 v1 清单**（grill 冻结）：Chrome(chrome.exe)、Edge(msedge.exe)、Brave(brave.exe)、Vivaldi(vivaldi.exe)、Opera(opera.exe)、360 极速(360se.exe/360chrome.exe)、QQ 浏览器(QQBrowser.exe)、夸克(QuarkPC.exe)。夸克 CDP 兼容性最存疑——探测到但启动失败属预期路径，报错明确即可。

## D5 浏览器选择持久化与校验

**Decision**: 【实施修订 2026-08-29】持久化改为注册表域自持 `~/.career-scout/browser_selection.json`（原方案搭 `advanced_settings.json` 通道，但其键白名单位于 `pipeline_exec_settings.py`，不在本批次允许修改的文件清单内，按文件边界绕开）：最终布局（用户已批准该偏差）：`{"mode": "auto|registry|manual", "key": "<注册表key>", "manual_path": "<手动路径>"}`，缺省/损坏 = `{"mode": "auto"}`（按注册表顺序探测，chrome/edge 优先）。手动路径保存时执行 `<exe> --version` 探活（超时 10s）：进程能启动且输出含版本号 → 通过；输出含 `Firefox` 或不可执行 → 明确报错。启动后二次校验：调试端点 `/json/version` 的 `Browser` 字段含 `Chrome/`（Chromium 系均满足）方可继续，否则报"内核不兼容"。

**Rationale**: `--version` 输出可区分内核家族（Firefox 明示自己的名字；Chromium 系输出版本串），不开浏览器即可做保存时校验；CDP 端点校验兜住"伪装路径/魔改内核"，两层各有分工。`settings_api.py` 568 行近预警线 → 端点开新路由域 `webui/browser_registry_api.py`（022 log_api 先例），经 `app.py` 一行注册。

## D6 账号数据目录按浏览器命名空间

**Decision**: 账号 `profile_dir` 存储结构**不变**（显式绝对路径）。启动抓取时按所选浏览器派生**生效数据目录**：chrome/edge → 用存储值原样（现状兼容，存量登录态无损）；其他浏览器 B → `<profile_dir 所在父目录>/chrome-profile-<B 的 data_dir_key>/<profile_dir 目录名>`。效果：切到 B = 各账号空目录 = 重新登录一次；旧目录原样保留，切回 chrome/edge 免登录；B→C 再换命名空间。

**Rationale**: 账号簿不新增字段（grill 决策：全局选择，切换低频）；`profile_dir` 现以 `~/.career-scout/chrome-profile` 为默认根，派生规则把浏览器命名空间放在目录名后缀而非根目录重构，零迁移。实现落在 `pipeline_exec_accounts.py`（账号与 CDP 数据目录域）新纯函数，可单测。

**Alternatives considered**: 账号加 browser 字段——账号簿结构、API 投影、前端展示全要跟着动，复杂度不值（grill 已否）。

## D7 桌面壳启动路径与 quit_app 复用

**Decision**: `create_window` 增加 `maximized=True` 参数支持（已核实 6.2.1 原生支持）由记忆的 `maximized` 驱动；`_on_closing` 改为经 window_state 域的状态收集器落盘；`_quit_and_cleanup`（应用内更新重启）复用同一 closing 逻辑——更新重启后记忆同样正确。窗口状态域的运行时状态收集器以 `packaging/window_state.py` 类承载，deps 注入事件源与时间源，纯逻辑单测。

**Rationale**: 现状 `_on_closing` 与 `_quit_and_cleanup` 已共用清理路径，保持单一代码路径；pywebview `create_window(maximized=True)` 优于启动后调 `window.maximize()`（避免首帧闪烁）。
