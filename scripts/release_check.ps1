<#
.SYNOPSIS
  Release closure verification for Career Scout.

.DESCRIPTION
  Runs only hygiene/diff/sync/artifact checks. It deliberately does NOT run full
  backend or frontend test suites. Release closure tasks must use this script
  instead of inventing a verification matrix.

.PARAMETER Version
  Expected product version, e.g. 1.7.10.

.PARAMETER RequireArtifact
  Fail if .release/CareerScout-v<Version>.exe and .sha256 do not exist.
#>
param(
    [string]$Version = "",
    [switch]$RequireArtifact
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $root

Write-Host "==> Release closure checks (no full test suite)"
Write-Host "Root: $root"

if ($Version -ne "") {
    Write-Host "==> Version consistency: $Version"
    & uv run python scripts/bump_version.py --check --expect $Version
    if ($LASTEXITCODE -ne 0) { throw "Version consistency check failed for $Version" }
} else {
    Write-Host "==> Version consistency: current files"
    & uv run python scripts/bump_version.py --check
    if ($LASTEXITCODE -ne 0) { throw "Version consistency check failed" }
}

Write-Host "==> Frontend dist sync"
& uv run python webui/ensure_frontend_sync.py --check
if ($LASTEXITCODE -ne 0) { throw "Frontend dist is out of sync" }

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
