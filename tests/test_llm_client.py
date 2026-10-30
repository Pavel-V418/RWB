import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx2 as httpx  # транспорт openai 3.x
import openai
import pytest
from openai import OpenAI
from pydantic import BaseModel

from specforge.core.contracts import Usage
from specforge.llm import (
    ChatMessage,
    Expect,
    FakeLLM,
    FakeReply,
    FakeToolCall,
    LLMCallRecord,
    LLMClient,
    LLMConfig,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMTransientError,
    LLMUnavailableError,
    NullLLM,
    ProfileName,
    StructuredOutputError,
    ToolCall,
    ToolSpec,
)
from specforge.llm.client import _message_param
from specforge.llm.types import RawCompletion

USER = [ChatMessage(role="user", content="сколько?")]
READ_FILE = ToolSpec(
    name="read_file",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)


class Answer(BaseModel):
    value: int


# --- structured ----------------------------------------------------------------------------------


def test_structured_parses_json_and_sends_schema() -> None:
    llm = FakeLLM(
        [FakeReply(content='```json\n{"value": 42}\n```', expect=Expect(schema="Answer"))]
    )
    assert llm.structured(USER, Answer) == Answer(value=42)
    assert llm.calls[0].response_schema is not None


def test_structured_retries_once_with_error_text() -> None:
    llm = FakeLLM(['{"value": "много"}', FakeReply(structured=Answer(value=7))])
    assert llm.structured(USER, Answer).value == 7
    retry = llm.calls[1].messages
    assert retry[-2].role == "assistant"
    assert retry[-2].content == '{"value": "много"}'
    assert "не прошёл валидацию" in retry[-1].content
    assert "value" in retry[-1].content


def test_structured_raises_after_retry() -> None:
    llm = FakeLLM(["не json", "всё ещё не json", "лишний"])
    with pytest.raises(StructuredOutputError) as info:
        llm.structured(USER, Answer)
    assert info.value.raw == "всё ещё не json"
    assert "нет JSON" in info.value.validation_error
    assert llm.remaining == 1  # ровно один повтор


def test_structured_retry_history_has_no_reasoning() -> None:
    llm = FakeLLM(
        [
            FakeReply(content="плохо", reasoning="секретные мысли"),
            FakeReply(structured={"value": 1}),
        ]
    )
    llm.structured(USER, Answer, ProfileName.ANALYSIS)
    assert all("секретные мысли" not in m.content for m in llm.calls[1].messages)


def test_prompt_mode_puts_schema_into_messages() -> None:
    llm = FakeLLM(['{"value": 1}'], config=LLMConfig(model="fake", structured_mode="prompt"))
    llm.structured(USER, Answer)
    call = llm.calls[0]
    assert call.response_schema_name is None
    assert '"value"' in call.messages[-1].content


# --- chat: профили, reasoning, tool calls --------------------------------------------------------


def test_profile_sets_sampling_and_overrides_win() -> None:
    llm = FakeLLM(["a", "b", "c"])
    llm.chat(USER, profile=ProfileName.ANALYSIS)
    llm.chat(USER, profile="REPAIR", temperature=0.5)
    llm.chat(USER, thinking=True)
    analysis, repair, default = llm.calls
    assert (analysis.thinking, analysis.temperature, analysis.top_k) == (True, 0.6, 20)
    assert (repair.thinking, repair.temperature) == (False, 0.5)
    assert (default.profile, default.thinking, default.temperature) == ("CODE", True, 0.2)
    with pytest.raises(LLMConfigError, match="неизвестный профиль"):
        llm.chat(USER, profile="NOPE")


def test_reasoning_split_from_content() -> None:
    llm = FakeLLM(["<think>план</think>\nответ", FakeReply(content="ок", reasoning="от сервера")])
    first = llm.chat(USER)
    assert (first.reasoning, first.content) == ("план", "ответ")
    second = llm.chat(USER)
    assert second.reasoning == "от сервера"
    assert second.to_message() == ChatMessage(role="assistant", content="ок")


def test_server_tool_calls_are_validated() -> None:
    llm = FakeLLM(
        [
            FakeReply(
                tool_calls=[
                    FakeToolCall("read_file", {"path": "a.md"}, id="c1"),
                    FakeToolCall("read_file", "{битый", id="c2"),
                    FakeToolCall("rm_rf", {}, id="c3"),
                ],
                expect=Expect(tools=["read_file"]),
            )
        ]
    )
    r = llm.chat(USER, tools=[READ_FILE])
    ok, broken, unknown = r.tool_calls
    assert ok.valid
    assert ok.arguments == {"path": "a.md"}
    assert broken.error is not None
    assert "JSON" in broken.error
    assert unknown.error is not None
    assert "неизвестный инструмент" in unknown.error
    assert r.finish_reason == "tool_calls"


def test_tool_calls_extracted_from_content_when_server_did_not_parse() -> None:
    llm = FakeLLM(
        ['Читаю.\n<tool_call>{"name": "read_file", "arguments": {"path": "x"}}</tool_call>']
    )
    r = llm.chat(USER, tools=[READ_FILE])
    assert [(c.name, c.arguments) for c in r.tool_calls] == [("read_file", {"path": "x"})]
    assert r.content == "Читаю."


def test_tool_call_json_not_extracted_without_tools() -> None:
    text = '{"name": "read_file", "arguments": {"path": "x"}}'
    assert FakeLLM([text]).chat(USER).content == text


# --- usage, bind, hooks --------------------------------------------------------------------------


def test_usage_tracker_breakdown_by_run_stage_agent() -> None:
    llm = FakeLLM([FakeReply(content="x", usage=Usage(prompt_tokens=10, calls=1))] * 3)
    a = llm.bind(run_id="r1", stage="analysis", agent="analyzer")
    b = llm.bind(run_id="r1", stage="backend", agent="coder")
    a.chat(USER)
    a.chat(USER)
    b.chat(USER)
    assert a.usage.prompt_tokens == 20
    assert llm.usage.prompt_tokens == 30
    assert llm.tracker.total(run_id="r1").calls == 3
    assert {k: v.calls for k, v in llm.tracker.breakdown("stage").items()} == {
        "analysis": 2,
        "backend": 1,
    }
    assert llm.tracker.breakdown("agent")["coder"].prompt_tokens == 10


def test_bind_inherits_labels_and_shares_script() -> None:
    llm = FakeLLM(["a"])
    bound = llm.bind(run_id="r").bind(agent="x")
    assert bound.labels.run_id == "r"
    assert bound.labels.agent == "x"
    bound.chat(USER)
    assert llm.calls
    assert llm.remaining == 0


def test_track_counts_only_inside_block() -> None:
    llm = FakeLLM(["a" * 40, "b" * 40])
    llm.chat(USER)
    with llm.track() as meter:
        llm.chat(USER)
    assert meter.usage.calls == 1
    assert meter.usage.completion_tokens == 10
    assert llm.usage.calls == 2


def test_on_call_hook_gets_record_and_errors_do_not_break_call() -> None:
    records: list[LLMCallRecord] = []

    def broken_hook(record: LLMCallRecord) -> None:
        raise RuntimeError("телеметрия упала")

    llm = FakeLLM([FakeReply(content="ok", tool_calls=[FakeToolCall("read_file", {"path": "p"})])])
    llm.add_hook(records.append)
    llm.add_hook(broken_hook)
    llm.bind(run_id="r", stage="s", agent="a").chat(USER, tools=[READ_FILE])
    (record,) = records
    assert (record.run_id, record.stage, record.agent, record.profile) == ("r", "s", "a", "CODE")
    assert record.tool_calls == 1
    assert record.attempts == 1
    assert record.error is None


# --- ретраи, семафор -----------------------------------------------------------------------------


def test_transient_errors_retried_with_backoff() -> None:
    llm = FakeLLM([LLMTransientError("503"), LLMTransientError("conn reset"), "ok"])
    delays: list[float] = []
    llm._sleep = delays.append
    records: list[LLMCallRecord] = []
    llm.add_hook(records.append)
    assert llm.chat(USER).content == "ok"
    assert len(delays) == 2
    assert 0.5 <= delays[0] <= 1.0  # backoff 1 с с джиттером 50–100 %
    assert 1.0 <= delays[1] <= 2.0
    assert records[0].attempts == 3


def test_retries_exhausted() -> None:
    config = LLMConfig(model="fake", max_retries=1)
    llm = FakeLLM([LLMTransientError("503")] * 2, config=config)
    records: list[LLMCallRecord] = []
    llm.add_hook(records.append)
    with pytest.raises(LLMUnavailableError, match="2 попыток"):
        llm.chat(USER)
    assert records[0].error is not None


def test_non_transient_error_not_retried() -> None:
    llm = FakeLLM([LLMError("400 bad request"), "не дойдём"])
    with pytest.raises(LLMError, match="400"):
        llm.chat(USER)
    assert llm.remaining == 1


def test_semaphore_limits_concurrency() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    class Slow(LLMClient):
        def _transport(self, request: LLMRequest) -> RawCompletion:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return RawCompletion(content="ok", usage=Usage(calls=1))

    llm = Slow(LLMConfig(model="m", max_concurrency=2), profiles={})
    llm.profiles = FakeLLM().profiles
    threads = [
        threading.Thread(target=llm.bind(agent=f"a{i}").chat, args=(USER,)) for i in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 2
    assert llm.usage.calls == 6


def test_null_llm_refuses() -> None:
    with pytest.raises(LLMError, match="llm_free"):
        NullLLM().chat(USER)


# --- конфигурация --------------------------------------------------------------------------------


def test_config_from_env(tmp_path: Path) -> None:
    config = LLMConfig.from_env(
        {
            "LLM_MODEL": "qwen",
            "LLM_BASE_URL": "http://gpu:8000/v1",
            "LLM_VLLM_EXTRAS": "false",
            "LLM_MAX_CONCURRENCY": "8",
            "LLM_PROFILES_PATH": str(tmp_path / "p.yaml"),
        }
    )
    assert config.base_url == "http://gpu:8000/v1"
    assert not config.vllm_extras
    assert config.max_concurrency == 8
    assert config.profiles_path == tmp_path / "p.yaml"
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env({})
    with pytest.raises(LLMConfigError, match="structured_mode"):
        LLMConfig.from_env({"LLM_MODEL": "qwen", "LLM_STRUCTURED_MODE": "xml"})


# --- транспорт openai ----------------------------------------------------------------------------


class _Completions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _completion(message: SimpleNamespace, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=64),
        ),
    )


def _client(outcomes: list[Any], **config: Any) -> tuple[LLMClient, _Completions]:
    completions = _Completions(outcomes)
    fake_openai = cast(OpenAI, SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    llm = LLMClient(LLMConfig(model="qwen", **config), openai_client=fake_openai)
    llm._sleep = lambda _: None
    return llm, completions


def test_openai_request_shape_and_usage() -> None:
    message = SimpleNamespace(content='{"value": 3}', tool_calls=None, reasoning_content="думал")
    llm, completions = _client([_completion(message)])

    assert llm.structured(USER, Answer, ProfileName.ANALYSIS).value == 3

    sent = completions.requests[0]
    assert sent["model"] == "qwen"
    assert sent["messages"] == [{"role": "user", "content": "сколько?"}]
    assert sent["response_format"]["json_schema"]["name"] == "Answer"
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}, "top_k": 20}
    assert sent["temperature"] == 0.6
    assert sent["top_p"] == 0.95
    assert llm.usage == Usage(prompt_tokens=100, completion_tokens=5, cached_tokens=64, calls=1)


def test_openai_tools_and_history_roundtrip() -> None:
    call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="read_file", arguments='{"path": "a"}'),
    )
    message = SimpleNamespace(content=None, tool_calls=[call], reasoning="мысли")
    llm, completions = _client([_completion(message, "tool_calls")], vllm_extras=False)

    r = llm.chat(USER, tools=[READ_FILE])

    assert r.reasoning == "мысли"
    assert r.tool_calls == [
        ToolCall(
            id="call_1", name="read_file", arguments={"path": "a"}, raw_arguments='{"path": "a"}'
        )
    ]
    sent = completions.requests[0]
    assert sent["tool_choice"] == "auto"
    assert sent["tools"][0]["function"]["name"] == "read_file"
    assert sent["extra_body"] is None

    history = [*USER, r.to_message(), ChatMessage.tool_result("call_1", "содержимое")]
    params = [_message_param(m) for m in history]
    assert params[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a"}'},
            }
        ],
    }
    assert params[2] == {"role": "tool", "tool_call_id": "call_1", "content": "содержимое"}


def test_openai_errors_classified() -> None:
    request = httpx.Request("POST", "http://gpu/v1/chat/completions")
    ok = _completion(SimpleNamespace(content="ok", tool_calls=None))
    server_error = openai.InternalServerError(
        "boom", response=httpx.Response(503, request=request), body=None
    )
    llm, completions = _client([openai.APIConnectionError(request=request), server_error, ok])
    assert llm.chat(USER).content == "ok"
    assert len(completions.requests) == 3

    bad = openai.BadRequestError("bad", response=httpx.Response(400, request=request), body=None)
    llm, completions = _client([bad, ok])
    with pytest.raises(LLMError, match="HTTP 400"):
        llm.chat(USER)
    assert len(completions.requests) == 1
