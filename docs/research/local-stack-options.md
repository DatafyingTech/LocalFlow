# Local speech-to-text on Windows: engines, apps and architecture evaluated

Research date: 2026-09-03. Reference machine: Windows 11 Pro x64, NVIDIA RTX 4070 SUPER (12 GB VRAM), 32 GB RAM, Python 3.12, no system-wide CUDA toolkit (the CUDA runtime arrives as pip wheels). Ollama installed.

Design target: hold a hotkey -> speak -> release -> local ASR -> a cleanup pass that removes filler words, punctuates, and resolves spoken self-corrections -> text typed into the focused app. Latency budget: well under one second end to end.

---

## 1. Ready-made open-source apps (Windows-capable)

| App | Repo | Stars / activity | Stack | Hotkey model | Local engine(s) | Windows GPU | Types into any app | LLM clean-up | Install | Maturity |
|---|---|---|---|---|---|---|---|---|---|---|
| **Handy** | [cjpais/Handy](https://github.com/cjpais/Handy) | ~30.9k; v0.9.6 released 2026-08-24, weekly commits, 100+ contributors | Tauri (Rust + React) | Hold-to-record, toggle, or both; separate "transcribe with post-processing" hotkey | whisper.cpp (small/medium/turbo/large), Parakeet V2/V3 (ONNX), Moonshine, Cohere Transcribe; streaming since v0.9.0 | Yes: Vulkan via whisper-rs/transcribe-rs (not CUDA); Parakeet runs on CPU | Yes (paste, clipboard preserved since v0.9.5) | Yes (experimental): OpenAI/Anthropic/Groq/OpenRouter + **custom OpenAI-compatible URL** (LM Studio/Ollama work), custom prompt with `${output}` | `winget install cjpais.Handy` (community manifest) or .exe from Releases | Most mature; MIT |
| **OpenWhispr** | [OpenWhispr/openwhispr](https://github.com/OpenWhispr/openwhispr) | ~6.4k; 2,100+ commits, active | Electron 41 + React | Global hotkey (dictation), separate AI-assistant and translation hotkeys | whisper.cpp, Parakeet via sherpa-onnx | Yes: CUDA + Vulkan for whisper.cpp | Yes (auto-paste) | Yes: cloud BYOK + local llama.cpp; custom dictionary | .exe | Solid; MIT; heavier (Electron) |
| **Whispering (Epicenter)** | [EpicenterHQ/epicenter](https://github.com/EpicenterHQ/epicenter) | v7.11.0 (2025-12-26) | Tauri + Svelte | Push-to-talk + toggle | Parakeet only on Windows (whisper.cpp/Moonshine are macOS/Linux due to whisper-rs/MSVC toolchain conflict) | No GPU on Windows (Parakeet CPU) | Yes | Yes: "transformations" via any OpenAI-compatible endpoint (Ollama) | .msi/.exe | Good, but Windows is the weak platform |
| **Whisper Local** | [drajb/whisper-local](https://github.com/drajb/whisper-local) | 44 stars; 540 commits; young | **Python** (faster-whisper, sounddevice, ten-vad, pyperclip, pystray) | Push-to-talk `Ctrl+Win` + toggle; 500 ms pre-roll buffer | faster-whisper (CTranslate2) | Yes: CUDA via one-click pip runtime install (`nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12`) | Yes (Ctrl+V paste or key injection, per-app) | Yes: optional local Ollama clean-up; "scratch that" voice editing; YAML voice commands | `pip install whisper-local` (Py 3.11-3.13) or .exe | Early but exactly the target feature set; best reference code |
| **WhisperWriter** | [savbell/whisper-writer](https://github.com/savbell/whisper-writer) | ~1.1k; last major rewrite 2024, slow maintenance | Python 3.11 + PyQt5 | continuous / VAD / press-to-toggle / hold-to-record | faster-whisper or OpenAI API | CUDA (manual cuBLAS/cuDNN via Purfview zip) | Yes (keyboard simulation) | Planned, not implemented | git clone + venv | Aging; pinned to Py 3.11 |
| **Buzz** | [chidiwilliams/buzz](https://github.com/chidiwilliams/buzz) | ~15k+ | Python/Qt | none for dictation | whisper, whisper.cpp, faster-whisper | Yes | **No** - file/recording transcription into its own UI | No | winget / .exe | Mature but wrong tool |
| **Vibe** | [thewh1teagle/vibe](https://github.com/thewh1teagle/vibe) | popular | Tauri | none | whisper.cpp (Vulkan/CUDA builds) | Yes | **No** - file transcription | No | .exe | Wrong tool |
| **Speech Note** | Linux-only (Flatpak) | - | - | - | - | - | - | - | - | Not for Windows |
| **VoiceInk** | macOS-only | - | Swift | - | whisper.cpp | - | - | - | - | Not for Windows |
| **VoiceTypr** | [moinulmoin/voicetypr](https://github.com/moinulmoin/voicetypr) | AGPL; binaries need paid license | Tauri | PTT | whisper.cpp | - | Yes | Yes | .exe (licensed) | Source-available, not free binaries |
| **RealtimeSTT** (library) | [KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | ~10.1k; Py 3.11/3.12 CI | Python | n/a (library) | faster_whisper, sherpa-onnx (Parakeet/Nemotron), whisper.cpp, Moonshine | CUDA via faster-whisper | n/a | n/a | `pip install "RealtimeSTT[faster-whisper]"` | Good building block if you want VAD/streaming later |

Also seen in the [awesome-voice-typing list](https://github.com/primaprashant/awesome-voice-typing) for Windows: Voquill, Amical, Tambourine Voice, OmniDictate, Chirp (Parakeet CPU), HNS, SpeakoFlow, OpenWhisper (fsouza-dot). None beat Handy on maturity.

---

## 2. Engines: accuracy / latency on a 12 GB RTX 4070 SUPER (English dictation)

| Engine / model | Runtime on Windows | English WER (Open ASR LB) | Speed on 40-series | VRAM | Streaming | Verdict |
|---|---|---|---|---|---|---|
| **NVIDIA Parakeet TDT 0.6B v2/v3** | `onnx-asr` or sherpa-onnx (ONNX Runtime CUDA or CPU) - **no NeMo needed** | ~6.3 % (v3), v2 English-only slightly better | RTFx ~320 on RTX 5070 Ti (TensorRT), 57 on T4; 20-36x on a desktop CPU. A 10 s utterance = tens of ms on GPU, ~0.3-0.5 s on CPU. Built-in punctuation + casing. | <1 GB | offline (v3); NVIDIA cites ~160 ms streaming for unified variants | **Best latency/accuracy for English dictation.** Only 25 languages; CC-BY-4.0 |
| **whisper-large-v3-turbo** (faster-whisper, CTranslate2) | `pip install faster-whisper` + pip CUDA wheels | ~7.7 % (within 0.4 pt of large-v3) | 4 decoder layers; ~6x faster than large-v3. faster-whisper large-v3 int8 = ~12x real-time on RTX 4070; turbo is several x faster again -> 10 s clip in ~0.3-0.6 s | ~1.6 GB fp16 | no (chunked hacks only) | **Best multilingual / robust fallback.** Slight hallucination risk on silence; use `vad_filter=True`, `condition_on_previous_text=False` |
| **distil-large-v3.5** | faster-whisper | ~slightly better than turbo on short-form | ~1.5x faster than turbo | ~1.5 GB | no | English-only; good alternative to turbo |
| **whisper.cpp** (Vulkan/CUDA) | Handy/OpenWhispr embed it; `pywhispercpp` for Python | same models | ~8x real-time large-v3 on RTX 4070 (slower than CT2 int8); Vulkan init 5-30 s | more than CT2 int8 | partial | Fine inside Handy; not worth building on in Python |
| OpenAI `whisper` (PyTorch) | pip + torch cu12 | same | ~3-4x slower than faster-whisper | more | no | Skip |
| **Canary-Qwen-2.5B** | NeMo (Linux/WSL2) or onnx-asr `nemo-canary-1b-v2` | 5.6 % (LB #1 class) | RTFx ~418 | ~5 GB | no | Best WER but heavier; onnx-asr makes 1B usable |
| **Qwen3-ASR 0.6B / 1.7B** | transformers/vLLM | 5.8 % (1.7B), 52 langs | RTFx ~2000 claimed | 2-4 GB | no | New (2026); Windows tooling immature |
| **Cohere Transcribe 2B** | transformers/vLLM/Rust; Handy supports it | 5.4 % | RTFx ~231 | ~4 GB | no | Strong; try inside Handy |
| **Voxtral Mini 4B Realtime** | vLLM only (Linux); llama.cpp support still an open issue | 7.7 % | 80-1200 ms configurable delay | ~8-16 GB | **yes** | Not practical on Windows yet |
| **Kyutai STT 1B/2.6B** | PyTorch/MLX, Linux-centric | 6.4 % (2.6B) | 0.5-2.5 s delay | 3-8 GB | yes | Skip for Windows |
| **Moonshine** | ONNX (Handy, Whispering, RealtimeSTT) | ~ Whisper-base/small class | <100 ms per segment | tiny | yes | Great for live captions; accuracy below Parakeet |
| Streaming wrappers: WhisperLive / whisper_streaming / RealtimeSTT | Python | inherits model | 1-3 s LocalAgreement latency | - | yes | Not needed for PTT; PTT batch transcription is faster than "streaming" for utterance-length dictation |

Key insight: for push-to-talk dictation, the whole utterance is available on key release, so a fast *offline* model beats a streaming one. Parakeet-TDT on GPU transcribes a 10 s clip in well under 100 ms; whisper-turbo in a few hundred ms. Sources: [MarkTechPost ASR comparison 2026](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/), [onnx-asr benchmarks](https://github.com/istupakov/onnx-asr), [whisper.cpp vs faster-whisper 2026](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026), [Parakeet vs Whisper](https://www.parakeety.com/resources/parakeet-vs-whisper), [distil-large-v3.5](https://huggingface.co/distil-whisper/distil-large-v3.5), [Voxtral llama.cpp issue](https://github.com/ggml-org/llama.cpp/issues/20914).

---

## 3. Windows integration pieces (Python)

| Concern | Recommendation | Notes |
|---|---|---|
| Global hotkey | `pynput` (`keyboard.Listener` with on_press/on_release, pick a chord like `Ctrl+Win` or `Right Ctrl`) | `keyboard` (boppreh) is simpler and supports `suppress=True`, but is unmaintained, mis-maps some keys, and needs admin to hook elevated windows. Avoid `suppress=True` in pynput on Windows: it blocks *all* keys ([pynput #232](https://github.com/moses-palmer/pynput/issues/232), [#526](https://github.com/moses-palmer/pynput/issues/526)). Use a modifier-only or F-key chord so nothing needs suppressing. Run the tool as admin only if you need it to type into elevated apps (UIPI). |
| Audio capture | `sounddevice` (PortAudio, wheels bundle the DLL) at 16 kHz mono float32, `InputStream` with a callback appending to a list; start on key-down, stop on key-up | Keep a ~300-500 ms pre-roll ring buffer running so the first syllable isn't clipped (whisper-local does this). `pyaudio` works too but wheels lag Python versions. |
| Typing into the focused window | **Clipboard + Ctrl+V**: save clipboard (`pyperclip.paste()`), set text, `pynput` Controller press ctrl+v, restore clipboard after ~150 ms | `pyautogui.typewrite` cannot type non-ASCII/unicode and is slow per-char; `pyautogui.write`/`pynput.type` handle unicode via SendInput but drop chars in Electron apps and are slow for paragraphs. Paste is what Handy/OpenWhispr/whisper-local all use. Fallback per-app to key injection for apps that block paste (terminals, RDP). Add a trailing space option. |
| Tray icon | `pystray` + `Pillow` (menu: toggle enabled, model, clean-up on/off, quit) | pystray must run on the main thread on Windows; run hotkey listener + worker in threads. |
| Overlay indicator | Small `tkinter` top-most borderless window (`overrideredirect(1)`, `-topmost`, `-alpha 0.85`) near bottom-center showing "listening"/"transcribing"; or just a tray icon color change + `winsound.Beep` | tkinter is stdlib; keep all Tk calls on one thread via `after()`. |
| Packaging | Plain venv + a `.pyw` launcher / Task Scheduler at logon; PyInstaller only if needed | ffmpeg not required: feed numpy arrays directly to faster-whisper / onnx-asr. |

---

## 4. Text clean-up options

| Option | Latency added | Quality | Notes |
|---|---|---|---|
| Model's own punctuation/casing (Parakeet v2/v3 and Whisper both emit punctuation) | 0 | Good for punctuation; keeps "um", repeats, false starts | Baseline; always on |
| **Rule-based pass** (regex filler removal `\b(um+|uh+|erm|you know|like,)\b`, collapse duplicated words "the the", capitalize sentence starts, handle spoken "new line"/"period"/"comma", basic "scratch that" = delete previous utterance) | <1 ms | Handles 80 % of Wispr's "Light" formatting | Whisper often already omits "um/uh"; Parakeet keeps more fillers |
| **Tiny local LLM via Ollama** (`qwen3:1.7b` with `/no_think`, or `gemma3:1b`; `gemma3:4b` if quality matters) with `keep_alive=-1`, `temperature 0`, `num_predict` ~ 2x input tokens | ~100-300 ms for a 1-3 sentence utterance on a 4070 SUPER when the model is already resident (1-4B models decode at 150-300 tok/s); first call after idle adds load time -> set `OLLAMA_KEEP_ALIVE=-1` | Handles Backtrack-style self-corrections ("actually make that 75K"), casing, list structuring | Small models are unreliable editors: they sometimes answer the text instead of editing it ([lepisma: dictation with small LLMs](https://lepisma.xyz/journal/2024/09/27/dictation-with-small-llms-and-linguistic-pragmatics/)). Mitigate with a strict system prompt ("Return only the corrected text. Never answer questions in it."), few-shot examples, and a guard that falls back to raw text if the output length differs >40 % or contains the prompt. `gemma3:12b` (~8 GB) fits VRAM alongside Parakeet/Whisper (<2 GB) but decodes ~60-70 tok/s -> 1-2 s per utterance: acceptable as an opt-in "high formatting" hotkey, not for the default path. Strip `<think>` tags from reasoning models (deepseek-r1 is unsuitable). |
| llama.cpp server directly (`llama-server -m qwen3-1.7b-q4_k_m.gguf --port 8080`) | same as Ollama, slightly less overhead | same | Only if you want to avoid Ollama; OpenAI-compatible API either way |

Recommended: rule-based always; LLM pass on by default only with a 1-2B model, with the 12B model behind a second hotkey ("polish").

---

## 5. Reported real-world latency (40-series)

- faster-whisper large-v3 int8 on RTX 4070: ~12x real-time, ~2.5 GB VRAM; whisper.cpp CUDA ~8x ([promptquorum](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026)). Turbo/distil roughly 4-6x faster than large-v3 -> 10 s of speech in ~0.2-0.5 s.
- Parakeet TDT 0.6B via ONNX Runtime: RTFx 320 (RTX 5070 Ti/TensorRT), 57 (T4 CUDA), 20-36x on CPU ([onnx-asr](https://github.com/istupakov/onnx-asr), [snailtext](https://snailtext.app/blog/whisper-vs-parakeet-tdt/)). Expect 10 s of speech in <100 ms on the 4070 SUPER with CUDA EP; ~0.3-0.5 s on CPU int8.
- whisper-local (faster-whisper) advertises "sub-second" end-to-end on GPU ([repo](https://github.com/drajb/whisper-local)).
- Streaming Whisper wrappers: 1-3 s ([whisper_streaming](https://github.com/wonyx/whisper_streaming)); not competitive with PTT batch.
- Wispr Flow itself: <700 ms p99 claimed, 1-2 s felt.

Realistic end-to-end target for the recommended stack: key-release -> text on screen in **~250-500 ms** without LLM, **~500-900 ms** with a 1.7B clean-up model.

---

## Recommended architecture (ONE primary path)

**Build a ~300-line Python tool ("LocalFlow") on Parakeet-TDT (onnx-asr, CUDA) with faster-whisper large-v3-turbo as a switchable engine, clipboard-paste output, and an Ollama clean-up pass.**

Why build rather than install Handy:
1. Handy is excellent and is the right *quick win* (install it today via winget as a stopgap/benchmark), but on Windows its Whisper path is Vulkan (slower and 5-30 s GPU init) and Parakeet is CPU-only; it cannot use the 4070's CUDA. Its LLM post-processing is "experimental", cloud-first, and needs a separate hotkey.
2. Whispering has no Whisper on Windows at all; OpenWhispr is a large Electron app; WhisperWriter is pinned to Python 3.11 and lacks clean-up.
3. The pieces are all pip-installable and proven on the reference machine (the CUDA runtime wheels and Ollama are already known to work there). whisper-local (Python, MIT) is a near-identical design and serves as reference code, but it is 44 stars / single-author, so owning the ~300 lines is safer than depending on it.
4. Wispr's distinguishing features (Backtrack self-correction, per-app tone) are LLM-prompt features, which are trivial to own in a script and awkward to bolt onto a Tauri app.

Pipeline: `pynput` hotkey (hold `Ctrl+Win` or Right-Ctrl) -> `sounddevice` 16 kHz capture with 400 ms pre-roll -> on release: numpy float32 -> `onnx_asr` Parakeet (CUDA EP) [or `faster_whisper` turbo] -> rule-based clean-up -> optional Ollama `qwen3:1.7b` edit (timeout 800 ms, fall back to raw) -> `pyperclip` set + Ctrl+V + clipboard restore -> `pystray` tray + tiny tkinter overlay. Config in a YAML/TOML next to the script.

### Install commands (PowerShell, Python 3.12.4)

```powershell
# Stopgap / benchmark reference
winget install cjpais.Handy

# Project venv
mkdir LocalFlow; cd LocalFlow
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip

# CUDA 12 runtime + cuDNN 9 delivered by pip (no system CUDA toolkit needed)
pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"

# Engine A (primary): Parakeet via ONNX Runtime GPU
pip install "onnx-asr[gpu,hub]"

# Engine B (fallback / multilingual): faster-whisper (CTranslate2 >=4.5 wants CUDA 12 + cuDNN 9)
pip install faster-whisper

# Windows integration
pip install pynput sounddevice numpy pyperclip pystray pillow pyyaml requests

# Clean-up LLM (Ollama already installed)
ollama pull qwen3:1.7b
# optional higher-quality: ollama pull gemma3:4b
setx OLLAMA_KEEP_ALIVE -1
```

Model names: `nemo-parakeet-tdt-0.6b-v2` (English, best dictation), `nemo-parakeet-tdt-0.6b-v3` (25 langs); faster-whisper `turbo` (= large-v3-turbo), `distil-large-v3` / `distil-whisper/distil-large-v3.5-ct2`, `large-v3`; Ollama `qwen3:1.7b`, `gemma3:1b`, `gemma3:4b`, existing `gemma3:12b` for a "polish" hotkey.

Minimal engine calls:

```python
import os, glob, site
# Make pip-delivered CUDA DLLs discoverable (do this BEFORE importing onnxruntime/ctranslate2)
for pkg in ("cuda_runtime", "cublas", "cudnn"):
    for d in glob.glob(os.path.join(site.getsitepackages()[0], "nvidia", pkg, "bin")):
        os.add_dll_directory(d); os.environ["PATH"] = d + ";" + os.environ["PATH"]

import onnx_asr
asr = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
text = asr.recognize(audio_f32_mono, sample_rate=16000)

from faster_whisper import WhisperModel
wm = WhisperModel("turbo", device="cuda", compute_type="float16")
segs, _ = wm.transcribe(audio_f32_mono, language="en", beam_size=1, vad_filter=True,
                        condition_on_previous_text=False)
text = " ".join(s.text.strip() for s in segs)
```

### Known pitfalls

- **cuDNN 9 / `cudnn_ops64_9.dll not found`**: CTranslate2 >= 4.5 (faster-whisper >= 1.1) requires CUDA 12.3+ and cuDNN 9; with torch <= 2.3.1 or cuDNN 8 on PATH you get this error ([faster-whisper #1080](https://github.com/SYSTRAN/faster-whisper/issues/1080), [#1230](https://github.com/SYSTRAN/faster-whisper/issues/1230)). Fix: `nvidia-cudnn-cu12==9.*` from pip and put `site-packages\nvidia\cudnn\bin` (+ `cublas\bin`, `cuda_runtime\bin`) on PATH / `os.add_dll_directory` before import. Do not install torch at all for this tool (not needed by faster-whisper or onnx-asr); if torch is present it must be >= 2.4 to avoid pulling cuDNN 8 DLLs. Alternative: Purfview's prebuilt CUDA DLL zip ([faster-whisper README](https://github.com/SYSTRAN/faster-whisper)).
- **onnxruntime-gpu on Windows** also needs CUDA 12 + cuDNN 9 DLLs on PATH; same pip wheels cover it. If `CUDAExecutionProvider` silently falls back to CPU, check `onnxruntime.get_available_providers()` and `sess.get_providers()` at startup and log it.
- **Python 3.12**: faster-whisper/ctranslate2 (>=3.9), onnx-asr (3.10-3.14), pynput, sounddevice, pystray all ship 3.12 wheels. WhisperWriter (3.11 only) and older `pyaudio` do not. RealtimeSTT explicitly targets 3.11/3.12 and warns 3.13 is unsupported.
- **NeMo is not installable natively on Windows** (WSL2 only) - use onnx-asr / sherpa-onnx ONNX exports instead ([NeMo discussion](https://github.com/NVIDIA-NeMo/NeMo/discussions/6515)).
- **Whisper hallucinations on silence/short clips**: enable `vad_filter=True`, set `no_speech_threshold`, discard utterances < 0.4 s, and set `condition_on_previous_text=False`. Parakeet does not hallucinate on silence but returns empty strings - handle that.
- **Whisper drops fillers, Parakeet keeps them**: tune the rule-based filler regex per engine.
- **First-call latency**: keep models resident in the background process and warm them with a dummy 1 s buffer at start; set `OLLAMA_KEEP_ALIVE=-1` or the LLM adds 1-3 s load time after 5 min idle.
- **pynput `suppress=True` blocks all keys on Windows**; use a chord that does not need suppressing. Hotkey events are not delivered from elevated windows unless the tool itself runs elevated (UIPI).
- **Clipboard paste**: restore the previous clipboard after ~150 ms; some apps (RDP sessions, some terminals, password fields) ignore Ctrl+V - offer per-app fallback to `pynput` `Controller.type()`. Electron apps sometimes drop characters with direct typing, so paste stays the default.
- **VRAM budget**: Parakeet (<1 GB) + whisper turbo fp16 (~1.6 GB) + qwen3:1.7b (~2 GB) + gemma3:12b Q4 (~8 GB) = ~12.5 GB, i.e. do not keep the 12B model resident at the same time as both ASR engines; load one ASR engine at a time.
- **Small-LLM editing failures**: models answer questions inside the dictation or add commentary; guard with strict prompt, `temperature 0`, `/no_think` for Qwen3, strip `<think>`, length sanity check, hard 800 ms timeout with raw-text fallback.
- **Handy GPU on Windows is Vulkan**, init can take 5-30 s and is slower than CUDA int8 CT2; Handy Parakeet is CPU-only - fine for a stopgap, the reason not to stop there.

---

## Sources

- https://github.com/cjpais/Handy and https://github.com/cjpais/Handy/releases and https://handy.computer/docs/post-processing
- https://github.com/OpenWhispr/openwhispr
- https://github.com/EpicenterHQ/epicenter/releases (Whispering v7.11.0)
- https://github.com/drajb/whisper-local and its docs/gpu-setup.md
- https://github.com/savbell/whisper-writer
- https://github.com/KoljaB/RealtimeSTT
- https://github.com/primaprashant/awesome-voice-typing
- https://github.com/SYSTRAN/faster-whisper ; issues #1080, #1230 ; https://opennmt.net/CTranslate2/installation.html
- https://github.com/istupakov/onnx-asr ; https://istupakov.github.io/onnx-asr/usage/ ; https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx
- https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/
- https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026
- https://www.parakeety.com/resources/parakeet-vs-whisper ; https://snailtext.app/blog/whisper-vs-parakeet-tdt/
- https://huggingface.co/distil-whisper/distil-large-v3.5 ; https://huggingface.co/openai/whisper-large-v3-turbo
- https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602 ; https://github.com/ggml-org/llama.cpp/issues/20914
- https://github.com/moses-palmer/pynput/issues/232 ; https://github.com/moses-palmer/pynput/issues/526 ; https://github.com/boppreh/keyboard
- https://lepisma.xyz/journal/2024/09/27/dictation-with-small-llms-and-linguistic-pragmatics/
- https://docs.wisprflow.ai/articles/5373093536-how-do-i-use-smart-formatting-and-backtrack ; https://spokenly.app/blog/wispr-flow-review
