"""Persistent recently-used file list stored in ~/.argocheck/recents.json."""
from __future__ import annotations

import json
from pathlib import Path

_STORE = Path.home() / ".argocheck" / "recents.json"
_MAX = 10


def load() -> list[str]:
    try:
        data = json.loads(_STORE.read_text())
        # Silently drop paths that no longer exist on disk
        return [p for p in data if isinstance(p, str) and Path(p).exists()]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def add(path: str) -> None:
    recents = load()
    recents = [p for p in recents if p != path]  # deduplicate
    recents.insert(0, path)
    recents = recents[:_MAX]
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(recents, indent=2))


def remove(path: str) -> None:
    recents = [p for p in load() if p != path]
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(recents, indent=2))
