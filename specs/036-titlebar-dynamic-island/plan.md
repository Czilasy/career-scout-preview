# Implementation Plan: 桌面壳自绘标题栏 + 顶栏胶囊灵动岛

**Branch**: `036-titlebar-dynamic-island` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/036-titlebar-dynamic-island/spec.md`

## Summary

两块用户可感知改动：
1. **B084 桌面壳自绘标题栏（无边框窗口，仅 Windows）**：桌面版窗口去掉系统白色标题栏，改为应用自绘——页面顶部一条标题栏（左侧 `Career Scout` 文字、右侧最小化/最大化/关闭三按钮），支持拖拽移动、双击最大化/还原，颜色跟随主题（浅色白/暗色暗/特殊主题透明，特殊主题下按钮半透明磨砂、X 悬停红底）。窗口记忆（位置/大小/最大化）复用既有机制，不回归。
2. **B088 顶栏胶囊灵动岛**：顶栏中间胶囊从"纯展示一句话"升级为常驻活组件——空闲低调显示平台名（点回主页）、运行中显示实时进度数字+呼吸点（点回任务）、跑完显示结果数字（待确认>0 标亮，点去结果页）、需处理时变提醒条（暂停橙/出错红，点去现场）；多态按"需处理>运行中>有结果>空闲"取一件；动画必做且尊重系统"减少动态"；提醒按钮顺带通用化（显示各类提醒数量）。

技术路径：桌面壳 `packaging/desktop.py` 开无边框窗口 + 新增 `window_controls.py` 提供窗口控制（最小化/最大化/还原，无边框最大化避让任务栏）；前端 `App.vue` 挂载自绘标题栏（仅桌面版显示）并替换胶囊为 `DynamicIsland.vue` 组件；胶囊数据由既有 composables（`useDiscoveryTasks`/`useDiscoveryResults`/`useDiscoveryState`）汇总上抛，组件只消费不抓取。

## Technical Context

**Language/Version**: Python 3.11+（桌面壳）/ TypeScript + Vue 3（前端）

**Primary Dependencies**: pywebview 6.x（Windows 后端 WinForms + WebView2，`create_window` 支持 `frameless`、`easy_drag`；Window 提供 `minimize()`/`maximize()`/`restore()`/`toggle_fullscreen()`/`destroy()`）/ Vue 3 组合式 API / @lucide/vue 图标

**Storage**: 无新增持久化；窗口状态沿用既有 `~/.career-scout/desktop_window.json`（schema 3，029 契约）

**Testing**: 后端 pytest（`tests/test_desktop_shell.py`、`tests/test_desktop_window_state.py`）+ 前端 Vitest（`webui/src/**/__tests__`）

**Target Platform**: Windows 桌面版（B084 仅 Windows）；前端在浏览器模式与桌面版共用，标题栏仅桌面版渲染

**Project Type**: 桌面应用（pywebview 壳 + 本地 Web 工作台）

**Performance Goals**: 动画 60fps 级流畅，不阻塞主线程；胶囊数据更新不新增轮询（复用既有任务轮询）

**Constraints**: `webui/src/views/DiscoveryView.vue`（1249 行）超宪法红线，禁止修改；门面文件（`webui/app.py`/`store.py`/`source.py`/`scripts/boss_cdp_raw.py`/`scripts/zhilian_cdp_raw.py`/`webui/task_runners.py`/`webui/historical_recovery.py`）禁止追加逻辑；前端组件只消费上抛状态、不自行抓取

**Scale/Scope**: 单用户桌面工具；本轮仅做窗口装饰与顶栏胶囊，不涉及抓取/筛选业务逻辑、数据库、门面文件

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **职责分层（I）**：通过。窗口控制落 `packaging/window_controls.py` 独立域，`desktop.py` 只接线；胶囊数据由 composables 汇总、组件消费，符合 view → composable 分层。
- **单文件尺寸（II）**：通过。`packaging/desktop.py` 现 599 行（上限 800），净增需控制（窗口控制逻辑全放新模块）；`webui/src/App.vue` 现 829 行（上限 1200），标题栏挂载+胶囊替换需控制净增，超限则抽组件。
- **引用方向（III）**：通过。前端 view → component → composable → api client；`packaging/window_controls.py` 独立域，`desktop.py` 单向依赖。
- **拆分纪律（IV）**：通过。非重构 Spec，不拆既有大文件；新增组件均为新文件。
- **模块地图（VI）**：新文件（`window_controls.py`、`WindowTitleBar.vue`、`DynamicIsland.vue`）需在同一批次内登记进宪法模块地图。
- **DiscoveryView 红线**：`webui/src/views/DiscoveryView.vue` 1249 行超宪法红线（031 还债中），本 Spec 一律不改，胶囊数据从 composables 上抛。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`.*

- **Allowed files**:
  - `packaging/desktop.py`（仅接线：无边框参数、js_api 暴露窗口控制方法）
  - `packaging/window_controls.py`（新）
  - `webui/src/App.vue`（挂载标题栏 + 替换胶囊 + 提醒按钮扩展）
  - `webui/src/components/WindowTitleBar.vue`（新）
  - `webui/src/components/DynamicIsland.vue`（新）
  - `webui/src/composables/useDiscoveryTasks.ts`（进度数据并入上抛状态）
  - `webui/src/composables/useDiscoveryResults.ts`（结果态数据并入上抛状态）
  - `webui/src/composables/useDiscoveryState.ts`（胶囊状态派生）
  - `webui/src/api.ts`（提醒类型接口扩展，如需）
  - `tests/test_desktop_shell.py`、`tests/test_desktop_shell_wiring.py`（无边框/窗口控制测试）
  - `webui/src/**/__tests__`（新增 WindowTitleBar.spec.ts、DynamicIsland.spec.ts；更新 App.spec.ts 等）
  - `.specify/memory/constitution.md`（模块地图登记）
- **Forbidden files**: `webui/src/views/DiscoveryView.vue`（超限红线）、`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`webui/historical_recovery.py`、数据库与迁移、`roadmap/`、`.codebuddy/`
- **New files**:
  - `packaging/window_controls.py` — 窗口控制 Win32 助手（最小化/最大化/还原、无边框最大化避让任务栏），~100 行
  - `webui/src/components/WindowTitleBar.vue` — 自绘标题栏组件（文字+三按钮+拖拽区+主题配色），~120 行
  - `webui/src/components/DynamicIsland.vue` — 顶栏胶囊灵动岛组件（四态渲染、动画、点击），~250 行
  - `webui/src/components/__tests__/WindowTitleBar.spec.ts` — ~80 行
  - `webui/src/components/__tests__/DynamicIsland.spec.ts` — ~150 行
- **Reference direction**: 前端 `view → component → composable → api client`；`WindowTitleBar`/`DynamicIsland` 只消费上抛状态；`desktop.py → window_controls.py` 单向；`window_controls.py` 不 import 前端
- **Line gate**: `packaging/desktop.py` 净增后 ≤750（超 700 时窗口逻辑全在 `window_controls.py`）；`webui/src/App.vue` 净增后 ≤1100（超限则抽组件）
- **Rationale**: 标题栏与胶囊都是独立可复用 UI 组件，开新组件文件避免 `App.vue` 膨胀；窗口控制是独立域，开新模块避免 `desktop.py` 超限；不追加到既有超限/门面文件

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付最终门禁：相关模块聚焦测试（窗口控制、标题栏渲染/交互、胶囊四态/优先级/点击/动画）+ 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查。
- Windows 真实 EXE 端到端真跑（B084 无边框/交互/记忆回归 + B088 四态/点击/后台运行态）在交付后由用户验证。
- 不涉及版本提升/打包/发布；README 是否需同步，实现收敛后检查。

## Project Structure

### Documentation (this feature)

```text
specs/036-titlebar-dynamic-island/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
packaging/
├── desktop.py            # [改] 无边框接线 + js_api 窗口控制
└── window_controls.py    # [新] 窗口控制 Win32 助手

webui/src/
├── App.vue               # [改] 挂载标题栏（仅 EXE）+ 胶囊换灵动岛 + 提醒通用化
├── api.ts                # [改] 提醒类型接口扩展（如需）
├── components/
│   ├── WindowTitleBar.vue    # [新] 自绘标题栏
│   ├── DynamicIsland.vue     # [新] 顶栏胶囊灵动岛
│   └── __tests__/            # [新] WindowTitleBar.spec.ts / DynamicIsland.spec.ts
└── composables/
    ├── useDiscoveryTasks.ts    # [改] 进度数据上抛
    ├── useDiscoveryResults.ts  # [改] 结果态数据上抛
    └── useDiscoveryState.ts    # [改] 胶囊状态派生

tests/
├── test_desktop_shell.py        # [改] 无边框/窗口控制测试
└── test_desktop_shell_wiring.py # [改] 接线测试
```

**Structure Decision**: 采用既有单一项目结构，按域落位（宪法模块地图）：窗口控制独立 Python 域；标题栏/胶囊为独立前端组件；胶囊数据经既有 composables 汇总上抛。

## Complexity Tracking

无宪法违规，无需填写。
