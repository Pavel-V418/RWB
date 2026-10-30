"""Векторный слой RAG на фейковом Embedder: гибрид, RRF, фолбэк в FTS-only, переиндексация."""

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import numpy as np
import pytest

from specforge.doctor import DoctorDeps, check_embeddings
from specforge.rag.embeddings import Vectors, embedder_from_env, normalize
from specforge.rag.ingest import ingest_corpus
from specforge.rag.search import index_vectors, rrf, search
from specforge.rag.store import DocStore
from specforge.tools.docs import search_docs

CORPUS = Path(__file__).parent / "fixtures" / "docs-corpus"

# «Понятия» с русскими и английскими основами: перевод без настоящей модели.
CONCEPTS = {
    "spatial": ("spatial", "пространств", "geometr", "геометр"),
    "index": ("index", "индекс"),
    "query": ("query", "queries", "запрос", "predicate"),
    "migration": ("migrat", "миграц"),
    "branch": ("branch", "ветк"),
}


class FakeEmbedder:
    mode: Literal["remote", "local", "fake"] = "fake"

    def __init__(self, model: str = "fake-concepts") -> None:
        self.model = model
        self.passages = 0

    def _vector(self, text: str) -> list[float]:
        lower = text.lower()
        counts = [sum(lower.count(s) for s in stems) for stems in CONCEPTS.values()]
        return [*counts, 0.1]

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        self.passages += len(texts)
        return (
            normalize([self._vector(t) for t in texts]) if texts else np.zeros((0, 6), np.float32)
        )

    def embed_query(self, text: str) -> Vectors:
        return normalize(self._vector(text))[0]


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[DocStore, FakeEmbedder]:
    store = DocStore(tmp_path / "docs.db")
    embedder = FakeEmbedder()
    ingest_corpus(CORPUS, store, embedder)
    return store, embedder


def test_hybrid_finds_english_chunk_for_russian_query(
    indexed: tuple[DocStore, FakeEmbedder],
) -> None:
    store, embedder = indexed
    query = "ускорить пространственные запросы"
    assert store.search(query) == []  # в FTS5 русских слов из английского текста нет
    assert search(store, query, mode="fts", embedder=embedder) == []

    hybrid = search(store, query, embedder=embedder)
    assert hybrid[0].source == "postgis/spatial-index.md"
    vector = search(store, query, mode="vector", embedder=embedder)
    assert vector[0].source == "postgis/spatial-index.md"
    assert vector[0].score > 0.5  # косинус

    text = search_docs(query, db_path=store.path, embedder=embedder)
    assert "источник: postgis/spatial-index.md" in text
    assert "Ничего не найдено" in search_docs(query, db_path=store.path, embedder=None)


def test_rrf_order_and_hybrid_merge(indexed: tuple[DocStore, FakeEmbedder]) -> None:
    # 1: 1/61 + 1/62; 3: 1/63 + 1/61; 2: 1/62 → 1, 3, 2
    assert [cid for cid, _ in rrf([[1, 2, 3], [3, 1]])] == [1, 3, 2]
    assert rrf([[5]])[0][1] == pytest.approx(1 / 61)

    store, embedder = indexed
    # «migration index»: в FTS выше org-standards, в векторах — тоже миграции; гибрид берёт
    # чанки из обоих списков, а лидер совпадает у обоих источников
    hybrid = search(store, "migration index", embedder=embedder)
    fts = store.search("migration index", limit=5)
    assert hybrid[0].chunk_id in {h.chunk_id for h in fts[:2]}
    assert len({h.chunk_id for h in hybrid}) == len(hybrid)


def test_fts_only_fallback_without_model(tmp_path: Path) -> None:
    env = {"EMBED_LOCAL_MODEL": "no-such/model", "EMBED_CACHE_DIR": str(tmp_path / "models")}
    assert embedder_from_env(env) is None  # весов нет, скачивать во время прогона нельзя

    store = DocStore(tmp_path / "docs.db")
    ingest_corpus(CORPUS, store, None)
    assert store.read_vectors() is None
    fts = [h.chunk_id for h in store.search("spatial index")]
    for mode in ("hybrid", "vector"):
        assert [h.chunk_id for h in search(store, "spatial index", mode=mode, embedder=None)] == fts

    check = check_embeddings(DoctorDeps(embedder_factory=lambda: None))
    assert (check.status, check.detail.split(":")[0]) == ("SKIP", "FTS-only")
    ok = check_embeddings(DoctorDeps(embedder_factory=FakeEmbedder))
    assert (ok.status, ok.detail) == ("OK", "fake fake-concepts, размерность 6")


def test_reindex_skips_unchanged_chunks(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    store = DocStore(tmp_path / "docs.db")
    embedder = FakeEmbedder()
    total = ingest_corpus(corpus, store, embedder)
    assert embedder.passages == total

    again = FakeEmbedder()
    ingest_corpus(corpus, store, again)
    assert again.passages == 0

    naming = corpus / "org-standards" / "naming.md"
    naming.write_text(naming.read_text("utf-8").replace("feature/", "task/"), "utf-8")
    changed = FakeEmbedder()
    ingest_corpus(corpus, store, changed)
    assert changed.passages == 1  # изменился один чанк «Ветки и коммиты»

    stats = index_vectors(store, FakeEmbedder("other-model"))  # другая модель — всё заново
    assert (stats.embedded, stats.reused) == (total, 0)
    index = store.read_vectors()
    assert index is not None
    assert index.model == "other-model"
    assert index.matrix.shape == (total, 6)
