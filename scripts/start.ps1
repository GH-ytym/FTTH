# Compatibility wrapper; the cross-platform entry point is `uv run ftth serve`.
param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
uv run ftth serve --port $Port
exit $LASTEXITCODE
