# Career Scout EXE 打包

本目录存放 Career Scout Windows 桌面版（EXE 发行形态）的打包配置。
源码模式（`python webui/app.py`）不受影响，EXE 是新增的发行路径。

## 文件说明

| 文件 | 作用 |
|---|---|
| `desktop.py` | 桌面壳入口（Task 005 产出：pywebview + Flask + 单实例 + 窗口记忆） |
| `career_scout.spec` | PyInstaller 打包清单（Task 006 产出） |
| `build_exe.ps1` | 一键构建脚本（Task 006 产出） |

## 构建前置

- **操作系统**：Windows 10/11（WebView2 运行时预装或可从系统更新获得）
- **Python**：≥ 3.10（本地实测 3.11.15）
- **Node.js**：≥ 20（构建前端）
- **uv**：Python 包管理器
- **打包依赖**：`uv pip install pyinstaller pywebview`

## 构建步骤

在项目根目录执行：

```powershell
pwsh packaging/build_exe.ps1
```

脚本会自动：

1. 从 `pyproject.toml` 读取版本号
2. 校验 `webui/dist/index.html`，缺失则自动 `npm ci` + `npm run build`
3. 校验 `PyInstaller` 与 `pywebview` 可导入
4. 执行 `uv run pyinstaller packaging/career_scout.spec --noconfirm`
5. 将 `dist/CareerScout.exe` 重命名为 `.release/CareerScout-v{version}.exe`
6. 清理 `build/` 与 `dist/` 中间目录

成功后输出产物绝对路径。

## 产物位置

`.release/CareerScout-v{version}.exe`（如 `CareerScout-v2.3.0.exe`）

## 常见排错

### 杀毒软件误报

PyInstaller onefile 产物可能被部分杀软误报，属外部因素，本项目不处理。
如需验证，可自行构建后用 VirusTotal 比对，或改用源码模式运行。

### 缺少打包依赖

运行 `build_exe.ps1` 报 `打包依赖缺失` 时，执行：

```powershell
uv pip install pyinstaller pywebview
```

### WebView2 运行时缺失

Windows 11 预装；少数 Windows 10 设备可能未预装。

检测方法（注册表，`pv (REG_SZ)` 值存在且 > 0.0.0.0 即已安装）：

```
HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}
HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}
```

缺失时下载安装：<https://developer.microsoft.com/microsoft-edge/webview2/>

### 前端未构建

脚本会自动检测并构建前端；若需手动构建：

```powershell
cd webui
npm ci
npm run build
```

### 缺少 hiddenimports

实际构建若报 `ModuleNotFoundError`，在 `career_scout.spec` 的 `hiddenimports`
列表中补上缺失模块名，重新执行构建脚本。

## Release 发布流程

1. **本地构建**：执行 `pwsh packaging/build_exe.ps1`，确认 `.release/` 下产物可双击启动。
2. **计算 SHA256**：

   ```powershell
   Get-FileHash .release/CareerScout-v{version}.exe -Algorithm SHA256
   ```

3. **创建 GitHub Release**：
   - 在仓库 Releases 页点击 "Draft a new release"
   - Tag 填 `v{version}`（如 `v2.3.0`），目标分支选 `main`
   - 上传 `CareerScout-v{version}.exe`
   - 上传 SHA256 校验值（粘贴到发布说明或单独 `.sha256` 文件）
4. **发布说明模板要点**：
   - 版本号与 `pyproject.toml` 一致
   - 安装方式：下载 EXE 双击启动，无需安装依赖
   - 前置：Windows 10/11、Chrome 或 Edge 浏览器并完成 BOSS/智联登录
   - 数据目录：`~/.career-scout`，与源码版互通
   - 已知限制：onefile 首启解压延迟数秒；杀软可能误报
   - 校验：附 SHA256 供下载方核对
5. **发布**：确认无误后点击 "Publish release"。

## 免责声明

本项目为学习研究用途的开源工具，EXE 仅为构建产物，不改变开源性质。
打包配置（壳源码、spec、构建脚本、本文档）全部入库，任何人可自行构建验证。
