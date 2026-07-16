[CmdletBinding()]
param(
    [string]$VoiceModelPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $true
}
$Root = Split-Path -Parent $PSScriptRoot
$PiperEnvironment = Join-Path $Root ".piper-venv"
$PiperPython = Join-Path $PiperEnvironment "Scripts\python.exe"
$PiperExecutable = Join-Path $PiperEnvironment "Scripts\piper.exe"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ was not found."
}
if (-not (Test-Path -LiteralPath $PiperPython)) {
    & python -m venv $PiperEnvironment
    if ($LASTEXITCODE -ne 0) { throw "Creating the Piper environment failed." }
}
& $PiperPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Upgrading pip in the Piper environment failed." }
& $PiperPython -m pip install "piper-tts>=1.4,<1.5"
if ($LASTEXITCODE -ne 0) { throw "Installing Piper failed." }

if (-not (Test-Path -LiteralPath $PiperExecutable)) {
    throw "Piper installed without the expected Windows console executable."
}

Write-Host "Piper installed at:" -ForegroundColor Green
Write-Host $PiperExecutable

if ($VoiceModelPath) {
    $ResolvedVoice = (Resolve-Path -LiteralPath $VoiceModelPath).Path
    if ([IO.Path]::GetExtension($ResolvedVoice) -ne ".onnx") {
        throw "The Piper voice model must be an .onnx file."
    }
    $VoiceConfig = "$ResolvedVoice.json"
    if (-not (Test-Path -LiteralPath $VoiceConfig)) {
        Write-Warning "The matching voice config was not found at $VoiceConfig. Most voices require it."
    }
    Write-Host "`nAdd these values to .env:" -ForegroundColor Cyan
    Write-Host "PIPER_EXECUTABLE_PATH=$PiperExecutable"
    Write-Host "PIPER_MODEL_PATH=$ResolvedVoice"
}
else {
    Write-Host "`nDownload a compatible voice and its .onnx.json metadata separately, review its license, then rerun:" -ForegroundColor Yellow
    Write-Host ".\scripts\install-piper.ps1 -VoiceModelPath C:\path\voice.onnx"
    Write-Host "Voice catalog: https://huggingface.co/rhasspy/piper-voices"
}
