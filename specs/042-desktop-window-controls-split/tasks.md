# Tasks: 桌面壳原生窗口控制能力拆分（R0）

**Input**: `spec.md`、`plan.md`。本清单是 036 v2 T002 的具体落地步骤，必须先于 036 v2 全部任务完成。

**Delivery rule**: 只搬代码，不改行为。任何一处需要改变既有断言才能通过，即为失败，必须停下来复核方案。

## File Boundaries

- **Allowed product files**: `packaging/window_controls.py`、`packaging/desktop.py`
- **Allowed tests**: `tests/test_window_controls.py`、`tests/test_desktop_shell.py`（仅导入来源与纯逻辑分支覆盖）
- **Allowed documents**: `specs/042-desktop-window-controls-split/`、`.specify/memory/constitution.md` 模块地图
- **Forbidden**: 其它全部产品代码、`packaging/window_state.py`、036 v1 平铺文档
- **Reference direction**: `desktop → window_controls`，不得反向
- **Line gate**: 两文件均低于 800 行；`window_controls.py` 不得越过 600 行预警线

## Phase 1: Setup

- [x] T001 记录拆分基线：`packaging/desktop.py` 与 `packaging/window_controls.py` 行数、三项能力当前位置（句柄定位 / 最大化钳制 / 尺寸修正）与调用点清单
- [x] T002 逐行核读三项实现，确认其中不能被行为保持式迁移改变的分支、异常兜底、日志前缀与返回值

## Phase 2: 迁移（单批次，行为保持）

- [x] T003 在 `packaging/window_controls.py` 迁入句柄定位 `find_main_hwnd_pid()`（原 `_find_main_hwnd_pid`），保留「标题含 Career Scout 优先」规则与纯 Win32 读取
- [x] T004 在 `packaging/window_controls.py` 迁入最大化工作区钳制 `install_maximize_clamp(...)`，工作区改为注入，`[clamp]` 诊断文案与两级 `CallWindowProcW` 转发不变
- [x] T005 在 `packaging/window_controls.py` 迁入无边框尺寸修正 `fix_frameless_size(...)`，DPI 换算与 `GetWindowPlacement`/`SetWindowPlacement` 逻辑与 `[fixsize]` 文案不变
- [x] T006 在 `packaging/window_controls.py` 增加诊断出口 `log_desktop_error()`（延迟 import 桌面日志，失败静默），使模块不反向依赖桌面入口
- [x] T007 在 `packaging/desktop.py` 删除三项实现并改为从 `window_controls` 调用；保留既有私有别名与调用顺序，主编排 `events.shown` 行为不变

## Phase 3: 验证与收口

- [x] T008 运行 `uv run python -m unittest tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state`，确认既有断言全部原样通过
- [x] T009 逐函数等价性复核（分支/兜底/日志前缀/返回值/安装时机），结论写入 `specs/042-desktop-window-controls-split/quickstart.md`
- [x] T010 按统一验证节奏只运行仓库卫生检查、`git diff --check` 与 `git status --short` 并写入 `quickstart.md`；042 作为 036 v2 内部前置不要求单独重跑后端全量
- [x] T011 重新测量两文件行数并登记到 `quickstart.md`；越线即停止并回到 Plan
- [x] T012 更新 `.specify/memory/constitution.md` 模块地图中 `packaging/window_controls.py` 的职责描述
- [x] T013 零引用盘点：确认迁移符号无悬空引用、无孤儿公共符号，并把结论写入 `quickstart.md`

## Dependencies & Execution Order

```text
T001 → T002 → T003 → T004 → T005 → T006 → T007
  ↓
T008 → T009 → T011 → T012 → T013 → T010
```

- T003–T005 同文件，必须串行。
- T007 依赖 T003–T006 全部完成（调用点必须在实现就位后再切换）。
- T008 是行为等价的第一道客观证据；失败即回到 T002 复核。

## Notes

- 本清单不包含 036 v2 的实现任务；036 v2 必须在 T008–T013 全部通过后开始。
- 042 与 036 v2 属于同一交付链；后端全量、前端全量与构建只在 036 v2 最终收敛后统一运行。失败排查只跑原失败用例与直接受影响范围，禁止无改动重复全量。
- 不包含提交、推送、版本提升、打包发布授权。
- 不新增测试断言口径；既有测试是拆分等价性的证据来源。
