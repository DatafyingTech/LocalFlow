# LocalFlow phone API (v1)

The desktop app can expose its dictation pipeline to other devices on your Tailscale network.
A phone sends audio, the PC transcribes it with Parakeet on the GPU, cleans it with the local
model, and returns text. Nothing leaves your own devices; the tunnel is WireGuard-encrypted end to
end, so the API itself is plain HTTP.

This document is the contract between `localflow/server.py` (PC) and the Android app (`android/`).
Both are built against it. Change it here first.

## Where it lives

| | |
|---|---|
| Listens on | `127.0.0.1:8770` (never on a LAN or public interface) |
| Reached at | `http://<pc-name>.<tailnet>.ts.net` via `tailscale serve`, which proxies port 80 to 8770 |
| Example | `http://localflow-pc.tail1234.ts.net` (your own tailnet name; `tailscale status` on the PC shows it) |
| Enabled by | `server.enabled: true` in `config.yaml` (off by default) |
| Auth | `Authorization: Bearer <server.token>` on every `/v1/*` call except `/v1/health` |

The token is generated on first enable, stored in `config.yaml`, and shown in the tray menu under
**Phone setup**. Use the MagicDNS name, not the raw 100.x IP: `tailscale serve` routes by
hostname and answers 404 to a bare IP.

## Endpoints

### `GET /`
Human-readable status page (no auth). Shows the version, whether phone access is on, and the setup
steps. Never shows the token.

### `GET /v1/health`
No auth. For the phone's "Test connection" button and for the status page.

```json
{
  "ok": true,
  "version": "0.1.1",
  "engine": "parakeet",
  "gpu": true,
  "llm": "gemma3:4b",
  "llm_ok": true,
  "ready": true
}
```

`ready` is false while models are still loading; the phone should show "PC is warming up" and retry.

### `POST /v1/dictate`
Auth required. Send audio, get text.

**Request body:** a WAV file, 16 kHz, mono, 16-bit PCM, `Content-Type: audio/wav`.
Raw PCM is also accepted as `Content-Type: audio/pcm` with `X-Sample-Rate: 16000` (mono, 16-bit
little-endian). Other rates are resampled by the server. Maximum length is `audio.max_seconds`
(default 1200 s); longer returns 413.

**Query parameters:**

| Name | Values | Default | Meaning |
|---|---|---|---|
| `mode` | `ptt`, `handsfree` | `ptt` | `ptt` = one phrase, cleaned at `cleanup.level`. `handsfree` = a whole speech, cleaned at `cleanup.handsfree_level` in sentence-aligned segments (the same path the PC uses for double-tap). |
| `level` | `none`, `light`, `medium`, `high` | per mode | Override the cleanup level for this call. |
| `app` | free text, max 80 chars | `phone` | Recorded in `history.jsonl` as the `app` field. Send the target app's package name if known. |

**Response 200:**

```json
{
  "text": "Send a report to Sarah and CC me.",
  "raw": "Send a report to Mark. No, no, wait. I meant to say send it to Sarah and CC me.",
  "level": "medium",
  "llm_used": true,
  "press_enter": false,
  "empty": false,
  "audio_s": 5.0,
  "timings": { "asr_ms": 44, "rules_ms": 0.4, "llm_ms": 163, "total_ms": 208 }
}
```

- `text` is what the phone should insert. It may contain newlines (lists, "new paragraph").
- `press_enter` is true when the speaker said "press enter" at the end. The phone may act on it or
  ignore it; the text itself never includes a trailing newline for this.
- `empty` is true, with `text` and `raw` empty, when the audio contained no speech. This is a 200,
  not an error; the phone should return quietly to idle.

**Errors** (JSON body `{"error": "<human readable>"}`):

| Status | When |
|---|---|
| 400 | Body is not decodable audio, or wrong channel count |
| 401 | Missing or wrong token |
| 413 | Audio longer than `audio.max_seconds` |
| 415 | Unsupported `Content-Type` |
| 503 | Models not loaded yet, or the engine is paused ("Pause (free GPU)") |

The server processes one request at a time (it shares the GPU with the desktop hotkey). A second
concurrent request waits; it does not fail.

## What the phone should do with `text`

Insert it at the cursor of the focused text field. If a field has a selection, replace the
selection. Between two consecutive hands-free results, insert a single space unless the previous
text already ends with whitespace or a newline. Do not add a trailing space or newline after a
single push-to-talk result; the user will keep typing.

## Timeouts the phone should use

| Mode | Connect | Read |
|---|---|---|
| `ptt` | 5 s | 30 s |
| `handsfree` | 5 s | 120 s |

A read timeout means the PC is busy (a game on the GPU, a very long speech). The phone should
keep the audio and offer "Retry" rather than discard it.

## Versioning

The path prefix is `/v1`. Fields may be added to responses at any time; existing fields will not
change meaning or be removed within `/v1`.
