"""Граф конвейера на LangGraph.

    START → profile → approve_0 → шаг 1 → approve_1 → … → finalize → END

Шаг — один агент или параллельная группа (`parallel_group`): fan-out от предыдущего шага на
всех членов группы и fan-in в следующий. План шагов детерминирован (Registry.steps). Каждый узел
выполняется всегда и сам решает, запускать ли агента (зависимости, enabled_if, бюджет, кэш), —
поэтому падение этапа не останавливает конвейер, а join параллельной группы всегда срабатывает.

Узел approve_<i> после каждого шага — проход насквозь, кроме шага с этапом из
`--approve-after`: там граф останавливается (interrupt LangGraph) до `approve_run`, которому
можно передать правки артефактов. `--only` — запускаются только перечисленные этапы и их
зависимости, остальные skipped.

Чекпойнт пишется после каждого шага (PostgresSaver, thread_id = run_id): `resume_run` продолжает
с места падения процесса, `from_stage` — перезапускает конвейер с указанного этапа.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from psycopg import Connection
from psycopg.rows import dict_row

from specforge.core.agent_api import EmitFn
from specforge.core.artifacts import RunWorkspace
from specforge.core.contracts import CaseProfile, RunConfig, Usage
from specforge.core.events import Event, EventType
from specforge.llm import LLMClient
from specforge.orchestrator.budget import BudgetController, BudgetLimits
from specforge.orchestrator.cache import StageCache
from specforge.orchestrator.nodes import GraphState, PipelineNodes
from specforge.orchestrator.registry import PROFILE_STAGE, Registry, RegistryError
from specforge.tools.docs import DocumentError, extract_text

log = structlog.get_logger(__name__)

FINALIZE = "finalize"
REQUIREMENTS_TEXT = "requirements.md"
"""Текст ТЗ (извлечённый из pdf/docx) в artifacts/input/."""
CACHE_DIR = ".cache"

PipelineGraph = CompiledStateGraph[GraphState, Any, GraphState, GraphState]

# Типы, которые checkpointer восстанавливает из msgpack (остальные — запрещены).
SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ("specforge.core.contracts", name)
        for name in (
            "CaseProfile",
            "StageResult",
            "StageStatus",
            "Usage",
            "GateResult",
            "Diagnostic",
        )
    ]
)


class OrchestratorError(Exception):
    pass


def _discard(event: Event) -> None:
    del event


@dataclass(kw_only=True)
class GraphConfig:
    """Всё, что нужно графу помимо реестра. В чекпойнт не попадает."""

    run: RunConfig
    llm: LLMClient
    runs_dir: Path = Path("runs")
    emit: EmitFn = _discard
    checkpointer: BaseCheckpointSaver[Any] | None = None
    budget: BudgetLimits | None = None
    """None — из RunConfig без лимитов этапов."""
    use_cache: bool = True
    cache_dir: Path | None = None
    """None — <runs_dir>/.cache."""
    run_id: str | None = None
    """Для событий бюджета; узлы берут run_id из состояния."""


def build_graph(registry: Registry, config: GraphConfig) -> PipelineGraph:
    limits = config.budget or BudgetLimits.from_run_config(config.run)
    nodes = PipelineNodes(
        registry,
        llm=config.llm,
        budget=BudgetController(limits, run_id=config.run_id, emit=config.emit),
        emit=config.emit,
        cache=StageCache(config.cache_dir or config.runs_dir / CACHE_DIR)
        if config.use_cache
        else None,
    )
    graph = StateGraph(GraphState)
    graph.add_node(PROFILE_STAGE, nodes.profile)
    graph.add_edge(START, PROFILE_STAGE)
    steps: list[tuple[str, ...]] = [(PROFILE_STAGE,), *registry.steps]
    for index, step in enumerate(steps):
        gate = approval_name(index)
        # замыкания узлов mypy не сопоставляет с перегрузками add_node
        graph.add_node(gate, cast(Any, nodes.approval(step)))
        graph.add_edge(step[0] if len(step) == 1 else list(step), gate)
        following = steps[index + 1] if index + 1 < len(steps) else (FINALIZE,)
        for name in following:
            if name != FINALIZE:
                graph.add_node(name, cast(Any, nodes.stage(name)))
            graph.add_edge(gate, name)
    graph.add_node(FINALIZE, nodes.finalize)
    graph.add_edge(FINALIZE, END)
    return graph.compile(checkpointer=config.checkpointer)


def approval_name(index: int) -> str:
    """Узел подтверждения после шага `index` (0 — после profile)."""
    return f"approve_{index}"


def thread_config(run_id: str, registry: Registry, run: RunConfig) -> RunnableConfig:
    return {
        "configurable": {"thread_id": run_id},
        "max_concurrency": run.max_concurrency,
        # два суперстепа на шаг (этапы + подтверждение) + START/finalize и запас
        "recursion_limit": 2 * (len(registry.steps) + 1) + 10,
    }


def initial_state(
    run_id: str,
    workspace: RunWorkspace,
    requirements: Path,
    profile: CaseProfile,
    *,
    only: list[str] | None = None,
    approve_after: str | None = None,
) -> GraphState:
    return {
        "run_id": run_id,
        "requirements_path": str(requirements),
        "workdir": str(workspace.root),
        "profile": profile,
        "stage_results": {},
        "usage_total": Usage(),
        "stage_clock": {},
        "only": only,
        "approve_after": approve_after,
        "approvals": {},
    }


def start_run(
    graph: PipelineGraph,
    registry: Registry,
    config: GraphConfig,
    *,
    run_id: str,
    requirements: Path,
    profile: CaseProfile | None = None,
    only: list[str] | None = None,
    approve_after: str | None = None,
) -> GraphState:
    """Новый запуск: runs/<run_id>/, ТЗ — в artifacts/input/ (оригинал и текст в
    requirements.md), профиль по умолчанию — из CLI.

    `only` — имена агентов или этапов; к ним добавляются их зависимости. `approve_after` —
    агент или этап, после шага которого граф ждёт `approve_run`.
    """
    try:
        selected = sorted(registry.closure(only)) if only else None
        gate = registry.node_of(approve_after) if approve_after else None
        text = extract_text(requirements)
    except (RegistryError, DocumentError) as exc:
        raise OrchestratorError(str(exc)) from exc
    workspace = RunWorkspace.for_run(config.runs_dir, run_id)
    workspace.prepare(requirements)
    (workspace.input_dir / REQUIREMENTS_TEXT).write_text(text, encoding="utf-8", newline="")
    state = initial_state(
        run_id,
        workspace,
        requirements.resolve(),
        profile or CaseProfile(),
        only=selected,
        approve_after=gate,
    )
    config.emit(
        Event(
            kind=EventType.run_started,
            run_id=run_id,
            message=requirements.name,
            data={"only": list(selected or []), "approve_after": gate},
        )
    )
    return _invoke(graph, state, thread_config(run_id, registry, config.run), config)


def resume_run(
    graph: PipelineGraph,
    registry: Registry,
    config: GraphConfig,
    *,
    run_id: str,
    from_stage: str | None = None,
) -> GraphState:
    """Продолжить запуск с последнего чекпойнта или перезапустить с этапа `from_stage`.

    `from_stage` (имя агента или этапа) — откат к чекпойнту перед его шагом: этот шаг и все
    следующие выполняются заново (неизменившиеся этапы возьмутся из кэша).
    """
    cfg = thread_config(run_id, registry, config.run)
    snapshot = graph.get_state(cfg)
    if not snapshot.values:
        raise OrchestratorError(
            f"нет чекпойнта запуска {run_id!r} (чекпойнты между процессами — в Postgres: "
            "SPECFORGE_PG_DSN)"
        )
    if from_stage is not None:
        try:
            node = registry.node_of(from_stage)
        except RegistryError as exc:
            raise OrchestratorError(str(exc)) from exc
        target = next((s for s in graph.get_state_history(cfg) if node in s.next), None)
        if target is None:
            raise OrchestratorError(f"в истории запуска {run_id!r} нет шага {node!r}")
        cfg = {**cfg, "configurable": {**target.config.get("configurable", {})}}
        log.info("resume.from_stage", run_id=run_id, node=node)
    elif snapshot.interrupts:
        raise OrchestratorError(f"запуск {run_id!r} ждёт подтверждения (approve)")
    elif not snapshot.next:
        log.info("resume.already_finished", run_id=run_id)
        return cast(GraphState, snapshot.values)
    return _invoke(graph, None, cfg, config)


def pending_approval(
    graph: PipelineGraph, registry: Registry, config: GraphConfig, run_id: str
) -> dict[str, Any] | None:
    """Данные остановки на подтверждение (interrupt) или None."""
    snapshot = graph.get_state(thread_config(run_id, registry, config.run))
    for item in snapshot.interrupts:
        if isinstance(item.value, dict):
            return cast(dict[str, Any], item.value)
    return None


def validate_approval(
    graph: PipelineGraph,
    registry: Registry,
    config: GraphConfig,
    run_id: str,
    edits: dict[str, str] | None,
) -> None:
    """OrchestratorError, если запуск не ждёт подтверждения или путь правки недопустим."""
    if pending_approval(graph, registry, config, run_id) is None:
        raise OrchestratorError(f"запуск {run_id!r} не ждёт подтверждения")
    snapshot = graph.get_state(thread_config(run_id, registry, config.run))
    workspace = RunWorkspace(Path(snapshot.values["workdir"]))
    for rel in edits or {}:
        try:
            workspace.artifact_path(rel)
        except ValueError as exc:
            raise OrchestratorError(str(exc)) from exc


def approve_run(
    graph: PipelineGraph,
    registry: Registry,
    config: GraphConfig,
    *,
    run_id: str,
    edits: dict[str, str] | None = None,
) -> GraphState:
    """Подтвердить остановку и продолжить; `edits` — {путь в artifacts/: новое содержимое}."""
    validate_approval(graph, registry, config, run_id, edits)
    cfg = thread_config(run_id, registry, config.run)
    return _invoke(graph, Command(resume={"edits": edits or {}}), cfg, config)


def _invoke(
    graph: PipelineGraph,
    state: GraphState | Command[Any] | None,
    cfg: RunnableConfig,
    config: GraphConfig,
) -> GraphState:
    graph.invoke(state, config=cfg)
    thread = {"configurable": {"thread_id": cfg["configurable"]["thread_id"]}}
    snapshot = graph.get_state(cast(RunnableConfig, thread))
    for item in snapshot.interrupts:
        payload = item.value if isinstance(item.value, dict) else {"value": str(item.value)}
        config.emit(
            Event(
                kind=EventType.approval_required,
                run_id=str(cfg["configurable"]["thread_id"]),
                stage=payload.get("stage"),
                agent=payload.get("after"),
                message=f"ждёт подтверждения после {payload.get('after')}",
                data=payload,
            )
        )
    return cast(GraphState, snapshot.values)


@contextmanager
def open_checkpointer(dsn: str | None = None) -> Iterator[BaseCheckpointSaver[Any]]:
    """PostgresSaver по SPECFORGE_PG_DSN; без DSN — InMemorySaver (resume между процессами
    невозможен)."""
    dsn = dsn if dsn is not None else os.environ.get("SPECFORGE_PG_DSN", "")
    if not dsn:
        log.warning("checkpoint.in_memory", reason="SPECFORGE_PG_DSN не задан")
        yield InMemorySaver(serde=SERDE)
        return
    with Connection.connect(
        dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = PostgresSaver(conn, serde=SERDE)
        saver.setup()
        yield saver
