<#
.SYNOPSIS
  Career Scout EXE 构建脚本（spec003 tasks006 T043）。

.DESCRIPTION
  前置校验前端 dist 与打包依赖，调用 PyInstaller 按 packaging/career_scout.spec
  构建 onefile EXE，产物重命名为 .release/CareerScout-v{version}.exe。
  任一步失败非零退出；成功输出产物绝对路径。

  在项目根目录执行：pwsh packaging/build_exe.ps1
#>

$ErrorActionPreference = 'Stop'

# 项目根（脚本在 packaging/ 下，父目录即项目根）
$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -Path $ProjectRoot

# ---------------------------------------------------------------------------
# 1. 从 pyproject.toml 读版本
# ---------------------------------------------------------------------------
$versionMatch = Select-String -Path 'pyproject.toml' -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    Write-Error 'pyproject.toml 未找到 version 字段'
    exit 1
}
$version = $versionMatch.Matches[0].Groups[1].Value
Write-Host "Career Scout 版本：$version"

# ---------------------------------------------------------------------------
# 2. 前端构建产物校验（FR-016：前端未构建则失败并明确提示）
# ---------------------------------------------------------------------------
if (-not (Test-Path 'webui/dist/index.html')) {
    Write-Host 'webui/dist/index.html 缺失，执行前端构建...'
    Push-Location 'webui'
    try {
        Write-Host '> npm ci'
        & npm ci
        if ($LASTEXITCODE -ne 0) { Write-Error 'npm ci 失败'; exit $LASTEXITCODE }
        Write-Host '> npm run build'
        & npm run build
        if ($LASTEXITCODE -ne 0) { Write-Error 'npm run build 失败'; exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }
    if (-not (Test-Path 'webui/dist/index.html')) {
        Write-Error '前端构建后仍无 webui/dist/index.html'
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 3. 打包依赖校验（FR-016：依赖缺失明确提示）
# ---------------------------------------------------------------------------
& uv run python -c 'import PyInstaller, webview'
if ($LASTEXITCODE -ne 0) {
    Write-Error '打包依赖缺失，请先安装：uv pip install pyinstaller pywebview'
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# 4. PyInstaller 构建
# ---------------------------------------------------------------------------
# 在临时目录执行：项目根 packaging/ 目录（本项目的桌面壳包）会遮蔽 PyPI
# 的 packaging 库（PyInstaller 依赖其 packaging.requirements），在项目根
# cwd 下 import 直接 ModuleNotFoundError。spec 内路径全部为绝对路径
# （PROJECT_ROOT 由 SPECPATH 推导），产物经 --distpath/--workpath 指回。
$pyiWork = Join-Path $env:TEMP 'career-scout-pyi'
New-Item -ItemType Directory -Path $pyiWork -Force | Out-Null
$pyiExe = Join-Path $ProjectRoot '.venv\Scripts\pyinstaller.exe'
Write-Host '> pyinstaller packaging/career_scout.spec --noconfirm (cwd: temp)'
Push-Location $pyiWork
try {
    & $pyiExe (Join-Path $ProjectRoot 'packaging\career_scout.spec') --noconfirm `
        --distpath (Join-Path $ProjectRoot 'dist') --workpath (Join-Path $pyiWork 'build')
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'PyInstaller 构建失败'
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path 'dist/CareerScout.exe')) {
    Write-Error '构建完成但未找到 dist/CareerScout.exe'
    exit 1
}

# ---------------------------------------------------------------------------
# 5. 产物重命名到 .release/（避免与 webui/dist 混淆）
# ---------------------------------------------------------------------------
if (-not (Test-Path '.release')) {
    New-Item -ItemType Directory -Path '.release' | Out-Null
}
$releaseName = "CareerScout-v$version.exe"
$releasePath = Join-Path $ProjectRoot ".release\$releaseName"
Move-Item -Path 'dist/CareerScout.exe' -Destination $releasePath -Force

# ---------------------------------------------------------------------------
# 6. 生成 SHA256 校验文件（应用内更新强制依赖；随 Release 一起上传）
# ---------------------------------------------------------------------------
$hash = (Get-FileHash $releasePath -Algorithm SHA256).Hash.ToLower()
$shaPath = "$releasePath.sha256"
Set-Content -Path $shaPath -Value "$hash  $releaseName" -Encoding ascii -NoNewline
Write-Host "SHA256：$hash"

# 清理 PyInstaller 中间目录（build/ 与 dist/ 均已被 .gitignore 忽略，清理保持工作区干净）
if (Test-Path 'dist') {
    Remove-Item -Recurse -Force 'dist' -ErrorAction SilentlyContinue
}
if (Test-Path 'build') {
    Remove-Item -Recurse -Force 'build' -ErrorAction SilentlyContinue
}

Write-Host "构建成功：$releasePath"
Write-Output $releasePath
