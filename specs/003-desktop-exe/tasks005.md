# Task 005：桌面壳

**所属 Wave**：2（并行） | **硬前置**：Task 002 完成（运行时模块与 create_app config 契约） | **用户故事**：EXE1（下载即用）、EXE3（窗口体验）、EXE5（零回归）

## 必读文件

- 仓库根 `AGENTS.md`
- `specs/003-desktop-exe/contracts/desktop-shell.md`（冻结合同）
- pywebview 官方 API（`create_window` / `min_size` / `events.closing`；版本 6.2.1，见 research.md）
- Task 002 产出的 `webui/desktop_runtime.py`

## 写入范围（互斥）

`packaging/desktop.py`（新增）、`packaging/desktop_tests/` 或 `tests/` 新增测试文件（命名自定）。**禁止**修改 `webui/`、`scripts/`；`packaging/` 下其它文件（spec/构建脚本）归 Task 006。

## 原子清单

- [ ] T035 [P] 记录壳契约的接线点：单实例、随机端口、Flask 线程、就绪轮询、窗口参数、closing 生命周期、错误路径（只读）
- [ ] T036 添加**先失败**单实例测试：mutex 已存在时提示并退出（mutex 工厂可注入替身）；锁在 Flask 启动前获取
- [ ] T037 添加窗口状态文件测试：写入/读取、非法值（非数字/超界）回退默认、位置越出显示器工作区回退居中；文件位置 `~/.career-scout/desktop_window.json`（目录可注入，测试不写真实用户目录）
- [ ] T038 添加错误路径测试：端口选择失败、后端就绪超时、WebView2 初始化异常 → 明确提示（MessageBox 可注入/替身断言）+ 非零退出码；`desktop.log` 记录
- [ ] T039 实现 `packaging/desktop.py`：
  - 单实例（named mutex，`ctypes`；已存在 → 提示 + 退出 0）
  - `pick_free_port`（复用 desktop_runtime）→ Flask 线程 `create_app(config={"RUNTIME_MODE": "exe", "PYTHON_EXECUTABLE": sys.executable, "START_TASKS": True})`、`use_reloader=False`
  - 轮询 `/api/session` 就绪（超时 ≤30s → 错误退出）
  - pywebview 窗口：标题 `Career Scout v{version}`（从 pyproject.toml 读版本）、默认 1280×800、`min_size=(1024, 700)`、恢复窗口状态
  - `events.closing`：保存窗口状态 → 取消抓取（既有 TaskRunner 取消语义）→ `webview.start()` 返回后 `os._exit(0)` 兜底
  - 全部失败路径：MessageBox + `~/.career-scout/desktop.log` 追加记录 + 非零退出
- [ ] T040 运行聚焦测试，提交：仅 `packaging/desktop.py` 及其测试，信息 `feat: add desktop shell`

## 完成定义

纯逻辑聚焦测试全绿（窗口交互留 Task 007 真实验收）；源码模式不受影响（未改任何既有文件）；import pywebview 失败时有明确错误路径。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。