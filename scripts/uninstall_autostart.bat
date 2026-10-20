@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_autostart.ps1"
if errorlevel 1 pause
