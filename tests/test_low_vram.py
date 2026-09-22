"""Low VRAM mode: the cache check has to know which weights it is looking for.

`asr.parakeet_quantization: int8` used to be unusable on a fresh machine. `model_is_cached()`
answered "yes" as soon as ANY .onnx file existed, `_setup_hf_cache()` then set
HF_HUB_OFFLINE=1, and the int8 weights - different files in the same repo - could never be
fetched ("cached snapshot ... is incomplete: 2 file(s) are missing"). These tests pin the
per-quantization file list and the download-size message.
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")  # localflow.asr imports numpy at module level

from localflow.asr import (  # noqa: E402
    PARAKEET_FILES,
    PARAKEET_VRAM_MB,
    download_size,
    model_is_cached,
    parakeet_quantization,
    required_model_files,
)


def cfg(models_dir, quant=None, engine="parakeet"):
    return {"asr": {"engine": engine, "models_dir": str(models_dir), "parakeet_quantization": quant}}


def put(models_dir, *names):
    """Drop empty files where the Hugging Face cache would put them."""
    snap = models_dir / "hub" / "models--istupakov--parakeet-tdt-0.6b-v2-onnx" / "snapshots" / "abc"
    snap.mkdir(parents=True, exist_ok=True)
    for n in names:
        (snap / n).write_bytes(b"")


# ---------------------------------------------------------------- normalising the key
@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ("none", None),
    ("null", None),
    ("int8", "int8"),
    ("INT8", "int8"),
    ("  int8  ", "int8"),
])
def test_parakeet_quantization_normalises(tmp_path, value, expected):
    assert parakeet_quantization(cfg(tmp_path, value)) == expected


# ---------------------------------------------------------------- which files are needed
def test_required_files_differ_by_quantization(tmp_path):
    assert required_model_files(cfg(tmp_path)) == PARAKEET_FILES[None]
    assert required_model_files(cfg(tmp_path, "int8")) == PARAKEET_FILES["int8"]
    assert "encoder-model.int8.onnx" in PARAKEET_FILES["int8"]
    assert "decoder_joint-model.int8.onnx" in PARAKEET_FILES["int8"]


def test_whisper_does_not_use_the_parakeet_file_list(tmp_path):
    """The Whisper cache is checked by whisper_is_cached(), not by the .onnx file list."""
    assert required_model_files(cfg(tmp_path, engine="whisper")) == ()
    put(tmp_path, "whatever.onnx")
    assert model_is_cached(cfg(tmp_path, engine="whisper")) is False, \
        "0.3.0 bug: a Parakeet cache answered 'yes' for Whisper, so the download was blocked"


def test_a_genuinely_unknown_engine_still_uses_the_loose_check(tmp_path):
    put(tmp_path, "whatever.onnx")
    assert model_is_cached(cfg(tmp_path, engine="something-else")) is True


# ---------------------------------------------------------------- the actual bug
def test_missing_models_dir_is_not_cached(tmp_path):
    assert model_is_cached(cfg(tmp_path / "nope")) is False


def test_full_precision_cache_is_not_an_int8_cache(tmp_path):
    put(tmp_path, *PARAKEET_FILES[None])
    assert model_is_cached(cfg(tmp_path)) is True
    assert model_is_cached(cfg(tmp_path, "int8")) is False, \
        "this is the bug: fp32 weights must not stop the int8 download"


def test_int8_cache_satisfies_int8(tmp_path):
    put(tmp_path, *PARAKEET_FILES["int8"])
    assert model_is_cached(cfg(tmp_path, "int8")) is True


def test_a_half_downloaded_int8_cache_is_not_cached(tmp_path):
    put(tmp_path, "encoder-model.int8.onnx", "vocab.txt")
    assert model_is_cached(cfg(tmp_path, "int8")) is False


def test_both_variants_present(tmp_path):
    put(tmp_path, *PARAKEET_FILES[None], *PARAKEET_FILES["int8"])
    assert model_is_cached(cfg(tmp_path)) is True
    assert model_is_cached(cfg(tmp_path, "int8")) is True


# ---------------------------------------------------------------- what the user is told
def test_download_size_matches_the_variant(tmp_path):
    assert "2.5 GB" in download_size(cfg(tmp_path))
    assert "0.7 GB" in download_size(cfg(tmp_path, "int8"))


def test_measured_vram_numbers_are_the_ones_we_document():
    assert PARAKEET_VRAM_MB[None] == 3019
    assert PARAKEET_VRAM_MB["int8"] == 625
