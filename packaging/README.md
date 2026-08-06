# Career Scout 桌面版打包（Windows EXE / macOS DMG）

本目录存放 Career Scout 桌面版的打包配置。
源码模式（`python webui/app.py`）不受影响，桌面版是新增的发行路径。

## 文件说明

| 文件 | 作用 |
|---|---|
| `desktop.py` | 桌面壳入口（跨平台：pywebview + Flask + 单实例 + 窗口记忆） |
| `career_scout.spec` | Windows PyInstaller 打包清单 |
| `build_exe.ps1` | Windows 一键构建脚本 |
| `career_scout_macos.spec` | macOS PyInstaller 打包清单（.app） |
| `build_dmg.sh` | macOS 一键构建脚本（.app → .dmg） |

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

`.release/CareerScout-v{version}.exe`（如 `CareerScout-v2.4.0.exe`）

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

1. **本地构建 Windows EXE**：执行 `pwsh packaging/build_exe.ps1`，确认 `.release/` 下产物可双击启动。
   **macOS DMG 无需本地构建**：推 tag 后 GitHub Actions 自动构建（见下节）。
2. **计算 SHA256**：

   ```powershell
   Get-FileHash .release/CareerScout-v{version}.exe -Algorithm SHA256
   ```

3. **创建 GitHub Release**：
   - 在仓库 Releases 页点击 "Draft a new release"
   - Tag 填 `v{version}`（如 `v2.4.0`），目标分支选 `main`
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
6. **macOS DMG 自动挂接**：推同一个 `v{version}` tag 后，
   `.github/workflows/release-macos.yml` 在 GitHub 的 Mac runner 上构建
   dmg，自动附加到该 tag 的 Release（已有则附加，没有则创建）。
   构建失败时在 Actions 页看日志修复后重推 tag（或手动 workflow_dispatch）。

## macOS DMG 打包

### 构建方式（二选一）

- **CI 自动构建（推荐，维护者无需 Mac）**：推 `v{version}` tag 即触发，
  产物自动挂到 Release。工作流定义在 `.github/workflows/release-macos.yml`。
- **本地 Mac 构建**：

  ```bash
  bash packaging/build_dmg.sh
  ```

  前置：macOS ≥ 11、Python ≥ 3.10、Node.js ≥ 20、uv。
  脚本自动：读版本 → 构建前端（缺时）→ 安装打包依赖（缺时）→
  PyInstaller 产出 `CareerScout.app` → `hdiutil` 打包为
  `.release/CareerScout-v{version}.dmg`。

### 产物形态

- `.app`（onedir）而非 onefile：macOS GUI 应用不支持 PyInstaller onefile。
- 架构跟随构建机（macos-latest = arm64，覆盖所有 Apple Silicon Mac）。
  PyPI 上部分依赖（如 markupsafe）只有单架构 wheel，无法构建
  universal2；如需 Intel Mac 版，在 x86_64 环境重跑同一脚本即可。

### 用户首次打开（未签名应用）

dmg 未做代码签名与公证（Apple Developer 账号收费，开源工具暂不做）。
首次打开会被 Gatekeeper 拦截，两种解法：

1. 访达中**右键** `CareerScout.app` → 打开 → 弹窗中再点“打开”；
2. 或终端执行：

   ```bash
   xattr -d com.apple.quarantine /Applications/CareerScout.app
   ```

前置条件与 Windows 版一致：装有 Chrome（或 Edge）并完成 BOSS/智联登录；
数据目录 `~/.career-scout` 与源码版互通。

## 免责声明

本项目为学习研究用途的开源工具，EXE/DMG 仅为构建产物，不改变开源性质。
打包配置（壳源码、spec、构建脚本、CI 工作流、本文档）全部入库，
任何人可自行构建验证。
