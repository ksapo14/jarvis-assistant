[CmdletBinding()]
param(
    [switch]$AcceptModelLicense,
    [string]$ModelDirectory = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $true
}

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment missing. Run .\scripts\setup.ps1 first."
}
if (-not $AcceptModelLicense) {
    throw @"
The openWakeWord pretrained models use CC BY-NC-SA 4.0, which has attribution,
non-commercial, and share-alike terms. Review the model license at
https://github.com/dscripka/openWakeWord#license, then rerun with
-AcceptModelLicense if those terms fit your use.
"@
}

if (-not $ModelDirectory) {
    $LocalData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
    $ModelDirectory = Join-Path $LocalData "JarvisAssistant\models\openwakeword"
}
New-Item -ItemType Directory -Path $ModelDirectory -Force | Out-Null
$ResolvedDirectory = (Resolve-Path -LiteralPath $ModelDirectory).Path
$env:JARVIS_WAKE_MODEL_TARGET = $ResolvedDirectory

Write-Host "Downloading the official Hey Jarvis and feature models outside the repository..." -ForegroundColor Cyan
& $Python -c "import os, openwakeword; openwakeword.utils.download_models(model_names=['hey_jarvis'], target_directory=os.environ['JARVIS_WAKE_MODEL_TARGET'])"
if ($LASTEXITCODE -ne 0) {
    throw "openWakeWord model download failed with exit code $LASTEXITCODE."
}
$WakeModel = Get-ChildItem -LiteralPath $ResolvedDirectory -Filter "*hey_jarvis*.onnx" | Sort-Object Name | Select-Object -First 1
$MelspecModel = Join-Path $ResolvedDirectory "melspectrogram.onnx"
$EmbeddingModel = Join-Path $ResolvedDirectory "embedding_model.onnx"
if (-not $WakeModel -or -not (Test-Path -LiteralPath $MelspecModel) -or -not (Test-Path -LiteralPath $EmbeddingModel)) {
    throw "The download completed without the expected ONNX wake/feature model files."
}

Write-Host "Model assets are external and will not be embedded in the installer." -ForegroundColor Green
Write-Host "Add these values to .env:" -ForegroundColor Cyan
Write-Host "OPENWAKEWORD_MODEL_PATH=$($WakeModel.FullName)"
Write-Host "OPENWAKEWORD_MELSPEC_MODEL_PATH=$MelspecModel"
Write-Host "OPENWAKEWORD_EMBEDDING_MODEL_PATH=$EmbeddingModel"
