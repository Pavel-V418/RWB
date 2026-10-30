"""Публичные контракты платформы.

От этих моделей зависят агенты команды. Любое изменение — только вместе с docs/AGENT_SDK.md
и с пометкой BREAKING в коммите (добавление необязательного поля со значением по умолчанию
не ломает совместимость).
"""

import warnings
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Профили LLM -----------------------------------------------------------------------------


class ProfileName(StrEnum):
    """Профиль генерации (параметры — в specforge.llm.profiles и config/profiles.yaml)."""

    ANALYSIS = "ANALYSIS"
    REVIEW = "REVIEW"
    CODE = "CODE"
    REPAIR = "REPAIR"


THINKING_MIN_TEMPERATURE = 0.5
THINKING_SAFE_TEMPERATURE = 0.6
"""С включённым thinking низкая температура приводит к зацикливанию рассуждений (Qwen3)."""


def safe_thinking_temperature(temperature: float, where: str) -> float:
    """Температура для режима thinking: ниже THINKING_MIN_TEMPERATURE поднимается до
    THINKING_SAFE_TEMPERATURE с предупреждением."""
    if temperature >= THINKING_MIN_TEMPERATURE:
        return temperature
    warnings.warn(
        f"{where}: thinking с temperature={temperature} запрещён — "
        f"используется {THINKING_SAFE_TEMPERATURE}",
        UserWarning,
        stacklevel=3,
    )
    return THINKING_SAFE_TEMPERATURE


# --- Шлюзы ---------------------------------------------------------------------------------

Severity = Literal["error", "warning"]
DiagnosticCategory = Literal["compile", "test", "lint", "generic"]
"""Приоритет при отборе диагностик шлюза: compile → test → lint → generic."""


class Diagnostic(_Contract):
    """Одна находка шлюза (ошибка компилятора, линтера, упавший тест)."""

    tool: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    severity: Severity = "error"
    code: str | None = None
    message: str
    code_window: str | None = None
    """15 строк кода вокруг места ошибки с номерами (строка ошибки помечена `>`)."""
    category: DiagnosticCategory | None = None


class GateResult(_Contract):
    passed: bool
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    total_errors: int = 0
    log_path: str | None = None
    """Полный лог шлюза. В контекст LLM попадают только усечённые diagnostics."""
    duration_s: float = 0.0
    failed_command_index: int | None = None
    """Индекс упавшей команды в AgentSpec.gate_commands; None — все прошли."""
    tests_executed: int | None = None
    """Сколько тестов реально выполнилось (None — неизвестно)."""


# --- Описание агента -----------------------------------------------------------------------

AgentKind = Literal["structured", "tool"]


class AgentSpec(_Contract):
    """Декларация агента. Оркестратор читает только её и никогда не заглядывает в код агента."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    """Совпадает с именем каталога agents/<name>/."""
    stage: str
    kind: AgentKind
    description: str = ""
    inputs: list[str] = Field(default_factory=list)
    """Glob-шаблоны относительно artifacts/ (в локальном запуске — относительно --in)."""
    outputs: list[str] = Field(default_factory=list)
    """Glob-шаблоны файлов, которые агент обязан создать в output_dir."""
    prompt_dir: str = "prompts"
    """Каталог промптов относительно каталога агента."""
    tools: list[str] = Field(default_factory=list)
    """Имена инструментов для tool-агента (`read_file`, `str_replace`, `shell`, ...)."""
    sandbox_image: str | None = None
    gate_commands: list[str] = Field(default_factory=list)
    """Команды шлюза по порядку; этап пройден, если все завершились с кодом 0."""
    gate_parsers: dict[str, str] = Field(default_factory=dict)
    """Явный парсер вывода для команды шлюза: {команда: имя парсера}. Без записи парсер
    выбирается по виду команды (см. docs/AGENT_SDK.md, «Шлюз»)."""
    protected_globs: list[str] = Field(default_factory=list)
    """Файлы, которые агенту и ремонту запрещено менять (например, тесты-эталоны)."""
    profile: ProfileName = ProfileName.CODE
    """Профиль LLM: thinking, температура, top_p, max_tokens, таймаут."""
    thinking: bool | None = None
    """None — как в профиле."""
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    """None — как в профиле. С thinking=True меньше 0.5 нельзя: поднимается до 0.6."""
    max_steps: int = Field(default=30, ge=1)
    token_budget: int = Field(default=200_000, ge=1)
    max_repairs: int = Field(default=2, ge=0)
    depends_on: list[str] = Field(default_factory=list)
    enabled_if: str | None = None
    """Имя флага CaseProfile; если флаг ложен — этап пропускается (skipped)."""
    parallel_group: str | None = None
    llm_free: bool = False
    """Детерминированный агент: любой вызов LLM из него — ошибка."""

    @model_validator(mode="before")
    @classmethod
    def _thinking_temperature(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("thinking") is True:
            temperature = data.get("temperature")
            if isinstance(temperature, int | float):
                fixed = safe_thinking_temperature(temperature, f"AgentSpec {data.get('name')}")
                if fixed != temperature:
                    data = {**data, "temperature": fixed}
        return data


# --- Результаты ----------------------------------------------------------------------------


class Usage(_Contract):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            calls=self.calls + other.calls,
        )


class StageStatus(StrEnum):
    passed = "passed"
    failed = "failed"
    degraded = "degraded"
    """Шлюз красный, но ремонты исчерпаны — конвейер идёт дальше с пометкой."""
    skipped = "skipped"


class StageResult(_Contract):
    agent: str
    status: StageStatus
    attempts: int = 1
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0
    gate: GateResult | None = None
    artifacts: list[str] = Field(default_factory=list)
    """Пути созданных файлов относительно output_dir."""
    summary: str = ""
    stopped_reason: str | None = None
    """Почему агент остановился (AgentOutcome.stopped_reason для tool-агента)."""


# --- Кейс и запуск -------------------------------------------------------------------------


class CaseProfile(_Contract):
    """Профиль кейса, извлечённый из ТЗ. Определяет, какие этапы включены (AgentSpec.enabled_if)."""

    has_mobile: bool = False
    has_web: bool = False
    has_backend: bool = False
    has_geo: bool = False
    has_events: bool = False
    has_integrations: bool = False
    extra: dict[str, bool] = Field(default_factory=dict)

    def is_enabled(self, flag: str) -> bool:
        if flag in type(self).model_fields and flag != "extra":
            return bool(getattr(self, flag))
        return self.extra.get(flag, False)


class RunConfig(_Contract):
    llm_base_url: str
    model: str
    global_token_budget: int = Field(default=5_000_000, ge=1)
    wall_clock_limit_s: float = Field(default=4 * 3600, gt=0)
    autopilot: bool = False
    max_concurrency: int = Field(default=2, ge=1)


StopReason = Literal["finish", "max_steps", "token_budget", "loop", "error"]


class AgentOutcome(_Contract):
    """Итог agent_loop для tool-агента."""

    summary: str
    """Аргумент finish(summary) или причина остановки."""
    steps: int = 0
    """Число вызовов LLM."""
    usage: Usage = Field(default_factory=Usage)
    stopped_reason: StopReason = "finish"
    """finish — модель вызвала finish; max_steps / token_budget — исчерпан лимит;
    loop — зацикливание; error — LLM недоступна или неверный набор инструментов."""
    changed_files: list[str] = Field(default_factory=list)
    """Файлы, созданные или изменённые инструментами (относительно /work)."""


# --- Состояние LangGraph -------------------------------------------------------------------


def merge_stage_results(
    left: dict[str, StageResult], right: dict[str, StageResult]
) -> dict[str, StageResult]:
    """Редьюсер: результаты параллельных узлов сливаются по имени агента, новый побеждает."""
    return {**left, **right}


def add_usage(left: Usage, right: Usage) -> Usage:
    """Редьюсер: узлы возвращают прирост usage, а не итог."""
    return left.add(right)


class PipelineState(TypedDict):
    """Состояние графа. События сюда не пишутся — только через ctx.emit / EventBus."""

    run_id: str
    requirements_path: str
    workdir: str
    profile: CaseProfile
    stage_results: Annotated[dict[str, StageResult], merge_stage_results]
    usage_total: Annotated[Usage, add_usage]
