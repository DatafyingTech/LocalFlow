"""A dead GPU context ("CUDA failure 999" after sleep or a driver reset) must not lose a dictation.

The pipeline reloads the speech engine once and transcribes the SAME audio again.
"""
import types

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pynput")
pytest.importorskip("sounddevice")

import localflow.__main__ as lf  # noqa: E402


class FakeEngine:
    def __init__(self, fail: bool, text: str = "hello world"):
        self.fail, self.text, self.loaded, self.unloaded = fail, text, False, False

    def load(self): self.loaded = True
    def warmup(self): return 1.0
    def unload(self): self.unloaded = True
    def on_gpu(self): return True

    def transcribe(self, audio):
        if self.fail:
            raise RuntimeError("[ONNXRuntimeError] : 1 : FAIL : CUDA failure 999: unknown error")
        return self.text


def _app(engine):
    app = lf.App.__new__(lf.App)
    app.engine = engine
    app.cfg = {}
    app.set_state = lambda *a, **k: None
    app.tray = types.SimpleNamespace(notify=lambda *a, **k: None)
    return app


def test_recovers_and_returns_the_text(monkeypatch):
    broken = FakeEngine(fail=True)
    fresh = FakeEngine(fail=False, text="recovered text")
    monkeypatch.setattr(lf, "create_engine", lambda cfg: fresh)
    app = _app(broken)
    raw, ms = app._recover_engine_and_retry(np.zeros(1600, np.float32), RuntimeError("CUDA failure 999"))
    assert raw == "recovered text"
    assert broken.unloaded and fresh.loaded
    assert app.engine is fresh


def test_gives_up_cleanly_when_the_reload_does_not_help(monkeypatch):
    monkeypatch.setattr(lf, "create_engine", lambda cfg: FakeEngine(fail=True))
    app = _app(FakeEngine(fail=True))
    with pytest.raises(lf.ASRError):
        app._recover_engine_and_retry(np.zeros(1600, np.float32), RuntimeError("CUDA failure 999"))
