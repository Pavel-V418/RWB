"""Разбор вывода модели: размышления, JSON, вызовы инструментов.

Обычно vLLM сам разбирает tool calls (--tool-call-parser) и reasoning (--reasoning-parser).
Если парсер не справился, вызов остаётся в content — тогда его достаёт этот модуль.
Поддерживаемые формы в content:
  - Hermes/Qwen:   <tool_call>{"name": "...", "arguments": {...}}</tool_call>
  - Qwen3-Coder:   <tool_call><function=NAME><parameter=P>знач.</parameter></function></tool_call>
  - JSON-блок:     ```json {"name": "...", "arguments": {...}} ``` (или массив таких объектов)
  - Голый JSON:    весь ответ — такой объект/массив.
Невалидный вызов не бросает исключение: он возвращается как ToolCall с `error`, текст которой
(tool_error_message) нужно отдать модели в ответе инструмента, чтобы она повторила вызов.
"""

import json
import re
import uuid
from collections.abc import Sequence
from typing import Any

from specforge.llm.types import RawToolCall, ToolCall, ToolSpec

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)(?:</tool_call>|\Z)", re.DOTALL)
_FUNCTION_RE = re.compile(r"<function=([^>\s]+)>(.*?)(?:</function>|\Z)", re.DOTALL)
_PARAMETER_RE = re.compile(
    r"<parameter=([^>\s]+)>(.*?)(?:</parameter>|(?=<parameter=)|\Z)", re.DOTALL
)


# --- Размышления и JSON ------------------------------------------------------------------------


def split_reasoning(text: str) -> tuple[str | None, str]:
    """Отделить `<think>…</think>` от ответа. Возвращает (reasoning | None, content).

    Обрабатывает и «осиротевший» `</think>`: шаблон чата Qwen3 подставляет открывающий тег сам.
    """
    parts = [m.strip() for m in _THINK_RE.findall(text)]
    rest = _THINK_RE.sub("", text)
    if "</think>" in rest:
        head, rest = rest.split("</think>", 1)
        parts.insert(0, head.strip())
    reasoning = "\n\n".join(p for p in parts if p)
    return (reasoning or None), rest.strip()


def strip_think(text: str) -> str:
    return split_reasoning(text)[1]


def extract_json(text: str) -> str:
    """Достать JSON-документ из ответа модели. ValueError, если его там нет."""
    text = strip_think(text)
    fence = _FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("в ответе модели нет JSON")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    if end < start:
        raise ValueError("JSON в ответе модели не закрыт")
    return text[start : end + 1]


# --- Вызовы инструментов -----------------------------------------------------------------------


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def parse_tool_call(
    name: str, arguments: str | dict[str, Any], tools: Sequence[ToolSpec], call_id: str = ""
) -> ToolCall:
    """Проверить вызов против объявленных инструментов. Ошибка — в ToolCall.error."""
    call_id = call_id or new_call_id()
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)

    def fail(error: str) -> ToolCall:
        return ToolCall(id=call_id, name=name, raw_arguments=raw, error=error)

    spec = next((t for t in tools if t.name == name), None)
    if spec is None:
        known = ", ".join(t.name for t in tools) or "—"
        return fail(f"неизвестный инструмент {name!r}; доступны: {known}")
    if isinstance(arguments, str):
        try:
            parsed: object = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return fail(f"аргументы не являются валидным JSON: {exc.msg} (позиция {exc.pos})")
    else:
        parsed = arguments
    if not isinstance(parsed, dict):
        return fail(f"аргументы должны быть JSON-объектом, получено: {type(parsed).__name__}")
    required = spec.parameters.get("required", [])
    missing = [p for p in required if p not in parsed]
    if missing:
        return fail(f"не хватает обязательных аргументов: {', '.join(missing)}")
    properties = spec.parameters.get("properties", {})
    if spec.parameters.get("additionalProperties") is False:
        extra = [k for k in parsed if k not in properties]
        if extra:
            return fail(f"неизвестные аргументы: {', '.join(extra)}")
    return ToolCall(id=call_id, name=name, arguments=parsed, raw_arguments=raw)


def parse_raw_tool_calls(raw: Sequence[RawToolCall], tools: Sequence[ToolSpec]) -> list[ToolCall]:
    """Tool calls, разобранные сервером: проверить аргументы и имена."""
    return [parse_tool_call(c.name, c.arguments, tools, c.id or new_call_id()) for c in raw]


def extract_tool_calls(content: str, tools: Sequence[ToolSpec]) -> tuple[list[ToolCall], str]:
    """Достать вызовы инструментов, оставшиеся в тексте. Возвращает (вызовы, остаток текста)."""
    if not tools:
        return [], content

    blocks = _TOOL_CALL_RE.findall(content)
    if blocks:
        calls = [_parse_tool_call_block(b, tools) for b in blocks]
        return calls, _TOOL_CALL_RE.sub("", content).strip()

    for fence in _FENCE_RE.finditer(content):
        calls = _parse_json_calls(fence.group(1), tools)
        if calls:
            rest = (content[: fence.start()] + content[fence.end() :]).strip()
            return calls, rest

    calls = _parse_json_calls(content.strip(), tools)
    if calls:
        return calls, ""
    return [], content


def tool_error_message(call: ToolCall) -> str:
    """Текст ответа инструмента для невалидного вызова — модель увидит его и повторит вызов."""
    return (
        f"Ошибка вызова инструмента {call.name!r}: {call.error}. "
        "Исправь вызов и повтори его; аргументы — JSON-объект по схеме инструмента."
    )


def _parse_tool_call_block(block: str, tools: Sequence[ToolSpec]) -> ToolCall:
    block = block.strip()
    function = _FUNCTION_RE.search(block)
    if function:
        name = function.group(1).strip()
        spec = next((t for t in tools if t.name == name), None)
        properties = spec.parameters.get("properties", {}) if spec else {}
        arguments = {
            key.strip(): _coerce_parameter(value, properties.get(key.strip(), {}))
            for key, value in _PARAMETER_RE.findall(function.group(2))
        }
        return parse_tool_call(name, arguments, tools)
    try:
        payload = json.loads(block)
    except json.JSONDecodeError as exc:
        return ToolCall(
            id=new_call_id(),
            name="?",
            raw_arguments=block,
            error=f"содержимое <tool_call> не является JSON: {exc.msg} (позиция {exc.pos})",
        )
    return _call_from_payload(payload, tools, block)


def _parse_json_calls(text: str, tools: Sequence[ToolSpec]) -> list[ToolCall]:
    """JSON-объект/массив вида {"name", "arguments"} — только если имя объявлено как инструмент,
    иначе это обычный JSON-ответ, а не вызов."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = payload if isinstance(payload, list) else [payload]
    names = {t.name for t in tools}
    if not items or not all(isinstance(i, dict) and i.get("name") in names for i in items):
        return []
    return [_call_from_payload(i, tools, json.dumps(i, ensure_ascii=False)) for i in items]


def _call_from_payload(payload: object, tools: Sequence[ToolSpec], raw: str) -> ToolCall:
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return ToolCall(
            id=new_call_id(),
            name="?",
            raw_arguments=raw,
            error='ожидается объект {"name": "...", "arguments": {...}}',
        )
    arguments = payload.get("arguments", payload.get("parameters", {}))
    if not isinstance(arguments, str | dict):
        arguments = json.dumps(arguments)
    return parse_tool_call(payload["name"], arguments, tools)


def _coerce_parameter(value: str, schema: dict[str, Any]) -> Any:
    """Значения <parameter=…> — строки; нестроковые типы схемы пробуем разобрать как JSON."""
    text = value.strip("\n")
    if schema.get("type", "string") == "string":
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
