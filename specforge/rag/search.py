"""Векторный слой поверх FTS5: индексация векторов чанков и гибридный поиск.

- index_vectors: векторы считаются только для новых и изменённых чанков (по хэшу содержимого);
  неизменённые берутся из прошлого data/docs_vectors.npy, если модель та же;
- search: hybrid — топ-20 FTS5 + топ-20 векторов → Reciprocal Rank Fusion (k=60) → топ-5;
  fts / vector — один источник (для отладки и сравнения). Без эмбеддера или векторного индекса
  hybrid и vector сводятся к fts.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import structlog

from specforge.rag.embeddings import Embedder
from specforge.rag.store import DocHit, DocStore

log = structlog.get_logger(__name__)

SearchMode = Literal["hybrid", "fts", "vector"]
CANDIDATES = 20
RRF_K = 60


@dataclass
class VectorStats:
    embedded: int = 0
    reused: int = 0


def index_vectors(store: DocStore, embedder: Embedder) -> VectorStats:
    chunks = store.chunks()
    previous = store.read_vectors()
    cache: dict[str, np.ndarray] = {}
    if previous is not None and previous.model == embedder.model:
        cache = {d: previous.matrix[i] for i, d in enumerate(previous.digests)}
    digests = [chunk.digest for _, chunk in chunks]
    missing = [i for i, d in enumerate(digests) if d not in cache]
    fresh = embedder.embed_passages([chunks[i][1].text_for_embedding() for i in missing])
    for row, i in enumerate(missing):
        cache[digests[i]] = fresh[row]
    rows = [cache[d] for d in digests]
    matrix = np.vstack(rows).astype(np.float32) if rows else np.zeros((0, 0), np.float32)
    store.write_vectors([cid for cid, _ in chunks], digests, matrix, embedder.model)
    stats = VectorStats(embedded=len(missing), reused=len(chunks) - len(missing))
    log.info("rag.vectors", model=embedder.model, embedded=stats.embedded, reused=stats.reused)
    return stats


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank), rank с 1."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def search(
    store: DocStore,
    query: str,
    *,
    tech: str | None = None,
    mode: SearchMode = "hybrid",
    embedder: Embedder | None = None,
    limit: int = 5,
) -> list[DocHit]:
    vector_ids = _vector_ranking(store, query, tech, embedder) if mode != "fts" else None
    if mode == "fts" or vector_ids is None:
        return store.search(query, tech=tech, limit=limit)
    if mode == "vector":
        ranked = [(cid, score) for cid, score in vector_ids][:limit]
    else:
        fts = [h.chunk_id for h in store.search(query, tech=tech, limit=CANDIDATES)]
        ranked = rrf([fts, [cid for cid, _ in vector_ids]])[:limit]
    hits = store.get([cid for cid, _ in ranked])
    return [hits[cid].model_copy(update={"score": s}) for cid, s in ranked if cid in hits]


def _vector_ranking(
    store: DocStore, query: str, tech: str | None, embedder: Embedder | None
) -> list[tuple[int, float]] | None:
    if embedder is None:
        return None
    index = store.read_vectors()
    if index is None:
        return None
    if index.model != embedder.model:
        log.warning("rag.model_mismatch", index=index.model, embedder=embedder.model)
        return None
    return index.search(embedder.embed_query(query), tech, CANDIDATES)
