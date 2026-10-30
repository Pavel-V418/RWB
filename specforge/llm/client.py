"""LLMClient — единственная точка вызова LLM в платформе и агентах.

- chat(): текст, reasoning отдельно, tool calls (включая извлечённые из content);
- structured(): JSON по pydantic-схеме через response_format=json_schema (guided decoding vLLM),
  один повтор с текстом ошибки валидации, затем StructuredOutputError;
- профили генерации (profiles.py), thinking через chat_template_kwargs;
- повторы сетевых ошибок/5xx/429 с экспоненциальным backoff, таймауты, семафор параллелизма;
- учёт токенов в UsageTracker (run/stage/agent) и хуки on_call для телеметрии.
"""

import copy
import json
import os
import random
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import openai
import structlog
from openai import OpenAI, omit
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from pydantic import BaseModel, Field, ValidationError

from specforge.core.contracts import Usage
from specforge.llm.profiles import DEFAULT_PROFILES, Profile, ProfileName, load_profiles
from specforge.llm.tool_parsing import (
    extract_json,
    extract_tool_calls,
    parse_raw_tool_calls,
    split_reasoning,
)
from specforge.llm.types import (
    ChatMessage,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
    LLMUnavailableError,
    RawCompletion,
    RawToolCall,
    StructuredOutputError,
    ToolSpec,
)
from specforge.llm.usage import LLMCallRecord, OnCallHook, UsageKey, UsageMeter, UsageTracker

log = structlog.get_logger(__name__)

_ERROR_FEEDBACK_CHARS = 2000

StructuredMode = Literal["json_schema", "prompt"]
ProfileRef = Profile | str


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:8000/v1"
    model: str
    api_key: str = "EMPTY"
    timeout_s: float = Field(default=600.0, gt=0)
    max_retries: int = Field(default=4, ge=0)
    """Повторы при сетевых ошибках, 5xx и 429 (всего попыток = max_retries + 1)."""
    retry_backoff_s: float = Field(default=1.0, ge=0)
    retry_backoff_max_s: float = Field(default=30.0, ge=0)
    max_concurrency: int = Field(default=4, ge=1)
    """Одновременных запросов к эндпоинту из процесса."""
    structured_mode: StructuredMode = "json_schema"
    structured_attempts: int = Field(default=2, ge=1)
    """Попыток structured(): первая + повтор с текстом ошибки валидации."""
    vllm_extras: bool = True
    """Слать vLLM-специфичные поля в extra_body: chat_template_kwargs.enable_thinking и top_k.
    Для эндпоинтов, которые их отвергают (OpenAI), — false."""
    profiles_path: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        e = os.environ if env is None else env
        model = e.get("LLM_MODEL", "").strip()
        if not model:
            raise LLMConfigError("не задана переменная окружения LLM_MODEL (см. .env.example)")
        names = {
            "base_url": "LLM_BASE_URL",
            "api_key": "LLM_API_KEY",
            "timeout_s": "LLM_TIMEOUT_S",
            "max_retries": "LLM_MAX_RETRIES",
            "retry_backoff_s": "LLM_RETRY_BACKOFF_S",
            "retry_backoff_max_s": "LLM_RETRY_BACKOFF_MAX_S",
            "max_concurrency": "LLM_MAX_CONCURRENCY",
            "structured_mode": "LLM_STRUCTURED_MODE",
            "structured_attempts": "LLM_STRUCTURED_ATTEMPTS",
            "vllm_extras": "LLM_VLLM_EXTRAS",
        }
        raw: dict[str, object] = {f: e[v] for f, v in names.items() if e.get(v)}
        raw["model"] = model
        raw["profiles_path"] = e.get("LLM_PROFILES_PATH") or "config/profiles.yaml"
        try:
            return cls.model_validate(raw)  # pydantic сам приводит строки env к типам полей
        except ValidationError as exc:
            raise LLMConfigError(f"неверные переменные LLM_*: {exc}") from exc


class LLMClient:
    """Клиент LLM. Дешёвые копии через `bind()` делят транспорт, семафор, трекер и хуки."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        openai_client: OpenAI | None = None,
        profiles: Mapping[str, Profile] | None = None,
        tracker: UsageTracker | None = None,
        on_call: Sequence[OnCallHook] = (),
    ) -> None:
        self.config = config
        self.profiles: dict[str, Profile] = (
            dict(profiles) if profiles is not None else load_profiles(config.profiles_path)
        )
        self.tracker = tracker or UsageTracker()
        self.hooks: list[OnCallHook] = list(on_call)
        self.labels = UsageKey()
        self._shared = _SharedOpenAI(openai_client)
        self._semaphore = threading.BoundedSemaphore(config.max_concurrency)
        self._meters: list[UsageMeter] = []
        self._sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_env(cls) -> "LLMClient":
        return cls(LLMConfig.from_env())

    # --- привязка и учёт ----------------------------------------------------------------------

    def bind(
        self, *, run_id: str | None = None, stage: str | None = None, agent: str | None = None
    ) -> "LLMClient":
        """Клиент с метками для учёта токенов; незаданные метки наследуются."""
        bound = copy.copy(self)
        bound.labels = UsageKey(
            run_id=run_id or self.labels.run_id,
            stage=stage or self.labels.stage,
            agent=agent or self.labels.agent,
        )
        bound._meters = []
        return bound

    def add_hook(self, hook: OnCallHook) -> None:
        self.hooks.append(hook)

    @property
    def usage(self) -> Usage:
        """Токены в пределах меток этого клиента (у непривязанного — все)."""
        return self.tracker.total(
            run_id=self.labels.run_id, stage=self.labels.stage, agent=self.labels.agent
        )

    @contextmanager
    def track(self) -> Iterator[UsageMeter]:
        """Посчитать токены вызовов внутри блока (вложенные блоки тоже считаются)."""
        meter = UsageMeter()
        self._meters.append(meter)
        try:
            yield meter
        finally:
            self._meters.remove(meter)

    def profile(self, ref: ProfileRef) -> Profile:
        if isinstance(ref, Profile):
            return ref
        try:
            return self.profiles[str(ref)]
        except KeyError:
            known = ", ".join(sorted(self.profiles))
            raise LLMConfigError(f"неизвестный профиль {ref!r}; доступны: {known}") from None

    # --- публичные вызовы ---------------------------------------------------------------------

    def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
        profile: ProfileRef = ProfileName.CODE,
        *,
        temperature: float | None = None,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Один ход диалога. temperature/thinking/max_tokens перекрывают значения профиля."""
        request = self._request(messages, tools, profile, temperature, thinking, max_tokens)
        return self._call(request)

    def structured[T: BaseModel](
        self,
        messages: Sequence[ChatMessage],
        schema: type[T],
        profile: ProfileRef = ProfileName.CODE,
        *,
        temperature: float | None = None,
        thinking: bool | None = None,
        max_tokens: int | None = None,
    ) -> T:
        """Ответ, валидный по схеме. Ошибка валидации возвращается модели, после исчерпания
        попыток — StructuredOutputError."""
        json_schema = schema.model_json_schema()
        conversation = list(messages)
        if self.config.structured_mode == "prompt":
            conversation.append(ChatMessage(role="user", content=_schema_instruction(json_schema)))
        raw, error = "", ""
        for attempt in range(1, self.config.structured_attempts + 1):
            request = self._request(conversation, None, profile, temperature, thinking, max_tokens)
            if self.config.structured_mode == "json_schema":
                request.response_schema_name = schema.__name__
                request.response_schema = json_schema
            response = self._call(request)
            raw = response.content
            try:
                return schema.model_validate_json(extract_json(raw))
            except (ValidationError, ValueError) as exc:
                error = str(exc)[:_ERROR_FEEDBACK_CHARS]
                log.warning(
                    "llm.structured.invalid", schema=schema.__name__, attempt=attempt, **self._lb()
                )
                conversation += [
                    response.to_message(),
                    ChatMessage(
                        role="user",
                        content=(
                            f"Ответ не прошёл валидацию по схеме {schema.__name__}:\n{error}\n"
                            "Верни исправленный JSON целиком, без пояснений."
                        ),
                    ),
                ]
        raise StructuredOutputError(
            f"модель не вернула валидный {schema.__name__} за "
            f"{self.config.structured_attempts} попыт(ки): {error[:300]}",
            raw,
            error,
        )

    # --- конвейер вызова ----------------------------------------------------------------------

    def _request(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None,
        profile: ProfileRef,
        temperature: float | None,
        thinking: bool | None,
        max_tokens: int | None,
    ) -> LLMRequest:
        p = self.profile(profile)
        return LLMRequest(
            messages=list(messages),
            tools=list(tools or []),
            profile=p.name,
            temperature=p.temperature if temperature is None else temperature,
            top_p=p.top_p,
            top_k=p.top_k,
            max_tokens=max_tokens if max_tokens is not None else p.max_tokens,
            thinking=p.thinking if thinking is None else thinking,
        )

    def _call(self, request: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        attempts = 0
        try:
            raw, attempts = self._with_retries(request)
        except LLMError as exc:
            self._notify(request, Usage(), time.monotonic() - started, attempts, None, str(exc))
            raise
        response = self._postprocess(raw, request, time.monotonic() - started)
        self._record(response.usage)
        self._notify(request, response.usage, response.latency_s, attempts, response, None)
        return response

    def _with_retries(self, request: LLMRequest) -> tuple[RawCompletion, int]:
        attempts_total = self.config.max_retries + 1
        for attempt in range(1, attempts_total + 1):
            try:
                with self._semaphore:
                    return self._transport(request), attempt
            except LLMTransientError as exc:
                if attempt == attempts_total:
                    raise LLMUnavailableError(
                        f"{self.config.base_url}: нет ответа после {attempts_total} попыток: {exc}"
                    ) from exc
                delay = self._backoff(attempt)
                log.warning(
                    "llm.retry", attempt=attempt, delay_s=round(delay, 2), error=str(exc)[:200]
                )
                self._sleep(delay)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        base = self.config.retry_backoff_s * 2.0 ** (attempt - 1)
        return min(self.config.retry_backoff_max_s, base) * (
            0.5 + random.random() / 2
        )  # джиттер, не криптография

    def _postprocess(self, raw: RawCompletion, request: LLMRequest, latency: float) -> LLMResponse:
        reasoning, content = raw.reasoning, raw.content
        think, content = split_reasoning(content)
        if think:  # reasoning-парсер на сервере не включён — размышления пришли в content
            reasoning = f"{reasoning}\n\n{think}" if reasoning else think
        tool_calls = parse_raw_tool_calls(raw.tool_calls, request.tools)
        if request.tools and not tool_calls:
            tool_calls, content = extract_tool_calls(content, request.tools)
            if tool_calls:
                log.info("llm.tool_calls.fallback", count=len(tool_calls), **self._lb())
        return LLMResponse(
            content=content,
            reasoning=reasoning or None,
            tool_calls=tool_calls,
            usage=raw.usage,
            latency_s=latency,
            finish_reason=raw.finish_reason,
        )

    def _record(self, usage: Usage) -> None:
        self.tracker.record(self.labels, usage)
        for meter in self._meters:
            meter.usage = meter.usage.add(usage)

    def _notify(
        self,
        request: LLMRequest,
        usage: Usage,
        latency: float,
        attempts: int,
        response: LLMResponse | None,
        error: str | None,
    ) -> None:
        record = LLMCallRecord(
            **self._lb(),
            profile=request.profile,
            model=self.config.model,
            usage=usage,
            latency_s=latency,
            attempts=attempts,
            tool_calls=len(response.tool_calls) if response else 0,
            finish_reason=response.finish_reason if response else None,
            structured_schema=request.response_schema_name,
            error=error,
        )
        log.info(
            "llm.call",
            **record.model_dump(exclude={"usage", "run_id", "stage", "agent"}),
            **self._lb(),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
        )
        for hook in self.hooks:
            try:
                hook(record)
            except Exception:  # телеметрия не должна ронять конвейер
                log.exception("llm.hook_failed", hook=getattr(hook, "__name__", repr(hook)))

    def _lb(self) -> dict[str, str | None]:
        return self.labels._asdict()

    # --- транспорт: подменяется в FakeLLM -----------------------------------------------------

    def _transport(self, request: LLMRequest) -> RawCompletion:
        client = self._client()
        extra_body: dict[str, object] = {}
        if self.config.vllm_extras:
            extra_body["chat_template_kwargs"] = {"enable_thinking": request.thinking}
            if request.top_k is not None:
                extra_body["top_k"] = request.top_k
        tools = [_tool_param(t) for t in request.tools]
        try:
            completion = client.chat.completions.create(
                model=self.config.model,
                messages=[_message_param(m) for m in request.messages],
                temperature=request.temperature,
                top_p=omit if request.top_p is None else request.top_p,
                max_tokens=omit if request.max_tokens is None else request.max_tokens,
                tools=tools or omit,
                tool_choice="auto" if tools else omit,
                response_format=(
                    {
                        "type": "json_schema",
                        "json_schema": {
                            "name": request.response_schema_name or "response",
                            "schema": request.response_schema,
                            "strict": False,
                        },
                    }
                    if request.response_schema is not None
                    else omit
                ),
                extra_body=extra_body or None,
            )
        except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as e:
            raise LLMTransientError(_describe(e)) from e
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                raise LLMTransientError(_describe(e)) from e
            raise LLMError(f"{self.config.base_url}: {_describe(e)}") from e
        except openai.OpenAIError as e:
            raise LLMError(f"{self.config.base_url}: {_describe(e)}") from e

        if not completion.choices:
            raise LLMError("эндпоинт вернул ответ без choices")
        choice = completion.choices[0]
        message = choice.message
        usage = Usage(calls=1)
        if completion.usage is not None:
            details = completion.usage.prompt_tokens_details
            usage = Usage(
                prompt_tokens=completion.usage.prompt_tokens,
                completion_tokens=completion.usage.completion_tokens,
                cached_tokens=(details.cached_tokens or 0) if details else 0,
                calls=1,
            )
        raw_calls = [
            RawToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
            for c in message.tool_calls or []
            if c.type == "function"
        ]
        # vLLM отдаёт размышления в `reasoning_content` (старые версии) или `reasoning`.
        reasoning = getattr(message, "reasoning_content", None) or getattr(
            message, "reasoning", None
        )
        return RawCompletion(
            content=message.content or "",
            reasoning=reasoning if isinstance(reasoning, str) and reasoning else None,
            tool_calls=raw_calls,
            usage=usage,
            finish_reason=choice.finish_reason,
        )

    def _client(self) -> OpenAI:
        shared = self._shared
        with shared.lock:
            if shared.client is None:
                shared.client = OpenAI(
                    base_url=self.config.base_url,
                    api_key=self.config.api_key,
                    timeout=self.config.timeout_s,
                    max_retries=0,  # повторы — в _with_retries, с нашим backoff и логами
                )
            return shared.client


class _SharedOpenAI:
    """openai-клиент, общий для LLMClient и всех его bind()-копий (создаётся лениво)."""

    def __init__(self, client: OpenAI | None) -> None:
        self.client = client
        self.lock = threading.Lock()


class NullLLM(LLMClient):
    """LLM для агентов с `llm_free=True`: любой вызов — ошибка."""

    def __init__(self) -> None:
        super().__init__(LLMConfig(model="none"), profiles=DEFAULT_PROFILES)

    def _transport(self, request: LLMRequest) -> RawCompletion:
        raise LLMError("агент помечен llm_free=True, но вызвал LLM")


def _message_param(message: ChatMessage) -> ChatCompletionMessageParam:
    match message.role:
        case "system":
            return ChatCompletionSystemMessageParam(role="system", content=message.content)
        case "user":
            return ChatCompletionUserMessageParam(role="user", content=message.content)
        case "tool":
            return ChatCompletionToolMessageParam(
                role="tool", tool_call_id=message.tool_call_id or "", content=message.content
            )
        case "assistant":
            param = ChatCompletionAssistantMessageParam(role="assistant", content=message.content)
            if message.tool_calls:
                param["tool_calls"] = [
                    ChatCompletionMessageFunctionToolCallParam(
                        id=c.id,
                        type="function",
                        function={"name": c.name, "arguments": c.arguments_json()},
                    )
                    for c in message.tool_calls
                ]
            return param


def _tool_param(tool: ToolSpec) -> ChatCompletionFunctionToolParam:
    parameters: dict[str, object] = dict(tool.parameters)
    return ChatCompletionFunctionToolParam(
        type="function",
        function={"name": tool.name, "description": tool.description, "parameters": parameters},
    )


def _schema_instruction(json_schema: dict[str, Any]) -> str:
    return (
        "Ответь одним JSON-документом, строго соответствующим JSON Schema ниже, "
        "без пояснений и без markdown.\n" + json.dumps(json_schema, ensure_ascii=False)
    )


def _describe(exc: openai.OpenAIError) -> str:
    status = getattr(exc, "status_code", None)
    text = str(exc)[:500]
    return f"HTTP {status}: {text}" if status else f"{type(exc).__name__}: {text}"
