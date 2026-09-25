# Implementation Plan: 关闭确认自绘化与运行中关闭收尾

**Branch**: `codex/fix/unfinished-flow-recovery` | **Date**: 2026-09-25 | **Spec**: [spec.md](./spec.md)（045 v2）

**Input**: `spec.md`、`research.md`、`045` v1 全部历史工件、`.specify/memory/constitution.md`

## Summary

045 v1 已完成「未完成流程落轮进历史 + 关闭前保存」，但关闭确认用的是系统原生弹窗，且只覆盖了未运行场景。本轮做两件事：

1. 关闭确认改为**应用内自绘**，复用既有对话框基座，天然跟随 boss/智联 × 明暗四套主题；
2. 补上**正在运行中关闭**的场景，收尾复用既有的「暂停 → 落轮」路线，只给「立即结束」一个动作键。

技术上最关键的一点：桌面壳的 `closing` 事件是同步的，而自绘弹窗是前端异步交互。按 research D1，**改为两段式**——`closing` 立即返回 False 取消本次关闭并通知前端弹框，用户点击后结果经 pywebview 的 Promise 回调回到壳侧，再执行收尾并关窗。

## Technical Context

**Language/Version**: Python 3.11（后端与桌面壳）、TypeScript / Vue 3（前端）

**Primary Dependencies**: Flask（本地 HTTP）、pywebview 6.2.1（桌面壳，WinForms / macOS 原生）、Vue 3 + Vite

**Storage**: SQLite（`scrape_run_jobs`、`screening_runs`），本轮**不新增表、不改迁移**

**Testing**: `unittest`（后端与壳侧）、Vitest（前端）；真实界面走查不可被自动化替代

**Target Platform**: Windows 桌面 EXE（主）、macOS 桌面壳（次）、浏览器（明确不做本功能）

**Project Type**: desktop-app + 本地 web

**Constraints**: 桌面壳不得阻塞 UI 线程；不得新增独立存储 / 状态机 / 页面 / 提醒体系

**Scale/Scope**: 单用户桌面应用；本轮改动面集中在关闭链路

## Constitution Check

*GATE: 设计前通过，Phase 1 设计后复核。*

- **原则 I 职责分层**：前端关闭确认编排落入独立 composable，壳侧关闭生命周期落入 `desktop_close.py`；判定与呈现分离。✓
- **原则 II 尺寸边界**：`desktop.py` 935 行（已超 800 红线）。本轮对它是**只减不增**——删除 tkinter / osascript 原生弹窗实现，改为调用 `desktop_close`；不追加任何新逻辑。`desktop_close.py` 70 行，承载新逻辑后预计 ~270 行，远低于红线。✓
- **原则 III 引用方向**：壳侧 `desktop.py → desktop_close.py → HTTP API`；前端 `host 组件 → composable → apiRequest`。无反向依赖。✓
- **原则 IV 拆分与重构纪律**：本轮是功能开发，不是重构。不为降低 `desktop.py` 行数做拆分（拆分须单独立项）；仅删除其中的原生弹窗代码使其净减行。✓
- **原则 V 验证门禁**：按聚焦测试执行，交付链收敛后一次全量。✓
- **原则 VI 落位规则**：`App.vue`（945 行）、`DiscoveryView.vue`（1202 行）、`useDiscoveryExecution.ts`（1332 行）均已过预警线或红线，本轮**一律不碰**；关闭确认作为新域新建模块，并登记进 constitution 模块地图。✓

## File Boundaries

- **Allowed files**（可修改）：
  - `packaging/desktop_close.py` — 关闭生命周期域：场景判定、两段式编排、关闭守卫、收尾顺序、前端不可用回退
  - `packaging/desktop.py` — **仅限**：删除 `_win_task_dialog_confirm` 与 `_default_messagebox` 的原生弹窗分支；把 `_on_closing` / `_quit_and_cleanup` 改为调用 `desktop_close`；更新重启路径改为静默退出。不新增任何逻辑
  - `webui/running_task_api.py` — 仅在运行中分支补齐岗位数字段（research D5）
  - `webui/src/components/PauseBatchChoiceDialog.vue` — 新增 `kind="close"`
  - `webui/src/main.ts` — 挂载关闭确认宿主
  - `tests/test_desktop_shell.py` — 同步调整 v1 关闭判定用例
  - `webui/src/components/__tests__/PauseBatchChoiceDialog.spec.ts` — 补 `kind="close"` 用例
  - `.specify/memory/constitution.md` — 登记新模块
  - `CHANGELOG.md` — 用户可感知改动

- **Forbidden files**（禁止修改）：
  - `webui/app.py`、`webui/store.py`、`webui/task_continue_api.py`（暂停与落轮接口已存在，只调用不修改）
  - `webui/src/App.vue`（945 行，过预警线）、`webui/src/views/DiscoveryView.vue`（1202 行，超红线）、`webui/src/composables/useDiscoveryExecution.ts`（1332 行）
  - 平台 / 抓取模块、`scripts/**`、数据库迁移、正式数据目录

- **New files**：
  - `webui/src/composables/useCloseConfirm.ts`（约 180 行）— 关闭确认编排：暴露给壳的 Promise 入口、场景文案、等待反馈、防重入
  - `webui/src/components/CloseConfirmHost.vue`（约 70 行）— 薄宿主：使用该 composable 并渲染确认框；由 `main.ts` 独立挂载
  - `tests/test_desktop_close_bridge.py`（约 160 行）— 壳侧两段式、守卫、收尾顺序、回退的聚焦测试
  - `webui/src/composables/__tests__/useCloseConfirm.spec.ts`（约 120 行）— 前端编排聚焦测试
  - `specs/045-unfinished-flow-recovery/v2/contracts/close-confirm-bridge.md` — 壳 ↔ 前端契约

- **Reference direction**：`desktop.py → desktop_close.py → HTTP API`；`CloseConfirmHost.vue → useCloseConfirm.ts → apiRequest`；壳到前端只经 pywebview 内置的 `evaluate_js` 通道，不构成代码层反向依赖。

- **Line gate**：
  - `packaging/desktop_close.py` ≤ 320 行（红线 800）
  - `packaging/desktop.py` 净减行，不得增长
  - `webui/running_task_api.py` ≤ 620 行
  - `PauseBatchChoiceDialog.vue` ≤ 200 行
  - 新 composable ≤ 220 行，新宿主组件 ≤ 100 行

- **Rationale**：`desktop.py` 已超红线、三个前端文件已过预警线，因此新逻辑必须分流到独立模块而不是就地追加；确认框复用既有 `BaseDialog` 基座以零成本获得主题跟随，不另造弹窗体系；`main.ts` 独立挂载第二个 Vue 实例是为了完全不触碰 `App.vue`（`BaseDialog` 已验证自包含，可独立挂载）。

## Design

### 关闭链路（两段式）

1. `closing` 事件触发 → 壳侧守卫已置位则直接放行（research D2，避免 `destroy()` 二次触发弹框）。
2. 否则查询最新未完成流程：
   - 查询失败 → 记录日志并放行关闭（不阻塞用户）。
   - 无流程或岗位数 0 → 保存窗口状态、取消任务、放行（FR-013）。
   - 有流程且岗位数 > 0 → 判定场景：运行中 → `running_stop`；未运行 → `idle_save`。
3. 调 `evaluate_js` 触发前端弹框并**立即返回 False** 取消本次关闭。
4. 前端按场景渲染：
   - `idle_save`：标题「结束并保存结果」，动作键「结束并保存」，取消为右上 ✕ / Esc。
   - `running_stop`：标题「还有任务正在进行中」，说明行「立即结束只保存已抓到的，这一批会丢弃；不着急可以先暂停，落盘后再关闭」，动作键「立即结束」，无等待类选项。
5. 用户点击 → Promise resolve `{ action: "confirm" | "cancel" }` → 壳侧 callback。
6. `cancel` → 什么都不做，保持现场（FR-011）。
7. `confirm` → 转后台线程：运行中先 `pause(mode="immediate")` 再 `finish`，未运行直接 `finish` → 置守卫 → `destroy()` 关窗。
8. 等待期间动作键置灰并显示等待指示，防重入（FR-012）。

### 前端不可用回退

`evaluate_js` 抛错或前端未挂载 → 不弹框、直接放行关闭并写桌面日志。最坏情况退化为 v1 行为（轮落 paused、下次可接回），既不丢数据也不卡住用户（research D6）。

### 更新重启

`quit_app` 路径跳过弹框，直接执行静默收尾与退出（FR-017）。

## Verification Gate

- 开发、调试和返修只运行聚焦测试、原失败用例与直接受影响的必要相邻回归；禁止每次修改机械运行后端全量。
- 聚焦范围：`tests/test_desktop_close_bridge.py`、`tests/test_desktop_shell.py` 关闭相关用例、`tests/test_run_lifecycle.py` 相邻回归、`PauseBatchChoiceDialog` 与 `useCloseConfirm` 前端用例。
- 整条交付链收敛后最终门禁一次：后端全量、前端测试、`npm run build`、仓库卫生检查、`git diff --check`、`git status --short`。
- 全量失败只按失败清单做聚焦返修，无相关改动禁止重跑全量。
- 真实界面走查（不可自动化替代）：未运行保存关闭、未运行取消、运行中立即结束、运行中取消四条路径，外加四种主题组合下的视觉一致性，逐条记录实际现象 / 期望现象 / 是否一致。

## Project Structure

```text
packaging/
  desktop.py                        # 仅删除原生弹窗 + 改为调用 desktop_close
  desktop_close.py                  # 关闭生命周期域（场景判定 / 两段式 / 守卫 / 收尾）

webui/
  running_task_api.py               # 运行中分支补岗位数字段

webui/src/
  main.ts                           # 独立挂载关闭确认宿主
  composables/useCloseConfirm.ts    # 新增：关闭确认编排
  components/CloseConfirmHost.vue   # 新增：薄宿主
  components/PauseBatchChoiceDialog.vue  # 新增 kind="close"

tests/
  test_desktop_close_bridge.py      # 新增：壳侧聚焦测试
  test_desktop_shell.py             # 调整 v1 关闭判定用例

specs/045-unfinished-flow-recovery/v2/
  contracts/close-confirm-bridge.md # 新增：壳 ↔ 前端契约
```

**Structure Decision**: 沿用仓库既有结构，不新增顶层目录。前端关闭确认作为独立域挂载在 `main.ts`，不进入任何既有大文件。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 修改已超红线的 `packaging/desktop.py`（935 行） | `closing` 事件的接线点唯一在此，无法绕开 | 不修改则关闭链路无法切换；但本轮对它**只删不增**（删除原生弹窗实现并改为调用 `desktop_close`），净行数下降，不构成"向超大文件追加新逻辑" |
