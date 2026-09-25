$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path $PSScriptRoot -Parent
$pidPath = Join-Path $demoRoot 'runtime/server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) { throw 'No managed background server PID. Stop the foreground terminal with Ctrl+C.' }
$demoServerPid = [int](Get-Content -LiteralPath $pidPath)
$proc = Get-Process -Id $demoServerPid -ErrorAction SilentlyContinue
if ($proc -and $proc.Path -eq "$demoRoot\.venv\Scripts\python.exe") {
    # /T also stops active algorithm workers owned by this background server.
    taskkill /PID $demoServerPid /T /F
} else { Write-Output 'Recorded server is not running or its identity differs; no process was terminated.' }
