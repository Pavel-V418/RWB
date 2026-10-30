"""Исполнение этапа (песочница → агент → шлюз → ремонт) и узлы графа LangGraph.

`execute_stage` используется CLI `agent run` и узлами графа; `PipelineNodes` — узлы
profile / этапы / finalize с проверкой зависимостей, enabled_if, бюджета и кэша этапов.
"""

import dataclasses
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import structlog
from langgraph.types import interrupt
from pydantic import BaseModel, Field, JsonValue

from specforge.core.agent_api import Agent, AgentContext, EmitFn
from specforge.core.artifacts import RunWorkspace, glob_files
from specforge.core.contracts import (
    AgentSpec,
    CaseProfile,
    ClarificationQuestion,
    PipelineState,
    StageResult,
    StageStatus,
    Usage,
)
from specforge.core.events import Event, EventType
from specforge.gates.infra import INFRA_MAX_RETRIES
from specforge.gates.runner import GateConfigError, GateRunner, diagnostics_digest
from specforge.llm import LLMClient, NullLLM
from specforge.orchestrator.budget import BudgetController, active_seconds
from specforge.orchestrator.cache import StageCache, stage_key
from specforge.orchestrator.clarify import read_questions, record_answers
from specforge.orchestrator.registry import PROFILE_STAGE, Registry
from specforge.repair.loop import repair_loop
from specforge.repair.snapshots import SnapshotError, Snapshotter
from specforge.sandbox.base import BaseSandbox, Sandbox, SandboxError
from specforge.sandbox.docker_sandbox import DockerSandbox
from specforge.sandbox.local_sandbox import LocalSandbox

log = structlog.get_logger(__name__)

FINISHED_EVENT = {
    StageStatus.passed: EventType.stage_passed,
    StageStatus.degraded: EventType.stage_degraded,
    StageStatus.failed: EventType.stage_failed,
    StageStatus.skipped: EventType.stage_skipped,
}


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
        llm=ctx.llm.bind(run_id=ctx.run_id, stage=spec.stage, agent=spec.name)
        .for_agent(spec.name)
        .with_emit(ctx.emit),
        protected_globs=[*ctx.protected_globs, *spec.protected_globs],
    )
    ctx.emit(
        Event(
            kind=EventType.stage_started,
            run_id=ctx.run_id,
            stage=spec.stage,
            agent=spec.name,
        )
    )

    timeout = spec.timeout_s or stage_timeout_s()
    deadline = started + timeout
    ctx = dataclasses.replace(ctx, deadline=deadline)
    retries = 0
    while True:
        result, infra = _attempt(agent, spec, ctx, started)
        if infra is None or retries >= INFRA_MAX_RETRIES or time.monotonic() >= deadline:
            break
        retries += 1
        ctx.emit(
            Event(
                kind="stage_retry",
                run_id=ctx.run_id,
                stage=spec.stage,
                agent=spec.name,
                message=f"инфраструктурный сбой, повтор {retries}/{INFRA_MAX_RETRIES}: {infra}",
                data={"retry": retries, "reason": infra},
            )
        )
    if infra is not None:
        result = result.model_copy(
            update={"stopped_reason": "infra", "summary": f"инфраструктура: {infra}"[:1000]}
        )
    if ctx.llm.llm_unavailable_exhausted:
        result = result.model_copy(
            update={
                "status": StageStatus.failed,
                "stopped_reason": LLM_UNAVAILABLE,
                "summary": f"LLM недоступна дольше LLM_WAIT_MAX_S: {result.summary}"[:1000],
            }
        )

    ctx.emit(
        Event(
            kind=FINISHED_EVENT[result.status],
            run_id=ctx.run_id,
            stage=spec.stage,
            agent=spec.name,
            message=result.summary,
            data={"status": result.status.value, "tokens": result.usage.total_tokens},
        )
    )
    return result


LLM_UNAVAILABLE = "llm_unavailable"
"""stopped_reason этапа, на котором LLM не ответила за LLM_WAIT_MAX_S: resume начнёт с него."""
DEFAULT_STAGE_TIMEOUT_S = 3600.0
DEFAULT_TIMEOUT_GRACE_S = 30.0


def stage_timeout_s() -> float:
    return float(os.environ.get("SPECFORGE_STAGE_TIMEOUT_S", DEFAULT_STAGE_TIMEOUT_S))


def timeout_grace_s() -> float:
    """Сколько ждать агента после дедлайна: agent_loop и ремонт останавливаются сами между шагами,
    сторожевой поток — на случай зависшего вызова."""
    return float(os.environ.get("SPECFORGE_STAGE_TIMEOUT_GRACE_S", DEFAULT_TIMEOUT_GRACE_S))


def _attempt(
    agent: Agent, spec: AgentSpec, ctx: AgentContext, started: float
) -> tuple[StageResult, str | None]:
    """Одна попытка этапа в своей песочнице: (результат, причина инфраструктурного сбоя | None).

    Агент, шлюз и ремонт идут в сторожевом потоке: истёк дедлайн (+ grace) — откат к последнему
    зелёному снимку и degraded / failed по наличию артефактов.
    """
    own_sandbox: BaseSandbox | None = None
    try:
        if ctx.sandbox is None and (spec.kind == "tool" or spec.gate_commands):
            own_sandbox = create_sandbox(spec, ctx.output_dir, ctx.workdir / "logs")
            ctx = dataclasses.replace(ctx, sandbox=own_sandbox)
        results: list[StageResult] = []
        errors: list[BaseException] = []

        def work() -> None:
            try:
                results.append(_run_agent_and_gate(agent, spec, ctx, started))
            except BaseException as exc:  # переносится в поток этапа
                errors.append(exc)

        worker = threading.Thread(target=work, name=f"stage-{spec.name}", daemon=True)
        worker.start()
        assert ctx.deadline is not None
        worker.join(max(ctx.deadline - time.monotonic(), 0.0) + timeout_grace_s())
        if worker.is_alive():
            log.error("stage.timeout_abandoned", agent=spec.name)
            return _timed_out(spec, ctx, started), None
        if errors:
            raise errors[0]
        result = results[0]
        if result.stopped_reason == "timeout" or (
            time.monotonic() >= ctx.deadline and result.status != StageStatus.passed
        ):
            return _timed_out(spec, ctx, started, result), None
        infra = result.gate.infra_error if result.gate is not None else None
        return result, infra
    except SandboxError as exc:
        result = StageResult(
            agent=spec.name,
            status=StageStatus.failed,
            duration_s=time.monotonic() - started,
            summary=f"песочница: {exc}",
        )
        return result, f"песочница: {exc}"[:300]
    finally:
        if own_sandbox is not None:
            try:
                own_sandbox.close()
            except SandboxError:
                log.exception("sandbox.close_failed", agent=spec.name)


def _timed_out(
    spec: AgentSpec, ctx: AgentContext, started: float, partial: StageResult | None = None
) -> StageResult:
    """Таймаут этапа: откат к specforge-green-<agent>, если он есть; degraded, если после отката
    есть артефакты, иначе failed."""
    restored = False
    if ctx.sandbox is not None:
        snapshots = Snapshotter(ctx.sandbox, spec.name)
        try:
            green = snapshots.green() if snapshots.available else None
            if green:
                snapshots.restore(green)
                restored = True
        except SnapshotError as exc:
            log.warning("stage.timeout_restore_failed", agent=spec.name, error=str(exc))
    artifacts = [
        p.relative_to(ctx.output_dir).as_posix() for p in glob_files(ctx.output_dir, spec.outputs)
    ]
    timeout = (ctx.deadline or started) - started
    summary = f"таймаут этапа {timeout:.0f} с" + (
        "; откат к последнему зелёному снимку" if restored else ""
    )
    ctx.emit(
        Event(
            kind="stage_timeout",
            run_id=ctx.run_id,
            stage=spec.stage,
            agent=spec.name,
            message=summary,
            data={"timeout_s": round(timeout, 1), "restored_green": restored},
        )
    )
    return StageResult(
        agent=spec.name,
        status=StageStatus.degraded if artifacts else StageStatus.failed,
        attempts=partial.attempts if partial else 1,
        usage=partial.usage if partial else ctx.llm.usage,
        duration_s=time.monotonic() - started,
        gate=partial.gate if partial else None,
        artifacts=artifacts,
        summary=summary,
        stopped_reason="timeout",
    )


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
        _mark_green(ctx.sandbox, spec.name)
        return result.model_copy(update={"gate": gate})
    ctx.emit(
        Event(
            kind=EventType.gate_failed,
            run_id=ctx.run_id,
            stage=spec.stage,
            agent=spec.name,
            message=gate.diagnostics[0].message[:300] if gate.diagnostics else "",
            data={
                "total_errors": gate.total_errors,
                "failed_command_index": gate.failed_command_index,
                "infra_error": gate.infra_error,
                "diagnostics": diagnostics_digest(gate),
            },
        )
    )
    # ремонт инфраструктуру не починит — этап повторит execute_stage (stage_retry)
    if spec.max_repairs == 0 or gate.infra_error:
        return result.model_copy(update={"gate": gate, "status": StageStatus.failed})

    repaired = repair_loop(spec, ctx, gate)
    if repaired.status == StageStatus.passed:
        _mark_green(ctx.sandbox, spec.name)
    return result.model_copy(
        update={
            "status": repaired.status,
            "gate": repaired.gate,
            "attempts": result.attempts + repaired.attempts,
            "usage": result.usage.add(repaired.usage),
            "summary": f"{result.summary}\nремонт: {repaired.summary}"[:1000],
        }
    )


def _mark_green(sandbox: Sandbox, agent: str) -> None:
    """Запомнить зелёное состояние workspace (specforge-green-<agent>) — к нему откатятся
    неудачный ремонт и этап по таймауту."""
    snapshots = Snapshotter(sandbox, agent)
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


def merge_approvals(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    return {**left, **right}


class GraphState(PipelineState, total=False):
    """PipelineState + служебные поля оркестратора."""

    stage_clock: Annotated[dict[str, list[float]], merge_clock]
    """[начало, конец] работы этапов (time.time()) — для бюджета времени."""
    only: list[str] | None
    """Агенты, которые запускаются (--only с зависимостями); None — все."""
    approve_after: str | None
    """Узел (агент или profile), после шага которого граф ждёт подтверждения."""
    approvals: Annotated[dict[str, dict[str, Any]], merge_approvals]
    """Выданные подтверждения: {узел: {"edits": [пути]}}."""
    clarify: bool
    """True (--ask) — на вопросы агентов (questions.json) граф ждёт ответов пользователя;
    False — автопилот: вместо ответов допущения из вопросов."""
    clarified: Annotated[dict[str, dict[str, Any]], merge_approvals]
    """Обработанные вопросы: {агент: {"asked": n, "answered": m}}."""


NodeUpdate = dict[str, Any]

PROFILE_FILE = "case_profile.json"
"""Файл, который агент этапа profile пишет в свой output_dir (CaseProfile в JSON)."""
PIPELINE_FILE = "pipeline.json"
CLARIFICATIONS_EVENT = "clarifications"
STAGE_SNAPSHOT_FILE = "stage_results.json"


class StageSnapshot(BaseModel):
    """Результаты этапов на момент запуска очередного этапа (runs/<id>/stage_results.json)."""

    results: dict[str, StageResult]
    specs: dict[str, AgentSpec]
    order: list[str]
    """Порядок этапов по плану (шаги графа)."""


class PipelineSummary(BaseModel):
    """Итог конвейера — runs/<run_id>/pipeline.json."""

    run_id: str
    profile: CaseProfile
    counts: dict[str, int]
    usage: Usage
    active_s: float
    stages: dict[str, StageResult]
    clock: dict[str, list[float]] = Field(default_factory=dict)
    """[начало, конец] этапов (unix time) — для диаграммы Ганта в отчёте."""
    llm_free: list[str] = Field(default_factory=list)
    """Агенты с AgentSpec.llm_free=True."""


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
            self._event("profile_from_cli", state, data=state["profile"].model_dump(mode="json"))
            return {}
        started = time.time()
        result = self._execute(agent, state)
        update = self._update(agent.spec, result, started)
        profile = self._read_profile(agent, state)
        if profile is None:
            self._event(
                "profile_fallback",
                state,
                spec=agent.spec,
                message=f"нет корректного {PROFILE_FILE} — профиль из флагов CLI",
            )
        else:
            update["profile"] = profile
            self._event(
                "profile_built", state, spec=agent.spec, data=profile.model_dump(mode="json")
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
            only = state.get("only")
            if only is not None and name not in only:
                result = _skipped(spec, "не выбран в --only", "only")
            elif blocker is not None:
                result = _skipped(spec, blocker, "dependency")
            elif spec.enabled_if and not state["profile"].is_enabled(spec.enabled_if):
                result = _skipped(spec, f"{spec.enabled_if} = false в профиле кейса", "enabled_if")
            else:
                result = self._execute(agent, state)
            if result.status == StageStatus.skipped:
                self._event(EventType.stage_skipped, state, spec=spec, message=result.summary)
            return self._update(spec, result, started)

        node.__name__ = f"stage_{name}"
        return node

    # --- подтверждение ----------------------------------------------------------------------

    def approval(self, step: tuple[str, ...]) -> Callable[[GraphState], NodeUpdate]:
        """Узел после шага: если в шаге этап из approve_after — interrupt до approve_run.

        При продолжении LangGraph выполняет узел заново и interrupt возвращает ответ
        ({"edits": {путь: содержимое}}), поэтому до interrupt узел ничего не меняет.
        """

        def node(state: GraphState) -> NodeUpdate:
            ws = _workspace(state)
            target = state.get("approve_after")
            need_approval = (
                target is not None and target in step and target not in state.get("approvals", {})
            )
            asked = self._questions(state, step)
            update: NodeUpdate = {}
            if asked and not state.get("clarify"):
                # автопилот: не останавливаемся, ответы — допущения из вопросов (идемпотентно:
                # при продолжении после approve узел выполнится заново)
                update["clarified"] = self._record(state, ws, asked, {})
                asked = {}
            if not asked and not need_approval:
                return update

            first = target if need_approval and target else next(iter(asked))
            spec = self._spec_of(first)
            stage = spec.stage if spec else PROFILE_STAGE
            manifest = ws.read_manifest(stage)
            payload: dict[str, Any] = {
                "kind": "approval" if need_approval else "questions",
                "after": first,
                "stage": stage,
                "statuses": {
                    n: r.status.value for n, r in state["stage_results"].items() if n in step
                },
                "artifacts": [f"{stage}/{e.path}" for e in manifest.files] if manifest else [],
                "questions": [
                    {**q.model_dump(), "agent": agent} for agent, qs in asked.items() for q in qs
                ],
            }
            answer = interrupt(payload)
            answer = answer if isinstance(answer, dict) else {}
            if asked:
                answers = answer.get("answers") or {}
                update["clarified"] = self._record(
                    state, ws, asked, {str(k): str(v) for k, v in answers.items()}
                )
            if need_approval and target is not None:
                applied = apply_edits(ws, answer.get("edits") or {})
                if applied:
                    self._event("artifacts_edited", state, spec=spec, data={"paths": list(applied)})
                update["approvals"] = {target: {"edits": applied}}
            return update

        node.__name__ = f"approval_{'_'.join(step)}"
        return node

    def _questions(
        self, state: GraphState, step: tuple[str, ...]
    ) -> dict[str, list[ClarificationQuestion]]:
        """Новые вопросы агентов шага (questions.json в каталоге этапа)."""
        done = state.get("clarified", {})
        ws = _workspace(state)
        found: dict[str, list[ClarificationQuestion]] = {}
        for name in step:
            spec = self._spec_of(name)
            result = state["stage_results"].get(name)
            if spec is None or name in done or result is None:
                continue
            if result.status not in (StageStatus.passed, StageStatus.degraded):
                continue
            questions = read_questions(ws.stage_dir(spec.stage))
            if questions:
                found[name] = questions
        return found

    def _record(
        self,
        state: GraphState,
        ws: RunWorkspace,
        asked: dict[str, list[ClarificationQuestion]],
        answers: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        done: dict[str, dict[str, Any]] = {}
        for agent, questions in asked.items():
            records = record_answers(ws, agent, questions, answers)
            answered = sum(r.source == "user" for r in records)
            done[agent] = {"asked": len(records), "answered": answered}
            self._event(
                CLARIFICATIONS_EVENT,
                state,
                spec=self._spec_of(agent),
                message=f"вопросов {len(records)}, ответов {answered}",
                data={
                    "asked": len(records),
                    "answered": answered,
                    "assumed": len(records) - answered,
                    "ids": [r.id for r in records],
                },
            )
        return done

    def _spec_of(self, node: str) -> AgentSpec | None:
        if node == PROFILE_STAGE:
            profile = self.registry.profile_agent
            return profile.spec if profile else None
        return self.registry.agents[node].spec

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
            run_id=state["run_id"],
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
                self._event("stage_cached", state, spec=spec, data={"key": key})
                return result

        output_dir = ws.clean_outputs(spec.stage, spec.outputs)
        self._write_snapshot(state)
        llm: LLMClient = NullLLM() if spec.llm_free else self.llm
        if decision.thinking_off:
            llm = llm.without_thinking()
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
        self.budget.after_stage(spec, result, run_id=state["run_id"])
        if result.status in (StageStatus.passed, StageStatus.degraded):
            ws.write_manifest(spec.stage, spec.name)
        if self.cache is not None and key is not None:
            self.cache.store(agent, key, result, output_dir)
        return result

    def _write_snapshot(self, state: GraphState) -> None:
        """runs/<id>/stage_results.json: результаты и spec этапов на момент запуска этапа —
        приёмщик сверяет с ними итоговый репозиторий."""
        snapshot = StageSnapshot(
            results=state["stage_results"],
            specs={name: agent.spec for name, agent in self.registry.agents.items()},
            order=[name for step in self.registry.steps for name in step],
        )
        path = _workspace(state).root / STAGE_SNAPSHOT_FILE
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")

    def _update(self, spec: AgentSpec, result: StageResult, started: float) -> NodeUpdate:
        return {
            "stage_results": {spec.name: result},
            "usage_total": result.usage,
            "stage_clock": {spec.name: [started, time.time()]},
        }

    # --- finalize ---------------------------------------------------------------------------

    def finalize(self, state: GraphState) -> NodeUpdate:
        summary = summarize(state)
        summary.llm_free = [n for n, s in self.registry.specs.items() if s.llm_free]
        path = _workspace(state).root / PIPELINE_FILE
        path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        data: dict[str, JsonValue] = {
            "counts": dict(summary.counts),
            "tokens": summary.usage.total_tokens,
        }
        self._event(EventType.run_finished, state, data=data)
        return {}

    def _event(
        self,
        kind: str,
        state: GraphState,
        *,
        spec: AgentSpec | None = None,
        message: str = "",
        data: dict[str, JsonValue] | None = None,
    ) -> None:
        self.emit(
            Event(
                kind=kind,
                run_id=state["run_id"],
                stage=spec.stage if spec else None,
                agent=spec.name if spec else None,
                message=message,
                data=data or {},
            )
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
        clock=dict(state.get("stage_clock", {})),
    )


def _skipped(spec: AgentSpec, reason: str, stopped_reason: str) -> StageResult:
    return StageResult(
        agent=spec.name,
        status=StageStatus.skipped,
        attempts=0,
        summary=reason,
        stopped_reason=stopped_reason,
    )


def apply_edits(ws: RunWorkspace, edits: dict[str, str]) -> list[str]:
    """Записать правки артефактов (пути относительно artifacts/) и пересобрать манифесты."""
    applied = []
    stages = set()
    for rel, content in edits.items():
        path = ws.artifact_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        relative = path.relative_to(ws.artifacts_dir)
        applied.append(relative.as_posix())
        stages.add(relative.parts[0])
    for stage in sorted(stages):
        ws.write_manifest(stage, "approval")
    return applied


def _workspace(state: GraphState) -> RunWorkspace:
    return RunWorkspace(Path(state["workdir"]))
