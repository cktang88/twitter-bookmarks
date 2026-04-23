# twitter-bookmarks

Pull your X bookmarks, follow the links, summarize each one with an LLM, cluster them with a local embedding model, and emit a hierarchical Obsidian-style markdown vault. Tweets that were successfully turned into notes get un-bookmarked from your account at the end.

Two stages, both **idempotent** — rerunning either one picks up where it left off.

## Architecture

```
fetch.py   →  .cache/bookmarks/<id>.json   (raw tweet + classification + articles + media)
organize.py →  notes/<Primary>/<Sub>/*.md  (summary + clustering + wikilinks)  +  X un-bookmark
```

- **Scraping / unbookmarking** — delegated to [`twitter-cli`](https://github.com/public-clis/twitter-cli), which logs in via your browser cookies. No X developer portal, no OAuth, no API keys.
- **Thread detection** — cheap heuristics first, then `gpt-5.4-nano` for the ambiguous ones. Threads are skipped (not rendered, not un-bookmarked).
- **Article extraction** — `trafilatura` with precision mode, falling back to recall mode for short articles (Mastodon posts, changelogs, GitHub issues).
- **Media** — photos are downloaded and embedded in the note; videos/GIFs kept as links.
- **Summarization** — `gpt-5.4-mini` per bookmark.
- **Hierarchy** — `BAAI/bge-small-en-v1.5` embeddings via [`fastembed`](https://github.com/qdrant/fastembed) (ONNX, no GPU), two-level agglomerative clustering on cosine distance, then `gpt-5.4-nano` to label the clusters. No more "Machine Learning" vs "ML" vs "AI/ML" folder duplication.

## Setup

1. **Install deps (uses [uv](https://docs.astral.sh/uv/)):**
   ```bash
   uv sync
   ```
   This pulls in `twitter-cli` too — no separate global install needed. It reads cookies directly from Chrome/Arc/Firefox/Edge/Brave, so just make sure you're logged in to x.com in a supported browser. See [twitter-cli troubleshooting](https://github.com/public-clis/twitter-cli#troubleshooting) for macOS Keychain quirks.

2. **OpenRouter key** — <https://openrouter.ai/keys>.

3. **Config:**
   ```bash
   cp .env.example .env
   # fill in OPENROUTER_API_KEY
   ```

## Run

```bash
# Stage 1: fetch + classify + enrich (safe to interrupt / re-run)
uv run python fetch.py

# Stage 2: summarize + embed + cluster + render + unbookmark
uv run python organize.py
```

Output:

- `notes/index.md` — hierarchical TOC
- `notes/<Primary>/<Sub>/<slug>.md` — one note per bookmark with TL;DR, key points, tags, linked article excerpts, embedded photos, video links, and `[[wikilinks]]` to related notes
- `notes/attachments/<tweet_id>/…` — downloaded photos
- `.cache/bookmarks/<id>.json` — persistent per-bookmark state (source of truth)
- `.cache/raw_bookmarks.json` — safety dump of the raw twitter-cli response, refreshed every fetch
- `.cache/media/<id>/…` — downloaded photos (source)

## Tuning

Edit `.env`:

- `OPENROUTER_SUMMARY_MODEL` / `OPENROUTER_NANO_MODEL` — swap models
- `EMBEDDING_MODEL` — any [fastembed-supported](https://qdrant.github.io/fastembed/examples/Supported_Models/) model, e.g. `nomic-ai/nomic-embed-text-v1.5`, `BAAI/bge-base-en-v1.5`
- `CLUSTER_PRIMARY_THRESHOLD` / `CLUSTER_SUB_THRESHOLD` — lower = more folders, tighter groups
- `MAX_BOOKMARKS` — cap for the twitter-cli fetch (default 800; X returns the most recent 800 anyway)
- `UNBOOKMARK_AFTER_RENDER=0` — keep your X bookmarks in place

Force-redo steps by deleting state:

- `rm -rf notes/` — re-render only (stage 2 regenerates notes/ every run)
- `rm -rf .cache/bookmarks/*.json` — re-classify + re-extract everything
- `.cache/bookmarks/<id>.json` — delete a single field (e.g. `"summary": null`) to force that step to re-run for that bookmark

## Known limits

- X's bookmarks endpoint only surfaces the most recent ~800 bookmarks; if you have more and care about the older ones, use [`twitter-web-exporter`](https://github.com/prinsss/twitter-web-exporter) (browser UserScript) to dump them first, then hand the JSON to `fetch.py`.
- `response_format={"type": "json_object"}` is silently ignored by some OpenRouter providers. We strip fences + regex-extract as a hedge, but if you switch to an exotic model and get crashes, that's the first place to look.
