"""Tray icon (pystray, main thread) + always-visible "Flow Dot" (tkinter, own thread).

Flow Dot: a 22x22 px dark circle with a ~10 px coloured status dot inside (no text), always on
top, draggable (position persisted in config.yaml), left-click toggles hands-free dictation,
right-click opens a menu mirroring the tray menu. State is colour only: grey idle, red
listening, pulsing orange-red hands-free, blinking blue processing, red ring = error.
It carries the WS_EX_NOACTIVATE extended style so clicking it never steals keyboard focus from
the text field the user is dictating into.

Both menus are built from one "menu spec" supplied by the app:
    ("label", text)                      disabled header
    ("sep",)
    ("cmd", text, callback)
    ("check", text, is_checked_fn, callback)
    ("radio", text, is_checked_fn, callback)
    ("menu", text, items_or_callable_returning_items)
"""
from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from typing import Callable

from PIL import Image, ImageDraw

from . import __version__

log = logging.getLogger("localflow.ui")

# state -> (accent colour, label used by the tray tooltip / legacy callers)
STATES = {
    "loading": ("#3b82f6", "load"),
    "idle": ("#6b7280", "Flow"),
    "listening": ("#ef4444", "REC"),
    "handsfree": ("#f97316", "hands-free"),
    "processing": ("#3b82f6", "…"),
    "done": ("#22c55e", "✓"),
    "error": ("#ef4444", "ERR"),
    "paused": ("#6b7280", "paused"),  # hollow grey ring: engines unloaded, GPU free
}
ACTIVE_STATES = ("listening", "handsfree", "processing")  # the only states that animate
HANDSFREE_PULSE = ("#f97316", "#fb923c", "#ef4444")  # orange -> bright orange -> red, cycled
BG = "#1f2937"
TRANSPARENT = "#010203"

WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20


def make_icon(state: str, size: int = 64) -> Image.Image:
    fg = STATES.get(state, STATES["idle"])[0]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if state == "paused":
        d.ellipse((4, 4, size - 4, size - 4), outline=fg, width=6)
    else:
        d.ellipse((4, 4, size - 4, size - 4), fill=fg)
    d.rounded_rectangle((size * 0.40, size * 0.20, size * 0.60, size * 0.55), radius=6, fill="white")
    d.arc((size * 0.30, size * 0.30, size * 0.70, size * 0.68), 0, 180, fill="white", width=4)
    d.line((size * 0.5, size * 0.68, size * 0.5, size * 0.80), fill="white", width=4)
    d.line((size * 0.38, size * 0.80, size * 0.62, size * 0.80), fill="white", width=4)
    return img


def _resolve(items):
    return list(items() if callable(items) else items)


# ---------------------------------------------------------------------- Flow Dot
class FlowBar:
    """The overlay (kept under its historical name). 22x22 px circle with a status dot."""

    W, H = 22, 22
    DOT = 10

    def __init__(
        self,
        ui_cfg: dict,
        *,
        on_click: Callable[[], None],
        on_moved: Callable[[int, int], None],
        menu_spec: Callable[[], list],
        enabled: bool = True,
    ):
        self.cfg = ui_cfg
        self.enabled = enabled
        self.on_click = on_click
        self.on_moved = on_moved
        self.menu_spec = menu_spec
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._root = None
        self._canvas = None
        self._state = "loading"
        self._level = 0.0
        self._drag = None
        self._hide_job = None
        self._blink = False
        self._tick = 0
        self._suppressed = False  # hidden because a fullscreen app is in front (independent of `enabled`)

    # ------------------------------------------------------------------ public (thread-safe)
    def start(self) -> None:
        if self._thread:
            self._q.put(("show", None))
            return
        self._thread = threading.Thread(target=self._run, name="flowbar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._q.put(("quit", None))

    def hide(self) -> None:
        self._q.put(("hide", None))

    def set_suppressed(self, suppressed: bool) -> None:
        """Withdraw while a fullscreen app is in front (hide_when_fullscreen); restore afterwards."""
        self._q.put(("suppress", bool(suppressed)))

    def set_state(self, state: str, text: str | None = None) -> None:
        self._q.put(("state", (state, text)))

    def set_level(self, level: float) -> None:
        self._level = level

    # ------------------------------------------------------------------ tk thread
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as e:  # pragma: no cover
            log.error("tkinter unavailable, Flow Bar disabled: %s", e)
            return
        self._tk = tk
        root = tk.Tk()
        self._root = root
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.96)
        try:
            root.attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass
        root.configure(bg=TRANSPARENT)
        self._canvas = tk.Canvas(root, width=self.W, height=self.H, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self._canvas.pack()
        self._canvas.bind("<ButtonPress-1>", self._press)
        self._canvas.bind("<B1-Motion>", self._motion)
        self._canvas.bind("<ButtonRelease-1>", self._release)
        self._canvas.bind("<ButtonPress-3>", self._popup)
        self._place_initial()
        self._draw()
        if self.enabled:
            root.deiconify()
        root.update()
        self._apply_noactivate()
        root.after(40, self._poll)
        root.mainloop()

    def _hwnd(self) -> int:
        try:
            return ctypes.windll.user32.GetParent(self._root.winfo_id()) or self._root.winfo_id()
        except Exception:
            return 0

    def _apply_noactivate(self) -> None:
        """Clicks on the pill must not move keyboard focus away from the user's text field."""
        try:
            hwnd = self._hwnd()
            u32 = ctypes.windll.user32
            style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
            # HWND_TOPMOST re-assert without activating
            u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOSIZE|NOMOVE|NOACTIVATE
        except Exception as e:
            log.debug("WS_EX_NOACTIVATE failed: %s", e)

    def _place_initial(self) -> None:
        root = self._root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = self.cfg.get("pill_x"), self.cfg.get("pill_y")
        if not isinstance(x, int) or not isinstance(y, int) or not (0 <= x < sw and 0 <= y < sh):
            x, y = (sw - self.W) // 2, sh - self.H - 72
        root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def _draw(self) -> None:
        c = self._canvas
        if c is None:
            return
        c.delete("all")
        accent = STATES.get(self._state, STATES["idle"])[0]
        w, h = self.W, self.H
        cx, cy = w / 2, h / 2
        # dark circular background
        c.create_oval(0, 0, w - 1, h - 1, fill=BG, outline=BG)
        # status dot; grows with mic level while listening, pulses in hands-free, blinks processing
        d = self.DOT
        col = accent
        if self._state == "listening":
            d += min(4, int(self._level * 40))
        elif self._state == "handsfree":
            phase = self._tick // 3  # redrawn every 3rd poll (~180 ms)
            col = HANDSFREE_PULSE[phase % len(HANDSFREE_PULSE)]
            d += (1 if phase % 2 else 0) + min(3, int(self._level * 30))
        elif self._state == "processing" and self._blink:
            col = "#1e3a8a"  # dim blue on the off-beat
        if self._state in ("error", "paused"):
            # ring around a dark centre: red = error, grey = paused (GPU freed)
            c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, outline=accent, width=2)
            return
        c.create_oval(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2, fill=col, outline=col)
        if self._state == "handsfree":
            c.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=col, width=1)

    def _poll(self) -> None:
        root = self._root
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "quit":
                    root.quit()
                    return
                if kind == "state":
                    self._apply_state(*payload)
                elif kind == "hide":
                    root.withdraw()
                elif kind == "show":
                    if not self._suppressed:
                        root.deiconify()
                        root.update()
                        self._apply_noactivate()
                elif kind == "suppress":
                    self._suppressed = payload
                    if payload:
                        root.withdraw()
                    elif self.enabled:
                        root.deiconify()
                        root.update()
                        self._apply_noactivate()
        except queue.Empty:
            pass
        active = self._state in ACTIVE_STATES
        if active and not self._suppressed:
            self._tick += 1
            self._blink = (self._tick // 4) % 2 == 1 if self._state == "processing" else False
            if self._state != "handsfree" or self._tick % 3 == 0:
                self._draw()
        # idle / paused / done / error: nothing animates, poll the queue at >= 250 ms and never redraw
        root.after(60 if active else 250, self._poll)

    def _apply_state(self, state: str, text: str | None) -> None:
        if self._hide_job:
            self._root.after_cancel(self._hide_job)
            self._hide_job = None
        self._state = state
        self._draw()
        if state in ("done", "error"):
            self._hide_job = self._root.after(1000 if state == "done" else 2500, lambda: self._apply_state("idle", None))

    # ------------------------------------------------------------------ mouse
    def _press(self, ev) -> None:
        self._drag = (ev.x_root, ev.y_root, self._root.winfo_x(), self._root.winfo_y(), False)

    def _motion(self, ev) -> None:
        if not self._drag:
            return
        sx, sy, wx, wy, moved = self._drag
        dx, dy = ev.x_root - sx, ev.y_root - sy
        if moved or abs(dx) > 3 or abs(dy) > 3:
            self._drag = (sx, sy, wx, wy, True)
            self._root.geometry(f"+{wx + dx}+{wy + dy}")

    def _release(self, ev) -> None:
        if not self._drag:
            return
        moved = self._drag[4]
        self._drag = None
        if moved:
            x, y = self._root.winfo_x(), self._root.winfo_y()
            try:
                self.on_moved(x, y)
            except Exception:
                log.exception("on_moved failed")
        else:
            try:
                self.on_click()
            except Exception:
                log.exception("on_click failed")

    def _popup(self, ev) -> None:
        tk = self._tk
        try:
            menu = self._build_menu(tk.Menu(self._root, tearoff=0), self.menu_spec())
            menu.tk_popup(ev.x_root, ev.y_root)
        except Exception:
            log.exception("popup menu failed")
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _build_menu(self, menu, items):
        tk = self._tk
        for it in items:
            kind = it[0]
            if kind == "sep":
                menu.add_separator()
            elif kind == "label":
                menu.add_command(label=it[1], state="disabled")
            elif kind == "cmd":
                menu.add_command(label=it[1], command=it[2])
            elif kind in ("check", "radio"):
                on = False
                try:
                    on = bool(it[2]())
                except Exception:
                    pass
                mark = ("● " if on else "○ ") if kind == "radio" else ("☑ " if on else "☐ ")
                menu.add_command(label=mark + it[1], command=it[3])
            elif kind == "menu":
                sub = tk.Menu(menu, tearoff=0)
                self._build_menu(sub, _resolve(it[2]))
                menu.add_cascade(label=it[1], menu=sub)
        return menu


# ---------------------------------------------------------------------- tray
class Tray:
    def __init__(self, menu_spec: Callable[[], list]):
        import pystray

        self._pystray = pystray
        self.menu_spec = menu_spec
        self._icons = {s: make_icon(s) for s in STATES}
        self._state = "loading"
        self.icon = pystray.Icon(
            "LocalFlow", self._icons["loading"], f"LocalFlow {__version__} — loading", menu=pystray.Menu(self._items)
        )

    def _items(self):
        yield from self._build(self.menu_spec())

    def _build(self, items):
        ps = self._pystray
        Item, Menu = ps.MenuItem, ps.Menu
        for it in items:
            kind = it[0]
            if kind == "sep":
                yield Menu.SEPARATOR
            elif kind == "label":
                yield Item(it[1], None, enabled=False)
            elif kind == "cmd":
                yield Item(it[1], (lambda cb=it[2]: (lambda icon, item: cb()))())
            elif kind in ("check", "radio"):
                yield Item(
                    it[1],
                    (lambda cb=it[3]: (lambda icon, item: cb()))(),
                    checked=(lambda fn=it[2]: (lambda item: bool(fn())))(),
                    radio=(kind == "radio"),
                )
            elif kind == "menu":
                yield Item(it[1], Menu((lambda sub=it[2]: (lambda: self._build(_resolve(sub))))()))

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        self._state = state
        try:
            self.icon.icon = self._icons.get(state, self._icons["idle"])
            self.icon.title = tooltip or f"LocalFlow {__version__} — {state}"
        except Exception as e:
            log.debug("tray update failed: %s", e)

    def notify(self, msg: str, title: str = "LocalFlow") -> None:
        try:
            self.icon.notify(msg, title)
        except Exception:
            pass

    def run(self, setup: Callable[[], None] | None = None) -> None:
        def _setup(icon):
            icon.visible = True
            if setup:
                threading.Thread(target=setup, name="setup", daemon=True).start()

        self.icon.run(setup=_setup)

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass


def beep(kind: str, enabled: bool = True) -> None:
    if not enabled:
        return

    def _do():
        try:
            import winsound

            if kind == "start":
                winsound.Beep(880, 50)
            elif kind == "stop":
                winsound.Beep(660, 50)
            elif kind == "handsfree":
                winsound.Beep(880, 50)
                time.sleep(0.02)
                winsound.Beep(1175, 70)
            elif kind == "handsfree_off":
                winsound.Beep(1175, 50)
                time.sleep(0.02)
                winsound.Beep(880, 70)
            elif kind == "error":
                winsound.Beep(300, 150)
            elif kind == "cancel":
                winsound.Beep(440, 80)
        except Exception:
            pass

    threading.Thread(target=_do, daemon=True).start()
