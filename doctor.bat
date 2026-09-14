@echo off
rem LocalFlow self-check. Double-click me and paste what it prints into a bug report.
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo LocalFlow is not installed yet - no .venv found.
  echo Run install.bat first, then double-click this file.
  echo.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m localflow --doctor
echo.
pause
