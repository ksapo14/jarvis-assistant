[CmdletBinding()]
param([switch]$Mock)

$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $true
}
$Root = Split-Path -Parent $PSScriptRoot
$Executable = Join-Path $Root ".venv\Scripts\jarvis-assistant.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Backend is not installed. Run .\scripts\setup.ps1 first."
}
if (-not $env:ASSISTANT_SESSION_TOKEN) {
    $env:ASSISTANT_SESSION_TOKEN = [Convert]::ToHexString(
        [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    ).ToLowerInvariant()
    Write-Host "Generated a one-session local API token." -ForegroundColor Yellow
}
if ($Mock) { $env:ASSISTANT_ENV = "mock" }
& $Executable
if ($LASTEXITCODE -ne 0) { throw "The assistant backend exited with code $LASTEXITCODE." }
