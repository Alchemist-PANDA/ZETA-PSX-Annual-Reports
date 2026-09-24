# ZETA Pakistan — Bootstrap Script
# Safe to re-run. Detects, creates, validates the entire Pakistan harvest environment.
# Usage: .\scripts\bootstrap_pakistan.ps1

$ErrorActionPreference = "Stop"

Write-Host "`n=== ZETA Pakistan Bootstrap ===" -ForegroundColor Cyan

# 1. Detect Google Drive
$driveRoot = $null
foreach ($letter in @('G','H','I','D','E','F')) {
    $candidate = "${letter}:\My Drive"
    if (Test-Path $candidate) {
        $driveRoot = $candidate
        break
    }
}
if (-not $driveRoot) {
    Write-Host "FAIL: Google Drive not detected." -ForegroundColor Red
    exit 1
}
Write-Host "Google Drive: $driveRoot" -ForegroundColor Green

# 2. Pakistan stock root
$pakRoot = Join-Path $driveRoot "Pakistan stock"
$dirs = @(
    "$pakRoot\_SYSTEM",
    "$pakRoot\_SYSTEM\source",
    "$pakRoot\_SYSTEM\baseline",
    "$pakRoot\_SYSTEM\config",
    "$pakRoot\_SYSTEM\state-backups",
    "$pakRoot\_SYSTEM\installers",
    "$pakRoot\_SYSTEM\source-profiles",
    "$pakRoot\_SYSTEM\benchmarks",
    "$pakRoot\_MANIFESTS",
    "$pakRoot\_AUDITS",
    "$pakRoot\_LOGS",
    "$pakRoot\_FAILURES"
)
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path $d -ErrorAction SilentlyContinue | Out-Null
}
Write-Host "Pakistan stock root: $pakRoot" -ForegroundColor Green

# 3. Local runtime
$localRuntime = "E:\ZETA-PSX-RUNTIME"
if (-not (Test-Path "E:\")) {
    $localRuntime = "$env:LOCALAPPDATA\ZETA-PSX-RUNTIME"
}
New-Item -ItemType Directory -Force -Path $localRuntime -ErrorAction SilentlyContinue | Out-Null
New-Item -ItemType Directory -Force -Path "$localRuntime\local" -ErrorAction SilentlyContinue | Out-Null
Write-Host "Local runtime: $localRuntime" -ForegroundColor Green

# 4. Python check
$pyVersion = py --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: Python not found." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $pyVersion" -ForegroundColor Green

# 5. Git check
$gitVersion = git --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: Git not found." -ForegroundColor Red
    exit 1
}
Write-Host "Git: $gitVersion" -ForegroundColor Green

# 6. Venv
$repoDir = "$localRuntime\ZETA-Pakistan-Annual-Reports"
$venvDir = "$localRuntime\.venv"
if (-not (Test-Path "$venvDir\Scripts\python.exe")) {
    Write-Host "Creating venv..."
    py -m venv $venvDir
}
Write-Host "Venv: $venvDir" -ForegroundColor Green

# 7. Install dependencies
Write-Host "Installing/updating dependencies..."
& "$venvDir\Scripts\python.exe" -m pip install --upgrade pip --quiet 2>&1 | Out-Null
if (Test-Path "$repoDir\pyproject.toml") {
    Push-Location $repoDir
    & "$venvDir\Scripts\python.exe" -m pip install -e ".[test]" --quiet 2>&1 | Out-Null
    Pop-Location
    Write-Host "Dependencies installed." -ForegroundColor Green
} else {
    Write-Host "WARN: Repository not found at $repoDir" -ForegroundColor Yellow
}

# 8. Browser check
$chrome = Get-Command chrome -ErrorAction SilentlyContinue
$edge = Get-Command msedge -ErrorAction SilentlyContinue
if ($chrome -or $edge) {
    Write-Host "Browser: available (optional)" -ForegroundColor Green
} else {
    Write-Host "Browser: not found (optional, needed for JS-only sites)" -ForegroundColor Yellow
}

# 9. Run tests
if (Test-Path "$repoDir\pyproject.toml") {
    Write-Host "`nRunning tests..."
    Push-Location $repoDir
    & "$venvDir\Scripts\python.exe" -m pytest -q 2>&1
    Pop-Location
}

# 10. Environment summary
Write-Host "`n=== Environment Summary ===" -ForegroundColor Cyan
Write-Host "  Google Drive:   $driveRoot"
Write-Host "  Pakistan Root:  $pakRoot"
Write-Host "  Local Runtime:  $localRuntime"
Write-Host "  Python:         $pyVersion"
Write-Host "  Git:            $gitVersion"
Write-Host "  Venv:           $venvDir"
Write-Host "`n=== Bootstrap Complete ===" -ForegroundColor Green
