"""Hotkey state machine, driven with synthetic events and a fake "is this key physically down" probe.

The bug these guard against: the pressed-key set was built only from hook events. Windows does not
deliver a key-up in several ordinary situations (Win+L, a UAC prompt, an elevated window in
front), so "win" stayed in the set forever and the next lone Ctrl looked like Ctrl+Win and started
a recording.
"""
import pytest

pytest.importorskip("pynput")

from pynput.keyboard import Key  # noqa: E402

from localflow.hotkeys import Hotkeys  # noqa: E402

CFG = {"ptt": ["ctrl", "win"], "handsfree": ["ctrl", "win", "space"], "cancel": ["esc"],
       "repaste": ["shift", "alt", "z"], "polish": ["ctrl", "win", "alt"],
       "double_tap_toggle": True, "double_tap_ms": 400, "tap_max_ms": 250}


class Rig:
    def __init__(self):
        self.events: list[str] = []
        self.physical: dict[str, bool] = {}
        self.hk = Hotkeys(
            CFG,
            on_ptt_start=lambda: self.events.append("start"),
            on_ptt_stop=lambda held_ms: self.events.append("stop"),
            on_toggle_handsfree=lambda: self.events.append("handsfree"),
            on_cancel=lambda: self.events.append("cancel"),
            on_repaste=lambda: self.events.append("repaste"),
            on_polish_start=lambda: self.events.append("polish_start"),
            on_polish_stop=lambda: self.events.append("polish_stop"),
            probe=lambda name: self.physical.get(name),
        )

    def press(self, key, name):
        self.physical[name] = True
        self.hk._on_press(key)

    def release(self, key, name, *, deliver=True):
        self.physical[name] = False
        if deliver:
            self.hk._on_release(key)


def test_normal_hold_starts_and_stops():
    r = Rig()
    r.press(Key.ctrl_l, "ctrl")
    r.press(Key.cmd, "win")
    assert r.events == ["start"]
    r.release(Key.cmd, "win")
    assert r.events == ["start", "stop"]


def test_missed_win_release_does_not_make_ctrl_alone_record():
    """The reported bug: Win's key-up never arrives, then Ctrl alone starts a recording."""
    r = Rig()
    r.press(Key.ctrl_l, "ctrl")
    r.press(Key.cmd, "win")
    r.release(Key.ctrl_l, "ctrl")                 # stops the recording normally
    r.release(Key.cmd, "win", deliver=False)      # Windows swallowed this key-up (lock / UAC / admin window)
    r.events.clear()

    r.press(Key.ctrl_l, "ctrl")                   # the user just wants Ctrl+C
    assert r.events == [], "a lone Ctrl must never start a recording"
    assert "win" not in r.hk._down


def test_stale_key_is_dropped_even_when_it_is_the_other_modifier():
    r = Rig()
    r.press(Key.cmd, "win")
    r.press(Key.ctrl_l, "ctrl")
    r.release(Key.cmd, "win")
    r.release(Key.ctrl_l, "ctrl", deliver=False)  # this time Ctrl's key-up went missing
    r.events.clear()
    r.press(Key.cmd, "win")                       # opening the Start menu
    assert r.events == []


def test_real_chord_still_works_after_a_stale_key_was_cleared():
    r = Rig()
    r.press(Key.ctrl_l, "ctrl")
    r.press(Key.cmd, "win")
    r.release(Key.ctrl_l, "ctrl")
    r.release(Key.cmd, "win", deliver=False)
    r.events.clear()
    r.press(Key.ctrl_l, "ctrl")
    r.release(Key.ctrl_l, "ctrl")
    r.hk._last_tap_at = 0.0   # a real pause; otherwise these microsecond-apart presses are a double-tap
    r.press(Key.ctrl_l, "ctrl")
    r.press(Key.cmd, "win")
    assert r.events == ["start"]


def test_watchdog_stops_a_recording_whose_key_up_never_arrived():
    """The mirror image: recording stuck ON because the release was swallowed mid-hold."""
    r = Rig()
    r.press(Key.ctrl_l, "ctrl")
    r.press(Key.cmd, "win")
    assert r.events == ["start"]
    r.release(Key.cmd, "win", deliver=False)
    r.release(Key.ctrl_l, "ctrl", deliver=False)
    r.hk._watchdog_tick()
    assert r.events == ["start"], "one miss is not enough; the hook event may simply be in flight"
    r.hk._watchdog_tick()
    assert r.events == ["start", "stop"]
    assert not r.hk._down


def test_watchdog_leaves_a_genuinely_held_chord_alone():
    r = Rig()
    r.press(Key.ctrl_l, "ctrl")
    r.press(Key.cmd, "win")
    for _ in range(10):
        r.hk._watchdog_tick()
    assert r.events == ["start"]


def test_keys_the_probe_cannot_verify_are_kept():
    r = Rig()
    r.hk._down.add("some_odd_key")                # probe returns None for it
    r.press(Key.ctrl_l, "ctrl")
    r.hk._watchdog_tick(); r.hk._watchdog_tick()
    assert "some_odd_key" in r.hk._down
