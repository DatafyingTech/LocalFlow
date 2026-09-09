<#
LocalFlow installer (Windows 10/11 x64, Python 3.11-3.13).

What it does:
  1. pre-flight checks (Windows, 64-bit, Python, NVIDIA driver, free disk on BOTH the
     LocalFlow drive and the user-profile drive Ollama writes to, Ollama)
  2. creates .venv and installs the pinned requirements (CUDA runtime wheels, onnxruntime-gpu,
     onnx-asr, faster-whisper with --no-deps)
  3. pulls the cleanup LLM with Ollama (gemma3:4b) if Ollama is available
  4. writes config.yaml (from config.example.yaml when present)
  5. downloads the Parakeet ASR model (~2.5 GB, one time) into .\models and runs the smoke test
  6. runs the unit tests

Everything is idempotent: re-running skips work that is already done.

Usage:
  install.bat                        (double-click - easiest)
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 [options]

Options:
  -Python <path>   use this python.exe instead of auto-detection
  -CPU             force the no-GPU configuration: installs requirements-cpu.txt instead
                   (no CUDA wheels, ~2 GB less), slower but works without NVIDIA
  -SkipOllama      do not install/pull the optional cleanup LLM
  -SkipSmoke       do not download the model / run the ASR smoke test (the app then
                   downloads the model itself, with a notification, on its first run)
  -Force           delete and recreate .venv from scratch
#>
param(
    [string]$Python = "",
    [switch]$CPU,
    [switch]$SkipOllama,
    [switch]$SkipSmoke,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$PY_DOWNLOAD = "https://www.python.org/downloads/windows/"
$OLLAMA_DOWNLOAD = "https://ollama.com/download"
$NVIDIA_DRIVERS = "https://www.nvidia.com/Download/index.aspx"
# Disk is checked on two drives: the repo drive holds .venv + the ASR model, and the
# user-profile drive holds Ollama's model store (%USERPROFILE%\.ollama, usually C:).
$MIN_DISK_REPO_GB = 5
$MIN_DISK_PROFILE_GB = 4
$MIN_DRIVER = 525

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    [ok]   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    [warn] $msg" -ForegroundColor Yellow }
function Info($msg) { Write-Host "    $msg" -ForegroundColor Gray }
function Die($msg, $hint) {
    Write-Host ""
    Write-Host "INSTALL STOPPED" -ForegroundColor Red
    Write-Host "  $msg" -ForegroundColor Red
    if ($hint) { Write-Host "  -> $hint" -ForegroundColor Yellow }
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "  LocalFlow installer" -ForegroundColor White
Write-Host "  local push-to-talk dictation - nothing leaves this machine" -ForegroundColor DarkGray

# ---------------------------------------------------------------- 1. pre-flight
Step "Pre-flight checks"

# --- operating system
$osv = [Environment]::OSVersion.Version
if ($osv.Major -lt 10) {
    Die "Windows $($osv.Major).$($osv.Minor) is not supported." "LocalFlow needs Windows 10 or 11."
}
$osName = "Windows $($osv.Major)"
if ($osv.Build -ge 22000) { $osName = "Windows 11" } elseif ($osv.Major -eq 10) { $osName = "Windows 10" }
Ok "$osName (build $($osv.Build))"

if (-not [Environment]::Is64BitOperatingSystem) {
    Die "This is a 32-bit Windows install." "LocalFlow needs 64-bit Windows (the CUDA and ONNX wheels are x64 only)."
}
Ok "64-bit Windows"

# --- free disk space: the repo drive AND the user-profile drive (Ollama's model store)
function Get-FreeGB([string]$path) {
    $qual = (Split-Path -Qualifier $path).TrimEnd(":")
    if (-not $qual) { return $null }
    $d = Get-PSDrive -Name $qual -ErrorAction Stop
    return [pscustomobject]@{ Drive = $qual; FreeGB = [math]::Round($d.Free / 1GB, 1) }
}

$repoDrive = $null
$profileDrive = $null
try { $repoDrive = Get-FreeGB $root } catch { Warn "Could not check free space on the LocalFlow drive: $($_.Exception.Message)" }
try { $profileDrive = Get-FreeGB $env:USERPROFILE } catch { Warn "Could not check free space on your user-profile drive: $($_.Exception.Message)" }

if ($repoDrive) {
    if ($repoDrive.FreeGB -lt $MIN_DISK_REPO_GB) {
        Die "Only $($repoDrive.FreeGB) GB free on drive $($repoDrive.Drive): - LocalFlow needs about $MIN_DISK_REPO_GB GB there (.venv plus the ~2.5 GB speech model)." `
            "Free up space on $($repoDrive.Drive): or move the LocalFlow folder to a drive that has room."
    }
    Ok "$($repoDrive.FreeGB) GB free on drive $($repoDrive.Drive): - LocalFlow folder (need ~$MIN_DISK_REPO_GB GB)"
}
if ($profileDrive) {
    $sameDrive = ($repoDrive -and $repoDrive.Drive -eq $profileDrive.Drive)
    $needProfile = if ($sameDrive) { $MIN_DISK_REPO_GB + $MIN_DISK_PROFILE_GB } else { $MIN_DISK_PROFILE_GB }
    if ($SkipOllama) {
        Info "Skipping the Ollama disk check (-SkipOllama)."
    } elseif ($profileDrive.FreeGB -lt $needProfile) {
        Die "Only $($profileDrive.FreeGB) GB free on drive $($profileDrive.Drive): - that drive needs about $needProfile GB." `
            "Ollama stores the cleanup model (gemma3:4b, ~3.3 GB) in $env:USERPROFILE\.ollama, which lives on $($profileDrive.Drive):. Free up space there, or re-run with -SkipOllama to install without the cleanup model."
    } elseif (-not $sameDrive) {
        Ok "$($profileDrive.FreeGB) GB free on drive $($profileDrive.Drive): - Ollama models in $env:USERPROFILE\.ollama (need ~$needProfile GB)"
    } else {
        Ok "same drive also holds $env:USERPROFILE\.ollama (need ~$needProfile GB in total)"
    }
}

# --- Python: auto-detect unless -Python was passed
# NB: no double quotes and no % in this one-liner - PowerShell mangles both when passing
# arguments to a native executable. chr(80) is "P" (pointer size -> 32/64-bit).
$probe = 'import sys,struct;print(sys.version_info[0],sys.version_info[1],sys.version_info[2],struct.calcsize(chr(80))*8,sys.executable)'

function Probe-Python([string[]]$cmd) {
    $rest = @()
    if ($cmd.Count -gt 1) { $rest = $cmd[1..($cmd.Count - 1)] }
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"   # a candidate that is missing must not abort the script
    try {
        $out = & $cmd[0] @rest -c $probe 2>$null
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $old
    }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $parts = ("$out".Trim() -split "\s+", 5)
    if ($parts.Count -lt 5) { return $null }
    return [pscustomobject]@{
        Major = [int]$parts[0]; Minor = [int]$parts[1]; Micro = [int]$parts[2]
        Bits  = [int]$parts[3]; Exe = $parts[4]
        Label = "Python $($parts[0]).$($parts[1]).$($parts[2]) ($($parts[3])-bit)"
    }
}

function Test-Supported($p) {
    if (-not $p) { return $false }
    if ($p.Bits -ne 64) { return $false }
    if ($p.Major -ne 3) { return $false }
    return ($p.Minor -ge 11 -and $p.Minor -le 13)
}

$pyInfo = $null
if ($Python) {
    if (-not (Test-Path $Python)) { Die "-Python `"$Python`" does not exist." "Pass the full path to python.exe, or drop -Python to auto-detect." }
    $pyInfo = Probe-Python @($Python)
    if (-not $pyInfo) { Die "Could not run `"$Python`"." "Is it really a python.exe?" }
    if (-not (Test-Supported $pyInfo)) {
        Die "$($pyInfo.Label) at $Python is not supported." "LocalFlow needs 64-bit Python 3.11, 3.12 or 3.13. Download: $PY_DOWNLOAD"
    }
} else {
    $candidates = @(@("py", "-3.12"), @("py", "-3.13"), @("py", "-3.11"), @("python"))
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $p = Probe-Python $c
        if (Test-Supported $p) { $pyInfo = $p; break }
    }
    if (-not $pyInfo) {
        Die "No supported Python found (looked for py -3.12, py -3.13, py -3.11 and python on PATH)." `
            "Install 64-bit Python 3.12 from $PY_DOWNLOAD (tick `"Add python.exe to PATH`"), then run this installer again. Already have one? Pass it: install.bat -Python C:\path\to\python.exe"
    }
}
$Python = $pyInfo.Exe
Ok "$($pyInfo.Label) - $Python"

# --- NVIDIA GPU
$gpuOk = $false
$gpuName = ""
$driver = ""
# nvidia-smi is often NOT on PATH even on a healthy NVIDIA machine, so fall back to the
# standard location the driver installs it to. Always call the resolved *path*, never the
# bare name - calling a bare name that is not on PATH throws and would silently look like
# "no GPU here".
$smiPath = ""
$smiCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smiCmd) { $smiPath = $smiCmd.Source }
if (-not $smiPath) {
    foreach ($cand in @("$env:SystemRoot\System32\nvidia-smi.exe",
                        "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe")) {
        if (Test-Path $cand) { $smiPath = $cand; break }
    }
}
$smiError = ""
if ($smiPath) {
    try {
        $q = & $smiPath --query-gpu=driver_version,name --format=csv,noheader 2>$null
        if ($LASTEXITCODE -ne 0) { $smiError = "nvidia-smi exited with code $LASTEXITCODE" }
        if ($LASTEXITCODE -eq 0 -and $q) {
            $first = ("$q" -split "`n")[0]
            $driver = ($first -split ",")[0].Trim()
            $gpuName = ($first -split ",")[1].Trim()
            $gpuOk = $true
        }
    } catch { $smiError = $_.Exception.Message }
} else {
    $smiError = "nvidia-smi.exe was not found on PATH or in $env:SystemRoot\System32"
}
if ($gpuOk) {
    Ok "NVIDIA GPU: $gpuName (driver $driver)"
    $major = 0
    [void][int]::TryParse(($driver -split "\.")[0], [ref]$major)
    if ($major -gt 0 -and $major -lt $MIN_DRIVER) {
        Warn "Driver $driver is older than $MIN_DRIVER. The CUDA 12 runtime wheels need >= $MIN_DRIVER."
        Info "Update your driver: $NVIDIA_DRIVERS  (no CUDA toolkit needed, just the driver)"
    }
} else {
    Warn "No NVIDIA GPU detected."
    Info "Reason: $smiError"
}

$cpuMode = $false
if ($CPU) {
    $cpuMode = $true
    Info "-CPU given: installing the no-GPU configuration (no CUDA wheels, ~2 GB less to download)."
} elseif (-not $gpuOk) {
    # Never downgrade to CPU silently: an NVIDIA owner whose driver is broken (or whose
    # nvidia-smi we simply could not find) would end up with a 10x slower install and no idea why.
    Write-Host ""
    Write-Host "    LocalFlow could not find an NVIDIA GPU on this machine." -ForegroundColor Yellow
    Write-Host "    $smiError" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    If you do NOT have an NVIDIA card, that is expected. LocalFlow still works;" -ForegroundColor Yellow
    Write-Host "    it just runs the speech model on your CPU, which is roughly 10x slower" -ForegroundColor Yellow
    Write-Host "    (usually still under a second per phrase on a modern CPU)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    If you DO have an NVIDIA card, stop here. Install or repair its driver" -ForegroundColor Yellow
    Write-Host "    ($NVIDIA_DRIVERS), reboot, and run this installer again. Installing in CPU" -ForegroundColor Yellow
    Write-Host "    mode now means the GPU stays unused until you re-install." -ForegroundColor Yellow
    Write-Host ""
    $answer = ""
    if ([Environment]::UserInteractive) {
        $answer = Read-Host "    Continue in CPU mode? [y] yes  /  [n] stop so I can fix my driver (y/n)"
    } else {
        Warn "Not an interactive console; continuing in CPU mode. Re-run with -CPU to silence this."
        $answer = "y"
    }
    if ($answer -notmatch '^\s*[yY]') {
        Die "Stopped so you can install the NVIDIA driver." "Get the driver from $NVIDIA_DRIVERS (no CUDA toolkit needed), reboot, then run install.bat again. To install without a GPU on purpose, run: install.bat -CPU"
    }
    $cpuMode = $true
    Info "Continuing in CPU mode."
}

# --- Ollama (optional: text cleanup LLM)
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
$ollamaRunning = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $ollamaRunning = $true }
} catch { }
if ($ollamaCmd -and $ollamaRunning) { Ok "Ollama installed and running (optional LLM cleanup available)" }
elseif ($ollamaCmd) { Warn "Ollama is installed but not running. Start it (search 'Ollama' in the Start menu) or run: ollama serve" }
else { Warn "Ollama is not installed - LocalFlow will run with rules-only cleanup (still perfectly usable)." }

# ---------------------------------------------------------------- 2. virtual environment
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

if ($Force -and (Test-Path ".\.venv")) {
    Step "Removing the existing .venv (-Force)"
    Remove-Item -Recurse -Force ".\.venv"
}

Step "Virtual environment (.venv)"
if (Test-Path $venvPy) {
    $existing = Probe-Python @($venvPy)
    if (Test-Supported $existing) {
        Ok "already exists ($($existing.Label)) - reusing it"
    } else {
        Warn "existing .venv is unusable; recreating it"
        Remove-Item -Recurse -Force ".\.venv"
    }
}
if (-not (Test-Path $venvPy)) {
    Info "creating with $Python ..."
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
        Die "Could not create the virtual environment." "Check that $Python works and that you can write to $root."
    }
    Ok "created"
}
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check

# ---------------------------------------------------------------- 3. dependencies
# CPU mode installs the smaller CPU-only set (no CUDA wheels, plain onnxruntime) when that
# requirements file is present; otherwise it just uses the normal one.
$reqFile = "requirements.txt"
if ($cpuMode) {
    $reqFile = "requirements-cpu.txt"
    if (-not (Test-Path ".\$reqFile")) {
        Die "$reqFile is missing from the LocalFlow folder." "Re-download LocalFlow, or run the installer without -CPU (that pulls about 2 GB of CUDA wheels this machine will not use)."
    }
}

if ($cpuMode) {
    Step "Installing Python packages from $reqFile - CPU build, no CUDA wheels (about 2 GB less to download)"
} else {
    Step "Installing Python packages from $reqFile (a few hundred MB the first time; go make coffee)"
}
if ($reqFile -eq "requirements-cpu.txt") {
    # if this venv was previously a GPU install, drop the GPU wheel so the CPU one can take over
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $venvPy -m pip uninstall -y onnxruntime-gpu 2>$null | Out-Null } catch { } finally { $ErrorActionPreference = $old }
}
& $venvPy -m pip install --disable-pip-version-check -r $reqFile
if ($LASTEXITCODE -ne 0) {
    Die "pip install -r $reqFile failed (see the output above)." `
        "Most common causes: no internet / a proxy, or a corporate TLS inspection proxy. Retry, or run: .\.venv\Scripts\python.exe -m pip install -r $reqFile"
}

Step "Installing faster-whisper without deps (its metadata would otherwise pull in the wrong onnxruntime)"
& $venvPy -m pip install --disable-pip-version-check --no-deps -r requirements-whisper.txt
if ($LASTEXITCODE -ne 0) { Die "pip install faster-whisper failed (see above)." "Retry with a working internet connection." }

# Guard: in a GPU install the CPU-only onnxruntime wheel must NOT be present (it shadows the
# GPU build). In CPU mode that wheel is exactly what we want, so the guard is skipped.
if (-not $cpuMode) {
    $cpuOrt = $null
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"   # pip writes "not found" to stderr; that is not an error here
    try { $cpuOrt = & $venvPy -m pip show onnxruntime 2>$null } catch { } finally { $ErrorActionPreference = $old }
    if ($cpuOrt) {
        Warn "CPU onnxruntime detected; removing it and re-installing onnxruntime-gpu"
        & $venvPy -m pip uninstall -y onnxruntime onnxruntime-gpu
        & $venvPy -m pip install --disable-pip-version-check --no-deps onnxruntime-gpu==1.24.4
    }
}
Ok "packages installed"

# ---------------------------------------------------------------- 4. optional LLM
$ollamaReady = $false
if ($SkipOllama) {
    Info "Skipping Ollama (-SkipOllama)"
} elseif (-not $ollamaCmd) {
    Step "Optional cleanup LLM (Ollama) - not installed, skipping"
    Info "LocalFlow works without it: the deterministic rules pass still removes fillers, applies"
    Info "spoken punctuation, self-corrections, your dictionary and snippets."
    Info "To add the smarter cleanup later:"
    Info "  1. install Ollama from $OLLAMA_DOWNLOAD"
    Info "  2. run:  ollama pull gemma3:4b"
    Info "  3. set  cleanup.level: medium  in config.yaml (or pick it in the tray menu) and restart"
} else {
    Step "Pulling the cleanup LLM with Ollama (gemma3:4b, ~3 GB)"
    $ok = $false
    for ($i = 1; $i -le 3 -and -not $ok; $i++) {
        & ollama pull gemma3:4b
        if ($LASTEXITCODE -eq 0) { $ok = $true }
        else { Warn "ollama pull failed (attempt $i of 3), retrying in 5 s..."; Start-Sleep 5 }
    }
    if ($ok) { $ollamaReady = $true; Ok "gemma3:4b ready" }
    else { Warn "Could not pull gemma3:4b. LocalFlow will run rules-only until you run: ollama pull gemma3:4b" }
}

# ---------------------------------------------------------------- 5. configuration
Step "Configuration"
& $venvPy -c "from localflow import config; config.load(); print('    config: ' + str(config.CONFIG_PATH))"
if ($LASTEXITCODE -ne 0) { Die "Could not create config.yaml." "See the error above." }

if ($cpuMode) {
    & $venvPy -c "from localflow import config; c=config.load(); c['asr']['allow_cpu_fallback']=True; config.save(c); print('    asr.allow_cpu_fallback: true (CPU mode)')"
    Info "Parakeet will run on the CPU (verified working). If it is too slow, try Whisper small:"
    Info "  asr.engine: whisper   and   asr.whisper_model: small   in config.yaml"
    Info "  (also set asr.whisper_compute_type: int8 for CPU)"
}
if (-not $ollamaReady -and -not $SkipOllama) {
    # Leave cleanup.level at its default. LocalFlow already detects Ollama at runtime and
    # falls back to rules-only when it is missing, so pinning the level here would quietly
    # downgrade the user forever once they do install Ollama.
    Info "Cleanup will run rules-only until Ollama is available; no config change needed."
}

# ---------------------------------------------------------------- 6. model + smoke test
if ($SkipSmoke) {
    Info "Skipping the model download and smoke test (-SkipSmoke)"
} else {
    Step "Speech model + smoke test"
    Info "Downloading NVIDIA Parakeet TDT 0.6B v2 into .\models - about 2.5 GB, one time only."
    Info "Nothing is downloaded again after this; LocalFlow runs fully offline."
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
    & $venvPy -m tests.smoke_asr
    if ($LASTEXITCODE -ne 0) {
        if ($cpuMode) {
            Die "The speech smoke test failed." "Scroll up for the error. Common causes: the download was interrupted (just re-run install.bat) or the disk filled up."
        } else {
            Die "The speech smoke test failed." "Scroll up for the error. If it says CUDA/cuDNN, update your NVIDIA driver ($NVIDIA_DRIVERS) or re-run with:  install.bat -CPU  to run without a GPU. See README.md -> Troubleshooting."
        }
    }
    Ok "speech recognition works"
}

# ---------------------------------------------------------------- 7. tests
Step "Running unit tests"
& $venvPy -m pytest -q tests
if ($LASTEXITCODE -ne 0) { Die "The unit tests failed." "That is a bug - please open an issue and paste the output above." }
Ok "tests passed"

# ---------------------------------------------------------------- done
$ver = ""
$old = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try { $ver = & $venvPy -c "import localflow; print(localflow.__version__)" 2>$null } catch { } finally { $ErrorActionPreference = $old }
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "   LocalFlow $ver is installed." -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "   Next step:  double-click run.bat" -ForegroundColor White
Write-Host "               wait for the small dot near the bottom of the screen to turn grey" -ForegroundColor Gray
Write-Host "               (a few seconds while the model loads)" -ForegroundColor Gray
Write-Host ""
Write-Host "   Then:       click into any text box, hold Ctrl+Win, talk, let go." -ForegroundColor White
Write-Host ""
Write-Host "   Esc cancels. Right-click the dot (or the tray icon) for the menu." -ForegroundColor Gray
if ($cpuMode) {
    Write-Host "   CPU mode: recognition takes noticeably longer than on a GPU, but it works." -ForegroundColor Yellow
}
if (-not $ollamaReady -and -not $SkipOllama) {
    Write-Host "   Rules-only cleanup (no Ollama). See README.md to add the LLM pass later." -ForegroundColor Yellow
}
Write-Host "   Something wrong? Run:  .\.venv\Scripts\python.exe -m localflow --doctor" -ForegroundColor Gray
Write-Host ""
exit 0
