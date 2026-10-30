"""Память исправлений: успешные ремонты, к которым можно вернуться на похожей ошибке.

Ошибка нормализуется в подпись: инструмент + код + шаблон сообщения (без путей, имён,
чисел). Поиск: тот же инструмент и код, похожий шаблон (триграммы, как pg_trgm). Топ-3
найденных решений попадает в промпт ремонта как «известные решения».

Хранилище — Postgres (таблица fix_memory, расширение pg_trgm). Без SPECFORGE_PG_DSN или
при недоступной базе память отключается: ремонт без неё работает, только без подсказок.
"""

import os
import re
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
import structlog
from pydantic import BaseModel, ConfigDict, Field

from specforge.core.contracts import Diagnostic

log = structlog.get_logger(__name__)

MIN_SIMILARITY = 0.3
TOP_K = 3
MAX_DIFF_CHARS = 6000

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS fix_memory (
    id          bigserial PRIMARY KEY,
    tool        text NOT NULL,
    code        text NOT NULL DEFAULT '',
    pattern     text NOT NULL,
    fix_summary text NOT NULL,
    diff        text NOT NULL,
    agent       text,
    uses        integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tool, code, pattern, diff)
);
CREATE INDEX IF NOT EXISTS fix_memory_tool_code ON fix_memory (tool, code);
CREATE INDEX IF NOT EXISTS fix_memory_pattern_trgm ON fix_memory USING gin (pattern gin_trgm_ops);
"""

LOOKUP_SQL = """
SELECT id, tool, code, pattern, fix_summary, diff, similarity(pattern, %(pattern)s) AS score
FROM fix_memory
WHERE tool = %(tool)s AND code = %(code)s AND similarity(pattern, %(pattern)s) >= %(min)s
ORDER BY score DESC, uses DESC, created_at DESC
LIMIT %(limit)s
"""

INSERT_SQL = """
INSERT INTO fix_memory (tool, code, pattern, fix_summary, diff, agent)
VALUES (%(tool)s, %(code)s, %(pattern)s, %(fix_summary)s, %(diff)s, %(agent)s)
ON CONFLICT (tool, code, pattern, diff) DO UPDATE SET uses = fix_memory.uses + 1
"""


class ErrorSignature(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    code: str
    pattern: str


class KnownFix(BaseModel):
    signature: ErrorSignature
    fix_summary: str
    diff: str
    score: float = 1.0
    id: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FixMemory(Protocol):
    def lookup(self, signature: ErrorSignature, limit: int = TOP_K) -> list[KnownFix]: ...

    def record(
        self, signature: ErrorSignature, fix_summary: str, diff: str, agent: str | None = None
    ) -> None: ...


# --- нормализация --------------------------------------------------------------------------------

_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`|‘[^’]*’|“[^”]*”")
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:)?(?:[\w.$-]*[/\\])+[\w.$-]+|\b[\w-]+\.(?:cs|ts|tsx|js|kt|kts|java|py|sql|ya?ml|json|html)\b"
)
_HEX_RE = re.compile(  # сначала GUID целиком, иначе его съест шаблон для hex-строк
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b(?:0x)?[0-9a-f]{8,}\b", re.IGNORECASE
)
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_SPACE_RE = re.compile(r"\s+")


def normalize_message(message: str) -> str:
    """Шаблон сообщения: первая строка, имена в кавычках → <name>, пути → <path>, числа → <n>."""
    first = message.strip().splitlines()[0] if message.strip() else ""
    text = _QUOTED_RE.sub("<name>", first)
    text = _PATH_RE.sub("<path>", text)
    text = _HEX_RE.sub("<id>", text)
    text = _NUM_RE.sub("<n>", text)
    return _SPACE_RE.sub(" ", text).strip().lower()


def signature_of(diagnostic: Diagnostic) -> ErrorSignature:
    return ErrorSignature(
        tool=diagnostic.tool,
        code=diagnostic.code or "",
        pattern=normalize_message(diagnostic.message),
    )


def trigram_similarity(a: str, b: str) -> float:
    """Схожесть по триграммам слов — та же формула, что similarity() в pg_trgm."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in re.findall(r"[^\W_]+", text.lower()):
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


# --- хранилища -----------------------------------------------------------------------------------


class NullFixMemory:
    """Память отключена."""

    def lookup(self, signature: ErrorSignature, limit: int = TOP_K) -> list[KnownFix]:
        del signature, limit
        return []

    def record(
        self, signature: ErrorSignature, fix_summary: str, diff: str, agent: str | None = None
    ) -> None:
        del signature, fix_summary, diff, agent


class InMemoryFixMemory:
    """Память в процессе: для тестов и локальных прогонов без Postgres."""

    def __init__(self) -> None:
        self.items: list[KnownFix] = []
        self._lock = threading.Lock()

    def lookup(self, signature: ErrorSignature, limit: int = TOP_K) -> list[KnownFix]:
        with self._lock:
            candidates = [
                fix.model_copy(update={"score": score})
                for fix in self.items
                if fix.signature.tool == signature.tool
                and fix.signature.code == signature.code
                and (score := trigram_similarity(fix.signature.pattern, signature.pattern))
                >= MIN_SIMILARITY
            ]
        candidates.sort(key=lambda f: (f.score, f.created_at), reverse=True)
        return candidates[:limit]

    def record(
        self, signature: ErrorSignature, fix_summary: str, diff: str, agent: str | None = None
    ) -> None:
        del agent
        with self._lock:
            if any(f.signature == signature and f.diff == diff for f in self.items):
                return
            self.items.append(
                KnownFix(signature=signature, fix_summary=fix_summary, diff=diff[:MAX_DIFF_CHARS])
            )


class PostgresFixMemory:
    """fix_memory в Postgres; ошибки базы не роняют ремонт — только предупреждение в лог.
    После первой неудачной попытки подключиться память отключается до конца процесса, чтобы
    ремонт не ждал таймаут соединения на каждой ошибке."""

    def __init__(self, dsn: str, *, connect: Any = None) -> None:
        self.dsn = dsn
        self._connect = connect
        self._schema_ready = False
        self.disabled = False

    def lookup(self, signature: ErrorSignature, limit: int = TOP_K) -> list[KnownFix]:
        if self.disabled:
            return []
        params = {**signature.model_dump(), "min": MIN_SIMILARITY, "limit": limit}
        try:
            with self._connection() as conn:
                rows = conn.execute(LOOKUP_SQL, params).fetchall()
        except Exception as exc:
            log.warning("fix_memory.lookup_failed", error=str(exc)[:300])
            return []
        return [
            KnownFix(
                id=row[0],
                signature=ErrorSignature(tool=row[1], code=row[2], pattern=row[3]),
                fix_summary=row[4],
                diff=row[5],
                score=float(row[6]),
            )
            for row in rows
        ]

    def record(
        self, signature: ErrorSignature, fix_summary: str, diff: str, agent: str | None = None
    ) -> None:
        if self.disabled:
            return
        params = {
            **signature.model_dump(),
            "fix_summary": fix_summary,
            "diff": diff[:MAX_DIFF_CHARS],
            "agent": agent,
        }
        try:
            with self._connection() as conn:
                conn.execute(INSERT_SQL, params)
        except Exception as exc:
            log.warning("fix_memory.record_failed", error=str(exc)[:300])

    def _connection(self) -> Any:
        try:
            if self._connect is not None:
                conn = self._connect(self.dsn)
            else:
                conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
        except Exception:
            self.disabled = True
            log.warning("fix_memory.disabled", reason="база недоступна")
            raise
        if not self._schema_ready:
            conn.execute(SCHEMA_SQL)
            self._schema_ready = True
        return conn


def fix_memory_from_env() -> FixMemory:
    dsn = os.environ.get("SPECFORGE_PG_DSN", "").strip()
    return PostgresFixMemory(dsn) if dsn else NullFixMemory()


def lookup_many(
    memory: FixMemory, diagnostics: Sequence[Diagnostic], limit: int = TOP_K
) -> list[KnownFix]:
    """Известные решения для ошибок этапа: по каждой подписи, без дублей, лучшие `limit`."""
    found: dict[tuple[str, str], KnownFix] = {}
    seen: set[ErrorSignature] = set()
    for diagnostic in diagnostics:
        if diagnostic.severity != "error":
            continue
        signature = signature_of(diagnostic)
        if signature in seen:
            continue
        seen.add(signature)
        for fix in memory.lookup(signature, limit):
            key = (fix.fix_summary, fix.diff)
            if key not in found or found[key].score < fix.score:
                found[key] = fix
    return sorted(found.values(), key=lambda f: f.score, reverse=True)[:limit]
