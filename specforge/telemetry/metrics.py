"""Метрики прогона из StageResult (pipeline.json), UsageTracker, событий и журнала вызовов LLM.

Источники в runs/<run_id>/:
- pipeline.json   — PipelineSummary: StageResult этапов, интервалы этапов, llm_free-агенты;
- llm_calls.jsonl — LLMCallRecord каждого вызова (пишет CallLog — хук LLMClient): повторы;
- события         — events.jsonl в каталоге прогона или runs/events.sqlite (EventBus):
                    ошибки шлюза до ремонта (gate_failed), эскалации.
"""

import json
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from specforge.core.contracts import StageResult, StageStatus, Usage
from specforge.core.events import Event, EventType, SqliteEventStore
from specforge.llm.usage import LLMCallRecord, UsageTracker
from specforge.orchestrator.budget import active_seconds
from specforge.orchestrator.nodes import PIPELINE_FILE, PipelineSummary

CALLS_FILE = "llm_calls.jsonl"
EVENTS_FILE = "events.jsonl"
EVENTS_SQLITE = "events.sqlite"

GateStatus = Literal["passed", "failed", "нет"]
"""«нет» — у этапа без шлюза."""


class StageMetrics(BaseModel):
    agent: str
    status: StageStatus
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_hit_rate: float = 0.0
    """cached / prompt."""
    llm_calls: int = 0
    dedup_hits: int = 0
    """Ответы из кэша дедупликации LLMClient (temperature == 0)."""
    retries: int = 0
    """Повторы транспорта LLM (attempts − 1 по всем вызовам)."""
    wall_s: float = 0.0
    start_s: float | None = None
    """Начало относительно старта прогона."""
    attempts: int = 0
    """1 + ремонты; 0 — этап не запускался."""
    repairs: int = 0
    gate: GateStatus = "нет"
    errors_before: int | None = None
    """Ошибок шлюза до ремонта (первый красный шлюз); None — шлюз не падал или неизвестно."""
    errors_after: int = 0
    llm_free: bool = False
    from_cache: bool = False
    summary: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class RunMetrics(BaseModel):
    run_id: str
    stages: list[StageMetrics] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_hit_rate: float = 0.0
    llm_calls: int = 0
    dedup_hits: int = 0
    """Ответы из кэша дедупликации LLMClient (temperature == 0)."""
    retries: int = 0
    wall_s: float = 0.0
    """Время прогона — объединение интервалов этапов (параллельные не считаются дважды)."""
    stage_time_s: float = 0.0
    """Сумма времени этапов."""
    attempts: int = 0
    repairs: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    gates_passed: int = 0
    gates_failed: int = 0
    errors_before: int = 0
    errors_after: int = 0
    llm_free_share: float = 0.0
    """Доля llm_free-этапов среди запускавшихся."""
    escalations: list[str] = Field(default_factory=list)
    questions_asked: int = 0
    """Уточняющих вопросов агентов (событие clarifications)."""
    questions_answered: int = 0
    """Из них ответил пользователь; остальные — допущения."""
    manual_edits: int = 0
    """Правок артефактов при подтверждении (artifacts_edited) — ручное вмешательство."""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def compute_metrics(
    summary: PipelineSummary,
    *,
    events: Sequence[Event] = (),
    calls: Sequence[LLMCallRecord] = (),
    tracker: UsageTracker | None = None,
) -> RunMetrics:
    """Агрегаты по этапам и прогону. Токены — из UsageTracker, если он передан, иначе из
    StageResult.usage."""
    usage = (
        {a: tracker.total(run_id=summary.run_id, agent=a) for a in summary.stages}
        if tracker is not None
        else {a: r.usage for a, r in summary.stages.items()}
    )
    origin = min((c[0] for c in summary.clock.values()), default=0.0)
    first_gate = _first_gate_errors(events)
    stages = [
        _stage(
            agent,
            result,
            usage[agent],
            summary.clock.get(agent),
            origin,
            calls=[c for c in calls if c.agent == agent],
            errors_before=first_gate.get(agent),
            llm_free=agent in summary.llm_free,
        )
        for agent, result in summary.stages.items()
    ]
    executed = [s for s in stages if s.status != StageStatus.skipped]
    total = Usage()
    for item in usage.values():
        total = total.add(item)
    escalations = [
        f"{e.agent}: {e.data.get('reason', e.message)}" for e in events if e.kind == "escalation"
    ]
    return RunMetrics(
        run_id=summary.run_id,
        stages=stages,
        prompt_tokens=total.prompt_tokens,
        completion_tokens=total.completion_tokens,
        cached_tokens=total.cached_tokens,
        cache_hit_rate=_rate(total.cached_tokens, total.prompt_tokens),
        llm_calls=total.calls,
        dedup_hits=total.dedup_hits,
        retries=sum(s.retries for s in stages),
        wall_s=active_seconds(summary.clock.values()) if summary.clock else summary.active_s,
        stage_time_s=sum(s.wall_s for s in stages),
        attempts=sum(s.attempts for s in stages),
        repairs=sum(s.repairs for s in stages),
        counts={s.value: sum(x.status == s for x in stages) for s in StageStatus},
        gates_passed=sum(s.gate == "passed" for s in stages),
        gates_failed=sum(s.gate == "failed" for s in stages),
        errors_before=sum(s.errors_before or 0 for s in stages),
        errors_after=sum(s.errors_after for s in stages),
        llm_free_share=_rate(sum(s.llm_free for s in executed), len(executed)),
        escalations=escalations,
        questions_asked=_sum_data(events, "clarifications", "asked"),
        questions_answered=_sum_data(events, "clarifications", "answered"),
        manual_edits=sum(
            len(paths)
            for e in events
            if e.kind == "artifacts_edited" and isinstance(paths := e.data.get("paths"), list)
        ),
    )


def _stage(
    agent: str,
    result: StageResult,
    usage: Usage,
    clock: Sequence[float] | None,
    origin: float,
    *,
    calls: Sequence[LLMCallRecord],
    errors_before: int | None,
    llm_free: bool,
) -> StageMetrics:
    gate = result.gate
    errors_after = gate.total_errors if gate is not None and not gate.passed else 0
    if errors_before is None and gate is not None and not gate.passed:
        errors_before = gate.total_errors  # шлюз упал, а события не сохранились
    return StageMetrics(
        agent=agent,
        status=result.status,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        cache_hit_rate=_rate(usage.cached_tokens, usage.prompt_tokens),
        llm_calls=usage.calls,
        dedup_hits=usage.dedup_hits,
        retries=sum(max(c.attempts - 1, 0) for c in calls),
        wall_s=(clock[1] - clock[0]) if clock else result.duration_s,
        start_s=(clock[0] - origin) if clock else None,
        attempts=result.attempts if result.status != StageStatus.skipped else 0,
        repairs=max(result.attempts - 1, 0) if result.status != StageStatus.skipped else 0,
        gate="нет" if gate is None else ("passed" if gate.passed else "failed"),
        errors_before=errors_before,
        errors_after=errors_after,
        llm_free=llm_free,
        from_cache=result.summary.startswith("кэш "),
        summary=result.summary,
    )


def _first_gate_errors(events: Iterable[Event]) -> dict[str, int]:
    found: dict[str, int] = {}
    for e in events:
        if e.kind == EventType.gate_failed and e.agent and e.agent not in found:
            value = e.data.get("total_errors")
            if isinstance(value, int):
                found[e.agent] = value
    return found


def _sum_data(events: Iterable[Event], kind: str, key: str) -> int:
    return sum(v for e in events if e.kind == kind and isinstance(v := e.data.get(key), int))


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


# --- загрузка из каталога прогона ---------------------------------------------------------------


def load_summary(run_dir: Path) -> PipelineSummary:
    path = run_dir / PIPELINE_FILE
    if not path.is_file():
        raise FileNotFoundError(f"нет {path} — прогон не завершён или каталог неверный")
    return PipelineSummary.model_validate_json(path.read_text(encoding="utf-8"))


def load_events(run_dir: Path, run_id: str) -> list[Event]:
    jsonl = run_dir / EVENTS_FILE
    if jsonl.is_file():
        return [_parse(Event, line) for line in _lines(jsonl)]
    sqlite = run_dir.parent / EVENTS_SQLITE
    if sqlite.is_file():
        return [s.event for s in SqliteEventStore(sqlite).list(run_id, limit=100_000)]
    return []


def load_calls(run_dir: Path) -> list[LLMCallRecord]:
    path = run_dir / CALLS_FILE
    return [_parse(LLMCallRecord, line) for line in _lines(path)] if path.is_file() else []


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _parse[T: BaseModel](model: type[T], line: str) -> T:
    return model.model_validate(json.loads(line))


class CallLog:
    """Хук LLMClient: каждый вызов — строка в runs/<run_id>/llm_calls.jsonl (для повторов)."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self._lock = threading.Lock()

    def __call__(self, record: LLMCallRecord) -> None:
        if not record.run_id:
            return
        path = self.runs_dir / record.run_id / CALLS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
