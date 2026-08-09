# Career Scout 桌面版打包（Windows EXE / macOS DMG）

本目录存放 Career Scout 桌面版的打包配置。
源码模式（`python webui/app.py`）不受影响，桌面版是新增的发行路径。
本目录是打包发布操作手册；发布卫生规则以根目录 `AGENTS.md` 为准。

用户首次运行指引（Chrome/Edge、WebView2、首次启动解压延迟、macOS Gatekeeper、数据目录与常见排错）以根目录 `README.md`「桌面版」为准，本手册不重复维护用户指引。

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
- **打包依赖**：`uv pip install pyinstaller "pywebview>=6.0"`
  （窗口状态记忆与关闭时任务取消依赖 pywebview 6.x 的事件 API）

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
uv pip install pyinstaller "pywebview>=6.0"
```

pywebview 必须 ≥ 6.0（窗口事件 API `window.events.closing` 自 6.0 引入；
旧版本下窗口状态记忆与关闭时任务取消会静默失效，desktop.log 会有提示）。

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
   - 上传 `CareerScout-v{version}.exe.sha256`（构建脚本自动生成；
     **应用内更新强制依赖该文件，缺失时用户端会拒绝自动安装**）
   - 上传 SHA256 校验值（粘贴到发布说明或单独 `.sha256` 文件）
4. **发布说明**：按 `.github/release-template.md` 模板填写，版本号与 `pyproject.toml` 一致；模板覆盖安装包、SHA256、前置条件、已知限制与常见排错入口，发布时从模板带入，不在本手册重复维护；格式按 `AGENTS.md`「文档卫生」简单列表。
5. **发布**：确认无误后点击 "Publish release"。
6. **macOS DMG 自动挂接**：推同一个 `v{version}` tag 后，
   `.github/workflows/release-macos.yml` 在 GitHub 的 Mac runner 上构建
   dmg，自动附加到该 tag 的 Release（已有则附加，没有则创建）。
   CI 会自检 dmg（挂载验证 .app/架构/完整性）并自动生成上传
   `.sha256` 校验文件。
   构建失败时在 Actions 页看日志修复后重推 tag（或手动 workflow_dispatch）。

## 应用内更新（v2.5.0 起）

桌面版内置检查更新：启动时实时查 GitHub latest release（更新检查缓存已关闭），
发现新版顶栏提示 → 应用内下载（进度条 + SHA256 强制校验）→
点「立即重启完成更新」自动替换并拉起新版（quitAndInstall 模式）。

发布约束：

- Release 必须附各产物的 `.sha256` 文件，否则用户端拒绝自动安装
  （降级为引导浏览器下载）；
- 源码模式（`python webui/app.py`）不提供应用内更新提示；更新代码请用 `git pull`，顶栏 GitHub 链接保留。

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

macOS 首次打开被 Gatekeeper 拦截时的处理方式、前置条件与数据目录，见根目录 `README.md`「桌面版」章节；本手册只保留构建与发布所需信息。

## 免责声明

本项目为学习研究用途的开源工具，EXE/DMG 仅为构建产物，不改变开源性质。
打包配置（壳源码、spec、构建脚本、CI 工作流、本文档）全部入库，
任何人可自行构建验证。
