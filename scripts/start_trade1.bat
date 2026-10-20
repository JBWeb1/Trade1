@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_trade1.ps1"
if errorlevel 1 pause
