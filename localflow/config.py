"""YAML configuration. config.yaml lives in the project root (next to the localflow package).

On first run the file is created with defaults. Unknown keys are preserved; missing keys are
filled from DEFAULTS at load time (deep merge), so upgrading never breaks an old config.
"""
from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("localflow.config")

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.yaml"
EXAMPLE_PATH = PROJECT_DIR / "config.example.yaml"

DEFAULTS: dict[str, Any] = {
    "hotkeys": {
        # key names: ctrl, win, alt, shift, space, esc, tab, enter, a-z, 0-9, f1-f12
        "ptt": ["ctrl", "win"],  # hold to talk
        "handsfree": ["ctrl", "win", "space"],  # toggle hands-free
        "double_tap_toggle": True,  # double-tap the PTT chord -> hands-free
        "double_tap_ms": 400,
        "tap_max_ms": 250,  # a PTT hold shorter than this counts as a "tap"
        "cancel": ["esc"],
        "repaste": ["shift", "alt", "z"],
        "polish": ["ctrl", "win", "alt"],  # hold: like PTT but cleaned with llm.polish_model
    },
    "audio": {
        "device": "",  # substring of the input device name; "" = system default
        "sample_rate": 16000,
        "preroll_ms": 400,
        "min_duration_ms": 400,  # clips shorter than this are dropped
        "rms_threshold": 0.0005,  # clips quieter than this (RMS, float32 scale) are dropped; empty clips are
        # otherwise rejected by the ASR result (empty / punctuation-only transcript)
        "normalize": True,  # peak-normalise each clip to ~-3 dBFS before ASR so whispers transcribe
        "normalize_dbfs": -3.0,
        "max_seconds": 1200,  # hands-free cap (20 minutes)
        # hands-free chunking: a pause of this length after speech ends the chunk, which is
        # transcribed and pasted while you keep talking
        "handsfree_mode": "whole",  # whole = record until you stop, then transcribe once | chunked = paste at each pause
        "handsfree_silence_ms": 700,
        "handsfree_vad_threshold": 0.002,  # per-20 ms block RMS that counts as speech (chunked mode only; quiet speakers sit near 0.003)
        "handsfree_max_chunk_s": 30,  # force a chunk boundary at the next pause after this
    },
    "asr": {
        "engine": "parakeet",  # parakeet | whisper
        "parakeet_model": "nemo-parakeet-tdt-0.6b-v2",
        "parakeet_quantization": None,  # None (fp32) | "int8"
        "whisper_model": "turbo",  # large-v3-turbo; also distil-large-v3, large-v3
        "whisper_compute_type": "float16",
        "language": "en",
        "allow_cpu_fallback": False,  # False = hard-fail at startup if CUDA provider is not active
        "models_dir": "models",  # HF cache, relative to project dir
        # ONNX Runtime CUDA arena cap (per session) so Parakeet cannot grow its VRAM footprint while a
        # game runs; arena_extend_strategy is kSameAsRequested
        "gpu_mem_limit_mb": 3072,
    },
    "cleanup": {
        # LLM level: none | light | medium | high. Rules below always run (they are ~1 ms).
        "level": "medium",
        "handsfree_level": "high",  # cleanup level for a whole hands-free speech (the enhanced pass)
        "rules": True,  # set False for completely raw ASR output
        "spoken_punctuation": True,
        "remove_fillers": True,
        "fillers": ["um", "umm", "uh", "uhm", "uhh", "erm", "er", "hmm", "hm", "mhm", "ah"],
        "filler_phrases": [],  # e.g. ["you know", "sort of"]
        "collapse_repeats": True,  # "the the" -> "the"
        "backtrack": True,
        "backtrack_strong": [
            "scratch that", "strike that", "no no wait", "no wait", "wait no", "no no", "oops",
            "I meant to say", "I mean to say", "I meant", "let me rephrase", "correction",
        ],
        "backtrack_weak": ["i mean", "actually"],  # only when they start a comma-clause
        "press_enter_phrases": ["press enter", "hit enter"],
        # spoken lists: "I need to get milk, eggs and bread" -> lead-in + "- item" lines
        "auto_lists": True,
        "list_leadins": [
            "I need to get", "I need to buy", "I need to pick up", "I need to do", "we need to get",
            "we need to buy", "the list is", "the items are", "the following", "here is the list",
            "here's the list", "things to do", "things to get", "things to buy", "shopping list",
            "to do list", "to-do list", "the steps are", "the options are",
        ],
        "dictionary": {  # misspelling -> correction, whole-word, case-insensitive
            "local flow": "LocalFlow",
        },
        "snippets": {  # whole-word trigger phrase -> expansion
            "my signature": "Best regards,\n<your name>",
        },
        "per_app": {  # foreground window title substring -> level override
            # "Slack": "medium",
            # "Visual Studio Code": "none",
        },
    },
    "llm": {
        "host": "http://127.0.0.1:11434",
        "model": "gemma3:4b",  # benchmarked: 265-660 ms medium on 10-40 words; qwen3 reasons inline
        "polish_model": "gemma3:4b",  # same model: two resident models spill out of VRAM with Parakeet
        # Skip the cleanup call outright when Ollama says the model is not in VRAM, start a
        # background warm, and let the NEXT dictation have it. Cold, one call costs 7-19 s;
        # warm, 150-600 ms. Set false to go back to waiting for a cold load.
        "skip_when_cold": True,
        # On timeout the rules-cleaned text is pasted instead, so the budget is set to what a
        # warm model actually needs (measured 150-600 ms) rather than to what a cold one needs:
        # waiting 6-10 s and then throwing the answer away is worse than not trying.
        "timeout_ms": 2500,  # base timeout for short inputs
        "timeout_per_word_ms": 80,  # added per input word ...
        "timeout_max_ms": 20000,  # ... up to this cap
        "polish_timeout_ms": 20000,
        "handsfree_timeout_ms": 60000,  # a long speech gets a long timeout; correctness over speed
        "num_ctx": 4096,  # model context; the largest request the app can send is ~1,875 tokens
        "segment_words": 400,  # long texts are cleaned in sentence-aligned segments of about this size
        # start Ollama ourselves when a probe finds it down and its own startup entry did not fire
        # (local hosts only; at most 3 attempts per session, 5 minutes apart)
        "autostart_ollama": True,
        "keep_alive": 1800,  # seconds the model stays in VRAM after a call (-1 = forever); re-warmed on PTT press
        "temperature": 0,
    },
    "inject": {
        # auto = scancode typing when the foreground window is fullscreen or matches type_apps, else paste
        "method": "auto",  # auto | paste | scancode | type
        "force_scancode": False,  # tray toggle "Force keystroke typing"
        "scancode_delay_ms": 12,  # pause between keys in scancode mode
        "scancode_newlines": False,  # False: newlines become spaces (chat boxes are single-line)
        "scancode_max_chars": 500,  # never type more than this into a game (chat-length only)
        "restore_clipboard_ms": 150,
        "enter_delay_ms": 150,  # wait before the Enter key for "press enter" (slow apps need more)
        "trailing_space": False,
        "handsfree_trailing_space": True,  # separate consecutive hands-free chunks with a space
        # window-title substrings / exe-name prefixes that always get scancode typing.
        # Empty by default (fullscreen apps are detected on their own); e.g. ["FiveM"]
        "type_apps": [],
    },
    "ui": {
        "overlay": True,  # show the Flow Bar pill
        "sounds": True,
        "pill_x": None,  # remembered when you drag the pill; null = bottom-center
        "pill_y": None,
        # withdraw the dot while a fullscreen app is in front; off by default (the dot is useful in games),
        # turn on if a game stutters with the topmost overlay present
        "hide_when_fullscreen": False,
    },
    "gpu": {
        # pause LocalFlow (unload Parakeet + gemma) automatically while a fullscreen app is in front,
        # resume ~5 s after it goes away. Off by default; the tray menu has a manual "Pause (free GPU)".
        "auto_pause_fullscreen": False,
        "auto_resume_delay_s": 5,
    },
    "history": {
        "enabled": True,
        "path": "history.jsonl",
        "max_entries": 5000,
    },
    "server": {
        # phone API (docs/API.md): a local HTTP server the Android app reaches over Tailscale.
        "enabled": False,  # tray: "Enable phone access"
        "port": 8770,
        "token": "",  # generated on first enable and saved here; tray: "Phone setup"
        "bind": "127.0.0.1",  # never a LAN interface: `tailscale serve` is the only way in
        "tailscale_serve": True,  # run `tailscale serve --bg --http=80 http://127.0.0.1:<port>` at startup
    },
    "debug": False,
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _seed_from_example(p: Path) -> bool:
    """Create the first-run config.yaml by copying config.example.yaml (the documented reference).

    Returns True on success. Falls back to the built-in DEFAULTS when the example is missing or
    unreadable, so the app never fails to start over a missing file.
    """
    if not EXAMPLE_PATH.is_file():
        return False
    try:
        text = EXAMPLE_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return True
    except (OSError, yaml.YAMLError) as e:
        log.warning("could not seed config from %s: %s", EXAMPLE_PATH.name, e)
        return False


def load(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        if _seed_from_example(p):
            log.info("Created %s from %s", p, EXAMPLE_PATH.name)
        else:
            save(DEFAULTS, p)
            log.info("Created default config at %s", p)
            return copy.deepcopy(DEFAULTS)
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p} must contain a YAML mapping")
    return _deep_merge(DEFAULTS, data)


def save(cfg: dict[str, Any], path: Path | str | None = None) -> None:
    p = Path(path) if path else CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# LocalFlow configuration. Edit and restart (or use the tray menu).\n")
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp, p)


# ------------------------------------------------------------------ last startup failure
# When the speech engine cannot be loaded the app stays up in its error state (0.3.1), so the
# reason has to outlive the process that saw it: --doctor runs in a separate process, usually
# minutes later, and "it did not start" is not a bug report. One line of JSON, rewritten on
# every start and deleted as soon as a load succeeds.
LAST_ERROR_PATH = PROJECT_DIR / "last_error.json"


def save_last_error(message: str, *, engine: str = "", path: Path | str | None = None) -> None:
    """Record why the speech engine could not load. Never raises."""
    import json
    import time

    p = Path(path) if path else LAST_ERROR_PATH
    try:
        p.write_text(
            json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "engine": engine, "error": message}),
            encoding="utf-8",
        )
    except OSError as e:
        log.debug("could not record the load failure: %s", e)


def clear_last_error(path: Path | str | None = None) -> None:
    """The engine loaded: forget any previous failure. Never raises."""
    p = Path(path) if path else LAST_ERROR_PATH
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        log.debug("could not clear the recorded load failure: %s", e)


def load_last_error(path: Path | str | None = None) -> dict[str, Any] | None:
    """The last recorded load failure, or None. Never raises."""
    import json

    p = Path(path) if path else LAST_ERROR_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("error") else None


def resolve_path(cfg_value: str) -> Path:
    """Resolve a path from config relative to the project dir."""
    p = Path(cfg_value).expanduser()
    return p if p.is_absolute() else PROJECT_DIR / p
