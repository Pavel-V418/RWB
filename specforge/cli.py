"""CLI платформы: `specforge agent run <name> --in <dir> --out <dir>`."""

import io
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import structlog
import typer
from dotenv import find_dotenv, load_dotenv

from specforge.core.agent_api import AgentContext
from specforge.core.contracts import StageStatus
from specforge.core.events import Event, EventBus
from specforge.llm import LLMClient, LLMError, NullLLM
from specforge.orchestrator.nodes import execute_stage
from specforge.orchestrator.registry import RegistryError, available_agents, load_agent

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


if __name__ == "__main__":
    app()
