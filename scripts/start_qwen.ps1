# Qwen compatibility wrapper: read local .env and check the model before serving.
param(
    [ValidateSet('chat','responses')][string]$Api = 'chat',
    [switch]$NoThinking,
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$cli = @('serve', '--provider', 'qwen', '--api', $Api, '--check-model', '--port', $Port)
if ($NoThinking) { $cli += '--no-thinking' }
$launchArgs = $cli | Select-Object -Skip 1
uv run --frozen python -X utf8 scripts/start_backend.py @launchArgs
exit $LASTEXITCODE
