"""Smoke test: load the ASR engine, verify the CUDA provider, transcribe a WAV, print timings.

Usage (from the project root, venv active):
    python -m tests.smoke_asr                 # uses tests/sample_tts.wav (generated via SAPI if missing)
    python -m tests.smoke_asr path\to.wav     # any 16 kHz-ish mono/stereo WAV
    python -m tests.smoke_asr --llm           # also run rules cleanup + Ollama cleanup and time it
    python -m tests.smoke_asr --engine whisper
Exit code 0 only if transcription succeeded on the GPU (or allow_cpu_fallback is set).
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from localflow import config as cfgmod  # noqa: E402
from localflow import asr as asrmod  # noqa: E402
from localflow.asr import create_engine  # noqa: E402

SAMPLE = ROOT / "tests" / "sample_tts.wav"
SAMPLE_TEXT = "um so this is a test comma scratch that this is the final test period new line press enter"


def make_tts_wav(path: Path, text: str = SAMPLE_TEXT) -> None:
    """Generate a 16 kHz mono 16-bit WAV with Windows SAPI (System.Speech)."""
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{path}', $fmt); $s.Speak('{text}'); $s.Dispose();"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        # simple linear resample; good enough for a smoke test
        x_old = np.linspace(0, 1, len(a), endpoint=False)
        x_new = np.linspace(0, 1, int(len(a) * 16000 / sr), endpoint=False)
        a = np.interp(x_new, x_old, a).astype(np.float32)
    return a


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", nargs="?", default=str(SAMPLE))
    ap.add_argument("--llm", action="store_true", help="also run cleanup rules + Ollama LLM")
    ap.add_argument("--engine", choices=["parakeet", "whisper"], default=None)
    ap.add_argument("--level", default=None, help="LLM level for --llm (none/light/medium/high)")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The model download otherwise buries the real output under ~80 httpx request lines.
    asrmod._quiet_hub_logging()
    cfg = cfgmod.load()
    if args.engine:
        cfg["asr"]["engine"] = args.engine

    wav = Path(args.wav)
    if not wav.exists():
        if wav == SAMPLE:
            print(f"Generating {wav} with Windows SAPI TTS ...")
            make_tts_wav(wav)
        else:
            print(f"WAV not found: {wav}")
            return 2
    audio = read_wav(wav)
    dur = len(audio) / 16000
    print(f"Clip: {wav.name}  {dur:.2f} s  rms={float(np.sqrt(np.mean(audio**2))):.4f}")

    eng = create_engine(cfg)
    t0 = time.perf_counter()
    eng.load()
    print(f"Engine {eng.name} loaded in {(time.perf_counter() - t0) * 1000:.0f} ms")
    print("Session providers:")
    for k, v in eng.providers().items():
        print(f"  {k:30s} {v}")
    print(f"GPU active: {eng.on_gpu()}")

    ms_warm = eng.warmup()
    print(f"Warmup (1 s silence): {ms_warm:.0f} ms")

    times = []
    text = ""
    for _ in range(max(1, args.runs)):
        t = time.perf_counter()
        text = eng.transcribe(audio)
        times.append((time.perf_counter() - t) * 1000)
    print(f"ASR: {' / '.join(f'{m:.0f}' for m in times)} ms  (clip {dur:.2f} s, RTFx {dur * 1000 / min(times):.0f})")
    print(f"RAW : {text!r}")

    if args.llm:
        from localflow import cleanup, llm

        level = args.level or cfg["cleanup"]["level"]
        t = time.perf_counter()
        res = cleanup.clean(text, cfg["cleanup"])
        ms_rules = (time.perf_counter() - t) * 1000
        print(f"RULES ({ms_rules:.2f} ms): {res.text!r}  press_enter={res.press_enter}")
        if level != "none":
            client = llm.OllamaClient(cfg["llm"])
            print(f"LLM warmup: {client.warmup():.0f} ms")
            out = client.cleanup(res.text, level)
            print(f"LLM {level} ({out.ms:.0f} ms, used={out.used}, reason={out.reason}): {out.text!r}")
            total = min(times) + ms_rules + out.ms
            print(f"TOTAL (asr + rules + llm): {total:.0f} ms")

    ok = eng.on_gpu() or cfg["asr"].get("allow_cpu_fallback")
    print("SMOKE TEST", "PASSED" if (ok and text) else "FAILED")
    return 0 if (ok and text) else 1


if __name__ == "__main__":
    sys.exit(main())
