# Launch the LocalFlow tray app without a console window.
# Add -Debug to run in a console with verbose logs instead.
param([switch]$Debug)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
# HF_HUB_OFFLINE is set by localflow/asr.py, but only once the model is cached in .\models.
# Setting it here unconditionally meant a -SkipSmoke install could never fetch the model.
if ($Debug) {
    & "$root\.venv\Scripts\python.exe" -m localflow --debug
} else {
    Start-Process -FilePath "$root\.venv\Scripts\pythonw.exe" -ArgumentList "-m", "localflow" -WorkingDirectory $root -WindowStyle Hidden
}
