"""Manual check of the scancode injection path: open Notepad, focus it, type a sentence with
SendInput scan codes, read the text back (Ctrl+A, Ctrl+C) and compare. Closes Notepad afterwards.

    python -m tests.notepad_scancode ["custom text"]

Steals focus for ~3 s. Exit code 0 only if the text round-trips exactly.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

import pyperclip

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localflow import inject, winfocus  # noqa: E402

TEXT = "Hello, World! It's 5pm: test (ok)?"
u32 = ctypes.windll.user32


def _wait_notepad(pid: int, timeout=8.0) -> int:
    t0 = time.time()
    while time.time() - t0 < timeout:
        info = winfocus.foreground()
        if info.process.lower() == "notepad.exe":
            return info.hwnd
        time.sleep(0.1)
    return 0


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else TEXT
    prev_clip = None
    try:
        prev_clip = pyperclip.paste()
    except Exception:
        pass
    proc = subprocess.Popen(["notepad.exe"])
    hwnd = _wait_notepad(proc.pid)
    if not hwnd:
        # Notepad may have opened but not taken focus (e.g. a fullscreen game in front): try to front it
        time.sleep(1.0)
        info = winfocus.foreground()
        print(f"foreground is {info.process} {info.title!r}; Notepad did not take focus")
        return 2
    time.sleep(0.6)  # let the editor initialise
    # Windows 11 Notepad restores unsaved tabs from the last session: work in a fresh tab
    inject._kb.press(inject.Key.ctrl)
    inject._kb.press("n")
    inject._kb.release("n")
    inject._kb.release(inject.Key.ctrl)
    time.sleep(0.4)
    print(f"Notepad focused (hwnd {hwnd}); typing via scancode ...")
    t = time.perf_counter()
    ok = inject.inject(text, method="scancode", scancode_delay_ms=12)
    ms = (time.perf_counter() - t) * 1000
    time.sleep(0.3)
    # read back: select all + copy
    pyperclip.copy("")
    inject._kb.press(inject.Key.ctrl)
    inject._kb.press("a")
    inject._kb.release("a")
    inject._kb.press("c")
    inject._kb.release("c")
    inject._kb.release(inject.Key.ctrl)
    time.sleep(0.3)
    got = pyperclip.paste().replace("\r\n", "\n")
    # clear the tab so Notepad closes it without an unsaved-tab prompt (Ctrl+W), then close the app
    inject._kb.press(inject.Key.delete)
    inject._kb.release(inject.Key.delete)
    time.sleep(0.2)
    inject._kb.press(inject.Key.ctrl)
    inject._kb.press("w")
    inject._kb.release("w")
    inject._kb.release(inject.Key.ctrl)
    time.sleep(0.3)
    u32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE to *Notepad* (our test target, not a game)
    time.sleep(0.5)
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass
    if prev_clip is not None:
        try:
            pyperclip.copy(prev_clip)
        except Exception:
            pass
    print(f"sent   : {text!r}  (inject returned {ok}, {ms:.0f} ms)")
    print(f"readback: {got!r}")
    match = got == text
    print("SCANCODE TEST", "PASSED" if match else "FAILED")
    return 0 if match else 1


if __name__ == "__main__":
    sys.exit(main())
