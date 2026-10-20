@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_checks.ps1"
if errorlevel 1 pause
