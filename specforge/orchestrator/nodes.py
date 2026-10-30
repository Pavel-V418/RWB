"""Исполнение этапа (песочница → агент → шлюз → ремонт) и узлы графа LangGraph.

`execute_stage` используется CLI `agent run` и узлами графа; `PipelineNodes` — узлы
profile / этапы / finalize с проверкой зависимостей, enabled_if, бюджета и кэша этапов.
"""

import dataclasses
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import structlog
from pydantic import BaseModel, JsonValue

from specforge.core.agent_api import Agent, AgentContext, EmitFn
from specforge.core.artifacts import RunWorkspace
from specforge.core.contracts import (
    AgentSpec,
    CaseProfile,
    PipelineState,
    StageResult,
    StageStatus,
    Usage,
)
from specforge.core.events import Event
from specforge.gates.runner import GateConfigError, GateRunner
from specforge.llm import LLMClient, NullLLM
from specforge.orchestrator.budget import BudgetController, active_seconds
from specforge.orchestrator.cache import StageCache, stage_key
from specforge.orchestrator.registry import Registry
from specforge.repair.loop import repair_loop
from specforge.repair.snapshots import SnapshotError, Snapshotter
from specforge.sandbox.base import BaseSandbox, Sandbox, SandboxError
from specforge.sandbox.docker_sandbox import DockerSandbox
from specforge.sandbox.local_sandbox import LocalSandbox

log = structlog.get_logger(__name__)


def create_sandbox(spec: AgentSpec, workspace: Path, logs_dir: Path) -> BaseSandbox:
    """Песочница этапа по SPECFORGE_SANDBOX: auto (Docker, если задан sandbox_image),
    docker или local."""
    mode = os.environ.get("SPECFORGE_SANDBOX", "auto")
    if mode not in ("auto", "docker", "local"):
        raise SandboxError(f"SPECFORGE_SANDBOX={mode!r}: ожидается auto, docker или local")
    use_docker = mode == "docker" or (mode == "auto" and spec.sandbox_image is not None)
    if use_docker:
        if spec.sandbox_image is None:
            raise SandboxError(f"SPECFORGE_SANDBOX=docker, но у {spec.name} нет sandbox_image")
        return DockerSandbox(
            spec.sandbox_image,
            workspace,
            logs_dir=logs_dir,
            log_prefix=spec.name,
            labels={"specforge.agent": spec.name},
        )
    return LocalSandbox(workspace, logs_dir=logs_dir, log_prefix=spec.name)


def execute_stage(
    agent: Agent, ctx: AgentContext, *, max_repairs: int | None = None
) -> StageResult:
    """Запустить агента и, если он отработал, его шлюз — в одной песочнице этапа, которая
    закрывается (контейнер удаляется) после этапа. Красный шлюз — ремонт (repair_loop).

    `max_repairs` перекрывает AgentSpec.max_repairs (решение BudgetController).
    """
    spec = agent.spec
    if max_repairs is not None and max_repairs != spec.max_repairs:
        spec = spec.model_copy(update={"max_repairs": max_repairs})
    started = time.monotonic()
    # Токены этапа учитываются с метками run/stage/agent в общем UsageTracker.
    ctx = dataclasses.replace(
        ctx,
        llm=ctx.llm.bind(run_id=ctx.run_id, stage=spec.stage, agent=spec.name),
        protected_globs=[*ctx.protected_globs, *spec.protected_globs],
    )
    ctx.emit(Event(kind="stage.started", run_id=ctx.run_id, agent=spec.name))

    own_sandbox: BaseSandbox | None = None
    try:
        if ctx.sandbox is None and (spec.kind == "tool" or spec.gate_commands):
            own_sandbox = create_sandbox(spec, ctx.output_dir, ctx.workdir / "logs")
            ctx = dataclasses.replace(ctx, sandbox=own_sandbox)
        result = _run_agent_and_gate(agent, spec, ctx, started)
    except SandboxError as exc:
        result = StageResult(
            agent=spec.name,
            status=StageStatus.failed,
            duration_s=time.monotonic() - started,
            summary=f"песочница: {exc}",
        )
    finally:
        if own_sandbox is not None:
            try:
                own_sandbox.close()
            except SandboxError:
                log.exception("sandbox.close_failed", agent=spec.name)

    ctx.emit(
        Event(
            kind="stage.finished",
            run_id=ctx.run_id,
            agent=spec.name,
            message=result.summary,
            data={"status": result.status.value, "tokens": result.usage.total_tokens},
        )
    )
    return result


def _run_agent_and_gate(
    agent: Agent, spec: AgentSpec, ctx: AgentContext, started: float
) -> StageResult:
    try:
        result = agent.run(ctx)
    except SandboxError:
        raise
    except Exception as exc:
        log.exception("stage.crashed", agent=spec.name)
        return StageResult(
            agent=spec.name,
            status=StageStatus.failed,
            duration_s=time.monotonic() - started,
            summary=f"{type(exc).__name__}: {exc}",
        )

    if result.status != StageStatus.passed or not spec.gate_commands or ctx.sandbox is None:
        return result
    try:
        gate = GateRunner(logs_dir=ctx.workdir / "logs").run(spec, ctx.sandbox, ctx.run_id)
    except GateConfigError as exc:
        return result.model_copy(update={"status": StageStatus.failed, "summary": f"шлюз: {exc}"})

    if gate.passed:
        _mark_green(ctx.sandbox)
        return result.model_copy(update={"gate": gate})
    if spec.max_repairs == 0:
        return result.model_copy(update={"gate": gate, "status": StageStatus.failed})

    repaired = repair_loop(spec, ctx, gate)
    if repaired.status == StageStatus.passed:
        _mark_green(ctx.sandbox)
    return result.model_copy(
        update={
            "status": repaired.status,
            "gate": repaired.gate,
            "attempts": result.attempts + repaired.attempts,
            "usage": result.usage.add(repaired.usage),
            "summary": f"{result.summary}\nремонт: {repaired.summary}"[:1000],
        }
    )


def _mark_green(sandbox: Sandbox) -> None:
    """Запомнить зелёное состояние workspace — к нему откатится будущий неудачный ремонт."""
    snapshots = Snapshotter(sandbox)
    try:
        if snapshots.available:
            snapshots.mark_green()
    except SnapshotError as exc:
        log.warning("stage.green_snapshot_failed", error=str(exc))


# --- узлы графа -------------------------------------------------------------------------------


def merge_clock(
    left: dict[str, list[float]], right: dict[str, list[float]]
) -> dict[str, list[float]]:
    return {**left, **right}


class GraphState(PipelineState, total=False):
    """PipelineState + служебные поля оркестратора."""

    stage_clock: Annotated[dict[str, list[float]], merge_clock]
    """[начало, конец] работы этапов (time.time()) — для бюджета времени."""


NodeUpdate = dict[str, Any]

PROFILE_FILE = "case_profile.json"
"""Файл, который агент этапа profile пишет в свой output_dir (CaseProfile в JSON)."""
PIPELINE_FILE = "pipeline.json"


class PipelineSummary(BaseModel):
    """Итог конвейера — runs/<run_id>/pipeline.json."""

    run_id: str
    profile: CaseProfile
    counts: dict[str, int]
    usage: Usage
    active_s: float
    stages: dict[str, StageResult]


class PipelineNodes:
    """Узлы графа. Зависимости (LLM, шина, бюджет, кэш) живут здесь, а не в состоянии:
    в чекпойнт попадают только данные."""

    def __init__(
        self,
        registry: Registry,
        *,
        llm: LLMClient,
        budget: BudgetController,
        emit: EmitFn,
        cache: StageCache | None = None,
    ) -> None:
        self.registry = registry
        self.llm = llm
        self.budget = budget
        self.emit = emit
        self.cache = cache

    # --- profile ----------------------------------------------------------------------------

    def profile(self, state: GraphState) -> NodeUpdate:
        """Агент этапа profile строит CaseProfile; без него остаётся профиль из флагов CLI."""
        agent = self.registry.profile_agent
        if agent is None:
            self._event("profile.from_cli", state, data=state["profile"].model_dump(mode="json"))
            return {}
        started = time.time()
        result = self._execute(agent, state)
        update = self._update(agent.spec, result, started)
        profile = self._read_profile(agent, state)
        if profile is None:
            self._event(
                "profile.fallback",
                state,
                agent=agent.spec.name,
                message=f"нет корректного {PROFILE_FILE} — профиль из флагов CLI",
            )
        else:
            update["profile"] = profile
            self._event(
                "profile.built", state, agent=agent.spec.name, data=profile.model_dump(mode="json")
            )
        return update

    def _read_profile(self, agent: Agent, state: GraphState) -> CaseProfile | None:
        path = _workspace(state).stage_dir(agent.spec.stage) / PROFILE_FILE
        if not path.is_file():
            return None
        try:
            return CaseProfile.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("profile.invalid", path=str(path))
            return None

    # --- этап -------------------------------------------------------------------------------

    def stage(self, name: str) -> Callable[[GraphState], NodeUpdate]:
        agent = self.registry.agents[name]

        def node(state: GraphState) -> NodeUpdate:
            started = time.time()
            spec = agent.spec
            blocker = self._blocker(spec, state["stage_results"])
            if blocker is not None:
                result = _skipped(spec, blocker, "dependency")
            elif spec.enabled_if and not state["profile"].is_enabled(spec.enabled_if):
                result = _skipped(spec, f"{spec.enabled_if} = false в профиле кейса", "enabled_if")
            else:
                result = self._execute(agent, state)
            if result.status == StageStatus.skipped:
                self._event("stage.skipped", state, agent=spec.name, message=result.summary)
            return self._update(spec, result, started)

        node.__name__ = f"stage_{name}"
        return node

    def _blocker(self, spec: AgentSpec, results: dict[str, StageResult]) -> str | None:
        """Почему этап нельзя запускать из-за зависимостей; None — можно.

        passed — можно; degraded с артефактами — можно (дальше с пометкой);
        failed, skipped или degraded без артефактов — этап пропускается.
        """
        for dep in spec.depends_on:
            result = results.get(dep)
            if result is None:
                return f"зависимость {dep} не выполнялась"
            if result.status == StageStatus.passed:
                continue
            if result.status == StageStatus.degraded and result.artifacts:
                continue
            detail = " без артефактов" if result.status == StageStatus.degraded else ""
            return f"зависимость {dep}: {result.status.value}{detail}"
        return None

    def _execute(self, agent: Agent, state: GraphState) -> StageResult:
        spec = agent.spec
        ws = _workspace(state)
        decision = self.budget.decide(
            spec,
            tokens_used=state["usage_total"].total_tokens,
            elapsed_s=active_seconds(state.get("stage_clock", {}).values()),
        )
        if decision.exhausted:
            return _skipped(spec, decision.exhausted, "budget")

        key = stage_key(agent, ws.artifacts_dir) if self.cache is not None else None
        if self.cache is not None and key is not None:
            cached = self.cache.lookup(agent, key)
            if cached is not None:
                stage_dir = ws.clean_outputs(spec.stage, spec.outputs)
                result = self.cache.restore(agent, key, cached, stage_dir)
                ws.write_manifest(spec.stage, spec.name)
                self._event("stage.cached", state, agent=spec.name, data={"key": key})
                return result

        output_dir = ws.clean_outputs(spec.stage, spec.outputs)
        llm: LLMClient = NullLLM() if spec.llm_free else self.llm
        if decision.thinking_off:
            llm = llm.with_thinking(False)
        ctx = AgentContext(
            run_id=state["run_id"],
            input_dir=ws.artifacts_dir,
            output_dir=output_dir,
            workdir=ws.root,
            llm=llm,
            budget_tokens=decision.token_budget,
            emit=self.emit,
        )
        result = execute_stage(agent, ctx, max_repairs=decision.max_repairs)
        self.budget.after_stage(spec, result)
        if result.status in (StageStatus.passed, StageStatus.degraded):
            ws.write_manifest(spec.stage, spec.name)
        if self.cache is not None and key is not None:
            self.cache.store(agent, key, result, output_dir)
        return result

    def _update(self, spec: AgentSpec, result: StageResult, started: float) -> NodeUpdate:
        return {
            "stage_results": {spec.name: result},
            "usage_total": result.usage,
            "stage_clock": {spec.name: [started, time.time()]},
        }

    # --- finalize ---------------------------------------------------------------------------

    def finalize(self, state: GraphState) -> NodeUpdate:
        summary = summarize(state)
        path = _workspace(state).root / PIPELINE_FILE
        path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        data: dict[str, JsonValue] = {
            "counts": dict(summary.counts),
            "tokens": summary.usage.total_tokens,
        }
        self._event("pipeline.finished", state, data=data)
        return {}

    def _event(
        self,
        kind: str,
        state: GraphState,
        *,
        agent: str | None = None,
        message: str = "",
        data: dict[str, JsonValue] | None = None,
    ) -> None:
        self.emit(
            Event(kind=kind, run_id=state["run_id"], agent=agent, message=message, data=data or {})
        )


def summarize(state: GraphState) -> PipelineSummary:
    results = state["stage_results"]
    return PipelineSummary(
        run_id=state["run_id"],
        profile=state["profile"],
        counts={s.value: sum(r.status == s for r in results.values()) for s in StageStatus},
        usage=state["usage_total"],
        active_s=active_seconds(state.get("stage_clock", {}).values()),
        stages=results,
    )


def _skipped(spec: AgentSpec, reason: str, stopped_reason: str) -> StageResult:
    return StageResult(
        agent=spec.name,
        status=StageStatus.skipped,
        attempts=0,
        summary=reason,
        stopped_reason=stopped_reason,
    )


def _workspace(state: GraphState) -> RunWorkspace:
    return RunWorkspace(Path(state["workdir"]))
