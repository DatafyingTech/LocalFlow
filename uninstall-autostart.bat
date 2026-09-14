@echo off
rem Turn off "Start with Windows": removes the per-user scheduled task named LocalFlow.
rem Exactly what unticking Start with Windows in the dot menu does.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\autostart.ps1" -Action Disable
echo.
echo If the line above says "exists":false, LocalFlow will not start by itself any more.
echo.
pause
