# Implementation Plan: Windows 桌面窗口拉伸与原生式顶栏拖动

**Branch**: `codex/feature/desktop-window-resize` | **Date**: 2026-09-14 | **Spec**: [spec.md](spec.md)

**Input**: 冻结需求来自 `specs/036-titlebar-dynamic-island/v2/spec.md`。Spec 是本计划及后续 Tasks 的最高裁决基线；发生冲突时修改下游工件，不反向削减 Spec。

## Summary

在 Windows 自绘无边框桌面壳上补齐四边四角八方向拉伸，并让最大化窗口能够拖下还原、普通窗口能够拖顶释放最大化。继续使用既有 schema 3 窗口记忆，但把普通尺寸从固定 1400×800 改为默认 1400×800、常规下限 1024×700、上限随当前显示器工作区变化；最大化矩形永远不写入普通矩形。

实现必须先通过精确壳层 Gate 1。**2026-09-14 真机实测（Windows 10 22H2 / pywebview 6.2.1 / WebView2）已否决「宿主自己解析原生命中」候选**：无边框窗体的客户区被 WebView2 窗口整块覆盖（覆盖矩形等于窗口矩形，含四边四角），顶层窗体收到的 `WM_NCHITTEST` 次数为 0；改钩真正接收输入的子窗口又被系统拒绝（跨进程 `ERROR_ACCESS_DENIED`）。据此本节与 Research D9 已改写候选路径：**区域判定放在页面（DOM 事件 + CSS 像素），命中后只发起一次调用；宿主用 Win32（`SetWindowPos` 循环 + 既有 `WM_GETMINMAXINFO` 链）完成拉伸、移动、拖下还原与顶部释放最大化。** 不新增任何 WndProc hook，不使用系统 `SC_MOVE` 循环（从机制上排除左右/四角贴靠），不引入透明边缘层、裸系统边框或前端逐帧 resize。

## Technical Context

**Language/Version**: Python ≥3.10；TypeScript/Vue 3（现有 WebUI）

**Primary Dependencies**: pywebview 6.2.1 实际锁定环境（项目声明 `>=6.0.0,<7.0.0`）、WinForms、WebView2、Windows User32/DWM；Vue 3 与 Vitest

**Storage**: `~/.career-scout/desktop_window.json`，沿用 schema 3；不新增数据库或迁移

**Testing**: Python `unittest`、前端 Vitest、前端构建、Windows 10/11 真实桌面包手动 Gate/E2E

**Target Platform**: Windows 10 与 Windows 11 桌面版；macOS 和浏览器模式必须零行为变化

**Project Type**: Python 桌面壳 + 本地 Web 应用

**Performance Goals**: 拉伸和移动期间窗口连续跟随指针，页面随每次尺寸变化实时重排；不得依赖高频磁盘写入或逐消息日志

**Constraints**: 现有无边框视觉不变；常规下限 1024×700；默认 1400×800；最大普通尺寸为当前显示器工作区；小工作区允许临时低于常规下限；只有顶部释放最大化；不增加左右/四角贴靠；失败停止

**Scale/Scope**: 单个顶层桌面窗口、一个全局最近普通矩形、四边四角与 36px 标题栏；不扩展多窗口管理

## Constitution Check

*GATE：Phase 0 前检查，并在 Phase 1 后复查。*

| 宪法要求 | 规划结论 |
|---|---|
| 用户已确认需求高于旧 Spec/实现 | PASS：`036 v2` 明确取代根目录 v1 的“固定大小”，Plan 不得反改 |
| Python 文件原则上 ≤800 行 | CONDITIONAL PASS：`packaging/desktop.py` 当前 1019 行，列为禁止修改；先由独立拆分 Spec 迁移现有原生适配逻辑 |
| 拆分与功能必须分开 Spec | PASS：本 Plan 不包含 `desktop.py` 拆分，设为外部阻断依赖 |
| 树干/树枝方向 | PASS：窗口控制是桌面壳通用能力，不含招聘平台名称或平台特例 |
| 后台任务切页/窗口操作不丢状态 | PASS：窗口适配器不得拥有或改变任务状态 |
| 真实 E2E 不得被低层测试冒充 | PASS：Gate 1 与 Windows 10/11 最终 E2E 分列 |
| 无敏感数据与外部写入 | PASS：只使用本机窗口状态文件，不新增凭据或外部服务 |

**阻断依赖 R0**：实现 036 v2 前，必须由独立工程还债 Spec 把 `packaging/desktop.py` 中既有 HWND 查找、WndProc 最大化工作区钳制和无边框尺寸修正迁入 `packaging/window_controls.py`，并保持行为与测试不变。R0 未完成时，036 v2 不得修改 `desktop.py`，也不得开始产品实现。

## File Boundaries

本边界已于 2026-09-14 获用户确认；随后只补入一个会被新最小尺寸直接影响的旧契约测试 `tests/test_desktop_shell.py`，不扩大产品代码范围。

**2026-09-14 用户批准修订（对应 Research D9）**：新增 `packaging/desktop.py` 为允许修改文件，用于 js_api 接线与适配器安装调用；该文件 R0 后 787 行，本功能只在其中替换既有安装调用并新增两个 js_api 方法（+约 25 行，完成后必须仍低于 800 行红线）。

- **Allowed product files**:
  - `packaging/window_controls.py`：在 R0 完成后的单一 Windows 原生适配器中增加 DPI 换算、逐轴最小/最大跟踪尺寸、当前显示器工作区与幂等安装生命周期；预计功能增量 120–220 行，文件完成后预计 700 行以内。
  - `packaging/window_interaction.py`：**新增模块**，承载「页面声明 + 宿主执行」的交互引擎（几何数学 + 指针/窗口矩形 Win32 原语 + 移动/拉伸循环 + `begin_move`/`begin_resize` 入口 + js_api 基类），只单向依赖 `window_controls` 与 `window_metrics`；预计 400–650 行。
  - `packaging/window_metrics.py`：**新增模块**（2026-09-14 宪法原则 VI「75% 预警线分流」整改）：显示器工作区解析、DPI 缩放、CSS/物理像素换算、逐轴最小跟踪尺寸，以及纯几何与判定（拉伸/移动矩形、抓取还原、拖动阈值、顶部释放）；`window_controls` 与 `window_interaction` 共用，实际 302 行。
  - `packaging/desktop.py`：仅替换适配器安装调用并新增两个 js_api 接线方法（`window_begin_move` / `window_begin_resize`），不调整其它职责。
  - `packaging/window_state.py`：把固定尺寸校验改为默认值、常规下限与动态工作区上限，保持 schema 3；预计增量不超过 80 行，完成后预计 520 行以内。
  - `webui/src/components/WindowTitleBar.vue`：移除旧 `pywebview-drag-region` 路径，新增边缘/标题带区域上报与指针反馈，保持原视觉、按钮与双击契约；预计净变化不超过 120 行，完成后远低于 Vue 红线。
  - `webui/src/api.ts`：**仅新增两行类型声明**（`window_begin_move` / `window_begin_resize`，桌面壳 js_api 类型面），不加入任何运行时代码或逻辑。
  - `webui/src/styles.css`：仅在 1024×700 全局布局审计发现问题时做最小响应式修正；如问题位于其他 Vue 文件，必须先回到 Plan 增补精确路径，不得自行扩范围。
- **Allowed tests**:
  - `tests/test_window_controls.py`
  - `tests/test_desktop_window_state.py`
  - `tests/test_desktop_shell.py`（仅更新由 1400×800 固定下限变为 1024×700 的创建参数契约）
  - `webui/src/components/__tests__/WindowTitleBar.spec.ts`
- **Allowed feature documents**: `specs/036-titlebar-dynamic-island/INDEX.md` 与 `specs/036-titlebar-dynamic-island/v2/` 内的 Plan、Research、Data Model、Contracts、Quickstart、Tasks、Changes 和检查表。
- **Forbidden files**:
  - 除上述允许清单外的其它产品代码，尤其是灵动岛、提醒、主题视觉、macOS 桌面壳、数据库、任务运行器和各招聘平台抓取模块
  - 除 `WindowTitleBar.vue` 外的 Vue 页面或组件，除非先修订本 Plan 并再次确认路径
  - 根目录 036 v1 平铺历史文档
- **New product files**: `packaging/window_interaction.py`（交互引擎）。它是 `window_controls` 的单一下游，不引入第二个 HWND/hook 所有权：句柄定位、适配器与诊断出口仍只有 `window_controls` 一处。
- **Reference direction**: `WindowTitleBar DOM 区域判定 → js_api（一次调用） → window_interaction 交互引擎 → window_controls 原语`；`desktop 壳层接线 → window_controls 适配器`；`desktop 窗口事件 → window_state 普通矩形`。`window_controls` 不导入 `window_interaction`，两者都不反向导入 `desktop`，`window_state` 不依赖 UI。
- **Line gate**: 所有允许修改的 Python/Vue 文件完成后必须重新计数并低于 800/1200 红线；`packaging/desktop.py` 本功能内必须仍低于 800 行。

## Architecture and Interaction Design

### 1. 单一原生窗口适配器 + 宿主交互引擎

- **hook 只有一条且仍是 R0 那一条**：顶层 HWND 上的 `WM_GETMINMAXINFO` 链（本功能内扩展为「当前显示器工作区 + 逐轴最小/最大跟踪尺寸」），适配器实例持有 HWND、原过程、强引用回调与安装状态。**不新增第二个 hook**（拉伸/移动不依赖任何 WndProc 消息，见 D9 实测）。
- **交互引擎独立成模块**（2026-09-14 线门禁触发后的修订）：几何数学（逐轴钳制、拉伸/移动矩形、抓取比例还原、顶边判定）与宿主循环放在 `packaging/window_interaction.py`，由页面一次调用启动；循环读取指针位置、用 `SetWindowPos` 写回窗口矩形，左键释放（`GetAsyncKeyState`）或超时即退出；循环只做窗口矩形计算与写入，不记录逐帧日志。`packaging/window_controls.py` 保留适配器（hook/工作区/DPI 换算/尺柄定位/尺寸修正）与窗口状态原语。拆分原因：单文件实现本功能会到 1100 行，越过项目 800 行红线。
- 安装必须幂等；重复安装返回同一状态，找不到 HWND、平台不支持或 hook 失败时返回明确失败状态（`installed` / `reason` / `hwnd_found`），Gate 1 不得把失败静默当成功。

### 2. 命中优先级（页面判定，宿主执行）

普通状态按以下优先级由页面判定：四角 → 四边 → 标题栏空白区 → 客户区。顶边最外层先用于拉伸，标题栏内部用于移动；三个窗口按钮矩形始终按客户区处理，保证点击进入 WebView。

最大化状态不提供边缘拉伸（页面不上报边缘）；标题栏空白区仍上报移动，以支持拖下还原。页面按 CSS 像素判定并上报，宿主在原生边界用当前 DPI 一次性换算为物理像素，避免 125%/150% 缩放错位。

### 3. 移动、还原与顶部最大化（宿主引擎）

- 标题栏按下 → 一次调用 → 宿主引擎接管：普通态按指针位移移动窗口；最大化态先还原到最近普通矩形，并按按下时的横向抓取比例重新定位，再继续跟随指针（同一次按压内完成）。
- 释放时指针位于当前显示器工作区顶部（小阈值内）才最大化；左侧、右侧、角落释放一律保持普通态，不产生半屏或四分屏（宿主不调用系统移动循环，故无系统贴靠结果）。
- 最大化通过既有 `window_controls.maximize` 链路（受 `WM_GETMINMAXINFO` 钳制）；不增加自定义贴靠预览，不重新启用裸系统粗边框。

### 4. 工作区与尺寸边界

- `WM_GETMINMAXINFO` 每次按窗口所在显示器读取工作区（`MonitorFromWindow` + `GetMonitorInfoW`），不再固定使用工作区列表第一项；原生读取失败时回退既有注入的 `workarea_provider`（保持 R0 注入面无反向依赖）。
- 常规最小跟踪尺寸为 1024×700 的当前 DPI 物理像素；若工作区更小，则对应轴的最小跟踪值降到工作区尺寸。
- 最大跟踪尺寸和最大化矩形使用该显示器工作区，支持任务栏处于四边任一位置。
- 交互引擎在写回窗口矩形前做同一套逐轴钳制，保证「不越过工作区、不缩过常规下限、小工作区例外」在拉伸过程中即时成立。
- 状态文件保存逻辑继续使用逻辑尺寸；原生消息边界负责物理像素，二者只在适配边界转换一次。

### 5. 普通窗口记忆

- schema 仍为 3，不新增字段；`width/height/x/y` 始终表示最近普通矩形，`maximized` 单独表示关闭状态。
- 移动/拉伸高频事件只更新内存 Tracker；关闭时单次写盘。
- 去掉 1400×800 同时充当最小与最大的旧校验。读取时先做类型/正值校验，再按当前工作区约束；保存的最大化矩形仍由 Tracker 冻结规则排除。

## Verification Gate

### Gate 1：精确壳层可行性（阻断全面集成）

必须使用当前项目锁定的 Python 环境、pywebview 6.2.1、WinForms、WebView2、`frameless=True`、现有页面和现有标题栏，不得用空白 WinForms 窗口代替最终结论。逐项验证：

1. 四边四角 8/8：在正式页面边缘按下并拖动，窗口仅按该方向改变矩形且连续跟随；
2. 100%、125%、150% DPI 下页面边缘判定与指针位置一致（含物理/逻辑换算无错位）；
3. 标题栏普通移动连续；最大化拖下还原到普通矩形且抓取位置横向比例连续；
4. 顶部释放最大化且不覆盖任务栏（最大化矩形=当前显示器工作区）；
5. 左、右、角落释放不形成半屏/四分屏最终状态；
6. 三个标题栏按钮、标题栏双击与靠近四边的页面控件仍可点击（按钮区不被拉伸占用）；
7. 拖动/拉伸期间页面实时重排；拖动结束后窗口记忆与最大化图标状态一致；
8. 重复安装幂等、连续启停 10 次无崩溃、无野回调、无输入残留（不出现鼠标被永久捕获）。

**已否决的原候选（不重复尝试）**：顶层 WndProc 处理 `WM_NCHITTEST` 与钩 WebView2 输入子窗口，两条均已真机实测失败并记录在 Research D9。

任一项失败：记录 Windows 版本、DPI、操作、实际结果和日志节点，036 v2 状态改为阻断并停止。不得继续集成窗口状态或页面修复，不得自动尝试透明前端手柄、`easy_drag=True`、裸 `WS_THICKFRAME`、前端逐帧 resize 等历史失败路径。

### 最终功能门禁

- **执行节奏**：R0、Gate 1、US1–US4 和返修属于同一交付链；过程中只跑各阶段列出的聚焦测试。以下后端全量只在全部实现与聚焦回归收敛后运行一次，不得在 R0 或每个用户故事后重复运行。
- 聚焦：`uv run python -m unittest tests.test_window_controls tests.test_desktop_window_state tests.test_desktop_shell`
- 后端全量：`uv run python -m unittest discover -s tests`
- 前端：在 `webui/` 执行 `npm test`
- 构建：在 `webui/` 执行 `npm run build`
- 卫生：`uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`
- 真实 E2E：按 [quickstart.md](quickstart.md) 在 Windows 10、Windows 11 执行完整矩阵；自动测试不得冒充真机通过。
- **失败处理**：后端全量失败时保留完整输出和失败名称，只重跑失败用例、直接受影响测试与必要相邻回归；禁止为了捞取失败名称或在没有相关改动时再次跑全量。实际修复并重新收敛后才允许下一次最终全量确认。

## Project Structure

### Documentation

```text
specs/036-titlebar-dynamic-island/
├── INDEX.md
└── v2/
    ├── spec.md
    ├── plan.md
    ├── research.md
    ├── data-model.md
    ├── quickstart.md
    ├── changes.md
    ├── contracts/
    │   └── desktop-window-interaction.md
    ├── checklists/
    │   └── requirements.md
    └── tasks.md
```

### Source Code

```text
packaging/
├── desktop.py                 # R0 后只接线，本功能禁止修改
├── window_controls.py         # 单一 Windows 原生窗口适配器
└── window_state.py            # schema 3 普通矩形与启动恢复

webui/src/
├── components/
│   ├── WindowTitleBar.vue
│   └── __tests__/WindowTitleBar.spec.ts
└── styles.css                 # 仅条件性全局小窗口修正

tests/
├── test_window_controls.py
├── test_desktop_window_state.py
└── test_desktop_shell.py
```

**Structure Decision**: 深化现有窗口控制和窗口状态两个模块，不创建第二个 HWND 适配层。前端标题栏只保留展示与按钮，原生窗口交互集中到 `window_controls.py`。

## Post-Design Constitution Check

- PASS：无未解决的需求澄清；Spec 的所有 24 条功能要求均有设计落点或验证门禁。
- PASS：功能文件均在规模红线内；超限 `desktop.py` 被独立 R0 隔离。
- PASS：没有新增外部服务、数据库、权限或平台分支业务。
- PASS：真实 E2E、失败停止和非部分交付均进入 Tasks 的阻断链。
- PASS：没有因技术便利引入 Spec 禁止的左右贴靠、透明边缘层或视觉改版。

## Complexity Tracking

无宪法例外。R0 是必须先完成的独立拆分工作，不作为本功能的例外或隐藏任务。
