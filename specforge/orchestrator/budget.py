"""Бюджет конвейера: глобальный лимит токенов и времени и лимиты этапов.

Перед каждым этапом `BudgetController.decide` смотрит на долю израсходованного глобального
бюджета (бо́льшую из долей по токенам и по времени) и решает:
- > 70% — этап идёт с thinking off (принудительно, что бы ни просил агент или профиль);
- > 85% — max_repairs = 1 (если у агента было больше);
- ≥ 100% — этап не запускается (skipped со stopped_reason="budget");
- бюджет токенов этапа — min(лимита этапа, остатка глобального бюджета).
Каждое решение — событие `budget_warning`. Время этапа прервать нельзя: превышение лимита
времени этапа фиксируется после него событием `budget_warning`.

Время конвейера — объединение интервалов работы этапов, поэтому простой между падением
процесса и `specforge resume` в бюджет не входит.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from specforge.core.contracts import AgentSpec, RunConfig, StageResult
from specforge.core.events import Event

THINKING_OFF_RATIO = 0.70
SINGLE_REPAIR_RATIO = 0.85


class StageLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: int | None = Field(default=None, ge=1)
    """None — AgentSpec.token_budget."""
    time_s: float | None = Field(default=None, gt=0)
    """None — без лимита."""


class BudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_tokens: int = Field(ge=1)
    wall_clock_s: float = Field(gt=0)
    stages: dict[str, StageLimits] = Field(default_factory=dict)
    """Лимиты по имени агента."""
    thinking_off_ratio: float = Field(default=THINKING_OFF_RATIO, gt=0, le=1)
    single_repair_ratio: float = Field(default=SINGLE_REPAIR_RATIO, gt=0, le=1)

    @classmethod
    def from_run_config(
        cls, config: RunConfig, stages: Mapping[str, StageLimits] | None = None
    ) -> "BudgetLimits":
        return cls(
            global_tokens=config.global_token_budget,
            wall_clock_s=config.wall_clock_limit_s,
            stages=dict(stages or {}),
        )


class BudgetDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_budget: int
    max_repairs: int
    thinking_off: bool = False
    exhausted: str | None = None
    """Почему этап не запускается; None — запускается."""
    ratio: float = 0.0
    """Доля израсходованного глобального бюджета."""


def active_seconds(intervals: Iterable[Sequence[float]]) -> float:
    """Длина объединения интервалов [start, end] (параллельные этапы не считаются дважды)."""
    total = 0.0
    current: tuple[float, float] | None = None
    for start, end in sorted((float(i[0]), float(i[1])) for i in intervals):
        if current is None or start > current[1]:
            if current is not None:
                total += current[1] - current[0]
            current = (start, end)
        else:
            current = (current[0], max(current[1], end))
    if current is not None:
        total += current[1] - current[0]
    return total


class BudgetController:
    def __init__(
        self,
        limits: BudgetLimits,
        *,
        run_id: str | None = None,
        emit: Callable[[Event], None] | None = None,
    ) -> None:
        self.limits = limits
        self.run_id = run_id
        self._emit_fn = emit

    def ratio(self, tokens_used: int, elapsed_s: float) -> float:
        return max(tokens_used / self.limits.global_tokens, elapsed_s / self.limits.wall_clock_s)

    def decide(self, spec: AgentSpec, *, tokens_used: int, elapsed_s: float) -> BudgetDecision:
        ratio = self.ratio(tokens_used, elapsed_s)
        stage_limits = self.limits.stages.get(spec.name, StageLimits())
        stage_tokens = stage_limits.tokens or spec.token_budget
        remaining = self.limits.global_tokens - tokens_used

        if tokens_used >= self.limits.global_tokens or elapsed_s >= self.limits.wall_clock_s:
            what = (
                f"токены {tokens_used}/{self.limits.global_tokens}"
                if tokens_used >= self.limits.global_tokens
                else f"время {elapsed_s:.0f}/{self.limits.wall_clock_s:.0f} с"
            )
            reason = f"глобальный бюджет исчерпан ({what})"
            self._warn(spec, "exhausted", reason, ratio=ratio)
            return BudgetDecision(token_budget=0, max_repairs=0, exhausted=reason, ratio=ratio)

        decision = BudgetDecision(
            token_budget=min(stage_tokens, remaining), max_repairs=spec.max_repairs, ratio=ratio
        )
        if remaining < stage_tokens:
            self._warn(
                spec,
                "stage_tokens_clamped",
                f"бюджет этапа урезан до остатка глобального: {stage_tokens} → {remaining}",
                ratio=ratio,
                token_budget=remaining,
            )
        if ratio > self.limits.thinking_off_ratio:
            decision.thinking_off = True
            self._warn(
                spec,
                "thinking_off",
                f"израсходовано {ratio:.0%} бюджета — этап без thinking",
                ratio=ratio,
            )
        if ratio > self.limits.single_repair_ratio and spec.max_repairs > 1:
            decision.max_repairs = 1
            self._warn(
                spec,
                "single_repair",
                f"израсходовано {ratio:.0%} бюджета — не больше одного ремонта",
                ratio=ratio,
                max_repairs=1,
            )
        return decision

    def after_stage(self, spec: AgentSpec, result: StageResult) -> None:
        """Зафиксировать превышение лимита времени этапа."""
        limit = self.limits.stages.get(spec.name, StageLimits()).time_s
        if limit is not None and result.duration_s > limit:
            self._warn(
                spec,
                "stage_time_exceeded",
                f"этап шёл {result.duration_s:.0f} с при лимите {limit:.0f} с",
                duration_s=result.duration_s,
                limit_s=limit,
            )

    def _warn(self, spec: AgentSpec, decision: str, message: str, **data: JsonValue) -> None:
        if self._emit_fn is None:
            return
        self._emit_fn(
            Event(
                kind="budget_warning",
                run_id=self.run_id,
                agent=spec.name,
                message=message,
                data={"decision": decision, **data},
            )
        )
