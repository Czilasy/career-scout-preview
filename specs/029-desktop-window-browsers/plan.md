# Implementation Plan: 桌面壳窗口记忆修复批（最大化记忆 + 首开默认 + 多浏览器）

**Branch**: `029-desktop-window-browsers` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/029-desktop-window-browsers/spec.md`

## Summary

三项打包（B082）：①窗口记忆重构为"普通矩形 + 最大化标记"双态模型——首开/无记忆/污染记忆一律最大化开窗，普通态默认 1545×900 居中（小屏钳制），最大化关窗不再用全屏矩形覆盖普通记忆；记忆文件 schema 2→3 平滑升级。②修复最大化关闭导致拖动记忆丢失的核心 Bug：运行时监听窗口事件实时维护最后一次普通矩形，关窗按当前状态落盘。③新增 Chromium 浏览器注册表（8 家）+ 探测 + 设置页全局选择 + 手动路径校验 + 启动链路接线，抓取专用实例按浏览器命名空间隔离数据目录。

技术路径：`packaging/window_state.py` 新模块承接窗口状态域（desktop.py 分流，宪法原则 II/VI）；`scripts/boss/browser_registry.py` 新模块承载注册表与校验；设置 API 开独立路由域 `webui/browser_registry_api.py`（仿 log_api 先例）；选择持久化搭 `advanced_settings.json` 现有通道；前端按现有"设置菜单 + 对话框"模式新增 BrowserSettingsDialog。

## Technical Context

**Language/Version**: Python 3.11（后端/桌面壳）+ TypeScript/Vue 3 + Vite（前端）

**Primary Dependencies**: Flask（后端）、pywebview 6.2.1 WinForms 后端（桌面壳）、PyInstaller（打包）、Vitest（前端测试）

**Storage**: 本地 JSON 文件：`~/.career-scout/desktop_window.json`（窗口记忆，schema 3）、`~/.career-scout/advanced_settings.json`（浏览器选择新键，经现有读写通道）——实施修订（用户已批准）：选择持久化改为注册表域自持 `browser_selection.json`（原计划依赖的键白名单位于允许清单外的 settings 域文件，经批准维持绕开方案）；浏览器账号 `profile_dir` 为显式路径存储（现有机制，不改账号结构）。**无数据库 schema 变更。**

**Testing**: unittest（后端，含 deps 注入式纯逻辑单测）+ Vitest（前端组件测试）

**Target Platform**: Windows 桌面壳为主（EXE）；macOS 侧窗口记忆逻辑通用、仅保证不回归

**Performance Goals**: 窗口事件处理为轻量内存更新，不引入可感知延迟；抓取启动链路新增的浏览器解析为本地路径探测，无网络等待

**Constraints**: pywebview 事件在 WinForms 后端的触发时序存在不确定性 → 关窗时刻兜底保存为准（见 research D1）；国产魔改内核 CDP 兼容性未知 → 双重校验（保存时 `--version` 探活 + 启动后调试端点内核判定），失败给明确报错

**Scale/Scope**: 单机单用户桌面应用；新增 ~6 个文件（~1.3k 行含测试）、修改 ~10 个文件（小改）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 结论 | 依据 |
|---|---|---|
| I. 职责分层 | ✅ | 窗口状态域独立模块；注册表域独立模块；路由域只做路由/校验/响应组装 |
| II. 单文件尺寸边界 | ✅ | `desktop.py` 730 行（>600 预警）迁出后降至 ~480；`settings_api.py` 568 行不动（新路由开新模块）；`test_desktop_shell.py` 828 行只迁出不新增；新文件均在限内 |
| III. 引用方向 | ✅ | `desktop.py → window_state.py`；`webui → scripts/boss`（现状方向）；`browser_registry_api → browser_registry`；前端 `App.vue → 组件`、`api.ts → 端点` |
| IV. 拆分纪律 | ✅ | 本次 desktop.py 迁出属原则 VI 预警线分流（功能批次内允许），非整文件重构 Spec；行为变化由新增测试先行定义 |
| V. 验证门禁 | ✅ | 交付门禁 = 聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 仓库卫生（Verification Gate 节） |
| VI. 模块地图 | ✅（需登记） | 新文件 `packaging/window_state.py`、`scripts/boss/browser_registry.py`、`webui/browser_registry_api.py` 须在批次收尾时登记进宪法模块地图（列入 tasks 收尾任务） |

**Phase 1 设计后复查**：无新增违规。数据目录命名空间方案（research D6）不改账号存储结构、不改数据库，原则边界维持。

## File Boundaries

*GATE: 2026-08-29 经用户确认（会话内落位清单逐项确认"可以，开始"）。*

- **Allowed files（修改）**: `packaging/desktop.py`、`scripts/boss/constants.py`、`scripts/boss/browser.py`、`webui/pipeline_exec_chrome.py`、`webui/pipeline_exec_accounts.py`、`webui/app.py`（仅一行路由注册）、`webui/src/components/AppSettingsMenu.vue`、`webui/src/App.vue`、`webui/src/api.ts`、`tests/test_desktop_shell.py`（仅迁出用例）、`.specify/memory/constitution.md`（仅模块地图登记，收尾批次）
- **Forbidden files**: `webui/store.py`、`webui/source.py`、`webui/ai.py`、`webui/tuning.py`、`webui/pipeline_exec.py`、`webui/settings_api.py`、`webui/browser_support.py`、`scripts/boss_cdp_raw.py`（门面禁改；若兼容 re-export 必须经门面，须先在批次内说明理由并保持纯 re-export 行）；任何数据库迁移文件
- **New files**:
  - `packaging/window_state.py` — 窗口状态域：schema 3 读写/升级/钳制/普通矩形追踪（~300 行）
  - `scripts/boss/browser_registry.py` — 浏览器注册表：8 家配置/探测/手动路径校验/内核校验（~200 行）
  - `webui/browser_registry_api.py` — 路由域：探测列表/保存选择/路径校验端点（~90 行）
  - `webui/src/components/BrowserSettingsDialog.vue` — 浏览器选择对话框（~250 行）
  - `tests/test_desktop_window_state.py` — 窗口状态域单测（~300 行）
  - `tests/test_browser_registry.py` — 注册表/探测/校验单测（~200 行）
  - `tests/test_desktop_shell_wiring.py` — 窗口状态编排层测试（实施期从 test_desktop_window_state.py 拆出，防超 800 行，沿 027 先例）
  - `webui/src/components/__tests__/BrowserSettingsDialog.spec.ts`（或现有前端测试约定路径）— 组件测试（~120 行）
- **Reference direction**: `packaging/desktop.py → packaging/window_state.py`（壳层内单向，window_state 不得 import desktop）；`scripts/boss/browser_registry.py ← constants.py/browser.py/pipeline_exec_chrome.py 消费`；`webui/browser_registry_api.py → scripts/boss/browser_registry`（跨包单向向下）；前端 `App.vue → BrowserSettingsDialog/AppSettingsMenu`、`api.ts → HTTP 端点`
- **Line gate**: `desktop.py` ≈480 < 600；新 Python 模块 90~300 < 600；Vue 组件 ~250 < 900；`App.vue` ~700 < 900；`api.ts` ~290；全部满足宪法 II
- **Rationale**: desktop.py 已过 600 预警线，宪法原则 VI 强制后续改动开新模块分流；浏览器注册表属全新领域，无既有域可落；settings_api.py 568 行接近预警线，按 022 log_api 先例开独立路由域而非推高既有文件

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试（`tests/test_desktop_window_state.py`、`tests/test_browser_registry.py`、`tests/test_desktop_shell.py` 及前端组件测试）、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）。
- 本 Spec 特别约定（来自 spec Verification Scope）：窗口记忆真机冒烟（最大化/还原/拖拽记忆）在 Windows 实机执行一次并记录；多浏览器真机冒烟仅覆盖本机已安装的 Chrome、Edge，其余注册表条目以配置正确性单测兜底。

## Project Structure

### Documentation (this feature)

```text
specs/029-desktop-window-browsers/
├── plan.md              # 本文件
├── research.md          # Phase 0 产出：D1-D7 技术决策
├── data-model.md        # Phase 1 产出：实体与校验规则
├── quickstart.md        # Phase 1 产出：验证指南
├── contracts/
│   ├── desktop-window-state.md   # desktop_window.json schema 3 文件契约
│   └── browser-registry-api.md   # 浏览器注册表 HTTP 端点契约
└── tasks.md             # Phase 2 产出（/speckit-tasks）
```

### Source Code (repository root)

```text
packaging/
├── desktop.py               # 壳编排：事件接线、启动 maximized、closing 兜底（修改，瘦身）
└── window_state.py          # 窗口状态域（新增）

scripts/boss/
├── constants.py             # 探测改查注册表 + 兼容 re-export（修改）
├── browser.py               # 进程枚举 exe 名单接注册表（修改）
└── browser_registry.py      # 浏览器注册表域（新增）

webui/
├── app.py                   # 仅一行路由注册（修改）
├── browser_registry_api.py  # 路由域（新增）
├── pipeline_exec_chrome.py  # 启动 exe 解析接所选浏览器（修改）
└── pipeline_exec_accounts.py# 账号数据目录浏览器命名空间派生（修改）

webui/src/
├── App.vue                  # 对话框挂载接线（修改）
├── api.ts                   # 客户端方法（修改）
└── components/
    ├── AppSettingsMenu.vue  # 菜单项（修改）
    ├── BrowserSettingsDialog.vue  # 选择对话框（新增）
    └── __tests__/BrowserSettingsDialog.spec.ts  # 组件测试（新增）

tests/
├── test_desktop_shell.py    # 仅迁出窗口状态用例（修改）
├── test_desktop_window_state.py  # 窗口状态单测（新增）
└── test_browser_registry.py # 注册表单测（新增）
```

**Structure Decision**: 沿用现有分层（packaging 壳层 / scripts/boss 抓取域 / webui 路由域 + Vue 组件），不引入新顶层目录；新模块全部按宪法模块地图登记。

## Complexity Tracking

> 无宪法违规需要豁免。
