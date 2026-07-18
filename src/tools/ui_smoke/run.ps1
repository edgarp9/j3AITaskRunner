[CmdletBinding()]
param(
    [switch]$KeepTemp,
    [double]$TimeoutSeconds = 0
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$argsList = @((Join-Path $PSScriptRoot "run_ui_smoke.py"))

if ($KeepTemp) {
    $argsList += "--keep-temp"
}
if ($TimeoutSeconds -gt 0) {
    $argsList += "--timeout"
    $argsList += [string]$TimeoutSeconds
}
if ($VerbosePreference -ne "SilentlyContinue") {
    $argsList += "--verbose"
}

Push-Location $repoRoot
try {
    & $python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "UI smoke wrapper failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
