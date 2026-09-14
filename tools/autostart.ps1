<#
Register / remove / inspect the per-user "LocalFlow" scheduled task.

This is the single definition of the autostart task. install.ps1 calls it, and so does the
"Start with Windows" item in the tray menu (localflow/autostart.py), so the task registered by
the installer and the one registered from the app are byte-for-byte the same.

Why a scheduled task and not a Startup shortcut: a Startup shortcut cannot delay itself past
the logon storm, cannot restart LocalFlow if it dies, and cannot run "when available" after a
missed logon. The task does all three, and being a per-user task it needs no elevation.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\autostart.ps1 -Action Status
  ...                                                                    -Action Enable
  ...                                                                    -Action Disable

Always prints one line of compact JSON: {"exists":bool,"state":"...","command":"...","ok":bool,"error":"..."}
#>
param(
    [ValidateSet("Status", "Enable", "Disable")]
    [string]$Action = "Status",
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"
$TaskName = "LocalFlow"
# 20 s: long enough for Tailscale, the audio stack and (usually) Ollama to be up before
# LocalFlow probes them. LocalFlow re-probes Ollama afterwards anyway, so this is only politeness.
$DelaySeconds = 20

if (-not $Repo) { $Repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath) }
try { $Repo = (Resolve-Path -LiteralPath $Repo).Path } catch { }
$Pythonw = Join-Path $Repo ".venv\Scripts\pythonw.exe"

function Out-Json($obj) { Write-Output ($obj | ConvertTo-Json -Compress) }

function Get-Task {
    try { return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch { return $null }
}

function Report([bool]$ok, [string]$err) {
    $t = Get-Task
    $cmd = ""
    if ($t) {
        $a = @($t.Actions)[0]
        if ($a) { $cmd = ("{0} {1}" -f $a.Execute, $a.Arguments).Trim() }
    }
    Out-Json ([pscustomobject]@{
        exists  = [bool]$t
        state   = if ($t) { "$($t.State)" } else { "" }
        command = $cmd
        ok      = $ok
        error   = $err
    })
}

switch ($Action) {
    "Status" { Report $true ""; exit 0 }

    "Disable" {
        try {
            if (Get-Task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
            Report $true ""
            exit 0
        } catch {
            Report $false $_.Exception.Message
            exit 1
        }
    }

    "Enable" {
        try {
            if (-not (Test-Path -LiteralPath $Pythonw)) {
                throw "$Pythonw does not exist - run install.bat first so the virtual environment is there."
            }
            $user = "$env:USERDOMAIN\$env:USERNAME"
            $taskAction = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m localflow" -WorkingDirectory $Repo
            $taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
            $taskTrigger.Delay = "PT${DelaySeconds}S"
            $taskSettings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -MultipleInstances IgnoreNew
            $taskPrincipal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
            # -Force updates the existing task in place, so re-running never makes a second one.
            Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $taskTrigger `
                -Settings $taskSettings -Principal $taskPrincipal -Force `
                -Description "Starts LocalFlow (local dictation) when $user signs in. Registered by LocalFlow." | Out-Null
            Report $true ""
            exit 0
        } catch {
            Report $false $_.Exception.Message
            exit 1
        }
    }
}
