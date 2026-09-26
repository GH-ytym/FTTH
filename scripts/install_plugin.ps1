# Compatibility wrapper for `uv run ftth plugin install`.
param([string]$Profile = 'default', [switch]$Update)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$cli = @('plugin', 'install', '--profile', $Profile)
if ($Update) { $cli += '--update' }
uv run ftth @cli
exit $LASTEXITCODE
