# Trade1 Trading AI - one-time setup for Windows.
# Run by double-clicking scripts\setup_windows.bat (or: powershell -ExecutionPolicy Bypass -File setup_windows.ps1)
$ErrorActionPreference = "Continue"  # native-command failures are checked via $LASTEXITCODE
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Venv = Join-Path $Backend ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nSETUP STOPPED: $msg" -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

if ($Root -match "OneDrive|Dropbox|Google Drive") {
    Write-Host "`nWARNING: this folder is synced by OneDrive/Dropbox: $Root" -ForegroundColor Yellow
    Write-Host "Syncing locks and can corrupt Trade1's database. Recommended: move the whole folder to C:\Trade1 first." -ForegroundColor Yellow
    $ans = Read-Host "Continue here anyway? (y/n)"
    if ($ans -ne "y") { Write-Host "Move the folder to C:\Trade1 and run setup_windows.bat from there."; Read-Host "Press Enter to close"; exit 1 }
}

Step "Looking for 64-bit Python 3.12 or 3.13"
$pyver = $null
if (Get-Command py -ErrorAction SilentlyContinue) { foreach ($v in @("3.12", "3.13")) {
    & py "-$v" -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $pyver = $v; break }
} }
if (-not $pyver) {
    $ans = Read-Host "Python 3.12 (64-bit) not found. Install it now with winget? (y/n)"
    if ($ans -ne "y") { Fail "Install Python 3.12 64-bit from python.org, then run this again." }
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    & py -3.12 --version 2>$null
    if ($LASTEXITCODE -ne 0) { Fail "Python installed, but this window can't see it yet. Close it and run setup again." }
    $pyver = "3.12"
}
Write-Host "Using Python $pyver"

Step "Creating virtual environment in backend\.venv"
if (-not (Test-Path $Py)) { & py "-$pyver" -m venv $Venv; if ($LASTEXITCODE -ne 0) { Fail "could not create the virtual environment" } }

Step "Installing dependencies (includes the official MetaTrader5 package)"
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r (Join-Path $Backend "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (see the error above). If it is MetaTrader5, use Python 3.12 64-bit." }
& $Py -c "import MetaTrader5"
if ($LASTEXITCODE -ne 0) { Fail "the MetaTrader5 package did not install correctly." }

Step "Creating backend\.env (kept private, never committed)"
$EnvFile = Join-Path $Backend ".env"
if (Test-Path $EnvFile) {
    Write-Host ".env already exists - leaving it unchanged."
} else {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    $content = Get-Content (Join-Path $Backend ".env.example")
    $content = $content -replace '^TRADE1_DATABASE_URL=.*', 'TRADE1_DATABASE_URL=sqlite:///./trade1.db'
    $content = $content -replace '^TRADE1_API_TOKEN=.*', "TRADE1_API_TOKEN=$token"
    [System.IO.File]::WriteAllLines($EnvFile, [string[]]$content)  # UTF-8 without BOM
    Write-Host "Created .env using a local SQLite database (backend\trade1.db) and a random API token."
}

Step "Creating database tables"
Push-Location $Backend
try {
    & $Py -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { Fail "database migration failed" }

    Step "Running the test suite (uses a fake MT5 - never touches your account)"
    & $Py -m pytest -q
    if ($LASTEXITCODE -ne 0) { Fail "some tests failed - copy the output above and send it to me." }
} finally { Pop-Location }

Step "Setting up the dashboard (Node.js)"
$Frontend = Join-Path $Root "frontend"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    $ans = Read-Host "Node.js (needed for the dashboard) not found. Install the LTS version with winget? (y/n)"
    if ($ans -eq "y") {
        winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    }
}
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location $Frontend
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) { Fail "npm ci failed (see above)" }
        & npm run build
        if ($LASTEXITCODE -ne 0) { Fail "dashboard build failed (see above)" }
    } finally { Pop-Location }
} else {
    Write-Host "Skipping the dashboard: Node.js is not installed. Everything else works from the command line." -ForegroundColor Yellow
}

Write-Host "`nSETUP COMPLETE." -ForegroundColor Green
Write-Host "Next: open MetaTrader 5, log in to your Exness DEMO account, then double-click scripts\run_checks.bat"
Write-Host "Dashboard: double-click scripts\start_trade1.bat"
Read-Host "Press Enter to close"
