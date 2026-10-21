# Trade1 Trading AI - download history from your MT5 terminal and run a backtest.
# Read-only towards the broker: no orders are placed. Run via scripts\run_backtest.bat
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$Stamp = Get-Date -Format "yyyyMMdd_HHmm"
$Report = Join-Path $Root "trade1_backtest_$Stamp.txt"

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
    Write-Host "MetaTrader 5 does not seem to be running. Open it and log in to the demo account." -ForegroundColor Yellow
    Read-Host "Press Enter once MT5 is open"
}

$symbol = Read-Host "Symbol (exact MT5 name, e.g. EURUSDm)"
$tf = Read-Host "Timeframe: M1, M15, H1 or H4 [H1]"; if (-not $tf) { $tf = "H1" }
$tf = $tf.ToUpper()
Write-Host ""
Write-Host "What do you want to test?" -ForegroundColor Cyan
Write-Host "  1 = Does the strategy work at all?   (virtual 100,000 balance, 0.25% risk - every signal can be taken)"
Write-Host "  2 = What could MY account trade?    (your current MT5 demo balance, 1% risk)"
Write-Host "  3 = Enter balance and risk myself"
$mode = Read-Host "Choose 1, 2 or 3 [1]"; if (-not $mode) { $mode = "1" }
switch ($mode) {
    "2" {
        Push-Location $Backend
        $acct = & $Py -c "from app.config import get_settings; from app.mt5.gateway import MT5Gateway; print(MT5Gateway(get_settings()).get_account_info()['balance'])" 2>$null
        Pop-Location
        $balance = "$($acct | Select-Object -Last 1)".Trim()
        if (-not ($balance -as [double])) {
            Write-Host "Could not read the MT5 balance. Using 1000." -ForegroundColor Yellow
            $balance = "1000"
        }
        $risk = "1"
    }
    "3" {
        $balance = Read-Host "Starting balance in dollars, e.g. 1000 (NOT a lot size)"
        $risk = Read-Host "Risk per trade in PERCENT of balance, e.g. 1 means 1% (NOT a lot size) [1]"; if (-not $risk) { $risk = "1" }
    }
    default { $balance = "100000"; $risk = "0.25" }
}
if (-not ($balance -as [double]) -or [double]$balance -le 0) { Write-Host "Balance must be a number like 1000." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
if (-not ($risk -as [double]) -or [double]$risk -lt 0.1) {
    Write-Host "Risk is a percentage of the balance (e.g. 1 = 1%), not a lot size. Trade1 picks the lot size itself. Using 1." -ForegroundColor Yellow
    $risk = "1"
}
if ([double]$risk -gt 2) {
    Write-Host "Risk above 2% per trade is not allowed here. Using 2." -ForegroundColor Yellow
    $risk = "2"
}
if ([double]$balance -lt 500) {
    Write-Host "Note: with a balance under 500, most signals are skipped because even the broker's smallest" -ForegroundColor Yellow
    Write-Host "lot risks more than allowed. The report explains how much would be needed." -ForegroundColor Yellow
}
$commission = Read-Host "Commission per lot per side (0 for Exness Standard - just press Enter) [0]"; if (-not $commission) { $commission = "0" }
Write-Host "`nSettings: symbol $symbol, timeframe $tf, balance $balance, risk $risk% per trade, commission $commission per lot per side"

$end = (Get-Date).ToUniversalTime().AddDays(1).ToString("yyyy-MM-dd")  # tomorrow: include today's bars (capped at now)
switch ($tf) {
    "M1"  { $start = (Get-Date).ToUniversalTime().AddDays(-90).ToString("yyyy-MM-dd"); $htf = "H1" }
    "M15" { $start = (Get-Date).ToUniversalTime().AddDays(-365).ToString("yyyy-MM-dd"); $htf = "H4" }
    "H1"  { $start = (Get-Date).ToUniversalTime().AddDays(-730).ToString("yyyy-MM-dd"); $htf = "H4" }
    "H4"  { $start = (Get-Date).ToUniversalTime().AddDays(-1825).ToString("yyyy-MM-dd"); $htf = "H4" }
    default { Write-Host "Unsupported timeframe $tf" -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
}

Start-Transcript -Path $Report -Force | Out-Null
Write-Host "Settings: symbol $symbol, timeframe $tf, balance $balance, risk $risk% per trade, commission $commission per lot per side"
Push-Location $Backend
try {
    Write-Host "`n===== Demo verification =====" -ForegroundColor Cyan
    Invoke-Trade1 mt5-check
    if ($LASTEXITCODE -ne 0) { Write-Host "Account not verified as demo - stopping." -ForegroundColor Red; return }

    Write-Host "`n===== Refreshing instrument specifications =====" -ForegroundColor Cyan
    Invoke-Trade1 discover

    Write-Host "`n===== Downloading $symbol $tf and $htf ($start -> $end) =====" -ForegroundColor Cyan
    Invoke-Trade1 ingest --symbol $symbol --timeframe $tf --start $start --end $end
    if ($htf -ne $tf) {
        $htfStart = (Get-Date $start).AddDays(-60).ToString("yyyy-MM-dd")  # indicator warm-up
        Invoke-Trade1 ingest --symbol $symbol --timeframe $htf --start $htfStart --end $end
    }

    foreach ($strategy in @("trend_continuation", "range_breakout", "liquidity_sweep")) {
        Write-Host "`n===== Backtest: $strategy =====" -ForegroundColor Cyan
        Invoke-Trade1 backtest --symbol $symbol --timeframe $tf --htf $htf --start $start --end $end `
            --strategy $strategy --balance $balance --commission $commission --risk $risk
        Write-Host "`n===== Validation: $strategy (takes up to a few minutes) =====" -ForegroundColor Cyan
        Invoke-Trade1 validate --symbol $symbol --timeframe $tf --htf $htf --start $start --end $end `
            --strategy $strategy --balance $balance --commission $commission --risk $risk
    }
} finally {
    Pop-Location
    Stop-Transcript | Out-Null
}
Write-Host "`nReport saved to: $Report" -ForegroundColor Green
Write-Host "Share it with Claude. Most strategies FAIL validation - that is the system protecting you."
Write-Host "Running this again on the same period re-uses the out-of-sample data; reports will say so."
Read-Host "Press Enter to close"
