<#
.SYNOPSIS
  发布安装包到自建更新镜像（国内可达静态分发）。

.DESCRIPTION
  把 .release/ 内指定版本的 EXE/DMG 及 .sha256 上传到镜像服务器，
  并逐平台调用服务器端 /root/update_manifest.py 合并 manifest 条目
  （应用内更新检测优先读 manifest，GitHub 兜底）。
  本地有的平台才上传；EXE 本地构建必有，DMG 云端构建通常没有。
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

$uploads = @()
$entries = @()
foreach ($platform in @(
    @{ key = 'win';  pattern = "CareerScout-v$Version.exe" },
    @{ key = 'mac';  pattern = "CareerScout-v$Version.dmg" })) {
    $pkg = Join-Path $releaseDir $platform.pattern
    if (-not (Test-Path $pkg)) {
        if ($platform.key -eq 'win') { Write-Error "找不到 $pkg，请先构建" }
        continue  # dmg 在 macOS 工作流构建，本机没有则跳过
    }
    $uploads += $pkg
    $shaFile = "$pkg.sha256"
    if (Test-Path $shaFile) { $uploads += $shaFile }
    $entries += @{
        platform = $platform.key
        name     = Split-Path $pkg -Leaf
        sha256   = (Get-FileHash $pkg -Algorithm SHA256).Hash.ToLower()
        size     = (Get-Item $pkg).Length
    }
}
if (-not $entries) { Write-Error '没有任何可上传的安装包，中止' }

scp -i $KeyPath @uploads "root@${Host_}:$RemoteDir/"
if ($LASTEXITCODE -ne 0) { Write-Error '安装包上传失败' }

foreach ($e in $entries) {
    ssh -i $KeyPath "root@${Host_}" "python3 /root/update_manifest.py '$Version' '$($e.platform)' '$($e.name)' '$($e.sha256)' $($e.size)" | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "manifest 合并失败（$($e.platform)）" }
}
Write-Host "镜像已更新：http://${Host_}/manifest.json（$($entries.platform -join '、')）"
