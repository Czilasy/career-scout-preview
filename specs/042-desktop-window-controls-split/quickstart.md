# Quickstart: 042 拆分验证指南（R0）

本指南用于验证「桌面壳原生窗口控制能力拆分」是否做到只搬代码、不改行为。

## 0. 前置条件

1. 当前分支没有无关改动。
2. 本拆分不要求真实真机验收（真机验收属于 036 v2）；作为 036 v2 的内部前置，本批只执行聚焦测试、等价性复核和仓库卫生检查。

## 1. 聚焦测试

```powershell
uv run python -m unittest tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state
```

既有断言必须原样通过；若需要修改断言才能通过，说明迁移改变了行为，必须回到方案复核。

## 2. 统一全量延后到 036 v2

042 不单独运行后端全量、前端全量或构建；这些只在 036 v2 整条交付链最终收敛后统一执行。本批只运行：

```powershell
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

若聚焦测试失败，只重跑失败用例与直接受影响范围；禁止为了获取失败名称或在没有相关改动时重跑全量。

## 3. 结构检查

```powershell
# 三项能力的定义只应出现在 window_controls.py
Select-String -Path packaging\*.py -Pattern "def find_main_hwnd_pid|def install_maximize_clamp|def fix_frameless_size|SetWindowLongPtrW|WINDOWPLACEMENT|MINMAXINFO"
# 行数
(Get-Content packaging\window_controls.py).Count
(Get-Content packaging\desktop.py).Count
```

## 4. 等价性复核清单

- [x] 句柄定位：`EnumWindows` 回调过滤条件、标题优先规则、无候选返回一致。
- [x] 最大化钳制：`ptMaxSize`/`ptMaxPosition`/`ptMaxTrackSize` 赋值一致；取工作区失败与找不到 HWND 的留痕一致；`SW_RESTORE` → `SW_MAXIMIZE` 重切顺序一致；`GetWindowRect` 读回留痕一致。
- [x] 尺寸修正：`GetDpiForWindow` 换算、`WINDOWPLACEMENT` 结构体字段、已正确时提前返回、按工作区居中、失败静默一致。
- [x] 安装时机：仍在 `events.shown`，调用顺序（钳制 → 尺寸修正）不变。
- [x] 日志前缀与文案前缀（`[clamp]`/`[fixsize]`）不变。

## 5. 记录要求

执行结果（命令、退出码、结论）与行数测量结果写入本文件「执行记录」小节，不得以估算代替。

## 6. 执行记录（2026-09-14 收口复核）

- **聚焦测试**：`uv run python -m unittest tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state` → `Ran 94 tests ... OK`（R0 拆分当期基线；036 v2 之后同命令为 143 用例，全部通过）。
- **等价性复核**：句柄定位（EnumWindows 过滤与标题优先）、最大化钳制（`ptMaxSize`/`ptMaxPosition`/`ptMaxTrackSize` 赋值、重切顺序、读回留痕）、尺寸修正（DPI 换算、`WINDOWPLACEMENT` 字段、已正确时提前返回、按工作区居中）、安装时机（仍在 `events.shown`，钳制 → 尺寸修正顺序不变）、日志前缀（`[clamp]`/`[fixsize]`）逐项核对与迁移前一致；差异仅限函数归属与依赖注入参数。
- **结构检查**：三项能力只在 `packaging/window_controls.py` 定义，`packaging/desktop.py` 只保留调用与兼容别名；引用方向 `desktop → window_controls`，无反向导入（036 v2 新增的窗口交互域只单向依赖本模块）。
- **行数测量**：拆分当期 `window_controls.py` 431 / `desktop.py` 787（拆分前 1019）；036 v2 完成后为 660 / 790，均在 800 红线内（详见 `specs/036-titlebar-dynamic-island/v2/quickstart.md` §8.8）。
- **卫生检查**：`uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status --short` 通过（结果见 036 v2/quickstart §8.8）。
- **说明**：042 作为 036 v2 的内部前置，按计划不单独运行后端全量；整条交付链的最终全量见 036 v2/quickstart §8.8。
