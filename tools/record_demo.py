"""Record the LocalFlow demo clip (docs/images/demo.mp4 and demo.gif).

Everything on screen is produced by the real app code: the Flow Dot is `localflow.ui.FlowBar`,
the transcript comes from the real Parakeet ASR engine (`localflow.asr`), the text is cleaned by
the real `localflow.cleanup` rules plus the real Ollama pass (`localflow.llm`), and it lands in the
text box through the real `localflow.inject` keystroke path. The only synthetic part is the audio:
nobody can speak into the microphone during an automated recording, so each utterance is rendered
to a 16 kHz mono WAV with Windows SAPI TTS (the helper from tests/smoke_asr.py) and fed through the
same ASR path a live recording takes.

Privacy: only our own always-on-top stage window is ever captured, via
`ffmpeg -f gdigrab -i title=<STAGE_TITLE>`. The desktop is never recorded.

Safety: real injection sends real keystrokes to whatever window is in the foreground, so every
scene checks `GetForegroundWindow()` first and skips the injection rather than typing into
somebody else's app.

Usage (project root, venv active):
    python -m tools.record_demo             # -> docs/images/demo.mp4 + demo.gif
    python -m tools.record_demo --still     # one PNG frame of the stage, for a privacy check
    python -m tools.record_demo --no-gif
"""
from __future__ import annotations

import argparse
import ctypes
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from localflow import cleanup, inject, llm, ui  # noqa: E402
from localflow import config as cfgmod  # noqa: E402
from localflow.asr import create_engine  # noqa: E402
from tests.smoke_asr import make_tts_wav, read_wav  # noqa: E402

STAGE_TITLE = "LocalFlow Demo Stage"
W, H = 900, 520
STAGE_X, STAGE_Y = 360, 150

BG = "#0f172a"
PANEL = "#111c31"
EDGE = "#1e293b"
FG = "#e2e8f0"
MUTED = "#94a3b8"
ACCENT = "#38bdf8"
GREEN = "#22c55e"

OUT = ROOT / "docs" / "images"
TMP = OUT / "_tmp"
VOICE = OUT / "_voice"   # real human recordings; preferred over TTS

SCENES = [
    dict(say="um hey Sarah comma can you send me that report by Friday question mark",
         note="fillers out, spoken punctuation in"),
    dict(say="send a report to Mark, no no wait, I meant to say send it to Sarah and CC me",
         note="say “no wait” and it fixes it"),
    dict(say="for the store I need potatoes, cream cheese, lasagna and spaghetti noodles",
         note="a spoken list becomes a real list"),
]

user32 = ctypes.windll.user32
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD, WS_POPUP = 0x40000000, 0x80000000
WS_EX_LAYERED = 0x00080000

# The Flow Dot normally punches its square corners out with a transparent colour key. A layered
# window is composited by the DWM and would not appear in a gdigrab window capture, so for the
# recording we hand FlowBar the stage background as its "transparent" colour and un-layer it
# after docking: it then paints its corners in the stage colour instead. FlowBar itself is
# unmodified - this only changes the constant it reads.
ui.TRANSPARENT = BG


def find_flow_dot_hwnd() -> int:
    """The Flow Dot has no title; find our own 22x22 top-level window."""
    found = []
    pid = wintypes.DWORD()
    me = ctypes.windll.kernel32.GetCurrentProcessId()
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != me or not user32.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.right - r.left) == ui.FlowBar.W and (r.bottom - r.top) == ui.FlowBar.H:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else 0


def dock_flow_dot(dot_hwnd: int, stage_hwnd: int, x: int, y: int) -> bool:
    """Make the real Flow Dot a child of the stage window.

    gdigrab captures a window's own surface, so an overlapping always-on-top overlay would not
    show up in the recording. Re-parenting the (unmodified) FlowBar window into the stage puts it
    on the stage's surface, where it is captured - and it keeps drawing itself with its own code.
    """
    if not dot_hwnd or not stage_hwnd:
        return False
    style = user32.GetWindowLongW(dot_hwnd, GWL_STYLE) & 0xFFFFFFFF
    new = ctypes.c_int(((style & ~WS_POPUP) | WS_CHILD) - (1 << 32)
                       if ((style & ~WS_POPUP) | WS_CHILD) >= (1 << 31)
                       else ((style & ~WS_POPUP) | WS_CHILD))
    user32.SetWindowLongW(dot_hwnd, GWL_STYLE, new)
    if not user32.SetParent(dot_hwnd, stage_hwnd):
        return False
    ex = user32.GetWindowLongW(dot_hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    ex &= ~WS_EX_LAYERED
    user32.SetWindowLongW(dot_hwnd, GWL_EXSTYLE,
                          ctypes.c_int(ex - (1 << 32) if ex >= (1 << 31) else ex))
    user32.SetWindowPos(dot_hwnd, 0, x, y, ui.FlowBar.W, ui.FlowBar.H,
                        SWP_NOACTIVATE | SWP_SHOWWINDOW)
    user32.RedrawWindow(dot_hwnd, None, None, 0x0001 | 0x0004 | 0x0080)  # INVALIDATE|ERASE|FRAME
    return True


# ---------------------------------------------------------------- the stage window
class Stage:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.title(STAGE_TITLE)
        self.root.configure(bg=BG)
        self.root.geometry(f"{W}x{H}+{STAGE_X}+{STAGE_Y}")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        pad = 26
        head = tk.Frame(self.root, bg=BG)
        head.place(x=pad, y=18, width=W - 2 * pad - 60, height=30)
        tk.Label(head, text="LocalFlow", bg=BG, fg=FG, font=("Segoe UI Semibold", 15)).pack(side="left")
        tk.Label(head, text="hold Ctrl+Win, talk, let go", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left", padx=(12, 0))
        # the real Flow Dot (localflow.ui.FlowBar) is docked into the gap at the right ->
        tk.Label(self.root, text="the real Flow Dot →", bg=BG, fg="#64748b",
                 font=("Segoe UI", 10), anchor="e").place(x=W - 230, y=25, width=166, height=18)

        self.caption = tk.Label(self.root, text="", bg=BG, fg=ACCENT, font=("Consolas", 14),
                                anchor="nw", justify="left", wraplength=W - 2 * pad - 40)
        self.caption.place(x=pad, y=58, width=W - 2 * pad - 40, height=58)

        self.note = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 11, "italic"),
                             anchor="w")
        self.note.place(x=pad, y=120, width=W - 2 * pad, height=20)

        tk.Label(self.root, text="your text field", bg=BG, fg="#64748b",
                 font=("Segoe UI", 9)).place(x=pad + 2, y=148)
        box = tk.Frame(self.root, bg=EDGE)
        box.place(x=pad, y=170, width=W - 2 * pad, height=254)
        self.text = tk.Text(box, bg=PANEL, fg=FG, insertbackground=ACCENT, font=("Segoe UI", 16),
                            relief="flat", bd=0, wrap="word", padx=16, pady=14,
                            selectbackground="#1d4ed8", spacing1=2, spacing3=4)
        self.text.place(x=2, y=2, width=W - 2 * pad - 4, height=250)

        self.timing = tk.Label(self.root, text="", bg=BG, fg=GREEN, font=("Consolas", 12), anchor="w")
        self.timing.place(x=pad, y=438, width=W - 2 * pad, height=24)
        tk.Label(self.root, text="100% local  \u00b7  Parakeet on CUDA + gemma3:4b via Ollama  \u00b7  MIT",
                 bg=BG, fg="#64748b", font=("Segoe UI", 10), anchor="w"
                 ).place(x=pad, y=468, width=W - 2 * pad, height=22)

        self.card = None
        self.root.update()
        self.hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        self.root.after(20, self._poll)

    # -- called from the worker thread ---------------------------------------
    def do(self, fn, *a):
        self.q.put((fn, a))

    def _poll(self):
        try:
            while True:
                fn, a = self.q.get_nowait()
                try:
                    fn(*a)
                except Exception as e:
                    print("stage cmd failed:", e)
        except queue.Empty:
            pass
        self.root.after(20, self._poll)

    # -- widget ops (main thread only) ---------------------------------------
    def set_caption(self, s):
        self.caption.config(text=s)

    def set_note(self, s):
        self.note.config(text=s)

    def set_timing(self, s):
        self.timing.config(text=s)

    def clear_text(self):
        self.text.delete("1.0", "end")

    def focus_text(self):
        self.text.focus_set()

    def title_card(self):
        self.card = tk.Frame(self.root, bg=BG)
        self.card.place(x=0, y=0, width=W, height=H)
        tk.Label(self.card, text="LocalFlow", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 46)).pack(pady=(160, 8))
        tk.Label(self.card, text="github.com/DatafyingTech/LocalFlow", bg=BG, fg=ACCENT,
                 font=("Consolas", 19)).pack(pady=6)
        tk.Label(self.card, text="free  \u00b7  local  \u00b7  MIT", bg=BG, fg=MUTED,
                 font=("Segoe UI", 16)).pack(pady=12)


def raise_stage(hwnd: int, dot_hwnd: int = 0) -> bool:
    """Foreground the stage and re-assert the Flow Dot above it.

    Returns True only if the stage really is the foreground window. Callers must not send any
    keystrokes otherwise: they would land in whatever app the user is actually using.
    """
    for _ in range(12):
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
        if user32.GetForegroundWindow() == hwnd:
            if dot_hwnd:
                user32.SetWindowPos(dot_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
            return True
    return False


# ---------------------------------------------------------------- ffmpeg
_FFMPEG = None


def ffmpeg_bin() -> str:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    for c in (r"C:\ffmpeg\bin\ffmpeg.exe", "ffmpeg"):
        try:
            subprocess.run([c, "-version"], capture_output=True, check=True)
            _FFMPEG = c
            return c
        except Exception:
            continue
    raise RuntimeError("ffmpeg not found (expected C:\\ffmpeg\\bin or on PATH)")


def grab_still(path: Path) -> None:
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "gdigrab", "-framerate", "5", "-i", f"title={STAGE_TITLE}",
                    "-frames:v", "1", str(path)], check=True)


def start_recorder(path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "gdigrab", "-framerate", "30", "-draw_mouse", "0",
         "-i", f"title={STAGE_TITLE}",
         "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-pix_fmt", "yuv420p", "-r", "30", "-an", str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_recorder(p: subprocess.Popen) -> None:
    try:
        p.stdin.write(b"q")
        p.stdin.flush()
        p.stdin.close()
    except Exception:
        pass
    try:
        p.wait(timeout=20)
    except Exception:
        p.terminate()


def make_gif(mp4: Path, gif: Path, fps: int = 14, width: int = 720) -> None:
    pal = TMP / "palette.png"
    vf = f"fps={fps},scale={width}:-2:flags=lanczos"
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4),
                    "-vf", vf + ",palettegen=max_colors=128:stats_mode=diff", str(pal)], check=True)
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(mp4), "-i", str(pal),
                    "-lavfi", vf + "[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3"
                                   ":diff_mode=rectangle",
                    "-loop", "0", str(gif)], check=True)


# ---------------------------------------------------------------- the run
def worker(stage, dot, dot_hwnd, engine, ollama, cfg, args, done):
    ccfg = cfg["cleanup"]
    level = ccfg.get("level", "medium")
    use_llm_cfg = ollama is not None and level != "none"
    rec = None
    try:
        raise_stage(stage.hwnd, dot_hwnd)
        time.sleep(0.5)
        if not args.still:
            rec = start_recorder(args.mp4)
            time.sleep(1.3)

        for i, sc in enumerate(SCENES):
            audio = read_wav(TMP / f"scene{i + 1}.wav")

            stage.do(stage.set_caption, "")
            stage.do(stage.set_note, "")
            stage.do(stage.set_timing, "")
            stage.do(stage.clear_text)
            dot.set_state("idle")
            time.sleep(0.5)

            prefix = '\U0001f3a4  "'
            for n in range(len(sc["say"]) + 1):
                stage.do(stage.set_caption, prefix + sc["say"][:n] + '"')
                time.sleep(0.024)
            stage.do(stage.set_note, sc["note"])
            time.sleep(0.2)

            # ---- listening (real Flow Dot, red, reacting to a level)
            dot.set_state("listening")
            for k in range(16):
                dot.set_level(0.15 + 0.15 * ((k % 5) / 5.0))
                time.sleep(0.06)
            time.sleep(0.3)

            # ---- processing: real ASR -> real rules -> real Ollama
            dot.set_state("processing")
            t = time.perf_counter()
            raw = engine.transcribe(audio)
            ms_asr = (time.perf_counter() - t) * 1000

            t = time.perf_counter()
            res = cleanup.clean(raw, ccfg, defer_lists=use_llm_cfg, defer_backtrack=use_llm_cfg)
            ms_rules = (time.perf_counter() - t) * 1000
            text, ms_llm, used = res.text, 0.0, False
            if text and use_llm_cfg and not res.snippet_fired:
                out = ollama.cleanup(text, level)
                ms_llm, used = out.ms, out.used
                if used:
                    text = cleanup.post_llm(out.text, ccfg)
                else:
                    print(f"  LLM not used: {out.reason}")
            if use_llm_cfg and not used and text:
                t2 = time.perf_counter()
                text, _ = cleanup.apply_backtrack(text, ccfg)
                ms_rules += (time.perf_counter() - t2) * 1000
            if use_llm_cfg and text and not res.snippet_fired and ccfg.get("auto_lists", True):
                text, _ = cleanup.auto_list(text, ccfg.get("list_leadins"))
            total = ms_asr + ms_rules + ms_llm

            print(f"\nscene {i + 1}")
            print(f"  RAW : {raw!r}")
            print(f"  OUT : {text!r}")
            print(f"  asr {ms_asr:.0f} ms | rules {ms_rules:.1f} ms | llm {ms_llm:.0f} ms "
                  f"(used={used}) | total {total:.0f} ms")

            # ---- inject through the real keystroke path
            dot.set_state("done")
            stage.do(stage.focus_text)
            time.sleep(0.1)
            if not raise_stage(stage.hwnd, dot_hwnd):
                print("  !! stage never reached the foreground - injection skipped on purpose")
            else:
                inject.inject(text, method="scancode", scancode_delay_ms=14,
                              scancode_newlines=True, scancode_max_chars=800)
            stage.do(stage.set_timing,
                     f"ASR {ms_asr:.0f} ms  \u00b7  rules {ms_rules:.1f} ms  \u00b7  "
                     f"LLM {ms_llm:.0f} ms  \u00b7  total {total:.0f} ms")
            dot.set_state("idle")
            time.sleep(2.3)

            if args.still and i == 0:
                break

        if args.still:
            time.sleep(0.4)
            grab_still(args.still_path)
        else:
            stage.do(stage.title_card)
            time.sleep(2.3)
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        if rec is not None:
            stop_recorder(rec)
        done.set()
        stage.do(stage.root.quit)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--still", action="store_true", help="grab one PNG frame instead of recording")
    ap.add_argument("--no-gif", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    args.mp4 = OUT / "demo.mp4"
    args.still_path = TMP / "still.png"

    cfg = cfgmod.load()

    # A real human recording (docs/images/_voice/sceneN.wav, made with
    # tools/record_my_voice.py) always wins over synthesized speech: the demo is more
    # honest, and the ASR has to cope with a real voice. Fall back to SAPI TTS so the
    # harness still runs on a machine with no recordings.
    for i, sc in enumerate(SCENES):
        wav = TMP / f"scene{i + 1}.wav"
        voiced = VOICE / f"scene{i + 1}.wav"
        if voiced.exists():
            wav.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(voiced, wav)
            print(f"scene {i + 1}: using real voice recording")
        elif not wav.exists():
            print(f"scene {i + 1}: no recording found, synthesizing with Windows SAPI")
            make_tts_wav(wav, sc["say"])

    print("loading the real ASR engine ...")
    engine = create_engine(cfg)
    engine.load()
    print(f"  {engine.name}  GPU={engine.on_gpu()}  warmup={engine.warmup():.0f} ms")

    ollama = llm.OllamaClient(
        cfg["llm"],
        backtrack_phrases=list(cfg["cleanup"].get("backtrack_strong") or [])
        + list(cfg["cleanup"].get("backtrack_weak") or []),
    )
    if ollama.available() and ollama.has_model():
        print(f"  Ollama {ollama.model} warm in "
              f"{ollama.warmup(level=cfg['cleanup']['level']):.0f} ms")
    else:
        print("  Ollama unavailable - rules-only cleanup")
        ollama = None

    stage = Stage()
    # park the real Flow Dot inside the stage's *client* area (what gdigrab captures)
    cx, cy = stage.root.winfo_rootx(), stage.root.winfo_rooty()
    print(f"  stage client origin = {cx},{cy}")
    dot = ui.FlowBar({"pill_x": cx + W - 54, "pill_y": cy + 22},
                     on_click=lambda: None, on_moved=lambda x, y: None, menu_spec=lambda: [])
    dot.start()
    time.sleep(1.5)
    dot_hwnd = find_flow_dot_hwnd()
    docked = dock_flow_dot(dot_hwnd, stage.hwnd, W - 54, 20)
    print(f"  Flow Dot hwnd = {dot_hwnd}  docked={docked}")
    dot.set_state("idle")

    done = threading.Event()
    threading.Thread(target=worker,
                     args=(stage, dot, dot_hwnd, engine, ollama, cfg, args, done),
                     name="demo", daemon=True).start()
    stage.root.mainloop()
    done.wait(10)
    dot.stop()

    if not args.still and not args.no_gif:
        print("building gif ...")
        make_gif(args.mp4, OUT / "demo.gif")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
