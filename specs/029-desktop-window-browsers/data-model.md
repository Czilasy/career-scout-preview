# Phase 1 Data Model: 029 桌面壳窗口记忆修复批

**Date**: 2026-08-29 | 规范来源：[spec.md](./spec.md)、[research.md](./research.md)

## 实体 1：窗口状态记忆文件 `desktop_window.json`（schema 3）

文件契约详见 [contracts/desktop-window-state.md](./contracts/desktop-window-state.md)。

| 字段 | 类型 | 语义 | 校验规则 |
|---|---|---|---|
| `schema` | int | 格式版本 | 必须 = 3；2 按升级规则解释；其他/缺失 → 无记忆 |
| `width`, `height` | int | **普通矩形**尺寸 | `MIN_WIDTH/MIN_HEIGHT(1024/700)` ≤ 值 ≤ `MAX(8192)`；超任一工作区 → 读时钳制 |
| `x`, `y` | int | 普通矩形位置 | 越出全部工作区 → 主工作区居中（读时） |
| `maximized` | bool | 上次关窗是否最大化 | 缺失 → false |
| `default_width`, `default_height` | int | 用户自定义普通默认（覆盖 1545×900） | 同尺寸校验；非法 → 回退常量 |

**状态转移**（运行时内存模型，由 `packaging/window_state.py` 的 WindowStateTracker 承载）：

- `NORMAL(矩形R)` --拖动/缩放(resized/moved)--> `NORMAL(矩形R')`（仅更新内存）
- `NORMAL(R)` --maximized 事件--> `MAXIMIZED`（R 冻结为"最后普通矩形"）
- `MAXIMIZED` --restored 事件--> `NORMAL(R)`（回到冻结矩形）
- 任意态 --closing--> 落盘：MAXIMIZED → `{R, maximized:true}`；NORMAL → `{当前矩形, maximized:false}`
- 启动：读文件 → 无记忆 → `{默认普通矩形, maximized:true}`（首开最大化）；有记忆 → `{R, maximized}` 原样驱动开窗

## 实体 2：浏览器注册表条目（内存常量，`scripts/boss/browser_registry.py`）

```text
BrowserRegistryEntry:
  key: str              # 稳定标识（api/持久化用）：chrome|edge|brave|vivaldi|opera|se360|qqbrowser|quark
  name: str             # 展示名：Chrome / Edge / Brave / Vivaldi / Opera / 360极速 / QQ浏览器 / 夸克
  exe_names: list[str]  # 进程枚举与路径匹配用：["chrome.exe"] 等；360 双 exe
  path_candidates: list[tuple[str, ...]]  # 环境变量名 + 相对段，如 ("PROGRAMFILES","BraveSoftware","Brave-Browser","Application","brave.exe")
  data_dir_key: str     # 数据目录命名空间后缀，如 "brave"
```

校验规则：`key` 全表唯一；`path_candidates` 至少 2 条；`data_dir_key` 全表唯一且 `[a-z0-9-]+`。注册表完整性（8 条齐全、字段合法）由 `tests/test_browser_registry.py` 断言——这是"真机只有 Chrome/Edge"时其余 6 家的兜底验收。

## 实体 3：浏览器选择（实施修订：`~/.career-scout/browser_selection.json`，注册表域自持；原定 advanced_settings.json 新键因键白名单文件不在允许清单内而绕开）

| 键 | 类型 | 语义 | 校验 |
|---|---|---|---|
| `mode` | str | `auto`（缺省，按注册表顺序探测）/ `registry` / `manual` | 其他值视同 auto |
| `key` | str | `mode=registry` 时的注册表 key | 不在注册表 → 视同 auto |
| `manual_path` | str | `mode=manual` 时的可执行文件路径 | 保存时 `--version` 探活通过才允许保存；启动时再经 CDP 内核校验 |

**关系**：选择 → 启动解析（`pipeline_exec_chrome.py`）→ 可执行文件路径；选择 + 账号 → 生效数据目录（D6 派生规则）。

## 实体 4：生效数据目录派生（纯函数，`pipeline_exec_accounts.py`）

```text
effective_data_dir(profile_dir: str, browser_key: str) -> str
  browser_key ∈ {chrome, edge}  → profile_dir 原样（存量兼容，登录态无损）
  其他                          → <profile_dir 父目录>/chrome-profile-<data_dir_key>/<profile_dir 目录名>
```

不变量：函数纯（不触碰文件系统、不做迁移）；同一 `(profile_dir, browser_key)` 恒等映射（切回免重登的保证）；chrome/edge 恒等映射（现状零回归）。

## 接口面（模块级，供 tasks 引用）

- `packaging.window_state.WindowStateTracker`：`on_resized/on_moved/on_maximized/on_restored/snapshot_for_save()`——纯逻辑，事件与写盘解耦（research D1）
- `packaging.window_state.load_window_state(state_dir, workarea_provider) / save_window_state(...) / read_default_size(...)`：沿用 desktop.py 现有函数名（desktop.py re-export 兼容，`tests/test_desktop_shell.py` 旧用例迁移后仍适用）
- `scripts.boss.browser_registry.detect_browsers() -> list[dict]`（探测命中列表）、`resolve_executable(selection) -> str|None`（选择 → exe 路径）、`validate_manual_path(path) -> (ok, message)`（`--version` 探活）、`all_registry_exe_names() -> set[str]`（进程枚举过滤器用）
- HTTP 端点见 [contracts/browser-registry-api.md](./contracts/browser-registry-api.md)
