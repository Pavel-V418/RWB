"""CLI платформы.

specforge list-agents                     — реестр, проверка и план шагов конвейера
specforge agent run <name> --in --out     — один агент и его шлюз
specforge run <requirements.md> [флаги]   — весь конвейер
specforge resume <run_id> [--from <этап>] — продолжить запуск с чекпойнта
"""

import io
import logging
import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import structlog
import typer
from dotenv import find_dotenv, load_dotenv

from specforge.core.agent_api import AgentContext
from specforge.core.contracts import CaseProfile, RunConfig, StageStatus
from specforge.core.events import Event, EventBus
from specforge.llm import LLMClient, LLMError, NullLLM
from specforge.orchestrator.graph import (
    GraphConfig,
    OrchestratorError,
    PipelineGraph,
    build_graph,
    open_checkpointer,
    resume_run,
    start_run,
)
from specforge.orchestrator.nodes import GraphState, execute_stage, summarize
from specforge.orchestrator.registry import (
    Registry,
    RegistryError,
    available_agents,
    load_agent,
)

app = typer.Typer(no_args_is_help=True, help="SpecForge — агентный конвейер разработки.")
agent_app = typer.Typer(no_args_is_help=True, help="Агенты из реестра agents/.")
app.add_typer(agent_app, name="agent")

log = structlog.get_logger("specforge.cli")


@app.callback()
def main() -> None:
    """SpecForge — агентный конвейер разработки."""
    # Вывод CLI — JSON в UTF-8 независимо от кодовой страницы консоли (cp1251/cp866 на Windows).
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def configure_logging() -> None:
    level = logging.getLevelNamesMapping().get(
        os.environ.get("SPECFORGE_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # sys.stderr берётся при каждом создании логгера: его подменяют тесты и перенаправления.
        logger_factory=lambda *_: structlog.PrintLogger(sys.stderr),
        cache_logger_on_first_use=False,
    )


def build_llm() -> LLMClient:
    """Точка подмены LLM в тестах."""
    return LLMClient.from_env()


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _log_event(event: Event) -> None:
    log.info(event.kind, agent=event.agent, message=event.message, data=event.data)


@agent_app.command("list")
def list_agents() -> None:
    """Показать агентов из реестра."""
    for name in available_agents():
        try:
            spec = load_agent(name).spec
        except Exception as exc:  # чужой агент не должен ронять листинг
            typer.echo(f"{name:24} ОШИБКА: {exc}")
            continue
        typer.echo(f"{name:24} {spec.kind:10} {spec.stage:16} {spec.description}")


@agent_app.command("run")
def run_agent(
    name: Annotated[str, typer.Argument(help="Имя агента (каталог agents/<name>/).")],
    input_dir: Annotated[
        Path,
        typer.Option("--in", exists=True, file_okay=False, resolve_path=True, help="Входы."),
    ],
    output_dir: Annotated[
        Path, typer.Option("--out", file_okay=False, resolve_path=True, help="Выходы.")
    ],
    workdir: Annotated[
        Path | None, typer.Option(help="Служебный каталог; по умолчанию runs/<run_id>.")
    ] = None,
    run_id: Annotated[str | None, typer.Option(help="Идентификатор запуска.")] = None,
    budget: Annotated[
        int | None, typer.Option(help="Бюджет токенов; по умолчанию spec.token_budget.")
    ] = None,
) -> None:
    """Запустить одного агента (и его шлюз) на каталоге входов."""
    load_dotenv(find_dotenv(usecwd=True))  # .env текущего каталога, не каталога скрипта
    configure_logging()
    try:
        agent = load_agent(name)
    except RegistryError as exc:
        typer.echo(f"Ошибка: {exc}", err=True)
        raise typer.Exit(2) from exc

    spec = agent.spec
    try:
        llm = NullLLM() if spec.llm_free else build_llm()
    except LLMError as exc:
        typer.echo(f"Ошибка настройки LLM: {exc}", err=True)
        raise typer.Exit(2) from exc

    run_id = run_id or new_run_id()
    workdir = (workdir or Path("runs") / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    bus = EventBus()
    bus.subscribe(_log_event)
    ctx = AgentContext(
        run_id=run_id,
        input_dir=input_dir,
        output_dir=output_dir,
        workdir=workdir,
        llm=llm,
        budget_tokens=budget or spec.token_budget,
        emit=bus.emit,
    )
    result = execute_stage(agent, ctx)
    (workdir / f"{name}.result.json").write_text(result.model_dump_json(indent=2), "utf-8")
    typer.echo(result.model_dump_json(indent=2))
    ok = result.status in (StageStatus.passed, StageStatus.degraded)
    raise typer.Exit(0 if ok else 1)


# --- конвейер ---------------------------------------------------------------------------------


def build_registry() -> Registry:
    """Точка подмены реестра в тестах."""
    return Registry.discover()


@app.command("list-agents")
def list_pipeline_agents() -> None:
    """Агенты конвейера, проверка реестра и план шагов."""
    try:
        registry = build_registry()
    except RegistryError as exc:
        typer.echo(f"Ошибка: {exc}", err=True)
        raise typer.Exit(1) from exc
    for name, spec in registry.specs.items():
        extras = [
            f"после {','.join(spec.depends_on)}" if spec.depends_on else "",
            f"группа {spec.parallel_group}" if spec.parallel_group else "",
            f"если {spec.enabled_if}" if spec.enabled_if else "",
        ]
        details = "; ".join(e for e in extras if e)
        typer.echo(f"{name:24} {spec.kind:10} {spec.stage:16} {details}")
    profile = registry.profile_agent
    plan = [f"profile({profile.spec.name})" if profile else "profile(флаги CLI)"]
    plan += [" | ".join(step) for step in registry.steps]
    typer.echo("\nПлан: " + " → ".join([*plan, "finalize"]))


def run_config_from_env(llm: LLMClient) -> RunConfig:
    env = os.environ
    fields = RunConfig.model_fields
    return RunConfig(
        llm_base_url=llm.config.base_url,
        model=llm.config.model,
        global_token_budget=int(
            env.get("SPECFORGE_TOKEN_BUDGET", fields["global_token_budget"].default)
        ),
        wall_clock_limit_s=float(
            env.get("SPECFORGE_WALL_CLOCK_S", fields["wall_clock_limit_s"].default)
        ),
        max_concurrency=int(
            env.get("SPECFORGE_MAX_CONCURRENCY", fields["max_concurrency"].default)
        ),
    )


@dataclass
class _Pipeline:
    registry: Registry
    config: GraphConfig
    graph: PipelineGraph


@contextmanager
def _pipeline(
    run_id: str, *, use_cache: bool, budget: int | None, time_limit: float | None
) -> Iterator[_Pipeline]:
    load_dotenv(find_dotenv(usecwd=True))
    configure_logging()
    try:
        registry = build_registry()
        llm = build_llm()
    except (RegistryError, LLMError) as exc:
        typer.echo(f"Ошибка: {exc}", err=True)
        raise typer.Exit(2) from exc
    run = run_config_from_env(llm)
    updates: dict[str, int | float] = {}
    if budget is not None:
        updates["global_token_budget"] = budget
    if time_limit is not None:
        updates["wall_clock_limit_s"] = time_limit
    run = run.model_copy(update=updates)
    bus = EventBus()
    bus.subscribe(_log_event)
    with open_checkpointer() as checkpointer:
        config = GraphConfig(
            run=run,
            llm=llm,
            runs_dir=Path(os.environ.get("SPECFORGE_RUNS_DIR", "runs")),
            emit=bus.emit,
            checkpointer=checkpointer,
            use_cache=use_cache and os.environ.get("SPECFORGE_STAGE_CACHE", "true") != "false",
            run_id=run_id,
        )
        yield _Pipeline(registry, config, build_graph(registry, config))


def _finish(state: GraphState) -> None:
    summary = summarize(state)
    typer.echo(summary.model_dump_json(indent=2))
    raise typer.Exit(1 if summary.counts[StageStatus.failed.value] else 0)


@app.command("run")
def run_pipeline(
    requirements: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, resolve_path=True, help="Файл ТЗ.")
    ],
    run_id: Annotated[str | None, typer.Option(help="Идентификатор запуска.")] = None,
    mobile: Annotated[bool, typer.Option("--mobile", help="has_mobile в профиле.")] = False,
    web: Annotated[bool, typer.Option("--web", help="has_web в профиле.")] = False,
    backend: Annotated[bool, typer.Option("--backend", help="has_backend в профиле.")] = False,
    geo: Annotated[bool, typer.Option("--geo", help="has_geo в профиле.")] = False,
    events: Annotated[bool, typer.Option("--events", help="has_events в профиле.")] = False,
    integrations: Annotated[
        bool, typer.Option("--integrations", help="has_integrations в профиле.")
    ] = False,
    flag: Annotated[
        list[str] | None, typer.Option("--flag", help="Флаг CaseProfile.extra (повторяемый).")
    ] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Без кэша этапов.")] = False,
    budget: Annotated[int | None, typer.Option(help="Глобальный бюджет токенов.")] = None,
    time_limit: Annotated[float | None, typer.Option(help="Лимит времени конвейера, с.")] = None,
) -> None:
    """Запустить конвейер. Флаги профиля — если нет агента этапа profile."""
    run_id = run_id or new_run_id()
    profile = CaseProfile(
        has_mobile=mobile,
        has_web=web,
        has_backend=backend,
        has_geo=geo,
        has_events=events,
        has_integrations=integrations,
        extra=dict.fromkeys(flag or [], True),
    )
    with _pipeline(run_id, use_cache=not no_cache, budget=budget, time_limit=time_limit) as p:
        state = start_run(
            p.graph, p.registry, p.config, run_id=run_id, requirements=requirements, profile=profile
        )
    _finish(state)


@app.command("resume")
def resume_pipeline(
    run_id: Annotated[str, typer.Argument(help="Идентификатор запуска.")],
    from_stage: Annotated[
        str | None, typer.Option("--from", help="Перезапустить с этого агента или этапа.")
    ] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Без кэша этапов.")] = False,
) -> None:
    """Продолжить запуск с последнего чекпойнта (нужен SPECFORGE_PG_DSN)."""
    with _pipeline(run_id, use_cache=not no_cache, budget=None, time_limit=None) as p:
        try:
            state = resume_run(p.graph, p.registry, p.config, run_id=run_id, from_stage=from_stage)
        except OrchestratorError as exc:
            typer.echo(f"Ошибка: {exc}", err=True)
            raise typer.Exit(2) from exc
    _finish(state)


if __name__ == "__main__":
    app()
