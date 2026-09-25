# Research: 关闭确认自绘化与运行中关闭收尾

本文件记录 `045 v2` 的技术决策与查证证据。所有结论均来自本仓代码或已安装依赖的源码，不依赖外部记忆。

## 决策

### D1. 关闭确认改两段式：`closing` 取消 → 前端弹框 → 回调后真关

- 证据（pywebview 6.2.1 源码，本仓 `.venv/Lib/site-packages/webview/`）：
  - `window.py:437` `evaluate_js(script, callback=None)`：脚本返回 Promise 时，走 `value.then(... pywebview._asyncCallback(...))` 分支并**立即返回字符串 `"true"`**（`window.py:461-471`），不等待 Promise。
  - `js/api.js:155` `_asyncCallback` → `window.pywebview._jsApiCallback('pywebviewAsyncCallback', result, id)`。
  - `util.py:308-318` 收到 `pywebviewAsyncCallback` 后调 `window._callbacks[value_id](value)`，随后 `del window._callbacks[value_id]`。
  - `platforms/winforms.py:400-403` `on_closing`：`should_cancel = self.closing.set()`，为 True 时置 `args.Cancel = True`。
- 结论：壳在 `closing` 里调 `evaluate_js` 触发前端弹框并立即返回 False 取消本次关闭；前端弹框返回 Promise，用户点击后 resolve，结果经 `_asyncCallback` 回到壳侧 callback；壳侧 callback 里执行收尾并关窗。
- 影响：解决了「壳侧同步 vs 前端异步」的死锁——`closing` handler 返回后 UI 线程不再阻塞，WebView 能正常渲染与响应点击。

### D2. 收尾后关窗必须带守卫，否则会二次弹框

- 证据：`platforms/winforms.py:1039-1047` `destroy_window()` 内部是 `i.Invoke(Func[Type](_close))` + `i.Close()`；WinForms 的 `Form.Close()` 会再次触发 `FormClosing`，即**再次进入 `closing` 事件**。
- 结论：壳侧必须有一个「已决定关闭」的守卫标志。收尾完成准备关窗时置位，第二次 `closing` 命中守卫直接放行，避免弹框死循环。
- 影响：守卫必须覆盖所有关闭入口（原生 X、自绘 X、`quit_app`），不能只在某一条路径上置位。

### D3. 回调在 UI 线程，收尾重活必须转后台线程

- 证据：`_asyncCallback` 由 WebView 的 JS bridge 进入 `util.py` 的分发，WinForms 后端下该分发在 UI 线程；`util.py:318` 显示 callback 为**一次性**（用完即删）。
- 结论：callback 内不得直接做耗时 HTTP（`pause` + `finish` 可能数秒），必须丢到后台线程执行，避免冻结界面；且 callback 是一次性的，重试需要重新发起 `evaluate_js`。

### D4. 运行中关闭的收尾顺序：`pause(mode=immediate)` → `finish`

- 证据：
  - `webui/task_continue_api.py:526` 暂停接口缺省 `mode` 为 `graceful`（等这批抓完），**必须显式传 `immediate`** 才是立即停止。
  - `webui/src/composables/useScreenRoundFlow.ts:325` 已用 `{mode}` 调该接口；`useDiscoveryExecution.ts:716` 抓取侧调用不带 mode（即 graceful）。
  - `webui/task_continue_api.py:705-707` `finish` 允许的状态含 `queued/running/paused/failed/interrupted`，running 可直接 finish。
  - `useScreenRoundFlow.ts:313-315` 注释：immediate 覆盖一个岗位收尾，可能数十秒，轮询上限 240 × 300ms。
- 结论：先 `pause(mode="immediate")` 让 worker 停止，再 `finish` 落轮，与前端既有「暂停 → 结束并保存」顺序一致；不给 graceful 选项（用户已明确不要等待）。
- 影响：立即停止仍可能有数十秒收尾，故确认框必须有等待反馈（FR-012）。

### D5. 岗位数判定统一为「源抓取 run 已落盘岗位数」

- 证据：
  - `webui/running_task_api.py:122-166` 内存 running/queued 分支**不返回** `job_count` / `scraped_count`，而 `packaging/desktop_close.py:17-25` 只读这两个字段 → 运行中一律判为 0 → 静默关闭（这正是 FR-015 要修的漏判）。
  - 该接口的 DB 兜底分支（paused / interrupted 等）用 `ctx.store.count_scrape_run_jobs(paused_source_task_id)` 给出计数（第 218、294-295 行）。
  - `webui/store_scrape_runs.py:339` `count_scrape_run_jobs(run_id)` 统计 `scrape_run_jobs` 行数，是恢复计数的权威来源。
  - running 分支已提供 `scrape_task_id`（第 144-147 行：ai_screen 取 `source_task_id`，否则取自身相关值）。
- 结论：在 running/queued 分支补齐 `job_count` 与 `scraped_count`，取值来源与 DB 兜底分支一致（ai_screen 用源抓取 run，其余用自身 run）。所有状态共用一套计数口径。
- 影响：`desktop_close` 无需改判定逻辑，只需接口把字段补全。

### D6. 前端不可用时的回退：直接放行关闭

- 证据：`closing` 若不在有限时间内返回，WinForms 关闭流程会卡住；而 v1 `cancel_running_tasks` 会让轮落 `paused`，数据可恢复。
- 结论：`evaluate_js` 抛错或前端无响应时，不弹框、直接放行关闭，并写桌面日志留痕；不选择「保持现场」，避免用户关不掉窗口。
- 影响：最坏情况退化为 v1 的行为（轮落 paused，下次可接回），不丢数据。

### D7. 前端自绘框必须复用既有对话框基座

- 证据：
  - `webui/src/components/BaseDialog.vue`（110 行）样式全部走 CSS 变量，并有 `:root[data-theme="dark"]` 与彩蛋主题覆盖。
  - `webui/src/components/PauseBatchChoiceDialog.vue`（148 行）已基于 BaseDialog 实现「批中二选一」，含 `kind: pause | finish` 两套文案、右上 ✕ / Esc 取消、迷你档尺寸。
  - 主题维度中 `platform`（boss/智联）只在前端运行时存在、不持久化（`webui/src/composables/useTheme.ts`），壳侧无法还原。
- 结论：新增 `kind="close"` 复用该组件，只渲染一颗动作键（不渲染等待类选项）；主题跟随由 BaseDialog 的 CSS 变量天然提供，零适配代码。
- 影响：不需要也不可能由壳侧绘制主题化弹窗。

### D8. 文件落位受红线约束

- 证据（实测行数）：`packaging/desktop.py` **935 行**（超 Python 800 红线，036 索引已明示）；`webui/src/views/DiscoveryView.vue` **1202 行**（超 Vue 1200 红线）；`webui/src/composables/useDiscoveryExecution.ts` **1332 行**；`webui/running_task_api.py` **601 行**（过 600 预警线）。
- 结论：
  - 壳侧新逻辑一律落入 `packaging/desktop_close.py`（70 行，045 关闭生命周期域），`desktop.py` 只做最小接线。
  - 前端关闭确认编排新建独立 composable，不进 `DiscoveryView.vue` 与 `useDiscoveryExecution.ts`。
  - `running_task_api.py` 只做补字段的最小改动，不追加业务逻辑。
- 影响：符合宪法原则 II（尺寸边界）与 VI（落位规则）；新文件需登记进 constitution 模块地图。

## Alternatives considered

- **壳侧自绘并读主题文件跟随主题**：`platform` 维度不持久化，壳侧拿不到 boss/智联，不成立；放弃。
- **保留原生弹窗只改按钮文案**：违反 FR-002（禁止原生弹窗）且 macOS 走的是系统 AppleScript 框，两套实现；放弃。
- **在 `closing` 里同步阻塞等待前端结果**：UI 线程被占住，WebView 无法渲染与响应点击，必然死锁；放弃。
- **关窗时给「等这批抓完」选项**：用户明确否决（点关闭说明着急，等待拖住窗口）；放弃。
- **运行中直接 finish 不暂停**：用户选择保留暂停（保证落轮时 worker 已停）；放弃。
- **桌面层直连 SQLite 查岗位数**：v1 已否决（跨层 + 并发写冲突）；放弃。
