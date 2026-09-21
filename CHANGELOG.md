# Changelog

All notable changes to LocalFlow are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.6] - 2026-09-21

### Fixed

- **Install failed on Python 3.11.** `numpy` was pinned to 2.5.2, which has no Python 3.11 build
  (numpy 2.5 dropped 3.11), so `install.bat` stopped at the first package for anyone on 3.11 even
  though 3.11 is documented as supported. The pin now selects 2.4.6 on Python 3.11 and 2.5.2 on
  3.12 and newer. Every pin in both requirements files was re-checked against the real resolver
  on 3.11, 3.12 and 3.13, and a full install plus the speech self-test was run on Python 3.11.

### Changed

- CI now runs the whole `tests` directory instead of one file, and Windows runners execute the
  hotkey, text-injection and phone-API tests. That change is what exposed the bug above.

## [0.2.5] - 2026-09-21

### Fixed

- **Pressing Ctrl on its own could start a recording.** The set of held keys was built only from
  key-down and key-up events, and Windows does not deliver a key-up in several ordinary
  situations: locking the PC with Win+L, a UAC prompt, or an administrator window in front. The
  Windows key then stayed "held" forever, so the next lone Ctrl looked like Ctrl+Win. The only
  cure was to tap Ctrl+Win again. LocalFlow now checks the real keyboard state before acting on
  a press and drops any key Windows says is up, and a watchdog does the same every 150 ms.
- The mirror image of the same bug: a recording that never stopped because the key-up was
  swallowed mid-hold now ends on its own within about a third of a second.
- First tests for the hotkey state machine (`tests/test_hotkeys.py`).
- **Dictations lost after the PC wakes from sleep.** When the GPU context dies ("CUDA failure
  999", after sleep or a display-driver reset) every transcription failed until the models were
  reloaded by hand with Pause then Resume, and each failed dictation was thrown away. LocalFlow
  now reloads the speech engine by itself and transcribes the same audio again, so nothing is
  lost; it only reports an error if the reload does not help.

### Added

- **Restart LocalFlow** in the tray and dot menu. It starts a fresh copy and forces the old one
  out if it has not exited within a few seconds, so it works even when the app is wedged.

## [0.2.4] - 2026-09-14

A friend with a gaming PC and no developer habits tried to install LocalFlow from the README. He
got Python 3.14 from python.org's big yellow button, the installer told him "No supported Python
found" while a Python sat right there on his PATH, and that was the end of the attempt. This
release is that walk-through, fixed.

### Fixed

- **The Python link handed out a version LocalFlow cannot use.** The README now links the
  Python **3.12** release page directly, says plainly not to press the big yellow Download button,
  and reminds you to tick *Add python.exe to PATH* (the Microsoft Store's Python 3.12 works too and
  has no checkbox to forget).
- **"No supported Python found" now says what it found.** The installer looks at every Python it
  can reach and reports it by version and path — *Found Python 3.14.0 at C:\... but LocalFlow needs
  3.11 to 3.13* — instead of implying there is no Python at all.
- **Autostart registration was broken in the installer.** A stray control character had crept into
  the path to `tools\autostart.ps1`, so every install said *cannot register the task* no matter how
  you answered. The tray menu's **Start with Windows** was unaffected.
- **A small C: drive could fail the install halfway through.** pip unpacks every wheel in `%TEMP%`,
  which lives on C: even when LocalFlow does not, and the CUDA wheels alone are about 2 GB. The
  installer now points pip's temporary folder at the LocalFlow drive for the duration and warns if
  C: is short anyway.
- **`install.bat` no longer depends on PATH** to find PowerShell; it calls it by full path, so a
  damaged PATH gives a real install instead of "powershell is not recognized".
- **The blue dot is documented.** LocalFlow is blue, not grey, while the speech model loads. The
  README's dot table, the install banner and the first-launch instructions now all say *a blue dot
  appears while it loads; when it turns grey, you're ready*.
- **Uninstall said the wrong thing.** It claimed nothing is installed into Windows (the autostart
  task is), told you to delete a `shell:startup` shortcut that no version has created since 0.2.3,
  and did not mention the 3.3 GB Ollama model left behind. All three corrected, with turning
  autostart off as step 0.
- **CPU mode no longer looks broken.** Installing with `-CPU` (or setting
  `asr.allow_cpu_fallback: true`) had the smoke test print *ERROR ... CUDAExecutionProvider not
  available* and *WARNING ... CUDA provider will fail*, and `--doctor` answered with three
  warnings. Choosing CPU is not a fault: those lines are now a single INFO —
  *CPU mode (as configured): running without CUDA* — and the NVIDIA, ONNX Runtime, ORT-wheel and
  cuDNN rows read `[OK] ... CPU mode, as configured`, so the report ends with *Everything checks
  out.* The warnings still appear for anyone who did **not** choose CPU and has no CUDA — which
  really is a problem.
- **The one-time model download is readable again.** It used to scroll past an
  *unauthenticated requests to the HF Hub* notice and about eighty `INFO httpx: HTTP Request: GET
  https://huggingface.co/...` lines full of signed tokens. LocalFlow now turns the `httpx` and
  `huggingface_hub` loggers down to WARNING, sets `HF_HUB_VERBOSITY=warning` and
  `HF_HUB_DISABLE_TELEMETRY=1`, and prints one plain line — *Downloading the speech model (about
  2.5 GB). This happens once.* — above the progress bar, which is untouched.
- **A misspelt installer flag is no longer ignored.** `install.bat -cpu-only` used to run a full
  GPU install as though nothing had been typed. `install.ps1` now declares `[CmdletBinding()]`, so
  an unknown switch stops the install in under a second with PowerShell's *A parameter cannot be
  found that matches parameter name 'cpu-only'* and nothing is created.
- **Unattended runs no longer answer their own questions.** With input redirected
  (`install.bat < nul`, a CI job, a management tool) `Read-Host` returns an empty string, and the
  autostart prompt's default *yes* registered the scheduled task without anyone agreeing to it —
  the winget Python offer could install Python the same way. The installer now detects redirected
  input (`[Console]::IsInputRedirected`) and, unless `-Autostart`/`-NoAutostart` said otherwise,
  skips the prompt, registers nothing and prints *Not starting automatically (no interactive
  console). Turn it on later from the tray menu: Start with Windows.* Python is never installed
  unattended either: it prints the download link unless the new `-InstallPython` switch was passed.

### Added

- **The installer offers to install Python for you.** When no usable Python is found and Windows'
  package manager is available, it asks (Enter for yes) and installs Python 3.12 with winget,
  refreshes PATH in place and carries on with the install. `-NoPythonInstall` skips the offer,
  `-InstallPython` accepts it without asking (the only way to install Python in an unattended run);
  `-Python <path>` still wins.
- **`doctor.bat`** — double-click it to run the full self-check and read the report in a window
  that stays open. It is what the README, the install banner and the About box now point at, and
  there is a **Run doctor** item in the dot and tray menus that opens it.
- **`uninstall-autostart.bat`** — one double-click removes the scheduled task, for people who would
  rather not hunt through a menu before deleting the folder.

### Changed

- **Honest numbers and clearer prompts in the installer.** The package step says *about 2-3 GB,
  5-15 minutes on a normal connection* rather than "a few hundred MB"; the speech model download
  announces its size before it starts; and the autostart question reads *Start LocalFlow
  automatically when you sign in to Windows? Press Enter for yes, or type n then Enter for no*,
  explaining that it restarts LocalFlow if it crashes and that Quit from the menu is respected.
- **The README leads with the desktop.** A line under the tagline points straight at the three
  install steps and says the phone half is optional; the phone bullet moved to the end of
  Highlights and is marked optional; and the ZIP instructions now warn about the second
  `LocalFlow-main` folder inside the first, with the SmartScreen "Run anyway" note where you
  actually meet it.

## [0.2.3] - 2026-09-14

Windows logged a user's session out and straight back in (Winlogon 7002/7001, four seconds
apart). That closed LocalFlow and Ollama, nothing brought either back, and the phone spent the
rest of the day saying "PC not reachable, is Tailscale on?" — while Tailscale was fine. This
release removes all three reasons that took a day to work out.

### Added

- **Autostart.** `install.bat` now offers (default **yes**) to register a per-user scheduled
  task named `LocalFlow`: at sign-in, 20 s delay, restarts up to 10 times a minute apart, no
  execution time limit, a second launch ignored. `-Autostart` / `-NoAutostart` answer it
  non-interactively, and re-running updates the task instead of adding another. The tray and dot
  menus gained a **Start with Windows** tick that registers or removes exactly the same task
  (no elevation: it is a per-user task). The definition lives in one place,
  `tools/autostart.ps1`, so the installer and the app cannot drift apart.
- **`--doctor` reports autostart**: whether the `LocalFlow` task exists and its state, and
  whether Ollama has a startup entry of its own (Startup folder `Ollama.lnk` or `HKCU\Run`).
- **Single-instance guard.** Starting LocalFlow a second time used to give you two dots and two
  hotkey listeners. The second copy now logs it, says *LocalFlow is already running*, and exits 0.

### Fixed

- **`llm_ok` recovers on its own.** Ollama was probed once, at startup, so a LocalFlow that came
  up before Ollama reported `llm_ok: false` on `/v1/health` forever and silently ran rules-only
  cleanup until someone restarted it. Reachability is now live: a light background timer
  re-probes every 30 s while it is false (and stops once it is true), every dictation and every
  `/v1/health` and `/v1/warm` re-probe on demand, and a request that fails because Ollama went
  away flips it back to false and restarts the timer. When Ollama returns, the normal warmup runs
  once and the log says `Ollama is back; cleanup re-enabled`. All existing fallback behaviour is
  unchanged: cleanup never waits for the LLM and never loses your text.

### Android (0.1.4)

- **"PC not reachable, is Tailscale on?" is gone**, replaced by the three things that actually
  happen, each with its own toast: *Tailscale is off on this phone, or the PC name is wrong*
  (the name did not resolve), *The PC is on the network but LocalFlow is not running on it*
  (connection refused — the exact incident above), and *The PC is offline or asleep* (connect
  timeout or no route).
- **Tapping the red ring repeats the last error message**, which previously vanished with the
  toast after two seconds.
- **Diagnose** button on the setup screen: runs the health call and prints which of the three it
  was plus the raw exception class, for bug reports.

## [0.2.2] - 2026-09-12

### Fixed

- **`install.bat` run on its own, or from inside the ZIP, no longer fails with a confusing
  "No such file or directory: requirements.txt".** The installer now checks that it is inside a
  complete copy of the project. If the files are missing it downloads them next to itself and
  carries on; if it was launched from inside the ZIP (Windows runs it from a temp folder), it
  says so and tells you to extract first. `-FetchOnly` downloads the files and stops.

## [0.2.1] - 2026-09-12

### Added

- `POST /v1/warm` on the phone API. The PC unloads its cleanup model after idling and reloading
  it takes several seconds; the desktop hides that behind the hotkey key-down, but a phone gave
  the PC no such signal, so the first dictation after a pause waited 4 to 9 s. The phone app
  (0.1.3) now calls this the moment recording starts and when a text field gains focus, so the
  reload overlaps with speaking. Contract in `docs/API.md`.

## [0.2.0] - 2026-09-11

### Added

- **Phone access.** A small HTTP API on the PC (`server.enabled`, off by default) lets another
  device send audio and get back the same cleaned text the desktop hotkey produces. It listens
  only on `127.0.0.1:8770`; `tailscale serve` exposes it on your Tailscale name with no firewall
  change. Bearer-token auth, WAV or raw PCM input with resampling, `mode=ptt` or `handsfree`,
  the same one-at-a-time lock as the desktop path. Tray menu gains **Enable phone access** and
  **Phone setup** (copies a `URL|TOKEN` line for the phone). Contract in `docs/API.md`.
- **Android app** (`android/`, released separately as `android-v*` tags). A floating dot over
  every app: hold for one phrase, tap for hands-free with a live waveform and discard/send
  buttons, same colours as the desktop. Text is typed at the cursor through an accessibility
  service, with clipboard fallback. Setup guide in `docs/ANDROID.md`. Compiled and unit-tested;
  first device runs are by the community.
- `run_pipeline` in `localflow/__main__.py`: the transcribe-and-clean pipeline as a reusable,
  side-effect-free function shared by the hotkey path and the API.

### Changed

- The privacy statement now distinguishes "never leaves this machine" (default) from "never
  leaves your own devices" (phone access on).

## [0.1.1] - 2026-09-11

### Changed

- **Hands-free now records the whole speech** and transcribes it in one pass when you stop
  (`audio.handsfree_mode: whole`, the new default). Nothing is pasted while you talk. The result
  is cleaned at the new `cleanup.handsfree_level` (default `high`), the same enhanced pass the
  polish hotkey uses. The previous chunk-at-every-pause behaviour is still available as
  `audio.handsfree_mode: chunked`.
- `audio.handsfree_vad_threshold` default lowered from `0.008` to `0.002` for chunked mode. The
  old value sat above a normal speaking level (around 0.003 RMS), so real speech was classified as
  silence: chunks closed while the speaker was still talking and the "only silence so far" buffer
  trim discarded words. This was the root cause of hands-free dropping words during continuous
  speech.

### Added

- **Segmented cleanup for long speech** (`llm.cleanup_long`). A transcript is split at sentence
  boundaries into segments of about `llm.segment_words` (new, default 400) words, each cleaned
  with `llm.handsfree_timeout_ms` (new, default 60000), then joined. A segment whose cleanup fails
  or times out falls back to the rules-based Backtrack for that segment alone, so one slow request
  no longer throws a whole speech back to raw text.
- Sentences opening with a spoken correction ("No, no, wait.", "I meant to say", "scratch that",
  "actually", "oops") are glued to the sentence before them, so a correction is never split from
  what it corrects by a segment boundary.
- `llm.num_ctx` (new, default 8192) raises the cleanup model's context window so about 25 minutes
  of speech fits. This costs roughly 0.5 GB more VRAM for gemma3:4b than the 4096 default; the
  README's VRAM guidance now says to set it back to 4096 on 4 GB and 6 GB cards.
- A real-voice demo recording in the README, produced by `python -m tools.record_demo`.

### Fixed

- A spoken "question mark" at the end of a sentence the recogniser had already punctuated produced
  `??`. Duplicate terminal punctuation is now collapsed, with ellipsis (`...`) preserved.

## [0.1.0] - 2026-09-09

First public release. A fully local push-to-talk dictation app for Windows: hold a hotkey, talk,
release, and cleaned text appears in whatever text field has focus. Nothing leaves the machine.

### Added

- **Push-to-talk dictation** (`Ctrl+Win` by default) with a 400 ms pre-roll buffer, so the start
  of a phrase is never clipped. Release finalizes and pastes the whole utterance.
- **Hands-free mode** - double-tap the PTT chord, press `Ctrl+Win+Space`, or click the dot;
  chunks are transcribed and pasted at every ~700 ms pause while you keep talking.
- **Speech recognition on the GPU**: NVIDIA Parakeet TDT 0.6B v2 via `onnx-asr` on the ONNX
  Runtime CUDA provider (~40 ms for a 5.8 s clip on an RTX 4070 SUPER), with
  faster-whisper large-v3-turbo as a selectable fallback engine and an optional CPU mode.
- **Deterministic rules cleanup** (~1 ms, no LLM required): filler removal, spoken punctuation,
  self-correction "Backtrack" ("scratch that", "no no, I meant ..."), duplicate-word collapse,
  a personal dictionary, snippet expansion, spoken-list formatting, and a trailing
  "press enter" command (the only way Enter is ever pressed).
- **Optional local LLM cleanup** through Ollama (`gemma3:4b`) at four levels
  (none / light / medium / high) with scaled timeouts, a strict edit-only prompt and a sanity
  guard; any failure, timeout or suspicious output falls back to the rules text, so an
  utterance is never lost. A separate polish hotkey (`Ctrl+Win+Alt`) runs the high-quality pass.
- **Text injection** by clipboard paste with clipboard restore, or per-key scan-code `SendInput`
  typing for games that ignore synthetic `Ctrl+V` (auto-selected for fullscreen apps and
  `inject.type_apps`).
- **Flow Dot overlay** - a 22 px always-on-top, draggable, click-to-toggle status dot that never
  steals keyboard focus - plus a tray icon with the same menu (cleanup level, ASR engine,
  microphone, pause/resume, sounds, config/history/log shortcuts).
- **GPU citizenship**: capped ONNX Runtime VRAM arena, `keep_alive`-based LLM eviction, manual
  "Pause (free GPU)", optional auto-pause and dot-hiding while a fullscreen app is in front.
- **Local JSONL history** with per-utterance latency breakdown, plus a rotating log file.
- **Configuration** in `config.yaml`, seeded from the documented `config.example.yaml`, with
  per-app cleanup levels and deep-merged defaults so partial files are fine.
- **Installer**: `install.bat` / `install.ps1` with Python auto-detection, pre-flight checks
  (OS, 64-bit, driver version, disk space, Ollama), a no-GPU CPU mode, and an ASR smoke test.
- **Diagnostics**: `python -m localflow --version` and `python -m localflow --doctor`
  (Python, OS, GPU and driver, ONNX Runtime providers, cuDNN DLLs, Ollama and its models,
  audio input devices, config and model paths).
