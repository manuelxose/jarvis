param(
    [switch]$ForceDependencies,
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$bootstrapArgs = @{
    Run = $true
}
if ($ForceDependencies) {
    $bootstrapArgs["ForceDependencies"] = $true
}
if ($PythonPath) {
    $bootstrapArgs["PythonPath"] = $PythonPath
}

& ".\bootstrap.ps1" @bootstrapArgs
exit $LASTEXITCODE
