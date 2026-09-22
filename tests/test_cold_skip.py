"""A cold cleanup model must never make the user wait (0.3.1).

From the owner's log, two ways the old policy wasted time and then threw the work away:
  - `llm 10332 ms (medium, skipped)`, `8530`, `7216`, `6277` - the user waited out the timeout
    and still got the rules-only text.
  - `llm 18364 ms (high, used)` for the single word "Okay." - a cold model load, blocking.
Measured on a free GPU with the model warm, the same calls take 251, 346 and 580 ms.

So: ask Ollama whether the model is resident (a few ms, cached for a second or two), and when it
is not, warm it in the background and use the rules-only text for this one.
"""
from __future__ import annotations

import pytest

from localflow.llm import RESIDENT_CACHE_S, OllamaClient, skip_because_cold


class FakeClient:
    """Stands in for OllamaClient: records what the policy asked it to do."""

    def __init__(self, resident):
        self._resident = resident
        self.probes = 0
        self.warms: list[str] = []

    def resident(self, max_age_s: float = RESIDENT_CACHE_S):
        self.probes += 1
        return self._resident

    def warm_now(self, level: str = "light") -> bool:
        self.warms.append(level)
        return True


def test_cold_model_is_skipped_and_warmed():
    c = FakeClient(False)
    assert skip_because_cold(c, level="medium") is True
    assert c.warms == ["medium"], "the next dictation must find the model loaded"


def test_warm_model_is_used_normally():
    c = FakeClient(True)
    assert skip_because_cold(c, level="medium") is False
    assert c.warms == []


def test_skip_when_cold_false_restores_the_old_behaviour():
    c = FakeClient(False)
    assert skip_because_cold(c, level="medium", enabled=False) is False
    assert c.probes == 0, "with the policy off, do not even spend the /api/ps probe"
    assert c.warms == []


def test_an_unknown_answer_does_not_skip():
    """Ollama not answering /api/ps is the monitor's business, not this policy's."""
    c = FakeClient(None)
    assert skip_because_cold(c) is False
    assert c.warms == []


def test_a_broken_client_never_breaks_a_dictation():
    class Boom:
        def resident(self, max_age_s=RESIDENT_CACHE_S):
            raise RuntimeError("no")

    assert skip_because_cold(Boom()) is False


def test_a_failed_warm_still_skips():
    class WarmBoom(FakeClient):
        def warm_now(self, level="light"):
            raise RuntimeError("busy")

    assert skip_because_cold(WarmBoom(False)) is True


def test_the_skip_is_logged_in_plain_words(caplog):
    with caplog.at_level("INFO", logger="localflow.llm"):
        skip_because_cold(FakeClient(False))
    assert "cleanup skipped: model was not loaded (warming it for next time)" in caplog.text


# ---------------------------------------------------------------- the cached probe
def test_resident_is_cached_so_a_segmented_speech_probes_once():
    c = OllamaClient({})
    calls = []

    def fake_is_loaded(model=None):
        calls.append(model)
        return True

    c.is_loaded = fake_is_loaded
    assert c.resident() is True
    for _ in range(10):  # a whole hands-free speech, one call per segment
        assert c.resident() is True
    assert len(calls) == 1


def test_resident_re_probes_once_the_cache_is_stale():
    c = OllamaClient({})
    calls = []
    c.is_loaded = lambda model=None: (calls.append(model), False)[1]
    assert c.resident() is False
    assert c.resident(max_age_s=0.0) is False
    assert len(calls) == 2


def test_forget_resident_drops_the_cache():
    c = OllamaClient({})
    c.is_loaded = lambda model=None: True
    assert c.resident() is True
    c.forget_resident()
    c.is_loaded = lambda model=None: False
    assert c.resident() is False


# ---------------------------------------------------------------- the config key
def test_skip_when_cold_defaults_on_and_can_be_turned_off():
    assert OllamaClient({}).skip_when_cold is True
    assert OllamaClient({"skip_when_cold": False}).skip_when_cold is False


def test_interactive_timeout_is_sized_for_a_warm_model():
    c = OllamaClient({})
    assert c.timeout == pytest.approx(2.5)
    # the per-word allowance and the long-text cap are unchanged
    assert c.timeout_per_word == pytest.approx(0.08)
    assert c.timeout_max == pytest.approx(20.0)
    assert c.handsfree_timeout == pytest.approx(60.0)
