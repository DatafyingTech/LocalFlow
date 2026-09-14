"""The "LocalFlow" per-user scheduled task: register it, remove it, report on it.

Windows signing a user out (Winlogon 7002) kills every app that user owns, LocalFlow and
Ollama included. Signing back in restores the desktop but not the apps, so the phone gets
"PC not reachable" until somebody double-clicks run.bat. A logon-triggered scheduled task
fixes that, and unlike a Startup shortcut it can delay itself past the logon storm and
restart LocalFlow if it dies.

The task itself is defined once, in tools/autostart.ps1, which install.ps1 and this module
both call - so the installer and the tray menu register exactly the same thing.

Reads use `schtasks` (about 50 ms, so the tray menu can call it); writes use the PowerShell
script (Register-ScheduledTask takes the rich settings schtasks cannot express). Stdlib only,
so --doctor can import it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "LocalFlow"
PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_DIR / "tools" / "autostart.ps1"

# no console window when the app (running under pythonw) shells out
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        creationflags=_NO_WINDOW, cwd=str(PROJECT_DIR),
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


# ---------------------------------------------------------------- read
def status() -> dict:
    """{'exists': bool, 'state': str, 'command': str} - never raises."""
    out = {"exists": False, "state": "", "command": ""}
    if sys.platform != "win32":
        return out
    try:
        rc, stdout, _err = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"], timeout=20.0)
    except Exception:  # noqa: BLE001
        return out
    if rc != 0:
        return out  # 1 = task not found
    out["exists"] = True
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "status" and not out["state"]:
            out["state"] = val
        elif key == "scheduled task state" and not out["state"]:
            out["state"] = val
        elif key == "task to run" and not out["command"]:
            out["command"] = val
    return out


def is_enabled() -> bool:
    return bool(status().get("exists"))


# ---------------------------------------------------------------- write
def _powershell(action: str) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "autostart is Windows-only"
    if not SCRIPT.is_file():
        return False, f"{SCRIPT} is missing"
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT), "-Action", action, "-Repo", str(PROJECT_DIR),
    ]
    try:
        rc, stdout, stderr = _run(cmd, timeout=90.0)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    info = {}
    for line in reversed(stdout.splitlines()):  # the JSON is the last line
        line = line.strip()
        if line.startswith("{"):
            try:
                info = json.loads(line)
            except ValueError:
                info = {}
            break
    if rc == 0 and info.get("ok"):
        return True, ""
    return False, (info.get("error") or stderr or stdout or f"exit code {rc}")


def enable() -> tuple[bool, str]:
    """Register (or update) the logon task. Returns (ok, error message)."""
    return _powershell("Enable")


def disable() -> tuple[bool, str]:
    """Remove the logon task. Returns (ok, error message)."""
    return _powershell("Disable")


# ---------------------------------------------------------------- Ollama's own autostart
def ollama_startup() -> tuple[bool, str]:
    """Does Ollama start itself at logon? (bool, where it was found).

    LocalFlow starting itself is only half the fix: if Ollama does not come back too, cleanup
    silently degrades to rules-only (which is what the re-probe in llm.py is for, but it still
    has nothing to find).
    """
    if sys.platform != "win32":
        return False, ""
    found: list[str] = []
    try:
        appdata = os.environ.get("APPDATA")
        if appdata:
            lnk = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Ollama.lnk"
            if lnk.is_file():
                found.append("Startup folder (Ollama.lnk)")
    except Exception:  # noqa: BLE001
        pass
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            i = 0
            while True:
                try:
                    name, _value, _typ = winreg.EnumValue(key, i)
                except OSError:
                    break
                if "ollama" in name.lower():
                    found.append(f"HKCU Run value {name!r}")
                i += 1
    except Exception:  # noqa: BLE001
        pass
    return bool(found), "; ".join(found)
