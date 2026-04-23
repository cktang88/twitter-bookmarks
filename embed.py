"""Local embeddings (fastembed / ONNX) + hierarchical agglomerative clustering."""
from __future__ import annotations

import os

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from state import BookmarkState


def _model_name() -> str:
    return os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")


_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding  # lazy: import has a nontrivial cost

        _embedder = TextEmbedding(model_name=_model_name())
    return _embedder


def content_for_embed(bm: BookmarkState) -> str:
    """Concatenate the fields that best describe a bookmark for clustering."""
    parts = [bm.text]
    if bm.summary:
        parts.append(bm.summary.get("tl_dr", ""))
        parts.extend(bm.summary.get("key_points") or [])
        parts.extend(bm.summary.get("tags") or [])
    for a in bm.articles:
        if a.title:
            parts.append(a.title)
        if a.text:
            parts.append(a.text[:500])
    return "\n".join(p for p in parts if p).strip() or bm.id


def embed_texts(texts: list[str]) -> np.ndarray:
    emb = _get_embedder()
    vectors = list(emb.embed(texts))
    arr = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def hierarchical_cluster(
    embeddings: np.ndarray,
    primary_threshold: float | None = None,
    sub_threshold: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (primary_labels, sub_labels) as int arrays of length N.

    Two-level clustering on cosine distance: first pass yields broad primary
    buckets; within each bucket, a second pass splits out sub-topics.
    """
    n = len(embeddings)
    if n == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    if n == 1:
        return np.zeros(1, dtype=int), np.zeros(1, dtype=int)

    pthr = primary_threshold if primary_threshold is not None else float(
        os.environ.get("CLUSTER_PRIMARY_THRESHOLD", "0.7")
    )
    sthr = sub_threshold if sub_threshold is not None else float(
        os.environ.get("CLUSTER_SUB_THRESHOLD", "0.45")
    )

    primary = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=pthr,
        metric="cosine",
        linkage="average",
    ).fit_predict(embeddings)

    sub = np.zeros(n, dtype=int)
    offset = 0
    for pc in sorted(set(primary.tolist())):
        mask = primary == pc
        if mask.sum() < 3:
            sub[mask] = offset
            offset += 1
            continue
        local = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=sthr,
            metric="cosine",
            linkage="average",
        ).fit_predict(embeddings[mask])
        sub[mask] = local + offset
        offset += int(local.max()) + 1

    return primary, sub


def representative_summaries(
    bookmarks: list[BookmarkState],
    labels: np.ndarray,
    cluster_id: int,
    limit: int = 10,
) -> list[str]:
    """Return up to `limit` tl_dr strings from the members of a cluster."""
    members = [bm for bm, lab in zip(bookmarks, labels) if lab == cluster_id]
    out: list[str] = []
    for m in members:
        if m.summary and m.summary.get("tl_dr"):
            out.append(m.summary["tl_dr"])
        elif m.text:
            out.append(m.text[:300])
        if len(out) >= limit:
            break
    return out
