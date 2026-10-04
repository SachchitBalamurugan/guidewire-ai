@echo off
rem Double-click to start Guidewire. The first run sets everything up (needs internet once).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
