"""Download photos attached to a tweet. Videos are kept as URLs only."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from state import MediaItem, MEDIA_DIR

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _guess_ext(url: str) -> str:
    path = urlparse(url).path
    if "." in path:
        ext = "." + path.rsplit(".", 1)[-1].lower()
        if ext in _PHOTO_EXTS:
            return ext
    return ".jpg"


def download_photos(bookmark_id: str, items: list[MediaItem]) -> None:
    """Mutates each photo item in-place to set `local_path`. Idempotent."""
    photos = [m for m in items if m.type == "photo"]
    if not photos:
        return

    folder = MEDIA_DIR / bookmark_id
    folder.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for i, m in enumerate(photos):
            if m.local_path and Path(m.local_path).exists():
                continue
            target = folder / f"{i}{_guess_ext(m.url)}"
            try:
                r = client.get(m.url)
                r.raise_for_status()
                target.write_bytes(r.content)
                m.local_path = str(target)
            except Exception as e:
                print(f"  photo download failed ({m.url}): {e}")
