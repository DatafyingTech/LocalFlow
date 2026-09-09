"""Microphone capture: 16 kHz mono float32 with a pre-roll ring buffer.

The input stream runs continuously so the ~400 ms before the hotkey press is captured
(first syllables are otherwise clipped). While not recording, 20 ms blocks go into a bounded
deque; start() snapshots the deque and switches to accumulating; stop() returns one array.

Hands-free ("chunked") mode: while recording, a simple energy VAD watches for a pause of
`handsfree_silence_ms` after speech. Each pause closes a chunk which is handed to `on_chunk`
(from the audio thread - the callback must only enqueue) while capture continues, so text
keeps flowing while the user talks.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger("localflow.audio")


def list_input_devices() -> list[tuple[int, str]]:
    out = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append((i, d["name"]))
    except Exception as e:
        log.error("query_devices failed: %s", e)
    return out


def find_device(substr: str | None) -> int | None:
    """Return the device index whose name contains `substr` (case-insensitive), else None (=default)."""
    if not substr:
        return None
    s = substr.lower()
    for idx, name in list_input_devices():
        if s in name.lower():
            return idx
    log.warning("No input device matching %r; using default", substr)
    return None


def default_input_name() -> str:
    try:
        idx = sd.default.device[0]
        return sd.query_devices(idx)["name"] if idx is not None and idx >= 0 else "system default"
    except Exception:
        return "system default"


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def normalize(audio: np.ndarray, target_dbfs: float = -3.0, min_peak: float = 1e-4) -> np.ndarray:
    """Peak-normalise a float32 clip to `target_dbfs` (default -3 dBFS = 0.708).

    Whispered / quiet speech is scaled up so the ASR sees a normal-level signal; clips whose
    peak is below `min_peak` (digital silence) are returned unchanged.
    """
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < min_peak:
        return audio
    target = 10 ** (target_dbfs / 20)
    return (audio * (target / peak)).astype(np.float32, copy=False)


class Recorder:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.sr = int(cfg.get("sample_rate", 16000))
        self.block = int(self.sr * 0.02)  # 20 ms
        self.preroll_blocks = max(1, int(cfg.get("preroll_ms", 400) / 20))
        self.min_ms = int(cfg.get("min_duration_ms", 400))
        self.rms_threshold = float(cfg.get("rms_threshold", 0.0005))
        self.normalize = bool(cfg.get("normalize", True))
        self.normalize_dbfs = float(cfg.get("normalize_dbfs", -3.0))
        self.max_seconds = float(cfg.get("max_seconds", 1200))
        self.device_name = cfg.get("device") or ""
        # hands-free chunking
        self.silence_blocks_needed = max(1, int(cfg.get("handsfree_silence_ms", 700) / 20))
        self.vad_threshold = float(cfg.get("handsfree_vad_threshold", 0.008))
        self.max_chunk_blocks = int(float(cfg.get("handsfree_max_chunk_s", 30)) * 50)

        self._ring: collections.deque = collections.deque(maxlen=self.preroll_blocks)
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._chunked = False
        self._speech_seen = False
        self._silence_run = 0
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._level = 0.0
        self._started_at = 0.0
        self._max_fired = False
        self.on_max_duration: Callable[[], None] | None = None
        self.on_chunk: Callable[[np.ndarray], None] | None = None

    # ------------------------------------------------------------------ stream
    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("audio status: %s", status)
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        level = float(np.sqrt(np.mean(mono**2))) if mono.size else 0.0
        self._level = level
        emit = None
        with self._lock:
            if not self._recording:
                self._ring.append(mono)
                return
            self._chunks.append(mono)
            if self._chunked:
                if level > self.vad_threshold:
                    self._speech_seen = True
                    self._silence_run = 0
                else:
                    self._silence_run += 1
                n = len(self._chunks)
                if self._speech_seen and (
                    self._silence_run >= self.silence_blocks_needed
                    or (n >= self.max_chunk_blocks and self._silence_run >= 5)
                ):
                    emit = np.concatenate(self._chunks)
                    self._chunks = []
                    self._speech_seen = False
                    self._silence_run = 0
                elif not self._speech_seen and n > self.preroll_blocks * 2:
                    # only silence so far: keep just a pre-roll's worth so memory stays flat
                    self._chunks = self._chunks[-self.preroll_blocks :]
            if (
                self.max_seconds
                and not self._max_fired
                and (time.monotonic() - self._started_at) > self.max_seconds
            ):
                self._max_fired = True
                cb = self.on_max_duration
                if cb:
                    threading.Thread(target=cb, daemon=True).start()
        if emit is not None and self.on_chunk:
            try:
                self.on_chunk(emit.astype(np.float32, copy=False))
            except Exception:
                log.exception("on_chunk failed")

    def open(self, device_name: str | None = None) -> None:
        self.close()
        if device_name is not None:
            self.device_name = device_name
        idx = find_device(self.device_name)
        self._stream = sd.InputStream(
            samplerate=self.sr,
            channels=1,
            dtype="float32",
            blocksize=self.block,
            device=idx,
            callback=self._callback,
        )
        self._stream.start()
        name = sd.query_devices(idx)["name"] if idx is not None else default_input_name()
        log.info("Audio input open: %s @ %d Hz", name, self.sr)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def ensure_open(self) -> None:
        if self._stream is None or not self._stream.active:
            self.open()

    @property
    def level(self) -> float:
        return self._level

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def chunked(self) -> bool:
        return self._chunked

    def current_device_name(self) -> str:
        idx = find_device(self.device_name)
        try:
            return sd.query_devices(idx)["name"] if idx is not None else default_input_name()
        except Exception:
            return self.device_name or "default"

    # ------------------------------------------------------------------ control
    def start(self, chunked: bool = False) -> None:
        self.ensure_open()
        with self._lock:
            self._chunks = list(self._ring)  # pre-roll
            self._ring.clear()
            self._recording = True
            self._chunked = chunked
            self._speech_seen = False
            self._silence_run = 0
            self._started_at = time.monotonic()
            self._max_fired = False

    def set_chunked(self, chunked: bool) -> None:
        """Switch an in-progress recording into/out of hands-free chunking."""
        with self._lock:
            self._chunked = chunked
            self._speech_seen = any(rms(c) > self.vad_threshold for c in self._chunks[-25:])
            self._silence_run = 0

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            self._chunked = False
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def cancel(self) -> None:
        with self._lock:
            self._recording = False
            self._chunked = False
            self._chunks = []

    def duration_s(self) -> float:
        return (time.monotonic() - self._started_at) if self._recording else 0.0

    # ------------------------------------------------------------------ VAD
    def is_usable(self, audio: np.ndarray) -> tuple[bool, str]:
        dur_ms = len(audio) / self.sr * 1000
        if dur_ms < self.min_ms:
            return False, f"too short ({dur_ms:.0f} ms)"
        r = rms(audio)
        if r < self.rms_threshold:
            return False, f"too quiet (rms {r:.4f} < {self.rms_threshold})"
        return True, f"{dur_ms:.0f} ms, rms {r:.4f}"

    def prepare(self, audio: np.ndarray) -> np.ndarray:
        """Apply configured pre-ASR conditioning (peak normalisation)."""
        return normalize(audio, self.normalize_dbfs) if self.normalize else audio
