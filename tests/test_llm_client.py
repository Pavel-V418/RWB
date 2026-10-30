from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import OpenAI
from pydantic import BaseModel

from specforge.llm import (
    ChatMessage,
    FakeLLM,
    LLMClient,
    LLMConfig,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    NullLLM,
)
from specforge.llm.tool_parsing import extract_json, strip_think


class Answer(BaseModel):
    value: int


USER = [ChatMessage(role="user", content="сколько?")]


def test_structured_parses_fenced_json_after_think() -> None:
    llm = FakeLLM(['<think>считаю…</think>\nВот:\n```json\n{"value": 42}\n```'])
    assert llm.structured(USER, Answer) == Answer(value=42)
    assert llm.calls[0].json_schema_name == "Answer"


def test_structured_retries_with_validation_error() -> None:
    llm = FakeLLM(['{"value": "много"}', '{"value": 7}'])
    assert llm.structured(USER, Answer).value == 7
    retry_messages = llm.calls[1].messages
    assert retry_messages[-2].role == "assistant"
    assert "не прошёл валидацию" in retry_messages[-1].content
    assert llm.usage.calls == 2


def test_structured_gives_up() -> None:
    llm = FakeLLM(["не json", "всё ещё не json"])
    with pytest.raises(LLMOutputError) as info:
        llm.structured(USER, Answer)
    assert info.value.raw == "всё ещё не json"


def test_prompt_mode_puts_schema_into_messages() -> None:
    llm = FakeLLM(['{"value": 1}'], config=LLMConfig(model="fake", structured_mode="prompt"))
    llm.structured(USER, Answer)
    call = llm.calls[0]
    assert call.json_schema_name is None
    assert '"value"' in call.messages[-1].content


def test_track_counts_only_inside_block() -> None:
    llm = FakeLLM(["a" * 40, "b" * 40])
    llm.chat(USER)
    with llm.track() as tracker:
        llm.chat(USER)
    assert tracker.usage.calls == 1
    assert tracker.usage.completion_tokens == 10
    assert llm.usage.calls == 2


def test_fake_llm_exhausted_and_callable_reply() -> None:
    llm = FakeLLM([lambda msgs: f"эхо: {msgs[-1].content}"])
    assert llm.chat(USER).content == "эхо: сколько?"
    with pytest.raises(LLMError, match="закончились"):
        llm.chat(USER)


def test_null_llm_refuses() -> None:
    with pytest.raises(LLMError, match="llm_free"):
        NullLLM().chat(USER)


def test_config_from_env() -> None:
    config = LLMConfig.from_env(
        {
            "LLM_MODEL": "qwen",
            "LLM_BASE_URL": "http://gpu:8000/v1",
            "LLM_CHAT_TEMPLATE_KWARGS": "true",
            "LLM_STRUCTURED_MODE": "prompt",
        }
    )
    assert config.base_url == "http://gpu:8000/v1"
    assert config.chat_template_kwargs
    assert config.structured_mode == "prompt"
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env({})
    with pytest.raises(LLMConfigError, match="structured_mode"):
        LLMConfig.from_env({"LLM_MODEL": "qwen", "LLM_STRUCTURED_MODE": "xml"})


class _RecordingCompletions:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"value": 3}'), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=5,
                prompt_tokens_details=SimpleNamespace(cached_tokens=64),
            ),
        )


def test_openai_transport_request_and_usage() -> None:
    completions = _RecordingCompletions()
    fake_openai = cast(OpenAI, SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    llm = LLMClient(LLMConfig(model="qwen", chat_template_kwargs=True), openai_client=fake_openai)

    assert llm.structured(USER, Answer, thinking=True).value == 3

    sent = completions.kwargs
    assert sent["model"] == "qwen"
    assert sent["messages"] == [{"role": "user", "content": "сколько?"}]
    assert sent["response_format"]["json_schema"]["name"] == "Answer"
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
    assert llm.usage.cached_tokens == 64
    assert llm.usage.prompt_tokens == 100


def test_strip_think_and_extract_json() -> None:
    assert strip_think("мысли</think>ответ") == "ответ"
    assert extract_json('Результат: {"a": [1, 2]} конец') == '{"a": [1, 2]}'
    assert extract_json("[1, 2]") == "[1, 2]"
    with pytest.raises(ValueError, match="нет JSON"):
        extract_json("пусто")
