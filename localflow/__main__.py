"""LocalFlow entry point:  python -m localflow [--debug] [--config path] [--version] [--doctor]

Wiring: hotkeys / Flow Bar -> audio -> ASR -> cleanup rules -> (LLM) -> inject; tray + pill for
state; JSONL history; per-utterance latency log. A single worker thread drains an utterance
queue so hands-free chunks are pasted strictly in order.
"""
from __future__ import annotations

import argparse
import gc
import logging
import os
import platform
import queue
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from . import audio as audiomod
from . import cleanup, config, inject, llm, winfocus
from .asr import CudaNotActiveError, create_engine, model_is_cached
from .history import History
from .hotkeys import Hotkeys
from .ui import FlowBar, Tray, beep

log = logging.getLogger("localflow")
LOG_PATH = config.PROJECT_DIR / "localflow.log"


class ASRError(RuntimeError):
    """The speech engine raised while transcribing (wrapped so callers can tell it apart)."""


@dataclass
class PipelineResult:
    """What one utterance became. `empty` means there is nothing to paste (dropped clip, silence,
    or cleanup removed everything); `reason` says which, for the log."""

    text: str = ""
    raw: str = ""
    level: str = ""
    llm_used: bool = False
    press_enter: bool = False
    empty: bool = False
    audio_s: float = 0.0
    timings: dict = field(default_factory=lambda: {"asr_ms": 0.0, "rules_ms": 0.0, "llm_ms": 0.0, "total_ms": 0.0})
    reason: str = ""
    snippet_fired: bool = False


def setup_logging(debug: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # absent under pythonw
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("httpx", "huggingface_hub", "urllib3", "PIL", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


class App:
    def __init__(self, cfg: dict, cfg_path: Path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.debug = bool(cfg.get("debug"))
        self.engine = None
        self.ready = False
        self.state = "loading"
        self.handsfree = False
        self.polish_mode = False
        self.paused = False  # engines unloaded (Pause / free GPU)
        self._pause_lock = threading.Lock()
        self._auto_resume_timer: threading.Timer | None = None
        self.tap_max_ms = int(cfg["hotkeys"].get("tap_max_ms", 250))
        cfg.setdefault("gpu", {})

        self.recorder = audiomod.Recorder(cfg["audio"])
        self.recorder.on_max_duration = self._on_max_duration
        self.recorder.on_chunk = self._on_chunk
        self.history = History(cfg["history"])
        self.llm = llm.OllamaClient(
            cfg["llm"],
            backtrack_phrases=list(cfg["cleanup"].get("backtrack_strong") or []) + list(cfg["cleanup"].get("backtrack_weak") or []),
        )
        self.llm_ok = False

        self.pill = FlowBar(
            cfg["ui"],
            on_click=self.toggle_handsfree,
            on_moved=self._pill_moved,
            menu_spec=self.menu_spec,
            enabled=bool(cfg["ui"].get("overlay", True)),
        )
        self.tray = Tray(menu_spec=self.menu_spec)
        self.hotkeys = Hotkeys(
            cfg["hotkeys"],
            on_ptt_start=self.start_recording,
            on_ptt_stop=self.stop_recording,
            on_toggle_handsfree=self.toggle_handsfree,
            on_cancel=self.cancel,
            on_repaste=self.repaste,
            on_polish_start=lambda: self.start_recording(polish=True),
            on_polish_stop=lambda held_ms=0.0: self.stop_recording(held_ms),
            is_active=lambda: self.state in ("listening", "handsfree", "processing"),  # "paused" is not active
            is_handsfree=lambda: self.handsfree,
        )
        self._queue: queue.Queue = queue.Queue()
        # one utterance at a time through ASR + LLM: the worker below and the phone API share it
        self._pipe_lock = threading.Lock()
        self.server = None  # localflow.server.DictationServer while phone access is on
        cfg.setdefault("server", {})
        self._worker = threading.Thread(target=self._worker_loop, name="pipeline", daemon=True)
        self._worker.start()
        self._level_thread = None
        self.fullscreen = winfocus.FullscreenWatcher(self._on_fullscreen_change, interval_s=1.0)

    # ------------------------------------------------------------------ menu (tray + pill)
    def menu_spec(self) -> list:
        ccfg = self.cfg["cleanup"]
        levels = [
            ("radio", lv.capitalize(), (lambda l=lv: ccfg["level"] == l), (lambda l=lv: self.set_level(l)))
            for lv in ("none", "light", "medium", "high")
        ]
        engines = [
            ("radio", "Parakeet TDT 0.6B v2 (CUDA)", lambda: self.cfg["asr"]["engine"] == "parakeet", lambda: self.set_engine("parakeet")),
            ("radio", "Whisper large-v3-turbo (CUDA)", lambda: self.cfg["asr"]["engine"] == "whisper", lambda: self.set_engine("whisper")),
        ]

        def mics():
            cur = (self.cfg["audio"].get("device") or "").lower()
            items = [("radio", "System default", lambda: cur == "", lambda: self.set_mic(""))]
            for _, name in audiomod.list_input_devices():
                items.append(
                    ("radio", name, (lambda n=name: bool(cur) and cur in n.lower()), (lambda n=name: self.set_mic(n)))
                )
            return items

        hf_label = "Stop hands-free" if self.handsfree else "Start hands-free dictation"
        pause_item = ("cmd", "Resume (reload models)", self.resume) if self.paused else ("cmd", "Pause (free GPU)", self.pause)
        return [
            ("label", f"LocalFlow {__version__} — {self.state}"),
            ("sep",),
            ("cmd", hf_label, self.toggle_handsfree),
            ("cmd", "Re-paste last (Shift+Alt+Z)", self.repaste),
            pause_item,
            ("sep",),
            ("menu", "Cleanup level", levels),
            ("menu", "ASR engine (restart to apply)", engines),
            ("menu", "Microphone", mics),
            ("check", "Force keystroke typing", lambda: bool(self.cfg["inject"].get("force_scancode", False)), self.toggle_force_scancode),
            ("check", "Hide dot in fullscreen apps", lambda: bool(self.cfg["ui"].get("hide_when_fullscreen", False)), self.toggle_hide_fullscreen),
            ("check", "Auto-pause in fullscreen apps", lambda: bool(self.cfg["gpu"].get("auto_pause_fullscreen", False)), self.toggle_auto_pause),
            ("check", "Show Flow Bar", lambda: self.pill.enabled, self.toggle_overlay),
            ("check", "Sounds", lambda: bool(self.cfg["ui"].get("sounds", True)), self.toggle_sounds),
            ("sep",),
            ("check", "Enable phone access", lambda: bool(self.cfg["server"].get("enabled", False)), self.toggle_server),
            ("cmd", "Phone setup", self.phone_setup),
            ("sep",),
            ("cmd", "Open config.yaml", lambda: os.startfile(str(self.cfg_path))),
            ("cmd", "Open history", self.open_history),
            ("cmd", "Open log", lambda: os.startfile(str(LOG_PATH))),
            ("cmd", f"About LocalFlow {__version__}", self.about),
            ("sep",),
            ("cmd", "Quit LocalFlow", self.quit),
        ]

    # ------------------------------------------------------------------ state / settings
    def set_state(self, state: str, text: str | None = None) -> None:
        self.state = state
        self.pill.set_state(state, text)
        self.tray.set_state(state)

    def save_cfg(self) -> None:
        try:
            config.save(self.cfg, self.cfg_path)
        except Exception as e:
            log.warning("could not save config: %s", e)

    def set_level(self, level: str) -> None:
        self.cfg["cleanup"]["level"] = level
        self.save_cfg()
        log.info("cleanup level -> %s", level)
        if level != "none" and self.llm_ok:
            threading.Thread(target=self.llm.warmup, kwargs={"level": level}, daemon=True).start()

    def set_engine(self, name: str) -> None:
        if name == self.cfg["asr"]["engine"]:
            return
        self.cfg["asr"]["engine"] = name
        self.save_cfg()
        self.tray.notify(f"ASR engine set to {name}. Restart LocalFlow to apply.")

    def set_mic(self, name: str) -> None:
        self.cfg["audio"]["device"] = name
        self.save_cfg()
        try:
            self.recorder.open(name)
            self.tray.notify(f"Microphone: {self.recorder.current_device_name()}")
        except Exception as e:
            log.error("mic change failed: %s", e)
            self.tray.notify(f"Could not open microphone: {e}")

    def toggle_overlay(self) -> None:
        self.pill.enabled = not self.pill.enabled
        self.cfg["ui"]["overlay"] = self.pill.enabled
        self.save_cfg()
        if self.pill.enabled:
            self.pill.start()
        else:
            self.pill.hide()

    def toggle_sounds(self) -> None:
        self.cfg["ui"]["sounds"] = not self.cfg["ui"].get("sounds", True)
        self.save_cfg()

    def toggle_force_scancode(self) -> None:
        self.cfg["inject"]["force_scancode"] = not self.cfg["inject"].get("force_scancode", False)
        self.save_cfg()
        log.info("force keystroke typing -> %s", self.cfg["inject"]["force_scancode"])

    def toggle_hide_fullscreen(self) -> None:
        on = not self.cfg["ui"].get("hide_when_fullscreen", False)
        self.cfg["ui"]["hide_when_fullscreen"] = on
        self.save_cfg()
        self.pill.set_suppressed(on and self.fullscreen.fullscreen)

    def toggle_auto_pause(self) -> None:
        on = not self.cfg["gpu"].get("auto_pause_fullscreen", False)
        self.cfg["gpu"]["auto_pause_fullscreen"] = on
        self.save_cfg()
        if on and self.fullscreen.fullscreen and not self.paused:
            self.pause(auto=True)

    # ------------------------------------------------------------------ fullscreen / GPU friendliness
    def _on_fullscreen_change(self, fullscreen: bool, info: winfocus.ForegroundInfo) -> None:
        """Watcher thread callback (1 s poll)."""
        if self.cfg["ui"].get("hide_when_fullscreen", False):
            self.pill.set_suppressed(fullscreen)
        if not self.cfg["gpu"].get("auto_pause_fullscreen", False):
            return
        if self._auto_resume_timer:
            self._auto_resume_timer.cancel()
            self._auto_resume_timer = None
        if fullscreen:
            if not self.paused and self.ready:
                threading.Thread(target=self.pause, kwargs={"auto": True}, name="auto-pause", daemon=True).start()
        elif self.paused and getattr(self, "_auto_paused", False):
            delay = float(self.cfg["gpu"].get("auto_resume_delay_s", 5))
            self._auto_resume_timer = threading.Timer(delay, self._auto_resume)
            self._auto_resume_timer.daemon = True
            self._auto_resume_timer.start()

    def _auto_resume(self) -> None:
        if self.paused and not self.fullscreen.fullscreen:
            self.resume()

    def pause(self, auto: bool = False) -> None:
        """Unload Parakeet and evict gemma from VRAM. PTT only beeps until Resume."""
        with self._pause_lock:
            if self.paused or not self.ready:
                return
            if self.handsfree:
                self.toggle_handsfree()
            elif self.state == "listening":
                self.cancel()
            self.paused = True
            self._auto_paused = auto
            self.set_state("paused")
            t = time.perf_counter()
            try:
                if self.engine is not None:
                    self.engine.unload()
            except Exception:
                log.exception("ASR unload failed")
            gc.collect()
            if self.llm_ok:
                self.llm.unload()
            log.info("paused (%s): ASR + LLM unloaded in %.0f ms; GPU free", "auto" if auto else "manual", (time.perf_counter() - t) * 1000)
            self.tray.set_state("paused", "LocalFlow — paused (GPU free)")

    def resume(self) -> None:
        with self._pause_lock:
            if not self.paused:
                return
            self.set_state("loading")
            t = time.perf_counter()
            try:
                if self.engine is None:
                    self.engine = create_engine(self.cfg)
                self.engine.load()
                self.engine.warmup()
            except Exception as e:
                log.exception("ASR reload failed")
                self.set_state("error", "ASR reload failed")
                self.tray.notify(f"ASR reload failed: {e}", "LocalFlow error")
                return
            self.paused = False
            self._auto_paused = False
            self.set_state("idle")
            log.info("resumed: ASR reloaded in %.0f ms (GPU=%s)", (time.perf_counter() - t) * 1000, self.engine.on_gpu())
        if self.llm_ok:
            lvl = self.cfg["cleanup"]["level"]
            threading.Thread(target=self.llm.warmup, kwargs={"level": lvl if lvl != "none" else "light"}, daemon=True).start()

    def about(self) -> None:
        engine = getattr(self.engine, "name", self.cfg["asr"].get("engine", "?"))
        gpu_txt = "GPU" if (self.engine is not None and self.engine.on_gpu()) else "CPU"
        self.tray.notify(
            f"LocalFlow {__version__}\n"
            f"ASR: {engine} ({gpu_txt}) | cleanup: {self.cfg['cleanup'].get('level')}"
            f"{'' if self.llm_ok else ' (rules only)'}\n"
            f"Python {platform.python_version()} | fully local, nothing is uploaded\n"
            "Diagnostics: python -m localflow --doctor",
            f"About LocalFlow {__version__}",
        )

    def open_history(self) -> None:
        if self.history.path.exists():
            os.startfile(str(self.history.path))
        else:
            self.tray.notify("No history yet.")

    def _pill_moved(self, x: int, y: int) -> None:
        self.cfg["ui"]["pill_x"], self.cfg["ui"]["pill_y"] = int(x), int(y)
        self.save_cfg()

    def _sounds(self) -> bool:
        return bool(self.cfg["ui"].get("sounds", True))

    # ------------------------------------------------------------------ startup
    def load_models(self) -> None:
        # First run after install.bat -SkipSmoke: the model is not cached yet, so engine.load()
        # will spend several minutes downloading it. Say so, or the app just looks hung.
        downloading = not model_is_cached(self.cfg)
        try:
            self.set_state("loading")
            if downloading:
                msg = (
                    "Downloading the speech model (about 2.5 GB). This happens once and can take "
                    "several minutes on a normal connection. The dot turns grey when LocalFlow is ready."
                )
                log.info("Speech model is not cached yet - downloading it now (~2.5 GB, one time).")
                self.tray.set_state("loading", "LocalFlow — downloading speech model (one time)…")
                self.tray.notify(msg, "LocalFlow: first-run download")
            t = time.perf_counter()
            self.engine = create_engine(self.cfg)
            self.engine.load()
            ms_load = (time.perf_counter() - t) * 1000
            ms_warm = self.engine.warmup()
            log.info("ASR %s ready: load %.0f ms, warmup %.0f ms, GPU=%s", self.engine.name, ms_load, ms_warm, self.engine.on_gpu())
        except CudaNotActiveError as e:
            log.error("%s", e)
            self.set_state("error", "CUDA not active")
            self.tray.notify(f"{e}\nSet asr.allow_cpu_fallback: true to run on CPU.", "LocalFlow: GPU error")
            return
        except Exception as e:
            log.exception("ASR load failed")
            self.set_state("error", "ASR load failed")
            if downloading:
                self.tray.notify(
                    f"Could not download the speech model: {e}\n"
                    "Check your internet connection and start LocalFlow again, or run install.bat.",
                    "LocalFlow: download failed",
                )
            else:
                self.tray.notify(f"ASR load failed: {e}", "LocalFlow error")
            return

        try:
            self.recorder.open()
        except Exception as e:
            log.error("audio open failed: %s", e)
            self.tray.notify(f"Microphone error: {e}", "LocalFlow")

        if self.llm.available():
            if self.llm.has_model():
                self.llm_ok = True
                lvl = self.cfg["cleanup"]["level"]

                def _warm(level=lvl if lvl != "none" else "light"):
                    # in the background: a saturated GPU (game + render) can make this take minutes,
                    # and dictation must not wait for it (calls fall back to rules until it is warm)
                    ms = self.llm.warmup(level=level)
                    log.info("LLM %s warm in %.0f ms", self.llm.model, ms)

                threading.Thread(target=_warm, name="llm-warm-startup", daemon=True).start()
            else:
                log.warning("Ollama is running but model %s is missing (ollama pull %s); LLM cleanup disabled", self.llm.model, self.llm.model)
        else:
            log.warning("Ollama not reachable at %s; LLM cleanup disabled (rules only)", self.llm.host)

        self.ready = True
        self.hotkeys.start()
        self.fullscreen.start()
        self.set_state("idle")
        self.tray.set_state("idle", f"LocalFlow {__version__} — hold {'+'.join(sorted(self.hotkeys.ptt))} to talk")
        log.info("LocalFlow %s ready.", __version__)

    # ------------------------------------------------------------------ recording
    def start_recording(self, polish: bool = False) -> None:
        if not self.ready or self.state in ("listening", "handsfree"):
            return
        if self.paused:
            beep("error", self._sounds())  # engines unloaded: Resume from the tray / dot menu first
            return
        self.polish_mode = polish
        if self.llm_ok:
            # the key just went down: if Ollama has unloaded gemma (keep_alive), reload it now so the
            # load overlaps with the user speaking instead of adding to the paste latency
            lvl = self.cfg["cleanup"]["level"]
            self.llm.warm_if_idle(lvl if lvl != "none" else "light")
        try:
            self.recorder.start()
        except Exception as e:
            self.set_state("error", "Mic error")
            log.error("recorder.start failed: %s", e)
            return
        self.set_state("listening")
        beep("start", self._sounds())
        self._start_level_feed()

    def _start_level_feed(self) -> None:
        def feed():
            while self.state in ("listening", "handsfree"):
                self.pill.set_level(self.recorder.level)
                time.sleep(0.05)

        self._level_thread = threading.Thread(target=feed, daemon=True)
        self._level_thread.start()

    def stop_recording(self, held_ms: float = 1e9) -> None:
        if self.handsfree or self.state != "listening":
            return
        if held_ms <= self.tap_max_ms:
            # a tap (possibly the first half of a double-tap) is not an utterance
            self.recorder.cancel()
            self.set_state("idle")
            return
        audio = self.recorder.stop()
        beep("stop", self._sounds())
        self._queue.put((audio, self.polish_mode, False))

    def toggle_handsfree(self) -> None:
        if not self.ready:
            return
        if self.paused and not self.handsfree:
            beep("error", self._sounds())
            return
        if self.handsfree:
            self.handsfree = False
            audio = self.recorder.stop()
            beep("handsfree_off", self._sounds())
            if self._handsfree_whole():
                # one transcription of the whole speech, cleaned at the enhanced level
                self._queue.put((audio, self.cfg["cleanup"].get("handsfree_level", "high"), True))
            else:
                self._queue.put((audio, False, True))
            if self._queue.empty() and self.state != "processing":
                self.set_state("idle")
            log.info("hands-free off")
        else:
            chunked = not self._handsfree_whole()
            try:
                if self.state == "listening" and self.recorder.recording:
                    self.recorder.set_chunked(chunked)  # PTT hold upgraded to hands-free
                else:
                    self.recorder.start(chunked=chunked)
            except Exception as e:
                log.error("recorder.start failed: %s", e)
                return
            self.handsfree = True
            self.polish_mode = False
            self.set_state("handsfree")
            beep("handsfree", self._sounds())
            self._start_level_feed()
            if chunked:
                log.info("hands-free on (chunked: paste at each %d ms pause)",
                         self.cfg["audio"].get("handsfree_silence_ms", 700))
            else:
                log.info("hands-free on (whole: transcribe everything when you stop; cleanup level %s)",
                         self.cfg["cleanup"].get("handsfree_level", "high"))

    def _handsfree_whole(self) -> bool:
        return str(self.cfg["audio"].get("handsfree_mode", "whole")).lower() != "chunked"

    def _on_chunk(self, audio) -> None:
        """Called from the audio thread when a hands-free pause closes a chunk."""
        self._queue.put((audio, False, True))

    def _on_max_duration(self) -> None:
        log.info("max duration reached; finalising")
        if self.handsfree:
            self.toggle_handsfree()
        else:
            self.stop_recording()

    def cancel(self) -> None:
        if self.state in ("listening", "handsfree") or self.handsfree:
            self.recorder.cancel()
            self.handsfree = False
            self.set_state("idle")
            beep("cancel", self._sounds())
            log.info("cancelled")

    def repaste(self) -> None:
        icfg = self.cfg["inject"]
        method = inject.resolve_method(
            icfg.get("method", "auto"), icfg.get("type_apps"), force_scancode=bool(icfg.get("force_scancode", False))
        )
        if inject.repaste_last(
            method=method,
            restore_ms=int(icfg.get("restore_clipboard_ms", 150)),
            scancode_delay_ms=int(icfg.get("scancode_delay_ms", 12)),
            scancode_newlines=bool(icfg.get("scancode_newlines", False)),
            scancode_max_chars=int(icfg.get("scancode_max_chars", 500)),
        ):
            log.info("re-pasted last text (%s)", method)

    # ------------------------------------------------------------------ pipeline
    def _worker_loop(self) -> None:
        while True:
            audio, polish, from_handsfree = self._queue.get()
            try:
                with self._pipe_lock:
                    self._process(audio, polish, from_handsfree)
            except Exception:
                log.exception("pipeline failed")
                self.set_state("error")
            finally:
                self._queue.task_done()

    def _level_for_window(self, title: str) -> str:
        level = self.cfg["cleanup"].get("level", "light")
        for sub, lv in (self.cfg["cleanup"].get("per_app") or {}).items():
            if sub and sub.lower() in title.lower():
                return lv
        return level

    def _after_state(self, ok: bool = True) -> None:
        if self.handsfree:
            self.set_state("handsfree")
        else:
            self.set_state("done" if ok else "error")

    def run_pipeline(self, audio, *, mode: str = "ptt", level_override: str | None = None, app_title: str = "") -> PipelineResult:
        """Audio -> text, with no side effects (no state change, injection, history or log line).

        mode: "ptt" (one phrase, cleaned at cleanup.level / per_app for `app_title`),
              "polish" (the polish hotkey: llm.polish at level high),
              "handsfree" (a whole speech: llm.cleanup_long at cleanup.handsfree_level, in
              sentence-aligned segments with the rules Backtrack as per-segment fallback).
        `level_override` replaces the mode's level. Raises ASRError if the engine fails.
        """
        t0 = time.perf_counter()
        ccfg = self.cfg["cleanup"]
        res = PipelineResult(audio_s=round(len(audio) / self.recorder.sr, 2))
        ok, why = self.recorder.is_usable(audio)
        if not ok:
            res.empty, res.reason = True, f"dropped: {why}"
            res.timings["total_ms"] = (time.perf_counter() - t0) * 1000
            return res
        try:
            t = time.perf_counter()
            raw = self.engine.transcribe(self.recorder.prepare(audio))
            ms_asr = (time.perf_counter() - t) * 1000
        except Exception as e:
            raise ASRError(str(e)) from e
        res.raw = raw or ""
        res.timings["asr_ms"] = ms_asr
        if not raw or not re.search(r"\w", raw):
            # silence / breath noise: the ASR returns nothing (or just punctuation) -> drop quietly
            res.empty, res.reason = True, f"silence ({why})"
            res.timings["total_ms"] = (time.perf_counter() - t0) * 1000
            return res

        whole_speech = mode == "handsfree"
        polish = mode == "polish"
        if level_override:
            level = level_override
        elif whole_speech:
            level = ccfg.get("handsfree_level", "high")
        elif polish:
            level = "high"
        else:
            level = self._level_for_window(app_title)
        res.level = level
        use_llm = level != "none" and self.llm_ok
        t = time.perf_counter()
        # with the LLM on, self-corrections (Backtrack) and list formatting are left to the model;
        # the rules versions run afterwards only if the LLM fails or times out
        cr = cleanup.clean(raw, ccfg, defer_lists=use_llm, defer_backtrack=use_llm)
        ms_rules = (time.perf_counter() - t) * 1000
        text = cr.text
        res.press_enter, res.snippet_fired = cr.press_enter, cr.snippet_fired

        ms_llm, used = 0.0, False
        if text and use_llm and not cr.snippet_fired:
            if whole_speech:
                out = self.llm.cleanup_long(text, level, fallback=lambda s: cleanup.apply_backtrack(s, ccfg)[0])
            elif polish:
                out = self.llm.polish(text)
            else:
                out = self.llm.cleanup(text, level)
            ms_llm, used = out.ms, out.used
            if out.used:
                text = cleanup.post_llm(out.text, ccfg)
            else:
                log.info("LLM not used (%s); applying rules Backtrack fallback", out.reason)
        if use_llm and not used and text:
            t2 = time.perf_counter()
            text, n_bt = cleanup.apply_backtrack(text, ccfg)
            ms_rules += (time.perf_counter() - t2) * 1000
            if n_bt:
                log.debug("rules Backtrack fallback removed %d clause(s)", n_bt)
        if use_llm and text and not cr.snippet_fired and ccfg.get("auto_lists", True):
            # rules-based list formatting: the LLM fallback path, or an LLM output that left a
            # clear enumeration inline (auto_list never touches text that already has bullets)
            text, _ = cleanup.auto_list(text, ccfg.get("list_leadins"))

        res.text, res.llm_used = text, used
        res.timings.update(asr_ms=ms_asr, rules_ms=ms_rules, llm_ms=ms_llm, total_ms=(time.perf_counter() - t0) * 1000)
        if not text:
            res.empty, res.reason = True, "cleaned away"
        return res

    def _process(self, audio, polish: bool = False, from_handsfree: bool = False) -> None:
        t_release = time.perf_counter()
        ok, why = self.recorder.is_usable(audio)
        if not ok:
            log.info("clip dropped: %s", why)
            if not self.handsfree:
                self.set_state("idle")
            return
        if not self.handsfree:
            self.set_state("processing")
        title = inject.foreground_window_title()
        icfg = self.cfg["inject"]
        # `polish` is False (normal), True (polish hotkey) or a level name: a whole hands-free
        # speech, cleaned at cleanup.handsfree_level in sentence-aligned segments.
        if isinstance(polish, str):
            mode, level_override = "handsfree", polish
        elif polish:
            mode, level_override = "polish", None
        else:
            mode, level_override = "ptt", None
        try:
            res = self.run_pipeline(audio, mode=mode, level_override=level_override, app_title=title)
        except ASRError:
            log.exception("ASR failed")
            self._after_state(ok=False)
            beep("error", self._sounds())
            return
        if res.empty:
            if res.reason.startswith("silence"):
                log.debug("empty transcription (%s): %r", why, res.raw)
                if self.handsfree:
                    self.set_state("handsfree")
                else:
                    self.set_state("idle")
            else:
                log.info("nothing to paste after cleanup (raw=%r)", res.raw)
                self._after_state()
            return
        text, raw, level, used = res.text, res.raw, res.level, res.llm_used
        ms_asr, ms_rules, ms_llm = res.timings["asr_ms"], res.timings["rules_ms"], res.timings["llm_ms"]

        method = inject.resolve_method(
            icfg.get("method", "auto"), icfg.get("type_apps"), force_scancode=bool(icfg.get("force_scancode", False))
        )
        trailing = bool(icfg.get("handsfree_trailing_space", True)) if from_handsfree else bool(icfg.get("trailing_space", False))
        t = time.perf_counter()
        pasted = inject.inject(
            text,
            press_enter=res.press_enter,  # only ever pressed when the user literally said "press enter"
            method=method,
            restore_ms=int(icfg.get("restore_clipboard_ms", 150)),
            trailing_space=trailing,
            enter_delay_ms=int(icfg.get("enter_delay_ms", 150)),
            scancode_delay_ms=int(icfg.get("scancode_delay_ms", 12)),
            scancode_newlines=bool(icfg.get("scancode_newlines", False)),
            scancode_max_chars=int(icfg.get("scancode_max_chars", 500)),
        )
        ms_inject = (time.perf_counter() - t) * 1000
        total = (time.perf_counter() - t_release) * 1000

        self.history.append(
            raw, text, ms_asr, ms_llm, ms_rules=round(ms_rules, 2), ms_total=round(total, 1),
            level=level, llm_used=used, press_enter=res.press_enter, handsfree=from_handsfree,
            app=title[:80], audio_s=round(len(audio) / self.recorder.sr, 2),
        )
        log.info(
            "%.1fs audio | asr %.0f ms | rules %.1f ms | llm %.0f ms (%s, %s) | inject %.0f ms (%s) | total %.0f ms | %r",
            len(audio) / self.recorder.sr, ms_asr, ms_rules, ms_llm, level, "used" if used else "skipped",
            ms_inject, method, total, text if self.debug else text[:60],
        )
        if not pasted:
            self.tray.notify("Paste failed — the text is on your clipboard (Ctrl+V).")
        self._after_state(ok=pasted)

    # ------------------------------------------------------------------ phone API
    def dictate_remote(self, audio, mode: str, level: str | None, app: str) -> PipelineResult:
        """Server entry point (called under self._pipe_lock by the server): pipeline + history + log."""
        res = self.run_pipeline(audio, mode=mode, level_override=level, app_title=app)
        if res.empty:
            log.info("phone %s (%s): %s", app, mode, res.reason)
            res.raw = ""  # contract: raw is empty alongside text when nothing was said
            return res
        t = res.timings
        self.history.append(
            res.raw, res.text, t["asr_ms"], t["llm_ms"], ms_rules=round(t["rules_ms"], 2), ms_total=round(t["total_ms"], 1),
            level=res.level, llm_used=res.llm_used, press_enter=res.press_enter, handsfree=(mode == "handsfree"),
            app=app[:80], audio_s=res.audio_s,
        )
        log.info(
            "%.1fs audio | asr %.0f ms | rules %.1f ms | llm %.0f ms (%s, %s) | phone %s (%s) | total %.0f ms | %r",
            res.audio_s, t["asr_ms"], t["rules_ms"], t["llm_ms"], res.level, "used" if res.llm_used else "skipped",
            app, mode, t["total_ms"], res.text if self.debug else res.text[:60],
        )
        return res

    def server_status(self) -> dict:
        return {
            "version": __version__,
            "engine": getattr(self.engine, "name", self.cfg["asr"].get("engine", "")),
            "gpu": bool(self.engine is not None and self.engine.on_gpu()),
            "llm": self.llm.model,
            "llm_ok": self.llm_ok,
            "ready": self.ready,
            "paused": self.paused,
        }

    def _ensure_token(self) -> str:
        scfg = self.cfg["server"]
        if not scfg.get("token"):
            scfg["token"] = secrets.token_urlsafe(24)  # 32 chars
            self.save_cfg()
            log.info("phone API token created and saved to %s", self.cfg_path.name)
        return scfg["token"]

    def start_server(self) -> None:
        """Start the phone API thread (idempotent). Never blocks: tailscale setup runs in the background."""
        if self.server is not None:
            return
        from .server import DictationServer

        scfg = self.cfg["server"]
        self._ensure_token()
        try:
            self.server = DictationServer(
                scfg, pipeline=self.dictate_remote, status=self.server_status, warm=self.warm_remote,
                lock=self._pipe_lock,
                max_seconds=float(self.cfg["audio"].get("max_seconds", 1200)),
            )
            self.server.start()
        except Exception as e:
            log.error("phone API failed to start on %s:%s: %s", scfg.get("bind"), scfg.get("port"), e)
            self.server = None
            self.tray.notify(f"Phone access could not start: {e}", "LocalFlow")

    def warm_remote(self) -> dict:
        """The phone started recording: reload the cleanup model in the background if it was
        unloaded after idling, exactly as the desktop does on hotkey key-down."""
        loaded = True
        if self.llm is not None and self.llm_ok:
            try:
                level = self.cfg["cleanup"].get("handsfree_level", "high")
                resident = self.llm.is_loaded()  # ask Ollama; the idle timer can be wrong
                loaded = bool(resident) if resident is not None else not self.llm.needs_warm()
                if not loaded:
                    self.llm.warm_now(level=level)
                else:
                    self.llm.warm_if_idle(level=level)
            except Exception as e:  # noqa: BLE001
                log.debug("warm_remote: %s", e)
        return {"llm_loaded": loaded}

    def stop_server(self) -> None:
        srv, self.server = self.server, None
        if srv is not None:
            srv.stop()

    def toggle_server(self) -> None:
        on = not self.cfg["server"].get("enabled", False)
        self.cfg["server"]["enabled"] = on
        self.save_cfg()
        log.info("phone access -> %s", "on" if on else "off")
        if on:
            self.start_server()
        else:
            self.stop_server()

    def phone_setup(self) -> None:
        """Small dialog with the URL, the token and a "Copy setup line" button (URL|TOKEN)."""
        token = self._ensure_token()
        enabled = bool(self.cfg["server"].get("enabled", False))
        port = int(self.cfg["server"].get("port", 8770))
        url = (self.server.public_url if self.server else None) or ""
        if not url:
            from .server import tailscale_available, tailscale_dnsname

            name = tailscale_dnsname() if tailscale_available() else None
            url = f"http://{name}" if name else f"http://127.0.0.1:{port}"
        note = "" if enabled else 'Phone access is OFF: tick "Enable phone access" in the menu first.'

        def _dialog():
            try:
                import tkinter as tk
                from tkinter import ttk
            except Exception as e:
                log.error("tkinter unavailable for the phone setup dialog: %s", e)
                self.tray.notify(f"{note}\nURL: {url}\nToken: {token}", "LocalFlow phone setup")
                return
            root = tk.Tk()
            root.title("LocalFlow — Phone setup")
            root.attributes("-topmost", True)
            root.resizable(False, False)
            frm = ttk.Frame(root, padding=14)
            frm.grid()
            if note:
                ttk.Label(frm, text=note, foreground="#b45309", wraplength=420).grid(column=0, row=0, columnspan=2, sticky="w", pady=(0, 8))
            ttk.Label(frm, text="URL").grid(column=0, row=1, sticky="w", padx=(0, 8))
            e1 = ttk.Entry(frm, width=52)
            e1.insert(0, url)
            e1.configure(state="readonly")
            e1.grid(column=1, row=1, sticky="w", pady=2)
            ttk.Label(frm, text="Token").grid(column=0, row=2, sticky="w", padx=(0, 8))
            e2 = ttk.Entry(frm, width=52)
            e2.insert(0, token)
            e2.configure(state="readonly")
            e2.grid(column=1, row=2, sticky="w", pady=2)
            status = ttk.Label(frm, text="Paste the setup line into the phone app. Health check: URL + /v1/health", wraplength=420)
            status.grid(column=0, row=3, columnspan=2, sticky="w", pady=(8, 4))

            def copy():
                line = f"{url}|{token}"
                try:
                    import pyperclip

                    pyperclip.copy(line)
                except Exception:
                    root.clipboard_clear()
                    root.clipboard_append(line)
                    root.update()
                status.configure(text="Copied  URL|TOKEN  to the clipboard.")

            btns = ttk.Frame(frm)
            btns.grid(column=0, row=4, columnspan=2, sticky="e")
            ttk.Button(btns, text="Copy setup line", command=copy).grid(column=0, row=0, padx=(0, 6))
            ttk.Button(btns, text="Close", command=root.destroy).grid(column=1, row=0)
            root.eval("tk::PlaceWindow . center")
            root.mainloop()

        threading.Thread(target=_dialog, name="phone-setup", daemon=True).start()

    # ------------------------------------------------------------------ shutdown
    def quit(self) -> None:
        log.info("quitting")
        try:
            self.fullscreen.stop()
            self.hotkeys.stop()
            self.recorder.close()
            self.pill.stop()
            srv, self.server = self.server, None
            if srv is not None:
                srv.stop(remove_tailscale=False)  # the mapping is harmless while the app is down
        finally:
            self.tray.stop()

    def run(self) -> None:
        self.pill.start()
        if self.cfg["server"].get("enabled", False):
            self.start_server()  # answers 503 until load_models sets ready
        self.tray.run(setup=self.load_models)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="localflow", description="Local push-to-talk dictation for Windows.")
    ap.add_argument("--debug", action="store_true", help="verbose logging in a console")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    ap.add_argument("--version", action="version", version=f"LocalFlow {__version__}")
    ap.add_argument(
        "--verbose", action="store_true",
        help="with --doctor: include audio device names and full paths (not for public pastes)",
    )
    ap.add_argument(
        "--doctor", action="store_true",
        help="print an environment diagnostic report (paste this into bug reports) and exit",
    )
    args = ap.parse_args(argv)

    if args.doctor:
        from .doctor import main as doctor_main

        return doctor_main(verbose=args.verbose)

    cfg_path = Path(args.config) if args.config else config.CONFIG_PATH
    cfg = config.load(cfg_path)
    if args.debug:
        cfg["debug"] = True
    setup_logging(bool(cfg.get("debug")))
    log.info("LocalFlow %s starting (Python %s, config %s)", __version__, platform.python_version(), cfg_path)

    # single-instance guard
    try:
        import ctypes

        ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\LocalFlowSingleton")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            log.error("LocalFlow is already running")
            return 1
    except Exception:
        pass

    app = App(cfg, cfg_path)
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
