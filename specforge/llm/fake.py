"""FakeLLM — LLMClient со скриптованными ответами для тестов без GPU и сети."""

from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from specforge.core.contracts import Usage
from specforge.llm.client import ChatMessage, ChatResponse, LLMClient, LLMConfig, LLMError

FakeReply = str | Callable[[list[ChatMessage]], str]


class FakeCall(BaseModel):
    messages: list[ChatMessage]
    temperature: float
    thinking: bool
    json_schema_name: str | None


class FakeLLM(LLMClient):
    """Отдаёт ответы по очереди. Ответ — строка или функция от сообщений запроса.

    Наследует LLMClient целиком, поэтому structured(), повторы и учёт токенов работают как в бою.
    Токены считаются приближённо: 1 токен ≈ 4 символа.
    """

    def __init__(self, replies: Sequence[FakeReply] = (), *, config: LLMConfig | None = None):
        super().__init__(config or LLMConfig(model="fake"))
        self._replies: deque[FakeReply] = deque(replies)
        self.calls: list[FakeCall] = []

    def push(self, *replies: FakeReply) -> None:
        self._replies.extend(replies)

    @property
    def remaining(self) -> int:
        return len(self._replies)

    def _complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        thinking: bool,
        json_schema: tuple[str, dict[str, Any]] | None,
    ) -> ChatResponse:
        history = list(messages)
        self.calls.append(
            FakeCall(
                messages=history,
                temperature=temperature,
                thinking=thinking,
                json_schema_name=json_schema[0] if json_schema else None,
            )
        )
        if not self._replies:
            raise LLMError(f"FakeLLM: скриптованные ответы закончились (вызов №{len(self.calls)})")
        reply = self._replies.popleft()
        content = reply if isinstance(reply, str) else reply(history)
        prompt_chars = sum(len(m.content) for m in history)
        return ChatResponse(
            content=content,
            usage=Usage(
                prompt_tokens=prompt_chars // 4,
                completion_tokens=len(content) // 4,
                calls=1,
            ),
            finish_reason="stop",
        )
