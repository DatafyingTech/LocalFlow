@echo off
rem Launch LocalFlow tray app (no console). Logs go to localflow.log next to this file.
cd /d "%~dp0"
rem NOTE: HF_HUB_OFFLINE is deliberately NOT set here. localflow/asr.py turns it on itself
rem once the speech model is present in .\models, so a first run after install.bat -SkipSmoke
rem can still download it. After that, LocalFlow never touches the network.
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo LocalFlow is not installed yet - no .venv found.
  echo Run install.bat first, then start LocalFlow with this file.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m localflow
