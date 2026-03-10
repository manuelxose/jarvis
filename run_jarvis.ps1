param(
    [switch]$ForceDependencies,
    [switch]$NoMonitor,
    [switch]$WithMonitor,
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
if ($NoMonitor) {
    $bootstrapArgs["NoMonitor"] = $true
}
if ($WithMonitor) {
    $bootstrapArgs["WithMonitor"] = $true
}
if ($PythonPath) {
    $bootstrapArgs["PythonPath"] = $PythonPath
}

& ".\bootstrap.ps1" @bootstrapArgs
exit $LASTEXITCODE
