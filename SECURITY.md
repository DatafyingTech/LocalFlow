# Security policy

LocalFlow is a small, offline Windows app maintained by [Datafying Tech](https://datafying.tech).
We take reports seriously and will answer every one, even the ones that turn out to be nothing.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private reporting instead:

1. Go to the [Security tab](https://github.com/DatafyingTech/LocalFlow/security/advisories/new).
2. Click **Report a vulnerability**.
3. Tell us what you found, how to reproduce it, and what an attacker could do with it.

If that form is not available to you, email **kevin@datafying.tech** with `LocalFlow security` in
the subject line.

Please include:

- LocalFlow version (`.\.venv\Scripts\python.exe -m localflow --version`)
- Windows version, and whether you run LocalFlow as administrator
- Steps to reproduce, and a proof of concept if you have one
- Your `--doctor` output if it is relevant. It contains no dictated text, but it does list your
  audio device names, so check them before posting.

**What to expect.** We aim to acknowledge a report within 3 business days and to have a fix or a
clear plan within 30 days. We will credit you in the release notes unless you would rather we did
not. This is a free project with no bug bounty, but we will not be difficult about disclosure: tell
us first, give us a reasonable window, and publish whatever you like afterwards.

## Scope

**In scope:**

- Code execution, privilege escalation, or file overwrite triggered by a crafted `config.yaml`,
  `history.jsonl`, or a model file in `models/`
- Anything that makes LocalFlow send audio, transcripts, or telemetry off the machine
- Clipboard or keystroke handling that leaks dictated text to another process beyond the normal
  paste into the focused window
- Writing dictated text somewhere the user was not told about (anything outside `history.jsonl`
  and `localflow.log`)
- Dependency vulnerabilities that are actually reachable from LocalFlow's code paths
- Anything in `install.ps1` that could be hijacked to run untrusted code during install

**Out of scope:**

- Attacks that require an attacker who already has code execution on the machine as your user.
  Such an attacker can already read your clipboard and your keystrokes; LocalFlow cannot defend
  against that and does not try to.
- The fact that `history.jsonl` is plain text on disk. That is documented, on purpose, and you can
  turn it off with `history.enabled: false`.
- Antivirus or SmartScreen warnings about the global hotkey listener or synthetic keystrokes. That
  behaviour is the app working as designed and is explained in the README.
- Anti-cheat systems reacting to synthetic keystrokes.
- Vulnerabilities in Ollama, Python, or the NVIDIA driver. Report those upstream.
- Reports produced only by an automated scanner, with no working path through LocalFlow's code.

## A note on the attack surface

**LocalFlow takes no network input.** It opens no listening socket, has no server, no update
check, and no telemetry. The only outbound requests it ever makes are to `http://127.0.0.1:11434`
(your own Ollama, on loopback) and, exactly once, to Hugging Face to download the speech model if
it is not already in `models/`. After that the app sets `HF_HUB_OFFLINE=1` on itself and runs with
no network at all.

That means there is no remote attack surface to speak of. The realistic threats are local: a
malicious `config.yaml`, a tampered model file, or a supply-chain problem in a pinned dependency.
Those are the ones we care most about hearing.
