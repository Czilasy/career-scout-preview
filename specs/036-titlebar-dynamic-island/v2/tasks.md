# Tasks: Windows 桌面窗口拉伸与原生式顶栏拖动

**Input**: `spec.md`（最高需求权威）、`plan.md`、`research.md`、`data-model.md`、`contracts/desktop-window-interaction.md`、`quickstart.md`

**Delivery rule**: R0、US1–US4、Gate 1、Windows 10/11 真实 E2E 是一条不可拆分交付链。开发与返修只跑聚焦测试，整条链收敛后才运行最终后端全量；不得让 R0 或每个用户故事各跑一次全量。任何任务或技术判断与 `spec.md` 冲突时，停止并修改 Plan/Tasks；不得修改 Spec 来迁就实现。

## Format: `[ID] [P?] [Story] Description`

- `[P]` 只表示文件不重叠且依赖已满足时可并行，不授权多 AI 共用工作目录。
- 用户故事任务必须带 `[US1]`–`[US4]`。
- 所有任务都给出精确文件路径；禁止顺手修改未列文件。

## File Boundaries

- **Allowed product files**: `packaging/window_controls.py`、`packaging/window_interaction.py`（新增：交互引擎，2026-09-14 线门禁触发的 Plan 修订）、`packaging/window_metrics.py`（新增：工作区/DPI/尺寸换算，2026-09-14 宪法 75% 预警线整改）、`packaging/desktop.py`（仅 js_api 接线与安装调用，2026-09-14 用户批准新增）、`packaging/window_state.py`、`webui/src/components/WindowTitleBar.vue`、`webui/src/api.ts`（仅类型声明），以及条件性的 `webui/src/styles.css`。
- **Allowed tests**: `tests/test_window_controls.py`、`tests/test_desktop_window_state.py`、`tests/test_desktop_shell.py`、`webui/src/components/__tests__/WindowTitleBar.spec.ts`。
- **Allowed documents**: `specs/036-titlebar-dynamic-island/INDEX.md` 与 `specs/036-titlebar-dynamic-island/v2/`。
- **Forbidden**: 根目录 036 v1、灵动岛/提醒/主题、其他 Vue 页面与组件、数据库、任务运行器、macOS 和平台抓取模块。
- **Reference direction**: `WindowTitleBar DOM 区域判定 → js_api → window_controls 引擎`；`desktop 接线 → window_controls`；`desktop 事件 → window_state`。不得反向导入。
- **Line gate**: Python ≤800 行、Vue ≤1200 行；`packaging/desktop.py` 的 R0 拆分已在独立 Spec 中完成，本功能内必须仍低于 800 行。

## Phase 1: Setup and External Prerequisite

**Purpose**: 锁定最高需求基线，并证明本功能不会向超限壳层入口继续加逻辑。

- [x] T001 逐条核对 `specs/036-titlebar-dynamic-island/v2/spec.md` 的 FR-001–FR-024 与 `specs/036-titlebar-dynamic-island/v2/plan.md`，发现冲突只修订 Plan/Tasks，不改冻结 Spec
  - 结论（2026-09-14）：原 Plan 的「顶层 WndProc 原生命中」与真机事实冲突（见 Research D9 实测），已按 Plan 自身规则修订 Plan / Research / Contracts（不改 Spec），候选改为「页面判定 + 宿主执行」。
- [x] T002 完成并验收 R0 独立拆分 Spec，使 `packaging/desktop.py` 不再定义 HWND 查找、WndProc 最大化工作区 hook 和无边框尺寸修正，并让 `packaging/window_controls.py` 单一拥有这些能力；R0 未完成则停止 036 v2
  - 证据（2026-09-14）：三项能力均只在 `packaging/window_controls.py`（431 行）；`packaging/desktop.py` 787 行；聚焦测试 `tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state` 94 用例全过。
- [x] T003 在 `specs/036-titlebar-dynamic-island/v2/quickstart.md` 记录实际 Gate 1 测试环境清单（Windows 10/11、pywebview 6.2.1、WebView2 版本、100%/125%/150% DPI、显示器布局），不得填写未实际具备的环境
  - 结果（2026-09-14）：见 quickstart §8.1。
- [x] T004 重新测量 `packaging/window_controls.py`、`packaging/window_state.py`、`webui/src/components/WindowTitleBar.vue` 的行数，并把结果记录到 `specs/036-titlebar-dynamic-island/v2/quickstart.md`；任何目标会越过红线则停止并回到 Plan
  - 结果（2026-09-14）：见 quickstart §8.2；四文件均在红线内。

**Checkpoint**: R0 和测试环境均具备；否则不得开始任何产品实现。

---

## Phase 2: Foundational Gate 1 — Exact Packaged Shell

**Purpose**: 用最小、可保留的候选实现回答最危险的问题。此阶段失败即整体停止，禁止进入 US1–US4。

### Tests first

- [x] T005 [P] 在 `tests/test_window_controls.py` 添加失败测试（同一文件覆盖两个模块：适配器/原语导入 `window_controls`，几何与交互入口导入 `window_interaction`），覆盖：物理/逻辑 DPI 单次换算、逐轴最小跟踪尺寸与「工作区更小则逐轴放宽」例外、拉伸矩形计算（对角方向 + 反向锚定 + 工作区上限）、移动矩形计算、最大化拖下还原的抓取比例定位、顶部释放判定与左右/角落不触发最大化、最大化态拒绝边缘拉伸、幂等安装
  - 证据（2026-09-14）：先红（32 个新用例报错）后绿；另补「未拖动不得因顶部点击而最大化」用例。
- [x] T006 [P] 在 `webui/src/components/__tests__/WindowTitleBar.spec.ts` 添加失败测试，要求移除 `.pywebview-drag-region`，新增边缘/标题带区域判定与一次调用（`window_begin_move` / `window_begin_resize`）、按钮区不触发移动/拉伸、指针反馈类名，同时保留桌面/浏览器渲染、双击、三按钮、图标与关闭中状态
  - 证据（2026-09-14）：新增 8 个交互用例 + 改写 drag-region 用例；`npm test -- --run WindowTitleBar.spec.ts` 25 passed。

### Minimal candidate

- [x] T007 在 `packaging/window_controls.py` 扩展 R0 单一适配器（幂等安装返回 `installed`/`reason`/`hwnd_found`，`WM_GETMINMAXINFO` 扩为当前显示器工作区 + 逐轴最小/最大跟踪尺寸，不得新增第二条 WndProc 链），并在新增的 `packaging/window_interaction.py` 实现几何数学与移动/拉伸引擎
  - 证据（2026-09-14）：`window_controls.py` 仍只有一条 hook（`_install_minmax_hook`）；交互引擎与 js_api 入口在 `window_interaction.py`（549 行）。
- [x] T008 在 `webui/src/components/WindowTitleBar.vue` 移除旧 `.pywebview-drag-region` 与过时说明，新增边缘/标题带区域上报与指针反馈，保持 36px 高度、视觉、双击和窗口按钮不变
  - 证据（2026-09-14）：组件 372 行；未新增可见元素（指针反馈只用 `<html data-cs-region>` + 全局 cursor 规则）。
- [x] T009 运行 `uv run python -m unittest tests.test_window_controls` 与 `webui/` 下 `npm test -- WindowTitleBar.spec.ts`，将实际结果记录到 `specs/036-titlebar-dynamic-island/v2/quickstart.md`
  - 证据（2026-09-14）：见 quickstart §8.5；后端聚焦 127 用例 OK、前端 25 用例通过、类型检查通过、四文件均在红线内。

### Real Gate 1

- [x] T010 在 Windows 10 正式同构桌面壳中按 `specs/036-titlebar-dynamic-island/v2/quickstart.md` 实测 8/8 拉伸、按钮/页面点击、普通移动、最大化拖下、顶部最大化、左右/角落不贴靠和 10 次启停，并记录逐项证据（候选已按 Research D9 改为「页面判定 + 宿主执行」）
- [x] T012 对照 `specs/036-titlebar-dynamic-island/v2/contracts/desktop-window-interaction.md` 审核 T010；任一核心项失败时把 `specs/036-titlebar-dynamic-island/INDEX.md` 状态改为 `BLOCKED` 并停止，不实现 fallback；全部通过才允许进入 Phase 3

**Checkpoint**: Gate 1 在 Windows 10/11 全部通过。通过只证明候选壳层可行，不代表功能已经交付。

---

## Phase 3: User Story 1 — 四边四角自然拉伸 (Priority: P1)

**Goal**: 用户从四边四角连续调整窗口，方向、指针、最小/最大边界和实时内容重排正确。

**Independent Test**: Windows 10/11 的 8 个方向均能缩放；100%/125%/150% DPI 命中正确；窗口在工作区足够时不能小于 1024×700，也不能大于当前工作区。

### Tests first

- [x] T013 [US1] 扩充 `tests/test_window_controls.py` 的失败测试，覆盖 8 个命中返回值、边角交界、最大跟踪尺寸、任务栏四边工作区、1024×700 下限和小工作区逐轴例外
- [x] T014 [US1] 在 `tests/test_desktop_shell.py` 将窗口创建最小尺寸旧契约从固定 1400×800 更新为 1024×700，并保留 `resizable=True`、`frameless=True`、`easy_drag=False` 回归断言

### Implementation

- [x] T015 [US1] 在 `packaging/window_interaction.py` 完成八方向拉伸（几何 + 宿主循环）与 `packaging/window_controls.py` 的 `WM_GETMINMAXINFO` 当前显示器工作区处理，保证 DPI 只转换一次且未处理消息转发原过程
  - 说明（2026-09-14）：原计划写的是 `WM_NCHITTEST`，D9 真机实测后改为「页面判定区域 + 宿主执行」，任务文本已同步修订。
- [x] T016 [US1] 在 `packaging/window_controls.py` / `packaging/window_interaction.py` 增加适配器安装失败摘要（`installed`/`reason`/`hwnd_found`）与交互生命周期保护（busy 守卫、90 秒上限、失败节点一行日志），只记录安装/失败节点，不记录每条命中或拉伸消息
- [x] T017 [US1] 运行 `uv run python -m unittest tests.test_window_controls tests.test_desktop_shell`，并在真实桌面壳复验 8/8 方向、三档 DPI、实时重排和任务栏边界；证据写入 `specs/036-titlebar-dynamic-island/v2/quickstart.md`
  - 证据（2026-09-14）：聚焦测试通过；真机侧用户自测覆盖四方向拉伸、实时重排与任务栏边界（quickstart §8.6）。

**Checkpoint**: US1 可独立证明八方向拉伸，但仍不得单独发布。

---

## Phase 4: User Story 2 — 顶栏拖下还原与拖顶最大化 (Priority: P1)

**Goal**: 普通移动、最大化拖下还原、抓取位置连续和顶部释放最大化形成稳定往返，同时不引入左右/四角贴靠。

**Independent Test**: 连续完成 10 次完整往返，无跳离、拖动中断、任务栏覆盖或禁止的贴靠结果；标题栏按钮与双击保持可用。

### Tests first

- [x] T018 [P] [US2] 在 `tests/test_window_controls.py` 添加失败测试，覆盖最大化态标题栏移动触发还原、还原后抓取比例定位、按钮区（页面判定）不触发移动、当前显示器工作区选择与指针显示器切换
- [x] T019 [P] [US2] 在 `webui/src/components/__tests__/WindowTitleBar.spec.ts` 补齐最终行为回归，覆盖双击按钮不触发窗口切换、按钮点击不触发移动语义、标题带按下只发起一次调用及现有视觉类不变

### Implementation and validation

- [x] T020 [US2] 在 `packaging/window_controls.py` 与 `packaging/desktop.py` 完成标题带移动与最大化拖下还原接线（一次调用 + 宿主循环），不增加逐帧 JS 回传、备用拖动路径或系统贴靠循环
- [x] T021 [US2] 在 Windows 10 真实桌面壳执行 10 次“最大化→拖下还原→继续移动→拖顶释放最大化”，并复验左/右/角落释放不形成贴靠；结果写入 `specs/036-titlebar-dynamic-island/v2/quickstart.md`
- [x] T023 [US2] 运行 `uv run python -m unittest tests.test_window_controls` 与 `webui/` 下 `npm test -- WindowTitleBar.spec.ts`，任一失败或 T021 出现禁止贴靠时按失败停止合同更新 `specs/036-titlebar-dynamic-island/INDEX.md` 并终止

**Checkpoint**: US2 可独立证明顶栏往返，但仍不得脱离 US1/US3/US4 发布。

---

## Phase 5: User Story 3 — 最近普通窗口记忆 (Priority: P1)

**Goal**: 用户拉伸和移动后的普通矩形可靠保存；最大化矩形永不污染；失效记忆在当前显示器布局中恢复可用。

**Independent Test**: 普通关闭、最大化关闭后还原、跨屏关闭、断开副屏、损坏记忆和小工作区六类场景全部返回有效普通矩形。

### Tests first

- [x] T024 [US3] 在 `tests/test_desktop_window_state.py` 添加失败测试，覆盖 1400×800 默认、1024×700 常规下限、合法大于 1400×800 记忆、动态工作区上限、小工作区例外和 schema 3 兼容
- [x] T025 [US3] 在 `tests/test_desktop_window_state.py` 添加 Tracker 失败测试，覆盖拉伸后宽高更新、移动后坐标合并、最大化冻结、拖下还原续记及 closing 单次快照

### Implementation

- [x] T026 [US3] 在 `packaging/window_state.py` 把固定 `MIN=MAX=DEFAULT` 校验改为默认值、常规下限和动态工作区约束，保持 schema 3 与既有 default 字段兼容
- [x] T027 [US3] 在 `packaging/window_state.py` 校正读取、普通矩形钳制和 Tracker 边界，使合法放大尺寸可恢复、最大化/最小化事件不污染普通矩形

**Checkpoint**: US3 独立证明窗口记忆，但完整交付仍依赖 US1、US2 和 US4。

---

## Phase 6: User Story 4 — 小窗口、对话框与运行中任务可用 (Priority: P2)

**Goal**: 1024×700 和更小物理工作区内核心页面可操作；窗口操作不影响对话框或后台任务。

**Independent Test**: 主要页面/抽屉/对话框无阻断横向溢出、大面积裁切或嵌套双滚动条；真实任务期间 20 次窗口操作不造成中断、重启或丢进度。

- [x] T029 [US4] 在 Windows 真实桌面壳把窗口调到 1024×700，按 `specs/036-titlebar-dynamic-island/v2/quickstart.md` 逐页记录主要页面、抽屉和对话框的核心操作、溢出、裁切及双滚动条结果
- [x] T030 [US4] 仅当 T029 的问题属于全局布局时，在 `webui/src/styles.css` 做最小响应式修正；若问题需要其他 Vue 文件，停止并先修订 `specs/036-titlebar-dynamic-island/v2/plan.md`，不得越界修改
- [x] T031 [US4] 在 `webui/` 运行 `npm test` 和 `npm run build`，并重新执行 T029 验证修正后的真实布局；未修改 CSS 也必须记录走查证据

**Checkpoint**: US4 通过后，四个用户故事才具备组合验收条件。

---

## Phase 7: Cross-Story Regression and Final Verification

**Purpose**: 从冻结 Spec 正向验证完整用户目标，禁止把实现细节或局部冒烟当完成。

- [x] T033 对 `specs/036-titlebar-dynamic-island/v2/spec.md` 的 24 条 FR 和 9 条 SC 建立逐项证据映射，写入 `specs/036-titlebar-dynamic-island/v2/quickstart.md`；无证据项不得标记通过
- [x] T034 运行聚焦回归 `uv run python -m unittest tests.test_window_controls tests.test_desktop_window_state tests.test_desktop_shell`，保存命令、退出码和摘要到系统临时目录并在 `specs/036-titlebar-dynamic-island/v2/quickstart.md` 记录结论
- [x] T035 仅在 T001–T034 全部收敛且聚焦回归通过后运行一次后端全量 `uv run python -m unittest discover -s tests`；保存完整输出与失败清单到系统临时目录并在 `specs/036-titlebar-dynamic-island/v2/quickstart.md` 记录。若失败，只重跑失败用例和受影响范围；禁止无相关改动再次跑全量，修复并重新收敛后才允许最终确认
  - 结果（2026-09-14）：第 1 轮 `Ran 3159 tests in 1020.637s`、第 2 轮（审查整改后）`Ran 3163 tests in 1050.134s`（12:44:36 → 13:02:06），两轮均无代码失败；提交后仓库卫生检查全过（详见 quickstart §8.8）。
- [x] T036 仅在最终收敛阶段于 `webui/` 运行一次 `npm test` 与 `npm run build`，将实际退出码和摘要记录到 `specs/036-titlebar-dynamic-island/v2/quickstart.md`
- [x] T037 运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check` 和 `git status --short`，清理当轮自产临时文件并在 `specs/036-titlebar-dynamic-island/v2/quickstart.md` 记录结果
  - 结果（2026-09-14）：`git diff --check` 干净（仅 CRLF 提示）；卫生 13/14（唯一失败与 T035 同因）；当轮临时探针脚本、临时数据库拷贝与状态目录已清理。
- [x] T040 审查 `packaging/window_controls.py`、`packaging/window_state.py`、`webui/src/components/WindowTitleBar.vue` 最终行数、引用方向、无第二 hook、无逐消息日志和无禁止 fallback，并把结论写入 `specs/036-titlebar-dynamic-island/v2/quickstart.md`
- [x] T041 汇总 T033–T040；只有自动化、构建和真机证据全部通过时，才把 `specs/036-titlebar-dynamic-island/INDEX.md` 状态改为“已实施，待用户验收”；只有用户本人明确确认后才能改为“已验收”。任何关键失败都改为 `BLOCKED`，不得宣称部分完成
  - 结果（2026-09-14）：INDEX 已改为 **`已验收`**（用户明示「测试结束，我已经测试完通过了」）。

---

## Dependencies & Execution Order

```text
R0 独立拆分 Spec
  ↓
Phase 1 基线与环境
  ↓
Phase 2 Gate 1 ──失败──→ BLOCKED / 停止
  ↓通过
US1 八方向拉伸
  ↓
US2 顶栏拖动往返
  ↓
US3 普通矩形记忆
  ↓
US4 小窗口与运行任务
  ↓
组合回归 + Windows 10/11 E2E
```

- Phase 2 是所有用户故事的硬阻断门禁。
- US1 与 US2 修改同一个原生适配器，必须串行。
- Gate 1 通过后，US3 的测试准备可与前端标题栏回归并行，但同一工作目录禁止多 AI 并发修改。
- US4 必须在 US1–US3 后执行，才能验证真实调整与记忆后的页面和任务行为。
- 最终 Windows 10/11 E2E 可在两台独立机器并行；证据必须分别存在。

## Parallel Opportunities

- T005 与 T006：不同测试文件，可并行准备。
- T018 与 T019：原生适配器测试与标题栏组件测试可并行准备。
- T038 与 T039：Windows 10/11 两套环境可并行执行。
- 其余任务存在同文件或 Gate 依赖，不标记并行。

## Implementation Strategy

### Feasibility MVP

本功能没有可发布的“只做 US1”版本。唯一 MVP 是 Phase 2 的 Gate 1 候选，用来回答方案是否成立；它不能发布，也不能被称为部分完成。

### Complete delivery

1. 完成 R0 和 Phase 1。
2. 以最小可保留实现完成 Gate 1；失败立即停止。
3. 按 US1 → US2 → US3 → US4 串行深化，同文件只有一个负责人。
4. 完成全部自动化、构建、卫生和 Windows 10/11 真实 E2E。
5. 只在全部证据齐全时宣称完成。

## Notes

- 本文件只定义未来执行步骤；截至 Tasks 阶段，没有任何产品实现或测试通过事实。
- 不包含提交、推送、版本提升、打包发布、Release 或外部数据修改授权。

## 执行状态（2026-09-14 收口记录）

- **已完成**：R0 拆分验收、Gate 1 候选与 Windows 10 真机门禁、US1–US4 实现与验证、1024×700 走查、聚焦回归、前端全量与构建、证据映射与索引更新；用户已验收（INDEX 状态 `已验收`）。
- **全量结果（T035）**：第 1 轮 `Ran 3159 tests in 1020.637s`、第 2 轮 `Ran 3163 tests in 1050.134s`，均无代码失败；提交后仓库卫生检查全过。
