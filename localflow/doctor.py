"""`python -m localflow --doctor`: a one-page environment report to paste into a bug report.

Every check is independent and wrapped in its own try/except: a broken or missing piece is
reported as FAIL/WARN with the error message, never as a traceback, and never stops the rest of
the report. The whole thing runs with nothing but the standard library available.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "  "

_lines: list[str] = []

# --doctor output is written to be pasted into public bug reports, so identifying details
# (audio device names, the Ollama library, the install path) are withheld by default.
# `--doctor --verbose` shows them for local debugging.
VERBOSE = False

# True when the user configured CPU mode (asr.allow_cpu_fallback). A missing GPU, a missing
# CUDA provider and a missing cuDNN are then the expected result of that choice, not faults -
# reporting them as warnings sent CPU users hunting for a problem they had already decided not
# to have. Set by report() before the GPU checks run.
CPU_MODE = False
CPU_NOTE = "CPU mode, as configured"


def _emit(status: str, label: str, value: str = "") -> None:
    tag = f"[{status}]" if status.strip() else "     "
    body = f"{label}: {value}" if value else label
    _lines.append(f"{tag:6} {body}")


def _check(label: str, fn: Callable[[], tuple[str, str]]) -> None:
    """Run one check; report its exception instead of raising."""
    try:
        status, value = fn()
    except Exception as e:  # noqa: BLE001 - the whole point is to never crash
        status, value = FAIL, f"{type(e).__name__}: {e}"
    _emit(status, label, value)


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or p.stderr or "").strip()


def _size_gb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return round(total / (1024 ** 3), 2)


# ---------------------------------------------------------------- checks
def _safe_path(p: "Path | str") -> str:
    """Render a path without the Windows username.

    --doctor output is meant to be pasted into public bug reports, and an install under
    a home directory would otherwise publish that name.
    """
    try:
        s = str(Path(p).resolve())
        home = str(Path.home().resolve())
        if s.lower().startswith(home.lower()):
            return "~" + s[len(home):]
        return s
    except Exception:  # noqa: BLE001
        return str(p)



def _c_localflow() -> tuple[str, str]:
    from . import __version__

    return OK, f"{__version__}  ({_safe_path(Path(__file__).resolve().parent.parent)})"


def _c_python() -> tuple[str, str]:
    import struct

    bits = struct.calcsize("P") * 8
    v = sys.version_info
    status = OK if (v[:2] >= (3, 11) and v[:2] <= (3, 13) and bits == 64) else WARN
    return status, f"{platform.python_version()} ({bits}-bit) at {_safe_path(sys.executable)}"


def _c_venv() -> tuple[str, str]:
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return (OK if in_venv else WARN), ("running inside .venv" if in_venv else f"NOT in a venv (prefix {sys.prefix})")


def _c_os() -> tuple[str, str]:
    is64 = platform.machine().endswith("64")
    status = OK if (sys.platform == "win32" and is64) else WARN
    return status, f"{platform.platform()} / {platform.machine()}"


def _c_nvidia() -> tuple[str, str]:
    try:
        rc, out = _run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used", "--format=csv,noheader"])
    except FileNotFoundError:
        if CPU_MODE:
            return OK, f"{CPU_NOTE} - no NVIDIA GPU / driver needed"
        return WARN, "nvidia-smi not found - no NVIDIA GPU / driver (CPU mode only)"
    if rc != 0 or not out:
        if CPU_MODE:
            return OK, f"{CPU_NOTE} - GPU not used (nvidia-smi returned {rc})"
        return WARN, f"nvidia-smi returned {rc}: {out[:200]}"
    rows = [r.strip() for r in out.splitlines() if r.strip()]
    driver = rows[0].split(",")[1].strip() if "," in rows[0] else "?"
    status = OK
    try:
        if int(driver.split(".")[0]) < 525:
            status = WARN
    except ValueError:
        pass
    detail = " | ".join(rows)
    if status == WARN:
        detail += "   <- driver older than 525; update it for the CUDA 12 wheels"
    return status, detail


def _c_ort() -> tuple[str, str]:
    try:
        from . import gpu as gpumod

        gpumod.register_cuda_dlls(cpu_mode=CPU_MODE)
    except Exception as e:  # noqa: BLE001
        return WARN, f"could not register CUDA DLL dirs: {e}"
    try:
        import onnxruntime as ort
    except Exception as e:  # noqa: BLE001
        return FAIL, f"onnxruntime not importable ({type(e).__name__}: {e}) - run install.ps1"
    provs = ort.get_available_providers()
    has_cuda = "CUDAExecutionProvider" in provs
    if has_cuda:
        status, extra = OK, ""
    elif CPU_MODE:
        status, extra = OK, f"   <- {CPU_NOTE}"
    else:
        status = WARN
        extra = "   <- no CUDA provider: LocalFlow will need asr.allow_cpu_fallback: true"
    return status, f"onnxruntime {ort.__version__}, providers {provs}{extra}"


def _c_ort_cpu_wheel() -> tuple[str, str]:
    """The CPU-only `onnxruntime` wheel shadows onnxruntime-gpu in the same package dir."""
    try:
        from importlib import metadata

        names = {d.metadata["Name"].lower() for d in metadata.distributions() if d.metadata["Name"]}
    except Exception as e:  # noqa: BLE001
        return WARN, f"could not read installed packages: {e}"
    has_cpu, has_gpu = "onnxruntime" in names, "onnxruntime-gpu" in names
    if has_cpu and has_gpu:
        return FAIL, "both onnxruntime and onnxruntime-gpu are installed - the CPU wheel wins. Run: pip uninstall onnxruntime"
    if has_cpu:
        if CPU_MODE:
            return OK, f"onnxruntime (CPU wheel) only - {CPU_NOTE}"
        return WARN, "only the CPU onnxruntime wheel is installed (no GPU acceleration)"
    if has_gpu:
        return OK, "onnxruntime-gpu only (correct)"
    return WARN, "onnxruntime is not installed - run install.ps1"


def _c_cudnn() -> tuple[str, str]:
    try:
        from . import gpu as gpumod

        dirs = gpumod.register_cuda_dlls(cpu_mode=CPU_MODE)
    except Exception as e:  # noqa: BLE001
        return WARN, f"{type(e).__name__}: {e}"
    if not dirs:
        if CPU_MODE:
            return OK, f"not needed - {CPU_NOTE}"
        return WARN, "no site-packages/nvidia/*/bin directories (CUDA runtime wheels missing)"
    found = [d for d in dirs if Path(d, "cudnn64_9.dll").is_file()]
    if found:
        return OK, f"cudnn64_9.dll found in {_safe_path(found[0])}"
    if CPU_MODE:
        return OK, f"cudnn64_9.dll not present - {CPU_NOTE}"
    return WARN, f"cudnn64_9.dll NOT found in {len(dirs)} nvidia/*/bin dirs (nvidia-cudnn-cu12 missing?)"


def _c_ollama(host: str) -> tuple[str, str]:
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - localhost only
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return WARN, f"not reachable at {host} ({e.reason}) - LocalFlow runs rules-only cleanup"
    except Exception as e:  # noqa: BLE001
        return WARN, f"not reachable at {host} ({type(e).__name__}: {e})"
    models = [m.get("name", "?") for m in data.get("models", [])]
    # Deliberately not listing the model names: --doctor gets pasted publicly and the user's
    # library is their business. _c_ollama_model() reports the one model LocalFlow needs.
    return OK, f"reachable at {host}; {len(models)} model(s) available"


def _c_ollama_model(host: str, model: str) -> tuple[str, str]:
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return WARN, f"cannot verify {model} (Ollama not reachable)"
    names = [m.get("name", "") for m in data.get("models", [])]
    if any(n == model or n.split(":")[0] == model.split(":")[0] for n in names):
        return OK, f"{model} is available"
    return WARN, f"{model} is NOT pulled - run: ollama pull {model}"


def _c_autostart() -> tuple[str, str]:
    """Is the "LocalFlow" logon task registered?

    Without it, signing out of Windows (or a Winlogon session bounce) leaves LocalFlow down
    until somebody double-clicks run.bat - which, from the phone, looks exactly like Tailscale
    being broken.
    """
    from . import autostart

    st = autostart.status()
    if not st.get("exists"):
        return WARN, ('no "LocalFlow" scheduled task - LocalFlow will not come back after you sign out. '
                      'Turn on "Start with Windows" in the tray menu, or re-run install.bat -Autostart')
    state = st.get("state") or "?"
    cmd = _safe_path(st["command"]) if st.get("command") else "?"
    status = OK if state.lower() in ("ready", "running") else WARN
    return status, f'task "{autostart.TASK_NAME}" exists, state {state}; runs {cmd}'


def _c_ollama_autostart() -> tuple[str, str]:
    from . import autostart

    ok, where = autostart.ollama_startup()
    if ok:
        return OK, f"Ollama starts at sign-in ({where})"
    return WARN, ("no Ollama startup entry found (Startup folder Ollama.lnk / HKCU Run) - after a sign-out "
                  "Ollama may stay down; LocalFlow then runs rules-only until you start it")


def _c_audio() -> tuple[str, str]:
    try:
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        return FAIL, f"sounddevice not importable ({type(e).__name__}: {e}) - run install.ps1"
    devs = sd.query_devices()
    # Device names often include a personal device ("Kim's Pixel"), so they are hidden unless
    # --verbose is passed. The index is enough to set audio.device in config.yaml.
    if VERBOSE:
        ins = [f"[{i}] {d['name']}" for i, d in enumerate(devs) if d.get("max_input_channels", 0) > 0]
    else:
        ins = [f"[{i}] ({d.get('max_input_channels', 0)} ch)"
               for i, d in enumerate(devs) if d.get("max_input_channels", 0) > 0]
    if not ins:
        return FAIL, (
            "no audio input devices found. Plug in or enable a microphone, then check "
            "Settings > Privacy & security > Microphone > 'Let desktop apps access your microphone'"
        )
    try:
        default_in = sd.default.device[0]
        if isinstance(default_in, int) and default_in >= 0:
            default = devs[default_in]["name"] if VERBOSE else f"[{default_in}]"
        else:
            default = "(system default)"
    except Exception:  # noqa: BLE001
        default = "(unknown)"
    hidden_note = "(device names hidden; run --doctor --verbose to show them)"
    tail = "" if VERBOSE else ("\n       " + hidden_note)
    body = ("\n       ").join(ins)
    return OK, f"{len(ins)} input device(s), default: {default}\n       {body}{tail}"


def _c_mic_access(cfg: dict[str, Any]) -> tuple[str, str]:
    """Actually open the microphone.

    Windows can list a microphone and still refuse to give a desktop app any audio from it:
    Settings > Privacy & security > Microphone > "Let desktop apps access your microphone".
    When that is off, recording silently produces nothing, so we open the stream and read a
    block here rather than trusting the device list.
    """
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        return WARN, f"cannot test (sounddevice/numpy not importable: {type(e).__name__}: {e})"

    privacy = ("Windows is blocking microphone access. Turn on Settings > Privacy & security > "
               "Microphone > 'Let desktop apps access your microphone', then try again.")
    audio_cfg = cfg.get("audio", {}) if isinstance(cfg, dict) else {}
    want = str(audio_cfg.get("device") or "")
    rate = int(audio_cfg.get("sample_rate") or 16000)

    device = None
    if want:
        try:
            matches = [i for i, d in enumerate(sd.query_devices())
                       if d.get("max_input_channels", 0) > 0 and want.lower() in str(d["name"]).lower()]
        except Exception:  # noqa: BLE001
            matches = []
        if not matches:
            return WARN, f"audio.device {want!r} matches no input device - LocalFlow will use the system default"
        device = matches[0]

    try:
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32", device=device, blocksize=1024) as s:
            block, overflowed = s.read(1024)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        hint = privacy if ("Unanticipated host error" in msg or "-9999" in msg or "denied" in msg.lower()) else \
            "Close whatever else is using the microphone, or set audio.device in config.yaml."
        return FAIL, f"could not open the microphone ({type(e).__name__}: {msg}). {hint}"

    peak = float(np.max(np.abs(block))) if block.size else 0.0
    _dev = device if device is not None else sd.default.device[0]
    name = sd.query_devices(_dev)["name"] if VERBOSE else f"input device [{_dev}]"
    if peak == 0.0:
        return WARN, (f"opened '{name}' at {rate} Hz but the first block was digital silence. "
                      f"That is normal in a quiet room; if recording never works, check: {privacy}")
    return OK, f"opened '{name}' at {rate} Hz and read audio (peak {peak:.6f})"


def _c_deps() -> tuple[str, str]:
    missing = []
    for mod in ("yaml", "numpy", "requests", "pyperclip", "pynput", "pystray", "PIL", "onnx_asr", "tkinter"):
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    if missing:
        return FAIL, f"missing/unimportable: {', '.join(missing)} - run install.ps1"
    return OK, "all runtime imports available"


def _c_config(cfg_path: Path) -> tuple[str, str]:
    if cfg_path.is_file():
        return OK, _safe_path(cfg_path)
    return WARN, f"{_safe_path(cfg_path)} does not exist yet (it is created on first run)"


def _c_models(models_dir: Path) -> tuple[str, str]:
    if not models_dir.exists():
        return WARN, f"{models_dir} does not exist - the model downloads on first run (~2.5 GB)"
    gb = _size_gb(models_dir)
    status = OK if gb > 1.0 else WARN
    note = "" if status == OK else "   <- looks too small; the download may have been interrupted"
    return status, f"{models_dir}  ({gb} GB){note}"


def _c_runtime_files(project: Path) -> tuple[str, str]:
    bits = []
    for name in ("localflow.log", "history.jsonl"):
        p = project / name
        bits.append(f"{name}: {'present' if p.is_file() else 'not created yet'}")
    return INFO, "; ".join(bits)


def _c_disk(project: Path) -> tuple[str, str]:
    """Both drives that matter: the LocalFlow folder, and %USERPROFILE% where Ollama keeps models."""
    import shutil

    parts, status = [], OK
    home = Path.home()
    seen: set[str] = set()
    for path, what, need in ((project, "LocalFlow folder", 3.0), (home, "Ollama models (%USERPROFILE%\\.ollama)", 2.0)):
        drive = (path.drive or path.anchor)
        if drive.lower() in seen:
            continue
        seen.add(drive.lower())
        try:
            free = round(shutil.disk_usage(path).free / (1024 ** 3), 1)
        except OSError as e:
            parts.append(f"{drive} unreadable ({e})")
            status = WARN
            continue
        if free < need:
            status = WARN
            parts.append(f"{free} GB free on {drive} - {what}   <- low")
        else:
            parts.append(f"{free} GB free on {drive} - {what}")
    return status, "; ".join(parts)


# ---------------------------------------------------------------- report
def report() -> str:
    _lines.clear()
    _lines.append("LocalFlow diagnostic report")
    _lines.append("=" * 70)

    # config is needed by several checks; failing to read it must not stop the report
    cfg: dict[str, Any] = {}
    cfg_path = Path("config.yaml")
    project = Path(__file__).resolve().parent.parent
    try:
        from . import config as cfgmod

        cfg_path = cfgmod.CONFIG_PATH
        cfg = cfgmod.load()
    except Exception as e:  # noqa: BLE001
        _emit(WARN, "config", f"could not load ({type(e).__name__}: {e}); using built-in defaults")
        try:
            from .config import DEFAULTS

            cfg = DEFAULTS
        except Exception:  # noqa: BLE001
            cfg = {}

    llm_cfg = cfg.get("llm", {}) if isinstance(cfg, dict) else {}
    asr_cfg = cfg.get("asr", {}) if isinstance(cfg, dict) else {}
    host = llm_cfg.get("host") or "http://127.0.0.1:11434"
    model = llm_cfg.get("model") or "gemma3:4b"
    models_dir = project / str(asr_cfg.get("models_dir") or "models")

    _lines.append("")
    _lines.append("-- environment ------------------------------------------------------")
    _check("LocalFlow", _c_localflow)
    _check("Python", _c_python)
    _check("Virtualenv", _c_venv)
    _check("OS", _c_os)
    _check("Disk", lambda: _c_disk(project))

    global CPU_MODE
    CPU_MODE = bool(asr_cfg.get("allow_cpu_fallback"))

    _lines.append("")
    _lines.append("-- GPU --------------------------------------------------------------")
    _check("NVIDIA", _c_nvidia)
    _check("ONNX Runtime", _c_ort)
    _check("ORT wheels", _c_ort_cpu_wheel)
    _check("cuDNN", _c_cudnn)
    _emit(INFO, "asr.engine", f"{asr_cfg.get('engine', '?')} (allow_cpu_fallback: {asr_cfg.get('allow_cpu_fallback')})")
    if CPU_MODE:
        _emit(INFO, "GPU rows above", "CPU mode is configured, so the GPU is not expected to be used")

    _lines.append("")
    _lines.append("-- cleanup LLM (optional) -------------------------------------------")
    _check("Ollama", lambda: _c_ollama(host))
    _check("Model", lambda: _c_ollama_model(host, model))
    _emit(INFO, "cleanup.level", str((cfg.get("cleanup") or {}).get("level", "?")))
    _emit(INFO, "Ollama re-probe", "every 30 s while unreachable; llm_ok recovers without a restart")

    _lines.append("")
    _lines.append("-- autostart --------------------------------------------------------")
    _check("LocalFlow task", _c_autostart)
    _check("Ollama at sign-in", _c_ollama_autostart)

    _lines.append("")
    _lines.append("-- audio ------------------------------------------------------------")
    _check("Input devices", _c_audio)
    _check("Mic access", lambda: _c_mic_access(cfg))
    _emit(INFO, "audio.device", repr((cfg.get("audio") or {}).get("device", "")) + "  ('' = system default)")

    _lines.append("")
    _lines.append("-- packages / paths -------------------------------------------------")
    _check("Runtime deps", _c_deps)
    _check("Config", lambda: _c_config(cfg_path))
    _check("Model cache", lambda: _c_models(models_dir))
    _check("Runtime files", lambda: _c_runtime_files(project))

    fails = sum(1 for ln in _lines if ln.startswith("[FAIL"))
    warns = sum(1 for ln in _lines if ln.startswith("[WARN"))
    _lines.append("")
    _lines.append("-" * 70)
    if fails:
        _lines.append(f"{fails} problem(s) and {warns} warning(s). LocalFlow will probably not work; see the FAIL lines.")
    elif warns:
        _lines.append(f"No blocking problems, {warns} warning(s). LocalFlow should run (check the WARN lines for speed/feature limits).")
    else:
        _lines.append("Everything checks out.")
    if VERBOSE:
        _lines.append("VERBOSE: this report includes your audio device names and full paths.")
        _lines.append("Review it before posting anywhere public.")
    else:
        _lines.append("Safe to paste into a bug report: no dictated text, no device names, no")
        _lines.append("username in paths. Use --doctor --verbose locally to see those details.")
    return "\n".join(_lines)


def main(verbose: bool = False) -> int:
    global VERBOSE
    VERBOSE = verbose
    print(report())
    return 0
