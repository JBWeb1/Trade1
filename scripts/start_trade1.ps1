# Trade1 Trading AI - start the backend API and the dashboard, then open the browser.
# Both listen on 127.0.0.1 only (this PC). Close the two windows to stop them.
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup_windows.bat first." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
if (-not (Test-Path (Join-Path $Frontend ".next"))) { Write-Host "Dashboard not built - run scripts\setup_windows.bat (needs Node.js)." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }

Start-Process powershell -WorkingDirectory $Backend -ArgumentList "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'Trade1 backend'; & '$Py' -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000"
Start-Process powershell -WorkingDirectory $Frontend -ArgumentList "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'Trade1 dashboard'; npm run start"

Write-Host "Starting... the dashboard opens in your browser in a few seconds."
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3000" -TimeoutSec 2 | Out-Null; break } catch { }
}
Start-Process "http://127.0.0.1:3000"
