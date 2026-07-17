[CmdletBinding()]
param(
    [switch]$Mock,
    [switch]$WebOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $true
}
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$BackendExecutable = Join-Path $Root ".venv\Scripts\jarvis-assistant.exe"

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    foreach ($Line in Get-Content -LiteralPath $Path) {
        $Trimmed = $Line.Trim()
        if (-not $Trimmed -or $Trimmed.StartsWith("#") -or -not $Trimmed.Contains("=")) { continue }
        $Name, $Value = $Trimmed.Split("=", 2)
        if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
            [Environment]::SetEnvironmentVariable($Name, $Value.Trim('"', "'"), "Process")
        }
    }
}

if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $BackendExecutable)) {
    throw "The development environment is not ready. Run .\scripts\setup.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "node_modules"))) {
    throw "Node dependencies are missing. Run .\scripts\setup.ps1 first."
}

Import-DotEnv (Join-Path $Root ".env")
$TokenBytes = [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
$Token = [Convert]::ToHexString($TokenBytes).ToLowerInvariant()
$env:ASSISTANT_SESSION_TOKEN = $Token
$env:VITE_ASSISTANT_SESSION_TOKEN = $Token
$AssistantPort = if ($env:ASSISTANT_PORT) { $env:ASSISTANT_PORT } else { "8765" }
$env:ASSISTANT_PORT = $AssistantPort
$env:VITE_ASSISTANT_URL = "http://127.0.0.1:$AssistantPort"
$env:JARVIS_BACKEND_EXECUTABLE = $BackendExecutable
$env:ASSISTANT_BACKEND_MANAGED = "1"
if ($Mock) {
    $env:ASSISTANT_ENV = "mock"
    $env:ASSISTANT_STT_PROVIDER = "mock"
    $env:ASSISTANT_LLM_PROVIDER = "mock"
    $env:ASSISTANT_TTS_PROVIDER = "mock"
}

Push-Location $Root
$BackendProcess = $null
try {
    if ($WebOnly) {
        $env:ASSISTANT_BACKEND_MANAGED = "0"
        $BackendProcess = Start-Process -FilePath $BackendExecutable -PassThru -WindowStyle Hidden
        Write-Host "Backend started on $env:VITE_ASSISTANT_URL (PID $($BackendProcess.Id))." -ForegroundColor Cyan
        & npm --workspace "@jarvis/desktop" run dev
        if ($LASTEXITCODE -ne 0) { throw "Vite exited with code $LASTEXITCODE." }
    }
    else {
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
            throw "Cargo is required for Tauri development. Install Rust with rustup, or use -WebOnly."
        }
        & npm --workspace "@jarvis/desktop" run tauri:dev
        if ($LASTEXITCODE -ne 0) { throw "Tauri development exited with code $LASTEXITCODE." }
    }
}
finally {
    if ($BackendProcess -and -not $BackendProcess.HasExited) {
        try {
            Invoke-RestMethod -Method Post `
                -Uri "$env:VITE_ASSISTANT_URL/v1/shutdown" `
                -Headers @{ "X-Assistant-Token" = $Token } `
                -ContentType "application/json" `
                -Body "{}" | Out-Null
            $BackendProcess.WaitForExit(5000) | Out-Null
        }
        catch {
            Write-Warning "The backend did not accept graceful shutdown: $($_.Exception.Message)"
        }
        if (-not $BackendProcess.HasExited) {
            Stop-Process -Id $BackendProcess.Id -Force
            $BackendProcess.WaitForExit()
        }
    }
    Pop-Location
}
