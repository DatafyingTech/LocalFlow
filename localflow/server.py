"""Phone API: a tiny HTTP server that exposes the dictation pipeline to other devices.

The contract is docs/API.md. Standard library only (http.server); the server binds to
127.0.0.1 and is reached from a phone through `tailscale serve`, which proxies port 80 on the
PC's MagicDNS name to it. Bearer-token auth on /v1/dictate and /v1/warm; /v1/health and / are open.

The server owns no model state. It is given three callables so it is testable without a GPU:

    pipeline(audio, mode, level, app) -> PipelineResult   (see localflow.__main__.run_pipeline)
    status() -> dict   with ready / paused / version / engine / gpu / llm / llm_ok
    lock               the app's pipeline lock (one dictation at a time, never interleaved
                       with the desktop hotkey path)
"""
from __future__ import annotations

import hmac
import html
import io
import json
import logging
import os
import subprocess
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import numpy as np

from . import __version__

log = logging.getLogger("localflow.server")

MAX_BODY = 64 * 1024 * 1024  # bytes; anything larger is refused before it is read
TARGET_SR = 16000
MODES = ("ptt", "handsfree")
LEVELS = ("none", "light", "medium", "high")
TAILSCALE_EXE = r"C:\Program Files\Tailscale\tailscale.exe"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class AudioError(ValueError):
    """Body is not decodable audio (-> 400)."""


# ---------------------------------------------------------------- audio decoding
def decode_wav(body: bytes) -> tuple[np.ndarray, int]:
    """WAV bytes -> (float32 mono in [-1, 1], sample rate). Stereo is averaged to mono."""
    try:
        with wave.open(io.BytesIO(body), "rb") as w:
            ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            frames = w.readframes(n)
    except (wave.Error, EOFError, OSError, ValueError) as e:
        raise AudioError(f"not a valid WAV file ({e})") from e
    if ch not in (1, 2):
        raise AudioError(f"unsupported channel count {ch} (send mono or stereo)")
    if width == 2:
        x = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        x = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        x = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 3:
        b = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    else:
        raise AudioError(f"unsupported sample width {width * 8} bit")
    if ch == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    if sr <= 0:
        raise AudioError("invalid sample rate in WAV header")
    return np.ascontiguousarray(x, dtype=np.float32), int(sr)


def decode_pcm(body: bytes, sr: int, channels: int = 1) -> tuple[np.ndarray, int]:
    """Raw 16-bit little-endian PCM -> (float32 mono, sample rate)."""
    if channels not in (1, 2):
        raise AudioError(f"unsupported channel count {channels} (send mono or stereo)")
    if sr <= 0:
        raise AudioError("X-Sample-Rate must be a positive integer")
    if len(body) < 2 or len(body) % (2 * channels):
        raise AudioError("PCM body length is not a whole number of 16-bit frames")
    x = np.frombuffer(body, dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    return np.ascontiguousarray(x, dtype=np.float32), int(sr)


def _lowpass_taps(cutoff_frac: float, n_taps: int = 65) -> np.ndarray:
    """Windowed-sinc FIR low-pass; `cutoff_frac` is cutoff / input sample rate."""
    m = np.arange(n_taps) - (n_taps - 1) / 2
    h = 2 * cutoff_frac * np.sinc(2 * cutoff_frac * m)
    h *= np.hamming(n_taps)
    return (h / h.sum()).astype(np.float32)


def resample(x: np.ndarray, sr_in: int, sr_out: int = TARGET_SR) -> np.ndarray:
    """Resample mono float32 to `sr_out`. Downsampling is low-pass filtered first (anti-alias),
    then the signal is linearly interpolated onto the output grid. Good enough for speech ASR."""
    if sr_in == sr_out or x.size == 0:
        return x.astype(np.float32, copy=False)
    if sr_out < sr_in:
        x = np.convolve(x, _lowpass_taps(0.45 * sr_out / sr_in), mode="same")
    n_out = int(round(x.size * sr_out / sr_in))
    t_out = np.arange(n_out, dtype=np.float64) * (sr_in / sr_out)
    y = np.interp(t_out, np.arange(x.size, dtype=np.float64), x)
    return np.ascontiguousarray(y, dtype=np.float32)


# ---------------------------------------------------------------- tailscale
def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def tailscale_available(exe: str = TAILSCALE_EXE) -> bool:
    return os.path.isfile(exe)


def tailscale_dnsname(exe: str = TAILSCALE_EXE) -> str | None:
    """This machine's MagicDNS name (Self.DNSName without the trailing dot), or None."""
    try:
        rc, out = _run([exe, "status", "--json"], timeout=10)
        if rc != 0:
            return None
        name = (json.loads(out).get("Self") or {}).get("DNSName") or ""
        return name.rstrip(".") or None
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def tailscale_serve(port: int, exe: str = TAILSCALE_EXE) -> str | None:
    """`tailscale serve --bg --http=80 http://127.0.0.1:<port>`; returns the resulting URL."""
    try:
        rc, out = _run([exe, "serve", "--bg", "--http=80", f"http://127.0.0.1:{port}"])
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("tailscale serve failed: %s", e)
        return None
    if rc != 0:
        log.warning("tailscale serve failed (rc %s): %s", rc, out.strip()[:300])
        return None
    name = tailscale_dnsname(exe)
    return f"http://{name}" if name else None


def tailscale_serve_off(exe: str = TAILSCALE_EXE) -> bool:
    try:
        rc, out = _run([exe, "serve", "--http=80", "off"])
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("tailscale serve off failed: %s", e)
        return False
    if rc != 0:
        log.warning("tailscale serve off failed (rc %s): %s", rc, out.strip()[:300])
    return rc == 0


# ---------------------------------------------------------------- HTTP
_STATUS_HTML = """<!doctype html><meta charset="utf-8"><title>LocalFlow {version}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:40em;margin:3em auto;padding:0 1em;color:#222}}
code{{background:#f2f2f2;padding:.1em .3em;border-radius:3px}}.ok{{color:#1a7f37}}.warn{{color:#b45309}}</style>
<h1>LocalFlow {version}</h1>
<p>Phone access is <b class="ok">on</b>. Engine: <code>{engine}</code> ({gpu}); cleanup model:
<code>{llm}</code>{llm_note}. Status: <b class="{ready_cls}">{ready_txt}</b>.</p>
<h2>Set up the phone</h2>
<ol>
<li>Install Tailscale on the phone and sign in to the same tailnet as this PC.</li>
<li>Install the LocalFlow keyboard and open its settings.</li>
<li>On the PC, open the tray menu &rarr; <b>Phone setup</b> &rarr; <b>Copy setup line</b>, and paste it into
the app (or type the URL <code>{url}</code> and the token shown there).</li>
<li>Tap <b>Test connection</b>; it calls <code>GET /v1/health</code>, which needs no token.</li>
</ol>
<p>The token is never shown on this page. API reference: <code>docs/API.md</code> in the LocalFlow folder.</p>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = f"LocalFlow/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def api(self) -> "DictationServer":
        return self.server.api  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # quiet: one line per dictation is logged by the app
        log.debug("%s %s", self.address_string(), fmt % args)

    # -- helpers
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _error(self, code: int, msg: str) -> None:
        self._json(code, {"error": msg})

    def _authed(self) -> bool:
        token = self.api.token or ""
        got = self.headers.get("Authorization", "")
        if not token or not got.lower().startswith("bearer "):
            return False
        return hmac.compare_digest(got[7:].strip().encode(), token.encode())

    def _drain(self) -> None:
        """Discard an unread body so the connection can be reused (or closed cleanly)."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            self.close_connection = True
            return
        while n > 0:
            chunk = self.rfile.read(min(n, 65536))
            if not chunk:
                break
            n -= len(chunk)

    def _warm(self) -> None:
        """POST /v1/warm: the phone is about to send audio; reload the cleanup model now.

        Mirrors the desktop, which re-warms on hotkey key-down so the reload overlaps with the
        user speaking. Returns immediately; the reload runs in the background on the PC.
        """
        self._drain()
        if not self._authed():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="LocalFlow"')
            body = b'{"error": "missing or wrong token"}'
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        st = self.api.status()
        if not st.get("ready"):
            self._error(503, "PC is not ready yet")
            return
        if st.get("paused"):
            self._error(503, "PC is paused (Pause (free GPU) in the tray menu)")
            return
        info = {}
        if self.api.warm is not None:
            try:
                info = self.api.warm() or {}
            except Exception as e:  # noqa: BLE001
                log.warning("warm callback failed: %s", e)
        self._json(200, {"warming": True, "llm_loaded": bool(info.get("llm_loaded", False)),
                         "ready": True})

    # -- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/":
            st = self.api.status()
            ready = bool(st.get("ready")) and not st.get("paused")
            page = _STATUS_HTML.format(
                version=html.escape(str(st.get("version", __version__))),
                engine=html.escape(str(st.get("engine", "?"))),
                gpu="GPU" if st.get("gpu") else "CPU",
                llm=html.escape(str(st.get("llm", "?"))),
                llm_note="" if st.get("llm_ok") else " (not reachable; rules-only cleanup)",
                ready_cls="ok" if ready else "warn",
                ready_txt="ready" if ready else ("paused (Resume from the tray)" if st.get("paused") else "warming up"),
                url=html.escape(self.api.public_url or f"http://127.0.0.1:{self.api.port}"),
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/v1/health":
            st = self.api.status()
            self._json(200, {
                "ok": True,
                "version": st.get("version", __version__),
                "engine": st.get("engine", ""),
                "gpu": bool(st.get("gpu")),
                "llm": st.get("llm", ""),
                "llm_ok": bool(st.get("llm_ok")),
                "ready": bool(st.get("ready")) and not bool(st.get("paused")),
            })
        else:
            self._error(404, "not found")

    def do_POST(self):
        u = urlsplit(self.path)
        if u.path.rstrip("/") == "/v1/warm":
            self._warm()
            return
        if u.path.rstrip("/") != "/v1/dictate":
            self._drain()
            self._error(404, "not found")
            return
        if not self._authed():
            self._drain()
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="LocalFlow"')
            body = b'{"error": "missing or wrong token"}'
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        q = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        mode = (q.get("mode") or "ptt").lower()
        level = (q.get("level") or "").lower() or None
        app = (q.get("app") or "phone").strip()[:80] or "phone"
        if mode not in MODES:
            self._drain()
            self._error(400, f"mode must be one of {', '.join(MODES)}")
            return
        if level is not None and level not in LEVELS:
            self._drain()
            self._error(400, f"level must be one of {', '.join(LEVELS)}")
            return

        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in ("audio/wav", "audio/x-wav", "audio/wave", "audio/pcm", "audio/l16"):
            self._drain()
            self._error(415, "Content-Type must be audio/wav or audio/pcm")
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self.close_connection = True
            self._error(400, "Content-Length required")
            return
        if length > MAX_BODY:
            self.close_connection = True
            self._error(413, f"body larger than {MAX_BODY // (1024 * 1024)} MB")
            return
        if length <= 0:
            self._error(400, "empty body")
            return

        st = self.api.status()
        if not st.get("ready") or st.get("paused"):
            self._drain()
            self._error(503, "engine paused (Resume from the tray)" if st.get("paused") else "models not loaded yet")
            return

        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            self._error(400, "truncated body")
            return

        try:
            if ctype in ("audio/pcm", "audio/l16"):
                try:
                    sr = int(self.headers.get("X-Sample-Rate") or TARGET_SR)
                    ch = int(self.headers.get("X-Channels") or 1)
                except ValueError as e:
                    raise AudioError("X-Sample-Rate / X-Channels must be integers") from e
                audio, sr = decode_pcm(body, sr, ch)
            else:
                audio, sr = decode_wav(body)
        except AudioError as e:
            self._error(400, str(e))
            return
        del body
        audio = resample(audio, sr, TARGET_SR)
        audio_s = audio.size / TARGET_SR
        if audio_s > self.api.max_seconds:
            self._error(413, f"audio is {audio_s:.0f} s; the maximum is {self.api.max_seconds:.0f} s")
            return

        with self.api.lock:
            st = self.api.status()
            if not st.get("ready") or st.get("paused"):
                self._error(503, "engine paused (Resume from the tray)" if st.get("paused") else "models not loaded yet")
                return
            try:
                res = self.api.pipeline(audio, mode, level, app)
            except Exception as e:
                log.exception("phone dictation failed")
                self._error(500, f"dictation failed: {type(e).__name__}: {e}")
                return
        self._json(200, result_to_json(res))


def result_to_json(res: Any) -> dict:
    """PipelineResult (or any object with the same attributes) -> the API response body."""
    t = dict(getattr(res, "timings", {}) or {})
    return {
        "text": res.text or "",
        "raw": res.raw or "",
        "level": res.level or "",
        "llm_used": bool(res.llm_used),
        "press_enter": bool(res.press_enter),
        "empty": bool(res.empty),
        "audio_s": round(float(res.audio_s or 0.0), 2),
        "timings": {
            "asr_ms": round(float(t.get("asr_ms", 0.0)), 1),
            "rules_ms": round(float(t.get("rules_ms", 0.0)), 2),
            "llm_ms": round(float(t.get("llm_ms", 0.0)), 1),
            "total_ms": round(float(t.get("total_ms", 0.0)), 1),
        },
    }


class _Server(ThreadingHTTPServer):
    # No SO_REUSEADDR: on Windows it would let a second instance bind the port the running app
    # already holds (and quietly steal or share its requests). Fail loudly instead.
    allow_reuse_address = False


class DictationServer:
    """Owns the listening socket and its thread. `start()` returns immediately."""

    def __init__(
        self,
        cfg: dict,
        *,
        pipeline: Callable[[np.ndarray, str, str | None, str], Any],
        status: Callable[[], dict],
        warm: "Callable[[], dict] | None" = None,
        lock: "threading.Lock | None" = None,
        max_seconds: float = 1200.0,
        tailscale_exe: str = TAILSCALE_EXE,
    ):
        self.cfg = cfg
        self.bind = str(cfg.get("bind") or "127.0.0.1")
        port = cfg.get("port")
        self.port = int(8770 if port is None or port == "" else port)  # 0 = let the OS pick (tests)
        self.token = str(cfg.get("token") or "")
        self.use_tailscale = bool(cfg.get("tailscale_serve", True))
        self.tailscale_exe = tailscale_exe
        self.pipeline = pipeline
        self.status = status
        self.warm = warm
        self.lock = lock or threading.Lock()
        self.max_seconds = float(max_seconds)
        self.public_url: str | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._ts_active = False

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = _Server((self.bind, self.port), _Handler)
        httpd.daemon_threads = True
        httpd.api = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = httpd.server_address[1]  # port 0 -> the OS picked one (tests)
        self._thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.1), name="phone-api", daemon=True)
        self._thread.start()
        log.info("phone API listening on http://%s:%d", self.bind, self.port)
        if self.use_tailscale:
            # the tailscale CLI takes ~1 s; never hold up model loading or the hotkeys for it
            threading.Thread(target=self._setup_tailscale, name="tailscale-serve", daemon=True).start()

    def _setup_tailscale(self) -> None:
        if not tailscale_available(self.tailscale_exe):
            log.warning("tailscale.exe not found at %s; phone access is loopback-only", self.tailscale_exe)
            return
        url = tailscale_serve(self.port, self.tailscale_exe)
        if url:
            self.public_url = url
            self._ts_active = True
            log.info("tailscale serve: %s -> http://127.0.0.1:%d (tailnet only)", url, self.port)
        else:
            log.warning("tailscale serve did not come up; the phone cannot reach the PC yet")

    def stop(self, remove_tailscale: bool = True) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            log.info("phone API stopped")
        if remove_tailscale and self._ts_active:
            self._ts_active = False
            self.public_url = None
            threading.Thread(target=tailscale_serve_off, args=(self.tailscale_exe,), name="tailscale-off", daemon=True).start()
