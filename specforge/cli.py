"""CLI платформы.

specforge list-agents                       — реестр, проверка и план шагов конвейера
specforge agent run <name> --in --out       — один агент и его шлюз
specforge run <ТЗ.md|.txt|.pdf|.docx> [...] — весь конвейер (дерево этапов — rich)
specforge resume <run_id> [--from <этап>]   — продолжить запуск / подтвердить остановку
specforge status <run_id>                   — дерево этапов со статусами
specforge report <run_id>                   — отчёт с графиками в runs/<run_id>/report/
"""

import io
import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import structlog
import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.tree import Tree

from specforge.core.agent_api import AgentContext
from specforge.core.contracts import CaseProfile, StageStatus
from specforge.core.events import Event, EventBus, EventType
from specforge.llm import LLMClient, LLMError, NullLLM
from specforge.orchestrator.graph import OrchestratorError
from specforge.orchestrator.nodes import execute_stage
from specforge.orchestrator.registry import (
    Registry,
    RegistryError,
    available_agents,
    load_agent,
)
from specforge.orchestrator.service import (
    GraphView,
    RunInfo,
    RunOptions,
    RunService,
    new_run_id,
    open_service,
)
from specforge.telemetry.report import register_cli
from specforge.tools.docs import SUPPORTED_SUFFIXES

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


@contextmanager
def _service(
    *, use_cache: bool = True, budget: int | None = None, time_limit: float | None = None
) -> Iterator[RunService]:
    load_dotenv(find_dotenv(usecwd=True))
    configure_logging()
    try:
        with open_service(
            registry_factory=build_registry,
            llm_factory=build_llm,
            use_cache=use_cache,
            budget=budget,
            time_limit=time_limit,
        ) as service:
            service.bus.subscribe(_log_event)
            yield service
    except (RegistryError, LLMError) as exc:
        typer.echo(f"Ошибка: {exc}", err=True)
        raise typer.Exit(2) from exc


ICONS = {
    "passed": "[green]✔ passed[/]",
    "degraded": "[yellow]⚠ degraded[/]",
    "failed": "[red]✘ failed[/]",
    "skipped": "[dim]⏭ skipped[/]",
    "running": "[cyan]… running[/]",
    "waiting_approval": "[magenta]⏸ ждёт подтверждения[/]",
    "pending": "[dim]· pending[/]",
}


def render_tree(view: GraphView) -> Tree:
    """Дерево этапов по шагам конвейера со статусами."""
    tree = Tree(f"[bold]{view.run_id}[/] — {view.status}")
    by_id = {n.id: n for n in view.nodes}
    for step in view.steps:
        parent = tree if len(step) == 1 else tree.add("[bold]параллельно[/]")
        for node_id in step:
            node = by_id[node_id]
            status = node.status.value if isinstance(node.status, StageStatus) else node.status
            label = f"{node.agent or node.id}  {ICONS.get(status, status)}"
            if node.usage.total_tokens:
                label += f"  [dim]{node.usage.total_tokens} tok, {node.duration_s:.1f} с[/]"
            if status in ("failed", "degraded", "skipped") and node.summary:
                label += f"\n[dim]{escape(node.summary.splitlines()[0][:120])}[/]"
            parent.add(label)
    return tree


def _drive(service: RunService, run_id: str, work: Callable[[], None]) -> None:
    """Выполнить запуск с живым деревом этапов и завершить с кодом по итогу."""
    console = Console()
    with Live(render_tree(service.graph_view(run_id)), console=console, transient=False) as live:

        def refresh(event: Event) -> None:
            if event.run_id == run_id and event.kind in REFRESH_ON:
                live.update(render_tree(service.graph_view(run_id)))

        unsubscribe = service.bus.subscribe(refresh)
        try:
            work()
        except OrchestratorError as exc:
            live.stop()
            typer.echo(f"Ошибка: {exc}", err=True)
            raise typer.Exit(2) from exc
        finally:
            unsubscribe()
        live.update(render_tree(service.graph_view(run_id)))
    _exit_with(service.info(run_id))


REFRESH_ON = {t.value for t in EventType} | {"stage_cached"}


def _exit_with(info: RunInfo) -> None:
    if info.status == "waiting_approval":
        after = (info.pending_approval or {}).get("after")
        typer.echo(f"Ждёт подтверждения после {after}: specforge resume {info.run_id}")
        raise typer.Exit(0)
    failed = sum(r.status == StageStatus.failed for r in info.stages.values())
    typer.echo(f"Итог: {info.status}; failed: {failed}; токенов: {info.usage.total_tokens}")
    raise typer.Exit(1 if failed or info.status == "failed" else 0)


def _split(value: str | None) -> list[str] | None:
    items = [v.strip() for v in (value or "").split(",") if v.strip()]
    return items or None


@app.command("run")
def run_pipeline(
    requirements: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, resolve_path=True, help="ТЗ: .md, .txt, .pdf, .docx."
        ),
    ],
    run_id: Annotated[str | None, typer.Option(help="Идентификатор запуска.")] = None,
    autopilot: Annotated[
        bool, typer.Option("--autopilot", help="Без остановок на подтверждение (по умолчанию).")
    ] = False,
    approve_after: Annotated[
        str | None, typer.Option("--approve-after", help="Остановиться после этого этапа.")
    ] = None,
    only: Annotated[
        str | None,
        typer.Option("--only", help="Только эти этапы (через запятую) и их зависимости."),
    ] = None,
    budget: Annotated[int | None, typer.Option(help="Глобальный бюджет токенов.")] = None,
    time_limit: Annotated[float | None, typer.Option(help="Лимит времени конвейера, с.")] = None,
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
) -> None:
    """Запустить конвейер. Флаги профиля — если нет агента этапа profile."""
    if autopilot and approve_after:
        typer.echo("Ошибка: --autopilot и --approve-after несовместимы", err=True)
        raise typer.Exit(2)
    if requirements.suffix.lower() not in SUPPORTED_SUFFIXES:
        typer.echo(f"Ошибка: ожидается {', '.join(SUPPORTED_SUFFIXES)}", err=True)
        raise typer.Exit(2)
    run_id = run_id or new_run_id()
    options = RunOptions(
        profile=CaseProfile(
            has_mobile=mobile,
            has_web=web,
            has_backend=backend,
            has_geo=geo,
            has_events=events,
            has_integrations=integrations,
            extra=dict.fromkeys(flag or [], True),
        ),
        only=_split(only),
        approve_after=approve_after,
    )
    with _service(use_cache=not no_cache, budget=budget, time_limit=time_limit) as service:

        def work() -> None:
            service.start(requirements, options, run_id=run_id)

        _drive(service, run_id, work)


@app.command("resume")
def resume_pipeline(
    run_id: Annotated[str, typer.Argument(help="Идентификатор запуска.")],
    from_stage: Annotated[
        str | None, typer.Option("--from", help="Перезапустить с этого агента или этапа.")
    ] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Без кэша этапов.")] = False,
) -> None:
    """Продолжить запуск с чекпойнта; если он ждёт подтверждения — подтвердить без правок."""
    with _service(use_cache=not no_cache) as service:
        if from_stage is None and service.info(run_id).status == "waiting_approval":
            _drive(service, run_id, lambda: service.approve(run_id))
        else:
            _drive(service, run_id, lambda: service.resume(run_id, from_stage))


@app.command("status")
def run_status(run_id: Annotated[str, typer.Argument(help="Идентификатор запуска.")]) -> None:
    """Дерево этапов запуска со статусами."""
    with _service() as service:
        info = service.info(run_id)
        if info.status == "unknown":
            typer.echo(f"Запуск {run_id!r} не найден", err=True)
            raise typer.Exit(2)
        Console().print(render_tree(service.graph_view(run_id)))
        if info.error:
            typer.echo(f"Ошибка: {info.error}")


register_cli(app)  # report <run_id> → runs/<run_id>/report/

if __name__ == "__main__":
    app()
