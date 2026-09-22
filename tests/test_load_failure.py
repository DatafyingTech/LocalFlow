"""A speech engine that cannot load must not take the app down (0.3.1 BLOCKER).

0.3.0: the owner picked the Whisper engine, pressed Restart LocalFlow, and "it closed and never
came back" - no tray icon behaviour that said anything, no reason anywhere, and `--doctor` knew
nothing about it afterwards. The app has to survive the failure: keep the icon and the dot in
the error state, say what went wrong and what to do, answer /v1/health with ready:false plus a
reason, and record the failure where --doctor can find it.
"""
from __future__ import annotations

import types

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pynput")
pytest.importorskip("sounddevice")

import localflow.__main__ as lf  # noqa: E402
from localflow import config as cfgmod  # noqa: E402


class ExplodingEngine:
    """An engine whose load() raises, like faster-whisper with the hub switched offline."""

    name = "whisper"

    def __init__(self, exc):
        self.exc = exc

    def load(self):
        raise self.exc

    def warmup(self):  # pragma: no cover - never reached
        return 0.0

    def unload(self):
        pass

    def on_gpu(self):
        return False


class FakeTray:
    def __init__(self, *a, **k):
        self.notes: list[tuple[str, str]] = []
        self.states: list[tuple] = []

    def set_state(self, state, tooltip=None):
        self.states.append((state, tooltip))

    def notify(self, msg, title="LocalFlow"):
        self.notes.append((title, msg))

    def run(self, setup=None):  # pragma: no cover - the test drives load_models directly
        pass

    def stop(self):
        pass


class FakePill(FakeTray):
    enabled = True

    def set_state(self, state, text=None):
        self.states.append((state, text))

    def start(self):
        pass

    def hide(self):
        pass

    def set_level(self, level):
        pass

    def set_suppressed(self, on):
        pass


class FakeRecorder:
    sr = 16000
    level = 0.0
    recording = False

    def __init__(self, *a, **k):
        self.on_max_duration = None
        self.on_chunk = None

    def open(self, name=None):
        pass

    def close(self):
        pass


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """A fully constructed App with the hardware and the UI stubbed out."""
    monkeypatch.setattr(lf.audiomod, "Recorder", FakeRecorder)
    monkeypatch.setattr(lf, "Tray", FakeTray)
    monkeypatch.setattr(lf, "FlowBar", lambda *a, **k: FakePill())
    monkeypatch.setattr(lf, "Hotkeys", lambda *a, **k: types.SimpleNamespace(
        start=lambda: None, stop=lambda: None, ptt={"ctrl", "win"}))
    monkeypatch.setattr(lf.winfocus, "FullscreenWatcher", lambda *a, **k: types.SimpleNamespace(
        start=lambda: None, stop=lambda: None, fullscreen=False))
    monkeypatch.setattr(cfgmod, "LAST_ERROR_PATH", tmp_path / "last_error.json")
    monkeypatch.setattr(lf.config, "LAST_ERROR_PATH", tmp_path / "last_error.json")

    cfg = cfgmod.load(tmp_path / "config.yaml")
    cfg["asr"]["engine"] = "whisper"
    cfg["server"]["enabled"] = False
    cfg["history"]["path"] = str(tmp_path / "history.jsonl")
    a = lf.App(cfg, tmp_path / "config.yaml")  # must not raise
    a.llm_mon.start = lambda: False
    return a


OFFLINE = RuntimeError(
    "Cannot find an appropriate cached snapshot folder for the specified revision on the local "
    "disk and outgoing traffic has been disabled."
)


def test_the_app_survives_a_failed_load_and_says_why(app, monkeypatch):
    monkeypatch.setattr(lf, "create_engine", lambda cfg: ExplodingEngine(OFFLINE))

    app.load_models()  # must not raise

    assert app.ready is False
    assert app.load_error, "the reason has to be somewhere the user can reach it"
    assert "Whisper model could not be downloaded" in app.load_error
    assert app.state == "error"
    titles = [t for t, _ in app.tray.notes]
    body = "\n".join(m for _, m in app.tray.notes)
    assert titles, "the user gets a notification, not silence"
    assert "Restart LocalFlow" in body and "cannot transcribe" in body


def test_health_reports_the_error(app, monkeypatch):
    monkeypatch.setattr(lf, "create_engine", lambda cfg: ExplodingEngine(OFFLINE))
    app.load_models()
    st = app.server_status()
    assert st["ready"] is False
    assert st["error"] == app.load_error


def test_doctor_can_read_the_failure_afterwards(app, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "create_engine", lambda cfg: ExplodingEngine(OFFLINE))
    app.load_models()
    rec = cfgmod.load_last_error(tmp_path / "last_error.json")
    assert rec and rec["engine"] == "whisper"
    assert "could not be downloaded" in rec["error"]


def test_an_unexpected_failure_anywhere_in_startup_is_caught(app, monkeypatch):
    """Not just engine.load(): nothing on the setup thread may escape."""
    def boom(cfg):
        raise KeyError("asr")

    monkeypatch.setattr(lf, "create_engine", boom)
    app.load_models()
    assert app.ready is False and app.load_error


class GoodEngine:
    name = "whisper"

    def load(self):
        pass

    def warmup(self):
        return 1.0

    def unload(self):
        pass

    def on_gpu(self):
        return True


def test_a_successful_load_clears_a_recorded_failure(app, monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "create_engine", lambda cfg: ExplodingEngine(OFFLINE))
    app.load_models()
    assert (tmp_path / "last_error.json").exists()

    monkeypatch.setattr(lf, "create_engine", lambda cfg: GoodEngine())
    app.load_models()
    assert app.ready is True and app.load_error == ""
    assert not (tmp_path / "last_error.json").exists()


def test_the_reason_names_the_cause(app):
    assert "no internet" in app._load_failure_reason(OFFLINE)
    assert "not enough disk space" in app._load_failure_reason(OSError("[Errno 28] No space left on device"))
    other = app._load_failure_reason(ValueError("something odd"))
    assert "could not be loaded" in other and "something odd" in other
