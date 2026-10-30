"""События конвейера: агенты и платформа сообщают о ходе работы через AgentContext.emit.

События не хранятся в состоянии графа (PipelineState): они идут через EventBus, который
сохраняет их в хранилище (Postgres — таблица `events`; SQLite или память — в тестах и без БД)
и раздаёт подписчикам (телеметрия, SSE, отчёт, логи). `EventBus.emit` подходит как
AgentContext.emit. Упавшее хранилище или подписчик не мешают конвейеру.

Типы событий платформы — `EventType`; агенты могут слать и свои (`kind` — любая строка).
"""

import json
import sqlite3
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field, JsonValue

log = structlog.get_logger(__name__)


class EventType(StrEnum):
    run_started = "run_started"
    stage_started = "stage_started"
    stage_skipped = "stage_skipped"
    gate_failed = "gate_failed"
    repair_attempt = "repair_attempt"
    stage_passed = "stage_passed"
    stage_degraded = "stage_degraded"
    stage_failed = "stage_failed"
    budget_warning = "budget_warning"
    approval_required = "approval_required"
    run_finished = "run_finished"


class Event(BaseModel):
    """Событие для телеметрии, SSE и отчёта. `kind` — EventType или своё имя агента."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    run_id: str | None = None
    stage: str | None = None
    agent: str | None = None
    message: str = ""
    data: dict[str, JsonValue] = Field(default_factory=dict)
    """Полезная нагрузка (в таблице events и API — `payload`)."""
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def payload(self) -> dict[str, JsonValue]:
        return self.data


class StoredEvent(BaseModel):
    """Событие из хранилища: с порядковым id (для SSE Last-Event-ID)."""

    id: int
    event: Event


Subscriber = Callable[[Event], None]


class EventStore(Protocol):
    def append(self, event: Event) -> int: ...

    def list(self, run_id: str, *, after_id: int = 0, limit: int = 1000) -> list[StoredEvent]: ...


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = threading.Lock()

    def append(self, event: Event) -> int:
        with self._lock:
            self._events.append(event)
            return len(self._events)

    def list(self, run_id: str, *, after_id: int = 0, limit: int = 1000) -> list[StoredEvent]:
        with self._lock:
            items = list(enumerate(self._events, 1))
        found = [
            StoredEvent(id=i, event=e) for i, e in items if i > after_id and e.run_id == run_id
        ]
        return found[:limit]


EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id {id_type},
    run_id TEXT,
    kind TEXT NOT NULL,
    stage TEXT,
    agent TEXT,
    message TEXT NOT NULL DEFAULT '',
    payload {json_type} NOT NULL,
    ts TIMESTAMPTZ NOT NULL
)
"""
EVENTS_INDEX = "CREATE INDEX IF NOT EXISTS events_run_id ON events (run_id, id)"
COLUMNS = ("kind", "run_id", "stage", "agent", "message", "payload", "ts")


def _row(event: Event) -> tuple[Any, ...]:
    return (
        event.kind,
        event.run_id,
        event.stage,
        event.agent,
        event.message,
        json.dumps(event.data, ensure_ascii=False),
        event.ts.isoformat(),
    )


def _event(row: Sequence[Any]) -> StoredEvent:
    _, kind, run_id, stage, agent, message, payload, ts = row
    data = payload if isinstance(payload, dict) else json.loads(payload)
    when = ts if isinstance(ts, datetime) else datetime.fromisoformat(ts)
    return StoredEvent(
        id=int(row[0]),
        event=Event(
            kind=kind, run_id=run_id, stage=stage, agent=agent, message=message, data=data, ts=when
        ),
    )


class SqliteEventStore:
    """Таблица events в SQLite — для тестов и запуска без Postgres."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                EVENTS_DDL.format(id_type="INTEGER PRIMARY KEY AUTOINCREMENT", json_type="TEXT")
            )
            self._conn.execute(EVENTS_INDEX)
            self._conn.commit()

    def append(self, event: Event) -> int:
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO events ({', '.join(COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                _row(event),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def list(self, run_id: str, *, after_id: int = 0, limit: int = 1000) -> list[StoredEvent]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, {', '.join(COLUMNS)} FROM events "
                "WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                (run_id, after_id, limit),
            ).fetchall()
        return [_event(r) for r in rows]


class PostgresEventStore:
    """Таблица events в Postgres (SPECFORGE_PG_DSN). Создаётся при первом подключении."""

    def __init__(self, dsn: str) -> None:
        import psycopg  # noqa: PLC0415 — драйвер нужен только с Postgres

        self._conn = psycopg.connect(dsn, autocommit=True)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                EVENTS_DDL.format(id_type="BIGSERIAL PRIMARY KEY", json_type="JSONB")
            )
            self._conn.execute(EVENTS_INDEX)

    def append(self, event: Event) -> int:
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO events ({', '.join(COLUMNS)}) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                _row(event),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def list(self, run_id: str, *, after_id: int = 0, limit: int = 1000) -> list[StoredEvent]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, {', '.join(COLUMNS)} FROM events "
                "WHERE run_id = %s AND id > %s ORDER BY id LIMIT %s",
                (run_id, after_id, limit),
            ).fetchall()
        return [_event(r) for r in rows]


class EventBus:
    """Сохранение событий и рассылка подписчикам."""

    def __init__(self, store: EventStore | None = None) -> None:
        self.store = store
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self._store_failed = False

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Подписаться; возвращает функцию отписки."""
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def emit(self, event: Event) -> None:
        if self.store is not None and not self._store_failed:
            try:
                self.store.append(event)
            except Exception:
                # хранилище отключается после первой ошибки, чтобы не тормозить конвейер
                self._store_failed = True
                log.exception("event_bus.store_failed", kind=event.kind)
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                log.exception("event_bus.subscriber_failed", kind=event.kind)
