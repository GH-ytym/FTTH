# Compatibility wrapper for `uv run ftth serve --provider qwen --check-model`.
# The key is read with hidden input by ftth when FTTH_API_KEY/DASHSCOPE_API_KEY are unset.
param(
    [ValidateSet('chat','responses')][string]$Api = 'chat',
    [switch]$NoThinking,
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$cli = @('serve', '--provider', 'qwen', '--api', $Api, '--check-model', '--port', $Port)
if ($NoThinking) { $cli += '--no-thinking' }
uv run ftth @cli
exit $LASTEXITCODE
