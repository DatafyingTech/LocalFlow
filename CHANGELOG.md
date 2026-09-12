# Changelog

All notable changes to LocalFlow are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

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
