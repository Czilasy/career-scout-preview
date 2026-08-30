<#
.SYNOPSIS
  发布安装包到自建更新镜像（国内可达静态分发）。

.DESCRIPTION
  把 .release/ 内指定版本的 EXE/DMG 及 .sha256 上传到镜像服务器，
  并逐平台调用服务器端部署账号 home 下的 update_manifest.py 合并
  manifest 条目（应用内更新检测优先读 manifest，GitHub 兜底）。
  本地有的平台才上传；EXE 本地构建必有，DMG 云端构建通常没有。
  服务器地址与部署账号经参数显式传入（FR-005：不写入公开仓库）。
  用法：pwsh scripts/publish_mirror.ps1 -Version 1.8.3 -Host_ <镜像地址> -User <部署账号>
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Host_,
    [Parameter(Mandatory = $true)][string]$User,
    [string]$KeyPath = "$env:USERPROFILE\.ssh\career_scout_server",
    [string]$RemoteDir = "/var/www/career-scout",
    [string]$ManifestScript = "~/update_manifest.py"
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

scp -i $KeyPath @uploads "${User}@${Host_}:$RemoteDir/"
if ($LASTEXITCODE -ne 0) { Write-Error '安装包上传失败' }

foreach ($e in $entries) {
    ssh -i $KeyPath "${User}@${Host_}" "python3 $ManifestScript '$Version' '$($e.platform)' '$($e.name)' '$($e.sha256)' $($e.size)" | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "manifest 合并失败（$($e.platform)）" }
}
Write-Host "镜像已更新：http://${Host_}/manifest.json（$($entries.platform -join '、')）"
