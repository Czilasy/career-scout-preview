# Research: Windows 桌面窗口拉伸与原生式顶栏拖动

**Date**: 2026-09-14

## 研究结论

静态证据支持“单一原生 WndProc + 自绘标题栏”的方向，但不能证明当前 pywebview 6.2.1 / WinForms / WebView2 组合一定满足全部交互。尤其是 WebView2 子控件输入、混合 DPI、最大化拖下还原和“仅顶部最大化但不左右贴靠”必须由精确壳层 Gate 1 实测。结论是“值得实现门禁原型”，不是“已经证明可发布”。

## D1：不依赖 pywebview 的 `resizable=True` 自动恢复无边框拉伸

**Decision**: 在应用自己的 Windows 原生窗口适配器中处理无边框命中。

**Rationale**:

- Microsoft 的自定义窗口框架文档明确说明，移除标准框架后会失去默认移动和拉伸，应用需处理 `WM_NCHITTEST` 并返回边、角和标题栏命中结果：[Custom Window Frame Using DWM](https://learn.microsoft.com/en-us/windows/win32/dwm/customframe)。
- pywebview 维护者在 2025 年明确回答 frameless 窗口不可拉伸，说明 `resizable=True` 不是当前稳定版的解决方案：[pywebview Discussion #1591](https://github.com/r0x0r/pywebview/discussions/1591)。
- 当前本地 pywebview WinForms 实现先设置 `MinimumSize`，再在 frameless 模式将 `FormBorderStyle` 设为 `None`，没有为无边框补命中逻辑。

**Alternatives considered**:

- 裸恢复系统粗边框：历史真机产生系统非客户区、标题栏回归和卡顿，违反 Spec。
- 前端透明边缘手柄：历史真机出现粘边和页面点击问题，违反 Spec。
- 仅设置 `resizable=True`：上游明确不提供 frameless resize，放弃。

## D2：所有 Windows 消息必须由一个适配器和一条 WndProc 链处理

**Decision**: R0 先把现有 `WM_GETMINMAXINFO` hook 迁入 `window_controls.py`；本功能只扩展这一个适配器，绝不叠加第二个 hook。

**Rationale**:

- 当前项目已实测 WndProc 回调必须保持强引用，否则可能形成野指针崩溃。
- 同一 HWND 多次替换 WndProc 会使原过程链和卸载顺序依赖安装时序，增加关闭崩溃及消息丢失风险。
- Microsoft 自定义框架示例把 DWM、非客户区命中和默认过程转发组合在同一主过程内，支持单一所有权模型。

**Alternatives considered**:

- 保留 `desktop.py` 旧 hook，再由 `window_controls.py` 安装拉伸 hook：违反单一所有权并继续向 1019 行超限文件叠加风险。
- 在多个前端组件分别处理边缘：跨页面重复且无法统一 DPI、最小尺寸和工作区。

## D3：边角命中采用原生返回值，角优先于边

**Decision**: 普通状态按四角、四边、标题栏、客户区顺序判定，返回系统定义的命中结果。

**Rationale**:

- `WM_NCHITTEST` 官方定义了 `HTLEFT`、`HTRIGHT`、`HTTOP`、`HTBOTTOM` 和四个角的返回值；系统据此提供对应指针与调整方向：[WM_NCHITTEST](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-nchittest)。
- Microsoft 示例同样使用 3×3 命中矩阵，角落同时表达两轴，能避免边角交界处随机方向。

**Alternatives considered**:

- 手工监听鼠标移动并连续调用 resize：跨 JS/Python 高频调用更易受 DPI、丢失 mouseup 和 WebView 输入影响。
- 可见手柄：用户明确不要，放弃。

## D4：标题栏先验证原生标题命中，不预先承诺系统贴靠行为

**Decision**: Gate 1 的标题栏候选是让 36px 标题栏空白区返回原生标题栏命中，右上按钮区域返回客户区；只有精确壳层实测同时满足拖下还原、顶部最大化、无左右/四角最终贴靠时才采用。

**Rationale**:

- 原生标题栏移动循环最有机会保留连续抓取和最大化拖下还原语义。
- 当前 pywebview 的 `.pywebview-drag-region` 实现是在浏览器 `mousemove` 中反复调用内部移动回调；它没有最大化拖下还原或释放位置判定，不能直接满足 Spec。
- 操作系统贴靠行为会受到窗口样式和用户系统设置影响，静态文档不能证明当前 frameless 样式是否只触发顶部最大化，因此必须实测。

**Alternatives considered**:

- `easy_drag=True`：历史上会把全页面 mousedown 变为窗口拖动并卡住点击，禁止。
- 保留旧 drag-region 与原生标题命中并行：同一按压可能启动两套移动逻辑，禁止。
- 自动准备第二套自定义拖动作为 fallback：用户已确认 fail-stop，不预埋。

## D5：DPI 与工作区以 Windows 物理像素为原生边界

**Decision**: 原生命中和 `WM_GETMINMAXINFO` 使用物理像素；UI 标题栏高度、常规最小值和边缘宽度按目标窗口当前 DPI 转换。窗口状态文件继续保存项目现有逻辑尺寸。

**Rationale**:

- 本地 pywebview 6.2.1 WinForms 后端对初始 Size 和 MinimumSize 明确乘以当前缩放比例，说明宿主原生边界使用物理像素。
- pywebview Issue #1676 报告了 125%/150% 下前端手工边缘方案的坐标混乱，印证转换只能集中在一个边界层：[Issue #1676](https://github.com/r0x0r/pywebview/issues/1676)。
- 工作区应由当前显示器而非主屏固定值决定；`MonitorFromWindow` 可取得与窗口相关的显示器，[GetMonitorInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmonitorinfoa) 提供 monitor 与 work rectangle。

**Alternatives considered**:

- 全部使用 CSS 像素：原生消息坐标与多 DPI 屏幕会错位。
- 固定 8 物理像素：高 DPI 下命中区域过窄。

## D6：最小尺寸与最大化工作区由同一 `WM_GETMINMAXINFO` 处理

**Decision**: 每次消息按当前显示器填充最小跟踪尺寸、最大跟踪尺寸、最大化位置和最大化尺寸；小工作区时逐轴下调最小跟踪尺寸。

**Rationale**:

- `WM_GETMINMAXINFO` 的官方职责就是覆盖窗口最大尺寸、最大位置和最小/最大跟踪尺寸：[WM_GETMINMAXINFO](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-getminmaxinfo)。
- 现有项目已真实验证该消息能修复无边框最大化覆盖任务栏，复用同一消息比在拉伸结束后纠正窗口更稳定。

**Alternatives considered**:

- 只依赖 WinForms `MinimumSize`：无法表达“物理工作区小于常规下限时临时放宽”，也不能解决最大化任务栏边界。
- 拉伸结束后再钳制：用户会先看到越界和跳回，违反连续体验。

## D7：窗口记忆保持 schema 3，取消静态最大尺寸语义

**Decision**: 不迁移 schema；普通矩形字段与 `maximized` 保持分离。1400×800 只作为默认值，1024×700 是常规下限，动态工作区是实际上限。

**Rationale**:

- schema 3 已正确表达普通矩形与最大化状态，不需要新增数据形状。
- 当前 Tracker 已在最大化期间冻结普通矩形，高频 resized/moved 只更新内存，关闭时单次写盘，正好满足拉伸记忆。
- 旧 `MIN=MAX=DEFAULT=1400×800` 是固定窗口历史决定，必须更新；继续使用静态 `MAX_WIDTH/MAX_HEIGHT=1400/800` 会直接拒绝用户放大后的合法记忆。

**Alternatives considered**:

- schema 4：没有新字段或不可兼容语义，属于无收益迁移。
- 每显示器一份矩形：用户已明确选择全局一份，放弃。

## D8：观测只记录安装与门禁摘要，不记录每条窗口消息

**Decision**: 复用桌面日志记录安装成功/失败、HWND、Windows/DPI/工作区摘要和 Gate 失败节点；不得逐条记录 `WM_NCHITTEST`、move 或 resize。

**Rationale**:

- 命中和移动消息频率很高，逐消息写盘会制造卡顿并淹没有效证据。
- Gate 失败需要可定位到安装、命中、工作区或状态保存节点，摘要日志足够。

**Alternatives considered**:

- 完全静默：无法区分未安装、被 WebView 截获与命中逻辑错误。
- 永久详细消息日志：影响性能且不符合日志卫生。

## D9：命中必须由页面声明，宿主只负责原生执行（2026-09-14 真机实测后替代 D4）

**Decision**: 拉伸/移动的**区域判定放在页面**（DOM 事件，CSS 像素），命中后**只发起一次调用**；宿主进程用 Win32（`SetWindowPos` 循环 + 既有 `WM_GETMINMAXINFO` 链）完成拉伸、移动、最大化拖下还原与顶部释放最大化。不使用系统 `SC_MOVE` 拖动循环（避免系统左右贴靠），不新增任何 WndProc hook。

**Rationale（本机 Windows 10 22H2 / pywebview 6.2.1 / WebView2 实测证据）**:

- 无边框窗体（`FormBorderStyle=None`）的客户区被 WebView2 窗口整块覆盖，覆盖矩形等于整个窗口矩形，**包括四边四角**（实测子窗口链：`Chrome_RenderWidgetHostHWND` → `Chrome_WidgetWin_1` → `Chrome_WidgetWin_0` → WebView2 控件窗口 → 窗体）。
- 顶层窗体在整个窗口范围收到 `WM_NCHITTEST` 的次数为 **0**（六个采样位置：左右边、上边、右下角、标题带、中心）。原 D4 的「顶层 WndProc 原生命中」候选因此不可实现：宿主进程根本不在输入链上。
- 改成钩真正接收输入的子窗口同样不可行：`SetWindowLongPtrW` 返回 **0 / `ERROR_ACCESS_DENIED`**——该窗口属于 WebView2 运行时自己的进程，跨进程改窗口过程被系统拒绝（本机实测 `win_thread` 与调用线程不同、返回值为 0）。
- 把子窗口缩小留出边框会露出壳自身底色（浅色主题即一条黑边），等于系统边框重新可见，违反 Spec FR-021。
- 页面能收到鼠标事件（本机实测页面内合成点击计数递增），说明唯一可用的输入链在页面侧；这与 Electron `-webkit-app-region` 的同族做法一致：**区域声明在页面，原生行为在宿主**。

**Alternatives considered**:

- 顶层 WndProc 处理 `WM_NCHITTEST`（原 D4 候选）：实测收不到消息，废弃。
- 钩 WebView2 输入子窗口：跨进程拒绝（`ERROR_ACCESS_DENIED`），且属改别人进程的窗口，废弃。
- 全局低级鼠标钩子（`WH_MOUSE_LL`）：能拿到系统级鼠标事件，但侵入全系统输入链、与其它应用共享副作用，未采用。
- 前端逐帧 resize（v1 方案 B）：慢、粘连，且违反「实时重排」体感要求，废弃。本次改为「按下时一次调用 + 宿主循环执行」，页面不再逐帧回传。

## 开源借鉴结论

- pywebview 官方仓库确认当前稳定 frameless 不自动拉伸，可借鉴其 WinForms DPI 转换和现有窗口事件，不把未发布能力视为依赖。
- Microsoft DWM 自定义框架示例是命中矩阵与默认过程转发的权威基线。
- 社区前端透明手柄方案只用于识别已知风险，不采用其高频 JS/Python resize 结构。
- 本轮不复制第三方代码；使用 Windows 公共消息契约和项目既有实现，因此不引入新的第三方许可证义务。
