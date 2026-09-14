# Implementation Plan: 桌面壳原生窗口控制能力拆分（R0）

**Branch**: `codex/feature/desktop-window-resize` | **Date**: 2026-09-14 | **Spec**: [spec.md](spec.md)

**Input**: 冻结需求来自 `specs/042-desktop-window-controls-split/spec.md`。本 Plan 只做行为保持式拆分，是 036 v2 的前置阻断依赖。

## Summary

把 `packaging/desktop.py` 中的三项原生窗口控制能力（`EnumWindows` 句柄定位、`WM_GETMINMAXINFO` 最大化工作区钳制、无边框尺寸修正）整体下沉到 `packaging/window_controls.py`，桌面入口只保留调用与兼容导入面。迁移使用依赖注入替代原有的跨模块直连：工作区由入口注入（沿用既有 `window_state.default_workarea_provider`），诊断日志由入口注入 `logger`/`state_dir`，默认尺寸由入口传入。这样 `window_controls.py` 不反向 import 桌面入口，行为保持一致。

不做任何行为变更，因此不新增测试断言口径；既有测试是等价性的客观证据。

## Technical Context

**Language/Version**: Python ≥3.10（本机 3.11.15）

**Primary Dependencies**: `ctypes`（Win32 API）、pywebview 6.2.1（既有）；无新增第三方依赖

**Storage**: 无变化（`~/.career-scout/desktop_window.json` schema 3 不动）

**Testing**: `uv run python -m unittest`（Python `unittest`）、前端 Vitest 与构建、仓库卫生测试

**Target Platform**: Windows 10/11 桌面版为主；非 Windows 必须保持“不执行、不报错”

**Project Type**: Python 桌面壳（EXE 入口）+ 本地 Web 应用

**Constraints**: 只搬代码不改行为；不得为通过测试而改断言；不新增第二个 WndProc 安装点；窗口控制模块不得反向依赖桌面入口

**Scale/Scope**: 两个 Python 文件、一个测试文件（仅在符号归属变化导致导入失效时才同步）；预计迁移约 230 行

## Constitution Check

*GATE：Phase 0 前检查，并在 Phase 1 后复查。*

| 宪法要求 | 规划结论 |
|---|---|
| 原则 I 职责分层 | PASS：原生窗口控制集中到一个模块，桌面入口回到入口与接线职责 |
| 原则 II 单文件尺寸边界 | PASS：拆分后桌面入口显著变小、窗口控制模块远低于 800 行；拆分只搬代码不改行为 |
| 原则 III 引用方向 | PASS：入口 → 窗口控制单向；窗口控制不 import 入口 |
| 原则 IV 拆分与重构纪律 | PASS：本拆分单独 Spec/Plan/Tasks，且先于 036 v2 实施 |
| 原则 V 验证门禁 | PASS：本前置拆分只跑聚焦、等价性与卫生；统一全量留到 036 v2 整条交付链最终收敛 |
| 原则 VI 模块地图与落位规则 | PASS：`packaging/window_controls.py` 职责扩展后更新模块地图条目 |
| 原则 VII 错误处理与可观测性 | PASS：既有静默降级与日志前缀原样保留，不新增纯 pass 吞异常 |
| 无敏感数据与外部写入 | PASS：只动本机代码，不涉及凭据或外部服务 |

**阻断关系**：本拆分是 036 v2 的硬前置；未完成前不得在 036 v2 中修改 `packaging/desktop.py`，也不得开始产品实现。

## File Boundaries

- **Allowed product files**:
  - `packaging/window_controls.py`：迁入句柄定位、最大化工作区钳制、无边框尺寸修正三项能力；预计 +230 行左右，完成后仍低于 800 行红线。
  - `packaging/desktop.py`：删除上述三项实现，保留调用与兼容导入面；预计减少 200 行以上。
- **Allowed tests**: `tests/test_window_controls.py`（仅在需要覆盖迁移后能力的纯逻辑分支时扩充）、`tests/test_desktop_shell.py`（仅在符号归属变化导致既有导入失效时同步导入来源；不得改变断言口径）。
- **Allowed documents**: `specs/042-desktop-window-controls-split/` 内全部工件、`.specify/memory/constitution.md` 模块地图条目。
- **Forbidden files**: 除上述两文件外的全部产品代码（含 `webui/`、`scripts/`）、`packaging/window_state.py`、其他 Vue 组件、数据库与任务运行器、根目录 036 v1 平铺文档。
- **New product files**: 无。本次只迁移既有实现，不新建模块。
- **Reference direction**: `packaging/desktop.py → packaging/window_controls.py`；`window_controls` 不 import `desktop`，也不 import `webui` 业务模块；工作区数据与日志出口由入口注入。
- **Line gate**: 拆分后两个文件都必须低于 800 行红线；`packaging/window_controls.py` 不得因迁移越过 600 行预警线（预计约 360 行）。

## Architecture and Interaction Design

### 1. 迁移后的职责切分

- `packaging/window_controls.py`：
  - 既有窗口原语（`minimize`/`maximize`/`restore`/`toggle_maximize`/`note_maximized`/`is_maximized`）；
  - 句柄定位 `find_main_hwnd_pid()`；
  - 最大化工作区钳制 `install_maximize_clamp(window, workarea_provider=None, state_dir=None, logger=None)`；
  - 无边框尺寸修正 `fix_frameless_size(window, target_w, target_h, state_dir=None, logger=None)`；
  - 诊断出口 `log_desktop_error(message, state_dir=None, logger=None)`（延迟 import 桌面入口的日志函数，失败静默）。
- `packaging/desktop.py`：
  - 保留常量、单实例、错误日志、就绪轮询、任务取消、js_api、主编排；
  - 从 `window_controls` 导入上述能力并保留既有私有别名（`_find_main_hwnd_pid` 等）作为兼容导入面；
  - 主编排的 `events.shown` 回调顺序与调用参数保持一致。

### 2. 依赖注入方向

- 工作区：入口在调用点注入 `default_workarea_provider`（既有 `window_state` 能力），窗口控制模块不 import 状态模块。
- 日志：入口继续注入 `state_dir`/`logger`；窗口控制模块不直接写文件时也能留痕。
- 默认尺寸：入口把既有常量传入尺寸修正，窗口控制模块不持有窗口尺寸口径。

### 3. 行为保持要点

- 分支、异常兜底、日志前缀（`[clamp]`/`[fixsize]`）、返回值语义、安装时机（`events.shown`）逐项与原实现一致。
- 非 Windows 平台直接返回，与拆分前一致。
- 不新增第二个 WndProc 安装点；迁移后同一 HWND 仍只有一条由窗口控制模块拥有的子过程链（`window._cs_maximize_hook` 语义不变）。

## Verification Gate

- 聚焦：`uv run python -m unittest tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state`
- 卫生：`uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status --short`
- 等价性复核：逐函数对比迁移前后实现，并把结论写入本目录 `quickstart.md`。
- 本拆分是 036 v2 的内部前置，不单独运行后端全量、前端全量或构建；这些只在 036 v2 整条交付链完成并收敛后统一运行。
- 聚焦失败后只重跑失败用例与直接受影响范围；禁止为了获取失败名称或在没有相关改动时运行全量。
- 本拆分不包含真机验收；真机验收属于 036 v2。

## Project Structure

### Documentation

```text
specs/042-desktop-window-controls-split/
├── spec.md
├── plan.md
├── tasks.md
├── research.md
└── quickstart.md
```

### Source Code

```text
packaging/
├── desktop.py          # EXE 入口与接线（迁移后显著变小，保留兼容导入面）
└── window_controls.py  # 窗口原语 + 句柄定位 + 最大化钳制 + 尺寸修正（单一归属）
tests/
├── test_window_controls.py
└── test_desktop_shell.py
```

**Structure Decision**: 深化既有 `window_controls.py`，不新建平行模块。同一 HWND 的原生消息所有权必须唯一，任何新增文件都会分散所有权。

## Post-Design Constitution Check

- PASS：无未解决的需求澄清；FR-001–FR-011 均有设计落点或验证门禁。
- PASS：拆分后两文件均在红线内，且不触碰其它超限文件。
- PASS：无新增外部依赖、数据库、权限或平台分支业务。
- PASS：失败时整体停止（不完成拆分即不得开工 036 v2），不存在部分交付。

## Complexity Tracking

无宪法例外。本拆分正是宪法原则 II/IV 要求的“超限文件先拆分”动作。
