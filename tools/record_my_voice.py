r"""Record the three demo lines in your own voice, at the format the demo harness wants.

Run it, read each line out loud, press Enter. That's it.

    .\.venv\Scripts\python.exe tools\record_my_voice.py

Writes 16 kHz mono WAVs to docs/images/_voice/. tools/record_demo.py picks them up
automatically and uses them instead of synthesized speech.

Pass --device "part of your mic name" to pick a specific microphone, or --list to see them.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images" / "_voice"
RATE = 16000

# Say these naturally. The stumbles are the point: they are what the cleanup removes.
SCENES: list[tuple[str, str, str]] = [
    (
        "scene1",
        "um hey Sarah comma can you send me that report by Friday question mark",
        "Say the words 'comma' and 'question mark' out loud, exactly like that. "
        "The demo shows the 'um' vanishing and the spoken punctuation becoming real punctuation.",
    ),
    (
        "scene2",
        "send a report to Mark, no no wait, I meant to say send it to Sarah and CC me",
        "Correct yourself the way you actually would: a beat after 'Mark', then the correction. "
        "This is the scene people will find most impressive, so it is worth a second take.",
    ),
    (
        "scene3",
        "for the store I need potatoes, cream cheese, lasagna and spaghetti noodles",
        "Rattle the list off at normal speed. The demo shows it becoming a bulleted list.",
    ),
]


def record_one(device: int | str | None) -> np.ndarray:
    """Record until the user presses Enter again."""
    frames: list[np.ndarray] = []

    def cb(indata, _frames, _time, status):  # noqa: ANN001
        if status:
            print(f"  (audio status: {status})", file=sys.stderr)
        frames.append(indata.copy())

    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=device, callback=cb):
        input()
    if not frames:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(frames, axis=0).reshape(-1)


def save(path: Path, audio: np.ndarray) -> float:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = audio / peak * 0.89  # normalize so every clip sits at a consistent level
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return peak


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=None, help="substring of your microphone's name")
    ap.add_argument("--list", action="store_true", help="list input devices and exit")
    ap.add_argument("--only", default=None, help="re-record just one scene, e.g. scene2")
    args = ap.parse_args()

    devices = sd.query_devices()
    if args.list:
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                print(f"[{i}] {d['name']}")
        return 0

    device: int | str | None = None
    if args.device:
        matches = [i for i, d in enumerate(devices)
                   if d.get("max_input_channels", 0) > 0 and args.device.lower() in str(d["name"]).lower()]
        if not matches:
            print(f"No input device matching {args.device!r}. Run with --list to see them.")
            return 1
        device = matches[0]
        print(f"Using [{device}] {devices[device]['name']}")
    else:
        print("Using your default microphone. Pass --device \"name\" to choose another.")

    print("\nThree short lines. Press Enter to start each one, read it out, press Enter to stop.")
    print("Speak normally. Do not over-enunciate, and do not clean it up as you go.\n")

    todo = [s for s in SCENES if not args.only or s[0] == args.only]
    for name, line, note in todo:
        print("=" * 72)
        print(f"  {name}   say this:")
        print(f"\n    \"{line}\"\n")
        print(f"  {note}")
        print("=" * 72)
        while True:
            input("Press Enter to START recording... ")
            print("  RECORDING. Read the line, then press Enter to stop.")
            audio = record_one(device)
            secs = len(audio) / RATE
            peak = save(OUT / f"{name}.wav", audio)
            print(f"  saved {name}.wav  ({secs:.1f}s, peak {peak:.3f})")
            if secs < 0.8:
                print("  That was very short. Let's do it again.")
                continue
            if peak < 0.01:
                print("  That was nearly silent, so the mic may be wrong or muted. Let's do it again.")
                continue
            again = input("  Enter to keep it, or type 'r' to redo: ").strip().lower()
            if again != "r":
                break
        print()

    print(f"Done. Files are in {OUT}")
    print("Tell Claude they are ready and the demo will be rebuilt with your voice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
