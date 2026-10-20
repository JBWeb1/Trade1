# Trade1 Trading AI - checks against your running MT5 terminal. Read-only: places no orders.
# Run by double-clicking scripts\run_checks.bat
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$Report = Join-Path $Root "trade1_check_report.txt"

$env:PYTHONUTF8 = "1"   # UTF-8 output even when piped into this report
# Run a Trade1 command and merge its error output into the transcript (Windows PowerShell 5.1
# transcripts do not record stderr, which made failures invisible in the report).
function Invoke-Trade1 { & $Py -m app.cli @args 2>&1 | ForEach-Object { "$_" } }
if ($Root -match "OneDrive|Dropbox|Google Drive") {
    Write-Host "WARNING: Trade1 is inside a cloud-synced folder ($Root)." -ForegroundColor Yellow
    Write-Host "Sync locks Trade1's database file. Move the whole folder to C:\Trade1 and run setup there." -ForegroundColor Yellow
}
if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup_windows.bat first." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
if (-not (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
    Write-Host "MetaTrader 5 (terminal64.exe) does not seem to be running. Open it and log in to the demo account first." -ForegroundColor Yellow
    Read-Host "Press Enter once MT5 is open (or Ctrl+C to cancel)"
}

Start-Transcript -Path $Report -Force | Out-Null
Push-Location $Backend
try {
    Write-Host "`n===== 1. Connection and demo verification =====" -ForegroundColor Cyan
    Invoke-Trade1 mt5-check
    if ($LASTEXITCODE -ne 0) { Write-Host "Demo verification did not pass - stopping here. Send me this report." -ForegroundColor Red; return }

    Write-Host "`n===== 2. Market discovery =====" -ForegroundColor Cyan
    Invoke-Trade1 discover
    Invoke-Trade1 markets

    $symbol = Read-Host "`nSymbol to download (exact name as your MT5 Market Watch shows it, e.g. EURUSDm)"
    $today = (Get-Date).ToUniversalTime().AddDays(1).ToString("yyyy-MM-dd")  # tomorrow: include today's bars (capped at now)
    $m1start = (Get-Date).ToUniversalTime().AddDays(-20).ToString("yyyy-MM-dd")
    Write-Host "`n===== 3. History: $symbol H1, 2023 to today =====" -ForegroundColor Cyan
    Invoke-Trade1 ingest --symbol $symbol --timeframe H1 --start 2023-01-01 --end $today
    Write-Host "`n===== 4. History: $symbol M1, last 20 days (MT5 keeps little M1 history) =====" -ForegroundColor Cyan
    Invoke-Trade1 ingest --symbol $symbol --timeframe M1 --start $m1start --end $today
    Write-Host "`n===== 5. Stored coverage =====" -ForegroundColor Cyan
    Invoke-Trade1 coverage
} finally {
    Pop-Location
    Stop-Transcript | Out-Null
}
Write-Host "`nDone. Report saved to: $Report" -ForegroundColor Green
Write-Host "Open it, optionally blank out your login number, and paste it to Claude."
Read-Host "Press Enter to close"
