r"""Render the documentation screenshots without ever capturing the desktop.

    .\.venv\Scripts\python.exe tools\render_ui_shots.py

It starts the real Flow Dot widget from localflow/ui.py, walks it through every state, and
captures *only that window* with PrintWindow(PW_RENDERFULLCONTENT), which asks Windows for the
window's own pixels instead of reading the screen. Nothing behind or around the dot is ever
sampled, so running this on a machine somebody is using leaks nothing.

Output (committed, well under 300 KB):
    docs/images/flow-dot-states.png   one labelled strip, all seven states

The tray / right-click menu is not captured - see the note further down for why.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from localflow.ui import FlowBar  # noqa: E402

OUT_DIR = ROOT / "docs" / "images"

# states in the order the README explains them
ORDER = ["idle", "listening", "handsfree", "processing", "done", "error", "paused"]
CAPTIONS = {
    "idle": "idle\nready",
    "listening": "listening\npush-to-talk",
    "handsfree": "hands-free\ncontinuous",
    "processing": "processing\nthinking",
    "done": "done\njust pasted",
    "error": "error\ncheck the log",
    "paused": "paused\nGPU freed",
}

SCALE = 4  # 22 px dot -> 88 px tile
PAD = 18
LABEL_H = 40
SHEET_BG = (243, 244, 246)
TILE_BG = (255, 255, 255)
TEXT = (31, 41, 55)
SUBTEXT = (107, 114, 128)

PW_RENDERFULLCONTENT = 0x00000002


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ("_pad", wintypes.DWORD * 3),
    ]


def capture_window(hwnd: int) -> Image.Image | None:
    """Return the window's own pixels as RGBA, or None. Never reads the screen."""
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wintypes.RECT()
    if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    old = g32.SelectObject(mdc, bmp)
    try:
        if not u32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
            return None
        bi = _BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER) - ctypes.sizeof(wintypes.DWORD) * 3
        bi.biWidth, bi.biHeight = w, -h  # negative height = top-down rows
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if not g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGBA")
    finally:
        g32.SelectObject(mdc, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mdc)
        u32.ReleaseDC(hwnd, hdc)


def drop_chroma(img: Image.Image) -> Image.Image:
    """The dot window is layered with -transparentcolor #010203; those pixels (and the
    untouched corners) come back near-black. Make them transparent so the tile shows through."""
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, _ = px[x, y]
            if r <= 6 and g <= 6 and b <= 6:
                px[x, y] = (0, 0, 0, 0)
    return img


def _font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_states() -> Path:
    bar = FlowBar(
        {"pill_x": 60, "pill_y": 60},           # fixed spot; never read back from the real config
        on_click=lambda: None,
        on_moved=lambda x, y: None,
        menu_spec=lambda: [],
        enabled=True,
    )
    bar.start()
    time.sleep(1.5)                              # let the tk thread build and show the window
    hwnd = bar._hwnd()
    if not hwnd:
        raise RuntimeError("the Flow Dot window never appeared")

    shots: dict[str, Image.Image] = {}
    try:
        for state in ORDER:
            bar.set_state(state)
            time.sleep(0.45)                     # the widget redraws on its own 60 ms poll
            # listening / hands-free / processing animate, and half the blink frames are dim.
            # Take a few frames and keep the brightest so the strip shows each state's colour.
            best, best_lum = None, -1.0
            for _ in range(6):
                img = capture_window(hwnd)
                if img is not None:
                    lum = sum(img.convert("L").tobytes()) / (img.width * img.height)
                    if lum > best_lum:
                        best, best_lum = img, lum
                time.sleep(0.08)
            if best is None:
                raise RuntimeError(f"could not capture the {state} state")
            shots[state] = drop_chroma(best)
    finally:
        bar.stop()

    tile = 22 * SCALE
    cell_w = tile + PAD * 2
    sheet = Image.new("RGB", (cell_w * len(ORDER), tile + PAD * 2 + LABEL_H), SHEET_BG)
    d = ImageDraw.Draw(sheet)
    f_name, f_note = _font(15, bold=True), _font(13)

    for i, state in enumerate(ORDER):
        x0 = i * cell_w
        d.rectangle((x0 + 4, 4, x0 + cell_w - 5, tile + PAD * 2 - 5), fill=TILE_BG)
        big = shots[state].resize((tile, tile), Image.LANCZOS)
        sheet.paste(big, (x0 + PAD, PAD), big)
        name, note = CAPTIONS[state].split("\n")
        y = tile + PAD * 2
        d.text((x0 + cell_w / 2, y), name, font=f_name, fill=TEXT, anchor="ma")
        d.text((x0 + cell_w / 2, y + 18), note, font=f_note, fill=SUBTEXT, anchor="ma")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "flow-dot-states.png"
    sheet.convert("P", palette=Image.ADAPTIVE, colors=64).save(out, optimize=True)
    return out


# ---------------------------------------------------------------- tray / right-click menu
# Deliberately not rendered.
#
# Both menus (tray and right-click) are native Win32 popup menus: Tk hands them to Windows,
# which runs its own modal message loop until a human dismisses them. Neither Menu.post() nor
# Menu.tk_popup() returns while the menu is up, so a capture pass can only be driven from a
# second thread - and if anything goes wrong there, the menu is left stuck on top of whatever
# the person at the keyboard is doing. Tried both; both hung. Not worth a screenshot.
#
# The menu items are documented in the README table instead, and the Flow Dot strip below is
# rendered from the real widget, which is the part people actually need to recognise.


def main() -> int:
    states = render_states()
    print(f"wrote {states.relative_to(ROOT)}  ({states.stat().st_size / 1024:.0f} KB)")
    print("tray-menu.png is not rendered: native Win32 popup menus are modal (see the note above)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
