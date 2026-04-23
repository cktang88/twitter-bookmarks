"""Per-bookmark JSON state files. Enables idempotent, resumable runs."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CACHE = Path(".cache")
BOOKMARKS_DIR = CACHE / "bookmarks"
MEDIA_DIR = CACHE / "media"
RAW_BOOKMARKS_FILE = CACHE / "raw_bookmarks.json"


@dataclass
class MediaItem:
    type: str  # "photo" | "video" | "animated_gif"
    url: str
    local_path: str | None = None  # set once downloaded (photos only)


@dataclass
class ArticleData:
    url: str
    title: str = ""
    text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


@dataclass
class QuotedTweet:
    """A tweet referenced by a bookmark via quote-tweet. Recursive: a quoted
    tweet may itself quote another tweet."""
    id: str
    text: str = ""
    tweet_url: str = ""
    author_username: str = ""
    author_name: str = ""
    created_at: str = ""
    lang: str = ""
    urls: list[str] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    articles: list[ArticleData] = field(default_factory=list)
    quoted: list["QuotedTweet"] = field(default_factory=list)
    fetch_error: str = ""  # set when tcli.fetch_tweet failed; rest of fields may be stub-only

    @classmethod
    def from_dict(cls, d: dict) -> "QuotedTweet":
        d = dict(d)
        d["media"] = [MediaItem(**m) for m in d.get("media") or []]
        d["articles"] = [ArticleData(**a) for a in d.get("articles") or []]
        d["quoted"] = [cls.from_dict(q) for q in d.get("quoted") or []]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class BookmarkState:
    id: str
    text: str = ""
    tweet_url: str = ""
    author_username: str = ""
    author_name: str = ""
    created_at: str = ""
    lang: str = ""
    urls: list[str] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    quoted: list[QuotedTweet] = field(default_factory=list)

    # Stage 1 enrichment
    is_thread: bool | None = None
    thread_reason: str = ""
    articles: list[ArticleData] = field(default_factory=list)

    # Stage 2 outputs
    summary: dict | None = None  # Summary.model_dump()
    embedding: list[float] | None = None
    primary_topic: str = ""
    sub_topic: str = ""
    note_slug: str = ""
    note_rel_path: str = ""

    # Terminal flags
    skip_reason: str = ""  # "thread" | "retweet" | "empty" | "error:<...>"
    unbookmarked: bool = False

    @property
    def path(self) -> Path:
        return BOOKMARKS_DIR / f"{self.id}.json"

    def save(self) -> None:
        BOOKMARKS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, d: dict) -> "BookmarkState":
        d = dict(d)
        d["media"] = [MediaItem(**m) for m in d.get("media") or []]
        d["articles"] = [ArticleData(**a) for a in d.get("articles") or []]
        d["quoted"] = [QuotedTweet.from_dict(q) for q in d.get("quoted") or []]
        known = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in known}
        return cls(**d)

    @classmethod
    def load(cls, bookmark_id: str) -> "BookmarkState | None":
        p = BOOKMARKS_DIR / f"{bookmark_id}.json"
        if not p.exists():
            return None
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))


def load_all() -> list[BookmarkState]:
    out: list[BookmarkState] = []
    if not BOOKMARKS_DIR.exists():
        return out
    for p in sorted(BOOKMARKS_DIR.glob("*.json")):
        try:
            out.append(BookmarkState.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except Exception as e:
            print(f"warning: could not load {p}: {e}")
    return out
