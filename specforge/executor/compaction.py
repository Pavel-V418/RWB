"""Компакция истории ReAct-цикла.

Когда история превышает COMPACT_AT токенов, результаты инструментов старше KEEP_LAST шагов
заменяются заглушками («[run: exit 0, 340 строк, лог: …]»), а длинные строковые аргументы старых
вызовов (содержимое write_file, str_replace) — пометкой о пропуске. Префикс (system и задача)
не трогается никогда. Сжатое сообщение больше не меняется, поэтому после компакции префикс снова
стабилен и prefix caching продолжает работать; компакция идёт пачкой, а не по одному сообщению.
"""

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass

from specforge.llm import ChatMessage, ToolCall

COMPACT_AT = int(os.environ.get("SPECFORGE_COMPACT_AT", "60000"))
KEEP_LAST = int(os.environ.get("SPECFORGE_KEEP_LAST", "4"))
LONG_ARG_CHARS = 1000
PREFIX_STEP = 0


@dataclass
class HistoryEntry:
    message: ChatMessage
    step: int
    """Номер шага ReAct; PREFIX_STEP — неизменный префикс (system, задача)."""
    stub: str | None = None
    """Заглушка для результата инструмента."""
    compacted: bool = False


def estimate_tokens(messages: Sequence[ChatMessage]) -> int:
    """Грубая оценка: 1 токен ≈ 4 символа."""
    chars = sum(
        len(m.content) + sum(len(c.arguments_json()) for c in m.tool_calls) for m in messages
    )
    return chars // 4


def compact(entries: list[HistoryEntry], *, current_step: int, keep_last: int) -> int:
    """Сжать всё старше последних `keep_last` шагов. Возвращает число изменённых сообщений."""
    cutoff = current_step - keep_last
    changed = 0
    for entry in entries:
        if entry.step == PREFIX_STEP or entry.step > cutoff or entry.compacted:
            continue
        entry.compacted = True
        message = entry.message
        if message.role == "tool" and entry.stub and message.content != entry.stub:
            entry.message = message.model_copy(update={"content": entry.stub})
            changed += 1
        elif message.role == "assistant" and message.tool_calls:
            calls = [_shorten_call_arguments(c) for c in message.tool_calls]
            if calls != message.tool_calls:
                entry.message = message.model_copy(update={"tool_calls": calls})
                changed += 1
    return changed


def _shorten_call_arguments(call: ToolCall) -> ToolCall:
    if not call.valid:
        return call
    arguments = {
        key: (
            f"[опущено {len(value)} символов]"
            if isinstance(value, str) and len(value) > LONG_ARG_CHARS
            else value
        )
        for key, value in call.arguments.items()
    }
    if arguments == call.arguments:
        return call
    return call.model_copy(
        update={"arguments": arguments, "raw_arguments": json.dumps(arguments, ensure_ascii=False)}
    )
