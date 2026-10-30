"""Сервис запусков — общий для CLI и API: старт (в фоне или синхронно), resume, approve,
статус, граф для консоли, отчёт."""

import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from specforge.core.contracts import CaseProfile, RunConfig, StageResult, StageStatus, Usage
from specforge.core.events import (
    EventBus,
    EventStore,
    InMemoryEventStore,
    PostgresEventStore,
    SqliteEventStore,
    StoredEvent,
)
from specforge.llm import LLMClient
from specforge.orchestrator.graph import (
    FINALIZE,
    GraphConfig,
    OrchestratorError,
    PipelineGraph,
    approve_run,
    build_graph,
    open_checkpointer,
    pending_approval,
    resume_run,
    start_run,
    validate_approval,
)
from specforge.orchestrator.nodes import PIPELINE_FILE, GraphState, PipelineSummary, summarize
from specforge.orchestrator.registry import PROFILE_STAGE, Registry

log = structlog.get_logger(__name__)

RunStatus = Literal["running", "waiting_approval", "finished", "failed", "interrupted", "unknown"]
"""interrupted — процесс упал посреди запуска, можно resume; failed — исключение в графе."""


class RunOptions(BaseModel):
    profile: CaseProfile = Field(default_factory=CaseProfile)
    only: list[str] | None = None
    approve_after: str | None = None
    """None — автопилот (без остановок)."""


class RunInfo(BaseModel):
    run_id: str
    status: RunStatus
    requirements: str | None = None
    profile: CaseProfile | None = None
    stages: dict[str, StageResult] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    pending_approval: dict[str, Any] | None = None
    error: str | None = None


class GraphNode(BaseModel):
    id: str
    kind: Literal["profile", "stage", "finalize"]
    agent: str | None = None
    stage: str | None = None
    status: StageStatus | Literal["pending", "running", "waiting_approval"] = "pending"
    parallel_group: str | None = None
    enabled_if: str | None = None
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0
    attempts: int = 0
    summary: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str


class GraphView(BaseModel):
    """Граф для отрисовки в консоли: логические рёбра depends_on + profile/finalize."""

    run_id: str
    status: RunStatus
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    steps: list[list[str]]
    """Порядок выполнения: шаги (параллельные группы — по несколько узлов)."""


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


class RunService:
    def __init__(self, registry: Registry, config: GraphConfig, bus: EventBus) -> None:
        self.registry = registry
        self.config = config
        self.bus = bus
        if bus.store is None:
            bus.store = InMemoryEventStore()
        self.graph: PipelineGraph = build_graph(registry, config)
        self._threads: dict[str, threading.Thread] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    # --- запуск -------------------------------------------------------------------------------

    def start(
        self,
        requirements: Path,
        options: RunOptions | None = None,
        *,
        run_id: str | None = None,
        background: bool = False,
    ) -> str:
        options = options or RunOptions()
        run_id = run_id or new_run_id()

        def work() -> None:
            start_run(
                self.graph,
                self.registry,
                self.config,
                run_id=run_id,
                requirements=requirements,
                profile=options.profile,
                only=options.only,
                approve_after=options.approve_after,
            )

        self._submit(run_id, work, background)
        return run_id

    def resume(
        self, run_id: str, from_stage: str | None = None, *, background: bool = False
    ) -> None:
        self._submit(
            run_id,
            lambda: resume_run(
                self.graph, self.registry, self.config, run_id=run_id, from_stage=from_stage
            ),
            background,
        )

    def approve(
        self, run_id: str, edits: dict[str, str] | None = None, *, background: bool = False
    ) -> None:
        if self.is_active(run_id):
            raise OrchestratorError(f"запуск {run_id!r} ещё выполняется")
        validate_approval(self.graph, self.registry, self.config, run_id, edits)
        self._submit(
            run_id,
            lambda: approve_run(self.graph, self.registry, self.config, run_id=run_id, edits=edits),
            background,
        )

    def _submit(self, run_id: str, work: Callable[[], object], background: bool) -> None:
        with self._lock:
            if run_id in self._threads and self._threads[run_id].is_alive():
                raise OrchestratorError(f"запуск {run_id!r} уже выполняется")
            self._errors.pop(run_id, None)
        if not background:
            work()
            return

        def target() -> None:
            try:
                work()
            except Exception as exc:
                with self._lock:  # сначала запомнить: лог может сам упасть (кодировка консоли)
                    self._errors[run_id] = f"{type(exc).__name__}: {exc}"
                log.exception("run.failed", run_id=run_id)

        thread = threading.Thread(target=target, name=f"run-{run_id}", daemon=True)
        with self._lock:
            self._threads[run_id] = thread
        thread.start()

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(run_id)
        return thread is not None and thread.is_alive()

    def wait(self, run_id: str, timeout_s: float = 60.0) -> None:
        with self._lock:
            thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout_s)

    # --- чтение -------------------------------------------------------------------------------

    def state(self, run_id: str) -> GraphState | None:
        snapshot = self.graph.get_state(self._thread(run_id))
        return snapshot.values if snapshot.values else None  # type: ignore[return-value]  # values — dict состояния

    def info(self, run_id: str) -> RunInfo:
        snapshot = self.graph.get_state(self._thread(run_id))
        values: dict[str, Any] = dict(snapshot.values or {})
        with self._lock:
            error = self._errors.get(run_id)
        if not values:
            summary = self._summary_file(run_id)
            if summary is not None:  # запуск из другого процесса без общего хранилища чекпойнтов
                return RunInfo(
                    run_id=run_id,
                    status="finished",
                    profile=summary.profile,
                    stages=summary.stages,
                    usage=summary.usage,
                )
            status: RunStatus = "running" if self.is_active(run_id) else "unknown"
            return RunInfo(run_id=run_id, status="failed" if error else status, error=error)
        pending = pending_approval(self.graph, self.registry, self.config, run_id)
        if self.is_active(run_id):
            status = "running"
        elif error:
            status = "failed"
        elif pending is not None:
            status = "waiting_approval"
        elif not snapshot.next:
            status = "finished"
        else:
            status = "interrupted"
        return RunInfo(
            run_id=run_id,
            status=status,
            requirements=values.get("requirements_path"),
            profile=values.get("profile"),
            stages=values.get("stage_results", {}),
            usage=values.get("usage_total", Usage()),
            pending_approval=pending,
            error=error,
        )

    def list_runs(self) -> list[RunInfo]:
        runs_dir = self.config.runs_dir
        ids = (
            sorted(
                (d.name for d in runs_dir.iterdir() if d.is_dir() and not d.name.startswith(".")),
                reverse=True,
            )
            if runs_dir.is_dir()
            else []
        )
        with self._lock:
            ids += [i for i in self._threads if i not in ids]
        return [self.info(i) for i in ids]

    def events(self, run_id: str, after_id: int = 0) -> list[StoredEvent]:
        assert self.bus.store is not None
        return self.bus.store.list(run_id, after_id=after_id)

    def summary(self, run_id: str) -> PipelineSummary | None:
        state = self.state(run_id)
        if state is not None:
            return summarize(state)
        return self._summary_file(run_id)

    def graph_view(self, run_id: str) -> GraphView:
        info = self.info(run_id)
        pending_after = (info.pending_approval or {}).get("after")
        nodes = [GraphNode(id=PROFILE_STAGE, kind="profile", stage=PROFILE_STAGE)]
        profile = self.registry.profile_agent
        if profile is not None:
            nodes[0] = self._node(PROFILE_STAGE, "profile", profile.spec.name, info)
        for name in [n for step in self.registry.steps for n in step]:
            nodes.append(self._node(name, "stage", name, info))
        nodes.append(GraphNode(id=FINALIZE, kind="finalize"))
        if info.status == "finished":
            nodes[-1].status = StageStatus.passed
        for node in nodes:
            if node.id == pending_after:
                node.status = "waiting_approval"

        edges = []
        for name, spec in self.registry.specs.items():
            if profile is not None and name == profile.spec.name:
                continue
            deps = [d for d in spec.depends_on if profile is None or d != profile.spec.name]
            edges += [GraphEdge(source=d, target=name) for d in deps]
            if not deps:
                edges.append(GraphEdge(source=PROFILE_STAGE, target=name))
        has_dependents = {e.source for e in edges}
        edges += [
            GraphEdge(source=n.id, target=FINALIZE)
            for n in nodes
            if n.kind == "stage" and n.id not in has_dependents
        ]
        if not any(n.kind == "stage" for n in nodes):
            edges.append(GraphEdge(source=PROFILE_STAGE, target=FINALIZE))
        return GraphView(
            run_id=run_id,
            status=info.status,
            nodes=nodes,
            edges=edges,
            steps=[[PROFILE_STAGE], *[list(s) for s in self.registry.steps], [FINALIZE]],
        )

    def _node(
        self, node_id: str, kind: Literal["profile", "stage"], agent: str, info: RunInfo
    ) -> GraphNode:
        spec = self.registry.agents[agent].spec
        node = GraphNode(
            id=node_id,
            kind=kind,
            agent=agent,
            stage=spec.stage,
            parallel_group=spec.parallel_group,
            enabled_if=spec.enabled_if,
        )
        result = info.stages.get(agent)
        if result is not None:
            node.status = result.status
            node.usage = result.usage
            node.duration_s = result.duration_s
            node.attempts = result.attempts
            node.summary = result.summary
        elif info.status == "running":
            started = {
                e.event.agent for e in self.events(info.run_id) if e.event.kind == "stage_started"
            }
            if agent in started:
                node.status = "running"
        return node

    def _summary_file(self, run_id: str) -> PipelineSummary | None:
        path = self.config.runs_dir / run_id / PIPELINE_FILE
        if not path.is_file():
            return None
        return PipelineSummary.model_validate_json(path.read_text(encoding="utf-8"))

    def _thread(self, run_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": run_id}}


EVENTS_SQLITE = "events.sqlite"


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


def event_store_from_env(runs_dir: Path) -> EventStore:
    """Postgres по SPECFORGE_PG_DSN, иначе SQLite в <runs_dir>/events.sqlite."""
    dsn = os.environ.get("SPECFORGE_PG_DSN", "")
    if dsn:
        try:
            return PostgresEventStore(dsn)
        except Exception:
            log.exception("events.postgres_unavailable")
    runs_dir.mkdir(parents=True, exist_ok=True)
    return SqliteEventStore(runs_dir / EVENTS_SQLITE)


@contextmanager
def open_service(
    *,
    registry_factory: Callable[[], Registry] = Registry.discover,
    llm_factory: Callable[[], LLMClient] = LLMClient.from_env,
    use_cache: bool = True,
    budget: int | None = None,
    time_limit: float | None = None,
) -> Iterator[RunService]:
    """RunService по окружению: реестр agents/, LLM_*, SPECFORGE_* и чекпойнты Postgres."""
    registry = registry_factory()
    llm = llm_factory()
    run = run_config_from_env(llm)
    updates: dict[str, int | float] = {}
    if budget is not None:
        updates["global_token_budget"] = budget
    if time_limit is not None:
        updates["wall_clock_limit_s"] = time_limit
    run = run.model_copy(update=updates)
    runs_dir = Path(os.environ.get("SPECFORGE_RUNS_DIR", "runs"))
    bus = EventBus(event_store_from_env(runs_dir))
    with open_checkpointer() as checkpointer:
        config = GraphConfig(
            run=run,
            llm=llm,
            runs_dir=runs_dir,
            emit=bus.emit,
            checkpointer=checkpointer,
            use_cache=use_cache and os.environ.get("SPECFORGE_STAGE_CACHE", "true") != "false",
        )
        yield RunService(registry, config, bus)
