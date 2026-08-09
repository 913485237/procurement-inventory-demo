$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    Write-Host '未找到 Python。请安装 Python 3.11 或更高版本后重试。' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '  Aether AI 原生制造业 ERP' -ForegroundColor Cyan
Write-Host '  正在初始化本地数据库与业务服务…' -ForegroundColor DarkGray
Write-Host ''

& python app.py --open

