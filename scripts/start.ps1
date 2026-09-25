param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $demoRoot
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Stop the existing backend before starting another instance."
}
if (-not (Test-Path '.venv/Scripts/python.exe')) { uv sync --frozen }
& '.venv/Scripts/python.exe' -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port $Port
