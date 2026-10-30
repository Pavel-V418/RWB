from typing import Any

import pytest

from specforge.core.contracts import Diagnostic
from specforge.repair.fix_memory import (
    INSERT_SQL,
    LOOKUP_SQL,
    SCHEMA_SQL,
    ErrorSignature,
    InMemoryFixMemory,
    NullFixMemory,
    PostgresFixMemory,
    fix_memory_from_env,
    lookup_many,
    normalize_message,
    signature_of,
    trigram_similarity,
)


def diag(message: str, code: str = "CS0103", tool: str = "msbuild") -> Diagnostic:
    return Diagnostic(tool=tool, code=code, message=message)


def test_normalize_message_strips_names_paths_numbers() -> None:
    a = normalize_message("The name 'orderSvc' does not exist in the current context")
    b = normalize_message("The name 'userRepo' does not exist in the current context")
    assert a == b == "the name <name> does not exist in the current context"
    assert (
        normalize_message("Source file '/work/src/Gen/A.cs' could not be found")
        == "source file <name> could not be found"
    )
    assert (
        normalize_message("error at /work/src/a.ts line 42\nstack...") == "error at <path> line <n>"
    )
    assert normalize_message('Type "Foo[]" is not assignable to type "Foo"') == (
        "type <name> is not assignable to type <name>"
    )
    assert normalize_message("id 3f2a9c1e-1111-2222-3333-444455556666 failed") == "id <id> failed"


def test_trigram_similarity_matches_pg_trgm_semantics() -> None:
    assert trigram_similarity("word", "word") == 1.0
    assert trigram_similarity("", "x") == 0.0
    close = trigram_similarity(
        "cannot convert from <name> to <name>", "cannot convert <name> to <name>"
    )
    far = trigram_similarity("cannot convert from <name> to <name>", "missing semicolon")
    assert close > 0.6 > far


def test_in_memory_record_and_lookup() -> None:
    memory = InMemoryFixMemory()
    sig = signature_of(diag("The name 'orderSvc' does not exist in the current context"))
    memory.record(sig, "переименовал поле", "-orderSvc\n+_orderService")
    memory.record(sig, "переименовал поле", "-orderSvc\n+_orderService")  # дубль игнорируется
    assert len(memory.items) == 1

    similar = signature_of(diag("The name 'repo' does not exist in this context"))
    (found,) = memory.lookup(similar)
    assert found.fix_summary == "переименовал поле"
    assert 0.3 <= found.score < 1.0

    assert memory.lookup(signature_of(diag("The name 'x' does not exist", code="CS1002"))) == []
    assert memory.lookup(signature_of(diag("x", tool="tsc"))) == []
    assert memory.lookup(signature_of(diag("totally different text about semicolons"))) == []


def test_lookup_top_three_and_dedup() -> None:
    memory = InMemoryFixMemory()
    base = "cannot convert from <name> to <name>"
    for i, pattern in enumerate([base, base + " here", base + " in call", base + " ok", "cannot"]):
        memory.record(
            ErrorSignature(tool="msbuild", code="CS1503", pattern=pattern), f"fix {i}", f"d{i}"
        )
    diagnostics = [
        diag("Argument 1: cannot convert from 'string' to 'Guid'", code="CS1503"),
        diag("Argument 2: cannot convert from 'int' to 'long'", code="CS1503"),  # та же подпись
        diag("warning text", code="CS1503").model_copy(update={"severity": "warning"}),
    ]
    found = lookup_many(memory, diagnostics)
    assert len(found) == 3
    assert found[0].fix_summary == "fix 0"
    assert [f.score for f in found] == sorted((f.score for f in found), reverse=True)


class FakeConn:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail
        self.executed: list[tuple[str, Any]] = []

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> "FakeConn":
        if self.fail and sql != SCHEMA_SQL:
            raise RuntimeError("connection refused")
        self.executed.append((sql, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


def test_postgres_memory_sql() -> None:
    conn = FakeConn(rows=[(7, "msbuild", "CS0103", "the name <name> ...", "fix", "diff", 0.82)])
    memory = PostgresFixMemory("postgresql://x", connect=lambda dsn: conn)
    sig = ErrorSignature(tool="msbuild", code="CS0103", pattern="the name <name> does not exist")

    (found,) = memory.lookup(sig)
    assert (found.id, found.fix_summary, found.score) == (7, "fix", 0.82)
    memory.record(sig, "переименовал", "d" * 10_000, agent="coder")

    sqls = [sql for sql, _ in conn.executed]
    assert sqls == [SCHEMA_SQL, LOOKUP_SQL, INSERT_SQL]  # схема создаётся один раз
    assert "pg_trgm" in SCHEMA_SQL
    assert "gin_trgm_ops" in SCHEMA_SQL
    assert "similarity(pattern" in LOOKUP_SQL
    lookup_params = conn.executed[1][1]
    assert lookup_params == {**sig.model_dump(), "min": 0.3, "limit": 3}
    insert_params = conn.executed[2][1]
    assert insert_params["agent"] == "coder"
    assert len(insert_params["diff"]) == 6000


def test_postgres_errors_do_not_break_repair() -> None:
    memory = PostgresFixMemory("postgresql://x", connect=lambda dsn: FakeConn(fail=True))
    sig = ErrorSignature(tool="t", code="c", pattern="p")
    assert memory.lookup(sig) == []
    memory.record(sig, "s", "d")  # без исключения


def test_postgres_unreachable_disables_memory() -> None:
    attempts: list[str] = []

    def refuse(dsn: str) -> FakeConn:
        attempts.append(dsn)
        raise OSError("connection refused")

    memory = PostgresFixMemory("postgresql://x", connect=refuse)
    sig = ErrorSignature(tool="t", code="c", pattern="p")
    assert memory.lookup(sig) == []
    assert memory.lookup(sig) == []
    memory.record(sig, "s", "d")
    assert memory.disabled
    assert len(attempts) == 1  # таймаут соединения ждём один раз за процесс


def test_fix_memory_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPECFORGE_PG_DSN", raising=False)
    assert isinstance(fix_memory_from_env(), NullFixMemory)
    monkeypatch.setenv("SPECFORGE_PG_DSN", "postgresql://u:p@localhost/db")
    memory = fix_memory_from_env()
    assert isinstance(memory, PostgresFixMemory)
    assert memory.dsn == "postgresql://u:p@localhost/db"
