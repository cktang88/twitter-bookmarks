"""OpenRouter calls: thread classification, summarization, cluster labeling."""
from __future__ import annotations

import json
import os
import re

from openai import OpenAI
from pydantic import BaseModel, Field

from state import ArticleData, BookmarkState


# ---------- client ----------

def _client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://github.com/local/twitter-bookmarks",
            "X-Title": "twitter-bookmarks",
        },
    )


def _summary_model() -> str:
    return os.environ.get("OPENROUTER_SUMMARY_MODEL", "openai/gpt-5.4-mini")


def _nano_model() -> str:
    return os.environ.get("OPENROUTER_NANO_MODEL", "openai/gpt-5.4-nano")


def _strip_fences(s: str) -> str:
    s = s.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    return m.group(1) if m else s


def _json_call(model: str, system: str, user: str, temperature: float = 0.1) -> dict:
    resp = _client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    raw = _strip_fences(resp.choices[0].message.content or "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # One soft retry: the model wrapped JSON in prose. Extract the first {...}.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# ---------- 1. Thread classification (nano) ----------

_THREAD_SYSTEM = """You classify a single tweet as either SELF_CONTAINED or THREAD_FRAGMENT.

THREAD_FRAGMENT means the tweet is only the beginning, middle, or a teaser
of a longer multi-tweet thread and only makes sense in that context. Signals:
- "🧵", "thread:", "1/", "/", "(1/N)", "👇", "more below"
- ends with "→", "...", or a colon hanging mid-thought
- vague hype like "this is huge" or "wait for it" with no actual claim

SELF_CONTAINED means the tweet has standalone value — a complete thought,
a linked article, a screenshot, a full quote, a joke, etc. Single tweets
with a long body (note_tweet) are SELF_CONTAINED even if they reference
a thread.

Return JSON: {"is_thread": bool, "reason": "<short phrase>"}"""


def classify_thread(bm: BookmarkState) -> tuple[bool, str]:
    """Return (is_thread, short_reason)."""
    # Cheap heuristic shortcut: if the tweet has any external url, quoted tweet, or >300 chars body,
    # it is very likely self-contained. Saves an LLM call.
    text = bm.text.strip()
    if not text:
        return False, "empty text; treated as self-contained"
    if bm.urls or bm.media or len(text) >= 300:
        return False, "has links/media or long body"

    user = f"TWEET by @{bm.author_username}:\n\n{text}"
    data = _json_call(_nano_model(), _THREAD_SYSTEM, user, temperature=0.0)
    return bool(data.get("is_thread", False)), str(data.get("reason") or "")[:120]


# ---------- 2. Summarization (mini) ----------

class Summary(BaseModel):
    tl_dr: str = Field(default="", description="2-4 sentence plain-English summary of the whole thing")
    key_points: list[str] = Field(default_factory=list, description="3-7 concrete takeaways as short bullets")
    tags: list[str] = Field(default_factory=list, description="3-6 short lowercase tags, no # prefix")


_SUMMARY_SYSTEM = """You are a precise technical note-taker. Given a bookmarked
tweet, any attached images/videos, and the text of any articles it links to,
produce a JSON object summarizing the material. Be concrete and specific.
Do not invent topics. Output ONLY valid JSON matching the schema, no code
fences, no prose."""


def _article_block(i: int, a: ArticleData) -> str:
    header = f"--- LINKED ARTICLE {i}: {a.title or '(no title)'} ({a.url}) ---"
    if a.ok:
        return f"{header}\n{a.text}"
    return f"{header}\n(could not extract: {a.error})"


def _summary_prompt(bm: BookmarkState) -> str:
    parts = [
        f"TWEET by @{bm.author_username} ({bm.author_name}) at {bm.created_at}:",
        bm.text or "(no text)",
        "",
    ]
    photo_count = sum(1 for m in bm.media if m.type == "photo")
    video_count = sum(1 for m in bm.media if m.type in ("video", "animated_gif"))
    if photo_count or video_count:
        parts.append(f"(ATTACHED MEDIA: {photo_count} photo(s), {video_count} video(s)/gif(s))")
        parts.append("")
    if bm.articles:
        for i, a in enumerate(bm.articles, 1):
            parts.append(_article_block(i, a))
            parts.append("")
    else:
        parts.append("(no external articles linked)")
    parts.append(
        "Return JSON with keys: tl_dr (string), key_points (list of strings), tags (list of lowercase strings)."
    )
    return "\n".join(parts)


def summarize(bm: BookmarkState) -> Summary:
    data = _json_call(_summary_model(), _SUMMARY_SYSTEM, _summary_prompt(bm), temperature=0.2)
    data["tags"] = [
        re.sub(r"[^a-z0-9-]+", "-", t.lower()).strip("-")
        for t in (data.get("tags") or [])
        if t
    ]
    return Summary(
        tl_dr=str(data.get("tl_dr") or ""),
        key_points=[str(p) for p in (data.get("key_points") or [])],
        tags=[t for t in data["tags"] if t],
    )


# ---------- 3. Cluster labelling (nano) ----------

_CLUSTER_SYSTEM = """You name a thematic cluster of bookmarks with a short,
specific label in Title Case, 1-4 words, no quotes, no punctuation.
Prefer domain-specific names ("Rust Async Runtime", "LLM Agents") over
generic ones ("Programming", "Tech")."""


def label_cluster(summaries: list[str], granularity: str = "broad") -> str:
    if not summaries:
        return "Misc"
    hint = {
        "broad": "Return a BROAD primary topic (e.g. 'Machine Learning', 'Databases').",
        "specific": "Return a SPECIFIC sub-topic (e.g. 'Transformer Architectures', 'Postgres Replication').",
    }[granularity]

    sample = "\n---\n".join(s[:400] for s in summaries[:10])
    user = (
        f"{hint}\n\nHere are representative bookmark summaries from this cluster:\n\n{sample}"
        "\n\nReturn JSON: {\"label\": \"<Title Case, 1-4 words>\"}"
    )
    data = _json_call(_nano_model(), _CLUSTER_SYSTEM, user, temperature=0.1)
    label = str(data.get("label") or "").strip().strip("\"'")
    return label[:60] or "Misc"
