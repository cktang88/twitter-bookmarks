"""Thin wrapper around the `twitter` CLI (public-clis/twitter-cli).

We only need two commands:
- `twitter bookmarks --max N --json --full-text` → list bookmarks
- `twitter unbookmark <id>`                      → remove a bookmark
"""
from __future__ import annotations

import json
import shutil
import subprocess


class TwitterCliError(RuntimeError):
    pass


def _require_binary() -> None:
    if shutil.which("twitter") is None:
        raise TwitterCliError(
            "`twitter` binary not found on PATH. Run `uv sync` "
            "(or invoke via `uv run python fetch.py`)."
        )


def _run(args: list[str]) -> str:
    _require_binary()
    result = subprocess.run(
        ["twitter", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise TwitterCliError(
            f"twitter {' '.join(args)} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def fetch_bookmarks(max_count: int = 800) -> list[dict]:
    """Return the full list of bookmarked tweet dicts (twitter-cli JSON schema)."""
    raw = _run(["bookmarks", "--max", str(max_count), "--full-text", "--json"])
    payload = json.loads(raw)

    # twitter-cli uses an envelope: { ok: true, data: [...] }
    if isinstance(payload, dict):
        if payload.get("ok") is False:
            err = payload.get("error") or {}
            raise TwitterCliError(f"twitter-cli error: {err.get('code')} — {err.get('message')}")
        data = payload.get("data")
        if isinstance(data, list):
            return data
        raise TwitterCliError(f"Unexpected envelope: {list(payload)[:5]}")

    if isinstance(payload, list):
        return payload

    raise TwitterCliError("Could not parse twitter-cli output as list of tweets")


def unbookmark(tweet_id: str) -> None:
    _run(["unbookmark", str(tweet_id)])
