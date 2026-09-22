"""Phone API tests (docs/API.md) against a fake pipeline: no GPU, no microphone, no Ollama."""
from __future__ import annotations

import io
import json
import sys
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

np = pytest.importorskip("numpy", reason="numpy is a runtime dependency (requirements.txt)")

from localflow import server as srv  # noqa: E402

TOKEN = "test-token-0123456789abcdefghij"
MAX_SECONDS = 4.0


class FakeResult:
    def __init__(self, **kw):
        self.text = kw.get("text", "")
        self.raw = kw.get("raw", "")
        self.level = kw.get("level", "medium")
        self.llm_used = kw.get("llm_used", False)
        self.press_enter = kw.get("press_enter", False)
        self.empty = kw.get("empty", False)
        self.audio_s = kw.get("audio_s", 0.0)
        self.timings = kw.get("timings", {"asr_ms": 10, "rules_ms": 0.5, "llm_ms": 20, "total_ms": 31})


class Fake:
    """Records what the server handed to the pipeline; answers like the desktop app would."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.state = {"version": "0.1.1", "engine": "parakeet", "gpu": True, "llm": "gemma3:4b", "llm_ok": True,
                      "ready": True, "paused": False, "error": ""}
        self.cleanup = {"level": "medium", "handsfree_level": "high"}

    def status(self):
        return dict(self.state)

    def pipeline(self, audio, mode, level, app):
        self.calls.append((audio, mode, level, app))
        if audio.size < 1600:
            return FakeResult(empty=True, audio_s=round(audio.size / 16000, 2))
        lvl = level or (self.cleanup["handsfree_level"] if mode == "handsfree" else self.cleanup["level"])
        return FakeResult(text="Hello world.", raw="hello world", level=lvl, llm_used=True,
                          audio_s=round(audio.size / 16000, 2))


@pytest.fixture
def api():
    fake = Fake()
    s = srv.DictationServer(
        {"bind": "127.0.0.1", "port": 0, "token": TOKEN, "tailscale_serve": False},
        pipeline=fake.pipeline, status=fake.status, lock=threading.Lock(), max_seconds=MAX_SECONDS,
    )
    s.start()
    try:
        yield s, fake
    finally:
        s.stop()


def _call(s, path, *, method="GET", body=None, headers=None, token=TOKEN):
    h = dict(headers or {})
    if token is not None and method == "POST":
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:{s.port}{path}", data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def wav_bytes(samples: np.ndarray, sr: int, channels: int = 1) -> bytes:
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def tone(seconds: float, sr: int, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


# ---------------------------------------------------------------- health / status page
def test_health_shape(api):
    s, _ = api
    code, body, hdr = _call(s, "/v1/health")
    assert code == 200 and hdr["Content-Type"].startswith("application/json")
    j = json.loads(body)
    assert set(j) == {"ok", "version", "engine", "gpu", "llm", "llm_ok", "ready", "error"}
    assert j["ok"] is True and j["ready"] is True and j["engine"] == "parakeet" and j["gpu"] is True
    assert j["error"] == ""  # a healthy PC reports no failure


def test_health_reports_a_failed_model_load(api):
    """A speech engine that could not load must say so here, not just answer ready:false."""
    s, fake = api
    fake.state["ready"] = False
    fake.state["error"] = "The Whisper model could not be downloaded (no internet)."
    code, body, _ = _call(s, "/v1/health")
    j = json.loads(body)
    assert code == 200 and j["ready"] is False
    assert j["error"] == "The Whisper model could not be downloaded (no internet)."


def test_health_not_ready_and_paused(api):
    s, fake = api
    fake.state["ready"] = False
    assert json.loads(_call(s, "/v1/health")[1])["ready"] is False
    fake.state["ready"], fake.state["paused"] = True, True
    assert json.loads(_call(s, "/v1/health")[1])["ready"] is False


def test_status_page_never_contains_token(api):
    s, _ = api
    code, body, hdr = _call(s, "/")
    assert code == 200 and hdr["Content-Type"].startswith("text/html")
    page = body.decode("utf-8")
    assert "LocalFlow" in page and "Phone setup" in page
    assert TOKEN not in page
    assert TOKEN not in _call(s, "/v1/health")[1].decode()


# ---------------------------------------------------------------- auth
@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN[:-1], TOKEN + "x"])
def test_401_without_or_with_wrong_token(api, token):
    s, fake = api
    body = wav_bytes(tone(1.0, 16000), 16000)
    code, resp, hdr = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"}, token=token)
    assert code == 401
    assert json.loads(resp)["error"]
    assert "Bearer" in hdr.get("WWW-Authenticate", "")
    assert fake.calls == []


# ---------------------------------------------------------------- request validation
def test_415_wrong_content_type(api):
    s, fake = api
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=b"{}", headers={"Content-Type": "application/json"})
    assert code == 415 and "Content-Type" in json.loads(resp)["error"]
    assert fake.calls == []


def test_400_garbage_wav(api):
    s, fake = api
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=b"RIFF this is not audio at all" * 10,
                          headers={"Content-Type": "audio/wav"})
    assert code == 400 and json.loads(resp)["error"]
    assert fake.calls == []


def test_400_bad_mode_and_level(api):
    s, _ = api
    body = wav_bytes(tone(1.0, 16000), 16000)
    assert _call(s, "/v1/dictate?mode=shout", method="POST", body=body, headers={"Content-Type": "audio/wav"})[0] == 400
    assert _call(s, "/v1/dictate?level=extreme", method="POST", body=body, headers={"Content-Type": "audio/wav"})[0] == 400


def test_413_over_max_seconds(api):
    s, fake = api
    body = wav_bytes(tone(MAX_SECONDS + 1, 16000), 16000)
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert code == 413 and "maximum" in json.loads(resp)["error"]
    assert fake.calls == []


def test_413_body_over_cap_is_refused_before_reading(api):
    s, fake = api
    # lie about the size: the server must refuse from the header alone, without reading 64 MB
    hdr = {"Content-Type": "audio/wav", "Content-Length": str(srv.MAX_BODY + 1)}
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=b"RIFF", headers=hdr)
    assert code == 413
    assert fake.calls == []


def test_503_before_ready_and_when_paused(api):
    s, fake = api
    body = wav_bytes(tone(1.0, 16000), 16000)
    fake.state["ready"] = False
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert code == 503 and "not loaded" in json.loads(resp)["error"]
    fake.state["ready"], fake.state["paused"] = True, True
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert code == 503 and "paused" in json.loads(resp)["error"]
    assert fake.calls == []


# ---------------------------------------------------------------- audio decoding
def test_wav_and_raw_pcm_decode_to_the_same_float32(api):
    s, fake = api
    x = tone(1.0, 16000)
    pcm = np.clip(x * 32767, -32768, 32767).astype("<i2").tobytes()
    assert _call(s, "/v1/dictate", method="POST", body=wav_bytes(x, 16000), headers={"Content-Type": "audio/wav"})[0] == 200
    assert _call(s, "/v1/dictate", method="POST", body=pcm,
                 headers={"Content-Type": "audio/pcm", "X-Sample-Rate": "16000"})[0] == 200
    a, b = fake.calls[0][0], fake.calls[1][0]
    assert a.dtype == np.float32 and b.dtype == np.float32
    assert a.shape == b.shape == x.shape
    np.testing.assert_array_equal(a, b)
    assert np.max(np.abs(a - x)) < 1e-4


def test_48k_stereo_wav_is_resampled_to_16k_mono(api):
    s, fake = api
    x = tone(2.0, 48000, hz=300.0)
    stereo = np.stack([x, x], axis=1).reshape(-1)  # interleaved L R
    body = wav_bytes(stereo, 48000, channels=2)
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert code == 200
    audio = fake.calls[0][0]
    assert audio.ndim == 1 and audio.dtype == np.float32
    assert abs(audio.size - 32000) <= 1
    assert json.loads(resp)["audio_s"] == pytest.approx(2.0, abs=0.01)
    # the tone survives: a 300 Hz sine at 16 kHz has ~53.3 samples per cycle; check the peak bin
    spec = np.abs(np.fft.rfft(audio * np.hanning(audio.size)))
    peak_hz = np.argmax(spec) * 16000 / audio.size
    assert abs(peak_hz - 300.0) < 5.0


def test_pcm_other_rate_is_resampled(api):
    s, fake = api
    x = tone(1.0, 44100)
    pcm = np.clip(x * 32767, -32768, 32767).astype("<i2").tobytes()
    code, _, _ = _call(s, "/v1/dictate", method="POST", body=pcm,
                       headers={"Content-Type": "audio/pcm", "X-Sample-Rate": "44100"})
    assert code == 200
    assert abs(fake.calls[0][0].size - 16000) <= 1


def test_resample_helpers_directly():
    x = tone(1.0, 8000)
    y = srv.resample(x, 8000, 16000)
    assert y.size == 16000 and y.dtype == np.float32
    assert srv.resample(x, 8000, 8000) is x or np.array_equal(srv.resample(x, 8000, 8000), x)
    with pytest.raises(srv.AudioError):
        srv.decode_pcm(b"\x00\x01\x02", 16000)
    with pytest.raises(srv.AudioError):
        srv.decode_wav(b"not a wav")


# ---------------------------------------------------------------- modes / response
def test_mode_handsfree_selects_handsfree_level(api):
    s, fake = api
    body = wav_bytes(tone(1.0, 16000), 16000)
    code, resp, _ = _call(s, "/v1/dictate?mode=handsfree&app=com.example.notes", method="POST", body=body,
                          headers={"Content-Type": "audio/wav"})
    assert code == 200
    j = json.loads(resp)
    assert fake.calls[-1][1:] == ("handsfree", None, "com.example.notes")
    assert j["level"] == fake.cleanup["handsfree_level"] == "high"
    # default mode is ptt at cleanup.level, app defaults to "phone"
    _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert fake.calls[-1][1:] == ("ptt", None, "phone")
    # explicit level override is passed through
    code, resp, _ = _call(s, "/v1/dictate?mode=handsfree&level=light", method="POST", body=body,
                          headers={"Content-Type": "audio/wav"})
    assert fake.calls[-1][2] == "light" and json.loads(resp)["level"] == "light"


def test_app_param_is_capped_at_80_chars(api):
    s, fake = api
    body = wav_bytes(tone(1.0, 16000), 16000)
    _call(s, "/v1/dictate?app=" + "a" * 200, method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert len(fake.calls[-1][3]) == 80


def test_response_shape(api):
    s, _ = api
    body = wav_bytes(tone(1.5, 16000), 16000)
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert code == 200
    j = json.loads(resp)
    assert set(j) == {"text", "raw", "level", "llm_used", "press_enter", "empty", "audio_s", "timings"}
    assert set(j["timings"]) == {"asr_ms", "rules_ms", "llm_ms", "total_ms"}
    assert j["text"] == "Hello world." and j["raw"] == "hello world" and j["llm_used"] is True
    assert j["empty"] is False and j["press_enter"] is False and j["audio_s"] == 1.5


def test_empty_result_shape(api):
    s, _ = api
    body = wav_bytes(np.zeros(800, dtype=np.float32), 16000)  # 50 ms: the fake pipeline drops it
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert code == 200
    j = json.loads(resp)
    assert j["empty"] is True and j["text"] == "" and j["raw"] == ""
    assert isinstance(j["timings"]["total_ms"], (int, float))


def test_pipeline_exception_is_a_500_json_error(api):
    s, fake = api

    def boom(audio, mode, level, app):
        raise RuntimeError("engine exploded")

    s.pipeline = boom
    body = wav_bytes(tone(1.0, 16000), 16000)
    code, resp, _ = _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})
    assert code == 500 and "engine exploded" in json.loads(resp)["error"]


def test_dictations_are_serialised_by_the_lock(api):
    s, fake = api
    inside, max_inside, lk = [0], [0], threading.Lock()
    import time

    def slow(audio, mode, level, app):
        with lk:
            inside[0] += 1
            max_inside[0] = max(max_inside[0], inside[0])
        time.sleep(0.15)
        with lk:
            inside[0] -= 1
        return FakeResult(text="x", raw="x", audio_s=1.0)

    s.pipeline = slow
    body = wav_bytes(tone(1.0, 16000), 16000)
    codes = []
    ths = [threading.Thread(target=lambda: codes.append(
        _call(s, "/v1/dictate", method="POST", body=body, headers={"Content-Type": "audio/wav"})[0])) for _ in range(3)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert codes == [200, 200, 200] and max_inside[0] == 1


def test_404_elsewhere(api):
    s, _ = api
    assert _call(s, "/v1/nope")[0] == 404
    assert _call(s, "/v1/nope", method="POST", body=b"x", headers={"Content-Type": "audio/wav"})[0] == 404


# ------------------------------------------------------------------ /v1/warm
def _warm_server(ready=True, paused=False, warm=None):
    import localflow.server as srv
    calls = []

    def _warm():
        calls.append(1)
        return {"llm_loaded": False}

    s = srv.DictationServer(
        {"enabled": True, "port": 0, "token": "tok", "bind": "127.0.0.1", "tailscale_serve": False},
        pipeline=lambda *a, **k: None,
        status=lambda: {"ready": ready, "paused": paused, "version": "t", "engine": "fake",
                        "gpu": False, "llm": None, "llm_ok": False},
        warm=warm if warm is not None else _warm,
    )
    s.start()
    return s, calls


def test_warm_requires_token():
    import urllib.request, urllib.error
    s, calls = _warm_server()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{s.port}/v1/warm", data=b"", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        assert calls == []
    finally:
        s.stop()


def test_warm_calls_back_and_reports_state():
    import json, urllib.request
    s, calls = _warm_server()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{s.port}/v1/warm", data=b"", method="POST",
                                     headers={"Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=5) as r:
            j = json.load(r)
        assert j == {"warming": True, "llm_loaded": False, "ready": True}
        assert calls == [1]
    finally:
        s.stop()


def test_warm_503_when_not_ready_or_paused():
    import urllib.request, urllib.error
    for kw in ({"ready": False}, {"paused": True}):
        s, calls = _warm_server(**kw)
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{s.port}/v1/warm", data=b"", method="POST",
                                         headers={"Authorization": "Bearer tok"})
            try:
                urllib.request.urlopen(req, timeout=5)
                assert False, "expected 503"
            except urllib.error.HTTPError as e:
                assert e.code == 503
            assert calls == []
        finally:
            s.stop()
