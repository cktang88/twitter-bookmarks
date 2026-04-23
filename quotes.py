"""Resolve quote-tweet chains. A bookmark that quote-tweets another tweet
carries only a stub payload from the bookmarks endpoint (id, text, author).
To get the quoted tweet's media, links, and any further nested quotes we
fetch each one via `twitter tweet <id>` and recurse.

Design notes:
- Recursion is bounded by `MAX_DEPTH` and a `seen` set to guard cycles.
- Each node is enriched like a top-level bookmark: photo download +
  external article extraction — so downstream summarization sees the
  same shape of content everywhere.
- Reruns are idempotent: `resolve_for_bookmark` skips nodes whose
  children are already populated.
"""
from __future__ import annotations

import extract
import media as media_mod
import tcli
from state import ArticleData, MediaItem, QuotedTweet

MAX_DEPTH = 3


def _is_twitter_url(u: str) -> bool:
    return "twitter.com" in u or "x.com" in u


def _tweet_url(tweet: dict) -> str:
    screen = (tweet.get("author") or {}).get("screenName", "i")
    return f"https://twitter.com/{screen}/status/{tweet.get('id')}"


def _quoted_from_full(tweet: dict) -> QuotedTweet:
    """Build a QuotedTweet from a full tweet payload (as returned by
    `twitter tweet <id>`). Does not recurse or enrich — callers do that."""
    author = tweet.get("author") or {}
    return QuotedTweet(
        id=str(tweet["id"]),
        text=str(tweet.get("text") or ""),
        tweet_url=_tweet_url(tweet),
        author_username=str(author.get("screenName") or "unknown"),
        author_name=str(author.get("name") or "Unknown"),
        created_at=str(tweet.get("createdAtISO") or tweet.get("createdAt") or ""),
        lang=str(tweet.get("lang") or ""),
        urls=[u for u in (tweet.get("urls") or []) if u and not _is_twitter_url(u)],
        media=[
            MediaItem(type=str(m.get("type") or ""), url=str(m.get("url") or ""))
            for m in (tweet.get("media") or [])
            if m.get("url")
        ],
    )


def _quoted_from_stub(stub: dict, error: str) -> QuotedTweet:
    """Fallback when we can't fetch the full tweet — keep whatever the
    bookmarks stub told us so the note isn't empty."""
    author = stub.get("author") or {}
    qid = str(stub.get("id") or "")
    return QuotedTweet(
        id=qid,
        text=str(stub.get("text") or ""),
        tweet_url=f"https://twitter.com/{author.get('screenName', 'i')}/status/{qid}" if qid else "",
        author_username=str(author.get("screenName") or "unknown"),
        author_name=str(author.get("name") or "Unknown"),
        fetch_error=error,
    )


def _enrich(q: QuotedTweet) -> None:
    """Download photos and extract article text for this node. Idempotent:
    skips photos with a local_path and urls already in articles."""
    if any(m.type == "photo" and not m.local_path for m in q.media):
        # Namespace by quoted id so different bookmarks sharing a quote dedupe.
        media_mod.download_photos(q.id, q.media)

    done = {a.url for a in q.articles}
    for url in q.urls:
        if url in done:
            continue
        art = extract.fetch_article(url)
        q.articles.append(
            ArticleData(url=art.url, title=art.title, text=art.text, error=art.error)
        )


def _resolve(stub: dict, depth: int, seen: set[str]) -> QuotedTweet | None:
    """Expand one quoted-tweet stub into a fully enriched QuotedTweet,
    recursing into any tweet it itself quotes. Returns None if stub has no id."""
    qid = str(stub.get("id") or "")
    if not qid:
        return None
    if qid in seen:
        return _quoted_from_stub(stub, error="cycle detected")
    if depth > MAX_DEPTH:
        return _quoted_from_stub(stub, error=f"max depth {MAX_DEPTH} exceeded")
    seen.add(qid)

    try:
        full = tcli.fetch_tweet(qid)
    except tcli.TwitterCliError as e:
        return _quoted_from_stub(stub, error=str(e))
    if not full:
        return _quoted_from_stub(stub, error="empty payload")

    node = _quoted_from_full(full)
    _enrich(node)

    nested = full.get("quotedTweet")
    if isinstance(nested, dict):
        child = _resolve(nested, depth + 1, seen)
        if child is not None:
            node.quoted.append(child)
    return node


def resolve_for_bookmark(bm_quoted: list[QuotedTweet], raw_tweet: dict) -> bool:
    """Populate `bm_quoted` in place from the bookmark's raw tweet dict.
    No-op if the bookmark has no quotedTweet stub or the chain is already
    resolved. Returns True if anything changed."""
    stub = raw_tweet.get("quotedTweet")
    if not isinstance(stub, dict) or not stub.get("id"):
        return False
    if bm_quoted:
        # Already resolved in a previous run; don't re-fetch.
        return False

    resolved = _resolve(stub, depth=1, seen=set())
    if resolved is None:
        return False
    bm_quoted.append(resolved)
    return True


# ---------- helpers for downstream consumers ----------

def walk(quoted: list[QuotedTweet]):
    """Yield every QuotedTweet node in the chain, depth-first."""
    for q in quoted:
        yield q
        yield from walk(q.quoted)
