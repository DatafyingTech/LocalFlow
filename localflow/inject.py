"""Put text into the focused window.

Methods:
  paste     clipboard set -> Ctrl+V -> restore clipboard after N ms (default for normal apps)
  scancode  type character by character with SendInput KEYEVENTF_SCANCODE (hardware scan codes),
            for games that read raw input and ignore synthetic Ctrl+V (FiveM chat box). Characters
            with no scan code in the current layout (emoji, curly quotes, non-ASCII) are sent as a
            KEYEVENTF_UNICODE event instead. Newlines become spaces (chat boxes are single-line)
            unless scancode_newlines is set. Enter is NEVER pressed unless the user said "press enter".
  type      pynput Controller.type() (legacy per-character unicode typing)
  auto      scancode when the foreground window is fullscreen or its process/title matches
            inject.type_apps (empty by default), paste otherwise

Before sending anything we wait until the user's hotkey modifiers (Ctrl/Win/Alt/Shift) are
physically released; otherwise Win+Ctrl+V would open the Windows clipboard history instead of
pasting, and typed letters would come out as shortcuts.

ANTI-CHEAT SAFETY (hard constraint - keep it that way):
  The game-typing path uses ONLY user-mode SendInput from our own process, i.e. the same OS
  mechanism as a macro keyboard, AutoHotkey, Steam Input or accessibility software. Detection
  uses read-only user32 window queries (GetForegroundWindow, GetWindowRect, GetWindowLong,
  GetClassName, GetWindowThreadProcessId, MonitorFromWindow) plus a Toolhelp process-list
  snapshot for the exe name. LocalFlow never touches the game process: no DLL injection, no
  SetWindowsHookEx into other processes, no OpenProcess / ReadProcessMemory on the game, no
  DirectX / overlay hooking, no driver, and no PostMessage / SendMessage of WM_CHAR (or anything
  else) to the game HWND. Keystroke timing is jittered (+/- 4 ms) so it does not look like a
  perfectly periodic macro, and typing is capped at inject.scancode_max_chars so a long dictation
  never floods a game. Intended for chat text only.
"""
from __future__ import annotations

import ctypes
import logging
import random
import threading
import time
from ctypes import wintypes

import pyperclip
from pynput.keyboard import Controller, Key

from . import winfocus

log = logging.getLogger("localflow.inject")

# private user32 instance: setting argtypes on the shared ctypes.windll.user32 would leak into pynput
_user32 = ctypes.WinDLL("user32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None
_VK_MODS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # SHIFT, CONTROL, MENU(alt), LWIN, RWIN

_kb = Controller()
_lock = threading.Lock()
_last_text: str = ""

METHODS = ("auto", "paste", "scancode", "type")
SCANCODE_JITTER_MS = 4  # +/- on every inter-key delay
DEFAULT_SCANCODE_MAX_CHARS = 500
DEFAULT_TYPE_APPS: list[str] = []  # e.g. ["FiveM"] matches FiveM.exe, FiveM_GTAProcess.exe (exe-name prefix)

# ---------------------------------------------------------------------- SendInput plumbing
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC_EX = 4
SC_LSHIFT, SC_LCTRL, SC_LALT, SC_RALT, SC_ENTER = 0x2A, 0x1D, 0x38, 0x38, 0x1C
VK_SHIFT, VK_CONTROL, VK_MENU, VK_RMENU, VK_RETURN = 0x10, 0x11, 0x12, 0xA5, 0x0D
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


if _user32 is not None:
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, wintypes.HKL]
    _user32.VkKeyScanExW.restype = ctypes.c_short
    _user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HKL]
    _user32.MapVirtualKeyExW.restype = wintypes.UINT
    _user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    _user32.GetKeyboardLayout.restype = wintypes.HKL


def _foreground_layout() -> int:
    """Keyboard layout (HKL) of the foreground window's thread; 0 = ours."""
    if _user32 is None:
        return 0
    try:
        hwnd = _user32.GetForegroundWindow()
        tid = _user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
        return _user32.GetKeyboardLayout(tid or 0) or 0
    except Exception:
        return 0


def _key_input(scan: int = 0, vk: int = 0, flags: int = 0, unicode_char: str | None = None) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    if unicode_char is not None:
        inp.u.ki = KEYBDINPUT(0, ord(unicode_char), KEYEVENTF_UNICODE | flags, 0, 0)
    else:
        inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return inp


def _send(*inputs: INPUT) -> bool:
    arr = (INPUT * len(inputs))(*inputs)
    n = _user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if n != len(inputs):
        log.debug("SendInput sent %d/%d (err %d)", n, len(inputs), ctypes.get_last_error())
        return False
    return True


def scancode_for(ch: str, hkl: int = 0) -> tuple[int, int, bool] | None:
    """(scan, modifier_bits, extended) for a character in the layout, or None if it has no key.

    modifier bits from VkKeyScan: 1 = Shift, 2 = Ctrl, 4 = Alt (6 = AltGr).
    """
    if _user32 is None or len(ch) != 1 or ord(ch) > 0xFFFF:
        return None  # non-BMP (emoji) has no key: unicode fallback
    r = _user32.VkKeyScanExW(ch, hkl)
    if r == -1:
        return None
    vk, mods = r & 0xFF, (r >> 8) & 0xFF
    if vk == 0 or vk == 0xFF:
        return None
    scan = _user32.MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC_EX, hkl)
    if not scan:
        return None
    ext = (scan >> 8) == 0xE0
    return scan & 0xFF, mods, ext


def _utf16_units(ch: str) -> list[str]:
    b = ch.encode("utf-16-le")
    return [b[i : i + 2].decode("utf-16-le", "surrogatepass") for i in range(0, len(b), 2)]


def _tap_scan(scan: int, ext: bool = False, vk: int = 0) -> bool:
    fl = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0)
    return _send(_key_input(scan, vk, fl), _key_input(scan, vk, fl | KEYEVENTF_KEYUP))


def _mods_down(mods: int) -> list[INPUT]:
    out = []
    if mods & 1:
        out.append(_key_input(SC_LSHIFT, VK_SHIFT, KEYEVENTF_SCANCODE))
    if mods & 6 == 6:  # AltGr = right Alt (extended 0x38)
        out.append(_key_input(SC_RALT, VK_RMENU, KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY))
    else:
        if mods & 2:
            out.append(_key_input(SC_LCTRL, VK_CONTROL, KEYEVENTF_SCANCODE))
        if mods & 4:
            out.append(_key_input(SC_LALT, VK_MENU, KEYEVENTF_SCANCODE))
    return out


def _mods_up(mods: int) -> list[INPUT]:
    return [_key_input(i.u.ki.wScan, i.u.ki.wVk, i.u.ki.dwFlags | KEYEVENTF_KEYUP) for i in reversed(_mods_down(mods))]


def type_scancodes(text: str, *, delay_ms: int = 12, newlines: bool = False, jitter_ms: int = SCANCODE_JITTER_MS) -> bool:
    """Type `text` with hardware scan codes. Returns False if SendInput failed for any key.

    The inter-key delay is `delay_ms` +/- `jitter_ms` (uniform), never below 1 ms.
    """
    if _user32 is None:
        return False
    hkl = _foreground_layout()
    base = max(0, delay_ms)
    ok = True
    for ch in text:
        if ch in "\r\n":
            if not newlines:
                continue  # converted to spaces by the caller; nothing to do here
            ok &= _tap_scan(SC_ENTER, vk=VK_RETURN)
        elif ch == "\t":
            ok &= _tap_scan(0x0F)
        else:
            sc = scancode_for(ch, hkl)
            if sc is None:
                # no key for this character in the layout: one unicode event (per UTF-16 unit)
                for unit in _utf16_units(ch):
                    ok &= _send(_key_input(unicode_char=unit), _key_input(unicode_char=unit, flags=KEYEVENTF_KEYUP))
            else:
                scan, mods, ext = sc
                fl = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0)
                seq = _mods_down(mods) + [_key_input(scan, 0, fl), _key_input(scan, 0, fl | KEYEVENTF_KEYUP)] + _mods_up(mods)
                ok &= _send(*seq)
        if base:
            time.sleep(max(1, base + random.uniform(-jitter_ms, jitter_ms)) / 1000)
    return ok


# ---------------------------------------------------------------------- helpers
def modifiers_down() -> bool:
    if _user32 is None:
        return False
    return any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in _VK_MODS)


def wait_modifiers_released(max_ms: int = 600) -> bool:
    t0 = time.perf_counter()
    while modifiers_down():
        if (time.perf_counter() - t0) * 1000 > max_ms:
            return False
        time.sleep(0.005)
    return True


def foreground_window_title() -> str:
    return winfocus.foreground().title


def resolve_method(method: str, type_apps=None, *, force_scancode: bool = False, info: winfocus.ForegroundInfo | None = None) -> str:
    """Turn the configured method (auto|paste|scancode|type) into a concrete one for this paste."""
    if force_scancode:
        return "scancode"
    m = (method or "auto").lower()
    if m != "auto":
        return m if m in METHODS else "paste"
    info = info if info is not None else winfocus.foreground()
    if info.fullscreen or winfocus.matches_app(info, type_apps if type_apps is not None else DEFAULT_TYPE_APPS):
        return "scancode"
    return "paste"


def _clip_get() -> str | None:
    try:
        return pyperclip.paste()
    except Exception as e:  # non-text clipboard content etc.
        log.debug("clipboard read failed: %s", e)
        return None


def _clip_set(text: str) -> bool:
    for _ in range(5):  # clipboard can be briefly locked by another app
        try:
            pyperclip.copy(text)
            return True
        except Exception as e:
            log.debug("clipboard set failed: %s", e)
            time.sleep(0.02)
    return False


def _press_enter() -> None:
    _kb.press(Key.enter)
    _kb.release(Key.enter)


def last_text() -> str:
    return _last_text


def inject(
    text: str,
    *,
    press_enter: bool = False,
    method: str = "paste",
    restore_ms: int = 150,
    trailing_space: bool = False,
    enter_delay_ms: int = 150,
    scancode_delay_ms: int = 12,
    scancode_newlines: bool = False,
    scancode_max_chars: int = DEFAULT_SCANCODE_MAX_CHARS,
) -> bool:
    """Insert `text` at the cursor of the focused app. Returns True if the text was sent.

    `method` must already be concrete (see `resolve_method`); "auto" is resolved here as a
    convenience. Paste: on success the previous clipboard is restored after `restore_ms`; on
    failure the dictated text stays on the clipboard so the user can paste it manually.
    """
    global _last_text
    if not text:
        return False
    if method == "auto":
        method = resolve_method("auto")
    if trailing_space and not text.endswith(("\n", " ")):
        text += " "
    _last_text = text

    with _lock:
        if not wait_modifiers_released():
            log.warning("modifiers still held after 600 ms; injecting anyway")

        if method == "scancode":
            t = text
            if not scancode_newlines:
                t = " ".join(t.replace("\r", "").split("\n"))
            t = t.rstrip("\r\n")  # never end with a newline: a chat box would send the message
            if scancode_max_chars and len(t) > scancode_max_chars:
                log.warning("scancode typing capped at %d of %d chars (inject.scancode_max_chars)", scancode_max_chars, len(t))
                t = t[:scancode_max_chars]
            ok = type_scancodes(t, delay_ms=scancode_delay_ms, newlines=scancode_newlines)
            if press_enter:  # only when the user literally said "press enter"
                time.sleep(max(enter_delay_ms, 20) / 1000)
                _tap_scan(SC_ENTER, vk=VK_RETURN)
            if not ok:
                log.warning("scancode typing reported SendInput failures")
            return True

        if method == "type":
            try:
                _kb.type(text)
                if press_enter:
                    _press_enter()
                return True
            except Exception as e:
                log.error("type() failed: %s; falling back to paste", e)

        prev = _clip_get()
        if not _clip_set(text):
            log.error("could not set clipboard")
            return False
        time.sleep(0.03)  # let the clipboard settle before the target app reads it
        try:
            _kb.press(Key.ctrl)
            _kb.press("v")
            _kb.release("v")
            _kb.release(Key.ctrl)
        except Exception as e:
            log.error("Ctrl+V failed: %s (text left on clipboard)", e)
            return False
        if press_enter:
            # the new Windows 11 Notepad (and Electron apps) apply the paste asynchronously;
            # give them time so the Enter lands after the text, not before it
            time.sleep(max(enter_delay_ms, 20) / 1000)
            _press_enter()

    if prev is not None:

        def _restore(expected: str = text, previous: str = prev) -> None:
            try:
                if _clip_get() == expected:  # only if nobody else changed it meanwhile
                    _clip_set(previous)
            except Exception as e:
                log.debug("clipboard restore failed: %s", e)

        threading.Timer(max(restore_ms, 50) / 1000, _restore).start()
    return True


def repaste_last(**kw) -> bool:
    if not _last_text:
        return False
    return inject(_last_text, **kw)
