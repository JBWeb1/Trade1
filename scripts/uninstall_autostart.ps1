# Remove the Trade1 scheduled tasks created by install_autostart.ps1.
foreach ($name in @("Trade1 Dashboard", "Trade1 Backup", "Trade1 News")) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed: $name"
    }
}
Read-Host "Press Enter to close"
