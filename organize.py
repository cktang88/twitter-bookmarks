"""Stage 2: summarize each bookmark, embed, cluster into primary/sub-topics,
render the Obsidian vault, and un-bookmark anything successfully rendered.

Idempotent: re-running picks up where it left off and re-clusters as the
corpus grows. Notes/ is regenerated each run (attachments/ preserved).

Usage: uv run python organize.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

import embed as embed_mod
import llm
import render
import tcli
from state import BookmarkState, load_all


def _active(bookmarks: list[BookmarkState]) -> list[BookmarkState]:
    return [b for b in bookmarks if not b.skip_reason]


def _summarize_all(bookmarks: list[BookmarkState]) -> None:
    needs = [b for b in bookmarks if not b.summary]
    if not needs:
        return
    print(f"Summarizing {len(needs)} bookmark(s)...")
    for i, bm in enumerate(needs, 1):
        print(f"  [{i}/{len(needs)}] @{bm.author_username} · {bm.id}")
        try:
            summary = llm.summarize(bm)
            bm.summary = summary.model_dump()
            bm.save()
        except Exception as e:
            print(f"    summarize failed: {e}")
            bm.skip_reason = f"error: summarize failed ({type(e).__name__})"
            bm.save()


def _embed_all(bookmarks: list[BookmarkState]) -> None:
    needs = [b for b in bookmarks if not b.embedding]
    if not needs:
        return
    print(f"Embedding {len(needs)} bookmark(s)...")
    texts = [embed_mod.content_for_embed(b) for b in needs]
    vectors = embed_mod.embed_texts(texts)
    for bm, v in zip(needs, vectors):
        bm.embedding = v.tolist()
        bm.save()


def _cluster_and_label(bookmarks: list[BookmarkState]) -> None:
    if not bookmarks:
        return
    embeddings = np.array([b.embedding for b in bookmarks], dtype=np.float32)
    primary_labels, sub_labels = embed_mod.hierarchical_cluster(embeddings)

    primary_names: dict[int, str] = {}
    for pc in sorted(set(primary_labels.tolist())):
        samples = embed_mod.representative_summaries(bookmarks, primary_labels, pc)
        try:
            name = llm.label_cluster(samples, granularity="broad")
        except Exception as e:
            print(f"  primary cluster {pc} label failed: {e}")
            name = f"Cluster {pc}"
        primary_names[pc] = name
        print(f"  primary[{pc}] n={(primary_labels == pc).sum():>3} → {name}")

    sub_names: dict[int, str] = {}
    for sc in sorted(set(sub_labels.tolist())):
        samples = embed_mod.representative_summaries(bookmarks, sub_labels, sc)
        try:
            name = llm.label_cluster(samples, granularity="specific")
        except Exception as e:
            print(f"  sub cluster {sc} label failed: {e}")
            name = f"Sub {sc}"
        sub_names[sc] = name

    for bm, pc, sc in zip(bookmarks, primary_labels, sub_labels):
        bm.primary_topic = primary_names[int(pc)]
        bm.sub_topic = sub_names[int(sc)]
        bm.save()


def _unbookmark_rendered(bookmarks: list[BookmarkState]) -> None:
    if os.environ.get("UNBOOKMARK_AFTER_RENDER", "1") != "1":
        print("UNBOOKMARK_AFTER_RENDER=0 → leaving X bookmarks intact.")
        return
    needs = [b for b in bookmarks if not b.unbookmarked and b.note_rel_path]
    if not needs:
        return
    print(f"Un-bookmarking {len(needs)} rendered tweet(s)...")
    for i, bm in enumerate(needs, 1):
        try:
            tcli.unbookmark(bm.id)
            bm.unbookmarked = True
            bm.save()
            print(f"  [{i}/{len(needs)}] {bm.id} ✓")
        except tcli.TwitterCliError as e:
            print(f"  [{i}/{len(needs)}] {bm.id} failed: {e}")


def main() -> int:
    load_dotenv()
    if "OPENROUTER_API_KEY" not in os.environ:
        print("error: OPENROUTER_API_KEY not set; copy .env.example to .env", file=sys.stderr)
        return 2

    notes_dir = Path(os.environ.get("NOTES_DIR", "notes"))

    all_bms = load_all()
    if not all_bms:
        print("No bookmark state files found. Run `uv run python fetch.py` first.", file=sys.stderr)
        return 1

    active = _active(all_bms)
    skipped = [b for b in all_bms if b.skip_reason]
    print(
        f"Loaded {len(all_bms)} state file(s): {len(active)} active, {len(skipped)} skipped."
    )
    if not active:
        print("Nothing to organize.")
        return 0

    _summarize_all(active)

    # Re-select active in case summarize marked some as errored
    all_bms = load_all()
    active = _active(all_bms)
    skipped = [b for b in all_bms if b.skip_reason]

    _embed_all(active)
    # Reload to get fresh embeddings
    active = _active(load_all())

    print("Clustering...")
    _cluster_and_label(active)
    active = _active(load_all())

    print(f"Rendering {len(active)} note(s) to {notes_dir}/")
    render.write_all(active, skipped, notes_dir)
    for bm in active:
        bm.save()

    _unbookmark_rendered(active)

    print(f"\nDone. Vault at {notes_dir}/  (index.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
