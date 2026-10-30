"""Исполнение одного этапа: агент → шлюз. Используется CLI и (позже) узлами графа LangGraph."""

import time

import structlog

from specforge.core.agent_api import Agent, AgentContext
from specforge.core.contracts import StageResult, StageStatus
from specforge.core.events import Event
from specforge.gates.runner import run_gate
from specforge.sandbox.local_sandbox import LocalSandbox

log = structlog.get_logger(__name__)


def execute_stage(agent: Agent, ctx: AgentContext) -> StageResult:
    """Запустить агента и, если он отработал, его шлюз. Ремонт пока не выполняется."""
    spec = agent.spec
    ctx.emit(Event(kind="stage.started", run_id=ctx.run_id, agent=spec.name))
    started = time.monotonic()
    try:
        result = agent.run(ctx)
    except Exception as exc:
        log.exception("stage.crashed", agent=spec.name)
        result = StageResult(
            agent=spec.name,
            status=StageStatus.failed,
            duration_s=time.monotonic() - started,
            summary=f"{type(exc).__name__}: {exc}",
        )

    if result.status == StageStatus.passed and spec.gate_commands:
        gate = run_gate(
            spec.gate_commands,
            ctx.sandbox or LocalSandbox(ctx.output_dir),
            log_path=ctx.workdir / "logs" / f"{spec.name}.gate.log",
        )
        result = result.model_copy(
            update={
                "gate": gate,
                "status": StageStatus.passed if gate.passed else StageStatus.failed,
            }
        )

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
