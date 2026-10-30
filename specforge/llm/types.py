"""Типы обмена с LLM: сообщения, инструменты, запрос и ответ, ошибки."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from specforge.core.contracts import Usage

# --- Ошибки ------------------------------------------------------------------------------------


class LLMError(Exception):
    """Ошибка вызова LLM или непригодный ответ."""


class LLMConfigError(LLMError):
    """Неверные или недостающие настройки (LLM_*, config/profiles.yaml)."""


class LLMTransientError(LLMError):
    """Временный сбой (сеть, 5xx, 429): вызов повторяется с backoff."""


class LLMUnavailableError(LLMError):
    """Эндпоинт не ответил после всех повторов."""


class LLMEndpointDownError(LLMTransientError):
    """Эндпоинт лежит (нет соединения, 502/503/504): после повторов LLMClient ждёт его
    до LLM_WAIT_MAX_S."""


class LLMWaitExhaustedError(LLMUnavailableError):
    """Эндпоинт не поднялся за LLM_WAIT_MAX_S — этап failed (stopped_reason=llm_unavailable)."""


class StructuredOutputError(LLMError):
    """Модель не вернула JSON, валидный по схеме, и после повтора с текстом ошибки."""

    def __init__(self, message: str, raw: str, validation_error: str) -> None:
        super().__init__(message)
        self.raw = raw
        self.validation_error = validation_error


LLMOutputError = StructuredOutputError
"""Прежнее имя StructuredOutputError — оставлено для совместимости."""


# --- Инструменты -------------------------------------------------------------------------------


class ToolSpec(BaseModel):
    """Описание инструмента для модели (function calling)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    """JSON Schema аргументов."""


class ToolCall(BaseModel):
    """Вызов инструмента моделью. Если `error` задан, вызов невалиден и его текст нужно
    вернуть модели (см. tool_parsing.tool_error_message), а не исполнять."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    raw_arguments: str = ""
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.error is None

    def arguments_json(self) -> str:
        if self.error is not None and self.raw_arguments:
            return self.raw_arguments
        return json.dumps(self.arguments, ensure_ascii=False)


# --- Сообщения ---------------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    """Сообщение истории. Рассуждения (reasoning) сюда не попадают никогда."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    """Только для role=assistant."""
    tool_call_id: str | None = None
    """Только для role=tool: на какой вызов это ответ."""

    @classmethod
    def tool_result(cls, call_id: str, content: str) -> "ChatMessage":
        return cls(role="tool", tool_call_id=call_id, content=content)


class LLMRequest(BaseModel):
    """Запрос в том виде, в каком он уходит на транспорт (FakeLLM записывает именно его)."""

    messages: list[ChatMessage]
    tools: list[ToolSpec] = Field(default_factory=list)
    profile: str
    temperature: float
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = None
    timeout_s: float | None = None
    """Таймаут HTTP-запроса; None — LLMConfig.timeout_s."""
    thinking: bool = False
    response_schema_name: str | None = None
    response_schema: dict[str, Any] | None = None

    @property
    def json_schema_name(self) -> str | None:
        return self.response_schema_name

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]


class RawToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class RawCompletion(BaseModel):
    """Ответ транспорта до разбора: tool calls ещё строки, reasoning может сидеть в content."""

    content: str = ""
    reasoning: str | None = None
    tool_calls: list[RawToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None


class LLMResponse(BaseModel):
    content: str
    reasoning: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_s: float = 0.0
    finish_reason: str | None = None

    def to_message(self) -> ChatMessage:
        """Сообщение для истории: content и tool_calls, без reasoning."""
        return ChatMessage(role="assistant", content=self.content, tool_calls=self.tool_calls)


ChatResponse = LLMResponse
"""Прежнее имя LLMResponse — оставлено для совместимости."""
