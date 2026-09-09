# Changelog

All notable changes to LocalFlow are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

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
