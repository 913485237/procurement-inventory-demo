param(
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    Write-Host 'Python was not found. Install Python 3.11 or newer and try again.' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Aether AI Native ERP' -ForegroundColor Cyan
Write-Host '  Initializing the local database and service...' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('app.py')
if (-not $NoOpen) {
    $arguments += '--open'
}

& python -u @arguments
exit $LASTEXITCODE
