"""Публичный API для авторов агентов.

Агент — объект с `spec: AgentSpec` и `run(ctx) -> StageResult`. Обычно достаточно унаследовать
StructuredAgent (один вызов LLM → JSON по pydantic-схеме) или ToolAgent (цикл с инструментами)
и написать промпты в prompts/. Изменения этого модуля — только вместе с docs/AGENT_SDK.md
и с пометкой BREAKING в коммите.
"""

import dataclasses
import inspect
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from specforge.core.contracts import AgentOutcome, AgentSpec, StageResult, StageStatus, Usage
from specforge.core.events import Event
from specforge.executor.react import run_react_loop
from specforge.executor.structured import run_structured
from specforge.llm import ChatMessage, LLMClient, LLMError
from specforge.sandbox.base import WORKDIR, Sandbox

EmitFn = Callable[[Event], None]


def _discard(event: Event) -> None:
    del event


@dataclass(kw_only=True)
class AgentContext:
    """Всё, что платформа даёт агенту на время одного запуска."""

    run_id: str
    input_dir: Path
    """Корень входных артефактов; AgentSpec.inputs считаются относительно него."""
    output_dir: Path
    """Куда агент пишет результаты; AgentSpec.outputs считаются относительно него."""
    workdir: Path
    """Служебный каталог запуска (runs/<run_id>): логи, временные файлы."""
    llm: LLMClient
    budget_tokens: int
    sandbox: Sandbox | None = None
    """Песочница этапа (/work = output_dir). None — agent_loop создаст локальную."""
    emit: EmitFn = _discard
    protected_globs: Sequence[str] = ()
    """Файлы, которые инструментам запрещено менять (AgentSpec.protected_globs)."""


@runtime_checkable
class Agent(Protocol):
    spec: AgentSpec

    def run(self, ctx: AgentContext) -> StageResult: ...


def agent_loop(
    ctx: AgentContext,
    system_prompt: str,
    task: str,
    tools: Sequence[str],
    *,
    max_steps: int = 30,
    temperature: float = 0.2,
    thinking: bool = False,
) -> AgentOutcome:
    """ReAct-цикл tool-агента: модель вызывает инструменты `tools` (имена из AgentSpec.tools,
    finish доступен всегда), пока не вызовет finish или не упрётся в лимит.

    Инструменты работают в ctx.sandbox (/work); бюджет — ctx.budget_tokens. Остановка и её причина —
    в AgentOutcome.stopped_reason. Реализация — specforge.executor.react.
    """
    return run_react_loop(
        ctx,
        system_prompt,
        task,
        tools,
        max_steps=max_steps,
        temperature=temperature,
        thinking=thinking,
    )


class InputFile(BaseModel):
    path: str
    """Путь относительно input_dir, через `/`."""
    content: str


class BaseAgent(ABC):
    """Общие помощники: промпты из каталога агента, чтение входов, сбор артефактов."""

    spec: AgentSpec

    @property
    def agent_dir(self) -> Path:
        return Path(inspect.getfile(type(self))).resolve().parent

    def prompt_path(self, name: str) -> Path:
        return self.agent_dir / self.spec.prompt_dir / name

    def load_prompt(self, name: str, **variables: str) -> str:
        """Прочитать prompts/<name> и подставить `$переменные` (string.Template).

        Фигурные скобки в промптах (примеры JSON) экранировать не нужно.
        """
        text = self.prompt_path(name).read_text(encoding="utf-8")
        return Template(text).safe_substitute(variables)

    def read_inputs(self, ctx: AgentContext) -> list[InputFile]:
        """Файлы из input_dir, подходящие под spec.inputs, в стабильном порядке."""
        return [
            InputFile(path=p.relative_to(ctx.input_dir).as_posix(), content=p.read_text("utf-8"))
            for p in _glob_files(ctx.input_dir, self.spec.inputs)
        ]

    def collect_artifacts(self, ctx: AgentContext) -> list[str]:
        """Файлы из output_dir, подходящие под spec.outputs."""
        return [
            p.relative_to(ctx.output_dir).as_posix()
            for p in _glob_files(ctx.output_dir, self.spec.outputs)
        ]

    def result(
        self,
        status: StageStatus,
        *,
        started: float,
        usage: Usage | None = None,
        artifacts: list[str] | None = None,
        summary: str = "",
    ) -> StageResult:
        return StageResult(
            agent=self.spec.name,
            status=status,
            usage=usage or Usage(),
            duration_s=time.monotonic() - started,
            artifacts=artifacts or [],
            summary=summary,
        )

    @abstractmethod
    def run(self, ctx: AgentContext) -> StageResult: ...


class StructuredAgent[T: BaseModel](BaseAgent):
    """Агент «один вызов → JSON по схеме».

    Автору достаточно задать `spec`, `schema`, `output_file` и написать prompts/system.md
    (и при желании prompts/user.md с переменной `$inputs`). Для нестандартной сборки запроса
    переопределите `build_messages`, для своих выходных файлов — `write_outputs`.
    """

    schema: type[T]
    output_file: str = "result.json"

    def build_messages(self, ctx: AgentContext) -> list[ChatMessage]:
        inputs = render_inputs(self.read_inputs(ctx))
        user = (
            self.load_prompt("user.md", inputs=inputs)
            if self.prompt_path("user.md").exists()
            else inputs
        )
        return [
            ChatMessage(role="system", content=self.load_prompt("system.md")),
            ChatMessage(role="user", content=user),
        ]

    def write_outputs(self, ctx: AgentContext, result: T) -> list[str]:
        path = ctx.output_dir / self.output_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return [self.output_file]

    def summarize(self, result: T) -> str:
        return f"{type(result).__name__} → {self.output_file}"

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        if self.spec.inputs and not _glob_files(ctx.input_dir, self.spec.inputs):
            return self.result(
                StageStatus.failed,
                started=started,
                summary=f"нет входных файлов {self.spec.inputs} в {ctx.input_dir}",
            )
        with ctx.llm.track() as tracker:
            try:
                value = run_structured(
                    ctx,
                    self.build_messages(ctx),
                    self.schema,
                    temperature=self.spec.temperature,
                    thinking=self.spec.thinking,
                )
            except LLMError as exc:
                return self.result(
                    StageStatus.failed, started=started, usage=tracker.usage, summary=str(exc)
                )
        return self.result(
            StageStatus.passed,
            started=started,
            usage=tracker.usage,
            artifacts=self.write_outputs(ctx, value),
            summary=self.summarize(value),
        )


class ToolAgent(BaseAgent):
    """Агент с инструментами (ReAct): пишет файлы в output_dir через инструменты из spec.tools.

    Автору достаточно задать `spec` и написать prompts/system.md и prompts/task.md.
    В task.md доступны `$inputs` (содержимое входных файлов), `$workdir` (/work — это output_dir),
    `$input_dir` и `$output_dir` (пути на хосте). Задача — часть неизменного префикса запроса,
    поэтому в ней не должно быть ничего, что меняется между шагами.
    """

    def system_prompt(self, ctx: AgentContext) -> str:
        del ctx
        return self.load_prompt("system.md")

    def task(self, ctx: AgentContext) -> str:
        return self.load_prompt(
            "task.md",
            inputs=render_inputs(self.read_inputs(ctx)),
            workdir=WORKDIR,
            input_dir=ctx.input_dir.as_posix(),
            output_dir=ctx.output_dir.as_posix(),
        )

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        ctx = dataclasses.replace(
            ctx, protected_globs=[*ctx.protected_globs, *self.spec.protected_globs]
        )
        try:
            outcome = agent_loop(
                ctx,
                self.system_prompt(ctx),
                self.task(ctx),
                self.spec.tools,
                max_steps=self.spec.max_steps,
                temperature=self.spec.temperature,
                thinking=self.spec.thinking,
            )
        except LLMError as exc:
            return self.result(StageStatus.failed, started=started, summary=str(exc))
        status = StageStatus.passed if outcome.stopped_reason == "finish" else StageStatus.failed
        return self.result(
            status,
            started=started,
            usage=outcome.usage,
            artifacts=self.collect_artifacts(ctx),
            summary=f"[{outcome.stopped_reason}] {outcome.summary}"[:500],
        )


def render_inputs(files: Sequence[InputFile]) -> str:
    """Входные файлы одним блоком для промпта."""
    return "\n\n".join(f'<file path="{f.path}">\n{f.content}\n</file>' for f in files)


def _glob_files(root: Path, patterns: Sequence[str]) -> list[Path]:
    found = {p for pattern in patterns for p in root.glob(pattern) if p.is_file()}
    return sorted(found)
