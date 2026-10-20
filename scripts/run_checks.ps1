# Trade1 Trading AI - checks against your running MT5 terminal. Read-only: places no orders.
# Run by double-clicking scripts\run_checks.bat
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$Report = Join-Path $Root "trade1_check_report.txt"

if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup_windows.bat first." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
if (-not (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
    Write-Host "MetaTrader 5 (terminal64.exe) does not seem to be running. Open it and log in to the demo account first." -ForegroundColor Yellow
    Read-Host "Press Enter once MT5 is open (or Ctrl+C to cancel)"
}

Start-Transcript -Path $Report -Force | Out-Null
Push-Location $Backend
try {
    Write-Host "`n===== 1. Connection and demo verification =====" -ForegroundColor Cyan
    & $Py -m app.cli mt5-check
    if ($LASTEXITCODE -ne 0) { Write-Host "Demo verification did not pass - stopping here. Send me this report." -ForegroundColor Red; return }

    Write-Host "`n===== 2. Market discovery =====" -ForegroundColor Cyan
    & $Py -m app.cli discover

    $symbol = Read-Host "`nSymbol to download (exact name as your MT5 Market Watch shows it, e.g. EURUSDm)"
    Write-Host "`n===== 3. History: $symbol H1, 2024 =====" -ForegroundColor Cyan
    & $Py -m app.cli ingest --symbol $symbol --timeframe H1 --start 2024-01-01 --end 2025-01-01
    Write-Host "`n===== 4. History: $symbol M1, January 2025 =====" -ForegroundColor Cyan
    & $Py -m app.cli ingest --symbol $symbol --timeframe M1 --start 2025-01-01 --end 2025-02-01
    Write-Host "`n===== 5. Stored coverage =====" -ForegroundColor Cyan
    & $Py -m app.cli coverage
} finally {
    Pop-Location
    Stop-Transcript | Out-Null
}
Write-Host "`nDone. Report saved to: $Report" -ForegroundColor Green
Write-Host "Open it, optionally blank out your login number, and paste it to Claude."
Read-Host "Press Enter to close"
