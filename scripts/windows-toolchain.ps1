Set-StrictMode -Version Latest

function Enable-RustToolchainPath {
    if (Get-Command cargo -ErrorAction SilentlyContinue) { return }
    $CargoBin = Join-Path $HOME ".cargo\bin"
    if (Test-Path -LiteralPath (Join-Path $CargoBin "cargo.exe")) {
        $env:PATH = "$CargoBin;$env:PATH"
    }
}

function Import-MsvcDeveloperEnvironment {
    if (Get-Command link.exe -ErrorAction SilentlyContinue) { return }
    $NativeProgramFiles = if ($env:ProgramW6432) { $env:ProgramW6432 } else { $env:ProgramFiles }
    $VisualStudioRoot = Join-Path $NativeProgramFiles "Microsoft Visual Studio\2022"
    $DeveloperCommand = Get-ChildItem `
        -Path (Join-Path $VisualStudioRoot "*\Common7\Tools\VsDevCmd.bat") `
        -File `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $DeveloperCommand) { return }

    $HostLine = (& rustc -vV | Select-String '^host:').Line
    $Architecture = if ($HostLine -match 'aarch64') { "arm64" } else { "x64" }
    $Command = "call `"$($DeveloperCommand.FullName)`" -no_logo -arch=$Architecture -host_arch=$Architecture >nul && set"
    $EnvironmentDump = & $env:ComSpec /d /s /c $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Visual Studio developer-environment setup failed with exit code $LASTEXITCODE."
    }
    $ImportedNames = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Line in $EnvironmentDump) {
        $Separator = $Line.IndexOf('=')
        if ($Separator -le 0) { continue }
        $Name = $Line.Substring(0, $Separator)
        # Some Windows hosts expose both PATH and Path. VsDevCmd writes the updated
        # uppercase entry first; do not let the stale casing variant overwrite it.
        if (-not $ImportedNames.Add($Name)) { continue }
        [Environment]::SetEnvironmentVariable(
            $Name,
            $Line.Substring($Separator + 1),
            "Process"
        )
    }
    Enable-RustToolchainPath
}
