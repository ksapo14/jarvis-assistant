[CmdletBinding()]
param(
    [switch]$RequireRust,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $true
}
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
. (Join-Path $PSScriptRoot "windows-toolchain.ps1")
Enable-RustToolchainPath

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment missing. Run .\scripts\setup.ps1 -MockOnly first."
}

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

Push-Location $Root
try {
    Write-Host "==> Ruff" -ForegroundColor Cyan
    & $Python -m ruff check services/assistant/src services/assistant/tests
    Assert-NativeSuccess "Ruff"
    & $Python -m ruff format --check services/assistant/src services/assistant/tests
    Assert-NativeSuccess "Ruff formatting"

    Write-Host "`n==> Pytest" -ForegroundColor Cyan
    $PytestTempRoot = Join-Path $Root ".test-tmp"
    New-Item -ItemType Directory -Path $PytestTempRoot -Force | Out-Null
    # A unique directory avoids stale Windows ACL/reparse-point artifacts from a prior run.
    $PytestTemp = Join-Path $PytestTempRoot "pytest-$PID"
    & $Python -m pytest services/assistant/tests --basetemp $PytestTemp
    Assert-NativeSuccess "Pytest"

    Write-Host "`n==> Frontend lint" -ForegroundColor Cyan
    & npm run desktop:lint
    Assert-NativeSuccess "Frontend lint"

    Write-Host "`n==> Frontend formatting" -ForegroundColor Cyan
    & npm --workspace "@jarvis/desktop" run format
    Assert-NativeSuccess "Frontend formatting"

    Write-Host "`n==> Frontend tests" -ForegroundColor Cyan
    & npm run desktop:test
    Assert-NativeSuccess "Frontend tests"

    if (-not $SkipBuild) {
        Write-Host "`n==> Frontend production build" -ForegroundColor Cyan
        & npm run desktop:build
        Assert-NativeSuccess "Frontend production build"
    }

    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        Import-MsvcDeveloperEnvironment
        Write-Host "`n==> Rust formatting and checks" -ForegroundColor Cyan
        & cargo fmt --manifest-path apps/desktop/src-tauri/Cargo.toml -- --check
        Assert-NativeSuccess "Rust formatting"
        & cargo check --locked --manifest-path apps/desktop/src-tauri/Cargo.toml
        Assert-NativeSuccess "Tauri host check"
    }
    elseif ($RequireRust) {
        throw "Rust/Cargo is required for this test run but was not found."
    }
    else {
        Write-Warning "Skipping cargo fmt/check because Rust is not installed."
    }

    Write-Host "`nAll available checks passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
