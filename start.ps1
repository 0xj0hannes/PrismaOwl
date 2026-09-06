# Start the PrismaOwl web interface on Windows (run .\install.ps1 first).
#   .\start.ps1              -> http://127.0.0.1:8000
#   $env:PORT=8080; .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) { Write-Host "No virtual environment found. Run .\install.ps1 first." -ForegroundColor Red; exit 1 }
$hostName = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$port = if ($env:PORT) { $env:PORT } else { "8000" }
$url = "http://$hostName`:$port"
Write-Host "PrismaOwl: $url   (press Ctrl+C to stop)"
if (-not $env:PRISMAOWL_NO_BROWSER) { Start-Job -ScriptBlock {
    param($u)
    for ($i = 0; $i -lt 40; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 2 | Out-Null; Start-Process $u; break } catch { Start-Sleep -Milliseconds 500 }
    }
} -ArgumentList $url | Out-Null }
& ".venv\Scripts\python.exe" -m uvicorn app:app --host $hostName --port $port
