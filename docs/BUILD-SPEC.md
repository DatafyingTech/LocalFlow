# LocalFlow — Build Spec (v1)

> Historical document: this is the original build specification the first version was written
> against, kept for design rationale. It is not user documentation — see README.md for that, and
> note that some details (model choices, timeouts) changed during implementation.

Goal: a free, fully local, GPU-accelerated push-to-talk dictation tool for Windows. Hold a hotkey, talk, release, and clean text appears in whatever text field has focus. Target end-to-end latency (key release -> text pasted): <= 500 ms without LLM polish, <= 1 s with.

Input: docs/research/local-stack-options.md surveys the local ASR engines, Windows integration
libraries and cleanup approaches that were evaluated, and explains why this stack was chosen.

## Reference machine
Windows 11 Pro x64, NVIDIA RTX 4070 SUPER 12 GB, 32 GB RAM, Python 3.12 (any 3.11-3.13 x64 works),
Ollama installed. No system CUDA toolkit: use pip nvidia-* runtime wheels and os.add_dll_directory
before importing engines. All timings quoted in the README were measured on this class of machine.

## Layout
<repo root>/
  install.ps1          create .venv, pip install everything, ollama pull qwen3:1.7b, download Parakeet model, run smoke test
  run.ps1 / run.bat    launch tray app (pythonw, no console)
  localflow\
    __main__.py        entry
    config.py          YAML config load/save (config.yaml next to it, created with defaults on first run)
    hotkeys.py         pynput global listener: PTT hold (default Ctrl+Win), hands-free toggle (Ctrl+Win+Space or double-tap), Esc cancels, Shift+Alt+Z re-pastes last
    audio.py           sounddevice 16 kHz mono capture with 400 ms pre-roll ring buffer; device selection by name substring; simple RMS/VAD to drop empty clips
    asr.py             Engine interface; ParakeetEngine (onnx-asr, parakeet-tdt-0.6b-v2, CUDA provider, log providers at startup, hard-fail if CPU fallback unless config allows); WhisperEngine (faster-whisper large-v3-turbo, vad_filter) fallback selectable in config
    cleanup.py         rules pass: filler words, spoken punctuation ("comma","period","new line","new paragraph"), Backtrack ("scratch that", "no wait", "I mean", "actually" -> drop preceding clause), dictionary replacements, snippets (whole-word trigger -> expansion), trailing "press enter" -> flag
    llm.py             optional Ollama cleanup: levels none/light/medium/high, model qwen3:1.7b, keep_alive=-1, temperature 0, 800 ms timeout, strict edit-only prompt, sanity guard (reject if output length deviates >2x or contains "Here is"), fall back to rules-only text on any failure
    inject.py          pyperclip set -> Ctrl+V via pynput -> restore prior clipboard after 150 ms; optional Enter keypress
    ui.py              pystray tray icon (idle/listening/processing states), tkinter always-on-top borderless pill overlay near bottom-center showing state; tray menu: toggle LLM level, pick mic, open config, open history, quit
    history.py         append JSONL of {ts, raw, cleaned, ms_asr, ms_llm} to history.jsonl; nothing leaves the machine
  tests\
    test_cleanup.py    unit tests for Backtrack, fillers, spoken punctuation, snippets, dictionary
    smoke_asr.py       transcribes a bundled/generated WAV and prints timings; used by install.ps1
  README.md            install + usage + config reference + troubleshooting (cuDNN DLL, CPU fallback, hotkey conflicts, clipboard restore failures)

## Must-haves
1. Hold-to-talk default Ctrl+Win, configurable; hands-free toggle; Esc cancel; re-paste last.
2. Finalize on release, paste as one block via clipboard with restore. Works in any app that accepts Ctrl+V.
3. Local ASR with punctuation/casing; word boosting via dictionary replacements post-ASR.
4. Cleanup levels none/light/medium/high; fillers, Backtrack, spoken punctuation, "new line".
5. Dictionary + snippets in config.yaml.
6. "press enter" trailing command.
7. Tray + overlay pill; mic selection; VAD/empty-clip rejection.
8. Local history file.
9. Latency logged per utterance; print on debug flag.

## Nice-to-have (only if cheap)
Per-app cleanup style by foreground window title (pywin32/GetForegroundWindow); "polish" hotkey using gemma3:12b.

## Non-negotiables
- No torch. Use nvidia-cuda-runtime-cu12, nvidia-cublas-cu12, nvidia-cudnn-cu12==9.*; register their bin dirs with os.add_dll_directory before importing onnxruntime/ctranslate2.
- Never use pynput suppress=True (blocks all keys on Windows).
- Any LLM failure or timeout must still paste the rules-cleaned text. Never lose the user's speech.
- Pin every dependency version in requirements.txt.
- Everything runs offline after install. No telemetry.

## Definition of done
- install.ps1 runs clean on a fresh machine and the smoke test reports CUDA provider active and ASR time for a 5 s clip.
- pytest tests/ passes.
- run.bat starts tray app; holding Ctrl+Win in Notepad and speaking pastes cleaned text.
- README documents everything above. Report measured latencies.

## Amendments (from user, 2026-09-03)
10. Overlay pill is a real button: always visible, draggable, click toggles hands-free, right-click menu mirrors tray, position persisted, topmost + WS_EX_NOACTIVATE so it never steals focus. ~90x32 px, bottom-center default.
11. Double-tap of the PTT hotkey (within ~400 ms, configurable) enters hands-free continuous dictation; chunks transcribed and pasted on VAD silence (~700 ms, configurable) so text flows while talking. Single tap, Esc, or clicking the pill ends it. Pill shows hands-free state distinctly.

## Amendments (2026-09-11)

12. **Hands-free records the whole speech instead of chunking on VAD silence.** Amendment 11 above
    describes the original design and is now the non-default `audio.handsfree_mode: chunked` path.
    The default is `whole`: record until the user stops, then transcribe everything in one pass and
    clean it at `cleanup.handsfree_level` (default `high`).

    Why it changed. Chunking lost words during continuous speech. The VAD loudness threshold
    (`handsfree_vad_threshold: 0.008`) sat above the author's actual speaking level of about
    0.003 RMS, so real speech was classified as silence: chunks closed while the user was still
    talking, and the "only silence so far" buffer trim discarded speech outright. The threshold
    default is now 0.002, which fixes chunked mode for quiet speakers, but the deeper point is that
    pasting partial text live is the wrong trade for this app. One accurate pass over a finished
    speech beats a stream of guesses at pause boundaries.

    Consequence for cleanup. A whole speech can run many minutes, which no single LLM request
    handles well. `llm.cleanup_long()` splits the transcript at sentence boundaries into segments
    of about `llm.segment_words` (400), cleans each with `llm.handsfree_timeout_ms` (60 s), and
    joins them; a segment that fails falls back to the rules Backtrack for that segment only.
    Sentences opening with a spoken correction cue are glued to the sentence before them so a
    correction is never separated from what it corrects. `llm.num_ctx` was raised to 8192 so about
    25 minutes of speech fits in context.
