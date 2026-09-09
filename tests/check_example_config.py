"""Check that config.example.yaml stays in sync with localflow/config.py DEFAULTS.

Run:  python -m tests.check_example_config
Needs only PyYAML. Exits 1 (and lists the offending keys) when the shipped example is missing
keys that the app defines, or defines keys the app does not know about.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from localflow.config import DEFAULTS, EXAMPLE_PATH  # noqa: E402


def flatten(d: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for k, v in d.items():
        path = f"{prefix}{k}"
        keys.add(path)
        # only descend into structural sections, not user data maps (dictionary, snippets, per_app)
        if isinstance(v, dict) and path not in ("cleanup.dictionary", "cleanup.snippets", "cleanup.per_app"):
            keys |= flatten(v, path + ".")
    return keys


def main() -> int:
    if not EXAMPLE_PATH.is_file():
        print(f"FAIL: {EXAMPLE_PATH.name} is missing")
        return 1
    data = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print(f"FAIL: {EXAMPLE_PATH.name} is not a YAML mapping")
        return 1

    want, have = flatten(DEFAULTS), flatten(data)
    missing, extra = sorted(want - have), sorted(have - want)
    if missing:
        print("FAIL: config.example.yaml is missing keys:  " + ", ".join(missing))
    if extra:
        print("FAIL: config.example.yaml has unknown keys:  " + ", ".join(extra))
    if missing or extra:
        return 1
    print(f"OK: config.example.yaml covers all {len(want)} config keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
