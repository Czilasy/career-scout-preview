# 调研：Windows 桌面版（EXE 发行形态）

**创建日期**：2026-08-06

**目的**：为 `specs/003-desktop-exe` 的 Plan 阶段提供外部事实依据。所有外部信息均查证自官方资料，标注来源与日期。

## 1. 桌面壳选型：pywebview

### 1.1 版本与许可（来源：pywebview 官网，2026-08 查证）

- 当前版本：**6.2.1**（https://pywebview.flowrl.com/）
- 许可：BSD（与项目 Apache-2.0 兼容，可引用）
- 平台：Windows / macOS / Linux / Android

### 1.2 Windows 后端与渲染（来源：pywebview 官方文档）

- Windows 后端：**WinForms 原生窗口 + WebView2 运行时**
- 渲染内核与 Chrome/Edge 同源（Chromium），与现有前端无渲染差异
- 官方明确声明 **"Bundler friendly"**：可轻松集成 PyInstaller / Nuitka / py2app，冻结时不捆绑重型 GUI 工具包或渲染器，EXE 体积可控

### 1.3 窗口 API（来源：pywebview 官方 API 文档）

`webview.create_window()` 支持本功能所需全部能力：

| 能力 | API | 对应需求 |
|---|---|---|
| 窗口尺寸/位置 | `width` / `height` / `x` / `y` | FR-006 窗口记忆 |
| 可缩放 | `resizable=True` | FR-005 |
| 最小尺寸 | `min_size=(w, h)` | FR-005 |
| 窗口事件 | `events.closing` / `events.closed` | FR-007 关闭终止进程 |
| 标题 | `title` | FR-013 版本标识 |

### 1.4 官方 Flask 集成示例（来源：pywebview 官方仓库）

官方仓库提供 Flask 应用示例（`pywebview/tree/docs/examples/flask_app`）：**Flask 本地服务 + webview 窗口指向 localhost 地址**——与本项目架构（`webui/app.py` Flask + 前端）完全同构，可直接借鉴接线方式。

### 1.5 单实例

**pywebview 不内置单实例**。需自建：Windows named mutex（`CreateMutexW`，可通过 `ctypes` 或 win32 扩展实现）；已有实例时通过窗口查找/消息聚焦或退出提示。实现方案归 Plan 阶段。

## 2. 打包工具：PyInstaller

### 2.1 版本与 Python 兼容（来源：PyInstaller 官方 GitHub README，2026-06 更新）

- 支持 Python **3.8-3.15**
- 注意事项：**Python 3.10.0 首个版本有 bug 不受支持**（需使用 3.10.x 后续版本）
- Windows 支持 32 位 / 64 位 / ARM64

### 2.2 本项目 Python 版本

- 项目约束：`requires-python >= 3.10`（pyproject.toml）
- 本地实测：**Python 3.11.15**（uv 环境）→ PyInstaller 完全支持，无兼容风险

### 2.3 onefile 模式

- `--onefile`：产出单个 EXE，首启需自解压（秒级延迟），符合"下载一个文件双击即用"的产品形态
- `--windowed`（`--noconsole`）：GUI 应用不弹控制台窗口
- 已知外部因素：onefile 产物可能被部分杀软误报，属外部因素，文档说明不处理（spec 边界情况已记录）

### 2.4 产物可解包性

- PyInstaller 产物可被 `pyinstxtractor` 等工具解包提取字节码——本项目源码本身公开（Apache-2.0），无保密诉求，该特性无影响

### 2.5 资源路径语义（PyInstaller 下 `__file__` 变化）

- onefile 模式下代码从 `sys._MEIPASS` 临时目录解压运行，`__file__` 指向临时目录
- 本项目 `webui/app.py` 以 `HERE = Path(__file__).resolve().parent` 定位 `dist/`、以 `PROJECT_ROOT` 定位 `scripts/`、`data/` —— **打包时必须把这些资源作为 data 收集进包，并确保运行时定位逻辑在 EXE 模式下仍正确**（实现细节归 Plan/Tasks）
- 子进程链路（`python.exe scripts/boss_cdp_raw.py`）在 EXE 下不可用 → 对应 spec FR-010 的 in-process 执行路径

## 3. WebView2 运行时（来源：微软官方 Learn 文档，2026-08 查证）

### 3.1 预装情况（官方原文要点）

- **Windows 11：Evergreen WebView2 运行时作为操作系统的一部分包含在内**（预装）
- Windows 10：微软自 2022-12 起通过 Windows Update 向托管设备推送；**绝大多数 Windows 10 设备已安装**；少数设备可能未预装
- 官方建议：应用创建 WebView2 前**必须检查运行时是否存在**

### 3.2 检测方法（官方文档）

检查注册表 `pv (REG_SZ)` 值，64 位 Windows 的两个位置：

```
HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}
HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}
```

规则：至少一个存在且版本 > 0.0.0.0 → 已安装。

→ 该注册表检测直接作为环境检查 FR-009 "WebView2 运行时"检查项的实现依据。

### 3.3 缺失时引导（官方推荐）

- 联机：Evergreen Bootstrapper（约 2MB，静默安装）
- 脱机：独立安装程序
- 本项目不做静默安装（尊重用户），环境检查项给出下载引导即可

### 3.4 生产限制（官方原文）

- 生产应用只能使用 WebView2 运行时作为渲染引擎，**不得依赖 Microsoft Edge 稳定版**——pywebview 使用 WebView2 Runtime，符合官方规范；这也说明"复用本机 Edge"的方案不被微软支持

## 4. 项目现状核对（本地证据）

| 项 | 现状 | EXE 化影响 |
|---|---|---|
| Flask 启动 | `create_app().run(host="127.0.0.1", port=5000)` | 固定端口 → 需随机空闲端口（FR-003） |
| 任务执行 | `TaskRunner` → `ScraperExecutor` spawn `[python.exe, scripts/boss_cdp_raw.py, ...]` | EXE 下无外部 python → in-process 路径（FR-010） |
| Python 解析 | `_resolve_python_executable()` 找 `.venv` | EXE 模式下不适用，需运行时判定 |
| 前端资源 | `FRONTEND_DIST = HERE / "dist"` | 打包为 data，定位逻辑需兼容 `sys._MEIPASS` |
| 数据目录 | `~/.career-scout`（环境变量可覆盖） | 保持不变（FR-012） |
| 环境检查 | `env_check()` 三组检查项 + `EnvCheckDialog.vue` | EXE 模式适配（FR-008/009）：deps 项语义 + WebView2 项 |
| 响应式前端 | 1050/760/640/430px 断点 + 320px 底线 | 窗口缩放自适应（FR-005）已有基础 |
| 前端构建 | `webui/` npm build → `webui/dist` | 构建脚本前置步骤（FR-014/016） |

## 5. 结论与风险

### 5.1 技术选型结论

| 决策点 | 结论 | 依据 |
|---|---|---|
| 桌面壳 | pywebview 6.2.1 | 官方 Windows 支持 + Flask 示例 + 声明支持 PyInstaller |
| 打包 | PyInstaller onefile + windowed | Python 3.11.15 完全支持；onefile 符合产品形态 |
| 渲染 | WebView2 Runtime | Win11 预装；Win10 绝大多数已装；pywebview 默认后端 |
| 单实例/窗口记忆 | 自建（mutex / 本地状态文件） | pywebview 不内置 |

### 5.2 风险登记

| 风险 | 程度 | 缓解 |
|---|---|---|
| PyInstaller 收集 pywebview 原生依赖（WebView2Loader）不完整 | 中 | 官方声明 bundler friendly；打包验证阶段确认，必要时自定义 hook；验证项列入 Tasks |
| onefile 下资源路径（dist/scripts/data）定位错误 | 中 | Plan 阶段冻结资源定位契约；构建后启动验证列为硬性验收 |
| in-process 改造影响现有子进程链路 | 中 | 源码模式链路零改动，in-process 为新增路径；全量回归门禁 |
| WebView2 缺失（少数 Win10） | 低 | 环境检查注册表检测 + 下载引导（官方推荐方案） |
| onefile 杀软误报 | 低（外部） | 文档说明，不处理 |

### 5.3 待验证项（留给 Implement 阶段）

1. pywebview 6.2.1 + PyInstaller 在本项目依赖集下打包成功且窗口可启动
2. onefile 内前端资源路径定位正确（`sys._MEIPASS` 语义）
3. in-process 抓取执行与子进程执行产物/日志/取消行为一致
4. WebView2 注册表检测在真实 Win10/11 的准确性
5. 窗口关闭时进程树完整退出（无孤儿进程）