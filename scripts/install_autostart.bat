@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autostart.ps1"
if errorlevel 1 pause
