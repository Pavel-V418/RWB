"""Хранилище документации для RAG: SQLite FTS5 (data/docs.db), поиск bm25 с префиксами.

Без эмбеддингов: токенизатор unicode61 (remove_diacritics 2) одинаково работает с русским и
английским, а префиксный поиск по усечённым словам заменяет стемминг («миграции» → «мигра*»
находит «миграция», «migrations» → «migrati*» — «migration»).
"""

import os
import re
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from specforge.paths import PROJECT_ROOT

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


class DocHit(DocChunk):
    score: float
    """bm25 (чем меньше, тем лучше)."""


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
        with self._lock:
            self._conn.execute(SCHEMA)

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
            f"SELECT section, text, tech, source, bm25(chunks, {SECTION_WEIGHT}, 1.0) AS score "
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
        return [DocHit(section=r[0], text=r[1], tech=r[2], source=r[3], score=r[4]) for r in rows]

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
