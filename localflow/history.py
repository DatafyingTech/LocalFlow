"""Local-only JSONL history: one line per utterance. Nothing leaves the machine."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .config import resolve_path

log = logging.getLogger("localflow.history")


class History:
    def __init__(self, cfg: dict[str, Any]):
        self.enabled = bool(cfg.get("enabled", True))
        self.path: Path = resolve_path(cfg.get("path") or "history.jsonl")
        self.max_entries = int(cfg.get("max_entries", 5000) or 0)
        self._lock = threading.Lock()
        self._count = 0
        if self.enabled and self.path.exists():
            try:
                with open(self.path, "rb") as f:
                    self._count = sum(1 for _ in f)
            except OSError:
                pass

    def append(self, raw: str, cleaned: str, ms_asr: float, ms_llm: float, **extra: Any) -> None:
        if not self.enabled:
            return
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "raw": raw,
            "cleaned": cleaned,
            "ms_asr": round(ms_asr, 1),
            "ms_llm": round(ms_llm, 1),
        }
        rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                self._count += 1
                if self.max_entries and self._count > self.max_entries * 1.2:
                    self._trim()
            except OSError as e:
                log.warning("history write failed: %s", e)

    def _trim(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        keep = lines[-self.max_entries :]
        self.path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        self._count = len(keep)

    def last(self, n: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines()[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
