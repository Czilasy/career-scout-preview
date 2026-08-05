# Task 002：桌面运行时模块

**所属 Wave**：1（并行） | **用户故事**：EXE2（环境就绪检查与引导）、EXE5（源码模式零回归）

## 必读文件

- 仓库根 `AGENTS.md`
- `specs/003-desktop-exe/contracts/runtime-mode.md`（冻结合同）
- `webui/app.py` 的 `create_app` config 注入方式与 `env_check()`（只读）、`webui/constants.py`（只读）

## 写入范围（互斥）

`webui/desktop_runtime.py`（新增）、`tests/test_desktop_runtime.py`（新增）。**禁止**修改 `webui/app.py`、其它既有文件。

## 原子清单

- [ ] T011 [P] 记录 `create_app(config)` 的 config 键约定与 `env_check` 响应契约，确定本模块的调用面（不修改 app.py）
- [ ] T012 在 `tests/test_desktop_runtime.py` 添加**先失败** `runtime_mode` 判定测试：config 显式值优先；无 config 时 `sys.frozen` 兜底；源码环境默认 `"source"`
- [ ] T013 添加 WebView2 注册表检测测试（注入注册表读取器替身）：`HKLM\SOFTWARE\WOW6432Node\...` 与 `HKCU\...` 任一存在且版本 > 0.0.0.0 为已装；两个位置缺失/版本无效为未装；非 Windows（无 `winreg`）返回不可用而不抛异常；只读不写
- [ ] T014 添加随机端口测试：`pick_free_port()` 返回可绑定端口、重复调用可得到不同端口（不保证必不同，验证可绑定且是回环地址）
- [ ] T015 实现 `webui/desktop_runtime.py`：`runtime_mode(config)`、`check_webview2(reg_reader=None)`、`pick_free_port()`；纯函数、无副作用、可注入
- [ ] T016 运行聚焦测试，提交：仅 `webui/desktop_runtime.py`、`tests/test_desktop_runtime.py`，信息 `feat: add desktop runtime detection`

## 完成定义

聚焦测试全绿；无平台分支泄漏（非 Windows 安全退化已验证）；不 import pywebview（本模块不做窗口）。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。