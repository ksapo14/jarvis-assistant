[CmdletBinding()]
param(
    [ValidateSet("nsis", "msi", "all")]
    [string]$Target = "all"
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
    throw "Python environment missing. Run .\scripts\setup.ps1 first."
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Rust/Cargo is required to build the Windows installer. Install Rust with rustup."
}
Import-MsvcDeveloperEnvironment

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

function Test-PackagedBackend([string]$Executable) {
    $SmokeRoot = Join-Path $Root "services\assistant\build\packaged-smoke-$PID"
    New-Item -ItemType Directory -Path $SmokeRoot -Force | Out-Null
    $ReadyFile = Join-Path $SmokeRoot "ready.json"
    $Token = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    $Nonce = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    $Names = @(
        "ASSISTANT_ENV",
        "ASSISTANT_SESSION_TOKEN",
        "ASSISTANT_HOST",
        "ASSISTANT_PORT",
        "ASSISTANT_DATA_DIR",
        "ASSISTANT_READY_FILE",
        "ASSISTANT_READY_NONCE"
    )
    $Previous = @{}
    foreach ($Name in $Names) {
        $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    }
    $Process = $null
    $RuntimeProcessId = $null
    try {
        $env:ASSISTANT_ENV = "mock"
        $env:ASSISTANT_SESSION_TOKEN = $Token
        $env:ASSISTANT_HOST = "127.0.0.1"
        $env:ASSISTANT_PORT = "0"
        $env:ASSISTANT_DATA_DIR = $SmokeRoot
        $env:ASSISTANT_READY_FILE = $ReadyFile
        $env:ASSISTANT_READY_NONCE = $Nonce
        $Process = Start-Process -FilePath $Executable -PassThru -WindowStyle Hidden

        $Deadline = [DateTime]::UtcNow.AddSeconds(60)
        while (-not (Test-Path -LiteralPath $ReadyFile)) {
            $Process.Refresh()
            if ($Process.HasExited) {
                throw "Packaged backend exited before readiness with code $($Process.ExitCode)."
            }
            if ([DateTime]::UtcNow -ge $Deadline) {
                throw "Packaged backend did not publish readiness within 60 seconds."
            }
            Start-Sleep -Milliseconds 100
        }

        $Ready = Get-Content -LiteralPath $ReadyFile -Raw | ConvertFrom-Json
        # Development can run directly in the spawned process; PyInstaller
        # one-file runs Python as its child. Reject any other process lineage.
        $LineageMatches = (
            [int]$Ready.pid -eq $Process.Id -or
            [int]$Ready.parent_pid -eq $Process.Id
        )
        if ($Ready.nonce -cne $Nonce -or -not $LineageMatches -or [int]$Ready.port -lt 1024) {
            throw "Packaged backend returned an invalid readiness response."
        }
        $RuntimeProcessId = [int]$Ready.pid
        Remove-Item -LiteralPath $ReadyFile -Force
        $BaseUrl = "http://127.0.0.1:$([int]$Ready.port)"
        $Headers = @{ "X-Assistant-Token" = $Token }
        $Health = Invoke-RestMethod -Uri "$BaseUrl/v1/health" -Headers $Headers -TimeoutSec 5
        if ($Health.status -notin @("ok", "degraded")) {
            throw "Packaged backend health check returned an unexpected state."
        }
        $Unauthorized = Invoke-WebRequest -Uri "$BaseUrl/v1/health" -SkipHttpErrorCheck -TimeoutSec 5
        if ([int]$Unauthorized.StatusCode -ne 401) {
            throw "Packaged backend accepted a request without its session token."
        }
        $Shutdown = Invoke-RestMethod `
            -Method Post `
            -Uri "$BaseUrl/v1/shutdown" `
            -Headers $Headers `
            -ContentType "application/json" `
            -Body "{}" `
            -TimeoutSec 5
        if (-not $Shutdown.shutting_down) {
            throw "Packaged backend did not accept graceful shutdown."
        }
        if (-not $Process.WaitForExit(10000)) {
            throw "Packaged backend did not exit after graceful shutdown."
        }
        if ($Process.ExitCode -ne 0) {
            throw "Packaged backend exited with code $($Process.ExitCode)."
        }
    }
    finally {
        if ($RuntimeProcessId -and $RuntimeProcessId -ne $Process.Id) {
            $RuntimeProcess = Get-Process -Id $RuntimeProcessId -ErrorAction SilentlyContinue
            if ($RuntimeProcess -and -not $RuntimeProcess.HasExited) {
                Stop-Process -Id $RuntimeProcessId -Force
                $RuntimeProcess.WaitForExit()
            }
        }
        if ($Process) {
            $Process.Refresh()
            if (-not $Process.HasExited) {
                Stop-Process -Id $Process.Id -Force
                $Process.WaitForExit()
            }
        }
        foreach ($Name in $Names) {
            [Environment]::SetEnvironmentVariable($Name, $Previous[$Name], "Process")
        }
    }
}

Push-Location $Root
try {
    Write-Host "==> Ensuring production backend dependencies are installed" -ForegroundColor Cyan
    & $Python -m pip install --quiet -e ".\services\assistant[windows,wake,secrets]"
    Assert-NativeSuccess "Installing production backend dependencies"
    & $Python -m pip install --quiet pyinstaller
    Assert-NativeSuccess "Ensuring PyInstaller is installed"

    Write-Host "==> Running release checks" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "test.ps1") -RequireRust

    Write-Host "`n==> Packaging the authenticated Python backend" -ForegroundColor Cyan
    & $Python -m PyInstaller --log-level WARN --noconfirm --clean `
        --distpath (Join-Path $Root "services\assistant\dist") `
        --workpath (Join-Path $Root "services\assistant\build") `
        (Join-Path $PSScriptRoot "jarvis-assistant.spec")
    Assert-NativeSuccess "Packaging the Python backend"
    $BuiltBackend = Join-Path $Root "services\assistant\dist\jarvis-assistant.exe"
    if (-not (Test-Path -LiteralPath $BuiltBackend)) {
        throw "PyInstaller did not produce $BuiltBackend."
    }
    $ArchiveEntries = & $Python -m PyInstaller.utils.cliutils.archive_viewer -l $BuiltBackend 2>&1
    Assert-NativeSuccess "Inspecting the packaged backend"
    $BundledModels = @($ArchiveEntries | Select-String -Pattern '(?i)\.(onnx|tflite)(?:\W|$)')
    if ($BundledModels.Count -gt 0) {
        throw "The backend archive contains model assets. Wake and voice models must remain external: $($BundledModels -join '; ')"
    }
    Write-Host "`n==> Exercising the packaged backend through the Rust host handshake" -ForegroundColor Cyan
    $PreviousPackagedTestPath = [Environment]::GetEnvironmentVariable(
        "JARVIS_PACKAGED_BACKEND_TEST_PATH",
        "Process"
    )
    try {
        $env:JARVIS_PACKAGED_BACKEND_TEST_PATH = $BuiltBackend
        & cargo test `
            --manifest-path (Join-Path $Root "apps\desktop\src-tauri\Cargo.toml") `
            packaged_backend_handshake_matches_host_contract `
            -- `
            --ignored `
            --nocapture
        Assert-NativeSuccess "Testing the packaged backend through the Rust handshake"
    }
    finally {
        [Environment]::SetEnvironmentVariable(
            "JARVIS_PACKAGED_BACKEND_TEST_PATH",
            $PreviousPackagedTestPath,
            "Process"
        )
    }
    Write-Host "`n==> Smoke-testing the packaged backend" -ForegroundColor Cyan
    Test-PackagedBackend $BuiltBackend

    $HostLine = (& rustc -vV | Select-String '^host:').Line
    if (-not $HostLine) { throw "Could not determine the Rust host target." }
    $HostTarget = $HostLine.Substring(5).Trim()
    $BinaryDirectory = Join-Path $Root "apps\desktop\src-tauri\binaries"
    New-Item -ItemType Directory -Path $BinaryDirectory -Force | Out-Null
    $Sidecar = Join-Path $BinaryDirectory "jarvis-assistant-$HostTarget.exe"
    Copy-Item -LiteralPath $BuiltBackend -Destination $Sidecar -Force

    Write-Host "`n==> Building Tauri installers" -ForegroundColor Cyan
    if ($Target -eq "all") {
        & npm --workspace "@jarvis/desktop" run tauri:build:production
    }
    else {
        & npm --workspace "@jarvis/desktop" run tauri:build:production -- --bundles $Target
    }
    Assert-NativeSuccess "Building Tauri installers"

    Write-Host "`nInstallers are under apps\desktop\src-tauri\target\release\bundle\." -ForegroundColor Green
}
finally {
    Pop-Location
}
