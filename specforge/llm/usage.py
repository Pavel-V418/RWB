"""Учёт токенов с разрезом run / stage / agent и запись о каждом вызове для телеметрии."""

import threading
from collections.abc import Callable
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

from specforge.core.contracts import Usage


class UsageKey(NamedTuple):
    run_id: str | None = None
    stage: str | None = None
    agent: str | None = None


class LLMCallRecord(BaseModel):
    """Один вызов LLM (со всеми повторами) — то, что получают хуки on_call."""

    run_id: str | None = None
    stage: str | None = None
    agent: str | None = None
    profile: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    latency_s: float = 0.0
    attempts: int = 1
    tool_calls: int = 0
    finish_reason: str | None = None
    structured_schema: str | None = None
    error: str | None = None


OnCallHook = Callable[[LLMCallRecord], None]

Dimension = Literal["run", "stage", "agent"]


class UsageTracker:
    """Потокобезопасный накопитель Usage. Один на процесс/запуск, общий для всех привязанных
    клиентов (LLMClient.bind)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[UsageKey, Usage] = {}

    def record(self, key: UsageKey, usage: Usage) -> None:
        with self._lock:
            self._by_key[key] = self._by_key.get(key, Usage()).add(usage)

    def total(
        self, *, run_id: str | None = None, stage: str | None = None, agent: str | None = None
    ) -> Usage:
        """Сумма по ключам, совпадающим с заданными измерениями (None — любое значение)."""
        result = Usage()
        with self._lock:
            items = list(self._by_key.items())
        for key, usage in items:
            if (
                (run_id is None or key.run_id == run_id)
                and (stage is None or key.stage == stage)
                and (agent is None or key.agent == agent)
            ):
                result = result.add(usage)
        return result

    def breakdown(self, by: Dimension) -> dict[str, Usage]:
        """Usage по одному измерению; вызовы без метки попадают в ключ "-"."""
        index = {"run": 0, "stage": 1, "agent": 2}[by]
        result: dict[str, Usage] = {}
        with self._lock:
            items = list(self._by_key.items())
        for key, usage in items:
            label = key[index] or "-"
            result[label] = result.get(label, Usage()).add(usage)
        return result

    def entries(self) -> list[tuple[UsageKey, Usage]]:
        with self._lock:
            return list(self._by_key.items())


class UsageMeter:
    """Накопитель для блока `with llm.track() as meter:` — токены вызовов внутри блока."""

    def __init__(self) -> None:
        self.usage = Usage()
