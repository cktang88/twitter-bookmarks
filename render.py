"""Render BookmarkState objects as a hierarchical Obsidian-style markdown vault."""
from __future__ import annotations

import re
import shutil
from collections import defaultdict
from pathlib import Path

from state import BookmarkState, QuotedTweet

_SLUG_RX = re.compile(r"[^a-z0-9]+")


def slugify(s: str, max_len: int = 50) -> str:
    s = _SLUG_RX.sub("-", s.lower()).strip("-")
    return s[:max_len].strip("-") or "untitled"


def _title(bm: BookmarkState) -> str:
    tldr = (bm.summary or {}).get("tl_dr", "") or ""
    txt = tldr.split(". ")[0].strip().rstrip(".")
    if not txt:
        txt = bm.text.split("\n")[0].strip()
    return txt[:90] or f"Tweet {bm.id}"


def _assign_paths(bookmarks: list[BookmarkState]) -> None:
    used: set[str] = set()
    for bm in bookmarks:
        stem = slugify(_title(bm) or bm.id)
        base, i = stem, 2
        while stem in used:
            stem = f"{base}-{i}"
            i += 1
        used.add(stem)
        bm.note_slug = stem

        folder = Path(slugify(bm.primary_topic or "Misc"))
        sub = slugify(bm.sub_topic or "")
        if sub and sub != folder.name:
            folder = folder / sub
        bm.note_rel_path = str(folder / f"{stem}.md")


def _copy_attachments(bm: BookmarkState, notes_root: Path) -> list[tuple[str, Path]]:
    """Copy downloaded photos into notes/attachments/<id>/ and return
    (media_item.url, vault-relative path) pairs for embedding."""
    from state import MediaItem  # avoid cycle at import time
    _ = MediaItem  # noqa: F841

    out: list[tuple[str, Path]] = []
    attach_dir = notes_root / "attachments" / bm.id
    for i, m in enumerate(bm.media):
        if m.type != "photo" or not m.local_path:
            continue
        src = Path(m.local_path)
        if not src.exists():
            continue
        attach_dir.mkdir(parents=True, exist_ok=True)
        dst = attach_dir / f"{i}{src.suffix}"
        if not dst.exists():
            shutil.copy2(src, dst)
        out.append((m.url, dst.relative_to(notes_root)))
    return out


def _related(bm: BookmarkState, all_bms: list[BookmarkState], max_n: int = 5) -> list[BookmarkState]:
    """Notes sharing the most tags or topic. Requires >=2 shared tags to link
    (avoids Obsidian-graph-fungus of weak links)."""
    my_tags = set((bm.summary or {}).get("tags") or [])
    scored: list[tuple[int, BookmarkState]] = []
    for other in all_bms:
        if other.id == bm.id:
            continue
        shared = len(my_tags & set((other.summary or {}).get("tags") or []))
        same_sub = int(other.sub_topic == bm.sub_topic and bool(bm.sub_topic))
        same_pri = int(other.primary_topic == bm.primary_topic and bool(bm.primary_topic))
        score = shared * 3 + same_sub * 2 + same_pri
        if shared >= 2 or (same_sub and shared >= 1):
            scored.append((score, other))
    scored.sort(key=lambda x: (-x[0], x[1].note_slug))
    return [o for _, o in scored[:max_n]]


def _render_quoted(q: QuotedTweet, depth: int) -> list[str]:
    """Render a quoted tweet (and any nested quotes) as markdown lines.
    `depth` controls the heading level: 1 → `## Quoted Tweet`,
    2 → `### Quote-of-Quote`, etc."""
    heading_prefix = "#" * min(depth + 1, 6)
    label = "Quoted Tweet" if depth == 1 else f"Quoted Tweet (depth {depth})"
    header = f"{heading_prefix} {label} — @{q.author_username}"
    if q.tweet_url:
        header = f"{heading_prefix} [{label} — @{q.author_username}]({q.tweet_url})"

    lines: list[str] = [header, ""]
    if q.fetch_error:
        lines.append(f"_Could not fully fetch this quote: {q.fetch_error}_")
        lines.append("")
    for line in (q.text or "").splitlines():
        lines.append(f"> {line}" if line else ">")
    lines.append("")

    videos = [m for m in q.media if m.type in ("video", "animated_gif")]
    if videos:
        lines.append("**Videos:**")
        for v in videos:
            lines.append(f"- <{v.url}>")
        lines.append("")

    photos = [m for m in q.media if m.type == "photo" and m.url]
    if photos:
        lines.append("**Images:** " + " ".join(f"<{m.url}>" for m in photos))
        lines.append("")

    for a in q.articles:
        lines.append(f"**Linked:** [{a.title or a.url}]({a.url})")
        if a.ok:
            snippet = a.text[:600].strip()
            if len(a.text) > 600:
                snippet += "..."
            lines.append("")
            lines.append(snippet)
        elif a.error:
            lines.append(f"_Could not extract: {a.error}_")
        lines.append("")

    for child in q.quoted:
        lines.extend(_render_quoted(child, depth + 1))

    return lines


def _render_note(bm: BookmarkState, related: list[BookmarkState], attachments: list[tuple[str, Path]]) -> str:
    lines: list[str] = []
    lines.append(f"# {_title(bm)}")
    lines.append("")
    lines.append(f"- **Tweet:** [{bm.author_name} (@{bm.author_username})]({bm.tweet_url})")
    if bm.created_at:
        lines.append(f"- **Posted:** {bm.created_at}")
    topic = bm.primary_topic or "Misc"
    if bm.sub_topic and bm.sub_topic != bm.primary_topic:
        topic = f"{bm.primary_topic} → {bm.sub_topic}"
    lines.append(f"- **Topic:** {topic}")
    tags = (bm.summary or {}).get("tags") or []
    if tags:
        lines.append("- **Tags:** " + " ".join(f"#{t}" for t in tags))
    lines.append("")

    summary = bm.summary or {}
    if summary.get("tl_dr"):
        lines.append("## TL;DR")
        lines.append(summary["tl_dr"])
        lines.append("")
    if summary.get("key_points"):
        lines.append("## Key Points")
        for p in summary["key_points"]:
            lines.append(f"- {p}")
        lines.append("")

    lines.append("## Original Tweet")
    for line in (bm.text or "").splitlines():
        lines.append(f"> {line}" if line else ">")
    lines.append("")

    for q in bm.quoted:
        lines.extend(_render_quoted(q, depth=1))

    if attachments:
        lines.append("## Images")
        for _src_url, rel in attachments:
            lines.append(f"![[{rel.as_posix()}]]")
        lines.append("")

    videos = [m for m in bm.media if m.type in ("video", "animated_gif")]
    if videos:
        lines.append("## Videos")
        for v in videos:
            lines.append(f"- <{v.url}>")
        lines.append("")

    if bm.articles:
        lines.append("## Linked Articles")
        for a in bm.articles:
            lines.append(f"### {a.title or a.url}")
            lines.append(f"<{a.url}>")
            lines.append("")
            if a.error is None and a.text.strip():
                snippet = a.text[:1200].strip()
                if len(a.text) > 1200:
                    snippet += "..."
                lines.append(snippet)
            else:
                lines.append(f"_Could not extract: {a.error}_")
            lines.append("")

    if related:
        lines.append("## Related")
        for r in related:
            lines.append(f"- [[{r.note_slug}]] — {_title(r)}")
        lines.append("")

    return "\n".join(lines)


def _render_index(bookmarks: list[BookmarkState], skipped: list[BookmarkState]) -> str:
    by_primary: dict[str, dict[str, list[BookmarkState]]] = defaultdict(lambda: defaultdict(list))
    for bm in bookmarks:
        by_primary[bm.primary_topic or "Misc"][bm.sub_topic or ""].append(bm)

    lines = [
        "# Bookmarks Index",
        "",
        f"Total: **{len(bookmarks)}** notes across **{len(by_primary)}** topics "
        f"({len(skipped)} skipped).",
        "",
    ]
    for primary in sorted(by_primary):
        lines.append(f"## {primary}")
        subs = by_primary[primary]
        for sub in sorted(subs):
            items = sorted(subs[sub], key=lambda n: n.note_slug)
            if sub and sub != primary:
                lines.append(f"### {sub}")
            for bm in items:
                lines.append(f"- [[{bm.note_slug}|{_title(bm)}]]")
            lines.append("")

    if skipped:
        lines.append("## Skipped")
        lines.append("")
        lines.append(f"{len(skipped)} bookmarks were skipped (threads, retweets, or errors):")
        lines.append("")
        for bm in skipped[:50]:
            lines.append(
                f"- [{bm.author_username}/{bm.id}]({bm.tweet_url}) — {bm.skip_reason}"
            )
        if len(skipped) > 50:
            lines.append(f"- ...and {len(skipped) - 50} more")
    return "\n".join(lines)


def write_all(
    active: list[BookmarkState],
    skipped: list[BookmarkState],
    root: Path,
) -> None:
    """Idempotent render. Wipes previous notes/ content (but not attachments
    that are still referenced) and regenerates everything from state files."""
    root.mkdir(parents=True, exist_ok=True)

    # Clear previous markdown files (keep attachments folder — we dedupe copies
    # above). Safest: nuke everything except .git and attachments, regenerate.
    for child in root.iterdir():
        if child.name in (".git", "attachments"):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    _assign_paths(active)

    for bm in active:
        attachments = _copy_attachments(bm, root)
        related = _related(bm, active)
        path = root / bm.note_rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_note(bm, related, attachments), encoding="utf-8")

    (root / "index.md").write_text(_render_index(active, skipped), encoding="utf-8")
