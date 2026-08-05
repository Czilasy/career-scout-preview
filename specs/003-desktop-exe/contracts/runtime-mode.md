# 契约：运行时模式与环境检查适配（runtime-mode）

**所属**：specs/003-desktop-exe

**状态**：冻结（2026-08-06）。实现必须遵循本契约；发现契约冲突回到主会话统一修订。

## 1. 运行时模式（RUNTIME_MODE）

`webui/app.py` 的 `create_app(config)` 新增可配置项：

| 键 | 类型 | 默认值 | 语义 |
|---|---|---|---|
| `RUNTIME_MODE` | str | `"source"` | `"source"` = 源码模式（`python webui/app.py`）；`"exe"` = 桌面 EXE 模式（运行时内嵌，无外部 Python/Node） |

- 源码模式启动路径（`if __name__ == "__main__"`）不设置该键，保持默认 `"source"`，行为零变化。
- EXE 模式由桌面壳（desktop.py）以 `create_app(config={"RUNTIME_MODE": "exe", ...})` 注入。
- `RUNTIME_MODE` 的判定必须集中在一个小型模块（如 `webui/desktop_runtime.py`），禁止在 app.py 内散落 `sys.frozen` 判断。

### 1.1 EXE 运行时判定（内部事实）

| 判定 | 方式 | 用途 |
|---|---|---|
| 是否运行于冻结环境 | `getattr(sys, "frozen", False)`（PyInstaller 设置） | `"exe"` 模式探测；实现时以 `RUNTIME_MODE` 配置为准，`sys.frozen` 仅作兜底 |
| 资源根 | `sys._MEIPASS`（冻结）或 `PROJECT_ROOT`（源码） | 前端 dist / 城市码表定位 |

## 2. 环境检查适配（/api/env-check）

### 2.1 响应新增字段

```jsonc
{
  "ok": true,
  "runtime_mode": "source" | "exe",   // 新增
  "groups": [ /* 结构不变 */ ],
  "active_account": "...",
  "cooldowns": [],
  "checked_at": 123
}
```

- 前端按 `runtime_mode` 渲染差异文案；不新增独立接口。
- 既有字段顺序与结构不得变化（前端既有测试依赖）。

### 2.2 EXE 模式检查项差异（仅 `runtime_mode == "exe"`）

| 组 | 项 id | 差异 |
|---|---|---|
| local | `deps` | 名称改「内置运行时」；状态恒 `ok`；detail 文案「Python 运行时与依赖已内置，无需安装」；`fix` 为 `null` |
| local | `webview2` | **新增项**（仅 EXE 模式返回；源码模式不存在该项）：检查 WebView2 运行时是否安装（注册表检测，见 §3）；失败时 `fix` 文案含「安装 WebView2 运行时」 |

其余检查项（browsers / cdp / boss_login / ai_key / data_dir / webui_dist）行为与源码模式完全一致。

### 2.3 源码模式

不返回 `webview2` 项；`deps` 保持现有行为；`runtime_mode` 为 `"source"`。前端无需改动源码模式渲染路径。

## 3. WebView2 检测（注册表）

依据微软官方文档（research.md §3.2），检查以下两个注册表位置的 `pv (REG_SZ)`（64 位 Windows）：

```
HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}
HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}
```

规则：**任一位置存在且版本字符串解析后 > 0.0.0.0** → 已安装。

实现约束：

- 检测函数必须可单测：允许注入注册表读取器（`winreg.OpenKey` 默认实现 / 测试替身）；无 `winreg` 平台（非 Windows）返回「不可用」而非抛异常。
- 只读注册表，不写入、不修改。
- 失败文案面向普通用户：「未检测到 WebView2 运行时，请安装 Microsoft Edge WebView2（下载地址见环境检查提示）」。

## 4. 前端契约（EnvCheckDialog.vue）

- 从 `/api/env-check` 响应读取 `runtime_mode`；仅用于展示差异文案，不改变检查流程。
- `webview2` 项按通用 CheckItem 渲染（✅/❌ + fix 按钮逻辑复用现有 `fixAction`，fix 文案含「安装 WebView2 运行时」时不生成按钮——与现有「仅两类修复动作生成按钮」策略一致，或按实现会话决定，但不得引入新修复动作）。

## 5. 非目标

- 本契约不涉及窗口壳、端口、单实例（见 desktop-shell.md）。
- 不改变源码模式的任何检查项语义。