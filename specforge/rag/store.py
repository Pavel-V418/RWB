"""Хранилище документации для RAG: SQLite FTS5 (data/docs.db), поиск bm25 с префиксами.

Без эмбеддингов: токенизатор unicode61 (remove_diacritics 2) одинаково работает с русским и
английским, а префиксный поиск по усечённым словам заменяет стемминг («миграции» → «мигра*»
находит «миграция», «migrations» → «migrati*» — «migration»).
"""

import hashlib
import os
import re
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from specforge.paths import PROJECT_ROOT
from specforge.rag.embeddings import Vectors

DEFAULT_DB = PROJECT_ROOT / "data" / "docs.db"
SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    section,
    text,
    tech UNINDEXED,
    source UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""
SECTION_WEIGHT = 3.0
"""Совпадение в заголовке раздела весит больше, чем в тексте."""
MIN_WORD = 2
STEM_FROM = 6
"""Слова не короче STEM_FROM усекаются на 2 символа (грубый стемминг)."""
MAX_TERMS = 12


class DocChunk(BaseModel):
    tech: str
    section: str
    """Путь заголовков: «Миграции > Применение»."""
    source: str
    """Файл относительно корпуса: <tech>/<файл>."""
    text: str

    def text_for_embedding(self) -> str:
        """Раздел + текст: заголовок даёт контекст коротким фрагментам."""
        return f"{self.section}\n{self.text}"

    @property
    def digest(self) -> str:
        """Хэш чанка: по нему переиндексация векторов пропускает неизменённые чанки."""
        payload = "\0".join((self.tech, self.source, self.section, self.text))
        return hashlib.sha256(payload.encode()).hexdigest()


class DocHit(DocChunk):
    score: float
    """bm25 (меньше — лучше), косинус или RRF (больше — лучше) — зависит от режима поиска."""
    chunk_id: int = 0


@dataclass
class VectorIndex:
    model: str
    ids: list[int]
    digests: list[str]
    techs: list[str | None]
    matrix: Vectors

    def search(self, query: Vectors, tech: str | None, limit: int) -> list[tuple[int, float]]:
        """Косинус brute force (векторы нормализованы): [(chunk_id, сходство)]."""
        if not self.ids:
            return []
        scores = self.matrix @ query
        if tech:
            mask = np.array([t == tech for t in self.techs])
            scores = np.where(mask, scores, -np.inf)
        order = np.argsort(-scores)[:limit]
        return [(self.ids[i], float(scores[i])) for i in order if np.isfinite(scores[i])]


def _hit(row: Sequence[object]) -> DocHit:
    return DocHit(
        section=str(row[0]),
        text=str(row[1]),
        tech=str(row[2]),
        source=str(row[3]),
        score=float(row[4]),  # type: ignore[arg-type]  # число из SQLite
        chunk_id=int(row[5]),  # type: ignore[call-overload]  # rowid из SQLite
    )


def default_db_path() -> Path:
    return Path(os.environ.get("SPECFORGE_DOCS_DB") or DEFAULT_DB)


def match_query(query: str) -> str | None:
    """Запрос FTS5: слова → префиксы через OR (bm25 ранжирует по числу и весу совпадений)."""
    terms: list[str] = []
    for word in re.findall(r"\w+", query.lower()):
        if len(word) < MIN_WORD:
            continue
        stem = word[:-2] if len(word) >= STEM_FROM else word
        term = f'"{stem}"*'
        if term not in terms:
            terms.append(term)
    return " OR ".join(terms[:MAX_TERMS]) or None


class DocStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        self._vectors: VectorIndex | None = None
        with self._lock:
            self._conn.execute(SCHEMA)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS vector_ids "
                "(row INTEGER PRIMARY KEY, chunk_id INTEGER NOT NULL, digest TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def replace_all(self, chunks: Iterable[DocChunk]) -> int:
        """Пересобрать индекс целиком."""
        rows = [(c.section, c.text, c.tech, c.source) for c in chunks]
        with self._lock:
            self._conn.execute("DELETE FROM chunks")
            self._conn.executemany(
                "INSERT INTO chunks (section, text, tech, source) VALUES (?, ?, ?, ?)", rows
            )
            self._conn.commit()
        return len(rows)

    def search(self, query: str, tech: str | None = None, limit: int = 5) -> list[DocHit]:
        match = match_query(query)
        if match is None:
            return []
        sql = (
            "SELECT section, text, tech, source, "
            f"bm25(chunks, {SECTION_WEIGHT}, 1.0) AS score, rowid "
            "FROM chunks WHERE chunks MATCH ?"
        )
        params: list[str | int] = [match]
        if tech:
            sql += " AND tech = ?"
            params.append(tech)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_hit(r) for r in rows]

    def get(self, ids: Sequence[int]) -> dict[int, DocHit]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                "SELECT section, text, tech, source, 0.0, rowid FROM chunks "
                f"WHERE rowid IN ({marks})",
                list(ids),
            ).fetchall()
        return {int(r[5]): _hit(r) for r in rows}

    def chunks(self) -> list[tuple[int, DocChunk]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, section, text, tech, source FROM chunks ORDER BY rowid"
            ).fetchall()
        return [
            (int(r[0]), DocChunk(section=r[1], text=r[2], tech=r[3], source=r[4])) for r in rows
        ]

    # --- векторы: матрица в <db>_vectors.npy, id строк — в таблице vector_ids ---------------

    @property
    def vectors_path(self) -> Path:
        return self.path.with_name(f"{self.path.stem}_vectors.npy")

    def write_vectors(
        self, ids: Sequence[int], digests: Sequence[str], matrix: Vectors, model: str
    ) -> None:
        tmp = self.vectors_path.with_suffix(".tmp.npy")
        np.save(tmp, matrix)
        tmp.replace(self.vectors_path)
        with self._lock:
            self._conn.execute("DELETE FROM vector_ids")
            self._conn.executemany(
                "INSERT INTO vector_ids (row, chunk_id, digest) VALUES (?, ?, ?)",
                [(i, cid, d) for i, (cid, d) in enumerate(zip(ids, digests, strict=True))],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_model', ?)", (model,)
            )
            self._conn.commit()
        self._vectors = None

    def read_vectors(self) -> VectorIndex | None:
        """Матрица векторов, id чанков, их хэши и модель; None — векторного индекса нет."""
        if self._vectors is not None:
            return self._vectors
        with self._lock:
            model_row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'embed_model'"
            ).fetchone()
            rows = self._conn.execute(
                "SELECT v.chunk_id, v.digest, c.tech FROM vector_ids v "
                "LEFT JOIN chunks c ON c.rowid = v.chunk_id ORDER BY v.row"
            ).fetchall()
        if model_row is None or not rows or not self.vectors_path.is_file():
            return None
        matrix = np.load(self.vectors_path).astype(np.float32, copy=False)
        if matrix.shape[0] != len(rows):
            return None  # индекс рассинхронизирован — нужен ingest
        self._vectors = VectorIndex(
            model=str(model_row[0]),
            ids=[int(r[0]) for r in rows],
            digests=[str(r[1]) for r in rows],
            techs=[r[2] for r in rows],
            matrix=matrix,
        )
        return self._vectors

    def techs(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT tech FROM chunks ORDER BY tech").fetchall()
        return [r[0] for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
