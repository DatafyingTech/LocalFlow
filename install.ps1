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
  -NoPythonInstall do not offer to install Python 3.12 with winget when none is found
  -InstallPython   install Python 3.12 with winget without asking (needed for unattended runs:
                   with no console to answer from, the installer never installs Python by itself)
  -CPU             force the no-GPU configuration: installs requirements-cpu.txt instead
                   (no CUDA wheels, ~2 GB less), slower but works without NVIDIA
  -SkipOllama      do not install/pull the optional cleanup LLM
  -SkipSmoke       do not download the model / run the ASR smoke test (the app then
                   downloads the model itself, with a notification, on its first run)
  -Autostart       register the per-user "LocalFlow" logon task without asking
  -NoAutostart     do not register it (and do not ask)
  -LowVram         force the compact int8 speech model (about 0.6 GB of VRAM instead of 3.0 GB,
                   about a third of a second slower per dictation, same words)
  -FullPrecision   force the full-precision speech model even on a small GPU
  -Update          refresh the project files from GitHub first (git pull, or the ZIP), then
                   install normally and restart LocalFlow if it was running. Use update.bat.
  -Force           delete and recreate .venv from scratch
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$NoPythonInstall,
    [switch]$InstallPython,
    [switch]$CPU,
    [switch]$SkipOllama,
    [switch]$SkipSmoke,
    [switch]$Autostart,
    [switch]$NoAutostart,
    [switch]$LowVram,
    [switch]$FullPrecision,
    [switch]$Update,
    [string]$Repo = "",    # the LocalFlow folder to work on; update.bat passes it because it
                           # runs this script from a copy in %TEMP% (see update.bat)
    [switch]$Force,
    [switch]$FetchOnly     # download the project files next to this script and stop
)

$ErrorActionPreference = "Stop"
if ($Repo) {
    if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
        Write-Host "  -Repo `"$Repo`" is not a folder." -ForegroundColor Red
        exit 1
    }
    $root = (Resolve-Path -LiteralPath $Repo).Path
} else {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $root

$PY_DOWNLOAD = "https://www.python.org/downloads/windows/"
# The specific 3.12 release page. python.org's big download button hands out 3.14, which
# LocalFlow cannot use yet, so never point a first-time installer at the front page.
$PY_312_RELEASE = "https://www.python.org/downloads/release/python-3129/"
$OLLAMA_DOWNLOAD = "https://ollama.com/download"
$NVIDIA_DRIVERS = "https://www.nvidia.com/Download/index.aspx"
# Disk is checked on two drives: the repo drive holds .venv + the ASR model, and the
# user-profile drive holds Ollama's model store (%USERPROFILE%\.ollama, usually C:).
$MIN_DISK_REPO_GB = 5
$MIN_DISK_PROFILE_GB = 4
$MIN_DISK_TEMP_GB = 4      # pip unpacks wheels in TEMP (on C:) before installing them
$MIN_DRIVER = 525
# Below this much video memory the full-precision speech model (measured: 3,019 MB) leaves
# nothing for the cleanup model or a game, so the compact int8 build is chosen instead.
$MIN_VRAM_MIB = 6144

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    [ok]   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    [warn] $msg" -ForegroundColor Yellow }
function Info($msg) { Write-Host "    $msg" -ForegroundColor Gray }
# Unattended runs must not decide for the user. Under `install.bat < nul` (a pipeline, a CI job,
# a remote-management tool) [Environment]::UserInteractive is STILL true and Read-Host returns ""
# immediately - so a prompt that defaults to yes would quietly register a scheduled task or
# install Python that nobody asked for. Only prompt when there is a real console to answer from.
function Test-InputRedirected {
    try { return [Console]::IsInputRedirected } catch { return ($Host.Name -ne 'ConsoleHost') }
}
function Test-CanPrompt {
    return ([Environment]::UserInteractive -and -not (Test-InputRedirected))
}

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
# ---------------------------------------------------------------- 0. am I inside a complete copy?
# People save install.bat on its own, or double-click it from inside the ZIP (Explorer then runs it
# from a temp folder with nothing else in it). Either way the rest of the project is missing and
# every later step fails with a confusing "No such file". Detect it here and either fetch the
# project or explain exactly what to do.
$PROJECT_ZIP = "https://github.com/DatafyingTech/LocalFlow/archive/refs/heads/main.zip"
$markers = @("requirements.txt", "run.bat", "localflow\__init__.py", "localflow\__main__.py")
$missing = @($markers | Where-Object { -not (Test-Path (Join-Path $root $_)) })
if ($missing.Count -gt 0) {
    Step "This folder does not contain LocalFlow yet"
    Info "Running from: $root"
    Info ("Missing: " + ($missing -join ", "))
    $tmpRoot = [IO.Path]::GetTempPath().TrimEnd('\')
    if ($root.StartsWith($tmpRoot, [StringComparison]::OrdinalIgnoreCase) -or $root -match '\\Temp\d*_.*\.zip') {
        Die "It looks like install.bat was opened from inside the ZIP file, so Windows ran it from a temporary folder." `
            "Right-click the ZIP, choose Extract All, open the extracted LocalFlow-main folder (there is a second LocalFlow-main inside it), and double-click install.bat there."
    }
    Info "Downloading the project so the install can continue (about 1 MB)..."
    $zip = Join-Path $tmpRoot ("LocalFlow-" + [guid]::NewGuid().ToString("N") + ".zip")
    $ext = $zip -replace '\.zip$', ''
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PROJECT_ZIP -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $ext -Force
        $inner = Get-ChildItem -Path $ext -Directory | Select-Object -First 1
        if (-not $inner) { throw "the downloaded archive was empty" }
        Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $root -Recurse -Force
        Ok "project files copied into $root"
    } catch {
        Die "Could not download the project: $($_.Exception.Message)" `
            "Download it yourself: $PROJECT_ZIP - extract it, and run install.bat from inside the extracted folder."
    } finally {
        Remove-Item -Path $zip, $ext -Recurse -Force -ErrorAction SilentlyContinue
    }
    $stillMissing = @($markers | Where-Object { -not (Test-Path (Join-Path $root $_)) })
    if ($stillMissing.Count -gt 0) {
        Die ("The download finished but these files are still missing: " + ($stillMissing -join ", ")) `
            "Download $PROJECT_ZIP by hand, extract it, and run install.bat from inside the extracted folder."
    }
}
# ---------------------------------------------------------------- 0b. -Update: refresh the files
# update.bat runs this script from %TEMP%, because the step below overwrites install.ps1 and
# update.bat themselves. Nothing here deletes anything: config.yaml, history.jsonl,
# localflow.log, models\ and .venv\ are not in the repository, so they simply stay put.
function Get-LocalFlowVersion([string]$dir) {
    $init = Join-Path $dir "localflow\__init__.py"
    if (-not (Test-Path $init)) { return "" }
    $m = [regex]::Match((Get-Content -Raw -LiteralPath $init), '__version__\s*=\s*"([^"]+)"')
    if ($m.Success) { return $m.Groups[1].Value }
    return ""
}

function Get-LocalFlowProcesses([string]$dir) {
    $needle = $dir.TrimEnd('\').ToLowerInvariant()
    try {
        return @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($needle) })
    } catch {
        return @()
    }
}

$updateOldVersion = ""
$updateWasRunning = $false
if ($Update) {
    Step "Updating LocalFlow in $root"
    $updateOldVersion = Get-LocalFlowVersion $root
    $updateWasRunning = ((Get-LocalFlowProcesses $root).Count -gt 0)
    if ($updateWasRunning) { Info "LocalFlow is running; it will be restarted when the update finishes." }

    $git = Get-Command git -ErrorAction SilentlyContinue
    $isRepo = (Test-Path (Join-Path $root ".git"))
    $pulled = $false
    if ($git -and $isRepo) {
        Info "This is a git checkout: git pull --ff-only"
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { & git -C $root pull --ff-only } catch { Warn "git pull failed: $($_.Exception.Message)" } finally { $ErrorActionPreference = $old }
        if ($LASTEXITCODE -eq 0) { $pulled = $true; Ok "files updated with git" }
        else { Warn "git pull --ff-only did not succeed; falling back to the ZIP from GitHub." }
    }
    if (-not $pulled) {
        Info "Downloading the latest files from GitHub (about 1 MB)..."
        $tmpRoot = [IO.Path]::GetTempPath().TrimEnd('\')
        $zip = Join-Path $tmpRoot ("LocalFlow-update-" + [guid]::NewGuid().ToString("N") + ".zip")
        $ext = $zip -replace '\.zip$', ''
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $PROJECT_ZIP -OutFile $zip -UseBasicParsing
            Expand-Archive -Path $zip -DestinationPath $ext -Force
            $inner = Get-ChildItem -Path $ext -Directory | Select-Object -First 1
            if (-not $inner) { throw "the downloaded archive was empty" }
            # Copy over the top. The archive holds no config.yaml, history.jsonl, localflow.log,
            # models\ or .venv\, so everything of yours survives untouched.
            Copy-Item -Path (Join-Path $inner.FullName "*") -Destination $root -Recurse -Force
            Ok "files updated from $PROJECT_ZIP"
        } catch {
            Die "Could not download the update: $($_.Exception.Message)" `
                "Check your internet connection and try again, or download $PROJECT_ZIP by hand and extract it over this folder."
        } finally {
            Remove-Item -Path $zip, $ext -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    $newVersion = Get-LocalFlowVersion $root
    if ($updateOldVersion -and $newVersion -and $updateOldVersion -eq $newVersion) {
        Info "LocalFlow $newVersion - already up to date."
    } else {
        Ok "LocalFlow $(if ($updateOldVersion) { $updateOldVersion } else { '?' }) -> $(if ($newVersion) { $newVersion } else { '?' })"
    }
}

if ($FetchOnly) {
    Ok "Project files are in place. Run install.bat again (without -FetchOnly) to install."
    exit 0
}

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

# pip unpacks every wheel in the temp folder before installing it. We redirect TEMP onto the
# LocalFlow drive for the pip steps (see below), so this is only a warning - but if that
# redirection fails, this is the drive that fills up.
try {
    $tempDrive = Get-FreeGB ([IO.Path]::GetTempPath())
    if ($tempDrive -and $tempDrive.FreeGB -lt $MIN_DISK_TEMP_GB `
        -and (-not $repoDrive -or $repoDrive.Drive -ne $tempDrive.Drive)) {
        Warn "Only $($tempDrive.FreeGB) GB free on drive $($tempDrive.Drive): - pip needs about $MIN_DISK_TEMP_GB GB of temporary space on $($tempDrive.Drive): during install."
        Info "LocalFlow moves that temporary space onto drive $(if ($repoDrive) { $repoDrive.Drive } else { '?' }): for you, so this usually does not matter. Free up space on $($tempDrive.Drive): if the install stops with 'No space left on device'."
    }
} catch { }

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

# Look at every Python we can reach and remember what each one was, so that when none of them
# is usable we can say WHICH version we found instead of the useless "no Python found" - the
# common case today is python.org's big download button handing out 3.14.
function Find-Pythons {
    $found = @()
    $seen = @{}
    $candidates = @(@("py", "-3.12"), @("py", "-3.13"), @("py", "-3.11"), @("python"), @("python3"))
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $p = Probe-Python $c
        if (-not $p) { continue }
        $key = "$($p.Exe)".ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $found += $p
    }
    return $found
}

function Describe-Pythons($list) {
    if (-not $list -or $list.Count -eq 0) { return "" }
    return (($list | ForEach-Object { "$($_.Label) at $($_.Exe)" }) -join "; ")
}

function Refresh-PathFromRegistry {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = ($machine + ";" + $user)
}

$pyInfo = $null
if ($Python) {
    if (-not (Test-Path $Python)) { Die "-Python `"$Python`" does not exist." "Pass the full path to python.exe, or drop -Python to auto-detect." }
    $pyInfo = Probe-Python @($Python)
    if (-not $pyInfo) { Die "Could not run `"$Python`"." "Is it really a python.exe?" }
    if (-not (Test-Supported $pyInfo)) {
        Die "$($pyInfo.Label) at $Python is not supported." "LocalFlow needs 64-bit Python 3.11, 3.12 or 3.13. Download: $PY_312_RELEASE"
    }
} else {
    $all = Find-Pythons
    $pyInfo = $all | Where-Object { Test-Supported $_ } | Select-Object -First 1

    if (-not $pyInfo) {
        $what = Describe-Pythons $all
        if ($what) {
            Warn "Found $what but LocalFlow needs 3.11 to 3.13."
        } else {
            Warn "No Python at all was found on this computer (looked for py -3.12, py -3.13, py -3.11, python and python3)."
        }

        $winget = Get-Command winget -ErrorAction SilentlyContinue
        $tryWinget = $false
        if ($NoPythonInstall) {
            Info "Not offering to install Python (-NoPythonInstall)."
        } elseif (-not $winget) {
            Info "Windows' package manager (winget) is not available here, so Python cannot be installed for you."
        } elseif ($InstallPython) {
            $tryWinget = $true
            Info "-InstallPython given: installing Python 3.12 with winget."
        } elseif (Test-CanPrompt) {
            $answer = Read-Host "    Install Python 3.12 now with Windows' package manager? Press Enter for yes, n for no"
            $tryWinget = ($answer -notmatch '^\s*[nN]')
        } else {
            # No console to answer from: installing Python is far too big a decision to take on
            # a silent non-answer, so it is never done unless -InstallPython said so explicitly.
            Info "No interactive console, so Python is not installed automatically."
            Info "Install 64-bit Python 3.12 yourself: $PY_312_RELEASE"
            Info "(or re-run with -InstallPython to let winget do it unattended)"
        }

        if ($tryWinget) {
            Step "Installing Python 3.12 with winget (about 30 MB)"
            $old = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements --override "/quiet PrependPath=1 Include_launcher=1"
            } catch {
                Warn "winget could not run: $($_.Exception.Message)"
            } finally {
                $ErrorActionPreference = $old
            }
            Refresh-PathFromRegistry
            $all = Find-Pythons
            $pyInfo = $all | Where-Object { Test-Supported $_ } | Select-Object -First 1
            if ($pyInfo) { Ok "Python 3.12 installed" }
        }
    }

    if (-not $pyInfo) {
        $what = Describe-Pythons (Find-Pythons)
        $msg = if ($what) { "Found $what but LocalFlow needs 3.11 to 3.13." } else { "No supported Python found (looked for py -3.12, py -3.13, py -3.11, python and python3 on PATH)." }
        Die $msg `
            "Install 64-bit Python 3.12 from $PY_312_RELEASE - scroll down to `"Windows installer (64-bit)`" and tick `"Add python.exe to PATH`". Do not use the big yellow Download button on python.org: it gives a newer Python that LocalFlow cannot use yet. Then run install.bat again. Already have a suitable Python? Pass it: install.bat -Python C:\path\to\python.exe"
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

# --- how much video memory? The full-precision speech model needs about 3.0 GB, and the
# cleanup model wants another 3.8 GB, so a small card is much happier with the compact int8
# build: same accuracy in our tests, about a third of a second slower per dictation.
$vramMiB = 0
if ($gpuOk -and $smiPath) {
    try {
        $v = & $smiPath --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $first = (("$v" -split "`n")[0]).Trim()
            [void][int]::TryParse($first, [ref]$vramMiB)
        }
    } catch { }
}

$useInt8 = $false
if ($LowVram -and $FullPrecision) {
    Die "-LowVram and -FullPrecision cannot both be given." "Pick one, or neither and let the installer decide from your GPU."
} elseif ($LowVram) {
    $useInt8 = $true
    Info "-LowVram given: LocalFlow will use its compact speech model."
} elseif ($FullPrecision) {
    Info "-FullPrecision given: LocalFlow will use the full-precision speech model."
} elseif ($vramMiB -gt 0) {
    $vramGB = [math]::Round($vramMiB / 1024, 1)
    if ($vramMiB -lt $MIN_VRAM_MIB) {
        $useInt8 = $true
        Write-Host "    Your GPU has $vramGB GB of video memory, so LocalFlow will use its compact" -ForegroundColor Gray
        Write-Host "    speech model: same accuracy in our tests, about a third of a second slower" -ForegroundColor Gray
        Write-Host "    per dictation, and about 0.6 GB of video memory instead of 3.0 GB." -ForegroundColor Gray
        Write-Host "    Change it any time: right-click the dot -> Low VRAM mode." -ForegroundColor Gray
    } else {
        Ok "$vramGB GB of video memory - the full-precision speech model fits comfortably"
    }
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
    if (Test-CanPrompt) {
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
    Step "Installing Python packages from $reqFile - about 2-3 GB, 5-15 minutes on a normal connection"
}
if ($reqFile -eq "requirements-cpu.txt") {
    # if this venv was previously a GPU install, drop the GPU wheel so the CPU one can take over
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $venvPy -m pip uninstall -y onnxruntime-gpu 2>$null | Out-Null } catch { } finally { $ErrorActionPreference = $old }
}

# pip downloads and unpacks every wheel in %TEMP% before installing it, and %TEMP% is on C: even
# when LocalFlow lives on another drive. The CUDA wheels alone are ~2 GB, so a small C: can run
# out of space mid-install although the pre-flight passed. Keep the transient on the LocalFlow
# drive instead, and put it back afterwards.
$pipTmp = Join-Path $root ".venv\tmp"
$oldTmp = $env:TMP
$oldTemp = $env:TEMP
try {
    New-Item -ItemType Directory -Path $pipTmp -Force | Out-Null
    $env:TMP = $pipTmp
    $env:TEMP = $pipTmp
    Info "pip will unpack into $pipTmp (keeps a few GB of temporary files off your C: drive)"
} catch {
    Warn "Could not create $pipTmp; pip will use the normal temp folder on C:."
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

# hand TEMP back to Windows and drop the scratch folder (it can hold a few GB after the CUDA wheels)
$env:TMP = $oldTmp
$env:TEMP = $oldTemp
Remove-Item -Recurse -Force $pipTmp -ErrorAction SilentlyContinue
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

if ($useInt8) {
    & $venvPy -c "from localflow import config; c=config.load(); c['asr']['parakeet_quantization']='int8'; config.save(c); print('    asr.parakeet_quantization: int8 (Low VRAM mode)')"
} elseif ($FullPrecision) {
    & $venvPy -c "from localflow import config; c=config.load(); c['asr']['parakeet_quantization']=None; config.save(c); print('    asr.parakeet_quantization: null (full precision)')"
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
    Info "Downloading ~2.5 GB, progress below. A long pause with no output is normal on a slow link."
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

# ---------------------------------------------------------------- 6b. autostart
# Signing out of Windows kills every app the user owns - LocalFlow and Ollama included - and
# signing back in restores neither. A per-user logon task is the only thing that brings
# LocalFlow back on its own, so it is offered (default yes) on every interactive install.
Step "Start LocalFlow automatically when you sign in"
$wantAuto = $false
if ($Update) {
    Info "Update: keeping your existing autostart setting exactly as it is."
} elseif ($NoAutostart) {
    Info "Skipping autostart (-NoAutostart). Turn it on later from the tray menu: Start with Windows."
} elseif ($Autostart) {
    $wantAuto = $true
} elseif (Test-CanPrompt) {
    Info "Windows closes every app when you sign out, and does not reopen them when you sign back in."
    Info "A scheduled task starts LocalFlow 20 s after each sign-in and restarts it if it crashes"
    Info "(Quit from the menu is respected)."
    Info "You can change this any time: right-click the dot -> Start with Windows."
    $answer = Read-Host "    Start LocalFlow automatically when you sign in to Windows? Press Enter for yes, or type n then Enter for no"
    $wantAuto = ($answer -notmatch '^\s*[nN]')
} else {
    Info "Not starting automatically (no interactive console). Turn it on later from the tray menu: Start with Windows."
    Info "Or re-run the installer with -Autostart to register it now."
}

$autoOn = $false
if ($wantAuto) {
    $autoScript = Join-Path $root "tools\autostart.ps1"
    if (-not (Test-Path $autoScript)) {
        Warn "tools\autostart.ps1 is missing; cannot register the task."
    } else {
        $json = ""
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { $json = & $autoScript -Action Enable -Repo $root } catch { Warn "Autostart failed: $($_.Exception.Message)" } finally { $ErrorActionPreference = $old }
        $res = $null
        try { $res = ("$json" -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1) | ConvertFrom-Json } catch { }
        if ($res -and $res.ok -and $res.exists) {
            $autoOn = $true
            Ok "scheduled task 'LocalFlow' registered (at sign-in, 20 s delay, restarts up to 10 times)"
        } else {
            $why = if ($res) { $res.error } else { "$json" }
            Warn "Could not register the task: $why"
            Info "You can still turn it on later from the tray menu: Start with Windows."
        }
    }
}

# ---------------------------------------------------------------- 7. tests
Step "Running unit tests"
& $venvPy -m pytest -q tests
if ($LASTEXITCODE -ne 0) { Die "The unit tests failed." "That is a bug - please open an issue and paste the output above." }
Ok "tests passed"

# ---------------------------------------------------------------- 7b. restart after an update
$restarted = $false
if ($Update -and $updateWasRunning) {
    Step "Restarting LocalFlow"
    foreach ($p in (Get-LocalFlowProcesses $root)) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Warn "Could not stop process $($p.ProcessId): $($_.Exception.Message)" }
    }
    Start-Sleep -Milliseconds 800
    $task = $null
    try { $task = Get-ScheduledTask -TaskName "LocalFlow" -ErrorAction Stop } catch { }
    if ($task) {
        try { Start-ScheduledTask -TaskName "LocalFlow" -ErrorAction Stop; $restarted = $true; Ok "started from the 'LocalFlow' scheduled task" }
        catch { Warn "Could not start the scheduled task: $($_.Exception.Message)" }
    }
    if (-not $restarted) {
        $runBat = Join-Path $root "run.bat"
        if (Test-Path $runBat) {
            try { Start-Process -FilePath $runBat -WorkingDirectory $root -WindowStyle Hidden; $restarted = $true; Ok "started with run.bat" }
            catch { Warn "Could not run run.bat: $($_.Exception.Message)" }
        }
    }
    if (-not $restarted) { Warn "LocalFlow could not be restarted automatically - double-click run.bat." }
}

# ---------------------------------------------------------------- done
$ver = ""
$old = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try { $ver = & $venvPy -c "import localflow; print(localflow.__version__)" 2>$null } catch { } finally { $ErrorActionPreference = $old }
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
if ($Update) {
    if ($updateOldVersion -and $ver -and $updateOldVersion -ne "$ver".Trim()) {
        Write-Host "   LocalFlow updated: $updateOldVersion -> $ver" -ForegroundColor Green
    } else {
        Write-Host "   LocalFlow $ver is up to date." -ForegroundColor Green
    }
} else {
    Write-Host "   LocalFlow $ver is installed." -ForegroundColor Green
}
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
if ($Update) {
    if ($restarted) {
        Write-Host "   LocalFlow has been restarted for you." -ForegroundColor White
    } else {
        Write-Host "   Next step:  double-click run.bat" -ForegroundColor White
    }
    Write-Host "   Your config.yaml, history and downloaded models were left alone." -ForegroundColor Gray
    Write-Host ""
    exit 0
}
Write-Host "   Next step:  double-click run.bat" -ForegroundColor White
Write-Host "               a blue dot appears near the bottom of the screen while it loads;" -ForegroundColor Gray
Write-Host "               when it turns grey, you are ready (5-20 s, longer the first time)" -ForegroundColor Gray
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
if ($autoOn) {
    Write-Host "   LocalFlow will start by itself the next time you sign in to Windows." -ForegroundColor Gray
} else {
    Write-Host "   Not starting automatically. Right-click the dot -> Start with Windows to change that." -ForegroundColor Gray
}
Write-Host "   Something wrong? Double-click doctor.bat and paste what it prints." -ForegroundColor Gray
Write-Host ""
exit 0
