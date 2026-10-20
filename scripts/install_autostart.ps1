# Trade1 Trading AI - register scheduled tasks for the current Windows user:
#   * "Trade1 Dashboard"  - at logon: start backend + dashboard (start_trade1.ps1)
#   * "Trade1 Backup"     - daily 23:30: database backup to <Trade1>\backups (keeps 14)
#   * "Trade1 News"       - daily 06:05: fetch the Forex Factory calendar (if TRADE1_NEWS_FEED_URL is set)
#   * "Trade1 Report"     - daily 23:05: demo forward-test report (Telegram if configured)
#   * "Trade1 Demo Runner" (optional, asked) - at logon: the demo runner (for a VPS)
# MT5 itself: tick "Start with Windows" in MT5 or put its shortcut in shell:startup.
# Remove everything again with uninstall_autostart.bat.
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Host "Run scripts\setup_windows.bat first." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

function Register($name, $trigger, $exe, $arguments, $workdir) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $workdir
    Register-ScheduledTask -TaskName $name -Trigger $trigger -Action $action -Principal $principal `
        -Settings $settings -Force | Out-Null
    Write-Host "Registered: $name" -ForegroundColor Green
}

try {
    Register "Trade1 Dashboard" (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME") "powershell.exe" `
        "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\start_trade1.ps1`"" $Root
    Register "Trade1 Backup" (New-ScheduledTaskTrigger -Daily -At "23:30") $Py `
        "-m app.cli backup --dest `"$Root\backups`" --keep 14" $Backend
    Register "Trade1 News" (New-ScheduledTaskTrigger -Daily -At "06:05") $Py "-m app.cli news-fetch" $Backend
    Register "Trade1 Report" (New-ScheduledTaskTrigger -Daily -At "23:05") $Py "-m app.cli daily-report" $Backend
    $ans = Read-Host "Also start the DEMO runner automatically at logon? (recommended only on a dedicated VPS) (y/n)"
    if ($ans -eq "y") {
        Register "Trade1 Demo Runner" (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME") `
            "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run_demo.ps1`"" $Root
    }
} catch {
    Write-Host "Could not register tasks: $_" -ForegroundColor Red
    Write-Host "If Windows refuses, right-click this script and choose 'Run with PowerShell' as administrator."
}
Write-Host "`nDone. See them in Task Scheduler (taskschd.msc) under Task Scheduler Library."
Read-Host "Press Enter to close"
