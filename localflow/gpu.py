"""Make the pip-delivered CUDA 12 / cuDNN 9 DLLs discoverable.

Must be called BEFORE importing onnxruntime or ctranslate2 (faster-whisper).
There is no system CUDA toolkit on the target machine, so the runtime comes from
nvidia-cuda-runtime-cu12, nvidia-cublas-cu12, nvidia-cudnn-cu12 (9.x), nvidia-cufft-cu12,
nvidia-curand-cu12 wheels installed in the venv.
"""
from __future__ import annotations

import glob
import logging
import os
import site
import sys

log = logging.getLogger("localflow.gpu")

_registered: list[str] = []


def _site_dirs() -> list[str]:
    dirs: list[str] = []
    try:
        dirs.extend(site.getsitepackages())
    except Exception:  # pragma: no cover
        pass
    # venv fallback
    dirs.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    seen = set()
    out = []
    for d in dirs:
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


CPU_MODE_NOTE = "CPU mode (as configured): running without CUDA"


def register_cuda_dlls(cpu_mode: bool = False) -> list[str]:
    """Add every site-packages/nvidia/*/bin dir to the DLL search path and PATH.

    Returns the list of directories registered. Safe to call more than once.

    cpu_mode=True means the user asked for CPU (asr.allow_cpu_fallback), so a missing CUDA
    runtime is the expected outcome and is reported at INFO instead of WARNING.
    """
    if _registered:
        return list(_registered)
    for sp in _site_dirs():
        for d in sorted(glob.glob(os.path.join(sp, "nvidia", "*", "bin"))):
            if not os.path.isdir(d):
                continue
            try:
                os.add_dll_directory(d)
            except (AttributeError, OSError):  # non-Windows or bad path
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            _registered.append(d)
    if not _registered:
        if cpu_mode:
            log.info(CPU_MODE_NOTE)
        else:
            log.warning("No nvidia/*/bin directories found in site-packages; CUDA provider will fail")
    else:
        log.debug("Registered CUDA DLL dirs: %s", _registered)
    return list(_registered)


def cudnn_present() -> bool:
    """Quick check that a cuDNN 9 DLL exists in one of the registered dirs."""
    for d in _registered:
        if glob.glob(os.path.join(d, "cudnn64_9.dll")):
            return True
    return False
