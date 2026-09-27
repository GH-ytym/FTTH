# Read the repository's local .env; an explicit port overrides it.
param([int]$Port = 0)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$cli = @()
if ($Port) { $cli += @('--port', $Port) }
uv run --frozen python -X utf8 scripts/start_backend.py @cli
exit $LASTEXITCODE
