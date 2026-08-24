$ErrorActionPreference = "Stop"

$clientRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = Split-Path -Parent $clientRoot
$python = Join-Path $workspaceRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "PyCharm workspace Python not found: $python"
}

Push-Location $clientRoot
try {
    & $python -m voice_client.app
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
