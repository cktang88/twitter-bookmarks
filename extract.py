"""Fetch external URLs and extract readable article content."""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
import trafilatura

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)


@dataclass
class Article:
    url: str
    title: str
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


def _extract(html: str, url: str, *, precision: bool) -> str:
    return trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=precision,
        favor_recall=not precision,
    ) or ""


def fetch_article(url: str) -> Article:
    timeout = float(os.environ.get("FETCH_TIMEOUT_SECS", "20"))
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": UA},
        ) as client:
            r = client.get(url)
            r.raise_for_status()
            html = r.text
            final_url = str(r.url)
    except Exception as e:
        return Article(url=url, title="", text="", error=f"fetch failed: {e}")

    try:
        extracted = _extract(html, final_url, precision=True)
        # Short content fallback — precision mode drops tiny valid articles
        # (Mastodon posts, changelogs, GitHub issues). Retry with recall mode.
        if len(extracted.strip()) < 200:
            recall = _extract(html, final_url, precision=False)
            if len(recall.strip()) > len(extracted.strip()):
                extracted = recall

        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta else None) or final_url
    except Exception as e:
        return Article(url=final_url, title="", text="", error=f"parse failed: {e}")

    if len(extracted) > 12000:
        extracted = extracted[:12000] + "\n\n[...truncated...]"

    return Article(url=final_url, title=title.strip(), text=extracted.strip())
