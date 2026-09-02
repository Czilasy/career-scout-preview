# Tasks: 桌面壳自绘标题栏 + 顶栏胶囊灵动岛

**Input**: Design documents from `/specs/036-titlebar-dynamic-island/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/

**Tests**: 本 Spec 验证门禁含聚焦测试与全量门禁（spec Verification Scope），故各 Story 含测试任务。

**Organization**: 按 User Story 分组，可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: US1 = B084 自绘标题栏；US2 = B088 灵动岛
- 含确切文件路径

## File Boundaries

解析自 plan.md，每个任务只碰允许文件：

- **Allowed files**: `packaging/desktop.py`、`packaging/window_controls.py`（新）、`webui/src/App.vue`、`webui/src/components/WindowTitleBar.vue`（新）、`webui/src/components/DynamicIsland.vue`（新）、`webui/src/components/__tests__/WindowTitleBar.spec.ts`（新）、`webui/src/components/__tests__/DynamicIsland.spec.ts`（新）、`webui/src/composables/useDiscoveryTasks.ts`、`webui/src/composables/useDiscoveryResults.ts`、`webui/src/composables/useDiscoveryState.ts`、`webui/src/api.ts`、`tests/test_desktop_shell.py`、`tests/test_desktop_shell_wiring.py`、`tests/test_desktop_window_state.py`、`tests/test_window_controls.py`（新）、`webui/src/**/__tests__`（既有测试更新）
- **Forbidden files**: `webui/src/views/DiscoveryView.vue`（1249 行超限）、`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`webui/historical_recovery.py`、数据库与迁移、`roadmap/`、`.codebuddy/`
- **New files**: `packaging/window_controls.py`（窗口控制 Win32 助手，~100 行）、`webui/src/components/WindowTitleBar.vue`（自绘标题栏，~120 行）、`webui/src/components/DynamicIsland.vue`（灵动岛，~250 行）、`tests/test_window_controls.py`、两个组件测试
- **Reference direction**: 前端 `view → component → composable → api client`；`desktop.py → window_controls.py`；组件只消费上抛状态
- **Line gate**: `packaging/desktop.py` 净增后 ≤750；`webui/src/App.vue` 净增后 ≤1100

## Verification Gate (task-type aware)

- 功能交付最终门禁：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- Windows 真实 EXE 端到端真跑在交付后由用户执行（见 quickstart.md）。
- 不涉及版本提升/打包/发布。

---

## Phase 1: Setup & Foundational (阻塞前置)

**Purpose**: 无边框窗口控制域（US1 前置）+ 胶囊数据契约（US2 前置）

- [x] T001 真机验证 pywebview frameless 行为（Windows）：`frameless=True` + `easy_drag=True` 下最大化是否天然避让任务栏、拖拽与双击行为，把结论写入 `packaging/window_controls.py` 与 `specs/036-titlebar-dynamic-island/research.md`（决定最大化避让是否需 Win32 处理；阻塞 T002）
- [x] T002 [P] 新建 `packaging/window_controls.py`：窗口控制原语（minimize/maximize/restore、最大化避让任务栏适配位），仅依赖注入的 window 对象，纯逻辑可单测
- [x] T003 [P] 新建 `tests/test_window_controls.py`：窗口控制聚焦测试（mock window 对象验证各原语调用与错误路径）

**Checkpoint**: 窗口控制域就绪；US1 可开工

---

## Phase 2: User Story 1 - B084 桌面壳自绘标题栏 (P1) 🎯 MVP

**Goal**: Windows 桌面版无系统标题栏，页面顶部自绘标题栏（拖拽/双击/三按钮/跟随主题），窗口记忆不回归

**Independent Test**: `uv run python -m unittest tests.test_desktop_shell tests.test_desktop_shell_wiring tests.test_desktop_window_state tests.test_window_controls` + `cd webui && npx vitest run src/components/__tests__/WindowTitleBar.spec.ts src/__tests__/App.spec.ts`；Windows 真机 EXE 走查

### Implementation for User Story 1

- [x] T004 [P] [US1] `packaging/desktop.py`：`create_window` 加 `frameless=True`（仅 Windows）+ `easy_drag=True`；`DesktopJsApi` 新增 `window_minimize()`/`window_toggle_maximize()`/`window_close()`（调用 `window_controls.py` 原语与既有优雅退出），净增 ≤150 行
- [x] T005 [P] [US1] 新建 `webui/src/components/WindowTitleBar.vue`：自绘标题栏（左侧 `Career Scout` 文字、右侧最小化/最大化还原/关闭三按钮、标题栏空白区加 `pywebview-drag-region` 类拖拽区、双击显式绑定最大化、主题跟随：浅色白/暗色暗/特殊主题透明、特殊主题下半透明磨砂按钮 + X 悬停红底 + 其余悬停线条变深），仅 `window.pywebview` 存在时渲染，~120 行
- [x] T006 [P] [US1] 新建 `webui/src/components/__tests__/WindowTitleBar.spec.ts`：渲染条件（有/无 pywebview）、三按钮点击触发正确 js_api 调用、主题类名随主题变化
- [x] T007 [US1] `webui/src/App.vue`：挂载 `WindowTitleBar`（页面最顶部，仅桌面版），现有业务顶栏下移，净增 ≤30 行
- [x] T008 [P] [US1] `tests/test_desktop_shell.py` / `tests/test_desktop_shell_wiring.py`：无边框参数断言、js_api 窗口控制方法接线与错误路径测试
- [x] T009 [US1] 回归验证：`tests/test_desktop_window_state.py` 全绿（窗口记忆不回归：位置/大小/最大化/多屏钳制）

**Checkpoint**: B084 完成——无边框标题栏功能可用、窗口记忆不回归

---

## Phase 3: User Story 2 - B088 顶栏胶囊灵动岛 (P1)

**Goal**: 顶栏胶囊升级为常驻灵动岛（空闲/运行中/跑完/需处理四态、优先级取一件、点击直达、动画、提醒按钮通用化）

**Independent Test**: `cd webui && npx vitest run src/components/__tests__/DynamicIsland.spec.ts src/__tests__/App.spec.ts`；Windows 真机 EXE 走查四态与点击

### Implementation for User Story 2

- [x] T010 [P] [US2] `webui/src/composables/useDiscoveryTasks.ts`：将抓取/筛选进度（done/total）并入上抛状态（`round-status` 扩展，无新轮询）
- [x] T011 [P] [US2] `webui/src/composables/useDiscoveryResults.ts`：将结果态（matched/pending）并入上抛状态
- [x] T012 [P] [US2] `webui/src/composables/useDiscoveryState.ts`：胶囊状态派生（四态 + "attention > running > completed > idle" 优先级、空闲平台名默认 boss、total 未知省略分母、pending=0 不显示）
- [x] T013 [P] [US2] 新建 `webui/src/components/DynamicIsland.vue`：胶囊组件（四态渲染、呼吸点、点击派发 home/task/results/attention、动画：数字跳动/展开收缩/醒的过程/待确认标亮、`prefers-reduced-motion` 降级、特殊主题半透明），~250 行
- [x] T014 [P] [US2] 新建 `webui/src/components/__tests__/DynamicIsland.spec.ts`：四态渲染、优先级（多态并存取最高一件）、点击行为、数字变化、减少动态降级
- [x] T015 [US2] `webui/src/App.vue`：将现有 `round-pill` 替换为 `DynamicIsland`，接入上抛状态与点击跳转（依赖 T007 已挂载标题栏；同文件冲突，串行）
- [x] T016 [US2] `webui/src/App.vue` + `webui/src/api.ts`：提醒按钮通用化（显示各类提醒数量：投递/待确认/出错/跑完，数量来自状态层汇总，点击仍打开提醒抽屉）
- [x] T017 [US2] 更新既有前端测试：`webui/src/__tests__/App.spec.ts` 等适配胶囊替换与提醒扩展

**Checkpoint**: B088 完成——四态灵动岛可独立验证

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: 收尾、登记、门禁

- [x] T018 确认宪法模块地图登记：`.specify/memory/constitution.md` 已含 `window_controls.py`/`WindowTitleBar.vue`/`DynamicIsland.vue` 三条目（plan 阶段已登记，核对无遗漏）
- [x] T019 检查 README/文档是否需同步（新增桌面无边框行为说明，若需则更新 `README.md`、`packaging/README.md`）
- [x] T020 全量门禁：后端全量测试（`uv run python -m unittest discover -s tests -t tests`，2808 用例，除卫生测试 1 例未跟踪新文件预期失败外全绿）+ 前端全量（`npx vitest --run`，47 文件 635 用例全绿）+ `npm run build`（vue-tsc + vite 通过）+ 仓库卫生（未提交新文件导致 1 例失败，如实报告）
- [x] T021 交付后用户端到端真跑清单核对：2026-09-02 用户真机走查发现两处缺陷
      并已修复落地——①`desktop.py` 关 `easy_drag`（True→False，全局 mousedown 拖窗
      导致点击卡死）；②`WindowTitleBar.vue` 渲染条件改响应式 + 监听 `pywebviewready`
      补渲染（setup 一次性判断导致标题栏整条不渲染、窗口手柄消失）。聚焦测试已过，
      重新构建 EXE 供用户实测中。

---

## Phase 5: B084 返工（2026-09-02 用户真机反馈，spec 修订后）

**Purpose**: 兑现 Windows 原版窗口基础功能 + 特殊主题大类毛玻璃磨砂

- [x] T022 [US1] 无边框窗口边缘 resize：用户 2026-09-02 确认**取消**（不再实现）。
      窗口固定大小，仅保留全屏/还原切换（frameless 本无系统拉伸边框 + 不实现
      热区 = 事实固定大小，`resizable` 参数在 frameless 下无实际效果）。
      此前方案 A（WS_THICKFRAME 改样式）与方案 B（前端边缘手柄）真机不达预期的
      尝试记录保留在 research.md T022。
- [x] T023 [US1] 最大化/还原切换：真实状态判断**已修复**（T022 事件驱动实时标记
      ——desktop.py 订阅 `events.maximized/restored` → `window_controls.note_maximized`，
      toggle_maximize 不再依赖 `window.maximized` 构造快照；双击/按钮切换可用）。
      按钮图标随状态切换**已补**（FR-004）：`window_controls.is_maximized` +
      js_api `window_is_maximized()` 查询初始态，`toggle_maximize` 返回切换后
      `maximized` 供前端同步；`WindowTitleBar.vue` 单方框 ↔ 重叠方块（Copy）切换。
- [x] T024 [US1] 特殊主题大类窗口控制条毛玻璃磨砂：明/暗主题保持现状；
      特殊主题大类（`data-theme-category="special"`，挂大类标记不绑定具体主题 id，
      新特殊主题自动继承 A9）整条半透明深色毛玻璃磨砂（blur 22px 强模糊）+
      三按钮悬停反馈（X 红底白字通用 / 特殊主题下最小化/最大化悬停变深色）；
      `.window-titlebar` 抬升 z-index 确保 KaleidoField 光场（z-index 0）不遮挡
      （FR-008/FR-009/FR-022）。自动化测试过，磨砂视觉效果待用户真机 EXE 走查。
- [~] T025 [US1] 回归验证：`tests/test_window_controls.py`（72）/`tests/test_desktop_shell.py`
      /`tests/test_desktop_shell_wiring.py`（72 合跑）/`tests/test_desktop_window_state.py`（46）
      /`WindowTitleBar.spec.ts`+`useTheme.spec.ts`（29）/`App.spec.ts`（27）全绿。
      Windows 真机 EXE 走查（最大化-还原往返/图标切换/磨砂/光场遮挡）待用户实测。
- [ ] T026 收口：本阶段与 T021 未提交修复（easy_drag 关闭、pywebviewready 补渲染）
      一并提交并重新构建 EXE 供用户实测（T021 后续交付核对）。已重建 EXE 供本轮
      实测；提交待用户明确授权。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（Setup/Foundational）**: 无依赖，先行；T001 真机验证阻塞 US1 最大化决策
- **Phase 2（US1 B084）**: 依赖 Phase 1（window_controls 域）；MVP
- **Phase 3（US2 B088）**: 依赖 Phase 1（数据契约）；与 US1 的 `App.vue` 改动串行（T015 依赖 T007）
- **Phase 4（Polish）**: 依赖 US1/US2 完成

### User Story Dependencies

- **US1（P1）**: Phase 1 后即可开始，独立可测
- **US2（P1）**: Phase 1 后即可开始；与 US1 共享 `App.vue`（T007 → T015 串行），其余可并行

### Within Each User Story

- 组件/工具先行，接线后行；测试与实现同批交付；聚焦测试通过后再进入下一任务

### Parallel Opportunities

- T002/T003（Phase 1）可并行
- T004/T005/T006/T008（US1 内）可并行（不同文件），T007 依赖 T005/T004 接线
- T010/T011/T012/T013/T014（US2 内）可并行（不同文件），T015/T016/T017 依赖其上抛状态接线

---

## Parallel Example: User Story 1

```bash
# 并行：窗口控制原语 + 标题栏组件 + 各自测试
Task: "T004 desktop.py 无边框接线与 js_api"
Task: "T005 WindowTitleBar.vue 组件"
Task: "T006 WindowTitleBar.spec.ts"
Task: "T008 desktop_shell 测试"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1：window_controls 域（T001 真机验证 → T002/T003）
2. Phase 2：US1（T004→T007→T008/T009）—— 自绘标题栏落地
3. **STOP and VALIDATE**：聚焦测试 + 真机 EXE 走查
4. 继续 Phase 3：US2 灵动岛

### Incremental Delivery

1. Phase 1 → 窗口控制域就绪
2. US1 自绘标题栏 → 测试 → 真机走查（MVP）
3. US2 灵动岛 → 测试 → 真机走查
4. Polish 门禁全绿 → 交付

---

## Notes

- [P] 任务 = 不同文件、无依赖，可并行
- `webui/src/views/DiscoveryView.vue` 一律禁止修改（超限红线）
- 胶囊数据一律经 composables 上抛，组件不自行抓取
- 真机 EXE 验证项最终由用户执行，自动化门禁覆盖单元/组件层
