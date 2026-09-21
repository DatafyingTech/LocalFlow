@echo off
rem LocalFlow updater - double-click me.
rem
rem Downloads the latest LocalFlow over this folder and re-runs the installer. Your
rem config.yaml, history.jsonl, localflow.log, downloaded models and .venv are not part of
rem the download, so nothing of yours is touched.
rem
rem WHY THE WHOLE BODY IS ONE ( ) BLOCK, AND WHY install.ps1 IS COPIED TO %TEMP%:
rem cmd.exe reads a .bat file incrementally, by byte offset, while it runs it. This update
rem overwrites update.bat and install.ps1 - the two files being read right now. A parenthesised
rem block is parsed in full before any of it executes, so the rest of this file is already in
rem memory by then, and `exit /b` at the end of the block means cmd never reads from the
rem replaced file again. PowerShell has the same problem with install.ps1, so we run a copy.
setlocal EnableDelayedExpansion
(
  cd /d "%~dp0"
  set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
  if not exist "!PS!" set "PS=powershell"
  if not exist "%~dp0install.ps1" (
    echo install.ps1 is missing from this folder, so there is nothing to run.
    echo Download LocalFlow again: https://github.com/DatafyingTech/LocalFlow
    pause
    exit /b 1
  )
  rem The script runs from %TEMP%, so it cannot work out which folder to update on its own.
  set "REPO=%~dp0"
  if "!REPO:~-1!"=="\" set "REPO=!REPO:~0,-1!"
  set "TMPPS=%TEMP%\LocalFlow-update-%RANDOM%%RANDOM%.ps1"
  copy /y "%~dp0install.ps1" "!TMPPS!" >nul
  if not exist "!TMPPS!" (
    echo Could not copy install.ps1 into your temp folder.
    pause
    exit /b 1
  )
  echo.
  echo Updating LocalFlow. This leaves your settings, history and models alone.
  echo.
  "!PS!" -NoProfile -ExecutionPolicy Bypass -File "!TMPPS!" -Update -Repo "!REPO!" %*
  set "RC=!ERRORLEVEL!"
  del "!TMPPS!" >nul 2>&1
  echo.
  if not "!RC!"=="0" (
    echo Update did not finish ^(exit code !RC!^). Scroll up to see what went wrong.
    echo If you are stuck, open an issue and paste the last 20 lines above.
  )
  echo.
  pause
  exit /b !RC!
)
