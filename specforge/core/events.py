"""События конвейера: агенты и платформа сообщают о ходе работы через AgentContext.emit."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Event(BaseModel):
    """Событие для телеметрии, SSE и отчёта. `kind` — точечное имя: `stage.started`, `llm.call`."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    run_id: str | None = None
    agent: str | None = None
    message: str = ""
    data: dict[str, JsonValue] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
