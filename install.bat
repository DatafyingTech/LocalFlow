@echo off
rem LocalFlow installer - double-click me.
rem Runs install.ps1 with PowerShell's execution policy bypassed for this one command,
rem so you never have to change any Windows security setting.
cd /d "%~dp0"
rem Call PowerShell by its full path. A user with a damaged PATH (or a PATH that a previous
rem installer truncated) otherwise gets "powershell is not recognized" and exit code 9009,
rem which looks like a LocalFlow bug and is not one.
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo Install did not finish ^(exit code %RC%^). Scroll up to see what went wrong.
  echo If you are stuck, open an issue and paste the last 20 lines above.
)
echo.
pause
exit /b %RC%
