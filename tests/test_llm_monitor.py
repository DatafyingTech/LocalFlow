"""OllamaMonitor state machine: the "llm_ok stayed False forever" bug.

A Windows sign-out kills LocalFlow and Ollama together. If LocalFlow comes back first it used
to decide `llm_ok = False` once, at startup, and never look again - so cleanup stayed
rules-only until somebody restarted the app by hand. These tests drive the transitions with a
fake client and a fake timer, so nothing here needs a real Ollama (or 30 seconds of patience).
"""
from __future__ import annotations

import pytest

from localflow.llm import OllamaMonitor, looks_unreachable


class FakeClient:
    """Stands in for OllamaClient: two switches and a call counter."""

    def __init__(self, up: bool = False, has_model: bool = True):
        self.up = up
        self.model_present = has_model
        self.probes = 0
        self.raise_on_probe = False

    def available(self) -> bool:
        self.probes += 1
        if self.raise_on_probe:
            raise RuntimeError("boom")
        return self.up

    def has_model(self, model=None) -> bool:
        return self.model_present


class FakeTimer:
    """threading.Timer with a hand crank: fire() runs the callback on the calling thread."""

    created: list["FakeTimer"] = []

    def __init__(self, interval, fn):
        self.interval = interval
        self.fn = fn
        self.started = False
        self.cancelled = False
        self.fired = False
        self.daemon = False
        FakeTimer.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fired = True
        self.fn()


@pytest.fixture(autouse=True)
def _clear_timers():
    FakeTimer.created = []
    yield
    FakeTimer.created = []


def make(client, **kw):
    kw.setdefault("interval_s", 30.0)
    kw.setdefault("min_gap_s", 0.0)  # tests probe back-to-back; the rate limit is not under test
    return OllamaMonitor(client, timer_factory=FakeTimer, **kw)


def live_timers():
    return [t for t in FakeTimer.created if t.started and not t.cancelled and not t.fired]


# ---------------------------------------------------------------- startup
def test_start_up_means_ok_and_no_timer():
    m = make(FakeClient(up=True))
    assert m.start() is True
    assert m.ok is True
    assert live_timers() == []  # nothing to watch for


def test_start_down_arms_the_timer():
    m = make(FakeClient(up=False))
    assert m.start() is False
    assert m.ok is False
    assert len(live_timers()) == 1
    assert live_timers()[0].interval == 30.0


def test_ollama_up_but_model_missing_counts_as_down():
    m = make(FakeClient(up=True, has_model=False))
    assert m.start() is False
    assert len(live_timers()) == 1


def test_probe_that_raises_is_down_not_a_crash():
    c = FakeClient(up=True)
    c.raise_on_probe = True
    m = make(c)
    assert m.start() is False


# ---------------------------------------------------------------- recovery
def test_background_timer_recovers_when_ollama_returns(caplog):
    c = FakeClient(up=False)
    back = []
    m = make(c, on_back=lambda: back.append(1))
    m.start()
    t1 = live_timers()[0]

    t1.fire()  # still down: re-arms
    assert m.ok is False
    assert len(live_timers()) == 1 and live_timers()[0] is not t1

    c.up = True
    with caplog.at_level("INFO", logger="localflow.llm"):
        live_timers()[0].fire()
    assert m.ok is True
    assert back == [1], "the warmup callback runs exactly once"
    assert "Ollama is back; cleanup re-enabled" in caplog.text
    assert live_timers() == [], "the timer stops once Ollama is back"


def test_check_recovers_on_demand():
    c = FakeClient(up=False)
    m = make(c)
    m.start()
    assert m.check() is False
    c.up = True
    assert m.check() is True
    assert m.ok is True


def test_check_is_free_once_ok():
    c = FakeClient(up=True)
    m = make(c)
    m.start()
    n = c.probes
    for _ in range(5):
        assert m.check() is True
    assert c.probes == n, "no HTTP traffic while the answer is already yes"


def test_min_gap_rate_limits_probes():
    c = FakeClient(up=False)
    m = make(c, min_gap_s=60.0)
    m.start()
    n = c.probes
    assert m.check() is False
    assert c.probes == n, "a second check inside the gap does not re-probe"
    assert m.check(force=True) is False
    assert c.probes == n + 1, "force bypasses the gap (the background timer uses it)"


# ---------------------------------------------------------------- going down again
def test_mark_down_flips_back_and_restarts_the_timer():
    c = FakeClient(up=True)
    m = make(c)
    m.start()
    assert live_timers() == []

    m.mark_down("error ConnectionError")
    assert m.ok is False
    assert len(live_timers()) == 1, "a failed request restarts the watch"

    c.up = True
    live_timers()[0].fire()
    assert m.ok is True


def test_full_incident_cycle():
    """Sign-out: Ollama dies, LocalFlow restarts first, Ollama comes back a minute later."""
    c = FakeClient(up=False)
    warms = []
    m = make(c, on_back=lambda: warms.append(1))
    assert m.start() is False  # LocalFlow up before Ollama: rules-only

    for _ in range(3):  # a minute and a half of patient re-probing
        live_timers()[0].fire()
        assert m.ok is False

    c.up = True
    live_timers()[0].fire()
    assert m.ok is True and warms == [1]

    m.stop()
    assert live_timers() == []


def test_stop_cancels_a_pending_timer():
    m = make(FakeClient(up=False))
    m.start()
    m.stop()
    assert live_timers() == []


# ---------------------------------------------------------------- reason classification
@pytest.mark.parametrize("reason", [
    "error ConnectionError",
    "error NewConnectionError",
    "http 503",
    "2/3 segments fell back (error ConnectionError; rejected: empty)",
])
def test_unreachable_reasons(reason):
    assert looks_unreachable(reason) is True


@pytest.mark.parametrize("reason", [
    "",
    "ok",
    "timeout",
    "level none",
    "rejected: length 5 vs 80",
    "http 400",
    "error ValueError",
])
def test_reasons_that_do_not_mean_ollama_is_gone(reason):
    assert looks_unreachable(reason) is False
