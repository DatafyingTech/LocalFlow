"""Unit tests for the rules pass (no GPU, no network)."""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The rules pass itself is pure Python (stdlib + PyYAML). A few tests below also exercise
# localflow.llm (needs `requests`) or the audio helper (needs `numpy`); skip those when only the
# CI dependencies are installed, so `pytest tests/test_cleanup.py` is green without the GPU stack.
def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


needs_requests = pytest.mark.skipif(not _has("requests"), reason="requests not installed (runtime stack absent)")
needs_numpy = pytest.mark.skipif(not _has("numpy"), reason="numpy not installed (runtime stack absent)")

from localflow import cleanup  # noqa: E402
from localflow.config import DEFAULTS  # noqa: E402


@pytest.fixture
def cfg():
    c = copy.deepcopy(DEFAULTS["cleanup"])
    c["dictionary"] = {"local flow": "LocalFlow", "acme": "Acme", "jon": "John"}
    c["snippets"] = {"my signature": "Best regards,\nAlex", "sig short": "-- A"}
    return c


def clean(text, cfg):
    return cleanup.clean(text, cfg)


# ------------------------------------------------------------------ the end-to-end TTS sentence
def test_tts_sentence(cfg):
    raw = "Um so this is a test comma scratch that this is the final test period new line press enter."
    r = clean(raw, cfg)
    assert r.text == "This is the final test."
    assert r.press_enter is True
    assert r.backtracks == 1


# ------------------------------------------------------------------ fillers
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("um so this is it", "So this is it"),
        ("Um, I think, uh, we should go.", "I think we should go."),
        ("this is umm really uhh good", "This is really good"),
        ("Hmm. Let me think.", "Let me think."),
    ],
)
def test_fillers(cfg, raw, expected):
    assert clean(raw, cfg).text == expected


def test_fillers_disabled(cfg):
    cfg["remove_fillers"] = False
    assert "um" in clean("um hello", cfg).text.lower()


def test_filler_not_inside_words(cfg):
    # "um" must not eat "umbrella", "uh" must not eat "uhuru"
    assert clean("bring an umbrella", cfg).text == "Bring an umbrella"


# ------------------------------------------------------------------ spoken punctuation
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("hello comma world period", "Hello, world."),
        ("hello, comma, world, period.", "Hello, world."),  # ASR already added punctuation around the command words
        ("is it done question mark", "Is it done?"),
        ("wow exclamation point", "Wow!"),
        ("first item new line second item", "First item\nSecond item"),
        ("para one new paragraph para two", "Para one\n\nPara two"),
        ("Para one. New line. Para two.", "Para one.\nPara two."),
        ("a semicolon b colon c", "A; b: c"),
        ("wait dot dot dot what", "Wait... what"),
        ("send to bob at sign example dot com", "Send to bob@example dot com"),
        ("fifty percent sign off", "Fifty% off"),
        ("path c backslash users slash alex", "Path c\\users/alex"),
        ("hashtag win and a dash b", "#win and a - b"),
        ("great, um. next", "Great. Next"),
        ("ninety nine percent sign done", "Ninety nine% done"),
    ],
)
def test_spoken_punctuation(cfg, raw, expected):
    assert clean(raw, cfg).text == expected


def test_spoken_punctuation_disabled(cfg):
    cfg["spoken_punctuation"] = False
    assert clean("hello comma world", cfg).text == "Hello comma world"


# ------------------------------------------------------------------ backtrack
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("let's meet at 2, scratch that, 3 pm in the lobby", "3 pm in the lobby"),
        ("so this is a test, scratch that this is the final test.", "This is the final test."),
        ("send it tomorrow. Scratch that. Send it today.", "Send it today."),
        ("the report is due friday. Strike that. Due monday.", "Due monday."),
        ("call bob, no wait, call alice", "Call alice"),
        ("let's meet at two, actually three pm", "Three pm"),  # weak trigger after a comma: drop the clause
        ("Actually, I think we should go.", "I think we should go."),  # sentence-initial weak trigger: drop the word only
        ("I think, I mean, we should leave now", "We should leave now"),
        ("I think I mean we should leave now", "I think we should leave now"),
        ("one two three scratch that", ""),  # scratched everything
    ],
)
def test_backtrack(cfg, raw, expected):
    assert clean(raw, cfg).text == expected


def test_backtrack_disabled(cfg):
    cfg["backtrack"] = False
    assert "scratch that" in clean("a scratch that b", cfg).text.lower()


def test_backtrack_count(cfg):
    r = clean("a, scratch that, b, no wait, c", cfg)
    assert r.text == "C"
    assert r.backtracks == 2


# ------------------------------------------------------------------ repeats
def test_collapse_repeats(cfg):
    assert clean("the the report is is ready", cfg).text == "The report is is ready"  # 'is' allowed to repeat
    assert clean("we we we should go", cfg).text == "We we we should go"  # short function words untouched
    assert clean("send send it", cfg).text == "Send it"


# ------------------------------------------------------------------ dictionary
def test_dictionary_whole_word_case_insensitive(cfg):
    assert clean("open local flow and Local Flow now", cfg).text == "Open LocalFlow and LocalFlow now"
    assert clean("ask jon about acme", cfg).text == "Ask John about Acme"
    # not inside other words
    assert clean("jonathan uses acmeX", cfg).text == "Jonathan uses acmeX"


def test_dictionary_after_punctuation(cfg):
    assert clean("we use acme.", cfg).text == "We use Acme."


# ------------------------------------------------------------------ snippets
def test_snippet_whole_dictation_with_trailing_period(cfg):
    r = clean("My signature.", cfg)
    assert r.text == "Best regards,\nAlex"
    assert r.snippet_fired is True


def test_snippet_mid_sentence(cfg):
    r = clean("thanks and then my signature and done", cfg)
    assert r.text == "Thanks and then Best regards,\nAlex and done"
    assert r.snippet_fired


def test_snippet_not_partial(cfg):
    assert clean("sig shortly", cfg).text == "Sig shortly"
    assert clean("sig short", cfg).text == "-- A"


# ------------------------------------------------------------------ press enter
@pytest.mark.parametrize(
    "raw, expected, enter",
    [
        ("hello world press enter", "Hello world", True),
        ("hello world, press enter.", "Hello world", True),
        ("Hello world. Press Enter.", "Hello world.", True),
        ("hit enter", "", True),
        ("please press enter to continue", "Please press enter to continue", False),  # not trailing
        ("hello world", "Hello world", False),
    ],
)
def test_press_enter(cfg, raw, expected, enter):
    r = clean(raw, cfg)
    assert (r.text, r.press_enter) == (expected, enter)


# ------------------------------------------------------------------ tidy / misc
def test_capitalisation_and_spacing(cfg):
    assert clean("hello .world ,again", cfg).text == "Hello. World, again"
    assert clean("i think i'm fine", cfg).text == "I think I'm fine"
    assert clean("done. next thing? yes! ok", cfg).text == "Done. Next thing? Yes! Ok"


def test_empty_and_whitespace(cfg):
    assert clean("", cfg).text == ""
    assert clean("   ", cfg).text == ""


def test_rules_off_returns_raw(cfg):
    cfg["rules"] = False
    assert clean("um hello comma world", cfg).text == "um hello comma world"


def test_post_llm_applies_dictionary(cfg):
    assert cleanup.post_llm("we love local flow", cfg) == "We love LocalFlow"


def test_post_llm_normalises_quotes_and_newlines(cfg):
    out = cleanup.post_llm("It\u2019s \u201cdone\u201d, \u2018ok\u2019.\n\n\n\nNext line", cfg)
    assert out == "It's \"done\", 'ok'.\n\nNext line"


def test_result_is_dataclass(cfg):
    r = clean("hi", cfg)
    assert isinstance(r, cleanup.CleanResult)
    assert r.text == "Hi" and r.press_enter is False


# ------------------------------------------------------------------ backtrack: extended triggers
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("call bob, no no, call alice", "Call alice"),
        ("call bob no no call alice", "Call alice"),
        ("send it friday. Oops. Send it monday.", "Send it monday."),
        ("the price is fifty, oops, sixty dollars", "Sixty dollars"),
        ("meet at two, I meant three", "Three"),
        ("meet at two, I meant to say three pm", "Three pm"),  # longer trigger wins, no "to say" left over
        ("Meet at two. I meant three.", "Three."),  # after a full stop: drop the previous sentence
        ("we ship tuesday, correction, wednesday", "Wednesday"),
        ("the meeting is at noon, wait no, at one", "At one"),
        ("the meeting is at noon, let me rephrase, it is at one", "It is at one"),
        ("I meant it when I said thanks", "I meant it when I said thanks"),  # sentence-initial: ordinary words
        ("first sentence. second sentence, no no, third sentence", "First sentence. Third sentence"),
    ],
)
def test_backtrack_extended(cfg, raw, expected):
    assert clean(raw, cfg).text == expected


def test_backtrack_defaults_include_new_triggers(cfg):
    for t in ("no no", "oops", "I meant", "I meant to say", "wait no", "let me rephrase", "correction"):
        assert t in cfg["backtrack_strong"]


# ------------------------------------------------------------------ spoken lists
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("I need to get milk, eggs, bread and butter.", "I need to get:\n- milk\n- eggs\n- bread\n- butter"),
        ("Today I need to get milk, eggs and bread. Then go home.", "Today I need to get:\n- milk\n- eggs\n- bread\nThen go home."),
        ("the list is apples, oranges, pears", "The list is:\n- apples\n- oranges\n- pears"),
        ("Please do the following: update the docs, run the tests and ship it", "Please do the following:\n- update the docs\n- run the tests\n- ship it"),
        ("items colon hammer comma nails comma glue", "Items:\n- hammer\n- nails\n- glue"),  # spoken punctuation first
        ("here is the list: back up, wipe, reinstall the OS.", "Here is the list:\n- back up\n- wipe\n- reinstall the OS"),
    ],
)
def test_auto_list(cfg, raw, expected):
    r = clean(raw, cfg)
    assert r.text == expected
    assert r.list_fired is True


@pytest.mark.parametrize(
    "raw",
    [
        "I need to get milk and eggs",  # only two items
        "I went to the store and bought milk, eggs and bread",  # no lead-in
        "I need to get this done, and then go home, and then sleep",  # clauses, not items
        "the following is a long explanation of why the deployment failed on friday afternoon",
    ],
)
def test_auto_list_not_triggered(cfg, raw):
    r = clean(raw, cfg)
    assert "\n- " not in r.text
    assert r.list_fired is False


def test_auto_list_disabled(cfg):
    cfg["auto_lists"] = False
    r = clean("I need to get milk, eggs, bread and butter.", cfg)
    assert r.text == "I need to get milk, eggs, bread and butter."
    assert r.list_fired is False


def test_auto_list_custom_leadin(cfg):
    cfg["list_leadins"] = ["remember to grab"]
    assert clean("remember to grab keys, wallet, phone", cfg).text == "Remember to grab:\n- keys\n- wallet\n- phone"
    assert clean("I need to get milk, eggs, bread", cfg).list_fired is False


# ------------------------------------------------------------------ LLM timeout scaling (no network)
@needs_requests
def test_llm_timeout_scales_with_words():
    from localflow.llm import OllamaClient

    c = OllamaClient({"timeout_ms": 800, "timeout_per_word_ms": 15, "timeout_max_ms": 2500})
    assert c.timeout_for("short text") == pytest.approx(0.8 + 2 * 0.015)
    assert c.timeout_for(" ".join(["word"] * 60)) == pytest.approx(0.8 + 60 * 0.015)  # 1.7 s
    assert c.timeout_for(" ".join(["word"] * 500)) == pytest.approx(2.5)  # capped


@needs_numpy
def test_audio_normalize():
    import numpy as np

    from localflow.audio import normalize

    a = np.array([0.0, 0.05, -0.02, 0.0], dtype=np.float32)
    n = normalize(a, -3.0)
    assert float(np.abs(n).max()) == pytest.approx(10 ** (-3 / 20), abs=1e-4)
    silent = np.full(100, 1e-6, dtype=np.float32)
    assert normalize(silent) is silent  # below min_peak: untouched
    assert normalize(np.zeros(0, dtype=np.float32)).size == 0


# ------------------------------------------------------------------ LLM sanity guard (mocked HTTP, no Ollama)
class _FakeResp:
    ok = True
    status_code = 200

    def __init__(self, content):
        self._c = content

    def json(self):
        return {"message": {"content": self._c}}


def _client_with_output(monkeypatch, content):
    from localflow.llm import OllamaClient

    c = OllamaClient({"timeout_ms": 900})
    monkeypatch.setattr(c.session, "post", lambda *a, **k: _FakeResp(content))
    return c


@needs_requests
def test_llm_sanity_allows_short_output_after_backtrack(monkeypatch):
    inp = "so um send the invoice to Mark no no oops I meant to say send it to Sarah instead and cc me"
    c = _client_with_output(monkeypatch, "Send the invoice to Sarah and cc me.")
    r = c.cleanup(inp, "medium")
    assert r.used is True and r.text == "Send the invoice to Sarah and cc me."


@needs_requests
def test_llm_sanity_still_rejects_short_output_without_backtrack(monkeypatch):
    inp = "so today I want to talk about the quarterly numbers and how the team performed against plan"
    c = _client_with_output(monkeypatch, "Quarterly numbers.")
    r = c.cleanup(inp, "medium")
    assert r.used is False and r.reason.startswith("rejected: length")


@needs_requests
def test_llm_sanity_accepts_bulleted_list(monkeypatch):
    inp = "agenda for today is budget review, hiring update, roadmap and open questions"
    out = "Agenda for today is:\n- Budget review\n- Hiring update\n- Roadmap\n- Open questions"
    c = _client_with_output(monkeypatch, out)
    r = c.cleanup(inp, "medium")
    assert r.used is True and r.text == out


@needs_requests
def test_llm_sanity_accepts_numbered_list(monkeypatch):
    inp = "the steps are back up the data, wipe the drive, reinstall the OS and restore"
    out = "The steps are:\n1. Back up the data\n2. Wipe the drive\n3. Reinstall the OS\n4. Restore"
    c = _client_with_output(monkeypatch, out)
    assert c.cleanup(inp, "medium").used is True


@needs_requests
def test_llm_sanity_rejects_non_list_multiline_much_shorter(monkeypatch):
    inp = "so today I want to talk about the quarterly numbers and how the team performed against plan and what comes next"
    out = "Quarterly numbers and team performance.\nAgainst plan.\nWhat comes next."
    c = _client_with_output(monkeypatch, out)
    r = c.cleanup(inp, "medium")
    assert r.used is False


@needs_requests
def test_llm_sanity_keeps_commentary_guard(monkeypatch):
    inp = "send the report to bob by friday"
    c = _client_with_output(monkeypatch, "Here is the cleaned text: Send the report to Bob by Friday.")
    assert c.cleanup(inp, "medium").used is False


@needs_requests
def test_llm_backtrack_phrases_follow_config():
    from localflow.llm import OllamaClient

    c = OllamaClient({}, backtrack_phrases=["zap that"])
    assert c.backtrack_re.search("one zap that two")
    assert not c.backtrack_re.search("one scratch that two")


def test_clean_defer_lists(cfg):
    raw = "I need to get milk, eggs, bread and butter."
    r = cleanup.clean(raw, cfg, defer_lists=True)
    assert r.list_fired is False and "\n- " not in r.text
    text, fired = cleanup.auto_list(r.text, cfg["list_leadins"])
    assert fired and text == "I need to get:\n- milk\n- eggs\n- bread\n- butter"


def test_auto_list_never_double_formats(cfg):
    llm_out = "The agenda for today is:\n* Budget review\n* Hiring update\n* Roadmap\n* Open questions"
    assert cleanup.auto_list(llm_out, cfg["list_leadins"]) == (llm_out, False)
    dashed = "I need to get:\n- milk\n- eggs\n- bread"
    assert cleanup.auto_list(dashed, cfg["list_leadins"]) == (dashed, False)
    # an LLM output that kept the enumeration inline still gets formatted
    inline = "When I go to the supermarket I need to get milk, eggs, bread, apples and some coffee."
    assert cleanup.auto_list(inline, cfg["list_leadins"])[0] == (
        "When I go to the supermarket I need to get:\n- milk\n- eggs\n- bread\n- apples\n- some coffee"
    )


# ------------------------------------------------------------------ backtrack: LLM fallback path (deferred)
RAW_MARK = "Send a report to Mark. No, no, wait. I meant to say CC. I mean, I meant to say send it to Sarah and CC me."
RAW_KROGER = (
    "Let's go ahead and send no, I mean to say let's get a shopping list together for Kroger and in the list "
    "let's have potatoes, potato chips, cream cheese, lasagna, and spaghetti noodles."
)


def test_defer_backtrack_leaves_triggers_for_llm(cfg):
    r = cleanup.clean(RAW_MARK, cfg, defer_lists=True, defer_backtrack=True)
    assert "I meant to say" in r.text and r.backtracks == 0


def test_fallback_backtrack_mark(cfg):
    r = cleanup.clean(RAW_MARK, cfg, defer_lists=True, defer_backtrack=True)
    text, n = cleanup.apply_backtrack(r.text, cfg)
    assert text == "Send it to Sarah and CC me."
    assert n == 2


def test_fallback_backtrack_kroger(cfg):
    r = cleanup.clean(RAW_KROGER, cfg, defer_lists=True, defer_backtrack=True)
    text, n = cleanup.apply_backtrack(r.text, cfg)
    assert text == (
        "Let's get a shopping list together for Kroger and in the list let's have potatoes, potato chips, "
        "cream cheese, lasagna, and spaghetti noodles."
    )
    assert n == 1
    assert not text.lower().startswith("to say")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("send it to bob, no, I mean to say send it to alice", "Send it to alice"),  # abort-word clause skipped too
        ("send it to bob no I mean to say send it to alice", "Send it to alice"),
        ("call bob, no wait, actually call alice", "Call alice"),  # consecutive triggers merged
        ("I mean to say hello", "Hello"),  # trigger opening the dictation: nothing to drop
    ],
)
def test_backtrack_new_triggers(cfg, raw, expected):
    assert clean(raw, cfg).text == expected


def test_apply_backtrack_disabled(cfg):
    cfg["backtrack"] = False
    assert cleanup.apply_backtrack("a scratch that b", cfg) == ("a scratch that b", 0)

def test_spoken_punctuation_does_not_double_up(cfg):
    # Parakeet often punctuates the sentence itself, so a spoken "question mark" on the end
    # of an already-terminated sentence used to produce "Friday??".
    assert clean("hey Sarah comma can you send me that report by Friday question mark",
                 cfg).text == "Hey Sarah, can you send me that report by Friday?"
    assert clean("Are we done? question mark", cfg).text == "Are we done?"
    assert clean("this is a test period", cfg).text == "This is a test."
