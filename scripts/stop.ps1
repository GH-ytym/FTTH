# Compatibility wrapper; `uv run ftth stop` works on every platform.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
uv run ftth stop
exit $LASTEXITCODE
