param(
    [ValidateSet('chat','responses')][string]$Api = 'chat',
    [switch]$NoThinking,
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $demoRoot
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Stop the existing demo backend (scripts/stop.ps1 or Ctrl+C) before starting Qwen."
}
$env:FTTH_PROVIDER = 'qwen'
$env:FTTH_BASE_URL = 'https://maas.qianwenaiapi.com/compatible-mode/v1'
$env:FTTH_MODEL = 'qwen3.8-flash'
$env:FTTH_MODEL_API = $Api
$env:FTTH_ENABLE_THINKING = if ($NoThinking) { 'false' } else { 'true' }
$env:PYTHONUTF8 = '1'
if (-not $env:FTTH_API_KEY -and -not $env:DASHSCOPE_API_KEY) {
    $secureKey = Read-Host 'Paste Qwen API Key (hidden; not saved to disk)' -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try { $env:DASHSCOPE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer) }
}
if (-not $env:FTTH_API_KEY -and -not $env:DASHSCOPE_API_KEY) { throw 'API key is empty.' }
if (-not (Test-Path '.venv/Scripts/python.exe')) { uv sync --frozen }
# This probe uses only synthetic data, and verifies a tool call plus follow-up.
& '.venv/Scripts/python.exe' scripts/check_model.py
if ($LASTEXITCODE -ne 0) { throw 'Qwen tool-calling check failed. Backend not started.' }
& "$PSScriptRoot/start.ps1" -Port $Port
