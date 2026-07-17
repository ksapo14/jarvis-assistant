[CmdletBinding()]
param(
    [switch]$MockOnly,
    [switch]$SkipNode
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $true
}
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "windows-toolchain.ps1")
Enable-RustToolchainPath

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Require-Command([string]$Name, [string]$InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $InstallHint"
    }
}

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

Push-Location $Root
try {
    Write-Step "Checking supported Windows development tools"
    if (-not $IsWindows) {
        throw "JARVIS desktop targets Windows 10/11. Run setup.ps1 from Windows PowerShell 7+."
    }
    Require-Command "python" "Install Python 3.11 or newer from https://www.python.org/downloads/windows/."
    $VersionText = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $Parts = $VersionText.Split(".")
    if ([int]$Parts[0] -lt 3 -or ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -lt 11)) {
        throw "Python 3.11+ is required; found $VersionText."
    }

    Write-Step "Creating the Python virtual environment"
    if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
        & python -m venv .venv
        Assert-NativeSuccess "Creating the Python virtual environment"
    }
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    & $Python -m pip install --upgrade pip wheel
    Assert-NativeSuccess "Upgrading pip and wheel"
    if ($MockOnly) {
        & $Python -m pip install -e ".\services\assistant[dev]"
        Assert-NativeSuccess "Installing the mock backend"
    }
    else {
        & $Python -m pip install -e ".\services\assistant[dev,windows,wake,secrets]"
        Assert-NativeSuccess "Installing the Windows backend"
    }

    if (-not $SkipNode) {
        Write-Step "Installing desktop dependencies"
        Require-Command "node" "Install Node.js 20 LTS or newer from https://nodejs.org/."
        Require-Command "npm" "Install npm with Node.js."
        & npm install
        Assert-NativeSuccess "Installing desktop dependencies"
    }

    if (-not (Test-Path -LiteralPath ".env")) {
        Copy-Item -LiteralPath ".env.example" -Destination ".env"
        Write-Host "Created .env from .env.example. Add provider keys before using live mode." -ForegroundColor Yellow
    }

    Write-Step "Checking Tauri prerequisites"
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        Write-Warning "Rust/Cargo is missing. Install it with rustup, then reopen the terminal: https://rustup.rs/"
    }
    else {
        Import-MsvcDeveloperEnvironment
        & cargo --version
        Assert-NativeSuccess "Checking Cargo"
    }
    Write-Host "Tauri also needs Microsoft C++ Build Tools (Desktop development with C++) and WebView2." -ForegroundColor Gray

    Write-Host "`nSetup complete." -ForegroundColor Green
    Write-Host "Mock desktop: .\scripts\dev.ps1 -Mock"
    Write-Host "Tests:        .\scripts\test.ps1"
}
finally {
    Pop-Location
}
