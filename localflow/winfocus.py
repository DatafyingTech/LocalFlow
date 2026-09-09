"""Foreground-window inspection (win32 via ctypes): title, process name, fullscreen detection.

`is_fullscreen(hwnd)` is true when the window is borderless (no WS_CAPTION) and its rect covers
the monitor it is on. The desktop / shell windows (Progman, WorkerW, explorer.exe) are ignored.
A maximised normal window keeps its caption, so it does not count; a game in exclusive or
borderless fullscreen, or a browser in F11 mode, does.

`FullscreenWatcher` polls once a second and calls back on changes; the app uses it to withdraw
the Flow Dot (a topmost overlay can knock a game out of independent-flip presentation), to pick
the keystroke injection method, and optionally to pause the GPU engines.

Anti-cheat note: everything here is a read-only user32 window query or a Toolhelp process-list
snapshot (CreateToolhelp32Snapshot). No handle to the foreground process is ever opened
(no OpenProcess), nothing is hooked, nothing is sent to the window. See inject.py.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("localflow.winfocus")

# private DLL instances: argtypes set here must not leak into pynput / pystray (shared ctypes.windll)
_u32 = ctypes.WinDLL("user32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None
_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
MONITOR_DEFAULTTONEAREST = 2
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_SHELL_CLASSES = {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"}
_SHELL_PROCS = {"explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe"}


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


if _u32 is not None:
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.MonitorFromWindow.restype = wintypes.HANDLE
    _u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    _u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.GetWindowLongW.restype = wintypes.LONG
    _u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

_proc_cache: dict[int, str] = {}  # pid -> exe name (refreshed when a pid is unknown)


@dataclass
class ForegroundInfo:
    hwnd: int = 0
    title: str = ""
    process: str = ""  # exe basename, e.g. "FiveM_b3095_GTAProcess.exe"
    pid: int = 0
    fullscreen: bool = False


def window_title(hwnd: int) -> str:
    if not _u32 or not hwnd:
        return ""
    try:
        n = _u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def window_class(hwnd: int) -> str:
    if not _u32 or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    if not _u32 or not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _snapshot_processes() -> dict[int, str]:
    """pid -> exe basename via a Toolhelp snapshot (no process handles are opened)."""
    out: dict[int, str] = {}
    if not _k32:
        return out
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out[int(pe.th32ProcessID)] = os.path.basename(pe.szExeFile)
            ok = _k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        _k32.CloseHandle(snap)
    return out


def process_name(pid: int) -> str:
    """Exe basename of a process ('' if unknown). Cached; the process list is re-read for new pids."""
    if not pid:
        return ""
    name = _proc_cache.get(pid)
    if name is None:
        _proc_cache.clear()
        _proc_cache.update(_snapshot_processes())
        name = _proc_cache.get(pid, "")
    return name


def is_fullscreen(hwnd: int, process: str | None = None) -> bool:
    """Borderless window whose rect covers its whole monitor (desktop/shell windows excluded)."""
    if not _u32 or not hwnd:
        return False
    try:
        if window_class(hwnd).lower() in _SHELL_CLASSES:
            return False
        proc = process if process is not None else process_name(window_pid(hwnd))
        if proc.lower() in _SHELL_PROCS:
            return False
        style = _u32.GetWindowLongW(hwnd, GWL_STYLE)
        if style & WS_CAPTION == WS_CAPTION:
            return False  # a normal (even maximised) window keeps its title bar
        rect = wintypes.RECT()
        if not _u32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        mon = _u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not mon or not _u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        if (m.right - m.left) <= 0 or (m.bottom - m.top) <= 0:
            return False
        return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    except Exception as e:
        log.debug("is_fullscreen failed: %s", e)
        return False


def foreground() -> ForegroundInfo:
    if not _u32:
        return ForegroundInfo()
    hwnd = _u32.GetForegroundWindow()
    if not hwnd:
        return ForegroundInfo()
    pid = window_pid(hwnd)
    proc = process_name(pid)
    return ForegroundInfo(hwnd=int(hwnd), title=window_title(hwnd), process=proc, pid=pid, fullscreen=is_fullscreen(hwnd, proc))


def matches_app(info: ForegroundInfo, patterns) -> bool:
    """True if any pattern is a case-insensitive substring of the title or a prefix of the exe name."""
    t, p = info.title.lower(), info.process.lower()
    for pat in patterns or []:
        s = str(pat).strip().lower()
        if not s:
            continue
        if s in t or p.startswith(s) or p == s:
            return True
    return False


class FullscreenWatcher:
    """Background poll (default 1 s) of the foreground window; `on_change(fullscreen, info)`."""

    def __init__(self, on_change: Callable[[bool, ForegroundInfo], None], interval_s: float = 1.0):
        self.on_change = on_change
        self.interval = max(0.25, float(interval_s))
        self.fullscreen = False
        self.info = ForegroundInfo()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="fullscreen-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                info = foreground()
            except Exception as e:  # never let the watcher die
                log.debug("foreground() failed: %s", e)
                continue
            self.info = info
            if info.fullscreen != self.fullscreen:
                self.fullscreen = info.fullscreen
                log.info("fullscreen %s (%s)", "on" if info.fullscreen else "off", info.process)
                # The window title can name a document, browser tab or game server, and users are
                # asked to paste localflow.log into public bug reports. Keep it at debug level.
                log.debug("fullscreen window title: %r", info.title[:40])
                try:
                    self.on_change(info.fullscreen, info)
                except Exception:
                    log.exception("fullscreen callback failed")


if __name__ == "__main__":  # quick manual check: python -m localflow.winfocus
    logging.basicConfig(level=logging.INFO)
    for _ in range(3):
        print(foreground())
        time.sleep(1)
