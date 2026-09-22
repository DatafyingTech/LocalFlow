"""ASR engines.

ParakeetEngine: NVIDIA parakeet-tdt-0.6b-v2 via onnx-asr on ONNX Runtime CUDA EP (primary).
WhisperEngine:  faster-whisper large-v3-turbo via CTranslate2 CUDA (fallback / multilingual).

Both engines log the execution providers they actually got at load time. If the CUDA provider
silently fell back to CPU, ParakeetEngine raises unless cfg.asr.allow_cpu_fallback is True.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import gpu
from .config import resolve_path

log = logging.getLogger("localflow.asr")


class CudaNotActiveError(RuntimeError):
    pass


class Engine:
    name = "base"

    def load(self) -> None:
        raise NotImplementedError

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: float32 mono 16 kHz in [-1, 1]. Returns text ('' for silence)."""
        raise NotImplementedError

    def warmup(self) -> float:
        """Run one dummy inference; returns ms."""
        t = time.perf_counter()
        self.transcribe(np.zeros(16000, dtype=np.float32))
        return (time.perf_counter() - t) * 1000

    def providers(self) -> dict[str, list[str]]:
        return {}

    def unload(self) -> None:
        """Drop the model / sessions so the GPU memory is released (Pause)."""
        self.model = None
        import gc

        gc.collect()

    def on_gpu(self) -> bool:
        provs = self.providers()
        return bool(provs) and all(p[0].startswith(("CUDA", "cuda")) for p in provs.values() if p)


# The files onnx-asr actually asks the Hugging Face hub for, per quantization. Listing them
# here (rather than importing onnx_asr, which drags in onnxruntime) keeps the startup check
# cheap, and - the point of the list - keeps it QUANTIZATION-AWARE. The fp32 and int8 weights
# are different files in the same repo, so a cache holding only the fp32 pair is not a cache
# for int8: reporting it as one is what used to switch HF_HUB_OFFLINE on and leave the int8
# download impossible ("cached snapshot ... is incomplete: 2 file(s) are missing").
PARAKEET_FILES: dict[str | None, tuple[str, ...]] = {
    None: ("encoder-model.onnx", "decoder_joint-model.onnx", "vocab.txt"),
    "int8": ("encoder-model.int8.onnx", "decoder_joint-model.int8.onnx", "vocab.txt"),
}
# Download sizes, for the one-line message the user sees on a fresh machine.
PARAKEET_DOWNLOAD_GB: dict[str | None, str] = {None: "about 2.5 GB", "int8": "about 0.7 GB"}
# Measured on an RTX 4070 SUPER over four real dictations: fp32 3,019 MB / 74 ms median,
# int8 625 MB / 429 ms median, with identical transcripts. --doctor reports these.
PARAKEET_VRAM_MB: dict[str | None, int] = {None: 3019, "int8": 625}
# ... rounded the way the README and the menu say it, so every surface agrees
PARAKEET_VRAM_LABEL: dict[str | None, str] = {None: "about 3.0 GB", "int8": "about 0.6 GB"}


# faster-whisper resolves a friendly model name to a Hugging Face repo id (faster_whisper.utils.
# _MODELS). The map is copied here rather than imported so the startup cache check stays cheap -
# importing faster_whisper drags in ctranslate2 - and so the check can name the exact repo the
# engine will ask for. A name containing "/" is already a repo id and is used as-is.
WHISPER_REPOS: dict[str, str] = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
# Download sizes for the one-line "this happens once" message. `turbo` is measured (1.5 GB on
# disk after the download); the rest are the published CTranslate2 weight sizes, rounded.
WHISPER_DOWNLOAD_GB: dict[str, str] = {
    "tiny.en": "about 0.1 GB", "tiny": "about 0.1 GB",
    "base.en": "about 0.2 GB", "base": "about 0.2 GB",
    "small.en": "about 0.5 GB", "small": "about 0.5 GB",
    "medium.en": "about 1.5 GB", "medium": "about 1.5 GB",
    "large-v1": "about 3.1 GB", "large-v2": "about 3.1 GB", "large-v3": "about 3.1 GB",
    "large": "about 3.1 GB",
    "distil-large-v2": "about 1.5 GB", "distil-large-v3": "about 1.5 GB",
    "distil-large-v3.5": "about 1.5 GB",
    "distil-medium.en": "about 0.8 GB", "distil-small.en": "about 0.4 GB",
    "large-v3-turbo": "about 1.5 GB", "turbo": "about 1.5 GB",
}
WHISPER_DEFAULT_GB = "about 1.5 GB"
# The one file that proves a Whisper snapshot is really on disk. Everything else in the repo is
# a few kB of JSON, and an interrupted download leaves the blob as "<hash>.incomplete" with no
# link in snapshots/, so "model.bin resolves to a real file" is the honest test.
WHISPER_WEIGHT_FILE = "model.bin"
# Measured on an RTX 4070 SUPER over the same four clips as the Parakeet numbers above:
# large-v3-turbo holds 2,323 MB and takes 135-151 ms per clip, against Parakeet's 3,009 MB and
# 41-46 ms. Whisper is the multilingual option, not the fast one. README says so in full.
WHISPER_VRAM_MB: dict[str, int] = {"turbo": 2323, "large-v3-turbo": 2323}


def parakeet_quantization(cfg: dict[str, Any]) -> str | None:
    """asr.parakeet_quantization, normalised ("" and "none" both mean fp32)."""
    q = cfg["asr"].get("parakeet_quantization")
    q = str(q).strip().lower() if q is not None else ""
    return q or None if q not in ("none", "null", "") else None


def engine_name(cfg: dict[str, Any]) -> str:
    return (cfg["asr"].get("engine") or "parakeet").lower()


def required_model_files(cfg: dict[str, Any]) -> tuple[str, ...]:
    """Filenames this configuration needs in the cache, or () when we cannot say."""
    if engine_name(cfg) != "parakeet":
        return ()
    return PARAKEET_FILES.get(parakeet_quantization(cfg), ())


def whisper_model_repo(cfg: dict[str, Any]) -> str:
    """The Hugging Face repo faster-whisper will ask for, or "" when the name is unknown."""
    name = str(cfg["asr"].get("whisper_model") or "turbo").strip()
    if "/" in name:
        return name
    return WHISPER_REPOS.get(name.lower(), "")


def _hub_cache_dirs(models_dir: Path) -> list[Path]:
    """Where a `models--org--name` folder can live for this config.

    `_setup_hf_cache` points HF_HUB_CACHE at <models_dir>/hub, so that is the answer; the bare
    models_dir is checked too for caches written before that layout. An HF_HUB_CACHE the user
    set themselves is deliberately NOT consulted: guessing "cached" wrongly is what switches
    the hub offline and breaks the download, while guessing "not cached" only costs one extra
    online look-up that finds the files anyway.
    """
    return [models_dir / "hub", models_dir]


def whisper_is_cached(cfg: dict[str, Any]) -> bool:
    """True when the configured Whisper model is really on disk.

    "Really" is the whole point: the old check just looked for any .onnx file under models_dir,
    which Parakeet satisfies, so HF_HUB_OFFLINE=1 was set and faster-whisper could never fetch
    its own weights (0.3.0: the app died on startup the moment anyone chose the Whisper engine).
    A snapshot folder alone is not enough either - an interrupted download leaves the JSON files
    and a `<hash>.incomplete` blob behind - so the test is that `model.bin` inside the snapshot
    resolves to a real, non-empty file.
    """
    repo = whisper_model_repo(cfg)
    if not repo:
        return False
    folder = "models--" + repo.replace("/", "--")
    models_dir = resolve_path(cfg["asr"].get("models_dir", "models"))
    for base in _hub_cache_dirs(models_dir):
        snaps = base / folder / "snapshots"
        if not snaps.is_dir():
            continue
        for snap in snaps.iterdir():
            weights = snap / WHISPER_WEIGHT_FILE
            try:
                if weights.is_file() and weights.stat().st_size > 1_000_000:
                    return True
            except OSError:  # a dangling link into blobs/, or a file being written right now
                continue
    return False


def download_size(cfg: dict[str, Any]) -> str:
    if engine_name(cfg) == "whisper":
        name = str(cfg["asr"].get("whisper_model") or "turbo").strip().lower()
        return WHISPER_DOWNLOAD_GB.get(name, WHISPER_DEFAULT_GB)
    return PARAKEET_DOWNLOAD_GB.get(parakeet_quantization(cfg), "about 2.5 GB")


def model_is_cached(cfg: dict[str, Any]) -> bool:
    """True when the ASR model this config asks for already lives in the local cache.

    Used to decide whether this run needs the network at all, so it has to be engine-aware:
    a cache full of Parakeet weights says nothing about Whisper, and vice versa. For Parakeet
    the check names the exact weight files for the configured precision; for Whisper it looks
    for that model's own snapshot; for anything else it falls back to the old loose test (any
    .onnx under models_dir), so a partially downloaded cache still reports True and the hub
    fetches only what is missing.
    """
    models_dir = resolve_path(cfg["asr"].get("models_dir", "models"))
    if not models_dir.is_dir():
        return False
    if engine_name(cfg) == "whisper":
        return whisper_is_cached(cfg)
    wanted = required_model_files(cfg)
    if not wanted:
        return any(models_dir.rglob("*.onnx"))
    return all(any(models_dir.rglob(name)) for name in wanted)


class _NoTokenNag(logging.Filter):
    localflow_hf_token_nag = True

    def filter(self, record: logging.LogRecord) -> bool:
        return "unauthenticated requests" not in record.getMessage()


def _quiet_hub_logging() -> None:
    """Keep the one-time model download readable.

    huggingface_hub talks to the Hub over httpx, and both log every single request at INFO -
    about 80 lines of URLs with long signed tokens - plus an "unauthenticated requests" notice
    that means nothing here: LocalFlow only ever reads a public model. None of that is the
    user's business, so it is turned down to WARNING. The download progress bar is untouched.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    os.environ.setdefault("HF_HUB_VERBOSITY", "warning")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    # The hub also nags about anonymous downloads ("set a HF_TOKEN for higher rate limits"),
    # twice, at WARNING. LocalFlow only ever reads one public model and never wants an account,
    # so that one message is dropped while every other hub warning still gets through.
    _http = logging.getLogger("huggingface_hub.utils._http")
    if not any(getattr(f, "localflow_hf_token_nag", False) for f in _http.filters):
        _http.addFilter(_NoTokenNag())


def _setup_hf_cache(cfg: dict[str, Any]) -> None:
    """Point Hugging Face at .\\models and go offline *only once the model is actually there*.

    The launchers used to export HF_HUB_OFFLINE=1 unconditionally, which meant the model could
    only ever be fetched by the installer's smoke test - install with -SkipSmoke and the app
    could never download it. Now the offline switch is set here, and only when there is
    something to be offline with. Setting HF_HUB_OFFLINE yourself still wins.
    """
    models_dir = resolve_path(cfg["asr"].get("models_dir", "models"))
    models_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(models_dir))
    os.environ.setdefault("HF_HUB_CACHE", str(models_dir / "hub"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    _quiet_hub_logging()
    if model_is_cached(cfg):
        # cached: never phone home again, and never stall on a flaky connection
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    else:
        size = download_size(cfg)
        which = (whisper_model_repo(cfg) or str(cfg["asr"].get("whisper_model"))
                 if engine_name(cfg) == "whisper" else f"parakeet {parakeet_quantization(cfg) or 'fp32'}")
        log.info("ASR model (%s) not in %s yet - allowing a one-time download (%s) from Hugging Face",
                 which, models_dir, size)
        try:
            print(f"Downloading the speech model ({size}). This happens once.", flush=True)
        except Exception:  # noqa: BLE001 - no console (pythonw): the progress bar is gone too
            pass


def _find_sessions(obj: Any, depth: int = 0, seen: set[int] | None = None) -> dict[str, Any]:
    """Recursively find onnxruntime InferenceSession objects hanging off `obj`."""
    import onnxruntime as ort

    seen = seen if seen is not None else set()
    found: dict[str, Any] = {}
    if depth > 4 or id(obj) in seen:
        return found
    seen.add(id(obj))
    try:
        items = list(vars(obj).items())
    except TypeError:
        return found
    for name, val in items:
        if isinstance(val, ort.InferenceSession):
            found[name.lstrip("_")] = val
        elif isinstance(val, (list, tuple)):
            for i, v in enumerate(val):
                if isinstance(v, ort.InferenceSession):
                    found[f"{name.lstrip('_')}[{i}]"] = v
        elif hasattr(val, "__dict__") and not isinstance(val, (str, bytes, np.ndarray, dict)):
            sub = _find_sessions(val, depth + 1, seen)
            for k, v in sub.items():
                found[f"{name.lstrip('_')}.{k}"] = v
    return found


class ParakeetEngine(Engine):
    name = "parakeet"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.model_name = cfg["asr"]["parakeet_model"]
        self.quant = parakeet_quantization(cfg)
        self.allow_cpu = bool(cfg["asr"].get("allow_cpu_fallback", False))
        self.model = None
        self._providers: dict[str, list[str]] = {}

    def load(self) -> None:
        gpu.register_cuda_dlls(cpu_mode=self.allow_cpu)
        _setup_hf_cache(self.cfg)
        import onnxruntime as ort
        import onnx_asr

        avail = ort.get_available_providers()
        log.info("onnxruntime %s available providers: %s", ort.__version__, avail)
        limit_mb = int(self.cfg["asr"].get("gpu_mem_limit_mb") or 0)
        cuda_opts: dict[str, Any] = {"arena_extend_strategy": "kSameAsRequested"}
        if limit_mb > 0:
            cuda_opts["gpu_mem_limit"] = str(limit_mb * 1024 * 1024)
        want: list = [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"]
        if "CUDAExecutionProvider" not in avail:
            msg = "CUDAExecutionProvider not available in this onnxruntime build (is onnxruntime-gpu installed?)"
            if not self.allow_cpu:
                raise CudaNotActiveError(msg)
            # The user chose CPU (allow_cpu_fallback). This is the configuration working as
            # asked, not a fault, so it must not look like one.
            log.info(gpu.CPU_MODE_NOTE)
            want = ["CPUExecutionProvider"]

        so = ort.SessionOptions()
        so.log_severity_level = 3  # errors only; a CPU fallback shows up in get_providers() anyway
        t = time.perf_counter()
        self.model = onnx_asr.load_model(
            self.model_name,
            quantization=self.quant,
            providers=want,
            sess_options=so,
        )
        where = f"CUDA arena: {cuda_opts}" if want != ["CPUExecutionProvider"] else "CPU"
        log.info("Loaded %s in %.0f ms (%s)", self.model_name, (time.perf_counter() - t) * 1000, where)

        self._providers = {k: list(s.get_providers()) for k, s in _find_sessions(self.model).items()}
        for k, p in self._providers.items():
            log.info("session %-28s providers=%s", k, p)
        if not self._providers:
            log.warning("Could not introspect onnx-asr sessions to verify providers")
        core = {k: p for k, p in self._providers.items() if "preprocess" not in k.lower() and "resampl" not in k.lower()}
        cpu_only = [k for k, p in core.items() if p and p[0] != "CUDAExecutionProvider"]
        if cpu_only and "CUDAExecutionProvider" in avail:
            msg = f"ONNX Runtime fell back to CPU for sessions {cpu_only} (cuDNN/cuBLAS DLLs missing?)"
            if not self.allow_cpu:
                raise CudaNotActiveError(msg)
            log.info("%s - %s", gpu.CPU_MODE_NOTE, msg)

    def providers(self) -> dict[str, list[str]]:
        return dict(self._providers)

    def transcribe(self, audio: np.ndarray) -> str:
        assert self.model is not None, "call load() first"
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        text = self.model.recognize(audio, sample_rate=16000)
        return (text or "").strip()


class WhisperEngine(Engine):
    name = "whisper"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.model_name = cfg["asr"]["whisper_model"]
        self.compute_type = cfg["asr"].get("whisper_compute_type", "float16")
        self.language = cfg["asr"].get("language") or None
        self.allow_cpu = bool(cfg["asr"].get("allow_cpu_fallback", False))
        self.model = None
        self._providers: dict[str, list[str]] = {}

    def load(self) -> None:
        gpu.register_cuda_dlls(cpu_mode=self.allow_cpu)
        _setup_hf_cache(self.cfg)
        import ctranslate2
        from faster_whisper import WhisperModel

        cuda_n = ctranslate2.get_cuda_device_count()
        log.info("ctranslate2 %s cuda devices: %d", ctranslate2.__version__, cuda_n)
        device = "cuda" if cuda_n > 0 else "cpu"
        if device == "cpu":
            if not self.allow_cpu:
                raise CudaNotActiveError("CTranslate2 sees no CUDA device (CUDA/cuDNN DLLs missing?)")
            log.info(gpu.CPU_MODE_NOTE)
        t = time.perf_counter()
        self.model = WhisperModel(
            self.model_name,
            device=device,
            compute_type=self.compute_type if device == "cuda" else "int8",
            download_root=os.environ.get("HF_HUB_CACHE"),
        )
        log.info("Loaded whisper %s on %s in %.0f ms", self.model_name, device, (time.perf_counter() - t) * 1000)
        self._providers = {"whisper": ["CUDAExecutionProvider" if device == "cuda" else "CPUExecutionProvider"]}

    def providers(self) -> dict[str, list[str]]:
        return dict(self._providers)

    def transcribe(self, audio: np.ndarray) -> str:
        assert self.model is not None
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        segs, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segs).strip()


def create_engine(cfg: dict[str, Any]) -> Engine:
    name = (cfg["asr"].get("engine") or "parakeet").lower()
    if name == "parakeet":
        return ParakeetEngine(cfg)
    if name == "whisper":
        return WhisperEngine(cfg)
    raise ValueError(f"unknown asr.engine {name!r} (parakeet | whisper)")
