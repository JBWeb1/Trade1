# Trade1 Trading AI - download history from your MT5 terminal and run a backtest.
# Read-only towards the broker: no orders are placed. Run via scripts\run_backtest.bat
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$Stamp = Get-Date -Format "yyyyMMdd_HHmm"
$Report = Join-Path $Root "trade1_backtest_$Stamp.txt"

if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup_windows.bat first." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
if (-not (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
    Write-Host "MetaTrader 5 does not seem to be running. Open it and log in to the demo account." -ForegroundColor Yellow
    Read-Host "Press Enter once MT5 is open"
}

$symbol = Read-Host "Symbol (exact MT5 name, e.g. EURUSDm)"
$tf = Read-Host "Timeframe: M1, M15, H1 or H4 [H1]"; if (-not $tf) { $tf = "H1" }
$tf = $tf.ToUpper()
$balance = Read-Host "Starting virtual balance [10000]"; if (-not $balance) { $balance = "10000" }
$commission = Read-Host "Commission per lot per side in account currency (0 for Exness Standard) [0]"; if (-not $commission) { $commission = "0" }

$end = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
switch ($tf) {
    "M1"  { $start = (Get-Date).ToUniversalTime().AddDays(-90).ToString("yyyy-MM-dd"); $htf = "H1" }
    "M15" { $start = (Get-Date).ToUniversalTime().AddDays(-365).ToString("yyyy-MM-dd"); $htf = "H4" }
    "H1"  { $start = (Get-Date).ToUniversalTime().AddDays(-730).ToString("yyyy-MM-dd"); $htf = "H4" }
    "H4"  { $start = (Get-Date).ToUniversalTime().AddDays(-1825).ToString("yyyy-MM-dd"); $htf = "H4" }
    default { Write-Host "Unsupported timeframe $tf" -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
}

Start-Transcript -Path $Report -Force | Out-Null
Push-Location $Backend
try {
    Write-Host "`n===== Demo verification =====" -ForegroundColor Cyan
    & $Py -m app.cli mt5-check
    if ($LASTEXITCODE -ne 0) { Write-Host "Account not verified as demo - stopping." -ForegroundColor Red; return }

    Write-Host "`n===== Refreshing instrument specifications =====" -ForegroundColor Cyan
    & $Py -m app.cli discover | Out-Null

    Write-Host "`n===== Downloading $symbol $tf and $htf ($start -> $end) =====" -ForegroundColor Cyan
    & $Py -m app.cli ingest --symbol $symbol --timeframe $tf --start $start --end $end
    if ($htf -ne $tf) {
        $htfStart = (Get-Date $start).AddDays(-60).ToString("yyyy-MM-dd")  # indicator warm-up
        & $Py -m app.cli ingest --symbol $symbol --timeframe $htf --start $htfStart --end $end
    }

    Write-Host "`n===== Backtest: trend_continuation =====" -ForegroundColor Cyan
    & $Py -m app.cli backtest --symbol $symbol --timeframe $tf --htf $htf --start $start --end $end `
        --balance $balance --commission $commission --risk 0.25
} finally {
    Pop-Location
    Stop-Transcript | Out-Null
}
Write-Host "`nReport saved to: $Report" -ForegroundColor Green
Write-Host "Paste it to Claude. These are historical, in-sample simulation results - not a forecast."
Read-Host "Press Enter to close"
