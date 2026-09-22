"""Choosing the Whisper engine must not be fatal (0.3.0 BLOCKER).

`model_is_cached()` only knew about Parakeet: for any other engine it answered "yes" as soon as
any .onnx file existed under models_dir, which a Parakeet cache always satisfies.
`_setup_hf_cache()` then set HF_HUB_OFFLINE=1, faster-whisper could never fetch its weights, and
the app died during model load with LocalEntryNotFoundError / OfflineModeIsEnabled - which the
user saw as "it closed and never came back".

These tests pin the engine-aware cache check, the repo the check looks for, and the download
size in the one-line message the user gets.
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")  # localflow.asr imports numpy at module level

from localflow.asr import (  # noqa: E402
    WHISPER_DOWNLOAD_GB,
    WHISPER_REPOS,
    WHISPER_WEIGHT_FILE,
    download_size,
    engine_name,
    model_is_cached,
    whisper_is_cached,
    whisper_model_repo,
)

TURBO_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"


def cfg(models_dir, model="turbo", engine="whisper"):
    return {"asr": {"engine": engine, "models_dir": str(models_dir), "whisper_model": model}}


def put_whisper(models_dir, repo=TURBO_REPO, *, weight_bytes=8 * 1024 * 1024, name=WHISPER_WEIGHT_FILE):
    """Lay out a snapshot the way huggingface_hub does under <models_dir>/hub."""
    snap = models_dir / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots" / "deadbeef"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    if weight_bytes is not None:
        (snap / name).write_bytes(b"\0" * weight_bytes)
    return snap


def put_parakeet(models_dir):
    snap = models_dir / "hub" / "models--istupakov--parakeet-tdt-0.6b-v2-onnx" / "snapshots" / "abc"
    snap.mkdir(parents=True, exist_ok=True)
    for n in ("encoder-model.onnx", "decoder_joint-model.onnx", "vocab.txt"):
        (snap / n).write_bytes(b"")


# ---------------------------------------------------------------- the repo id
def test_turbo_maps_to_the_repo_faster_whisper_asks_for():
    assert WHISPER_REPOS["turbo"] == TURBO_REPO
    assert WHISPER_REPOS["large-v3-turbo"] == TURBO_REPO


def test_repo_is_derived_from_the_configured_model(tmp_path):
    assert whisper_model_repo(cfg(tmp_path, "turbo")) == TURBO_REPO
    assert whisper_model_repo(cfg(tmp_path, "small")) == "Systran/faster-whisper-small"
    # an explicit repo id is passed straight through
    assert whisper_model_repo(cfg(tmp_path, "someone/their-ct2-model")) == "someone/their-ct2-model"
    assert whisper_model_repo(cfg(tmp_path, "not-a-model")) == ""


def test_engine_name_defaults_to_parakeet(tmp_path):
    assert engine_name({"asr": {}}) == "parakeet"
    assert engine_name({"asr": {"engine": "WHISPER"}}) == "whisper"


# ---------------------------------------------------------------- the actual bug
def test_a_parakeet_cache_is_not_a_whisper_cache(tmp_path):
    put_parakeet(tmp_path)
    assert model_is_cached({"asr": {"engine": "parakeet", "models_dir": str(tmp_path),
                                    "parakeet_quantization": None}}) is True
    assert model_is_cached(cfg(tmp_path)) is False, \
        "this is the blocker: Parakeet's files must not switch the hub offline for Whisper"


def test_a_real_whisper_snapshot_is_cached(tmp_path):
    put_whisper(tmp_path)
    assert whisper_is_cached(cfg(tmp_path)) is True
    assert model_is_cached(cfg(tmp_path)) is True


def test_another_whisper_model_does_not_count(tmp_path):
    put_whisper(tmp_path)  # turbo
    assert model_is_cached(cfg(tmp_path, "small")) is False


def test_an_interrupted_download_is_not_cached(tmp_path):
    """JSON files and an `.incomplete` blob, but no model.bin: still needs the network."""
    put_whisper(tmp_path, weight_bytes=None)
    (tmp_path / "hub" / ("models--" + TURBO_REPO.replace("/", "--")) / "blobs").mkdir(parents=True)
    (tmp_path / "hub" / ("models--" + TURBO_REPO.replace("/", "--")) / "blobs" / "abc.incomplete").write_bytes(b"x" * 99)
    assert whisper_is_cached(cfg(tmp_path)) is False


def test_an_empty_placeholder_model_bin_is_not_cached(tmp_path):
    put_whisper(tmp_path, weight_bytes=16)
    assert whisper_is_cached(cfg(tmp_path)) is False


def test_missing_models_dir_is_not_cached(tmp_path):
    assert model_is_cached(cfg(tmp_path / "nope")) is False


def test_an_unknown_model_name_is_never_reported_as_cached(tmp_path):
    put_whisper(tmp_path)
    assert model_is_cached(cfg(tmp_path, "not-a-model")) is False


# ---------------------------------------------------------------- what the user is told
def test_download_size_is_whisper_s_own_size(tmp_path):
    assert download_size(cfg(tmp_path)) == WHISPER_DOWNLOAD_GB["turbo"] == "about 1.5 GB"
    assert download_size(cfg(tmp_path, "small")) == "about 0.5 GB"
    # not the Parakeet number, which is what the old code would have said
    assert "2.5 GB" not in download_size(cfg(tmp_path))
