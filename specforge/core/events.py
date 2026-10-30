"""События конвейера: агенты и платформа сообщают о ходе работы через AgentContext.emit.

События не хранятся в состоянии графа (PipelineState): они идут через EventBus, на который
подписаны телеметрия, SSE, отчёт и логи. `EventBus.emit` подходит как AgentContext.emit.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, ConfigDict, Field, JsonValue

log = structlog.get_logger(__name__)


class Event(BaseModel):
    """Событие для телеметрии, SSE и отчёта. `kind` — точечное имя: `stage.started`, `llm.call`."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    run_id: str | None = None
    agent: str | None = None
    message: str = ""
    data: dict[str, JsonValue] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


Subscriber = Callable[[Event], None]


class EventBus:
    """Рассылка событий подписчикам. Упавший подписчик не мешает остальным и конвейеру."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

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
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                log.exception("event_bus.subscriber_failed", kind=event.kind)
