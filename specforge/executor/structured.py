"""Исполнитель агентов без инструментов: один structured-вызов LLM по pydantic-схеме."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel

from specforge.core.events import Event
from specforge.llm import ChatMessage, LLMError, ProfileName
from specforge.llm.client import ProfileRef

if TYPE_CHECKING:
    from specforge.core.agent_api import AgentContext


def run_structured[T: BaseModel](
    ctx: "AgentContext",
    messages: Sequence[ChatMessage],
    schema: type[T],
    *,
    profile: ProfileRef = ProfileName.CODE,
    temperature: float | None = None,
    thinking: bool | None = None,
) -> T:
    """Вызвать LLM и вернуть ответ, валидный по схеме. StructuredOutputError/LLMError — наружу."""
    agent = ctx.llm.labels.agent
    ctx.emit(Event(kind="step_started", run_id=ctx.run_id, agent=agent, data={"step": 1}))
    with ctx.llm.track() as meter:
        try:
            value = ctx.llm.structured(
                messages, schema, profile, temperature=temperature, thinking=thinking
            )
        except LLMError as exc:
            _finished(ctx, "error", str(exc), meter.usage.total_tokens)
            raise
    _finished(ctx, "finish", schema.__name__, meter.usage.total_tokens)
    return value


def _finished(ctx: "AgentContext", reason: str, message: str, tokens: int) -> None:
    ctx.emit(
        Event(
            kind="agent_finished",
            run_id=ctx.run_id,
            agent=ctx.llm.labels.agent,
            message=message[:500],
            data={"stopped_reason": reason, "steps": 1, "tokens": tokens},
        )
    )
