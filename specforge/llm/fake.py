"""FakeLLM — LLMClient со скриптованными ответами для тестов без GPU и сети.

Подменяет только транспорт, поэтому профили, повторы, разбор reasoning и tool calls, structured
с повтором и учёт токенов работают как в бою.

    llm = FakeLLM([
        "просто текст",
        FakeReply(tool_calls=[FakeToolCall("read_file", {"path": "a.md"})]),
        FakeReply(structured=Answer(value=1), expect=Expect(schema="Answer")),
        FakeReply(content="ok", reasoning="думаю", expect=Expect(profile="ANALYSIS")),
        LLMTransientError("503"),          # транспорт бросит — проверка повторов
        lambda request: "ответ по запросу", # динамический ответ
    ])
    ...
    llm.assert_exhausted()

Токены считаются приближённо: 1 токен ≈ 4 символа.
"""

import json
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from specforge.core.contracts import Usage
from specforge.llm.client import LLMClient, LLMConfig
from specforge.llm.profiles import DEFAULT_PROFILES, Profile
from specforge.llm.tool_parsing import new_call_id
from specforge.llm.types import LLMError, LLMRequest, RawCompletion, RawToolCall


class FakeExpectationError(AssertionError):
    """Запрос не совпал с ожиданием из скрипта FakeLLM."""


@dataclass
class FakeToolCall:
    name: str
    arguments: Mapping[str, Any] | str = field(default_factory=dict)
    """dict — будет сериализован в JSON; str — уйдёт как есть (можно проверить невалидный JSON)."""
    id: str = ""


@dataclass
class Expect:
    """Ожидания к запросу; None — не проверять."""

    profile: str | None = None
    thinking: bool | None = None
    tools: Sequence[str] | None = None
    """Ровно этот набор имён инструментов."""
    schema: str | None = None
    """Имя схемы structured-вызова (response_format)."""
    contains: str | None = None
    """Подстрока в последнем сообщении запроса."""
    check: Callable[[LLMRequest], bool] | None = None


@dataclass
class FakeReply:
    content: str = ""
    reasoning: str | None = None
    tool_calls: Sequence[FakeToolCall] = ()
    structured: BaseModel | Mapping[str, Any] | None = None
    """Ответ structured-вызова; сериализуется в JSON и заменяет content."""
    usage: Usage | None = None
    finish_reason: str = "stop"
    expect: Expect | None = None


ScriptItem = str | FakeReply | Exception | Callable[[LLMRequest], "str | FakeReply"]


class FakeLLM(LLMClient):
    def __init__(
        self,
        script: Sequence[ScriptItem] = (),
        *,
        config: LLMConfig | None = None,
        profiles: Mapping[str, Profile] | None = None,
    ) -> None:
        super().__init__(
            config or LLMConfig(model="fake"),
            profiles=profiles if profiles is not None else DEFAULT_PROFILES,
        )
        self._script: deque[ScriptItem] = deque(script)
        self.calls: list[LLMRequest] = []
        self._sleep = lambda _: None  # повторы в тестах — без реального ожидания

    def push(self, *items: ScriptItem) -> None:
        self._script.extend(items)

    @property
    def remaining(self) -> int:
        return len(self._script)

    def assert_exhausted(self) -> None:
        if self._script:
            raise FakeExpectationError(f"FakeLLM: не использовано ответов: {len(self._script)}")

    def _transport(self, request: LLMRequest) -> RawCompletion:
        self.calls.append(request)
        number = len(self.calls)
        if not self._script:
            raise LLMError(f"FakeLLM: скриптованные ответы закончились (вызов №{number})")
        item = self._script.popleft()
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(request)
        reply = FakeReply(content=item) if isinstance(item, str) else item
        if reply.expect is not None:
            _check(reply.expect, request, number)
        return _completion(reply, request)


def _check(expect: Expect, request: LLMRequest, number: int) -> None:
    problems: list[str] = []
    if expect.profile is not None and request.profile != expect.profile:
        problems.append(f"profile={request.profile!r}, ожидался {expect.profile!r}")
    if expect.thinking is not None and request.thinking != expect.thinking:
        problems.append(f"thinking={request.thinking}, ожидался {expect.thinking}")
    if expect.tools is not None and sorted(request.tool_names) != sorted(expect.tools):
        problems.append(f"tools={request.tool_names}, ожидались {list(expect.tools)}")
    if expect.schema is not None and request.response_schema_name != expect.schema:
        problems.append(f"schema={request.response_schema_name!r}, ожидалась {expect.schema!r}")
    if expect.contains is not None:
        last = request.messages[-1].content if request.messages else ""
        if expect.contains not in last:
            problems.append(f"последнее сообщение не содержит {expect.contains!r}")
    if expect.check is not None and not expect.check(request):
        problems.append("не выполнена проверка check()")
    if problems:
        raise FakeExpectationError(f"FakeLLM, вызов №{number}: " + "; ".join(problems))


def _completion(reply: FakeReply, request: LLMRequest) -> RawCompletion:
    content = reply.content
    if reply.structured is not None:
        content = (
            reply.structured.model_dump_json()
            if isinstance(reply.structured, BaseModel)
            else json.dumps(reply.structured, ensure_ascii=False)
        )
    calls = [
        RawToolCall(
            id=c.id or new_call_id(),
            name=c.name,
            arguments=(
                c.arguments
                if isinstance(c.arguments, str)
                else json.dumps(dict(c.arguments), ensure_ascii=False)
            ),
        )
        for c in reply.tool_calls
    ]
    usage = reply.usage or _estimate_usage(request, content, reply.reasoning, calls)
    return RawCompletion(
        content=content,
        reasoning=reply.reasoning,
        tool_calls=calls,
        usage=usage,
        finish_reason="tool_calls"
        if calls and reply.finish_reason == "stop"
        else reply.finish_reason,
    )


def _estimate_usage(
    request: LLMRequest, content: str, reasoning: str | None, calls: Sequence[RawToolCall]
) -> Usage:
    prompt_chars = sum(
        len(m.content) + sum(len(c.arguments_json()) for c in m.tool_calls)
        for m in request.messages
    )
    prompt_chars += sum(len(t.model_dump_json()) for t in request.tools)
    completion_chars = len(content) + len(reasoning or "") + sum(len(c.arguments) for c in calls)
    return Usage(prompt_tokens=prompt_chars // 4, completion_tokens=completion_chars // 4, calls=1)
