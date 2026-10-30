"""Исполнение одного этапа: песочница → агент → шлюз.

Используется CLI и (позже) узлами графа LangGraph.
"""

import dataclasses
import os
import time
from pathlib import Path

import structlog

from specforge.core.agent_api import Agent, AgentContext
from specforge.core.contracts import AgentSpec, StageResult, StageStatus
from specforge.core.events import Event
from specforge.gates.runner import GateConfigError, GateRunner
from specforge.sandbox.base import BaseSandbox, SandboxError
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


def execute_stage(agent: Agent, ctx: AgentContext) -> StageResult:
    """Запустить агента и, если он отработал, его шлюз — в одной песочнице этапа, которая
    закрывается (контейнер удаляется) после этапа. Ремонт пока не выполняется."""
    spec = agent.spec
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
        result = _run_agent_and_gate(agent, ctx, started)
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


def _run_agent_and_gate(agent: Agent, ctx: AgentContext, started: float) -> StageResult:
    spec = agent.spec
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

    if result.status == StageStatus.passed and spec.gate_commands and ctx.sandbox is not None:
        try:
            gate = GateRunner(logs_dir=ctx.workdir / "logs").run(spec, ctx.sandbox, ctx.run_id)
        except GateConfigError as exc:
            return result.model_copy(
                update={"status": StageStatus.failed, "summary": f"шлюз: {exc}"}
            )
        result = result.model_copy(
            update={
                "gate": gate,
                "status": StageStatus.passed if gate.passed else StageStatus.failed,
            }
        )
    return result
