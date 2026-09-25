param([string]$Profile = 'default', [switch]$Update)
$ErrorActionPreference = 'Stop'
if ($Profile -notmatch '^[\w-]+$') { throw 'Profile must be a simple QGIS profile name.' }
$demoRoot = Split-Path $PSScriptRoot -Parent
$target = Join-Path $env:APPDATA "QGIS/QGIS3/profiles/$Profile/python/plugins/ftth_demo0"
if (Test-Path -LiteralPath $target) {
    if (-not $Update) { throw "Target already exists: $target. Use -Update to back up and update it, or install the ZIP in QGIS." }
    $backup = Join-Path $demoRoot ("runtime/plugin-backups/ftth_demo0-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path (Split-Path $backup -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $target -Destination $backup -Recurse
    Write-Output "Previous plugin backed up to $backup"
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
Get-ChildItem -LiteralPath "$demoRoot/qgis_plugin/ftth_demo0" -Exclude '__pycache__' | Copy-Item -Destination $target -Recurse -Force
Write-Output "Installed to $target. Restart QGIS, then enable FTTH Agent demo0 in Plugin Manager."
