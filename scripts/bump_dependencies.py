#!/usr/bin/env python3
"""Bump every exact-pinned dependency in pyproject.toml to its latest PyPI release.

Only rewrites the version pins themselves (stdlib regex over the raw text, no
TOML round-tripping) so formatting/comments in pyproject.toml are untouched.
Run this, then regenerate the lock files (see DEV.md) before testing/committing.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
PIN_RE = re.compile(r'"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)"')


def latest_version(name: str) -> str:
    url = f"https://pypi.org/pypi/{name}/json"
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.load(r)
    return data["info"]["version"]


def main() -> int:
    text = PYPROJECT.read_text()
    changed: list[str] = []
    errors: list[str] = []

    def repl(match: re.Match) -> str:
        name, pinned = match.group(1), match.group(2)
        try:
            latest = latest_version(name)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
            errors.append(f"{name}: could not check latest version ({e})")
            return match.group(0)
        if latest != pinned:
            changed.append(f"{name}: {pinned} -> {latest}")
            return f'"{name}=={latest}"'
        return match.group(0)

    new_text = PIN_RE.sub(repl, text)

    if errors:
        for line in errors:
            print(f"ERROR: {line}", file=sys.stderr)
        return 1

    if changed:
        PYPROJECT.write_text(new_text)
        print("Updated:")
        for line in changed:
            print(f"  {line}")
    else:
        print("All pinned dependencies already at latest.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
