# Contract: 窗口控制（B084 自绘标题栏）

**Spec**: [spec.md](../spec.md) | **Date**: 2026-09-01

## 1. 窗口创建参数（desktop.py → pywebview）

- `frameless=True`：无系统标题栏/边框（仅 Windows 生效；macOS 行为差异，本轮不启用无边框）。
- `easy_drag=True`（默认已为 True）：启用 pywebview 内置拖拽支持——前端标题栏空白区元素加 `pywebview-drag-region` 类即成为拖拽区。
- 其余参数（尺寸/位置/`maximized`/`min_size`/`background_color`/`js_api` 等）保持既有。
- `background_color` 保持既有 `#0d1113`（页面加载期底色；加载完成后标题栏背景由前端主题控制）。

## 2. js_api 新增方法（前端 window.pywebview.api）

| 方法 | 参数 | 返回 | 行为 |
|---|---|---|---|
| `window_minimize()` | 无 | `{ok: bool, error?: string}` | 调用 `window.minimize()` |
| `window_toggle_maximize()` | 无 | `{ok: bool, error?: string}` | 当前非最大化→`maximize()`；最大化→`restore()` |
| `window_close()` | 无 | `{ok: bool, error?: string}` | 走既有优雅退出（保存窗口状态→取消运行中任务→退出），等价关闭按钮 |

错误语义：窗口句柄不可用返回 `{ok: false, error: "no_window"}`；异常返回 `{ok: false, error: <msg>}`。

## 3. 最大化避让任务栏（window_controls.py）

- 目的：无边框窗口最大化时覆盖工作区但不覆盖任务栏（spec FR-011）。
- 实现期真实验证 pywebview frameless 最大化行为：
  - 若天然避让任务栏 → 无需额外处理；
  - 若覆盖任务栏 → 在 `window_controls.py` 用 Win32（`WM_GETMINMAXINFO` 按工作区钳制最大化尺寸）修复。
- `window_controls.py` 为独立域，`desktop.py` 单向依赖；其内部失败不阻断启动（降级为依赖 pywebview 默认行为）。

## 4. 前端标题栏（WindowTitleBar.vue）

- 渲染条件：仅桌面版（检测 `window.pywebview` 存在）。
- 布局：左侧 `Career Scout` 文字；右侧最小化 / 最大化/还原 / 关闭三按钮。
- 拖拽：标题栏空白区元素加 `pywebview-drag-region` 类（pywebview 6.x easy_drag 机制）；按钮不在该类内，点击正常。
- 双击标题栏：显式绑定 `dblclick` → 调 `js_api.window_toggle_maximize()`（easy_drag 只处理拖拽，不处理双击）。
- 主题：背景跟随主题（浅色=白、暗色=暗、特殊主题=透明）；按钮在特殊主题下半透明磨砂、X 悬停红底、其余悬停线条变深。
