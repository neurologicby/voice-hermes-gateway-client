[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Version = "0.1.0",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ClientRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path (Split-Path -Parent $ClientRoot) ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python interpreter not found: $Python"
}

Push-Location $ClientRoot
try {
    & $Python -m PyInstaller --clean --noconfirm "packaging\voice-client.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    $Executable = Join-Path $ClientRoot "dist\VoiceGatewayClient\VoiceGatewayClient.exe"
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "PyInstaller did not create $Executable"
    }
    Write-Host "Application bundle: $Executable"

    if ($SkipInstaller) {
        return
    }
    $MakeNsis = (Get-Command makensis.exe -ErrorAction SilentlyContinue).Source
    if (-not $MakeNsis) {
        $DefaultNsis = Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"
        if (Test-Path -LiteralPath $DefaultNsis -PathType Leaf) {
            $MakeNsis = $DefaultNsis
        }
    }
    if (-not $MakeNsis) {
        throw "NSIS was not found. Install NSIS or run with -SkipInstaller."
    }
    $BuildDir = Join-Path $ClientRoot "dist\VoiceGatewayClient"
    $OutputDir = Join-Path $ClientRoot "dist"
    & $MakeNsis "/DAPP_VERSION=$Version" "/DBUILD_DIR=$BuildDir" `
        "/DOUTPUT_DIR=$OutputDir" "packaging\installer.nsi"
    if ($LASTEXITCODE -ne 0) {
        throw "NSIS failed with exit code $LASTEXITCODE"
    }
    Write-Host "Installer: $(Join-Path $OutputDir "VoiceGatewayClient-$Version-Setup.exe")"
}
finally {
    Pop-Location
}
