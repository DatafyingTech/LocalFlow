<div align="center">

# LocalFlow

**Hold a key. Talk. Let go. Your words appear — cleaned up — in whatever you're typing into.**

A free, private, GPU-accelerated voice dictation app for Windows.
Everything runs on your own machine. No account, no subscription, no cloud, no word limits.

**Want it on your PC?** [Install in three steps](#install). The phone part is optional and lives
at the [bottom](#from-your-phone).

[![Tests](https://github.com/DatafyingTech/LocalFlow/actions/workflows/tests.yml/badge.svg)](https://github.com/DatafyingTech/LocalFlow/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11–3.13](https://img.shields.io/badge/Python-3.11–3.13-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](#requirements)

</div>

---

## What it does

![LocalFlow in action](docs/images/demo.gif)

*Nothing here is staged. A real person speaking into a real microphone, transcribed by Parakeet on
the GPU, cleaned up by a local gemma3:4b, and typed into the text field by the real injection code.
The millisecond figures on screen are the actual measured times from that take. Reproduce it with
`python -m tools.record_demo`.*

You hold **Ctrl+Win**, say *"send a report to Mark, no no wait, I meant to say send it to Sarah and
CC me"*, and let go. Under half a second later, this lands in your email, chat box, or code editor:

> Send a report to Sarah and CC me.

That is the second scene in the recording above, and it took 394 ms end to end.

The filler words are gone. The punctuation and capitals are there. It understood that you corrected
yourself and kept only what you meant. It never left your computer.

It works in **any** app that accepts typed text — browser, Slack, Word, VS Code, even fullscreen games.

### Why not just use a cloud dictation app?

Commercial dictation apps like Wispr Flow are excellent, and this project is openly inspired by one.
But they send your microphone audio to someone else's servers, cost around $12–15/month, and cap
what you can dictate. LocalFlow is a like-for-like replacement that runs entirely on your GPU.

|  | LocalFlow | Typical cloud dictation app |
|---|---|---|
| **Cost** | Free forever | ~$12–15/month |
| **Word limit** | None | Often ~2,000 words/week on free tiers |
| **Your audio** | Never leaves the machine | Uploaded to a vendor's servers |
| **Works offline** | Yes, completely | No |
| **Speed** | ~0.25–0.5 s (your GPU) | ~1–2 s (network round trip) |
| **In fullscreen games** | Yes, types like a real keyboard | Usually not |
| **Setup** | One script, 10-30 minutes | Download and sign in |
| **Needs** | An NVIDIA GPU for full speed | Nothing but internet |

---

## Highlights

- **Push-to-talk or hands-free.** Hold the key for one phrase, or double-tap it and talk for as
  long as you like. Hands-free records the whole speech and delivers one cleaned result when you
  stop, so nothing is cut off mid-sentence.
- **Fixes what you say as you say it.** Say *"no wait"*, *"scratch that"*, *"I meant"*, or *"oops"*
  and the correction is applied instead of transcribed.
- **Understands lists.** *"For the store I need potatoes, cream cheese, lasagna and spaghetti"*
  comes out as a lead-in line with a proper bullet list under it.
- **Whisper-quiet input works.** Every clip is volume-normalized before transcription, so you can
  dictate at a near-silent murmur in a shared room.
- **Types into fullscreen games.** Games ignore normal paste; LocalFlow switches to real keystrokes.
  It never presses Enter for you unless you literally say *"press enter"*.
- **Stays out of your GPU's way.** Caps its own video memory, unloads the language model when idle,
  and has a one-click **Pause (free GPU)** for gaming sessions. **Low VRAM mode** in the same menu
  swaps in a compact speech model that uses about 0.6 GB instead of 3.0 GB — in our tests it
  produced word-for-word identical transcripts and cost about a third of a second per dictation.
- **A dot, not a window.** A 22-pixel status dot you can drag anywhere. It never steals focus from
  whatever you're typing into.
- **Your words stay yours.** A local history file you own, and nothing else. No telemetry at all.
- **Works from your phone (optional).** An Android app puts the same dot on your phone screen. It
  sends your voice to your own PC over Tailscale and types the result into whatever app you are in.
  Your desktop does the work; nothing goes to anyone else's server.

---

## Requirements

|  | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10 64-bit, version 1809 | Windows 11 |
| **CPU** | Any 64-bit x86 CPU | 6 cores or more |
| **RAM** | 8 GB | 16 GB |
| **GPU** | None. It runs on CPU, slowly | NVIDIA with **6 GB VRAM**, driver 525+ |
| **Disk** | 10 GB free, split across two drives | SSD |
| **Python** | 3.11 | 3.12 |
| **Internet** | Only to install | Not needed afterwards |

**How much VRAM you actually need.** Measured on an RTX 4070 SUPER:

| What | Full precision (default) | Low VRAM mode |
|---|---|---|
| Speech model | **about 3.0 GB**, 74 ms per dictation | **about 0.6 GB**, 429 ms per dictation |
| Cleanup model (gemma3:4b, optional) | about 3.8 GB | about 3.8 GB |

Low VRAM mode is the same speech model built with int8 weights. On four real recordings the
transcripts were word-for-word identical to full precision; it simply costs about a third of a
second per dictation. Turn it on any time: right-click the dot → **Low VRAM mode (frees 2.4 GB,
slower)**. The installer turns it on for you if your card has less than 6 GB.

| Your GPU | What to expect |
|---|---|
| **8 GB or more** | Everything on, nothing to think about. A GTX 1660, RTX 2060, 3060, 4060 or better. |
| **6 GB** | Works well as shipped. Turn on **Low VRAM mode** if you also want to game while LocalFlow is loaded. |
| **4 GB** | Turn on **Low VRAM mode** (the installer does it for you). That leaves room for the cleanup model; if it is still tight, use a smaller one (`ollama pull gemma3:1b`, then set `llm.model: gemma3:1b`) or set `cleanup.level: none`. |
| **No NVIDIA GPU** | Run `install.bat -CPU`. Expect a few seconds per utterance instead of a fraction of a second. |

**About `llm.num_ctx`.** It is the context window given to the cleanup model, and it ships at
**4096**. The largest request LocalFlow can ever send is one full segment at level `high`: 835
prompt tokens plus at most 1,040 tokens of output, about 1,875 in total — so 4096 leaves more than
twice the headroom that is ever used. Dropping it from the old 8192 takes gemma3:4b from 4,060 MB
down to 3,832 MB of VRAM (about 230 MB back) with no change in speed. Long hands-free speeches are
unaffected, because they are cleaned in 400-word segments rather than in one giant request. The
only reason to raise `num_ctx` is if you raise `llm.segment_words`; LocalFlow logs a warning at
startup if you set the two so that a segment can no longer fit.

**Where the 10 GB goes.** About 5.3 GB sits next to LocalFlow: 2.4 GB for the speech model and
2.9 GB for the Python environment. About 3.3 GB more goes to `%USERPROFILE%\.ollama` for the
cleanup model, which is usually on your C: drive. The installer checks both and tells you which one
is short.

You do **not** need the CUDA toolkit or Visual Studio. The installer pulls everything through pip.
Do not install PyTorch into this environment; it brings an incompatible cuDNN.

**What is optional.** [Ollama](https://ollama.com) powers the smart cleanup that removes filler
words, applies your self-corrections and formats lists. Without it, LocalFlow still transcribes and
still applies its built-in rules, just less cleverly. AMD and Intel GPUs are not accelerated yet.

**A microphone**, obviously. Anything works, including a laptop's built-in one. Audio is normalized
before transcription, so a cheap mic and a quiet voice are both fine.

### Linux and macOS

**Not supported today, and it is not a small job.** Roughly two thirds of the code is portable:
speech recognition, audio capture, the cleanup rules, the Ollama pass, config and history have no
Windows dependency at all. The parts that do not port are the ones that make it feel instant:

| Piece | Portable? |
|---|---|
| Parakeet / Whisper recognition, audio capture, cleanup, Ollama, config, history | Yes, as-is |
| Global hotkey | Mostly. `pynput` works on X11 and macOS, but Wayland blocks global key grabs |
| Typing into the focused window | No. Rewrites needed for X11 (XTest), Wayland (portals, heavily restricted) or macOS (CGEvent, needs Accessibility permission) |
| Focus and fullscreen detection | No. Win32-only today |
| The status dot staying unfocused | No. Relies on a Win32 window style |

Wayland is the real obstacle: it deliberately prevents applications from reading global keys or
injecting synthetic input into other windows, which is the entire mechanism LocalFlow depends on.
A Linux port is realistic on X11 and macOS, and awkward on Wayland. Contributions welcome, see
[CONTRIBUTING.md](CONTRIBUTING.md).

---

## Install

**1. Install the two prerequisites** (skip either if you already have it):

- [Python 3.12](https://www.python.org/downloads/release/python-3129/) — on that page, scroll to the
  bottom and pick **Windows installer (64-bit)**. **Do not use the big yellow Download button on
  python.org, it gives a newer version LocalFlow cannot use yet.** In the installer, **tick "Add
  python.exe to PATH"** before you click Install. The **Microsoft Store** version of Python 3.12
  works too and has no PATH checkbox to remember. No Python at all? Run `install.bat` anyway — it
  offers to install 3.12 for you with Windows' own package manager.
- [Ollama](https://ollama.com/download) — optional, but it makes the cleanup much better.

**2. Download LocalFlow** — [grab the ZIP](https://github.com/DatafyingTech/LocalFlow/archive/refs/heads/main.zip),
right-click it, choose **Extract All**. That creates a folder named `LocalFlow-main`; open it (there
is a second `LocalFlow-main` inside), and that inner folder is LocalFlow. Or, with git:

```powershell
git clone https://github.com/DatafyingTech/LocalFlow.git
cd LocalFlow
```

**3. Double-click `install.bat`** in that folder and wait. Run it from inside the extracted folder,
not from inside the ZIP. If you only saved `install.bat` on its own, it fetches the rest of the
project for you and carries on.

Windows will probably show **"Windows protected your PC"** the first time: that is SmartScreen
reacting to any script that came from the internet, not to LocalFlow. Click **More info**, then
**Run anyway**. You can avoid it entirely by right-clicking the ZIP *before* extracting, choosing
**Properties**, ticking **Unblock** at the bottom, clicking OK, and extracting after that.

It checks your system, creates an isolated Python environment, writes your `config.yaml`,
downloads the speech model (~2.5 GB, one time), pulls the cleanup model, and runs a self-test.
That is about **8 GB of downloads** and **10 to 30 minutes** on a normal connection. It tells you
plainly if something is missing and what to do about it.

That 10 GB is not all in one place. About 5.3 GB goes next to LocalFlow (`.venv` plus `models`) and
about 3.3 GB goes to `%USERPROFILE%\.ollama`, which is usually on C:. The installer checks both drives
and names the one that is short.

**4. Double-click `run.bat`.**

A blue dot appears near the bottom of your screen while it loads; when it turns grey, you're ready.
A tray icon appears by the clock at the same time. The first load takes longest, 5-20 seconds.

### Keep it running

From 0.2.3 the installer offers to start LocalFlow for you, and the answer defaults to **yes**.
It registers a per-user scheduled task called **LocalFlow** that runs 20 seconds after each
sign-in and restarts the app (up to 10 times, a minute apart) if it ever stops. No administrator
rights are needed and nothing runs as a service.

Why it exists: **signing out of Windows closes every app you own.** Signing back in restores your
desktop but not your apps, so LocalFlow stays down until somebody starts it by hand. This task is
what brings it back. A Windows session can also bounce on its own after an update or a crash, with
no warning.

To turn it on or off: right-click the dot (or the tray icon) and use **Start with Windows**. It
shows a tick when the task exists. From the installer: `install.bat -Autostart` or
`install.bat -NoAutostart` skips the question entirely. `--doctor` reports whether the task
exists, what state it is in, and whether Ollama has a startup entry of its own.

Starting LocalFlow twice is harmless: the second copy notices the first, says so, and exits
rather than adding a second dot and a second hotkey listener.

### Two things Windows will do that are not bugs

**1. Recording produces nothing at all.**
Check **Settings > Privacy & security > Microphone** and make sure **"Let desktop apps access your
microphone"** is switched on. When it is off Windows does not show an error or a prompt. The
microphone simply returns silence, forever, and LocalFlow looks broken. `--doctor` opens the
microphone and tells you if this is what is happening.

**2. Your antivirus flags LocalFlow.**
LocalFlow watches for a global hotkey and sends synthetic keystrokes, because that is the only way
to hear Ctrl+Win while you are typing in another app and then put text into it. Those two
behaviours are also what a keylogger does, so some antivirus heuristics score them. It is a
heuristic, not a detection of anything LocalFlow actually does: the source is right here, the
keystroke listener never writes keys anywhere, and nothing is sent off the machine. If your AV
quarantines it, add the LocalFlow folder as an exclusion, or read
[`localflow/hotkeys.py`](localflow/hotkeys.py) and
[`localflow/inject.py`](localflow/inject.py) first and decide for yourself.

---

## Using it

Open any text box. Notepad, a browser address bar, Slack, your email. Hold **Ctrl+Win** and talk.

| What you want | How |
|---|---|
| **Dictate one thing** | Hold **Ctrl+Win**, talk, release |
| **Keep dictating** | **Double-tap Ctrl+Win**, press **Ctrl+Win+Space**, or click the dot. Talk as long as you like; nothing is pasted until you stop |
| **Stop dictating** | Tap **Ctrl+Win** again, press **Ctrl+Win+Space**, or click the dot. The whole speech is transcribed and cleaned in one pass, then pasted. **Esc** stops and throws it away |
| **Throw away what you just said** | **Esc** while still holding |
| **Paste that again** | **Shift+Alt+Z** |
| **Extra-polished version** | Hold **Ctrl+Win+Alt** instead (slower, rewrites for clarity) |
| **Free up the GPU for a game** | Right-click the dot → **Pause (free GPU)**, or **Low VRAM mode** to hand back 2.4 GB and keep dictating |
| **Update to the latest version** | Double-click `update.bat` |
| **It is stuck or misbehaving** | Right-click the dot → **Restart LocalFlow** |
| **Change anything** | Right-click the dot or the tray icon |

### Things you can just say

| Say this | Get this |
|---|---|
| "period", "comma", "question mark" | `.` `,` `?` |
| "new line", "new paragraph" | A line break, or a blank line |
| "no wait", "scratch that", "I meant", "oops" | The thing you said before it is replaced |
| "press enter" | Your text is sent (the only time Enter is ever pressed) |
| Three or more items in a row | A bulleted list |

### What the dot is telling you

![The Flow Dot in each of its states](docs/images/flow-dot-states.png)

| Dot | Meaning |
|---|---|
| Blue, steady | Loading the speech model, 5-20 s after launch, longer the first time |
| Grey | Ready and listening for your hotkey |
| Red, pulsing with your voice | Recording |
| Orange/red with a ring | Hands-free mode is on. It stays like this for the whole speech, then turns blue while it transcribes and cleans |
| Blue, blinking | Thinking — your text is coming |
| Green | Just pasted |
| Red ring | Something went wrong (check `localflow.log`) |
| Hollow grey ring | Paused, GPU freed |

Drag the dot anywhere you like; it remembers. Right-click it for the full menu.

---

## Making it yours

Everything lives in `config.yaml`, next to the app. The installer creates it for you, and
LocalFlow creates it on first run if it is missing. Either way it starts as a copy of
`config.example.yaml`, so nothing you see there is a surprise.
[`config.example.yaml`](config.example.yaml) documents every option. The settings people change most:

```yaml
hotkeys:
  ptt: [ctrl, win]        # Conflicts with something? Try [ctrl, alt] or a single key like [f9]

cleanup:
  level: medium           # none = fastest, raw-ish | light | medium | high = most rewriting
  handsfree_level: high   # The level used for a whole hands-free speech
  dictionary:             # Names it keeps getting wrong
    local flow: LocalFlow
  snippets:               # Say the phrase, get the text
    "my signature": "Best regards,\nYour Name"

audio:
  device: ""              # Part of your mic's name, or "" for the system default
  handsfree_mode: whole   # whole = one pass when you stop | chunked = the old paste-at-each-pause mode

asr:
  engine: parakeet        # parakeet = the default, fastest, English only | whisper = multilingual
```

**The speech engine, and why you probably want the default.** `asr.engine` takes `parakeet` or
`whisper`. Whisper is the multilingual option — it is not the fast one. Measured on an RTX 4070
SUPER on the same four recordings, five runs each, median per clip:

| | Parakeet TDT 0.6B v2 (default) | Whisper large-v3-turbo |
|---|---|---|
| "Hey Sarah, can you send me that report by Friday question mark" (4.7 s) | **43 ms** | 135 ms |
| "Send a report to Mark. No, no, wait…" (5.0 s) | **43 ms** | 151 ms |
| "For the store I need potatoes, cream cheese…" (5.9 s) | **46 ms** | 141 ms |
| "Um so this is a test comma scratch that…" (5.8 s) | **41 ms** | 144 ms |
| Video memory while loaded | 3,009 MB | **2,323 MB** |
| Languages | English | ~99 |

So Whisper is about **three times slower** per dictation and saves about 0.7 GB of video memory.
If you switched to it hoping dictation would get faster, switch back. There is a second
difference worth knowing: Whisper punctuates for you and drops what it thinks is noise, so the
spoken commands LocalFlow understands are eaten before they reach it — on the clips above it
turned "…by Friday question mark" into "…by Friday?" and swallowed a leading "Um" and a spoken
"comma". Parakeet transcribes what you actually said and lets the cleanup rules do that work.
Pick Whisper when you dictate in a language Parakeet does not speak; otherwise stay on the
default. Changing the engine needs a restart (the menu says so), and the first start on a new
engine downloads its model: about 1.5 GB for Whisper, about 2.5 GB for Parakeet.

A single key such as `[f9]` works fine as the push-to-talk chord. Bear in mind LocalFlow never
swallows the key, so whatever you pick still reaches the app you are typing into. Function keys and
modifier chords are the safe picks; a letter key is not.

**Cleanup levels.** `none` is pure transcription and the lowest latency. `light` removes fillers and
fixes punctuation. `medium` (the default) also fixes grammar and formats lists. `high` rewrites for
clarity and brevity — good for turning rambling into a tidy paragraph.

**Hands-free uses its own level.** `cleanup.handsfree_level` (default `high`) is applied to a whole
hands-free speech, because a long speech is where the heavier rewrite earns its keep. There is a
real trade-off to know about. `high` handles spoken corrections best: in testing it resolved a
mid-sentence "scratch that" correctly and kept every fact. It is also willing to tidy things away.
In the same test it dropped a throwaway transition ("Oh, and one more thing.") and, where the
speaker said the same thing twice, it kept it only once. `medium` stays closer to your wording but
mangled that "scratch that" clause. So leave it on `high` if corrections matter most, set it to
`medium` if you want your own words preserved as closely as possible, or `none` for a raw
transcript. A long speech is cleaned in sentence-aligned segments of about `llm.segment_words`
(400) words, and a segment the model fails on falls back to the rules cleanup on its own, so one
slow request never costs you the whole speech.

---

## Privacy and your data

Nothing you say or type is transmitted anywhere. There is no account, no telemetry, no crash
reporting, no update check, and no analytics of any kind. LocalFlow opens no listening socket
unless you turn on phone access, and then only on the local machine for Tailscale to reach
(see [From your phone](#from-your-phone)).

Everything it writes lives in the LocalFlow folder, and that is the complete list:

| File | What is in it |
|---|---|
| `config.yaml` | Your settings. Also your dictionary and snippets, so treat it as yours. |
| `history.jsonl` | **The plain text of every utterance you dictate**, one JSON line each, capped at `history.max_entries` (5000 by default). Each line also records the **window title** of the app you dictated into, which can name a document or a browser tab. |
| `localflow.log` | Timings, errors, state changes, and the name of the foreground program. With `debug: true` it also logs window titles and the full pasted text. |
| `models/` | The downloaded speech model. No personal data. |

`history.jsonl` is the one to know about. It is readable plain text on your disk, so anyone who
can read your files can read what you dictated. That is a deliberate trade for being able to
re-paste and search your own words. You can delete it at any time (LocalFlow just starts a new
one), or turn it off completely:

```yaml
history:
  enabled: false
```

**The only time LocalFlow uses the network is during install**: pip fetches the Python packages,
Hugging Face serves the speech model once, and Ollama pulls the cleanup model. After that the app
sets `HF_HUB_OFFLINE=1` on itself and the only connection it ever makes is to `127.0.0.1:11434`,
which is Ollama on your own machine. Unplug the network and everything still works.

---

## Updating

**Double-click `update.bat`.** That is the whole thing. It fetches the latest files over your
folder (with `git pull` if you cloned, otherwise the ZIP from GitHub), re-runs the installer, and
restarts LocalFlow if it was running. It prints the old and new version numbers when it finishes.

Your `config.yaml`, `history.jsonl`, `localflow.log`, the downloaded models and `.venv` are not part
of the download, so nothing of yours is touched or deleted. Your **Start with Windows** setting is
left exactly as it is — `update.bat` never asks about it and never changes the scheduled task.

**Updating an install older than 0.3.0**, which has no `update.bat` yet:

1. [Download the ZIP](https://github.com/DatafyingTech/LocalFlow/archive/refs/heads/main.zip),
   right-click it → **Properties** → tick **Unblock** → OK, then **Extract All**.
2. Copy everything from the extracted `LocalFlow-main` folder over your old LocalFlow folder and
   choose **Replace the files in the destination**. Your settings, history and downloaded models
   are not in the ZIP, so they are kept.
3. Double-click `install.bat`.

From then on, `update.bat` does all of that for you.

New settings added by an update are filled in from the built-in defaults, so an old `config.yaml`
never stops working.

## Uninstall

There is nothing in Add/Remove Programs. Two steps, in this order:

0. **Turn off autostart first.** Right-click the dot → untick **Start with Windows** (or
   double-click `uninstall-autostart.bat`). This removes the scheduled task named `LocalFlow`. Do it
   before you delete the folder, otherwise the task stays registered and fails quietly at every
   sign-in.
1. **Delete the LocalFlow folder.** That takes `.venv`, `models`, your `config.yaml`, your
   `history.jsonl` and `localflow.log` with it.

**What is still on your PC after the folder is gone:** the Ollama cleanup model, about 3.3 GB in
`%USERPROFILE%\.ollama`. Remove it with `ollama rm gemma3:4b` if you do not use Ollama for anything
else, and uninstall Ollama itself from Windows Settings. Python also stays installed; uninstall it
from Windows Settings if you only added it for LocalFlow. Nothing else of LocalFlow's remains —
no registry entries, no services, no files outside its own folder.

---

## Games and anti-cheat

Games read the keyboard directly and ignore a normal paste, so when a fullscreen game is in front,
LocalFlow types your text **key by key using hardware scan codes** — the same mechanism used by
macro keyboards, AutoHotkey, Steam Input, and accessibility software.

**LocalFlow never touches the game process.** No DLL injection, no hooks in other programs, no
reading game memory, no graphics overlay hooking, no driver, and no messages posted into the game's
window. It only asks Windows which window is in front, then sends keystrokes to whatever has focus.
Keystroke timing is slightly randomized so it doesn't look like a periodic macro, typing is capped
at 500 characters, and **Enter is never pressed unless you say "press enter"**.

That said: this is a tool for typing chat messages, not for automating gameplay. No third-party tool
can promise how a given anti-cheat will behave, and strict kernel-level systems may simply ignore
synthetic input. Use it for chat, and if a game's rules forbid any synthetic input, don't use it there.

**Sharing the GPU with a game.** Three settings, in order of how little they cost you:

- **Low VRAM mode** (right-click the dot) swaps the speech model for its int8 build: about
  0.6 GB of video memory instead of 3.0 GB, so you hand the game back about 2.4 GB and still
  dictate at any time. Measured cost: about a third of a second per dictation, and the same words.
- **Pause (free GPU)** unloads both models entirely. Nothing dictates until you resume.
- **Auto-pause in fullscreen apps** does that for you whenever a game is in front, and resumes
  about five seconds after you leave it.

---

## From your phone

The Android app is a floating dot, like the desktop one, that dictates through your PC.

![The Flow Dot in each of its states](docs/images/flow-dot-states.png)

- **Long-press and hold** the dot to dictate one phrase. Release to send.
- **Tap** it to start hands-free. It expands into a pill with a live waveform between an ✕ to
  discard and a ✓ to send. Tap ✓ or the dot to finish.
- The colours mean the same as on the desktop: grey idle, red listening, orange hands-free,
  blue while your PC thinks, green when the text has landed, a red ring on an error.
- The text is typed at the cursor of whatever field is focused, in any app. If an app blocks
  that, it goes to the clipboard and you paste it.

**How it connects.** The phone talks to a small API on the PC over
[Tailscale](https://tailscale.com), a private network between your own devices that works from
anywhere with a signal. The PC transcribes and cleans exactly as it does for the desktop hotkey,
so quality is identical. Round trip from LTE measured at about 40 ms plus the usual processing,
so well under a second per phrase.

**Setup, in order:**

1. Install Tailscale on the PC and on the phone, signed in with the **same** account.
2. On the PC, right-click the dot and turn on **Enable phone access**, then open **Phone setup**
   and press **Copy setup line**.
3. Install the Android app from the [Releases page](https://github.com/DatafyingTech/LocalFlow/releases)
   (the `.apk` asset of the latest `android-v*` release), open it, and press **Paste setup line**.
4. Grant the four permissions the app asks for, in the order it asks: microphone, display over
   other apps, the LocalFlow accessibility service, and notifications.
5. Switch on **Show the dot**.

The full guide, with the exact Settings paths on Samsung and stock Android and a
troubleshooting list, is in [docs/ANDROID.md](docs/ANDROID.md). The API the phone uses is
documented in [docs/API.md](docs/API.md), so you can build your own client.

**What this changes about privacy.** With phone access on, audio travels between your phone and
your PC through Tailscale, which is encrypted end to end and never sees the content. The PC API
listens only on the local machine and is reachable only through that tunnel, with a token the PC
generates. So the promise becomes "never leaves your own devices" rather than "never leaves this
machine". Phone access is off by default. If the PC is asleep or off, the phone cannot dictate;
it says so instead of failing silently.

**iPhone** is not supported. iOS keyboard extensions cannot draw a floating dot or type into
other apps the way Android's accessibility services can.

---

## When something goes wrong

**Start here.** **Double-click `doctor.bat` and paste what it prints** — it checks every part of the
stack and tells you which one is unhappy. From a terminal, the same thing:

```powershell
.\.venv\Scripts\python.exe -m localflow --doctor
```

<details>
<summary><b>The phone says it cannot reach the PC</b></summary>

From 0.1.4 the phone app names which of the three it is, and they have different fixes:

| The phone says | What actually happened | Fix |
| --- | --- | --- |
| *Tailscale is off on this phone, or the PC name is wrong* | The PC's name did not resolve at all | Turn Tailscale on **on the phone**, or re-paste the setup line (PC tray → **Phone setup**) |
| *The PC is on the network but LocalFlow is not running on it* | The PC answered and refused the connection | Start LocalFlow on the PC, and tick **Enable phone access** in its menu. If this keeps happening after you sign out of Windows, turn on **Start with Windows** |
| *The PC is offline or asleep* | Nothing answered | Wake the PC, or check Tailscale **on the PC** |

The **Diagnose** button on the app's setup screen runs the health check and prints which of the
three it was plus the raw exception name, which is the useful thing to paste into a bug report.
Tapping the red ring on the dot repeats the last message.
</details>

<details>
<summary><b>It seems frozen, or it started recording when I only pressed Ctrl</b></summary>

Right-click the dot or the tray icon and choose **Restart LocalFlow**. It starts a fresh copy and
forces the old one out if needed, so it works even when the app is stuck.

Both causes are fixed from 0.2.5. Windows sometimes never reports that a key was released (after
Win+L, a UAC prompt, or with an administrator window in front), which used to leave the Windows key
"held" so that a lone Ctrl started a recording; LocalFlow now checks the real keyboard state. And
after the PC wakes from sleep the GPU can drop its session, which used to make every dictation fail
until you paused and resumed; LocalFlow now reloads the speech engine itself and keeps what you said.
</details>

<details>
<summary><b>The hotkey does nothing</b></summary>

Another app may already own Ctrl+Win (PowerToys is a common culprit) — change `hotkeys.ptt` in
`config.yaml`. Windows also blocks normal apps from seeing keys while an **administrator** window is
focused; if you dictate into admin apps, run LocalFlow as administrator too.
</details>

<details>
<summary><b>It says it can't use my GPU / fell back to CPU</b></summary>

Update your NVIDIA driver (525 or newer). Then check that the CPU-only ONNX package isn't shadowing
the GPU one: `pip show onnxruntime` should print **nothing**. If it prints something, run
`pip uninstall onnxruntime onnxruntime-gpu` then `pip install --no-deps onnxruntime-gpu==1.24.4`.
Never install PyTorch into this environment — it brings an incompatible cuDNN with it.
</details>

<details>
<summary><b>Text is slow to appear, or comes out messy</b></summary>

Something else is probably using your GPU. A game or video editor competing for VRAM makes the
cleanup model slow, and when it takes too long LocalFlow pastes the simpler rule-based version
instead so you never lose what you said. Use **Pause (free GPU)** or **Low VRAM mode** while
gaming, or set `cleanup.level: none` for pure speed. A transcription that takes longer than a
second is logged as `slow transcription (N ms) — something else may be using the GPU`, so the log
tells you which of the two is happening.

The first dictation after a long pause is a special case, and since 0.3.1 it no longer costs you
anything. Ollama unloads the cleanup model after `llm.keep_alive`, and loading it back takes
7–19 seconds. Rather than make you wait, LocalFlow checks whether the model is in memory, uses
the rule-cleaned text for that one dictation, and warms the model in the background — the log
says `cleanup skipped: model was not loaded (warming it for next time)` — so everything after it
is fully cleaned again. Set `llm.skip_when_cold: false` if you would rather wait for the model.

If the text is merely *plainer* than usual, Ollama is probably not running: LocalFlow falls back
to the rules-only cleanup rather than failing. It re-checks every 30 seconds, and if Ollama is
installed but nothing named `ollama*` is running, it starts Ollama itself (at most three times
per session, five minutes apart) — so this usually fixes itself within a minute, with no restart,
and `/v1/health` flips `llm_ok` back to `true`. Set `llm.autostart_ollama: false` if you would
rather LocalFlow never launched anything.
</details>

<details>
<summary><b>Nothing gets typed in my game</b></summary>

Right-click the dot and turn on **Force keystroke typing**. If your game isn't detected as
fullscreen, add part of its window title or executable name to `inject.type_apps` in `config.yaml`.
</details>

<details>
<summary><b>Nothing is recorded at all - the dot goes red but no text ever arrives</b></summary>

Almost always the Windows microphone privacy setting. Open **Settings > Privacy & security >
Microphone** and turn on **"Let desktop apps access your microphone"**. Windows gives no error when
this is off; the microphone just returns silence. `--doctor` now opens the microphone for real and
reports exactly what Windows said.
</details>

<details>
<summary><b>Hands-free dropped words / stopped mid-sentence</b></summary>

This was real, and it is fixed. Hands-free used to work in chunks: a voice-activity detector
watched for a 700 ms pause, closed the chunk there, transcribed it and pasted it while you carried
on talking. The detector's loudness threshold sat above a normal speaking level, so quiet speech
was read as silence, chunks closed while you were still talking, and words went missing.

Hands-free now records the whole speech and transcribes it once when you stop, which is the
`audio.handsfree_mode: whole` default. Nothing is pasted until you stop, and nothing is dropped in
between. If you preferred live text arriving as you talk, set `audio.handsfree_mode: chunked` in
`config.yaml`. The detector threshold in that mode is now `0.002` instead of `0.008`, which is
below a quiet speaking voice, so chunked mode works far better than it did. Raise
`audio.handsfree_vad_threshold` again if you are in a noisy room.
</details>

<details>
<summary><b>My quiet speech gets dropped</b></summary>

It shouldn't — audio is normalized before transcription and quiet speech is well supported. If it
still happens, check that `audio.device` names the right microphone, and look at `localflow.log`
for `clip dropped`.
</details>

Design notes and the engine comparison live in [docs/](docs/). Still stuck? [Open an issue](https://github.com/DatafyingTech/LocalFlow/issues)
and paste your `--doctor` output.

---

## How it works

```
Ctrl+Win ──► record 16 kHz audio ──► normalize ──► Parakeet ASR (your GPU)
                                                          │
       paste, or type into a game ◄── cleanup rules ◄──────┘
                                            │
                                     Ollama LLM pass
                              (fillers, corrections, lists)
```

**Speech recognition** is NVIDIA's [Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2)
running on ONNX Runtime with CUDA. It's more accurate on English than Whisper large-v3-turbo,
punctuates on its own, and doesn't hallucinate text during silence.

**Cleanup** is a fast local [gemma3:4b](https://ollama.com/library/gemma3) pass through Ollama, with
a strict edit-only prompt and a sanity check on its output. If it's slow, unavailable, or returns
something suspicious, the deterministic rule-based cleanup is used instead — **you never lose an
utterance**.

Measured on an RTX 4070 SUPER with the GPU otherwise idle:

| Stage | Time |
|---|---|
| Transcription (5.8 s of audio) | ~40 ms |
| Rule-based cleanup | < 1 ms |
| Language model cleanup (10–40 words) | ~265–660 ms |
| Paste | ~90 ms |
| **Total, cleanup off** | **~150 ms** |
| **Total, cleanup on** | **~250–500 ms** |

Hands-free is a longer job because it holds the whole speech, but it is not a slow one. On the same
machine, 35 seconds of continuous speech (88 words) transcribed in 524 ms with nothing dropped, and
the cleanup of a realistic 215-word dictation took about 2.3 s at either `medium` or `high`.

The reasoning behind each choice — engines benchmarked, models rejected, and why — is written up in
[docs/research/](docs/research/).

---

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for how we expect people to treat each other. Found a
security problem? Please read [SECURITY.md](SECURITY.md) and report it privately rather than in an
issue.

Things that would help most:

- AMD/Intel GPU acceleration (DirectML or Vulkan)
- More languages (Parakeet is English-only; the Whisper engine is already multilingual)
- A packaged installer so Python isn't a prerequisite

## Credits

Built on [Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) by NVIDIA,
[onnx-asr](https://github.com/istupakov/onnx-asr), [Ollama](https://ollama.com),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), and
[pynput](https://github.com/moses-palmer/pynput).

The idea came from using [Wispr Flow](https://wisprflow.ai) and wanting the same convenience without
a subscription or the cloud. If you want a polished cross-platform product with a support team
behind it, go buy theirs. It is genuinely good.

LocalFlow is an independent implementation written from scratch on open-source components. It is not
affiliated with, endorsed by, or sponsored by any commercial dictation product, and contains none of
their code. Product names and trademarks belong to their respective owners.

## License

**LocalFlow's own code is [MIT](LICENSE).** Use it, fork it, ship it commercially.

**The models it downloads are not MIT, and they are not all open source.** The installer fetches
them from third parties, and their terms are yours to follow:

| What | License | What that means in practice |
|---|---|---|
| [Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) (speech recognition) | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) | Free for commercial use. Credit NVIDIA if you redistribute the model or build a product on it. |
| [gemma3:4b](https://ollama.com/library/gemma3) (text cleanup, optional) | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) | **Not an open-source license.** Google's [prohibited use policy](https://ai.google.dev/gemma/prohibited_use_policy) applies, and you must pass the same restrictions on to anyone you give it to. |
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) (optional fallback) | MIT | No restrictions. |

If the Gemma terms do not work for you, set `cleanup.level: none` for rules-only cleanup, or point
`llm.model` at any other Ollama model you prefer. Nothing in LocalFlow depends on Gemma specifically.

Among the Python dependencies, [pynput](https://github.com/moses-palmer/pynput) is LGPL-3.0. It is
used as an unmodified library, which is what LGPL allows; if you redistribute a modified pynput you
inherit its terms.

None of this affects you dictating on your own machine. It matters if you redistribute LocalFlow
bundled with the models, or build a product on top of it.
