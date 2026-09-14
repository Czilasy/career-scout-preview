# Feature Specification: 桌面壳原生窗口控制能力拆分（R0）

**Feature Branch**: `codex/feature/desktop-window-resize`

**Created**: 2026-09-14

**Status**: Frozen（行为保持式拆分，独立于 036 v2）

**Input**: 036 v2 的 Plan 把 `packaging/desktop.py`（当前 1019 行，超 800 行红线）列为禁止修改，并要求在实施 036 v2 之前，由独立工程还债 Spec 把该文件中既有的 HWND 查找、WndProc 最大化工作区 hook 与无边框尺寸修正迁入 `packaging/window_controls.py`，保持行为与测试不变。本 Spec 即该拆分（编号 R0）。

## 拆分目标与边界

### 为什么要拆

- `packaging/desktop.py` 已超过项目 Python 文件 800 行红线；按项目规则，超限文件在未拆分前不得继续追加新逻辑。
- 原生窗口控制能力（窗口句柄定位、最大化工作区钳制、无边框尺寸修正）当前与 EXE 启动编排、单实例、错误日志、js_api 混在同一个文件里，职责不单一。
- 036 v2 需要的八方向拉伸、当前显示器工作区、DPI 命中与安装生命周期，都属于「原生窗口控制」这一类职责；这些能力必须先有一个唯一的归属模块，才能保证同一 HWND 只有一条 WndProc 链。

### 拆分范围

**只搬代码，不改变行为。** 本 Spec 不新增、不删除、不修改任何用户可感知行为，不调整窗口尺寸口径、不调整日志文案、不调整安装时机、不调整任何返回值语义。

### A 组：迁移内容（全部来自 `packaging/desktop.py`）

- A1 窗口句柄定位：`EnumWindows` + `GetWindowThreadProcessId`/`IsWindowVisible`/`GetWindowTextW` 的纯 Win32 查找，含「标题含 Career Scout 优先」规则。
- A2 最大化工作区钳制：安装 `WM_GETMINMAXINFO` 子过程，把 `ptMaxSize`/`ptMaxPosition`/`ptMaxTrackSize` 填成工作区矩形，安装后重切一次最大化以让消息重发，并读回实际矩形留痕。
- A3 无边框尺寸修正：按 `GetDpiForWindow` 换算目标物理尺寸，用 `GetWindowPlacement`/`SetWindowPlacement` 修正 `rcNormalPosition`。
- A4 三处共用的诊断记录路径：迁移后仍写到既有 `desktop.log`，前缀（`[clamp]` / `[fixsize]`）与内容口径不变。

### B 组：明确不做

- 不新增八方向拉伸、不做 `WM_NCHITTEST` 命中、不做当前显示器工作区选择、不做 DPI 命中和标题栏拖动。这些属于 036 v2，必须在本拆分完成之后进行。
- 不改 schema、不改窗口状态文件、不改窗口创建参数、不改标题栏前端组件。
- 不删除、不重命名任何既有对外符号；`packaging/desktop.py` 继续作为 EXE 入口并保留兼容导入面。
- 不为“顺手”拆分 `packaging/desktop.py` 的其它部分（单实例、错误日志、就绪轮询、任务取消、js_api、主编排）。

## User Scenarios & Testing

本拆分无用户可见行为变化，因此验收以「既有行为等价」为核心：迁移前已有的测试必须全部继续通过，且不得为了让测试通过而修改断言口径。

### User Story 1 - 原生窗口控制代码只在一个地方定义（Priority: P1）

维护者打开 `packaging/desktop.py` 时，看到的是 EXE 启动编排与接线，而不是 Win32 句柄查找、WndProc 子过程与 `WINDOWPLACEMENT` 结构体；这些能力全部集中在 `packaging/window_controls.py` 一处定义。

**Why this priority**: 这是整个拆分的目的；未达成则 036 v2 无法在不违反文件红线的前提下动工。

**Independent Test**: 在 `packaging/desktop.py` 中检索 HWND 查找、`SetWindowLongPtrW`、`WINDOWPLACEMENT`、`MINMAXINFO` 等标识，均不得再有定义；在 `packaging/window_controls.py` 中可完整检索到。

**Acceptance Scenarios**:

1. **Given** 迁移完成，**When** 检索上述标识，**Then** 定义只在 `packaging/window_controls.py`，`packaging/desktop.py` 只保留调用与接线。
2. **Given** 迁移完成，**When** 阅读 `packaging/desktop.py`，**Then** `events.shown` 回调仍按原顺序调用钳制与尺寸修正，行为不变。

### User Story 2 - 既有窗口行为完全不变（Priority: P1）

用户升级后感知不到任何变化：窗口仍以既有尺寸规则打开、最大化仍不覆盖任务栏、无边框窗口尺寸仍被修正、关闭仍保存窗口状态并取消任务、错误路径仍弹提示并记日志。

**Why this priority**: 拆分若改变行为，就会与本项目「拆文件只搬代码、不改行为」的纪律冲突，也会让 036 v2 失去干净的基线。

**Independent Test**: 运行既有窗口控制与桌面壳聚焦测试并逐项复核迁移函数体的逻辑等价性。042 是 036 v2 的内部前置拆分，不单独重复后端全量、前端全量或构建；这些只在整条 036 v2 交付链最终收敛后统一执行。

**Acceptance Scenarios**:

1. **Given** 拆分前后的代码，**When** 逐函数对比迁移逻辑，**Then** 分支、异常兜底、日志前缀与返回值一致，差异仅限函数归属与必要的依赖注入参数。
2. **Given** 非 Windows 平台，**When** 运行既有桌面壳测试，**Then** 与拆分前一致（钳制与尺寸修正仍为不执行）。
3. **Given** 窗口正常关闭，**When** 触发 `closing`，**Then** 仍按既有链路保存窗口状态、取消运行中任务，退出码不变。

### User Story 3 - 不残留孤儿符号（Priority: P2）

迁移后不留下“搬走了但还被引用”的私有符号，也不留下无人调用的新公共符号。

**Why this priority**: 孤儿符号会让后续 036 v2 的实现者误判所有权，重复安装第二条 WndProc 链。

**Independent Test**: 对迁移后的模块做一次引用盘点：每个迁移符号都有明确调用方；`packaging/desktop.py` 中不再出现对已迁移私有符号的悬空引用。

**Acceptance Scenarios**:

1. **Given** 迁移完成，**When** 检查 `_cs_maximize_hook` 写入点与读取点，**Then** 写入与读取指向同一模块语义，且没有任何模块再定义第二个同类钩子。
2. **Given** 迁移完成，**When** 检查窗口状态相关调用，**Then** 仍由桌面入口注入工作区提供者，窗口控制模块不反向依赖桌面入口。

### Edge Cases

- 找不到 HWND（窗口尚未显示、被其它顶层窗口干扰）：与拆分前一致，记录失败原因并静默返回，不阻断启动。
- 工作区提供者返回空：与拆分前一致，钳制放弃并留痕。
- `GetWindowPlacement`/`SetWindowPlacement` 失败或抛异常：与拆分前一致，静默降级。
- 非 Windows 平台：与拆分前一致，直接返回。
- 迁移函数被重复调用：与拆分前一致，不产生第二条钩子（幂等性由后续 036 v2 的安装合同负责，本 Spec 不改变现有语义）。

## Requirements

### Functional Requirements

- **FR-001**: `packaging/desktop.py` MUST NOT 再定义窗口句柄查找、WndProc 最大化工作区子过程、无边框尺寸修正这三项能力的实现。
- **FR-002**: `packaging/window_controls.py` MUST 单一拥有 FR-001 所列三项能力，并保留既有模块职责（最小化/最大化/还原原语）。
- **FR-003**: 本次迁移 MUST 保持行为不变：分支逻辑、异常兜底、日志前缀、返回值语义与安装时机均与迁移前一致。
- **FR-004**: `packaging/desktop.py` MUST 继续作为 EXE 入口与接线层，保留兼容导入面，使既有测试与外部引用不被破坏。
- **FR-005**: 迁移 MUST NOT 引入对 `packaging/desktop.py` 的反向依赖：`packaging/window_controls.py` 不得 import 桌面入口。
- **FR-006**: 迁移 MUST NOT 改变窗口创建参数、窗口状态 schema、窗口尺寸口径与标题栏前端行为。
- **FR-007**: 迁移后 `packaging/desktop.py` MUST 显著低于拆分前规模，`packaging/window_controls.py` MUST 低于 800 行红线。
- **FR-008**: 迁移 MUST NOT 新增第二个 WndProc 安装点；同一 HWND 仍只有一条由本模块拥有的子过程链。
- **FR-009**: 迁移 MUST NOT 删除或重命名既有对外符号，除非同批次同步全部调用方与测试。
- **FR-010**: 本 Spec MUST NOT 包含 036 v2 的任何功能实现，包括八方向命中、当前显示器工作区、DPI 命中与标题栏拖动。
- **FR-011**: 拆分批次 MUST 通过既有聚焦测试、等价性复核与仓库卫生检查；作为 036 v2 的内部前置，MUST NOT 单独重复后端全量、前端全量或构建，整条交付链收敛后统一验证。

### Key Entities

- **窗口句柄定位**：从当前进程的可见顶层窗口中找到目标 HWND 的纯 Win32 查找，含标题优先规则。
- **最大化工作区钳制**：通过 `WM_GETMINMAXINFO` 子过程把最大化矩形限制到显示器工作区的原生适配。
- **无边框尺寸修正**：消除「先设尺寸后去边框」造成的窗口缩水，按 DPI 换算并用 `SetWindowPlacement` 修正还原矩形。
- **原生窗口控制模块**：`packaging/window_controls.py`，拥有上述三项能力与既有窗口原语，是后续窗口交互增强的唯一落点。

## Success Criteria

### Measurable Outcomes

- **SC-001**: `packaging/desktop.py` 中上述三项能力的定义数量为 0，`packaging/window_controls.py` 中为 3（句柄定位、工作区钳制、尺寸修正）。
- **SC-002**: 迁移批次的既有聚焦测试、等价性复核与仓库卫生检查通过，且未修改任何既有断言口径；整条 036 v2 交付链最终全量验证留到功能收敛后执行。
- **SC-003**: `packaging/window_controls.py` 行数低于 800；`packaging/desktop.py` 行数低于拆分前，且文件职责收敛为入口与接线。
- **SC-004**: 引用方向检查：`packaging/window_controls.py` 对 `packaging/desktop.py` 的 import 数量为 0。
- **SC-005**: 迁移符号孤儿数（无调用方的新增公共符号）为 0。

## Verification Scope

- 聚焦测试：既有窗口控制与桌面壳测试全量通过，不新增“为了迁移”的断言口径变更。
- 验证节奏：042 只做聚焦测试、等价性复核和仓库卫生检查，不单独运行后端全量、前端全量或构建；统一全量属于 036 v2 最终收敛门禁。
- 等价性复核：逐函数对比迁移前后的分支、兜底、日志与返回值。
- 本 Spec 不授权版本提升、提交、推送、打包发布或修改外部数据，也不包含真实真机验收（该验收属于 036 v2）。

## Assumptions

- **A1**: 拆分前基线为当前分支 `packaging/desktop.py` 1019 行、`packaging/window_controls.py` 117 行。
- **A2**: 迁移使用“实现下沉 + 入口保留调用与兼容导入面”的方式，不需要改动任何调用方。
- **A3**: 迁移阶段的诊断日志继续写入既有 `desktop.log`，不引入新的日志目标。
- **A4**: 本次拆分不承担修复职责；若迁移过程中发现既有缺陷，只记录，不在本 Spec 内修复。

## Out of Scope

- 八方向拉伸、顶栏拖动还原、顶边释放最大化、窗口记忆口径调整（属于 036 v2）。
- 视觉、主题、标题栏高度、按钮样式（属于既有视觉基线）。
- macOS/Linux 桌面壳行为调整。
- `packaging/desktop.py` 其它职责（单实例、错误日志、就绪轮询、任务取消、js_api、主编排）的进一步拆分。
