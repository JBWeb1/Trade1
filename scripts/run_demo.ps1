# Trade1 Trading AI - start the demo forward-test runner (DEMO account only).
# It does nothing until demo trading is enabled on the dashboard after a PASSED validation.
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup_windows.bat first." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
$host.UI.RawUI.WindowTitle = "Trade1 demo runner"
Set-Location $Backend
& $Py -m app.cli demo-status
Write-Host "`nStarting the runner. Close this window or press Ctrl+C to stop it (open positions keep their SL/TP)." -ForegroundColor Cyan
& $Py -m app.cli demo-run --interval 20
