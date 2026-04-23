"""Stage 1: fetch bookmarks via twitter-cli, classify threads, download media,
extract linked articles. Writes per-bookmark state under .cache/bookmarks/
so reruns pick up where they left off.

Usage: uv run python fetch.py
"""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

import extract
import llm
import media as media_mod
import tcli
from state import (
    BOOKMARKS_DIR,
    CACHE,
    RAW_BOOKMARKS_FILE,
    ArticleData,
    BookmarkState,
    MediaItem,
)


def _tweet_url(tweet: dict) -> str:
    screen = (tweet.get("author") or {}).get("screenName", "i")
    return f"https://twitter.com/{screen}/status/{tweet.get('id')}"


def _new_state(tweet: dict) -> BookmarkState:
    author = tweet.get("author") or {}
    urls = [
        u
        for u in (tweet.get("urls") or [])
        if u and "twitter.com" not in u and "x.com" not in u
    ]
    return BookmarkState(
        id=str(tweet["id"]),
        text=str(tweet.get("text") or ""),
        tweet_url=_tweet_url(tweet),
        author_username=str(author.get("screenName") or "unknown"),
        author_name=str(author.get("name") or "Unknown"),
        created_at=str(tweet.get("createdAtISO") or tweet.get("createdAt") or ""),
        lang=str(tweet.get("lang") or ""),
        urls=urls,
        media=[
            MediaItem(type=str(m.get("type") or ""), url=str(m.get("url") or ""))
            for m in (tweet.get("media") or [])
            if m.get("url")
        ],
    )


def _merge_raw(tweet: dict, bm: BookmarkState) -> bool:
    """Refresh metadata from a freshly-fetched tweet without clobbering
    enrichment. Returns True if anything changed."""
    changed = False
    fresh = _new_state(tweet)
    for field in ("text", "tweet_url", "author_username", "author_name", "created_at", "lang"):
        if getattr(bm, field) != getattr(fresh, field) and getattr(fresh, field):
            setattr(bm, field, getattr(fresh, field))
            changed = True
    # Merge URLs (preserve order, dedupe)
    existing_urls = set(bm.urls)
    for u in fresh.urls:
        if u not in existing_urls:
            bm.urls.append(u)
            existing_urls.add(u)
            changed = True
    # Merge media entries by URL
    existing_media = {m.url for m in bm.media}
    for m in fresh.media:
        if m.url not in existing_media:
            bm.media.append(m)
            existing_media.add(m.url)
            changed = True
    return changed


def main() -> int:
    load_dotenv()
    if "OPENROUTER_API_KEY" not in os.environ:
        print("error: OPENROUTER_API_KEY not set; copy .env.example to .env", file=sys.stderr)
        return 2

    CACHE.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_DIR.mkdir(parents=True, exist_ok=True)

    max_count = int(os.environ.get("MAX_BOOKMARKS", "800") or "800")
    print(f"Fetching up to {max_count} bookmarks via twitter-cli...")
    try:
        raw_tweets = tcli.fetch_bookmarks(max_count=max_count)
    except tcli.TwitterCliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    RAW_BOOKMARKS_FILE.write_text(
        json.dumps(raw_tweets, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  fetched {len(raw_tweets)} (backup → {RAW_BOOKMARKS_FILE})")

    for i, tweet in enumerate(raw_tweets, 1):
        tid = str(tweet.get("id") or "")
        if not tid:
            continue

        bm = BookmarkState.load(tid)
        created_new = bm is None
        if created_new:
            bm = _new_state(tweet)
        else:
            _merge_raw(tweet, bm)
        bm.save()

        prefix = f"[{i}/{len(raw_tweets)}] @{bm.author_username} · {tid}"

        if bm.unbookmarked:
            print(f"{prefix}  (already processed, skip)")
            continue
        if bm.skip_reason:
            print(f"{prefix}  (skipped: {bm.skip_reason})")
            continue

        # Retweets carry no standalone value — skip.
        if tweet.get("isRetweet"):
            bm.skip_reason = "retweet"
            bm.save()
            print(f"{prefix}  retweet, skipping")
            continue

        print(prefix)

        # 1. Thread classification
        if bm.is_thread is None:
            try:
                is_thread, reason = llm.classify_thread(bm)
            except Exception as e:
                print(f"  thread classify failed: {e}")
                is_thread, reason = False, f"classify error: {e}"
            bm.is_thread = is_thread
            bm.thread_reason = reason
            if is_thread:
                bm.skip_reason = "thread"
            bm.save()
            print(f"  thread? {is_thread} — {reason}")
            if is_thread:
                continue

        # 2. Photo downloads
        if any(m.type == "photo" and not m.local_path for m in bm.media):
            n = sum(1 for m in bm.media if m.type == "photo")
            print(f"  downloading {n} photo(s)...")
            media_mod.download_photos(bm.id, bm.media)
            bm.save()

        # 3. Article extraction (idempotent per URL)
        done_urls = {a.url for a in bm.articles}
        new_urls = [u for u in bm.urls if u not in done_urls]
        for url in new_urls:
            print(f"  extracting {url}")
            art = extract.fetch_article(url)
            bm.articles.append(
                ArticleData(url=art.url, title=art.title, text=art.text, error=art.error)
            )
            bm.save()

    # Summary
    all_states = list(BOOKMARKS_DIR.glob("*.json"))
    print(f"\nStage 1 complete. {len(all_states)} state file(s) in {BOOKMARKS_DIR}/")
    print("Next: uv run python organize.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
