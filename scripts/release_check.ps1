<#
.SYNOPSIS
  Release closure verification for Career Scout.

.DESCRIPTION
  Runs only hygiene/diff/rebuild/artifact checks. It deliberately does NOT run full
  backend or frontend test suites. Release closure tasks must use this script
  instead of inventing a verification matrix.

.PARAMETER Version
  Expected product version, e.g. 1.7.10.

.PARAMETER RequireArtifact
  Fail if .release/CareerScout-v<Version>.exe and .sha256 do not exist.
#>
param(
    [string]$Version = "",
    [switch]$RequireArtifact,
    [switch]$SkipTagCheck
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $root

Write-Host "==> Release closure checks (no full test suite)"
Write-Host "Root: $root"

# 版本-标签纪律（FR-025）：CHANGELOG 最新版本必须已打 v* 标签，
# 防止"先发版后打标"脱节导致应用内更新与 Release 资产错位。
if ($SkipTagCheck) {
    Write-Host "==> Tag discipline: SKIPPED (-SkipTagCheck 显式豁免；发布完成前请补打标签)"
} else {
    Write-Host "==> Tag discipline: CHANGELOG latest vs git tags"
    $latest = $null
    foreach ($line in Get-Content -LiteralPath (Join-Path $root "CHANGELOG.md")) {
        if ($line -match '^## \[(\d+\.\d+\.\d+)\]') { $latest = $Matches[1]; break }
    }
    if (-not $latest) { throw "CHANGELOG 中找不到版本标题（## [x.y.z]）" }
    & git rev-parse --verify --quiet "refs/tags/v$latest" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "版本与标签脱节：CHANGELOG 最新版本 $latest 缺少对应标签 v$latest"
    }
    Write-Host "==> Tag discipline: v$latest OK"
}

if ($Version -ne "") {
    Write-Host "==> Version consistency: $Version"
    & uv run python scripts/bump_version.py --check --expect $Version
    if ($LASTEXITCODE -ne 0) { throw "Version consistency check failed for $Version" }
} else {
    Write-Host "==> Version consistency: current files"
    & uv run python scripts/bump_version.py --check
    if ($LASTEXITCODE -ne 0) { throw "Version consistency check failed" }
}

Write-Host "==> Frontend rebuild"
& uv run python webui/ensure_frontend_sync.py
if ($LASTEXITCODE -ne 0) { throw "Frontend rebuild failed" }

Write-Host "==> Repo hygiene"
& uv run python -m unittest tests.test_repo_hygiene
if ($LASTEXITCODE -ne 0) { throw "Repo hygiene check failed" }

Write-Host "==> Whitespace and worktree"
& git diff --check
if ($LASTEXITCODE -ne 0) { throw "git diff --check failed" }
& git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw "git diff --cached --check failed" }
& git status --short

if ($RequireArtifact -and $Version -ne "") {
    $exe = Join-Path $root ".release\CareerScout-v$Version.exe"
    $sha = "$exe.sha256"
    if (-not (Test-Path -LiteralPath $exe)) { throw "Missing release artifact: $exe" }
    if (-not (Test-Path -LiteralPath $sha)) { throw "Missing release checksum: $sha" }
    Write-Host "==> Artifacts present: $exe"
}

Write-Host "==> Release closure checks passed"
