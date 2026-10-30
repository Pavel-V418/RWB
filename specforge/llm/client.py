"""LLMClient — единственная точка вызова LLM в платформе и агентах.

Временная реализация: openai SDK к OpenAI-совместимому эндпоинту, учёт токенов, structured-вывод
с повтором при невалидном JSON. Профили, телеметрия Langfuse и бюджеты добавятся без изменения
публичных методов `chat()`, `structured()` и `track()`.
"""

import json
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal

import structlog
from openai import OpenAI, omit
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from specforge.core.contracts import Usage
from specforge.llm.tool_parsing import extract_json

log = structlog.get_logger(__name__)

_ERROR_FEEDBACK_CHARS = 2000


class LLMError(Exception):
    """Ошибка вызова LLM или непригодный ответ."""


class LLMConfigError(LLMError):
    """Не хватает настроек (например, LLM_MODEL)."""


class LLMOutputError(LLMError):
    """Модель так и не вернула ответ, проходящий валидацию по схеме."""

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class ChatResponse(BaseModel):
    content: str
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None


StructuredMode = Literal["json_schema", "prompt"]


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:8000/v1"
    model: str
    api_key: str = "EMPTY"
    timeout_s: float = 600.0
    max_retries: int = 3
    structured_mode: StructuredMode = "json_schema"
    structured_attempts: int = Field(default=2, ge=1)
    chat_template_kwargs: bool = False
    """Передавать `chat_template_kwargs.enable_thinking` (vLLM + Qwen3)."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        e = os.environ if env is None else env
        model = e.get("LLM_MODEL", "").strip()
        if not model:
            raise LLMConfigError("не задана переменная окружения LLM_MODEL (см. .env.example)")
        raw = {
            "base_url": e.get("LLM_BASE_URL", "http://localhost:8000/v1"),
            "model": model,
            "api_key": e.get("LLM_API_KEY") or "EMPTY",
            "timeout_s": e.get("LLM_TIMEOUT_S", "600"),
            "max_retries": e.get("LLM_MAX_RETRIES", "3"),
            "structured_mode": e.get("LLM_STRUCTURED_MODE", "json_schema"),
            "structured_attempts": e.get("LLM_STRUCTURED_ATTEMPTS", "2"),
            "chat_template_kwargs": e.get("LLM_CHAT_TEMPLATE_KWARGS", "false"),
        }
        try:
            return cls.model_validate(raw)  # pydantic сам приводит строки env к типам полей
        except ValidationError as exc:
            raise LLMConfigError(f"неверные переменные LLM_*: {exc}") from exc


class UsageTracker:
    """Накопитель usage для блока `with llm.track()`."""

    def __init__(self) -> None:
        self.usage = Usage()


class LLMClient:
    def __init__(self, config: LLMConfig, *, openai_client: OpenAI | None = None) -> None:
        self.config = config
        self.usage = Usage()
        self._openai = openai_client
        self._trackers: list[UsageTracker] = []

    @classmethod
    def from_env(cls) -> "LLMClient":
        return cls(LLMConfig.from_env())

    @contextmanager
    def track(self) -> Iterator[UsageTracker]:
        """Посчитать токены всех вызовов внутри блока (вложенные блоки тоже считаются)."""
        tracker = UsageTracker()
        self._trackers.append(tracker)
        try:
            yield tracker
        finally:
            self._trackers.remove(tracker)

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        thinking: bool = False,
    ) -> ChatResponse:
        response = self._complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            json_schema=None,
        )
        self._record(response)
        return response

    def structured[T: BaseModel](
        self,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: bool = False,
    ) -> T:
        """Получить ответ, валидный по pydantic-схеме; при ошибке модель получает её текст и
        пробует снова (до `structured_attempts` раз)."""
        json_schema = schema.model_json_schema()
        conversation = list(messages)
        if self.config.structured_mode == "prompt":
            conversation.append(ChatMessage(role="user", content=_schema_instruction(json_schema)))
        raw = ""
        for attempt in range(1, self.config.structured_attempts + 1):
            response = self._complete(
                conversation,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                json_schema=(
                    (schema.__name__, json_schema)
                    if self.config.structured_mode == "json_schema"
                    else None
                ),
            )
            self._record(response)
            raw = response.content
            try:
                return schema.model_validate_json(extract_json(raw))
            except (ValidationError, ValueError) as exc:
                error = str(exc)[:_ERROR_FEEDBACK_CHARS]
                log.warning("llm.structured.invalid", schema=schema.__name__, attempt=attempt)
                conversation += [
                    ChatMessage(role="assistant", content=raw),
                    ChatMessage(
                        role="user",
                        content=(
                            f"Ответ не прошёл валидацию по схеме {schema.__name__}:\n{error}\n"
                            "Верни исправленный JSON целиком, без пояснений."
                        ),
                    ),
                ]
        raise LLMOutputError(
            f"модель не вернула валидный {schema.__name__} за "
            f"{self.config.structured_attempts} попыт(ки)",
            raw,
        )

    # --- транспорт: подменяется в FakeLLM -----------------------------------------------------

    def _complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        thinking: bool,
        json_schema: tuple[str, dict[str, Any]] | None,
    ) -> ChatResponse:
        client = self._client()
        params = [_to_openai(m) for m in messages]
        extra_body: dict[str, object] | None = (
            {"chat_template_kwargs": {"enable_thinking": thinking}}
            if self.config.chat_template_kwargs
            else None
        )
        try:
            if json_schema is None:
                completion = client.chat.completions.create(
                    model=self.config.model,
                    messages=params,
                    temperature=temperature,
                    max_tokens=omit if max_tokens is None else max_tokens,
                    extra_body=extra_body,
                )
            else:
                name, schema = json_schema
                completion = client.chat.completions.create(
                    model=self.config.model,
                    messages=params,
                    temperature=temperature,
                    max_tokens=omit if max_tokens is None else max_tokens,
                    extra_body=extra_body,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": name, "schema": schema, "strict": False},
                    },
                )
        except Exception as exc:
            raise LLMError(f"вызов {self.config.base_url} не удался: {exc}") from exc

        if not completion.choices:
            raise LLMError("эндпоинт вернул ответ без choices")
        choice = completion.choices[0]
        usage = Usage(calls=1)
        if completion.usage is not None:
            details = completion.usage.prompt_tokens_details
            usage = Usage(
                prompt_tokens=completion.usage.prompt_tokens,
                completion_tokens=completion.usage.completion_tokens,
                cached_tokens=(details.cached_tokens or 0) if details else 0,
                calls=1,
            )
        return ChatResponse(
            content=choice.message.content or "",
            usage=usage,
            finish_reason=choice.finish_reason,
        )

    def _client(self) -> OpenAI:
        if self._openai is None:
            self._openai = OpenAI(
                base_url=self.config.base_url,
                api_key=self.config.api_key,
                timeout=self.config.timeout_s,
                max_retries=self.config.max_retries,
            )
        return self._openai

    def _record(self, response: ChatResponse) -> None:
        self.usage = self.usage.add(response.usage)
        for tracker in self._trackers:
            tracker.usage = tracker.usage.add(response.usage)
        log.info(
            "llm.call",
            model=self.config.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            cached_tokens=response.usage.cached_tokens,
            finish_reason=response.finish_reason,
        )


class NullLLM(LLMClient):
    """LLM для агентов с `llm_free=True`: любой вызов — ошибка."""

    def __init__(self) -> None:
        super().__init__(LLMConfig(model="none"))

    def _complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        thinking: bool,
        json_schema: tuple[str, dict[str, Any]] | None,
    ) -> ChatResponse:
        raise LLMError("агент помечен llm_free=True, но вызвал LLM")


def _to_openai(message: ChatMessage) -> ChatCompletionMessageParam:
    match message.role:
        case "system":
            return ChatCompletionSystemMessageParam(role="system", content=message.content)
        case "user":
            return ChatCompletionUserMessageParam(role="user", content=message.content)
        case "assistant":
            return ChatCompletionAssistantMessageParam(role="assistant", content=message.content)


def _schema_instruction(json_schema: dict[str, Any]) -> str:
    return (
        "Ответь одним JSON-документом, строго соответствующим JSON Schema ниже, "
        "без пояснений и без markdown.\n" + json.dumps(json_schema, ensure_ascii=False)
    )
