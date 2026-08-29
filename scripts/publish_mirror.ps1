<#
.SYNOPSIS
  发布安装包到自建更新镜像（国内可达静态分发）。

.DESCRIPTION
  把 .release/ 内指定版本的 EXE/DMG 及 .sha256 上传到镜像服务器，
  并重新生成 manifest.json（应用内更新检测优先读它，GitHub 兜底）。
  用法：pwsh scripts/publish_mirror.ps1 -Version 1.8.1
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Host_ = "49.232.60.135",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\career_scout_server",
    [string]$RemoteDir = "/var/www/career-scout"
)
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$releaseDir = Join-Path $ProjectRoot '.release'

$entries = @{}
$uploads = @()
foreach ($platform in @(
    @{ key = 'win';  pattern = "CareerScout-v$Version.exe" },
    @{ key = 'mac';  pattern = "CareerScout-v$Version.dmg" })) {
    $pkg = Join-Path $releaseDir $platform.pattern
    if (-not (Test-Path $pkg)) {
        if ($platform.key -eq 'win') { Write-Error "找不到 $pkg，请先构建" }
        continue  # dmg 在 macOS 工作流构建，本机没有则跳过
    }
    $hashLine = (Get-FileHash $pkg -Algorithm SHA256).Hash.ToLower()
    $name = Split-Path $pkg -Leaf
    $entries[$platform.key] = @{ name = $name; size = (Get-Item $pkg).Length; sha256 = $hashLine }
    $uploads += $pkg
    $shaFile = "$pkg.sha256"
    if (Test-Path $shaFile) { $uploads += $shaFile }
}

# 经临时清单文件 scp 上传（scp 不支持 stdin 管道内容作为远端文件）
$manifestPath = Join-Path $env:TEMP "manifest-$Version.json"
@{ latest = $Version; released = (Get-Date -Format 'yyyy-MM-dd'); files = $entries } |
    ConvertTo-Json -Depth 5 | Set-Content $manifestPath -Encoding utf8NoBOM

scp -i $KeyPath @uploads "root@${Host_}:$RemoteDir/"
if ($LASTEXITCODE -ne 0) { Write-Error '安装包上传失败' }
scp -i $KeyPath $manifestPath "root@${Host_}:$RemoteDir/manifest.json"
Remove-Item $manifestPath

Write-Host "镜像已更新：http://${Host_}/manifest.json"
