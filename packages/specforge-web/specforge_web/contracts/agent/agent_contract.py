"""Контракт агента SpecForge.

Владелец — участник 1. Этот файл — согласованная версия, на которой участник 4 пишет свои агенты.
Любой агент — автономная функция, которую можно запустить из CLI и встроить узлом в граф LangGraph.

    run(input_dir, output_dir, llm) -> GateResult
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class GateError:
    message: str
    file: str | None = None
    line: int | None = None
    severity: str = "error"  # error | warning
    code: str | None = None  # машинный код проверки, напр. UX_UNKNOWN_OPERATION


@dataclass
class GateResult:
    passed: bool
    errors: list[GateError] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    llm_calls: int = 0
    attempts: int = 0
    duration_sec: float = 0.0
    artifacts: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)  # специфичные метрики этапа

    @property
    def blocking(self) -> list[GateError]:
        return [e for e in self.errors if e.severity == "error"]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def write(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "gate-result.json").write_text(self.to_json(), encoding="utf-8")


class LLM(Protocol):
    """Минимальный интерфейс LLM, который отдаёт участник 1 (заглушка agent_loop → настоящий клиент vLLM)."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict | None = None,  # guided decoding
        thinking: bool = False,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        tag: str = "",  # имя агента/шага для телеметрии
    ) -> "LLMResponse": ...


@dataclass
class LLMResponse:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0


class Agent(Protocol):
    def __call__(self, input_dir: Path, output_dir: Path, llm: LLM) -> GateResult: ...
