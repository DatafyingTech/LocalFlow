"""Global hotkeys via pynput (never suppress=True: on Windows that blocks every key).

Chords are sets of key names from config.yaml. PTT = hold the chord; the moment any key of the
chord is released the utterance is finalised. Double-tapping the PTT chord (two quick taps)
toggles hands-free mode, as does the dedicated hands-free chord. Esc cancels; the repaste
chord re-injects the last result; the polish chord is PTT with the big-model cleanup.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable

from pynput import keyboard
from pynput.keyboard import Key, KeyCode

log = logging.getLogger("localflow.hotkeys")

_KEY_ALIASES = {
    "win": "win", "cmd": "win", "super": "win", "meta": "win", "windows": "win",
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "space": "space", "esc": "esc", "escape": "esc", "tab": "tab", "enter": "enter", "return": "enter",
    "caps_lock": "caps_lock", "capslock": "caps_lock",
}

_SPECIAL = {
    Key.ctrl: "ctrl", Key.ctrl_l: "ctrl", Key.ctrl_r: "ctrl",
    Key.alt: "alt", Key.alt_l: "alt", Key.alt_r: "alt", Key.alt_gr: "alt",
    Key.shift: "shift", Key.shift_l: "shift", Key.shift_r: "shift",
    Key.cmd: "win", Key.cmd_l: "win", Key.cmd_r: "win",
    Key.space: "space", Key.esc: "esc", Key.tab: "tab", Key.enter: "enter",
    Key.caps_lock: "caps_lock",
}
for _i in range(1, 13):
    _SPECIAL[getattr(Key, f"f{_i}")] = f"f{_i}"

_VK_MAP = {0x20: "space", 0x1B: "esc", 0x09: "tab", 0x0D: "enter"}
for _vk in range(0x30, 0x3A):
    _VK_MAP[_vk] = chr(_vk)  # 0-9
for _vk in range(0x41, 0x5B):
    _VK_MAP[_vk] = chr(_vk).lower()  # a-z
for _i in range(1, 13):
    _VK_MAP[0x70 + _i - 1] = f"f{_i}"


def normalize_chord(names: Iterable[str]) -> frozenset[str]:
    out = set()
    for n in names or []:
        n = str(n).strip().lower()
        out.add(_KEY_ALIASES.get(n, n))
    return frozenset(out)


def key_name(key) -> str | None:
    if key in _SPECIAL:
        return _SPECIAL[key]
    if isinstance(key, KeyCode):
        if key.vk in _VK_MAP:  # vk is reliable even when ctrl mangles .char
            return _VK_MAP[key.vk]
        if key.char:
            return key.char.lower()
    return None


class Hotkeys:
    def __init__(
        self,
        cfg: dict,
        *,
        on_ptt_start: Callable[[], None],
        on_ptt_stop: Callable[[float], None],  # receives held_ms
        on_toggle_handsfree: Callable[[], None],
        on_cancel: Callable[[], None],
        on_repaste: Callable[[], None],
        on_polish_start: Callable[[], None] | None = None,
        on_polish_stop: Callable[[], None] | None = None,
        is_active: Callable[[], bool] = lambda: False,
        is_handsfree: Callable[[], bool] = lambda: False,
    ):
        self.ptt = normalize_chord(cfg.get("ptt") or ["ctrl", "win"])
        self.handsfree = normalize_chord(cfg.get("handsfree") or [])
        self.cancel = normalize_chord(cfg.get("cancel") or ["esc"])
        self.repaste = normalize_chord(cfg.get("repaste") or [])
        self.polish = normalize_chord(cfg.get("polish") or [])
        self.double_tap = bool(cfg.get("double_tap_toggle", True))
        self.double_tap_ms = int(cfg.get("double_tap_ms", 400))
        self.tap_max_ms = int(cfg.get("tap_max_ms", 250))

        self.on_ptt_start = on_ptt_start
        self.on_ptt_stop = on_ptt_stop
        self.on_toggle_handsfree = on_toggle_handsfree
        self.on_cancel = on_cancel
        self.on_repaste = on_repaste
        self.on_polish_start = on_polish_start
        self.on_polish_stop = on_polish_stop
        self.is_active = is_active
        self.is_handsfree = is_handsfree

        self._down: set[str] = set()
        self._ptt_held = False
        self._polish_held = False
        self._ptt_down_at = 0.0
        self._last_tap_at = 0.0
        self._fired: set[frozenset] = set()  # chords already fired while still held
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None
        self.enabled = True

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        log.info(
            "Hotkeys: PTT=%s hands-free=%s cancel=%s repaste=%s polish=%s double-tap=%s",
            "+".join(sorted(self.ptt)), "+".join(sorted(self.handsfree)), "+".join(sorted(self.cancel)),
            "+".join(sorted(self.repaste)), "+".join(sorted(self.polish)) or "-", self.double_tap,
        )

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    # ------------------------------------------------------------------ helpers
    def _chord_down(self, chord: frozenset[str]) -> bool:
        return bool(chord) and chord <= self._down

    def _exact(self, chord: frozenset[str]) -> bool:
        """chord is held and nothing else that belongs to a *longer* configured chord is."""
        return self._chord_down(chord)

    def _safe(self, cb: Callable | None, what: str) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.exception("hotkey callback %s failed", what)

    # ------------------------------------------------------------------ events
    def _on_press(self, key) -> None:
        name = key_name(key)
        if name is None or not self.enabled:
            return
        with self._lock:
            first = name not in self._down
            self._down.add(name)
            if not first:
                return  # key auto-repeat
            now = time.monotonic()

            # cancel (Esc) - only meaningful while recording/processing
            if self._chord_down(self.cancel) and self.is_active():
                self._reset_holds()
                self._safe(self.on_cancel, "cancel")
                return

            # longer chords first so Ctrl+Win+Space does not also start PTT
            if self._chord_down(self.handsfree) and self.handsfree not in self._fired:
                self._fired.add(self.handsfree)
                if self._ptt_held:  # Space pressed while Ctrl+Win held: convert to hands-free
                    self._ptt_held = False
                self._safe(self.on_toggle_handsfree, "handsfree")
                return
            if self.polish and self._chord_down(self.polish) and not self._polish_held:
                if self._ptt_held:  # Alt added while Ctrl+Win held -> upgrade to polish
                    self._ptt_held = False
                self._polish_held = True
                self._safe(self.on_polish_start, "polish_start")
                return
            if self._chord_down(self.repaste) and self.repaste not in self._fired:
                self._fired.add(self.repaste)
                self._safe(self.on_repaste, "repaste")
                return
            if self._chord_down(self.ptt) and not self._ptt_held and not self._polish_held:
                if self.polish and self.polish > self.ptt and self._chord_down(self.polish):
                    return
                if self.ptt in self._fired:
                    return
                if self.is_handsfree():
                    # single tap of the PTT chord while hands-free -> end hands-free
                    self._fired.add(self.ptt)
                    self._last_tap_at = 0.0
                    self._safe(self.on_toggle_handsfree, "handsfree-off")
                    return
                if self.double_tap and (now - self._last_tap_at) * 1000 <= self.double_tap_ms:
                    self._last_tap_at = 0.0
                    self._fired.add(self.ptt)  # swallow this press; release does nothing
                    self._safe(self.on_toggle_handsfree, "double-tap")
                    return
                self._ptt_held = True
                self._ptt_down_at = now
                self._safe(self.on_ptt_start, "ptt_start")

    def _on_release(self, key) -> None:
        name = key_name(key)
        if name is None:
            return
        with self._lock:
            self._down.discard(name)
            now = time.monotonic()
            # chords that fired on press are re-armed once any of their keys is released
            for chord in list(self._fired):
                if name in chord:
                    self._fired.discard(chord)
            if self._polish_held and name in self.polish:
                self._polish_held = False
                self._safe(self.on_polish_stop, "polish_stop")
                return
            if self._ptt_held and name in self.ptt:
                self._ptt_held = False
                held_ms = (now - self._ptt_down_at) * 1000
                if self.double_tap and held_ms <= self.tap_max_ms:
                    self._last_tap_at = now
                try:
                    self.on_ptt_stop(held_ms)
                except Exception:
                    log.exception("hotkey callback ptt_stop failed")

    def _reset_holds(self) -> None:
        self._ptt_held = False
        self._polish_held = False
        self._last_tap_at = 0.0
