"""Реализация agent_loop (ReAct-цикл tool-агента).

ВРЕМЕННО: один вызов LLM без инструментов. Полный цикл (tool calls, str_replace, компакция,
бюджет) заменит тело `run_react_loop`, не меняя сигнатуру `specforge.core.agent_api.agent_loop`.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from specforge.core.contracts import AgentOutcome
from specforge.core.events import Event
from specforge.llm import ChatMessage
from specforge.llm.tool_parsing import strip_think

if TYPE_CHECKING:
    from specforge.core.agent_api import AgentContext


def run_react_loop(
    ctx: "AgentContext",
    system_prompt: str,
    task: str,
    tools: Sequence[str],
    *,
    max_steps: int,
    temperature: float,
    thinking: bool,
) -> AgentOutcome:
    del tools, max_steps  # инструменты появятся вместе с полным циклом
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=task),
    ]
    with ctx.llm.track() as tracker:
        response = ctx.llm.chat(messages, temperature=temperature, thinking=thinking)
    ctx.emit(
        Event(
            kind="agent.step",
            run_id=ctx.run_id,
            data={"step": 1, "completion_tokens": response.usage.completion_tokens},
        )
    )
    return AgentOutcome(
        final_message=strip_think(response.content),
        usage=tracker.usage,
        steps=1,
        stop_reason="final",
    )
