"""Граф конвейера на LangGraph.

    START → profile → шаг 1 → шаг 2 → … → finalize → END

Шаг — один агент или параллельная группа (`parallel_group`): fan-out от предыдущего шага на
всех членов группы и fan-in в следующий. План шагов детерминирован (Registry.steps). Каждый узел
выполняется всегда и сам решает, запускать ли агента (зависимости, enabled_if, бюджет, кэш), —
поэтому падение этапа не останавливает конвейер, а join параллельной группы всегда срабатывает.

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
from psycopg import Connection
from psycopg.rows import dict_row

from specforge.core.agent_api import EmitFn
from specforge.core.artifacts import RunWorkspace
from specforge.core.contracts import CaseProfile, RunConfig, Usage
from specforge.core.events import Event
from specforge.llm import LLMClient
from specforge.orchestrator.budget import BudgetController, BudgetLimits
from specforge.orchestrator.cache import StageCache
from specforge.orchestrator.nodes import GraphState, PipelineNodes
from specforge.orchestrator.registry import PROFILE_STAGE, Registry, RegistryError

log = structlog.get_logger(__name__)

FINALIZE = "finalize"
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
    previous: list[str] = [PROFILE_STAGE]
    for step in registry.steps:
        for name in step:
            # замыкание узла mypy не сопоставляет с перегрузками add_node
            graph.add_node(name, cast(Any, nodes.stage(name)))
            graph.add_edge(_join(previous), name)
        previous = list(step)
    graph.add_node(FINALIZE, nodes.finalize)
    graph.add_edge(_join(previous), FINALIZE)
    graph.add_edge(FINALIZE, END)
    return graph.compile(checkpointer=config.checkpointer)


def _join(nodes: list[str]) -> str | list[str]:
    """Ребро от одного узла или fan-in от всех узлов параллельного шага."""
    return nodes[0] if len(nodes) == 1 else nodes


def thread_config(run_id: str, registry: Registry, run: RunConfig) -> RunnableConfig:
    return {
        "configurable": {"thread_id": run_id},
        "max_concurrency": run.max_concurrency,
        # по суперстепу на шаг + START/profile/finalize и запас
        "recursion_limit": len(registry.steps) + 10,
    }


def initial_state(
    run_id: str, workspace: RunWorkspace, requirements: Path, profile: CaseProfile
) -> GraphState:
    return {
        "run_id": run_id,
        "requirements_path": str(requirements),
        "workdir": str(workspace.root),
        "profile": profile,
        "stage_results": {},
        "usage_total": Usage(),
        "stage_clock": {},
    }


def start_run(
    graph: PipelineGraph,
    registry: Registry,
    config: GraphConfig,
    *,
    run_id: str,
    requirements: Path,
    profile: CaseProfile | None = None,
) -> GraphState:
    """Новый запуск: runs/<run_id>/, ТЗ — в artifacts/input/, профиль по умолчанию — из CLI."""
    workspace = RunWorkspace.for_run(config.runs_dir, run_id)
    workspace.prepare(requirements)
    state = initial_state(run_id, workspace, requirements.resolve(), profile or CaseProfile())
    return _invoke(graph, state, thread_config(run_id, registry, config.run))


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
    elif not snapshot.next:
        log.info("resume.already_finished", run_id=run_id)
        return cast(GraphState, snapshot.values)
    return _invoke(graph, None, cfg)


def _invoke(graph: PipelineGraph, state: GraphState | None, cfg: RunnableConfig) -> GraphState:
    return cast(GraphState, graph.invoke(state, config=cfg))


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
