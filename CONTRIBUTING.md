# Contributing to LocalFlow

Thanks for taking a look. LocalFlow is a small, dependency-pinned Windows app; PRs that keep it
small and pinned are the easiest to merge.

## What you need, depending on what you want to change

| You want to work on | You need |
|---|---|
| `cleanup.py` and its tests (most PRs) | Any OS, any Python 3.11-3.13, `pyyaml` + `pytest`. No GPU, no audio, no Windows. |
| Everything else - audio, hotkeys, injection, ASR, the UI | **Windows 10/11 plus an NVIDIA GPU.** |

The rules pass is deliberately pure Python so anyone can contribute to it from anywhere, and CI
runs exactly that subset. The rest of the app is Windows-only by design: it uses Win32 hotkeys,
Win32 synthetic input and foreground-window queries, and the speech model runs on ONNX Runtime's
CUDA provider. There is no macOS or Linux build and no AMD/Intel GPU path yet - see the wish list
at the bottom of the README if you want to add one.

## Dev setup

```powershell
git clone <your fork>
cd LocalFlow
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1   # or double-click install.bat
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`install.ps1` is idempotent, so re-run it any time.

- `-SkipOllama` skips the ~3.3 GB cleanup-model pull. Safe: the rules pass covers everything the
  tests check.
- `-CPU` installs `requirements-cpu.txt` instead: the same pins minus the CUDA wheels, with the
  plain `onnxruntime`. About 2 GB less to download, and useful for checking the no-GPU path.
- `-SkipSmoke` skips the 2.5 GB speech-model download and the ASR self-test. The app will then
  download the model itself the first time you run it (it shows a "downloading speech model"
  notification while it does), so this is fine for iterating on the rules pass or the UI. Do not
  use it when you are testing ASR, and do not use it for the run you hand to a user.

Only want to work on the rules pass? You do not need a GPU, audio, or Windows at all:

```bash
python -m venv .venv && .venv/bin/pip install pyyaml pytest
.venv/bin/python -m pytest tests/test_cleanup.py
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests          # everything
.\.venv\Scripts\python.exe -m pytest -q tests\test_cleanup.py   # pure rules, no GPU/audio needed
.\.venv\Scripts\python.exe -m tests.smoke_asr          # loads the ASR model, prints providers + timings
.\.venv\Scripts\python.exe -m tests.smoke_asr --llm    # also times the Ollama cleanup pass
.\.venv\Scripts\python.exe -m tests.notepad_scancode   # opens Notepad, round-trips scan-code typing
.\.venv\Scripts\python.exe -m localflow --doctor       # environment diagnostic
```

`tests/test_cleanup.py` is the CI gate: it must keep running with nothing installed but
`pyyaml` and `pytest`. Do not import GPU, audio or Windows modules from `localflow/cleanup.py`
or `localflow/config.py`.

## Code layout

| File | Responsibility |
|---|---|
| `localflow/__main__.py` | wiring, state machine, worker queue, CLI (`--debug`, `--version`, `--doctor`) |
| `localflow/config.py` | YAML defaults, load/save, first-run seeding from `config.example.yaml` |
| `localflow/hotkeys.py` | pynput global hotkeys (never `suppress=True` on Windows) |
| `localflow/audio.py` | sounddevice capture, pre-roll ring buffer, hands-free VAD chunking |
| `localflow/asr.py` | Parakeet (onnx-asr/ONNX Runtime CUDA) and Whisper engines |
| `localflow/cleanup.py` | deterministic rules pass - pure Python, fully unit tested |
| `localflow/llm.py` | optional Ollama pass; every failure path returns the input unchanged |
| `localflow/inject.py` | clipboard paste / scan-code typing |
| `localflow/winfocus.py` | read-only foreground-window and fullscreen detection |
| `localflow/ui.py` | tray icon + Flow Dot overlay |
| `localflow/history.py` | local JSONL history |
| `localflow/doctor.py` | `--doctor` diagnostics |

## What a good PR looks like

- One focused change with a title that says what it does.
- New behaviour in `cleanup.py` comes with cases in `tests/test_cleanup.py`.
- New dependencies are pinned in `requirements.txt` (exact `==` version) and justified in the PR.
  **Never add torch** - it ships its own cuDNN and breaks the ONNX Runtime CUDA provider.
- Any LLM/ASR failure path must still paste something. Never lose the user's speech.
- Nothing leaves the machine: no telemetry, no network calls beyond localhost Ollama and the
  one-time model download.
- Says how you tested it: `pytest tests`, and `smoke_asr` output if you touched ASR.
- README/`config.example.yaml` updated when you add or rename a config key.

Bug reports and feature requests go through the issue templates - the bug template asks for the
`--doctor` output, which answers most questions before anyone has to.
