"""ReAct-цикл tool-агента (реализация specforge.core.agent_api.agent_loop).

Шаг = один вызов LLM. Вызовы инструментов из ответа выполняются по порядку, их результаты
возвращаются модели. Остановка: finish | max_steps | token_budget | loop (один и тот же вызов
LOOP_REPEATS раза подряд или ответы без вызовов MAX_TEXT_ONLY раза подряд) | error (LLM недоступна).
Ошибки инструментов цикл не роняют: модель получает текст ошибки и может исправиться.

Ради prefix caching запрос всегда начинается с одного и того же: system, задача (с входными
артефактами) и описания инструментов — байт-в-байт, без счётчиков шагов и времени. Всё, что
меняется, дописывается в конец истории.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from pydantic import JsonValue

from specforge.core.contracts import AgentOutcome, StopReason, Usage
from specforge.core.events import Event
from specforge.executor.compaction import (
    COMPACT_AT,
    KEEP_LAST,
    PREFIX_STEP,
    HistoryEntry,
    compact,
    estimate_tokens,
)
from specforge.llm import ChatMessage, LLMError, ProfileName, ToolCall, tool_error_message
from specforge.sandbox.base import SandboxError
from specforge.sandbox.local_sandbox import LocalSandbox
from specforge.tools import FINISH, Tool, ToolEnv, ToolError, ToolResult, get_tools

if TYPE_CHECKING:
    from specforge.core.agent_api import AgentContext

log = structlog.get_logger(__name__)


NUDGE = (
    "Ответ без вызова инструмента. Продолжай работу инструментами; "
    "когда задача выполнена — вызови finish(summary)."
)


@dataclass
class LoopSettings:
    compact_at: int = COMPACT_AT
    keep_last: int = KEEP_LAST
    loop_repeats: int = 3
    max_text_only: int = 3
    event_chars: int = 500
    max_run_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("SPECFORGE_MAX_RUN_TIMEOUT_S", "1800"))
    )


def run_react_loop(
    ctx: "AgentContext",
    system_prompt: str,
    task: str,
    tools: Sequence[str],
    *,
    max_steps: int,
    temperature: float | None,
    thinking: bool | None,
    profile: str | None = None,
    settings: LoopSettings | None = None,
) -> AgentOutcome:
    settings = settings or LoopSettings()
    try:
        toolset = get_tools(tools)
    except ValueError as exc:
        return _finish(ctx, "error", str(exc), steps=0)

    own_sandbox = ctx.sandbox is None
    sandbox = ctx.sandbox or LocalSandbox(
        ctx.output_dir, logs_dir=ctx.workdir / "logs", log_prefix=ctx.llm.labels.agent or "agent"
    )
    env = ToolEnv(sandbox, ctx.protected_globs, settings.max_run_timeout_s, ctx.allowed_commands)
    loop = _Loop(ctx, toolset, env, settings, profile or ProfileName.CODE)
    try:
        return loop.run(system_prompt, task, max_steps, temperature, thinking)
    finally:
        if own_sandbox:
            sandbox.close()


class _Loop:
    def __init__(
        self,
        ctx: "AgentContext",
        toolset: list[Tool],
        env: ToolEnv,
        settings: LoopSettings,
        profile: str,
    ) -> None:
        self.ctx = ctx
        self.profile = profile
        self.env = env
        self.settings = settings
        self.tools = {t.name: t for t in toolset}
        # Список описаний строится один раз: одинаковый порядок и JSON во всех запросах.
        self.specs = [t.spec for t in toolset] + [FINISH]
        self.changed: list[str] = []
        self._last_signature: str | None = None
        self._repeats = 0

    def run(
        self,
        system_prompt: str,
        task: str,
        max_steps: int,
        temperature: float | None,
        thinking: bool | None,
    ) -> AgentOutcome:
        entries = [
            HistoryEntry(ChatMessage(role="system", content=system_prompt), PREFIX_STEP),
            HistoryEntry(ChatMessage(role="user", content=task), PREFIX_STEP),
        ]
        reason: StopReason = "max_steps"
        summary = f"достигнут лимит шагов ({max_steps})"
        steps = 0
        text_only = 0
        with self.ctx.llm.track() as meter:
            for step in range(1, max_steps + 1):
                if meter.usage.total_tokens >= self.ctx.budget_tokens:
                    reason = "token_budget"
                    used, budget = meter.usage.total_tokens, self.ctx.budget_tokens
                    summary = f"исчерпан бюджет токенов ({used}/{budget})"
                    break
                steps = step
                self._emit("step_started", step=step, tokens=meter.usage.total_tokens)
                try:
                    response = self.ctx.llm.chat(
                        [e.message for e in entries],
                        tools=self.specs,
                        profile=self.profile,
                        temperature=temperature,
                        thinking=thinking,
                    )
                except LLMError as exc:
                    reason, summary = "error", f"LLM: {exc}"
                    break
                entries.append(HistoryEntry(response.to_message(), step))

                if not response.tool_calls:
                    text_only += 1
                    if text_only >= self.settings.max_text_only:
                        reason = "loop"
                        summary = (
                            f"{text_only} ответа подряд без вызова инструментов: "
                            f"{response.content[:300]}"
                        )
                        break
                    entries.append(HistoryEntry(ChatMessage(role="user", content=NUDGE), step))
                    continue
                text_only = 0

                stop = self._run_calls(response.tool_calls, entries, step)
                if stop is not None:
                    reason, summary = stop
                    break

                history = response.usage.prompt_tokens + response.usage.completion_tokens
                history += estimate_tokens([e.message for e in entries if e.step == step][1:])
                if history > self.settings.compact_at:
                    n = compact(entries, current_step=step, keep_last=self.settings.keep_last)
                    if n:
                        self._emit("history_compacted", step=step, messages=n, tokens=history)

        return _finish(
            self.ctx, reason, summary, steps=steps, usage=meter.usage, changed=self.changed
        )

    def _run_calls(
        self, calls: Sequence[ToolCall], entries: list[HistoryEntry], step: int
    ) -> tuple[StopReason, str] | None:
        for call in calls:
            signature = f"{call.name}:{call.arguments_json() if call.valid else call.raw_arguments}"
            self._repeats = self._repeats + 1 if signature == self._last_signature else 1
            self._last_signature = signature
            if self._repeats >= self.settings.loop_repeats:
                return (
                    "loop",
                    f"зацикливание: {call.name} с теми же аргументами {self._repeats} раза подряд",
                )
            if call.valid and call.name == FINISH.name:
                summary = call.arguments.get("summary")
                return "finish", summary if isinstance(summary, str) else ""

            self._emit(
                "tool_called",
                step=step,
                tool=call.name,
                arguments=self._short(call.arguments_json()),
            )
            result, ok = self._execute(call)
            if result.changed and result.changed not in self.changed:
                self.changed.append(result.changed)
            entries.append(
                HistoryEntry(
                    ChatMessage.tool_result(call.id, result.content), step, stub=result.stub
                )
            )
            self._emit(
                "tool_result", step=step, tool=call.name, ok=ok, result=self._short(result.content)
            )
        return None

    def _execute(self, call: ToolCall) -> tuple[ToolResult, bool]:
        if not call.valid:
            return ToolResult(
                content=tool_error_message(call), stub=f"[{call.name}: невалидный вызов]"
            ), False
        tool = self.tools[call.name]
        try:
            return tool.fn(self.env, call.arguments), True
        except (ToolError, SandboxError) as exc:
            text = f"Ошибка {call.name}: {exc}"
        except Exception as exc:  # инструмент не должен ронять цикл
            log.exception("tool.crashed", tool=call.name)
            text = f"Внутренняя ошибка инструмента {call.name}: {type(exc).__name__}: {exc}"
        return ToolResult(content=text, stub=f"[{call.name}: ошибка: {str(text)[:120]}]"), False

    def _emit(self, kind: str, **data: JsonValue) -> None:
        self.ctx.emit(
            Event(kind=kind, run_id=self.ctx.run_id, agent=self.ctx.llm.labels.agent, data=data)
        )

    def _short(self, text: str) -> str:
        limit = self.settings.event_chars
        return text if len(text) <= limit else text[:limit] + "…"


def _finish(
    ctx: "AgentContext",
    reason: StopReason,
    summary: str,
    *,
    steps: int,
    usage: Usage | None = None,
    changed: list[str] | None = None,
) -> AgentOutcome:
    outcome = AgentOutcome(
        summary=summary,
        steps=steps,
        usage=usage or Usage(),
        stopped_reason=reason,
        changed_files=changed or [],
    )
    ctx.emit(
        Event(
            kind="agent_finished",
            run_id=ctx.run_id,
            agent=ctx.llm.labels.agent,
            message=summary[:500],
            data={
                "stopped_reason": reason,
                "steps": steps,
                "tokens": outcome.usage.total_tokens,
                "changed_files": list[JsonValue](outcome.changed_files),
            },
        )
    )
    return outcome
