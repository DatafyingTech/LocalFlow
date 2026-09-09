"""Unit tests for injection-method selection and the scancode mapping (no keys are sent)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# These tests exercise Windows-only modules (ctypes.wintypes, user32) and the desktop deps
# (pynput, pyperclip). Skip the whole module elsewhere so CI on Linux/macOS stays green.
if sys.platform != "win32":
    pytest.skip("Windows-only (inject/winfocus use user32)", allow_module_level=True)
pytest.importorskip("pynput", reason="desktop dependency not installed")
pytest.importorskip("pyperclip", reason="desktop dependency not installed")

from localflow import inject, winfocus  # noqa: E402
from localflow.config import DEFAULTS  # noqa: E402

# inject.type_apps ships empty (see test_defaults); this is the documented FiveM example, kept
# here because the prefix/substring matching is what these tests are actually about.
TYPE_APPS = ["FiveM"]


def _info(process="", title="", fullscreen=False):
    return winfocus.ForegroundInfo(hwnd=1, title=title, process=process, pid=1, fullscreen=fullscreen)


@pytest.mark.parametrize(
    "process, expected",
    [
        ("FiveM.exe", True),
        ("FiveM_GTAProcess.exe", True),
        ("FiveM_b3788_GTAProcess.exe", True),
        ("fivem_b1234_gtaprocess.exe", True),
        ("notepad.exe", False),
        ("chrome.exe", False),
    ],
)
def test_type_apps_prefix_match(process, expected):
    assert winfocus.matches_app(_info(process=process), TYPE_APPS) is expected


def test_type_apps_title_substring():
    assert winfocus.matches_app(_info(process="code.exe", title="main.py - Visual Studio Code"), ["Visual Studio Code"])
    assert not winfocus.matches_app(_info(process="code.exe", title="main.py"), ["Visual Studio Code"])
    assert not winfocus.matches_app(_info(process="x.exe"), ["", None])


def test_resolve_method_auto():
    apps = TYPE_APPS
    assert inject.resolve_method("auto", apps, info=_info(process="notepad.exe")) == "paste"
    assert inject.resolve_method("auto", apps, info=_info(process="FiveM_b3788_GTAProcess.exe")) == "scancode"
    assert inject.resolve_method("auto", apps, info=_info(process="game.exe", fullscreen=True)) == "scancode"
    assert inject.resolve_method("auto", apps, info=_info(process="notepad.exe"), force_scancode=True) == "scancode"


def test_resolve_method_explicit():
    assert inject.resolve_method("paste", ["FiveM"], info=_info(process="FiveM.exe")) == "paste"
    assert inject.resolve_method("scancode", [], info=_info(process="notepad.exe")) == "scancode"
    assert inject.resolve_method("type", [], info=_info()) == "type"
    assert inject.resolve_method("bogus", [], info=_info()) == "paste"


def test_defaults():
    assert DEFAULTS["inject"]["method"] == "auto"
    # ships empty: fullscreen apps are detected on their own, and a shipped app name would force
    # keystroke typing on users who have never heard of that app
    assert DEFAULTS["inject"]["type_apps"] == []
    assert DEFAULTS["inject"]["scancode_delay_ms"] == 12
    assert DEFAULTS["inject"]["scancode_max_chars"] == 500
    assert DEFAULTS["inject"]["scancode_newlines"] is False
    assert DEFAULTS["ui"]["hide_when_fullscreen"] is False
    assert DEFAULTS["gpu"]["auto_pause_fullscreen"] is False
    assert DEFAULTS["llm"]["keep_alive"] == 600
    assert (DEFAULTS["llm"]["timeout_ms"], DEFAULTS["llm"]["timeout_per_word_ms"], DEFAULTS["llm"]["timeout_max_ms"]) == (6000, 80, 20000)
    assert DEFAULTS["asr"]["gpu_mem_limit_mb"] == 3072


@pytest.mark.skipif(sys.platform != "win32", reason="win32 keyboard layout")
def test_scancode_mapping_ascii():
    # every printable ASCII character has a key (possibly shifted) in a US/most Latin layouts
    for ch in "Hello, World! It's 5pm: test (ok)?":
        sc = inject.scancode_for(ch)
        assert sc is not None, ch
        scan, mods, ext = sc
        assert 0 < scan < 0x80
        assert mods in (0, 1, 2, 4, 6)
    assert inject.scancode_for("H")[1] & 1  # shifted
    assert inject.scancode_for("h")[1] == 0
    assert inject.scancode_for("!")[1] & 1
    assert inject.scancode_for("\U0001F600") is None  # emoji: unicode fallback


def test_utf16_units():
    assert inject._utf16_units("a") == ["a"]
    assert len(inject._utf16_units("\U0001F600")) == 2  # surrogate pair


def test_llm_keep_alive_parsing():
    from localflow.llm import OllamaClient

    assert OllamaClient({"keep_alive": 600}).keep_alive_s() == 600
    assert OllamaClient({"keep_alive": -1}).keep_alive_s() is None
    assert OllamaClient({"keep_alive": "10m"}).keep_alive_s() == 600
    c = OllamaClient({"keep_alive": 600})
    assert c.needs_warm()  # never called
    import time

    c.last_ok = time.monotonic()
    assert not c.needs_warm()
    c.last_ok = time.monotonic() - 590
    assert c.needs_warm()  # within the 30 s margin of unloading
