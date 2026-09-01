# Research: 桌面壳自绘标题栏 + 顶栏胶囊灵动岛

**Spec**: [spec.md](./spec.md) | **Date**: 2026-09-01

## R1. 无边框窗口与自绘标题栏的实现路径

- **Decision**: 桌面壳 `create_window(..., frameless=True, easy_drag=True)` 开无边框窗口；前端页面顶部渲染 `WindowTitleBar.vue`（仅桌面版显示），标题栏空白区元素加 `pywebview-drag-region` 类实现拖拽（pywebview 6.x easy_drag 机制），按钮不加该类保持可点击；双击标题栏由前端显式绑定 `dblclick` → `js_api.window_toggle_maximize()`；三按钮通过 `js_api` 调用 `window.minimize()/maximize()/restore()` 与关闭流程。
- **Rationale**: pywebview 6.x 无边框拖拽的官方机制是给元素加 `pywebview-drag-region` 类（`easy_drag` 注入脚本监听该类 mousedown 后移动窗口），并非浏览器 `-webkit-app-region`；窗口控制用 pywebview Window 原生方法，最小侵入，无需手写 Win32 拖拽消息循环。
- **Alternatives considered**:
  - 手写 Win32 `WM_NCHITTEST`/`WM_NCLBUTTONDOWN` 拖拽：能精确控制但复杂度高、与 pywebview 事件循环耦合，且 `easy_drag` 已覆盖基本拖拽需求。不选。
  - 用 `transparent=True` 做真透明窗口：仅特殊主题需要透明观感，但透明窗口在 WebView2 上性能与边界处理复杂。改为"前端背景透明"（标题栏不画底色露出页面主题背景），满足需求且实现稳定。不选真窗口透明。

## R2. 无边框窗口最大化与任务栏

- **Decision**: 无边框窗口最大化行为（是否覆盖任务栏）在实现期做一次真实验证：优先验证 pywebview frameless + maximized 是否天然避开任务栏；若最大化覆盖任务栏，则在 `window_controls.py` 用 Win32（`WM_GETMINMAXINFO` 或 `ShowWindow(SW_MAXIMIZE)` 结合工作区钳制）保证最大化不盖任务栏。
- **Rationale**: frameless 最大化在各版本/后端行为有差异，属"必须真机确认"的事实；`window_controls.py` 作为窗口控制域预留该适配位，避免污染 `desktop.py`。
- **Alternatives considered**: 直接依赖 pywebview 默认行为不做处理——若其覆盖任务栏则验收不通过（spec FR-011），不满足需求。不选。

### T001 真机验证结论（2026-09-01 实施时更新）

- **结论**: 待 Windows 真机 EXE 验证。`window_controls.py` 已预留适配位
  `MAXIMIZE_WORKAREA_CLAMP`（默认 `False`）+ `_clamp_to_workarea()` 占位实现；
  若真机发现 frameless 最大化覆盖任务栏，置该开关为 `True` 并落地钳制逻辑即可，
  不阻塞无边框标题栏其余交付。
- **验证方法**: 打包 EXE 后点击最大化按钮/双击标题栏，观察窗口是否覆盖任务栏；
  若覆盖，按契约 §3 实现 Win32 工作区钳制。

## R3. 窗口记忆（B082）在无边框下的回归风险

- **Decision**: 无边框不改变窗口状态域语义：`packaging/window_state.py` 的 `WindowStateTracker` 仍监听 `resized/moved/maximized/restored` 维护普通矩形与最大化标记，closing 落盘流程不变；`desktop.py` 仅调整 `create_window` 参数（`frameless=True`），不改 `window_state.py`。
- **Rationale**: 窗口记忆与窗口装饰正交——事件来源不变（窗口尺寸/位置/最大化态），无边框只是去掉系统装饰，Tracker 照常工作。
- **Alternatives considered**: 无。既有机制经 029/082 打磨，直接复用是低风险路径。

## R4. 胶囊（灵动岛）数据源

- **Decision**: 胶囊不新增轮询、不自行抓取：扩展现有 `round-status` 上抛数据，由 `useDiscoveryTasks.ts`（抓取/筛选进度）、`useDiscoveryResults.ts`（匹配/待确认数）、`useDiscoveryState.ts`（运行态/胶囊状态派生）汇总成一份胶囊状态对象，经 App 传给 `DynamicIsland.vue`。
- **Rationale**: 遵循宪法引用方向（view → composable → api client），任务进度本就由既有轮询驱动，胶囊只是"换一种消费方式"；避免组件层重复数据获取与状态不同步。
- **Alternatives considered**: 胶囊内自行轮询 `/api/...`：新增请求负担、与既有状态双源易漂移。不选。

## R5. 特殊主题（万花筒）下的视觉

- **Decision**: 标题栏与胶囊在特殊主题下不画实色背景，露出主题自身背景；按钮/胶囊用半透明白磨砂底保证可见。CSS 基于主题令牌实现，不新增第三方库。
- **Rationale**: 主题系统（032）以令牌驱动，前端组件读主题变量即自动适配明暗/特殊主题；半透明磨砂用 backdrop-filter + 半透明色即可。
- **Alternatives considered**: 为主题各写一套专属样式——维护成本高且与 032 主题机制脱节。不选。

## R6. 提醒按钮通用化

- **Decision**: 提醒按钮从"仅投递提醒数量"扩展为"各类提醒数量"（投递、待确认、出错、跑完等），数量由提醒状态层汇总；点击仍打开提醒抽屉。
- **Rationale**: 用户已确认本轮一并实现（spec FR-021）；提醒类型数据以既有投递提醒为基础扩展，抽屉复用。
- **Alternatives considered**: 仅在样式上聚合不打通数据——无法显示真实数量，不满足需求。不选。

## R7. 动画实现

- **Decision**: 基于 CSS 过渡/关键帧实现数字跳动、展开收缩、呼吸点、待确认标亮；用 `prefers-reduced-motion` 媒体查询降级为静态切换。
- **Rationale**: 纯 CSS 无新依赖，浏览器自动调度，遵循系统"减少动态"是既有前端实践。
- **Alternatives considered**: 引入动画库（如 motion/GSAP）——本项目 UI 复杂度无需引入依赖，且违反"不引第三方库"的假设（spec A5）。不选。
