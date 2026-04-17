param(
    [switch]$OpenBrowser = $true
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# Prefer local venv Python to avoid global package mismatch.
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

# Prefer explicit override, then known local pydeps path.
if (-not $env:TRADING_AI_PYDEPS -or [string]::IsNullOrWhiteSpace($env:TRADING_AI_PYDEPS)) {
    $env:TRADING_AI_PYDEPS = "C:\Users\keert\AppData\Local\trading_ai_pydeps_chart"
}
$env:PYTHONPATH = $env:TRADING_AI_PYDEPS

Write-Host "Using Python: $pythonExe"
Write-Host "Using PYTHONPATH: $($env:PYTHONPATH)"
Write-Host "Starting trading services..."

# 1) Core engine (local project entrypoint)
Start-Process -FilePath $pythonExe -ArgumentList "main.py" -WorkingDirectory $projectRoot

# 2) Streamlit dashboard
Start-Process -FilePath $pythonExe -ArgumentList "-m", "streamlit", "run", "run_dashboard.py", "--server.port", "8501", "--server.address", "127.0.0.1" -WorkingDirectory $projectRoot

# 3) Live chart server with accurate forming-candle behavior
Start-Process -FilePath $pythonExe -ArgumentList "live_chart_server.py" -WorkingDirectory $projectRoot

# 4) Flask signal + ARTY monitor dashboard
Start-Process -FilePath $pythonExe -ArgumentList "dashboard.py" -WorkingDirectory $projectRoot

Start-Sleep -Seconds 3

if ($OpenBrowser) {
    Start-Process "http://127.0.0.1:8501/?fresh=1"
    Start-Process "http://127.0.0.1:8050"
    Start-Process "http://127.0.0.1:5000/arty-monitor"
}

Write-Host "Started:"
Write-Host "  Dashboard: http://127.0.0.1:8501/?fresh=1"
Write-Host "  Live Chart: http://127.0.0.1:8050"
Write-Host "  ARTY Monitor: http://127.0.0.1:5000/arty-monitor"
